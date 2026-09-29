"""Valutazione LOCALE delle regole di partenariato (WP3): la pipeline di
produzione senza il DB primario.

Dal backend: `python -m app.services.partenariato_valutazione --reale --locale
--tetto-cents N [--conferma] [--out FILE] [--solo ID,ID]`.

A differenza di `--reale` (che usa il primario: claim, righe in
`bando_partenariato`, registro di spesa, migration 0034) qui NON c'è nessun
claim, nessuna cache, nessuna riga su un database. Il primario non esiste: le
Settings si costruiscono qui (`impostazioni_locali`) con valori segnaposto
dichiarati, e qualunque lettura della configurazione del primario solleva
`PrimarioVietatoError` (anche `create_primary_client`).

Stessi passi della pipeline di produzione, con le STESSE funzioni di
`partenariato_service`: catalogo in sola lettura con la anon key, selezione dei
documenti, download sicuro, lettura dei PDF nel processo figlio,
pre-classificatore (livello «nessuno» → `nessun_segnale` senza modello, costo
0), input di `partenariato_prompts`, modello / max_tokens / timeout delle
Settings, post-elaborazione con la verifica delle citazioni.

Due fasi:
1. preparazione di TUTTI i bandi scelti (documenti, lettura,
   pre-classificazione, input), senza spesa: dà la riserva esatta di ogni
   bando, e la stima si stampa PRIMA di qualunque chiamata. Senza `--conferma`
   ci si ferma qui: il modello non si crea nemmeno;
2. con `--conferma`, una chiamata al modello alla volta, nell'ordine del
   campione, con la spesa FAIL-CLOSED in memoria (`Spesa`): prima di ogni
   chiamata si riserva il caso peggiore sull'input davvero inviato
   (`stima_input_cents`) e la chiamata si rifiuta se speso + riserve aperte +
   nuova riserva supera il tetto; lì ci si ferma (`tetto_raggiunto`) e i bandi
   rimasti restano `non_valutato`. Dopo la chiamata vale il costo reale
   (usage); su errore con usage noto max(reale, riserva); su timeout o esito
   ignoto la riserva, mai 0. Ci si ferma anche al primo errore NON
   transitorio del provider (un 4xx diverso da 408/409/429, per esempio un
   400 `invalid_request_error`: la richiesta stessa è rifiutata e sugli altri
   bandi fallirebbe allo stesso modo) con `errore_non_transitorio`, e dopo
   `MAX_ERRORI_CONSECUTIVI` errori di fila di qualunque tipo con
   `errori_consecutivi`: i bandi rimasti restano `non_valutato`. Un 4xx non
   transitorio senza usage resta registrato alla riserva (fail-closed; in
   produzione costa 0), ma il risultato riporta
   anche `costo_probabile_cents` 0: la richiesta è rifiutata prima della
   generazione.

Uscita: JSON su stdout o in `--out` (FUORI dal repository: contiene brani dei
documenti nelle citazioni), scritto anche se l'esecuzione si interrompe. Le
metriche di campo contano solo le risposte del sistema, gli errori a parte
(`metriche_locali`, che unisce anche i risultati di più esecuzioni).

Il tetto vale per UNA esecuzione: più esecuzioni (prova, ripresa dopo
un'interruzione) vanno lanciate col tetto residuo.
"""

import asyncio
import base64
import binascii
import json
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings
from app.core.errors import AiNotConfiguredError, AiTimeoutError, AiUpstreamError
from app.services import bandi_service, bando_fonti_service
from app.services import partenariato_service as ps
from app.services.ai_prezzi import CARATTERI_PER_TOKEN, costo_cents
from app.services.partenariato_preclassificatore import preclassifica
from app.services.partenariato_prompts import SYSTEM_PARTENARIATO
from app.services.partenariato_service import non_transitorio, stato_http
from app.services.partenariato_valutazione import (
    OUTPUT_TIPICO_TOKEN,
    _dentro_repo,
    calcola_metriche,
    risultato_da_regole,
)

logger = logging.getLogger("bandofit.partenariati")

