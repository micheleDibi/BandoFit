"""Bilanci per esercizio dell'azienda attiva (WP1).

- `persisti_import`: alla conferma dell'import registra le fonti `it_full`
  (l'esercizio di IT-full) e `it_advanced` (lo storico già pagato
  nell'anteprima). BEST-EFFORT: non fa mai fallire la conferma.
- `get_bilanci`: riga fusa per esercizio (company_financials), indicatori e
  fasce. Prima fa la RIMAPPATURA PIGRA e gratuita dai payload conservati se la
  versione del mapping è vecchia (anche il backfill delle aziende importate
  prima di WP1).
- `recupera_bilanci`: «Recupera i bilanci», l'unica chiamata a pagamento di
  questo modulo (IT-advanced, 0,10 €), con le stesse guardie dell'import.
- `chiama_it_advanced`: la chiamata IT-advanced condivisa da anteprima e
  recupero. Non solleva mai e annota il registro consumi su ogni esito.

La precedenza tra le fonti (xbrl > it_full > it_advanced, campo per campo) la
decide SOLO la RPC `fn_bilanci_registra_fonte`: qui si passano le righe
normalizzate e si legge il risultato. I payload grezzi (`advanced_raw`) non
escono mai verso il client e la P.IVA non finisce in chiaro in log, registro
consumi o audit.
"""

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from postgrest.exceptions import APIError

from app.clients.openapi import (
    OpenapiInvalidIdError,
    OpenapiNessunDatoError,
    OpenapiNonInviataError,
)
from app.core.config import get_settings
from app.core.errors import (
    AppError,
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
    UpstreamError,
)
from app.core.privacy import mask_piva
from app.schemas.bilanci import BilanciOut, EsercizioOut, FasceOut, IndicatoreOut
from app.services import openapi_service
from app.services.bilanci_indicatori import (
    EsercizioBilancio,
    calcola_fasce,
    calcola_indicatori,
)
from app.services.bilanci_mapping import (
    CAMPI_BILANCIO,
    MAPPING_BILANCI_VERSIONE,
    RANGO_FONTE,
    RigaFonte,
    a_payload_rpc,
    da_it_advanced,
    da_it_full,
    ids_advanced,
)
from app.services.openapi_mapping import forma_giuridica_codice, validate_partita_iva

logger = logging.getLogger("bandofit.bilanci")

# Recupero dei bilanci: mint del gruppo `advanced` + GET da 25 s + scritture
# stanno ampiamente nel TTL; il tetto rigido della chiamata è 55 s (sotto il
# timeout di 60 s del proxy e dei 60 s del frontend).
BILANCI_LOCK_TTL_SECONDS = 120
BILANCI_DEADLINE_SECONDS = 55.0

FINANCIALS_SELECT = "anno,data_chiusura,tipo_bilancio,fonte_per_campo," + ",".join(
    CAMPI_BILANCIO
)
STATO_SELECT = (
    "advanced_esito,advanced_motivo,advanced_tentato_at,advanced_fetched_at,"
    "advanced_sandbox,advanced_fetch_count,advanced_piva,mapping_versione"
)

# Detail delle RPC 0032: tutti errori INTERNI (input costruito dal backend),
# quindi log + 502, mai un messaggio tecnico all'utente.
_RPC_ERRORS = frozenset({"fonte_non_valida", "azienda_non_trovata", "righe_non_valide"})


@dataclass
class EsitoAdvanced:
    """Esito di un tentativo IT-advanced (draft e company_financials_stato).

    `esito`: ok | non_disponibili | errore | timeout | saltato | mismatch;
    None = mai richiesto (draft precedente alla 0032). `raw` = data[0], solo
    con esito ok. `tentato_at` (ISO) = quando è partita la chiamata a
    pagamento: base del cooldown; None se la chiamata non è partita."""

    esito: str | None
    motivo: str | None
    raw: dict | None = None
    tentato_at: str | None = None

    @classmethod
    def saltato(cls, motivo: str) -> "EsitoAdvanced":
        return cls(esito="saltato", motivo=motivo)


