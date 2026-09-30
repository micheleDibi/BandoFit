"""Pulsanti e allegati della scheda di un bando (contratto DB bandi §5 e §5.1).

La scheda mostra un pulsante principale (`cta`), un pulsante «Fonte
ufficiale» (`link_fonte`) e l'elenco degli allegati. Li calcola il backend,
così l'API e il frontend non cambiano quando le colonne di ripiego escono
dalla select (`bandi_service.COLONNE_RIPIEGO_51`).

Fonti: le righe di `bando_link` del bando (una lettura sola, colonne per
nome) e, come ripiego, le colonne deprecate della riga. Ogni URL passa dal
filtro dei link su tutte le fonti (`link_policy.link_pubblicabile`): un URL
scartato non si mostra e si passa al candidato successivo.

Se la lettura di `bando_link` non riesce, la scheda si calcola con i soli
ripieghi: un warning nel log, mai un 5xx.
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from postgrest.exceptions import APIError

from app.schemas.bando import AllegatoScheda, LinkScheda, OrigineLink
from app.services.link_policy import host_pubblicabile, link_pubblicabile, scrub_text_mentions

logger = logging.getLogger("bandofit.bando_scheda_link")

BANDO_LINK_SELECT = "id,bando_id,url,dominio,tipo,etichetta,content_type,ultimo_visto_at"
TIPI_SCHEDA = ("candidatura", "portale", "atto", "allegato")
# Tetto della lettura: un bando ne ha di norma poche decine.
LIMITE_RIGHE = 200

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


def _etichetta(valore: Any) -> str | None:
    """Etichetta da mostrare, senza menzioni dei domini esclusi."""
    return _testo(scrub_text_mentions(valore)) if isinstance(valore, str) else None


def _id_riga(riga: dict) -> float:
    valore = riga.get("id")
    if isinstance(valore, int) and not isinstance(valore, bool):
        return valore
    return float("inf")


def _istante(valore: Any) -> datetime | None:
    if not isinstance(valore, str):
        return None
    try:
        istante = datetime.fromisoformat(valore.strip())
    except ValueError:
        return None
    return istante if istante.tzinfo else istante.replace(tzinfo=timezone.utc)


def _righe(link: list[dict], tipo: str) -> list[dict]:
    return [r for r in link if isinstance(r, dict) and r.get("tipo") == tipo]


def _per_recenza(righe: list[dict]) -> list[dict]:
    """`ultimo_visto_at` più recente per primo (i NULL in coda), a parità
    l'id più basso."""

    def chiave(riga: dict):
        istante = _istante(riga.get("ultimo_visto_at"))
        return (istante is None, -istante.timestamp() if istante else 0.0, _id_riga(riga))

    return sorted(righe, key=chiave)


def _per_id(righe: list[dict]) -> list[dict]:
    return sorted(righe, key=_id_riga)


def _chiave_url(url: str) -> str:
    """URL uguali = identici dopo aver tolto la sola barra finale."""
    return url[:-1] if url.endswith("/") else url


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
    dall'API: se il filtro dei link ne scarta uno, cadono entrambi."""
    url = riga.get("fonte_ufficiale_url")
    host = riga.get("fonte_ufficiale_host")
    url_ok = url is None or link_pubblicabile(url) is not None
    host_ok = _testo(host) is None or host_pubblicabile(host) is not None
    return (url, host) if url_ok and host_ok else (None, None)


def _fonte_trovata(riga: dict) -> bool:
    return riga.get("fonte_ufficiale_stato") == "trovata"


def _candidati_cta(riga: dict, link: list[dict]) -> Iterator[LinkScheda | None]:
    for r in _per_recenza(_righe(link, "candidatura")):
        yield _link(r.get("url"), "candidatura")
    yield _link(riga.get("link_candidatura"), "link_candidatura")
    if _fonte_trovata(riga):
        yield _link(
            riga.get("fonte_ufficiale_url"), "fonte_ufficiale", riga.get("fonte_ufficiale_host")
        )
    for r in _per_recenza(_righe(link, "portale")):
        yield _link(r.get("url"), "portale")
    yield _link(riga.get("link_bando"), "link_bando")


def calcola_cta(riga: dict, link: list[dict]) -> LinkScheda | None:
    """Pulsante principale, nell'ordine del §5.1: riga `candidatura` →
    `link_candidatura` → fonte ufficiale (solo se `trovata`) → riga
    `portale` → `link_bando`. Con più righe dello stesso tipo vince la più
    recente (`ultimo_visto_at`), a parità l'id più basso. Il primo URL
    ammesso dal filtro vince."""
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


def calcola_allegati(riga: dict, link: list[dict]) -> list[AllegatoScheda]:
    """Allegati della scheda: righe `bando_link` `atto` poi `allegato` (per
    id), poi il jsonb `allegati` nel suo ordine (tipo `allegato`), senza
    doppioni per URL. A parità di URL vince la prima voce (quindi la riga di
    `bando_link`), e un'etichetta vuota si completa con quella di un doppione."""
    candidati: list[tuple[Any, str | None, str, Any, str | None]] = [
        (r.get("url"), _etichetta(r.get("etichetta")), tipo, r.get("content_type"), None)
        for tipo in ("atto", "allegato")
        for r in _per_id(_righe(link, tipo))
    ]
    candidati += [
        (url, etichetta, "allegato", None, formato)
        for url, etichetta, formato in _voci_jsonb(riga.get("allegati"))
    ]

    visti: dict[str, AllegatoScheda] = {}
    for url, etichetta, tipo, content_type, formato in candidati:
        ammesso = link_pubblicabile(url)
        if ammesso is None:
            continue
        url_ok = ammesso[0]
        chiave = _chiave_url(url_ok)
        gia = visti.get(chiave)
        if gia is not None:
            if gia.etichetta is None and etichetta:
                gia.etichetta = etichetta
            continue
        visti[chiave] = AllegatoScheda(
            url=url_ok, etichetta=etichetta, tipo=tipo,
            formato=_formato(url_ok, content_type, formato),
        )
    return list(visti.values())
