"""Punteggio di compatibilità a-priori azienda↔bando.

Metrica DINAMICA (mai persistita) mostrata in elenco e dettaglio bando prima
e senza l'AI-check: quanti dei requisiti di catalogo del bando (regioni,
divisioni ATECO, settori, beneficiari) l'azienda soddisfa — es. «3/4».

Regole (confermate col prodotto):
- **Dentro una dimensione le voci sono alternative (OR)**: un bando che elenca
  quattro settori li accetta tutti, quindi UNO in comune basta perché il
  requisito «settore» sia soddisfatto. Contare 1/4 sarebbe sbagliato: non è
  un bando che chiede di operare in tutti e quattro i settori insieme.
- **Tra dimensioni si somma**: il punteggio è la frazione di requisiti
  soddisfatti su quelli valutabili, tutti dello stesso peso.
- **Tutte le sedi** concorrono alla dimensione territoriale (sede legale +
  unità locali): il requisito è soddisfatto se ANCHE UNA SOLA sede è in una
  regione ammessa. Ne segue che un bando nazionale (tutte le regioni del
  catalogo) soddisfa il territorio da sé: `nazionale` è solo un flag per
  spiegarlo in UI, non incide sul punteggio.
- **Gate di visibilità**: si calcola solo per un'azienda con P.IVA importata
  (ha `ateco_id` e `regione_id`); altrimenti nessun punteggio.
- Una dimensione entra nel conto solo se l'azienda ha quel dato: settore e
  categorie di beneficiario solo se dichiarati (non si penalizza una
  dimensione che l'azienda non ha ancora compilato).

I due DB (azienda sul primario, facet bando sul secondario) non si possono
unire in SQL: i facet azienda si costruiscono una volta per richiesta (con
cache TTL breve) e il confronto per-bando è Python puro.
"""

import logging
import time
from dataclasses import dataclass, field

from app.schemas.bando import LookupsOut
from app.services.family_service import owner_and_editable
from app.services.openapi_mapping import ateco_division, company_regioni_ids

logger = logging.getLogger("bandofit.compatibility")

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple["CompanyFacets | None", float]] = {}


@dataclass
class CompanyFacets:
    """Insiemi di id (namespace lookup del catalogo) dei facet dell'azienda."""

    regioni_ids: set[int] = field(default_factory=set)
    ateco_ids: set[int] = field(default_factory=set)
    settore_id: int | None = None
    beneficiari_ids: set[int] = field(default_factory=set)
    sufficiente: bool = False


def build_company_facets(
    company: dict | None, derived: dict | None, lookups: LookupsOut
) -> CompanyFacets:
    """Costruisce i facet dell'azienda (id catalogo) da `company_profiles` +
    `company_data.derived`. Puro (nessun I/O)."""
    company = company or {}
    derived = derived or {}

    # ATECO: divisioni (2 cifre) → id lookup. Principale già come `ateco_id`;
    # le secondarie certificate si mappano via il codice divisione.
    codice_to_id = {a.codice: a.id for a in lookups.codici_ateco}
    ateco_ids: set[int] = set()
    if company.get("ateco_id") is not None:
        ateco_ids.add(int(company["ateco_id"]))
    for code in [derived.get("ateco_divisione"), *(derived.get("ateco_secondari") or [])]:
        division = ateco_division(code)
        if division and division in codice_to_id:
            ateco_ids.add(codice_to_id[division])

    # Beneficiari: dichiarati dall'utente, non deducibili dalla visura (il
    # catalogo distingue Istituti Scolastici, Enti pubblici… che nessun
    # attributo camerale esprime). Vuoto → requisito non valutato.
    beneficiari_ids = {
        int(b["id"]) for b in (company.get("beneficiari") or []) if b.get("id") is not None
    }

    settore_id = company.get("settore_id")

    return CompanyFacets(
        regioni_ids=company_regioni_ids(company, derived),
        ateco_ids=ateco_ids,
        settore_id=int(settore_id) if settore_id is not None else None,
        beneficiari_ids=beneficiari_ids,
        # P.IVA importata: ATECO e regione della sede legale valorizzati.
        sufficiente=company.get("ateco_id") is not None and company.get("regione_id") is not None,
    )