# Come le Settings: relativo alla cartella da cui si lancia (backend/).
ENV_FILE = ".env"
# Segnaposto del primario: `.invalid` non si risolve mai (RFC 2606).
PRIMARIO_URL_SEGNAPOSTO = "https://primario-non-usato.invalid"
PRIMARIO_CHIAVE_SEGNAPOSTO = "primario-non-usato"
_CAMPI_PRIMARIO = frozenset({
    "primary_supabase_url",
    "primary_supabase_service_role_key",
    "primary_supabase_jwt_secret",
    "jwt_issuer",
    "jwks_url",
})
MSG_PRIMARIO = "Valutazione locale: il DB primario non si usa, per nessun motivo"

MAX_ERRORI_CONSECUTIVI = 3
NOTA_RICHIESTA_RIFIUTATA = "richiesta rifiutata prima della generazione"


class PrimarioVietatoError(RuntimeError):
    """Un accesso al DB primario durante la valutazione locale."""


class ConfigurazioneLocaleError(Exception):
    """Configurazione mancante o non valida (il messaggio non contiene valori)."""


class ImpostazioniLocali(Settings):
    """Settings della valutazione locale: il primario ha valori segnaposto e
    leggerne la configurazione (URL, chiavi, issuer) solleva subito."""

    def __getattribute__(self, nome: str):
        if nome in _CAMPI_PRIMARIO:
            raise PrimarioVietatoError(MSG_PRIMARIO)
        return super().__getattribute__(nome)


class _CatalogoEnv(BaseSettings):
    """Le variabili del catalogo, dall'ambiente o dal file .env: SECONDARY_*
    oppure i nomi del catalogo (SUPABASE_URL_BANDI, PUBLIC_…_ANON_KEY)."""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    secondary_supabase_url: str = ""
    secondary_supabase_anon_key: str = ""
    supabase_url_bandi: str = ""
    public_supabase_bandi_anon_key: str = ""


def _ruolo_jwt(chiave: str) -> str | None:
    parti = chiave.split(".")
    if len(parti) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parti[1] + "=" * (-len(parti[1]) % 4)))
    except (ValueError, binascii.Error):
        return None
    return payload.get("role") if isinstance(payload, dict) else None


def _chiave_privilegiata(chiave: str) -> bool:
    return chiave.startswith("sb_secret_") or _ruolo_jwt(chiave) == "service_role"


def impostazioni_locali(env_file: str | Path | None = ENV_FILE) -> ImpostazioniLocali:
    """Settings esplicite della valutazione locale: primario segnaposto,
    catalogo dalle variabili SECONDARY_* o, se mancano, SUPABASE_URL_BANDI /
    PUBLIC_SUPABASE_BANDI_ANON_KEY (sola lettura: rifiutata una chiave
    service_role), tutto il resto (ANTHROPIC_API_KEY, PARTENARIATO_*) come
    le Settings dell'app. Nessun valore finisce nei messaggi d'errore."""
    catalogo = _CatalogoEnv(_env_file=env_file)
    url = (catalogo.secondary_supabase_url or catalogo.supabase_url_bandi).strip()
    chiave = (catalogo.secondary_supabase_anon_key or catalogo.public_supabase_bandi_anon_key).strip()
    if not url or not chiave:
        raise ConfigurazioneLocaleError(
            "Servono SECONDARY_SUPABASE_URL e SECONDARY_SUPABASE_ANON_KEY "
            "(oppure SUPABASE_URL_BANDI e PUBLIC_SUPABASE_BANDI_ANON_KEY)"
        )
    if _chiave_privilegiata(chiave):
        raise ConfigurazioneLocaleError(
            "La chiave del catalogo deve essere la anon key (sola lettura), non una service_role"
        )
    try:
        settings = ImpostazioniLocali(
            _env_file=env_file,
            primary_supabase_url=PRIMARIO_URL_SEGNAPOSTO,
            primary_supabase_service_role_key=PRIMARIO_CHIAVE_SEGNAPOSTO,
            primary_supabase_jwt_secret="",
            secondary_supabase_url=url,
            secondary_supabase_anon_key=chiave,
        )
    except ValidationError as exc:
        motivi = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(configurazione)'}: {e['msg']}"
            for e in exc.errors()
        )
        raise ConfigurazioneLocaleError(f"Configurazione non valida — {motivi}") from None
    if not settings.anthropic_api_key.strip():
        raise ConfigurazioneLocaleError(
            "ANTHROPIC_API_KEY mancante: aggiungila in backend/.env (o nell'ambiente)"
        )
    return settings


