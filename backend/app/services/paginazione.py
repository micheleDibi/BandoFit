"""Pagina a offset con conteggio esatto, senza 5xx oltre l'ultima riga.

PostgREST (primario e catalogo) con `count=exact` risponde «intervallo non
soddisfacibile» (`PGRST103`) a un offset oltre le righe della query, invece
di una pagina vuota (contratto DB bandi §8). Succede quando il totale cala fra
due letture (righe rimosse da un'altra scheda o dalla rimappatura dei fusi,
code admin che si svuotano) o con una chiamata diretta all'API: l'elenco
risponde vuoto con il totale reale, mai un errore.
"""

from collections.abc import Callable
from typing import Any

from postgrest.exceptions import APIError

CODICE_INTERVALLO_NON_SODDISFACIBILE = "PGRST103"


async def pagina(
    costruisci: Callable[[], Any], offset: int, quante: int
) -> tuple[list[dict], int]:
    """Righe `offset..offset+quante-1` di una query e suo conteggio esatto.

    `costruisci` crea da zero la query a ogni chiamata (`select(...,
    count="exact")`, filtri e ordinamento): i builder di postgrest-py
    accumulano i parametri e non si riusano. Un offset oltre le righe fa
    rispondere `PGRST103`: vale come pagina vuota, e il conteggio si rilegge
    con una richiesta senza offset (`limit(1)`). Ogni altro errore risale
    invariato."""
    try:
        resp = await costruisci().range(offset, offset + quante - 1).execute()
    except APIError as exc:
        if exc.code != CODICE_INTERVALLO_NON_SODDISFACIBILE:
            raise
        resp = await costruisci().limit(1).execute()
        return [], resp.count or 0
    return list(resp.data or []), resp.count or 0
