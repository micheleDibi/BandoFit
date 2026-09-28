"""Export PDF dell'azienda attiva: due documenti.

- **Scheda azienda** (`export_scheda_pdf`): i dati DICHIARATI dall'utente
  (`company_profiles`) + le preferenze di ricerca seguite. Non certificato.
- **Dossier** (`export_dossier_pdf`): la visura certificata del Registro Imprese
  importata da openapi.it. Alimentato SOLO da `openapi_service.get_dossier`
  (`DossierResponse`, già privo del `raw` grezzo) e, per i bilanci per
  esercizio, da `bilanci_service.get_bilanci` (`BilanciOut`, già fuso e senza
  payload del provider): il payload grezzo non esce.

Entrambi costruiscono un `PdfDoc` astratto (puro, testabile) e lo rendono con
`pdf_service.render` in un thread (il rendering è CPU-bound)."""

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.errors import NotFoundError
from app.schemas.bilanci import BilanciOut
from app.schemas.openapi_data import DossierResponse
from app.services import (
    bilanci_service,
    company_service,
    openapi_service,
    pdf_service,
    preferences_service,
)
from app.services.pdf_service import (
    PdfDoc,
    chips_block,
    fields_block,
    rows_block,
    section,
    text_block,
)

logger = logging.getLogger("bandofit.pdf")

# Etichette leggibili degli enum (il DB tiene i codici; il PDF mostra i nomi).
_CLASSE_LABELS = {
    "micro": "Micro impresa",
    "piccola": "Piccola impresa",
    "media": "Media impresa",
    "grande": "Grande impresa",
}
_FASCIA_LABELS = {
    "fino_100k": "Fino a 100.000 €",
    "100k_500k": "100.000 – 500.000 €",
    "500k_2m": "500.000 € – 2 M€",
    "2m_10m": "2 – 10 M€",
    "10m_50m": "10 – 50 M€",
    "oltre_50m": "Oltre 50 M€",
}
_FACET_SEZIONE = {
    "regioni": "Regioni",
    "settori": "Settori",
    "beneficiari": "Beneficiari",
    "codici_ateco": "Codici ATECO",
    "tipologie": "Tipologie di bando",
    "modalita": "Modalità di erogazione",
    "programmi": "Programmi",
}
# Voci dei bilanci per esercizio, nell'ordine di CAMPI_BILANCIO.
_VOCI_BILANCIO = (
    ("fatturato", "Fatturato"),
    ("valore_produzione", "Valore della produzione"),
    ("risultato_esercizio", "Utile (perdita) d'esercizio"),
    ("patrimonio_netto", "Patrimonio netto"),
    ("capitale_sociale", "Capitale sociale"),
    ("totale_attivo", "Totale attivo"),
    ("debiti_totali", "Debiti totali"),
    ("disponibilita_liquide", "Disponibilità liquide"),
    ("ebitda", "MOL (EBITDA)"),
    ("ebit", "Risultato operativo (EBIT)"),
    ("cash_flow", "Cash flow"),
    ("oneri_finanziari", "Oneri finanziari"),
    ("dipendenti", "Dipendenti"),
    ("costo_personale", "Costo del personale"),
    ("retribuzione_media_lorda", "Retribuzione media lorda"),
)
_BILANCI_ANNI_PDF = 5
_FONTI_LABELS = {
    "xbrl": "bilancio ufficiale (XBRL)",
    "it_full": "Registro Imprese, ultimo bilancio depositato",
    "it_advanced": "storico bilanci del Registro Imprese",
}
_FLAG_LABELS = {
    "startup_innovativa": "Startup innovativa",
    "pmi_innovativa": "PMI innovativa",
    "impresa_artigiana": "Impresa artigiana",
    "esportatore": "Esportatore",
    "importatore": "Importatore",
    "certificazione_soa": "Certificazione SOA",
    "gruppo_societario": "Appartiene a un gruppo societario",
}


@dataclass
class PdfResult:
    content: bytes
    filename: str


# ---------------------------------------------------------------------------
# Helper di formattazione
# ---------------------------------------------------------------------------


def _eur(value) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):,.0f}".replace(",", ".") + " €"
    except (TypeError, ValueError):
        return None


def _numero_it(value, decimali: int = 0) -> str | None:
    """Numero in formato italiano (1.234,5): per dipendenti e indicatori."""
    if value is None:
        return None
    testo = f"{float(value):,.{decimali}f}".replace(",", "_").replace(".", ",")
    testo = testo.replace("_", ".")
    if decimali and "," in testo:
        testo = testo.rstrip("0").rstrip(",")
    return testo


