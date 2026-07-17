"""Email transazionali con provider configurabile da env.

Priorità: SMTP (``SMTP_HOST`` valorizzato, es. casella OVH) → Resend
(``RESEND_API_KEY``) → fallback log-only (sviluppo). Gli invii NON sollevano
mai eccezioni: il canale affidabile per gli inviti è il banner in-app;
l'email è una notifica best-effort.
"""

import html
import logging
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger("bandofit.email")

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 15


def _branded_html(heading: str, paragraphs: list[str], cta_label: str, cta_url: str, footer: str) -> str:
    """Wrapper HTML comune a tutte le email transazionali (i paragrafi sono
    già HTML: l'escaping dei dati utente avviene nei chiamanti)."""
    body = "".join(
        f'<p style="font-size:15px;line-height:1.6;margin:0 0 12px">{p}</p>' for p in paragraphs
    )
    return f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1e293b">
  <div style="background:#1E5EFF;border-radius:12px 12px 0 0;padding:20px 28px">
    <span style="color:#fff;font-size:20px;font-weight:700">BandoFit</span>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;padding:28px">
    <h1 style="font-size:18px;margin:0 0 12px">{html.escape(heading)}</h1>
    {body}
    <a href="{cta_url}"
       style="display:inline-block;background:#1E5EFF;color:#fff;text-decoration:none;
              font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px;margin-top:8px">
      {html.escape(cta_label)}
    </a>
    <p style="font-size:13px;color:#64748b;margin:20px 0 0">{html.escape(footer)}</p>
  </div>
</div>"""


def _invitation_html(parent_display_name: str, denominazione: str, cta_url: str) -> str:
    parent = html.escape(parent_display_name)
    name = html.escape(denominazione)
    return _branded_html(
        "Sei stato invitato su BandoFit",
        [
            f"<strong>{parent}</strong> ti ha invitato a unirti alla sua azienda "
            f"su BandoFit come <strong>{name}</strong>.",
            "Entrando nell'azienda condividerai l'abbonamento e i dati aziendali del titolare.",
        ],
        "Vai all'invito",
        cta_url,
        "Se non ti aspettavi questo invito puoi ignorare questa email o rifiutarlo dalla piattaforma.",
    )


def _plain_text(parent_display_name: str, denominazione: str, cta_url: str) -> str:
    return (
        f"{parent_display_name} ti ha invitato a unirti alla sua azienda "
        f"su BandoFit come «{denominazione}».\n\n"
        f"Vai all'invito: {cta_url}\n\n"
        "Se non ti aspettavi questo invito puoi ignorare questa email."
    )


def _sanitize_header(value: str) -> str:
    """Le intestazioni email non devono contenere newline (header injection)."""
    return value.replace("\r", " ").replace("\n", " ").strip()


async def _send_via_smtp(
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> bool:
    import aiosmtplib

    display_name, from_address = parseaddr(settings.email_from)
    message = EmailMessage()
    sender_domain = None
    if from_address and "@" in from_address:
        user, _, domain = from_address.partition("@")
        sender_domain = domain
        message["From"] = Address(display_name or "BandoFit", user, domain)
    else:
        message["From"] = settings.email_from
    message["To"] = to_email
    message["Subject"] = subject
    # Message-ID e Date NON vengono aggiunti automaticamente da EmailMessage:
    # la loro assenza è un forte segnale di spam per Gmail/Outlook.
    message["Message-ID"] = make_msgid(domain=sender_domain)
    message["Date"] = formatdate(localtime=True)
    for key, value in (headers or {}).items():
        message[_sanitize_header(key)] = _sanitize_header(value)
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    # Porta 465 = TLS implicito; 587 (o altre) = STARTTLS.
    use_tls = settings.smtp_port == 465
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        use_tls=use_tls,
        start_tls=not use_tls,
        timeout=_TIMEOUT_SECONDS,
    )
    return True


async def _send_via_resend(
    settings: Settings,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> bool:
    payload: dict = {
        "from": settings.email_from,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    if headers:
        payload["headers"] = {
            _sanitize_header(k): _sanitize_header(v) for k, v in headers.items()
        }
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json=payload,
        )
    if resp.status_code >= 400:
        logger.error("Resend ha rifiutato l'invio (%s): %s", resp.status_code, resp.text[:300])
        return False
    return True


async def _dispatch(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> bool:
    """Invia con il provider configurato (SMTP → Resend → log). Mai raise.
    Ogni tentativo viene loggato, riuscito o meno: è il punto di verità
    quando "le email non arrivano". `headers` = intestazioni extra
    (es. List-Unsubscribe), sanificate contro l'header injection."""
    settings = get_settings()
    subject = _sanitize_header(subject)
    try:
        if settings.smtp_host:
            sent = await _send_via_smtp(
                settings, to_email, subject, html_body, text_body, headers
            )
            logger.info("Email inviata via SMTP a %s (%r)", to_email, subject)
            return sent
        if settings.resend_api_key:
            sent = await _send_via_resend(
                settings, to_email, subject, html_body, text_body, headers
            )
            if sent:
                logger.info("Email inviata via Resend a %s (%r)", to_email, subject)
            return sent
    except Exception as exc:
        logger.error("Invio email a %s fallito: %s", to_email, exc)
        return False
    logger.info("[email dev fallback] to=%s subject=%r", to_email, subject)
    return True


