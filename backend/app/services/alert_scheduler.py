"""Scheduler in-process degli alert nuovi-bandi.

Un task asyncio avviato nel lifespan: uvicorn gira come PROCESSO SINGOLO
(Dockerfile senza --workers), quindi nessuna dipendenza di scheduling. Il
CLAIM della run è l'insert su bando_alert_runs (PK giorno): se in futuro i
processi diventassero più di uno, il 23505 fa da guardia — esegue al massimo
uno. Al riavvio c'è il catch-up: se la run di oggi manca ed è già passata
l'ora di invio, parte subito.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.services import bando_alert_service

logger = logging.getLogger("bandofit.alert_scheduler")

# Dormire a blocchi rende il loop reattivo a sveglie anomale e cambi d'orologio.
_MAX_SLEEP_SECONDS = 3600


def prossima_esecuzione(adesso: datetime, ora_invio: str, fuso: ZoneInfo) -> datetime:
    """Il prossimo istante (aware) in cui tentare la run: oggi alle HH:MM
    locali se non ancora passate, altrimenti domani. DST-safe: l'orario a
    muro resta quello configurato attraverso i cambi di ora legale."""
    locale = adesso.astimezone(fuso)
    ore, minuti = (int(parte) for parte in ora_invio.split(":"))
    obiettivo = locale.replace(hour=ore, minute=minuti, second=0, microsecond=0)
    if obiettivo <= locale:
        domani = (locale + timedelta(days=1)).date()
        obiettivo = datetime.combine(domani, time(ore, minuti), tzinfo=fuso)
    return obiettivo


async def claim_run(primary, giorno: date) -> bool:
    """Rivendica la run del giorno: True = tocca a noi; False = già
    eseguita/in corso altrove (23505 sulla PK)."""
    try:
        await primary.table("bando_alert_runs").insert(
            {"giorno": giorno.isoformat()}
        ).execute()
        return True
    except APIError as exc:
        if exc.code == "23505":
            return False
        raise


async def esegui_se_dovuto(primary, secondary, adesso: datetime) -> dict | None:
    """Esegue la run di oggi se è passata l'ora di invio e nessuno l'ha già
    rivendicata. Copre sia il tick giornaliero sia il catch-up al riavvio."""
    settings = get_settings()
    fuso = ZoneInfo(settings.alert_fuso)
    locale = adesso.astimezone(fuso)
    ore, minuti = (int(parte) for parte in settings.alert_ora_invio.split(":"))
    if (locale.hour, locale.minute) < (ore, minuti):
        return None
    oggi = locale.date()
    if not await claim_run(primary, oggi):
        return None
    return await bando_alert_service.esegui_run(primary, secondary, oggi)


async def run_forever(primary, secondary) -> None:
    """Loop dello scheduler. Non muore MAI in silenzio: qualunque errore
    viene loggato e il loop riprova."""
    settings = get_settings()
    if not (settings.smtp_host or settings.resend_api_key):
        logger.warning(
            "alert bandi: nessun provider email configurato — gli invii "
            "finiranno nel fallback log-only (contano come inviati)"
        )
    while True:
        try:
            await esegui_se_dovuto(primary, secondary, datetime.now(ZoneInfo("UTC")))
            prossima = prossima_esecuzione(
                datetime.now(ZoneInfo("UTC")),
                get_settings().alert_ora_invio,
                ZoneInfo(get_settings().alert_fuso),
            )
            while True:
                resta = (prossima - datetime.now(ZoneInfo("UTC"))).total_seconds()
                if resta <= 0:
                    break
                await asyncio.sleep(min(resta, _MAX_SLEEP_SECONDS))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "scheduler alert: errore inatteso, riprovo tra un'ora", exc_info=True
            )
            await asyncio.sleep(_MAX_SLEEP_SECONDS)
