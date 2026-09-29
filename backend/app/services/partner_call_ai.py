"""Job AI della call di partenariato: posizioni e testi (WP5, T6-T7).

Stesso schema della bozza AI del profilo (WP4): la prenotazione fail-closed la
fa il servizio (`fn_partner_call_ai_prenota`: limite per call e per servizio,
limite per titolare e budget del gruppo `altri`), poi il job gira in
background (`esegui_job`, non solleva MAI salvo la cancellazione del task) e
chiude job ed esecuzione in UNA transazione (`fn_partner_call_ai_concludi`,
che scrive il job solo se è ancora quello di questa esecuzione e in corso:
il failsafe o un nuovo job vincono). Il consumo si registra SOLO se ha chiuso
lui l'esecuzione, una volta: altrimenti l'ha chiusa (e registrata) il failsafe.

Costi come il WP3/WP4: modello non chiamato → 0 esplicito; risposta arrivata
ma inutilizzabile → max(reale, riserva); timeout → riserva; errore di rete
senza usage → costo ignoto (None: la riserva resta nel budget).

Post-elaborazione DETERMINISTICA (le proposte non si salvano mai da sole):
- posizioni: codici del vocabolario, divisioni ATECO a due cifre, regioni
  mappate sulla lookup del catalogo, paesi ISO, quote e numero nei range,
  riferimenti ai requisiti per etichetta (quelli ignoti si scartano), testi
  senza contatti né identificativi dell'azienda; avvisi sulle quote (somma
  oltre il 100%, minimi e massimi per partner) e sul numero di partner;
- testi: `anonimizza` con gli identificativi dell'azienda (le call sono
  anonime), troncati alle lunghezze della DDL, rilievi rimasti (avvisi).

Log: mai testi, solo id della call e codici.
"""

import asyncio
import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AiNotConfiguredError, AiTimeoutError, AiUpstreamError, AppError
from app.schemas.partner_call import (
    MAX_COMPETENZE_POSIZIONE,
    MAX_DESCRIZIONE_PUBBLICA,
    MAX_DIVISIONI_POSIZIONE,
    MAX_PAESI_POSIZIONE,
    MAX_POSIZIONI,
    MAX_PROFILO_IDEALE,
    MAX_REGIONI_POSIZIONE,
    MAX_REQUISITI_POSIZIONE,
    MAX_TIPI_POSIZIONE,
    NUMERO_MAX_POSIZIONE,
    TITOLO_MAX,
    TITOLO_MIN,
    TITOLO_POSIZIONE_MAX,
    TITOLO_POSIZIONE_MIN,
    PosizioneProposta,
    PropostaPosizioniOut,
    PropostaTestiOut,
    RegoleCallSnapshot,
    RilievoOut,
    paese_iso2,
)
from app.services import partenariato_vocabolario as voc
from app.services.ai_prezzi import costo_cents, stima_cents
from app.services.openapi_service import record_usage
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    Identificativi,
    anonimizza,
    normalizza,
    trova_rilievi,
)
from app.services.partner_call_prompts import BozzaTestiCall, PropostaPosizioni
from app.services.partner_profilo_pubblico import CLASSI_DIMENSIONALI

logger = logging.getLogger("bandofit.partenariati")

SERVIZIO_POSIZIONI = "partner_call_posizioni"
SERVIZIO_TESTI = "partner_call_testi"
SERVIZI = (SERVIZIO_POSIZIONI, SERVIZIO_TESTI)
MAX_MOTIVAZIONE = 300
# Tempo massimo per chiudere il job quando il task viene cancellato
# (spegnimento del processo): poi ci pensa il failsafe.
CHIUSURA_SU_CANCELLAZIONE_SECONDI = 5.0

# Codice di errore del job → messaggio per l'utente (JobAiOut.errore).
MESSAGGI_ERRORE = {
    "timeout": "La proposta ha richiesto troppo tempo: riprova più tardi",
    "interrotta": "La preparazione della proposta si è interrotta: riprova",
    "ai_risposta_non_valida": "La proposta non è venuta bene: riprova più tardi",
    "ai_non_disponibile": (
        "Il servizio che prepara la proposta non è disponibile: riprova più tardi"
    ),
}
MESSAGGIO_ERRORE = "Non siamo riusciti a preparare la proposta: riprova più tardi"