async def send_confirmation_email(to_email: str, cta_url: str) -> bool:
    """Email di conferma indirizzo dopo la registrazione.

    Il link porta alla pagina dove si sceglie ANCHE la password: la
    registrazione non la raccoglie più (anti-enumerazione — vedi
    auth_service.register), quindi l'account resta inutilizzabile finché chi
    possiede davvero la casella non completa da qui.
    """
    html_body = _branded_html(
        "Completa la tua registrazione",
        [
            "Benvenuto su BandoFit! Per attivare il tuo account conferma il tuo "
            "indirizzo email e scegli la password con il pulsante qui sotto.",
        ],
        "Completa la registrazione",
        cta_url,
        "Se non ti sei registrato tu su BandoFit puoi ignorare questa email.",
    )
    text = (
        "Benvenuto su BandoFit! Completa la registrazione (conferma dell'indirizzo e "
        f"scelta della password) aprendo questo link:\n\n{cta_url}\n\n"
        "Se non ti sei registrato tu, ignora questa email."
    )
    return await _dispatch(to_email, "Completa la tua registrazione — BandoFit", html_body, text)


async def send_account_exists_email(to_email: str, login_url: str, recovery_url: str) -> bool:
    """Tentativo di registrazione su un indirizzo GIÀ registrato e confermato.

    È il canale che sostituisce il vecchio 409: l'esistenza dell'account non può
    più trapelare nella risposta HTTP, quindi la diciamo a chi possiede la
    casella — l'unico che ha diritto di saperlo.
    """
    html_body = _branded_html(
        "Hai già un account BandoFit",
        [
            "Qualcuno — probabilmente tu — ha appena provato a creare un account con "
            "questo indirizzo. Non ne abbiamo creato uno nuovo: ne hai già uno.",
            "Se sei stato tu, accedi pure dal pulsante qui sotto. Non ricordi la "
            f'password? <a href="{recovery_url}" style="color:#1E5EFF">Puoi reimpostarla</a>.',
        ],
        "Vai al login",
        login_url,
        "Se non sei stato tu, puoi ignorare questa email: il tuo account e la tua "
        "password non sono stati toccati.",
    )
    text = (
        "Hai provato a registrarti su BandoFit, ma esiste già un account con questo "
        f"indirizzo. Accedi qui:\n\n{login_url}\n\n"
        f"Non ricordi la password? Reimpostala qui:\n\n{recovery_url}\n\n"
        "Se non sei stato tu, ignora questa email: nulla è stato modificato."
    )
    return await _dispatch(to_email, "Hai già un account — BandoFit", html_body, text)


