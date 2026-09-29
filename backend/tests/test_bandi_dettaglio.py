"""Test del dettaglio bando (R0-b): risoluzione dello slug (corrente, 301,
fusione, 410, 404) per `fetch_bando_by_slug` e `fetch_bando_for_ai`, colonne
`fonte_ufficiale_*` nella select e nella mappatura, endpoint
`GET /api/v1/bandi/{slug}`."""

import inspect
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from postgrest.exceptions import APIError

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import bandi as bandi_router
from app.core.errors import BandoRitiratoError, NotFoundError, register_exception_handlers
from app.schemas.bando import LookupsOut
from app.services.bandi_service import (
    DETAIL_SELECT,
    fetch_bando_by_slug,
    fetch_bando_for_ai,
    map_detail,
)
from tests.test_bandi_risoluzione import FakeSecondary

USER_ID = "a0000000-0000-0000-0000-000000000001"

FONTE_CAMPI = (
    "fonte_ufficiale_url",
    "fonte_ufficiale_host",
    "fonte_ufficiale_tipo",
    "fonte_ufficiale_stato",
    "fonte_ufficiale_verificata_at",
)


def _riga(bando_id: int, slug: str, **extra) -> dict:
    """Riga di dettaglio minima e pulita (contenuto doppio-encodato di
    proposito: deve arrivare normalizzato)."""
    return {
        "id": bando_id,
        "slug": slug,
        "titolo": f"Bando {slug}",
        "titolo_breve": None,
        "descrizione_breve": None,
        "stato_bando": "aperto",
        "livello": "flash_bando",
        "data_pubblicazione": "2026-05-26",
        "data_apertura": None,
        "data_scadenza": "2026-12-31",
        "importo_totale_eur": None,
        "importo_max_per_progetto_eur": None,
        "ente_erogatore": "Regione",
        "tipologie_bando": None,
        "modalita_erogazione": None,
        "bando_regioni": [],
        "area_geografica": None,
        "tematica": [],
        "link_bando": "https://bandi.regione.piemonte.it/bando/1",
        "link_candidatura": None,
        "contenuto": '{"sections": [{"type": "paragraph", "text": "testo"}]}',
        "allegati": [],
        "fonte_ufficiale_url": "https://www.regione.piemonte.it/bando/1",
        "fonte_ufficiale_host": "www.regione.piemonte.it",
        "fonte_ufficiale_tipo": "ente",
        "fonte_ufficiale_stato": "trovata",
        "fonte_ufficiale_verificata_at": "2026-09-20T10:00:00+00:00",
        "programmi": None,
        "bando_settori": [],
        "bando_beneficiari": [],
        "bando_codici_ateco": [],
        **extra,
    }


CORRENTE = _riga(1, "bando-a")
MASTER = _riga(42, "bando-master")


def _catalogo(*, storico: list | None = None, fusione: list | None = None, fail=None):
    """Secondario con due bandi vivi (`bando-a`, `bando-master`) letti per
    slug o per id, più le tabelle di risoluzione."""

    def bando(filters):
        return [
            r
            for r in (CORRENTE, MASTER)
            if ("slug" in filters and r["slug"] == filters["slug"])
            or ("id" in filters and r["id"] == filters["id"])
        ]

    return FakeSecondary(
        {
            "bando": bando,
            "bando_slug_storico": storico or [],
            "bando_fusione": fusione or [],
        },
        fail=fail,
    )


STORICO_301 = [{"slug": "vecchio", "bando_id": 42, "esito": "301"}]
STORICO_410 = [{"slug": "ritirato", "bando_id": 42, "esito": "410"}]
FUSIONE = [{"bando_id": 7, "master_id": 42, "master_slug": "bando-master"}]


def _colonne_top(select: str) -> list[str]:
    """Colonne di primo livello della select (gli embed restano interi)."""
    out, depth, cur = [], 0, ""
    for ch in select:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    out.append(cur)
    return out


# ------------------------------------------------------------------- select

class TestDetailSelect:
    def test_contiene_le_colonne_della_fonte_ufficiale(self):
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in FONTE_CAMPI:
            assert campo in colonne

    def test_fonte_dopo_allegati_e_prima_degli_embed(self):
        colonne = _colonne_top(DETAIL_SELECT)
        i_allegati = colonne.index("allegati")
        assert colonne[i_allegati + 1 : i_allegati + 6] == list(FONTE_CAMPI)
        primo_embed = next(i for i, c in enumerate(colonne) if "(" in c)
        assert colonne.index("fonte_ufficiale_verificata_at") < primo_embed

    def test_conserva_le_colonne_storiche(self):
        # Le usa anche il modulo partenariati: nessuna colonna tolta.
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in (
            "id", "slug", "titolo", "titolo_breve", "descrizione_raw", "descrizione_breve",
            "stato_bando", "livello", "data_pubblicazione", "data_apertura", "data_scadenza",
            "importo_totale_eur", "importo_max_per_progetto_eur", "ente_erogatore",
            "area_geografica", "tematica", "link_bando", "link_candidatura", "contenuto",
            "allegati",
        ):
            assert campo in colonne
        assert "programmi(id,nome)" in colonne
        assert "bando_codici_ateco(codici_ateco(id,codice,descrizione))" in colonne

    def test_senza_colonne_della_sola_vista(self):
        # Esistono solo su `bando_pubblico` (fase c): su `bando` darebbero 42703.
        assert "stato_effettivo" not in DETAIL_SELECT
        assert "fonte_ufficiale_e_atto" not in DETAIL_SELECT


