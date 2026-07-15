"""Export PDF dell'azienda attiva: due documenti.

- **Scheda azienda** (`export_scheda_pdf`): i dati DICHIARATI dall'utente
  (`company_profiles`) + le preferenze di ricerca seguite. Non certificato.
- **Dossier** (`export_dossier_pdf`): la visura certificata del Registro Imprese
  importata da openapi.it. Alimentato SOLO da `openapi_service.get_dossier`
  (`DossierResponse`, già privo del `raw` grezzo): il payload grezzo non esce.

Entrambi costruiscono un `PdfDoc` astratto (puro, testabile) e lo rendono con
`pdf_service.render` in un thread (il rendering è CPU-bound)."""

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.errors import NotFoundError
from app.schemas.openapi_data import DossierResponse
from app.services import company_service, openapi_service, pdf_service, preferences_service
from app.services.pdf_service import PdfDoc, chips_block, fields_block, rows_block, section

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


def build_dossier_doc(resp: DossierResponse) -> PdfDoc:
    """Dossier certificato. `resp.dossier` è un dict a 8 sezioni (build_dossier),
    `resp.people` le cariche/soci, `resp.derived` i valori calcolati. Nessun dato
    grezzo: `DossierResponse` non contiene `raw`."""
    d = resp.dossier or {}
    ana = d.get("anagrafica") or {}
    att = d.get("attivita") or {}
    sede = d.get("sede") or {}
    con = d.get("contatti") or {}
    dip = d.get("dipendenti") or {}
    bil = d.get("bilanci") or {}
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
                        ("Fatturato", _eur(bil.get("fatturato"))),
                        ("Capitale sociale", _eur(bil.get("capitale_sociale"))),
                        ("Patrimonio netto", _eur(bil.get("patrimonio_netto"))),
                        ("EBITDA", _eur(bil.get("ebitda"))),
                        ("Utile", _eur(bil.get("utile"))),
                    ]
                )
            ],
        ),
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
    doc = build_dossier_doc(resp)
    content = await asyncio.to_thread(pdf_service.render, doc)
    denominazione = (resp.dossier.get("anagrafica") or {}).get("denominazione") or "azienda"
    return PdfResult(content=content, filename=f"dossier-{_slug(denominazione)}.pdf")
