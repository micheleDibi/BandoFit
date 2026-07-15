"""Generazione PDF: modello di documento astratto + due motori di rendering.

Il documento è un modello dati puro (`PdfDoc`), costruito dai chiamanti (vedi
`company_pdf_service`) senza toccare alcuna libreria: è testabile da solo e non
fa mai uscire dati grezzi che il chiamante non ci ha messo.

Rendering — due backend intercambiabili sullo STESSO modello:
- **WeasyPrint** (principale): il modello → HTML (Jinja2, autoescape) → PDF. Dà
  layout ricco (`@page`, header/footer, numeri di pagina) ma richiede librerie
  di sistema (pango/cairo/gdk-pixbuf) nell'immagine.
- **ReportLab** (fallback): il modello → flowables Platypus → PDF. Pure-Python,
  nessuna libreria di sistema; layout più sobrio.

Gli import delle librerie sono LOCALI alle funzioni di rendering: il modulo si
importa (e i costruttori di documento si testano) anche se nessun motore è
installato. Il motore si sceglie da `settings.pdf_engine` (`auto` = WeasyPrint
se importabile, altrimenti ReportLab)."""

import logging
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.config import get_settings
from app.core.errors import PdfEngineUnavailableError

logger = logging.getLogger("bandofit.pdf")

# ---------------------------------------------------------------------------
# Modello di documento (puro, nessuna dipendenza di rendering)
# ---------------------------------------------------------------------------


@dataclass
class KV:
    """Una coppia etichetta/valore in un blocco `fields`."""

    label: str
    value: str


@dataclass
class Block:
    """Un blocco di contenuto. `kind` discrimina come renderizzarlo:
    - "fields": griglia etichetta→valore (`fields`)
    - "rows":   tabella con intestazioni (`headers`) e righe (`rows`)
    - "chips":  elenco inline di pillole (`chips`)
    - "text":   paragrafo libero (`text`)."""

    kind: str
    fields: list[KV] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    chips: list[str] = field(default_factory=list)
    text: str | None = None


@dataclass
class Section:
    heading: str
    blocks: list[Block]


@dataclass
class PdfDoc:
    title: str
    subtitle: str | None = None
    badges: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    footer: str | None = None


# ---------------------------------------------------------------------------
# Costruttori-helper: scartano i valori vuoti (il PDF mostra solo ciò che c'è)
# ---------------------------------------------------------------------------


def fields_block(pairs: list[tuple[str, object]]) -> Block | None:
    """Blocco etichetta/valore. Salta le coppie con valore vuoto/None;
    ritorna None se non resta nulla (così la sezione può omettersi)."""
    kvs = [KV(label, str(value)) for label, value in pairs if _has_value(value)]
    return Block(kind="fields", fields=kvs) if kvs else None


def rows_block(headers: list[str], rows: list[list[object]]) -> Block | None:
    cleaned = [[("" if c is None else str(c)) for c in row] for row in rows]
    return Block(kind="rows", headers=headers, rows=cleaned) if cleaned else None


def chips_block(items: list[object]) -> Block | None:
    chips = [str(i) for i in items if _has_value(i)]
    return Block(kind="chips", chips=chips) if chips else None


def text_block(text: str | None) -> Block | None:
    return Block(kind="text", text=text) if _has_value(text) else None


def section(heading: str, blocks: list[Block | None]) -> Section | None:
    """Sezione con i soli blocchi non vuoti; None se resta a secco."""
    kept = [b for b in blocks if b is not None]
    return Section(heading=heading, blocks=kept) if kept else None


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(doc: PdfDoc) -> bytes:
    """Documento → PDF (bytes) col motore configurato. Solleva
    `PdfEngineUnavailableError` (503) se nessun motore è utilizzabile: la
    feature degrada, l'applicazione non cade.

    Robustezza: il pacchetto WeasyPrint può importarsi ma fallire il caricamento
    delle librerie NATIVE (pango/cairo) solo al momento del render (`OSError`).
    In modalità `auto` questo caso ripiega su ReportLab; se il motore è forzato
    a `weasyprint`, l'errore diventa un 503 esplicito."""
    engine = _resolve_engine()
    try:
        if engine == "weasyprint":
            try:
                return _render_weasyprint(doc)
            except OSError as exc:
                forced = (get_settings().pdf_engine or "auto").lower() == "weasyprint"
                if forced or not _reportlab_available():
                    raise PdfEngineUnavailableError(
                        "WeasyPrint non riesce a caricare le librerie di sistema"
                    ) from exc
                return _render_reportlab(doc)
        return _render_reportlab(doc)
    except PdfEngineUnavailableError:
        raise
    except Exception as exc:
        # Un guasto del motore (es. ReportLab LayoutError su contenuto troppo
        # grande per una pagina) NON deve diventare un 500 incontrollato: la
        # feature degrada a 503, l'applicazione resta in piedi.
        logger.error("generazione PDF fallita (%s)", engine, exc_info=True)
        raise PdfEngineUnavailableError("Impossibile generare il PDF in questo momento") from exc