async def crea_secondario(settings):
    """Client anon del catalogo (sola lettura)."""
    from app.clients.supabase import create_secondary_client

    return await create_secondary_client(settings)


def crea_ai(settings):
    from app.clients.anthropic_ai import AiCheckClient

    return AiCheckClient(settings)


# ------------------------------------------------------------ spesa


@dataclass
class Spesa:
    """Registro di spesa IN MEMORIA, fail-closed, in centesimi di USD."""

    tetto_cents: int
    speso_cents: int = 0  # costi chiusi (reali, o la riserva se ignoti)
    riserve_aperte_cents: int = 0
    chiamate: int = 0

    def prenota(self, riserva_cents: int) -> bool:
        """False = chiamata rifiutata: riserva non positiva o tetto superato."""
        riserva = int(riserva_cents)
        if riserva <= 0 or (
            self.speso_cents + self.riserve_aperte_cents + riserva > self.tetto_cents
        ):
            return False
        self.riserve_aperte_cents += riserva
        self.chiamate += 1
        return True

    def chiudi(self, riserva_cents: int, costo_cents: int) -> None:
        self.riserve_aperte_cents -= int(riserva_cents)
        self.speso_cents += max(int(costo_cents), 0)


# ------------------------------------------------------------ errori del provider
# Stato HTTP e 4xx non transitori: le stesse funzioni della produzione
# (`partenariato_service.stato_http`, `non_transitorio`).


def tipo_errore_provider(exc: BaseException) -> str | None:
    """Il tipo d'errore dichiarato dal provider (`invalid_request_error`,
    `rate_limit_error`...), mai il messaggio: nel risultato restano solo
    stato e tipo."""
    corpo = getattr(exc.__cause__, "body", None)
    errore = corpo.get("error") if isinstance(corpo, dict) else None
    tipo = errore.get("type") if isinstance(errore, dict) else None
    return tipo if isinstance(tipo, str) else None


# ------------------------------------------------------------ pipeline


@dataclass
class BandoValutato:
    voce: dict  # voce del campione (id, gruppo, etichetta)
    slug: str | None = None
    livello: str | None = None  # del pre-classificatore
    # estratta | nessun_segnale | errore | non_valutato; None = attende il modello
    esito: str | None = None
    errore_codice: str | None = None
    fonti: list[dict] = field(default_factory=list)
    ingresso: ps.InputModello | None = None
    riserva_cents: int = 0
    costo_tipico_cents: int = 0
    regole: dict | None = None
    costo_cents: int = 0
    costo_ignoto: bool = False
    # Costo probabile quando differisce da quello registrato (un 4xx non
    # transitorio senza usage: registrata la riserva, probabile 0), con la
    # sua nota.
    costo_probabile_cents: int | None = None
    nota_costo: str | None = None
    # Stato HTTP e tipo dell'errore del provider, se la risposta c'è stata.
    errore_http: int | None = None
    errore_tipo: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latenza_preparazione_s: float = 0.0
    latenza_modello_s: float | None = None

    @property
    def bando_id(self) -> int:
        return self.voce["bando_id"]


