"""Controllo anti-contatti e anonimizzazione dei testi liberi (modulo PURO).

Regola del modulo partenariati: prima dell'accettazione nessun contatto
diretto. Vale per TUTTI i testi liberi che altre aziende possono leggere
(profilo partner in WP4, call in WP5, bozze in WP10), anonimi o no.

`trova_rilievi(testo, ident, anonima=...)` restituisce i rilievi in ordine di
posizione, uno per coppia (tipo, estratto):
- sempre BLOCCANTI: `email` (anche offuscate: «nome [at] dominio», «(at)»,
  «chiocciola», «[dot]», «punto it», « . it»), `telefono` (italiani e
  internazionali, con separatori — spazio, trattino, barra, punto, trattino
  basso — o a cifre singole), `url` (con e senza schema, `www.`, domini nudi
  con un TLD noto, link brevi come «t.me/…», handle «@nome»), `piva_cf`
  (P.IVA di 11 cifre, anche con separatori se
  il checksum torna; CF di persona fisica), `iban` (checksum mod 97 o forma
  italiana), `dominio_bloccato` (`link_policy.is_blocked_link`);
- solo se `anonima` e `ident` è dato, in più: BLOCCANTI `ragione_sociale`
  (ragione sociale senza forma legale e denominazione del registro),
  `piva_cf` (P.IVA e CF dell'azienda anche spezzati da separatori) e
  `dominio_azienda` (dominio del sito); AVVISI non bloccanti `persona` (cognomi
  di `company_people` come parole singole: «Ferro» o «Costa» sono anche parole
  comuni, decide l'utente).

`anonimizza(testo, ident)` sostituisce con «[rimosso]» tutto ciò che
`trova_rilievi(..., anonima=True)` considera bloccante (con `ident=None` solo i
contatti): dopo, il testo non ha più rilievi bloccanti. Gli avvisi restano.

Falsi positivi esclusi per costruzione: anni e intervalli di anni («2021-2027»),
date, importi con separatore delle migliaia («€ 250.000», «1.250.000»),
percentuali, codici ATECO («62.01», «01.11.10»), norme («ISO 9001:2015»),
codici incollati a lettere (CUP, «H2020», «M4C2», «MI/012345»), fasce orarie
(«08-12 14-18»), numeri di protocollo, iscrizione o autorizzazione («prot.
0123456/2021», «Accreditamento n. 0456789»), abbreviazioni («p.es.», «ad.es.»,
«S.p.A.», «D.Lgs.»), nomi tecnici («ASP.NET»).

Linearità (i testi arrivano dagli utenti): nessuna regex ha gruppi ripetuti
(solo gruppi opzionali `?`) né `\\s*`; i quantificatori illimitati o partono
solo a inizio parola (lookbehind negativo) o chiudono il pattern; le forme di
date, importi e ATECO si riconoscono in Python sui singoli token numerici.
I confronti con ragione sociale e cognomi avvengono su un testo normalizzato
(minuscole, senza accenti, punteggiatura → spazio) con `str.find`.

Gli estratti e i testi rimossi contengono dati identificativi: MAI nei log.
"""

import re
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

from app.services.codice_fiscale import is_valid_cf
from app.services.link_policy import is_blocked_link
from app.services.openapi_mapping import validate_partita_iva

TipoRilievo = Literal[
    "email",
    "telefono",
    "url",
    "piva_cf",
    "iban",
    "dominio_bloccato",
    "ragione_sociale",
    "dominio_azienda",
    "persona",
]

SOSTITUTO = "[rimosso]"
MAX_ESTRATTO = 80

# Per i messaggi del servizio («Il campo X contiene …»): nominano il tipo di
# rilievo senza ripetere il dato.
ETICHETTE_RILIEVO: dict[str, str] = {
    "email": "un indirizzo email",
    "telefono": "un numero di telefono",
    "url": "un indirizzo web",
    "piva_cf": "una partita IVA o un codice fiscale",
    "iban": "un IBAN",
    "dominio_bloccato": "un sito non ammesso",
    "ragione_sociale": "il nome dell'azienda",
    "dominio_azienda": "il sito dell'azienda",
    "persona": "il cognome di una persona dell'azienda",
}


@dataclass(frozen=True)
class Rilievo:
    tipo: TipoRilievo
    estratto: str
    bloccante: bool


@dataclass(frozen=True)
class Identificativi:
    """Ciò che identifica un'azienda in un testo anonimo. `ragione_sociale`,
    `denominazione` e `cognomi` sono già NORMALIZZATI (minuscole, senza
    accenti, parole separate da uno spazio; ragione sociale e denominazione
    senza forma legale); P.IVA e CF solo alfanumerici maiuscoli; domini senza
    schema né `www.`."""

    ragione_sociale: str | None = None
    denominazione: str | None = None
    partite_iva: tuple[str, ...] = ()
    codici_fiscali: tuple[str, ...] = ()
    domini: tuple[str, ...] = ()
    cognomi: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Occorrenza:
    tipo: str
    inizio: int
    fine: int
    bloccante: bool


# Chi vince quando due rilievi si sovrappongono (numero più basso).
_PRIORITA = {
    "iban": 0,
    "email": 1,
    "dominio_bloccato": 2,
    "dominio_azienda": 2,
    "url": 2,
    "piva_cf": 3,
    "telefono": 4,
    "ragione_sociale": 5,
    "persona": 6,
}


# ------------------------------------------------------------ normalizzazione


