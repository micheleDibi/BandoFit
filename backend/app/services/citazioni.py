"""Verifica deterministica delle citazioni (modulo PURO, pubblico).

Nato per le regole di partenariato (WP3), dove le citazioni puntano anche al
testo estratto dai PDF ufficiali (blocchi `[D1-p3]`), ma riusabile da chiunque
debba controllare che un `testo_esatto` prodotto da un modello compaia davvero
nella sezione indicata.

Rispetto a `_citation_verified` dell'AI-check (che NON cambia) tollera gli
artefatti tipici dei PDF, normalizzando allo stesso modo ago e pagliaio:
- NFKC (scioglie le legature «ﬁ», «ﬂ», gli spazi speciali, «…» → «...»);
- apostrofi, virgolette e trattini tipografici ricondotti ad ASCII;
- soft hyphen e caratteri a larghezza zero rimossi;
- sillabazione a fine riga («ammis-\\nsibili» → «ammissibili»);
- spazi collassati, minuscolo, senza i marcatori `**` della serializzazione;
- chiave di sezione normalizzata («[D1-P3]», «D1 p3» → «D1-p3»);
- citazioni a cavallo di pagina (pagina N + N+1);
- ellissi («...», «…», «[...]»): ogni frammento ≥ 20 caratteri, in ordine.

Invariante: tutto ciò che accetta `_citation_verified` lo accetta anche
`verifica_citazione`, perché il primo tentativo ne replica la semantica. Unica
eccezione voluta: una citazione vuota (o di soli spazi e `**`) non è mai
verificata, mentre la vecchia la accettava (la stringa vuota è in ogni testo).
"""

import re
import unicodedata
from functools import lru_cache

# Frammenti di una citazione con ellissi: sotto questa lunghezza un pezzo
# (es. «il», «art. 3») si trova ovunque e non prova nulla.
MIN_FRAMMENTO_ELLISSI = 20

_MAPPA_TIPOGRAFICA = str.maketrans(
    {
        # apostrofi e virgolette singole
        "‘": "'", "’": "'", "‚": "'", "‛": "'",
        "′": "'", "´": "'", "`": "'", "ʼ": "'",
        "‹": "'", "›": "'",
        # virgolette doppie (anche le caporali: il modello le scambia)
        "“": '"', "”": '"', "„": '"', "‟": '"',
        "″": '"', "«": '"', "»": '"',
        # trattini e segno meno
        "‐": "-", "‑": "-", "‒": "-", "–": "-",
        "—": "-", "―": "-", "−": "-", "﹣": "-", "－": "-",
        # caratteri a larghezza zero
        "​": None, "‌": None, "‍": None, "⁠": None,
        "﻿": None, "᠎": None,
    }
)

# Soft hyphen: invisibile, ma a fine riga spezza la parola («parte\xad\ncipare»).
_SOFT_HYPHEN = re.compile("­\\s*")
# Sillabazione a fine riga: il trattino e l'a capo spariscono.
_SILLABAZIONE = re.compile(r"(?<=\w)-[ \t]*\r?\n\s*(?=\w)")
# Variante sull'ago: il modello copia la sillabazione come «ammis- sibili».
_TRATTINO_SPAZIO = re.compile(r"(?<=\w)- (?=\w)")
# Confronto insensibile ai trattini tra parole («pubblico-\nprivato» diventa
# «pubblicoprivato» con la sillabazione: così torna uguale a «pubblico-privato»).
_TRATTINO_TRA_PAROLE = re.compile(r"(?<=\w)-\s?(?=\w)")
_ELLISSI = re.compile(r"\[\s*\.{3,}\s*\]|\(\s*\.{3,}\s*\)|\.{3,}")
_BORDI_AGO = "\"' "
_PUNTEGGIATURA_FINALE = ".,;:"

# «D1-p3», «[D1-P3]», «D1 p3», «D1 pag. 3», «d1, pagina 3», «D1p3», «D1-3».
_CHIAVE_DOCUMENTO = re.compile(
    r"^d\s*(\d+)\s*(?:[-_,.:/ ]+\s*(?:p(?:ag(?:ina)?)?|page|pg)?|p(?:ag(?:ina)?)?|page|pg)"
    r"\s*\.?\s*(\d+)$",
    re.IGNORECASE,
)


def normalizza_caratteri(testo: str) -> str:
    """Normalizzazione dei caratteri che conserva maiuscole e a capo: NFKC,
    tipografia → ASCII, soft hyphen e larghezza zero rimossi, sillabazione a
    fine riga ricucita. Base comune di `normalizza_testo` e del
    pre-classificatore (che ha bisogno delle maiuscole per le sigle)."""
    if not isinstance(testo, str) or not testo:
        return ""
    s = testo.translate(_MAPPA_TIPOGRAFICA)
    s = unicodedata.normalize("NFKC", s)
    # NFKC può produrre di nuovo caratteri della mappa (forme a larghezza piena).
    s = s.translate(_MAPPA_TIPOGRAFICA)
    s = _SOFT_HYPHEN.sub("", s)
    return _SILLABAZIONE.sub("", s)


