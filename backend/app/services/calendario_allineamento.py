"""Riallineamento delle scadenze in calendario al catalogo bandi (migration
0048; passo periodico di `catalogo_scheduler`, dopo la rimappatura dei fusi).

Una scadenza in calendario (`calendar_events`, tipo 'bando') nasce come
istantanea del catalogo, che poi cambia da solo: chiusure, proroghe,
rettifiche di date, sospensioni, revoche. Un passo è una riconciliazione
idempotente:

1. le scadenze a pagine di `PAGINA_EVENTI` per id crescente (keyset,
   `id > ultimo`: una riga cancellata nel frattempo non ne fa saltare altre),
   con le sole colonne che servono;
2. `bando_pubblico` per blocchi di `BLOCCO_BANDI` id distinti;
3. per ogni scadenza con il bando nella vista: data, ora e «tutto il giorno»
   con le regole della creazione (`calendar_service.campi_scadenza`), se il
   catalogo ha la scadenza; lo stato (`stato_effettivo` → `bando_stato`)
   sempre. Mai titolo e note (sono dell'utente), né `ora_fine`, che va a NULL
   solo con «tutto il giorno» come vuole il vincolo di coerenza degli orari;
4. update per id delle sole righe cambiate, con la guardia sul bando letto:
   se nel frattempo la rimappatura ha spostato la riga, non si scrive.

Un bando assente dalla vista (fuso in attesa di rimappatura, ritirato) lascia
la scadenza com'è e si conta in `assenti`. Prima si legge tutto, poi si
scrive: un errore di lettura (scadenze o catalogo) chiude il passo senza
scritture, perché un bando non letto non vale mai come assente né come
invariato; un errore su una riga vale solo per quella. Con `scrivi=False`
nessuna scrittura: i conteggi sono quelli che la scrittura produrrebbe.
`passo` non solleva mai: gli errori vanno nel log (solo id e codici, mai i
messaggi di PostgREST) e nel contatore `errori`.
"""

import logging
from typing import Any

from app.services.bandi_risoluzione import VISTA_BANDI
from app.services.bandi_service import _ora
from app.services.calendar_service import _data_iso, campi_scadenza, stato_del_bando

logger = logging.getLogger("bandofit.calendario_allineamento")

PAGINA_EVENTI = 1000  # max-rows di PostgREST
BLOCCO_BANDI = 100
SELECT_EVENTI = "id,bando_id,data,tutto_il_giorno,ora_inizio,bando_stato"
SELECT_BANDI = "id,data_scadenza,ora_scadenza,stato_effettivo"


class _PaginaSenzaId(Exception):
    """Una pagina piena senza un id che faccia avanzare il cursore."""


def _intero_positivo(valore: Any) -> bool:
    return isinstance(valore, int) and not isinstance(valore, bool) and valore > 0


def _codice(exc: Exception) -> str:
    codice = getattr(exc, "code", None)
    return codice if isinstance(codice, str) and codice else type(exc).__name__


def _ora_iso(valore: Any) -> str | None:
    ora = _ora(valore)
    return ora.isoformat() if ora is not None else None


async def _scadenze(primary) -> list[dict]:
    """Tutte le scadenze in calendario, a pagine per id crescente."""
    eventi: list[dict] = []
    ultimo: str | None = None
    while True:
        query = primary.table("calendar_events").select(SELECT_EVENTI).eq("tipo", "bando")
        if ultimo is not None:
            query = query.gt("id", ultimo)
        resp = await query.order("id").limit(PAGINA_EVENTI).execute()
        righe = [riga for riga in resp.data or [] if isinstance(riga, dict)]
        eventi.extend(righe)
        if len(resp.data or []) < PAGINA_EVENTI:
            return eventi
        prossimo = str(righe[-1].get("id") or "") if righe else ""
        if not prossimo or prossimo == ultimo:
            raise _PaginaSenzaId()
        ultimo = prossimo