async def prepara_bando(secondary, voce: dict, *, settings) -> BandoValutato:
    """La pipeline di produzione fino all'input del modello, senza spesa:
    un errore chiude il bando come `errore` (costo 0) e non ferma gli altri."""
    b = BandoValutato(voce=voce)
    inizio = time.monotonic()
    try:
        b.slug = await ps._slug_da_id(secondary, b.bando_id)
        bando = await bandi_service.fetch_bando_for_ai(secondary, b.slug)
        links = await bando_fonti_service.leggi_link_documenti(secondary, b.bando_id)
        candidati = ps.candidati_documenti(bando, links, settings=settings)
        scaricati = await ps.scarica_documenti(candidati, settings=settings)
        testi = await ps.leggi_documenti(scaricati, settings=settings)
        documenti, b.fonti = ps.documenti_letti(candidati, scaricati, testi)
        mancati = ps.documenti_non_raggiungibili(links, documenti, scaricati, testi)
        del scaricati
        pre = await asyncio.to_thread(preclassifica, ps.sezioni_preclassificatore(bando, documenti))
        b.livello = pre.livello
        b.esito = ps.esito_senza_modello(pre.livello, mancati, forza=False)
        if b.esito == "errore":
            b.errore_codice = "documenti_non_raggiungibili"
        elif b.esito is None:
            b.ingresso = ps.prepara_input(
                bando, documenti, b.fonti, pre.per_sezione, settings=settings
            )
            b.fonti = b.ingresso.fonti
            b.riserva_cents = ps.stima_input_cents(b.ingresso.testo, settings=settings)
            caratteri = len(SYSTEM_PARTENARIATO) + len(b.ingresso.testo) + len(ps._schema_json())
            b.costo_tipico_cents = costo_cents(
                settings.partenariato_ai_model,
                math.ceil(caratteri / CARATTERI_PER_TOKEN),
                OUTPUT_TIPICO_TOKEN,
            )
    except Exception as exc:  # noqa: BLE001 — un bando non blocca il campione
        logger.warning("valutazione locale: preparazione fallita (bando %s, %s)",
                       b.bando_id, type(exc).__name__)
        b.esito, b.ingresso = "errore", None
        b.errore_codice = getattr(exc, "code", None) or "errore_interno"
    b.latenza_preparazione_s = round(time.monotonic() - inizio, 2)
    return b


async def valuta_con_modello(secondary, ai, b: BandoValutato, spesa: Spesa, *, settings) -> bool:
    """Una chiamata al modello per un bando preparato. False = rifiutata dal
    tetto (nessuna chiamata). Il costo si chiude SEMPRE, anche su errore o
    cancellazione: reale, max(reale, riserva) o la riserva se ignoto."""
    riserva = b.riserva_cents
    if b.ingresso is None or not spesa.prenota(riserva):
        return False
    modello = settings.partenariato_ai_model
    usage = None
    costo, ignoto = riserva, True  # finché non si sa altro: mai 0
    inizio = time.monotonic()
    try:
        estrazione, usage = await ps.genera_estrazione(ai, b.ingresso.testo, settings=settings)
        costo, ignoto = costo_cents(modello, usage.input_tokens, usage.output_tokens), False
        b.regole = await ps.regole_da_estrazione(
            secondary, estrazione, b.ingresso.sezioni, b.ingresso.fonti
        )
        b.esito = "estratta"
    except AiTimeoutError:
        b.errore_codice = "timeout"
    except AiUpstreamError as exc:
        usage = exc.usage
        b.errore_http, b.errore_tipo = stato_http(exc), tipo_errore_provider(exc)
        if usage is not None:
            b.errore_codice = "ai_risposta_non_valida"
        elif non_transitorio(b.errore_http):
            b.errore_codice = "ai_richiesta_rifiutata"
        else:
            b.errore_codice = "ai_non_disponibile"
    except AiNotConfiguredError:
        costo, ignoto = 0, False  # rifiutata dal client prima dell'invio
        b.errore_codice = "ai_non_configurata"
    except asyncio.CancelledError:
        b.errore_codice = "interrotta"
        raise
    except Exception as exc:  # noqa: BLE001 — il costo si chiude comunque
        logger.warning("valutazione locale: errore (bando %s, %s)", b.bando_id,
                       type(exc).__name__)
        b.errore_codice = "errore_interno"
    finally:
        if b.esito != "estratta":
            b.esito, b.regole = "errore", None
            if usage is not None:
                # Risposta arrivata e pagata: max(reale, riserva).
                reale = costo_cents(modello, usage.input_tokens, usage.output_tokens)
                costo, ignoto = max(reale, riserva), False
            elif non_transitorio(b.errore_http):
                # Rifiutata prima di generare (4xx non transitorio, come in
                # produzione, dove costa 0): si registra comunque la riserva
                # (fail-closed), il probabile 0 va a parte.
                b.costo_probabile_cents, b.nota_costo = 0, NOTA_RICHIESTA_RIFIUTATA
        b.costo_cents, b.costo_ignoto = costo, ignoto
        b.input_tokens = usage.input_tokens if usage is not None else 0
        b.output_tokens = usage.output_tokens if usage is not None else 0
        b.latenza_modello_s = round(time.monotonic() - inizio, 2)
        b.ingresso = None  # testo dei documenti: non serve più
        spesa.chiudi(riserva, costo)
    return True