def messaggio_errore(codice: str | None) -> str:
    return MESSAGGI_ERRORE.get(codice or "", MESSAGGIO_ERRORE)


def stima_riserva_cents(system: str, messaggio: str, schema: str) -> int:
    """Riserva al caso peggiore: prompt di sistema + messaggio + schema e
    tutto l'output consentito (2,5 caratteri per token)."""
    settings = get_settings()
    return stima_cents(
        settings.partenariato_ai_model,
        len(system) + len(messaggio) + len(schema),
        settings.partner_call_ai_max_tokens,
    )


# ------------------------------------------------------------ post: utilità


def _tronca(testo: str, massimo: int) -> str:
    """Taglia a `massimo` caratteri, all'ultimo spazio se possibile."""
    if len(testo) <= massimo:
        return testo
    taglio = testo[: massimo - 1]
    spazio = taglio.rfind(" ")
    if spazio > massimo // 2:
        taglio = taglio[:spazio]
    return taglio.rstrip() + "…"


def _pulisci(testo: str | None, ident: Identificativi | None, massimo: int) -> tuple[str, int]:
    """Testo del modello senza contatti né identificativi, spazi ai bordi
    tolti, troncato. Ritorna (testo, quanti brani rimossi)."""
    pulito, rimossi = anonimizza((testo or "").strip(), ident)
    return _tronca(pulito.strip(), massimo), len(rimossi)


def _percento(valore: Decimal) -> str:
    """«110», «25,5»: niente zeri inutili né notazione esponenziale."""
    return format(valore.quantize(Decimal("0.01")).normalize(), "f").replace(".", ",")


def _dedup(valori) -> list:
    return list(dict.fromkeys(valori))


def _quota(valore: float | None) -> Decimal | None:
    if valore is None or not isinstance(valore, (int, float)) or isinstance(valore, bool):
        return None
    if not math.isfinite(valore) or valore <= 0 or valore > 100:
        return None
    return Decimal(str(round(float(valore), 2)))


# ------------------------------------------------------------ post: posizioni


