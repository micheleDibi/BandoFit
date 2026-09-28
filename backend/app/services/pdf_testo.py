"""Testo dei PDF ufficiali, pagina per pagina, in un processo separato (WP3).

Un PDF scaricato da un portale è input NON fidato e pypdf non ha timeout: la
lettura gira in un processo `multiprocessing` con contesto `spawn`, uno per
documento, ucciso con `kill()` allo scadere del tempo. Nel figlio:
- `resource.setrlimit` best-effort (CPU ovunque; memoria solo su Linux, dove
  RLIMIT_AS è affidabile);
- `pypdf.Configuration` con limiti più stretti dei default (stream dichiarati
  e decompressi, alberi delle pagine, outline, XObject) e nessun binario
  esterno (jbig2dec);
- tetti su pagine lette, dimensione del content stream di pagina, caratteri
  per pagina e per documento;
- `decrypt("")` per i PDF cifrati senza password utente, altrimenti `protetto`.
Il figlio restituisce SOLO JSON con `send_bytes` (niente pickle di oggetti) e
questo modulo importa SOLO la libreria standard: il processo spawn lo
reimporta, e non deve trascinarsi dietro `app.core` né altro codice del
progetto. `pypdf` si importa dentro il figlio.

Dopo la lettura: `rimuovi_ripetuti` toglie intestazioni e piè di pagina
ripetuti; una media sotto 200 caratteri per pagina vuol dire scansione
(niente OCR in v1) → `non_leggibile`.
"""

import asyncio
import json
import logging
import math
import multiprocessing
import re
import signal
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("bandofit.partenariati")

StatoTesto = Literal["letto", "letto_parziale", "non_leggibile", "protetto", "corrotto", "timeout"]

MIN_MEDIA_CARATTERI_PAGINA = 200
MAX_CONTENT_STREAM_BYTES = 4_000_000
LETTURE_CONCORRENTI = 2
_RLIMIT_AS_BYTES = 768 * 1024 * 1024
_MAGIC_PDF = b"%PDF-"

# Limiti di pypdf più stretti dei default (75 MB): il file è già ≤ 15 MB e un
# content stream di pagina oltre 4 MB viene saltato comunque.
_CONFIGURAZIONE_PYPDF = {
    "maximum_declared_stream_length": 20_000_000,
    "array_based_stream_maximum_output_length": 20_000_000,
    "jbig2_maximum_output_length": 20_000_000,
    "lzw_maximum_output_length": 20_000_000,
    "run_length_maximum_output_length": 20_000_000,
    "zlib_maximum_output_length": 20_000_000,
    "zlib_maximum_recovery_input_length": 1_000_000,
    "image_maximum_buffer_size": 20_000_000,
    "xmp_maximum_input_length": 1_000_000,
    "xmp_maximum_element_count": 10_000,
    "outline_maximum_entries": 10_000,
    "outline_maximum_depth": 50,
    "page_tree_maximum_entries": 10_000,
    "page_tree_maximum_depth": 50,
    "xform_maximum_invocations_per_extraction": 1_000,
    "jbig2dec_binary": None,
}

_CONTROLLO = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class TestoPdf:
    stato: StatoTesto
    pagine_totali: int = 0
    # (numero di pagina originale, 1-based; testo ripulito), solo pagine non vuote
    pagine: list[tuple[int, str]] = field(default_factory=list)
    caratteri: int = 0


# ------------------------------------------------------------ figlio


def _applica_rlimit(timeout_s: float) -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - non POSIX
        return
    cpu = int(math.ceil(timeout_s)) + 2
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    except (ValueError, OSError):
        pass
    if sys.platform.startswith("linux"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (_RLIMIT_AS_BYTES, _RLIMIT_AS_BYTES))
        except (ValueError, OSError):
            pass


