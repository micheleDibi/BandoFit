"""Rimappatura dei bandi fusi nel DB primario (fase c del contratto DB bandi,
§6.2; migration 0043).

Il catalogo fonde i doppioni in un bando master: il doppione esce da
`bando_pubblico` e `bando_fusione` indica il master corrente (le catene sono
già appiattite dal produttore). Preferiti, eventi del calendario e call di
partenariato conservano un `bando_id` senza FK fra i due database, quindi
vanno spostati sul master. Un passo è una riconciliazione idempotente, non un
cursore sugli eventi:

1. `fn_bandi_in_uso` → gli id dei bandi referenziati nel primario, in un solo
   array (il max-rows di PostgREST non tronca un valore unico);
2. `bando_fusione?select=bando_id,master_id,master_slug&bando_id=in.(…)` a
   blocchi di 100: i doppioni fra quegli id;
3. per ogni coppia (doppione, master) `fn_rimappa_bando_fuso`; con
   `prova=True` la RPC conta senza scrivere.

`passo` non solleva mai: gli errori vanno nel log (solo id e codici, mai i
messaggi di PostgREST) e nel contatore `errori` del report. Un errore su una
coppia vale solo per quella: 23505 (l'utente ha salvato il master nello
stesso momento) e 40P01 sono transitori e il passo successivo la ripete. Una
coppia con call in collisione sul master resta in uso e ricompare a ogni
passo: è previsto, e si registra a livello INFO.
"""

import logging
from typing import Any

logger = logging.getLogger("bandofit.rimappatura_fusi")

BLOCCO_FUSIONI = 100
SELECT_FUSIONE = "bando_id,master_id,master_slug"
# Coppie riportate nel riassunto del log (il report completo resta al chiamante).
MAX_COPPIE_NEL_LOG = 50
_ERRORI_TRANSITORI = {"23505", "40P01"}
# Funzione non trovata (PostgREST / Postgres): migration 0043 non applicata.
_FUNZIONE_ASSENTE = {"PGRST202", "42883"}
_CHIAVI_CONTEGGI = {
    "saved_bandi": ("aggiornate", "eliminate"),
    "calendar_events": ("aggiornati", "eliminati", "convertiti"),
    "partner_calls": ("aggiornate", "in_collisione"),
}


def totali_vuoti() -> dict[str, dict[str, int]]:
    return {tabella: dict.fromkeys(chiavi, 0) for tabella, chiavi in _CHIAVI_CONTEGGI.items()}


def _intero_positivo(valore: Any) -> bool:
    return isinstance(valore, int) and not isinstance(valore, bool) and valore > 0


def _codice(exc: Exception) -> str:
    codice = getattr(exc, "code", None)
    return codice if isinstance(codice, str) and codice else type(exc).__name__


def _ids_in_uso(data: Any) -> list[int]:
    """L'array di `fn_bandi_in_uso` come lo restituisce PostgREST: la lista
    degli id oppure, per sicurezza, annidata sotto il nome della funzione."""
    if isinstance(data, dict):
        data = data.get("fn_bandi_in_uso")
    ids: list[int] = []
    for valore in data if isinstance(data, list) else []:
        if isinstance(valore, dict):
            valore = valore.get("fn_bandi_in_uso")
        if _intero_positivo(valore):
            ids.append(valore)
    return list(dict.fromkeys(ids))


def _conteggi(data: Any) -> dict[str, dict[str, int]]:
    """Conteggi della RPC con tutte le chiavi; un valore mancante o strano vale 0."""
    conteggi = totali_vuoti()
    for tabella, chiavi in _CHIAVI_CONTEGGI.items():
        voce = data.get(tabella) if isinstance(data, dict) else None
        for chiave in chiavi:
            valore = voce.get(chiave) if isinstance(voce, dict) else None
            if isinstance(valore, int) and not isinstance(valore, bool) and valore > 0:
                conteggi[tabella][chiave] = valore
    return conteggi


def _somma(totali: dict, conteggi: dict) -> None:
    for tabella, voce in conteggi.items():
        for chiave, valore in voce.items():
            totali[tabella][chiave] += valore