def _normalizza_con_mappa(testo: str) -> tuple[str, list[int]]:
    """Testo per i confronti di nomi: minuscole, accenti tolti (NFKD), ogni
    carattere non alfanumerico → un solo spazio. `mappa[j]` è l'indice nel
    testo originale del carattere normalizzato `j`."""
    out: list[str] = []
    mappa: list[int] = []
    spazio = True  # niente spazio iniziale
    for i, ch in enumerate(testo):
        if ch.isascii():
            pezzi: Iterable[str] = (ch.lower(),)
        else:
            pezzi = (
                c.lower()
                for c in unicodedata.normalize("NFKD", ch)
                if not unicodedata.combining(c)
            )
        for pezzo in pezzi:
            for c in pezzo:
                if c.isalnum():
                    out.append(c)
                    mappa.append(i)
                    spazio = False
                elif not spazio:
                    out.append(" ")
                    mappa.append(i)
                    spazio = True
    if out and out[-1] == " ":
        out.pop()
        mappa.pop()
    return "".join(out), mappa


def normalizza(testo: Any) -> str:
    """Forma normalizzata usata per ragione sociale, denominazione e cognomi."""
    if not isinstance(testo, str):
        return ""
    return _normalizza_con_mappa(testo)[0]


# Forme legali (già normalizzate), tolte dalla fine della ragione sociale finché
# ce n'è una; le più lunghe prima. «& C.» diventa «e c» prima di normalizzare.
_FORME_LEGALI_FINALI: tuple[tuple[str, ...], ...] = tuple(
    sorted(
        (
            tuple(forma.split())
            for forma in (
                "societa a responsabilita limitata semplificata",
                "societa a responsabilita limitata",
                "societa per azioni",
                "societa in accomandita per azioni",
                "societa in accomandita semplice",
                "societa in nome collettivo",
                "societa consortile a responsabilita limitata",
                "societa consortile per azioni",
                "societa consortile",
                "societa cooperativa sociale",
                "societa cooperativa agricola",
                "societa cooperativa",
                "cooperativa sociale",
                "societa semplice agricola",
                "societa semplice",
                "societa agricola",
                "societa benefit",
                "impresa individuale",
                "ditta individuale",
                "impresa sociale",
                "a responsabilita limitata",
                "in liquidazione",
                "in forma abbreviata",
                "unipersonale",
                "soc coop",
                "coop soc",
                "soc cons",
                "s r l s",
                "srls",
                "s r l",
                "srl",
                "s p a",
                "spa",
                "s a p a",
                "sapa",
                "s a s",
                "sas",
                "s n c",
                "snc",
                "s c a r l",
                "scarl",
                "s c r l",
                "scrl",
                "s c p a",
                "scpa",
                "s c",
                "s s d",
                "ssd",
                "a s d",
                "asd",
                "s s",
                "a r l",
                "arl",
                "s b",
                "sb",
                "onlus",
                "ets",
                "aps",
                "odv",
                "gmbh",
                "ltd",
                "llc",
                "inc",
                "plc",
                "sarl",
                "s a",
                "sa",
                "bv",
                "nv",
                "ag",
                "e c",
            )
        ),
        key=len,
        reverse=True,
    )
)
_FORME_LEGALI_INIZIALI: tuple[tuple[str, ...], ...] = tuple(
    tuple(forma.split())
    for forma in (
        "societa cooperativa sociale",
        "societa cooperativa agricola",
        "societa cooperativa",
        "cooperativa sociale",
        "societa semplice agricola",
        "societa agricola",
        "azienda agricola",
        "impresa individuale",
        "ditta individuale",
        "soc coop",
        "coop soc",
        "ditta",
    )
)
_E_C = re.compile(r"&[ \t]?c\b", re.IGNORECASE)
_MIN_NOME = 3  # caratteri alfanumerici minimi di un nome da cercare


def nome_senza_forma_legale(nome: Any) -> str | None:
    """Ragione sociale normalizzata senza forma legale («Rossi & C. S.n.c.» →
    «rossi»); None se ciò che resta è troppo corto o solo numerico."""
    if not isinstance(nome, str):
        return None
    parole = normalizza(_E_C.sub(" e c ", nome)).split()
    cambiato = True
    while cambiato and parole:
        cambiato = False
        for forma in _FORME_LEGALI_FINALI:
            if len(parole) > len(forma) and tuple(parole[-len(forma):]) == forma:
                del parole[-len(forma):]
                cambiato = True
                break
    for forma in _FORME_LEGALI_INIZIALI:
        if len(parole) > len(forma) and tuple(parole[: len(forma)]) == forma:
            del parole[: len(forma)]
            break
    risultato = " ".join(parole)
    compatto = risultato.replace(" ", "")
    if len(compatto) < _MIN_NOME or compatto.isdigit():
        return None
    return risultato


# ------------------------------------------------------------ identificativi


def _campo(riga: Any, *percorso: str) -> Any:
    corrente = riga
    for chiave in percorso:
        if not isinstance(corrente, dict):
            return None
        corrente = corrente.get(chiave)
    return corrente


