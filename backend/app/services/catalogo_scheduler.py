"""Scheduler in-process del catalogo bandi: rimappatura periodica dei bandi
fusi nel DB primario (`rimappatura_fusi.passo`, contratto DB bandi §6.2,
migration 0043).

Stesso stampo degli altri scheduler: un task asyncio avviato nel lifespan,
SOLO con `rimappatura_fusi_modalita` diversa da `spenta` (default).
- `prova`: il passo conta senza scrivere e il riassunto del report va nel log;
- `attiva`: il passo scrive (e il riassunto va comunque nel log).
Il riassunto va a WARNING se il passo ha errori o coppie scartate, altrimenti
a INFO. Il primo passo parte all'avvio, poi uno ogni
`rimappatura_fusi_intervallo_minuti` (minimo 5). Nessun claim a DB: il passo
è idempotente e le scritture si serializzano nella RPC. Il loop non muore
mai in silenzio: un errore imprevisto si scrive nel log e si riprova al giro
dopo.
"""

import asyncio
import logging

from app.core.config import INTERVALLO_RIMAPPATURA_MINIMO, get_settings
from app.services import rimappatura_fusi

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
    anomalo = (report.get("errori") or 0) > 0 or (report.get("scartate") or 0) > 0
    logger.log(
        logging.WARNING if anomalo else logging.INFO,
        "rimappatura fusi (%s): %s", modalita, rimappatura_fusi.riassunto(report),
    )
    return report


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
