"""Scheduler in-process del catalogo bandi: rimappatura periodica dei bandi
fusi nel DB primario e ripristino dopo una separazione (`rimappatura_fusi.passo`,
contratto DB bandi §6.2, migration 0043, 0044, 0045 e 0046).

Stesso stampo degli altri scheduler: un task asyncio avviato nel lifespan,
SOLO con `rimappatura_fusi_modalita` diversa da `spenta` (default).
- `prova`: il passo conta senza scrivere e il riassunto del report va nel log;
- `attiva`: il passo scrive (e il riassunto va comunque nel log).
Il riassunto va a WARNING se il passo ha errori, coppie scartate o righe
ripristinate (in prova: da ripristinare), altrimenti a INFO: le separazioni
in attesa della vista, le righe in conflitto e i conflitti resi definitivi
(scelte dell'utente) non lo alzano. Nello stesso giro, dopo la rimappatura e
nella stessa modalità, il riallineamento delle scadenze in calendario
(`calendario_allineamento.passo`, migration 0048: in prova conta senza
scrivere): conteggi a INFO, a WARNING con errori; una sua eccezione si
scrive nel log e non ferma né la rimappatura né il loop. Il primo passo
parte all'avvio, poi uno ogni
`rimappatura_fusi_intervallo_minuti` (minimo 5). Nessun claim a DB: il passo
è idempotente e le scritture si serializzano nella RPC. Il loop non muore
mai in silenzio: un errore imprevisto si scrive nel log e si riprova al giro
dopo.
"""

import asyncio
import logging

from app.core.config import INTERVALLO_RIMAPPATURA_MINIMO, get_settings
from app.services import calendario_allineamento, rimappatura_fusi

logger = logging.getLogger("bandofit.catalogo_scheduler")

MODALITA = ("spenta", "prova", "attiva")
INTERVALLO_MINIMO_MINUTI = INTERVALLO_RIMAPPATURA_MINIMO


def modalita_rimappatura() -> str | None:
    """La modalità configurata; None se il valore non è fra quelli ammessi."""
    modalita = get_settings().rimappatura_fusi_modalita
    return modalita if modalita in MODALITA else None


def intervallo_secondi() -> int:
    minuti = get_settings().rimappatura_fusi_intervallo_minuti
    return max(INTERVALLO_MINIMO_MINUTI, minuti) * 60


async def esegui_passo(primary, secondary) -> dict | None:
    """Un passo nella modalità configurata; None (nessuna lettura) se spenta
    o non valida."""
    modalita = modalita_rimappatura()
    if modalita not in ("prova", "attiva"):
        return None
    report = await rimappatura_fusi.passo(primary, secondary, prova=modalita == "prova")
    # Un report di una versione precedente non ha le chiavi nuove: `.get`.
    anomalo = any(
        (report.get(chiave) or 0) > 0 for chiave in ("errori", "scartate", "ripristinate")
    )
    logger.log(
        logging.WARNING if anomalo else logging.INFO,
        "rimappatura fusi (%s): %s", modalita, rimappatura_fusi.riassunto(report),
    )
    await _riallinea_calendario(primary, secondary, modalita)
    return report


async def _riallinea_calendario(primary, secondary, modalita: str) -> None:
    """Riallineamento delle scadenze in calendario nella modalità del passo.
    Un'eccezione resta qui (nel log solo il tipo, mai il messaggio)."""
    try:
        conteggi = await calendario_allineamento.passo(
            primary, secondary, scrivi=modalita == "attiva"
        )
    except Exception as exc:  # noqa: BLE001 — non ferma la rimappatura né il loop
        logger.error("riallineamento calendario (%s): errore inatteso (%s)",
                     modalita, type(exc).__name__)
        return
    logger.log(
        logging.WARNING if (conteggi.get("errori") or 0) > 0 else logging.INFO,
        "riallineamento calendario (%s): %s", modalita, conteggi,
    )


async def run_forever(primary, secondary) -> None:
    """Loop dello scheduler: un passo, poi l'attesa dell'intervallo."""
    while True:
        try:
            await esegui_passo(primary, secondary)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("catalogo scheduler: errore inatteso nel passo", exc_info=True)
        await asyncio.sleep(intervallo_secondi())
