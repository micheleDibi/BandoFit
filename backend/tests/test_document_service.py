"""Test del servizio documenti (visure camerali): cascata delle varianti con
rifiuti gratuiti, lock, registro consumi, evasione con estrazione PDF/testo e
archiviazione nel bucket."""

import base64
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from app.clients.openapi import OpenapiTimeoutError, OpenapiWrongTypeError
from app.core.errors import AppError, BadRequestError, ForbiddenError
from app.services import document_service
from tests.test_openapi_service import FakePrimary, USER

FIXTURES = Path(__file__).parent / "fixtures" / "openapi"

COMPANY = {"id": "c0000000-0000-0000-0000-000000000001",
           "partita_iva": "14061981008", "codice_fiscale": None}

ACCEPTED = json.loads((FIXTURES / "visura_request_accepted.json").read_text())["data"]
READY = json.loads((FIXTURES / "visura_ready.json").read_text())["data"]


def make_zip_with_pdf() -> bytes:
    """ZIP sintetico con un PDF vero (una pagina bianca, come da fixture shape)."""
    pdf_buf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(pdf_buf)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("6a4bf722_0.pdf", pdf_buf.getvalue())
    return zip_buf.getvalue()


def fake_openapi(
    request_results=None,   # lista di esiti per variante (dict o eccezione)
    status_result=None,
    allegati_file: bytes | None = None,
    enabled=True,
    sandbox=False,
):
    calls = {"request": [], "status": [], "allegati": []}
    results = list(request_results or [ACCEPTED])

    async def visura_request(variant, cf_piva_id):
        calls["request"].append(variant)
        result = results.pop(0) if results else ACCEPTED
        if isinstance(result, Exception):
            raise result
        return result

    async def visura_status(variant, request_id):
        calls["status"].append(request_id)
        return status_result if status_result is not None else ACCEPTED

    async def visura_allegati(variant, request_id):
        calls["allegati"].append(request_id)
        return {
            "nome": "req.zip",
            "dimensione": len(allegati_file or b""),
            "file": base64.b64encode(allegati_file or b"").decode(),
        }

    return SimpleNamespace(
        enabled=enabled, sandbox=sandbox, calls=calls,
        visura_request=visura_request, visura_status=visura_status,
        visura_allegati=visura_allegati,
    )


@pytest.fixture(autouse=True)
def no_membership(monkeypatch):
    async def membership(primary, user_id):
        return None

    monkeypatch.setattr("app.services.family_service.get_membership", membership)


class TestRichiesta:
    async def test_senza_dati_aziendali_400(self):
        primary = FakePrimary(selects={"company_profiles": []})
        with pytest.raises(BadRequestError):
            await document_service.request_document(primary, fake_openapi(), USER)

    async def test_figlio_attivo_403(self, monkeypatch):
        async def membership(primary, user_id):
            return {"status": "active", "parent_id": "x"}

        monkeypatch.setattr("app.services.family_service.get_membership", membership)
        with pytest.raises(ForbiddenError):
            await document_service.request_document(FakePrimary(), fake_openapi(), USER)

    async def test_pending_esistente_409(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": [{"id": "d1"}]}
        )
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await document_service.request_document(primary, openapi, USER)
        assert exc.value.code == "document_in_progress"
        assert openapi.calls["request"] == []  # nessuna spesa

    async def test_cascata_varianti_su_rifiuto_gratuito(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": []}
        )
        openapi = fake_openapi(
            request_results=[OpenapiWrongTypeError("not capitale"),
                             OpenapiWrongTypeError("not persone"),
                             ACCEPTED],
        )
        result = await document_service.request_document(primary, openapi, USER)
        assert openapi.calls["request"] == [
            "ordinaria-societa-capitale",
            "ordinaria-societa-persone",
            "ordinaria-impresa-individuale",
        ]
        assert result.status == "pending"
        assert result.endpoint == "ordinaria-impresa-individuale"
        inserted = primary.ops_for("company_documents", "insert")[0]
        assert inserted["request_id"] == ACCEPTED["id"]
        # ledger: costo della variante accettata (impresa individuale 2,90) e
        # request_id nel meta — se l'insert fallisse, il documento pagato
        # resterebbe comunque recuperabile.
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "success" and events[0]["cost_cents"] == 290
        assert events[0]["request_meta"]["request_id"] == ACCEPTED["id"]
        # lock rilasciato
        assert ("fn_release_import_lock", {"p_parent_id": USER["id"]}) in primary.rpcs

    async def test_ordine_varianti_da_forma_giuridica(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY],
                "company_documents": [],
                "company_data": [
                    {"raw": {"legalForm": {"detailedLegalForm": {"description": "Association"}}}}
                ],
            }
        )
        openapi = fake_openapi()
        await document_service.request_document(primary, openapi, USER)
        # ente/associazione → prima il canale impresa-individuale (verificato sul campo)
        assert openapi.calls["request"][0] == "ordinaria-impresa-individuale"

    async def test_tutte_le_varianti_rifiutate_400(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": []}
        )
        openapi = fake_openapi(
            request_results=[OpenapiWrongTypeError()] * 3,
        )
        with pytest.raises(BadRequestError):
            await document_service.request_document(primary, openapi, USER)
        events = primary.ops_for("api_usage_events", "insert")
        # UNA sola riga di errore nel registro (niente doppioni dal ramo AppError)
        assert len(events) == 1
        assert events[0]["outcome"] == "error" and events[0]["cost_cents"] == 0
        assert primary.ops_for("company_documents", "insert") == []
        released = [n for n, _ in primary.rpcs if n == "fn_release_import_lock"]
        assert released == ["fn_release_import_lock"]  # rilasciato una volta sola

    async def test_timeout_ledger_e_lock_non_rilasciato(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": []}
        )
        openapi = fake_openapi(request_results=[OpenapiTimeoutError()])
        with pytest.raises(OpenapiTimeoutError):
            await document_service.request_document(primary, openapi, USER)
        events = primary.ops_for("api_usage_events", "insert")
        assert events[0]["outcome"] == "timeout_unknown"
        released = [n for n, _ in primary.rpcs if n == "fn_release_import_lock"]
        assert released == []

    async def test_lock_occupato_409(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": []}, lock=False
        )
        openapi = fake_openapi()
        with pytest.raises(AppError) as exc:
            await document_service.request_document(primary, openapi, USER)
        assert exc.value.code == "document_in_progress"
        assert openapi.calls["request"] == []