def _date_it(iso) -> str | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(str(iso)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(iso)


def _slug(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    norm = re.sub(r"[^a-zA-Z0-9]+", "-", norm).strip("-").lower()
    return norm or "azienda"


def _oggi_it() -> str:
    return datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m/%Y")


def _persona_nome(persona) -> str:
    if persona.denominazione:
        return persona.denominazione
    parti = [persona.nome, persona.cognome]
    return " ".join(p for p in parti if p).strip() or "—"


def _ruoli_str(persona) -> str:
    # `description` viene dal payload del provider: difensivo su tipi non-str.
    descr = [str(r.get("description")) for r in (persona.ruoli or []) if r.get("description")]
    testo = ", ".join(descr)
    if persona.is_legale_rappresentante:
        testo = f"{testo} (Legale rappresentante)" if testo else "Legale rappresentante"
    return testo


def _ateco_str(ateco: dict | None) -> str | None:
    if not ateco:
        return None
    codice = ateco.get("codice")
    descr = ateco.get("descrizione")
    if codice and descr:
        return f"{codice} — {descr}"
    return codice or descr


# ---------------------------------------------------------------------------
# Costruttori di documento (puri, testabili senza motore PDF)
# ---------------------------------------------------------------------------


def build_scheda_doc(company, preferenze: dict[str, list[str]]) -> PdfDoc:
    """Scheda dei dati DICHIARATI dall'azienda + preferenze seguite."""
    ateco = None
    if company.ateco_codice and company.ateco_descrizione:
        ateco = f"{company.ateco_codice} — {company.ateco_descrizione}"
    elif company.ateco_codice:
        ateco = company.ateco_codice

    sezioni = [
        section(
            "Anagrafica",
            [
                fields_block(
                    [
                        ("Ragione sociale", company.ragione_sociale),
                        ("Forma giuridica", company.forma_giuridica),
                        ("Partita IVA", company.partita_iva),
                        ("Codice fiscale", company.codice_fiscale),
                        ("Anno di fondazione", company.anno_fondazione),
                    ]
                )
            ],
        ),
        section(
            "Attività e dimensione",
            [
                fields_block(
                    [
                        ("Codice ATECO", ateco),
                        ("Settore", company.settore_nome),
                        (
                            "Classe dimensionale",
                            _CLASSE_LABELS.get(company.classe_dimensionale),
                        ),
                        ("Numero dipendenti", company.numero_dipendenti),
                        ("Fascia di fatturato", _FASCIA_LABELS.get(company.fascia_fatturato)),
                    ]
                ),
                chips_block([b.nome for b in company.beneficiari]),
            ],
        ),
        section(
            "Sede e contatti",
            [
                fields_block(
                    [
                        ("Indirizzo", company.indirizzo),
                        ("Comune", company.comune),
                        ("Provincia", company.provincia),
                        ("CAP", company.cap),
                        ("Regione", company.regione_nome),
                        ("PEC", company.pec),
                        ("Telefono", company.telefono),
                        ("Sito web", company.sito_web),
                    ]
                )
            ],
        ),
        _sezione_preferenze(preferenze),
    ]
    return PdfDoc(
        title=company.ragione_sociale,
        subtitle="Scheda azienda — dati dichiarati",
        sections=[s for s in sezioni if s is not None],
        footer=(
            f"Documento generato da BandoFit il {_oggi_it()}. "
            "Dati dichiarati dall'utente, non certificati."
        ),
    )


def _sezione_preferenze(preferenze: dict[str, list[str]]):
    pairs = [
        (_FACET_SEZIONE[facet], ", ".join(preferenze[facet]))
        for facet in _FACET_SEZIONE
        if preferenze.get(facet)
    ]
    return section("Preferenze di ricerca seguite", [fields_block(pairs)])


def build_dossier_doc(resp: DossierResponse, bilanci: BilanciOut | None = None) -> PdfDoc:
    """Dossier certificato. `resp.dossier` è un dict a 8 sezioni (build_dossier),
    `resp.people` le cariche/soci, `resp.derived` i valori calcolati. Nessun dato
    grezzo: `DossierResponse` non contiene `raw`. `bilanci` (facoltativo)
    aggiunge la sezione «Bilanci per esercizio» dopo «Dati economici»."""
    d = resp.dossier or {}
    ana = d.get("anagrafica") or {}
    att = d.get("attivita") or {}
    sede = d.get("sede") or {}
    con = d.get("contatti") or {}
    dip = d.get("dipendenti") or {}
    bil = d.get("bilanci") or {}
    # Il fatturato di IT-full può riferirsi a un anno diverso dall'esercizio
    # (turnoverYear): in quel caso l'etichetta lo dice.
    anno_fatturato = bil.get("anno_fatturato")
    etichetta_fatturato = (
        f"Fatturato ({anno_fatturato})"
        if anno_fatturato and bil.get("anno") and anno_fatturato != bil.get("anno")
        else "Fatturato"
    )
    part = d.get("partecipazioni") or []
    flags = d.get("flags") or {}

    badges: list[str] = []
    if resp.sandbox:
        badges.append("Dati di test")
    if ana.get("stato"):
        badges.append(ana["stato"])

    sezioni = [
        section(
            "Anagrafica",
            [
                fields_block(
                    [
                        ("Denominazione", ana.get("denominazione")),
                        ("Partita IVA", ana.get("partita_iva")),
                        ("Codice fiscale", ana.get("codice_fiscale")),
                        ("Forma giuridica", ana.get("forma_giuridica_dettaglio") or ana.get("forma_giuridica")),
                        ("REA", ana.get("rea")),
                        ("CCIAA", ana.get("cciaa")),
                        ("Data di costituzione", _date_it(ana.get("data_costituzione"))),
                        ("Inizio attività", _date_it(ana.get("data_inizio_attivita"))),
                        ("Stato", ana.get("stato")),
                        ("Capogruppo", ana.get("capogruppo")),
                    ]
                )
            ],
        ),
        section(
            "Attività",
            [
                fields_block(
                    [
                        ("ATECO", _ateco_str(att.get("ateco"))),
                        ("ATECO 2022", _ateco_str(att.get("ateco_2022"))),
                        ("NACE", att.get("nace")),
                        ("SAE", att.get("sae")),
                    ]
                ),
                chips_block(att.get("ateco_secondari") or []),
            ],
        ),
        section(
            "Sede legale",
            [
                fields_block(
                    [
                        ("Indirizzo", sede.get("indirizzo")),
                        ("Comune", sede.get("comune")),
                        ("Provincia", sede.get("provincia")),
                        ("CAP", sede.get("cap")),
                        ("Regione", sede.get("regione")),
                        ("Numero sedi", sede.get("numero_sedi")),
                    ]
                ),
                rows_block(
                    ["Tipo", "Indirizzo", "Comune", "Prov.", "Stato"],
                    [
                        [
                            u.get("tipo"),
                            u.get("indirizzo"),
                            u.get("comune"),
                            u.get("provincia"),
                            u.get("stato"),
                        ]
                        for u in (sede.get("unita_locali") or [])
                    ],
                ),
            ],
        ),
        section(
            "Contatti",
            [
                fields_block(
                    [
                        ("PEC", con.get("pec")),
                        ("Email", con.get("email")),
                        ("Telefono", con.get("telefono")),
                        ("Fax", con.get("fax")),
                        ("Sito web", con.get("sito_web")),
                    ]
                )
            ],
        ),
        section(
            "Personale",
            [
                fields_block(
                    [
                        ("Numero dipendenti", dip.get("numero")),
                        ("Fascia", dip.get("fascia")),
                        ("Andamento", dip.get("trend")),
                    ]
                )
            ],
        ),
        section(
            "Dati economici",
            [
                fields_block(
                    [
                        ("Dimensione d'impresa", bil.get("dimensione_impresa")),
                        ("Esercizio", bil.get("anno")),
                        (etichetta_fatturato, _eur(bil.get("fatturato"))),
                        ("Capitale sociale", _eur(bil.get("capitale_sociale"))),
                        ("Patrimonio netto", _eur(bil.get("patrimonio_netto"))),
                        ("EBITDA", _eur(bil.get("ebitda"))),
                        ("Utile", _eur(bil.get("utile"))),
                    ]
                )
            ],
        ),
        _sezione_bilanci(bilanci),
        section(
            "Partecipazioni",
            [
                rows_block(
                    ["Denominazione", "Codice fiscale", "Quota"],
                    [
                        [p.get("denominazione"), p.get("codice_fiscale"), p.get("quota")]
                        for p in part
                    ],
                )
            ],
        ),
        section(
            "Caratteristiche",
            [chips_block([label for key, label in _FLAG_LABELS.items() if flags.get(key)])],
        ),
        *_sezioni_persone(resp.people),
    ]

    title = ana.get("denominazione") or "Dossier azienda"
    provenienza = "Dossier certificato — fonte Registro Imprese tramite openapi.it"
    if resp.fetched_at:
        provenienza += f", aggiornato al {_date_it(resp.fetched_at)}"
    if resp.sandbox:
        provenienza += " · ambiente di test"

    return PdfDoc(
        title=title,
        subtitle="Dossier certificato — Registro Imprese",
        badges=badges,
        sections=[s for s in sezioni if s is not None],
        footer=provenienza,
    )


def _valore_bilancio(campo: str, value) -> str | None:
    if campo == "dipendenti":
        return _numero_it(value, 1)
    return _eur(value)


def _valore_indicatore(indicatore) -> str | None:
    if indicatore.valore is None:
        return None
    if indicatore.unita == "percentuale":
        testo = f"{_numero_it(indicatore.valore, 2)} %"
    elif indicatore.unita == "euro":
        testo = _eur(indicatore.valore)
    else:
        testo = _numero_it(indicatore.valore, 4)
    anni = indicatore.anni
    if anni:
        periodo = f"{anni[0]}–{anni[-1]}" if len(anni) > 1 else str(anni[0])
        testo = f"{testo} ({periodo})"
    return testo


def _sezione_bilanci(bilanci: BilanciOut | None):
    """Voce × ultimi 5 esercizi (righe tutte vuote nascoste), indicatori già
    calcolati e nota sulle fonti. Nessun esercizio → nessuna sezione."""
    if bilanci is None or not bilanci.esercizi:
        return None
    esercizi = sorted(bilanci.esercizi, key=lambda e: e.anno)[-_BILANCI_ANNI_PDF:]
    righe = []
    for campo, etichetta in _VOCI_BILANCIO:
        valori = [_valore_bilancio(campo, getattr(e, campo)) for e in esercizi]
        if any(v is not None for v in valori):
            righe.append([etichetta, *valori])
    fonti_usate = sorted(
        {f for e in esercizi for f in e.fonti.values()},
        key=lambda f: list(_FONTI_LABELS).index(f) if f in _FONTI_LABELS else 99,
    )
    nota = (
        "Valori in euro fusi campo per campo da più fonti, con precedenza al bilancio "
        "ufficiale, poi all'ultimo bilancio del Registro Imprese, poi allo storico. "
        f"Fonti usate: {', '.join(_FONTI_LABELS.get(f, f) for f in fonti_usate)}. "
        "Dati riservati all'azienda: non condividerli con terzi."
    )
    if bilanci.storico_esito != "ok":
        nota += " Lo storico pluriennale non è completo: recuperalo dalla pagina Azienda."
    return section(
        "Bilanci per esercizio",
        [
            rows_block(["Voce", *[str(e.anno) for e in esercizi]], righe),
            fields_block([(i.etichetta, _valore_indicatore(i)) for i in bilanci.indicatori]),
            text_block(nota),
        ],
    )


def _sezioni_persone(people: list) -> list:
    managers = [p for p in people if p.kind == "manager"]
    shareholders = [p for p in people if p.kind == "shareholder"]
    auditors = [p for p in people if p.kind == "auditor"]
    return [
        section(
            "Amministratori e cariche",
            [
                rows_block(
                    ["Nome", "Carica", "Dal"],
                    [
                        [_persona_nome(p), _ruoli_str(p), _date_it(p.data_inizio_carica)]
                        for p in managers
                    ],
                )
            ],
        ),
        section(
            "Compagine sociale",
            [
                rows_block(
                    ["Socio", "Quota"],
                    [
                        [
                            _persona_nome(p),
                            f"{p.quota_percentuale}%" if p.quota_percentuale is not None else None,
                        ]
                        for p in shareholders
                    ],
                )
            ],
        ),
        section(
            "Organo di controllo",
            [
                rows_block(
                    ["Nome", "Carica"],
                    [[_persona_nome(p), _ruoli_str(p)] for p in auditors],
                )
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Orchestrazione (I/O + build + render)
# ---------------------------------------------------------------------------


async def export_scheda_pdf(primary, user, active) -> PdfResult:
    resp = await company_service.get_company(primary, active)
    if resp.company is None:
        raise NotFoundError("Nessuna azienda da esportare: compila prima il profilo aziendale")
    preferenze = await preferences_service.get_preferences_labeled(
        primary, user["id"], active
    )
    doc = build_scheda_doc(resp.company, preferenze)
    content = await asyncio.to_thread(pdf_service.render, doc)
    return PdfResult(content=content, filename=f"scheda-{_slug(resp.company.ragione_sociale)}.pdf")


async def export_dossier_pdf(primary, active) -> PdfResult:
    resp = await openapi_service.get_dossier(primary, active)
    if not resp.imported or not resp.dossier:
        raise NotFoundError("Nessun dossier importato per questa azienda")
    # Best-effort: il dossier resta scaricabile anche se i bilanci non si leggono.
    try:
        bilanci = await bilanci_service.get_bilanci(primary, active)
    except Exception:
        logger.exception("bilanci non leggibili per il PDF del dossier")
        bilanci = None
    doc = build_dossier_doc(resp, bilanci)
    content = await asyncio.to_thread(pdf_service.render, doc)
    denominazione = (resp.dossier.get("anagrafica") or {}).get("denominazione") or "azienda"
    return PdfResult(content=content, filename=f"dossier-{_slug(denominazione)}.pdf")