def _solo_piva(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    testo = re.sub(r"[^0-9A-Za-z]", "", valore).upper()
    if testo.startswith("IT") and len(testo) == 13:
        testo = testo[2:]
    return testo if len(testo) == 11 and testo.isdigit() else None


def _solo_cf(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    testo = re.sub(r"[^0-9A-Za-z]", "", valore).upper()
    if (len(testo) == 11 and testo.isdigit()) or len(testo) == 16:
        return testo
    return None


_SCHEMA = re.compile(r"^[a-z][a-z0-9+.\-]*:/{0,3}", re.IGNORECASE)


def dominio_da_url(valore: Any) -> str | None:
    """Host minuscolo senza schema, `www.`, porta e percorso («https://www.
    Acme.it/chi-siamo» → «acme.it»). None se non sembra un dominio."""
    if not isinstance(valore, str):
        return None
    testo = _SCHEMA.sub("", valore.strip().replace("\\", "/")).lower()
    for separatore in "/?#:":
        testo = testo.split(separatore, 1)[0]
    testo = testo.strip(".")
    testo = re.sub(r"^www[0-9]{0,3}\.", "", testo)
    etichette = testo.split(".")
    if len(etichette) < 2 or not all(etichette):
        return None
    if not all(re.fullmatch(r"[a-z0-9\-]{1,63}", e) for e in etichette):
        return None
    if not etichette[-1].isalpha():
        return None
    return testo


def _unici(valori: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for v in valori if v))


def identificativi_azienda(
    company: dict | None, company_data_row: dict | None, people: Iterable[dict] | None
) -> Identificativi:
    """Identificativi dell'azienda per i controlli dei testi anonimi.

    - `company`: riga di `company_profiles` (ragione_sociale, partita_iva,
      codice_fiscale, sito_web);
    - `company_data_row`: riga di `company_data` (denominazione, piva_fetched,
      raw IT-full per sito, P.IVA e CF del registro), può mancare;
    - `people`: righe di `company_people` (conta solo `cognome`)."""
    company = company or {}
    dati = company_data_row or {}
    raw = dati.get("raw") if isinstance(dati.get("raw"), dict) else {}
    denominazione = dati.get("denominazione") or _campo(raw, "companyDetails", "companyName")

    cognomi = []
    for persona in people or ():
        if isinstance(persona, dict):
            cognome = normalizza(persona.get("cognome"))
            if len(cognome.replace(" ", "")) >= _MIN_NOME:
                cognomi.append(cognome)

    return Identificativi(
        ragione_sociale=nome_senza_forma_legale(company.get("ragione_sociale")),
        denominazione=nome_senza_forma_legale(denominazione),
        partite_iva=_unici(
            _solo_piva(v)
            for v in (
                company.get("partita_iva"),
                dati.get("piva_fetched"),
                _campo(raw, "companyDetails", "vatCode"),
            )
        ),
        codici_fiscali=_unici(
            _solo_cf(v)
            for v in (company.get("codice_fiscale"), _campo(raw, "companyDetails", "taxCode"))
        ),
        domini=_unici(
            dominio_da_url(v)
            for v in (company.get("sito_web"), _campo(raw, "webAndSocial", "website"))
        ),
        cognomi=_unici(cognomi),
    )


# ------------------------------------------------- offuscamenti di email/url

# Un solo passaggio: ogni forma offuscata diventa «@» o «.» in un testo
# «canonico», con la mappa verso l'originale per gli estratti. Le «at» e
# «chiocciola» come parole sciolte (e «(AT)» maiuscolo: la sigla dell'Austria)
# danno una chiocciola DEBOLE: vale solo se segue un dominio con TLD. Così
# pure «@» staccata da spazi («competenze @ livello europeo» non è un'email,
# «mario @ acme.it» sì). «nome @dominio» (spazio solo prima) resta com'è: è la
# forma di un handle social.
_OFFUSCAMENTI = re.compile(
    r"(?P<at>[ \t]?[\[({<][ \t]?(?:at|chiocciola|@)[ \t]?[\])}>][ \t]?)"
    r"|(?P<dot>[ \t]?[\[({<][ \t]?(?:dot|punto|\.)[ \t]?[\])}>][ \t]?"
    r"|(?<=\w)[ \t]dot[ \t](?=\w)"
    r"|(?<=\w)[ \t](?:punto|\.)[ \t](?=(?:it|com|eu|org|net|info)\b))"
    r"|(?P<at_debole>(?<=\w)[ \t](?:at|chiocciola|@)[ \t](?=\w)|(?<=\w)@[ \t](?=\w))",
    re.IGNORECASE,
)


@dataclass
class _Canonico:
    testo: str
    inizio: list[int] | None  # None = identico all'originale
    fine: list[int] | None
    deboli: set[int]

    def originale(self, a: int, b: int) -> tuple[int, int]:
        if self.inizio is None or self.fine is None:
            return a, b
        return self.inizio[a], self.fine[b - 1]


def _canonico(testo: str) -> _Canonico:
    corrispondenze = list(_OFFUSCAMENTI.finditer(testo))
    if not corrispondenze:
        return _Canonico(testo, None, None, set())
    parti: list[str] = []
    inizio: list[int] = []
    fine: list[int] = []
    deboli: set[int] = set()
    pos = 0
    for m in corrispondenze:
        a, b = m.span()
        parti.append(testo[pos:a])
        inizio.extend(range(pos, a))
        fine.extend(range(pos + 1, a + 1))
        if m.lastgroup == "dot":
            parti.append(".")
        else:
            interno = m.group().strip(" \t[](){}<>")
            if m.lastgroup == "at_debole" or interno == "AT":
                deboli.add(len(inizio))
            parti.append("@")
        inizio.append(a)
        fine.append(b)
        pos = b
    parti.append(testo[pos:])
    inizio.extend(range(pos, len(testo)))
    fine.extend(range(pos + 1, len(testo) + 1))
    return _Canonico("".join(parti), inizio, fine, deboli)


# ------------------------------------------------------------------- email

_EMAIL = re.compile(r"(?<![\w.%+\-])[\w.%+\-]+@[\w\-][\w.\-]*")
# Handle social («@rossimeccanica»): contatto anche senza dominio. Si cerca
# sul testo ORIGINALE, così «at» sciolto non ne crea.
_HANDLE = re.compile(r"(?<![\w.@])@[A-Za-z_][A-Za-z0-9_.]{1,29}")


def _tld_email(dominio: str) -> bool:
    etichette = dominio.split(".")
    return (
        len(etichette) >= 2
        and all(etichette)
        and etichette[-1].isalpha()
        and 2 <= len(etichette[-1]) <= 24
    )


def _email(c: _Canonico) -> Iterator[tuple[str, int, int]]:
    testo = c.testo
    for m in _EMAIL.finditer(testo):
        chiocciola = testo.index("@", m.start())
        dominio = testo[chiocciola + 1 : m.end()].rstrip(".-")
        # senza TLD vale solo una «@» vera o offuscata tra parentesi («nome
        # [at] dominio»), mai una debole, e il dominio deve avere lettere
        if not _tld_email(dominio) and (
            chiocciola in c.deboli or not any(ch.isalpha() for ch in dominio)
        ):
            continue
        yield "email", m.start(), chiocciola + 1 + len(dominio)


# --------------------------------------------------------------------- url

# TLD riconosciuti nei domini SENZA schema né «www.». Esclusi quelli che sono
# anche parole comuni in italiano o in inglese (in, is, me, to, do, la, ma, mi,
# tu, ne, am, il, li, al), per non scambiare «fine.il» per un sito.
_TLD = frozenset(
    """
    com org net info biz edu gov int mil eu io ai app dev tech online site website web
    store shop cloud digital agency studio pro xyz eco energy solutions services
    consulting group company email news blog academy education global world network
    systems software science design media space social community partners ventures
    capital bio farm wine travel events museum swiss berlin paris london cat
    it sm va de fr es pt uk ie nl be lu ch at dk se no fi ee lv lt pl cz sk hu si hr
    ro bg gr cy mt rs ba mk tr ua us ca au nz jp cn kr br ar mx za ru ae sg hk tw co
    """.split()
)
# Nomi tecnici e abbreviazioni con la forma di un dominio.
_NON_DOMINI = frozenset({"asp.net", "vb.net", "ado.net", "ad.es"})

_CODA_URL = "[^\\s<>\"'«»]"
_URL_SCHEMA = re.compile(
    r"(?<![\w])(?:https?|ftp):/{0,3}[^\s<>\"'«»/]" + _CODA_URL + "*", re.IGNORECASE
)
_URL_WWW = re.compile(r"(?<![\w.\-])www[0-9]{0,3}\." + _CODA_URL + "+", re.IGNORECASE)
_DOMINIO_NUDO = re.compile(
    r"(?<![\w@.\-/])[A-Za-z0-9][A-Za-z0-9\-]{0,62}\.[A-Za-z0-9][A-Za-z0-9.\-]*"
)
_RESTO_URL = re.compile(_CODA_URL + "*")
# Link brevi di messaggistica e accorciatori, con TLD fuori dall'elenco o
# etichette di una lettera: sono contatti diretti.
_LINK_BREVI = re.compile(
    r"(?<![\w.\-])(?:t\.me|wa\.me|m\.me|bit\.ly|goo\.gl|lnkd\.in)/[^\s<>\"'«»/]"
    + _CODA_URL + "*",
    re.IGNORECASE,
)
_PUNTEGGIATURA_FINALE = ".,;:!?)]}'\"»"


def _senza_punteggiatura_finale(testo: str, a: int, b: int) -> int:
    while b > a and testo[b - 1] in _PUNTEGGIATURA_FINALE:
        b -= 1
    return b


def _host_nudo(candidato: str) -> str | None:
    """Il dominio più lungo, dall'inizio del candidato, che finisce con un TLD
    noto (maiuscolo o minuscolo, non misto: «progetti.Il» è un refuso)."""
    etichette = candidato.split(".")
    for fine in range(len(etichette) - 1, 0, -1):
        tld = etichette[fine]
        if tld.lower() not in _TLD or not (tld.islower() or tld.isupper()):
            continue
        host = etichette[: fine + 1]
        if not all(host) or len(host[-2]) < 2:
            continue
        dominio = ".".join(host)
        if dominio.lower() in _NON_DOMINI:
            return None
        return dominio
    return None


def _url(c: _Canonico) -> Iterator[tuple[str, int, int, str]]:
    """(tipo, inizio, fine, host) per URL con schema, «www.» e domini nudi."""
    testo = c.testo
    for regex in (_URL_SCHEMA, _URL_WWW, _LINK_BREVI):
        for m in regex.finditer(testo):
            fine = _senza_punteggiatura_finale(testo, m.start(), m.end())
            url = testo[m.start() : fine]
            tipo = "dominio_bloccato" if is_blocked_link(url) else "url"
            yield tipo, m.start(), fine, dominio_da_url(url) or ""
    coperto_fino = -1
    for m in _DOMINIO_NUDO.finditer(testo):
        if m.start() < coperto_fino:
            continue
        candidato = testo[m.start() : m.end()].rstrip(".-")
        host = _host_nudo(candidato)
        if host is None:
            continue
        fine = m.start() + len(host)
        if len(host) == len(candidato) and m.end() < len(testo) and testo[m.end()] in "/?#:":
            resto = _RESTO_URL.match(testo, m.end())
            fine = _senza_punteggiatura_finale(testo, m.start(), resto.end() if resto else fine)
        coperto_fino = fine
        tipo = "dominio_bloccato" if is_blocked_link(host) else "url"
        yield tipo, m.start(), fine, host.lower()


# ---------------------------------------------------------------- P.IVA e CF

_PIVA_NUDA = re.compile(r"(?<![A-Za-z0-9])(?:IT)?[0-9]{11}(?![A-Za-z0-9])", re.IGNORECASE)
_OMOCODIA = "[0-9LMNPQRSTUV]"
_CF_PERSONA = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{6}" + _OMOCODIA + "{2}[A-Z]" + _OMOCODIA + "{2}[A-Z]"
    + _OMOCODIA + "{3}[A-Z](?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _piva_cf(c: _Canonico) -> Iterator[tuple[str, int, int]]:
    testo = c.testo
    for m in _PIVA_NUDA.finditer(testo):
        cifre = m.group()[-11:]
        # 11 cifre senza checksum che hanno la forma di un telefono (es. un
        # fisso di 11 cifre) le etichetta il riconoscitore dei telefoni.
        if validate_partita_iva(cifre) or not _nazionale_italiano(cifre):
            yield "piva_cf", m.start(), m.end()
    for m in _CF_PERSONA.finditer(testo):
        cf = m.group().upper()
        if any(ch.isdigit() for ch in cf) or is_valid_cf(cf):
            yield "piva_cf", m.start(), m.end()


# -------------------------------------------------------------------- IBAN

_IBAN_INIZIO = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}[0-9]{2}")
_IBAN_LUNGHEZZE = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22,
    "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GI": 23, "GR": 27,
    "HR": 21, "HU": 28, "IE": 22, "IS": 26, "IT": 27, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "VA": 22,
}
_IBAN_ITALIANO = re.compile(r"(?:IT|SM)[0-9]{2}[A-Z][0-9]{10}[0-9A-Z]{12}")
# 34 alfanumerici al massimo più gli spazi singoli tra i gruppi di 4.
_IBAN_CORPO = re.compile(r"[A-Za-z0-9 ]{0,45}")