# ------------------------------------------------------ fetch_bando_by_slug

class TestFetchBandoBySlug:
    async def test_slug_corrente_una_query(self):
        db = _catalogo()
        detail = await fetch_bando_by_slug(db, "bando-a")
        assert detail.id == 1
        assert detail.slug == "bando-a"
        assert db.tabelle() == ["bando"]
        assert db.ops[0][1] == DETAIL_SELECT
        assert db.ops[0][2] == {"slug": "bando-a", "stato_processing": "completed", "__limit": 1}

    async def test_slug_301_dettaglio_del_master_con_slug_canonico(self):
        db = _catalogo(storico=STORICO_301)
        detail = await fetch_bando_by_slug(db, "vecchio")
        assert detail.id == 42
        assert detail.slug == "bando-master"
        assert db.tabelle() == ["bando", "bando_slug_storico", "bando"]
        # La riletta del master usa la stessa select del dettaglio.
        assert db.ops[2][1] == DETAIL_SELECT

    async def test_slug_fuso_dettaglio_del_master(self):
        db = _catalogo(fusione=FUSIONE)
        detail = await fetch_bando_by_slug(db, "doppione")
        assert detail.id == 42
        assert detail.slug == "bando-master"

    async def test_slug_ritirato_410(self):
        db = _catalogo(storico=STORICO_410, fusione=FUSIONE)
        with pytest.raises(BandoRitiratoError):
            await fetch_bando_by_slug(db, "ritirato")

    async def test_slug_sconosciuto_404(self):
        with pytest.raises(NotFoundError) as exc:
            await fetch_bando_by_slug(_catalogo(), "inesistente")
        assert exc.value.message == "Bando non trovato"

    async def test_fonte_ufficiale_mappata(self):
        detail = await fetch_bando_by_slug(_catalogo(), "bando-a")
        assert detail.fonte_ufficiale_url == "https://www.regione.piemonte.it/bando/1"
        assert detail.fonte_ufficiale_host == "www.regione.piemonte.it"
        assert detail.fonte_ufficiale_tipo == "ente"
        assert detail.fonte_ufficiale_stato == "trovata"
        assert detail.fonte_ufficiale_verificata_at == datetime(
            2026, 9, 20, 10, 0, tzinfo=timezone.utc
        )


# ------------------------------------------------------- fetch_bando_for_ai

class TestFetchBandoForAi:
    def test_firma_invariata(self):
        sig = inspect.signature(fetch_bando_for_ai)
        assert list(sig.parameters) == ["secondary", "slug"]
        assert sig.return_annotation in (dict, "dict")

    async def test_slug_corrente(self):
        db = _catalogo()
        row = await fetch_bando_for_ai(db, "bando-a")
        assert isinstance(row, dict)
        assert row["id"] == 1
        assert row["slug"] == "bando-a"
        assert row["contenuto"] == {"sections": [{"type": "paragraph", "text": "testo"}]}
        assert row["fonte_ufficiale_url"] == "https://www.regione.piemonte.it/bando/1"
        assert db.tabelle() == ["bando"]
        assert db.ops[0][1] == DETAIL_SELECT

    async def test_slug_301_riga_del_master(self):
        # ai_check_service salva bando["id"] e bando["slug"]: il check
        # riguarda il master.
        db = _catalogo(storico=STORICO_301)
        row = await fetch_bando_for_ai(db, "vecchio")
        assert isinstance(row, dict)
        assert row["id"] == 42
        assert row["slug"] == "bando-master"
        # Contenuto normalizzato anche sul percorso risolto.
        assert row["contenuto"] == {"sections": [{"type": "paragraph", "text": "testo"}]}
        assert db.tabelle() == ["bando", "bando_slug_storico", "bando"]

    async def test_slug_fuso_riga_del_master(self):
        row = await fetch_bando_for_ai(_catalogo(fusione=FUSIONE), "doppione")
        assert (row["id"], row["slug"]) == (42, "bando-master")

    async def test_slug_ritirato_410(self):
        with pytest.raises(BandoRitiratoError):
            await fetch_bando_for_ai(_catalogo(storico=STORICO_410), "ritirato")

    async def test_slug_sconosciuto_404(self):
        with pytest.raises(NotFoundError) as exc:
            await fetch_bando_for_ai(_catalogo(), "inesistente")
        assert exc.value.message == "Bando non trovato"

    async def test_non_muta_la_riga_del_client(self):
        await fetch_bando_for_ai(_catalogo(storico=STORICO_301), "vecchio")
        assert isinstance(MASTER["contenuto"], str)


