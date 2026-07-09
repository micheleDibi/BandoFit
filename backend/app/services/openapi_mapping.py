"""Mapping del payload IT-full di openapi.it verso il modello BandoFit.

Funzioni PURE (nessun I/O): ricevono il payload grezzo + le lookup del
catalogo bandi e producono autofill, persone, dossier e valori derivati.
I percorsi dei campi sono bloccati sulla risposta reale registrata in
tests/fixtures/openapi/it_full_sample.json. Ogni accesso è difensivo:
un blocco mancante produce sezioni/valori nulli, mai un'eccezione.
"""

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any

# ----------------------------------------------------------------- utilità

def validate_partita_iva(piva: str) -> bool:
    """Checksum ufficiale della partita IVA italiana (11 cifre, Luhn-like)."""
    if not re.fullmatch(r"[0-9]{11}", piva):
        return False
    digits = [int(c) for c in piva]
    odd = sum(digits[0:10:2])
    even = 0
    for d in digits[1:10:2]:
        doubled = d * 2
        even += doubled - 9 if doubled > 9 else doubled
    return (10 - (odd + even) % 10) % 10 == digits[10]


def _get(payload: Any, *path: str) -> Any:
    """Accesso annidato difensivo: ritorna None se un livello manca."""
    current = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _clean(value: Any) -> str | None:
    """Stringa ripulita, oppure None se vuota/assente."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_openapi_date(value: Any) -> date | None:
    """Le date IT-full sono mezzanotte LOCALE serializzata (quasi) in UTC:
    ``1985-06-03T22:00:00`` significa 1985-06-04 (confermato dal codice
    fiscale della stessa persona nella fixture). Se l'orario è nel tardo
    pomeriggio/sera si arrotonda al giorno successivo."""
    text = _clean(value)
    if text is None:
        return None
    # openapi usa anche frazioni a 7 cifre ("...39.0000000Z") che fromisoformat
    # non accetta: tronchiamo a 6.
    text = re.sub(r"\.(\d{6})\d+", r".\1", text.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.hour >= 20:
        return parsed.date() + timedelta(days=1)
    return parsed.date()


def normalize_region(name: Any) -> str:
    """Normalizza i nomi regione per il confronto: minuscole, senza accenti,
    tronca alla prima '/', solo lettere (es. catalogo 'Trentino-Alto
    Adige/Südtirol' e openapi 'TRENTINO-ALTO ADIGE' → 'trentino alto adige')."""
    text = _clean(name) or ""
    text = text.split("/")[0]
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-zA-Z]+", " ", text)
    return " ".join(text.lower().split())


def ateco_division(code: Any) -> str | None:
    """Divisione ATECO a 2 cifre dal codice openapi (senza punti, es.
    '85592' → '85'; '1' → '01'). Il catalogo bandi conosce solo le divisioni."""
    text = _clean(code)
    if text is None:
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    return digits.zfill(2)[:2] if len(digits) >= 2 else digits.zfill(2)


_STATO_IMPRESA = {
    "A": "Attiva",
    "I": "Inattiva",
    "S": "Sospesa",
    "C": "Cessata",
    "R": "Registrata",
    "L": "In liquidazione",
}


def stato_impresa(payload: dict) -> str | None:
    code = _clean(_get(payload, "companyStatus", "activityStatus", "code"))
    if code and code.upper() in _STATO_IMPRESA:
        return _STATO_IMPRESA[code.upper()]
    return _clean(_get(payload, "companyStatus", "activityStatus", "description"))


# ------------------------------------------------------------ ateco secondari

def secondary_ateco_codes(payload: dict) -> list[str]:
    """Codici ATECO secondari (completi) dal payload: openapi li fornisce come
    stringa singola, stringa con separatori o lista, a seconda del caso."""
    raw = _get(payload, "atecoClassification", "secondaryAteco")
    if raw is None:
        raw = _get(payload, "atecoClassification", "secondaryAteco2022")
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        items = re.split(r"[,;|]", str(raw))
    seen: list[str] = []
    for item in items:
        code = _clean(item)
        if code and re.sub(r"\D", "", code) and code not in seen:
            seen.append(code)
    return seen