async def send_account_pending_email(to_email: str, cta_url: str) -> bool:
    """Tentativo di registrazione su un indirizzo già registrato ma NON confermato.

    La CTA porta al form di richiesta di un nuovo link, non a un link pronto:
    emettere qui un token invaliderebbe quello che la persona ha già in casella
    (token_service.issue tiene valido un solo link per volta), e siccome questa
    email parte su richiesta di un anonimo, sarebbe un modo per sabotare la
    conferma altrui.
    """
    html_body = _branded_html(
        "Il tuo account è in attesa di conferma",
        [
            "Hai già iniziato la registrazione su BandoFit con questo indirizzo, ma "
            "non l'hai ancora confermata.",
            "Richiedi un nuovo link dal pulsante qui sotto: confermerai l'indirizzo e "
            "sceglierai la password per accedere.",
        ],
        "Completa la registrazione",
        cta_url,
        "Se non sei stato tu, puoi ignorare questa email: non è stato creato nessun "
        "nuovo account.",
    )
    text = (
        "Hai già una registrazione in attesa di conferma su BandoFit. Richiedi un "
        f"nuovo link di conferma qui:\n\n{cta_url}\n\n"
        "Se non sei stato tu, ignora questa email."
    )
    # Subject diverso da send_confirmation_email di proposito: con lo stesso
    # mittente e lo stesso oggetto, Gmail le raggrupperebbe nella stessa
    # conversazione — e chi si re-registra si troverebbe due messaggi
    # indistinguibili, uno col link buono e uno che rimanda al form.
    return await _dispatch(
        to_email, "Hai una registrazione da completare — BandoFit", html_body, text
    )


async def send_recovery_email(to_email: str, cta_url: str) -> bool:
    """Email con il link per reimpostare la password."""
    html_body = _branded_html(
        "Reimposta la tua password",
        [
            "Abbiamo ricevuto una richiesta di reimpostazione della password per il "
            "tuo account BandoFit. Il link vale una sola volta e per un tempo limitato.",
        ],
        "Reimposta la password",
        cta_url,
        "Se non hai richiesto tu la reimpostazione puoi ignorare questa email: "
        "la tua password resterà invariata.",
    )
    text = (
        "Reimposta la password del tuo account BandoFit aprendo questo link:\n\n"
        f"{cta_url}\n\nSe non l'hai richiesto tu, ignora questa email."
    )
    return await _dispatch(to_email, "Reimposta la tua password — BandoFit", html_body, text)


async def send_consulting_request_email(to_email: str, bando_titolo: str, cta_url: str) -> bool:
    """Evento 1 — a ogni progettista: nuova richiesta di consulto nel pool."""
    titolo = html.escape(bando_titolo)
    html_body = _branded_html(
        "Nuova richiesta di consulto",
        [
            f"Un'azienda ha richiesto un consulto sul bando <strong>{titolo}</strong>.",
            "Trovi i dettagli e l'AI-check nella sezione richieste: se il caso rientra "
            "nelle tue competenze, invia una proposta.",
        ],
        "Vedi le richieste",
        cta_url,
        "Ricevi questa email perché sei un progettista di BandoFit.",
    )
    text = (
        f"Un'azienda ha richiesto un consulto sul bando «{bando_titolo}».\n\n"
        f"Vedi le richieste: {cta_url}"
    )
    return await _dispatch(to_email, "Nuova richiesta di consulto — BandoFit", html_body, text)


