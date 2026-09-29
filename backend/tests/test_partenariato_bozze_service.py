"""Bozze AI dei documenti del partenariato (WP10, docs/partenariati.md W4,
T6-T8, Q7): servizio vero sul primario finto del WP8 esteso con le RPC della
0042 (`FakePrimaryWP10`, stesse guardie e detail; le guardie vere, i lock e
il conteggio mensile li verifica `tests/db/test_migration_0042.py`). MAI
Anthropic: il client AI è finto e registra ciò che riceverebbe il modello.

Verifica:
- input a WHITELIST: nel messaggio inviato nessun nome, P.IVA, email, id,
  codice pubblico di nessuna azienda (nemmeno con la rivelazione simmetrica
  tra aziende verificate), nessun valore di bilancio, budget esatto,
  dettaglio riservato, testo della call o persona; il nome della PROPRIA
  azienda solo con il flag esplicito; segnaposto stabili per ruolo e
  ordine d'ingresso, uguali per chiunque li chieda; il messaggio nasce solo
  dallo snapshot salvato;
- post-processing: segnaposto ammessi (anche in un'altra grafia) che
  sopravvivono all'anonimizzazione, quelli non previsti sostituiti,
  contatti e identificativi tolti, lunghezze, nessuna sezione → risposta non
  valida; disclaimer fisso fuori dal contenuto generato;
- costi e `record_usage` su OGNI ramo (successo, timeout, risposta non
  valida, rete, 4xx non transitorio a costo 0, 429 transitorio, modello non
  chiamato, guasto dopo il modello, superata), chiusura non riuscita lasciata
  al failsafe, `CancelledError` durante la chiamata;
- failsafe in lettura e nello scheduler; errori della prenotazione con i
  messaggi delle bozze; chi avvia e chi legge."""

import asyncio
import copy
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import anthropic
import httpx
import pytest

from app.clients.anthropic_ai import AiUsage
from app.core.errors import (
    AiNotConfiguredError,
    AiTimeoutError,
    AiUpstreamError,
    AppError,
    ForbiddenError,
    NotFoundError,
)
from app.schemas.partenariato_bozze import DISCLAIMER, BozzaAvviaIn
from app.services import partenariati_scheduler as sched
from app.services import partenariato_bozze_service as svc
from app.services import partenariato_collegamenti
from app.services import partenariato_consorzio_service as consorzio
from app.services.ai_prezzi import costo_cents
from app.services.partenariato_anonimato import (
    anonimizza,
    identificativi_azienda,
    trova_rilievi,
)
from app.services.partenariato_bozze_prompts import (
    BOZZE_PROMPT_VERSION,
    DA_COMPLETARE,
    BozzaDocumentoAi,
    SezioneBozzaAi,
    build_messaggio,
    costruisci_input,
    membri_con_segnaposto,
    segnaposto_ammessi,
    system_prompt,
)
from tests.fixtures.partenariati import esempio_guida as g
from tests.test_partenariato_candidature_service import (  # noqa: F401 — fixture
    FM_X,
    MEMBRO_X,
    FakeQueryWP7,
    attiva,
    errore,
    fixture_fondo,
    utente,
)
from tests.test_partenariato_consorzio_service import (
    EMAIL_MEMBRO_X,
    NUMERI_X,
    NUMERI_Y,
    FakePrimaryWP8,
    consorzio_xy,
)
from tests.test_partenariato_indice import (  # noqa: F401 — fixture autouse
    EMAIL,
    PIVA,
    RAGIONE,
    ambiente_wp6,
    carica_guida,
    secondario_guida,
)

MODELLO = "claude-sonnet-5"
TIPI = ("lettera_intenti", "nda", "term_sheet")
CALL = g.CALL_GUIDA_ID
TITOLO_CALL = "Cerchiamo un organismo di ricerca per la prototipazione"
# Dati che non devono MAI entrare nel messaggio al modello (oltre a nomi,
# P.IVA, email e id delle aziende, controllati da `vietati`).
VIETATI_SEMPRE = ("CANARY-RISERVATO", "3100000", "3.100.000", "Descrizione sintetica",
                  TITOLO_CALL, "Mario", "Rossi", "RSSMRA80A01H501U", "@", "company_profile_id",
                  "family_parent_id", EMAIL_MEMBRO_X, *NUMERI_X, *NUMERI_Y)


def _ora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(minuti_fa: float = 0) -> str:
    return (_ora() - timedelta(minutes=minuti_fa)).isoformat()


def _ts(valore) -> datetime:
    return datetime.fromisoformat(str(valore))


def vietati(*nomi: str) -> list[str]:
    """Identificativi delle aziende `nomi` (anche la ragione sociale in
    maiuscolo, come la denominazione del registro)."""
    valori: list[str] = []
    for nome in nomi:
        valori += [g.COMPANY[nome], g.OWNER[nome], PIVA[nome], RAGIONE[nome],
                   RAGIONE[nome].upper(), g.CODICE_PUBBLICO[nome], EMAIL[nome]]
    return valori


def assenti(testo: str, valori) -> None:
    for valore in valori:
        assert valore not in testo, valore


# ------------------------------------------------------------ primario finto


