"""Rimappatura dei bandi fusi nel DB primario (fase c del contratto DB bandi,
§6.2; migration 0043) e rilevazione delle separazioni (migration 0045 e 0046).

Il catalogo fonde i doppioni in un bando master: il doppione esce da
`bando_pubblico` e `bando_fusione` indica il master corrente (le catene sono
già appiattite dal produttore). Preferiti, eventi del calendario e call di
partenariato conservano un `bando_id` senza FK fra i due database, quindi
vanno spostati sul master. Un passo è una riconciliazione idempotente, non un
cursore sugli eventi:

1. le separazioni, PRIMA della rimappatura: `fn_doppioni_rimappati` (0045,
   0046) → i doppioni con righe rimappate non ancora ripristinate né marcate.
   Chi manca da `bando_fusione` è stato separato: se è di nuovo in
   `bando_pubblico` si chiama `fn_ripristina_rimappatura_voci` (0046) nel
   percorso automatico, con `p_prova` pari alla modalità; altrimenti tutto
   resta sul master e si riprova al passo dopo. Mai verso un id assente dalla
   vista, e la funzione non cancella righe. Prima della rimappatura perché, se
   nello stesso aggiornamento del catalogo il doppione si separa e il suo
   master viene fuso in un altro, le righe tornano al doppione invece di
   seguire il master. Le righe in conflitto si contano e non fermano il passo;
   un doppione che resta in conflitto oltre `PASSI_CONFLITTO_TOLLERATI` passi
   consecutivi va nel log a WARNING una volta al giorno. I conflitti che la
   funzione rende definitivi (doppione già nello stesso ambito, per scelta
   dell'utente) si contano a parte e non si riprovano;
2. `fn_bandi_in_uso` → gli id dei bandi referenziati nel primario, in un solo
   array (il max-rows di PostgREST non tronca un valore unico);
3. `bando_fusione?select=bando_id,master_id,master_slug&bando_id=in.(…)` a
   blocchi di 100: i doppioni fra quegli id;
4. per ogni coppia (doppione, master) `fn_rimappa_bando_fuso`; con
   `prova=True` la RPC conta senza scrivere;
5. il conteggio delle righe di `bando_fusione` (`coppie_catalogo`: una sola
   richiesta a conteggio esatto con `limit=1`, mai per `fuso_at` che non
   segnala i cambiamenti): il totale delle fusioni del catalogo, da
   confrontare con `fusi`, che conta solo i doppioni fra gli id in uso.

`passo` non solleva mai: gli errori vanno nel log (solo id e codici, mai i
messaggi di PostgREST) e nel contatore `errori` del report; se `fn_bandi_in_uso`
non risponde, il conteggio del catalogo e le separazioni si fanno lo stesso.
Un errore su una coppia vale solo per quella: 23505 (l'utente ha salvato il master nello
stesso momento) e 40P01 sono transitori e il passo successivo la ripete. Una
coppia con call in collisione sul master resta in uso e ricompare a ogni
passo: è previsto, e si registra a livello INFO.
"""

import logging
import time
from typing import Any

from app.services.bandi_risoluzione import VISTA_BANDI

logger = logging.getLogger("bandofit.rimappatura_fusi")

BLOCCO_FUSIONI = 100
SELECT_FUSIONE = "bando_id,master_id,master_slug"
# Coppie riportate nel riassunto del log (il report completo resta al chiamante).
MAX_COPPIE_NEL_LOG = 50
# Passi consecutivi in cui un doppione separato resta con sole righe in
# conflitto prima dell'avviso a WARNING, poi uno al giorno.
PASSI_CONFLITTO_TOLLERATI = 24
SECONDI_FRA_AVVISI_CONFLITTO = 24 * 3600
_ERRORI_TRANSITORI = {"23505", "40P01"}
# Funzione non trovata (PostgREST / Postgres): migration non applicata.
_FUNZIONE_ASSENTE = {"PGRST202", "42883"}
_CHIAVI_CONTEGGI = {
    "saved_bandi": ("aggiornate", "eliminate"),
    "calendar_events": ("aggiornati", "eliminati", "convertiti"),
    "partner_calls": ("aggiornate", "in_collisione"),
}
# `definitivi` (0046, percorso automatico): conflitti resi definitivi nella
# chiamata, già compresi in `in_conflitto`.
_CHIAVI_RIPRISTINO = ("ripristinate", "in_conflitto", "definitivi")