async def send_proposal_email(
    to_email: str, nome_progettista: str, bando_titolo: str, cta_url: str
) -> bool:
    """Evento 2 — al titolare: un progettista ha inviato una proposta.
    L'autore si presenta per nome e cognome (più umano del codice)."""
    autore = html.escape(nome_progettista)
    titolo = html.escape(bando_titolo)
    html_body = _branded_html(
        "Hai ricevuto una proposta di consulenza",
        [
            f"<strong>{autore}</strong> ti ha inviato una proposta per "
            f"il consulto sul bando <strong>{titolo}</strong>.",
            "Leggila dalla piattaforma: accettandola assegni la consulenza in via "
            "definitiva e puoi prenotare un appuntamento.",
        ],
        "Vedi la proposta",
        cta_url,
        "Ricevi questa email perché hai attivato il consulto esperto su questo bando.",
    )
    text = (
        f"{nome_progettista} ti ha inviato una proposta per il bando "
        f"«{bando_titolo}».\n\nVedi la proposta: {cta_url}"
    )
    return await _dispatch(
        to_email, "Hai ricevuto una proposta di consulenza — BandoFit", html_body, text
    )


async def send_booking_email(
    to_email: str,
    ragione_sociale: str,
    quando: str,
    cta_url: str,
    videocall_url: str | None = None,
) -> bool:
    """Evento 3 — al progettista: un cliente ha prenotato uno slot.
    `quando` arriva già formattato con il fuso dichiarato (ora italiana);
    `videocall_url` è la stanza Jitsi dedicata all'appuntamento."""
    azienda = html.escape(ragione_sociale)
    orario = html.escape(quando)
    paragraphs = [
        f"<strong>{azienda}</strong> ha prenotato una consulenza con te "
        f"il <strong>{orario}</strong>.",
        "Trovi i dettagli dell'azienda e del bando nella tua area progettista.",
    ]
    if videocall_url:
        link = html.escape(videocall_url, quote=True)
        paragraphs.insert(
            1,
            f'La consulenza si terrà in videochiamata: <a href="{link}">{link}</a> '
            "(si apre dal browser, senza installare nulla).",
        )
    html_body = _branded_html(
        "Nuova consulenza prenotata",
        paragraphs,
        "Vedi la consulenza",
        cta_url,
        "Ricevi questa email perché il cliente ha scelto uno dei tuoi slot di disponibilità.",
    )
    text = (
        f"{ragione_sociale} ha prenotato una consulenza con te il {quando}.\n\n"
        + (f"Videochiamata: {videocall_url}\n\n" if videocall_url else "")
        + f"Vedi la consulenza: {cta_url}"
    )
    return await _dispatch(to_email, "Nuova consulenza prenotata — BandoFit", html_body, text)


async def send_booking_confirmation_email(
    to_email: str, quando: str, videocall_url: str | None, cta_url: str
) -> bool:
    """Conferma della prenotazione al CLIENTE: orario + link videochiamata.
    Il link Jitsi viaggia solo via email (mai nelle notifiche conservate)."""
    orario = html.escape(quando)
    paragraphs = [
        f"Il tuo appuntamento di consulenza è confermato per il <strong>{orario}</strong>.",
    ]
    if videocall_url:
        link = html.escape(videocall_url, quote=True)
        paragraphs.append(
            "All'orario dell'appuntamento avvia la videochiamata da questo link: "
            f'<a href="{link}">{link}</a> (si apre dal browser, senza installare nulla).'
        )
    html_body = _branded_html(
        "Appuntamento confermato",
        paragraphs,
        "Vedi la consulenza",
        cta_url,
        "Ricevi questa email perché hai prenotato una consulenza su BandoFit.",
    )
    text = (
        f"Il tuo appuntamento di consulenza è confermato per il {quando}.\n\n"
        + (f"Videochiamata: {videocall_url}\n\n" if videocall_url else "")
        + f"Vedi la consulenza: {cta_url}"
    )
    return await _dispatch(
        to_email, "Appuntamento di consulenza confermato — BandoFit", html_body, text
    )