def normalizza_testo(testo: str) -> str:
    """Forma canonica per il confronto: `normalizza_caratteri`, senza `**`,
    spazi collassati, minuscolo (casefold)."""
    s = normalizza_caratteri(testo).replace("**", "")
    return " ".join(s.split()).casefold()


def normalizza_sezione(chiave: str) -> str:
    """Chiave di sezione canonica: «[D1-P3]», «D1 p3», «D1 pag. 3» → «D1-p3»;
    le altre («[s2]», «meta») in maiuscolo senza spazi («S2», «META»)."""
    if not isinstance(chiave, str):
        return ""
    s = unicodedata.normalize("NFKC", chiave).strip()
    s = s.strip("[]() ").strip()
    s = " ".join(s.split())
    m = _CHIAVE_DOCUMENTO.match(s)
    if m:
        return f"D{int(m.group(1))}-p{int(m.group(2))}"
    return s.replace(" ", "").upper()


def _pagina_successiva(chiave: str) -> str | None:
    m = re.fullmatch(r"D(\d+)-p(\d+)", chiave)
    if not m:
        return None
    return f"D{m.group(1)}-p{int(m.group(2)) + 1}"


@lru_cache(maxsize=128)
def _pagliaio(testo: str) -> tuple[str, str]:
    """(testo normalizzato, variante senza trattini tra parole). In cache
    perché la stessa sezione viene interrogata da molte citazioni."""
    norm = normalizza_testo(testo)
    return norm, _TRATTINO_TRA_PAROLE.sub("", norm)


def _varianti_ago(ago: str) -> list[str]:
    varianti = [ago]
    senza_trattino = _TRATTINO_SPAZIO.sub("", ago)
    if senza_trattino != ago:
        varianti.append(senza_trattino)
    ripulito = ago.strip(_BORDI_AGO).rstrip(_PUNTEGGIATURA_FINALE).strip(_BORDI_AGO)
    if ripulito and ripulito != ago:
        varianti.append(ripulito)
    return varianti


def _frammenti_in_ordine(frammenti: list[str], pagliaio: str) -> bool:
    pos = 0
    for frammento in frammenti:
        i = pagliaio.find(frammento, pos)
        if i < 0:
            return False
        pos = i + len(frammento)
    return True


def _trova(ago: str, testo_sezione: str) -> bool:
    """Ago GIÀ normalizzato contro il testo grezzo di una sezione."""
    norm, compatto = _pagliaio(testo_sezione)
    for variante in _varianti_ago(ago):
        if variante in norm:
            return True
        variante_compatta = _TRATTINO_TRA_PAROLE.sub("", variante)
        if variante_compatta and variante_compatta in compatto:
            return True
    if not _ELLISSI.search(ago):
        return False
    frammenti = [f.strip(_BORDI_AGO) for f in _ELLISSI.split(ago)]
    frammenti = [f for f in frammenti if f]
    if not frammenti or any(len(f) < MIN_FRAMMENTO_ELLISSI for f in frammenti):
        return False
    if _frammenti_in_ordine(frammenti, norm):
        return True
    compatti = [_TRATTINO_TRA_PAROLE.sub("", f) for f in frammenti]
    return _frammenti_in_ordine(compatti, compatto)


def _verifica_come_ai_check(chiave: str, testo_esatto: str, sezioni: dict[str, str]) -> bool:
    """Stessa semantica di `ai_check_scoring._citation_verified` (che resta
    com'è): garantisce che la nuova verifica non rifiuti nulla di ciò che la
    vecchia accettava. Unica differenza: la citazione vuota (o di soli spazi e
    marcatori `**`) è falsa."""
    key = chiave.strip().lstrip("[").rstrip("]").strip()
    section_text = sezioni.get(key) or sezioni.get(key.upper())
    if not section_text or not isinstance(section_text, str):
        return False
    needle = " ".join(testo_esatto.split()).lower()
    if not needle.replace("**", "").strip():
        return False
    haystack = " ".join(section_text.split()).lower()
    return needle in haystack or needle.replace("**", "") in haystack.replace("**", "")


def verifica_citazione(sezione: str, testo_esatto: str, sezioni: dict[str, str]) -> bool:
    """True se `testo_esatto` compare (a meno della normalizzazione) nella
    sezione indicata; per i blocchi di documento anche a cavallo con la pagina
    successiva. Non cerca mai in sezioni diverse da quella citata."""
    if not isinstance(sezione, str) or not isinstance(testo_esatto, str):
        return False
    if not isinstance(sezioni, dict) or not sezioni:
        return False
    if _verifica_come_ai_check(sezione, testo_esatto, sezioni):
        return True
    ago = normalizza_testo(testo_esatto)
    if not ago:
        return False
    per_chiave: dict[str, str] = {}
    for k, v in sezioni.items():
        if isinstance(v, str) and v:
            per_chiave.setdefault(normalizza_sezione(k), v)
    chiave = normalizza_sezione(sezione)
    testo = per_chiave.get(chiave)
    if not testo:
        return False
    if _trova(ago, testo):
        return True
    successiva = _pagina_successiva(chiave)
    if successiva and per_chiave.get(successiva):
        return _trova(ago, testo + "\n" + per_chiave[successiva])
    return False
