"""Pre-classificatore deterministico delle regole di partenariato (WP3, puro).

Cerca nelle sezioni del bando (META, S1…, D1-p3…) le formule del §1.4 della
spec, in italiano e nell'inglese dei bandi UE, divise in quattro categorie:
- `ammette`: «in forma singola o associata», «progetti congiunti», ATS/ATI/RTI,
  contratto di rete, consorzi, «affiliated entities»…
- `richiede`: «almeno N soggetti», «in collaborazione con organismi di
  ricerca», «partenariato composto da», capofila, «consortium»…
- `vincola`: quote minime o massime, «nessun partner può», imprese
  indipendenti o non collegate, «un solo partenariato», paesi distinti;
- `nega`: «esclusivamente in forma singola», «non sono ammesse aggregazioni».

Serve SOLO a ordinare documenti e pagine, a dare priorità al batch e a fare da
guardia di costo (livello `nessuno` → niente LLM). NON decide mai la modalità.

Falsi positivi noti esclusi (verificati sul catalogo): ATS = Agenzia di Tutela
della Salute, consorzi di tutela e di bonifica, partenariato pubblico-privato,
«Accordo di partenariato» UE-Italia, aggregazione sociale, Comuni capofila di
distretti o ambiti, RTI/mandataria come gestore di uno strumento finanziario.
"""

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Literal

from app.services.citazioni import normalizza_caratteri

Categoria = Literal["ammette", "richiede", "vincola", "nega"]
Livello = Literal["forte", "debole", "nessuno"]

MAX_ESTRATTO = 160
MAX_SEGNALI = 300
# Finestra di contesto per le esclusioni che non si sovrappongono al match
# (es. «RTI … soggetto gestore del fondo»).
_CONTESTO = 100

_I = re.IGNORECASE
_NUM = r"(?:\d{1,2}|due|tre|quattro|cinque|sei|sette|otto|nove|dieci)"
_NUM_EN = r"(?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)"
_PERCENTUALE = r"\d{1,3}(?:[.,]\d+)?\s*(?:%|per\s*cento)"
# Seguito di «in forma singola» che la rende un'ammissione della forma
# associata: «… o associata», «… oppure in ATS», «… che in raggruppamento».
_SINGOLA_O_ASSOCIATA = (
    r"(?:o|oppure|ovvero|e/o|che)\s+(?:costituit[aeio]\s+|riunit[aeio]\s+)?"
    r"(?:in\s+forma\s+|in\s+|come\s+|tramite\s+|quale\s+)?"
    r"(?:associat|aggregat|congiunt|collettiv|raggruppat|partenariat|raggruppament|aggregazion"
    r"|associazion|ATS|ATI|RTI|ret[ei]\b|consorz)\w*"
)


@dataclass(frozen=True)
class _Pattern:
    nome: str
    # Radici minuscole di cui ALMENO UNA compare in ogni corrispondenza: se
    # nessuna è nel testo il pattern non si esegue (pre-filtro economico).
    inneschi: tuple[str, ...]
    categoria: Categoria
    forte: bool
    regex: re.Pattern
    # Esclusioni che devono SOVRAPPORSI al match (falso amico che lo contiene).
    escludi: tuple[re.Pattern, ...] = ()
    # Esclusioni cercate nel contesto attorno al match.
    escludi_contesto: tuple[re.Pattern, ...] = ()


def _p(
    nome, inneschi, categoria, forte, regex, *, flags=_I, escludi=(), contesto=()
) -> _Pattern:
    return _Pattern(
        nome=nome,
        inneschi=inneschi,
        categoria=categoria,
        forte=forte,
        regex=re.compile(regex, flags),
        escludi=tuple(re.compile(r, _I) for r in escludi),
        escludi_contesto=tuple(re.compile(r, _I) for r in contesto),
    )