def _adesso() -> datetime:
    return datetime.now(timezone.utc)


def _cooldown() -> timedelta:
    return timedelta(minutes=get_settings().company_import_cooldown_minutes)


# ------------------------------------------------------------- IT-advanced

async def chiama_it_advanced(
    primary, openapi, *, owner_id: str, piva: str, timeout_s: float
) -> EsitoAdvanced:
    """Chiama IT-advanced (A PAGAMENTO) e classifica l'esito. Non solleva MAI
    (tranne la cancellazione del task): ogni ramo annota il registro consumi.

    | ramo                         | esito / motivo                     | registro            |
    | 204, 200 con data vuota      | non_disponibili / nessun_bilancio  | success, costo      |
    | 404/305                      | non_disponibili / nessun_bilancio  | error, 0            |
    | identificativo non valido    | errore / errore_provider           | error, 0            |
    | timeout                      | timeout / esito_incerto            | timeout_unknown, costo |
    | mint fallito, altro errore   | errore / errore_provider           | error, 0            |
    | P.IVA non corrispondente     | mismatch / dati_non_corrispondenti | success, costo      |
    | ok                           | ok                                 | success, costo      |

    Il tetto `timeout_s` copre mint del token + chiamata, ma il mint si fa
    PRIMA della chiamata: se è lui a esaurire il tempo, la richiesta a
    pagamento non è mai partita (errore, costo 0) e non diventa un falso
    «esito incerto» con costo pieno e lock lasciato scadere.
    """
    tentato_at = _adesso().isoformat()
    costo = 0 if openapi.sandbox else openapi_service.COST_IT_ADVANCED_CENTS
    meta = {"piva": mask_piva(piva)}

    async def registra(outcome: str, cost_cents: int, extra: dict | None = None) -> None:
        await openapi_service.record_usage(
            primary, user_id=owner_id, family_parent_id=owner_id,
            service="IT-advanced", outcome=outcome, cost_cents=cost_cents,
            meta={**meta, **(extra or {})},
        )

    try:
        avvio = time.monotonic()
        try:
            await asyncio.wait_for(openapi.prepara_token("advanced"), timeout=timeout_s)
        except TimeoutError as exc:
            raise OpenapiNonInviataError() from exc
        restante = timeout_s - (time.monotonic() - avvio)
        if restante <= 0:
            raise OpenapiNonInviataError()
        dato = await asyncio.wait_for(
            openapi.it_advanced(piva, timeout_s=restante), timeout=restante
        )
    except OpenapiNessunDatoError as exc:
        # 2xx senza dati (204, o 200 con `data` vuota) = chiamata riuscita:
        # l'addebito è possibile (stima prudente); 404/305 = richiesta
        # respinta, nessun addebito.
        if 200 <= exc.status < 300:
            await registra("success", costo, {"nessun_dato": True})
        else:
            await registra("error", 0, {"nessun_dato": True})
        return EsitoAdvanced("non_disponibili", "nessun_bilancio", tentato_at=tentato_at)
    except OpenapiInvalidIdError:
        await registra("error", 0)
        return EsitoAdvanced("errore", "errore_provider", tentato_at=tentato_at)
    except (OpenapiTimeoutError, TimeoutError):
        # Esito (e addebito) ignoto: mai retry, costo pieno a registro.
        await registra("timeout_unknown", costo)
        return EsitoAdvanced("timeout", "esito_incerto", tentato_at=tentato_at)
    except AppError:
        # OpenapiNonInviataError (mint del gruppo fallito), OpenapiUpstreamError…
        await registra("error", 0)
        return EsitoAdvanced("errore", "errore_provider", tentato_at=tentato_at)
    except Exception:
        logger.exception("IT-advanced: errore inatteso")
        await registra("error", 0)
        return EsitoAdvanced("errore", "errore_provider", tentato_at=tentato_at)

    if piva not in ids_advanced(dato):
        # Pagato ma inutilizzabile: i dati di un'altra impresa non entrano MAI.
        logger.error("IT-advanced: risposta per un'impresa diversa da %s", mask_piva(piva))
        await registra("success", costo, {"mismatch": True})
        return EsitoAdvanced("mismatch", "dati_non_corrispondenti", tentato_at=tentato_at)

    anni = [riga.anno for riga in da_it_advanced(dato)]
    await registra("success", costo, {"anni": anni})
    return EsitoAdvanced("ok", None, raw=dato, tentato_at=tentato_at)