# -------------------------------------------------------------- classe/fascia

def classe_dimensionale(payload: dict) -> str | None:
    """Classe dimensionale: dalla classificazione openapi se leggibile,
    altrimenti derivata dal numero di dipendenti (soglie UE su organico)."""
    desc = (_clean(_get(payload, "ecofin", "enterpriseSize", "description")) or "").lower()
    for needle, value in (
        ("micro", "micro"),
        ("small", "piccola"), ("piccola", "piccola"),
        ("medium", "media"), ("media", "media"),
        ("large", "grande"), ("grande", "grande"),
    ):
        if needle in desc:
            return value
    employees = _get(payload, "employees", "employee")
    if isinstance(employees, (int, float)) and employees >= 0:
        if employees < 10:
            return "micro"
        if employees < 50:
            return "piccola"
        if employees < 250:
            return "media"
        return "grande"
    return None


_FASCE = (
    (100_000, "fino_100k"),
    (500_000, "100k_500k"),
    (2_000_000, "500k_2m"),
    (10_000_000, "2m_10m"),
    (50_000_000, "10m_50m"),
)


def fascia_fatturato(payload: dict) -> str | None:
    """Bucket del fatturato più recente, se il payload lo espone (assente per
    imprese senza bilanci depositati, es. la fixture)."""
    for path in (("ecofin", "turnover"), ("ecofin", "revenue"), ("balanceSheets", "turnover")):
        value = _get(payload, *path)
        if isinstance(value, (int, float)) and value >= 0:
            for limit, fascia in _FASCE:
                if value <= limit:
                    return fascia
            return "oltre_50m"
    return None


# ----------------------------------------------------------------- persone

def _map_roles(person: dict) -> list[dict]:
    roles = person.get("roles") or []
    mapped = []
    for entry in roles:
        if not isinstance(entry, dict):
            continue
        mapped.append(
            {
                "code": _clean(_get(entry, "role", "code")),
                "description": _clean(_get(entry, "role", "description")),
                "start": _clean(entry.get("roleStartDate")),
            }
        )
    return mapped


def _first_role_start(roles: list[dict]) -> str | None:
    dates = sorted(r["start"] for r in roles if r.get("start"))
    if not dates:
        return None
    parsed = parse_openapi_date(dates[0])
    return parsed.isoformat() if parsed else None


def extract_people(payload: dict) -> list[dict]:
    """Estrae cariche, soci e organi di controllo in righe per company_people."""
    people: list[dict] = []

    for person in payload.get("managers") or []:
        if not isinstance(person, dict):
            continue
        roles = _map_roles(person)
        birth = parse_openapi_date(person.get("birthDate"))
        people.append(
            {
                "kind": "manager",
                "nome": _clean(person.get("name")),
                "cognome": _clean(person.get("surname")),
                "denominazione": None,
                "codice_fiscale": _clean(person.get("taxCode")),
                "data_nascita": birth.isoformat() if birth else None,
                "luogo_nascita": _clean(person.get("birthTown")),
                "genere": _clean(_get(person, "gender", "code")),
                "ruoli": roles,
                "is_legale_rappresentante": bool(person.get("isLegalRepresentative")),
                "quota_percentuale": None,
                "data_inizio_carica": _first_role_start(roles),
                "raw": person,
            }
        )

    for person in payload.get("shareholders") or []:
        if not isinstance(person, dict):
            continue
        quota = person.get("percentShare")
        try:
            quota = float(quota) if quota is not None else None
        except (TypeError, ValueError):
            quota = None
        people.append(
            {
                "kind": "shareholder",
                "nome": _clean(person.get("name")),
                "cognome": _clean(person.get("surname")),
                "denominazione": _clean(person.get("companyName")),
                "codice_fiscale": _clean(person.get("taxCode")),
                "data_nascita": None,
                "luogo_nascita": None,
                "genere": None,
                "ruoli": [],
                "is_legale_rappresentante": False,
                "quota_percentuale": quota,
                "data_inizio_carica": None,
                "raw": person,
            }
        )

    for person in payload.get("auditors") or []:
        if not isinstance(person, dict):
            continue
        roles = _map_roles(person)
        birth = parse_openapi_date(person.get("birthDate"))
        people.append(
            {
                "kind": "auditor",
                "nome": _clean(person.get("name")),
                "cognome": _clean(person.get("surname")),
                "denominazione": _clean(person.get("companyName")),
                "codice_fiscale": _clean(person.get("taxCode")),
                "data_nascita": birth.isoformat() if birth else None,
                "luogo_nascita": _clean(person.get("birthTown")),
                "genere": _clean(_get(person, "gender", "code")),
                "ruoli": roles,
                "is_legale_rappresentante": False,
                "quota_percentuale": None,
                "data_inizio_carica": _first_role_start(roles),
                "raw": person,
            }
        )

    return people


