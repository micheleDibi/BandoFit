"""Stampa le richieste R1-R8 del contratto DB bandi (§12) come le costruisce
il codice corrente: senza rete e senza credenziali.

Perché: la conferma scritta del passo c2 (contratto §10.1) elenca le richieste
R1-R8. I log dell'applicazione non riportano la query string (per scelta: le
query del DB primario contengono dati riservati) e quelli del pannello del
catalogo vanno confrontati con ciò che il codice manda davvero. Questo script
esegue le FUNZIONI REALI dei servizi contro un client PostgREST vero, con un
trasporto httpx finto che registra ogni richiesta e risponde vuoto: nessuna
connessione, nessuna chiave (l'indirizzo è fittizio) e nessun header stampato
oltre a `Prefer`. Il DB primario, dove serve (preferiti, calendario), è un
finto minimo. Nessuna copia a mano delle query: cambia il codice, cambia la
stampa.

Uso (da `backend/`):

    .venv/bin/python -m scripts.stampa_richieste_catalogo
    .venv/bin/python -m scripts.stampa_richieste_catalogo --come-inviata

Per ogni richiesta stampa metodo, percorso e query string (valori d'esempio:
slug `esempio-di-slug`, id 1 e 2, la data di Roma di oggi) e l'header `Prefer`
se presente. Di default la query è decodificata (`select=id,slug,…`, spazi
compresi: la forma del §12); con `--come-inviata` è quella sul filo, dove il
client codifica `,` `(` `)` come `%2C` `%28` `%29` e gli spazi come `+`, cioè
quella che compare nei log del gateway.
"""

import asyncio
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

import httpx
from postgrest import AsyncPostgrestClient

from app.api.deps import ActiveCompany
from app.core.config import Settings
from app.core.errors import AppError
from app.services import bando_alert_service, calendar_service, lookup_service, saved_bandi_service
from app.services.bandi_risoluzione import VISTA_BANDI
from app.services.bandi_service import (
    DEFAULT_SORT,
    BandiFilters,
    fetch_bandi,
    fetch_bando_by_slug,
    today_italy,
)
from app.services.compatibility import CompanyFacets

# Indirizzo fittizio: nessuna richiesta lo raggiunge (trasporto finto).
INDIRIZZO = "https://catalogo.invalid/rest/v1"
SLUG = "esempio-di-slug"
UTENTE = "00000000-0000-0000-0000-000000000001"
PAGINA = 20
_ADESSO = datetime.now(timezone.utc).isoformat()

# Riga minima restituita alla sola lettura di dettaglio per slug, così i passi
# che la seguono (righe `bando_link`, evento di calendario) partono davvero.
RIGA_DETTAGLIO = {"id": 1, "slug": SLUG, "titolo": "Esempio", "data_scadenza": "2026-12-31"}

# Il DB primario finto: due preferiti (uno sparito dal catalogo), nessun evento.
RIGHE_PRIMARIO = {
    "saved_bandi": [
        {"id": "s1", "bando_id": 1, "bando_slug": SLUG, "bando_titolo": "Esempio 1",
         "data_scadenza": "2026-12-31", "stato_bando": "aperto", "created_at": _ADESSO},
        {"id": "s2", "bando_id": 2, "bando_slug": "altro-esempio", "bando_titolo": "Esempio 2",
         "data_scadenza": None, "stato_bando": None, "created_at": _ADESSO},
    ],
}


@dataclass
class Richiesta:
    metodo: str
    percorso: str
    query: str  # come inviata
    prefer: str | None

    def testo(self, come_inviata: bool) -> str:
        # `unquote_plus`: il client manda gli spazi come `+`.
        query = self.query if come_inviata else unquote_plus(self.query)
        riga = f"{self.metodo} {self.percorso}" + (f"?{query}" if query else "")
        return riga + (f"\n   Prefer: {self.prefer}" if self.prefer else "")


class Catalogo:
    """Client PostgREST vero su un trasporto finto: registra e risponde vuoto."""

    def __init__(self) -> None:
        self.richieste: list[Richiesta] = []
        sessione = httpx.AsyncClient(
            base_url=INDIRIZZO, transport=httpx.MockTransport(self._rispondi)
        )
        self._client = AsyncPostgrestClient(INDIRIZZO, http_client=sessione)

    def table(self, nome: str):
        return self._client.from_(nome)

    def _rispondi(self, richiesta: httpx.Request) -> httpx.Response:
        query = richiesta.url.query.decode()
        self.richieste.append(
            Richiesta(richiesta.method, richiesta.url.path, query, richiesta.headers.get("prefer"))
        )
        righe: list[dict] = []
        if richiesta.url.path.endswith("/" + VISTA_BANDI) and f"slug=eq.{SLUG}" in query:
            righe = [RIGA_DETTAGLIO]
        return httpx.Response(200, json=righe, headers={"Content-Range": f"*/{len(righe)}"})

    def preleva(self) -> list[Richiesta]:
        richieste, self.richieste = self.richieste, []
        return richieste