# L'Accordo di partenariato tra Italia e Commissione (programmazione dei fondi
# UE) non è un partenariato tra beneficiari.
_ACCORDO_UE = (
    r"accordo\s+di\s+partenariato\s*(?:\(\s*a\.?\s*p\.?\s*\)\s*)?"
    r"(?:(?:per\s+(?:il\s+(?:periodo|ciclo)\s+(?:di\s+programmazione\s+)?)?)?"
    r"(?:19|20)\d\d|italia|ue\b|tra\s+l'italia|dell'italia|con\s+la\s+commissione)",
    r"(?:nell'ambito|previst[oa]|definit[oa]|in\s+coerenza\s+con|in\s+linea\s+con|ai\s+sensi)"
    r"\s+(?:del(?:l')?|dall'|con\s+l')\s*accordo\s+di\s+partenariato",
)
_PPP = (r"partenariat[oi]\s+pubblico[-\s]+privat\w*", r"\bPPP\b")
_COMUNE_CAPOFILA = (
    r"\bcomun[ei]\s+(?:\w+\s+){0,2}?capofila",
    r"capofila\s+(?:del|dell'|dei|degli|di)\s*(?:distrett|ambit|zon[ae]|unione|piano\s+di\s+zona"
    r"|sistema\s+bibliotecario|conferenza)",
)
_ATS_SALUTE = (
    r"A\.?T\.?S\.?\W{0,5}(?:\(\s*)?agenzi[ae]\s+(?:di\s+|per\s+la\s+)?tutela\s+della\s+salute",
    r"tutela\s+della\s+salute\W{0,5}(?:\(\s*)?A\.?T\.?S\.?",
    r"A\.?T\.?S\.?\s+(?:della\s+|di\s+|dell')?(?:citt[aà]\s+metropolitana|milano|insubria|brianza"
    r"|bergamo|brescia|montagna|pavia|val\s+padana|sardegna)",
)
_GESTORE = (r"\bgest(?:or[ei]|it[oa]|ione)\b", r"strument[oi]\s+finanziari")