# ------------------------------------------------------------------ autofill

# Campi del form azienda che l'import può compilare QUANDO SONO VUOTI.
# I valori inseriti dall'utente non vengono MAI sovrascritti: le differenze
# finiscono in `conflicts` e l'utente decide.
def build_autofill(
    payload: dict, current: dict | None, lookups
) -> tuple[dict, list[str], list[dict], dict]:
    """Ritorna (updates, applied, conflicts, suggestions).

    - updates: colonne di company_profiles da scrivere (solo campi vuoti);
    - applied: nomi dei campi compilati;
    - conflicts: campi in cui il valore utente differisce dal certificato;
    - suggestions: es. ATECO secondari da proporre come preferenze.
    """
    current = current or {}

    candidates: dict[str, Any] = {
        "ragione_sociale": _clean(_get(payload, "companyDetails", "companyName")),
        "codice_fiscale": _clean(_get(payload, "companyDetails", "taxCode")),
        "forma_giuridica": _clean(_get(payload, "legalForm", "detailedLegalForm", "description"))
        or _clean(_get(payload, "legalForm", "legalForm", "description")),
        "indirizzo": _clean(_get(payload, "address", "streetName")),
        "comune": _clean(_get(payload, "address", "town")),
        "provincia": _clean(_get(payload, "address", "province", "code")),
        "pec": _clean(payload.get("pec")),
        "telefono": _clean(_get(payload, "contacts", "telephoneNumber")),
        "sito_web": _clean(_get(payload, "webAndSocial", "website")),
        "classe_dimensionale": classe_dimensionale(payload),
        "fascia_fatturato": fascia_fatturato(payload),
    }

    cap = _clean(_get(payload, "address", "zipCode"))
    candidates["cap"] = cap if cap and re.fullmatch(r"[0-9]{5}", cap) else None

    incorporation = parse_openapi_date(
        _get(payload, "companyDates", "incorporationDate")
        or _get(payload, "companyDates", "startDate")
    )
    candidates["anno_fondazione"] = incorporation.year if incorporation else None

    employees = _get(payload, "employees", "employee")
    candidates["numero_dipendenti"] = (
        int(employees) if isinstance(employees, (int, float)) and employees >= 0 else None
    )

    # ATECO principale → divisione a 2 cifre del catalogo.
    primary_code = _clean(_get(payload, "atecoClassification", "ateco", "code")) or _clean(
        _get(payload, "atecoClassification", "ateco2022", "code")
    )
    division = ateco_division(primary_code)
    ateco_match = None
    if division:
        ateco_match = next((a for a in lookups.codici_ateco if a.codice == division), None)

    # Regione della sede legale → id del catalogo (nomi normalizzati).
    region_name = _get(payload, "address", "region", "description")
    region_match = None
    if region_name:
        target = normalize_region(region_name)
        region_match = next(
            (r for r in lookups.regioni if normalize_region(r.nome) == target), None
        )

    updates: dict[str, Any] = {}
    applied: list[str] = []
    conflicts: list[dict] = []

    def merge(field: str, certified: Any) -> None:
        if certified is None:
            return
        existing = current.get(field)
        is_empty = existing is None or (isinstance(existing, str) and not existing.strip())
        if is_empty:
            updates[field] = certified
            applied.append(field)
        elif str(existing).strip().lower() != str(certified).strip().lower():
            conflicts.append(
                {"campo": field, "valore_attuale": existing, "valore_certificato": certified}
            )

    for field, certified in candidates.items():
        merge(field, certified)

    if ateco_match is not None:
        if current.get("ateco_id") is None:
            updates["ateco_id"] = ateco_match.id
            updates["ateco_codice"] = ateco_match.codice
            updates["ateco_descrizione"] = ateco_match.descrizione
            applied.append("ateco_id")
        elif current.get("ateco_id") != ateco_match.id:
            conflicts.append(
                {
                    "campo": "ateco_id",
                    "valore_attuale": current.get("ateco_codice"),
                    "valore_certificato": ateco_match.codice,
                }
            )

    if region_match is not None:
        if current.get("regione_id") is None:
            updates["regione_id"] = region_match.id
            updates["regione_nome"] = region_match.nome
            applied.append("regione_id")
        elif current.get("regione_id") != region_match.id:
            conflicts.append(
                {
                    "campo": "regione_id",
                    "valore_attuale": current.get("regione_nome"),
                    "valore_certificato": region_match.nome,
                }
            )

    # Suggerimenti: divisioni degli ATECO secondari (diverse dalla principale)
    # da proporre come preferenze utente.
    suggestion_items: list[dict] = []
    for code in secondary_ateco_codes(payload):
        div = ateco_division(code)
        if not div or div == division:
            continue
        match = next((a for a in lookups.codici_ateco if a.codice == div), None)
        if match and not any(s["id"] == match.id for s in suggestion_items):
            suggestion_items.append(
                {"id": match.id, "codice": match.codice, "descrizione": match.descrizione}
            )
    suggestions = {"codici_ateco": suggestion_items}

    return updates, applied, conflicts, suggestions