# Stato in memoria dei doppioni separati con sole righe in conflitto: passi
# consecutivi e istante dell'ultimo avviso. Per worker; si azzera al riavvio.
_conflitti: dict[int, int] = {}
_avvisi_conflitto: dict[int, float] = {}
_orologio = time.monotonic


def totali_vuoti() -> dict[str, dict[str, int]]:
    return {tabella: dict.fromkeys(chiavi, 0) for tabella, chiavi in _CHIAVI_CONTEGGI.items()}


def _intero_positivo(valore: Any) -> bool:
    return isinstance(valore, int) and not isinstance(valore, bool) and valore > 0


def _codice(exc: Exception) -> str:
    codice = getattr(exc, "code", None)
    return codice if isinstance(codice, str) and codice else type(exc).__name__


def _ids_array(data: Any, nome: str) -> list[int]:
    """L'array di una RPC come lo restituisce PostgREST: la lista degli id
    oppure, per sicurezza, annidata sotto il nome della funzione."""
    if isinstance(data, dict):
        data = data.get(nome)
    ids: list[int] = []
    for valore in data if isinstance(data, list) else []:
        if isinstance(valore, dict):
            valore = valore.get(nome)
        if _intero_positivo(valore):
            ids.append(valore)
    return list(dict.fromkeys(ids))


def _ids_in_uso(data: Any) -> list[int]:
    return _ids_array(data, "fn_bandi_in_uso")


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


def _conteggi_ripristino(data: Any) -> dict[str, int]:
    """`ripristinate`, `in_conflitto` e `definitivi` (0046) sommati sulle tre
    tabelle; un valore mancante o strano vale 0."""
    totale = dict.fromkeys(_CHIAVI_RIPRISTINO, 0)
    for tabella in _CHIAVI_CONTEGGI:
        voce = data.get(tabella) if isinstance(data, dict) else None
        for chiave in _CHIAVI_RIPRISTINO:
            valore = voce.get(chiave) if isinstance(voce, dict) else None
            if _intero_positivo(valore):
                totale[chiave] += valore
    return totale


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