# ------------------------------------------------------------ esecuzione


def _costo_probabile(b: BandoValutato) -> int:
    return b.costo_cents if b.costo_probabile_cents is None else b.costo_probabile_cents


def _uscita_bando(b: BandoValutato) -> dict:
    """Una voce del risultato: mai i testi dei documenti (salvo le citazioni
    dentro `regole`)."""
    esito = b.esito or "non_valutato"
    predetto = risultato_da_regole(b.regole, esito)
    etichetta = b.voce.get("etichetta") or {}
    citazione = ((b.regole or {}).get("modalita") or {}).get("citazione")
    return {
        "bando_id": b.bando_id,
        "slug": b.slug,
        "gruppo": b.voce.get("gruppo"),
        "atteso": {k: etichetta.get(k) for k in ("modalita", "partner_min", "partner_max")},
        "esito": esito,
        "errore_codice": b.errore_codice,
        "errore_http": b.errore_http,
        "errore_tipo": b.errore_tipo,
        "livello_preclassificatore": b.livello,
        "modalita": predetto["modalita_dichiarata"],
        "modalita_effettiva": predetto["modalita"],
        "modalita_citazione_verificata": (
            None if b.regole is None
            else bool(citazione.get("verificata")) if isinstance(citazione, dict) else False
        ),
        "partner_min": predetto["partner_min"],
        "partner_max": predetto["partner_max"],
        "quote": predetto["quote"],
        "forme": [f.get("forma") for f in (b.regole or {}).get("forme_ammesse") or []],
        "fonti": [
            {k: f.get(k) for k in ("n", "etichetta", "dominio", "stato", "pagine_totali",
                                   "pagine_incluse", "troncato")}
            for f in b.fonti
        ],
        "riserva_cents": b.riserva_cents,
        "costo_cents": b.costo_cents,
        "costo_ignoto": b.costo_ignoto,
        "costo_probabile_cents": _costo_probabile(b),
        "nota_costo": b.nota_costo,
        "input_tokens": b.input_tokens,
        "output_tokens": b.output_tokens,
        "latenza_s": round(b.latenza_preparazione_s + (b.latenza_modello_s or 0), 2),
        "latenza_preparazione_s": b.latenza_preparazione_s,
        "latenza_modello_s": b.latenza_modello_s,
        "regole": b.regole,
    }


# Le risposte del sistema: solo su queste si misurano i campi.
ESITI_RISPOSTA = ("estratta", "nessun_segnale")


