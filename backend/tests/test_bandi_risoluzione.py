"""Test della risoluzione dei miss sul catalogo bandi (R0-b): storico degli
slug (301/410/annullato), fusioni, riletta del master e regola difensiva
sugli errori delle letture di risoluzione — con un fake del secondario per
nome di tabella."""

import logging
from types import SimpleNamespace

import httpx
import pytest
from postgrest.exceptions import APIError

from app.core.errors import BandoRitiratoError, NotFoundError
from app.services import bandi_risoluzione
from app.services.bandi_risoluzione import (
    EsitoSlug,
    Fusione,
    carica_per_slug,
    risolvi_fusioni,
    risolvi_slug,
)

SELECT = "id,slug,titolo"


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, owner, table: str):
        self._owner = owner
        self._table = table
        self._negato = False
        self.select_str: str | None = None
        self.filters: dict = {}

    def select(self, columns, *args, **kwargs):
        self.select_str = columns
        return self

    def eq(self, column, value):
        self.filters[column] = value
        return self

    def in_(self, column, values):
        self.filters[f"{column}__in"] = list(values)
        return self

    @property
    def not_(self):
        self._negato = True
        return self

    def is_(self, column, value):
        chiave = f"{column}__not_is" if self._negato else f"{column}__is"
        self._negato = False
        self.filters[chiave] = value
        return self

    def limit(self, n):
        self.filters["__limit"] = n
        return self

    async def execute(self):
        self._owner.ops.append((self._table, self.select_str, dict(self.filters)))
        guasto = self._owner.fail.get(self._table)
        errore = guasto(self.filters) if callable(guasto) else guasto
        if errore is not None:
            raise errore
        source = self._owner.selects.get(self._table, [])
        rows = source(self.filters) if callable(source) else source
        return SimpleNamespace(data=rows, count=len(rows))


class FakeSecondary:
    """Risposte per tabella in `selects` (lista fissa o callable sui filtri);
    in `fail` l'eccezione da sollevare per tabella (o callable sui filtri che
    la restituisce, None = nessun errore)."""

    def __init__(self, selects: dict | None = None, fail: dict | None = None):
        self.selects = selects or {}
        self.fail = fail or {}
        self.ops: list = []

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def tabelle(self) -> list[str]:
        return [t for t, _, _ in self.ops]

    def filtri(self, table: str) -> list[dict]:
        return [f for t, _, f in self.ops if t == table]


def _pg_error(code: str = "PGRST205", message: str = "errore") -> APIError:
    return APIError({"message": message, "code": code, "hint": None, "details": None})


MASTER = {"id": 42, "slug": "bando-master", "titolo": "Master"}


def _bando_per_id(riga: dict | None = MASTER):
    """`bando`: miss sulla lettura per slug, `riga` sulla riletta per id."""

    def source(filters):
        if "id" in filters and riga is not None:
            return [riga]
        return []

    return source


def _solo_riletta(errore: Exception):
    """Errore solo sulla riletta del master (per id), non sulla prima lettura."""
    return lambda filters: errore if "id" in filters else None


# ------------------------------------------------------ BandoRitiratoError

def test_bando_ritirato_error_default():
    exc = BandoRitiratoError()
    assert exc.status_code == 410
    assert exc.code == "bando_ritirato"
    assert exc.message == "Questo bando non è più disponibile."


# ---------------------------------------------------------- carica_per_slug

async def test_slug_trovato_una_sola_query():
    riga = {"id": 1, "slug": "bando-a", "titolo": "A"}
    db = FakeSecondary({"bando": [riga]})

    out = await carica_per_slug(db, "bando-a", SELECT)

    assert out == riga
    assert out is not riga  # copia, non la riga del client
    assert db.ops == [(
        "bando",
        SELECT,
        {"slug": "bando-a", "stato_processing": "completed", "__limit": 1},
    )]


