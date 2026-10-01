"""Fonti documentali del catalogo per le regole di partenariato (WP3).

Letture sul DB secondario (anon key, sola lettura, colonne sempre per nome):
- `bando_pubblico`: stato effettivo e ultimo cambiamento pubblico dei bandi;
- `bando_link`: i documenti ufficiali (`allegato`, `atto`, `pagina_bando`,
  `faq`), righe già filtrate dalla RLS (solo `pubblicabile`, 2xx, mai
  aggregatori). `select=*` risponde 42501: mai usarlo.

- `bando_fusione`: i doppioni fusi fra gli id assenti dalla vista.

I candidati documentali sono sempre l'unione delle righe `bando_link` e del
jsonb `allegati` della riga del bando (deprecato, dopo `scrub_bando_row`),
senza doppioni per URL: molte righe sostitutive del jsonb non sono ancora
leggibili (contratto §5.1). Ogni URL passa comunque da `link_policy` e dalla
denylist del contratto §5.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from app.services.download_sicuro import url_negato
from app.services.link_policy import senza_controlli
from app.services.partenariato_prompts import scrub_menzioni

logger = logging.getLogger("bandofit.partenariati")

FormatoDocumento = Literal["pdf", "docx", "doc", "zip", "p7m", "html", "xlsx", "altro"]
TipoLink = Literal["allegato", "atto", "pagina_bando", "faq"]

TIPI_DOCUMENTO: tuple[TipoLink, ...] = ("allegato", "atto", "pagina_bando", "faq")
SELECT_STATO = "id,slug,stato_effettivo,data_scadenza,ultimo_cambiamento_at"
SELECT_LINK = "id,bando_id,url,dominio,tipo,etichetta,content_type,ultimo_visto_at"
SELECT_FUSIONE = "bando_id,master_id"
BLOCCO_STATI = 100
BLOCCO_FUSIONI = 100
MAX_LINK = 50
MAX_ETICHETTA = 200

_MIME: dict[str, FormatoDocumento] = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/acrobat": "pdf",
    "application/vnd.pdf": "pdf",
    "applications/vnd.pdf": "pdf",
    "text/pdf": "pdf",
    "text/x-pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/zip": "zip",
    "application/x-zip": "zip",
    "application/x-zip-compressed": "zip",
    "multipart/x-zip": "zip",
    "application/pkcs7-mime": "p7m",
    "application/x-pkcs7-mime": "p7m",
    "application/pkcs7-signature": "p7m",
    "application/x-pkcs7-signature": "p7m",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xlsx",
}
_ESTENSIONI: dict[str, FormatoDocumento] = {
    "pdf": "pdf",
    "docx": "docx",
    "doc": "doc",
    "zip": "zip",
    "p7m": "p7m",
    "html": "html",
    "htm": "html",
    "xhtml": "html",
    "xlsx": "xlsx",
    "xls": "xlsx",
}
# Content-type che non dicono nulla del formato: decide l'estensione dell'URL.
_GENERICI = frozenset(
    {
        "", "application/octet-stream", "octet-stream", "binary/octet-stream",
        "application/force-download", "application/x-download", "application/download",
        "application/unknown", "application/binary", "application/save",
    }
)
# Estensioni di formati certamente non PDF (oltre a quelli del Literal): un
# link «altro» con una di queste non vale un download. Gli script dinamici
# (`download.php?id=…`) restano candidati: spesso servono proprio il PDF, e il
# controllo dei magic byte interrompe subito un download che non lo è.
_ESTENSIONI_NON_PDF = frozenset(
    {
        "odt", "ods", "odp", "rtf", "txt", "csv", "ppt", "pptx", "jpg", "jpeg", "png", "gif",
        "tif", "tiff", "mp4", "xml", "json", "rar", "7z", "eml", "msg",
    }
)

# Etichette dei documenti che contengono le regole (avviso, decreto…) e dei
# moduli da compilare (penalizzati: il testo delle regole di solito non c'è).
_ETICHETTA_REGOLE = re.compile(
    r"\b(?:avviso|bando|decreto|disciplinare|regolamento|call|guide\w*|programme"
    r"|linee\s+guida|delibera|determin\w*|dgr|dd)\b",
    re.IGNORECASE,
)
_ETICHETTA_MODULI = re.compile(
    r"\b(?:modulistica|modul[oi]|modell[oi]|fac[\s-]?simile|domanda|graduatori[ae]|elench?[oi]"
    r"|esiti|allegato\s+tecnico\s+di\s+domanda)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StatoBando:
    id: int
    slug: str | None
    stato_effettivo: str | None
    data_scadenza: date | None
    ultimo_cambiamento_at: datetime | None


@dataclass(frozen=True)
class LinkDocumento:
    id: int | None
    bando_id: int
    url: str
    dominio: str | None
    tipo: str
    etichetta: str | None
    content_type: str | None
    ultimo_visto_at: str | None


@dataclass(frozen=True)
class Candidato:
    url: str
    etichetta: str  # mai vuota: etichetta del catalogo, nome del file o ripiego
    dominio: str | None
    tipo: str  # tipo di bando_link; «allegato» per il ripiego su `allegati`
    formato: FormatoDocumento
    origine: Literal["bando_link", "allegati"]
    link_id: int | None
    priorita: int  # 0 = più importante


# ------------------------------------------------------------ letture


def _data(valore: Any) -> date | None:
    if not isinstance(valore, str) or not valore:
        return None
    try:
        return date.fromisoformat(valore[:10])
    except ValueError:
        return None


def _istante(valore: Any) -> datetime | None:
    if not isinstance(valore, str) or not valore:
        return None
    try:
        return datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError:
        return None


async def leggi_stato_bandi(secondary, ids: list[int]) -> dict[int, StatoBando]:
    """Stato dei bandi da `bando_pubblico`, a blocchi di 100 id (sotto il
    max-rows 1000 del PostgREST e con URL corti). Un id assente dal risultato
    = bando non pubblicato o fuso. Gli errori PROPAGANO: il chiamante non deve
    scambiare un errore per un'assenza."""
    unici = list(dict.fromkeys(i for i in ids if isinstance(i, int) and not isinstance(i, bool)))
    risultato: dict[int, StatoBando] = {}
    for inizio in range(0, len(unici), BLOCCO_STATI):
        blocco = unici[inizio : inizio + BLOCCO_STATI]
        resp = (
            await secondary.table("bando_pubblico")
            .select(SELECT_STATO)
            .in_("id", blocco)
            .execute()
        )
        for riga in resp.data or []:
            if not isinstance(riga, dict) or not isinstance(riga.get("id"), int):
                continue
            risultato[riga["id"]] = StatoBando(
                id=riga["id"],
                slug=riga.get("slug") if isinstance(riga.get("slug"), str) else None,
                stato_effettivo=(
                    riga.get("stato_effettivo")
                    if isinstance(riga.get("stato_effettivo"), str)
                    else None
                ),
                data_scadenza=_data(riga.get("data_scadenza")),
                ultimo_cambiamento_at=_istante(riga.get("ultimo_cambiamento_at")),
            )
    return risultato