def _iban_valido(iban: str) -> bool:
    riordinato = iban[4:] + iban[:4]
    try:
        numero = int("".join(str(int(ch, 36)) for ch in riordinato))
    except ValueError:
        return False
    return numero % 97 == 1


def _iban(c: _Canonico) -> Iterator[tuple[str, int, int]]:
    """IBAN con o senza spazi: lunghezza del paese e checksum mod 97 (o la
    forma italiana, anche con un refuso nel checksum)."""
    testo = c.testo
    for m in _IBAN_INIZIO.finditer(testo):
        lunghezza = _IBAN_LUNGHEZZE.get(m.group()[:2].upper())
        if lunghezza is None:
            continue
        corpo = _IBAN_CORPO.match(testo, m.start()).group().split("  ", 1)[0]
        compatto = corpo.replace(" ", "")
        if len(compatto) < lunghezza:
            continue
        iban = compatto[:lunghezza].upper()
        if sum(ch.isdigit() for ch in iban[4:]) < 8:
            continue
        if not (_iban_valido(iban) or _IBAN_ITALIANO.fullmatch(iban)):
            continue
        contati = 0
        fine = m.start()
        for k, ch in enumerate(corpo):
            if ch != " ":
                contati += 1
                if contati == lunghezza:
                    fine = m.start() + k + 1
                    break
        if fine < len(testo) and testo[fine].isalnum():
            continue  # più lungo dell'IBAN del paese: non è un IBAN
        yield "iban", m.start(), fine