def metriche_locali(campione: list[dict], bandi: list[dict]) -> dict | None:
    """Metriche dalle voci d'uscita (`bandi` del risultato, anche di più
    esecuzioni unite). I `non_valutato` restano fuori. Le metriche di campo
    (modalità, min/max, quote, anche `raggiungibili`) valgono solo sulle
    RISPOSTE del sistema (`estratta`, `nessun_segnale`): un `errore` non ha
    predizioni e il suo None coinciderebbe con un'etichetta «non indicato»,
    gonfiando l'exact match. Gli errori si contano a parte in `errori`; costi,
    latenze e citazioni restano su tutti i valutati (un errore si paga)."""
    voci = {v["bando_id"]: v for v in campione}
    valutati = [b for b in bandi if (b.get("esito") or "non_valutato") != "non_valutato"]
    if not valutati:
        return None
    risultati = {
        b["bando_id"]: {"regole": b.get("regole"), "esito": b["esito"],
                        "cost_cents": b.get("costo_cents"), "latenza_s": b.get("latenza_s")}
        for b in valutati
    }
    risposte = [voci.get(b["bando_id"], {"bando_id": b["bando_id"]})
                for b in valutati if b["esito"] in ESITI_RISPOSTA]
    per_codice: dict[str, int] = {}
    for b in valutati:
        if b["esito"] not in ESITI_RISPOSTA:
            codice = b.get("errore_codice") or "sconosciuto"
            per_codice[codice] = per_codice.get(codice, 0) + 1
    errori = sum(per_codice.values())
    return {
        **calcola_metriche(risposte, risultati),
        "errori": {"n": errori, "tasso": round(errori / len(valutati), 4),
                   "per_codice": per_codice},
    }


# Errori da rifare con gli altri dopo un arresto: il bando interrotto e
# quello la cui richiesta è stata rifiutata (va rifatto dopo la correzione).
_CODICI_DA_RIFARE = frozenset({"interrotta", "ai_richiesta_rifiutata"})
# Dopo un arresto per errori del provider anche gli errori SENZA risposta
# (timeout, provider non disponibile): sono loro ad aver causato l'arresto e
# la correzione (attendere, alzare il timeout) serve proprio a loro. Non
# `ai_risposta_non_valida`: la risposta è arrivata ed è stata pagata.
_ARRESTI_PER_ERRORI = frozenset({"errore_non_transitorio", "errori_consecutivi"})
_CODICI_SENZA_RISPOSTA = frozenset({"timeout", "ai_non_disponibile"})


def da_rifare(risultato: dict) -> list[int]:
    """Gli id da rilanciare con `--solo` dopo un arresto: i `non_valutato`, il
    bando interrotto e quello con la richiesta rifiutata dal provider (la loro
    riserva è già contata come spesa); dopo un arresto per errori anche i
    bandi in errore senza risposta del modello."""
    codici = _CODICI_DA_RIFARE
    if risultato.get("fermato") in _ARRESTI_PER_ERRORI:
        codici = codici | _CODICI_SENZA_RISPOSTA
    return [b["bando_id"] for b in risultato.get("bandi") or []
            if b.get("esito") == "non_valutato" or b.get("errore_codice") in codici]


def unisci_esecuzioni(risultati: list[dict]) -> list[dict]:
    """Le voci dei bandi di più esecuzioni (in ordine di lancio), per
    `metriche_locali`: di ogni bando vale l'ULTIMA esecuzione che l'ha
    valutato."""
    bandi: dict[int, dict] = {}
    for risultato in risultati:
        for b in risultato.get("bandi") or []:
            if b.get("esito") != "non_valutato" or b["bando_id"] not in bandi:
                bandi[b["bando_id"]] = b
    return list(bandi.values())


