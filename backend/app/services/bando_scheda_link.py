"""Pulsanti e allegati della scheda di un bando (contratto DB bandi §5 e §5.1).

La scheda mostra un pulsante principale (`cta`), un pulsante «Fonte
ufficiale» (`link_fonte`) e l'elenco degli allegati. Li calcola il backend,
così l'API e il frontend non cambiano quando le colonne di ripiego escono
dalla select (`bandi_service.COLONNE_RIPIEGO_51`).

Fonti: le righe di `bando_link` del bando (una lettura sola, colonne per
nome) e, come ripiego, le colonne deprecate della riga. Ogni URL passa dal
filtro dei link su tutte le fonti (`link_policy.link_pubblicabile`): un URL
scartato non si mostra e si passa al candidato successivo.

Gli allegati non hanno doppioni: la chiave è la normalizzazione degli URL
del catalogo replicata qui (`chiave_url_catalogo`), e con la stessa chiave
si toglie l'allegato uguale al pulsante principale, quando il chiamante dice
che il pulsante viene mostrato (`bandi_service.map_detail`).

Se la lettura di `bando_link` non riesce, la scheda si calcola con i soli
ripieghi: un warning nel log, mai un 5xx.
"""

import logging
import unicodedata
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote, urlsplit

from postgrest.exceptions import APIError

from app.schemas.bando import AllegatoScheda, LinkScheda, OrigineLink
from app.services.link_policy import host_pubblicabile, link_pubblicabile, scrub_text_mentions

logger = logging.getLogger("bandofit.bando_scheda_link")

# `ultimo_visto_at` non si legge: avanza a ogni verifica e non decide nulla.
BANDO_LINK_SELECT = "id,bando_id,url,dominio,tipo,etichetta,content_type"
TIPI_SCHEDA = ("candidatura", "portale", "atto", "allegato")
# Tetto della lettura: un bando ne ha di norma poche decine.
LIMITE_RIGHE = 200
# Etichetta di un allegato che non ha né etichetta, né label, né nome del file.
ETICHETTA_RIPIEGO = "Allegato"
MAX_ETICHETTA = 200
# Parametri di query che non distinguono due URL (oltre a `utm_*`).
_PARAMETRI_TRACCIAMENTO = frozenset({"fbclid", "gclid", "msclkid", "_ga"})

# Formati riconosciuti, dal content-type dichiarato o dall'estensione.
_FORMATI_CONTENT_TYPE = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/rtf": "rtf",
    "text/rtf": "rtf",
    "application/zip": "zip",
    "application/x-zip-compressed": "zip",
    "application/pkcs7-mime": "p7m",
    "application/x-pkcs7-mime": "p7m",
    "text/csv": "csv",
    "text/plain": "txt",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
_FORMATI_ESTENSIONE = frozenset(_FORMATI_CONTENT_TYPE.values())


# ------------------------------------------------------------------ lettura


async def carica_link_scheda(secondary, bando_id: int) -> list[dict] | None:
    """Righe `bando_link` del bando utili alla scheda; None se la lettura
    non riesce. Nel log vanno solo id del bando e codice dell'errore, mai
    URL né messaggio di PostgREST."""
    query = (
        secondary.table("bando_link")
        .select(BANDO_LINK_SELECT)
        .eq("bando_id", bando_id)
        .in_("tipo", list(TIPI_SCHEDA))
        .order("id")
        .limit(LIMITE_RIGHE)
    )
    try:
        resp = await query.execute()
    except Exception as exc:  # qualunque guasto: la scheda usa i ripieghi
        codice = (exc.code if isinstance(exc, APIError) else None) or type(exc).__name__
        logger.warning(
            "lettura di bando_link non riuscita: bando_id=%s codice=%s", bando_id, codice
        )
        return None
    return [
        riga
        for riga in (resp.data or [])
        if isinstance(riga, dict) and riga.get("bando_id") == bando_id
    ]


