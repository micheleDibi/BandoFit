"""Serializzazione degli input e prompt dell'AI-check.

Il bando viene appiattito in testo con INDICI DI SEZIONE ([META], [S1]..[Sn]):
ogni voce estratta dal modello deve citare l'indice e copiare il testo esatto,
così il report è verificabile punto-punto dall'utente (e in codice: il testo
citato deve essere una substring della sezione citata).

Il profilo azienda ("company pack") usa NOMI DI CAMPO ESPLICITI: il matching
deve citare il nome esatto del campo usato per ogni verdetto. I campi assenti
sono resi come NON DISPONIBILE — mai lasciare che l'assenza passi per un dato.

PROMPT_VERSION invalida la cache delle estrazioni quando i prompt cambiano.
"""

import hashlib
import json

PROMPT_VERSION = 1

# --------------------------------------------------------------- prompt A

SYSTEM_EXTRACT = """Sei un analista esperto di bandi e agevolazioni pubbliche italiane.
Dal testo del bando fornito estrai, separatamente:
1. i REQUISITI DI AMMISSIBILITÀ OBBLIGATORI: condizioni la cui mancanza esclude \
il richiedente (formule tipiche: "possono presentare domanda", "sono ammesse", \
"a pena di esclusione", "requisiti di ammissibilità", "sono escluse");
2. i CRITERI DI VALUTAZIONE GRADUABILI: elementi premiali che aumentano il \
punteggio in graduatoria senza essere condizioni di esclusione.

REGOLE VINCOLANTI:
- Usa SOLO il testo fornito: non aggiungere requisiti impliciti, prassi di \
settore o conoscenze esterne al testo.
- Ogni requisito e ogni criterio DEVE avere una citazione con l'indice della \
sezione di provenienza (`sezione` è il solo identificatore, SENZA parentesi \
quadre: "META", "S1", "S2", ...) e il testo COPIATO ALLA LETTERA dalla \
sezione (una frase o un passaggio, senza riformulare).
- `dato_richiesto` indica quale dato aziendale serve per verificare il \
requisito (es. "regione della sede operativa", "classe dimensionale", \
"iscrizione all'albo delle imprese artigiane").
- `griglia.presente` è true SOLO se il testo pubblica punti espliciti per \
criterio (es. "fino a 20 punti"); in quel caso valorizza `punti_max` dei \
criteri. Se il testo rimanda a un allegato non fornito per i punteggi, \
usa `presente=false` e `fonte="allegato"`. Se non c'è alcuna griglia, \
`presente=false` e `fonte="assente"`.
- Se il bando limita territorio, settore o tipologia di beneficiario, \
questi sono requisiti obbligatori (usa le sezioni del testo o, in mancanza, \
la sezione [META] con le classificazioni del catalogo).
- Scrivi in italiano, in modo conciso. Gli id sono progressivi: R1, R2, ... \
per i requisiti; C1, C2, ... per i criteri."""

# --------------------------------------------------------------- prompt B