def _estrai(pdf_bytes: bytes, limiti: dict) -> dict:
    """Lettura vera e propria (nel figlio). Restituisce un dict JSON-abile."""
    import io
    import warnings

    import pypdf
    from pypdf import PasswordType

    # I PDF malformati generano molti avvisi di pypdf: nel figlio non servono
    # (l'esito è già nello stato) e intaserebbero i log del container.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    warnings.simplefilter("ignore")
    ammessi = getattr(pypdf.Configuration, "__dataclass_fields__", {})
    pypdf.overwrite_configuration(
        **{k: v for k, v in _CONFIGURAZIONE_PYPDF.items() if k in ammessi}
    )
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes), strict=False)
    except Exception:  # noqa: BLE001
        return {"esito": "corrotto"}
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == PasswordType.NOT_DECRYPTED:
                return {"esito": "protetto"}
        except Exception:  # noqa: BLE001 — algoritmo non supportato, dati rotti
            return {"esito": "protetto"}
    try:
        totali = len(reader.pages)
    except Exception:  # noqa: BLE001
        return {"esito": "corrotto"}
    max_pagine = int(limiti["max_pagine"])
    max_pagina = int(limiti["max_caratteri_pagina"])
    max_doc = int(limiti["max_caratteri_doc"])
    pagine: list[list] = []
    parziale = totali > max_pagine
    tetto_doc = False
    tentate = 0
    errori = 0
    caratteri = 0
    for indice in range(min(totali, max_pagine)):
        tentate += 1
        try:
            pagina = reader.pages[indice]
            contenuto = pagina.get_contents()
            if contenuto is not None and len(contenuto.get_data()) > MAX_CONTENT_STREAM_BYTES:
                parziale = True
                continue
            testo = pagina.extract_text() or ""
        except Exception:  # noqa: BLE001 — LimitReachedError, pagina rotta
            errori += 1
            parziale = True
            continue
        testo = _CONTROLLO.sub("", testo.replace("\r\n", "\n").replace("\r", "\n"))
        if len(testo) > max_pagina:
            testo = testo[:max_pagina]
            parziale = True
        if caratteri + len(testo) > max_doc:
            testo = testo[: max(0, max_doc - caratteri)]
            parziale = tetto_doc = True
            if testo:
                pagine.append([indice + 1, testo])
            caratteri = max_doc
            break
        caratteri += len(testo)
        pagine.append([indice + 1, testo])
    if totali == 0 or (tentate and errori == tentate):
        return {"esito": "corrotto", "pagine_totali": totali}
    return {
        "esito": "ok",
        "pagine_totali": totali,
        "pagine_tentate": tentate,
        "parziale": parziale,
        "tetto_doc": tetto_doc,
        "pagine": pagine,
    }


def _estrai_nel_figlio(conn, pdf_bytes: bytes, limiti: dict) -> None:
    """Punto d'ingresso del processo figlio (top-level: serve allo spawn)."""
    risultato: dict = {"esito": "corrotto"}
    try:
        _applica_rlimit(float(limiti.get("timeout_s") or 30.0))
        risultato = _estrai(pdf_bytes, limiti)
    except BaseException:  # noqa: BLE001 — MemoryError compreso: esito, non crash
        risultato = {"esito": "corrotto"}
    try:
        conn.send_bytes(json.dumps(risultato, ensure_ascii=False).encode("utf-8"))
    except BaseException:  # noqa: BLE001
        pass
    finally:
        conn.close()


# ------------------------------------------------------------ padre


def _esegui_figlio(
    pdf_bytes: bytes, limiti: dict, timeout_s: float, bersaglio: Callable
) -> dict:
    """Avvia il figlio, attende al massimo `timeout_s`, lo uccide se serve.
    Bloccante: gira in un thread (`asyncio.to_thread`)."""
    ctx = multiprocessing.get_context("spawn")
    ricevi, invia = ctx.Pipe(duplex=False)
    processo = ctx.Process(target=bersaglio, args=(invia, pdf_bytes, limiti), daemon=True)
    # Tetto sul messaggio: 6 byte per carattere coprono UTF-8 ed escape JSON.
    massimo = int(limiti["max_caratteri_doc"]) * 6 + int(limiti["max_pagine"]) * 64 + 65_536
    try:
        processo.start()
        invia.close()
        if not ricevi.poll(timeout_s):
            return {"esito": "timeout"}
        try:
            dati = ricevi.recv_bytes(massimo)
        except (EOFError, OSError):
            processo.join(1)
            # Ucciso per il tetto di CPU (SIGXCPU): è un timeout, non un PDF rotto.
            sigxcpu = getattr(signal, "SIGXCPU", None)
            if sigxcpu is not None and processo.exitcode == -sigxcpu:
                return {"esito": "timeout"}
            return {"esito": "corrotto"}
        try:
            risultato = json.loads(dati.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"esito": "corrotto"}
        return risultato if isinstance(risultato, dict) else {"esito": "corrotto"}
    finally:
        ricevi.close()
        invia.close()
        if processo.pid is not None:
            if processo.is_alive():
                processo.kill()
            processo.join(5)


