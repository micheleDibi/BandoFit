"""Helper condiviso della paginazione a offset (`services/paginazione.py`):
una pagina oltre le righe risponde vuota con il totale esatto (il catalogo e
il primario rispondono `PGRST103` a un offset oltre il conteggio, contratto
DB bandi §8); ogni altro errore risale invariato; il percorso normale non
cambia. Query finta che si comporta come PostgREST con `count=exact`."""

from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.services import paginazione


def errore(codice: str) -> APIError:
    return APIError({"message": "Requested range not satisfiable", "code": codice,
                     "hint": None, "details": None})


class FakeQuery:
    """Una query con `totale` righe: risponde come PostgREST con il conteggio
    esatto (pagina vuota a offset uguale al totale, `PGRST103` oltre)."""

    def __init__(self, registro: list, totale: int, guasto: Exception | None = None):
        self.registro = registro
        self.totale = totale
        self.guasto = guasto
        self.intervallo: tuple[int, int] | None = None
        self.massimo: int | None = None

    def range(self, inizio, fine):
        self.intervallo = (inizio, fine)
        return self

    def limit(self, n):
        self.massimo = n
        return self

    async def execute(self):
        self.registro.append(("range", self.intervallo) if self.intervallo is not None
                             else ("limit", self.massimo))
        if self.guasto is not None:
            raise self.guasto
        if self.intervallo is None:
            righe = list(range(min(self.massimo or self.totale, self.totale)))
        else:
            inizio, fine = self.intervallo
            if inizio > self.totale:
                raise errore("PGRST103")
            righe = list(range(inizio, min(fine + 1, self.totale)))
        return SimpleNamespace(data=[{"id": i} for i in righe], count=self.totale)


def catalogo(totale: int, guasto: Exception | None = None):
    """Factory come quelle dei servizi: un builder NUOVO a ogni chiamata, più
    il registro delle richieste eseguite."""
    registro: list = []
    return (lambda: FakeQuery(registro, totale, guasto)), registro


class TestPagina:
    async def test_pagina_normale_una_sola_richiesta(self):
        costruisci, registro = catalogo(25)
        righe, totale = await paginazione.pagina(costruisci, 10, 10)
        assert [r["id"] for r in righe] == list(range(10, 20)) and totale == 25
        assert registro == [("range", (10, 19))]

    async def test_ultima_pagina_parziale(self):
        costruisci, registro = catalogo(25)
        righe, totale = await paginazione.pagina(costruisci, 20, 10)
        assert [r["id"] for r in righe] == [20, 21, 22, 23, 24] and totale == 25
        assert registro == [("range", (20, 29))]

    async def test_offset_uguale_al_totale_pagina_vuota_senza_ripiego(self):
        costruisci, registro = catalogo(20)
        righe, totale = await paginazione.pagina(costruisci, 20, 10)
        assert (righe, totale) == ([], 20)
        assert registro == [("range", (20, 29))]

    async def test_offset_oltre_il_totale_vuota_con_il_conteggio_riletto(self):
        # Il totale è calato fra due letture: righe vuote e conteggio reale,
        # riletto con una sola richiesta senza offset.
        costruisci, registro = catalogo(3)
        righe, totale = await paginazione.pagina(costruisci, 40, 20)
        assert (righe, totale) == ([], 3)
        assert registro == [("range", (40, 59)), ("limit", 1)]

    async def test_nessuna_riga_rimasta(self):
        costruisci, registro = catalogo(0)
        righe, totale = await paginazione.pagina(costruisci, 20, 10)
        assert (righe, totale) == ([], 0)
        assert registro == [("range", (20, 29)), ("limit", 1)]

    @pytest.mark.parametrize("codice", ["57014", "42703", "PGRST205"])
    async def test_altri_errori_risalgono_invariati(self, codice):
        costruisci, registro = catalogo(5, guasto=errore(codice))
        with pytest.raises(APIError) as info:
            await paginazione.pagina(costruisci, 0, 10)
        assert info.value.code == codice
        assert registro == [("range", (0, 9))]

    async def test_errore_non_postgrest_risale(self):
        costruisci, _ = catalogo(5, guasto=RuntimeError("rete"))
        with pytest.raises(RuntimeError):
            await paginazione.pagina(costruisci, 0, 10)

    async def test_dati_e_conteggio_assenti_valgono_vuoto(self):
        class Vuota:
            def range(self, *a):
                return self

            async def execute(self):
                return SimpleNamespace(data=None, count=None)

        assert await paginazione.pagina(Vuota, 0, 10) == ([], 0)

    async def test_builder_nuovo_a_ogni_richiesta(self):
        # La factory viene chiamata due volte sul ripiego: mai riusato il
        # builder con il range già impostato.
        creati: list[FakeQuery] = []
        registro: list = []

        def costruisci():
            creati.append(FakeQuery(registro, 0))
            return creati[-1]

        await paginazione.pagina(costruisci, 10, 10)
        assert len(creati) == 2 and creati[1].intervallo is None and creati[1].massimo == 1