async def test_storico_301_rilegge_il_master_per_id():
    db = FakeSecondary({
        "bando": _bando_per_id(),
        "bando_slug_storico": [{"slug": "vecchio", "bando_id": 42, "esito": "301"}],
    })

    out = await carica_per_slug(db, "vecchio", SELECT)

    assert out == MASTER
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando"]
    storico = db.ops[1]
    assert storico[1] == "slug,bando_id,esito"
    assert storico[2] == {"slug": "vecchio", "__limit": 1}
    riletta = db.ops[2]
    assert riletta[1] == SELECT
    assert riletta[2] == {
        "id": 42,
        "stato_processing": "completed",
        "slug__not_is": "null",
        "__limit": 1,
    }


async def test_storico_301_master_assente_404():
    db = FakeSecondary({
        "bando": _bando_per_id(None),
        "bando_slug_storico": [{"slug": "vecchio", "bando_id": 42, "esito": "301"}],
    })

    with pytest.raises(NotFoundError) as exc:
        await carica_per_slug(db, "vecchio", SELECT)

    assert exc.value.message == "Bando non trovato"
    # Nessuna catena: dopo la riletta non si risolve di nuovo.
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando"]


async def test_storico_410_ritirato():
    db = FakeSecondary({
        "bando": _bando_per_id(),
        "bando_slug_storico": [{"slug": "ritirato", "bando_id": 42, "esito": "410"}],
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
    })

    with pytest.raises(BandoRitiratoError) as exc:
        await carica_per_slug(db, "ritirato", SELECT)

    assert exc.value.status_code == 410
    assert exc.value.code == "bando_ritirato"
    assert db.tabelle() == ["bando", "bando_slug_storico"]


async def test_storico_annullato_passa_alla_fusione():
    db = FakeSecondary({
        "bando": _bando_per_id(),
        "bando_slug_storico": [{"slug": "doppione", "bando_id": 7, "esito": "annullato"}],
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
    })

    out = await carica_per_slug(db, "doppione", SELECT)

    assert out == MASTER
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione", "bando"]
    fusione = db.ops[2]
    assert fusione[1] == "bando_id,master_id,master_slug"
    assert fusione[2] == {"slug_originale": "doppione", "__limit": 1}
    assert db.ops[3][2]["id"] == 42


async def test_storico_vuoto_passa_alla_fusione():
    db = FakeSecondary({
        "bando": _bando_per_id(),
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
    })

    out = await carica_per_slug(db, "doppione", SELECT)

    assert out == MASTER
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione", "bando"]


@pytest.mark.parametrize("riga_storico", [
    {"slug": "x", "bando_id": "42", "esito": "301"},   # id non intero
    {"slug": "x", "bando_id": None, "esito": "301"},
    {"slug": "x", "bando_id": True, "esito": "301"},   # bool non è un id
    {"slug": "x", "bando_id": 42, "esito": "302"},     # esito sconosciuto
    {"slug": "x", "bando_id": 42, "esito": 301},       # esito non stringa
])
async def test_storico_malformato_passa_alla_fusione(riga_storico):
    db = FakeSecondary({
        "bando": _bando_per_id(),
        "bando_slug_storico": [riga_storico],
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
    })

    out = await carica_per_slug(db, "x", SELECT)

    assert out == MASTER
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione", "bando"]


async def test_niente_da_nessuna_parte_404():
    db = FakeSecondary()

    with pytest.raises(NotFoundError) as exc:
        await carica_per_slug(db, "inesistente", SELECT)

    assert exc.value.status_code == 404
    assert exc.value.code == "not_found"
    assert exc.value.message == "Bando non trovato"
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione"]


@pytest.mark.parametrize("riga_fusione", [
    {"bando_id": 7, "master_id": None, "master_slug": "m"},
    {"bando_id": 7, "master_id": "42", "master_slug": "m"},
    {"bando_id": 7, "master_id": False, "master_slug": "m"},
    {"bando_id": 7},
])
async def test_fusione_malformata_404(riga_fusione):
    db = FakeSecondary({"bando": _bando_per_id(), "bando_fusione": [riga_fusione]})

    with pytest.raises(NotFoundError):
        await carica_per_slug(db, "doppione", SELECT)

    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione"]