# ------------------------------------------------------------------- derived

def _match_regione_id(name: Any, lookups) -> int | None:
    """Id del catalogo per un nome regione (confronto normalizzato)."""
    target = normalize_region(name) if name else None
    if not target:
        return None
    match = next((r for r in lookups.regioni if normalize_region(r.nome) == target), None)
    return match.id if match else None


def all_regioni_ids(payload: dict, lookups) -> list[int]:
    """Id del catalogo per le regioni di TUTTE le sedi: sede legale +
    ogni unità locale (`allOffices`). Le stringhe non mappabili (estere o
    anomale) vengono ignorate. Ordine stabile, senza duplicati."""
    names = [_get(payload, "address", "region", "description")]
    for office in payload.get("allOffices") or []:
        if isinstance(office, dict):
            names.append(_get(office, "address", "region", "description"))
    ids: list[int] = []
    for name in names:
        rid = _match_regione_id(name, lookups)
        if rid is not None and rid not in ids:
            ids.append(rid)
    return ids


def company_regioni_ids(company: dict | None, derived: dict | None) -> set[int]:
    """Insieme delle regioni (id catalogo) dell'azienda, TUTTE le sedi.
    Usa `derived.regioni_ids` (popolato all'import); per le aziende importate
    prima di questa modifica ricade sulla sola sede legale (`regione_id`)."""
    company = company or {}
    derived = derived or {}
    raw_ids = derived.get("regioni_ids")
    if raw_ids:
        return {int(i) for i in raw_ids if i is not None}
    return {int(i) for i in (company.get("regione_id"), derived.get("regione_id")) if i is not None}