async def _coppie(secondary, ids: list[int], report: dict) -> list[tuple[int, int, str]]:
    """(doppione, master, slug del master) fra gli id in uso, a blocchi. Un
    blocco non leggibile si conta fra gli errori e si salta: le sue coppie
    tornano al passo successivo."""
    coppie: dict[int, tuple[int, int, str]] = {}
    for inizio in range(0, len(ids), BLOCCO_FUSIONI):
        blocco = ids[inizio : inizio + BLOCCO_FUSIONI]
        try:
            resp = (
                await secondary.table("bando_fusione")
                .select(SELECT_FUSIONE)
                .in_("bando_id", blocco)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 — un blocco non ferma gli altri
            report["errori"] += 1
            logger.warning("rimappatura fusi: bando_fusione non leggibile (%d id, %s)",
                           len(blocco), _codice(exc))
            continue
        for riga in resp.data or []:
            if not isinstance(riga, dict):
                continue
            doppione, master = riga.get("bando_id"), riga.get("master_id")
            slug = riga.get("master_slug")
            if not _intero_positivo(doppione) or doppione not in blocco or doppione in coppie:
                continue
            if (
                not _intero_positivo(master)
                or master == doppione
                or not isinstance(slug, str)
                or not slug.strip()
            ):
                report["scartate"] += 1
                logger.warning("rimappatura fusi: fusione del bando %s incompleta, saltata",
                               doppione)
                continue
            coppie[doppione] = (doppione, master, slug.strip())
    return list(coppie.values())


async def passo(primary, secondary, *, prova: bool) -> dict:
    """Un passo di riconciliazione. Con `prova` nessuna scrittura: i conteggi
    sono quelli che la scrittura produrrebbe. Report: modalità, id in uso,
    coppie fuse trovate e scartate, errori, totali per tabella e l'elenco
    delle coppie con i loro conteggi (o il codice d'errore)."""
    report: dict = {
        "modalita": "prova" if prova else "attiva",
        "in_uso": 0,
        "fusi": 0,
        "scartate": 0,
        "errori": 0,
        "totali": totali_vuoti(),
        "coppie": [],
    }
    try:
        resp = await primary.rpc("fn_bandi_in_uso", {}).execute()
    except Exception as exc:  # noqa: BLE001 — mai un'eccezione al chiamante
        report["errori"] += 1
        codice = _codice(exc)
        if codice in _FUNZIONE_ASSENTE:
            logger.error("rimappatura fusi: fn_bandi_in_uso assente (%s): la migration 0043 "
                         "va applicata prima di accendere la rimappatura", codice)
        else:
            logger.error("rimappatura fusi: bandi in uso non leggibili (%s)", codice)
        return report
    ids = _ids_in_uso(resp.data)
    report["in_uso"] = len(ids)
    coppie = await _coppie(secondary, ids, report) if ids else []
    report["fusi"] = len(coppie)
    for doppione, master, slug in coppie:
        voce: dict = {"doppione": doppione, "master": master}
        try:
            resp = await primary.rpc("fn_rimappa_bando_fuso", {
                "p_doppione": doppione,
                "p_master": master,
                "p_master_slug": slug,
                "p_prova": prova,
            }).execute()
        except Exception as exc:  # noqa: BLE001 — una coppia non ferma le altre
            codice = _codice(exc)
            report["errori"] += 1
            voce["errore"] = codice
            if codice in _ERRORI_TRANSITORI:
                logger.warning("rimappatura fusi: bando %s → %s rinviato al passo successivo "
                               "(%s)", doppione, master, codice)
            else:
                logger.error("rimappatura fusi: bando %s → %s non rimappato (%s)",
                             doppione, master, codice)
        else:
            conteggi = _conteggi(resp.data)
            voce["conteggi"] = conteggi
            _somma(report["totali"], conteggi)
            in_collisione = conteggi["partner_calls"]["in_collisione"]
            if in_collisione:
                logger.info("rimappatura fusi: %d call del bando %s in collisione con il "
                            "master %s, non spostate", in_collisione, doppione, master)
        report["coppie"].append(voce)
    return report


def riassunto(report: dict, max_coppie: int = MAX_COPPIE_NEL_LOG) -> dict:
    """Il report per il log: tutto tranne le coppie oltre le prime `max_coppie`."""
    coppie = report.get("coppie") or []
    breve = {chiave: valore for chiave, valore in report.items() if chiave != "coppie"}
    breve["coppie"] = coppie[:max_coppie]
    if len(coppie) > max_coppie:
        breve["coppie_omesse"] = len(coppie) - max_coppie
    return breve