class _Semaforo:
    """asyncio.Semaphore ricreato per event loop."""

    def __init__(self, n: int) -> None:
        self._n = n
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None

    def get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if loop is not self._loop or self._sem is None:
            self._loop = loop
            self._sem = asyncio.Semaphore(self._n)
        return self._sem


_SEMAFORO = _Semaforo(LETTURE_CONCORRENTI)


async def estrai_testo(
    pdf_bytes: bytes,
    *,
    max_pagine: int,
    timeout_s: float,
    max_caratteri_pagina: int = 20_000,
    max_caratteri_doc: int = 400_000,
    _bersaglio: Callable | None = None,
) -> TestoPdf:
    """Testo del PDF per pagina, ripulito. Non solleva mai: ogni esito è uno
    `stato`. `_bersaglio` sostituisce la funzione del figlio SOLO nei test."""
    if not pdf_bytes or not bytes(pdf_bytes[: len(_MAGIC_PDF)]) == _MAGIC_PDF:
        return TestoPdf(stato="corrotto")
    limiti = {
        "max_pagine": max(1, int(max_pagine)),
        "max_caratteri_pagina": max(1, int(max_caratteri_pagina)),
        "max_caratteri_doc": max(1, int(max_caratteri_doc)),
        "timeout_s": float(timeout_s),
    }
    try:
        async with _SEMAFORO.get():
            grezzo = await asyncio.to_thread(
                _esegui_figlio,
                bytes(pdf_bytes),
                limiti,
                float(timeout_s),
                _bersaglio or _estrai_nel_figlio,
            )
    except Exception:  # noqa: BLE001 — avvio del processo fallito
        logger.exception("pdf_partenariato: avvio della lettura non riuscito")
        return TestoPdf(stato="corrotto")
    return _componi(grezzo, limiti)


def _componi(grezzo: dict, limiti: dict) -> TestoPdf:
    esito = grezzo.get("esito")
    totali = grezzo.get("pagine_totali")
    totali = totali if isinstance(totali, int) and totali >= 0 else 0
    if esito in ("protetto", "corrotto", "timeout"):
        return TestoPdf(stato=esito, pagine_totali=totali)
    if esito != "ok":
        return TestoPdf(stato="corrotto", pagine_totali=totali)
    pagine: list[tuple[int, str]] = []
    for voce in grezzo.get("pagine") or []:
        if (
            isinstance(voce, list)
            and len(voce) == 2
            and isinstance(voce[0], int)
            and isinstance(voce[1], str)
            and 1 <= voce[0] <= max(totali, 1)
        ):
            pagine.append((voce[0], voce[1][: limiti["max_caratteri_pagina"]]))
    pagine = [(n, t) for n, t in rimuovi_ripetuti(pagine) if t.strip()]
    tentate = grezzo.get("pagine_tentate")
    tentate = tentate if isinstance(tentate, int) and tentate > 0 else max(len(pagine), 1)
    caratteri = sum(len(t) for _, t in pagine)
    # Raggiunto il tetto di caratteri del documento il testo c'è: la media
    # (con l'ultima pagina tagliata) non dice nulla sulla leggibilità.
    if not grezzo.get("tetto_doc") and caratteri / tentate < MIN_MEDIA_CARATTERI_PAGINA:
        stato: StatoTesto = "non_leggibile"
    elif grezzo.get("parziale"):
        stato = "letto_parziale"
    else:
        stato = "letto"
    return TestoPdf(stato=stato, pagine_totali=totali, pagine=pagine, caratteri=caratteri)