async def leggi_fusioni(secondary, ids: list[int]) -> dict[int, int | None]:
    """Doppioni fusi fra `ids` (di solito gli assenti da `bando_pubblico`):
    `{bando_id: master_id}`, a blocchi di 100 id (contratto §6.2). Gli errori
    PROPAGANO, a differenza di `bandi_risoluzione.risolvi_fusioni`: non
    sapere se un id è fuso non deve valere come «non fuso» (il chiamante
    marcherebbe o chiuderebbe una call per un bando che c'è ancora)."""
    unici = list(dict.fromkeys(i for i in ids if isinstance(i, int) and not isinstance(i, bool)))
    fusi: dict[int, int | None] = {}
    for inizio in range(0, len(unici), BLOCCO_FUSIONI):
        blocco = unici[inizio : inizio + BLOCCO_FUSIONI]
        resp = (
            await secondary.table("bando_fusione")
            .select(SELECT_FUSIONE)
            .in_("bando_id", blocco)
            .execute()
        )
        for riga in resp.data or []:
            bando_id = riga.get("bando_id") if isinstance(riga, dict) else None
            if isinstance(bando_id, int) and not isinstance(bando_id, bool) and bando_id in blocco:
                master_id = riga.get("master_id")
                fusi[bando_id] = (
                    master_id
                    if isinstance(master_id, int) and not isinstance(master_id, bool)
                    else None
                )
    return fusi