# --------------------------------------------------------------- scritture

async def registra_fonte(
    primary, company_id: str, fonte: str, righe: list[RigaFonte], riferimento: str,
    sostituisci: bool = False,
) -> list[int]:
    """Registra le righe di UNA fonte con `fn_bilanci_registra_fonte` (che
    ricalcola la riga fusa degli anni toccati). Ritorna gli anni registrati.
    Errori della RPC → log + UpstreamError."""
    try:
        resp = await primary.rpc(
            "fn_bilanci_registra_fonte",
            {
                "p_company_id": str(company_id),
                "p_fonte": fonte,
                "p_righe": a_payload_rpc(righe),
                "p_riferimento": riferimento,
                "p_sostituisci": sostituisci,
            },
        ).execute()
    except APIError as exc:
        detail = (exc.details or "").strip()
        codice = detail if detail in _RPC_ERRORS else exc.code
        logger.error("bilanci: registrazione della fonte %s fallita (%s)", fonte, codice)
        raise UpstreamError() from exc
    except Exception as exc:
        logger.exception("bilanci: registrazione della fonte %s fallita", fonte)
        raise UpstreamError() from exc
    data = resp.data if isinstance(resp.data, dict) else {}
    return [int(anno) for anno in (data.get("anni") or [])]


async def _registra_best_effort(
    primary, company_id: str, fonte: str, righe: list[RigaFonte], riferimento: str,
    *, sostituisci: bool,
) -> bool:
    """True se registrata (o se non c'era nulla da registrare). Nessuna riga
    → nessuna chiamata: un `sostituisci` con zero righe cancellerebbe tutto lo
    storico di quella fonte per un payload vuoto o anomalo."""
    if not righe:
        return True
    try:
        await registra_fonte(
            primary, company_id, fonte, righe, riferimento, sostituisci=sostituisci
        )
    except Exception:
        return False  # già loggato
    return True


async def _imposta_versione(primary, company_id: str, versione: int) -> None:
    """Versione del mapping con cui sono registrate le fonti. Si porta a
    MAPPING_BILANCI_VERSIONE SOLO dopo che tutte le registrazioni sono
    riuscite; 0 dopo un fallimento, così la rimappatura pigra riprova gratis."""
    try:
        await primary.table("company_financials_stato").upsert(
            {"company_profile_id": str(company_id), "mapping_versione": versione},
            on_conflict="company_profile_id",
        ).execute()
    except Exception:
        logger.exception("bilanci: versione del mapping non aggiornata")


def storico_completo(stato: dict | None) -> bool:
    """Lo storico IT-advanced di QUESTA azienda è stato recuperato: gli
    esercizi presenti sono tutti quelli noti al Registro Imprese. Altrimenti
    (mai richiesto, saltato, errore, timeout…) il loro numero è solo un
    minimo: «almeno 2 bilanci» non può diventare un falso «non soddisfatto»."""
    return (stato or {}).get("advanced_esito") == "ok"


def _storico_di_altra_piva(stato: dict | None, piva: str | None) -> bool:
    """Lo storico salvato (raw e righe `it_advanced`) è di una P.IVA diversa
    da `piva`. `advanced_piva` si scrive solo insieme al raw (esito ok)."""
    piva_storico = (stato or {}).get("advanced_piva")
    return bool(piva_storico) and piva_storico != piva