def post_posizioni(
    proposta: PropostaPosizioni,
    *,
    etichette: Mapping[str, str],
    regioni: Mapping[int, str],
    ident: Identificativi | None,
    ruolo_creatore: str | None,
    quota_creatore: Decimal | float | str | None,
    regole: RegoleCallSnapshot | None,
) -> dict:
    """Proposta di posizioni post-validata → JSON di `PropostaPosizioniOut`.

    - `etichette`: etichetta del requisito salvato («A») → suo id;
    - `regioni`: id → nome della lookup del catalogo (vuota = regioni scartate).
    """
    per_nome = {normalizza(nome): id_ for id_, nome in regioni.items()}
    posizioni: list[PosizioneProposta] = []
    avvisi: list[str] = []
    riferimenti_ignoti = regioni_ignote = scartate = quote_fuori = 0
    # Codici fuori vocabolario (lo schema del modello non li vincola più): si
    # scartano, ma una lista svuotata vuol dire «nessun vincolo» e la
    # posizione si allarga, quindi lo scarto si segnala.
    tipi_ignoti = competenze_ignote = 0

    for voce in proposta.posizioni[:MAX_POSIZIONI]:
        titolo, _ = _pulisci(" ".join(voce.titolo.split()), ident, TITOLO_POSIZIONE_MAX)
        if len(titolo) < TITOLO_POSIZIONE_MIN or titolo == "[rimosso]":
            scartate += 1
            continue
        ids_regioni: list[int] = []
        for nome in voce.regioni:
            id_ = per_nome.get(normalizza(nome))
            if id_ is None:
                regioni_ignote += 1
            elif id_ not in ids_regioni:
                ids_regioni.append(id_)
        ids_regioni = ids_regioni[:MAX_REGIONI_POSIZIONE]
        modalita = voce.territorio_modalita
        if not ids_regioni:
            modalita = "qualsiasi"
        elif modalita == "qualsiasi":
            # Regioni senza modalità: la meno escludente (sede da aprire).
            modalita = "sede_entro_erogazione"
        paesi: list[str] = []
        for codice in voce.paesi:
            try:
                iso = paese_iso2(codice)
            except AppError:
                continue
            if iso not in paesi:
                paesi.append(iso)
        requisiti_ids = []
        for etichetta in voce.requisiti:
            id_ = etichette.get(etichetta.strip())
            if id_ is None:
                riferimenti_ignoti += 1
            elif id_ not in requisiti_ids:
                requisiti_ids.append(id_)
        quota = _quota(voce.quota_ipotizzata_pct)
        if voce.quota_ipotizzata_pct is not None and quota is None:
            quote_fuori += 1
        ruolo = voce.ruolo
        if ruolo == "capofila" and ruolo_creatore == "capofila":
            ruolo = "partner"
            avvisi.append(
                f"«{titolo}»: il capofila sei tu, la posizione è proposta come partner"
            )
        motivazione, _ = _pulisci(" ".join(voce.motivazione.split()), ident, MAX_MOTIVAZIONE)
        tipi = [t for t in voce.tipi_soggetto if t in voc.TIPI_SOGGETTO]
        competenze = [c for c in voce.competenze if c in voc.COMPETENZE]
        tipi_ignoti += sum(1 for t in voce.tipi_soggetto if t.strip()) - len(tipi)
        competenze_ignote += sum(1 for c in voce.competenze if c.strip()) - len(competenze)
        posizioni.append(
            PosizioneProposta(
                titolo=titolo,
                ruolo=ruolo,
                tipi_soggetto=_dedup(tipi)[:MAX_TIPI_POSIZIONE],
                competenze=_dedup(competenze)[:MAX_COMPETENZE_POSIZIONE],
                ateco_divisioni=_dedup(
                    d.strip() for d in voce.ateco_divisioni
                    if len(d.strip()) == 2 and d.strip().isdigit()
                )[:MAX_DIVISIONI_POSIZIONE],
                regioni=ids_regioni,
                territorio_modalita=modalita,
                paesi=paesi[:MAX_PAESI_POSIZIONE],
                dimensioni=_dedup(d for d in voce.dimensioni if d in CLASSI_DIMENSIONALI),
                quota_ipotizzata_pct=quota,
                numero=min(max(int(voce.numero), 1), NUMERO_MAX_POSIZIONE),
                requisiti_ids=requisiti_ids[:MAX_REQUISITI_POSIZIONE],
                motivazione=motivazione or None,
            )
        )

    if scartate:
        avvisi.append(f"{scartate} posizioni senza un titolo valido sono state scartate")
    if riferimenti_ignoti:
        avvisi.append(
            f"{riferimenti_ignoti} riferimenti a requisiti che non esistono sono stati scartati"
        )
    if regioni_ignote:
        avvisi.append(f"{regioni_ignote} regioni non riconosciute sono state scartate")
    if tipi_ignoti:
        avvisi.append(f"{tipi_ignoti} tipi di soggetto non riconosciuti sono stati scartati")
    if competenze_ignote:
        avvisi.append(f"{competenze_ignote} competenze non riconosciute sono state scartate")
    if quote_fuori:
        avvisi.append(f"{quote_fuori} quote fuori dall'intervallo 0-100% sono state tolte")
    avvisi.extend(_avvisi_composizione(posizioni, ruolo_creatore, quota_creatore, regole))
    return PropostaPosizioniOut(posizioni=posizioni, avvisi=avvisi).model_dump(mode="json")


def _avvisi_composizione(
    posizioni: list[PosizioneProposta],
    ruolo_creatore: str | None,
    quota_creatore: Decimal | float | str | None,
    regole: RegoleCallSnapshot | None,
) -> list[str]:
    """Avvisi deterministici su quote e numero di partner (mai bloccanti)."""
    avvisi: list[str] = []
    creatore = Decimal(str(quota_creatore)) if quota_creatore is not None else None
    somma = (creatore or Decimal(0)) + sum(
        (p.quota_ipotizzata_pct * p.numero for p in posizioni if p.quota_ipotizzata_pct),
        Decimal(0),
    )
    if somma > 100:
        avvisi.append(f"Le quote ipotizzate, con la tua, superano il 100% ({_percento(somma)}%)")
    if ruolo_creatore == "cerco_capofila" and posizioni and not any(
        p.ruolo == "capofila" for p in posizioni
    ):
        avvisi.append("Cerchi un capofila: aggiungi una posizione con ruolo capofila")
    if regole is None:
        return avvisi
    membri = 1 + sum(p.numero for p in posizioni)
    if regole.partner_min is not None and membri < regole.partner_min.valore:
        avvisi.append(
            f"Il bando chiede almeno {regole.partner_min.valore} partner: con queste posizioni "
            f"sareste in {membri}"
        )
    if regole.partner_max is not None and membri > regole.partner_max.valore:
        avvisi.append(
            f"Il bando ammette al massimo {regole.partner_max.valore} partner: con queste "
            f"posizioni sareste in {membri}"
        )
    for quota in regole.quote:
        if quota.ambito != "per_partner":
            continue
        valori = [("la tua quota", creatore)] + [
            (f"«{p.titolo}»", p.quota_ipotizzata_pct) for p in posizioni
        ]
        for nome, valore in valori:
            if valore is None:
                continue
            if quota.min_percentuale is not None and valore < Decimal(str(quota.min_percentuale)):
                avvisi.append(
                    f"{nome[0].upper()}{nome[1:]}: la quota è sotto il minimo per partner del "
                    f"bando ({quota.min_percentuale:g}%)"
                )
            if quota.max_percentuale is not None and valore > Decimal(str(quota.max_percentuale)):
                avvisi.append(
                    f"{nome[0].upper()}{nome[1:]}: la quota supera il massimo per partner del "
                    f"bando ({quota.max_percentuale:g}%)"
                )
    return avvisi