class FakePrimaryWP10(FakePrimaryWP8):
    """Il primario finto del WP8 con le RPC della 0042 (stesse guardie e
    detail): prenotazione (partecipazione, limite mensile per owner, una
    pending per azienda × call × tipo, budget del gruppo `altri`), chiusura
    atomica e failsafe, con il registro unico della spesa e le righe
    `timeout_unknown`. `limiti_bozze` = bozze al mese per owner (default 3,
    come Smart; None = illimitate, 0 = non incluse)."""

    def __init__(self):
        super().__init__()
        self.limiti_bozze: dict[str, int | None] = {}
        self.esecuzioni: dict[str, dict] = {}
        self.tabelle.setdefault("partner_bozze_documento", [])
        self.tabelle.setdefault("api_usage_events", [])

    # -- aiuti
    @property
    def usage(self) -> list[dict]:
        return self.tabelle["api_usage_events"]

    def bozze(self, **filtri) -> list[dict]:
        return self.righe("partner_bozze_documento", **filtri)

    def spesa_altri(self) -> int:
        return sum(e["costo_riservato_cents"] if e["cost_cents"] is None else e["cost_cents"]
                   for e in self.esecuzioni.values())

    def esecuzioni_owner(self, owner) -> int:
        """Il conteggio per owner di fn_partenariati_ai_prenota (0034) sul
        servizio delle bozze: non contano, senza LLM, gli errori a costo 0 (il
        registro finto ha solo esecuzioni di oggi)."""
        return sum(
            1 for e in self.esecuzioni.values()
            if e["owner"] == owner and e["servizio"] == "partner_bozza"
            and not (not e["llm_eseguito"] and e["stato"] in ("errore", "interrotta")
                     and e["cost_cents"] == 0)
        )

    def bozze_usate(self, owner) -> int:
        """fn_partner_bozze_usate: mese solare Europe/Rome, pool dell'owner."""
        roma = ZoneInfo("Europe/Rome")
        inizio = _ora().astimezone(roma).replace(day=1, hour=0, minute=0, second=0,
                                                 microsecond=0)
        return sum(
            1 for b in self.bozze(family_parent_id=owner)
            if _ts(b["created_at"]) >= inizio
            and (b["stato"] in ("pending", "ready") or b["llm_eseguito"]
                 or (b["cost_cents"] != 0 and b["errore"] != "ai_non_disponibile"))
        )

    def _chiudi_esecuzione(self, eid, stato, cost, tin, tout, model, errore_codice):
        e = self.esecuzioni.get(eid)
        if not e or e["stato"] != "in_corso":
            return
        e.update(stato=stato, cost_cents=cost, input_tokens=tin or 0, output_tokens=tout or 0,
                 model=model or e["model"], errore_codice=errore_codice,
                 llm_eseguito=bool((cost or 0) > 0 or (tin or 0) > 0 or (tout or 0) > 0))

    def _esecuzione_interrotta(self, eid) -> bool:
        """fn_partner_bozza_esecuzione_interrotta."""
        e = self.esecuzioni.get(eid)
        if not e or e["stato"] != "in_corso":
            return False
        self.usage.append({
            "user_id": e["richiedente"], "family_parent_id": e["owner"],
            "provider": "anthropic", "service": "partner_bozza", "outcome": "timeout_unknown",
            "cost_cents": e["costo_riservato_cents"],
            "request_meta": {"company_profile_id": e["company"], "bando_id": e["bando_id"],
                             "esecuzione_id": eid, "esito": "interrotta", "failsafe": True},
        })
        self._chiudi_esecuzione(eid, "interrotta", None, 0, 0, None, "interrotta")
        return True

    # -- RPC della 0042
    def _fn_partner_bozza_prenota(self, p):
        inp = p.get("p_input")
        if (not p.get("p_owner") or not p.get("p_company") or not p.get("p_call")
                or p.get("p_tipo") not in TIPI or not isinstance(inp, dict)
                or len(json.dumps(inp).encode()) > 65536
                or p.get("p_costo_riservato_cents") is None
                or p["p_costo_riservato_cents"] < 0
                or (p.get("p_prompt_version") is not None and p["p_prompt_version"] < 1)):
            raise errore("parametri_non_validi")
        if p.get("p_richiedente") != p["p_owner"]:
            raise errore("attore_non_titolare")
        if not self.righe("profiles", id=p["p_owner"]):
            raise errore("owner_not_found")
        azienda = self._viva(p["p_company"])
        if azienda is None or azienda["parent_id"] != p["p_owner"]:
            raise errore("company_not_found")
        call = next(iter(self.righe("partner_calls", id=p["p_call"])), None)
        if call is None:
            raise errore("call_non_trovata")
        if not (call["company_profile_id"] == p["p_company"]
                and call["family_parent_id"] == p["p_owner"]):
            if call["stato"] == "sospesa_moderazione" or call.get("sospesa_at"):
                raise errore("call_non_trovata")
            if not any(m["stato"] != "uscito" for m in self.righe(
                    "partner_call_membri", partner_call_id=call["id"],
                    company_profile_id=p["p_company"])):
                raise errore("call_non_trovata")
        limite = self.limiti_bozze.get(p["p_owner"], 3)
        if limite is not None and limite <= 0:
            raise errore("funzione_non_inclusa")
        pending = next(iter(self.bozze(company_profile_id=p["p_company"],
                                       partner_call_id=call["id"], tipo=p["p_tipo"],
                                       stato="pending")), None)
        if pending is not None and _ts(pending["avviata_at"]) > _ora() - timedelta(minutes=10):
            raise errore("bozza_in_corso")
        if limite is not None and self.bozze_usate(p["p_owner"]) >= limite:
            raise errore("bozze_esaurite")
        if pending is not None:
            pending.update(stato="error", errore="interrotta", ready_at=_iso())
            self._esecuzione_interrotta(pending["esecuzione_id"])
        limite_owner = p.get("p_limite_owner")
        if limite_owner is not None and self.esecuzioni_owner(p["p_owner"]) >= max(
            limite_owner, 0
        ):
            raise errore("ai_limite_owner")
        budget = p["p_budget_cents"]
        if budget is None or budget <= 0 or (
            self.spesa_altri() + p["p_costo_riservato_cents"] > budget
        ):
            raise errore("ai_budget_esaurito")
        eid = str(uuid.uuid4())
        self.esecuzioni[eid] = {
            "id": eid, "servizio": "partner_bozza", "gruppo": "altri", "origine": "utente",
            "company": p["p_company"], "owner": p["p_owner"], "bando_id": call["bando_id"],
            "richiedente": p["p_richiedente"], "stato": "in_corso",
            "costo_riservato_cents": p["p_costo_riservato_cents"], "cost_cents": None,
            "input_tokens": 0, "output_tokens": 0, "model": None, "errore_codice": None,
            "llm_eseguito": False, "avviata_at": _iso(),
        }
        bozza = {
            "id": str(uuid.uuid4()), "partner_call_id": call["id"],
            "company_profile_id": p["p_company"], "family_parent_id": p["p_owner"],
            "richiesta_da_user_id": p["p_richiedente"], "tipo": p["p_tipo"],
            "stato": "pending", "input_snapshot": copy.deepcopy(inp), "contenuto": None,
            "errore": None, "esecuzione_id": eid, "avviata_at": _iso(),
            "llm_eseguito": False, "model": None, "prompt_version": p.get("p_prompt_version"),
            "input_tokens": 0, "output_tokens": 0, "cost_cents": None,
            "created_at": _iso(), "ready_at": None,
        }
        self.tabelle["partner_bozze_documento"].append(bozza)
        return {"bozza_id": bozza["id"], "esecuzione_id": eid}

    def _fn_partner_bozza_concludi(self, p):
        if (not p.get("p_bozza") or not p.get("p_esecuzione_id")
                or p.get("p_bozza_stato") not in ("ready", "error")
                or (p["p_bozza_stato"] == "ready" and not isinstance(p.get("p_contenuto"), dict))
                or (p["p_bozza_stato"] == "error"
                    and not 1 <= len(p.get("p_bozza_errore") or "") <= 200)
                or p.get("p_stato") not in ("conclusa", "errore", "timeout", "interrotta")
                or any((p.get(k) or 0) < 0 for k in ("p_cost_cents", "p_input_tokens",
                                                     "p_output_tokens"))):
            raise errore("parametri_non_validi")
        bozza = next((b for b in self.bozze(id=p["p_bozza"])
                      if b["esecuzione_id"] == p["p_esecuzione_id"] and b["stato"] == "pending"),
                     None)
        if bozza is not None:
            pronta = p["p_bozza_stato"] == "ready"
            bozza.update(
                stato=p["p_bozza_stato"],
                contenuto=copy.deepcopy(p["p_contenuto"]) if pronta else None,
                errore=None if pronta else p["p_bozza_errore"],
                cost_cents=p["p_cost_cents"], input_tokens=p["p_input_tokens"] or 0,
                output_tokens=p["p_output_tokens"] or 0, model=p["p_model"] or bozza["model"],
                llm_eseguito=bool((p["p_cost_cents"] or 0) > 0 or (p["p_input_tokens"] or 0) > 0
                                  or (p["p_output_tokens"] or 0) > 0),
                ready_at=_iso(),
            )
        e = self.esecuzioni.get(p["p_esecuzione_id"])
        chiusa = bool(e and e["stato"] == "in_corso" and e["servizio"] == "partner_bozza")
        if chiusa:
            self._chiudi_esecuzione(p["p_esecuzione_id"], p["p_stato"], p["p_cost_cents"],
                                    p["p_input_tokens"], p["p_output_tokens"], p["p_model"],
                                    p["p_errore"])
        return {"bozza_scritta": bozza is not None, "esecuzione_chiusa": chiusa}

    def _fn_partner_bozza_chiudi_stale(self, p):
        minuti = p.get("p_minuti")
        soglia = _ora() - timedelta(minutes=max(10 if minuti is None else minuti, 1))
        n = 0
        for b in self.bozze(stato="pending"):
            if _ts(b["avviata_at"]) <= soglia:
                b.update(stato="error", errore="interrotta", ready_at=_iso())
                self._esecuzione_interrotta(b["esecuzione_id"])
                n += 1
        referenziate = {b["esecuzione_id"] for b in self.bozze(stato="pending")}
        for eid, e in list(self.esecuzioni.items()):
            if (e["stato"] == "in_corso" and _ts(e["avviata_at"]) <= soglia
                    and eid not in referenziate and self._esecuzione_interrotta(eid)):
                n += 1
        return n