# ---------------------------------------------------------------- telefoni

# Token numerici: cifre con i separatori interni tipici dei telefoni. Le forme
# che NON sono telefoni (anni, date, importi, ATECO) si riconoscono in Python.
_TOKEN_NUMERICO = re.compile(r"[0-9()+./\-]+")
_SEPARATORI_DATA = re.compile(r"[./\-]")
# Tra due token della stessa catena: uno spazio, un trattino, una barra, un
# punto o un trattino basso («347_123_4567»).
_GIUNTURA = re.compile(r"[  ]?[\-/._]?[  ]?")
_PREFISSI_SPECIALI = frozenset({"800", "803", "840", "848", "892", "895", "899", "199"})
_SIMBOLI_DOPO = ("%", "€", "$", "£", "k€")
_SIMBOLI_PRIMA = ("€", "$", "£")
_PAROLE_IMPORTO = frozenset(
    {"euro", "eur", "keur", "k", "mln", "mld", "milioni", "milione", "mila", "migliaia"}
)
_MAX_TOKEN_FINESTRA = 6
_MAX_CIFRE_TELEFONO = 15
# Cifre singole di fila («3 4 7 1 2 3 4 5 6 7»): un numero scritto per non
# farsi riconoscere. Sotto questa soglia sono elenchi.
_MIN_CIFRE_SINGOLE = 6
# Fascia oraria «08-12», «8.30-12.30»: mai un telefono (e due fasce di fila
# non si uniscono in un numero).
_FASCIA_ORARIA = re.compile(r"([0-9]{1,2})(?:[.:][0-9]{2})?-([0-9]{1,2})(?:[.:][0-9]{2})?")
# Parole che, subito prima di un numero, lo rendono un codice di registro
# (protocollo, iscrizione, autorizzazione…) e non un recapito; quelle di
# recapito vincono. Radici (prefisso) e parole esatte, in minuscolo.
_RADICI_REGISTRO = (
    "protocoll", "iscrizion", "registrazion", "repertori", "autorizzazion", "accreditament",
    "certificazion", "attestazion", "decret", "matricol", "brevett",
)
_PAROLE_REGISTRO = frozenset(
    {"prot", "rep", "reg", "registro", "rea", "albo", "cig", "cup", "soa", "aua", "aia",
     "cod", "codice", "lotto", "pratica", "fattura", "ordine", "commessa", "contratto",
     "licenza", "delibera", "determina", "certificato", "accreditato", "accreditata",
     "iscritto", "iscritta"}
)
_RADICI_RECAPITO = (
    "tel", "cell", "fax", "chiam", "whatsapp", "contatt", "recapit", "sms", "scriv",
)
_RIEMPITIVI = frozenset({"n", "nr", "num", "numero", "no", "il", "al", "del", "della", "di"})
_PAROLA = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class _Token:
    inizio: int
    fine: int
    testo: str
    cifre: str
    telefonabile: bool