# ---------------------------------------------------------------- mappatura

class TestMapDetailFonteUfficiale:
    def test_campi_assenti_sono_none(self):
        riga = {k: v for k, v in CORRENTE.items() if k not in FONTE_CAMPI}
        detail = map_detail(riga)
        for campo in FONTE_CAMPI:
            assert getattr(detail, campo) is None

    def test_valori_sconosciuti_accettati(self):
        # Tipi str, non Literal: un valore nuovo del produttore non rompe.
        detail = map_detail(
            _riga(1, "bando-a", fonte_ufficiale_tipo="altro", fonte_ufficiale_stato="nuovo")
        )
        assert detail.fonte_ufficiale_tipo == "altro"
        assert detail.fonte_ufficiale_stato == "nuovo"

    def test_verificata_at_con_fuso(self):
        detail = map_detail(
            _riga(1, "bando-a", fonte_ufficiale_verificata_at="2026-09-20T12:00:00+02:00")
        )
        assert detail.fonte_ufficiale_verificata_at == datetime(
            2026, 9, 20, 10, 0, tzinfo=timezone.utc
        )

    def test_json_contiene_i_cinque_campi(self):
        dump = map_detail(CORRENTE).model_dump(mode="json")
        for campo in FONTE_CAMPI:
            assert campo in dump
        assert dump["fonte_ufficiale_verificata_at"].startswith("2026-09-20T10:00:00")


# ----------------------------------------------------------------- endpoint

LOOKUPS_VUOTI = LookupsOut(
    regioni=[], settori=[], beneficiari=[], codici_ateco=[],
    tipologie_bando=[], modalita_erogazione=[], programmi=[],
)


def _client(monkeypatch, secondary) -> httpx.AsyncClient:
    async def lookups(_secondary):
        return LOOKUPS_VUOTI

    async def facets(_primary, _active, _lookups):
        return None

    monkeypatch.setattr(bandi_router, "get_lookups", lookups)
    monkeypatch.setattr(bandi_router, "get_company_facets", facets)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(bandi_router.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": USER_ID}
    app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
        company_id=None, owner_id=USER_ID, editable=True, is_multi=False
    )
    app.dependency_overrides[deps.get_primary] = lambda: object()
    app.dependency_overrides[deps.get_secondary] = lambda: secondary
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TestEndpointDettaglio:
    async def test_200_slug_corrente(self, monkeypatch):
        async with _client(monkeypatch, _catalogo()) as client:
            resp = await client.get("/api/v1/bandi/bando-a")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "bando-a"
        for campo in FONTE_CAMPI:
            assert campo in body
        assert body["fonte_ufficiale_stato"] == "trovata"

    async def test_200_slug_spostato_con_slug_canonico_senza_redirect(self, monkeypatch):
        async with _client(monkeypatch, _catalogo(storico=STORICO_301)) as client:
            resp = await client.get("/api/v1/bandi/vecchio")
        assert resp.status_code == 200
        assert "location" not in resp.headers
        body = resp.json()
        assert body["id"] == 42
        assert body["slug"] == "bando-master"

    async def test_410_slug_ritirato(self, monkeypatch):
        async with _client(monkeypatch, _catalogo(storico=STORICO_410)) as client:
            resp = await client.get("/api/v1/bandi/ritirato")
        assert resp.status_code == 410
        assert resp.json() == {
            "error": {"code": "bando_ritirato", "message": "Questo bando non è più disponibile."}
        }

    async def test_404_slug_sconosciuto(self, monkeypatch):
        async with _client(monkeypatch, _catalogo()) as client:
            resp = await client.get("/api/v1/bandi/inesistente")
        assert resp.status_code == 404
        assert resp.json() == {"error": {"code": "not_found", "message": "Bando non trovato"}}

    async def test_404_se_la_risoluzione_fallisce(self, monkeypatch):
        # Mai un 5xx per colpa di una lettura di risoluzione.
        errore = APIError({"message": "x", "code": "PGRST205", "hint": None, "details": None})
        db = _catalogo(fail={"bando_slug_storico": errore})
        async with _client(monkeypatch, db) as client:
            resp = await client.get("/api/v1/bandi/vecchio")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