SYSTEM_MATCH = """Sei un verificatore che confronta punto per punto i requisiti di un bando \
con il profilo reale di un'azienda. Per OGNI requisito e OGNI criterio \
dell'estrazione fornita emetti un verdetto usando ESCLUSIVAMENTE i dati del \
profilo azienda fornito.

REGOLE VINCOLANTI:
- Se il dato aziendale necessario è assente, vuoto, "NON DISPONIBILE" o \
ambiguo, l'esito è `dato_mancante` — MAI `soddisfatto` per assenza di \
controindicazioni.
- Per gli esiti `soddisfatto` e `non_soddisfatto`, `dato_azienda` deve citare \
il NOME ESATTO del campo del profilo (es. "regione_nome", \
"dossier.attivita.ateco.codice") e il valore letto. Se non puoi citare un \
campo preciso, l'esito è `dato_mancante` con `dato_azienda` null.
- Le equivalenze semantiche sono ammesse ma vanno motivate (es. la divisione \
ATECO 62 rientra nei "servizi ICT"; la categoria "PMI" copre micro, piccole \
e medie imprese). Motivazione: massimo 2 frasi, in italiano.
- Le `motivazione` e le `descrizione` sono lette dall'utente finale: linguaggio \
naturale, MAI nomi tecnici di campi o percorsi (niente "settore_nome", \
"derived.ateco_secondari[1]") — descrivi il dato in italiano (es. «il settore \
indicato nei dati aziendali», «le categorie di beneficiario dichiarate»). \
I nomi tecnici vanno SOLO in `dato_azienda.campo` e in `dati_mancanti.campo`.
- Le VERIFICHE STRUTTURATE fornite sono fatti già accertati con confronto \
esatto sui dati del catalogo: non contraddirle mai.
- Per i requisiti TERRITORIALI considera TUTTE le sedi dell'azienda (sede \
legale e ogni unità locale in `dossier.sede.unita_locali`): il vincolo è \
soddisfatto se ANCHE UNA SOLA sede si trova in una regione ammessa.
- `criteri`: usa `parzialmente_soddisfatto` quando l'azienda copre solo in \
parte il criterio. NON assegnare MAI punteggi numerici: solo verdetti.
- Compila anche punti di forza, punti di debolezza e l'elenco dei dati \
mancanti (campo del profilo da completare + a quale requisito serve).
- Ogni verdetto deve riferire l'`id` esatto (R1, C2, ...) dell'estrazione."""


# --------------------------------------------------------- input del bando

def _segments_text(segments: list | None) -> str:
    parts: list[str] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "")
        if not text:
            continue
        parts.append(f"**{text}**" if seg.get("kind") == "bold" else text)
    return "".join(parts)