def pending_row(**overrides) -> dict:
    """Riga pending RECENTE (created_at dinamico: il failsafe 24h non scatta)."""
    row = {
        "id": "d0000000-0000-0000-0000-000000000001",
        "company_profile_id": COMPANY["id"],
        "kind": "visura", "endpoint": "ordinaria-impresa-individuale",
        "request_id": ACCEPTED["id"], "status": "pending", "error_detail": None,
        "file_path": None, "file_name": None, "file_size": None, "pages": None,
        "extracted_text": None, "cost_cents": 290, "sandbox": False,
        "created_at": datetime.now(timezone.utc).isoformat(), "ready_at": None,
    }
    row.update(overrides)
    return row


class TestEvasione:
    PENDING_ROW = pending_row()

    async def test_ancora_in_erogazione_resta_pending(self):
        primary = FakePrimary()
        openapi = fake_openapi(status_result=ACCEPTED)  # "In erogazione"
        row = await document_service._try_complete(primary, openapi, pending_row())
        assert row["status"] == "pending"
        assert openapi.calls["allegati"] == []

    async def test_esito_negativo_marca_errore(self):
        primary = FakePrimary()
        openapi = fake_openapi(
            status_result={"stato_richiesta": "Richiesta annullata", "allegati": []}
        )
        row = await document_service._try_complete(primary, openapi, pending_row())
        assert row["status"] == "error"
        update = primary.ops_for("company_documents", "update")[0]
        assert update["status"] == "error"

    async def test_pending_scaduta_dopo_24h_marca_errore(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        primary = FakePrimary()
        openapi = fake_openapi(status_result=ACCEPTED)  # ancora "In erogazione"
        row = await document_service._try_complete(
            primary, openapi, pending_row(created_at=old)
        )
        assert row["status"] == "error"
        assert "scaduta" in (row["error_detail"] or "")

    async def test_evasa_scarica_estrae_e_archivia(self):
        zip_bytes = make_zip_with_pdf()
        ready_row = dict(self.PENDING_ROW, status="ready", file_name="6a4bf722_0.pdf", pages=1)
        primary = FakePrimary(selects={"company_documents": [ready_row]})
        openapi = fake_openapi(status_result=READY, allegati_file=zip_bytes)
        row = await document_service._try_complete(primary, openapi, dict(self.PENDING_ROW))
        # upload nel bucket con path azienda/documento
        assert primary.storage.uploads, "nessun upload nel bucket"
        path, pdf = primary.storage.uploads[0]
        assert path == f"{COMPANY['id']}/{self.PENDING_ROW['id']}.pdf"
        assert pdf[:4] == b"%PDF"
        # update condizionato su pending + rilettura
        update = primary.ops_for("company_documents", "update")[0]
        assert update["status"] == "ready" and update["pages"] == 1
        assert update["file_name"] == "6a4bf722_0.pdf"
        assert row["status"] == "ready"

    async def test_allegato_non_zip_salva_i_byte_grezzi(self):
        pdf_bytes, name = document_service._extract_pdf(b"%PDF-1.4 finto")
        assert pdf_bytes.startswith(b"%PDF") and name == "visura.pdf"

    async def test_pdf_illeggibile_non_blocca(self):
        text, pages = document_service._extract_text(b"non-un-pdf")
        assert text is None and pages is None


class TestLetturaEDownload:
    async def test_lista_completa_le_pending(self):
        zip_bytes = make_zip_with_pdf()
        pending = dict(TestEvasione.PENDING_ROW)
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY],
                "company_documents": [pending],
            }
        )
        openapi = fake_openapi(status_result=READY, allegati_file=zip_bytes)
        result = await document_service.list_documents(primary, openapi, USER)
        assert len(result.documents) == 1
        assert openapi.calls["status"]  # poll-on-read eseguito

    async def test_download_solo_documenti_della_propria_azienda(self):
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": []}
        )
        with pytest.raises(AppError):
            await document_service.download_document(primary, USER, "doc-altrui")

    async def test_download_pronto(self):
        ready = dict(TestEvasione.PENDING_ROW, status="ready",
                     file_path=f"{COMPANY['id']}/x.pdf", file_name="visura.pdf")
        primary = FakePrimary(
            selects={"company_profiles": [COMPANY], "company_documents": [ready]}
        )
        primary.storage.files[ready["file_path"]] = b"%PDF-contenuto"
        pdf, name = await document_service.download_document(
            primary, USER, ready["id"]
        )
        assert pdf == b"%PDF-contenuto" and name == "visura.pdf"

    async def test_download_non_pronto_409(self):
        primary = FakePrimary(
            selects={
                "company_profiles": [COMPANY],
                "company_documents": [dict(TestEvasione.PENDING_ROW)],
            }
        )
        with pytest.raises(AppError) as exc:
            await document_service.download_document(
                primary, USER, TestEvasione.PENDING_ROW["id"]
            )
        assert exc.value.code == "document_not_ready"