async def _coppie_catalogo(secondary, report: dict) -> None:
    """Righe di `bando_fusione` con una sola richiesta a conteggio esatto
    (`limit=1`): il totale delle fusioni del catalogo in `coppie_catalogo`.
    Un errore, o un conteggio assente, conta fra gli errori e lascia None
    (che non è 0)."""
    try:
        resp = (
            await secondary.table("bando_fusione")
            .select("bando_id", count="exact")
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — la diagnostica non ferma il passo
        report["errori"] += 1
        logger.warning("rimappatura fusi: conteggio di bando_fusione non riuscito (%s)",
                       _codice(exc))
        return
    conteggio = getattr(resp, "count", None)
    if not isinstance(conteggio, int) or isinstance(conteggio, bool) or conteggio < 0:
        report["errori"] += 1
        logger.warning("rimappatura fusi: conteggio di bando_fusione assente nella risposta")
        return
    report["coppie_catalogo"] = conteggio


async def _presenti(
    secondary, tabella: str, colonna: str, ids: list[int], report: dict
) -> tuple[set[int], set[int]]:
    """Gli id presenti in `tabella` e quelli verificati, a blocchi di 100. Un
    blocco non leggibile conta fra gli errori e i suoi id non sono verificati:
    non valgono mai come assenti. Una riga presente, anche incompleta, vale
    come presente."""
    presenti: set[int] = set()
    verificati: set[int] = set()
    for inizio in range(0, len(ids), BLOCCO_FUSIONI):
        blocco = ids[inizio : inizio + BLOCCO_FUSIONI]
        try:
            resp = (
                await secondary.table(tabella).select(colonna).in_(colonna, blocco).execute()
            )
        except Exception as exc:  # noqa: BLE001 — un blocco non ferma gli altri
            report["errori"] += 1
            logger.warning("rimappatura fusi: %s non leggibile per le separazioni (%d id, %s)",
                           tabella, len(blocco), _codice(exc))
            continue
        verificati.update(blocco)
        for riga in resp.data or []:
            valore = riga.get(colonna) if isinstance(riga, dict) else None
            if _intero_positivo(valore) and valore in verificati:
                presenti.add(valore)
    return presenti, verificati


async def _ancora_fuso(secondary, doppione: int, report: dict) -> bool:
    """Riletta di `bando_fusione` per il solo doppione, prima di scrivere: vero
    se la riga è ricomparsa o non si è potuto leggere (si riprova al passo
    dopo)."""
    try:
        resp = (
            await secondary.table("bando_fusione")
            .select("bando_id")
            .eq("bando_id", doppione)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — senza conferma non si scrive
        report["errori"] += 1
        logger.warning("rimappatura fusi: riletta di bando_fusione per il bando %s non riuscita "
                       "(%s)", doppione, _codice(exc))
        return True
    return bool(resp.data)


def _avvisa_conflitto(doppione: int, in_conflitto: int) -> None:
    """Righe in conflitto di un doppione separato: INFO per i primi
    `PASSI_CONFLITTO_TOLLERATI` passi consecutivi, poi un WARNING al giorno."""
    passi = _conflitti.get(doppione, 0) + 1
    _conflitti[doppione] = passi
    if passi <= PASSI_CONFLITTO_TOLLERATI:
        logger.info("rimappatura fusi: bando %s separato, %d righe in conflitto restano sul "
                    "master (passo %d)", doppione, in_conflitto, passi)
        return
    adesso = _orologio()
    ultimo = _avvisi_conflitto.get(doppione)
    if ultimo is not None and adesso - ultimo < SECONDI_FRA_AVVISI_CONFLITTO:
        return
    _avvisi_conflitto[doppione] = adesso
    logger.warning("rimappatura fusi: bando %s separato da %d passi con %d righe in conflitto "
                   "sul master: da vedere a mano", doppione, passi, in_conflitto)


def _dimentica_conflitto(doppione: int) -> None:
    _conflitti.pop(doppione, None)
    _avvisi_conflitto.pop(doppione, None)


async def _separazioni(primary, secondary, report: dict, *, prova: bool) -> None:
    """Separazioni dei doppioni già rimappati (0045) e loro ripristino nel
    percorso automatico (0046): `separazioni_rilevate`, `ripristinate`,
    `in_conflitto` e `conflitti_definitivi` nel report."""
    try:
        resp = await primary.rpc("fn_doppioni_rimappati", {}).execute()
    except Exception as exc:  # noqa: BLE001 — il resto del report resta valido
        report["errori"] += 1
        codice = _codice(exc)
        if codice in _FUNZIONE_ASSENTE:
            logger.error("rimappatura fusi: fn_doppioni_rimappati assente (%s): la migration "
                         "0045 va applicata per rilevare le separazioni", codice)
        else:
            logger.error("rimappatura fusi: doppioni rimappati non leggibili (%s)", codice)
        return
    doppioni = _ids_array(resp.data, "fn_doppioni_rimappati")
    for doppione in list(_conflitti):
        if doppione not in doppioni:
            _dimentica_conflitto(doppione)
    if not doppioni:
        return
    ancora_fusi, verificati = await _presenti(secondary, "bando_fusione", "bando_id", doppioni,
                                              report)
    for doppione in ancora_fusi:
        _dimentica_conflitto(doppione)
    separati = [d for d in doppioni if d in verificati and d not in ancora_fusi]
    report["separazioni_rilevate"] = len(separati)
    if not separati:
        return
    nella_vista, verificati_vista = await _presenti(secondary, VISTA_BANDI, "id", separati,
                                                    report)
    for doppione in separati:
        if doppione not in verificati_vista:
            continue
        if doppione not in nella_vista:
            logger.info("rimappatura fusi: bando %s separato ma non ancora nella vista, righe "
                        "lasciate sul master", doppione)
            continue
        if not prova and await _ancora_fuso(secondary, doppione, report):
            continue
        try:
            # Percorso automatico: le voci marcate come conflitto definitivo
            # non si riprovano e i nuovi conflitti di quel tipo si marcano.
            resp = await primary.rpc("fn_ripristina_rimappatura_voci", {
                "p_doppione": doppione,
                "p_dal": "-infinity",
                "p_prova": prova,
                "p_automatico": True,
            }).execute()
        except Exception as exc:  # noqa: BLE001 — un doppione non ferma gli altri
            codice = _codice(exc)
            report["errori"] += 1
            if codice in _FUNZIONE_ASSENTE:
                logger.error("rimappatura fusi: fn_ripristina_rimappatura_voci assente (%s): la "
                             "migration 0046 va applicata", codice)
            elif codice in _ERRORI_TRANSITORI:
                logger.warning("rimappatura fusi: ripristino del bando %s rinviato al passo "
                               "successivo (%s)", doppione, codice)
            else:
                logger.error("rimappatura fusi: ripristino del bando %s non riuscito (%s)",
                             doppione, codice)
            continue
        conteggi = _conteggi_ripristino(resp.data)
        report["ripristinate"] += conteggi["ripristinate"]
        report["in_conflitto"] += conteggi["in_conflitto"]
        report["conflitti_definitivi"] += conteggi["definitivi"]
        # I conflitti definitivi non tornano al passo dopo: non contano per
        # l'avviso dei conflitti ripetuti.
        aperti = max(conteggi["in_conflitto"] - conteggi["definitivi"], 0)
        if conteggi["definitivi"]:
            logger.info("rimappatura fusi: bando %s separato, %d conflitti %s definitivi "
                        "(doppione già nello stesso ambito): righe lasciate sul master",
                        doppione, conteggi["definitivi"], "da rendere" if prova else "resi")
        if conteggi["ripristinate"]:
            _dimentica_conflitto(doppione)
            logger.warning("rimappatura fusi: bando %s separato, %d righe %s (%d in conflitto)",
                           doppione, conteggi["ripristinate"],
                           "da ripristinare" if prova else "ripristinate",
                           conteggi["in_conflitto"])
        elif aperti:
            _avvisa_conflitto(doppione, aperti)
        else:
            _dimentica_conflitto(doppione)
            if not conteggi["definitivi"]:
                logger.info("rimappatura fusi: bando %s separato, nessuna riga da ripristinare",
                            doppione)


async def passo(primary, secondary, *, prova: bool) -> dict:
    """Un passo di riconciliazione. Con `prova` nessuna scrittura: i conteggi
    sono quelli che la scrittura produrrebbe. Report: modalità, id in uso,
    coppie fuse trovate fra gli id in uso (`fusi`) e nel catalogo intero
    (`coppie_catalogo`, None se non leggibile), scartate, errori, totali per
    tabella, l'elenco delle coppie con i loro conteggi (o il codice
    d'errore), e le separazioni: rilevate, righe ripristinate, in conflitto e
    conflitti resi definitivi (compresi in `in_conflitto`). Le separazioni si
    trattano prima della rimappatura (docstring del modulo)."""
    report: dict = {
        "modalita": "prova" if prova else "attiva",
        "in_uso": 0,
        "fusi": 0,
        "coppie_catalogo": None,
        "scartate": 0,
        "errori": 0,
        "separazioni_rilevate": 0,
        "ripristinate": 0,
        "in_conflitto": 0,
        "conflitti_definitivi": 0,
        "totali": totali_vuoti(),
        "coppie": [],
    }
    await _separazioni(primary, secondary, report, prova=prova)
    try:
        resp = await primary.rpc("fn_bandi_in_uso", {}).execute()
    except Exception as exc:  # noqa: BLE001 — mai un'eccezione al chiamante
        # Senza gli id in uso non si rimappa nulla; il conteggio del catalogo
        # e le separazioni (che leggono altre fonti) si fanno lo stesso.
        report["errori"] += 1
        codice = _codice(exc)
        if codice in _FUNZIONE_ASSENTE:
            logger.error("rimappatura fusi: fn_bandi_in_uso assente (%s): la migration 0043 "
                         "va applicata prima di accendere la rimappatura", codice)
        else:
            logger.error("rimappatura fusi: bandi in uso non leggibili (%s)", codice)
        ids: list[int] = []
    else:
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
    await _coppie_catalogo(secondary, report)
    return report


def riassunto(report: dict, max_coppie: int = MAX_COPPIE_NEL_LOG) -> dict:
    """Il report per il log: tutto tranne le coppie oltre le prime `max_coppie`."""
    coppie = report.get("coppie") or []
    breve = {chiave: valore for chiave, valore in report.items() if chiave != "coppie"}
    breve["coppie"] = coppie[:max_coppie]
    if len(coppie) > max_coppie:
        breve["coppie_omesse"] = len(coppie) - max_coppie
    return breve