def _anno(parte: str) -> bool:
    return len(parte) == 4 and 1900 <= int(parte) <= 2099


def _e_data(parti: list[str]) -> bool:
    lun = [len(p) for p in parti]
    if len(parti) == 3:
        if lun[0] <= 2 and lun[1] <= 2 and (lun[2] == 2 or _anno(parti[2])):
            return True  # gg/mm/aa(aa)
        return _anno(parti[0]) and lun[1] <= 2 and lun[2] <= 2  # aaaa-mm-gg
    if len(parti) == 2:
        if lun[0] <= 2 and _anno(parti[1]):
            return True  # mm/aaaa
        return _anno(parti[0]) and (lun[1] <= 2 or _anno(parti[1]))  # aaaa-mm, 2021-2027
    return False


def _non_telefono(testo: str, cifre: str) -> bool:
    """Token che per forma NON è (parte di) un telefono."""
    if len(cifre) > _MAX_CIFRE_TELEFONO:
        return True
    if testo.isdigit():
        return _anno(testo)
    fascia = _FASCIA_ORARIA.fullmatch(testo)
    if fascia and int(fascia.group(1)) <= 24 and int(fascia.group(2)) <= 24:
        return True
    parti = testo.split(".")
    if len(parti) >= 2 and all(p.isdigit() for p in parti):
        if parti[0][0] != "0" and len(parti[0]) <= 3 and all(len(p) == 3 for p in parti[1:]):
            return True  # importo con le migliaia: 250.000, 1.250.000
        if len(parti) <= 3 and len(parti[0]) == 2 and all(len(p) <= 2 for p in parti[1:]):
            return True  # ATECO: 62.01, 01.11.10
    parti = _SEPARATORI_DATA.split(testo)
    return 2 <= len(parti) <= 3 and all(p.isdigit() for p in parti) and _e_data(parti)


def _token_numerici(testo: str) -> list[_Token]:
    token: list[_Token] = []
    for m in _TOKEN_NUMERICO.finditer(testo):
        a, b = m.span()
        # punteggiatura ai bordi e parentesi spaiate («1234567)» chiude una
        # frase); i conteggi evitano di riscandire il token a ogni passo
        apre, chiude = testo.count("(", a, b), testo.count(")", a, b)
        while a < b:
            if testo[a] in "./-" or (testo[a] == "(" and not chiude):
                apre -= testo[a] == "("
                a += 1
            elif testo[b - 1] in "./-" or (testo[b - 1] == ")" and not apre):
                chiude -= testo[b - 1] == ")"
                b -= 1
            else:
                break
        grezzo = testo[a:b]
        cifre = "".join(ch for ch in grezzo if ch.isdigit())
        if not cifre:
            continue
        # incollato a lettere, anche con una barra o un trattino in mezzo:
        # codici (CUP, «H2020», «M4C2», «MI/012345»), mai un recapito
        incollato = (
            (a > 0 and testo[a - 1].isalpha())
            or (b < len(testo) and testo[b].isalpha())
            or (m.start() > 0 and testo[m.start()] in "/-" and testo[m.start() - 1].isalpha())
        )
        telefonabile = not incollato and not _non_telefono(grezzo, cifre)
        token.append(_Token(a, b, grezzo, cifre, telefonabile))
    return token


def _catene(testo: str, token: list[_Token]) -> Iterator[list[_Token]]:
    catena: list[_Token] = []
    for t in token:
        if not t.telefonabile:
            if catena:
                yield catena
            catena = []
            continue
        if catena:
            giuntura = testo[catena[-1].fine : t.inizio]
            if giuntura and _GIUNTURA.fullmatch(giuntura):
                catena.append(t)
                continue
            yield catena
        catena = [t]
    if catena:
        yield catena


def _nazionale_italiano(numero: str) -> bool:
    if len(numero) < 6:
        return False
    if numero[0] == "3":
        return 9 <= len(numero) <= 10  # cellulari
    if numero[0] == "0":
        return numero[1] != "0" and len(numero) <= 11  # fissi, prefisso compreso
    if numero[:3] in _PREFISSI_SPECIALI:
        return 9 <= len(numero) <= 10  # numeri verdi e speciali
    return False