PATTERN: tuple[_Pattern, ...] = (
    # ------------------------------------------------------------ ammette
    _p(
        "forma_singola_o_associata", ("forma",), "ammette", True,
        r"\bin\s+forma\s+(?:singola|individuale)\s+" + _SINGOLA_O_ASSOCIATA
        + r"|\bin\s+forma\s+(?:associata|aggregata|congiunta|collettiva|raggruppata)\b",
    ),
    _p(
        "progetti_congiunti", ("progett",), "ammette", True,
        r"\bprogett[oi]\s+(?:congiunt[oi]|collaborativ[oi]|in\s+partenariato"
        r"|in\s+collaborazione)\b",
    ),
    _p("co_proponenti", ("proponent",), "ammette", True, r"\bco[-\s]?proponent[ei]\b"),
    _p(
        "aggregazioni_di_imprese", ("aggregazion",), "ammette", True,
        r"\baggregazion[ei]\s+(?:temporane[ae]\s+)?(?:di|tra|fra)\s+(?:almeno\s+\S+\s+)?"
        r"(?:micro|piccol|medie|pmi\b|impres|soggett|enti\b|operator|aziend|partner|profession)",
    ),
    _p(
        "aggregazioni", ("aggregazion",), "ammette", False, r"\baggregazion[ei]\b",
        escludi=(
            r"aggregazion[ei]\s+(?:social|giovanil|sportiv|cultural|comunitari)\w*",
            r"(?:centr[oi]|luogh[io]|spaz[io]|moment[oi]|occasion[ei]|punt[oi]|attivit[aà])"
            r"\s+(?:di\s+)?aggregazion[ei]",
            r"aggregazion[ei]\s+(?:dei|di)\s+dati",
        ),
    ),
    _p(
        "ats_esteso", ("temporane",), "ammette", True,
        r"\bassociazion[ei]\s+temporane[ae]\s+di\s+scopo\b",
    ),
    _p(
        "ati_rti_esteso", ("temporane",), "ammette", True,
        r"\b(?:associazion[ei]\s+temporane[ae]\s+(?:di\s+|d')\s*impres[ae]"
        r"|raggruppament[oi]\s+temporane[oi])\b",
        contesto=_GESTORE,
    ),
    _p(
        "sigla_ats", ("ats", "a.ts", "at.s", "a.t.s"), "ammette", True,
        r"(?<![\w.])A\.?T\.?S\.?(?!\w)",
        flags=0,
        escludi=_ATS_SALUTE,
    ),
    _p(
        "sigla_ati_rti", ("ati", "a.ti", "at.i", "a.t.i", "rti", "r.ti", "rt.i", "r.t.i"),
        "ammette", True,
        r"(?<![\w.])(?:A\.?T\.?I|R\.?T\.?I)\.?(?!\w)",
        flags=0,
        contesto=_GESTORE,
    ),
    _p(
        "contratto_di_rete", ("rete", "reti"), "ammette", True,
        r"\b(?:contratt[oi]\s+di\s+rete|ret[ei]\s+(?:di\s+|d')\s*impres[ae]"
        r"|rete[-\s](?:contratto|soggetto))\b",
    ),
    _p(
        "consorzio_ue", ("consorzi",), "ammette", True,
        r"\bconsorzi[o]?\s+(?:transnazional|internazional|europe|multinazional)\w*",
    ),
    _p(
        "consorzi", ("consorzi",), "ammette", False, r"\bconsorzi[o]?\b",
        escludi=(
            r"consorzi[o]?\s+(?:di\s+)?tutela",
            r"consorzi[o]?\s+(?:di\s+)?bonifica",
            r"consorzi[o]?\s+(?:per\s+(?:lo\s+|l')?|di\s+)?sviluppo\s+industriale",
            r"consorzi[o]?\s+universitar\w*",
            r"consorzi[o]?\s+(?:di\s+)?garanzia\s+(?:collettiva\s+)?(?:dei\s+)?fidi",
        ),
    ),
    _p(
        "accordo_di_partenariato", ("partenariato",), "ammette", True,
        r"\baccord[oi]\s+di\s+partenariato\b",
        escludi=_ACCORDO_UE,
    ),
    _p(
        "in_partenariato", ("partenariat",), "ammette", True,
        r"\b(?:in|come|tramite)\s+partenariat[oi]\b"
        r"|\bpartenariat[oi]\s+(?:transnazional|internazional|europe)\w*",
        escludi=_PPP + _ACCORDO_UE,
    ),
    _p(
        "partenariato", ("partenariat",), "ammette", False, r"\bpartenariat[oi]\b",
        escludi=_PPP + _ACCORDO_UE,
    ),
    _p("partner", ("partner",), "ammette", False, r"\bpartners?\b"),
    _p(
        "affiliated_entities", ("affiliated", "associated"), "ammette", True,
        r"\baffiliated\s+entit(?:y|ies)\b|\bassociated\s+partners?\b",
    ),
    _p(
        "joint_application", ("joint", "collaborative", "beneficiary"), "ammette", True,
        r"\bjoint\s+(?:application|proposal|project|applicant)s?\b"
        r"|\bcollaborative\s+(?:project|research|action)s?\b|\bmulti[-\s]beneficiary\b",
    ),
    _p(
        "partnership", ("partnership",), "ammette", False, r"\bpartnerships?\b",
        escludi=(r"public[-\s]+private\s+partnerships?",),
    ),
    # ------------------------------------------------------------ richiede
    _p(
        "almeno_n_soggetti", ("almeno", "minimo", "meno di"), "richiede", True,
        r"\b(?:almeno|minimo(?:\s+di)?|non\s+meno\s+di|un\s+minimo\s+di)\s+" + _NUM
        # Niente «partecipanti» né «professionisti»: «almeno 8 partecipanti per
        # corso» è la regola tipica dei bandi di formazione, non un partenariato.
        + r"\s+(?:\S+\s+){0,2}?(?:soggett|partner|impres|pmi\b|enti\b|organism|aziend|beneficiar"
        r"|operator|universit|component|associazion|cooperativ|micro|piccol|medie)",
    ),
    _p(
        "composto_da_almeno", ("almeno", "minimo", "meno di"), "richiede", True,
        r"\b(?:compost|costituit|format)[oiae]\s+da\s+(?:almeno|un\s+minimo\s+di"
        r"|non\s+meno\s+di)\s+" + _NUM + r"\b",
    ),
    _p(
        "collaborazione_ricerca", ("collaborazion",), "richiede", True,
        r"\bcollaborazion[ei]\s+con\s+(?:(?:uno\s+o\s+più|almeno\s+un[oa]?|un[oa]?|gli|le|i|l')"
        r"\s*)?(?:organism[io]\s+di\s+ricerca|universit|centr[io]\s+di\s+ricerca"
        r"|enti\s+di\s+ricerca|istitut[io]\s+di\s+ricerca|laboratori\s+di\s+ricerca)",
    ),
    _p(
        "partenariato_composto", ("partenariat",), "richiede", True,
        r"\bpartenariat[oi]\s+(?:(?:deve\s+essere\s+|dev'essere\s+|è\s+|sono\s+)?"
        r"(?:compost|costituit|format)[oi]\s+da"
        r"|(?:di|tra|fra)\s+(?:almeno|imprese|soggetti|enti|pmi))",
    ),
    _p(
        "capofila_ruolo", ("capofila",), "richiede", True,
        r"\b(?:(?:impresa|soggetto|partner|ente|organismo)\s+capofila"
        r"|capofila\s+(?:del|della|dell'|di\s+un[ao']?)\s*(?:partenariat|raggruppament|aggregazion"
        r"|associazion|ATS|ATI|RTI|ret[ei]|progett|consorzi|compagine)"
        r"|in\s+qualità\s+di\s+capofila|ruolo\s+di\s+capofila)",
        escludi=_COMUNE_CAPOFILA
        + (r"ente\s+capofila\s+(?:del|dell'|dei|di)\s*(?:distrett|ambit|zon|piano)",),
    ),
    _p(
        "capofila", ("capofila",), "richiede", False, r"\bcapofila\b",
        escludi=_COMUNE_CAPOFILA,
    ),
    _p(
        "mandato_collettivo", ("mandato",), "richiede", True,
        r"\bmandato\s+collettivo(?:\s+speciale)?\s+(?:gratuito\s+(?:e\s+)?irrevocabile\s+)?"
        r"con\s+rappresentanza\b",
    ),
    _p(
        "mandatario", ("mandatar",), "richiede", False, r"\bmandatari[oaie]\b",
        contesto=_GESTORE,
    ),
    _p("consortium", ("consorti",), "richiede", True, r"\bconsorti(?:um|a)\b"),
    _p(
        "at_least_n_entities", ("least",), "richiede", True,
        r"\bat\s+least\s+" + _NUM_EN + r"\s+(?:\S+\s+){0,3}?(?:legal\s+entit|independent"
        r"|partner|organi[sz]ation|beneficiar|participant|applicant|countr|member\s+state"
        r"|entit)",
    ),
    _p(
        "coordinator", ("coordinator", "lead"), "richiede", False,
        r"\bcoordinator\b|\blead\s+(?:partner|applicant|beneficiary)\b",
    ),
    # ------------------------------------------------------------ vincola
    _p(
        "quota_percentuale", ("quot",), "vincola", True,
        r"\bquot[ae]\s+(?:\S+\s+){0,6}?(?:non\s+(?:può|possono|deve|devono)\s+(?:essere\s+)?"
        r"(?:inferior|superior)\w*|non\s+inferior\w*|non\s+superior\w*|(?:pari\s+ad?\s+)?almeno"
        r"|minim[ao]|massim[ao])\s+(?:\S+\s+){0,3}?" + _PERCENTUALE,
    ),
    _p(
        "nessun_partner", ("nessun",), "vincola", True,
        r"\bnessun[oa]?\s+(?:partner|soggetto|componente|partecipante|membro|beneficiario)\s+"
        r"(?:\S+\s+){0,3}?(?:può|potrà|deve)\b",
    ),
    _p(
        "ciascun_partner_percentuale", ("ciascun", "ogni"), "vincola", True,
        r"\b(?:ciascun[oa]?|ogni)\s+(?:partner|componente|partecipante|membro"
        r"|soggetto\s+(?:partner|partecipante|aderente))\s+(?:\S+\s+){0,8}?" + _PERCENTUALE,
    ),
    _p(
        "indipendenti", ("indipendent", "collegat", "associat", "controllat"), "vincola", True,
        r"\b(?:impres[ae]|soggett[io]|partner|aziend[ae]|enti)\s+(?:\S+\s+){0,2}?"
        r"(?:(?:tra|fra)\s+(?:loro|di\s+loro)\s+)?(?:indipendent[ei]\b"
        r"|non\s+(?:\S+\s+){0,2}?(?:collegat[ei]|associat[ei]|controllat[ei])\b)"
        r"|\bnon\s+(?:devono\s+)?(?:essere\s+)?(?:tra|fra)\s+(?:loro|di\s+loro)\s+"
        r"(?:collegat[ei]|associat[ei]|controllat[ei])\b",
    ),
    _p(
        "un_solo_partenariato", ("solo", "sola", "unic", "più"), "vincola", True,
        r"\b(?:un\s+solo|un\s+unico|una\s+sola|un'unica)\s+(?:partenariat|raggruppament"
        r"|aggregazion|associazion[ei]\s+temporane|ATS|ATI|RTI|consorzi|compagin)\w*"
        r"|\bpiù\s+(?:di\s+un[oa]?\s+)?(?:partenariat|raggruppament|aggregazion|ATS\b|ATI\b"
        r"|RTI\b)\w*",
    ),
    _p(
        "independent_entities", ("independent",), "vincola", True,
        r"\bindependent\s+(?:legal\s+)?entit(?:y|ies)\b"
        r"|\bindependent\s+(?:of|from)\s+each\s+other\b",
    ),
    _p(
        "paesi_distinti", ("different", "distinct", "divers", "distint"), "vincola", True,
        r"\b(?:different|distinct)\s+(?:eu\s+)?(?:member\s+states?|countr(?:y|ies)"
        r"|associated\s+countr(?:y|ies))\b"
        r"|\bpaesi\s+(?:membri\s+)?(?:divers|distint)\w*|\bstati\s+membri\s+(?:divers|distint)\w*",
    ),
    # ------------------------------------------------------------ nega
    _p(
        "solo_forma_singola", ("forma", "ammess"), "nega", True,
        r"\b(?:esclusivamente|solo|soltanto|unicamente)\s+in\s+forma\s+(?:singola|individuale)\b"
        r"|\bnon\s+(?:sono|è)\s+ammess[aeio]\s+(?:la\s+|le\s+|i\s+|gli\s+)?"
        r"(?:partecipazion[ei]\s+in\s+forma\s+(?:associata|aggregata|congiunta)"
        r"|domande\s+(?:presentate\s+)?in\s+forma\s+(?:associata|aggregata|congiunta)"
        r"|aggregazioni|raggruppamenti|partenariati|forme\s+(?:associate|aggregate)"
        r"|ATS|ATI|RTI)",
    ),
    _p(
        "forma_singola", ("singola",), "nega", False,
        r"\bin\s+forma\s+singola\b(?!\s+" + _SINGOLA_O_ASSOCIATA + r")",
    ),
    _p(
        "mono_beneficiary", ("beneficiary", "applicant"), "nega", True,
        r"\b(?:mono|single)[-\s]beneficiary\b|\b(?:single|individual)\s+applicants?\s+only\b",
    ),
)


