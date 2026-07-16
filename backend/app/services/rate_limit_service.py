"""Rate limiting durable per gli endpoint auth pubblici (migration 0025).

Sostituisce, per le decisioni che contano, il cooldown in-process di
auth_service: quello è un dict di processo e si azzera a ogni deploy, quindi un
attaccante che aspetta un riavvio lo aggira. Qui il contatore vive nel database,
come il claim dello scheduler alert e il lock di import.

I bucket sono HMAC: a DB non finiscono mai IP o email in chiaro, così la tabella
non diventa né un registro di dati personali né un dizionario di indirizzi
attaccabile offline se qualcuno ne ottiene un dump.
"""

import hashlib
import hmac
import logging

from app.core.config import get_settings

logger = logging.getLogger("bandofit.auth")


def bucket(kind: str, value: str) -> str:
    """Chiave opaca per il contatore: `kind:<hmac>`.

    Il `kind` entra anche nel digest, così lo stesso valore in scope diversi
    (ip/email) non collide mai.
    """
    pepper = get_settings().rate_limit_pepper
    if not pepper:
        # Sviluppo: senza pepper l'HMAC non aggiungerebbe nulla, ma la forma del
        # bucket resta la stessa e il codice di produzione non cambia.
        logger.debug("rate_limit_pepper non configurato: bucket con hash non peppato")
    digest = hmac.new(
        pepper.encode("utf-8"), f"{kind}:{value}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{kind}:{digest[:32]}"


async def allow(primary, bucket_key: str, limit: int, window_seconds: int) -> bool:
    """Conta la richiesta e dice se è consentita.

    FAIL-OPEN: se l'RPC non risponde, la richiesta passa e resta un log di
    errore. Un database che fa i capricci non deve spegnere le registrazioni —
    e il cooldown in-process di auth_service regge comunque come backstop
    degradato. Il rovescio è che un attacco durante un guasto del DB non viene
    limitato: è il compromesso scelto, non una svista.
    """
    try:
        resp = await primary.rpc(
            "fn_consume_auth_rate_limit",
            {
                "p_bucket": bucket_key,
                "p_limit": limit,
                "p_window_seconds": window_seconds,
            },
        ).execute()
        return bool(resp.data)
    except Exception as exc:
        logger.error("Rate limit non applicato su %s: %s", bucket_key, exc)
        return True