def _resolve_engine() -> str:
    configured = (get_settings().pdf_engine or "auto").lower()
    if configured == "weasyprint":
        if not _weasyprint_available():
            raise PdfEngineUnavailableError()
        return "weasyprint"
    if configured == "reportlab":
        if not _reportlab_available():
            raise PdfEngineUnavailableError()
        return "reportlab"
    # auto: preferisci WeasyPrint (layout ricco), poi ripiega su ReportLab.
    if _weasyprint_available():
        return "weasyprint"
    if _reportlab_available():
        return "reportlab"
    raise PdfEngineUnavailableError()


@lru_cache(maxsize=1)
def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except Exception:
        # Import può fallire per librerie di sistema mancanti (OSError), non
        # solo ImportError: catturiamo tutto.
        return False
    return True


@lru_cache(maxsize=1)
def _reportlab_available() -> bool:
    try:
        import reportlab  # noqa: F401
    except Exception:
        return False
    return True


# ---- WeasyPrint (HTML + CSS via Jinja2) -----------------------------------


def build_html(doc: PdfDoc) -> str:
    """Documento → HTML brandizzato. Jinja2 con autoescape su TUTTI i valori
    (anti-XSS: i dati azienda/persone non possono iniettare markup). Isolata
    per poterla testare a parte dal rendering PDF vero."""
    import jinja2

    template = jinja2.Environment(autoescape=True).from_string(_DOCUMENT_TEMPLATE)
    return template.render(doc=doc, css=_PDF_CSS)


def _render_weasyprint(doc: PdfDoc) -> bytes:
    from weasyprint import HTML

    return HTML(string=build_html(doc)).write_pdf()


# ---- ReportLab (flowables Platypus) ---------------------------------------