@dataclass(frozen=True)
class Segnale:
    categoria: Categoria
    pattern: str
    sezione: str
    estratto: str

    def a_dict(self) -> dict:
        return {
            "categoria": self.categoria,
            "pattern": self.pattern,
            "sezione": self.sezione,
            "estratto": self.estratto,
        }


@dataclass(frozen=True)
class Preclassificazione:
    segnali: list[Segnale] = field(default_factory=list)
    # Numero di corrispondenze (anche ripetute) per sezione: serve a scegliere
    # le pagine da mandare al modello quando il testo supera il tetto.
    per_sezione: dict[str, int] = field(default_factory=dict)
    livello: Livello = "nessuno"

    def a_dict(self) -> dict:
        """Forma JSON per `bando_partenariato.preclassificazione`."""
        return {
            "segnali": [s.a_dict() for s in self.segnali],
            "per_sezione": dict(self.per_sezione),
            "livello": self.livello,
        }


def _prepara(testo: str) -> str:
    return " ".join(normalizza_caratteri(testo).split())


def _estratto(testo: str, inizio: int, fine: int) -> str:
    lunghezza = fine - inizio
    if lunghezza >= MAX_ESTRATTO:
        return testo[inizio : inizio + MAX_ESTRATTO].strip()
    margine = (MAX_ESTRATTO - lunghezza) // 2
    a = max(0, inizio - margine)
    b = min(len(testo), fine + margine)
    # Niente parole tagliate a metà ai bordi, se c'è spazio per evitarlo.
    if a > 0:
        spazio = testo.find(" ", a, inizio)
        if spazio != -1:
            a = spazio + 1
    if b < len(testo):
        spazio = testo.rfind(" ", fine, b)
        if spazio != -1:
            b = spazio
    return testo[a:b].strip()[:MAX_ESTRATTO]