def compute_compatibilita(
    facets: CompanyFacets | None, bando_facets: dict, *, totale_regioni: int
) -> dict | None:
    """Frazione «requisiti soddisfatti / requisiti valutabili» del bando.
    Ritorna None (nessun badge) se l'azienda non è sufficiente o il bando non
    ha requisiti valutabili. Puro (nessun I/O).

    `bando_facets`: {"regioni": [id...], "ateco": [id...], "settori": [id...],
    "beneficiari": [id...]} (id del namespace lookup del catalogo)."""
    if facets is None or not facets.sufficiente:
        return None

    company_sets = {
        "regioni": facets.regioni_ids,
        "ateco": facets.ateco_ids,
        "settori": {facets.settore_id} if facets.settore_id is not None else set(),
        "beneficiari": facets.beneficiari_ids,
    }

    dimensioni: dict[str, dict] = {}
    soddisfatte = 0
    for dim, company_set in company_sets.items():
        bando_set = {i for i in (bando_facets.get(dim) or []) if i is not None}
        # La dimensione entra solo se il bando la vincola E l'azienda ha il dato.
        if not bando_set or not company_set:
            continue
        intersezione = bando_set & company_set
        # Le voci di una dimensione sono alternative: una in comune basta.
        soddisfatta = bool(intersezione)
        dimensioni[dim] = {
            "soddisfatta": soddisfatta,
            "matched": len(intersezione),
            "totale": len(bando_set),
            "matched_ids": sorted(intersezione),
            # Bando aperto a tutte le regioni del catalogo: il territorio è
            # soddisfatto da sé (l'intersezione non può essere vuota). Solo
            # una nota per la UI, che altrimenti elencherebbe 20 chip.
            "nazionale": dim == "regioni" and len(bando_set) >= totale_regioni > 0,
        }
        soddisfatte += soddisfatta

    if not dimensioni:
        return None
    totale = len(dimensioni)
    return {
        "punteggio": round(soddisfatte / totale * 100),
        "matched": soddisfatte,
        "totale": totale,
        "dimensioni": dimensioni,
    }


def invalidate_company_facets(owner_id: str) -> None:
    """Da chiamare dopo OGNI scrittura sui dati aziendali (import P.IVA,
    modifica del profilo): senza, il badge resterebbe fermo ai dati vecchi
    fino allo scadere del TTL — e proprio dopo l'import, che è l'azione che
    lo abilita, non comparirebbe."""
    _cache.pop(str(owner_id), None)


async def load_company_facets(primary, user: dict, lookups: LookupsOut) -> CompanyFacets | None:
    """Facet dell'azienda della famiglia (i figli ereditano dal titolare), SENZA
    il gate `sufficiente`: None solo se l'azienda non esiste.

    È la fonte unica dei «dati reali dell'azienda»: il badge di compatibilità e
    l'AI-check li vogliono filtrati (vedi `get_company_facets`), il preset
    «Bandi per te» no — lì anche il solo settore, o i soli beneficiari
    dichiarati a mano, sono un filtro utile.

    Cache in-memory a TTL breve per owner (invalidata dalle scritture)."""
    owner_id, _editable = await owner_and_editable(primary, user)

    cached = _cache.get(owner_id)
    if cached is not None and (time.monotonic() - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    company_resp = (
        await primary.table("company_profiles")
        .select("id,ateco_id,settore_id,regione_id,beneficiari")
        .eq("parent_id", owner_id)
        .limit(1)
        .execute()
    )
    company = company_resp.data[0] if company_resp.data else None

    facets: CompanyFacets | None = None
    if company is not None:
        data_resp = (
            await primary.table("company_data")
            .select("derived")
            .eq("company_profile_id", company["id"])
            .limit(1)
            .execute()
        )
        derived = data_resp.data[0].get("derived") if data_resp.data else None
        facets = build_company_facets(company, derived, lookups)

    if len(_cache) > 512:  # backstop: evita crescita illimitata
        _cache.clear()
    _cache[owner_id] = (facets, time.monotonic())
    return facets


async def get_company_facets(primary, user: dict, lookups: LookupsOut) -> CompanyFacets | None:
    """Facet per il PUNTEGGIO: None anche quando l'azienda non è sufficiente
    (P.IVA non importata), perché senza ATECO e regione il badge mentirebbe.

    Il punteggio è ACCESSORIO: qualunque errore nella lettura dei dati
    aziendali degrada a None (nessun badge) e non deve mai far fallire
    l'elenco o il dettaglio di un bando, che vivono sul DB secondario."""
    try:
        facets = await load_company_facets(primary, user, lookups)
    except Exception:
        logger.warning("compatibilità non calcolabile: dati aziendali illeggibili", exc_info=True)
        return None
    return facets if facets is not None and facets.sufficiente else None
