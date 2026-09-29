"""API del bilancio ufficiale (WP2): GET/POST /me/company/bilanci/ufficiale,
GET .../{id} e GET .../{id}/pdf.

Mini-app con il solo router aziendale e `dependency_overrides` (modello di
test_bilanci_api.py); dietro, il servizio vero sul primario finto a stato di
test_bilancio_ufficiale_service. Si verificano le forme JSON del contratto,
i permessi (il membro legge, non spende), gli header del download e il
formato degli errori {error: {code}}."""

import hashlib
from datetime import date

import httpx
import pytest
from fastapi import FastAPI

from app.api import deps
from app.api.deps import ActiveCompany
from app.api.routers import company
from app.core.errors import register_exception_handlers
from tests.test_bilancio_ufficiale_service import (  # noqa: F401  fixture autouse
    ALTRA_COMPANY,
    COMPANY,
    OWNER,
    PDF,
    PIVA,
    PROVIDER_ID,
    FakeOpenapi,
    _api_error,
    db_base,
    db_con,
    facet_invalidati,
    notifiche,
    nuova_richiesta,
    spawned,
)

BASE = "/api/v1/me/company/bilanci/ufficiale"
CAMPI_RICHIESTA = {
    "id", "stato", "anno_richiesto", "anno_bilancio", "errore_codice", "messaggio",
    "xbrl_esito", "avvisi_count", "rimborsata", "pdf_disponibile", "created_at",
    "completata_at",
}


@pytest.fixture(autouse=True)
def storico_acceso(monkeypatch):
    """Il bilancio ufficiale esiste solo a storico acceso (spento: 404,
    test_bilanci_storico_flag.py)."""
    from app.core.config import get_settings

    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "BILANCI_STORICO_ATTIVO": "true",
    }.items():
        monkeypatch.setenv(chiave, valore)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(db, openapi=None, *, editable: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(company.router, prefix="/api/v1")
    app.dependency_overrides[deps.get_current_user] = lambda: {"id": OWNER}
    app.dependency_overrides[deps.active_company] = lambda: ActiveCompany(
        company_id=COMPANY, owner_id=OWNER, editable=editable
    )
    app.dependency_overrides[deps.get_primary] = lambda: db
    app.dependency_overrides[deps.get_secondary] = lambda: object()
    app.dependency_overrides[deps.get_openapi] = lambda: openapi or FakeOpenapi()
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _con_pdf(company_id: str = COMPANY):
    riga = nuova_richiesta("completata", anno_bilancio=2024, xbrl_esito="ok",
                           company_profile_id=company_id)
    db = db_con(riga)
    db.tabelle["company_bilancio_documenti"] = [{
        "richiesta_id": riga["id"], "company_profile_id": company_id, "tipo": "pdf",
        "nome_file": "bilancio-2024.pdf", "dimensione": len(PDF),
        "sha256": hashlib.sha256(PDF).hexdigest(), "contenuto": "\\x" + PDF.hex(),
    }]
    return db, riga


class TestLista:
    async def test_forma_del_contratto(self):
        db, riga = _con_pdf()
        async with _client(db) as client:
            resp = await client.get(BASE)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "editable", "richiedibile", "motivo_non_richiedibile", "addon", "quantita",
            "anni_acquisiti", "richieste",
        }
        assert set(body["addon"]) == {"slug", "nome", "tipo_prezzo", "etichetta_prezzo", "prezzo"}
        assert body["addon"]["slug"] == "bilancio-ufficiale"
        [richiesta] = body["richieste"]
        assert set(richiesta) == CAMPI_RICHIESTA
        assert richiesta["id"] == riga["id"] and richiesta["pdf_disponibile"] is True
        # mai id del provider, P.IVA o byte dei documenti
        assert PROVIDER_ID not in resp.text and PIVA not in resp.text
        assert PDF.hex()[:40] not in resp.text

    async def test_membro_legge_in_sola_lettura(self):
        db, _riga = _con_pdf()
        async with _client(db, editable=False) as client:
            resp = await client.get(BASE)
        assert resp.status_code == 200
        assert resp.json()["editable"] is False and resp.json()["richiedibile"] is False
        assert len(resp.json()["richieste"]) == 1