# ------------------------------------------------------------ post: testi

_CAMPI_TESTI = (
    ("titolo", "Il titolo", TITOLO_MAX),
    ("descrizione_pubblica", "La descrizione pubblica", MAX_DESCRIZIONE_PUBBLICA),
    ("profilo_partner_ideale", "Il profilo del partner ideale", MAX_PROFILO_IDEALE),
)


def post_testi(bozza: BozzaTestiCall, *, ident: Identificativi | None) -> dict:
    """Bozza dei testi post-validata → JSON di `PropostaTestiOut`: anonimizzata
    (contatti e identificativi dell'azienda tolti), troncata, con i rilievi
    rimasti (avvisi non bloccanti, per esempio i cognomi del registro)."""
    valori: dict[str, str | None] = {}
    rilievi: list[RilievoOut] = []
    avvisi: list[str] = []
    for campo, nome, massimo in _CAMPI_TESTI:
        testo, rimossi = _pulisci(getattr(bozza, campo), ident, massimo)
        valori[campo] = testo or None
        if rimossi:
            avvisi.append(
                f"{nome}: abbiamo tolto {rimossi} riferimenti non ammessi (contatti o dati che "
                "identificano l'azienda)"
            )
        for rilievo in trova_rilievi(testo, ident, anonima=True):
            rilievi.append(
                RilievoOut(
                    campo=campo,
                    tipo=rilievo.tipo,
                    estratto=rilievo.estratto,
                    bloccante=rilievo.bloccante,
                )
            )
    if valori["titolo"] is not None and len(valori["titolo"]) < TITOLO_MIN:
        avvisi.append(f"Il titolo proposto è troppo corto: servono almeno {TITOLO_MIN} caratteri")
    return PropostaTestiOut(**valori, rilievi=rilievi, avvisi=avvisi).model_dump(mode="json")


def etichetta_rilievo(tipo: str) -> str:
    return ETICHETTE_RILIEVO.get(tipo, "un dato non ammesso")


# ------------------------------------------------------------ esecuzione


@dataclass(frozen=True)
class RichiestaJob:
    """Tutto ciò che serve al job, raccolto al momento della prenotazione:
    il job non rilegge nulla prima della chiusura."""

    servizio: str
    call_id: str
    esecuzione_id: str
    company_id: str
    owner_id: str
    user_id: str
    system: str
    messaggio: str
    schema: type[BaseModel]
    riserva_cents: int
    prompt_version: int
    post: Callable[[BaseModel], dict]


@dataclass
class _Chiusura:
    """Una chiusura del job: job, esecuzione nel registro unico della spesa e
    riga del registro consumi da scrivere se l'esecuzione la chiude lei."""

    job_stato: str  # pronta | errore
    stato: str  # stato finale dell'esecuzione
    costo: int | None  # None = costo ignoto: la riserva resta nel budget
    costo_registro: int  # api_usage_events: mai ignoto (la riserva, al peggio)
    outcome: str  # success | error | timeout_unknown
    meta: dict
    proposta: dict | None = None
    job_errore: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None
    errore: str | None = None