def _item_text(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        if item.get("segments"):
            return _segments_text(item["segments"])
        return str(item.get("text") or "")
    return ""


def _flatten_section(section: dict) -> str:
    """Una sezione di `contenuto` resa come testo semplice."""
    kind = str(section.get("type") or "")
    if kind in ("h2", "h3"):
        return f"## {section.get('text') or ''}".strip()
    if kind in ("bullet_list", "numbered_list"):
        lines = []
        for i, item in enumerate(section.get("items") or [], start=1):
            prefix = f"{i}." if kind == "numbered_list" else "-"
            text = _item_text(item)
            if text:
                lines.append(f"{prefix} {text}")
        return "\n".join(lines)
    if kind == "faq":
        lines = []
        for item in section.get("items") or []:
            if not isinstance(item, dict):
                continue
            question = str(item.get("q") or "")
            answer = item.get("a") or {}
            answer_text = (
                _segments_text(answer.get("segments"))
                if isinstance(answer, dict)
                else str(answer)
            )
            if question or answer_text:
                lines.append(f"D: {question}\nR: {answer_text}")
        return "\n".join(lines)
    # paragraph e tipi sconosciuti: segments o text
    if section.get("segments"):
        return _segments_text(section["segments"])
    return str(section.get("text") or "")


def _fmt(value) -> str:
    if value is None or value == "" or value == []:
        return "non indicato"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _junction_names(bando: dict, junction: str, inner: str) -> list[str]:
    names = []
    for row in bando.get(junction) or []:
        item = row.get(inner) if isinstance(row, dict) else None
        if isinstance(item, dict):
            label = item.get("nome") or item.get("codice")
            if item.get("codice") and item.get("descrizione"):
                label = f"{item['codice']} — {item['descrizione']}"
            if label:
                names.append(str(label))
    return names


def build_bando_input(
    bando: dict,
    contenuto: dict | None,
    allegati_texts: list[tuple[str, str]] | None = None,
) -> tuple[str, dict[str, str]]:
    """Serializza TUTTI i dati del bando in testo indicizzato per sezione.

    Ritorna (testo completo, mappa indice→testo della sezione) — la mappa
    serve alla verifica in codice delle citazioni. `allegati_texts` è il
    gancio per la futura ingestione dei PDF ufficiali ([A1]..[An])."""
    lookup = bando.get("tipologie_bando") or {}
    modalita = bando.get("modalita_erogazione") or {}
    programma = bando.get("programmi") or {}
    allegati = bando.get("allegati") or []
    allegati_labels = [
        str(a.get("label") or a.get("nome") or a.get("url") or "allegato")
        for a in allegati
        if isinstance(a, dict)
    ]

    meta_lines = [
        f"Titolo: {_fmt(bando.get('titolo') or bando.get('titolo_breve'))}",
        f"Ente erogatore: {_fmt(bando.get('ente_erogatore'))}",
        f"Stato: {_fmt(bando.get('stato_bando'))}",
        f"Data pubblicazione: {_fmt(bando.get('data_pubblicazione'))}",
        f"Data apertura: {_fmt(bando.get('data_apertura'))}",
        f"Data scadenza: {_fmt(bando.get('data_scadenza'))}",
        f"Dotazione totale (EUR): {_fmt(bando.get('importo_totale_eur'))}",
        f"Importo max per progetto (EUR): {_fmt(bando.get('importo_max_per_progetto_eur'))}",
        f"Tipologia: {_fmt(lookup.get('nome'))}",
        f"Modalità di erogazione: {_fmt(modalita.get('nome'))}",
        f"Programma: {_fmt(programma.get('nome'))}",
        f"Area geografica: {_fmt(bando.get('area_geografica'))}",
        f"Tematiche: {_fmt(bando.get('tematica'))}",
        f"Regioni ammesse (catalogo): {_fmt(_junction_names(bando, 'bando_regioni', 'regioni'))}",
        f"Settori (catalogo): {_fmt(_junction_names(bando, 'bando_settori', 'settori'))}",
        f"Beneficiari (catalogo): {_fmt(_junction_names(bando, 'bando_beneficiari', 'beneficiari'))}",
        f"Codici ATECO (catalogo): {_fmt(_junction_names(bando, 'bando_codici_ateco', 'codici_ateco'))}",
        f"Sintesi: {_fmt(bando.get('descrizione_breve'))}",
        f"Allegati ufficiali (NON inclusi in questo testo): {_fmt(allegati_labels)}",
    ]

    sections: dict[str, str] = {"META": "\n".join(meta_lines)}
    blocks = [f"[META]\n{sections['META']}"]

    for i, section in enumerate((contenuto or {}).get("sections") or [], start=1):
        if not isinstance(section, dict):
            continue
        text = _flatten_section(section).strip()
        key = f"S{i}"
        sections[key] = text
        blocks.append(f"[{key}]\n{text}")

    for i, (label, text) in enumerate(allegati_texts or [], start=1):
        key = f"A{i}"
        sections[key] = text
        blocks.append(f"[{key}] {label}\n{text}")

    return "\n\n".join(blocks), sections


def compute_content_hash(bando: dict, bando_text: str) -> str:
    """Chiave di validità della cache estrazioni: SEMPRE l'hash del testo
    serializzato che il modello vede davvero. L'hash di ingestione del
    catalogo (`hash_bando`) NON basta: non copre i facet delle junction
    (regioni/settori/ATECO/beneficiari), le date e lo stato che entrano nel
    blocco [META] e da cui l'estrazione deriva requisiti obbligatori."""
    return hashlib.sha256(bando_text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ company pack

NON_DISPONIBILE = "NON DISPONIBILE"

# Campi del form aziendale resi SEMPRE, con NON DISPONIBILE esplicito:
# sono l'insieme canonico che il matching può citare.
_COMPANY_FIELDS = (
    "ragione_sociale", "forma_giuridica", "partita_iva", "codice_fiscale",
    "ateco_codice", "ateco_descrizione", "settore_nome", "regione_nome",
    "anno_fondazione", "indirizzo", "comune", "provincia", "cap",
    "classe_dimensionale", "numero_dipendenti", "fascia_fatturato",
    "pec", "telefono", "sito_web",
)


def _flatten_dict(prefix: str, value, lines: list[str]) -> None:
    """Dizionari annidati → righe `percorso.puntato: valore` (solo valori presenti)."""
    if isinstance(value, dict):
        for key, sub in value.items():
            _flatten_dict(f"{prefix}.{key}" if prefix else str(key), sub, lines)
    elif isinstance(value, list):
        if not value:
            return
        if all(isinstance(v, (str, int, float)) for v in value):
            lines.append(f"{prefix}: {', '.join(str(v) for v in value)}")
        else:
            for i, item in enumerate(value, start=1):
                _flatten_dict(f"{prefix}[{i}]", item, lines)
    elif value is not None and value != "":
        lines.append(f"{prefix}: {value}")


def build_company_pack(
    profile: dict,
    company: dict | None,
    dossier: dict | None,
    derived: dict | None,
    people: list[dict] | None,
    visura_text: str | None,
    visura_max_chars: int,
) -> str:
    """Profilo persona + azienda serializzato con nomi di campo citabili."""
    blocks: list[str] = []

    # Il valore del CF personale NON entra nel pack: il report (visibile a
    # tutta l'azienda) cita i dati usati, e il CF del titolare non deve
    # trapelare agli account collegati. Basta sapere se esiste ed è verificato.
    if profile.get("codice_fiscale"):
        cf_stato = "presente" + (
            ", verificato all'Anagrafe Tributaria" if profile.get("cf_verified_at") else ", non verificato"
        )
    else:
        cf_stato = NON_DISPONIBILE
    persona = [
        f"nome: {_fmt(profile.get('nome'))}",
        f"cognome: {_fmt(profile.get('cognome'))}",
        f"codice_fiscale (stato): {cf_stato}",
    ]
    blocks.append("## Profilo utente\n" + "\n".join(persona))

    company_lines = [
        f"{field}: {company.get(field) if (company or {}).get(field) not in (None, '') else NON_DISPONIBILE}"
        for field in _COMPANY_FIELDS
    ] if company else [NON_DISPONIBILE]
    if company:
        # Multi-valore: serializzato a parte, coi soli nomi (il campo citabile
        # resta `beneficiari`). Vuoto = non dichiarato, non "nessuna categoria".
        nomi = [b.get("nome") for b in (company.get("beneficiari") or []) if b.get("nome")]
        company_lines.append(f"beneficiari: {', '.join(nomi) if nomi else NON_DISPONIBILE}")
    blocks.append("## Dati aziendali (form)\n" + "\n".join(company_lines))

    if derived:
        lines: list[str] = []
        _flatten_dict("derived", derived, lines)
        if lines:
            blocks.append("## Dati derivati dal Registro Imprese\n" + "\n".join(lines))

    if dossier:
        lines = []
        _flatten_dict("dossier", dossier, lines)
        if lines:
            blocks.append("## Dossier certificato (Registro Imprese)\n" + "\n".join(lines))

    if people:
        rows = []
        for person in people:
            name = " ".join(
                p for p in (person.get("nome"), person.get("cognome")) if p
            ) or person.get("denominazione") or "—"
            ruoli = person.get("ruoli") or []
            ruoli_text = ", ".join(
                str(r.get("role") or r) if isinstance(r, dict) else str(r) for r in ruoli
            )
            legale = " [legale rappresentante]" if person.get("is_legale_rappresentante") else ""
            rows.append(f"- {person.get('kind')}: {name}{legale} ({ruoli_text or 'ruolo non indicato'})")
        blocks.append("## Persone e cariche\n" + "\n".join(rows))

    if visura_text:
        text = visura_text[:visura_max_chars]
        truncated = " (troncato)" if len(visura_text) > visura_max_chars else ""
        blocks.append(f"## Testo della visura camerale{truncated}\n{text}")

    return "\n\n".join(blocks)


def build_matching_input(
    extraction: dict, prechecks: dict, company_pack: str
) -> str:
    """Messaggio utente dello Stadio B: estrazione + verifiche strutturate +
    profilo azienda."""
    return (
        "## Estrazione dal bando (requisiti e criteri da verificare)\n"
        + json.dumps(extraction, ensure_ascii=False, indent=1)
        + "\n\n## Verifiche strutturate (fatti già accertati, non contraddirli)\n"
        + json.dumps(prechecks, ensure_ascii=False, indent=1)
        + "\n\n## Profilo azienda\n"
        + company_pack
    )