async def test_errore_sulla_prima_lettura_propagato():
    errore = _pg_error("57014")
    db = FakeSecondary(fail={"bando": errore})

    with pytest.raises(APIError) as exc:
        await carica_per_slug(db, "bando-a", SELECT)

    assert exc.value is errore
    assert db.tabelle() == ["bando"]


async def test_timeout_sulla_prima_lettura_propagato():
    db = FakeSecondary(fail={"bando": httpx.ReadTimeout("timeout")})

    with pytest.raises(httpx.ReadTimeout):
        await carica_per_slug(db, "bando-a", SELECT)


async def test_errore_sullo_storico_404_senza_fusione():
    db = FakeSecondary(
        {
            "bando": _bando_per_id(),
            "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
        },
        fail={"bando_slug_storico": _pg_error("PGRST205")},
    )

    with pytest.raises(NotFoundError) as exc:
        await carica_per_slug(db, "doppione", SELECT)

    assert exc.value.message == "Bando non trovato"
    assert db.tabelle() == ["bando", "bando_slug_storico"]


async def test_timeout_sullo_storico_404():
    db = FakeSecondary(fail={"bando_slug_storico": httpx.ReadTimeout("timeout")})

    with pytest.raises(NotFoundError):
        await carica_per_slug(db, "doppione", SELECT)


async def test_errore_httpx_sulla_fusione_404():
    db = FakeSecondary(
        {"bando": _bando_per_id()},
        fail={"bando_fusione": httpx.ConnectError("rete giù")},
    )

    with pytest.raises(NotFoundError) as exc:
        await carica_per_slug(db, "doppione", SELECT)

    assert exc.value.message == "Bando non trovato"
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando_fusione"]


async def test_errore_apierror_sulla_fusione_404():
    db = FakeSecondary(fail={"bando_fusione": _pg_error("42501")})

    with pytest.raises(NotFoundError):
        await carica_per_slug(db, "doppione", SELECT)


@pytest.mark.parametrize("errore", [
    _pg_error("42703"),
    httpx.ReadTimeout("timeout"),
])
async def test_errore_sulla_riletta_del_master_404(errore):
    db = FakeSecondary(
        {
            "bando": _bando_per_id(),
            "bando_slug_storico": [{"slug": "vecchio", "bando_id": 42, "esito": "301"}],
        },
        fail={"bando": _solo_riletta(errore)},
    )

    with pytest.raises(NotFoundError) as exc:
        await carica_per_slug(db, "vecchio", SELECT)

    assert exc.value.message == "Bando non trovato"
    assert db.tabelle() == ["bando", "bando_slug_storico", "bando"]


async def test_log_con_tabella_e_codice_slug_troncato(caplog):
    slug = "s" * 500
    errore = _pg_error("PGRST205", message=f"relazione mancante per {slug} dettaglio-riservato")
    db = FakeSecondary(fail={"bando_slug_storico": errore})

    with caplog.at_level(logging.WARNING, logger="bandofit.bandi_risoluzione"):
        with pytest.raises(NotFoundError):
            await carica_per_slug(db, slug, SELECT)

    records = [r for r in caplog.records if r.name == "bandofit.bandi_risoluzione"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    testo = records[0].getMessage()
    assert "tabella=bando_slug_storico" in testo
    assert "codice=PGRST205" in testo
    assert "s" * 100 in testo
    assert "s" * 101 not in testo
    # Mai il messaggio di PostgREST: può riportare lo slug richiesto.
    assert "relazione mancante" not in testo
    assert "dettaglio-riservato" not in testo
    assert records[0].exc_info is None


async def test_log_errore_httpx_riporta_la_classe(caplog):
    db = FakeSecondary(fail={"bando_fusione": httpx.ConnectError("rete giù")})

    with caplog.at_level(logging.WARNING, logger="bandofit.bandi_risoluzione"):
        assert await risolvi_slug(db, "doppione") is None

    testo = caplog.records[-1].getMessage()
    assert "tabella=bando_fusione" in testo
    assert "codice=ConnectError" in testo
    assert "rete giù" not in testo


async def test_slug_con_a_capo_non_spezza_il_log(caplog):
    db = FakeSecondary(fail={"bando_slug_storico": _pg_error("PGRST205")})

    with caplog.at_level(logging.WARNING, logger="bandofit.bandi_risoluzione"):
        await risolvi_slug(db, "riga1\nriga2")

    assert "\n" not in caplog.records[-1].getMessage()


# -------------------------------------------------------------- risolvi_slug

async def test_risolvi_slug_esiti():
    db = FakeSecondary({
        "bando_slug_storico": [{"slug": "vecchio", "bando_id": 42, "esito": "301"}],
    })
    assert await risolvi_slug(db, "vecchio") == EsitoSlug("spostato", 42)

    db = FakeSecondary({
        "bando_slug_storico": [{"slug": "via", "bando_id": 42, "esito": "410"}],
    })
    assert await risolvi_slug(db, "via") == EsitoSlug("ritirato", None)

    db = FakeSecondary({
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}],
    })
    assert await risolvi_slug(db, "doppione") == EsitoSlug("spostato", 42)

    assert await risolvi_slug(FakeSecondary(), "niente") is None


