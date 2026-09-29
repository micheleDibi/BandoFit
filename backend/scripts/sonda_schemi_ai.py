"""Sonda degli schemi di output AI del modulo partenariati: l'API li accetta?

Perché: la valutazione reale del WP3 è fallita su tutte le chiamate con HTTP 400
`invalid_request_error` «The compiled grammar is too large…». Lo schema dell'output
strutturato non compilava nella grammatica dell'API, e la documentazione non dà
limiti numerici: l'unica verifica affidabile è chiedere all'API, al minimo costo.

Uso (da `backend/`, con la chiave SOLO nell'ambiente della shell):

    .venv/bin/python -m scripts.sonda_schemi_ai                 # dimensioni, zero chiamate
    ANTHROPIC_API_KEY=... .venv/bin/python -m scripts.sonda_schemi_ai --conferma
    # opzioni: --model M, --solo estrazione,testi, --max-tokens 64, --timeout 60

Per ciascuno schema del modulo (estrazione WP3, bozza del profilo WP4, posizioni e
testi della call WP5), importato dal codice così la sonda prova sempre la versione
corrente, con `--conferma` invia UNA richiesta minima con lo stesso percorso della
produzione, nessun retry: per WP4/WP5 `AiCheckClient.genera` (output strutturato
strict: `output_config.format` con lo schema trasformato dall'SDK), per l'estrazione
WP3 `AiCheckClient.estrai_con_strumento` (strumento forzato NON strict con lo stesso
schema, nome, descrizione e convalida della produzione: nessuna grammatica). System
«Rispondi con un JSON conforme allo schema con valori vuoti», user «prova»,
`max_tokens` 64. Per schema stampa quale modo usa. Esiti:
- RIFIUTATO: HTTP 400 con «grammar» nel messaggio (lo schema non compila);
- ACCETTATO: l'API ha risposto, anche troncata da `max_tokens` o non conforme;
- ERRORE: qualunque altro errore (stato HTTP, tipo e messaggio del provider);
- IGNOTO: timeout, esito e addebito ignoti.
Per schema stampa esito, byte dello schema inviato, struttura (oggetti, proprietà,
anyOf, enum, $ref, $defs), token e costo (`ai_prezzi`).

Spesa: tetto interno di `TETTO_CENTS` centesimi di USD, fail-closed. Prima di ogni
chiamata si riserva il caso peggiore (schema contato due volte, perché l'API aggiunge
al prompt la descrizione del formato o dello strumento, più un margine) e se non ci sta ci si ferma.
Dopo la chiamata vale il costo dell'usage, o la riserva se l'usage manca (un 4xx non
transitorio, cioè diverso da 408/409/429, riporta anche il costo probabile 0: rifiutato
prima della generazione, come in produzione). Ci si ferma
anche su 401/403/404: chiave o modello sbagliati, le altre sonde fallirebbero uguali.

La chiave si legge SOLO da `ANTHROPIC_API_KEY` nell'ambiente e non si stampa mai:
nessuna Settings completa, nessun DB. Il modello: `--model`, altrimenti
`PARTENARIATO_AI_MODEL` risolto come le Settings (ambiente, poi `.env`, poi il default
di `core/config.py`), senza leggere nessun altro campo.

Codici d'uscita: 0 senza `--conferma` o tutti ACCETTATO; 1 se almeno uno schema non
è ACCETTATO o non è stato provato; 2 per argomenti o chiave mancanti.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import anthropic
from pydantic import BaseModel, TypeAdapter

# Lanciata come file (`python scripts/sonda_schemi_ai.py`) serve `backend/` nel path;
# i moduli dell'app si importano nelle funzioni, dopo questa riga.
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

SYSTEM_SONDA = "Rispondi con un JSON conforme allo schema con valori vuoti"
MESSAGGIO_SONDA = "prova"
MAX_TOKENS_DEFAULT = 64
MAX_TOKENS_MASSIMO = 1024  # è una sonda: mai una generazione vera
TIMEOUT_DEFAULT_S = 60.0
TETTO_CENTS = 20
# Il prompt di formato che l'API aggiunge per l'output strutturato non si vede
# da qui: lo schema si conta due volte e si aggiunge un centesimo.
MARGINE_RISERVA_CENTS = 1
# Chiave o modello sbagliati: le altre sonde fallirebbero allo stesso modo.
STATI_GLOBALI = frozenset({401, 403, 404})
MAX_MESSAGGIO = 300

ACCETTATO, RIFIUTATO, ERRORE, IGNOTO, NON_PROVATO = (
    "ACCETTATO", "RIFIUTATO", "ERRORE", "IGNOTO", "NON_PROVATO")
NOTA_RIFIUTATA = "richiesta rifiutata prima della generazione"


@dataclass(frozen=True)
class SchemaAi:
    nome: str  # per --solo
    servizio: str
    modello: type[BaseModel]
    # Argomenti di `estrai_con_strumento` (nome_strumento, descrizione,
    # convalida) per gli schemi inviati come strumento non strict; None =
    # output strutturato strict (`genera`).
    strumento: dict | None = None

    @property
    def modo(self) -> str:
        return ("strumento forzato non strict" if self.strumento is not None
                else "output strutturato strict")


def schemi_del_modulo() -> list[SchemaAi]:
    """Gli schemi di output che i servizi del modulo passano al client AI
    (`genera` o, per l'estrazione WP3, `estrai_con_strumento`), importati dal
    codice (sempre la versione corrente)."""
    from app.schemas.partenariato import PartenariatoEstrazione, convalida_tollerante
    from app.schemas.partner_profile import BozzaProfiloAi
    from app.services.partenariato_prompts import (
        DESCRIZIONE_STRUMENTO_ESTRAZIONE,
        STRUMENTO_ESTRAZIONE,
    )
    from app.services.partner_call_prompts import BozzaTestiCall, PropostaPosizioni

    strumento_wp3 = {"nome_strumento": STRUMENTO_ESTRAZIONE,
                     "descrizione": DESCRIZIONE_STRUMENTO_ESTRAZIONE,
                     "convalida": convalida_tollerante}
    return [
        SchemaAi("estrazione", "WP3 regole di partenariato per bando", PartenariatoEstrazione,
                 strumento_wp3),
        SchemaAi("bozza_profilo", "WP4 bozza AI del profilo partner", BozzaProfiloAi),
        SchemaAi("posizioni", "WP5 proposta delle posizioni della call", PropostaPosizioni),
        SchemaAi("testi", "WP5 bozza dei testi della call", BozzaTestiCall),
    ]


def schema_inviato(modello: type[BaseModel]) -> dict:
    """Lo schema come lo costruisce `AiCheckClient.genera` per `output_config.format`
    (lo stesso di `input_schema` in `estrai_con_strumento`)."""
    return anthropic.transform_schema(TypeAdapter(modello).json_schema())


def byte_schema(schema: dict) -> int:
    return len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))


def struttura(schema: dict) -> dict[str, int]:
    """Conteggi che pesano sulla grammatica: oggetti e proprietà, anyOf (e
    quanti sono nullable), enum e loro valori, $ref e $defs."""
    conta = dict.fromkeys(("oggetti", "proprieta", "anyOf", "rami_anyOf", "nullable",
                           "enum", "valori_enum", "array", "ref"), 0)
    conta["defs"] = len(schema.get("$defs") or {})

    def visita(nodo) -> None:
        if isinstance(nodo, list):
            for v in nodo:
                visita(v)
            return
        if not isinstance(nodo, dict):
            return
        if isinstance(nodo.get("properties"), dict):
            conta["oggetti"] += 1
            conta["proprieta"] += len(nodo["properties"])
        rami = nodo.get("anyOf")
        if isinstance(rami, list):
            conta["anyOf"] += 1
            conta["rami_anyOf"] += len(rami)
            if any(isinstance(r, dict) and r.get("type") == "null" for r in rami):
                conta["nullable"] += 1
        if isinstance(nodo.get("enum"), list):
            conta["enum"] += 1
            conta["valori_enum"] += len(nodo["enum"])
        if nodo.get("type") == "array":
            conta["array"] += 1
        if isinstance(nodo.get("$ref"), str):
            conta["ref"] += 1
        for v in nodo.values():
            visita(v)

    visita(schema)
    return conta


def riserva_cents(modello_ai: str, byte: int, max_tokens: int, extra: int = 0) -> int:
    """`extra`: caratteri inviati oltre allo schema (nome e descrizione dello strumento)."""
    from app.services.ai_prezzi import stima_cents

    caratteri = len(SYSTEM_SONDA) + len(MESSAGGIO_SONDA) + 2 * byte + extra
    return stima_cents(modello_ai, caratteri, max_tokens) + MARGINE_RISERVA_CENTS


@dataclass
class EsitoSonda:
    voce: SchemaAi
    byte: int
    struttura: dict[str, int]
    riserva_cents: int
    esito: str = NON_PROVATO
    dettaglio: str = ""
    stato_http: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    costo_cents: int = 0  # contato sul tetto (fail-closed)
    costo_probabile_cents: int | None = None  # solo se diverso da costo_cents
    chiamata: bool = False


def prepara(voci: list[SchemaAi], modello_ai: str, max_tokens: int) -> list[EsitoSonda]:
    esiti = []
    for voce in voci:
        schema = schema_inviato(voce.modello)
        byte = byte_schema(schema)
        extra = (len(voce.strumento["nome_strumento"]) + len(voce.strumento["descrizione"])
                 if voce.strumento is not None else 0)
        esiti.append(EsitoSonda(voce, byte, struttura(schema),
                                riserva_cents(modello_ai, byte, max_tokens, extra)))
    return esiti


def _errore_provider(exc: BaseException) -> tuple[int | None, str | None, str]:
    """Stato HTTP, tipo e messaggio dell'errore dell'SDK dietro un
    `AiUpstreamError` (sollevato `from` quello). Mai la chiave: il provider
    non la ripete nei messaggi."""
    causa = exc.__cause__
    stato = getattr(causa, "status_code", None)
    corpo = getattr(causa, "body", None)
    errore = corpo.get("error") if isinstance(corpo, dict) else None
    tipo = errore.get("type") if isinstance(errore, dict) else None
    messaggio = errore.get("message") if isinstance(errore, dict) else None
    if not isinstance(messaggio, str):
        messaggio = getattr(causa, "message", None) or (type(causa).__name__ if causa else "")
    return (stato if isinstance(stato, int) else None,
            tipo if isinstance(tipo, str) else None, str(messaggio))


async def sonda(ai, e: EsitoSonda, *, modello_ai: str, max_tokens: int,
                timeout: float) -> None:
    """UNA richiesta minima per lo schema di `e`, classificata e costata."""
    from app.core.errors import AiTimeoutError, AiUpstreamError
    from app.services.ai_prezzi import costo_cents
    from app.services.partenariato_service import non_transitorio

    usage = None
    e.chiamata = True
    try:
        if e.voce.strumento is not None:
            _, usage = await ai.estrai_con_strumento(
                SYSTEM_SONDA, MESSAGGIO_SONDA, e.voce.modello, **e.voce.strumento,
                model=modello_ai, max_tokens=max_tokens, timeout=timeout)
        else:
            _, usage = await ai.genera(SYSTEM_SONDA, MESSAGGIO_SONDA, e.voce.modello,
                                       model=modello_ai, max_tokens=max_tokens, timeout=timeout)
        e.esito, e.dettaglio = ACCETTATO, "risposta conforme allo schema"
    except AiUpstreamError as exc:
        usage = exc.usage
        if usage is not None:
            e.esito = ACCETTATO
            e.dettaglio = "risposta arrivata ma non conforme o troncata da max_tokens"
        else:
            e.stato_http, tipo, messaggio = _errore_provider(exc)
            grammatica = e.stato_http == 400 and "grammar" in messaggio.lower()
            e.esito = RIFIUTATO if grammatica else ERRORE
            e.dettaglio = (
                f"HTTP {e.stato_http} {tipo or ''}: {messaggio[:MAX_MESSAGGIO]}"
                if e.stato_http is not None
                else f"nessuna risposta HTTP ({messaggio[:MAX_MESSAGGIO]})"
            )
    except AiTimeoutError:
        e.esito, e.dettaglio = IGNOTO, "timeout: esito e addebito ignoti"
    except Exception as exc:  # noqa: BLE001 — la sonda continua, il costo si conta
        e.esito, e.dettaglio = ERRORE, f"errore locale {type(exc).__name__}"
    if usage is not None:
        e.input_tokens, e.output_tokens = usage.input_tokens, usage.output_tokens
        e.costo_cents = costo_cents(modello_ai, usage.input_tokens, usage.output_tokens)
    else:
        e.costo_cents = e.riserva_cents  # fail-closed: mai 0 senza usage
        if non_transitorio(e.stato_http):
            # 4xx non transitorio: rifiutata prima della generazione (in
            # produzione costa 0), come nella valutazione locale.
            e.costo_probabile_cents = 0


async def esegui_sonde(ai, esiti: list[EsitoSonda], *, modello_ai: str, max_tokens: int,
                       timeout: float, tetto_cents: int = TETTO_CENTS,
                       stampa: Callable[[EsitoSonda], None] | None = None) -> str | None:
    """Una sonda alla volta entro il tetto. Ritorna il motivo dell'arresto
    (None = tutte provate); le sonde non provate restano NON_PROVATO."""
    speso, fermato = 0, None
    for e in esiti:
        if fermato is None and speso + e.riserva_cents > tetto_cents:
            fermato = f"tetto di {tetto_cents} cent: speso {speso}, riserva {e.riserva_cents}"
        if fermato is not None:
            e.dettaglio = f"non provato ({fermato})"
        else:
            await sonda(ai, e, modello_ai=modello_ai, max_tokens=max_tokens, timeout=timeout)
            speso += e.costo_cents
            if e.stato_http in STATI_GLOBALI:
                fermato = f"HTTP {e.stato_http}: chiave o modello da controllare"
        if stampa:
            stampa(e)
    return fermato


# ------------------------------------------------------------ CLI


def modello_dalle_settings() -> str:
    """`PARTENARIATO_AI_MODEL` come lo risolvono le Settings (ambiente, `.env`,
    default del codice), senza costruire le Settings né leggere altri campi."""
    from pydantic_settings import BaseSettings, SettingsConfigDict

    from app.core.config import Settings

    class _SoloModello(BaseSettings):
        model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                          extra="ignore")
        partenariato_ai_model: str = Settings.model_fields["partenariato_ai_model"].default

    return _SoloModello().partenariato_ai_model


def crea_client(api_key: str, modello_ai: str, timeout: float):
    """Il client dell'app (`max_retries=0`), con le sole impostazioni che usa."""
    from app.clients.anthropic_ai import AiCheckClient

    return AiCheckClient(SimpleNamespace(anthropic_api_key=api_key, ai_check_model=modello_ai,
                                         ai_check_timeout_seconds=timeout))


def _descrivi(e: EsitoSonda) -> None:
    s = e.struttura
    print(f"[{e.voce.nome}] {e.voce.servizio} — {e.voce.modello.__name__}")
    print(f"  modo: {e.voce.modo}")
    print(f"  schema inviato: {e.byte} byte; oggetti {s['oggetti']}, proprietà "
          f"{s['proprieta']}, anyOf {s['anyOf']} (nullable {s['nullable']}, rami "
          f"{s['rami_anyOf']}), enum {s['enum']} (valori {s['valori_enum']}), array "
          f"{s['array']}, $ref {s['ref']}, $defs {s['defs']}; riserva {e.riserva_cents} cent")


def _stampa_esito(e: EsitoSonda) -> None:
    _descrivi(e)
    print(f"  esito: {e.esito}" + (f" — {e.dettaglio}" if e.dettaglio else ""))
    if not e.chiamata:
        return
    token = (f"{e.input_tokens} in / {e.output_tokens} out" if e.input_tokens is not None
             else "nessun usage")
    probabile = (f" (probabile {e.costo_probabile_cents}: {NOTA_RIFIUTATA})"
                 if e.costo_probabile_cents is not None else "")
    print(f"  token: {token}; costo contato {e.costo_cents} cent{probabile}", flush=True)


def _argomenti(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conferma", action="store_true",
                        help="invia davvero le richieste (senza: solo le dimensioni)")
    parser.add_argument("--model", help="modello da provare (default: quello delle Settings)")
    parser.add_argument("--solo", help="nomi degli schemi separati da virgole")
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS_DEFAULT)
    parser.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT_S)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _argomenti(argv)
    if not 1 <= args.max_tokens <= MAX_TOKENS_MASSIMO:
        print(f"--max-tokens deve stare tra 1 e {MAX_TOKENS_MASSIMO}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("--timeout deve essere positivo", file=sys.stderr)
        return 2
    voci = schemi_del_modulo()
    if args.solo is not None:
        nomi = [n.strip() for n in args.solo.split(",") if n.strip()]
        ignoti = sorted(set(nomi) - {v.nome for v in voci})
        if not nomi or ignoti:
            print(f"--solo: schemi validi {[v.nome for v in voci]}", file=sys.stderr)
            return 2
        voci = [v for v in voci if v.nome in nomi]
    modello_ai = args.model or modello_dalle_settings()
    esiti = prepara(voci, modello_ai, args.max_tokens)
    print(f"Sonda degli schemi AI: modello {modello_ai}, max_tokens {args.max_tokens}, "
          f"timeout {args.timeout:g} s, tetto {TETTO_CENTS} cent.")
    if not args.conferma:
        for e in esiti:
            _descrivi(e)
        print("Nessuna chiamata: aggiungi --conferma (con ANTHROPIC_API_KEY nell'ambiente).")
        return 0
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY mancante nell'ambiente (il .env non si legge per la chiave)",
              file=sys.stderr)
        return 2

    async def esegui() -> str | None:
        ai = crea_client(api_key, modello_ai, args.timeout)
        try:
            return await esegui_sonde(ai, esiti, modello_ai=modello_ai,
                                      max_tokens=args.max_tokens, timeout=args.timeout,
                                      tetto_cents=TETTO_CENTS, stampa=_stampa_esito)
        finally:
            await ai.aclose()

    fermato = asyncio.run(esegui())
    speso = sum(e.costo_cents for e in esiti)
    probabile = sum(e.costo_cents if e.costo_probabile_cents is None
                    else e.costo_probabile_cents for e in esiti)
    print(f"Speso {speso} cent (fail-closed) su un tetto di {TETTO_CENTS}, probabile "
          f"{probabile}; chiamate {sum(e.chiamata for e in esiti)}"
          + (f"; fermata: {fermato}." if fermato else "."))
    print("Riepilogo: " + "; ".join(f"{e.voce.nome} {e.esito}" for e in esiti))
    return 0 if all(e.esito == ACCETTATO for e in esiti) else 1


if __name__ == "__main__":
    sys.exit(main())