async def send_assignment_email(
    to_email: str, ragione_sociale: str, bando_titolo: str, cta_url: str
) -> bool:
    """Evento 4 — al progettista: la sua proposta è stata accettata."""
    azienda = html.escape(ragione_sociale)
    titolo = html.escape(bando_titolo)
    html_body = _branded_html(
        "Ti è stata assegnata una consulenza",
        [
            f"<strong>{azienda}</strong> ha accettato la tua proposta per il bando "
            f"<strong>{titolo}</strong>: la consulenza è assegnata a te in via definitiva.",
            "Da ora hai accesso completo ai dati dell'azienda e al suo dossier "
            "certificato dalla tua area progettista.",
        ],
        "Vedi la consulenza",
        cta_url,
        "Ricevi questa email perché la tua proposta è stata accettata dal cliente.",
    )
    text = (
        f"{ragione_sociale} ha accettato la tua proposta per il bando «{bando_titolo}».\n\n"
        f"Vedi la consulenza: {cta_url}"
    )
    return await _dispatch(
        to_email, "Ti è stata assegnata una consulenza — BandoFit", html_body, text
    )


async def send_family_invitation_email(
    to_email: str,
    parent_display_name: str,
    denominazione: str,
    cta_url: str | None = None,
) -> bool:
    """Invia (best-effort) l'email di invito famiglia.

    Ritorna True se l'invio è andato a buon fine, False altrimenti.
    Default: link al profilo (utenti esistenti, che accettano in-app);
    ``cta_url`` esplicito per i reinvii con link d'invito rigenerato.
    """
    settings = get_settings()
    cta_url = cta_url or f"{settings.frontend_url.rstrip('/')}/app/profilo"
    return await _dispatch(
        to_email,
        f"{parent_display_name} ti ha invitato su BandoFit",
        _invitation_html(parent_display_name, denominazione, cta_url),
        _plain_text(parent_display_name, denominazione, cta_url),
    )


def _format_eur(value: int | None) -> str | None:
    """Importo in euro con separatore migliaia italiano («1.500.000 €»)."""
    if value is None:
        return None
    return f"{value:,.0f}".replace(",", ".") + " €"


def _euro_da_cents(cents: int) -> str:
    """Importo transazionale (centesimi) → «1.234,56 €» stile italiano."""
    intero, resto = divmod(cents, 100)
    return f"{intero:,.0f}".replace(",", ".") + f",{resto:02d} €"


async def send_promemoria_rinnovo_email(
    to_email: str, piano: str, importo_cents: int, scadenza: str,
    auto: bool, cta_url: str,
) -> bool:
    """Preavviso di rinnovo. Con addebito automatico (auto=True) è l'avviso
    contrattuale ≥7 giorni; senza, è il promemoria di ri-acquisto."""
    importo = _euro_da_cents(importo_cents)
    if auto:
        heading = "Il tuo abbonamento si rinnova a breve"
        paragrafi = [
            f"Il <strong>{html.escape(piano)}</strong> si rinnoverà automaticamente "
            f"il <strong>{html.escape(scadenza)}</strong>.",
            f"Addebiteremo <strong>{importo}</strong> (IVA inclusa dove prevista) sul "
            "metodo di pagamento che hai salvato.",
            "Se non vuoi rinnovare puoi disdire quando vuoi dalla tua area "
            "abbonamento: nessun addebito verrà effettuato.",
        ]
        cta = "Gestisci l'abbonamento"
        footer = "Ti scriviamo con almeno 7 giorni di anticipo, come da nostri termini."
    else:
        heading = "Il tuo abbonamento sta per scadere"
        paragrafi = [
            f"Il <strong>{html.escape(piano)}</strong> scadrà il "
            f"<strong>{html.escape(scadenza)}</strong>.",
            f"Per continuare senza interruzioni rinnova ora ({importo}, IVA inclusa "
            "dove prevista): alla scadenza, senza rinnovo, tornerai al piano Gratuito.",
        ]
        cta = "Rinnova ora"
        footer = "Puoi rinnovare anche più avanti, ma dopo la scadenza il piano tornerà Gratuito."
    html_body = _branded_html(heading, paragrafi, cta, cta_url, footer)
    text = f"{heading}. {piano}, {importo}, scadenza {scadenza}. {cta_url}"
    return await _dispatch(to_email, f"{heading} — BandoFit", html_body, text)