class TestRichiesta:
    async def test_titolare_201(self):
        db, openapi = db_base(), FakeOpenapi()
        async with _client(db, openapi) as client:
            resp = await client.post(BASE, json={"anno": 2024})
        assert resp.status_code == 201
        body = resp.json()
        assert set(body) == CAMPI_RICHIESTA
        assert body["stato"] == "in_lavorazione" and body["anno_richiesto"] == 2024
        assert PROVIDER_ID not in resp.text
        assert openapi.chiamate == [("richiedi", PIVA, 2024)]

    async def test_ultimo_disponibile(self):
        db, openapi = db_base(), FakeOpenapi()
        async with _client(db, openapi) as client:
            resp = await client.post(BASE, json={"anno": None})
        assert resp.status_code == 201 and resp.json()["anno_richiesto"] is None

    async def test_membro_403(self):
        db, openapi = db_base(), FakeOpenapi()
        async with _client(db, openapi, editable=False) as client:
            resp = await client.post(BASE, json={"anno": None})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"
        assert openapi.chiamate == [] and db.rpcs == []
        assert db.inventario()["quantita"] == 2

    @pytest.mark.parametrize("anno", [1999, date.today().year + 1, "duemila"])
    async def test_anno_non_valido_422(self, anno):
        db, openapi = db_base(), FakeOpenapi()
        async with _client(db, openapi) as client:
            resp = await client.post(BASE, json={"anno": anno})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "validation_error"
        assert openapi.chiamate == [] and db.rpcs == []

    @pytest.mark.parametrize(
        ("prepara", "status", "code"),
        [
            (lambda db, o: setattr(o, "enabled", False), 503, "openapi_not_configured"),
            (lambda db, o: db.inventario().update(quantita=0), 409, "payment_required"),
            (lambda db, o: db.tabelle["company_data"][0]["raw"]["legalForm"]["legalForm"]
             .update(code="SP"), 409, "bilancio_non_richiedibile"),
            (lambda db, o: db.tabelle.update(company_financials_fonti=[{
                "company_profile_id": COMPANY, "anno": 2024, "fonte": "xbrl",
                "ruolo": "corrente"}]), 409, "bilancio_gia_presente"),
            (lambda db, o: db.rpc_errors.update(
                fn_bilancio_richiesta_crea=_api_error("bilanci_limite_piattaforma")),
             503, "bilanci_sospesi"),
        ],
        ids=["non_configurato", "senza_unita", "societa_di_persone", "anno_presente",
             "tetto_piattaforma"],
    )
    async def test_errori_formato_error_code(self, prepara, status, code):
        db, openapi = db_base(), FakeOpenapi()
        prepara(db, openapi)
        async with _client(db, openapi) as client:
            resp = await client.post(BASE, json={"anno": 2024})
        assert resp.status_code == status
        assert resp.json()["error"]["code"] == code
        assert resp.json()["error"]["message"]
        assert "richiedi" not in [c[0] for c in openapi.chiamate]


class TestDettaglio:
    async def test_200(self):
        db, riga = _con_pdf()
        async with _client(db) as client:
            resp = await client.get(f"{BASE}/{riga['id']}")
        assert resp.status_code == 200
        assert set(resp.json()) == CAMPI_RICHIESTA
        assert resp.json()["pdf_disponibile"] is True

    async def test_altra_azienda_404(self):
        db, riga = _con_pdf(company_id=ALTRA_COMPANY)
        async with _client(db) as client:
            resp = await client.get(f"{BASE}/{riga['id']}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_id_malformato_404(self):
        async with _client(db_base()) as client:
            resp = await client.get(f"{BASE}/non-un-uuid")
        assert resp.status_code == 404


class TestPdf:
    async def test_download_con_header_sicuri(self):
        db, riga = _con_pdf()
        async with _client(db, editable=False) as client:  # anche il membro scarica
            resp = await client.get(f"{BASE}/{riga['id']}/pdf")
        assert resp.status_code == 200
        assert resp.content == PDF
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.headers["content-disposition"] == 'attachment; filename="bilancio-2024.pdf"'
        assert resp.headers["x-content-type-options"] == "nosniff"

    async def test_fuori_azienda_404(self):
        db, riga = _con_pdf(company_id=ALTRA_COMPANY)
        async with _client(db) as client:
            resp = await client.get(f"{BASE}/{riga['id']}/pdf")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"

    async def test_senza_pdf_409(self):
        riga = nuova_richiesta("non_disponibile", errore_codice="bilancio_non_disponibile")
        async with _client(db_con(riga)) as client:
            resp = await client.get(f"{BASE}/{riga['id']}/pdf")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "documento_non_disponibile"