async def _scrivi_stato(
    primary, company_id: str, advanced: EsitoAdvanced, *, piva: str | None, sandbox: bool,
    stato_precedente: dict | None, riferimento: str,
) -> None:
    """Esito del tentativo IT-advanced (upsert: i campi assenti dal payload
    restano com'erano; un `saltato` non ha `tentato_at` e non tocca il
    cooldown).

    - ok: esito, raw, P.IVA del raw, contatore;
    - tentativo non riuscito o saltato quando c'è già uno storico recuperato
      per la STESSA P.IVA: esito e motivo NON cambiano (lo storico salvato
      resta completo: dirlo incompleto spingerebbe il titolare a ripagarlo),
      si aggiorna solo l'istante del tentativo;
    - altrimenti esito e motivo del tentativo.
    Uno storico salvato di un'ALTRA P.IVA non resta mai accanto ai dati di
    questa: via le sue righe (prima di tutto: se fallisce, lo stato resta
    intatto e l'errore sale) e il suo raw."""
    precedente = stato_precedente or {}
    altra_piva = _storico_di_altra_piva(precedente, piva)
    if altra_piva:
        logger.warning("bilanci: storico IT-advanced di un'altra P.IVA scartato")
        await registra_fonte(
            primary, company_id, "it_advanced", [], riferimento, sostituisci=True
        )
    riga: dict = {"company_profile_id": str(company_id)}
    if advanced.tentato_at:
        riga["advanced_tentato_at"] = advanced.tentato_at
    if advanced.esito == "ok" and isinstance(advanced.raw, dict):
        riga.update(
            advanced_esito="ok",
            advanced_motivo=None,
            advanced_raw=advanced.raw,
            advanced_fetched_at=advanced.tentato_at or _adesso().isoformat(),
            advanced_fetch_count=int(precedente.get("advanced_fetch_count") or 0) + 1,
            advanced_sandbox=sandbox,
            advanced_piva=piva if piva and validate_partita_iva(piva) else None,
        )
    elif precedente.get("advanced_fetched_at") and not altra_piva:
        pass  # storico completo già salvato: resta com'è
    else:
        riga["advanced_esito"] = advanced.esito
        riga["advanced_motivo"] = advanced.motivo
        if advanced.tentato_at:
            riga["advanced_sandbox"] = sandbox
        if altra_piva:
            riga.update(advanced_raw=None, advanced_fetched_at=None, advanced_piva=None)
    await primary.table("company_financials_stato").upsert(
        riga, on_conflict="company_profile_id"
    ).execute()


async def persisti_import(
    primary, company_id: str, *, piva: str, payload: dict, advanced: EsitoAdvanced | None,
    sandbox: bool,
) -> None:
    """Bilanci della conferma dell'import. BEST-EFFORT: logga e non solleva
    mai (la conferma non deve fallire per i bilanci; il draft verrebbe
    consumato comunque e la rimappatura pigra recupera gratis ciò che manca).

    Ordine: stato IT-advanced (se c'è un esito), fonte `it_full` SENZA
    sostituire (gli anni di patrimonio netto degli import precedenti restano),
    fonte `it_advanced` sostituendo lo storico (se ok), infine la versione
    del mapping — aggiornata solo se tutto è riuscito."""
    try:
        tutto_ok = True
        if advanced is not None and advanced.esito is not None:
            try:
                stato = await _fetch_stato(primary, company_id)
                await _scrivi_stato(
                    primary, company_id, advanced, piva=piva, sandbox=sandbox,
                    stato_precedente=stato, riferimento="import",
                )
            except Exception:
                logger.exception("bilanci: stato IT-advanced non scritto alla conferma")
                tutto_ok = False
        if not await _registra_best_effort(
            primary, company_id, "it_full", da_it_full(payload), "import", sostituisci=False
        ):
            tutto_ok = False
        if advanced is not None and advanced.esito == "ok" and isinstance(advanced.raw, dict):
            if not await _registra_best_effort(
                primary, company_id, "it_advanced", da_it_advanced(advanced.raw), "import",
                sostituisci=True,
            ):
                tutto_ok = False
        await _imposta_versione(
            primary, company_id, MAPPING_BILANCI_VERSIONE if tutto_ok else 0
        )
    except Exception:
        logger.exception("bilanci: persistenza all'import non riuscita")


