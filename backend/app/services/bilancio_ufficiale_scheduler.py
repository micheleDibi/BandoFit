"""Failsafe del bilancio ufficiale: task in-process che, ogni 10 minuti, fa
avanzare le richieste aperte (`in_invio`, `in_lavorazione`, `esito_ignoto`).

Il follower lanciato dopo la POST si perde a un riavvio e il poll-on-read
parte solo se qualcuno riapre la pagina: senza questo giro una richiesta
potrebbe restare aperta per sempre, con l'unità consumata e l'azienda
bloccata (una sola richiesta aperta). Qui non c'è logica propria: per ogni
riga si chiama `bilancio_ufficiale_service.avanza`, che prende il claim a DB
(un solo poller per richiesta, anche con il follower e le letture) e decide
completamento, rimborso o scadenza con le soglie del servizio.

Si interrogano solo le richieste nate nell'ambiente openapi in uso (colonna
`sandbox`): dopo un cambio di `OPENAPI_ENV` il provider dell'altro ambiente
non le conosce e le chiuderebbe (o rimborserebbe) a torto. Restano aperte,
contate nel log a ogni giro.

Parte dal lifespan solo con `bilanci_storico_attivo`. Con openapi non
configurato non fa nulla (un WARNING all'avvio): senza sentire il provider
chiuderebbe come scadute richieste forse pronte e già pagate. Non solleva
mai verso il chiamante (a parte la cancellazione del task) e nei log scrive
solo conteggi, id e codici: mai P.IVA.
"""

import asyncio
import logging

from app.services import bilancio_ufficiale_service

logger = logging.getLogger("bandofit.bilancio_ufficiale_scheduler")

INTERVALLO_SECONDS = 10 * 60
# Richieste esaminate per giro, dalle più vecchie (le più vicine alle
# scadenze). Le aperte sono poche per costruzione (tetto giornaliero della
# piattaforma nella RPC di creazione): il resto al giro dopo.
MAX_RICHIESTE_PASSO = 100


async def _attendi(secondi: float) -> None:
    """Attesa tra due giri (sostituibile nei test)."""
    await asyncio.sleep(secondi)


async def _conta_altro_ambiente(primary, sandbox: bool) -> int:
    """Richieste aperte nate nell'altro ambiente openapi (sandbox o
    produzione, colonna `sandbox`): il giro non le interroga, perché il
    provider di questo ambiente non le conosce e le chiuderebbe o
    rimborserebbe a torto. Solo per il log; 0 se la lettura non riesce."""
    try:
        resp = (
            await primary.table("company_bilancio_richieste")
            .select("id")
            .in_("stato", list(bilancio_ufficiale_service.STATI_APERTI))
            .eq("sandbox", not sandbox)
            .limit(MAX_RICHIESTE_PASSO)
            .execute()
        )
    except Exception:
        return 0
    return len(resp.data or [])


async def esegui_passo(primary, openapi) -> int:
    """Un giro: fa avanzare le richieste aperte dell'ambiente openapi in uso
    (le altre si saltano, contate nel log). Ritorna quante ne ha esaminate
    (0 anche se la lettura non riesce, o se openapi non è configurato)."""
    if not openapi.enabled:
        return 0
    sandbox = bool(openapi.sandbox)
    try:
        resp = (
            await primary.table("company_bilancio_richieste")
            .select(bilancio_ufficiale_service.RICHIESTA_SELECT)
            .in_("stato", list(bilancio_ufficiale_service.STATI_APERTI))
            .eq("sandbox", sandbox)
            .order("created_at")
            .limit(MAX_RICHIESTE_PASSO)
            .execute()
        )
    except Exception as exc:
        logger.error(
            "bilancio ufficiale: failsafe, richieste aperte non lette (%s, code=%s)",
            type(exc).__name__, getattr(exc, "code", None),
        )
        return 0
    righe = resp.data or []
    for riga in righe:
        try:
            await bilancio_ufficiale_service.avanza(primary, openapi, riga)
        except Exception as exc:  # avanza non solleva: difesa in profondità
            logger.error(
                "bilancio ufficiale: failsafe, richiesta %s non avanzata (%s)",
                riga.get("id"), type(exc).__name__,
            )
    saltate = await _conta_altro_ambiente(primary, sandbox)
    if righe or saltate:
        logger.info(
            "bilancio ufficiale: failsafe, %s richieste aperte esaminate, %s saltate perché "
            "nate nell'altro ambiente openapi",
            len(righe), saltate,
        )
    return len(righe)


async def run_forever(primary, openapi) -> None:
    if not openapi.enabled:
        # Il client non si abilita senza un riavvio: inutile girare a vuoto.
        logger.warning(
            "bilancio ufficiale: failsafe non avviato, openapi non configurato: le richieste "
            "aperte non avanzano"
        )
        return
    while True:
        try:
            await esegui_passo(primary, openapi)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("bilancio ufficiale: failsafe, errore inatteso (%s)", type(exc).__name__)
        await _attendi(INTERVALLO_SECONDS)
