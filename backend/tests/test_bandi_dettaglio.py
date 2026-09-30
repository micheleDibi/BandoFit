"""Test del dettaglio bando: risoluzione dello slug (corrente, 301, fusione,
410, 404) per `fetch_bando_by_slug` e `fetch_bando_for_ai` sulla vista
`bando_pubblico`, select del dettaglio (colonne di ripiego solo da
`COLONNE_RIPIEGO_51`), pulsanti e allegati da `bando_link` con degrado sui
ripieghi, mappatura ed endpoint `GET /api/v1/bandi/{slug}`."""

import inspect
import logging
from datetime import datetime, time, timezone

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
    COLONNE_RIPIEGO_51,
    DETAIL_SELECT,
    _detail_select,
    fetch_bando_by_slug,
    fetch_bando_for_ai,
    map_detail,
)
from app.services.bando_scheda_link import BANDO_LINK_SELECT, TIPI_SCHEDA
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
        "stato_effettivo": "aperto",
        "livello": "flash_bando",
        "data_pubblicazione": "2026-05-26",
        "data_apertura": None,
        "data_scadenza": "2026-12-31",
        "ora_apertura": None,
        "ora_scadenza": "12:00:00",
        "data_pubblicazione_verificata": None,
        "data_apertura_verificata": False,
        "data_scadenza_verificata": True,
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
        "fonte_ufficiale_e_atto": False,
        "programmi": None,
        "bando_settori": [],
        "bando_beneficiari": [],
        "bando_codici_ateco": [],
        **extra,
    }


CORRENTE = _riga(1, "bando-a")
MASTER = _riga(42, "bando-master")

# Righe `bando_link` dei due bandi (tipi della scheda).
LINK = [
    {"id": 10, "bando_id": 1, "url": "https://www.regione.piemonte.it/domanda",
     "dominio": "regione.piemonte.it", "tipo": "candidatura", "etichetta": "Domanda",
     "content_type": None, "ultimo_visto_at": "2026-09-29T08:00:00+00:00"},
    {"id": 11, "bando_id": 1, "url": "https://www.regione.piemonte.it/atto.pdf",
     "dominio": "regione.piemonte.it", "tipo": "atto", "etichetta": "Delibera",
     "content_type": "application/pdf", "ultimo_visto_at": "2026-09-29T08:00:00+00:00"},
    {"id": 20, "bando_id": 42, "url": "https://www.mimit.gov.it/portale",
     "dominio": "mimit.gov.it", "tipo": "portale", "etichetta": None,
     "content_type": None, "ultimo_visto_at": "2026-09-29T08:00:00+00:00"},
]


def _link_di(filters):
    return [r for r in LINK if r["bando_id"] == filters.get("bando_id")]


