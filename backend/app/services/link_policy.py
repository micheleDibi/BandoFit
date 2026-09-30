"""Rimozione dei link verso domini esclusi dal catalogo (concorrenti).

Nel DB secondario una parte dei bandi ha `link_bando`/`link_candidatura`
(e qualche segmento «link» dentro `contenuto`) che puntano a
obiettivoeuropa.com, un aggregatore concorrente: quei rimandi non devono
mai uscire dall'API — né in pagina né nel testo passato all'AI-check.
Lo stesso vale per la fonte ufficiale (`fonte_ufficiale_url`/`_host`).
Il filtro si applica alla riga grezza del bando alla frontiera del
catalogo (`fetch_bando_by_slug` e `fetch_bando_for_ai` in
`bandi_service`), unico punto di passaggio di tutte le superfici.
"""

import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlsplit

# Confronto sull'host: vale il dominio esatto e ogni sottodominio (www
# incluso), mai la substring sull'URL intero (un `?ref=` non blocca).
BLOCKED_LINK_HOSTS = frozenset({"obiettivoeuropa.com"})

# I browser normalizzano i backslash a slash e tollerano schemi con uno
# slash solo o assenti: l'estrazione dell'host deve reggere le stesse
# forme, o un URL sciatto ma cliccabile aggirerebbe il blocco.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:/*", re.IGNORECASE)


def host_non_normalizzabile(host: str) -> bool:
    """True se l'host non si può normalizzare come in un browser."""
    return any(unicodedata.ucd_3_2_0.category(c) == "Cn" for c in host)


def _host_normalizzato(host: str) -> str | None:
    """Host normalizzato come un browser, minuscolo e senza punto finale;
    None se non normalizzabile."""
    grezzo = unquote(host.strip()).rstrip(".")
    if host_non_normalizzabile(grezzo):
        return None
    try:
        return grezzo.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None


def is_blocked_link(url: Any) -> bool:
    """True se l'URL punta (anche via sottodominio) a un dominio escluso.
    Un host non normalizzabile vale come escluso."""
    if not isinstance(url, str):
        return False
    candidate = _SCHEME.sub("", url.strip().replace("\\", "/"))
    try:
        host = urlsplit("//" + candidate.lstrip("/")).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = _host_normalizzato(host)
    if host is None:
        return True
    return any(
        host == blocked or host.endswith("." + blocked)
        for blocked in BLOCKED_LINK_HOSTS
    )


_FONTE_SCHEMI = frozenset({"http", "https"})


def _ha_schema_http(url: Any) -> bool:
    """True se `url` è una stringa con schema http o https (maiuscole
    incluse). Senza schema, `javascript:`, `data:` e simili → False."""
    if not isinstance(url, str):
        return False
    try:
        return urlsplit(url.strip()).scheme.lower() in _FONTE_SCHEMI
    except ValueError:
        return False