class ValutazioneLocale:
    """Stato di un'esecuzione: bandi preparati, spesa e motivo dell'arresto."""

    def __init__(self, bandi: list[BandoValutato], spesa: Spesa, settings):
        self.bandi = bandi
        self.spesa = spesa
        self.settings = settings
        self.fermato: str | None = None
        self.completata = False

    @property
    def stima(self) -> dict:
        """Stima della preparazione (vale anche dopo le chiamate): la
        riserva c'è solo per i bandi che richiedono il modello."""
        con_modello = [b for b in self.bandi if b.riserva_cents > 0]
        return {
            "bandi": len(self.bandi),
            "con_modello": len(con_modello),
            "costo_tipico_totale_cents": sum(b.costo_tipico_cents for b in con_modello),
            "costo_max_totale_cents": sum(b.riserva_cents for b in con_modello),
        }

    async def esegui(self, secondary, ai, *, avanzamento=None) -> None:
        """Una chiamata alla volta. Ci si ferma al primo rifiuto del tetto
        (quel bando resta `non_valutato`), al primo errore non transitorio
        del provider e dopo `MAX_ERRORI_CONSECUTIVI` errori di fila (quel
        bando resta `errore`); i bandi successivi restano `non_valutato`."""
        in_attesa = [b for b in self.bandi if b.esito is None]
        consecutivi = 0
        for i, b in enumerate(in_attesa, start=1):
            if self.fermato is not None:
                b.esito = "non_valutato"
            elif not await valuta_con_modello(
                secondary, ai, b, self.spesa, settings=self.settings
            ):
                self.fermato, b.esito = "tetto_raggiunto", "non_valutato"
            else:
                consecutivi = consecutivi + 1 if b.esito == "errore" else 0
                if non_transitorio(b.errore_http):
                    self.fermato = "errore_non_transitorio"
                elif consecutivi >= MAX_ERRORI_CONSECUTIVI:
                    self.fermato = "errori_consecutivi"
            if avanzamento:
                avanzamento(i, len(in_attesa), b, self.spesa)
        self.completata = True

    def risultato(self) -> dict:
        esiti: dict[str, int] = {}
        for b in self.bandi:
            esiti[b.esito or "non_valutato"] = esiti.get(b.esito or "non_valutato", 0) + 1
        uscite = [_uscita_bando(b) for b in self.bandi]
        return {
            "modalita": "locale",
            "modello": self.settings.partenariato_ai_model,
            "tetto_cents": self.spesa.tetto_cents,
            "fermato": self.fermato or (None if self.completata else "interrotta"),
            "stima": self.stima,
            "spesa": {
                "speso_cents": self.spesa.speso_cents,
                "riserve_aperte_cents": self.spesa.riserve_aperte_cents,
                "chiamate": self.spesa.chiamate,
                # Solo informativo: il tetto residuo si calcola su speso_cents.
                "speso_probabile_cents": self.spesa.speso_cents - sum(
                    b.costo_cents - _costo_probabile(b) for b in self.bandi),
            },
            "esiti": esiti,
            "bandi": uscite,
            # Un `non_valutato` non è un errore del sistema; gli errori a parte.
            "metriche": metriche_locali([b.voce for b in self.bandi], uscite),
        }


# ------------------------------------------------------------ CLI


def filtra_campione(campione: list[dict], solo: str | None) -> list[dict]:
    """`--solo ID,ID`: le voci del campione con quegli id, nel suo ordine."""
    if solo is None:
        return list(campione)
    try:
        ids = {int(p) for p in solo.split(",") if p.strip()}
    except ValueError:
        raise ValueError("--solo vuole id numerici separati da virgole") from None
    if not ids:
        raise ValueError("--solo vuole almeno un id")
    mancanti = sorted(ids - {v["bando_id"] for v in campione})
    if mancanti:
        raise ValueError(f"--solo: id non presenti nel campione: {mancanti}")
    return [v for v in campione if v["bando_id"] in ids]


def controlla_out(out: str | None) -> str | None:
    """Motivo per cui `--out` non va bene, None se va bene. Si controlla
    PRIMA di spendere: un risultato pagato non deve andare perso."""
    if not out:
        return None
    percorso = Path(out).expanduser()
    if _dentro_repo(percorso):
        return "--out deve stare fuori dal repository (il risultato contiene brani dei documenti)"
    if percorso.exists():
        return "--out esiste già: scegli un altro file (un risultato non si sovrascrive)"
    if not percorso.parent.is_dir():
        return "--out: la cartella di destinazione non esiste"
    return None


def _err(testo: str) -> None:
    print(testo, file=sys.stderr, flush=True)


def _scrivi(risultato: dict, out: str | None) -> None:
    testo = json.dumps(risultato, ensure_ascii=False, indent=1, default=str)
    if out:
        try:
            Path(out).expanduser().write_text(testo, encoding="utf-8")
            return
        except OSError as exc:
            _err(f"--out non scrivibile ({type(exc).__name__}): il risultato va su stdout")
    print(testo)


