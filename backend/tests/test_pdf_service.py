"""Modello di documento PDF (puro) + selezione del motore di rendering.

Il rendering vero (WeasyPrint/ReportLab) è testato solo se la libreria è
installata (`importorskip`); qui si copre la logica indipendente dai motori."""

import pytest

from app.core.errors import PdfEngineUnavailableError
from app.services import pdf_service
from app.services.pdf_service import (
    PdfDoc,
    Section,
    chips_block,
    fields_block,
    rows_block,
    section,
    text_block,
)


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for key, value in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(key, value)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestBlocks:
    def test_fields_scarta_i_vuoti(self):
        block = fields_block([("A", "x"), ("B", None), ("C", ""), ("D", 0)])
        assert block is not None
        # None e "" saltati; 0 è un valore valido (non vuoto).
        assert [(kv.label, kv.value) for kv in block.fields] == [("A", "x"), ("D", "0")]

    def test_fields_tutti_vuoti_none(self):
        assert fields_block([("A", None), ("B", "")]) is None

    def test_rows_none_diventa_stringa_vuota(self):
        block = rows_block(["H1", "H2"], [["a", None], [1, 2]])
        assert block is not None
        assert block.rows == [["a", ""], ["1", "2"]]

    def test_rows_vuoto_none(self):
        assert rows_block(["H"], []) is None

    def test_chips_scarta_vuoti(self):
        assert chips_block(["a", None, "", "b"]).chips == ["a", "b"]
        assert chips_block([None, ""]) is None

    def test_text(self):
        assert text_block("ciao").text == "ciao"
        assert text_block(None) is None
        assert text_block("  ") is None


class TestSection:
    def test_scarta_blocchi_none(self):
        sec = section("H", [fields_block([("A", "x")]), None, chips_block([])])
        assert isinstance(sec, Section)
        assert len(sec.blocks) == 1

    def test_sezione_vuota_none(self):
        assert section("H", [None, fields_block([("A", None)])]) is None


class TestResolveEngine:
    def _doc(self):
        return PdfDoc(title="T", sections=[])

    def test_auto_preferisce_weasyprint(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: True)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: True)
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert pdf_service._resolve_engine() == "weasyprint"

    def test_auto_ripiega_su_reportlab(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: False)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: True)
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert pdf_service._resolve_engine() == "reportlab"

    def test_auto_nessun_motore_solleva(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: False)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: False)
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(PdfEngineUnavailableError):
            pdf_service._resolve_engine()

    def test_forzato_ma_indisponibile_solleva(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: False)
        monkeypatch.setenv("PDF_ENGINE", "weasyprint")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(PdfEngineUnavailableError):
            pdf_service._resolve_engine()


class TestRenderRobustezza:
    """Il gestore d'errore di render(): fallback e degradazione a 503."""

    def _doc(self):
        return PdfDoc(title="T", sections=[])

    def test_forzato_weasyprint_oserror_solleva(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: True)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: True)
        monkeypatch.setattr(
            pdf_service, "_render_weasyprint", lambda _d: (_ for _ in ()).throw(OSError("libgobject"))
        )
        monkeypatch.setenv("PDF_ENGINE", "weasyprint")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(PdfEngineUnavailableError):
            pdf_service.render(self._doc())

    def test_auto_oserror_senza_reportlab_solleva(self, monkeypatch):
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: True)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: False)
        monkeypatch.setattr(
            pdf_service, "_render_weasyprint", lambda _d: (_ for _ in ()).throw(OSError("libgobject"))
        )
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(PdfEngineUnavailableError):
            pdf_service.render(self._doc())

    def test_errore_render_degrada_a_503(self, monkeypatch):
        # Un guasto del motore (es. ReportLab LayoutError) → 503, non 500.
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: False)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: True)
        monkeypatch.setattr(
            pdf_service,
            "_render_reportlab",
            lambda _d: (_ for _ in ()).throw(RuntimeError("LayoutError simulato")),
        )
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(PdfEngineUnavailableError):
            pdf_service.render(self._doc())


class TestRenderReale:
    """Solo se le librerie sono installate (in CI/immagine con le system libs)."""

    def _doc(self):
        return PdfDoc(
            title="Prova & <Test>",  # verifica escaping
            subtitle="sottotitolo",
            badges=["Dati di test"],
            sections=[
                section(
                    "Sezione",
                    [
                        fields_block([("Chiave", "Valore <b>")]),
                        rows_block(["A", "B"], [["1", "2"]]),
                        chips_block(["x", "y"]),
                    ],
                )
            ],
            footer="piè di pagina",
        )

    def test_build_html_autoescape(self):
        pytest.importorskip("jinja2")
        html = pdf_service.build_html(self._doc())
        # I valori con markup sono escapati (anti-XSS): niente tag grezzi.
        assert "<b>" not in html
        assert "&lt;b&gt;" in html
        assert "Prova &amp; &lt;Test&gt;" in html

    def test_build_html_css_non_escapato(self):
        # Il CSS costante è iniettato con |safe: le virgolette restano grezze,
        # altrimenti WeasyPrint scarterebbe @page/font (numeri di pagina persi).
        pytest.importorskip("jinja2")
        html = pdf_service.build_html(self._doc())
        assert 'content: "BandoFit · pagina "' in html
        assert '"Helvetica Neue"' in html
        # I dati del doc non contengono virgolette → nessun &#34; nell'output.
        assert "&#34;" not in html

    def test_render_weasyprint_pdf(self):
        # WeasyPrint importa le librerie native (pango/cairo) AL momento
        # dell'import: dove mancano, l'import stesso solleva OSError. Si salta
        # se non è renderizzabile (macchina senza le librerie di sistema).
        try:
            out = pdf_service._render_weasyprint(self._doc())
        except (ImportError, OSError):
            pytest.skip("WeasyPrint o le sue librerie native non sono disponibili")
        assert out[:5] == b"%PDF-"

    def test_render_auto_ripiega_su_reportlab_se_native_ko(self, monkeypatch):
        """Se WeasyPrint importa ma le sue librerie native falliscono (OSError),
        in 'auto' si ottiene comunque un PDF (via ReportLab)."""
        pytest.importorskip("reportlab")
        monkeypatch.setattr(pdf_service, "_weasyprint_available", lambda: True)
        monkeypatch.setattr(pdf_service, "_reportlab_available", lambda: True)

        def _boom(_doc):
            raise OSError("cannot load library 'libgobject-2.0-0'")

        monkeypatch.setattr(pdf_service, "_render_weasyprint", _boom)
        monkeypatch.setenv("PDF_ENGINE", "auto")
        from app.core.config import get_settings

        get_settings.cache_clear()
        out = pdf_service.render(self._doc())
        assert out[:5] == b"%PDF-"

    def test_render_reportlab_pdf(self):
        pytest.importorskip("reportlab")
        out = pdf_service._render_reportlab(self._doc())
        assert out[:5] == b"%PDF-"