def _mentions_blocked_host(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return any(blocked in lowered for blocked in BLOCKED_LINK_HOSTS)


# Menzione testuale di un dominio escluso, con eventuale schema,
# sottodominio e coda di URL fino allo spazio.
_MENTION = re.compile(
    r"(?:https?:/+|//)?(?:[\w-]+\.)*(?:"
    + "|".join(re.escape(host) for host in BLOCKED_LINK_HOSTS)
    + r")(?:[/?#][^\s]*)?",
    re.IGNORECASE,
)


def scrub_text_mentions(node: Any) -> Any:
    """Rimuove le menzioni testuali dei domini esclusi da una struttura
    JSON-like. Serve ai report AI-check STORICI: le citazioni verbatim
    (`testo_esatto`) di un report generato prima del filtro possono
    contenere il dominio; i report nuovi nascono già puliti perché il
    testo serializzato del bando è filtrato a monte."""
    if isinstance(node, str):
        return _MENTION.sub("", node)
    if isinstance(node, list):
        return [scrub_text_mentions(item) for item in node]
    if isinstance(node, dict):
        return {key: scrub_text_mentions(value) for key, value in node.items()}
    return node


# Il renderer del frontend legge l'URL del segmento da `href ?? url`;
# `link` per simmetria con gli allegati.
_SEGMENT_URL_KEYS = ("url", "href", "link")


def _scrub_segments(segments: list) -> list:
    """Un segmento col dominio nel testo visibile cade per intero, link o
    no; uno con il solo link bloccato perde il link ma tiene il testo (di
    solito è un'ancora in mezzo a una frase: toglierlo la spezzerebbe)."""
    out = []
    for seg in segments:
        if not isinstance(seg, dict):
            out.append(seg)
            continue
        if _mentions_blocked_host(seg.get("text")):
            continue
        if not any(is_blocked_link(seg.get(key)) for key in _SEGMENT_URL_KEYS):
            out.append(seg)
            continue
        clean = {k: v for k, v in seg.items() if k not in _SEGMENT_URL_KEYS}
        if clean.get("kind") == "link":
            clean["kind"] = "text"
        out.append(clean)
    return out


def _scrub_contenuto(node: Any) -> Any:
    """Attraversa `contenuto`: filtra ogni lista `segments` ovunque sia
    annidata e rimuove le menzioni testuali da QUALUNQUE stringa — titoli
    di sezione, voci di elenco e risposte FAQ possono essere stringhe
    semplici, fuori da ogni segmento."""
    if isinstance(node, str):
        return _MENTION.sub("", node)
    if isinstance(node, list):
        return [_scrub_contenuto(item) for item in node]
    if isinstance(node, dict):
        return {
            key: _scrub_segments(value)
            if key == "segments" and isinstance(value, list)
            else _scrub_contenuto(value)
            for key, value in node.items()
        }
    return node


def scrub_bando_row(row: dict) -> dict:
    """Copia della riga del bando senza alcun rimando ai domini esclusi.

    `contenuto` deve essere già normalizzato (`normalize_contenuto`):
    una stringa doppio-encodata non verrebbe attraversata dal filtro.
    """
    row = dict(row)
    for key in ("link_bando", "link_candidatura"):
        if is_blocked_link(row.get(key)):
            row[key] = None
    # Fonte ufficiale: url e host vanno insieme (l'host è l'etichetta del
    # pulsante): cadono entrambi se uno dei due è escluso o se l'url non è
    # un indirizzo http/https.
    fonte_url = row.get("fonte_ufficiale_url")
    if (
        (fonte_url is not None and not _ha_schema_http(fonte_url))
        or is_blocked_link(fonte_url)
        or is_blocked_link(row.get("fonte_ufficiale_host"))
    ):
        row["fonte_ufficiale_url"] = None
        row["fonte_ufficiale_host"] = None
    allegati = row.get("allegati")
    if isinstance(allegati, list):
        row["allegati"] = [
            item
            for item in allegati
            if not (
                isinstance(item, dict)
                and (is_blocked_link(item.get("url")) or is_blocked_link(item.get("link")))
            )
        ]
    if isinstance(row.get("contenuto"), dict):
        row["contenuto"] = _scrub_contenuto(row["contenuto"])
    return row


# --- Link della scheda: pulsanti e allegati ---------------------------------
# Filtro dei link su tutte le fonti (contratto DB bandi §5): ogni URL che la
# scheda mostra come pulsante o come allegato passa da `link_pubblicabile`,
# qualunque colonna o tabella lo fornisca. È una lista separata da
# BLOCKED_LINK_HOSTS, che pilota anche le regex sulle menzioni nei testi:
# lì un dominio breve come «x.com» taglierebbe testo legittimo.
DOMINI_ESCLUSI = BLOCKED_LINK_HOSTS | frozenset(
    {
        # aggregatori
        "fasi.eu", "europafacile.net", "contributiregione.it", "finanziamentinews.it",
        "bandi.it", "infobandi.it", "ticonsiglio.com", "contributieuropa.com",
        "first.aster.it",
        # social, video, messaggistica
        "facebook.com", "instagram.com", "x.com", "twitter.com", "linkedin.com",
        "threads.net", "pinterest.com", "tiktok.com", "youtube.com", "youtu.be",
        "vimeo.com", "t.me", "telegram.me", "wa.me", "whatsapp.com",
    }
)

# Spazi (anche Unicode), caratteri di controllo e backslash: un URL che li
# contiene non si mostra, il browser lo interpreterebbe a modo suo.
_CARATTERI_VIETATI = re.compile(r"[\s\x00-\x1f\x7f\\]")
_HOST_AMMESSO = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-_")


def normalizza_host(host: Any) -> str | None:
    """Host normalizzato come un browser, minuscolo e senza punto finale.
    None se vuoto, non normalizzabile o con caratteri che un nome di dominio
    non ammette."""
    if not isinstance(host, str):
        return None
    norm = _host_normalizzato(host)
    if norm is None or "." not in norm or not set(norm) <= _HOST_AMMESSO:
        return None
    return norm


def _dominio_escluso(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in DOMINI_ESCLUSI)


def host_pubblicabile(host: Any) -> str | None:
    """Host senza schema (es. `fonte_ufficiale_host`), normalizzato, se può
    fare da etichetta a un link; None se vuoto, non valido o escluso."""
    if not isinstance(host, str) or _CARATTERI_VIETATI.search(host.strip()):
        return None
    try:
        grezzo = urlsplit("//" + host.strip()).hostname
    except ValueError:
        return None
    norm = normalizza_host(grezzo)
    if norm is None or _dominio_escluso(norm) or is_blocked_link(norm):
        return None
    return norm


def link_pubblicabile(url: Any) -> tuple[str, str] | None:
    """`(url, host normalizzato)` se l'URL può uscire dall'API come pulsante o
    allegato della scheda, altrimenti None.

    Solo `http`/`https` con un host; niente spazi, caratteri di controllo,
    backslash o credenziali; host fuori dai domini esclusi, sottodomini
    compresi. L'URL restituito è quello ricevuto, senza spazi ai bordi."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or _CARATTERI_VIETATI.search(url):
        return None
    try:
        parti = urlsplit(url)
        grezzo = parti.hostname
        credenziali = parti.username is not None or parti.password is not None
    except ValueError:
        return None
    if parti.scheme.lower() not in _FONTE_SCHEMI or credenziali or not grezzo:
        return None
    host = normalizza_host(grezzo)
    if host is None or _dominio_escluso(host) or is_blocked_link(url):
        return None
    return url, host