async def scenario_wp10() -> tuple[FakePrimaryWP10, object]:
    """L'esempio guida sul primario del WP10 (come `scenario_wp8`: membro di
    X con visibilità, collegamenti calcolati, backfill dei membri: X nel
    consorzio della call della guida)."""
    db = carica_guida(FakePrimaryWP10())
    db.tabelle["profiles"].append({"id": MEMBRO_X, "email": EMAIL_MEMBRO_X, "is_active": True})
    db.tabelle.setdefault("family_members", []).append({
        "id": FM_X, "parent_id": g.OWNER["X"], "member_id": MEMBRO_X, "status": "active",
        "denominazione": "Giulia del gruppo X"})
    db.tabelle.setdefault("family_member_company_access", []).append(
        {"family_member_id": FM_X, "company_profile_id": g.COMPANY["X"]})
    assert (await partenariato_collegamenti.backfill(db))["errori"] == 0
    assert db.backfill_membri() == 3
    db.ops.clear()
    db.rpcs.clear()
    return db, secondario_guida()


async def scenario_xy(fondo) -> tuple[FakePrimaryWP10, object]:
    """Y accettata; X capofila al 70%, Y partner al 30%."""
    db, sec = await scenario_wp10()
    await consorzio_xy(db, sec)
    return db, sec


# ------------------------------------------------------------ AI finta


def bozza_modello(**modifiche) -> BozzaDocumentoAi:
    dati = {
        "titolo": "Accordo di riservatezza tra i partner",
        "sezioni": [
            SezioneBozzaAi(titolo="Parti", testo="Tra [Capofila] e [Partner 1], di seguito "
                                                 "«le Parti».\n\n\nLe Parti premettono."),
            SezioneBozzaAi(titolo="Obblighi", testo="Le Parti mantengono riservate le "
                                                    "informazioni scambiate."),
            SezioneBozzaAi(titolo="Firme", testo="[Luogo], [Data]\n[Firma]"),
        ],
        "note_per_l_utente": ["Completa [Data] e [Luogo] prima di inviarlo."],
    }
    dati.update(modifiche)
    return BozzaDocumentoAi(**dati)


class FakeAi:
    """Al posto di `AiCheckClient`: registra ciò che il modello riceverebbe."""

    def __init__(self, *, errore: BaseException | None = None, risposta=None,
                 enabled: bool = True, usage: AiUsage | None = None,
                 attesa: asyncio.Event | None = None):
        self.enabled = enabled
        self.errore = errore
        self.risposta = risposta
        self.usage = usage or AiUsage(input_tokens=3_000, output_tokens=1_000)
        self.attesa = attesa
        self.chiamate: list[dict] = []

    async def genera(self, system, user_message, output_format, *, model=None,
                     max_tokens=None, timeout=None):
        self.chiamate.append({"system": system, "testo": user_message, "schema": output_format,
                              "model": model, "max_tokens": max_tokens, "timeout": timeout})
        if self.attesa is not None:
            await self.attesa.wait()
        if self.errore is not None:
            raise self.errore
        return (self.risposta if self.risposta is not None else bozza_modello()), self.usage