class _Esclusioni:
    """Corrispondenze delle regex di esclusione in UNA sezione, cercate una
    volta per regex (alla prima richiesta) invece che in una finestra per
    ogni corrispondenza del pattern: il costo resta lineare nel testo anche
    con decine di migliaia di corrispondenze. Le corrispondenze di `finditer`
    non si sovrappongono, quindi inizi e fini sono crescenti. Stesso esito
    della ricerca a finestre, salvo ai confini della finestra (parole tagliate
    a metà, esclusioni che vanno oltre i `_CONTESTO` caratteri): lì vale il
    testo intero."""

    def __init__(self, testo: str) -> None:
        self._testo = testo
        self._per_regex: dict[re.Pattern, tuple[list[int], list[int]]] = {}

    def _intervalli(self, regex: re.Pattern) -> tuple[list[int], list[int]]:
        intervalli = self._per_regex.get(regex)
        if intervalli is None:
            inizi: list[int] = []
            fini: list[int] = []
            for m in regex.finditer(self._testo):
                inizi.append(m.start())
                fini.append(m.end())
            intervalli = self._per_regex[regex] = (inizi, fini)
        return intervalli

    def sovrapposta(self, regex: re.Pattern, inizio: int, fine: int) -> bool:
        """Una corrispondenza di `regex` si sovrappone a [inizio, fine)."""
        inizi, fini = self._intervalli(regex)
        k = bisect_left(inizi, fine)
        return k > 0 and fini[k - 1] > inizio

    def dentro(self, regex: re.Pattern, da: int, a: int) -> bool:
        """Una corrispondenza di `regex` sta tutta dentro [da, a)."""
        inizi, fini = self._intervalli(regex)
        k = bisect_left(inizi, da)
        return k < len(inizi) and fini[k] <= a