# ------------------------------------------------------------ pulizia

# «Pagina 3 di 40», «Pag. 3/40», «Page 3 of 40», «pag 3»: numerazione esplicita.
# Si applica SOLO a righe corte e già compattate (spazi singoli, niente bordi):
# niente quantificatori di spazi adiacenti, quindi nessun backtracking
# esplosivo su righe ostili fatte di migliaia di spazi (questa pulizia gira
# nel processo principale, fuori dal figlio con timeout).
_NUMERAZIONE = re.compile(
    r"^[-–—]? ?(?:pag(?:ina|\.)?|page|p\.) ?\d{1,4}(?: ?(?:di|of|/|su) ?\d{1,4})? ?[-–—]?$",
    re.IGNORECASE,
)
# «- Pagina 1234 di 1234 -» sono 23 caratteri: oltre, non è una numerazione.
_MAX_NUMERAZIONE = 40
_RIGHE_BORDO = 2
_CIFRE = re.compile(r"\d+")
# Righe che con buona probabilità portano un numero di pagina: per queste il
# confronto ignora le cifre («Decreto 12/2026 - pag. 3» = «… pag. 4»). Per le
# altre il confronto è esatto: «Art. 3» e «Art. 4» in testa a due pagine
# restano entrambe.
_SOLO_NUMERI = re.compile(r"^[\W_]*\d[\W\d_]*$")
_INDIZIO_PAGINA = re.compile(
    r"\bpag(?:ina|\.)?\b|\bpage\b|\bp\.\s*\d|\d\s*(?:/|di|of|su)\s*\d", re.IGNORECASE
)


def _numerazione(riga: str) -> bool:
    """Riga di sola numerazione di pagina. Tempo lineare anche su righe
    ostili: si compatta (una passata), si scarta ciò che è lungo e solo
    allora si applica la regex."""
    compatta = " ".join(riga.split())
    return len(compatta) <= _MAX_NUMERAZIONE and bool(_NUMERAZIONE.match(compatta))


def _chiave_riga(riga: str) -> str:
    norm = " ".join(riga.split()).casefold()
    if _SOLO_NUMERI.match(norm) or (len(norm) <= 120 and _INDIZIO_PAGINA.search(norm)):
        return _CIFRE.sub("#", norm)
    return norm


def rimuovi_ripetuti(pagine: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Toglie intestazioni e piè di pagina (funzione pura).

    Una riga tra le prime o le ultime 2 (non vuote) di una pagina è rimossa se
    la stessa riga compare in quella posizione in almeno metà delle pagine (e
    in almeno 2); per le righe con aria di numerazione il confronto ignora le
    cifre. Le righe di numerazione esplicita
    («Pagina X di Y») si tolgono ovunque. Restituisce una pagina per pagina in
    ingresso, nello stesso ordine."""
    righe_per_pagina: list[list[str]] = []
    bordi_per_pagina: list[set[int]] = []
    conteggio: Counter[str] = Counter()
    for _, testo in pagine:
        righe = (testo or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        non_vuote = [i for i, r in enumerate(righe) if r.strip()]
        bordi = set(non_vuote[:_RIGHE_BORDO]) | set(non_vuote[-_RIGHE_BORDO:])
        righe_per_pagina.append(righe)
        bordi_per_pagina.append(bordi)
        conteggio.update({_chiave_riga(righe[i]) for i in bordi})
    soglia = max(2, math.ceil(len(pagine) / 2))
    ripetute = {k for k, n in conteggio.items() if n >= soglia} if len(pagine) >= 2 else set()
    risultato: list[tuple[int, str]] = []
    for (numero, _), righe, bordi in zip(pagine, righe_per_pagina, bordi_per_pagina, strict=True):
        tenute = [
            riga
            for i, riga in enumerate(righe)
            if not _numerazione(riga)
            and not (i in bordi and _chiave_riga(riga) in ripetute)
        ]
        risultato.append((numero, "\n".join(tenute).strip()))
    return risultato