# --------------------------------------------------------------- utilità


def _testo(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    valore = valore.strip()
    return valore or None


def _senza_controlli(testo: str) -> str:
    """Il testo senza caratteri di controllo e di direzione del testo
    (categorie Cc e Cf): l'etichetta esce dall'API ed entra nel prompt. Quelli
    di spaziatura (a capo, tabulazione) diventano uno spazio, per non unire
    le parole."""
    return "".join(
        " " if c.isspace() else c
        for c in testo
        if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf")
    )


def _etichetta(valore: Any) -> str | None:
    """Etichetta da mostrare: senza caratteri Cc/Cf, senza menzioni dei
    domini esclusi, spazi compressi, al massimo `MAX_ETICHETTA` caratteri."""
    if not isinstance(valore, str):
        return None
    testo = scrub_text_mentions(_senza_controlli(valore))
    return _testo(" ".join(testo.split())[:MAX_ETICHETTA])


def _id_riga(riga: dict) -> float:
    valore = riga.get("id")
    if isinstance(valore, int) and not isinstance(valore, bool):
        return valore
    return float("inf")


def _righe(link: list[dict], tipo: str) -> list[dict]:
    return [r for r in link if isinstance(r, dict) and r.get("tipo") == tipo]


def _per_id(righe: list[dict]) -> list[dict]:
    return sorted(righe, key=_id_riga)


def _parametro_di_tracciamento(segmento: str) -> bool:
    nome = segmento.split("=", 1)[0]
    return nome.startswith("utm_") or nome in _PARAMETRI_TRACCIAMENTO


def chiave_url_catalogo(url: str) -> str:
    """Chiave dei doppioni: la normalizzazione degli URL del catalogo
    replicata (contratto DB bandi §5.1). Trim; schema e host minuscoli; via
    `www.` iniziale, porta 80 o 443, frammento, parametri `utm_*`, `fbclid`,
    `gclid`, `msclkid`, `_ga` e una barra finale; `http` e `https` restano
    diversi. I parametri rimasti restano nell'ordine e nella forma
    originali. Un URL non analizzabile resta con il solo trim."""
    url = url.strip()
    try:
        parti = urlsplit(url)
        porta = parti.port
    except ValueError:
        return url
    host = (parti.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if porta is not None and porta not in (80, 443):
        host = f"{host}:{porta}"
    percorso = parti.path[:-1] if parti.path.endswith("/") else parti.path
    query = "&".join(
        segmento
        for segmento in parti.query.split("&")
        if not _parametro_di_tracciamento(segmento)
    )
    chiave = f"{parti.scheme.lower()}://{host}{percorso}"
    return f"{chiave}?{query}" if query else chiave


def _nome_file(url: str) -> str | None:
    """Nome del file dal percorso dell'URL, come etichetta di ripiego: ultimo
    segmento decodificato, senza query né estensione nota (il formato la
    scheda lo mostra a parte), poi ripulito come ogni etichetta
    (`_etichetta`). None se non resta nulla."""
    try:
        percorso = unquote(urlsplit(url).path)
    except ValueError:
        return None
    # La decodifica fa ricomparire caratteri di controllo e di direzione
    # del testo (categorie Cc e Cf): via, prima di cercare l'estensione.
    percorso = _senza_controlli(percorso)
    nome = percorso.rstrip("/").rsplit("/", 1)[-1]
    base, punto, estensione = nome.rpartition(".")
    if punto and estensione.lower() in _FORMATI_ESTENSIONE:
        nome = base
    return _etichetta(nome)


def _formato_dichiarato(valore: Any) -> str | None:
    """Formato dichiarato dal catalogo (es. «PDF»), se è un formato noto."""
    formato = _testo(valore)
    formato = formato.lower().lstrip(".") if formato else None
    return formato if formato in _FORMATI_ESTENSIONE else None


def _formato(url: str, content_type: Any, dichiarato: str | None = None) -> str | None:
    if dichiarato:
        return dichiarato
    tipo = _testo(content_type)
    if tipo:
        formato = _FORMATI_CONTENT_TYPE.get(tipo.split(";", 1)[0].strip().lower())
        if formato:
            return formato
    try:
        percorso = unquote(urlsplit(url).path)
    except ValueError:
        return None
    nome = percorso.rsplit("/", 1)[-1]
    if "." not in nome:
        return None
    estensione = nome.rsplit(".", 1)[-1].lower()
    return estensione if estensione in _FORMATI_ESTENSIONE else None


# ----------------------------------------------------------------- pulsanti


def _link(url: Any, origine: OrigineLink, host_dichiarato: Any = None) -> LinkScheda | None:
    ammesso = link_pubblicabile(url)
    if ammesso is None:
        return None
    url_ok, host = ammesso
    # L'etichetta è sempre l'host dell'URL, quello visitato. L'host dichiarato
    # (fonte ufficiale) va insieme all'url: se non è ammesso, cade il link.
    if _testo(host_dichiarato) is not None and host_pubblicabile(host_dichiarato) is None:
        return None
    return LinkScheda(url=url_ok, host=host, origine=origine)


def fonte_ufficiale_pubblicabile(riga: dict) -> tuple[Any, Any]:
    """`(fonte_ufficiale_url, fonte_ufficiale_host)` come possono uscire
    dall'API: se il filtro dei link ne scarta uno, cadono entrambi. L'URL è
    quello restituito dal filtro, lo stesso di `cta` e `link_fonte`."""
    url = riga.get("fonte_ufficiale_url")
    host = riga.get("fonte_ufficiale_host")
    if url is not None:
        ammesso = link_pubblicabile(url)
        if ammesso is None:
            return None, None
        url = ammesso[0]
    host_ok = _testo(host) is None or host_pubblicabile(host) is not None
    return (url, host) if host_ok else (None, None)


def _fonte_trovata(riga: dict) -> bool:
    return riga.get("fonte_ufficiale_stato") == "trovata"


def _candidati_cta(riga: dict, link: list[dict]) -> Iterator[LinkScheda | None]:
    for r in _per_id(_righe(link, "candidatura")):
        yield _link(r.get("url"), "candidatura")
    yield _link(riga.get("link_candidatura"), "link_candidatura")
    if _fonte_trovata(riga):
        yield _link(
            riga.get("fonte_ufficiale_url"), "fonte_ufficiale", riga.get("fonte_ufficiale_host")
        )
    for r in _per_id(_righe(link, "portale")):
        yield _link(r.get("url"), "portale")
    yield _link(riga.get("link_bando"), "link_bando")


def calcola_cta(riga: dict, link: list[dict]) -> LinkScheda | None:
    """Pulsante principale, nell'ordine del §5.1: riga `candidatura` →
    `link_candidatura` → fonte ufficiale (solo se `trovata`) → riga
    `portale` → `link_bando`. Con più righe dello stesso tipo vince l'id più
    basso (la lettura è con `order=id`); `ultimo_visto_at` non si usa, perché
    avanza a ogni verifica. Il primo URL ammesso dal filtro vince."""
    return next((c for c in _candidati_cta(riga, link) if c is not None), None)


def calcola_link_fonte(riga: dict) -> LinkScheda | None:
    """Pulsante «Fonte ufficiale»: la fonte se `trovata` e ammessa,
    altrimenti `link_bando`. La UI lo mostra solo se diverso dal
    pulsante principale."""
    if _fonte_trovata(riga):
        fonte = _link(
            riga.get("fonte_ufficiale_url"), "fonte_ufficiale", riga.get("fonte_ufficiale_host")
        )
        if fonte is not None:
            return fonte
    return _link(riga.get("link_bando"), "link_bando")


# ----------------------------------------------------------------- allegati


def _voci_jsonb(allegati: Any) -> Iterator[tuple[Any, str | None, str | None]]:
    """(url, etichetta, formato) dal jsonb deprecato `allegati`, nel suo
    ordine: il suo `tipo` è il formato del file. Le voci malformate si
    saltano."""
    if not isinstance(allegati, list):
        return
    for voce in allegati:
        if not isinstance(voce, dict):
            continue
        url = voce.get("url") if voce.get("url") is not None else voce.get("link")
        etichetta = next(
            (e for e in map(_etichetta, (voce.get(k) for k in ("label", "nome", "titolo"))) if e),
            None,
        )
        yield url, etichetta, _formato_dichiarato(voce.get("tipo"))


def calcola_allegati(
    riga: dict, link: list[dict], *, escludi: str | None = None
) -> list[AllegatoScheda]:
    """Allegati della scheda (contratto DB bandi §5.1): righe `bando_link`
    `atto`/`allegato` per id, poi il jsonb `allegati` nel suo ordine (tipo
    `allegato`), senza doppioni per URL (`chiave_url_catalogo`). A parità di
    chiave vince la prima voce (quindi la riga di `bando_link`); l'etichetta
    è quella della voce, poi quella di un doppione, poi il nome del file,
    infine «Allegato»: mai vuota e sempre distinta nella scheda (una
    ripetuta prende il primo numero libero: «scarica», «scarica (2)»…).
    `escludi` è
    l'URL del pulsante principale quando questo viene mostrato: ogni
    candidato con la sua chiave si salta prima del dedup (anche le
    varianti); con None nessun candidato si salta."""
    righe = _per_id(
        [r for r in link if isinstance(r, dict) and r.get("tipo") in ("atto", "allegato")]
    )
    candidati: list[tuple[Any, str | None, str, Any, str | None]] = [
        (r.get("url"), _etichetta(r.get("etichetta")), r["tipo"], r.get("content_type"), None)
        for r in righe
    ]
    candidati += [
        (url, etichetta, "allegato", None, formato)
        for url, etichetta, formato in _voci_jsonb(riga.get("allegati"))
    ]
    chiave_esclusa = chiave_url_catalogo(escludi) if escludi else None

    # Dedup su record grezzi: il modello (etichetta obbligatoria) si
    # costruisce alla fine, quando l'etichetta è risolta.
    visti: dict[str, dict[str, Any]] = {}
    for url, etichetta, tipo, content_type, formato in candidati:
        ammesso = link_pubblicabile(url)
        if ammesso is None:
            continue
        url_ok = ammesso[0]
        chiave = chiave_url_catalogo(url_ok)
        if chiave == chiave_esclusa:
            continue
        gia = visti.get(chiave)
        if gia is not None:
            if gia["etichetta"] is None and etichetta:
                gia["etichetta"] = etichetta
            continue
        visti[chiave] = {
            "url": url_ok, "etichetta": etichetta, "tipo": tipo,
            "formato": _formato(url_ok, content_type, formato),
        }
    # Etichette sempre distinte nella scheda, in due passate: prima ogni
    # etichetta distinta resta alla sua prima voce (così un'etichetta reale
    # come «scarica (2)» non viene mai rinumerata), poi ogni ripetuta (del
    # catalogo o di ripiego) prende il primo «(n)» libero.
    etichette = [
        voce["etichetta"] or _nome_file(voce["url"]) or ETICHETTA_RIPIEGO
        for voce in visti.values()
    ]
    usate = set(etichette)
    prime: set[str] = set()
    allegati: list[AllegatoScheda] = []
    for voce, etichetta in zip(visti.values(), etichette, strict=True):
        if etichetta in prime:
            n = 2
            while f"{etichetta} ({n})" in usate:
                n += 1
            etichetta = f"{etichetta} ({n})"
            usate.add(etichetta)
        else:
            prime.add(etichetta)
        allegati.append(AllegatoScheda(**{**voce, "etichetta": etichetta}))
    return allegati