def _escluso(pattern: _Pattern, esclusioni: _Esclusioni, inizio: int, fine: int) -> bool:
    if any(esclusioni.sovrapposta(regex, inizio, fine) for regex in pattern.escludi):
        return True
    da, a = max(0, inizio - _CONTESTO), fine + _CONTESTO
    return any(esclusioni.dentro(regex, da, a) for regex in pattern.escludi_contesto)


def preclassifica(sezioni: dict[str, str]) -> Preclassificazione:
    """Segnali di partenariato nelle sezioni, nell'ordine delle sezioni e
    della posizione. Un segnale per (pattern, sezione); `per_sezione` conta
    tutte le corrispondenze. Livello: `forte` se c'è almeno un segnale forte,
    `debole` se ci sono solo segnali generici, `nessuno` altrimenti."""
    segnali: list[Segnale] = []
    per_sezione: dict[str, int] = {}
    visti: set[tuple[str, str]] = set()
    forte = False
    for sezione, grezzo in (sezioni or {}).items():
        if not isinstance(grezzo, str) or not grezzo.strip():
            continue
        testo = _prepara(grezzo)
        minuscolo = testo.lower()
        esclusioni = _Esclusioni(testo)
        trovati: list[tuple[int, int, _Pattern]] = []
        for pattern in PATTERN:
            if not any(radice in minuscolo for radice in pattern.inneschi):
                continue
            for m in pattern.regex.finditer(testo):
                if _escluso(pattern, esclusioni, m.start(), m.end()):
                    continue
                trovati.append((m.start(), m.end(), pattern))
        if not trovati:
            continue
        per_sezione[str(sezione)] = len(trovati)
        trovati.sort(key=lambda t: t[0])
        for inizio, fine, pattern in trovati:
            forte = forte or pattern.forte
            chiave = (pattern.nome, str(sezione))
            if chiave in visti or len(segnali) >= MAX_SEGNALI:
                continue
            visti.add(chiave)
            segnali.append(
                Segnale(
                    categoria=pattern.categoria,
                    pattern=pattern.nome,
                    sezione=str(sezione),
                    estratto=_estratto(testo, inizio, fine),
                )
            )
    if forte:
        livello: Livello = "forte"
    elif segnali:
        livello = "debole"
    else:
        livello = "nessuno"
    return Preclassificazione(segnali=segnali, per_sezione=per_sezione, livello=livello)