# ----------------------------------------------------------------- letture

async def _fetch_stato(primary, company_id: str) -> dict | None:
    resp = (
        await primary.table("company_financials_stato")
        .select(STATO_SELECT)
        .eq("company_profile_id", str(company_id))
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _fetch_advanced_raw(primary, company_id: str) -> dict | None:
    resp = (
        await primary.table("company_financials_stato")
        .select("advanced_raw")
        .eq("company_profile_id", str(company_id))
        .limit(1)
        .execute()
    )
    raw = resp.data[0].get("advanced_raw") if resp.data else None
    return raw if isinstance(raw, dict) else None


async def _fetch_righe(primary, company_id: str) -> list[dict]:
    resp = (
        await primary.table("company_financials")
        .select(FINANCIALS_SELECT)
        .eq("company_profile_id", str(company_id))
        .order("anno")
        .execute()
    )
    return sorted(resp.data or [], key=lambda riga: int(riga["anno"]))


async def _rimappa_se_serve(primary, company_id: str, stato: dict | None) -> None:
    """Rimappatura pigra e GRATUITA dai payload conservati (company_data.raw e
    advanced_raw) quando la versione del mapping è vecchia o lo stato manca
    (aziende importate prima di WP1). `it_full` SENZA sostituire: il raw di
    IT-full conserva solo l'ultimo esercizio e gli anni precedenti andrebbero
    persi. Best-effort, idempotente; la versione sale solo a successo."""
    versione = int((stato or {}).get("mapping_versione") or 0)
    if versione >= MAPPING_BILANCI_VERSIONE:
        return
    try:
        dati = await openapi_service._fetch_company_data(primary, company_id)
        raw_full = (dati or {}).get("raw")
        if stato is None and not isinstance(raw_full, dict):
            return  # nulla da rimappare: nessuna scrittura
        raw_advanced = await _fetch_advanced_raw(primary, company_id) if stato else None
        if raw_advanced is not None and _storico_di_altra_piva(
            stato, (dati or {}).get("piva_fetched")
        ):
            # Lo storico di un'altra impresa non si registra di nuovo: lo
            # scarta il prossimo tentativo (import o recupero).
            logger.warning("bilanci: storico di un'altra P.IVA escluso dalla rimappatura")
            raw_advanced = None
        tutto_ok = True
        if isinstance(raw_full, dict):
            tutto_ok &= await _registra_best_effort(
                primary, company_id, "it_full", da_it_full(raw_full), "rimappatura",
                sostituisci=False,
            )
        if isinstance(raw_advanced, dict):
            tutto_ok &= await _registra_best_effort(
                primary, company_id, "it_advanced", da_it_advanced(raw_advanced),
                "rimappatura", sostituisci=True,
            )
        if tutto_ok:
            await _imposta_versione(primary, company_id, MAPPING_BILANCI_VERSIONE)
    except Exception:
        logger.exception("bilanci: rimappatura pigra non riuscita")


async def carica_bilanci(primary, company_id: str) -> tuple[list[EsercizioBilancio], bool]:
    """Esercizi fusi dell'azienda (anno crescente), dopo l'eventuale
    rimappatura pigra, e se lo storico è completo (`storico_completo`): senza
    storico il numero di esercizi è solo un minimo."""
    stato = await _fetch_stato(primary, company_id)
    await _rimappa_se_serve(primary, company_id, stato)
    righe = await _fetch_righe(primary, company_id)
    return [EsercizioBilancio.da_riga(riga) for riga in righe], storico_completo(stato)


async def carica_esercizi(primary, company_id: str) -> list[EsercizioBilancio]:
    """Esercizi fusi dell'azienda (anno crescente) per AI-check, PDF e regole,
    dopo l'eventuale rimappatura pigra."""
    esercizi, _completo = await carica_bilanci(primary, company_id)
    return esercizi


def _numero(valore) -> float | None:
    if valore is None or isinstance(valore, bool):
        return None
    return float(valore)


def _esercizio_out(riga: dict) -> EsercizioOut:
    fonti = {
        campo: fonte
        for campo, fonte in (riga.get("fonte_per_campo") or {}).items()
        if campo in CAMPI_BILANCIO and fonte in RANGO_FONTE and riga.get(campo) is not None
    }
    chiusura = riga.get("data_chiusura")
    return EsercizioOut(
        anno=int(riga["anno"]),
        data_chiusura=str(chiusura)[:10] if chiusura else None,
        tipo_bilancio=riga.get("tipo_bilancio") or "ignoto",
        fonti=fonti,
        **{campo: _numero(riga.get(campo)) for campo in CAMPI_BILANCIO},
    )


def _ultimo_tentativo(stato: dict | None, draft: dict | None, company_id: str) -> datetime | None:
    """Ultimo tentativo IT-advanced A PAGAMENTO per l'azienda: lo stato e il
    draft dell'anteprima, ma solo se il draft è di QUESTA azienda."""
    istanti = [openapi_service._parse_ts((stato or {}).get("advanced_tentato_at"))]
    if draft and openapi_service._stesso_id(draft.get("company_profile_id"), company_id):
        istanti.append(openapi_service._parse_ts(draft.get("advanced_tentato_at")))
    return max((t for t in istanti if t), default=None)


def _componi(
    *, editable: bool, stato: dict | None, righe: list[dict], draft: dict | None,
    company_id: str,
) -> BilanciOut:
    esercizi = [_esercizio_out(riga) for riga in righe]
    esito = (stato or {}).get("advanced_esito")
    if esercizi:
        stato_bilanci = "disponibili"
    elif esito:
        stato_bilanci = "non_disponibili"
    else:
        stato_bilanci = "mai_richiesti"

    if esito is None:
        motivo = "non_richiesto"
    elif esito == "ok":
        motivo = None if esercizi else "nessun_bilancio"
    else:
        motivo = (stato or {}).get("advanced_motivo")

    recuperabile_da = None
    if editable:
        ultimo = _ultimo_tentativo(stato, draft, company_id)
        fine = ultimo + _cooldown() if ultimo else None
        if fine and fine > _adesso():
            recuperabile_da = fine.isoformat()

    indicatori: list[IndicatoreOut] = []
    fasce = None
    if righe:
        calcolo = [EsercizioBilancio.da_riga(riga) for riga in righe]
        for indicatore in calcola_indicatori(calcolo):
            campi = asdict(indicatore)
            campi["valore"] = _numero(indicatore.valore)
            indicatori.append(IndicatoreOut(**campi))
        fasce = FasceOut(**asdict(calcola_fasce(calcolo)))

    return BilanciOut(
        editable=editable,
        stato=stato_bilanci,
        motivo=motivo,
        storico_esito=esito,
        ultimo_tentativo_at=(stato or {}).get("advanced_tentato_at"),
        recuperabile_da=recuperabile_da,
        sandbox=(stato or {}).get("advanced_sandbox"),
        esercizi=esercizi,
        indicatori=indicatori,
        fasce=fasce,
    )


async def get_bilanci(primary, active) -> BilanciOut:
    """Bilanci dell'azienda attiva: esatti per il titolare e per i membri con
    visibilità (sola lettura). Senza azienda: `mai_richiesti`."""
    if not active.company_id:
        return BilanciOut(editable=active.editable, stato="mai_richiesti", motivo="non_richiesto")
    company_id = str(active.company_id)
    stato = await _fetch_stato(primary, company_id)
    await _rimappa_se_serve(primary, company_id, stato)
    righe = await _fetch_righe(primary, company_id)
    # Il draft (per owner) serve solo al titolare, per il cooldown della CTA.
    draft = await openapi_service._fetch_draft(primary, active.owner_id) if active.editable else None
    return _componi(
        editable=active.editable, stato=stato, righe=righe, draft=draft, company_id=company_id
    )


# ----------------------------------------------------------------- recupero

async def _persisti_recupero(
    primary, company_id: str, esito: EsitoAdvanced, *, piva: str, sandbox: bool,
    stato: dict | None,
) -> list[int]:
    """Stato del tentativo (obbligatorio: senza, il pagamento andrebbe perso
    in silenzio) e, se ok, la fonte `it_advanced` (best-effort: se fallisce la
    versione torna a 0 e la rimappatura pigra la registra dal raw salvato)."""
    try:
        await _scrivi_stato(
            primary, company_id, esito, piva=piva, sandbox=sandbox,
            stato_precedente=stato, riferimento="recupero",
        )
    except Exception as exc:
        logger.exception("bilanci: stato del recupero non scritto")
        raise UpstreamError() from exc
    if esito.esito != "ok" or not isinstance(esito.raw, dict):
        return []
    righe = da_it_advanced(esito.raw)
    if not await _registra_best_effort(
        primary, company_id, "it_advanced", righe, "recupero", sostituisci=True
    ):
        await _imposta_versione(primary, company_id, 0)
    return [riga.anno for riga in righe]


async def recupera_bilanci(primary, openapi, active) -> BilanciOut:
    """«Recupera i bilanci» (IT-advanced, A PAGAMENTO) per l'azienda attiva.

    Solo per la P.IVA IMPORTATA (`company_data.piva_fetched`, legata
    all'azienda da Q10): mai per `company_profiles.partita_iva`, che il
    titolare modifica liberamente — altrimenti basterebbe cambiarla per
    avere lo storico esatto di qualsiasi impresa. Se i dati aziendali
    dichiarano un'altra P.IVA, niente chiamata (come `piva_diversa`
    nell'anteprima). Poi le stesse guardie dell'import: niente chiamata per
    le società di persone, cooldown persistente sull'ultimo tentativo (anche
    quello dell'anteprima di QUESTA azienda), lock per owner con token, tetto
    giornaliero fail-closed. Il tentativo si annota PRIMA della chiamata (il
    cooldown vale anche dopo un crash); nessun retry; dopo un timeout il lock
    si lascia scadere."""
    if not openapi.enabled:
        raise OpenapiNotConfiguredError()
    if not active.editable:
        raise ForbiddenError("I bilanci li recupera il titolare dell'azienda")
    if not active.company_id:
        raise NotFoundError("Nessuna azienda: compila prima i dati aziendali")
    company_id = str(active.company_id)
    owner_id = active.owner_id

    company_row = await openapi_service._fetch_company_row_by_id(primary, company_id)
    if company_row is None:
        raise NotFoundError("Azienda non trovata")
    dati = await openapi_service._fetch_company_data(primary, company_id)
    piva = (dati or {}).get("piva_fetched")
    if not piva or not validate_partita_iva(piva):
        raise BadRequestError(
            "Per recuperare i bilanci importa prima i dati aziendali da partita IVA dalla "
            "pagina Azienda"
        )
    piva_profilo = company_row.get("partita_iva")
    if piva_profilo and piva_profilo != piva:
        raise BadRequestError(
            "La partita IVA dei dati aziendali è diversa da quella importata dal Registro "
            "Imprese: correggila nei dati aziendali, poi recupera i bilanci"
        )
    if forma_giuridica_codice((dati or {}).get("raw") or {}) == "SP":
        raise AppError(
            409,
            "bilanci_non_previsti",
            "Le società di persone non depositano il bilancio: non ci sono bilanci da recuperare",
        )

    stato = await _fetch_stato(primary, company_id)
    draft = await openapi_service._fetch_draft(primary, owner_id)
    ultimo = _ultimo_tentativo(stato, draft, company_id)
    cooldown = _cooldown()
    if ultimo and _adesso() - ultimo < cooldown:
        remaining = cooldown - (_adesso() - ultimo)
        minutes = max(1, int(remaining.total_seconds() // 60) + 1)
        attesa = "un minuto" if minutes == 1 else f"{minutes} minuti"
        raise AppError(
            409,
            "bilanci_cooldown",
            f"Hai già richiesto i bilanci di recente: riprova tra circa {attesa}",
        )

    token = await openapi_service._acquire_lock(primary, owner_id, BILANCI_LOCK_TTL_SECONDS)
    if not token:
        raise AppError(
            409,
            "import_in_progress",
            "Un recupero dati è già in corso per questa azienda: riprova tra qualche minuto",
        )

    rilascia = True
    try:
        # Tetto giornaliero dopo il lock (un lock occupato non consuma quota).
        await openapi_service.prenota_operazione_openapi(primary, owner_id)

        tentato_at = _adesso().isoformat()
        try:
            await primary.table("company_financials_stato").upsert(
                {"company_profile_id": company_id, "advanced_tentato_at": tentato_at},
                on_conflict="company_profile_id",
            ).execute()
        except Exception as exc:
            # Senza il segno del tentativo il cooldown non reggerebbe a un
            # crash: meglio non spendere.
            logger.exception("bilanci: tentativo non annotato, recupero annullato")
            raise UpstreamError() from exc

        try:
            esito = await asyncio.wait_for(
                chiama_it_advanced(
                    primary, openapi, owner_id=owner_id, piva=piva,
                    timeout_s=openapi_service.IT_ADVANCED_TIMEOUT_SECONDS,
                ),
                timeout=BILANCI_DEADLINE_SECONDS,
            )
        except TimeoutError:
            # Tetto complessivo (mint compreso) superato: esito ignoto.
            await openapi_service.record_usage(
                primary, user_id=owner_id, family_parent_id=owner_id,
                service="IT-advanced", outcome="timeout_unknown",
                cost_cents=0 if openapi.sandbox else openapi_service.COST_IT_ADVANCED_CENTS,
                meta={"piva": mask_piva(piva)},
            )
            esito = EsitoAdvanced("timeout", "esito_incerto")
        esito.tentato_at = tentato_at
        if esito.esito == "timeout":
            rilascia = False  # il lock scade da solo: niente doppio addebito al buio

        anni = await _persisti_recupero(
            primary, company_id, esito, piva=piva, sandbox=openapi.sandbox, stato=stato
        )
    finally:
        if rilascia:
            await openapi_service._release_lock(primary, owner_id, token)

    try:
        await primary.table("audit_log").insert(
            {
                "actor_id": str(owner_id),
                "action": "company.bilanci_recuperati",
                "target_user_id": str(owner_id),
                "family_parent_id": str(owner_id),
                "payload": {"company_profile_id": company_id, "esito": esito.esito, "anni": anni},
            }
        ).execute()
    except Exception:
        logger.exception("audit del recupero bilanci non scrivibile")

    from app.services.compatibility import invalidate_company_facets  # import locale: evita cicli

    invalidate_company_facets(company_id)

    if esito.esito == "timeout":
        raise OpenapiTimeoutError()
    if esito.esito == "errore":
        raise OpenapiUpstreamError()
    if esito.esito == "mismatch":
        raise OpenapiUpstreamError(
            "Il servizio che fornisce i bilanci ha restituito i dati di un'altra impresa, "
            "quindi non li abbiamo salvati: riprova più tardi"
        )
    return await get_bilanci(primary, active)