def _catalogo(*, storico: list | None = None, fusione: list | None = None, fail=None):
    """Secondario con due bandi vivi (`bando-a`, `bando-master`) letti per
    slug o per id, le loro righe `bando_link` e le tabelle di risoluzione."""

    def bando(filters):
        return [
            r
            for r in (CORRENTE, MASTER)
            if ("slug" in filters and r["slug"] == filters["slug"])
            or ("id" in filters and r["id"] == filters["id"])
        ]

    return FakeSecondary(
        {
            "bando_pubblico": bando,
            "bando_link": _link_di,
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

NUOVE_COLONNE = (
    "stato_effettivo", "ora_apertura", "ora_scadenza", "data_pubblicazione_verificata",
    "data_apertura_verificata", "data_scadenza_verificata", "fonte_ufficiale_e_atto",
)
DEPRECATE = ("link_candidatura", "link_bando", "allegati")


class TestDetailSelect:
    def test_contiene_le_colonne_della_fonte_ufficiale(self):
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in FONTE_CAMPI:
            assert campo in colonne

    def test_contiene_le_colonne_della_vista(self):
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in NUOVE_COLONNE:
            assert campo in colonne

    def test_conserva_le_colonne_usate_da_partenariati_e_ai(self):
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in (
            "id", "slug", "titolo", "titolo_breve", "descrizione_breve",
            "stato_bando", "livello", "data_pubblicazione", "data_apertura", "data_scadenza",
            "importo_totale_eur", "importo_max_per_progetto_eur", "ente_erogatore",
            "area_geografica", "tematica", "contenuto",
        ):
            assert campo in colonne
        assert "programmi(id,nome)" in colonne
        assert "bando_codici_ateco(codici_ateco(id,codice,descrizione))" in colonne

    def test_senza_colonne_deprecate_non_di_ripiego(self):
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in ("descrizione_raw", "titolo_raw", "stato_processing"):
            assert campo not in colonne

    def test_ripieghi_solo_da_colonne_ripiego_51(self):
        assert COLONNE_RIPIEGO_51 == DEPRECATE
        assert DETAIL_SELECT == _detail_select(COLONNE_RIPIEGO_51)
        colonne = _colonne_top(DETAIL_SELECT)
        for campo in DEPRECATE:
            assert campo in colonne

    def test_select_valida_con_i_ripieghi_svuotati(self):
        # Fase c2: la tupla si svuota e la select resta ben formata.
        select = _detail_select(())
        colonne = _colonne_top(select)
        assert "" not in colonne
        assert ",," not in select
        for campo in DEPRECATE:
            assert campo not in colonne
        assert colonne[-1] == "bando_codici_ateco(codici_ateco(id,codice,descrizione))"
        assert _colonne_top(DETAIL_SELECT) == [
            c for c in colonne if "(" not in c
        ] + list(DEPRECATE) + [c for c in colonne if "(" in c]


# ------------------------------------------------------ fetch_bando_by_slug

class TestFetchBandoBySlug:
    async def test_slug_corrente_riga_e_link(self):
        db = _catalogo()
        detail = await fetch_bando_by_slug(db, "bando-a")
        assert detail.id == 1
        assert detail.slug == "bando-a"
        assert db.tabelle() == ["bando_pubblico", "bando_link"]
        assert db.ops[0][1] == DETAIL_SELECT
        assert db.ops[0][2] == {"slug": "bando-a", "__limit": 1}
        # bando_link: colonne per nome, solo i tipi della scheda, con tetto.
        assert db.ops[1][1] == BANDO_LINK_SELECT
        assert db.ops[1][2] == {
            "bando_id": 1,
            "tipo__in": list(TIPI_SCHEDA),
            "__order": "id",
            "__limit": 200,
        }

    async def test_slug_301_dettaglio_del_master_con_slug_canonico(self):
        db = _catalogo(storico=STORICO_301)
        detail = await fetch_bando_by_slug(db, "vecchio")
        assert detail.id == 42
        assert detail.slug == "bando-master"
        assert db.tabelle() == [
            "bando_pubblico", "bando_slug_storico", "bando_pubblico", "bando_link"
        ]
        # La riletta del master usa la stessa select del dettaglio.
        assert db.ops[2][1] == DETAIL_SELECT
        # I link sono quelli del master, non dello slug richiesto.
        assert db.ops[3][2]["bando_id"] == 42
        assert detail.cta.origine == "fonte_ufficiale"
        assert detail.allegati == []  # l'atto del bando 1 non c'entra

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

    async def test_pulsanti_e_allegati_da_bando_link_e_ripieghi(self):
        detail = await fetch_bando_by_slug(_catalogo(), "bando-a")
        # La riga `candidatura` vince su fonte ufficiale e link_bando.
        assert detail.cta.model_dump() == {
            "url": "https://www.regione.piemonte.it/domanda",
            "host": "www.regione.piemonte.it",
            "origine": "candidatura",
        }
        assert detail.link_fonte.model_dump() == {
            "url": "https://www.regione.piemonte.it/bando/1",
            "host": "www.regione.piemonte.it",
            "origine": "fonte_ufficiale",
        }
        assert [a.model_dump() for a in detail.allegati] == [{
            "url": "https://www.regione.piemonte.it/atto.pdf",
            "etichetta": "Delibera",
            "tipo": "atto",
            "formato": "pdf",
        }]

    @pytest.mark.parametrize(
        "errore",
        [
            APIError({"message": "x", "code": "42501", "hint": None, "details": None}),
            httpx.ReadTimeout("timeout"),
            RuntimeError("guasto"),
        ],
    )
    async def test_bando_link_in_errore_solo_ripieghi(self, errore, caplog):
        riga = _riga(
            1, "bando-a",
            link_candidatura="https://www.regione.piemonte.it/modulo",
            allegati=[{"label": "Bando", "url": "https://www.regione.piemonte.it/b.pdf",
                       "tipo": "bando"}],
        )
        db = FakeSecondary(
            {"bando_pubblico": [riga]}, fail={"bando_link": errore}
        )
        with caplog.at_level(logging.WARNING, logger="bandofit.bando_scheda_link"):
            detail = await fetch_bando_by_slug(db, "bando-a")
        assert db.tabelle() == ["bando_pubblico", "bando_link"]
        assert detail.cta.origine == "link_candidatura"
        assert detail.cta.url == "https://www.regione.piemonte.it/modulo"
        assert [(a.url, a.etichetta, a.tipo) for a in detail.allegati] == [
            ("https://www.regione.piemonte.it/b.pdf", "Bando", "allegato")
        ]
        [record] = caplog.records
        messaggio = record.getMessage()
        assert "bando_id=1" in messaggio
        assert "https://" not in messaggio


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
        # Nessuna lettura di bando_link: l'input dell'AI-check resta la riga.
        assert db.tabelle() == ["bando_pubblico"]
        assert db.ops[0][1] == DETAIL_SELECT

    async def test_riga_grezza_per_il_meta_invariata(self):
        # D9: il META dell'AI-check legge `stato_bando` e il jsonb `allegati`
        # dalla riga; nessun campo calcolato della scheda vi entra.
        allegati = [{"label": "Bando", "url": "https://www.regione.piemonte.it/b.pdf",
                     "tipo": "bando"}]
        db = FakeSecondary({"bando_pubblico": [_riga(1, "bando-a", allegati=allegati)]})
        row = await fetch_bando_for_ai(db, "bando-a")
        assert row["allegati"] == allegati
        assert row["stato_bando"] == "aperto"
        assert row["link_bando"] == "https://bandi.regione.piemonte.it/bando/1"
        for campo in ("cta", "link_fonte"):
            assert campo not in row

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
        assert db.tabelle() == ["bando_pubblico", "bando_slug_storico", "bando_pubblico"]

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


class TestMapDetailFaseC:
    def test_campi_della_vista(self):
        detail = map_detail(_riga(1, "bando-a", fonte_ufficiale_e_atto=True))
        assert detail.stato_effettivo == "aperto"
        assert detail.ora_apertura is None
        assert detail.ora_scadenza == time(12, 0)
        assert detail.data_pubblicazione_verificata is None
        assert detail.data_apertura_verificata is False
        assert detail.data_scadenza_verificata is True
        assert detail.fonte_ufficiale_e_atto is True

    def test_campi_della_vista_assenti_sono_none(self):
        riga = {k: v for k, v in CORRENTE.items() if k not in NUOVE_COLONNE}
        detail = map_detail(riga)
        for campo in NUOVE_COLONNE:
            assert getattr(detail, campo) is None

    def test_senza_link_e_senza_ripieghi_niente_pulsanti(self):
        riga = {k: v for k, v in CORRENTE.items() if k not in DEPRECATE}
        riga["fonte_ufficiale_stato"] = "in_verifica"
        detail = map_detail(riga)
        assert detail.cta is None
        assert detail.link_fonte is None
        assert detail.allegati == []

    @pytest.mark.parametrize(
        ("valore", "atteso"),
        [
            ("12:00:00", time(12, 0)),
            ("09:30", time(9, 30)),
            ("12:00:00+02", time(12, 0)),  # già ora di Roma: il fuso si toglie
            (" 18:00:00 ", time(18, 0)),
            ("12:30:15.123456", time(12, 30, 15)),  # l'API espone «HH:MM:SS»
            (time(8, 0, 0, 500, tzinfo=timezone.utc), time(8, 0)),
            ("24:00:00", None),  # «entro le ore 24»: nessuna ora = tutta la giornata
            ("24:00", None),
            ("boh", None),
            ("", None),
            (1200, None),
            (None, None),
        ],
    )
    def test_ore_tolleranti(self, valore, atteso):
        detail = map_detail(_riga(1, "bando-a", ora_apertura=valore, ora_scadenza=valore))
        assert detail.ora_apertura == atteso
        assert detail.ora_scadenza == atteso
        assert (detail.ora_scadenza is None) or detail.ora_scadenza.tzinfo is None

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("https://www.facebook.com/regione", "www.regione.piemonte.it"),
            ("javascript:alert(1)", "www.regione.piemonte.it"),
            ("https://www.regione.piemonte.it/bando/1", "www.youtube.com"),
        ],
    )
    def test_campi_della_fonte_filtrati_insieme(self, url, host):
        detail = map_detail(
            _riga(1, "bando-a", fonte_ufficiale_url=url, fonte_ufficiale_host=host)
        )
        assert detail.fonte_ufficiale_url is None
        assert detail.fonte_ufficiale_host is None
        assert detail.fonte_ufficiale_stato == "trovata"  # gli altri campi restano
        assert detail.link_fonte.origine == "link_bando"

    def test_link_bando_e_link_candidatura_non_escono(self):
        dump = map_detail(CORRENTE, LINK[:2]).model_dump(mode="json")
        assert "link_bando" not in dump
        assert "link_candidatura" not in dump


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
        assert body["stato_effettivo"] == "aperto"
        assert body["ora_scadenza"] == "12:00:00"
        assert body["cta"] == {
            "url": "https://www.regione.piemonte.it/domanda",
            "host": "www.regione.piemonte.it",
            "origine": "candidatura",
        }
        assert body["link_fonte"]["origine"] == "fonte_ufficiale"
        assert body["allegati"] == [{
            "url": "https://www.regione.piemonte.it/atto.pdf",
            "etichetta": "Delibera",
            "tipo": "atto",
            "formato": "pdf",
        }]
        assert "link_bando" not in body
        assert "link_candidatura" not in body

    async def test_200_con_ora_24(self, monkeypatch):
        riga = _riga(1, "bando-a", ora_scadenza="24:00:00", ora_apertura="non-un-orario")
        db = FakeSecondary({"bando_pubblico": [riga], "bando_link": []})
        async with _client(monkeypatch, db) as client:
            resp = await client.get("/api/v1/bandi/bando-a")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ora_scadenza"] is None
        assert body["ora_apertura"] is None

    async def test_200_se_bando_link_fallisce(self, monkeypatch):
        errore = APIError({"message": "x", "code": "42501", "hint": None, "details": None})
        async with _client(monkeypatch, _catalogo(fail={"bando_link": errore})) as client:
            resp = await client.get("/api/v1/bandi/bando-a")
        assert resp.status_code == 200
        body = resp.json()
        # Solo ripieghi: la fonte ufficiale trovata diventa il pulsante.
        assert body["cta"]["origine"] == "fonte_ufficiale"
        assert body["allegati"] == []

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