def _importo_o_percentuale(testo: str, inizio: int, fine: int) -> bool:
    """Il numero è un importo o una percentuale: «€ 3000000», «250000 euro»,
    «+300000 %»."""
    dopo = testo[fine : fine + 12].lstrip("  ").lower()
    if dopo.startswith(_SIMBOLI_DOPO):
        return True
    parola = dopo.split(" ", 1)[0].rstrip(".,;:") if dopo else ""
    if parola in _PAROLE_IMPORTO:
        return True
    prima = testo[max(0, inizio - 8) : inizio].rstrip("  ").lower()
    return prima.endswith(_SIMBOLI_PRIMA) or prima.rsplit(" ", 1)[-1] in ("eur", "euro")


def _classifica_numero(testo: str, primo: _Token, ultimo: _Token, cifre: str) -> str | None:
    if len(cifre) < 6:
        return None
    piu = primo.testo.startswith(("+", "(+"))
    tipo = None
    if not piu and len(cifre) == 11 and validate_partita_iva(cifre):
        tipo = "piva_cf"
    elif piu or cifre.startswith("00"):
        nazionale = cifre if piu else cifre[2:]
        if nazionale.startswith("39"):
            tipo = "telefono" if _nazionale_italiano(nazionale[2:]) else None
        elif 8 <= len(nazionale) <= _MAX_CIFRE_TELEFONO:
            tipo = "telefono"
    elif _nazionale_italiano(cifre) or (
        cifre.startswith("39") and _nazionale_italiano(cifre[2:])
    ):
        tipo = "telefono"
    if tipo is None or _importo_o_percentuale(testo, primo.inizio, ultimo.fine):
        return None
    return tipo


def _numero_di_registro(testo: str, inizio: int) -> bool:
    """Il numero che parte da `inizio` segue una parola di registro
    («prot.», «Accreditamento … n.», «Albo … n.») nella stessa frase: è un
    codice, non un recapito. Guarda al più 60 caratteri (tempo costante)."""
    prima = testo[max(0, inizio - 60) : inizio]
    for separatore in ",;:()\n":
        prima = prima.rsplit(separatore, 1)[-1]
    parole = _PAROLA.findall(prima.lower())
    while parole and parole[-1] in _RIEMPITIVI:
        parole.pop()
    ultime = parole[-3:]
    if any(p.startswith(_RADICI_RECAPITO) for p in ultime):
        return False
    return any(p in _PAROLE_REGISTRO or p.startswith(_RADICI_REGISTRO) for p in ultime)


def _telefoni(c: _Canonico) -> Iterator[tuple[str, int, int]]:
    testo = c.testo
    for catena in _catene(testo, _token_numerici(testo)):
        i = 0
        while i < len(catena):
            primo = catena[i]
            if len(primo.cifre) < 2 and not primo.testo.startswith("+"):
                # cifre singole di fila: un numero spezzato cifra per cifra
                k = i
                while k < len(catena) and len(catena[k].cifre) == 1:
                    k += 1
                cifre = "".join(t.cifre for t in catena[i:k])
                tipo = (
                    _classifica_numero(testo, primo, catena[k - 1], cifre)
                    if _MIN_CIFRE_SINGOLE <= len(cifre) <= _MAX_CIFRE_TELEFONO
                    else None
                )
                if tipo == "telefono":
                    yield tipo, primo.inizio, catena[k - 1].fine
                # la corsa di cifre singole si consuma intera (tempo lineare):
                # nessun numero parte da una cifra singola al suo interno
                i = k
                continue
            cifre = ""
            migliore: tuple[int, str] | None = None
            for j in range(i, min(len(catena), i + _MAX_TOKEN_FINESTRA)):
                # gruppi di una sola cifra: liste di numeri, non telefoni
                if j > i and len(catena[j].cifre) < 2:
                    break
                cifre += catena[j].cifre
                if len(cifre) > _MAX_CIFRE_TELEFONO:
                    break
                tipo = _classifica_numero(testo, primo, catena[j], cifre)
                if tipo:
                    migliore = (j, tipo)
            if migliore is None:
                i += 1
                continue
            j, tipo = migliore
            if not primo.testo.startswith(("+", "(+")) and _numero_di_registro(
                testo, primo.inizio
            ):
                i = j + 1  # protocollo, iscrizione, autorizzazione: un codice
                continue
            yield tipo, primo.inizio, catena[j].fine
            i = j + 1


# ------------------------------------------------ identificativi (anonimi)


def _cerca_normalizzato(
    testo: str, norm: str, mappa: list[int], ago: str
) -> Iterator[tuple[int, int]]:
    """Occorrenze di `ago` (normalizzato) a confini di parola, come intervalli
    del testo originale."""
    if not ago or not norm:
        return
    pagliaio = " " + norm + " "
    cercato = " " + ago + " "
    pos = pagliaio.find(cercato)
    while pos != -1:
        # pagliaio[pos + 1] = norm[pos]
        yield mappa[pos], mappa[pos + len(ago) - 1] + 1
        pos = pagliaio.find(cercato, pos + len(cercato) - 1)