def _render_reportlab(doc: PdfDoc) -> bytes:
    import io

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    navy = colors.HexColor("#182549")
    navy2 = colors.HexColor("#213b83")
    slate = colors.HexColor("#1e293b")
    muted = colors.HexColor("#64748b")
    border = colors.HexColor("#e2e8f0")
    chip_bg = colors.HexColor("#f3f5f9")

    base = getSampleStyleSheet()

    def style(name: str, **kw) -> ParagraphStyle:
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    st_brand = style("brand", textColor=navy, fontName="Helvetica-Bold", fontSize=12)
    st_title = style("title", textColor=navy, fontName="Helvetica-Bold", fontSize=20, spaceBefore=4, spaceAfter=2)
    st_subtitle = style("subtitle", textColor=muted, fontSize=10, spaceAfter=2)
    st_badges = style("badges", textColor=navy2, fontSize=9, spaceBefore=4)
    st_h2 = style("h2", textColor=navy2, fontName="Helvetica-Bold", fontSize=13, spaceBefore=12, spaceAfter=4)
    st_key = style("key", textColor=muted, fontSize=9.5)
    st_val = style("val", textColor=slate, fontSize=9.5)
    st_cell = style("cell", textColor=slate, fontSize=9)
    st_th = style("th", textColor=navy2, fontName="Helvetica-Bold", fontSize=8.5)
    st_text = style("text", textColor=slate, fontSize=9.5, alignment=TA_LEFT)
    st_chips = style("chips", textColor=slate, fontSize=9.5)
    st_footer = style("footer", textColor=colors.HexColor("#94a3b8"), fontSize=8)

    def esc(value: str) -> str:
        return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story: list = [
        Paragraph("BandoFit", st_brand),
        Paragraph(esc(doc.title), st_title),
    ]
    if doc.subtitle:
        story.append(Paragraph(esc(doc.subtitle), st_subtitle))
    if doc.badges:
        story.append(Paragraph(" &nbsp;·&nbsp; ".join(esc(b) for b in doc.badges), st_badges))
    story.append(Spacer(1, 6))

    for sec in doc.sections:
        story.append(Paragraph(esc(sec.heading), st_h2))
        for block in sec.blocks:
            if block.kind == "fields":
                data = [
                    [Paragraph(esc(kv.label), st_key), Paragraph(esc(kv.value), st_val)]
                    for kv in block.fields
                ]
                table = Table(data, colWidths=[62 * mm, None])
                table.setStyle(
                    TableStyle(
                        [
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                            ("LEFTPADDING", (0, 0), (0, -1), 0),
                        ]
                    )
                )
                story.append(table)
            elif block.kind == "rows":
                data = [[Paragraph(esc(h), st_th) for h in block.headers]]
                data += [[Paragraph(esc(c), st_cell) for c in row] for row in block.rows]
                table = Table(data, repeatRows=1)
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), chip_bg),
                            ("LINEBELOW", (0, 0), (-1, 0), 0.6, border),
                            ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#eef1f6")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ]
                    )
                )
                story.append(table)
            elif block.kind == "chips":
                story.append(Paragraph(" &nbsp;·&nbsp; ".join(esc(c) for c in block.chips), st_chips))
            elif block.kind == "text" and block.text:
                story.append(Paragraph(esc(block.text), st_text))
        story.append(Spacer(1, 4))

    def _footer(canvas, doc_) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawCentredString(
            A4[0] / 2, 12 * mm, f"BandoFit · pagina {doc_.page}"
        )
        canvas.restoreState()

    if doc.footer:
        story.append(Spacer(1, 10))
        story.append(Paragraph(esc(doc.footer), st_footer))

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=22 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=doc.title,
    )
    pdf.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Template Jinja2 + CSS (inline nel modulo: niente cartella template da
# impacchettare; coerente con l'HTML delle email costruito in-code)
# ---------------------------------------------------------------------------

_DOCUMENT_TEMPLATE = """<!doctype html>
<html lang="it">
<head><meta charset="utf-8"><style>{{ css|safe }}</style></head>
<body>
  <header class="doc-head">
    <div class="brand">BandoFit</div>
    <h1>{{ doc.title }}</h1>
    {% if doc.subtitle %}<p class="subtitle">{{ doc.subtitle }}</p>{% endif %}
    {% if doc.badges %}<div class="badges">{% for b in doc.badges %}<span class="badge">{{ b }}</span>{% endfor %}</div>{% endif %}
  </header>
  {% for section in doc.sections %}
  <section class="sec">
    <h2>{{ section.heading }}</h2>
    {% for block in section.blocks %}
      {% if block.kind == 'fields' %}
      <table class="fields">{% for f in block.fields %}<tr><td class="k">{{ f.label }}</td><td class="v">{{ f.value }}</td></tr>{% endfor %}</table>
      {% elif block.kind == 'rows' %}
      <table class="rows"><thead><tr>{% for h in block.headers %}<th>{{ h }}</th>{% endfor %}</tr></thead><tbody>{% for r in block.rows %}<tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
      {% elif block.kind == 'chips' %}
      <div class="chips">{% for c in block.chips %}<span class="chip">{{ c }}</span>{% endfor %}</div>
      {% elif block.kind == 'text' %}
      <p class="text">{{ block.text }}</p>
      {% endif %}
    {% endfor %}
  </section>
  {% endfor %}
  {% if doc.footer %}<footer class="doc-foot">{{ doc.footer }}</footer>{% endif %}
</body>
</html>"""

_PDF_CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 22mm;
  @bottom-center { content: "BandoFit · pagina " counter(page) " di " counter(pages); font-size: 8pt; color: #94a3b8; }
}
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; color: #1e293b; font-size: 10.5pt; line-height: 1.45; margin: 0; }
.doc-head { border-bottom: 3px solid #182549; padding-bottom: 10px; margin-bottom: 18px; }
.brand { color: #182549; font-weight: 700; font-size: 12pt; letter-spacing: .5px; }
h1 { color: #182549; font-size: 20pt; margin: 6px 0 2px; }
.subtitle { color: #64748b; font-size: 10pt; margin: 0; }
.badges { margin-top: 8px; }
.badge { display: inline-block; background: #e4e8f4; color: #213b83; border-radius: 10px; padding: 2px 10px; font-size: 8.5pt; font-weight: 600; margin-right: 6px; }
.sec { margin-bottom: 16px; page-break-inside: avoid; }
h2 { color: #213b83; font-size: 12.5pt; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; margin: 0 0 8px; }
table.fields { width: 100%; border-collapse: collapse; }
table.fields td { padding: 3px 0; vertical-align: top; }
table.fields td.k { color: #64748b; width: 38%; padding-right: 12px; }
table.fields td.v { color: #1e293b; font-weight: 500; }
table.rows { width: 100%; border-collapse: collapse; margin-top: 4px; }
table.rows th { background: #f3f5f9; color: #213b83; text-align: left; font-size: 8.5pt; text-transform: uppercase; letter-spacing: .3px; padding: 5px 8px; border-bottom: 1px solid #e2e8f0; }
table.rows td { padding: 5px 8px; border-bottom: 1px solid #eef1f6; font-size: 9.5pt; }
.chips .chip { display: inline-block; background: #f3f5f9; border: 1px solid #e2e8f0; border-radius: 8px; padding: 2px 9px; font-size: 9pt; margin: 0 6px 6px 0; }
.text { margin: 2px 0; }
.doc-foot { margin-top: 20px; padding-top: 8px; border-top: 1px solid #e2e8f0; color: #94a3b8; font-size: 8.5pt; }
"""