def build_derived(payload: dict, lookups) -> dict:
    """Valori calcolati all'import, salvati in company_data.derived: input
    pronti per il futuro AI-check senza dover rifare il parsing."""
    primary_code = _clean(_get(payload, "atecoClassification", "ateco", "code")) or _clean(
        _get(payload, "atecoClassification", "ateco2022", "code")
    )
    region_name = _get(payload, "address", "region", "description")
    return {
        "ateco_principale": primary_code,
        "ateco_divisione": ateco_division(primary_code),
        "ateco_secondari": secondary_ateco_codes(payload),
        "regione_nome": _clean(region_name),
        "regione_id": _match_regione_id(region_name, lookups),
        # Tutte le sedi (sede legale + unità locali): usato dal punteggio di
        # compatibilità e dall'AI-check per l'eleggibilità territoriale.
        "regioni_ids": all_regioni_ids(payload, lookups),
        "classe_dimensionale": classe_dimensionale(payload),
        "fascia_fatturato": fascia_fatturato(payload),
        # Nessun `beneficiari`: le categorie del catalogo (Istituti Scolastici,
        # Enti pubblici…) non si deducono dalla visura. Le dichiara l'utente su
        # `company_profiles.beneficiari`.
        "stato_impresa": stato_impresa(payload),
    }


# ------------------------------------------------------------------- dossier

def _office_entry(office: dict) -> dict:
    return {
        "tipo": _clean(_get(office, "companyDetails", "officeType", "description")),
        "indirizzo": _clean(_get(office, "address", "streetName")),
        "comune": _clean(_get(office, "address", "town")),
        "provincia": _clean(_get(office, "address", "province", "code")),
        "cap": _clean(_get(office, "address", "zipCode")),
        "regione": _clean(_get(office, "address", "region", "description")),
        "stato": _clean(_get(office, "companyStatus", "activityStatus", "description")),
    }