async def send_pagamento_fallito_email(
    to_email: str, piano: str, motivo: str | None, scadenza_grazia: str, cta_url: str,
) -> bool:
    """Addebito di rinnovo non riuscito: invito a pagare/cambiare carta entro
    la grazia."""
    dettaglio = {
        "insufficient_funds": "Fondi insufficienti sulla carta.",
        "expired_card": "La carta risulta scaduta.",
        "do_not_honour": "La banca ha rifiutato l'addebito.",
    }.get(motivo or "", "L'addebito non è andato a buon fine.")
    html_body = _branded_html(
        "Non siamo riusciti a rinnovare il tuo abbonamento",
        [
            f"{html.escape(dettaglio)} Il tuo <strong>{html.escape(piano)}</strong> resta "
            f"attivo fino al <strong>{html.escape(scadenza_grazia)}</strong>.",
            "Aggiorna il metodo di pagamento o completa l'addebito manualmente entro "
            "quella data per non perdere il piano.",
        ],
        "Paga ora o cambia carta",
        cta_url,
        "Dopo questa data, senza pagamento, l'abbonamento tornerà al piano Gratuito.",
    )
    text = (
        f"Rinnovo non riuscito per {piano}. {dettaglio} Attivo fino al "
        f"{scadenza_grazia}. Paga o cambia carta: {cta_url}"
    )
    return await _dispatch(to_email, "Rinnovo non riuscito — BandoFit", html_body, text)


async def send_downgrade_email(to_email: str, cta_url: str) -> bool:
    """Il piano è tornato Gratuito (grazia scaduta o mancato rinnovo)."""
    html_body = _branded_html(
        "Il tuo abbonamento è tornato Gratuito",
        [
            "Non avendo ricevuto il rinnovo, il tuo abbonamento è tornato al piano "
            "Gratuito. I tuoi dati restano al loro posto.",
            "Puoi riattivare un piano superiore quando vuoi.",
        ],
        "Scegli un piano",
        cta_url,
        "Se pensi si tratti di un errore, contattaci: siamo qui per aiutarti.",
    )
    text = f"Il tuo abbonamento è tornato Gratuito. Riattiva quando vuoi: {cta_url}"
    return await _dispatch(to_email, "Abbonamento tornato Gratuito — BandoFit", html_body, text)


async def send_ricevuta_pagamento_email(
    to_email: str, descrizione: str, totale_cents: int, cta_url: str,
) -> bool:
    """Conferma di pagamento riuscito (la fattura vera arriva via SDI)."""
    html_body = _branded_html(
        "Pagamento ricevuto, grazie!",
        [
            f"Abbiamo registrato il tuo pagamento di <strong>{_euro_da_cents(totale_cents)}</strong> "
            f"per <strong>{html.escape(descrizione)}</strong>.",
            "Trovi il dettaglio nella sezione «I tuoi acquisti». La fattura elettronica "
            "verrà emessa e ti sarà recapitata a breve.",
        ],
        "Vedi i tuoi acquisti",
        cta_url,
        "Conserva questa email come promemoria: il documento fiscale è la fattura elettronica.",
    )
    text = (
        f"Pagamento ricevuto: {_euro_da_cents(totale_cents)} per {descrizione}. "
        f"Dettaglio: {cta_url}"
    )
    return await _dispatch(to_email, "Pagamento ricevuto — BandoFit", html_body, text)


