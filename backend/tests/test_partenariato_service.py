"""Servizio delle regole di partenariato (WP3): claim → pipeline → concludi,
freschezza e «in corso» senza nuova prenotazione, `riusata` e
`nessun_segnale` senza modello, «Analizza comunque» una volta sola dopo
`nessun_segnale`, heartbeat perso → modello NON chiamato, costi sugli errori
(usage sull'eccezione → costo registrato e budget scalato; timeout → costo
riservato), registro consumi su ogni ramo, nessuna scrittura in `ai_checks`,
limite giornaliero del Gratuito con email verificata.

Il primario è un gemello in memoria delle funzioni SQL della 0034 (stessa
semantica di claim, cooldown, limiti, budget e chiusura); catalogo, download,
lettura dei PDF e modello sono finti. `_spawn` è sostituito: la pipeline si
attende nel test."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.clients.anthropic_ai import AiUsage
from app.core.errors import (
    AiNotConfiguredError,
    AiTimeoutError,
    AiUpstreamError,
    AppError,
    NotFoundError,
)
from app.schemas.partenariato import PartenariatoEstrazione
from app.services import partenariato_service as ps
from app.services.ai_prezzi import costo_cents
from app.services.bando_fonti_service import LinkDocumento, StatoBando
from app.services.download_sicuro import DocumentoScaricato
from app.services.pdf_testo import TestoPdf as _TestoPdf
from app.services.pdf_testo import estrai_testo as estrai_testo_vero

USER = {"id": "a0000000-0000-0000-0000-000000000001", "role": "cliente", "is_active": True}
OWNER = USER["id"]
COMPANY = "c0000000-0000-0000-0000-000000000001"
BANDO_ID = 101
MODELLO = "claude-sonnet-5"

PAGINA_1 = (
    "Art. 3 - Soggetti beneficiari\nPossono presentare domanda le PMI in forma singola o "
    "associata mediante ATS o contratto di rete."
)
PAGINA_2 = (
    "Art. 5 - Partenariato\nIl partenariato è composto da almeno 2 imprese. Ciascun partner "
    "sostiene almeno il 20% delle spese ammissibili."
)
NEUTRA_1 = "Art. 1 - Finalità\nIl bando sostiene gli investimenti in macchinari delle imprese."
NEUTRA_2 = "Art. 2 - Spese\nSono ammissibili le spese per impianti e attrezzature nuove."


def adesso() -> datetime:
    return datetime.now(timezone.utc)


def _errore_rpc(detail: str) -> APIError:
    return APIError({"message": detail, "code": "P0001", "hint": None, "details": detail})


# ------------------------------------------------------------ primario finto


class FakeQuery:
    def __init__(self, db, tabella: str):
        self.db, self.tabella = db, tabella
        self.op, self.payload, self.filtri = "select", None, {}

    def select(self, *args, **kwargs):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, colonna, valore):
        self.filtri[colonna] = valore
        return self

    def in_(self, colonna, valori):
        self.filtri[f"{colonna}__in"] = list(valori)
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args):
        return self

    def limit(self, *args):
        return self

    async def execute(self):
        self.db.ops.append((self.tabella, self.op, self.payload, dict(self.filtri)))
        if self.tabella == "ai_checks":
            raise AssertionError("il modulo partenariati non tocca mai ai_checks")
        if self.op == "insert":
            if self.tabella == "api_usage_events":
                self.db.usage.append(self.payload)
            return SimpleNamespace(data=[self.payload], count=None)
        if self.tabella == "bando_partenariato":
            riga = self.db.righe.get(self.filtri.get("bando_id"))
            dati = [{k: v for k, v in riga.items() if k != "claim_token"}] if riga else []
            return SimpleNamespace(data=dati, count=len(dati))
        if self.tabella == "partenariati_ai_esecuzioni":
            e = self.db.esecuzioni.get(self.filtri.get("id"))
            return SimpleNamespace(data=[e] if e else [], count=None)
        if self.tabella == "user_subscriptions":
            dati = [{"subscription_plans": {"slug": self.db.piano}}] if self.db.piano else []
            return SimpleNamespace(data=dati, count=None)
        return SimpleNamespace(data=[], count=0)


class FakeDb:
    """Gemello in memoria delle RPC della migration 0034."""

    def __init__(self):
        self.righe: dict[int, dict] = {}
        self.esecuzioni: dict[str, dict] = {}
        self.usage: list[dict] = []
        self.ops: list = []
        self.rpcs: list = []
        self.piano: str | None = "pro"
        self.verificati: set[str] = {OWNER}
        self.bando_ids: list[int] = []
        self.rinnova_fallisce: set[str] = set()
        self.prenota_in_corso = False

    def table(self, nome):
        return FakeQuery(self, nome)

    def rpc(self, nome, params):
        self.rpcs.append((nome, params))
        db = self

        class _Rpc:
            async def execute(self_inner):
                return SimpleNamespace(data=getattr(db, f"_{nome}")(params))

        return _Rpc()

    # -- aiuti
    def chiamate(self, nome):
        return [p for n, p in self.rpcs if n == nome]

    def spesa_oggi(self, gruppo="bando") -> int:
        return sum(
            e["costo_riservato_cents"] if e["cost_cents"] is None else e["cost_cents"]
            for e in self.esecuzioni.values() if e["gruppo"] == gruppo
        )

    def _chiudi_esecuzione(self, eid, stato, cost, tin, tout, model, errore):
        e = self.esecuzioni.get(eid)
        if not e or e["stato"] != "in_corso":
            return
        e.update(stato=stato, cost_cents=cost, input_tokens=tin or 0, output_tokens=tout or 0,
                 model=model or e["model"], errore_codice=errore,
                 llm_eseguito=bool((cost or 0) > 0 or (tin or 0) > 0 or (tout or 0) > 0))

    @staticmethod
    def _backoff(riga):
        return adesso() + min(timedelta(hours=6) * 2 ** min(riga["tentativi_falliti"], 5),
                              timedelta(hours=72))

    # -- RPC
    def _fn_email_verificate(self, p):
        return [u for u in p["p_user_ids"] if u in self.verificati]

    def _fn_partenariato_bando_ids(self, p):
        return list(self.bando_ids)

    def _esecuzione_scaduta(self, riga):
        """fn_partenariato_esecuzione_scaduta: prima dell'analisi costo 0; in
        analisi costo ignoto (la riserva resta) + riga timeout_unknown."""
        e = self.esecuzioni.get(riga["esecuzione_id"])
        if not e or e["stato"] != "in_corso":
            return
        if riga.get("fase") in ("documenti", "lettura"):
            self._chiudi_esecuzione(e["id"], "interrotta", 0, 0, 0, None, "claim_scaduto")
            return
        self.usage.append({
            "user_id": e["richiedente"], "family_parent_id": e["owner"],
            "provider": "anthropic", "service": "partenariato_estrazione",
            "outcome": "timeout_unknown", "cost_cents": e["costo_riservato_cents"],
            "request_meta": {"esecuzione_id": e["id"], "esito": "interrotta", "failsafe": True},
        })
        self._chiudi_esecuzione(e["id"], "interrotta", None, 0, 0, None, "claim_scaduto")

    def _fn_partenariato_chiudi_stale(self, p):
        n = 0
        for riga in self.righe.values():
            if riga["stato"] == "in_corso" and riga["claim_scade_at"] <= adesso():
                self._esecuzione_scaduta(riga)
                self._fn_errore(riga, "interrotta")
                n += 1
        return n

    def _fn_errore(self, riga, codice):
        riga.update(
            stato="pronta" if riga["esito"] else "errore", fase=None, claim_token=None,
            claim_scade_at=None, errore_codice=codice, errore_at=adesso(),
            prossimo_tentativo_at=self._backoff(riga), ultima_esecuzione_at=adesso(),
        )
        riga["tentativi_falliti"] += 1

    def _ai_prenota(self, p, gruppo):
        if p["p_richiedente"] and p["p_limite_utente"] is not None:
            n = sum(
                1 for e in self.esecuzioni.values()
                if e["richiedente"] == p["p_richiedente"]
                and not (not e["llm_eseguito"] and (
                    e["stato"] in ("riusata", "nessun_segnale")
                    or (e["stato"] in ("errore", "interrotta") and e["cost_cents"] == 0)))
            )
            if n >= max(p["p_limite_utente"], 0):
                raise _errore_rpc("ai_limite_utente")
        budget = p["p_budget_cents"]
        if budget is None or budget <= 0 or (
            self.spesa_oggi(gruppo) + p["p_costo_riservato_cents"] > budget
        ):
            raise _errore_rpc("ai_budget_esaurito")
        eid = str(uuid.uuid4())
        self.esecuzioni[eid] = {
            "id": eid, "gruppo": gruppo, "origine": p["p_origine"],
            "richiedente": p["p_richiedente"], "owner": p["p_owner"], "stato": "in_corso",
            "costo_riservato_cents": p["p_costo_riservato_cents"], "cost_cents": None,
            "input_tokens": 0, "output_tokens": 0, "model": None, "errore_codice": None,
            "llm_eseguito": False, "avviata_at": adesso().isoformat(), "content_hash": None,
        }
        return eid

    def _fn_partenariato_prenota(self, p):
        if self.prenota_in_corso:
            return {"esito": "in_corso", "claim_token": None, "esecuzione_id": None}
        riga = self.righe.get(p["p_bando_id"])
        if riga and riga["stato"] == "in_corso":
            if riga["claim_scade_at"] > adesso():
                return {"esito": "in_corso", "claim_token": None, "esecuzione_id": None}
            self._esecuzione_scaduta(riga)
        if riga and not p["p_ignora_cooldown"]:
            if riga["errore_codice"]:
                libera = riga["prossimo_tentativo_at"]
            elif riga["ultima_esecuzione_at"]:
                libera = riga["ultima_esecuzione_at"] + timedelta(minutes=p["p_cooldown_minuti"])
            else:
                libera = None
            if libera and libera > adesso():
                raise _errore_rpc("partenariato_cooldown")
        gruppo = {"batch": "batch", "valutazione": "valutazione"}.get(p["p_origine"], "bando")
        eid = self._ai_prenota(p, gruppo)
        token = str(uuid.uuid4())
        if riga is None:
            riga = self.righe[p["p_bando_id"]] = {
                "bando_id": p["p_bando_id"], "esito": None, "modalita": None,
                "modalita_effettiva": None, "extraction": None, "regole": None,
                "preclassificazione": {}, "fonti_usate": [], "catalogo_hash": None,
                "content_hash": None, "catalogo_aggiornato_at": None, "prompt_version": None,
                "schema_version": None, "model": None, "input_tokens": 0, "output_tokens": 0,
                "cost_cents": 0, "estratta_at": None, "verificata_at": None,
                "ultima_esecuzione_at": None, "ultima_forzata_at": None, "errore_codice": None,
                "errore_at": None, "tentativi_falliti": 0, "prossimo_tentativo_at": None,
                "updated_at": adesso(),
            }
        riga.update(
            bando_slug=p["p_bando_slug"], bando_titolo=p["p_bando_titolo"], stato="in_corso",
            fase="documenti", claim_token=token,
            claim_scade_at=adesso() + timedelta(seconds=min(max(p["p_ttl_secondi"], 60), 1800)),
            esecuzione_id=eid,
        )
        if p["p_ignora_cooldown"]:
            riga["ultima_forzata_at"] = adesso()
        return {"esito": "prenotata", "claim_token": token, "esecuzione_id": eid}

    def _fn_partenariato_rinnova(self, p):
        riga = self.righe.get(p["p_bando_id"])
        if p["p_fase"] in self.rinnova_fallisce:
            return False
        if (not riga or riga["claim_token"] != p["p_claim_token"]
                or riga["stato"] != "in_corso" or riga["claim_scade_at"] <= adesso()):
            return False
        riga["fase"] = p["p_fase"]
        riga["claim_scade_at"] = adesso() + timedelta(seconds=p["p_ttl_secondi"])
        return True

    def _fn_partenariato_concludi(self, p):
        riga = self.righe.get(p["p_bando_id"])
        if not riga or riga["claim_token"] != p["p_claim_token"] or riga["stato"] != "in_corso":
            return False
        dati, esito, errore = p["p_dati"], p["p_esito"], None
        cost = dati.get("cost_cents")
        if esito == "riusata" and riga["esito"] is None:
            esito, errore = "errore", "riusata_senza_estrazione"
        meta = ("preclassificazione", "fonti_usate", "catalogo_hash", "content_hash",
                "catalogo_aggiornato_at", "prompt_version", "schema_version")
        if esito in ("estratta", "nessun_segnale"):
            riga.update(esito=esito, modalita=dati.get("modalita"),
                        modalita_effettiva=dati.get("modalita_effettiva"),
                        extraction=dati.get("extraction"), regole=dati.get("regole"),
                        model=dati.get("model"), input_tokens=dati.get("input_tokens") or 0,
                        output_tokens=dati.get("output_tokens") or 0, cost_cents=cost or 0,
                        estratta_at=adesso(), verificata_at=adesso())
            riga.update({k: dati[k] for k in meta if k in dati})
        if esito == "riusata":
            riga.update(verificata_at=adesso())
            riga.update({k: dati[k] for k in meta if k in dati})
        if esito in ("estratta", "nessun_segnale", "riusata"):
            riga.update(stato="pronta", fase=None, claim_token=None, claim_scade_at=None,
                        ultima_esecuzione_at=adesso(), errore_codice=None, errore_at=None,
                        tentativi_falliti=0, prossimo_tentativo_at=None)
        else:
            errore = errore or dati.get("errore_codice") or esito
            self._fn_errore(riga, errore)
        self._chiudi_esecuzione(
            riga["esecuzione_id"], "conclusa" if esito == "estratta" else esito, cost,
            dati.get("input_tokens"), dati.get("output_tokens"), dati.get("model"), errore,
        )
        return True


class FakeSecondary:
    """Solo la lettura del bando per slug/id (il resto è monkeypatchato)."""

    def __init__(self, righe: list[dict]):
        self.righe = righe
        self.letture = 0

    def table(self, nome):
        secondario = self

        class _Q:
            filtri: dict = {}

            def select(self, *a, **k):
                self.filtri = {}
                return self

            def eq(self, c, v):
                self.filtri[c] = v
                return self

            def limit(self, *a):
                return self

            async def execute(self):
                secondario.letture += 1
                dati = [r for r in secondario.righe
                        if all(r.get(c) == v for c, v in self.filtri.items()
                               if c in ("slug", "id"))]
                return SimpleNamespace(data=dati)

        return _Q()


# ------------------------------------------------------------ modello finto


def estrazione_valida() -> PartenariatoEstrazione:
    return PartenariatoEstrazione.model_validate({
        "modalita": "ammesso",
        "modalita_citazione": {"sezione": "D1-p1", "testo_esatto": "in forma singola o associata"},
        "forme_ammesse": [{"forma": "ats", "note": None,
                           "citazione": {"sezione": "D1-p1", "testo_esatto": "mediante ATS"}}],
        "costituzione": "non_indicato", "costituzione_citazione": None,
        "partner_min": 2,
        "partner_min_citazione": {"sezione": "D1-p2", "testo_esatto": "almeno 2 imprese"},
        "partner_max": None, "partner_max_citazione": None, "conteggio_note": None,
        "composizione": [], "vincoli": [], "regole_finanziarie": [], "documenti_richiesti": [],
        "quote": [{"id": "Q1", "ambito": "per_partner", "categoria": None,
                   "min_percentuale": 20, "max_percentuale": None,
                   "base_calcolo": "spese_ammissibili", "effetto_violazione": "non_indicato",
                   "citazione": {"sezione": "D1-p2",
                                 "testo_esatto": "almeno il 20% delle spese ammissibili"}}],
        "fonti_insufficienti": False, "note": None,
    })


class FakeAi:
    def __init__(self, errore: Exception | None = None, enabled: bool = True):
        self.enabled = enabled
        self.model = "claude-test"
        self.errore = errore
        self.chiamate: list[dict] = []

    async def genera(self, system, user_message, output_format, *, model=None, max_tokens=None,
                     timeout=None):
        self.chiamate.append({"model": model, "max_tokens": max_tokens, "timeout": timeout,
                              "testo": user_message, "schema": output_format})
        if self.errore is not None:
            raise self.errore
        return estrazione_valida(), AiUsage(input_tokens=30_000, output_tokens=6_000)


# ------------------------------------------------------------ fixture


def bando(pagine_con_segnali: bool = True) -> dict:
    testo = "Possono partecipare le PMI." if pagine_con_segnali else "Contributi per investimenti."
    return {
        "id": BANDO_ID, "slug": "bando-reti", "titolo": "Bando reti", "titolo_breve": "Reti",
        "stato_bando": "aperto", "descrizione_breve": "Sostegno agli investimenti",
        "contenuto": {"sections": [{"type": "paragraph", "text": testo}]},
        "allegati": [],
    }


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "PARTENARIATO_AI_MODEL": MODELLO,
        "PARTENARIATO_BUDGET_CENTS_GIORNO": "500",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class Catalogo:
    """Stato dei finti di catalogo, download e lettura (configurabile)."""

    def __init__(self):
        self.bando = bando()
        self.pagine = [(1, PAGINA_1), (2, PAGINA_2)]
        self.download: list[str] = []
        self.letture = 0
        self.ultimo_cambiamento = adesso() - timedelta(days=30)
        self.link_errore: Exception | None = None


@pytest.fixture
def catalogo(monkeypatch):
    stato = Catalogo()

    async def fetch(secondary, slug):
        if slug != stato.bando["slug"]:
            raise NotFoundError("Bando non trovato")
        return stato.bando

    async def link(secondary, bando_id):
        if stato.link_errore:
            raise stato.link_errore
        return [LinkDocumento(id=1, bando_id=bando_id, url="https://regione.example.it/avviso.pdf",
                              dominio="regione.example.it", tipo="allegato",
                              etichetta="Avviso pubblico", content_type="application/pdf",
                              ultimo_visto_at=None)]

    async def stati(secondary, ids):
        return {BANDO_ID: StatoBando(id=BANDO_ID, slug="bando-reti", stato_effettivo="aperto",
                                     data_scadenza=None,
                                     ultimo_cambiamento_at=stato.ultimo_cambiamento)}

    async def scarica(url, *, max_bytes, timeout_s, **kwargs):
        stato.download.append(url)
        # byte diversi a ogni download (portali che rigenerano il PDF)
        contenuto = b"%PDF-1.7 " + uuid.uuid4().bytes
        return DocumentoScaricato(stato="ok", sha256=uuid.uuid4().hex, byte=len(contenuto),
                                  contenuto=contenuto)

    async def estrai(pdf_bytes, *, max_pagine, timeout_s, **kwargs):
        stato.letture += 1
        return _TestoPdf(stato="letto", pagine_totali=len(stato.pagine), pagine=list(stato.pagine),
                        caratteri=sum(len(t) for _, t in stato.pagine))

    async def lookups(secondary):
        return SimpleNamespace(regioni=[{"id": 1, "nome": "Piemonte"}])

    monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
    monkeypatch.setattr("app.services.bando_fonti_service.leggi_link_documenti", link)
    monkeypatch.setattr("app.services.bando_fonti_service.leggi_stato_bandi", stati)
    monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
    monkeypatch.setattr("app.services.pdf_testo.estrai_testo", estrai)
    monkeypatch.setattr("app.services.lookup_service.get_lookups", lookups)
    return stato


@pytest.fixture
def spawned(monkeypatch):
    catturati: list = []
    monkeypatch.setattr(ps, "_spawn", catturati.append)
    yield catturati
    for coro in catturati:
        coro.close()


def active() -> ActiveCompany:
    return ActiveCompany(company_id=COMPANY, owner_id=OWNER, editable=True)


async def avvia(db, ai, catalogo, **kwargs):
    secondary = FakeSecondary([catalogo.bando])
    return await ps.avvia_analisi(db, secondary, ai, USER, active(), "bando-reti", **kwargs)


async def stato(db, catalogo, ai=None):
    return await ps.get_stato(db, FakeSecondary([catalogo.bando]), "bando-reti", ai=ai)


async def esegui(spawned) -> str:
    coro = spawned.pop()
    return await coro


def invecchia(db, *, ore_esecuzione=48, giorni_verifica=20):
    """Porta il risultato fuori dal cooldown e oltre la riverifica."""
    riga = db.righe[BANDO_ID]
    riga["ultima_esecuzione_at"] = adesso() - timedelta(hours=ore_esecuzione)
    riga["verificata_at"] = adesso() - timedelta(days=giorni_verifica)


# ------------------------------------------------------------ flusso base


class TestFlussoBase:
    async def test_claim_pipeline_concludi(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is True
        assert out.stato == "in_corso" and out.fase == "documenti"
        assert out.puo_avviare is False and out.motivo_non_avviabile == "in_corso"
        [prenota] = db.chiamate("fn_partenariato_prenota")
        riserva = ps.stima_riserva_cents(catalogo.bando)
        assert prenota["p_costo_riservato_cents"] == riserva > 0
        assert prenota["p_budget_cents"] == 500
        assert prenota["p_origine"] == "utente" and prenota["p_limite_utente"] == 10
        assert prenota["p_cooldown_minuti"] == 24 * 60 and prenota["p_ttl_secondi"] == 900
        assert prenota["p_ignora_cooldown"] is False
        assert prenota["p_owner"] == OWNER and prenota["p_company"] == COMPANY

        assert await esegui(spawned) == "estratta"
        # heartbeat prima della lettura e SUBITO prima del modello
        assert [p["p_fase"] for p in db.chiamate("fn_partenariato_rinnova")] == [
            "lettura", "analisi"]
        [chiamata] = ai.chiamate
        assert chiamata["model"] == MODELLO
        assert (chiamata["max_tokens"], chiamata["timeout"]) == (16000, 180.0)
        assert chiamata["schema"] is PartenariatoEstrazione
        assert "[D1-p1]" in chiamata["testo"] and "[DOCUMENTO D1]" in chiamata["testo"]

        riga = db.righe[BANDO_ID]
        assert riga["stato"] == "pronta" and riga["esito"] == "estratta"
        assert riga["modalita_effettiva"] == "ammesso"
        costo = costo_cents(MODELLO, 30_000, 6_000)
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["stato"] == "conclusa" and esecuzione["cost_cents"] == costo
        assert db.spesa_oggi() == costo  # la riserva è stata sostituita dal costo reale
        [uso] = db.usage
        assert uso["provider"] == "anthropic" and uso["service"] == "partenariato_estrazione"
        assert uso["outcome"] == "success" and uso["cost_cents"] == costo
        assert uso["user_id"] == USER["id"] and uso["family_parent_id"] == OWNER
        assert "extraction" not in str(uso["request_meta"])

        dopo = await stato(db, catalogo, ai)
        assert dopo.stato == "pronta" and dopo.regole is not None
        assert dopo.regole.modalita.valore == dopo.regole.modalita_effettiva == "ammesso"
        assert dopo.regole.quote[0].stato == "verificata"
        [fonte] = dopo.fonti
        assert (fonte.n, fonte.stato, fonte.pagine_incluse, fonte.troncato) == (
            1, "letto", [1, 2], False)
        assert fonte.url == "https://regione.example.it/avviso.pdf"
        assert dopo.aggiornabile is False
        assert (dopo.puo_avviare, dopo.motivo_non_avviabile) == (False, "cooldown")

    async def test_risultato_fresco_200_senza_prenotazione(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        # fuori dal cooldown ma dentro la riverifica, catalogo invariato
        invecchia(db, giorni_verifica=2)
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is False and out.stato == "pronta"
        assert len(db.chiamate("fn_partenariato_prenota")) == 1
        assert out.motivo_non_avviabile == "aggiornata"

    async def test_in_corso_non_riprenota(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is False
        assert out.stato == "in_corso" and out.avviata_at is not None
        assert len(db.chiamate("fn_partenariato_prenota")) == 1
        assert len(spawned) == 1

    async def test_claim_vinto_da_altri_nella_rpc(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        db.prenota_in_corso = True
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is False and spawned == []
        assert out.stato == "non_estratta"

    async def test_ai_non_configurata(self, catalogo, spawned):
        with pytest.raises(AiNotConfiguredError):
            await avvia(FakeDb(), FakeAi(enabled=False), catalogo)

    async def test_bando_inesistente(self, catalogo, spawned):
        with pytest.raises(NotFoundError):
            await ps.avvia_analisi(FakeDb(), FakeSecondary([]), FakeAi(), USER, active(), "boh")

    async def test_nessuna_scrittura_in_ai_checks(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        assert not [op for op in db.ops if op[0] == "ai_checks"]


# ------------------------------------------------------------ senza modello


class TestSenzaModello:
    async def test_nessun_segnale_senza_llm(self, catalogo, spawned):
        catalogo.bando = bando(pagine_con_segnali=False)
        catalogo.pagine = [(1, NEUTRA_1), (2, NEUTRA_2)]
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "nessun_segnale"
        assert ai.chiamate == []
        riga = db.righe[BANDO_ID]
        assert riga["esito"] == "nessun_segnale" and riga["regole"] is None
        [esecuzione] = db.esecuzioni.values()
        # costo 0 ESPLICITO: la riserva esce dal budget
        assert esecuzione["stato"] == "nessun_segnale" and esecuzione["cost_cents"] == 0
        assert db.spesa_oggi() == 0
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("success", 0)
        out = await stato(db, catalogo, ai)
        assert out.stato == "nessun_segnale"
        assert out.puo_avviare is True  # «Analizza comunque»

    @pytest.mark.parametrize("motivo", ["timeout", "rete", "http_503", "imprevisto"])
    async def test_download_non_riuscito_errore_non_nessun_segnale(
        self, catalogo, spawned, monkeypatch, motivo
    ):
        """Nessun documento letto per una causa transitoria e scheda senza
        segnali: NON è «nessun segnale» (varrebbe 14 giorni senza aver letto
        nulla). Errore con backoff, costo 0, niente modello, limite intatto."""
        catalogo.bando = bando(pagine_con_segnali=False)

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            return DocumentoScaricato(stato="errore_download", motivo=motivo)

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        db, ai = FakeDb(), FakeAi()
        db.piano = "gratuito"
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "errore"
        assert catalogo.letture == 0 and ai.chiamate == []
        riga = db.righe[BANDO_ID]
        assert (riga["stato"], riga["esito"]) == ("errore", None)
        assert riga["errore_codice"] == "documenti_non_raggiungibili"
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("errore", 0)
        assert db.spesa_oggi() == 0
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("error", 0)
        out = await stato(db, catalogo)
        assert out.stato == "errore" and "scaricare i documenti" in out.errore
        assert out.riprova_dopo is not None
        # nessun credito consumato: su un altro bando il Gratuito (3/giorno) riparte
        for altro in (202, 303, 404):
            catalogo.bando = {**catalogo.bando, "id": altro}
            await avvia(db, ai, catalogo)
            assert await esegui(spawned) == "errore"

    async def test_link_del_catalogo_non_leggibili_errore(self, catalogo, spawned, monkeypatch):
        """`bando_link` non leggibile (errore del catalogo) e nessun allegato di
        ripiego: non si può dire che il bando non preveda partenariati."""
        catalogo.bando = bando(pagine_con_segnali=False)

        async def link(secondary, bando_id):
            return None

        monkeypatch.setattr("app.services.bando_fonti_service.leggi_link_documenti", link)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "errore"
        assert db.righe[BANDO_ID]["errore_codice"] == "documenti_non_raggiungibili"
        assert ai.chiamate == []

    async def test_nessun_documento_ufficiale_resta_nessun_segnale(
        self, catalogo, spawned, monkeypatch
    ):
        catalogo.bando = bando(pagine_con_segnali=False)

        async def link(secondary, bando_id):
            return []

        monkeypatch.setattr("app.services.bando_fonti_service.leggi_link_documenti", link)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "nessun_segnale"

    async def test_lettura_oltre_il_tempo_errore(self, catalogo, spawned, monkeypatch):
        catalogo.bando = bando(pagine_con_segnali=False)

        async def estrai(pdf_bytes, *, max_pagine, timeout_s, **kwargs):
            return _TestoPdf(stato="timeout")

        monkeypatch.setattr("app.services.pdf_testo.estrai_testo", estrai)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "errore"
        assert db.righe[BANDO_ID]["errore_codice"] == "documenti_non_raggiungibili"

    @pytest.mark.parametrize(
        "scaricato",
        [
            DocumentoScaricato(stato="errore_download", motivo="http_404"),
            DocumentoScaricato(stato="non_pdf", motivo="magic_bytes"),
            DocumentoScaricato(stato="troppo_grande", motivo="content_length"),
        ],
    )
    async def test_documento_assente_per_sempre_resta_nessun_segnale(
        self, catalogo, spawned, monkeypatch, scaricato
    ):
        catalogo.bando = bando(pagine_con_segnali=False)

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            return scaricato

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "nessun_segnale"
        [fonte] = (await stato(db, catalogo)).fonti
        assert fonte.stato == scaricato.stato and fonte.pagine_incluse == []

    async def test_documento_bloccato_per_policy_senza_link(
        self, catalogo, spawned, monkeypatch
    ):
        """Redirect verso un dominio escluso: il download lo blocca e il link
        del catalogo non va mostrato (il browser seguirebbe il redirect)."""
        catalogo.bando = bando(pagine_con_segnali=False)

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            return DocumentoScaricato(stato="bloccato_policy",
                                      motivo="redirect_dominio_negato")

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        [fonte] = (await stato(db, catalogo)).fonti
        assert (fonte.stato, fonte.url) == ("bloccato_policy", None)
        assert fonte.dominio == "regione.example.it"

    async def test_riusata_con_stesso_testo_e_pdf_rigenerato(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        invecchia(db)  # riverifica scaduta: si riscaricano i documenti
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is True and out.aggiornamento_in_corso is True and out.stato == "pronta"
        assert await esegui(spawned) == "riusata"
        assert len(ai.chiamate) == 1  # solo la prima
        assert len(catalogo.download) == 2  # riscaricato, byte diversi, stesso testo
        ultima = list(db.esecuzioni.values())[-1]
        assert ultima["stato"] == "riusata" and ultima["cost_cents"] == 0
        assert db.usage[-1]["outcome"] == "success" and db.usage[-1]["cost_cents"] == 0
        assert db.righe[BANDO_ID]["verificata_at"] > adesso() - timedelta(minutes=1)

    async def test_catalogo_invariato_riusata_senza_download(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        # il catalogo segnala un cambiamento (es. di stato) dopo la generazione
        invecchia(db, giorni_verifica=2)
        catalogo.ultimo_cambiamento = adesso()
        out, avviata = await avvia(db, ai, catalogo)
        assert avviata is True
        assert await esegui(spawned) == "riusata"
        assert len(catalogo.download) == 1 and len(ai.chiamate) == 1

    async def test_testo_cambiato_nuova_estrazione(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        invecchia(db)
        catalogo.pagine = [(1, PAGINA_1 + " Nuovo comma."), (2, PAGINA_2)]
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "estratta"
        assert len(ai.chiamate) == 2


# ------------------------------------------------------------ «Analizza comunque»


class TestForza:
    async def _nessun_segnale(self, catalogo, spawned, db, ai):
        catalogo.bando = bando(pagine_con_segnali=False)
        catalogo.pagine = [(1, NEUTRA_1), (2, NEUTRA_2)]
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "nessun_segnale"

    async def test_forza_senza_nessun_segnale_rifiutata(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        with pytest.raises(AppError) as exc:
            await avvia(db, ai, catalogo, forza=True)
        assert (exc.value.status_code, exc.value.code) == (409, "forza_non_ammessa")
        assert db.chiamate("fn_partenariato_prenota") == []

    async def test_forza_dopo_nessun_segnale_una_volta(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await self._nessun_segnale(catalogo, spawned, db, ai)
        # dentro il cooldown: la forzatura lo salta, e il modello viene chiamato
        out, avviata = await avvia(db, ai, catalogo, forza=True)
        assert avviata is True
        assert db.chiamate("fn_partenariato_prenota")[-1]["p_ignora_cooldown"] is True
        assert await esegui(spawned) == "estratta"
        assert len(ai.chiamate) == 1
        # l'esito ora è estratta: niente più forzature
        with pytest.raises(AppError) as exc:
            await avvia(db, ai, catalogo, forza=True)
        assert exc.value.code == "forza_non_ammessa"

    async def test_forzatura_fallita_non_si_ripete(self, catalogo, spawned):
        db = FakeDb()
        await self._nessun_segnale(catalogo, spawned, db, FakeAi())
        ai = FakeAi(errore=AiUpstreamError())
        await avvia(db, ai, catalogo, forza=True)
        assert await esegui(spawned) == "errore"
        riga = db.righe[BANDO_ID]
        assert riga["esito"] == "nessun_segnale" and riga["stato"] == "pronta"
        out = await stato(db, catalogo, ai)
        assert out.stato == "nessun_segnale" and out.puo_avviare is False
        with pytest.raises(AppError) as exc:
            await avvia(db, ai, catalogo, forza=True)
        assert exc.value.code == "forza_non_ammessa"


# ------------------------------------------------------------ heartbeat


class TestHeartbeat:
    async def test_claim_scaduto_prima_della_lettura(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        db.piano = "gratuito"
        await avvia(db, ai, catalogo)
        db.righe[BANDO_ID]["claim_scade_at"] = adesso() - timedelta(seconds=1)
        assert await esegui(spawned) == "claim_perso"
        assert ai.chiamate == []
        # il claim scaduto non è stato ripreso: la pipeline chiude da sé a
        # costo 0 (la riserva esce subito dal budget, il limite è intatto)
        riga = db.righe[BANDO_ID]
        assert (riga["stato"], riga["errore_codice"]) == ("errore", "interrotta")
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("errore", 0)
        assert db.spesa_oggi() == 0
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("error", 0)
        assert uso["request_meta"]["esito"] == "claim_perso"

    async def test_heartbeat_perso_prima_del_modello(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        db.rinnova_fallisce = {"analisi"}
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "claim_perso"
        assert ai.chiamate == []  # genera NON chiamato
        out = await stato(db, catalogo)
        assert out.stato == "errore"
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["cost_cents"] == 0 and db.spesa_oggi() == 0

    async def test_claim_scaduto_ripreso_da_una_nuova_richiesta(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        vecchia = spawned.pop()
        db.righe[BANDO_ID]["claim_scade_at"] = adesso() - timedelta(seconds=1)
        # la GET applica il failsafe: interrotta, backoff
        out = await stato(db, catalogo)
        assert out.stato == "errore" and out.riprova_dopo is not None
        assert await vecchia == "claim_perso"
        assert ai.chiamate == []
        # chiusa dal failsafe prima dell'analisi: costo 0, niente «fantasma»
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("interrotta", 0)
        assert db.spesa_oggi() == 0

    async def test_failsafe_in_analisi_riserva_e_registro_consumi(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        spawned.pop().close()
        riga = db.righe[BANDO_ID]
        riga["fase"] = "analisi"
        riga["claim_scade_at"] = adesso() - timedelta(seconds=1)
        await stato(db, catalogo)
        riserva = ps.stima_riserva_cents(catalogo.bando)
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("interrotta", None)
        assert db.spesa_oggi() == riserva
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", riserva)


class TestCancellazione:
    """Task cancellato (spegnimento del processo): la pipeline chiude e
    registra il consumo prima di lasciar proseguire la cancellazione."""

    async def test_durante_la_chiamata_al_modello(self, catalogo, spawned):
        partita = asyncio.Event()

        class AiLenta(FakeAi):
            async def genera(self, *args, **kwargs):
                self.chiamate.append(kwargs)
                partita.set()
                await asyncio.sleep(3600)

        db, ai = FakeDb(), AiLenta()
        await avvia(db, ai, catalogo)
        task = asyncio.create_task(spawned.pop())
        await asyncio.wait_for(partita.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        riserva = ps.stima_riserva_cents(catalogo.bando)
        riga = db.righe[BANDO_ID]
        assert riga["stato"] == "errore" and riga["errore_codice"] == "interrotta"
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("timeout", riserva)
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", riserva)

    async def test_prima_del_modello_costo_zero(self, catalogo, spawned, monkeypatch):
        partito = asyncio.Event()

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            partito.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        task = asyncio.create_task(spawned.pop())
        await asyncio.wait_for(partito.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ai.chiamate == []
        [esecuzione] = db.esecuzioni.values()
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("errore", 0)
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("error", 0)


# ------------------------------------------------------------ costi sugli errori


class TestCostiErrori:
    async def test_usage_sull_eccezione_costo_registrato_e_budget_scalato(
        self, catalogo, spawned, monkeypatch
    ):
        errore = AiUpstreamError("troncata")
        errore.usage = AiUsage(input_tokens=40_000, output_tokens=16_000)
        db, ai = FakeDb(), FakeAi(errore=errore)
        await avvia(db, ai, catalogo)
        riserva = ps.stima_riserva_cents(catalogo.bando)
        assert await esegui(spawned) == "errore"
        reale = costo_cents(MODELLO, 40_000, 16_000)
        atteso = max(reale, riserva)
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["stato"] == "errore" and esecuzione["cost_cents"] == atteso
        assert esecuzione["llm_eseguito"] is True
        assert db.spesa_oggi() == atteso >= riserva
        riga = db.righe[BANDO_ID]
        assert riga["stato"] == "errore" and riga["errore_codice"] == "ai_risposta_non_valida"
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("error", atteso)
        # il budget del giorno è scalato: con un tetto appena sopra la spesa,
        # la prenotazione successiva (altro bando) viene rifiutata
        monkeypatch.setenv("PARTENARIATO_BUDGET_CENTS_GIORNO", str(atteso + riserva - 1))
        from app.core.config import get_settings

        get_settings.cache_clear()
        catalogo.bando = {**catalogo.bando, "id": 202, "slug": "bando-reti"}
        with pytest.raises(AppError) as exc:
            await avvia(db, FakeAi(), catalogo)
        assert (exc.value.status_code, exc.value.code) == (429, "ai_sospesa_oggi")

    async def test_timeout_costo_riservato(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi(errore=AiTimeoutError())
        await avvia(db, ai, catalogo)
        riserva = ps.stima_riserva_cents(catalogo.bando)
        assert await esegui(spawned) == "timeout"
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["stato"] == "timeout" and esecuzione["cost_cents"] == riserva
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("timeout_unknown", riserva)
        out = await stato(db, catalogo, ai)
        assert out.stato == "errore" and "troppo tempo" in out.errore
        assert out.riprova_dopo is not None
        assert (out.puo_avviare, out.motivo_non_avviabile) == (False, "cooldown")
        # dentro il backoff la RPC rifiuta
        with pytest.raises(AppError) as exc:
            await avvia(db, ai, catalogo)
        assert (exc.value.status_code, exc.value.code) == (429, "partenariato_cooldown")

    async def test_timeout_costo_almeno_la_stima_dell_input_inviato(
        self, catalogo, spawned, monkeypatch
    ):
        # riserva sottostimata (es. tokenizer più denso del previsto): sul
        # timeout vale il caso peggiore calcolato sull'input davvero inviato
        monkeypatch.setattr(ps, "stima_riserva_cents", lambda bando: 1)
        db, ai = FakeDb(), FakeAi(errore=AiTimeoutError())
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "timeout"
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["cost_cents"] > 1
        testo = ai.chiamate[0]["testo"]
        atteso = ps.stima_cents(
            MODELLO, len(ps.SYSTEM_PARTENARIATO) + len(testo) + len(ps._schema_json()), 16000
        )
        assert esecuzione["cost_cents"] == atteso == db.usage[0]["cost_cents"]

    async def test_errore_di_rete_costo_ignoto_riserva_nel_budget(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi(errore=AiUpstreamError())
        await avvia(db, ai, catalogo)
        riserva = ps.stima_riserva_cents(catalogo.bando)
        assert await esegui(spawned) == "errore"
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["cost_cents"] is None
        assert db.spesa_oggi() == riserva
        [uso] = db.usage
        assert uso["outcome"] == "error" and uso["request_meta"]["costo_ignoto"] is True

    async def test_errore_prima_del_modello_costo_zero(self, catalogo, spawned):
        catalogo.link_errore = RuntimeError("boom")  # errore inatteso, non di lettura
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "errore"
        assert ai.chiamate == []
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["cost_cents"] == 0 and db.spesa_oggi() == 0
        assert db.righe[BANDO_ID]["errore_codice"] == "errore_interno"
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == ("error", 0)

    async def test_errore_dopo_il_modello_costo_max_reale_riserva(
        self, catalogo, spawned, monkeypatch
    ):
        def rotto(*args, **kwargs):
            raise ValueError("post-elaborazione rotta")

        monkeypatch.setattr(ps, "post_elabora", rotto)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "errore"
        [esecuzione] = db.esecuzioni.values()
        riserva = ps.stima_riserva_cents(catalogo.bando)
        assert esecuzione["cost_cents"] == max(costo_cents(MODELLO, 30_000, 6_000), riserva)
        assert esecuzione["llm_eseguito"] is True

    async def test_errore_precedente_resta_servito(self, catalogo, spawned):
        db = FakeDb()
        await avvia(db, FakeAi(), catalogo)
        await esegui(spawned)
        invecchia(db)
        catalogo.pagine = [(1, PAGINA_1 + " Modificato."), (2, PAGINA_2)]
        await avvia(db, FakeAi(errore=AiTimeoutError()), catalogo)
        assert await esegui(spawned) == "timeout"
        out = await stato(db, catalogo)
        assert out.stato == "pronta" and out.regole is not None
        # le regole precedenti restano servite, con il messaggio dell'ultimo tentativo
        assert "troppo tempo" in out.errore and out.riprova_dopo is not None


# ------------------------------------------------------------ limiti per utente


class TestLimiti:
    async def test_gratuito_con_email_verificata_limite_3(self, catalogo, spawned):
        db = FakeDb()
        db.piano = "gratuito"
        await avvia(db, FakeAi(), catalogo)
        assert db.chiamate("fn_partenariato_prenota")[0]["p_limite_utente"] == 3
        assert db.chiamate("fn_email_verificate")[0]["p_user_ids"] == [USER["id"]]

    async def test_senza_abbonamento_come_gratuito(self, catalogo, spawned):
        db = FakeDb()
        db.piano = None
        await avvia(db, FakeAi(), catalogo)
        assert db.chiamate("fn_partenariato_prenota")[0]["p_limite_utente"] == 3

    async def test_gratuito_senza_email_verificata_403(self, catalogo, spawned):
        db = FakeDb()
        db.piano, db.verificati = "gratuito", set()
        with pytest.raises(AppError) as exc:
            await avvia(db, FakeAi(), catalogo)
        assert (exc.value.status_code, exc.value.code) == (403, "email_non_verificata")
        assert db.chiamate("fn_partenariato_prenota") == []

    async def test_piano_a_pagamento_limite_10_senza_verifica_email(self, catalogo, spawned):
        db = FakeDb()
        db.piano = "smart"
        await avvia(db, FakeAi(), catalogo)
        assert db.chiamate("fn_partenariato_prenota")[0]["p_limite_utente"] == 10
        assert db.chiamate("fn_email_verificate") == []

    async def test_limite_raggiunto_429(self, catalogo, spawned, monkeypatch):
        monkeypatch.setenv("PARTENARIATO_LIMITE_UTENTE_GIORNO", "1")
        from app.core.config import get_settings

        get_settings.cache_clear()
        db = FakeDb()
        await avvia(db, FakeAi(), catalogo)
        await esegui(spawned)
        catalogo.bando = {**catalogo.bando, "id": 303}
        with pytest.raises(AppError) as exc:
            await avvia(db, FakeAi(), catalogo)
        assert (exc.value.status_code, exc.value.code) == (429, "ai_limite_giornaliero")

    async def test_budget_zero_negato(self, catalogo, spawned, monkeypatch):
        monkeypatch.setenv("PARTENARIATO_BUDGET_CENTS_GIORNO", "0")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(AppError) as exc:
            await avvia(FakeDb(), FakeAi(), catalogo)
        assert exc.value.code == "ai_sospesa_oggi"


# ------------------------------------------------------------ stato, admin, batch


class TestStatoEAltro:
    async def test_stato_non_estratta(self, catalogo):
        out = await stato(FakeDb(), catalogo, FakeAi())
        assert (out.stato, out.puo_avviare, out.stato_bando) == ("non_estratta", True, "aperto")
        out = await stato(FakeDb(), catalogo, FakeAi(enabled=False))
        assert (out.puo_avviare, out.motivo_non_avviabile) == (False, "ai_non_configurata")

    async def test_aggiornabile_per_prompt_nuovo(self, catalogo, spawned):
        db = FakeDb()
        await avvia(db, FakeAi(), catalogo)
        await esegui(spawned)
        invecchia(db, giorni_verifica=1)
        assert (await stato(db, catalogo)).aggiornabile is False
        db.righe[BANDO_ID]["prompt_version"] = 0
        out = await stato(db, catalogo)
        assert out.aggiornabile is True and out.puo_avviare is True

    async def test_aggiornabile_per_catalogo_cambiato_ma_non_nel_cooldown(self, catalogo, spawned):
        db = FakeDb()
        await avvia(db, FakeAi(), catalogo)
        await esegui(spawned)
        catalogo.ultimo_cambiamento = adesso() + timedelta(minutes=1)
        assert (await stato(db, catalogo)).aggiornabile is False  # cooldown 24 h
        invecchia(db, giorni_verifica=1)
        assert (await stato(db, catalogo)).aggiornabile is True

    async def test_forza_admin_paga_sempre(self, catalogo, spawned):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        await esegui(spawned)
        secondary = FakeSecondary([catalogo.bando])
        out, avviata = await ps.forza_admin(db, secondary, ai, USER, BANDO_ID,
                                            ignora_cooldown=True)
        assert avviata is True
        prenota = db.chiamate("fn_partenariato_prenota")[-1]
        assert (prenota["p_origine"], prenota["p_limite_utente"]) == ("admin", None)
        assert prenota["p_ignora_cooldown"] is True
        assert await esegui(spawned) == "estratta"
        assert len(ai.chiamate) == 2  # nessuna scorciatoia della cache

    async def test_batch_senza_utente(self, catalogo, spawned, monkeypatch):
        db, ai = FakeDb(), FakeAi()
        esito = await ps.esegui_per_bando(db, FakeSecondary([catalogo.bando]), ai,
                                          catalogo.bando, origine="batch", budget_cents=300)
        assert esito == "estratta"
        prenota = db.chiamate("fn_partenariato_prenota")[0]
        assert prenota["p_richiedente"] is None and prenota["p_limite_utente"] is None
        [esecuzione] = db.esecuzioni.values()
        assert esecuzione["gruppo"] == "batch"
        [uso] = db.usage
        assert uso["user_id"] is None and uso["family_parent_id"] is None

    async def test_bando_ids_per_modalita(self):
        db = FakeDb()
        db.bando_ids = [3, 2, 1]
        assert await ps.bando_ids_per_modalita(db, "ammesso") == [3, 2, 1]
        [chiamata] = db.chiamate("fn_partenariato_bando_ids")
        assert chiamata == {"p_modalita": ["ammesso", "obbligatorio"], "p_limite": 500}
        await ps.bando_ids_per_modalita(db, "obbligatorio")
        assert db.chiamate("fn_partenariato_bando_ids")[-1]["p_modalita"] == ["obbligatorio"]
        assert await ps.bando_ids_per_modalita(db, "boh") == []

    async def test_spawn_vero_esegue_la_pipeline(self, catalogo):
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        for _ in range(50):
            if db.righe[BANDO_ID]["stato"] != "in_corso":
                break
            await asyncio.sleep(0.01)
        assert db.righe[BANDO_ID]["esito"] == "estratta"


# ------------------------------------------------------------ budget del modulo


class FakeRpc:
    def __init__(self, risposta=None, errore: Exception | None = None):
        self.risposta, self.errore, self.chiamate = risposta, errore, []

    def rpc(self, nome, params):
        self.chiamate.append((nome, params))
        finto = self

        class _R:
            async def execute(self_inner):
                if finto.errore:
                    raise finto.errore
                return SimpleNamespace(data=finto.risposta)

        return _R()


class TestBudgetModulo:
    async def test_prenota_parametri_esatti(self):
        from app.services import partenariati_ai_budget as budget

        db = FakeRpc(risposta="e0000000-0000-0000-0000-000000000001")
        eid = await budget.prenota(
            db, servizio="partner_profilo_ai", origine="utente", gruppo="altri",
            budget_cents=200, costo_riservato_cents=7, richiedente=USER["id"],
            limite_richiedente=3, owner=OWNER, limite_owner=None, company_id=COMPANY,
        )
        assert eid == "e0000000-0000-0000-0000-000000000001"
        [(nome, params)] = db.chiamate
        assert nome == "fn_partenariati_ai_prenota"
        assert params == {
            "p_servizio": "partner_profilo_ai", "p_origine": "utente", "p_gruppo": "altri",
            "p_budget_cents": 200, "p_costo_riservato_cents": 7, "p_richiedente": USER["id"],
            "p_limite_richiedente": 3, "p_owner": OWNER, "p_limite_owner": None,
            "p_company": COMPANY, "p_bando_id": None,
        }

    async def test_prenota_detail_in_app_error(self):
        from app.services import partenariati_ai_budget as budget

        for detail, codice in (("ai_budget_esaurito", "ai_sospesa_oggi"),
                               ("ai_limite_owner", "ai_limite_giornaliero")):
            db = FakeRpc(errore=_errore_rpc(detail))
            with pytest.raises(AppError) as exc:
                await budget.prenota(
                    db, servizio="partner_bozza", origine="utente", gruppo="altri",
                    budget_cents=200, costo_riservato_cents=1, richiedente=None,
                    limite_richiedente=None, owner=OWNER, limite_owner=5,
                )
            assert (exc.value.status_code, exc.value.code) == (429, codice)

    async def test_prenota_senza_id_fail_closed(self):
        from app.core.errors import UpstreamError
        from app.services import partenariati_ai_budget as budget

        with pytest.raises(UpstreamError):
            await budget.prenota(
                FakeRpc(risposta=None), servizio="partner_bozza", origine="utente",
                gruppo="altri", budget_cents=200, costo_riservato_cents=1, richiedente=None,
                limite_richiedente=None, owner=None, limite_owner=None,
            )

    async def test_concludi_costo_ignoto_resta_none(self):
        from app.services import partenariati_ai_budget as budget

        db = FakeRpc()
        await budget.concludi(db, "e1", stato="timeout", cost_cents=None, model=MODELLO)
        await budget.concludi(db, "e2", stato="conclusa", cost_cents=12, input_tokens=100,
                              output_tokens=50, model=MODELLO)
        assert db.chiamate[0][1]["p_cost_cents"] is None
        assert db.chiamate[1][1] == {
            "p_esecuzione_id": "e2", "p_stato": "conclusa", "p_cost_cents": 12,
            "p_input_tokens": 100, "p_output_tokens": 50, "p_model": MODELLO, "p_errore": None,
        }

    def test_budget_per_gruppo(self):
        from app.services.partenariati_ai_budget import budget_cents_gruppo

        assert budget_cents_gruppo("bando") == 500
        assert budget_cents_gruppo("altri") == 200
        assert budget_cents_gruppo("batch") == 0
        assert budget_cents_gruppo("valutazione") == 0


# ------------------------------------------------------------ memoria


class TestMemoria:
    async def test_byte_dei_pdf_rilasciati_prima_del_modello(
        self, catalogo, spawned, monkeypatch
    ):
        """Dopo la lettura i byte dei PDF non servono più: non restano in
        memoria durante la chiamata al modello (fino a 4 × 15 MB per pipeline)."""
        import gc
        import weakref

        riferimenti: list = []

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            documento = DocumentoScaricato(stato="ok", sha256="x", byte=12,
                                           contenuto=b"%PDF-1.7 " + uuid.uuid4().bytes)
            riferimenti.append(weakref.ref(documento))
            return documento

        vivi_durante_la_chiamata: list[int] = []

        class AiCheControlla(FakeAi):
            async def genera(self, *args, **kwargs):
                gc.collect()
                vivi_durante_la_chiamata.append(sum(r() is not None for r in riferimenti))
                return await super().genera(*args, **kwargs)

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        db, ai = FakeDb(), AiCheControlla()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "estratta"
        assert riferimenti and vivi_durante_la_chiamata == [0]


# ------------------------------------------------------------ integrazione PDF


class TestIntegrazionePdf:
    async def test_pdf_vero_letto_nel_processo_separato(self, catalogo, spawned, monkeypatch):
        """Download finto, lettura VERA (pypdf nel processo spawn): le citazioni
        del modello si verificano sul testo estratto dal PDF, a capo compresi."""
        from tests.test_pdf_testo import genera_pdf

        # testo diverso per pagina: le righe ripetute sono tolte come piè di pagina
        prima = PAGINA_1 + " Il contributo copre le spese per consulenze, attrezzature e " \
            "servizi di ricerca svolti nel territorio regionale entro dodici mesi."
        seconda = PAGINA_2 + " La domanda si presenta tramite la piattaforma regionale " \
            "allegando il piano finanziario firmato digitalmente dal capofila."
        pdf = genera_pdf([prima, seconda])

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            return DocumentoScaricato(stato="ok", sha256="x", byte=len(pdf), contenuto=pdf)

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        monkeypatch.setattr("app.services.pdf_testo.estrai_testo", estrai_testo_vero)
        db, ai = FakeDb(), FakeAi()
        await avvia(db, ai, catalogo)
        assert await esegui(spawned) == "estratta"
        out = await stato(db, catalogo)
        [fonte] = out.fonti
        assert (fonte.stato, fonte.pagine_totali, fonte.pagine_incluse) == ("letto", 2, [1, 2])
        assert out.regole.modalita.stato == "verificata"
        assert out.regole.modalita.citazione.pagina == 1
        assert out.regole.partner_min.stato == "verificata"
        assert out.regole.quote[0].stato == "verificata"
