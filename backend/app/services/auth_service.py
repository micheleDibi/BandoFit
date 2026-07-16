"""Flussi auth con link email 100% di dominio.

Supabase è solo il deposito: gli utenti vengono creati/aggiornati via Admin
API (create_user / update_user_by_id) e NESSUN link viene generato da GoTrue.
I link nelle email portano token nostri (vedi token_service), verificati dal
backend; l'effetto (conferma email, cambio password) viene applicato via
Admin API. Il mailer di Supabase non viene mai attivato.

ANTI-ENUMERAZIONE (CWE-204). `register` risponde SEMPRE 202 {"ok": true}: chi
chiama non può distinguere un indirizzo nuovo da uno già registrato. Tre
proprietà tengono in piedi la cosa e vanno mantenute INSIEME — toglierne una le
vanifica tutte, e nessuna delle tre è una preferenza di stile:

1. La registrazione NON manda la password a GoTrue: l'utente la sceglie alla
   conferma, come nel flusso invito (family_service). Se la mandasse, lo stato
   dell'account diventerebbe osservabile dall'esterno anche a corpo neutro,
   perché GoTrue è raggiungibile dal browser con la anon key. NON rimetterla qui.
2. L'esistenza si comunica fuori banda, all'indirizzo (_avvisa_account_esistente):
   lo sa solo chi possiede la casella.
3. La risposta dura sempre almeno `register_latency_target_seconds`: senza, il
   tempo direbbe ciò che il corpo tace.

Restano limiti della piattaforma, non chiudibili da qui, finché l'autenticazione
vive nel browser: sono censiti nel piano di sicurezza interno, non qui.
"""

import asyncio
import logging
import time
from functools import partial
from urllib.parse import quote

from app.core.config import get_settings
from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UpstreamError,
)
from app.services import (
    email_service,
    job_position_service,
    rate_limit_service,
    token_service,
)

logger = logging.getLogger("bandofit.auth")

# Anti-abuso: questi endpoint sono pubblici e fanno partire email reali.
# Cooldown in-process per destinatario: sopravvive solo dentro un processo e si
# azzera a ogni deploy, quindi è il backstop — il limite vero è a DB
# (rate_limit_service, migration 0025).
_COOLDOWN_SECONDS = 60
_last_sent: dict[tuple[str, str], float] = {}

# Riferimenti forti ai task email in background (pattern consulting_service:
# senza, il GC può cancellare un task in volo).
_background_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _send_best_effort(send) -> None:
    """Riceve una FACTORY (partial), non una coroutine: la coroutine nasce solo
    quando il task gira davvero — niente «coroutine was never awaited» se il
    task viene cancellato prima di partire."""
    try:
        await send()
    except Exception:  # pragma: no cover - email_service non solleva mai
        logger.warning("invio email auth fallito", exc_info=True)


def _cooldown_ok(kind: str, email: str) -> bool:
    """True se si può procedere, False se l'ultima richiesta è troppo recente.

    Ritorna un bool invece di sollevare perché su /register un errore
    distinguibile ricostruirebbe l'oracolo appena chiuso: là il chiamante
    risponde 202 come sempre e semplicemente non manda l'email.
    """
    now = time.monotonic()
    # pulizia opportunistica per non far crescere il dizionario
    if len(_last_sent) > 5000:
        cutoff = now - _COOLDOWN_SECONDS
        for key in [k for k, ts in _last_sent.items() if ts < cutoff]:
            _last_sent.pop(key, None)
    key = (kind, email)
    if now - _last_sent.get(key, 0.0) < _COOLDOWN_SECONDS:
        return False
    _last_sent[key] = now
    return True


def _check_cooldown(kind: str, email: str) -> None:
    """Variante che solleva, per gli endpoint dove il 409 non rivela nulla.

    Su /recover e /resend-confirmation il contatore si arma PRIMA di sapere se
    l'account esiste, quindi il 409 correla con «hai già chiesto tu poco fa», mai
    con l'esistenza dell'indirizzo. Su /register non è così — là si usa
    _cooldown_ok.
    """
    if not _cooldown_ok(kind, email):
        raise ConflictError("Email già inviata da poco: attendi un minuto e riprova")


def _link(path: str, token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}?token={token}"


def _page(path: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}{path}"