def errore_http(stato: int) -> AiUpstreamError:
    """`AiUpstreamError` sollevato dal client da un errore HTTP del provider
    (senza usage: nessuna risposta utilizzabile)."""
    richiesta = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    corpo = {"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}
    causa = anthropic.APIStatusError("errore", response=httpx.Response(stato, request=richiesta),
                                     body=corpo)
    exc = AiUpstreamError()
    exc.__cause__ = causa
    return exc


def errore_rete() -> AiUpstreamError:
    exc = AiUpstreamError()
    exc.__cause__ = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return exc


# ------------------------------------------------------------ fixture


@pytest.fixture(autouse=True)
def ambiente_wp10(monkeypatch):
    """Le Settings del WP10 (oltre a quelle di `ambiente_wp6`, autouse)."""
    for chiave, valore in {
        "PARTENARIATO_AI_MODEL": MODELLO,
        "PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI": "200",
        "PARTNER_BOZZE_DOCUMENTO_MAX_TOKENS": "8000",
        "PARTNER_BOZZE_DOCUMENTO_TIMEOUT_SECONDS": "150",
        "PARTNER_BOZZE_DOCUMENTO_STALE_MINUTI": "10",
        "PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO": "10",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(name="lavori")
def fixture_lavori(monkeypatch):
    """I job in background raccolti (il test li esegue quando vuole)."""
    coda: list = []
    monkeypatch.setattr(svc, "_spawn", coda.append)
    yield coda
    for coro in coda:
        coro.close()


async def avvia(db, sec, ai, lavori, nome="X", tipo="nda", **k):
    """Avvia una bozza e restituisce (risposta 202, coroutine del job)."""
    out = await svc.avvia(db, sec, ai, attiva(nome, **k.pop("active", {})), utente(nome), CALL,
                          tipo, **k)
    return out, lavori.pop()


# ------------------------------------------------------------ input a whitelist


class TestInput:
    async def test_messaggio_senza_dati_vietati(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        ai = FakeAi()
        out, job = await avvia(db, sec, ai, lavori)
        assert out.stato == "pending" and out.includi_nome_azienda is False
        assert out.disclaimer == DISCLAIMER
        job.close()
        [p] = db.chiamate("fn_partner_bozza_prenota")
        riga = db.una("partner_bozze_documento", id=str(out.id))
        # il messaggio nasce SOLO dallo snapshot salvato
        messaggio = build_messaggio(p["p_input"])
        assert riga["input_snapshot"] == p["p_input"]
        assert p["p_prompt_version"] == BOZZE_PROMPT_VERSION
        assert p["p_limite_owner"] == 10  # tetto giornaliero del titolare
        testo = messaggio + json.dumps(p["p_input"], ensure_ascii=False)
        assenti(testo, [*vietati("X", "Y", "T", "Z", "W"), *VIETATI_SEMPRE])
        # e contiene la whitelist
        assert "Accordo di riservatezza (NDA)" in messaggio
        assert "Bando sintetico di ricerca e sviluppo" in messaggio
        assert "Programma: Horizon Europe" in messaggio
        assert "Associazione temporanea di scopo (ATS)" in messaggio
        assert "Ciascun partner risponde per la propria parte" in messaggio
        assert ("[Capofila] — ruolo: capofila; quota del budget: 70%; è l'azienda di chi "
                "chiede la bozza") in messaggio
        assert "[Partner 1] — ruolo: partner; quota del budget: 30%" in messaggio
        assert set(p["p_input"]) == {"versione", "tipo", "bando", "forma", "membri",
                                     "includi_nome_azienda", "nome_azienda"}
        assert p["p_input"]["nome_azienda"] is None

    async def test_messaggio_inviato_al_modello(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        ai = FakeAi()
        _, job = await avvia(db, sec, ai, lavori, tipo="term_sheet")
        assert await job == "pronta"
        [chiamata] = ai.chiamate
        [p] = db.chiamate("fn_partner_bozza_prenota")
        assert chiamata["testo"] == build_messaggio(p["p_input"])
        assert chiamata["system"] == system_prompt("term_sheet")
        assert chiamata["schema"] is BozzaDocumentoAi
        assert (chiamata["model"], chiamata["max_tokens"], chiamata["timeout"]) == (
            MODELLO, 8000, 150.0)
        assenti(chiamata["system"] + chiamata["testo"],
                [*vietati("X", "Y", "T"), *VIETATI_SEMPRE])

    async def test_nome_della_propria_azienda_solo_col_flag(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out, job = await avvia(db, sec, FakeAi(), lavori, includi_nome_azienda=True)
        job.close()
        assert out.includi_nome_azienda is True
        [p] = db.chiamate("fn_partner_bozza_prenota")
        messaggio = build_messaggio(p["p_input"])
        # la denominazione del registro (dati coerenti, T5), di X soltanto
        assert (f"Nome da usare per l'azienda di chi chiede la bozza: {RAGIONE['X'].upper()}"
                in messaggio)
        assenti(messaggio.replace(RAGIONE["X"].upper(), ""),
                [*vietati("X", "Y", "T"), *VIETATI_SEMPRE])
        # anche la controparte, col flag, vede solo il proprio nome
        out, job = await avvia(db, sec, FakeAi(), lavori, nome="Y", includi_nome_azienda=True)
        job.close()
        messaggio = build_messaggio(db.chiamate("fn_partner_bozza_prenota")[-1]["p_input"])
        assert RAGIONE["Y"].upper() in messaggio
        assenti(messaggio.replace(RAGIONE["Y"].upper(), ""),
                [*vietati("X", "Y", "T"), *VIETATI_SEMPRE])

    async def test_rivelazione_simmetrica_non_cambia_l_input(self, fondo, lavori):
        """Con X e Y verificate dall'admin (rivelazione simmetrica) e la call
        nominativa i nomi sono già noti alle parti, ma la bozza non li
        inserisce: l'input a whitelist è identico."""
        async def input_di(db, sec) -> dict[str, dict]:
            snapshot = {}
            for nome in ("X", "Y"):
                _, job = await avvia(db, sec, FakeAi(), lavori, nome=nome)
                job.close()
                snapshot[nome] = db.chiamate("fn_partner_bozza_prenota")[-1]["p_input"]
            return snapshot

        anonimi = await input_di(*await scenario_xy(fondo))
        # X e Y verificate PRIMA dell'accettazione: la rivelazione è davvero
        # accesa (audit scritto dalla RPC di decisione), la call nominativa
        db, sec = await scenario_wp10()
        db.verifica_identita(g.COMPANY["X"])
        db.verifica_identita(g.COMPANY["Y"])
        db.una("partner_calls", id=CALL)["anonima"] = False
        await consorzio_xy(db, sec)
        azioni = [a["action"] for a in db.tabelle["audit_log"]]
        assert azioni.count("partenariato.identita_rivelata") == 1
        rivelati = await input_di(db, sec)
        assert rivelati == anonimi
        for snapshot in rivelati.values():
            assenti(build_messaggio(snapshot) + json.dumps(snapshot, ensure_ascii=False),
                    [*vietati("X", "Y"), *VIETATI_SEMPRE])

    async def test_segnaposto_stabili_per_chiunque_li_chieda(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        for nome in ("X", "Y"):
            _, job = await avvia(db, sec, FakeAi(), lavori, nome=nome)
            job.close()
        px, py = (c["p_input"]["membri"] for c in db.chiamate("fn_partner_bozza_prenota"))
        assert [(m["segnaposto"], m["ruolo"], m["quota_percentuale"]) for m in px] == [
            ("[Capofila]", "capofila", "70.00"), ("[Partner 1]", "partner", "30.00")]
        assert [m["segnaposto"] for m in py] == [m["segnaposto"] for m in px]
        assert [m["tua_azienda"] for m in px] == [True, False]
        assert [m["tua_azienda"] for m in py] == [False, True]

    def test_segnaposto_per_ordine_d_ingresso(self):
        call = {"company_profile_id": "c-x", "ruolo_creatore": "cerco_capofila",
                "quota_creatore_pct": "40"}
        righe = [
            {"id": "3", "company_profile_id": "c-z", "ruolo": "partner", "stato": "confermato",
             "quota_percentuale": 20, "created_at": "2026-10-03"},
            {"id": "4", "company_profile_id": None, "ruolo": "associated_partner",
             "stato": "proposto", "quota_percentuale": None, "created_at": "2026-10-04"},
            {"id": "2", "company_profile_id": "c-y", "ruolo": "capofila", "stato": "confermato",
             "quota_percentuale": "40.5", "created_at": "2026-10-02"},
            {"id": "5", "company_profile_id": "c-u", "ruolo": "partner", "stato": "uscito",
             "quota_percentuale": 10, "created_at": "2026-10-05"},
            {"id": "1", "company_profile_id": "c-x", "ruolo": "partner", "stato": "confermato",
             "quota_percentuale": 40, "created_at": "2026-10-06"},
        ]
        for ordine in (righe, list(reversed(righe))):
            parti = membri_con_segnaposto(call, ordine, "c-z")
            assert [(m["segnaposto"], m["ruolo"], m["quota_percentuale"], m["tua_azienda"])
                    for m in parti] == [
                ("[Partner 1]", "partner", "40.00", False),  # il creatore per primo
                ("[Capofila]", "capofila", "40.50", False),
                ("[Partner 2]", "partner", "20.00", True),
                ("[Partner 3]", "associated_partner", None, False),  # l'uscito no
            ]

    def test_call_senza_consorzio_e_controparte_da_individuare(self):
        call = {"company_profile_id": "c-x", "ruolo_creatore": "capofila",
                "quota_creatore_pct": 60, "bando_titolo": "Bando [con] parentesi",
                "bando_programma_id": 11, "forma_aggregazione_prevista": None}
        snapshot = costruisci_input(tipo="lettera_intenti", call=call, membri=[],
                                    company_id="c-x", programmi={11: "Horizon Europe"},
                                    nome_azienda=None)
        assert [(m["segnaposto"], m["tua_azienda"], m["da_individuare"])
                for m in snapshot["membri"]] == [("[Capofila]", True, False),
                                                 ("[Partner 1]", False, True)]
        messaggio = build_messaggio(snapshot)
        assert "Titolo: Bando (con) parentesi" in messaggio  # nessun blocco imitato
        assert "Forma: non ancora indicata" in messaggio
        assert "[Partner 1] — ruolo: partner; quota del budget: non indicata; partner non " \
               "ancora individuato" in messaggio
        assert segnaposto_ammessi(snapshot) >= {"[Capofila]", "[Partner 1]", "[Data]"}


# ------------------------------------------------------------ post-processing


def ident_x():
    return identificativi_azienda(
        {"ragione_sociale": RAGIONE["X"], "partita_iva": PIVA["X"],
         "sito_web": "www.impresasinteticax.it"},
        {"denominazione": RAGIONE["X"].upper(), "piva_fetched": PIVA["X"]},
        [{"nome": "Mario", "cognome": "Rossi"}],
    )


AMMESSI = segnaposto_ammessi({"membri": [{"segnaposto": "[Capofila]"},
                                         {"segnaposto": "[Partner 1]"},
                                         {"segnaposto": "[Partner 12]"}]})


class TestPostProcessing:
    def test_segnaposto_sopravvivono_all_anonimizzazione(self):
        testo = ("Tra [Capofila], [Partner 1] e [Partner 12], a [Luogo] il [Data], per "
                 "[Importo] e con [Foro competente].")
        assert anonimizza(testo, ident_x()) == (testo, [])
        assert anonimizza(testo, None) == (testo, [])
        assert not any(r.bloccante for r in trova_rilievi(testo, ident_x(), anonima=True))
        out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="Parti",
                                                                   testo=testo)]),
                             tipo="nda", ammessi=AMMESSI, ident=ident_x())
        assert out["sezioni"][0]["testo"] == testo and out["avvisi"] == []

    def test_segnaposto_sopravvivono_anche_a_una_ragione_sociale_comune(self):
        """Una ragione sociale fatta di una parola comune («Partner Srl»,
        «Data Srl») si toglie dal testo, mai dai segnaposto."""
        for ragione in ("Partner Srl", "Data S.r.l.", "Capofila SpA"):
            ident = identificativi_azienda({"ragione_sociale": ragione}, None, None)
            testo = "Tra [Capofila] e [Partner 1], il [Data]: Partner, Data e Capofila."
            out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="Parti",
                                                                       testo=testo)]),
                                 tipo="nda", ammessi=AMMESSI, ident=ident)
            pulito = out["sezioni"][0]["testo"]
            assert pulito.startswith("Tra [Capofila] e [Partner 1], il [Data]:"), ragione
            assert pulito.count("[rimosso]") == 1, (ragione, pulito)

    def test_troncamento_senza_segnaposto_a_meta(self):
        testo = "a" * (svc.MAX_TESTO_SEZIONE - 12) + " [Partner 12] fine"
        # il taglio all'ultimo spazio cade DENTRO il segnaposto: senza la
        # guardia resterebbe «…[Partner…»
        assert svc._tronca(testo, svc.MAX_TESTO_SEZIONE).endswith("[Partner…")
        out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="T", testo=testo)]),
                             tipo="nda", ammessi=AMMESSI, ident=None)
        pulito = out["sezioni"][0]["testo"]
        assert len(pulito) <= svc.MAX_TESTO_SEZIONE and pulito.endswith("a…")
        assert "[" not in pulito

    def test_contatti_e_identificativi_tolti(self):
        testo = (f"Scrivi a info@acme.it o chiama il +39 333 1234567, sito www.acme.it, "
                 f"P.IVA {PIVA['X']}, IBAN IT60X0542811101000000123456. "
                 f"{RAGIONE['X']} è il [Capofila].")
        out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="Parti",
                                                                   testo=testo)]),
                             tipo="nda", ammessi=AMMESSI, ident=ident_x())
        pulito = out["sezioni"][0]["testo"]
        assenti(pulito, ("info@acme.it", "333 1234567", "www.acme.it", PIVA["X"],
                         "IT60X0542811101000000123456", "Impresa Sintetica X"))
        assert "[Capofila]" in pulito and "[rimosso]" in pulito
        assert not any(r.bloccante for r in trova_rilievi(pulito, ident_x(), anonima=True))
        assert any("riferimenti non ammessi" in a for a in out["avvisi"])

    def test_contatti_spezzati_da_un_a_capo_o_dalle_quadre(self):
        """Il controllo dei contatti guarda il testo INTERO con gli spazi già
        compattati: un numero spezzato da un a capo (nel titolo, in una nota,
        in una sezione) e un'email o un URL offuscati con le quadre non
        passano; gli altri a capo e i segnaposto restano."""
        bozza = bozza_modello(
            titolo="NDA tel +39 333\n1234567",
            sezioni=[SezioneBozzaAi(titolo="Contatti", testo=(
                "Chiama il +39 333\n1234567 per info.\n"
                "Scrivi a segreteria [at] progetto-alfa [dot] eu o su www.[Capofila].it\n\n"
                "Firma [Capofila] il [Data]."))],
            note_per_l_utente=["Chiama +39 333\n1234567"],
        )
        out = svc.post_bozza(bozza, tipo="nda", ammessi=AMMESSI, ident=None)
        assert out["titolo"] == "NDA tel [rimosso]"
        assert out["note_per_l_utente"] == ["Chiama [rimosso]"]
        assert out["sezioni"][0]["testo"] == (
            "Chiama il [rimosso] per info.\nScrivi a [rimosso] o su [rimosso]\n\n"
            "Firma [Capofila] il [Data].")
        assenti(json.dumps(out, ensure_ascii=False),
                ("1234567", "progetto-alfa", "www.", DA_COMPLETARE))
        assert "Abbiamo tolto 5 riferimenti non ammessi" in " ".join(out["avvisi"])

    def test_nome_proprio_restano_col_flag_ma_non_i_contatti(self):
        testo = f"{RAGIONE['X']}, email info@acme.it, è il [Capofila]."
        out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="Parti",
                                                                   testo=testo)]),
                             tipo="nda", ammessi=AMMESSI, ident=None)
        pulito = out["sezioni"][0]["testo"]
        assert RAGIONE["X"] in pulito and "info@acme.it" not in pulito

    def test_segnaposto_non_previsti_e_grafie(self):
        testo = ("Tra [partner  1], [CAPOFILA], [Rossi Meccanica S.r.l.] e [Partner 7]; "
                 "firma di [Mario Rossi].")
        out = svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="[Titolo]",
                                                                   testo=testo)]),
                             tipo="nda", ammessi=AMMESSI, ident=None)
        [sezione] = out["sezioni"]
        assert sezione["testo"] == (
            f"Tra [Partner 1], [Capofila], {DA_COMPLETARE} e {DA_COMPLETARE}; firma di "
            f"{DA_COMPLETARE}.")
        assert sezione["titolo"] == DA_COMPLETARE
        assert "4 segnaposto non previsti" in " ".join(out["avvisi"])

    def test_lunghezze_sezioni_vuote_e_note(self):
        sezioni = [SezioneBozzaAi(titolo="Vuota", testo="   "),
                   *[SezioneBozzaAi(titolo=f"S{i}", testo="parola " * 2000) for i in range(20)]]
        out = svc.post_bozza(bozza_modello(titolo="T" * 500, sezioni=sezioni,
                                           note_per_l_utente=["n" * 900, " ", "ok"] * 10),
                             tipo="term_sheet", ammessi=AMMESSI, ident=None)
        assert len(out["sezioni"]) == svc.MAX_SEZIONI
        assert all(len(s["testo"]) <= svc.MAX_TESTO_SEZIONE for s in out["sezioni"])
        assert len(out["titolo"]) <= svc.MAX_TITOLO
        assert len(out["note_per_l_utente"]) == svc.MAX_NOTE
        assert all(len(n) <= svc.MAX_NOTA for n in out["note_per_l_utente"])
        assert "6 sezioni vuote o in eccesso" in " ".join(out["avvisi"])

    def test_paragrafi_compattati(self):
        out = svc.post_bozza(bozza_modello(), tipo="nda", ammessi=AMMESSI, ident=None)
        assert out["sezioni"][0]["testo"] == (
            "Tra [Capofila] e [Partner 1], di seguito «le Parti».\n\nLe Parti premettono.")
        assert out["titolo"] == "Accordo di riservatezza tra i partner"

    def test_nessuna_sezione_utilizzabile(self):
        with pytest.raises(svc.BozzaNonValida):
            svc.post_bozza(bozza_modello(sezioni=[SezioneBozzaAi(titolo="x", testo=" ")]),
                           tipo="nda", ammessi=AMMESSI, ident=None)

    def test_titolo_di_ripiego_e_disclaimer_fuori_dal_contenuto(self):
        out = svc.post_bozza(bozza_modello(titolo=" "), tipo="lettera_intenti",
                             ammessi=AMMESSI, ident=None)
        assert out["titolo"] == "Lettera d'intenti"
        assert DISCLAIMER not in json.dumps(out, ensure_ascii=False)
        assert set(out) == {"titolo", "sezioni", "note_per_l_utente", "avvisi"}