def _digest_card(bando: dict) -> str:
    """Card HTML di un bando nel digest. Tutti i dati catalogo sono escapati
    qui (il template li tratta come HTML già pronto)."""
    titolo = html.escape(bando.get("titolo") or "Bando senza titolo")
    url = html.escape(bando["url"], quote=True)
    righe: list[str] = []
    if bando.get("ente_erogatore"):
        righe.append(html.escape(bando["ente_erogatore"]))
    importo = _format_eur(bando.get("importo_eur"))
    importo_max = _format_eur(bando.get("importo_max_eur"))
    if importo and importo_max:
        righe.append(f"Dotazione {importo} · fino a {importo_max} per progetto")
    elif importo:
        righe.append(f"Dotazione {importo}")
    elif importo_max:
        righe.append(f"Fino a {importo_max} per progetto")
    dettagli = "<br>".join(righe)

    giorni = bando.get("giorni_alla_scadenza")
    if bando.get("scadenza_label") is None:
        scadenza = '<span style="color:#64748b">Senza scadenza dichiarata</span>'
    else:
        label = html.escape(bando["scadenza_label"])
        scadenza = f'<strong style="color:#b45309">Scadenza: {label}</strong>'
        if giorni is not None and giorni <= 14:
            scadenza += (
                ' <span style="background:#fef3c7;color:#92400e;border-radius:6px;'
                'padding:1px 8px;font-size:12px;font-weight:600">'
                f"scade tra {giorni} giorni</span>"
                if giorni > 0
                else ' <span style="background:#fee2e2;color:#b91c1c;border-radius:6px;'
                'padding:1px 8px;font-size:12px;font-weight:600">scade oggi</span>'
            )

    motivo = html.escape(bando.get("motivo") or "")
    riga_motivo = (
        f'<span style="font-size:13px;color:#64748b">Perché lo vedi: {motivo}</span>'
        if motivo
        else ""
    )
    return (
        '<span style="display:block;border:1px solid #e2e8f0;border-radius:10px;'
        'padding:14px 16px;margin:0 0 4px">'
        f'<a href="{url}" style="font-size:16px;font-weight:600;color:#1E5EFF;'
        f'text-decoration:none">{titolo}</a><br>'
        f'<span style="font-size:13px;color:#475569">{dettagli}</span>'
        f'{"<br>" if dettagli else ""}'
        f'<span style="font-size:13px">{scadenza}</span><br>'
        f"{riga_motivo}"
        "</span>"
    )


def _section_header(azienda: str) -> str:
    """Intestazione di sezione (ragione sociale) nel digest multi-azienda."""
    nome = html.escape(azienda)
    return (
        '<span style="display:block;margin:18px 0 6px;font-size:15px;'
        "font-weight:700;color:#0f172a;border-bottom:2px solid #e2e8f0;"
        f'padding-bottom:4px">{nome}</span>'
    )