def build_dossier(payload: dict) -> dict:
    """Raggruppa il payload grezzo nelle sezioni mostrate dalla pagina Azienda.
    I campi assenti restano None: il frontend nasconde le righe vuote.
    Il raw NON viene mai rimandato integralmente al client."""
    incorporation = parse_openapi_date(_get(payload, "companyDates", "incorporationDate"))
    start = parse_openapi_date(_get(payload, "companyDates", "startDate"))

    anagrafica = {
        "denominazione": _clean(_get(payload, "companyDetails", "companyName")),
        "partita_iva": _clean(_get(payload, "companyDetails", "vatCode")),
        "codice_fiscale": _clean(_get(payload, "companyDetails", "taxCode")),
        "forma_giuridica": _clean(_get(payload, "legalForm", "legalForm", "description")),
        "forma_giuridica_dettaglio": _clean(
            _get(payload, "legalForm", "detailedLegalForm", "description")
        ),
        "rea": _clean(_get(payload, "companyDetails", "reaCode")),
        "cciaa": _clean(_get(payload, "companyDetails", "cciaa")),
        "data_costituzione": incorporation.isoformat() if incorporation else None,
        "data_inizio_attivita": start.isoformat() if start else None,
        "stato": stato_impresa(payload),
        "gruppo_societario": _clean(_get(payload, "corporateGroups", "groupName"))
        if _get(payload, "corporateGroups", "belongsToGroup")
        else None,
        "capogruppo": _clean(_get(payload, "corporateGroups", "holdingCompanyName"))
        if _get(payload, "corporateGroups", "belongsToGroup")
        else None,
    }

    attivita = {
        "ateco": {
            "codice": _clean(_get(payload, "atecoClassification", "ateco", "code")),
            "descrizione": _clean(_get(payload, "atecoClassification", "ateco", "description")),
        },
        "ateco_2022": {
            "codice": _clean(_get(payload, "atecoClassification", "ateco2022", "code")),
            "descrizione": _clean(
                _get(payload, "atecoClassification", "ateco2022", "description")
            ),
        },
        "ateco_secondari": secondary_ateco_codes(payload),
        "nace": _clean(_get(payload, "internationalClassification", "nace", "code")),
        "sae": _clean(_get(payload, "sae", "description")),
    }

    offices = [
        _office_entry(office)
        for office in (payload.get("allOffices") or [])
        if isinstance(office, dict)
    ]
    sede = {
        "indirizzo": _clean(_get(payload, "address", "streetName")),
        "comune": _clean(_get(payload, "address", "town")),
        "provincia": _clean(_get(payload, "address", "province", "code")),
        "cap": _clean(_get(payload, "address", "zipCode")),
        "regione": _clean(_get(payload, "address", "region", "description")),
        "numero_sedi": _get(payload, "branches", "numberOfBranches"),
        "unita_locali": offices,
    }

    contatti = {
        "pec": _clean(payload.get("pec")),
        "email": _clean(_get(payload, "mail", "email")),
        "telefono": _clean(_get(payload, "contacts", "telephoneNumber")),
        "fax": _clean(_get(payload, "contacts", "fax")),
        "sito_web": _clean(_get(payload, "webAndSocial", "website")),
    }

    stats = payload.get("employeesStatistic") or {}
    dipendenti = {
        "numero": _get(payload, "employees", "employee"),
        "fascia": _clean(_get(payload, "employees", "employeeRange", "description")),
        "trend": _get(payload, "employees", "employeeTrend"),
        "percentuali_contratti": {
            "tempo_indeterminato": stats.get("permanentContract"),
            "tempo_determinato": stats.get("fixedTermContract"),
            "full_time": stats.get("fullTimeContract"),
            "part_time": stats.get("partialTimeContract"),
            "impiegati": stats.get("whiteCollar"),
            "operai": stats.get("blueCollar"),
            "apprendisti": stats.get("apprentice"),
        }
        if stats
        else None,
    }

    # Blocco economico: presente solo per imprese con bilanci depositati.
    ecofin = payload.get("ecofin") or {}
    bilanci = {
        "dimensione_impresa": _clean(_get(ecofin, "enterpriseSize", "description")),
        "fatturato": ecofin.get("turnover") if isinstance(ecofin.get("turnover"), (int, float)) else None,
        "capitale_sociale": ecofin.get("shareCapital") if isinstance(ecofin.get("shareCapital"), (int, float)) else None,
        "patrimonio_netto": ecofin.get("netWorth") if isinstance(ecofin.get("netWorth"), (int, float)) else None,
        "ebitda": ecofin.get("ebitda") if isinstance(ecofin.get("ebitda"), (int, float)) else None,
        "utile": ecofin.get("profit") if isinstance(ecofin.get("profit"), (int, float)) else None,
    }

    partecipazioni = [
        {
            "denominazione": _clean(entry.get("companyName")),
            "codice_fiscale": _clean(entry.get("taxCode")),
            "quota": entry.get("percentShare"),
        }
        for entry in (payload.get("affiliateCompanies") or [])
        if isinstance(entry, dict)
    ]

    flags = {
        "esportatore": _get(payload, "foreignTrade", "isExporter"),
        "importatore": _get(payload, "foreignTrade", "isImporter"),
        "startup_innovativa": _get(payload, "innovativeSmeAndSu", "isInnovativeStartUp"),
        "pmi_innovativa": _get(payload, "innovativeSmeAndSu", "isInnovativeSme"),
        "impresa_artigiana": _get(payload, "artisanBusinessRegistry", "belongsToArtisanBusinessRegistry"),
        "certificazione_soa": _get(payload, "soaCertification", "hasSoaCertification"),
        "gruppo_societario": _get(payload, "corporateGroups", "belongsToGroup"),
    }

    return {
        "anagrafica": anagrafica,
        "attivita": attivita,
        "sede": sede,
        "contatti": contatti,
        "dipendenti": dipendenti,
        "bilanci": bilanci,
        "partecipazioni": partecipazioni,
        "flags": flags,
    }