def _regex_con_separatori(valore: str) -> re.Pattern:
    """P.IVA o CF dell'azienda anche spezzati: «012 345 678 97», «RSS-MRA…»."""
    return re.compile(
        r"(?<![A-Za-z0-9])" + "[ .\\-/]?".join(re.escape(ch) for ch in valore)
        + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _identificativi(testo: str, ident: Identificativi) -> Iterator[_Occorrenza]:
    norm, mappa = _normalizza_con_mappa(testo)
    for nome in _unici((ident.ragione_sociale, ident.denominazione)):
        for a, b in _cerca_normalizzato(testo, norm, mappa, nome):
            yield _Occorrenza("ragione_sociale", a, b, True)
    for cognome in ident.cognomi:
        for a, b in _cerca_normalizzato(testo, norm, mappa, cognome):
            yield _Occorrenza("persona", a, b, False)
    for valore in _unici((*ident.partite_iva, *ident.codici_fiscali)):
        for m in _regex_con_separatori(valore).finditer(testo):
            yield _Occorrenza("piva_cf", m.start(), m.end(), True)
    for dominio in ident.domini:
        regex = re.compile(r"(?<![\w.\-])" + re.escape(dominio) + r"(?![\w\-])", re.IGNORECASE)
        for m in regex.finditer(testo):
            yield _Occorrenza("dominio_azienda", m.start(), m.end(), True)


# --------------------------------------------------------------- motore


def _del_dominio(host: str, domini: tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in domini)


def _risolvi(occorrenze: list[_Occorrenza], lunghezza: int) -> list[_Occorrenza]:
    """Tiene, tra occorrenze sovrapposte, quella di priorità più alta (poi la
    prima e la più lunga). Risultato in ordine di posizione. La copertura è
    una maschera di byte: controlli e marcature in C, tempo lineare."""
    ordinate = sorted(
        occorrenze, key=lambda o: (_PRIORITA[o.tipo], o.inizio, o.inizio - o.fine)
    )
    coperto = bytearray(lunghezza)
    tenute: list[_Occorrenza] = []
    for o in ordinate:
        if o.fine <= o.inizio or coperto.find(1, o.inizio, o.fine) != -1:
            continue
        coperto[o.inizio : o.fine] = b"\x01" * (o.fine - o.inizio)
        tenute.append(o)
    return sorted(tenute, key=lambda o: o.inizio)


def _trova(testo: str, ident: Identificativi | None) -> list[_Occorrenza]:
    """Tutte le occorrenze; `ident` non None = controlli dei testi anonimi."""
    c = _canonico(testo)
    trovate: list[_Occorrenza] = []

    def aggiungi(tipo: str, a: int, b: int) -> None:
        inizio, fine = c.originale(a, b)
        trovate.append(_Occorrenza(tipo, inizio, fine, True))

    for tipo, a, b in _iban(c):
        aggiungi(tipo, a, b)
    for tipo, a, b in _email(c):
        aggiungi(tipo, a, b)
    for tipo, a, b, host in _url(c):
        if ident is not None and tipo == "url" and _del_dominio(host, ident.domini):
            tipo = "dominio_azienda"
        aggiungi(tipo, a, b)
    for m in _HANDLE.finditer(testo):
        fine = _senza_punteggiatura_finale(testo, m.start(), m.end())
        trovate.append(_Occorrenza("url", m.start(), fine, True))
    for tipo, a, b in _piva_cf(c):
        aggiungi(tipo, a, b)
    for tipo, a, b in _telefoni(c):
        aggiungi(tipo, a, b)
    if ident is not None:
        trovate.extend(_identificativi(testo, ident))
    return _risolvi(trovate, len(testo))


def _estratto(testo: str, o: _Occorrenza) -> str:
    estratto = " ".join(testo[o.inizio : o.fine].split())
    if len(estratto) > MAX_ESTRATTO:
        estratto = estratto[: MAX_ESTRATTO - 1] + "…"
    return estratto


def trova_rilievi(
    testo: str | None, ident: Identificativi | None, *, anonima: bool
) -> list[Rilievo]:
    """Rilievi del testo in ordine di posizione, senza doppioni (tipo,
    estratto). `ident` conta solo se `anonima`: un profilo nominativo può
    citare il proprio nome, mai i contatti."""
    if not isinstance(testo, str) or not testo.strip():
        return []
    rilievi: list[Rilievo] = []
    visti: set[tuple[str, str]] = set()
    for o in _trova(testo, ident if anonima else None):
        estratto = _estratto(testo, o)
        chiave = (o.tipo, estratto.casefold())
        if chiave in visti:
            continue
        visti.add(chiave)
        rilievi.append(Rilievo(o.tipo, estratto, o.bloccante))  # type: ignore[arg-type]
    return rilievi


def ha_bloccanti(rilievi: Iterable[Rilievo]) -> bool:
    return any(r.bloccante for r in rilievi)


_PASSATE_ANONIMIZZA = 3


def anonimizza(testo: str | None, ident: Identificativi | None) -> tuple[str, list[str]]:
    """Sostituisce con «[rimosso]» ogni rilievo bloccante (identificativi
    dell'azienda se `ident` è dato, contatti sempre). Restituisce il testo e
    i brani rimossi, senza doppioni, nell'ordine in cui compaiono: sono dati
    identificativi, da mostrare all'utente e MAI da loggare. Gli avvisi
    (cognomi) restano: sono ambigui e li decide l'utente."""
    if not isinstance(testo, str) or not testo:
        return testo or "", []
    rimossi: list[str] = []
    for _ in range(_PASSATE_ANONIMIZZA):
        bloccanti = [o for o in _trova(testo, ident) if o.bloccante]
        if not bloccanti:
            break
        parti: list[str] = []
        pos = 0
        for o in bloccanti:
            parti.append(testo[pos : o.inizio])
            parti.append(SOSTITUTO)
            rimossi.append(testo[o.inizio : o.fine])
            pos = o.fine
        parti.append(testo[pos:])
        testo = "".join(parti)
    return testo, list(dict.fromkeys(rimossi))
