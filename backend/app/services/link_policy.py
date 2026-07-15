"""Rimozione dei link verso domini esclusi dal catalogo (concorrenti).

Nel DB secondario una parte dei bandi ha `link_bando`/`link_candidatura`
(e qualche segmento «link» dentro `contenuto`) che puntano a
obiettivoeuropa.com, un aggregatore concorrente: quei rimandi non devono
mai uscire dall'API — né in pagina né nel testo passato all'AI-check.
Il filtro si applica alla riga grezza del bando alla frontiera del
catalogo (`fetch_bando_by_slug` e `fetch_bando_for_ai` in
`bandi_service`), unico punto di passaggio di tutte le superfici.
"""

import re
from typing import Any
from urllib.parse import urlsplit

# Confronto sull'host: vale il dominio esatto e ogni sottodominio (www
# incluso), mai la substring sull'URL intero (un `?ref=` non blocca).
BLOCKED_LINK_HOSTS = frozenset({"obiettivoeuropa.com"})

# I browser normalizzano i backslash a slash e tollerano schemi con uno
# slash solo o assenti: l'estrazione dell'host deve reggere le stesse
# forme, o un URL sciatto ma cliccabile aggirerebbe il blocco.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:/*", re.IGNORECASE)


def is_blocked_link(url: Any) -> bool:
    """True se l'URL punta (anche via sottodominio) a un dominio escluso."""
    if not isinstance(url, str):
        return False
    candidate = _SCHEME.sub("", url.strip().replace("\\", "/"))
    try:
        host = urlsplit("//" + candidate.lstrip("/")).hostname
    except ValueError:
        return False
    if not host:
        return False
    host = host.rstrip(".").lower()
    return any(
        host == blocked or host.endswith("." + blocked)
        for blocked in BLOCKED_LINK_HOSTS
    )


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