def _render_digest(
    sezioni: list[dict], *, cta_url: str, unsubscribe_url: str, multi: bool
) -> tuple[str, str, str]:
    """Costruisce (subject, html, text) del digest. `sezioni` = lista di
    `{"azienda": str | None, "bandi": [...]}`. Con `multi=False` (una sola
    sezione senza azienda: utenti non-Advisor) il testo è identico al digest
    classico; con `multi=True` ogni sezione porta l'intestazione con la
    ragione sociale."""
    tutti = [b for s in sezioni for b in s["bandi"]]
    quanti = len(tutti)
    unsubscribe_href = html.escape(unsubscribe_url, quote=True)

    if multi:
        heading = (
            "C'è un nuovo bando per le tue aziende"
            if quanti == 1
            else f"Ci sono {quanti} nuovi bandi per le tue aziende"
        )
        intro = "Ecco i nuovi bandi compatibili, raggruppati per azienda:"
        subject = (
            "Un nuovo bando per le tue aziende — BandoFit"
            if quanti == 1
            else f"{quanti} nuovi bandi per le tue aziende — BandoFit"
        )
        corpo: list[str] = []
        for sezione in sezioni:
            if not sezione["bandi"]:
                continue
            if sezione.get("azienda"):
                corpo.append(_section_header(sezione["azienda"]))
            corpo.extend(_digest_card(bando) for bando in sezione["bandi"])
    else:
        heading = (
            "C'è un nuovo bando per la tua azienda"
            if quanti == 1
            else f"Ci sono {quanti} nuovi bandi per la tua azienda"
        )
        intro = (
            "In base al profilo della tua azienda, questo bando sembra compatibile con te:"
            if quanti == 1
            else "In base al profilo della tua azienda, questi bandi sembrano compatibili con te:"
        )
        subject = (
            "Un nuovo bando per la tua azienda — BandoFit"
            if quanti == 1
            else f"{quanti} nuovi bandi per la tua azienda — BandoFit"
        )
        corpo = [_digest_card(bando) for bando in tutti]

    paragraphs = [
        intro,
        *corpo,
        f'<span style="font-size:12px;color:#94a3b8">Non vuoi più ricevere questi avvisi? '
        f'<a href="{unsubscribe_href}" style="color:#64748b">Disattivali con un clic</a> '
        "o dalle Preferenze della piattaforma.</span>",
    ]
    html_body = _branded_html(
        heading,
        paragraphs,
        "Vedi tutti i bandi",
        cta_url,
        "Ricevi questa email perché il tuo piano include gli avvisi sui nuovi bandi.",
    )

    righe_testo = []
    for sezione in sezioni:
        if not sezione["bandi"]:
            continue
        if multi and sezione.get("azienda"):
            righe_testo.append(f"[{sezione['azienda']}]")
        for bando in sezione["bandi"]:
            riga = f"- {bando.get('titolo') or 'Bando'}"
            if bando.get("scadenza_label"):
                riga += f" (scadenza {bando['scadenza_label']})"
            riga += f"\n  {bando['url']}"
            if bando.get("motivo"):
                riga += f"\n  Perché lo vedi: {bando['motivo']}"
            righe_testo.append(riga)
    text = (
        f"{heading}.\n\n"
        + "\n\n".join(righe_testo)
        + f"\n\nTutti i bandi: {cta_url}"
        + f"\n\nPer non ricevere più questi avvisi: {unsubscribe_url}"
    )
    return subject, html_body, text


async def send_bandi_digest_email(
    to_email: str,
    bandi: list[dict],
    cta_url: str,
    unsubscribe_url: str,
) -> bool:
    """Digest giornaliero dei nuovi bandi compatibili di UNA azienda. `bandi` =
    dict con titolo, ente_erogatore, importo_eur, importo_max_eur,
    scadenza_label, giorni_alla_scadenza, motivo, url — già risolti dal
    chiamante. Include il link di disiscrizione a un clic e gli header RFC
    8058. È la forma per i non-Advisor (digest classico)."""
    subject, html_body, text = _render_digest(
        [{"azienda": None, "bandi": bandi}],
        cta_url=cta_url,
        unsubscribe_url=unsubscribe_url,
        multi=False,
    )
    return await _dispatch(
        to_email,
        subject,
        html_body,
        text,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )


async def send_bandi_digest_email_multi(
    to_email: str,
    sezioni: list[dict],
    cta_url: str,
    unsubscribe_url: str,
) -> bool:
    """Digest multi-azienda (Advisor): una sola email con una sezione per
    azienda. `sezioni` = lista di `{"azienda": str, "bandi": [...]}` (già
    risolte). Stessi header RFC 8058 del digest classico."""
    subject, html_body, text = _render_digest(
        sezioni,
        cta_url=cta_url,
        unsubscribe_url=unsubscribe_url,
        multi=True,
    )
    return await _dispatch(
        to_email,
        subject,
        html_body,
        text,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
