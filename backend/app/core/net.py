"""IP del client dietro i proxy, per il rate limiting degli endpoint auth.

Perché non `request.client.host`: il backend gira in Docker con il mapping
127.0.0.1:3002 → 8000, quindi il peer TCP che uvicorn vede è il gateway della
bridge (172.x.x.1), IDENTICO per ogni utente del pianeta. Usarlo come chiave
significherebbe un unico bucket condiviso: il primo abusatore bloccherebbe
tutti gli altri.

E nemmeno `FORWARDED_ALLOW_IPS=*`, che sarebbe la scorciatoia ovvia: uvicorn in
quel caso (`ProxyHeadersMiddleware`, ramo `always_trust`) prende il PRIMO
elemento di X-Forwarded-For — cioè esattamente quello che il client può
iniettare. Sarebbe peggio di non fare nulla: un IP falsificabile a piacere.

Topologia (docs/deploy.md): client → Cloudflare → nginx dell'host → uvicorn.
Ogni hop APPENDE in coda a X-Forwarded-For (`$proxy_add_x_forwarded_for`),
quindi gli elementi iniettati dal client restano in testa e l'unica parte
attendibile è la coda: l'IP vero è a `-trusted_proxy_hops`. Contare da destra è
ciò che rende l'header non spoofabile.
"""

import ipaddress
import logging

from fastapi import Request

from app.core.config import get_settings

logger = logging.getLogger("bandofit.auth")


def _normalize(value: str) -> str | None:
    """IP normalizzato in forma di bucket, o None se non è un IP valido.

    IPv6 è troncato alla /64: un singolo utente ne ha tipicamente miliardi di
    indirizzi, quindi limitare il /128 sarebbe aggirabile a costo zero.
    """
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def client_ip(request: Request) -> str | None:
    """IP del client, o None se non è determinabile con certezza.

    None è deliberato: meglio nessun limite per IP che un limite su una chiave
    sbagliata e condivisa (che bloccherebbe tutti insieme). Il chiamante degrada
    sugli altri contatori. In sviluppo, senza proxy davanti, è il caso normale.
    """
    settings = get_settings()

    # Cloudflare mette qui l'IP reale, già risolto e senza ambiguità di hop.
    # NB: perché sia attendibile, nginx deve accettare solo gli IP di
    # Cloudflare — altrimenti chi trova l'IP origin lo falsifica (docs/deploy.md).
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        normalized = _normalize(cf_ip)
        if normalized:
            return normalized
        logger.warning("CF-Connecting-IP non è un IP valido: %r", cf_ip)

    hops = settings.trusted_proxy_hops
    if hops <= 0:
        # Nessun proxy dichiarato: il peer È il client (sviluppo locale).
        return _normalize(request.client.host) if request.client else None

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return None

    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    # Guardia sull'indice, non solo sul valore: con meno elementi degli hop
    # attesi, parts[-hops] solleverebbe IndexError → 500 su OGNI registrazione.
    # Header più corto del previsto = catena di proxy diversa da quella
    # dichiarata: non abbiamo un IP attendibile, non ne inventiamo uno.
    if len(parts) < hops:
        logger.warning(
            "X-Forwarded-For con %d elementi, attesi almeno %d (trusted_proxy_hops)",
            len(parts),
            hops,
        )
        return None

    return _normalize(parts[-hops])