class _QueryPrimario:
    """Accetta qualunque catena di metodi; `execute` risponde con le righe
    della tabella, o con la riga inserita completata dei campi generati."""

    def __init__(self, righe: list[dict], payload: dict | None = None) -> None:
        self._righe = righe
        self._payload = payload

    def __getattr__(self, nome: str):
        if nome == "not_":
            return self

        def metodo(*args, **kwargs):
            if nome == "insert":
                return _QueryPrimario(self._righe, payload=dict(args[0]))
            return self

        return metodo

    async def execute(self) -> SimpleNamespace:
        if self._payload is not None:
            riga = {"id": "e1", "ora_inizio": None, "ora_fine": None, "note": None,
                    "created_at": _ADESSO, "updated_at": _ADESSO, **self._payload}
            return SimpleNamespace(data=[riga], count=1)
        return SimpleNamespace(data=list(self._righe), count=len(self._righe))


class Primario:
    def table(self, nome: str) -> _QueryPrimario:
        return _QueryPrimario(RIGHE_PRIMARIO.get(nome, []))


def _predefinito(nome: str):
    """Valore di default di un'impostazione, senza leggere l'ambiente."""
    return Settings.model_fields[nome].default


async def scenari(catalogo: Catalogo) -> list[tuple[str, list[Richiesta], str | None]]:
    """(titolo, richieste registrate, esito se la funzione ha risposto con un
    errore atteso) per ogni percorso del §12, eseguito con il codice reale."""
    primario = Primario()
    attivo = ActiveCompany(company_id=None, owner_id=UTENTE, editable=True)
    oggi = today_italy()
    tutti_i_filtri = BandiFilters(
        q="innovazione digitale", stato=["aperto", "in apertura prossimamente"],
        livello="guida_bando", tipologie=[1, 2], modalita=[3], programmi=[7],
        regioni=[9, 12], settori=[4], beneficiari=[2], codici_ateco=[76],
        importo_min=100000, importo_max=5000000,
        scadenza_da=oggi, scadenza_a=date(oggi.year + 1, 12, 31),
    )
    facet = CompanyFacets(regioni_ids={12}, settore_id=4, sufficiente=True)
    passi = [
        ("R1/R2 — elenco, prima pagina, ordinamento di default: segmento «non chiusi», "
         "poi «chiusi»", fetch_bandi(catalogo, BandiFilters(), 1, PAGINA, DEFAULT_SORT)),
        ("R1/R2 — come sopra con un profilo aziendale (select <LIST><SCORING>)",
         fetch_bandi(catalogo, BandiFilters(), 1, PAGINA, DEFAULT_SORT,
                     company_facets=facet, totale_regioni=20)),
        ("R3 — tutti i filtri e la ricerca full-text (scadenza più vicina)",
         fetch_bandi(catalogo, tutti_i_filtri, 1, PAGINA, "scadenza_asc")),
        ("R4 — dettaglio per slug, poi le righe `bando_link` della scheda",
         fetch_bando_by_slug(catalogo, SLUG)),
        ("R4 — slug non più corrente: risoluzione su storico e fusioni",
         fetch_bando_by_slug(catalogo, "slug-spostato")),
        ("R5 — preferiti: dati vivi per id, poi le fusioni per gli id spariti",
         saved_bandi_service.list_saved(primario, catalogo, UTENTE, attivo, 1, PAGINA)),
        ("R6 — calendario: snapshot della scadenza",
         calendar_service.create_bando_event(primario, catalogo, UTENTE, attivo, SLUG)),
        ("R7 — candidati degli alert",
         bando_alert_service.carica_candidati(
             catalogo, oggi=oggi,
             attivazione=date.fromisoformat(_predefinito("alert_data_attivazione")),
             orizzonte_giorni=_predefinito("alert_orizzonte_giorni"),
             fuso=ZoneInfo(_predefinito("alert_fuso")),
         )),
        ("R8 — cataloghi (sette richieste in parallelo)", lookup_service._fetch_all(catalogo)),
    ]
    esiti = []
    for titolo, coro in passi:
        esito = None
        try:
            await coro
        except AppError as exc:  # atteso su un catalogo vuoto (es. 404 sul miss)
            esito = f"{exc.status_code} {exc.code}"
        esiti.append((titolo, catalogo.preleva(), esito))
    return esiti


def main(argomenti: list[str]) -> None:
    come_inviata = "--come-inviata" in argomenti
    for titolo, richieste, esito in asyncio.run(scenari(Catalogo())):
        print(f"\n== {titolo} ==" + (f"  [esito: {esito}]" if esito else ""))
        for n, richiesta in enumerate(richieste, start=1):
            print(f"{n}. {richiesta.testo(come_inviata)}")


if __name__ == "__main__":
    main(sys.argv[1:])