@dataclass
class _Job:
    """Cosa sa il job, per la chiusura su cancellazione: l'usage della
    risposta, se la richiesta può essere partita e quali passi ha già
    TENTATO (nessuno si ripete)."""

    usage: object = None
    inviata: bool = False
    chiusura: _Chiusura | None = None
    esito: dict | None = None
    registrata: bool = False


async def _chiudi(primary, job: _Job, r: RichiestaJob, chiusura: _Chiusura) -> dict | None:
    """Chiusura ATOMICA (`fn_partner_call_ai_concludi`). Se non riesce
    restano in corso job ed esecuzione e li chiude il failsafe, che registra
    lui il consumo. Ritorna `{job_scritto, esecuzione_chiusa}` o None."""
    job.chiusura, job.esito = chiusura, None
    try:
        resp = await primary.rpc(
            "fn_partner_call_ai_concludi",
            {
                "p_call": r.call_id,
                "p_esecuzione_id": r.esecuzione_id,
                "p_servizio": r.servizio,
                "p_job_stato": chiusura.job_stato,
                "p_proposta": chiusura.proposta,
                "p_job_errore": chiusura.job_errore,
                "p_stato": chiusura.stato,
                "p_cost_cents": chiusura.costo,
                "p_input_tokens": chiusura.input_tokens,
                "p_output_tokens": chiusura.output_tokens,
                "p_model": chiusura.model,
                "p_errore": chiusura.errore,
            },
        ).execute()
    except Exception as exc:  # noqa: BLE001 — ci pensa il failsafe
        logger.error("call: chiusura del job %s non riuscita (call %s, %s)", r.servizio,
                     r.call_id, type(exc).__name__)
        return None
    job.esito = resp.data if isinstance(resp.data, dict) else {}
    return job.esito


async def _registra_chiusura(primary, job: _Job, r: RichiestaJob) -> None:
    """Registro consumi dell'ultima chiusura, SOLO se ha chiuso lei
    l'esecuzione e mai due volte (il flag si alza PRIMA dell'insert)."""
    chiusura, esito = job.chiusura, job.esito
    if job.registrata or chiusura is None or not esito or not esito.get("esecuzione_chiusa"):
        return
    job.registrata = True
    outcome, meta = chiusura.outcome, chiusura.meta
    if chiusura.job_stato == "pronta" and not esito.get("job_scritto"):
        # Il failsafe (o un nuovo job) ha chiuso il job durante la chiamata:
        # il risultato pagato va perso, la spesa no.
        outcome, meta = "error", {**meta, "esito": "superata"}
    await record_usage(
        primary,
        user_id=r.user_id,
        family_parent_id=r.owner_id,
        service=r.servizio,
        outcome=outcome,
        cost_cents=int(chiusura.costo_registro),
        meta=meta,
        provider="anthropic",
    )