async def test_risolvi_slug_riga_non_dict_passa_alla_fusione():
    db = FakeSecondary({
        "bando_slug_storico": ["non-una-riga"],
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": None}],
    })
    assert await risolvi_slug(db, "x") == EsitoSlug("spostato", 42)


# ----------------------------------------------------------- risolvi_fusioni

async def test_risolvi_fusioni_vuoto_senza_query():
    db = FakeSecondary()
    assert await risolvi_fusioni(db, []) == {}
    assert await risolvi_fusioni(db, iter(())) == {}
    assert await risolvi_fusioni(db, (i for i in [])) == {}
    assert db.ops == []


async def test_risolvi_fusioni_mappa_e_dedup():
    db = FakeSecondary({
        "bando_fusione": [
            {"bando_id": 7, "master_id": 42, "master_slug": "bando-master"},
            {"bando_id": 8, "master_id": 43, "master_slug": None},
        ],
    })

    out = await risolvi_fusioni(db, [7, 8, 7, 9])

    assert out == {
        7: Fusione(42, "bando-master"),
        8: Fusione(43, None),
    }
    assert db.ops == [(
        "bando_fusione",
        "bando_id,master_id,master_slug",
        {"bando_id__in": [7, 8, 9]},
    )]


async def test_risolvi_fusioni_accetta_un_generatore():
    db = FakeSecondary({
        "bando_fusione": [{"bando_id": 7, "master_id": 42, "master_slug": "m"}],
    })

    out = await risolvi_fusioni(db, (i for i in [7]))

    assert out == {7: Fusione(42, "m")}


async def test_risolvi_fusioni_righe_malformate_ignorate():
    db = FakeSecondary({
        "bando_fusione": [
            "non-una-riga",
            None,
            {"bando_id": 1, "master_id": None, "master_slug": "a"},
            {"bando_id": 2, "master_id": "42", "master_slug": "b"},
            {"bando_id": 3, "master_id": True, "master_slug": "c"},
            {"bando_id": "4", "master_id": 42, "master_slug": "d"},
            {"master_id": 42, "master_slug": "e"},
            {"bando_id": 99, "master_id": 42, "master_slug": "non-richiesto"},
            {"bando_id": 5, "master_id": 45, "master_slug": 123},
            {"bando_id": 6, "master_id": 46, "master_slug": ""},
            {"bando_id": 7, "master_id": 47, "master_slug": "buono"},
        ],
    })

    out = await risolvi_fusioni(db, [1, 2, 3, 4, 5, 6, 7])

    assert out == {
        5: Fusione(45, None),
        6: Fusione(46, None),
        7: Fusione(47, "buono"),
    }


@pytest.mark.parametrize("errore", [
    _pg_error("PGRST205"),
    httpx.ReadTimeout("timeout"),
    httpx.HTTPStatusError(
        "502",
        request=httpx.Request("GET", "http://x"),
        response=httpx.Response(502),
    ),
])
async def test_risolvi_fusioni_errore_vuoto(errore, caplog):
    db = FakeSecondary(fail={"bando_fusione": errore})

    with caplog.at_level(logging.WARNING, logger="bandofit.bandi_risoluzione"):
        assert await risolvi_fusioni(db, [7, 8]) == {}

    assert "tabella=bando_fusione" in caplog.records[-1].getMessage()