async def _find_profile_by_email(primary, email: str) -> dict | None:
    # eq e MAI ilike: in ILIKE `_` è un carattere jolly, e l'underscore negli
    # indirizzi è comunissimo — «mario_rossi@x.it» matcherebbe «marioXrossi@x.it»,
    # cioè il profilo di un'altra persona. L'email arriva già in minuscolo dal
    # chiamante e GoTrue la normalizza allo stesso modo, quindi il confronto
    # esatto è corretto. L'indice che serve è profiles_email_exact_idx (0025):
    # senza, il miss costa un seq scan completo mentre l'hit può uscire subito —
    # una differenza di tempo che misura proprio ciò che stiamo nascondendo.
    resp = (
        await primary.table("profiles")
        .select("id,email")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _livella_latenza(started: float) -> None:
    """Porta la durata della risposta a un valore costante.

    Il ramo «indirizzo libero» crea l'utente ed emette un token, quello
    «indirizzo già registrato» no: senza questo, il cronometro direbbe ciò che
    il corpo della risposta tace.
    """
    resto = get_settings().register_latency_target_seconds - (time.monotonic() - started)
    if resto > 0:
        await asyncio.sleep(resto)


async def _avvisa_account_esistente(primary, email: str, user_id: str | None = None) -> None:
    """Dice all'indirizzo che un account esiste già — fuori banda, perché è
    l'unico canale in cui l'informazione raggiunge solo chi possiede la casella.

    È il sostituto del vecchio 409 e il punto in cui pre-check ed except
    convergono, così i due rami restano indistinguibili.
    """
    if user_id is None:
        profile = await _find_profile_by_email(primary, email)
        if profile is None:
            # Registrazione concorrente ancora in volo, oppure utente in
            # auth.users senza profilo (handle_new_user ha un `exception when
            # others`). Nessun avviso: meglio silenzio che un'email sbagliata.
            logger.warning("Account esistente per %s ma profilo non trovato", email)
            return
        user_id = profile["id"]

    try:
        user = (await primary.auth.admin.get_user_by_id(user_id)).user
    except Exception as exc:
        logger.warning("Avviso account esistente: utente %s non leggibile: %s", user_id, exc)
        return

    if user is not None and user.email_confirmed_at is None:
        # In attesa di conferma: la CTA porta al form che richiede un link
        # nuovo, non a un link già pronto. Emetterlo qui invaliderebbe quello
        # che la persona ha già in casella (token_service.issue tiene valido un
        # solo link per volta) — e visto che a chiedere è un anonimo, sarebbe un
        # modo per sabotare la conferma altrui.
        _spawn(
            _send_best_effort(
                partial(email_service.send_account_pending_email, email, _page("/conferma-email"))
            )
        )
    else:
        _spawn(
            _send_best_effort(
                partial(
                    email_service.send_account_exists_email,
                    email,
                    f"{_page('/login')}?email={quote(email)}",
                    _page("/recupera-password"),
                )
            )
        )


async def _gate_ip(primary, client_ip: str | None) -> None:
    """Limiti che dipendono solo dal chiamante: possono rifiutare con un 429
    esplicito senza rivelare nulla sull'indirizzo.

    Condiviso da register/recover/resend: sono le tre porte anonime che fanno
    partire email, e i contatori sono in comune di proposito — chi le alterna
    non deve trovare un budget fresco a ogni endpoint.
    """
    settings = get_settings()

    # Cap globale di SOLO ALLARME. Bloccare qui darebbe a un singolo IP
    # l'interruttore delle registrazioni di tutti: la difesa diventerebbe il DoS.
    if not await rate_limit_service.allow(
        primary, "global", settings.register_global_hourly_alert, 3600
    ):
        logger.error(
            "Registrazioni oltre la soglia globale di %s/h: possibile enumerazione in corso",
            settings.register_global_hourly_alert,
        )

    if client_ip is None:
        # IP non determinabile (sviluppo, o proxy diverso da quello dichiarato):
        # nessun limite per IP. Vedi core/net.py — una chiave sbagliata sarebbe
        # condivisa da tutti e bloccherebbe tutti insieme.
        return

    if not await rate_limit_service.allow(
        primary,
        rate_limit_service.bucket("ip", client_ip),
        settings.register_ip_burst_limit,
        settings.register_ip_burst_window_seconds,
    ):
        raise RateLimitedError("Troppi tentativi dalla tua rete: riprova tra qualche minuto.")

    if not await rate_limit_service.allow(
        primary,
        rate_limit_service.bucket("ip24", client_ip),
        settings.register_ip_daily_limit,
        86400,
    ):
        # Messaggio diverso: qui l'attesa è di ore, e dire «qualche minuto»
        # lascerebbe l'utente a sbattere contro il muro. Chi sta iscrivendo un
        # team dietro lo stesso NAT deve avere una via umana.
        raise RateLimitedError(
            "Dalla tua rete sono arrivate troppe richieste oggi. "
            "Se stai iscrivendo il tuo team, scrivici e ti diamo una mano."
        )


async def _registra_o_avvisa(
    primary,
    email: str,
    nome: str,
    cognome: str,
    azienda: str | None,
    telefono: str,
    job_position_slug: str,
    job_position_altro: str | None,
    plan_slug: str,
) -> None:
    """Fase che tocca il segreto. Non solleva mai nulla che dipenda
    dall'esistenza dell'indirizzo: il chiamante risponde 202 comunque."""
    settings = get_settings()

    # Il budget email si arma PRIMA di guardare se l'account esiste: armarlo
    # dopo lo renderebbe un oracolo (esisterebbe solo per gli indirizzi veri).
    # E sopprime SOLO l'invio, mai la creazione dell'account: se bloccasse la
    # registrazione, un anonimo potrebbe impedire a una persona precisa di
    # iscriversi, in silenzio, con quattro richieste.
    email_ok = await rate_limit_service.allow(
        primary,
        rate_limit_service.bucket("email", email),
        settings.register_email_hourly_limit,
        3600,
    )
    email_ok = _cooldown_ok("register", email) and email_ok

    profile = await _find_profile_by_email(primary, email)
    if profile is not None:
        if email_ok:
            await _avvisa_account_esistente(primary, email, profile["id"])
        return

    try:
        created = await primary.auth.admin.create_user(
            {
                "email": email,
                # NESSUNA password: la sceglie chi conferma. Vedi il modulo —
                # è ciò che impedisce di rileggere l'esistenza da GoTrue.
                "email_confirm": False,
                "user_metadata": {
                    "nome": nome,
                    "cognome": cognome,
                    "azienda": azienda or None,
                    "telefono": telefono,
                    "job_position_slug": job_position_slug,
                    "job_position_altro": job_position_altro,
                    "plan_slug": plan_slug,
                },
            }
        )
    except Exception as exc:
        message = str(exc).lower()
        if "already" in message or "exists" in message or "registered" in message:
            # Il pre-check diceva «libero»: o due registrazioni concorrenti
            # sullo stesso indirizzo, o un utente senza profilo. Stessa uscita
            # del ramo normale, così restano indistinguibili.
            if email_ok:
                await _avvisa_account_esistente(primary, email)
            return
        # Guasto upstream: si esce comunque 202. Un 502 qui sarebbe un oracolo —
        # il ramo «indirizzo esistente» non chiama create_user e quindi non può
        # produrlo, perciò durante un degrado di GoTrue «502 = indirizzo libero,
        # 202 = indirizzo esistente», vero a ogni richiesta e senza bisogno di
        # provocare il guasto: basta aspettarlo. Il prezzo è che la
        # registrazione fallisce in silenzio finché GoTrue non torna; il segnale
        # per noi è questo log, che va monitorato.
        logger.error("create_user fallita per %s: %s", email, exc)
        return

    # Account appena creato: la sua unica email parte SEMPRE, budget o no.
    # Il budget esiste per arginare gli avvisi ripetuti a una casella già
    # registrata, non per lasciare a secco un account nuovo — e a secco ci
    # finirebbe davvero: basta che create_user fallisca un paio di volte per un
    # guasto transitorio (ogni tentativo arma il contatore) perché il retry che
    # riesce trovi il budget esaurito e crei un account senza password, senza
    # token e senza email. Inviare qui non rivela nulla: l'email la vede solo
    # chi possiede la casella.
    user_id = str(created.user.id)
    token = await token_service.issue(primary, user_id, "confirm_email")
    _spawn(
        _send_best_effort(
            partial(email_service.send_confirmation_email, email, _link("/conferma-email", token))
        )
    )


async def register(
    primary,
    email: str,
    nome: str,
    cognome: str,
    azienda: str | None,
    telefono: str,
    job_position_slug: str,
    job_position_altro: str | None,
    plan_slug: str,
    client_ip: str | None = None,
) -> dict:
    """Avvia la registrazione. Risposta SEMPRE neutra: 202 {"ok": true}.

    Nessun parametro `password`: l'utente la sceglie confermando l'indirizzo
    (vedi `confirm_email`). Non è una scelta di UX ma il fulcro
    dell'anti-enumerazione — la docstring del modulo spiega perché.
    """
    email = email.strip().lower()

    # FASE 1 — validazioni che non dipendono dall'indirizzo: possono rispondere
    # 400 senza rivelare nulla. Restano prima dei limiti perché un tentativo
    # respinto non deve bruciare il budget di quello buono.
    plan_resp = (
        await primary.table("subscription_plans")
        .select("tipo_prezzo")
        .eq("slug", plan_slug)
        .limit(1)
        .execute()
    )
    if plan_resp.data and plan_resp.data[0]["tipo_prezzo"] == "su_richiesta":
        raise BadRequestError(
            "Questo piano è disponibile solo su richiesta: registrati con un "
            "altro piano e contattaci dalla pagina Abbonamento"
        )
    # Slug inesistente o disattivato: comportamento invariato, il trigger
    # handle_new_user ripiega sul piano Gratuito.

    # La posizione viene da una lookup: uno slug ignoto o disattivato è un
    # client fuori sincrono col catalogo.
    position = await job_position_service.get_active_by_slug(primary, job_position_slug)
    if position is None:
        raise BadRequestError(
            "La posizione selezionata non è più disponibile: "
            "ricarica la pagina e riprova"
        )
    if position["slug"] != job_position_service.SLUG_ALTRO:
        job_position_altro = None

    # Dipende solo dall'IP, che il chiamante già conosce: il 429 non rivela
    # nulla e NON va livellato — tenere aperte per un secondo e mezzo le
    # connessioni di chi martella regalerebbe un'amplificazione DoS.
    await _gate_ip(primary, client_ip)

    # FASE 2 — da qui si tocca il segreto: uscita unica, a durata costante.
    started = time.monotonic()
    try:
        await _registra_o_avvisa(
            primary,
            email,
            nome,
            cognome,
            azienda,
            telefono,
            job_position_slug,
            job_position_altro,
            plan_slug,
        )
    finally:
        await _livella_latenza(started)
    return {"ok": True}


async def confirm_email(primary, token: str, password: str) -> dict:
    """Completa la registrazione: imposta la password scelta ora e conferma
    l'indirizzo.

    La password arriva qui e non alla registrazione: il possesso della casella è
    provato dal token, quindi è anche il primo momento in cui è legittimo
    fissarla — come in `accept_invite`.

    L'ordine però è l'opposto: `accept_invite` e `reset_password` consumano il
    token PRIMA di applicare l'effetto, qui si consuma dopo. È deliberato: là la
    password è già stata validata quando il token viene speso, qui invece è il
    primo passo del nuovo flusso e una password rifiutata brucerebbe il link,
    lasciando fuori l'utente per aver scelto male. Prezzo del peek: finché
    l'update non riesce il link resta spendibile, quindi due invii concorrenti
    passano entrambi (impostano la stessa password, esito identico).
    """
    # peek e non consume: il token si brucia solo a cose fatte. Consumarlo prima
    # significherebbe che una password rifiutata (troppo debole) lascia fuori
    # l'utente con un link ormai morto — e che due click sullo stesso pulsante
    # fanno fallire il secondo.
    user_id = await token_service.peek(primary, token, "confirm_email")
    if user_id is None:
        raise NotFoundError("Link di conferma non valido o scaduto")
    try:
        updated = await primary.auth.admin.update_user_by_id(
            user_id, {"password": password, "email_confirm": True}
        )
    except Exception as exc:
        if "password" in str(exc).lower():
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("Conferma email fallita per %s: %s", user_id, exc)
        raise UpstreamError("Attivazione non riuscita, riprova") from exc
    await token_service.consume(primary, token, "confirm_email")
    return {"email": updated.user.email}


async def recover_password(primary, email: str, client_ip: str | None = None) -> None:
    """Invia il link di reimpostazione password (token nostro). SEMPRE
    risposta neutra: non riveliamo se l'email esiste (anti-enumerazione).

    Neutro nel corpo non basta: il ramo «esiste» emette un token e manda una
    email, quello «non esiste» ritorna subito. Senza invio in background e
    pavimento, il cronometro direbbe tutto — e sarebbe l'oracolo più comodo dei
    due, perché qui non serve nemmeno creare nulla.
    """
    email = email.strip().lower()
    # 429/409 esistenza-indipendenti: fuori dal pavimento, come in register.
    await _gate_ip(primary, client_ip)
    _check_cooldown("recover", email)

    started = time.monotonic()
    try:
        if not await rate_limit_service.allow(
            primary,
            rate_limit_service.bucket("email", email),
            get_settings().register_email_hourly_limit,
            3600,
        ):
            # Budget della casella esaurito: è anche l'argine alle molestie —
            # senza, chiunque potrebbe far piovere email di recupero su una
            # vittima.
            logger.warning("Budget email esaurito per il recupero di %s", email)
            return
        profile = await _find_profile_by_email(primary, email)
        if profile is None:
            logger.warning("Recovery richiesta per email non registrata: %s", email)
            return
        token = await token_service.issue(primary, profile["id"], "recovery")
        _spawn(
            _send_best_effort(
                partial(email_service.send_recovery_email, email, _link("/reimposta-password", token))
            )
        )
    finally:
        await _livella_latenza(started)


async def reset_password(primary, token: str, password: str) -> dict:
    """Consuma il token di recovery e imposta la nuova password via Admin API."""
    user_id = await token_service.consume(primary, token, "recovery")
    if user_id is None:
        raise NotFoundError("Link di reimpostazione non valido o scaduto")
    try:
        updated = await primary.auth.admin.update_user_by_id(user_id, {"password": password})
    except Exception as exc:
        if "password" in str(exc).lower():
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("Reset password fallito per %s: %s", user_id, exc)
        raise UpstreamError("Aggiornamento non riuscito, riprova") from exc
    return {"email": updated.user.email}


async def resend_confirmation(primary, email: str, client_ip: str | None = None) -> None:
    """Reinvia il link di conferma a un utente non ancora confermato.
    Risposta neutra in ogni caso — corpo, tempo e budget come recover_password.
    """
    email = email.strip().lower()
    await _gate_ip(primary, client_ip)
    _check_cooldown("confirm", email)

    started = time.monotonic()
    try:
        if not await rate_limit_service.allow(
            primary,
            rate_limit_service.bucket("email", email),
            get_settings().register_email_hourly_limit,
            3600,
        ):
            logger.warning("Budget email esaurito per il reinvio conferma a %s", email)
            return
        profile = await _find_profile_by_email(primary, email)
        if profile is None:
            logger.warning("Reinvio conferma per email non registrata: %s", email)
            return
        try:
            user = (await primary.auth.admin.get_user_by_id(profile["id"])).user
        except Exception as exc:
            logger.warning("Reinvio conferma: utente auth %s non leggibile: %s", profile["id"], exc)
            return
        if user is None or user.email_confirmed_at is not None:
            return  # già confermato: nulla da reinviare
        token = await token_service.issue(primary, profile["id"], "confirm_email")
        _spawn(
            _send_best_effort(
                partial(email_service.send_confirmation_email, email, _link("/conferma-email", token))
            )
        )
    finally:
        await _livella_latenza(started)


async def invite_info(primary, token: str) -> dict:
    """Contesto dell'invito per la pagina /accetta-invito (NON consuma il token)."""
    from app.services import family_service  # import locale: evita cicli

    user_id = await token_service.peek(primary, token, "invite")
    if user_id is None:
        raise NotFoundError("Invito non valido o scaduto")
    membership = await family_service.get_membership(primary, user_id)
    if membership is None or membership["status"] != "pending":
        raise NotFoundError("Invito non più valido")
    return {
        "email": membership["invited_email"],
        "denominazione": membership["denominazione"],
        "parent_display_name": await family_service.parent_display_name(
            primary, membership["parent_id"]
        ),
    }


async def accept_invite(primary, token: str, password: str) -> dict:
    """Consuma il token d'invito: imposta la password, conferma l'email e
    attiva la membership. Ritorna l'email per l'auto-login del frontend."""
    from app.services import family_service  # import locale: evita cicli

    user_id = await token_service.consume(primary, token, "invite")
    if user_id is None:
        raise NotFoundError("Invito non valido o scaduto")

    membership = await family_service.get_membership(primary, user_id)
    if membership is None or membership["status"] != "pending":
        raise NotFoundError("Invito non più valido")

    try:
        updated = await primary.auth.admin.update_user_by_id(
            user_id, {"password": password, "email_confirm": True}
        )
    except Exception as exc:
        if "password" in str(exc).lower():
            raise BadRequestError("La password non rispetta i requisiti minimi") from exc
        logger.error("Attivazione invitato %s fallita: %s", user_id, exc)
        raise UpstreamError("Attivazione non riuscita, riprova") from exc

    await family_service.accept_invitation(primary, user_id, membership["id"])
    return {"email": updated.user.email}