def _stampa_avanzamento(i: int, n: int, b: BandoValutato, spesa: Spesa) -> None:
    costo = f", {b.costo_cents} cent" if b.latenza_modello_s is not None else ""
    if b.nota_costo:
        costo += f" (probabile {_costo_probabile(b)}: {b.nota_costo})"
    if b.errore_http is not None:
        costo += f" [HTTP {' '.join(str(p) for p in (b.errore_http, b.errore_tipo) if p)}]"
    _err(f"[{i}/{n}] bando {b.bando_id}: {b.esito}{costo} "
         f"(speso {spesa.speso_cents}/{spesa.tetto_cents} cent)")


async def esegui_cli(
    campione: list[dict],
    *,
    tetto_cents: int,
    conferma: bool,
    out: str | None,
    solo: str | None,
) -> int:
    """`--reale --locale`: controlli (uscita, filtro, configurazione e chiave)
    PRIMA di qualunque download, preparazione e stima, poi le chiamate."""
    motivo = controlla_out(out)
    if motivo:
        _err(motivo)
        return 2
    try:
        scelti = filtra_campione(campione, solo)
        settings = impostazioni_locali(ENV_FILE)
    except (ValueError, ConfigurazioneLocaleError) as exc:
        _err(str(exc))
        return 2
    _err(
        f"Valutazione locale: {len(scelti)} bandi, modello {settings.partenariato_ai_model}, "
        f"max_tokens {settings.partenariato_ai_max_tokens}, "
        f"timeout {settings.partenariato_ai_timeout_seconds:g} s. Nessuna scrittura su DB."
    )
    secondary = await crea_secondario(settings)
    bandi: list[BandoValutato] = []
    for i, voce in enumerate(scelti, start=1):
        b = await prepara_bando(secondary, voce, settings=settings)
        stato = (
            f"{b.esito} ({b.errore_codice})" if b.errore_codice
            else b.esito or f"serve il modello, riserva {b.riserva_cents} cent"
        )
        _err(f"[{i}/{len(scelti)}] bando {b.bando_id}: livello {b.livello}, {stato}")
        bandi.append(b)

    valutazione = ValutazioneLocale(bandi, Spesa(tetto_cents=tetto_cents), settings)
    stima = valutazione.stima
    _err(
        f"Stima: {stima['costo_tipico_totale_cents']} cent tipici, "
        f"{stima['costo_max_totale_cents']} cent al massimo "
        f"({stima['con_modello']} bandi con il modello su {stima['bandi']}); "
        f"tetto {tetto_cents} cent."
    )
    if stima["costo_max_totale_cents"] > tetto_cents:
        _err("Il tetto può fermare la valutazione prima della fine: "
             "i bandi rimasti risulteranno non_valutato.")
    if not conferma:
        _err("Nessuna spesa: aggiungi --conferma per eseguire.")
        return 0

    ai = crea_ai(settings) if stima["con_modello"] else None
    if ai is not None and not ai.enabled:
        _err("API Anthropic non configurata")
        return 2
    try:
        await valutazione.esegui(secondary, ai, avanzamento=_stampa_avanzamento)
    finally:
        # Anche su interruzione: quanto già pagato non va perso.
        risultato = valutazione.risultato()
        _scrivi(risultato, out)
        if valutazione.fermato in ("errore_non_transitorio", "errori_consecutivi"):
            _err(f"Valutazione fermata ({valutazione.fermato}): i bandi rimasti sono "
                 "non_valutato; da rifare dopo la correzione: "
                 f"--solo {','.join(map(str, da_rifare(risultato)))}")
        probabile = risultato["spesa"]["speso_probabile_cents"]
        _err(f"Speso {valutazione.spesa.speso_cents} cent su un tetto di {tetto_cents}"
             + (f" (probabile {probabile})." if probabile != valutazione.spesa.speso_cents
                else "."))
        if ai is not None:
            await ai.aclose()
    return 0