def test_modulo_non_importa_bandi_service():
    """Niente import circolari: è `bandi_service` a usare questo modulo."""
    import ast
    import inspect

    albero = ast.parse(inspect.getsource(bandi_risoluzione))
    importati: list[str] = []
    for nodo in ast.walk(albero):
        if isinstance(nodo, ast.Import):
            importati += [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom):
            importati += [f"{nodo.module}.{a.name}" for a in nodo.names]
    assert not [n for n in importati if "bandi_service" in n]


# ------------------------------------------------ builder PostgREST reali
#
# Il FakeQuery qui sopra riproduce a mano `not_.is_` e `in_`: questi test
# usano i builder veri di postgrest-py (solo `execute` intercettato, niente
# rete) per fissare i parametri che arrivano davvero a PostgREST.

class RealSecondary:
    def __init__(self, client):
        self._client = client

    def table(self, name: str):
        return self._client.from_(name)


@pytest.fixture
async def postgrest_reale(monkeypatch):
    """(secondario, registro, risposte): `registro` riceve (tabella, params)
    per ogni richiesta; `risposte[tabella]` è una lista o un callable sui
    params."""
    from postgrest import AsyncPostgrestClient

    registro: list = []
    risposte: dict = {}

    async def execute(self):
        tabella = str(self.request.path).rsplit("/", 1)[-1]
        params = self.request.params.multi_items()
        registro.append((tabella, params))
        source = risposte.get(tabella, [])
        rows = source(dict(params)) if callable(source) else source
        return SimpleNamespace(data=rows, count=len(rows))

    async with AsyncPostgrestClient("http://x") as client:
        builder = type(client.from_("bando").select("id"))
        monkeypatch.setattr(builder, "execute", execute)
        yield RealSecondary(client), registro, risposte


async def test_builder_reali_301_rilegge_il_master(postgrest_reale):
    db, registro, risposte = postgrest_reale
    risposte["bando"] = lambda params: [MASTER] if "id" in params else []
    risposte["bando_slug_storico"] = [{"slug": "vecchio", "bando_id": 42, "esito": "301"}]

    assert await carica_per_slug(db, "vecchio", SELECT) == MASTER

    assert registro == [
        ("bando", [
            ("select", SELECT),
            ("slug", "eq.vecchio"),
            ("stato_processing", "eq.completed"),
            ("limit", "1"),
        ]),
        ("bando_slug_storico", [
            ("select", "slug,bando_id,esito"),
            ("slug", "eq.vecchio"),
            ("limit", "1"),
        ]),
        ("bando", [
            ("select", SELECT),
            ("id", "eq.42"),
            ("stato_processing", "eq.completed"),
            ("slug", "not.is.null"),
            ("limit", "1"),
        ]),
    ]


async def test_builder_reali_fusione_per_slug(postgrest_reale):
    db, registro, risposte = postgrest_reale
    risposte["bando_fusione"] = [{"bando_id": 7, "master_id": 42, "master_slug": "m"}]

    assert await risolvi_slug(db, "doppione") == EsitoSlug("spostato", 42)

    assert registro[-1] == ("bando_fusione", [
        ("select", "bando_id,master_id,master_slug"),
        ("slug_originale", "eq.doppione"),
        ("limit", "1"),
    ])


async def test_builder_reali_risolvi_fusioni(postgrest_reale):
    db, registro, risposte = postgrest_reale
    risposte["bando_fusione"] = [
        {"bando_id": 7, "master_id": 42, "master_slug": "bando-master"},
    ]

    out = await risolvi_fusioni(db, [7, 8, 7])

    assert out == {7: Fusione(42, "bando-master")}
    assert registro == [
        ("bando_fusione", [
            ("select", "bando_id,master_id,master_slug"),
            ("bando_id", "in.(7,8)"),
        ]),
    ]