async def leggi_link_documenti(secondary, bando_id: int) -> list[LinkDocumento] | None:
    """Link documentali del bando da `bando_link`. None SOLO su errore
    (tabella assente 42P01, permesso 42501, timeout…): restano i soli
    `allegati` e il documento mancato può essere transitorio. Una lista vuota
    è una risposta valida."""
    try:
        resp = (
            await secondary.table("bando_link")
            .select(SELECT_LINK)
            .eq("bando_id", bando_id)
            .in_("tipo", list(TIPI_DOCUMENTO))
            .order("id")
            .limit(MAX_LINK)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — qualunque errore = ripiego
        logger.warning(
            "bando_link non leggibile: bando_id=%s codice=%s",
            bando_id,
            getattr(exc, "code", None) or type(exc).__name__,
        )
        return None
    link: list[LinkDocumento] = []
    for riga in resp.data or []:
        if not isinstance(riga, dict):
            continue
        url, tipo = riga.get("url"), riga.get("tipo")
        if not isinstance(url, str) or not url.strip() or tipo not in TIPI_DOCUMENTO:
            continue
        link.append(
            LinkDocumento(
                id=riga.get("id") if isinstance(riga.get("id"), int) else None,
                bando_id=bando_id,
                url=url.strip(),
                dominio=riga.get("dominio") if isinstance(riga.get("dominio"), str) else None,
                tipo=tipo,
                etichetta=riga.get("etichetta") if isinstance(riga.get("etichetta"), str) else None,
                content_type=(
                    riga.get("content_type") if isinstance(riga.get("content_type"), str) else None
                ),
                ultimo_visto_at=(
                    riga.get("ultimo_visto_at")
                    if isinstance(riga.get("ultimo_visto_at"), str)
                    else None
                ),
            )
        )
    return link


# ------------------------------------------------------------ formato


def _estensione(url: str) -> str:
    try:
        path = unquote(urlsplit(url).path or "").lower().rstrip("/")
    except ValueError:
        return ""
    nome = path.rsplit("/", 1)[-1]
    if nome.endswith(".p7m"):
        return "p7m"  # «.pdf.p7m»: busta firmata, non un PDF
    if "." not in nome:
        return ""
    return nome.rsplit(".", 1)[-1]


def normalizza_content_type(content_type: str | None, url: str) -> FormatoDocumento:
    """Formato del documento dal content-type (MIME o estensione nuda, come
    li scrive il produttore) con ripiego sull'estensione dell'URL quando il
    content-type è vuoto o generico (octet-stream, force-download…)."""
    ct = (content_type or "").split(";", 1)[0].strip().lower().lstrip(".")
    if ct in _MIME:
        return _MIME[ct]
    if ct in _ESTENSIONI:
        return _ESTENSIONI[ct]
    if ct.endswith("+pdf") or ct.endswith("/pdf"):
        return "pdf"
    if ct in _GENERICI:
        estensione = _estensione(url or "")
        return _ESTENSIONI.get(estensione, "altro")
    return "altro"


def _forse_pdf(content_type: str | None, url: str) -> bool:
    """Un link di formato «altro» merita il download (decidono i magic byte)
    solo se nulla dice che NON è un PDF: content-type assente o generico e
    nessuna estensione di un altro formato."""
    ct = (content_type or "").split(";", 1)[0].strip().lower().lstrip(".")
    if ct not in _GENERICI:
        return False
    estensione = _estensione(url or "")
    return estensione not in _ESTENSIONI_NON_PDF and estensione not in _ESTENSIONI


# ------------------------------------------------------------ selezione


def _nome_file(url: str) -> str:
    try:
        nome = unquote(urlsplit(url).path or "").rstrip("/").rsplit("/", 1)[-1]
    except ValueError:
        return ""
    # La decodifica fa ricomparire caratteri di controllo e di direzione.
    nome = senza_controlli(nome)
    nome = re.sub(r"\.(pdf|p7m)$", "", nome, flags=re.IGNORECASE)
    return " ".join(re.sub(r"[_\-+]+", " ", nome).split())


def _etichetta(etichetta: str | None, url: str) -> str:
    """Etichetta del documento (esce dall'API ed entra nel prompt): senza
    caratteri di controllo e di direzione del testo (`senza_controlli`, la
    stessa pulizia degli allegati della scheda), spazi compressi, senza
    menzioni dei domini esclusi, al massimo `MAX_ETICHETTA` caratteri."""
    testo = (
        " ".join(senza_controlli(etichetta or "").split())
        or _nome_file(url)
        or "Documento ufficiale"
    )
    testo = scrub_menzioni(testo[: MAX_ETICHETTA * 4]).strip() or "Documento ufficiale"
    return testo[:MAX_ETICHETTA]


def _dominio(dominio: str | None, url: str) -> str | None:
    if isinstance(dominio, str) and dominio.strip():
        return dominio.strip().lower()
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _rango(tipo: str, testo_etichetta: str) -> int:
    """0 atto › 1 allegato «regole» › 2 altri allegati › 3 pagina_bando PDF
    › 4 allegati di modulistica › 5 faq."""
    if tipo == "atto":
        return 0
    if tipo == "faq":
        return 5
    if tipo == "pagina_bando":
        return 3
    if _ETICHETTA_MODULI.search(testo_etichetta):
        return 4
    if _ETICHETTA_REGOLE.search(testo_etichetta):
        return 1
    return 2


def _chiave_url(url: str) -> str:
    """Chiave dei doppioni: schema e host in minuscolo, senza la barra finale
    del path (come la scheda del bando, contratto §3)."""
    try:
        parti = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    percorso = parti.path[:-1] if parti.path.endswith("/") else parti.path
    return f"{parti.scheme.lower()}://{(parti.netloc or '').lower()}{percorso}?{parti.query}"


def _https(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() == "https"
    except ValueError:
        return False


def _voce_allegato(voce) -> tuple[str, str | None, str | None] | None:
    """(url, etichetta, formato dichiarato) di una voce del jsonb `allegati`."""
    if not isinstance(voce, dict):
        return None
    url = voce.get("url") or voce.get("link")
    if not isinstance(url, str) or not url.strip():
        return None
    etichetta = voce.get("label") or voce.get("etichetta")
    formato_dichiarato = voce.get("tipo") if isinstance(voce.get("tipo"), str) else None
    return url.strip(), etichetta if isinstance(etichetta, str) else None, formato_dichiarato


def seleziona_candidati(
    links: list[LinkDocumento] | None,
    allegati: list | None,
    max_documenti: int,
) -> list[Candidato]:
    """Documenti da scaricare, in ordine di priorità, al massimo
    `max_documenti`. Sempre l'unione delle righe `links` (bando_link; None se
    la lettura è fallita) e delle voci del jsonb `allegati`, senza doppioni
    per URL: a parità di URL vince la riga di `bando_link`, e un'etichetta
    vuota si completa con il `label` del jsonb. Esclusi: URL non https,
    bloccati dalla policy o dalla denylist, formati certamente non PDF;
    `pagina_bando` e `faq` passano solo se dichiarati PDF."""
    if max_documenti <= 0:
        return []
    voci = [v for v in (_voce_allegato(voce) for voce in allegati or []) if v is not None]
    etichette_jsonb: dict[str, str] = {}
    for url, etichetta, _ in voci:
        if etichetta and etichetta.strip():
            etichette_jsonb.setdefault(_chiave_url(url), etichetta)
    grezzi: list[tuple[str, str | None, str | None, str, str | None, str, int | None]] = []
    chiavi_link: set[str] = set()
    for link in links or []:
        chiave = _chiave_url(link.url)
        chiavi_link.add(chiave)
        etichetta = link.etichetta if (link.etichetta or "").strip() else etichette_jsonb.get(chiave)
        grezzi.append(
            (link.url, etichetta, link.dominio, link.tipo, link.content_type, "bando_link",
             link.id)
        )
    for url, etichetta, formato_dichiarato in voci:
        if _chiave_url(url) in chiavi_link:
            continue
        grezzi.append((url, etichetta, None, "allegato", formato_dichiarato, "allegati", None))
    candidati: list[tuple[int, int, int, Candidato]] = []
    for ordine, riga in enumerate(grezzi):
        url, etichetta, dominio, tipo, content_type, origine, link_id = riga
        if not _https(url) or url_negato(url):
            continue
        formato = normalizza_content_type(content_type, url)
        if formato == "pdf":
            incertezza = 0
        elif formato == "altro" and tipo in ("allegato", "atto") and _forse_pdf(content_type, url):
            incertezza = 1
        else:
            continue
        testo = f"{etichetta or ''} {_nome_file(url)}"
        rango = _rango(tipo, testo)
        candidati.append(
            (
                rango,
                incertezza,
                ordine,
                Candidato(
                    url=url,
                    etichetta=_etichetta(etichetta, url),
                    dominio=_dominio(dominio, url),
                    tipo=tipo,
                    formato=formato,
                    origine=origine,  # type: ignore[arg-type]
                    link_id=link_id,
                    priorita=rango,
                ),
            )
        )
    candidati.sort(key=lambda c: (c[0], c[1], c[2]))
    # Deduplica DOPO l'ordinamento: di due righe con lo stesso URL resta
    # quella con la priorità migliore.
    scelti: list[Candidato] = []
    visti: set[str] = set()
    for *_, candidato in candidati:
        chiave = _chiave_url(candidato.url)
        if chiave in visti:
            continue
        visti.add(chiave)
        scelti.append(candidato)
        if len(scelti) == max_documenti:
            break
    return scelti