async def esegui_job(primary, ai, r: RichiestaJob) -> str:
    """Chiamata al modello → post-elaborazione → chiusura atomica → registro
    consumi. Non solleva MAI (salvo la cancellazione del task, dopo aver
    chiuso e registrato ciò che mancava). Ritorna l'esito (pronta, superata,
    timeout, errore)."""
    settings = get_settings()
    modello = settings.partenariato_ai_model
    meta = {
        "company_profile_id": r.company_id,
        "call_id": r.call_id,
        "esecuzione_id": r.esecuzione_id,
        "model": modello,
        "prompt_version": r.prompt_version,
    }
    job = _Job()

    async def chiudi(chiusura: _Chiusura) -> dict | None:
        esito = await _chiudi(primary, job, r, chiusura)
        await _registra_chiusura(primary, job, r)
        return esito

    try:
        try:
            job.inviata = True  # la richiesta al modello può essere partita
            risposta, usage = await ai.genera(
                r.system,
                r.messaggio,
                r.schema,
                model=modello,
                max_tokens=settings.partner_call_ai_max_tokens,
                timeout=settings.partner_call_ai_timeout_seconds,
            )
            job.usage = usage
            costo = costo_cents(modello, usage.input_tokens, usage.output_tokens)
            proposta = r.post(risposta)
            esito = await chiudi(_Chiusura(
                job_stato="pronta", proposta=proposta, stato="conclusa", costo=costo,
                costo_registro=costo, outcome="success",
                meta={**meta, "esito": "pronta", "input_tokens": usage.input_tokens,
                      "output_tokens": usage.output_tokens},
                input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                model=modello,
            ))
            if esito is None:
                return "errore"  # restano in corso: failsafe
            if not esito.get("job_scritto"):
                logger.warning("call: proposta %s superata durante la generazione (call %s)",
                               r.servizio, r.call_id)
                return "superata"
            return "pronta"
        except AiTimeoutError:
            # Esito e addebito ignoti: si registra il caso peggiore, la riserva.
            await chiudi(_Chiusura(
                job_stato="errore", job_errore="timeout", stato="timeout",
                costo=r.riserva_cents, costo_registro=r.riserva_cents,
                outcome="timeout_unknown", meta={**meta, "esito": "timeout"}, model=modello,
                errore="timeout",
            ))
            return "timeout"
        except AiUpstreamError as exc:
            uso = exc.usage
            if uso is not None:
                # La risposta è arrivata (troncata o non valida): pagata.
                costo = max(costo_cents(modello, uso.input_tokens, uso.output_tokens),
                            r.riserva_cents)
                codice = "ai_risposta_non_valida"
            else:
                costo = None  # errore di rete o del provider: costo ignoto
                codice = "ai_non_disponibile"
            await chiudi(_Chiusura(
                job_stato="errore", job_errore=codice, stato="errore", costo=costo,
                costo_registro=r.riserva_cents if costo is None else costo, outcome="error",
                meta={**meta, "esito": "errore", "errore": codice, "costo_ignoto": costo is None},
                input_tokens=uso.input_tokens if uso else 0,
                output_tokens=uso.output_tokens if uso else 0, model=modello, errore=codice,
            ))
            return "errore"
        except Exception as exc:
            logger.error("call: proposta %s non riuscita (call %s, %s)", r.servizio, r.call_id,
                         type(exc).__name__)
            usage = job.usage
            if usage is not None:
                # Chiamata riuscita e pagata, guasto dopo: max(reale, riserva).
                costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                            r.riserva_cents)
            elif job.inviata and not isinstance(exc, AiNotConfiguredError):
                costo = None
            else:
                costo = 0  # il modello non è stato chiamato
            await chiudi(_Chiusura(
                job_stato="errore", job_errore="errore_interno", stato="errore",
                costo=costo, costo_registro=r.riserva_cents if costo is None else costo,
                outcome="error", meta={**meta, "esito": "errore", "costo_ignoto": costo is None},
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                model=modello if costo != 0 else None, errore="errore_interno",
            ))
            return "errore"
    except asyncio.CancelledError:
        # Task cancellato (spegnimento, deploy) in QUALUNQUE punto, anche
        # dentro un ramo d'errore: si completa ciò che manca, poi si rilancia.
        await _chiudi_su_cancellazione(primary, job, r, modello=modello, meta=meta)
        raise


async def _chiudi_su_cancellazione(
    primary, job: _Job, r: RichiestaJob, *, modello: str, meta: dict
) -> None:
    """Chiusura best-effort di un job il cui task è stato cancellato, con un
    tempo massimo, senza ripetere i passi già tentati: registro già scritto →
    niente; chiusura già tornata → si registra la sua; altrimenti si chiude
    come interrotta (la RPC è idempotente). Se non riesce, il failsafe."""
    if job.registrata:
        return
    usage = job.usage
    if usage is not None:
        costo = max(costo_cents(modello, usage.input_tokens, usage.output_tokens),
                    r.riserva_cents)
        outcome, tokens = "error", (usage.input_tokens, usage.output_tokens)
    elif job.inviata:
        # Chiamata forse partita e forse addebitata: come un timeout.
        costo, outcome, tokens = r.riserva_cents, "timeout_unknown", (0, 0)
    else:
        costo, outcome, tokens = 0, "error", (0, 0)
    try:
        async with asyncio.timeout(CHIUSURA_SU_CANCELLAZIONE_SECONDI):
            if job.esito is None:
                await _chiudi(primary, job, r, _Chiusura(
                    job_stato="errore", job_errore="interrotta", stato="interrotta",
                    costo=costo, costo_registro=costo, outcome=outcome,
                    meta={**meta, "esito": "interrotta"}, input_tokens=tokens[0],
                    output_tokens=tokens[1], model=modello if costo else None,
                    errore="interrotta",
                ))
            await _registra_chiusura(primary, job, r)
    except Exception:  # noqa: BLE001 — il failsafe chiuderà il job
        logger.warning("call: chiusura dopo la cancellazione non riuscita (call %s)", r.call_id)