# ------------------------------------------------------------ esecuzione e costi


def riserva(db) -> int:
    return db.chiamate("fn_partner_bozza_prenota")[-1]["p_costo_riservato_cents"]


class TestEsecuzione:
    async def test_pronta(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out, job = await avvia(db, sec, FakeAi(), lavori)
        assert await job == "pronta"
        letta = await svc.stato(db, attiva("X"), utente("X"), CALL, out.id)
        assert letta.stato == "ready" and letta.conclusa_at is not None
        assert letta.titolo == "Accordo di riservatezza tra i partner"
        assert [s.titolo for s in letta.sezioni] == ["Parti", "Obblighi", "Firme"]
        assert letta.note_per_l_utente == ["Completa [Data] e [Luogo] prima di inviarlo."]
        assert letta.disclaimer == DISCLAIMER and letta.errore is None
        costo = costo_cents(MODELLO, 3_000, 1_000)
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("conclusa", costo)
        riga = db.una("partner_bozze_documento", id=str(out.id))
        assert (riga["cost_cents"], riga["llm_eseguito"], riga["model"]) == (costo, True,
                                                                          MODELLO)
        [uso] = db.usage
        assert (uso["service"], uso["outcome"], uso["cost_cents"], uso["provider"]) == (
            "partner_bozza", "success", costo, "anthropic")
        assert uso["request_meta"]["bozza_id"] == str(out.id)
        assert uso["request_meta"]["call_id"] == CALL
        assert uso["request_meta"]["prompt_version"] == BOZZE_PROMPT_VERSION
        # nessuna scrittura in ai_checks (la quota dell'AI-check)
        assert not db.tabelle.get("ai_checks")

    @pytest.mark.parametrize(
        ("errore_ai", "outcome", "costo_atteso", "codice", "conta"),
        [
            (AiTimeoutError(), "timeout_unknown", "riserva", "timeout", True),
            ("usage", "error", "max", "ai_risposta_non_valida", True),
            (errore_rete(), "error", None, "ai_rete", True),
            # errore transitorio del provider senza generazione: costo ignoto
            # per il budget, ma non conta nel limite mensile
            (errore_http(429), "error", None, "ai_non_disponibile", False),
            (errore_http(529), "error", None, "ai_non_disponibile", False),
            (errore_http(500), "error", None, "ai_non_disponibile", False),
            (errore_http(400), "error", 0, "ai_richiesta_rifiutata", False),
            (errore_http(404), "error", 0, "ai_richiesta_rifiutata", False),
            (AiNotConfiguredError(), "error", 0, "errore_interno", False),
        ],
        ids=["timeout", "risposta_non_valida", "rete", "429_transitorio", "529_overloaded",
             "500_provider", "400_rifiutata", "404_rifiutata", "non_chiamato"],
    )
    async def test_costi_su_ogni_ramo(self, fondo, lavori, errore_ai, outcome, costo_atteso,
                                      codice, conta):
        db, sec = await scenario_xy(fondo)
        if errore_ai == "usage":
            errore_ai = AiUpstreamError()
            errore_ai.usage = AiUsage(input_tokens=10, output_tokens=5)
        out, job = await avvia(db, sec, FakeAi(errore=errore_ai), lavori)
        await job
        r = riserva(db)
        attesi = {"riserva": r, "max": max(costo_cents(MODELLO, 10, 5), r), None: None, 0: 0}
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["cost_cents"] == attesi[costo_atteso]
        [uso] = db.usage
        assert uso["outcome"] == outcome
        assert uso["cost_cents"] == (r if costo_atteso is None else attesi[costo_atteso])
        if outcome != "timeout_unknown":
            assert uso["request_meta"]["costo_ignoto"] is (costo_atteso is None)
        riga = db.una("partner_bozze_documento", id=str(out.id))
        assert (riga["stato"], riga["errore"]) == ("error", codice)
        letta = await svc.stato(db, attiva("X"), utente("X"), CALL, out.id)
        assert letta.stato == "error" and letta.errore == svc.messaggio_errore(codice)
        assert letta.sezioni == [] and letta.titolo is None
        # gli errori pagati (o a costo ignoto) contano nel limite, quelli a
        # costo 0 senza modello e gli errori transitori del provider no
        assert db.bozze_usate(g.OWNER["X"]) == (1 if conta else 0)

    async def test_risposta_senza_sezioni_e_pagata(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        vuota = bozza_modello(sezioni=[])
        out, job = await avvia(db, sec, FakeAi(risposta=vuota), lavori)
        assert await job == "errore"
        [uso] = db.usage
        assert uso["cost_cents"] == max(costo_cents(MODELLO, 3_000, 1_000), riserva(db))
        assert uso["request_meta"]["errore"] == "ai_risposta_non_valida"
        riga = db.una("partner_bozze_documento", id=str(out.id))
        assert (riga["errore"], riga["input_tokens"], riga["llm_eseguito"]) == (
            "ai_risposta_non_valida", 3_000, True)

    async def test_guasto_dopo_il_modello(self, fondo, lavori, monkeypatch):
        db, sec = await scenario_xy(fondo)

        def esplode(*a, **k):
            raise RuntimeError("bug")

        monkeypatch.setattr(svc, "post_bozza", esplode)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        assert await job == "errore"
        [uso] = db.usage
        assert uso["cost_cents"] == max(costo_cents(MODELLO, 3_000, 1_000), riserva(db))

    async def test_chiusura_non_riuscita_resta_al_failsafe(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.rpc_guasti["fn_partner_bozza_concludi"] = errore("guasto")
        out, job = await avvia(db, sec, FakeAi(), lavori)
        assert await job == "errore"
        assert db.usage == []
        assert db.una("partner_bozze_documento", id=str(out.id))["stato"] == "pending"

    async def test_superata_durante_la_generazione(self, fondo, lavori):
        """Il failsafe chiude la bozza mentre il modello genera: il risultato
        pagato va perso, la spesa no (una riga sola, chi chiude registra)."""
        db, sec = await scenario_xy(fondo)
        attesa = asyncio.Event()
        out, job = await avvia(db, sec, FakeAi(attesa=attesa), lavori)
        task = asyncio.create_task(job)
        await asyncio.sleep(0)
        db.una("partner_bozze_documento", id=str(out.id)).update(
            stato="error", errore="interrotta", ready_at=_iso())
        attesa.set()
        assert await task == "superata"
        [uso] = db.usage
        assert uso["outcome"] == "error" and uso["request_meta"]["esito"] == "superata"
        assert uso["cost_cents"] == costo_cents(MODELLO, 3_000, 1_000)

    async def test_cancellazione_durante_la_chiamata(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out, job = await avvia(db, sec, FakeAi(attesa=asyncio.Event()), lavori)
        task = asyncio.create_task(job)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", riserva(db))
        riga = db.una("partner_bozze_documento", id=str(out.id))
        assert (riga["stato"], riga["errore"]) == ("error", "interrotta")
        # un passaggio successivo del failsafe non registra di nuovo
        assert db._fn_partner_bozza_chiudi_stale({"p_minuti": 1}) == 0
        assert len(db.usage) == 1

    async def test_cancellazione_durante_il_registro(self, fondo, lavori, monkeypatch):
        """Il task viene cancellato mentre l'insert nel registro consumi della
        chiusura riuscita è in corso: registro già tentato → nessuna seconda
        riga, e la bozza pronta non viene richiusa come interrotta."""
        db, sec = await scenario_xy(fondo)
        attesa = asyncio.Event()
        originale = FakeQueryWP7.execute

        async def execute(self):
            risposta = await originale(self)
            if self.tabella == "api_usage_events" and self.op == "insert":
                await attesa.wait()  # la riga è scritta, la risposta non arriva
            return risposta

        monkeypatch.setattr(FakeQueryWP7, "execute", execute)
        out, job = await avvia(db, sec, FakeAi(), lavori)
        task = asyncio.create_task(job)
        for _ in range(20):
            await asyncio.sleep(0)
        assert len(db.usage) == 1  # il job è fermo dentro l'insert
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("success",
                                                      costo_cents(MODELLO, 3_000, 1_000))
        assert len(db.chiamate("fn_partner_bozza_concludi")) == 1  # nessuna richiusura
        riga = db.una("partner_bozze_documento", id=str(out.id))
        assert (riga["stato"], riga["errore"]) == ("ready", None)

    async def test_cancellazione_prima_dell_avvio_resta_al_failsafe(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out, job = await avvia(db, sec, FakeAi(), lavori)
        task = asyncio.create_task(job)
        task.cancel()  # prima che il job parta
        with pytest.raises(asyncio.CancelledError):
            await task
        # la coroutine non è mai partita: bozza ed esecuzione al failsafe
        assert db.usage == []
        db.una("partner_bozze_documento", id=str(out.id))["avviata_at"] = _iso(11)
        next(iter(db.esecuzioni.values()))["avviata_at"] = _iso(11)
        letta = await svc.stato(db, attiva("X"), utente("X"), CALL, out.id)
        assert letta.errore == svc.messaggio_errore("interrotta")
        [uso] = db.usage
        assert uso["outcome"] == "timeout_unknown" and uso["request_meta"]["failsafe"] is True


# ------------------------------------------------------------ failsafe


class TestFailsafe:
    async def test_bozza_orfana_chiusa_in_lettura(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()
        db.una("partner_bozze_documento", id=str(out.id))["avviata_at"] = _iso(30)
        next(iter(db.esecuzioni.values()))["avviata_at"] = _iso(30)
        lista = await svc.lista(db, attiva("X"), utente("X"), CALL)
        [bozza] = lista.bozze
        assert bozza.stato == "error" and bozza.errore == svc.messaggio_errore("interrotta")
        [uso] = db.usage
        assert uso["outcome"] == "timeout_unknown" and uso["cost_cents"] == riserva(db)
        assert db.chiamate("fn_partner_bozza_chiudi_stale") == [{"p_minuti": 10}]
        # conta nel limite (costo ignoto)
        assert db.bozze_usate(g.OWNER["X"]) == 1

    async def test_bozza_recente_nessuna_rpc(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()
        lista = await svc.lista(db, attiva("X"), utente("X"), CALL)
        assert [b.stato for b in lista.bozze] == ["pending"]
        assert db.chiamate("fn_partner_bozza_chiudi_stale") == []

    async def test_passo_dello_scheduler(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()
        db.bozze()[0]["avviata_at"] = _iso(30)
        next(iter(db.esecuzioni.values()))["avviata_at"] = _iso(30)
        assert await sched.failsafe_bozze(db) == 1
        assert db.chiamate("fn_partner_bozza_chiudi_stale") == [{"p_minuti": 10}]
        assert await sched.failsafe_bozze(db) == 0

    async def test_passo_su_un_primario_minimo(self):
        chiamate = []

        class Db:
            def rpc(self, nome, params):
                chiamate.append((nome, params))
                return SimpleNamespace(execute=lambda: _risposta())

            def table(self, nome):
                raise AssertionError("nessuna tabella")

        async def _risposta():
            return SimpleNamespace(data=4)

        assert await sched.failsafe_bozze(Db()) == 4
        assert chiamate == [("fn_partner_bozza_chiudi_stale", {"p_minuti": 10})]


# ------------------------------------------------------------ prenotazione e accesso


async def codice(coro) -> AppError:
    with pytest.raises(AppError) as info:
        await coro
    return info.value


class TestPrenotazione:
    async def test_messaggi_delle_bozze_per_i_detail_condivisi(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = 0
        e = await codice(avvia(db, sec, FakeAi(), lavori))
        assert (e.status_code, e.code) == (409, "funzione_non_inclusa")
        assert "bozze dei documenti" in e.message
        db.limiti_bozze[g.OWNER["X"]] = None
        _, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()
        e = await codice(avvia(db, sec, FakeAi(), lavori))
        assert (e.status_code, e.code) == (409, "bozza_in_corso")
        assert "bozza di questo documento" in e.message
        # un altro tipo sì
        _, job = await avvia(db, sec, FakeAi(), lavori, tipo="term_sheet")
        job.close()

    async def test_limite_mensile_e_budget(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = 1
        _, job = await avvia(db, sec, FakeAi(), lavori)
        assert await job == "pronta"
        e = await codice(avvia(db, sec, FakeAi(), lavori, tipo="term_sheet"))
        assert (e.status_code, e.code) == (409, "bozze_esaurite")
        db.limiti_bozze[g.OWNER["X"]] = None
        for eid in db.esecuzioni:
            db.esecuzioni[eid]["cost_cents"] = 200
        e = await codice(avvia(db, sec, FakeAi(), lavori, tipo="term_sheet"))
        assert (e.status_code, e.code) == (429, "ai_sospesa_oggi")
        assert e.message == "La generazione automatica è sospesa per oggi: riprova domani"
        [p] = db.chiamate("fn_partner_bozza_prenota")[-1:]
        assert p["p_budget_cents"] == 200

    async def test_tetto_giornaliero_del_titolare(self, fondo, lavori, monkeypatch):
        """Anche con un piano illimitato il titolare ha un tetto di bozze al
        giorno (Settings, passato alla RPC): il budget «altri» è condiviso con
        la bozza del profilo e le proposte della call."""
        from app.core.config import get_settings

        monkeypatch.setenv("PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO", "2")
        get_settings.cache_clear()
        db, sec = await scenario_xy(fondo)
        db.limiti_bozze[g.OWNER["X"]] = None
        for tipo in ("nda", "term_sheet"):
            _, job = await avvia(db, sec, FakeAi(), lavori, tipo=tipo)
            assert await job == "pronta"
        e = await codice(avvia(db, sec, FakeAi(), lavori, tipo="lettera_intenti"))
        assert (e.status_code, e.code) == (429, "ai_limite_giornaliero")
        assert e.message == "Hai raggiunto le bozze di documenti di oggi: riprova domani"
        assert [p["p_limite_owner"] for p in db.chiamate("fn_partner_bozza_prenota")] == [2] * 3
        assert len(db.bozze()) == 2 and len(db.esecuzioni) == 2 and lavori == []
        # Il tetto è del titolare: Y (un altro) prepara la sua.
        _, job = await avvia(db, sec, FakeAi(), lavori, nome="Y")
        job.close()

    async def test_riserva_al_caso_peggiore(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()
        [p] = db.chiamate("fn_partner_bozza_prenota")
        testo = build_messaggio(p["p_input"])
        assert p["p_costo_riservato_cents"] == svc.stima_riserva_cents(system_prompt("nda"),
                                                                       testo)
        # 8000 token di output a 10 $/MTok: almeno 8 centesimi
        assert p["p_costo_riservato_cents"] >= 8

    async def test_ai_non_configurata(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        e = await codice(avvia(db, sec, FakeAi(enabled=False), lavori))
        assert (e.status_code, e.code) == (503, "ai_not_configured")
        assert db.chiamate("fn_partner_bozza_prenota") == []

    async def test_tipo_non_valido(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        e = await codice(avvia(db, sec, FakeAi(), lavori, tipo="contratto"))
        assert e.status_code == 400
        with pytest.raises(ValueError):
            BozzaAvviaIn(tipo="nda", includi_nome_azienda="true")


class TestAccesso:
    async def test_solo_partecipanti(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        for coro in (
            avvia(db, sec, FakeAi(), lavori, nome="T"),
            svc.lista(db, attiva("T"), utente("T"), CALL),
        ):
            with pytest.raises(NotFoundError):
                await coro
        assert db.chiamate("fn_partner_bozza_prenota") == []

    async def test_membro_in_sola_lettura(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        await job
        membro = attiva("X", editable=False)
        utente_membro = {"id": MEMBRO_X, "role": "cliente", "is_active": True}
        with pytest.raises(ForbiddenError):
            await svc.avvia(db, sec, FakeAi(), membro, utente_membro, CALL, "nda")
        lista = await svc.lista(db, membro, utente_membro, CALL)
        assert lista.editable is False and [b.stato for b in lista.bozze] == ["ready"]
        assert (await svc.lista(db, attiva("X"), utente("X"), CALL)).editable is True

    async def test_ogni_azienda_vede_solo_le_proprie(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        out_x, job = await avvia(db, sec, FakeAi(), lavori)
        await job
        assert (await svc.lista(db, attiva("Y"), utente("Y"), CALL)).bozze == []
        with pytest.raises(NotFoundError):
            await svc.stato(db, attiva("Y"), utente("Y"), CALL, out_x.id)
        with pytest.raises(NotFoundError):
            await svc.stato(db, attiva("X"), utente("X"), CALL, "non-un-uuid")

    async def test_membro_uscito_e_call_sospesa(self, fondo, lavori):
        db, sec = await scenario_xy(fondo)
        y = db.membro_di(g.COMPANY["Y"])
        await consorzio.esci(db, sec, attiva("Y"), utente("Y"), CALL, y["id"])
        with pytest.raises(NotFoundError):
            await avvia(db, sec, FakeAi(), lavori, nome="Y")
        db.una("partner_calls", id=CALL).update(stato="sospesa_moderazione", sospesa_at=_iso())
        # il creatore sì (in qualunque stato)
        _, job = await avvia(db, sec, FakeAi(), lavori)
        job.close()

    async def test_la_rpc_ricontrolla_la_partecipazione(self, fondo, lavori):
        """Membro uscito tra la lettura e la prenotazione: la RPC risponde
        `call_non_trovata` → 404."""
        db, sec = await scenario_xy(fondo)
        originale = db._fn_partner_bozza_prenota

        def prenota(p):
            db.membro_di(g.COMPANY["Y"])["stato"] = "uscito"
            return originale(p)

        db._fn_partner_bozza_prenota = prenota
        e = await codice(avvia(db, sec, FakeAi(), lavori, nome="Y"))
        assert (e.status_code, e.code) == (404, "not_found")
        assert db.bozze() == [] and db.esecuzioni == {}


class TestImpostazioni:
    def test_default_di_produzione(self, monkeypatch):
        from app.core.config import Settings

        for chiave in ("PARTNER_BOZZE_DOCUMENTO_MAX_TOKENS",
                       "PARTNER_BOZZE_DOCUMENTO_TIMEOUT_SECONDS",
                       "PARTNER_BOZZE_DOCUMENTO_STALE_MINUTI",
                       "PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO"):
            monkeypatch.delenv(chiave, raising=False)
        s = Settings(_env_file=None, primary_supabase_url="https://dummy.supabase.co",
                     primary_supabase_service_role_key="k",
                     secondary_supabase_url="https://d2.supabase.co",
                     secondary_supabase_anon_key="k")
        assert (s.partner_bozze_documento_max_tokens, s.partner_bozze_documento_timeout_seconds,
                s.partner_bozze_documento_stale_minuti,
                s.partner_bozze_documento_limite_owner_giorno) == (8000, 150.0, 10, 10)