async def _catalogo(secondary, ids: list[int]) -> dict[int, dict]:
    """Le righe di `bando_pubblico` degli id, a blocchi; un id che manca dal
    risultato è assente dalla vista."""
    bandi: dict[int, dict] = {}
    for inizio in range(0, len(ids), BLOCCO_BANDI):
        blocco = ids[inizio : inizio + BLOCCO_BANDI]
        resp = await secondary.table(VISTA_BANDI).select(SELECT_BANDI).in_("id", blocco).execute()
        for riga in resp.data or []:
            if isinstance(riga, dict) and riga.get("id") in blocco:
                bandi[riga["id"]] = riga
    return bandi


def _modifiche(evento: dict, bando: dict) -> tuple[dict, bool, bool]:
    """(colonne da aggiornare, data/ora cambiate, stato cambiato)."""
    modifiche: dict = {}
    scadenza = campi_scadenza(bando.get("data_scadenza"), bando.get("ora_scadenza"))
    data_cambiata = False
    if scadenza is not None:
        attuale = (_data_iso(evento.get("data")), evento.get("tutto_il_giorno"),
                   _ora_iso(evento.get("ora_inizio")))
        nuova = (scadenza["data"], scadenza["tutto_il_giorno"], scadenza["ora_inizio"])
        if attuale != nuova:
            data_cambiata = True
            modifiche.update(scadenza)
            if scadenza["tutto_il_giorno"]:
                modifiche["ora_fine"] = None
    stato = stato_del_bando(bando.get("stato_effettivo"))
    stato_cambiato = stato != evento.get("bando_stato")
    if stato_cambiato:
        modifiche["bando_stato"] = stato
    return modifiche, data_cambiata, stato_cambiato


async def passo(primary, secondary, *, scrivi: bool) -> dict:
    """Un passo di riallineamento. Report: scadenze lette (`eventi`), bandi
    distinti (`bandi`), scadenze con data/ora aggiornate (`date_aggiornate`) e
    con lo stato aggiornato (`stati_aggiornati`; una scadenza può contare in
    entrambi), scadenze con il bando assente dalla vista (`assenti`), errori.
    Con `scrivi=False` conta senza scrivere."""
    report = {"eventi": 0, "bandi": 0, "date_aggiornate": 0, "stati_aggiornati": 0,
              "assenti": 0, "errori": 0}
    try:
        eventi = await _scadenze(primary)
    except Exception as exc:  # noqa: BLE001 — mai un'eccezione al chiamante
        report["errori"] += 1
        logger.warning("riallineamento calendario: scadenze non leggibili (%s), passo "
                       "interrotto", _codice(exc))
        return report
    report["eventi"] = len(eventi)
    ids = list(dict.fromkeys(
        evento["bando_id"] for evento in eventi if _intero_positivo(evento.get("bando_id"))
    ))
    report["bandi"] = len(ids)
    try:
        catalogo = await _catalogo(secondary, ids)
    except Exception as exc:  # noqa: BLE001 — nessuna scrittura senza il catalogo intero
        report["errori"] += 1
        logger.warning("riallineamento calendario: %s non leggibile (%s), passo interrotto "
                       "senza scritture", VISTA_BANDI, _codice(exc))
        return report

    for evento in eventi:
        bando_id = evento.get("bando_id")
        bando = catalogo.get(bando_id) if _intero_positivo(bando_id) else None
        if bando is None:
            report["assenti"] += 1
            continue
        modifiche, data_cambiata, stato_cambiato = _modifiche(evento, bando)
        if not modifiche:
            continue
        if scrivi:
            try:
                resp = await (
                    primary.table("calendar_events")
                    .update(modifiche)
                    .eq("id", str(evento.get("id")))
                    .eq("tipo", "bando")
                    .eq("bando_id", bando_id)
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001 — una riga non ferma le altre
                report["errori"] += 1
                logger.warning("riallineamento calendario: evento %s non aggiornato (%s)",
                               evento.get("id"), _codice(exc))
                continue
            if not resp.data:
                # Cancellata o spostata su un altro bando nel frattempo.
                continue
        report["date_aggiornate"] += data_cambiata
        report["stati_aggiornati"] += stato_cambiato
    return report
