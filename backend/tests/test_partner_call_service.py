"""Servizio delle call di partenariato (WP5): crea bozza (bando live,
`non_ammesso` con motivo, anonima forzata, limiti), conferma delle regole
(`confermata` solo su voci verificate, fonte scritta dal servizio), gap
analysis (fonti, precedenza, copertura del creatore, id ed etichette
conservati, evidenza dell'AI-check), job AI di posizioni e testi
(prenotazione, limiti, input minimizzato, update condizionato, costi e
registro consumi su ogni ramo come WP3/WP4, cancellazione del task, failsafe
in lettura), anteprima con rilievi, pubblicazione (bando live, scadenza di
default, rilievi bloccanti, Q11, identità e limiti del pool), chiusura,
versioni a whitelist, segnalazioni DSA con conferma di ricezione,
`ai_check_service.ultimo_ready`, `calls_aperte` nello stato del bando.

Il primario è un gemello in memoria delle tabelle e delle RPC della 0037
(stesse guardie, stessi detail); il modello è finto e NESSUNA chiamata esce
verso Anthropic."""

import asyncio
import copy
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
    UpstreamError,
)
from app.schemas.bando import LookupsOut
from app.schemas.common import AtecoItem, LookupItem
from app.schemas.partner_call import (
    CallAggiornaIn,
    CallCreaIn,
    ChiudiIn,
    PosizioniIn,
    RegoleConfermaIn,
    RequisitiIn,
    SegnalazioneIn,
)
from app.services import ai_check_service, bandi_service, partenariato_service
from app.services import partner_call_ai as pca
from app.services import partner_call_service as pcs
from app.services.ai_prezzi import costo_cents
from app.services.bando_fonti_service import StatoBando
from app.services.bilanci_indicatori import EsercizioBilancio
from app.services.partner_call_gap import id_voce, rif_con_impronta
from app.services.partner_call_prompts import (
    BozzaTestiCall,
    PosizioneAi,
    PropostaPosizioni,
)
from tests.test_partner_call_gap import conferma_tutto, regole_estratte

OWNER = "a0000000-0000-0000-0000-000000000001"
MEMBRO = "b0000000-0000-0000-0000-000000000002"
ALTRO_OWNER = "a0000000-0000-0000-0000-0000000000ff"
COMPANY = "c0000000-0000-0000-0000-000000000001"
COMPANY_B = "c0000000-0000-0000-0000-000000000002"  # seconda azienda dello stesso owner
ALTRA_COMPANY = "c0000000-0000-0000-0000-0000000000ff"  # di un altro owner
MODELLO = "claude-sonnet-5"
BANDO_ID = 101
SLUG = "bando-reti-2026"
PIVA = "01234567897"
PIVA_B = "09876543217"

CANARY_RISERVATI = "CANARYRISERVATO accordo con il cliente Zeta"
CANARY_BUDGET = Decimal("1234567.89")

USER_OWNER = {"id": OWNER, "nome": "Mario", "cognome": "Rossi", "role": "cliente",
              "is_active": True, "codice_fiscale": "RSSMRA80A01H501U",
              "cf_verified_at": "2026-01-10T10:00:00+00:00"}
USER_MEMBRO = {"id": MEMBRO, "nome": "Luca", "cognome": "Verdi", "role": "cliente",
               "is_active": True}
USER_ALTRO = {"id": ALTRO_OWNER, "nome": "Anna", "cognome": "Neri", "role": "cliente",
              "is_active": True}
USER_ADMIN = {"id": "d0000000-0000-0000-0000-000000000001", "role": "admin", "is_active": True}

LOOKUPS = LookupsOut(
    regioni=[LookupItem(id=1, nome="Piemonte"), LookupItem(id=3, nome="Lombardia"),
             LookupItem(id=5, nome="Veneto")],
    settori=[LookupItem(id=86, nome="Trasporti")],
    beneficiari=[LookupItem(id=26, nome="Piccola impresa")],
    codici_ateco=[AtecoItem(id=1, codice="62.01.00", descrizione="Software")],
    tipologie_bando=[LookupItem(id=2, nome="Contributo")],
    modalita_erogazione=[],
    programmi=[LookupItem(id=7, nome="PR FESR")],
)


def adesso() -> datetime:
    return datetime.now(timezone.utc)


def oggi() -> date:
    return bandi_service.today_italy()


def _iso(minuti_fa: float = 0) -> str:
    return (adesso() - timedelta(minutes=minuti_fa)).isoformat()


def _ts(valore) -> datetime | None:
    if not valore:
        return None
    return datetime.fromisoformat(str(valore).replace("Z", "+00:00"))


def errore_rpc(detail: str) -> APIError:
    return APIError({"message": detail, "code": "P0001", "hint": None, "details": detail})


def errore_pg(code: str) -> APIError:
    return APIError({"message": "vincolo", "code": code, "hint": None, "details": "riga (x)"})


# ------------------------------------------------------------ dati


def citazione(testo="Passaggio del bando", sezione="D1-p3") -> dict:
    return {"sezione": sezione, "testo": testo, "verificata": True,
            "fonte_etichetta": "Avviso — pag. 3"}


def snapshot_minimo(**voci) -> dict:
    """Snapshot valido senza estrazione (voci `modificata`)."""
    dati = {"versione": 1, "fonte": None,
            "modalita": {"valore": "ammesso", "origine_voce": "modificata"}}
    dati.update(voci)
    return dati


def snapshot_confermato() -> dict:
    """Snapshot «confermo tutte le voci verificate» dell'estrazione VERA
    (post_elabora), con la regola finanziaria RF1."""
    return {"versione": 1, "fonte": None, **conferma_tutto(regole_estratte())}


def regola_rf1() -> dict:
    voce = next(v for v in snapshot_confermato()["regole_finanziarie"] if v["id"] == "RF1")
    return {k: voce[k] for k in ("id", "descrizione", "ambito", "numeratore", "denominatore",
                                 "operatore", "soglia", "soglia_variabile",
                                 "soglia_coefficiente", "unita")}


def riga_call(**modifiche) -> dict:
    riga = {
        "id": str(uuid.uuid4()), "company_profile_id": COMPANY, "family_parent_id": OWNER,
        "creato_da": OWNER, "bando_id": BANDO_ID, "bando_slug": SLUG,
        "bando_titolo": "Bando reti di impresa 2026",
        "bando_scadenza": (oggi() + timedelta(days=120)).isoformat(),
        "bando_programma_id": 7, "bando_tipologia_id": 2, "bando_stato_effettivo": "aperto",
        "bando_verificato_at": _iso(), "bando_mancante_dal": None,
        "ruolo_creatore": "capofila", "forma_aggregazione_prevista": None, "anonima": True,
        "titolo": None, "descrizione_pubblica": None, "dettagli_riservati": None,
        "profilo_partner_ideale": None, "budget_fascia": None, "budget_progetto_eur": None,
        "quota_creatore_pct": None, "scadenza_call": None, "visibilita": "pubblica",
        "stato": "bozza", "motivo_chiusura": None, "override_non_ammesso_motivo": None,
        "partenariato_ref": None, "regole_partenariato": None, "regole_confermate_at": None,
        "esclusivita": False, "ai_check_id": None, "wizard_passo": 1, "versione": 0,
        "pubblicata_at": None, "chiusa_at": None, "sospesa_at": None, "sospeso_motivo": None,
        "sospeso_da": None, "stato_prima_sospensione": None,
        "ai_posizioni_stato": None, "ai_posizioni_proposta": None,
        "ai_posizioni_avviata_at": None, "ai_posizioni_esecuzione_id": None,
        "ai_posizioni_errore": None, "ai_testi_stato": None, "ai_testi_proposta": None,
        "ai_testi_avviata_at": None, "ai_testi_esecuzione_id": None, "ai_testi_errore": None,
        "created_at": _iso(), "updated_at": _iso(),
    }
    riga.update(modifiche)
    return riga


def company_data(company=COMPANY, piva=PIVA, **modifiche) -> dict:
    riga = {
        "company_profile_id": company, "piva_fetched": piva, "sandbox": False,
        "denominazione": "ROSSI MECCANICA SRL", "stato_impresa": "Attiva",
        "derived": {
            "ateco_principale": "62.01.00", "ateco_divisione": "62", "ateco_secondari": [],
            "regione_nome": "LOMBARDIA", "regione_id": 3, "regioni_ids": [3],
            "classe_dimensionale": "piccola", "fascia_fatturato": "500k_2m",
        },
        "raw": {"companyDetails": {"companyName": "ROSSI MECCANICA SRL", "vatCode": piva},
                "webAndSocial": {"website": "https://www.rossimeccanica.it"}},
    }
    riga.update(modifiche)
    return riga


# ------------------------------------------------------------ primario finto


class FakeQuery:
    def __init__(self, db, tabella: str):
        self.db, self.tabella = db, tabella
        self.op, self.payload, self.filtri = "select", None, []
        self.ordine: tuple[str, bool] | None = None
        self.intervallo: tuple[int, int] | None = None
        self.massimo: int | None = None
        self.conta = None
        self.on_conflict = None

    def select(self, *a, count=None, **k):
        self.conta = count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None, **k):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def eq(self, c, v):
        self.filtri.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.filtri.append(("in", c, [str(x) for x in v]))
        return self

    def is_(self, c, v):
        self.filtri.append(("is", c, v))
        return self

    def order(self, colonna, desc=False, **k):
        self.ordine = (colonna, desc)
        return self

    def range(self, a, b):
        self.intervallo = (a, b)
        return self

    def limit(self, n):
        self.massimo = n
        return self

    def _ok(self, riga: dict) -> bool:
        for tipo, colonna, valore in self.filtri:
            attuale = riga.get(colonna)
            if tipo == "eq" and (attuale is None or str(attuale) != str(valore)):
                return False
            if tipo == "in" and str(attuale) not in valore:
                return False
            if tipo == "is" and valore == "null" and attuale is not None:
                return False
        return True

    async def execute(self):
        db = self.db
        db.ops.append((self.tabella, self.op, copy.deepcopy(self.payload), list(self.filtri)))
        if self.tabella == "ai_checks" and self.op != "select":
            raise AssertionError("il modulo partenariati non scrive mai in ai_checks")
        guasto = db.guasti.get((self.tabella, self.op))
        if guasto is not None:
            raise guasto
        righe = db.tabelle.setdefault(self.tabella, [])
        if self.op == "select":
            trovate = [copy.deepcopy(r) for r in righe if self._ok(r)]
            if self.ordine:
                colonna, disc = self.ordine
                trovate.sort(key=lambda r: (r.get(colonna) is None, str(r.get(colonna))),
                             reverse=disc)
            totale = len(trovate)
            if self.intervallo:
                trovate = trovate[self.intervallo[0]: self.intervallo[1] + 1]
            if self.massimo is not None:
                trovate = trovate[: self.massimo]
            return SimpleNamespace(data=trovate, count=totale if self.conta else None)
        if self.op == "insert":
            nuove = self.payload if isinstance(self.payload, list) else [self.payload]
            inserite = [db.inserisci(self.tabella, dict(r)) for r in nuove]
            return SimpleNamespace(data=inserite, count=None)
        if self.op == "upsert":
            return SimpleNamespace(data=db.upsert(self.tabella, self.payload, self.on_conflict))
        aggiornate = []
        for riga in righe:
            if self._ok(riga):
                riga.update(copy.deepcopy(self.payload))
                aggiornate.append(copy.deepcopy(riga))
        return SimpleNamespace(data=aggiornate, count=None)


CAMPI_BOZZA = (
    "titolo", "descrizione_pubblica", "dettagli_riservati", "profilo_partner_ideale",
    "scadenza_call", "visibilita", "budget_fascia", "budget_progetto_eur",
    "quota_creatore_pct", "ruolo_creatore", "forma_aggregazione_prevista", "anonima",
    "override_non_ammesso_motivo", "partenariato_ref", "ai_check_id", "wizard_passo",
)
WHITELIST_PUBBLICATA = (
    "descrizione_pubblica", "dettagli_riservati", "profilo_partner_ideale", "scadenza_call",
    "visibilita", "budget_fascia", "budget_progetto_eur", "quota_creatore_pct",
)
CHIAVI_REQUISITO = ("id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine",
                    "rif_origine", "citazione", "copertura_creatore", "copertura_fonte",
                    "copertura_nota")
CHIAVI_POSIZIONE = ("id", "titolo", "ruolo", "tipi_soggetto", "competenze", "ateco_divisioni",
                    "regioni", "territorio_modalita", "paesi", "dimensioni",
                    "quota_ipotizzata_pct", "numero", "requisiti_ids", "note")
CAMPI_REGOLA = ("id", "descrizione", "ambito", "numeratore", "denominatore", "operatore",
                "soglia", "soglia_variabile", "soglia_coefficiente", "unita")
SERVIZI_CALL = ("partner_call_posizioni", "partner_call_testi")


def _etichetta(n: int) -> str:
    lettere = ""
    while n > 0:
        n -= 1
        lettere = chr(65 + n % 26) + lettere
        n //= 26
    return lettere


class FakeDb:
    """Gemello in memoria delle tabelle e delle RPC della 0037 usate dal
    servizio (più `fn_partenariati_snapshot` e il rate limit)."""

    def __init__(self):
        self.tabelle: dict[str, list[dict]] = {
            "profiles": [dict(USER_OWNER), {"id": ALTRO_OWNER}],
            "company_profiles": [
                {"id": COMPANY, "parent_id": OWNER, "ragione_sociale": "Rossi Meccanica S.r.l.",
                 "partita_iva": PIVA, "codice_fiscale": PIVA, "sito_web": "www.rossimeccanica.it",
                 "settore_id": 86, "deleted_at": None, "archived_at": None},
                {"id": COMPANY_B, "parent_id": OWNER, "ragione_sociale": "Bianchi Logistica S.p.A.",
                 "partita_iva": PIVA_B, "codice_fiscale": PIVA_B, "sito_web": None,
                 "settore_id": None, "deleted_at": None, "archived_at": None},
                {"id": ALTRA_COMPANY, "parent_id": ALTRO_OWNER, "ragione_sociale": "Neri Srl",
                 "partita_iva": "11111111115", "codice_fiscale": None, "sito_web": None,
                 "settore_id": None, "deleted_at": None, "archived_at": None},
            ],
            "company_data": [company_data(), company_data(COMPANY_B, PIVA_B,
                                                          denominazione="BIANCHI LOGISTICA SPA")],
            "company_people": [
                {"company_profile_id": COMPANY, "nome": "Mario", "cognome": "Rossi",
                 "codice_fiscale": "RSSMRA80A01H501U", "is_legale_rappresentante": True},
                {"company_profile_id": COMPANY, "nome": "Giulia", "cognome": "Bianchini",
                 "codice_fiscale": None, "is_legale_rappresentante": False},
            ],
            "company_partner_profiles": [
                {"company_profile_id": COMPANY, "tipi_soggetto": [],
                 "competenze": ["sviluppo_software", "intelligenza_artificiale_dati"],
                 "certificazioni": [], "esperienze": []},
            ],
            "partner_calls": [],
            "partner_call_requisiti": [],
            "partner_call_posizioni": [],
            "partner_call_versioni": [],
            "partner_segnalazioni": [],
            "bando_partenariato": [],
            "ai_checks": [],
            "notifications": [],
            "api_usage_events": [],
        }
        # Limiti del piano per owner (fn_partenariati_limiti): None = illimitato.
        self.limiti: dict[str, int | None] = {OWNER: 3, ALTRO_OWNER: 3}
        self.esecuzioni: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.ops: list = []
        self.rpcs: list = []
        self.guasti: dict = {}
        self.rpc_errori: dict[str, str] = {}
        self.attese: dict = {}
        self.rate: dict[str, int] = {}

    # -- accesso
    def table(self, nome):
        return FakeQuery(self, nome)

    def rpc(self, nome, params):
        self.rpcs.append((nome, copy.deepcopy(params)))
        db = self

        class _Rpc:
            async def execute(self_inner):
                attesa = db.attese.pop(nome, None)
                if attesa is not None:
                    await attesa.wait()
                if nome in db.rpc_errori:
                    raise errore_rpc(db.rpc_errori[nome])
                return SimpleNamespace(data=getattr(db, f"_{nome}")(copy.deepcopy(params)))

        return _Rpc()

    # -- aiuti
    def chiamate(self, nome):
        return [p for n, p in self.rpcs if n == nome]

    def righe(self, tabella, **filtri) -> list[dict]:
        return [r for r in self.tabelle[tabella]
                if all(str(r.get(k)) == str(v) for k, v in filtri.items())]

    def call(self, call_id) -> dict:
        return next(r for r in self.tabelle["partner_calls"] if r["id"] == str(call_id))

    @property
    def usage(self) -> list[dict]:
        return self.tabelle["api_usage_events"]

    def con_call(self, **modifiche) -> dict:
        riga = riga_call(**modifiche)
        self.tabelle["partner_calls"].append(riga)
        return riga

    def con_requisito(self, call_id, **modifiche) -> dict:
        esistenti = self.righe("partner_call_requisiti", call_id=call_id)
        riga = {
            "id": str(uuid.uuid4()), "call_id": str(call_id), "origine": "manuale",
            "rif_origine": None, "etichetta": _etichetta(len(esistenti) + 1),
            "testo": "Competenze di prototipazione", "criterio": {"tipo": "manuale"},
            "ambito": "consorzio", "copertura_creatore": "non_valutabile",
            "copertura_fonte": "nessuna", "copertura_nota": None, "cercato": True,
            "citazione": None, "ordine": len(esistenti), "created_at": _iso(),
            "updated_at": _iso(),
        }
        riga.update(modifiche)
        self.tabelle["partner_call_requisiti"].append(riga)
        return riga

    def con_posizione(self, call_id, **modifiche) -> dict:
        esistenti = self.righe("partner_call_posizioni", call_id=call_id)
        riga = {
            "id": str(uuid.uuid4()), "call_id": str(call_id), "titolo": "Organismo di ricerca",
            "ruolo": "partner", "tipi_soggetto": ["organismo_ricerca"], "competenze": [],
            "ateco_divisioni": [], "regioni": [], "territorio_modalita": "qualsiasi",
            "paesi": [], "dimensioni": [], "quota_ipotizzata_pct": 30.0, "numero": 1,
            "requisiti_ids": [], "note": None, "ordine": len(esistenti),
            "created_at": _iso(), "updated_at": _iso(),
        }
        riga.update(modifiche)
        self.tabelle["partner_call_posizioni"].append(riga)
        return riga

    def call_pronta(self, **modifiche) -> dict:
        """Bozza completa, pubblicabile."""
        base = {
            "titolo": "Cerchiamo un organismo di ricerca per la prototipazione",
            "descrizione_pubblica": "Progetto di innovazione nella logistica sostenibile.",
            "profilo_partner_ideale": "Un laboratorio con esperienza nei prototipi.",
            "regole_partenariato": snapshot_minimo(), "regole_confermate_at": _iso(),
            "budget_fascia": "500k_1m", "budget_progetto_eur": float(CANARY_BUDGET) / 2,
            "quota_creatore_pct": 60.0, "dettagli_riservati": CANARY_RISERVATI,
        }
        base.update(modifiche)
        call = self.con_call(**base)
        requisito = self.con_requisito(call["id"])
        self.con_posizione(call["id"], requisiti_ids=[requisito["id"]])
        return call

    def con_estrazione(self, modalita="obbligatorio", **modifiche) -> "FakeDb":
        riga = {"bando_id": BANDO_ID, "stato": "pronta", "esito": "estratta",
                "modalita_effettiva": modalita, "regole": regole_estratte(),
                "estratta_at": "2026-09-01T10:00:00+00:00", "prompt_version": 1}
        riga.update(modifiche)
        self.tabelle["bando_partenariato"] = [riga]
        return self

    def con_ai_check(self, report, **modifiche) -> dict:
        riga = {"id": str(uuid.uuid4()), "company_profile_id": COMPANY, "user_id": OWNER,
                "family_parent_id": OWNER, "bando_id": BANDO_ID, "bando_slug": SLUG,
                "bando_titolo": "Bando", "status": "ready", "report": report,
                "created_at": _iso(5), "ready_at": _iso(4)}
        riga.update(modifiche)
        self.tabelle["ai_checks"].append(riga)
        return riga

    def spesa_altri(self) -> int:
        return sum(e["costo_riservato_cents"] if e["cost_cents"] is None else e["cost_cents"]
                   for e in self.esecuzioni.values())

    # -- tabelle con vincoli
    def inserisci(self, tabella: str, riga: dict) -> dict:
        riga.setdefault("created_at", _iso())
        if tabella == "partner_segnalazioni":
            riga.setdefault("stato", "ricevuta")
            for r in self.tabelle[tabella]:
                if (r["oggetto_tipo"], r["oggetto_id"], r["segnalante_user_id"]) == (
                    riga["oggetto_tipo"], riga["oggetto_id"], riga["segnalante_user_id"]
                ) and r["stato"] in ("ricevuta", "in_esame"):
                    raise errore_pg("23505")
            assert riga["buona_fede"] is True
        self.tabelle.setdefault(tabella, []).append(riga)
        return copy.deepcopy(riga)

    def upsert(self, tabella: str, righe, on_conflict):
        assert tabella == "notifications" and on_conflict == "user_id,dedup_key"
        inserite = []
        for riga in righe if isinstance(righe, list) else [righe]:
            if any(r["user_id"] == riga["user_id"] and r["dedup_key"] == riga["dedup_key"]
                   for r in self.tabelle[tabella]):
                continue
            self.tabelle[tabella].append(dict(riga))
            inserite.append(dict(riga))
        return inserite

    # -- funzioni interne della 0037
    def _azienda(self, company, owner=None, *, viva=True):
        for r in self.tabelle["company_profiles"]:
            if r["id"] == str(company) and (owner is None or r["parent_id"] == str(owner)):
                if viva and (r.get("deleted_at") or r.get("archived_at")):
                    return None
                return r
        return None

    def _blocca_azienda(self, p, viva=True):
        if not any(r["id"] == p["p_owner"] for r in self.tabelle["profiles"]):
            raise errore_rpc("owner_not_found")
        if self._azienda(p["p_company"], p["p_owner"], viva=viva) is None:
            raise errore_rpc("company_not_found")

    def _blocca(self, p, viva=True) -> dict:
        self._blocca_azienda(p, viva)
        for r in self.tabelle["partner_calls"]:
            if (r["id"] == p["p_call"] and r["company_profile_id"] == p["p_company"]
                    and r["family_parent_id"] == p["p_owner"]):
                return r
        raise errore_rpc("call_not_found")

    def _attore(self, p, chiave="p_attore"):
        if p.get(chiave) is None or p[chiave] != p["p_owner"]:
            raise errore_rpc("attore_non_titolare")

    def _limite(self, owner):
        return self.limiti.get(owner, 0)

    def _usate(self, owner) -> int:
        return sum(
            1 for c in self.tabelle["partner_calls"]
            if c["family_parent_id"] == owner
            and c["stato"] in ("pubblicata", "sospesa_moderazione")
            and self._azienda(c["company_profile_id"]) is not None
        )

    def _contenuto(self, call_id) -> dict:
        call = {k: v for k, v in self.call(call_id).items()
                if not k.startswith("ai_") and k not in ("versione", "updated_at")}
        return {
            "call": copy.deepcopy(call),
            "requisiti": [{k: v for k, v in r.items() if k not in ("call_id", "created_at",
                                                                  "updated_at")}
                          for r in sorted(self.righe("partner_call_requisiti", call_id=call_id),
                                          key=lambda r: r["ordine"])],
            "posizioni": [{k: v for k, v in r.items() if k not in ("call_id", "created_at",
                                                                  "updated_at")}
                          for r in sorted(self.righe("partner_call_posizioni", call_id=call_id),
                                          key=lambda r: r["ordine"])],
        }

    def _nuova_versione(self, call_id, attore) -> int:
        call = self.call(call_id)
        call["versione"] += 1
        self.tabelle["partner_call_versioni"].append({
            "call_id": call_id, "versione": call["versione"],
            "snapshot": self._contenuto(call_id), "modificato_da": attore,
            "created_at": _iso(),
        })
        return call["versione"]

    def _regola_ok(self, regola, regole) -> bool:
        if not isinstance(regola, dict) or not regola.get("id"):
            return False
        for voce in (regole or {}).get("regole_finanziarie") or []:
            if voce.get("origine_voce") in ("confermata", "modificata") and all(
                regola.get(k) == voce.get(k) for k in CAMPI_REGOLA
            ):
                return True
        return False

    # -- RPC della 0037
    def _fn_partner_call_crea_bozza(self, p):
        self._attore(p)
        bando = p["p_bando"]
        if not isinstance(bando, dict) or set(bando) - {
            "id", "slug", "titolo", "scadenza", "programma_id", "tipologia_id", "stato_effettivo"
        } or not str(bando.get("id", "")).isdigit() or not (bando.get("titolo") or "").strip():
            raise errore_rpc("parametri_non_validi")
        dati = p["p_dati"] or {}
        if set(dati) - set(CAMPI_BOZZA):
            raise errore_rpc("campo_non_modificabile")
        if not dati.get("ruolo_creatore"):
            raise errore_rpc("dati_non_validi")
        self._blocca_azienda(p)
        limite = self._limite(p["p_owner"])
        if limite is not None and limite <= 0:
            raise errore_rpc("piano_non_include_call")
        if p["p_max_bozze"] is not None and sum(
            1 for c in self.tabelle["partner_calls"]
            if c["company_profile_id"] == p["p_company"] and c["stato"] == "bozza"
        ) >= max(p["p_max_bozze"], 0):
            raise errore_rpc("troppe_bozze")
        if any(c["company_profile_id"] == p["p_company"] and c["bando_id"] == int(bando["id"])
               and c["stato"] in ("bozza", "pubblicata", "sospesa_moderazione")
               for c in self.tabelle["partner_calls"]):
            raise errore_rpc("call_gia_presente")
        riga = riga_call(
            company_profile_id=p["p_company"], family_parent_id=p["p_owner"],
            creato_da=p["p_attore"], bando_id=int(bando["id"]), bando_slug=bando["slug"],
            bando_titolo=bando["titolo"], bando_scadenza=bando.get("scadenza"),
            bando_programma_id=bando.get("programma_id"),
            bando_tipologia_id=bando.get("tipologia_id"),
            bando_stato_effettivo=bando.get("stato_effettivo"),
            **{k: v for k, v in dati.items() if v is not None},
        )
        self.tabelle["partner_calls"].append(riga)
        self.audit.append({"action": "partenariato.call_creata", "call_id": riga["id"]})
        return copy.deepcopy(riga)

    def _fn_partner_call_aggiorna(self, p):
        self._attore(p)
        if not isinstance(p["p_campi"], dict):
            raise errore_rpc("parametri_non_validi")
        call = self._blocca(p)
        if call["stato"] not in ("bozza", "pubblicata"):
            raise errore_rpc("stato_call_non_valido")
        campi = p["p_campi"]
        if set(campi) - set(CAMPI_BOZZA):
            raise errore_rpc("campo_non_modificabile")
        nuova = {**call, **campi}
        if call["stato"] == "pubblicata":
            for k in campi:
                if k not in WHITELIST_PUBBLICATA and nuova[k] != call[k]:
                    raise errore_rpc("campo_non_modificabile")
            if not (nuova.get("descrizione_pubblica") or "").strip() or not nuova["scadenza_call"]:
                raise errore_rpc("call_incompleta")
            if nuova["scadenza_call"] != call["scadenza_call"] and (
                str(nuova["scadenza_call"]) < oggi().isoformat()
                or (call["bando_scadenza"] and str(nuova["scadenza_call"]) > call["bando_scadenza"])
            ):
                raise errore_rpc("scadenza_call_non_valida")
        cambiati = sorted(k for k in campi if nuova[k] != call[k])
        if not cambiati:
            return copy.deepcopy(call)
        call.update(campi, updated_at=_iso())
        if call["stato"] == "pubblicata":
            self._nuova_versione(call["id"], p["p_attore"])
            self.audit.append({"action": "partenariato.call_modificata", "campi": cambiati})
        return copy.deepcopy(call)

    def _fn_partner_call_conferma_regole(self, p):
        self._attore(p)
        call = self._blocca(p)
        if call["stato"] != "bozza":
            raise errore_rpc("stato_call_non_valido")
        regole = p["p_regole"]
        if (p["p_esclusivita"] is None or not isinstance(regole, dict)
                or regole.get("versione") != 1 or not isinstance(regole.get("modalita"), dict)
                or any(v.get("origine_voce") == "aggiunta"
                       for v in regole.get("regole_finanziarie") or [])):
            raise errore_rpc("regole_non_valide")
        call.update(regole_partenariato=regole, esclusivita=p["p_esclusivita"],
                    regole_confermate_at=_iso())
        return copy.deepcopy(call)

    def _fn_partner_call_sostituisci_requisiti(self, p):
        self._attore(p)
        call = self._blocca(p)
        if call["stato"] not in ("bozza", "pubblicata"):
            raise errore_rpc("stato_call_non_valido")
        voci = p["p_requisiti"]
        if not isinstance(voci, list) or len(voci) > 40:
            raise errore_rpc("requisiti_non_validi")
        esistenti = {r["id"]: r for r in self.righe("partner_call_requisiti", call_id=call["id"])}
        tenuti, usate = [], []
        for voce in voci:
            if set(voce) - set(CHIAVI_REQUISITO):
                raise errore_rpc("requisiti_non_validi")
            if voce.get("id") is not None:
                if voce["id"] in tenuti or voce["id"] not in esistenti:
                    raise errore_rpc("requisiti_non_validi")
                tenuti.append(voce["id"])
            etichetta = (voce.get("etichetta") or "").strip() or (
                esistenti[voce["id"]]["etichetta"] if voce.get("id") else None)
            if etichetta:
                if etichetta in usate:
                    raise errore_rpc("requisiti_non_validi")
                usate.append(etichetta)
            criterio = voce.get("criterio") or {}
            if criterio.get("tipo") == "regola_finanziaria" and (
                voce.get("origine") != "regola_finanziaria" or not call["regole_confermate_at"]
                or not self._regola_ok(criterio.get("regola"), call["regole_partenariato"])
            ):
                raise errore_rpc("requisiti_non_validi")
        prima = self._contenuto(call["id"])
        nuove, auto, nuove_esplicite = [], 0, {}
        for ordine, voce in enumerate(voci):
            identificativo = voce.get("id")
            etichetta = (voce.get("etichetta") or "").strip() or None
            esplicita_nuova = etichetta is not None and identificativo is None
            if etichetta is None and identificativo:
                etichetta = esistenti[identificativo]["etichetta"]
            if etichetta is None:
                while True:
                    auto += 1
                    etichetta = _etichetta(auto)
                    if etichetta not in usate:
                        break
                usate.append(etichetta)
            identificativo = identificativo or str(uuid.uuid4())
            if esplicita_nuova:
                nuove_esplicite[etichetta] = identificativo
            nuove.append({
                **{k: voce.get(k) for k in CHIAVI_REQUISITO if k not in ("id", "etichetta")},
                "id": identificativo, "call_id": call["id"], "etichetta": etichetta,
                "ambito": voce.get("ambito") or "consorzio", "cercato": bool(voce.get("cercato")),
                "ordine": ordine, "created_at": _iso(), "updated_at": _iso(),
            })
        mappa = {rid: nuove_esplicite[r["etichetta"]] for rid, r in esistenti.items()
                 if rid not in tenuti and r["etichetta"] in nuove_esplicite}
        self.tabelle["partner_call_requisiti"] = [
            r for r in self.tabelle["partner_call_requisiti"] if r["call_id"] != call["id"]
        ] + nuove
        for posizione in self.righe("partner_call_posizioni", call_id=call["id"]):
            posizione["requisiti_ids"] = [
                rid if rid in tenuti else mappa[rid] for rid in posizione["requisiti_ids"]
                if rid in tenuti or rid in mappa
            ]
        if call["stato"] == "pubblicata":
            if not any(r["cercato"] for r in nuove):
                raise errore_rpc("call_incompleta")
            if self._contenuto(call["id"]) != prima:
                self._nuova_versione(call["id"], p["p_attore"])
        return copy.deepcopy(sorted(nuove, key=lambda r: r["ordine"]))

    def _fn_partner_call_sostituisci_posizioni(self, p):
        self._attore(p)
        call = self._blocca(p)
        if call["stato"] not in ("bozza", "pubblicata"):
            raise errore_rpc("stato_call_non_valido")
        voci = p["p_posizioni"]
        if not isinstance(voci, list) or len(voci) > 10:
            raise errore_rpc("posizioni_non_valide")
        esistenti = {r["id"] for r in self.righe("partner_call_posizioni", call_id=call["id"])}
        requisiti = {r["id"] for r in self.righe("partner_call_requisiti", call_id=call["id"])}
        nuove, tenuti = [], []
        for ordine, voce in enumerate(voci):
            if set(voce) - set(CHIAVI_POSIZIONE):
                raise errore_rpc("posizioni_non_valide")
            if voce.get("id") is not None:
                if voce["id"] not in esistenti or voce["id"] in tenuti:
                    raise errore_rpc("posizioni_non_valide")
                tenuti.append(voce["id"])
            if not set(voce.get("requisiti_ids") or []) <= requisiti:
                raise errore_rpc("posizioni_non_valide")
            nuove.append({
                "ruolo": "partner", "tipi_soggetto": [], "competenze": [], "ateco_divisioni": [],
                "regioni": [], "territorio_modalita": "qualsiasi", "paesi": [], "dimensioni": [],
                "quota_ipotizzata_pct": None, "numero": 1, "requisiti_ids": [], "note": None,
                **{k: v for k, v in voce.items() if v is not None},
                "id": voce.get("id") or str(uuid.uuid4()), "call_id": call["id"],
                "ordine": ordine, "created_at": _iso(), "updated_at": _iso(),
            })
        prima = self._contenuto(call["id"])
        self.tabelle["partner_call_posizioni"] = [
            r for r in self.tabelle["partner_call_posizioni"] if r["call_id"] != call["id"]
        ] + nuove
        if call["stato"] == "pubblicata":
            if not nuove:
                raise errore_rpc("call_incompleta")
            if self._contenuto(call["id"]) != prima:
                self._nuova_versione(call["id"], p["p_attore"])
        return copy.deepcopy(nuove)

    def _identita_ok(self, company, richiedi) -> bool:
        azienda = self._azienda(company)
        return any(
            d["company_profile_id"] == company and d["piva_fetched"] == azienda["partita_iva"]
            and (d.get("stato_impresa") or "").strip().lower() == "attiva"
            and (richiedi is False or d["sandbox"] is False)
            for d in self.tabelle["company_data"]
        )

    def _fn_partner_call_pubblica(self, p):
        self._attore(p)
        call = self._blocca(p)
        if call["stato"] != "bozza":
            raise errore_rpc("stato_call_non_valido")
        if not self._identita_ok(p["p_company"], p["p_richiedi_non_sandbox"]):
            raise errore_rpc("identita_non_verificata")
        if not call["anonima"]:
            raise errore_rpc("rappresentante_non_verificato")
        if (p["p_bando_stato"] or "").strip().lower() not in ("aperto",
                                                              "in apertura prossimamente"):
            raise errore_rpc("bando_non_disponibile")
        scadenza = p["p_scadenza_call"] or call["scadenza_call"]
        if not scadenza or scadenza < oggi().isoformat() or (
            p["p_bando_scadenza"] and scadenza > p["p_bando_scadenza"]
        ):
            raise errore_rpc("scadenza_call_non_valida")
        requisiti = self.righe("partner_call_requisiti", call_id=call["id"])
        if (not (call["titolo"] or "").strip() or not (call["descrizione_pubblica"] or "").strip()
                or not call["regole_confermate_at"]
                or not self.righe("partner_call_posizioni", call_id=call["id"])
                or not any(r["cercato"] for r in requisiti)):
            raise errore_rpc("call_incompleta")
        if any((r.get("criterio") or {}).get("tipo") == "regola_finanziaria"
               and not self._regola_ok(r["criterio"].get("regola"), call["regole_partenariato"])
               for r in requisiti):
            raise errore_rpc("requisiti_non_validi")
        limite = self._limite(p["p_owner"])
        if limite is not None and limite <= 0:
            raise errore_rpc("piano_non_include_call")
        if limite is not None and self._usate(p["p_owner"]) >= limite:
            raise errore_rpc("limite_call_raggiunto")
        call.update(stato="pubblicata", pubblicata_at=_iso(), scadenza_call=scadenza,
                    bando_stato_effettivo=p["p_bando_stato"],
                    bando_scadenza=p["p_bando_scadenza"], bando_mancante_dal=None)
        self._nuova_versione(call["id"], p["p_attore"])
        self.audit.append({"action": "partenariato.call_pubblicata", "call_id": call["id"]})
        return copy.deepcopy(call)

    def _fn_partner_call_chiudi(self, p):
        if p["p_esito"] not in ("completata", "annullata"):
            raise errore_rpc("parametri_non_validi")
        self._attore(p)
        call = self._blocca(p, viva=False)
        if (p["p_esito"] == "completata" and call["stato"] != "pubblicata") or (
            p["p_esito"] == "annullata"
            and call["stato"] not in ("bozza", "pubblicata", "sospesa_moderazione")
        ):
            raise errore_rpc("stato_call_non_valido")
        stato = "chiusa_completata" if p["p_esito"] == "completata" else "chiusa_annullata"
        call.update(stato=stato, chiusa_at=_iso(), motivo_chiusura=f"creatore_{p['p_esito']}")
        return copy.deepcopy(call)

    def _fn_partner_call_chiudi_auto(self, p):
        if p["p_nuovo_stato"] not in ("scaduta", "chiusa_annullata") or p["p_motivo"] not in (
            "scadenza_call", "bando_chiuso", "bando_sospeso", "bando_revocato",
            "bando_non_disponibile", "azienda_non_disponibile",
        ):
            raise errore_rpc("parametri_non_validi")
        call = next((c for c in self.tabelle["partner_calls"] if c["id"] == p["p_call"]), None)
        if call is None or call["stato"] not in ("bozza", "pubblicata"):
            return False
        stato = "chiusa_annullata" if call["stato"] == "bozza" else p["p_nuovo_stato"]
        call.update(stato=stato, chiusa_at=_iso(), motivo_chiusura=p["p_motivo"])
        self.audit.append({"action": "partenariato.call_chiusa", "actor": None,
                           "call_id": call["id"], "motivo": p["p_motivo"]})
        return True

    # -- job AI (0037 sul registro della 0034)
    def _chiudi_esecuzione(self, eid, stato, cost, tin, tout, model, errore):
        e = self.esecuzioni.get(eid)
        if not e or e["stato"] != "in_corso":
            return
        e.update(stato=stato, cost_cents=cost, input_tokens=tin or 0, output_tokens=tout or 0,
                 model=model or e["model"], errore_codice=errore,
                 llm_eseguito=bool((cost or 0) > 0 or (tin or 0) > 0 or (tout or 0) > 0))

    @staticmethod
    def _senza_llm(e) -> bool:
        return not e["llm_eseguito"] and (
            e["stato"] in ("riusata", "nessun_segnale")
            or (e["stato"] in ("errore", "interrotta") and e["cost_cents"] == 0))

    def _fn_partner_call_ai_esecuzione_interrotta(self, eid) -> bool:
        e = self.esecuzioni.get(eid)
        if not e or e["stato"] != "in_corso" or e["servizio"] not in SERVIZI_CALL:
            return False
        self.tabelle["api_usage_events"].append({
            "user_id": e["richiedente"], "family_parent_id": e["owner"], "provider": "anthropic",
            "service": e["servizio"], "outcome": "timeout_unknown",
            "cost_cents": e["costo_riservato_cents"],
            "request_meta": {"company_profile_id": e["company"], "esecuzione_id": eid,
                             "esito": "interrotta", "failsafe": True},
        })
        self._chiudi_esecuzione(eid, "interrotta", None, 0, 0, None, "interrotta")
        return True

    def _fn_partner_call_ai_prenota(self, p):
        if p["p_servizio"] not in SERVIZI_CALL:
            raise errore_rpc("parametri_non_validi")
        self._attore(p, "p_richiedente")
        call = self._blocca(p)
        if call["stato"] != "bozza":
            raise errore_rpc("stato_call_non_valido")
        prefisso = "ai_posizioni" if p["p_servizio"] == "partner_call_posizioni" else "ai_testi"
        if call[f"{prefisso}_stato"] == "in_corso":
            if _ts(call[f"{prefisso}_avviata_at"]) > adesso() - timedelta(minutes=10):
                raise errore_rpc("ai_in_corso")
            self._fn_partner_call_ai_esecuzione_interrotta(call[f"{prefisso}_esecuzione_id"])
        if p["p_limite_call"] is not None and sum(
            1 for e in self.esecuzioni.values()
            if e["company"] == p["p_company"] and e["bando_id"] == call["bando_id"]
            and e["servizio"] == p["p_servizio"] and not self._senza_llm(e)
        ) >= max(p["p_limite_call"], 0):
            raise errore_rpc("ai_limite_call")
        if p["p_limite_owner"] is not None and sum(
            1 for e in self.esecuzioni.values()
            if e["owner"] == p["p_owner"] and e["servizio"] == p["p_servizio"]
            and not self._senza_llm(e)
        ) >= max(p["p_limite_owner"], 0):
            raise errore_rpc("ai_limite_owner")
        budget = p["p_budget_cents"]
        if budget is None or budget <= 0 or (
            self.spesa_altri() + p["p_costo_riservato_cents"] > budget
        ):
            raise errore_rpc("ai_budget_esaurito")
        eid = str(uuid.uuid4())
        self.esecuzioni[eid] = {
            "id": eid, "servizio": p["p_servizio"], "gruppo": "altri", "origine": "call",
            "company": p["p_company"], "owner": p["p_owner"], "bando_id": call["bando_id"],
            "richiedente": p["p_richiedente"], "stato": "in_corso",
            "costo_riservato_cents": p["p_costo_riservato_cents"], "cost_cents": None,
            "input_tokens": 0, "output_tokens": 0, "model": None, "errore_codice": None,
            "llm_eseguito": False, "avviata_at": _iso(),
        }
        call.update({f"{prefisso}_stato": "in_corso", f"{prefisso}_avviata_at": _iso(),
                     f"{prefisso}_esecuzione_id": eid, f"{prefisso}_errore": None})
        return eid

    def _fn_partner_call_ai_concludi(self, p):
        if (not p["p_call"] or not p["p_esecuzione_id"] or p["p_servizio"] not in SERVIZI_CALL
                or p["p_job_stato"] not in ("pronta", "errore")
                or (p["p_job_stato"] == "pronta" and not isinstance(p["p_proposta"], dict))):
            raise errore_rpc("parametri_non_validi")
        prefisso = "ai_posizioni" if p["p_servizio"] == "partner_call_posizioni" else "ai_testi"
        call = next((c for c in self.tabelle["partner_calls"] if c["id"] == p["p_call"]), None)
        scritto = bool(call and call[f"{prefisso}_esecuzione_id"] == p["p_esecuzione_id"]
                       and call[f"{prefisso}_stato"] == "in_corso")
        if scritto:
            pronta = p["p_job_stato"] == "pronta"
            call.update({f"{prefisso}_stato": p["p_job_stato"],
                         f"{prefisso}_proposta": p["p_proposta"] if pronta else None,
                         f"{prefisso}_errore": None if pronta else p["p_job_errore"]})
        e = self.esecuzioni.get(p["p_esecuzione_id"])
        chiusa = bool(e and e["stato"] == "in_corso" and e["servizio"] == p["p_servizio"])
        if chiusa:
            self._chiudi_esecuzione(p["p_esecuzione_id"], p["p_stato"], p["p_cost_cents"],
                                    p["p_input_tokens"], p["p_output_tokens"], p["p_model"],
                                    p["p_errore"])
        return {"job_scritto": scritto, "esecuzione_chiusa": chiusa}

    def _fn_partner_call_ai_chiudi_stale(self, p):
        soglia = adesso() - timedelta(minutes=max(p["p_minuti"] or 10, 1))
        n = 0
        for call in self.tabelle["partner_calls"]:
            for prefisso in ("ai_posizioni", "ai_testi"):
                if (call[f"{prefisso}_stato"] == "in_corso"
                        and _ts(call[f"{prefisso}_avviata_at"]) <= soglia):
                    call.update({f"{prefisso}_stato": "errore",
                                 f"{prefisso}_errore": "interrotta",
                                 f"{prefisso}_proposta": None})
                    self._fn_partner_call_ai_esecuzione_interrotta(
                        call[f"{prefisso}_esecuzione_id"])
                    n += 1
        in_corso = {c[f"{x}_esecuzione_id"] for c in self.tabelle["partner_calls"]
                    for x in ("ai_posizioni", "ai_testi") if c[f"{x}_stato"] == "in_corso"}
        for eid, e in list(self.esecuzioni.items()):
            if (e["stato"] == "in_corso" and eid not in in_corso
                    and _ts(e["avviata_at"]) <= soglia
                    and self._fn_partner_call_ai_esecuzione_interrotta(eid)):
                n += 1
        return n

    def _fn_partenariati_snapshot(self, p):
        limite = self._limite(p["p_owner"])
        usate = self._usate(p["p_owner"])
        return {
            "call_attive": {"limite": limite, "usate": usate,
                            "residuo": None if limite is None else max(limite - usate, 0)},
            "candidature_mese": {"limite": 5, "usate": 0, "residuo": 5,
                                 "periodo_inizio": "2026-09-01", "periodo_fine": "2026-09-30"},
        }

    def _fn_consume_auth_rate_limit(self, p):
        self.rate[p["p_bucket"]] = self.rate.get(p["p_bucket"], 0) + 1
        return self.rate[p["p_bucket"]] <= p["p_limit"]


# ------------------------------------------------------------ catalogo finto


def riga_bando(**modifiche) -> dict:
    riga = {
        "id": BANDO_ID, "slug": SLUG, "titolo": "Bando reti di impresa 2026",
        "titolo_breve": "Reti 2026", "stato_processing": "completed",
        "programmi": {"id": 7}, "tipologie_bando": {"id": 2},
        "bando_regioni": [{"regioni": {"id": 3, "nome": "Lombardia"}}],
        "bando_settori": [], "bando_beneficiari": [],
        "bando_codici_ateco": [{"codici_ateco": {"id": 1, "codice": "62.01.00"}}],
    }
    riga.update(modifiche)
    return riga


def riga_bando_pubblico(**modifiche) -> dict:
    riga = {"id": BANDO_ID, "slug": SLUG, "stato_effettivo": "aperto",
            "data_scadenza": (oggi() + timedelta(days=120)).isoformat(),
            "ultimo_cambiamento_at": "2026-09-01T10:00:00+00:00"}
    riga.update(modifiche)
    return riga


class FakeSecondary:
    def __init__(self, bandi=None, pubblici=None):
        self.bandi = [riga_bando()] if bandi is None else bandi
        self.pubblici = [riga_bando_pubblico()] if pubblici is None else pubblici
        self.guasto_pubblico: Exception | None = None
        self.letture: list[str] = []

    def table(self, nome):
        sec = self

        class _Q:
            def __init__(self):
                self.filtri = []

            def select(self, *a, **k):
                return self

            def eq(self, c, v):
                self.filtri.append((c, v))
                return self

            def in_(self, c, v):
                self.filtri.append((c, list(v)))
                return self

            def limit(self, n):
                return self

            async def execute(self):
                sec.letture.append(nome)
                if nome == "bando_pubblico":
                    if sec.guasto_pubblico is not None:
                        raise sec.guasto_pubblico
                    ids = next(v for c, v in self.filtri if c == "id")
                    return SimpleNamespace(data=[r for r in sec.pubblici if r["id"] in ids])
                if nome == "bando":
                    return SimpleNamespace(data=[
                        copy.deepcopy(r) for r in sec.bandi
                        if all(r.get(c) == v for c, v in self.filtri)
                    ])
                raise AssertionError(f"tabella del catalogo inattesa: {nome}")

        return _Q()


# ------------------------------------------------------------ modello finto


def proposta_posizioni(**modifiche) -> PropostaPosizioni:
    voce = {
        "titolo": "Organismo di ricerca per i prototipi", "ruolo": "partner",
        "tipi_soggetto": ["organismo_ricerca", "universita"],
        "competenze": ["prototipazione_testing"], "ateco_divisioni": ["72"],
        "regioni": ["lombardia", "Atlantide"], "territorio_modalita": "sede_attuale",
        "paesi": ["it", "ZZ"], "dimensioni": [], "quota_ipotizzata_pct": 30.0, "numero": 1,
        "requisiti": ["A", "Q"], "motivazione": "Copre il requisito A",
    }
    voce.update(modifiche)
    return PropostaPosizioni(posizioni=[PosizioneAi.model_validate(voce)])


def bozza_testi(**modifiche) -> BozzaTestiCall:
    dati = {
        "titolo": "Cerchiamo un laboratorio per prototipi nella logistica",
        "descrizione_pubblica": "Un progetto di logistica sostenibile. Scrivici a info@rossi.it",
        "profilo_partner_ideale": "Un organismo di ricerca con esperienza, come Rossi Meccanica.",
    }
    dati.update(modifiche)
    return BozzaTestiCall.model_validate(dati)


class FakeAi:
    def __init__(self, *, errore: Exception | None = None, risposta=None, enabled: bool = True,
                 usage: AiUsage | None = None, attesa: asyncio.Event | None = None):
        self.enabled = enabled
        self.errore = errore
        self.risposta = risposta
        self.usage = usage or AiUsage(input_tokens=3_000, output_tokens=1_000)
        self.attesa = attesa
        self.chiamate: list[dict] = []

    async def genera(self, system, user_message, output_format, *, model=None, max_tokens=None,
                     timeout=None):
        self.chiamate.append({"system": system, "testo": user_message, "schema": output_format,
                              "model": model, "max_tokens": max_tokens, "timeout": timeout})
        if self.attesa is not None:
            await self.attesa.wait()
        if self.errore is not None:
            raise self.errore
        if self.risposta is not None:
            return self.risposta, self.usage
        if output_format is PropostaPosizioni:
            return proposta_posizioni(), self.usage
        return bozza_testi(), self.usage


# ------------------------------------------------------------ fixture


@pytest.fixture(autouse=True)
def stub_settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "PARTENARIATI_ATTIVO": "true",
        "PARTENARIATO_AI_MODEL": MODELLO,
        "PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI": "200",
        "OPENAPI_ENV": "production",
        "PARTNER_CALL_BOZZE_MAX": "5",
        "PARTNER_CALL_AI_LIMITE_GIORNO": "10",
        "PARTNER_CALL_AI_LIMITE_OWNER_GIORNO": "30",
        "PARTNER_CALL_AI_MAX_TOKENS": "6000",
        "PARTNER_CALL_AI_TIMEOUT_SECONDS": "90",
        "PARTNER_CALL_SCADENZA_DEFAULT_GIORNI": "60",
        "PARTNER_SEGNALAZIONI_LIMITE_GIORNO": "10",
        "PARTNER_CALL_AI_STALE_MINUTI": "10",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def catalogo(monkeypatch):
    """Lookup del catalogo e bilanci finti (nessuna rete)."""
    stato = SimpleNamespace(
        lookups_errore=None,
        esercizi=[
            EsercizioBilancio(anno=2023, valori={"fatturato": Decimal("1500000")}),
            EsercizioBilancio(anno=2024, valori={"fatturato": Decimal("1700000")}),
        ],
        completo=True,
    )

    async def get_lookups(secondary):
        if stato.lookups_errore is not None:
            raise stato.lookups_errore
        return LOOKUPS

    async def carica_bilanci(primary, company_id):
        return stato.esercizi, stato.completo

    async def carica_esercizi(primary, company_id):
        return stato.esercizi

    monkeypatch.setattr("app.services.lookup_service.get_lookups", get_lookups)
    monkeypatch.setattr("app.services.bilanci_service.carica_bilanci", carica_bilanci)
    monkeypatch.setattr("app.services.bilanci_service.carica_esercizi", carica_esercizi)
    return stato


@pytest.fixture
def spawned(monkeypatch):
    catturati: list = []
    monkeypatch.setattr(pcs, "_spawn", catturati.append)
    yield catturati
    for coro in catturati:
        coro.close()


def titolare(company=COMPANY) -> ActiveCompany:
    return ActiveCompany(company_id=company, owner_id=OWNER, editable=True)


def membro() -> ActiveCompany:
    return ActiveCompany(company_id=COMPANY, owner_id=OWNER, editable=False)


def altro_owner() -> ActiveCompany:
    return ActiveCompany(company_id=ALTRA_COMPANY, owner_id=ALTRO_OWNER, editable=True)


def crea_in(**campi) -> CallCreaIn:
    return CallCreaIn.model_validate({"bando_slug": SLUG, "ruolo_creatore": "capofila", **campi})


async def leggi(db, call, active=None, user=None, secondary=None):
    return await pcs.dettaglio(db, secondary or FakeSecondary(), active or titolare(),
                               user or USER_OWNER, call["id"])


async def attendi_codice(coro, code: str, status: int | None = None) -> AppError:
    with pytest.raises(AppError) as exc:
        await coro
    assert exc.value.code == code, (exc.value.code, exc.value.message)
    if status is not None:
        assert exc.value.status_code == status
    return exc.value


# ------------------------------------------------------------ crea bozza


class TestCreaBozza:
    async def test_bozza_con_snapshot_del_bando(self):
        db = FakeDb().con_estrazione()
        out = await pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER,
                                   crea_in(forma_aggregazione_prevista="ats"))
        [p] = db.chiamate("fn_partner_call_crea_bozza")
        assert (p["p_owner"], p["p_company"], p["p_attore"]) == (OWNER, COMPANY, OWNER)
        assert p["p_bando"] == {
            "id": BANDO_ID, "slug": SLUG, "titolo": "Bando reti di impresa 2026",
            "scadenza": (oggi() + timedelta(days=120)).isoformat(), "programma_id": 7,
            "tipologia_id": 2, "stato_effettivo": "aperto",
        }
        # il passo 1 si salva creando la bozza: si riprende dal 2
        assert p["p_dati"]["anonima"] is True and p["p_dati"]["wizard_passo"] == 2
        assert p["p_dati"]["partenariato_ref"] == {
            "prompt_version": 1, "estratta_at": "2026-09-01T10:00:00+00:00",
            "modalita_effettiva": "obbligatorio",
        }
        assert p["p_max_bozze"] == 5
        assert out.stato == "bozza" and out.editable is True and out.anonima is True
        assert out.bando.slug == SLUG and out.forma_aggregazione_prevista == "ats"
        assert out.gap.partenariato.stato == "pronta"
        assert out.limiti.call_attive.limite == 3
        codici = {m.codice for m in out.motivi_blocco}
        assert {"titolo_mancante", "regole_non_confermate", "posizioni_mancanti"} <= codici
        assert out.puo_pubblicare is False

    async def test_anonima_forzata(self):
        db = FakeDb()
        await attendi_codice(
            pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER, crea_in(anonima=False)),
            "nominativo_non_disponibile", 409,
        )
        assert db.chiamate("fn_partner_call_crea_bozza") == []

    async def test_non_ammesso_richiede_il_motivo(self):
        db = FakeDb().con_estrazione(modalita="non_ammesso")
        await attendi_codice(
            pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER, crea_in()),
            "partenariato_non_ammesso", 409,
        )
        motivo = "Il bando all'articolo 4 ammette le ATS tra imprese"
        out = await pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER,
                                   crea_in(override_non_ammesso_motivo=motivo))
        assert out.override_non_ammesso_motivo == motivo
        # la pubblicazione lo ricontrolla: con il motivo non è un blocco
        assert "partenariato_non_ammesso" not in {m.codice for m in out.motivi_blocco}

    @pytest.mark.parametrize("stato", ["chiuso", "sospeso", "revocato"])
    async def test_bando_non_aperto(self, stato):
        db = FakeDb()
        secondary = FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo=stato)])
        await attendi_codice(
            pcs.crea_bozza(db, secondary, titolare(), USER_OWNER, crea_in()),
            "bando_non_disponibile", 409,
        )

    async def test_bando_assente_dal_catalogo_live(self):
        db = FakeDb()
        await attendi_codice(
            pcs.crea_bozza(db, FakeSecondary(pubblici=[]), titolare(), USER_OWNER, crea_in()),
            "bando_non_disponibile", 409,
        )

    async def test_stato_del_bando_non_leggibile_fail_closed(self):
        db = FakeDb()
        secondary = FakeSecondary()
        secondary.guasto_pubblico = RuntimeError("rete")
        with pytest.raises(UpstreamError):
            await pcs.crea_bozza(db, secondary, titolare(), USER_OWNER, crea_in())
        assert db.chiamate("fn_partner_call_crea_bozza") == []

    async def test_in_apertura_ammesso(self):
        db = FakeDb()
        secondary = FakeSecondary(pubblici=[riga_bando_pubblico(
            stato_effettivo="in apertura prossimamente")])
        out = await pcs.crea_bozza(db, secondary, titolare(), USER_OWNER, crea_in())
        assert out.bando.stato_effettivo == "in apertura prossimamente"

    async def test_bando_inesistente_404(self):
        with pytest.raises(NotFoundError):
            await pcs.crea_bozza(FakeDb(), FakeSecondary(bandi=[]), titolare(), USER_OWNER,
                                 crea_in())

    async def test_membro_403_e_senza_azienda_404(self):
        await attendi_codice(
            pcs.crea_bozza(FakeDb(), FakeSecondary(), membro(), USER_MEMBRO, crea_in()),
            "forbidden", 403,
        )
        senza = ActiveCompany(company_id=None, owner_id=OWNER, editable=True)
        with pytest.raises(NotFoundError):
            await pcs.crea_bozza(FakeDb(), FakeSecondary(), senza, USER_OWNER, crea_in())

    @pytest.mark.parametrize(
        ("preparazione", "code", "status"),
        [
            (lambda db: db.limiti.update({OWNER: 0}), "piano_non_include_call", 403),
            (lambda db: db.con_call(), "call_gia_presente", 409),
            (lambda db: [db.con_call(bando_id=900 + i) for i in range(5)], "troppe_bozze", 409),
        ],
        ids=["piano", "doppione", "bozze"],
    )
    async def test_errori_della_rpc(self, preparazione, code, status):
        db = FakeDb()
        preparazione(db)
        await attendi_codice(
            pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER, crea_in()), code, status
        )

    async def test_seconda_bozza_dopo_la_chiusura(self):
        db = FakeDb()
        db.con_call(stato="chiusa_annullata", chiusa_at=_iso(),
                    motivo_chiusura="creatore_annullata")
        out = await pcs.crea_bozza(db, FakeSecondary(), titolare(), USER_OWNER, crea_in())
        assert out.stato == "bozza"


# ------------------------------------------------------------ lettura


class TestDettaglio:
    async def test_membro_legge_non_scrive(self):
        db = FakeDb()
        call = db.call_pronta()
        out = await leggi(db, call, membro(), USER_MEMBRO)
        assert out.editable is False and out.dettagli_riservati == CANARY_RISERVATI
        assert "solo_titolare" in {m.codice for m in out.motivi_blocco}
        await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), membro(), USER_MEMBRO, call["id"],
                         CallAggiornaIn(titolo="Un titolo abbastanza lungo")),
            "forbidden", 403,
        )

    async def test_advisor_con_a_attiva_non_vede_b(self):
        db = FakeDb()
        call_b = db.call_pronta(company_profile_id=COMPANY_B)
        with pytest.raises(NotFoundError):
            await leggi(db, call_b, titolare(COMPANY))
        with pytest.raises(NotFoundError):
            await pcs.aggiorna(db, FakeSecondary(), titolare(COMPANY), USER_OWNER, call_b["id"],
                               CallAggiornaIn(titolo="Un titolo abbastanza lungo"))
        with pytest.raises(NotFoundError):
            await pcs.chiudi(db, FakeSecondary(), titolare(COMPANY), USER_OWNER, call_b["id"],
                             ChiudiIn(esito="annullata"))
        assert db.chiamate("fn_partner_call_aggiorna") == []
        assert db.chiamate("fn_partner_call_chiudi") == []
        # con B attiva sì
        assert (await leggi(db, call_b, titolare(COMPANY_B))).id == uuid.UUID(call_b["id"])

    async def test_pubblicata_di_b_non_scrivibile_con_a_attiva(self, spawned):
        """Con A attiva la call PUBBLICATA di B ha il ruolo «pubblico»: nessuna
        scrittura arriva nemmeno alla RPC (che comunque risponderebbe
        call_not_found). Dal WP6 la lettura è la vista PUBBLICA (niente
        riservati, niente `editable`)."""
        db = FakeDb()
        call_b = db.call_pronta(company_profile_id=COMPANY_B, stato="pubblicata",
                                pubblicata_at=_iso(),
                                scadenza_call=(oggi() + timedelta(days=9)).isoformat())
        vista = await leggi(db, call_b, titolare())
        assert not hasattr(vista, "editable")
        assert CANARY_RISERVATI not in vista.model_dump_json()
        scritture = [
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"],
                         CallAggiornaIn(descrizione_pubblica="Altro")),
            pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"],
                       ChiudiIn(esito="annullata")),
            pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"],
                                PosizioniIn()),
            pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"],
                                RequisitiIn()),
            pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"]),
            pcs.avvia_proposta_testi(db, FakeSecondary(), FakeAi(), titolare(), USER_OWNER,
                                     call_b["id"]),
        ]
        for scrittura in scritture:
            with pytest.raises(NotFoundError):
                await scrittura
        assert [n for n, _ in db.rpcs if n.startswith("fn_partner_call")] == []
        with pytest.raises(NotFoundError):
            await pcs.anteprima(db, FakeSecondary(), titolare(), USER_OWNER, call_b["id"])

    async def test_un_altro_owner_non_vede_nemmeno_la_pubblicata_come_creatore(self):
        # Dal WP6 vede la vista pubblica, mai quella del creatore.
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=30)).isoformat())
        vista = await leggi(db, call, altro_owner(), USER_ALTRO)
        testo = vista.model_dump_json()
        assert not hasattr(vista, "editable") and not hasattr(vista, "budget_progetto_eur")
        assert CANARY_RISERVATI not in testo and COMPANY not in testo and OWNER not in testo
        # la bozza di un altro owner resta 404
        bozza = db.call_pronta(bando_id=333)
        with pytest.raises(NotFoundError):
            await leggi(db, bozza, altro_owner(), USER_ALTRO)

    @pytest.mark.parametrize("identificativo", ["non-un-uuid", "", "urn:uuid:x"])
    async def test_id_malformato_404(self, identificativo):
        with pytest.raises(NotFoundError):
            await leggi(FakeDb(), {"id": identificativo})

    async def test_nessun_campo_interno(self):
        db = FakeDb()
        call = db.call_pronta()
        out = await leggi(db, call)
        dump = out.model_dump(mode="json")
        assert "family_parent_id" not in dump and "creato_da" not in dump
        assert OWNER not in out.model_dump_json()
        # il creatore vede i suoi riservati e il budget esatto
        assert out.budget_progetto_eur == Decimal(str(float(CANARY_BUDGET) / 2))

    async def test_copertura_ricalcolata_con_le_etichette_salvate(self):
        db = FakeDb()
        call = db.con_call()
        db.con_requisito(call["id"], etichetta="C", criterio={"tipo": "ateco", "divisioni": ["62"]},
                         cercato=False, ordine=0)
        db.con_requisito(call["id"], etichetta="A",
                         criterio={"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]},
                         ordine=1)
        out = await leggi(db, call)
        [ateco, ricerca] = out.gap.requisiti
        assert (ateco.etichetta, ateco.copertura_creatore, ateco.copertura_fonte) == (
            "C", "coperto", "registro")
        assert ateco.copertura_nota == "Coperto dal Registro Imprese"
        assert (ricerca.etichetta, ricerca.copertura_creatore, ricerca.cercato) == (
            "A", "non_coperto", True)
        assert out.gap.riepilogo.coperti == 1 and out.gap.riepilogo.non_coperti == 1

    async def test_registro_di_un_altra_piva_non_conta(self):
        db = FakeDb()
        db.tabelle["company_data"][0]["piva_fetched"] = "99999999999"
        call = db.con_call()
        db.con_requisito(call["id"], criterio={"tipo": "ateco", "divisioni": ["62"]})
        out = await leggi(db, call)
        assert out.gap.requisiti[0].copertura_creatore == "dato_mancante"
        assert "identita_non_verificata" in {m.codice for m in out.motivi_blocco}

    async def test_ai_check_disponibile(self):
        db = FakeDb()
        check = db.con_ai_check({"requisiti": [], "criteri": []})
        db.con_ai_check({}, status="error", created_at=_iso(1))
        out = await leggi(db, db.con_call())
        assert out.gap.ai_check.disponibile is True
        assert str(out.gap.ai_check.id) == check["id"]

    async def test_snapshot_non_leggibile_vale_assente(self, caplog):
        db = FakeDb()
        call = db.con_call(regole_partenariato={"versione": 1, "modalita": {"valore": "boh"}},
                           regole_confermate_at=_iso())
        out = await leggi(db, call)
        assert out.regole_partenariato is None

    async def test_lettura_senza_scritture(self):
        db = FakeDb()
        call = db.call_pronta()
        await leggi(db, call)
        assert [op for op in db.ops if op[1] != "select"] == []


class TestChiusuraInLettura:
    async def test_scadenza_passata_chiude_e_notifica(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() - timedelta(days=1)).isoformat())
        out = await leggi(db, call)
        assert (out.stato, out.motivo_chiusura) == ("scaduta", "scadenza_call")
        [notifica] = db.tabelle["notifications"]
        assert notifica["tipo"] == "partenariato.call_chiusa"
        assert notifica["dedup_key"] == f"call-chiusa:{call['id']}"
        assert notifica["url"] == f"/app/partenariati/call/{call['id']}?azienda={COMPANY}"
        assert notifica["company_profile_id"] == COMPANY and notifica["user_id"] == OWNER
        # una seconda lettura non notifica di nuovo
        await leggi(db, call)
        assert len(db.tabelle["notifications"]) == 1

    @pytest.mark.parametrize(
        ("stato_bando", "stato", "motivo"),
        [("chiuso", "scaduta", "bando_chiuso"), ("sospeso", "scaduta", "bando_sospeso"),
         ("revocato", "chiusa_annullata", "bando_revocato")],
    )
    async def test_stato_del_bando(self, stato_bando, stato, motivo):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=10)).isoformat())
        secondary = FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo=stato_bando)])
        out = await leggi(db, call, secondary=secondary)
        assert (out.stato, out.motivo_chiusura) == (stato, motivo)

    async def test_bozza_con_bando_chiuso_annullata(self):
        db = FakeDb()
        call = db.con_call()
        secondary = FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo="chiuso")])
        out = await leggi(db, call, secondary=secondary)
        assert (out.stato, out.motivo_chiusura) == ("chiusa_annullata", "bando_chiuso")

    async def test_errore_del_catalogo_non_chiude(self):
        db = FakeDb()
        call = db.call_pronta()
        secondary = FakeSecondary()
        secondary.guasto_pubblico = RuntimeError("rete")
        out = await leggi(db, call, secondary=secondary)
        assert out.stato == "bozza" and db.chiamate("fn_partner_call_chiudi_auto") == []
        # senza lettura il motivo «bando» non si dichiara
        assert "bando_non_disponibile" not in {m.codice for m in out.motivi_blocco}

    async def test_bando_assente_da_poco_resta_aperta(self):
        db = FakeDb()
        call = db.call_pronta(bando_mancante_dal=(oggi() - timedelta(days=3)).isoformat())
        out = await leggi(db, call, secondary=FakeSecondary(pubblici=[]))
        assert out.stato == "bozza"
        assert "bando_non_disponibile" in {m.codice for m in out.motivi_blocco}

    async def test_bando_assente_da_sette_giorni(self):
        db = FakeDb()
        call = db.call_pronta(bando_mancante_dal=(oggi() - timedelta(days=7)).isoformat())
        out = await leggi(db, call, secondary=FakeSecondary(pubblici=[]))
        assert (out.stato, out.motivo_chiusura) == ("chiusa_annullata", "bando_non_disponibile")

    def test_motivo_chiusura_puro(self):
        stato = StatoBando(id=1, slug="x", stato_effettivo="aperto", data_scadenza=None,
                           ultimo_cambiamento_at=None)
        base = riga_call(stato="pubblicata", scadenza_call=oggi().isoformat())
        assert pcs.motivo_chiusura_auto(base, stato_bando=stato, bando_letto=True,
                                        azienda_viva=True, oggi=oggi()) is None
        assert pcs.motivo_chiusura_auto(base, stato_bando=stato, bando_letto=True,
                                        azienda_viva=False, oggi=oggi()) == (
            "chiusa_annullata", "azienda_non_disponibile")
        bozza = riga_call(scadenza_call=(oggi() - timedelta(days=5)).isoformat())
        assert pcs.motivo_chiusura_auto(bozza, stato_bando=stato, bando_letto=True,
                                        azienda_viva=True, oggi=oggi()) is None  # non scade
        # un errore di lettura non è un'assenza, nemmeno dopo 7 giorni
        mancante = riga_call(bando_mancante_dal=(oggi() - timedelta(days=10)).isoformat())
        assert pcs.motivo_chiusura_auto(mancante, stato_bando=None, bando_letto=False,
                                        azienda_viva=True, oggi=oggi()) is None
        assert pcs.motivo_chiusura_auto(mancante, stato_bando=None, bando_letto=True,
                                        azienda_viva=True, oggi=oggi()) == (
            "chiusa_annullata", "bando_non_disponibile")
        for chiusa in ("scaduta", "chiusa_completata", "chiusa_annullata", "sospesa_moderazione"):
            assert pcs.motivo_chiusura_auto(riga_call(stato=chiusa), stato_bando=None,
                                            bando_letto=True, azienda_viva=False,
                                            oggi=oggi()) is None


# ------------------------------------------------------------ aggiornamento


class TestAggiorna:
    async def test_parziale_solo_i_campi_inviati(self):
        db = FakeDb()
        call = db.con_call(visibilita="solo_invitati", ruolo_creatore="cerco_capofila")
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(titolo="Un titolo abbastanza lungo", wizard_passo=3))
        [p] = db.chiamate("fn_partner_call_aggiorna")
        assert p["p_campi"] == {"titolo": "Un titolo abbastanza lungo", "wizard_passo": 3}
        assert db.call(call["id"])["visibilita"] == "solo_invitati"
        assert db.call(call["id"])["ruolo_creatore"] == "cerco_capofila"

    async def test_anonima_false_409(self):
        db = FakeDb()
        call = db.con_call()
        await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         CallAggiornaIn(anonima=False)),
            "nominativo_non_disponibile", 409,
        )

    @pytest.mark.parametrize(
        ("campi", "tipo"),
        [
            ({"titolo": "Scrivete a info@example.com per info"}, "un indirizzo email"),
            ({"descrizione_pubblica": "Chiamate il 347 123 4567"}, "un numero di telefono"),
            ({"profilo_partner_ideale": "Vedi www.altrosito.it"}, "un indirizzo web"),
            ({"profilo_partner_ideale": "Vedi www.rossimeccanica.it"}, "il sito dell'azienda"),
            ({"descrizione_pubblica": "Siamo la Rossi Meccanica di Brescia"},
             "il nome dell'azienda"),
            ({"titolo": "Partner per ROSSI MECCANICA nel bando"}, "il nome dell'azienda"),
            # forme che il controllo vede solo sul testo canonico: caratteri
            # invisibili, spazi Unicode, cifre e simboli a larghezza piena,
            # nome dell'azienda scritto attaccato
            ({"descrizione_pubblica": "Contattaci: 347\u200b1234567"}, "un numero di telefono"),
            ({"descrizione_pubblica": "Chiamate il 347\u2009123\u20094567"},
             "un numero di telefono"),
            ({"descrizione_pubblica": "Chiamate il \uff13\uff14\uff17\uff11\uff12\uff13\uff14"
                                      "\uff15\uff16\uff17"}, "un numero di telefono"),
            ({"profilo_partner_ideale": "Scrivete a mario\u200b@\u200bgmail\u200b.com"},
             "un indirizzo email"),
            ({"profilo_partner_ideale": "Scrivete a mario\uff20gmail\uff0ecom"},
             "un indirizzo email"),
            ({"descrizione_pubblica": "Siamo la Ros\u200bsi Meccanica di Brescia"},
             "il nome dell'azienda"),
            ({"descrizione_pubblica": "Siamo la RossiMeccanica di Brescia"},
             "il nome dell'azienda"),
            ({"profilo_partner_ideale": "Vedi rossimeccanica\u200b.it"}, "il sito dell'azienda"),
        ],
    )
    async def test_testi_pubblici_non_conformi(self, campi, tipo):
        db = FakeDb()
        call = db.con_call()
        errore = await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         CallAggiornaIn(**campi)),
            "testo_non_conforme", 400,
        )
        assert tipo in errore.message
        # il messaggio non ripete il dato
        assert "info@example.com" not in errore.message and "Rossi" not in errore.message
        assert db.chiamate("fn_partner_call_aggiorna") == []

    async def test_riservati_nome_si_contatti_no(self):
        db = FakeDb()
        call = db.con_call()
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(dettagli_riservati="Rossi Meccanica guida il progetto"))
        errore = await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         CallAggiornaIn(dettagli_riservati="Scrivete a mario@rossi.it")),
            "testo_non_conforme", 400,
        )
        assert "chat" in errore.message
        for nascosto in ("Scrivete a mario\u200b@\u200brossi\u200b.it",
                         "Scrivete a mario\uff20rossi\uff0eit"):
            await attendi_codice(
                pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                             CallAggiornaIn(dettagli_riservati=nascosto)),
                "testo_non_conforme", 400,
            )

    async def test_budget_nella_fascia_salvata(self):
        db = FakeDb()
        call = db.con_call(budget_fascia="50k_150k")
        errore = await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         CallAggiornaIn(budget_progetto_eur=Decimal("900000"))),
            "bad_request", 400,
        )
        assert "fascia" in errore.message
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(budget_progetto_eur=Decimal("120000.50")))
        assert db.chiamate("fn_partner_call_aggiorna")[-1]["p_campi"] == {
            "budget_progetto_eur": "120000.50"}

    async def test_pubblicata_whitelist_e_versione(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(), versione=1,
                              scadenza_call=(oggi() + timedelta(days=20)).isoformat())
        out = await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                 CallAggiornaIn(descrizione_pubblica="Nuova descrizione"))
        assert out.versione == 2
        await attendi_codice(
            pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         CallAggiornaIn(titolo="Un titolo diverso e lungo")),
            "campo_non_modificabile", 400,
        )

    async def test_dopo_la_scrittura_i_motivi_dicono_il_vero(self):
        db = FakeDb()
        call = db.call_pronta()
        chiuso = FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo="chiuso")])
        out = await pcs.aggiorna(db, chiuso, titolare(), USER_OWNER, call["id"],
                                 CallAggiornaIn(wizard_passo=6))
        assert out.puo_pubblicare is False
        assert "bando_non_disponibile" in {m.codice for m in out.motivi_blocco}

    async def test_scadenza_salvata_non_piu_valida(self):
        db = FakeDb()
        call = db.call_pronta(scadenza_call=(oggi() - timedelta(days=2)).isoformat())
        out = await leggi(db, call)
        assert "scadenza_call_non_valida" in {m.codice for m in out.motivi_blocco}
        oltre = db.call_pronta(bando_id=909,
                               scadenza_call=(oggi() + timedelta(days=300)).isoformat())
        assert "scadenza_call_non_valida" in {
            m.codice for m in (await leggi(db, oltre)).motivi_blocco}

    async def test_vuoto_nessuna_rpc(self):
        db = FakeDb()
        call = db.con_call()
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn())
        assert db.chiamate("fn_partner_call_aggiorna") == []


# ------------------------------------------------------------ regole


class TestConfermaRegole:
    async def test_voci_verificate_confermate_e_fonte_dal_servizio(self):
        db = FakeDb().con_estrazione()
        call = db.con_call()
        snap = snapshot_confermato()
        snap["fonte"] = {"estratta_at": "2020-01-01T00:00:00+00:00", "prompt_version": 99,
                         "modalita_effettiva": "ammesso"}  # quella del client si ignora
        out = await pcs.conferma_regole(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        RegoleConfermaIn.model_validate(
                                            {"regole": snap, "esclusivita": True}))
        [p] = db.chiamate("fn_partner_call_conferma_regole")
        assert p["p_regole"]["fonte"] == {"estratta_at": "2026-09-01T10:00:00Z",
                                          "prompt_version": 1,
                                          "modalita_effettiva": "obbligatorio"}
        assert p["p_esclusivita"] is True
        assert out.esclusivita is True and out.regole_partenariato is not None
        assert [r.id for r in out.regole_partenariato.regole_finanziarie] == ["RF1"]

    async def test_confermata_non_verificata_400(self):
        db = FakeDb().con_estrazione()
        call = db.con_call()
        snap = snapshot_confermato()
        snap["partner_min"]["valore"] = 5  # nel bando è 3
        errore = await attendi_codice(
            pcs.conferma_regole(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                RegoleConfermaIn.model_validate(
                                    {"regole": snap, "esclusivita": False})),
            "bad_request", 400,
        )
        assert "numero minimo di partner" in errore.message
        assert db.chiamate("fn_partner_call_conferma_regole") == []

    async def test_senza_estrazione_solo_modificate(self):
        db = FakeDb()
        call = db.con_call()
        await attendi_codice(
            pcs.conferma_regole(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                RegoleConfermaIn.model_validate(
                                    {"regole": snapshot_confermato(), "esclusivita": False})),
            "bad_request", 400,
        )
        await pcs.conferma_regole(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                  RegoleConfermaIn.model_validate(
                                      {"regole": snapshot_minimo(), "esclusivita": False}))
        assert db.chiamate("fn_partner_call_conferma_regole")[0]["p_regole"]["fonte"] is None

    async def test_solo_in_bozza(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=20)).isoformat())
        await attendi_codice(
            pcs.conferma_regole(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                RegoleConfermaIn.model_validate(
                                    {"regole": snapshot_minimo(), "esclusivita": False})),
            "stato_call_non_valido", 409,
        )


# ------------------------------------------------------------ requisiti


def report_ai_check() -> dict:
    return {
        "requisiti": [
            {"id": "R1", "testo": "Impresa con sede operativa in Lombardia",
             "categoria": "territoriale", "verdetto": "non_soddisfatto",
             "motivazione": "CANARYMOTIVO fatturato 7.654.321", "dato_azienda": "CANARYDATO",
             "riferimento_bando": {"sezione": "S2", "testo": "sede in Lombardia",
                                   "verificata": True}},
            {"id": "R2", "testo": "Esperienza in progetti di ricerca industriale",
             "categoria": "altro", "verdetto": "dato_mancante",
             "motivazione": "CANARYMOTIVO", "riferimento_bando": None},
        ],
        "criteri": [],
    }


class TestGeneraRequisiti:
    async def test_fonti_precedenza_e_copertura(self):
        db = FakeDb().con_estrazione()
        db.con_ai_check(report_ai_check())
        call = db.con_call(regole_partenariato=snapshot_confermato(),
                           regole_confermate_at=_iso(), budget_fascia="500k_1m",
                           budget_progetto_eur=800000.0, quota_creatore_pct=50.0)
        gap = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        origini = [(r.origine, id_voce(r.rif_origine)) for r in gap.requisiti]
        # regole → catalogo → AI-check. Il vincolo territoriale confermato (V1,
        # sulle regioni del bando dal catalogo) assorbe il pre-check della
        # regione e la voce territoriale dell'AI-check (R1)
        assert origini == [("bando_partenariato", "K1"), ("bando_partenariato", "V1"),
                           ("regola_finanziaria", "RF1"), ("precheck", "ateco"),
                           ("ai_check", "R2")]
        assert [r.etichetta for r in gap.requisiti] == ["A", "B", "C", "D", "E"]
        regione = next(r for r in gap.requisiti if id_voce(r.rif_origine) == "V1")
        assert regione.criterio.tipo == "regione" and regione.ambito == "ogni_membro"
        # il creatore ha sede in Lombardia (registro), ma l'AI-check dice no:
        # prevale il peggiore
        assert (regione.copertura_creatore, regione.copertura_fonte) == ("non_coperto", "ai_check")
        ricerca = next(r for r in gap.requisiti if id_voce(r.rif_origine) == "K1")
        assert ricerca.copertura_creatore == "non_coperto" and ricerca.cercato is True
        # la regola finanziaria è valutata sul budget ESATTO (vista «proprio»)
        rf1 = next(r for r in gap.requisiti if r.rif_origine == "RF1")
        assert rf1.copertura_fonte == "bilanci" and rf1.copertura_creatore == "coperto"
        # mai i testi del matching dell'AI-check
        testo = gap.model_dump_json()
        assert "CANARYMOTIVO" not in testo and "CANARYDATO" not in testo
        assert gap.ai_check.disponibile is True and gap.partenariato.stato == "pronta"
        # nessun salvataggio; ai_check_id annotato sulla bozza
        assert db.chiamate("fn_partner_call_sostituisci_requisiti") == []
        assert db.chiamate("fn_partner_call_aggiorna")[0]["p_campi"] == {
            "ai_check_id": db.tabelle["ai_checks"][0]["id"]}

    async def test_conserva_id_cercato_ed_etichette_e_i_manuali(self):
        db = FakeDb()
        call = db.con_call()
        salvato = db.con_requisito(call["id"], origine="precheck", rif_origine="ateco",
                                   etichetta="B", cercato=True, ordine=0)
        manuale = db.con_requisito(call["id"], origine="manuale", etichetta="A",
                                   testo="Esperienza nei fondi europei", ordine=1)
        gap = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        per_rif = {r.rif_origine: r for r in gap.requisiti}
        ateco = per_rif["ateco"]
        assert str(ateco.id) == salvato["id"] and ateco.etichetta == "B" and ateco.cercato is True
        regione = per_rif["regione"]
        assert regione.id is None and regione.etichetta == "C"  # A e B già usate
        ultimo = gap.requisiti[-1]
        assert str(ultimo.id) == manuale["id"] and ultimo.etichetta == "A"

    @pytest.mark.parametrize(
        ("testo_salvato", "eredita"),
        [("Esperienza in progetti di ricerca industriale", True),
         ("Certificazione ISO 9001 in corso di validità", False)],
        ids=["stesso_requisito", "rinumerato"],
    )
    async def test_ai_check_rinumerato_non_eredita(self, testo_salvato, eredita):
        """Un nuovo AI-check rinumera i requisiti: il vecchio «R2» salvato con
        un altro testo non passa id, etichetta e «cercato» al nuovo R2; lo
        stesso requisito (stesso id e testo) sì."""
        db = FakeDb()
        db.con_ai_check(report_ai_check())  # R2 = esperienza in ricerca industriale
        call = db.con_call()
        salvato = db.con_requisito(call["id"], origine="ai_check", etichetta="A", cercato=True,
                                   testo=testo_salvato,
                                   rif_origine=rif_con_impronta("R2", testo_salvato))
        gap = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        r2 = next(r for r in gap.requisiti if id_voce(r.rif_origine) == "R2")
        assert r2.testo == "Esperienza in progetti di ricerca industriale"
        if eredita:
            assert str(r2.id) == salvato["id"] and r2.etichetta == "A" and r2.cercato is True
        else:
            assert r2.id is None and r2.cercato is False
            assert salvato["id"] not in {str(r.id) for r in gap.requisiti}

    async def test_senza_ai_check_e_senza_regole(self):
        db = FakeDb()
        call = db.con_call()
        gap = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        assert gap.ai_check.disponibile is False
        assert gap.partenariato.stato == "non_estratta"
        assert {r.origine for r in gap.requisiti} == {"precheck"}

    async def test_bando_sparito_dal_catalogo(self):
        db = FakeDb()
        call = db.con_call()
        gap = await pcs.genera_requisiti(db, FakeSecondary(bandi=[]), titolare(), USER_OWNER,
                                         call["id"])
        assert gap.requisiti == []

    async def test_membro_403(self):
        db = FakeDb()
        call = db.con_call()
        await attendi_codice(
            pcs.genera_requisiti(db, FakeSecondary(), membro(), USER_MEMBRO, call["id"]),
            "forbidden", 403,
        )


def requisito_in(**campi) -> dict:
    return {"testo": "Laboratorio di prove accreditato", "cercato": True, **campi}


class TestSalvaRequisiti:
    async def test_copertura_da_template_ed_etichette_dalla_rpc(self):
        db = FakeDb()
        call = db.con_call()
        dati = RequisitiIn.model_validate({"requisiti": [
            requisito_in(criterio={"tipo": "ateco", "divisioni": ["62"]}, cercato=False,
                         origine="precheck", rif_origine="ateco"),
            requisito_in(criterio={"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]}),
            requisito_in(etichetta="Z"),
        ]})
        gap = await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        dati)
        [p] = db.chiamate("fn_partner_call_sostituisci_requisiti")
        assert [set(e) for e in p["p_requisiti"]][0] <= set(CHIAVI_REQUISITO)
        # l'etichetta di un requisito NUOVO non si manda: la assegna la RPC
        assert all("etichetta" not in e for e in p["p_requisiti"])
        assert p["p_requisiti"][0]["copertura_nota"] == "Coperto dal Registro Imprese"
        assert p["p_requisiti"][2]["copertura_nota"] == (
            "Da valutare a mano: requisito descritto solo a testo")
        assert [r.etichetta for r in gap.requisiti] == ["A", "B", "C"]
        assert [r.copertura_creatore for r in gap.requisiti] == [
            "coperto", "non_coperto", "non_valutabile"]

    async def test_evidenza_dell_ai_check_conservata(self):
        db = FakeDb()
        db.con_ai_check(report_ai_check())
        call = db.con_call()
        testo = "Esperienza in progetti di ricerca industriale"
        dati = RequisitiIn.model_validate({"requisiti": [
            requisito_in(testo=testo, origine="ai_check", rif_origine=rif_con_impronta("R2", testo)),
        ]})
        gap = await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        dati)
        assert (gap.requisiti[0].copertura_creatore, gap.requisiti[0].copertura_fonte) == (
            "dato_mancante", "ai_check")

    async def test_verdetto_di_un_altra_estrazione_non_vale(self):
        """Un requisito salvato da un AI-check precedente («R2» era un altro
        requisito): il verdetto del nuovo R2 non gli fa da evidenza."""
        db = FakeDb()
        db.con_ai_check(report_ai_check())  # R2 = esperienza in ricerca industriale
        call = db.con_call()
        vecchio = "Certificazione ISO 9001 in corso di validità"
        dati = RequisitiIn.model_validate({"requisiti": [
            requisito_in(testo=vecchio, origine="ai_check", rif_origine=rif_con_impronta("R2", vecchio)),
        ]})
        gap = await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        dati)
        assert (gap.requisiti[0].copertura_creatore, gap.requisiti[0].copertura_fonte) == (
            "non_valutabile", "nessuna")

    async def test_verdetto_assorbito_conservato(self):
        """Il pre-check della regione assorbe nella proposta il verdetto
        territoriale dell'AI-check: salvandolo, la copertura resta quella."""
        db = FakeDb()
        db.con_ai_check(report_ai_check())  # R1 territoriale: non soddisfatto
        call = db.con_call()
        proposta = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER,
                                              call["id"])
        regione = next(r for r in proposta.requisiti if r.rif_origine == "regione")
        assert (regione.copertura_creatore, regione.copertura_fonte) == ("non_coperto", "ai_check")
        dati = RequisitiIn.model_validate({"requisiti": [
            {k: v for k, v in r.model_dump(mode="json").items()
             if k in ("id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine",
                      "rif_origine", "citazione")}
            for r in proposta.requisiti
        ]})
        salvato = await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER,
                                            call["id"], dati)
        assert [(r.copertura_creatore, r.copertura_fonte) for r in salvato.requisiti] == [
            (r.copertura_creatore, r.copertura_fonte) for r in proposta.requisiti]

    async def test_q11_regola_finanziaria_solo_dallo_snapshot(self):
        db = FakeDb()
        call = db.con_call(regole_partenariato=snapshot_confermato(),
                           regole_confermate_at=_iso())
        regola = regola_rf1()
        buona = requisito_in(criterio={"tipo": "regola_finanziaria", "regola": regola},
                             origine="regola_finanziaria", rif_origine="RF1", ambito="ogni_membro",
                             cercato=False)
        await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                  RequisitiIn.model_validate({"requisiti": [buona]}))
        ritoccata = copy.deepcopy(buona)
        ritoccata["criterio"]["regola"]["soglia"] = "0.9"
        errore = await attendi_codice(
            pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                RequisitiIn.model_validate({"requisiti": [ritoccata]})),
            "bad_request", 400,
        )
        assert "RF1" in errore.message
        assert len(db.chiamate("fn_partner_call_sostituisci_requisiti")) == 1

    @pytest.mark.parametrize(
        "requisito",
        [
            requisito_in(etichetta="info@x.it"),
            requisito_in(testo="Contattare Rossi Meccanica al 02 1234 5678"),
        ],
        ids=["etichetta", "testo_manuale"],
    )
    async def test_anti_contatti(self, requisito):
        db = FakeDb()
        call = db.con_call()
        await attendi_codice(
            pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                RequisitiIn.model_validate({"requisiti": [requisito]})),
            "testo_non_conforme", 400,
        )

    @pytest.mark.parametrize("origine", ["ai_check", "precheck", "bando_partenariato"])
    @pytest.mark.parametrize("visibile", [{"cercato": True},
                                          {"cercato": False, "ambito": "ogni_membro"}])
    async def test_testo_visibile_controllato_qualunque_origine(self, origine, visibile):
        """L'origine la dichiara il client e il testo si può riscrivere: un
        requisito visibile ai terzi (cercato o per ogni membro) si controlla
        sempre."""
        db = FakeDb()
        call = db.con_call()
        dati = RequisitiIn.model_validate({"requisiti": [requisito_in(
            testo="Scrivi a info@rossicostruzioni.it o chiama 333 1234567", origine=origine,
            rif_origine="R9", **visibile)]})
        await attendi_codice(
            pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"], dati),
            "testo_non_conforme", 400,
        )
        assert db.chiamate("fn_partner_call_sostituisci_requisiti") == []

    async def test_testo_dal_bando_non_visibile_non_bloccato(self):
        """Un requisito dal bando che i terzi non vedono (non cercato, per il
        consorzio) può riportare un recapito del bando."""
        db = FakeDb()
        call = db.con_call()
        dati = RequisitiIn.model_validate({"requisiti": [requisito_in(
            testo="Domande via PEC a bandi@pec.regione.lombardia.it", origine="ai_check",
            rif_origine="R9", cercato=False)]})
        await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"], dati)
        assert len(db.chiamate("fn_partner_call_sostituisci_requisiti")) == 1

    async def test_etichetta_provvisoria_non_sposta_le_posizioni(self):
        """Rigenerando, un requisito nuovo riceve la lettera di uno salvato e
        tolto: al salvataggio la posizione che copriva quello tolto NON passa
        al nuovo (l'etichetta di un requisito nuovo non si manda)."""
        db = FakeDb()
        call = db.con_call()
        tolto = db.con_requisito(call["id"], etichetta="A", origine="bando_partenariato",
                                 rif_origine="K9~0000000000", testo="Organismo di ricerca")
        posizione = db.con_posizione(call["id"], requisiti_ids=[tolto["id"]])
        proposta = await pcs.genera_requisiti(db, FakeSecondary(), titolare(), USER_OWNER,
                                              call["id"])
        nuovo = proposta.requisiti[0]
        assert nuovo.id is None and nuovo.etichetta == "A"  # lettera provvisoria riusata
        dati = RequisitiIn.model_validate({"requisiti": [
            {k: v for k, v in r.model_dump(mode="json").items()
             if k in ("id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine",
                      "rif_origine", "citazione")}
            for r in proposta.requisiti
        ]})
        await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"], dati)
        assert db.righe("partner_call_posizioni", id=posizione["id"])[0]["requisiti_ids"] == []

    async def test_etichetta_dei_conservati_rispettata(self):
        db = FakeDb()
        call = db.con_call()
        salvato = db.con_requisito(call["id"], etichetta="B")
        dati = RequisitiIn.model_validate({"requisiti": [
            requisito_in(id=salvato["id"], etichetta="Z")]})
        gap = await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        dati)
        [p] = db.chiamate("fn_partner_call_sostituisci_requisiti")
        assert p["p_requisiti"][0]["etichetta"] == "Z" and gap.requisiti[0].etichetta == "Z"

    async def test_posizioni_riallineate(self):
        db = FakeDb()
        call = db.con_call()
        a = db.con_requisito(call["id"], etichetta="A")
        b = db.con_requisito(call["id"], etichetta="B")
        posizione = db.con_posizione(call["id"], requisiti_ids=[a["id"], b["id"]])
        dati = RequisitiIn.model_validate({"requisiti": [requisito_in(id=b["id"])]})
        await pcs.salva_requisiti(db, FakeSecondary(), titolare(), USER_OWNER, call["id"], dati)
        assert db.righe("partner_call_posizioni", id=posizione["id"])[0]["requisiti_ids"] == [
            b["id"]]


# ------------------------------------------------------------ posizioni


def posizione_in(**campi) -> dict:
    return {"titolo": "Organismo di ricerca", "tipi_soggetto": ["organismo_ricerca"], **campi}


class TestSalvaPosizioni:
    async def test_replace_all(self):
        db = FakeDb()
        call = db.con_call()
        requisito = db.con_requisito(call["id"])
        dati = PosizioniIn.model_validate({"posizioni": [posizione_in(
            regioni=[3], territorio_modalita="sede_attuale", requisiti_ids=[requisito["id"]],
            quota_ipotizzata_pct="25.5", paesi=["it"])]})
        out = await pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                        dati)
        [p] = db.chiamate("fn_partner_call_sostituisci_posizioni")
        [elemento] = p["p_posizioni"]
        assert "id" not in elemento and elemento["paesi"] == ["IT"]
        assert elemento["quota_ipotizzata_pct"] == "25.5"
        assert elemento["requisiti_ids"] == [requisito["id"]]
        assert out.posizioni[0].titolo == "Organismo di ricerca"

    async def test_regione_sconosciuta_e_catalogo_giu(self, catalogo):
        db = FakeDb()
        call = db.con_call()
        dati = PosizioniIn.model_validate({"posizioni": [posizione_in(
            regioni=[99], territorio_modalita="sede_attuale")]})
        errore = await attendi_codice(
            pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call["id"], dati),
            "bad_request", 400,
        )
        assert "Regione" in errore.message
        catalogo.lookups_errore = RuntimeError("giù")
        with pytest.raises(RuntimeError):
            await pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                      PosizioniIn.model_validate({"posizioni": [posizione_in(
                                          regioni=[3], territorio_modalita="sede_attuale")]}))
        assert db.chiamate("fn_partner_call_sostituisci_posizioni") == []

    async def test_note_con_contatti(self):
        db = FakeDb()
        call = db.con_call()
        await attendi_codice(
            pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                PosizioniIn.model_validate({"posizioni": [posizione_in(
                                    note="Scrivete su t.me/rossimeccanica")]})),
            "testo_non_conforme", 400,
        )

    async def test_requisito_di_un_altra_call(self):
        db = FakeDb()
        call = db.con_call()
        altra = db.con_call(bando_id=555)
        estraneo = db.con_requisito(altra["id"])
        await attendi_codice(
            pcs.salva_posizioni(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                                PosizioniIn.model_validate({"posizioni": [posizione_in(
                                    requisiti_ids=[estraneo["id"]])]})),
            "bad_request", 400,
        )


# ------------------------------------------------------------ job AI


async def avvia_posizioni(db, ai, spawned, call):
    out = await pcs.avvia_proposta_posizioni(db, FakeSecondary(), ai, titolare(), USER_OWNER,
                                             call["id"])
    return out, spawned.pop()


async def avvia_testi(db, ai, spawned, call):
    out = await pcs.avvia_proposta_testi(db, FakeSecondary(), ai, titolare(), USER_OWNER,
                                         call["id"])
    return out, spawned.pop()


def call_per_ai(db, **modifiche) -> dict:
    call = db.call_pronta(ruolo_creatore="capofila", **modifiche)
    return call


class TestAvvioJob:
    async def test_prenotazione_e_202(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        out, job = await avvia_posizioni(db, FakeAi(), spawned, call)
        job.close()
        assert out.stato == "in_corso" and out.avviata_at is not None
        [p] = db.chiamate("fn_partner_call_ai_prenota")
        assert p == {
            "p_owner": OWNER, "p_company": COMPANY, "p_call": call["id"],
            "p_richiedente": OWNER, "p_servizio": "partner_call_posizioni",
            "p_budget_cents": 200, "p_costo_riservato_cents": p["p_costo_riservato_cents"],
            "p_limite_call": 10, "p_limite_owner": 30,
        }
        assert p["p_costo_riservato_cents"] > 0
        assert db.call(call["id"])["ai_posizioni_stato"] == "in_corso"

    async def test_input_minimizzato(self, spawned):
        db = FakeDb()
        call = call_per_ai(db, titolo="Partner per il progetto di Rossi Meccanica srl",
                           descrizione_pubblica="Scrivete a mario.rossi@rossimeccanica.it, "
                                                "Giulia Bianchini vi risponde")
        # un testo corretto a mano anche se viene dal catalogo
        db.con_requisito(call["id"], origine="precheck", rif_origine="ateco",
                         testo="Fornitore abituale di Rossi Meccanica")
        ai = FakeAi()
        _, job = await avvia_testi(db, ai, spawned, call)
        await job
        _, job = await avvia_posizioni(db, ai, spawned, call)
        await job
        for chiamata in ai.chiamate:
            testo = chiamata["testo"]
            for vietato in (CANARY_RISERVATI, "CANARYRISERVATO", "617283", "1234567",
                            PIVA, "Rossi Meccanica", "ROSSI MECCANICA", "Bianchini",
                            "mario.rossi@", "rossimeccanica.it", OWNER, COMPANY):
                assert vietato not in testo, vietato
            assert "500.000 € - 1 milione" in testo  # solo la fascia pubblica
        assert ai.chiamate[0]["schema"] is BozzaTestiCall
        assert ai.chiamate[1]["schema"] is PropostaPosizioni
        assert "[REQUISITI]" in ai.chiamate[1]["testo"] and "A)" in ai.chiamate[1]["testo"]
        assert {c["model"] for c in ai.chiamate} == {MODELLO}
        assert {c["max_tokens"] for c in ai.chiamate} == {6000}
        assert {c["timeout"] for c in ai.chiamate} == {90.0}

    async def test_solo_bozza_titolare_e_ai_configurata(self, spawned):
        db = FakeDb()
        pubblicata = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                                    scadenza_call=(oggi() + timedelta(days=9)).isoformat(),
                                    bando_id=202)
        await attendi_codice(avvia_posizioni(db, FakeAi(), spawned, pubblicata),
                             "stato_call_non_valido", 409)
        bozza = call_per_ai(db)
        await attendi_codice(
            pcs.avvia_proposta_testi(db, FakeSecondary(), FakeAi(), membro(), USER_MEMBRO,
                                     bozza["id"]), "forbidden", 403)
        with pytest.raises(AiNotConfiguredError):
            await pcs.avvia_proposta_testi(db, FakeSecondary(), FakeAi(enabled=False),
                                           titolare(), USER_OWNER, bozza["id"])
        assert db.chiamate("fn_partner_call_ai_prenota") == [] and spawned == []

    async def test_in_corso_409(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_posizioni(db, FakeAi(), spawned, call)
        job.close()
        await attendi_codice(avvia_posizioni(db, FakeAi(), spawned, call), "ai_in_corso", 409)
        # l'altro servizio è indipendente
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        job.close()

    async def test_limite_per_call(self, spawned, monkeypatch):
        monkeypatch.setenv("PARTNER_CALL_AI_LIMITE_GIORNO", "1")
        from app.core.config import get_settings

        get_settings.cache_clear()
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_posizioni(db, FakeAi(), spawned, call)
        await job
        errore = await attendi_codice(avvia_posizioni(db, FakeAi(), spawned, call),
                                      "ai_limite_giornaliero", 429)
        assert "per questa call" in errore.message

    async def test_limite_per_titolare_e_budget(self, spawned, monkeypatch):
        db = FakeDb()
        call = call_per_ai(db)
        db.rpc_errori["fn_partner_call_ai_prenota"] = "ai_limite_owner"
        errore = await attendi_codice(avvia_testi(db, FakeAi(), spawned, call),
                                      "ai_limite_giornaliero", 429)
        assert "proposte automatiche" in errore.message
        db.rpc_errori["fn_partner_call_ai_prenota"] = "ai_budget_esaurito"
        await attendi_codice(avvia_testi(db, FakeAi(), spawned, call), "ai_sospesa_oggi", 429)
        assert spawned == []

    async def test_prenotazione_senza_id_fail_closed(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        db._fn_partner_call_ai_prenota = lambda p: None
        with pytest.raises(UpstreamError):
            await avvia_testi(db, FakeAi(), spawned, call)
        assert spawned == []


class TestEsecuzioneJob:
    async def test_posizioni_pronte_post_validate(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        [requisito] = db.righe("partner_call_requisiti", call_id=call["id"])
        ai = FakeAi()
        _, job = await avvia_posizioni(db, ai, spawned, call)
        assert await job == "pronta"
        out = await leggi(db, call)
        assert out.ai_posizioni.stato == "pronta"
        [proposta] = out.ai_posizioni.proposta.posizioni
        assert proposta.regioni == [3] and proposta.territorio_modalita == "sede_attuale"
        assert proposta.paesi == ["IT"]
        assert [str(i) for i in proposta.requisiti_ids] == [requisito["id"]]
        avvisi = " ".join(out.ai_posizioni.proposta.avvisi)
        assert "1 riferimenti a requisiti che non esistono" in avvisi
        assert "1 regioni non riconosciute" in avvisi
        # nulla è salvato: le posizioni della call sono quelle di prima
        assert len(out.posizioni) == 1 and out.posizioni[0].titolo == "Organismo di ricerca"
        [esecuzione] = db.esecuzioni.values()
        costo = costo_cents(MODELLO, 3_000, 1_000)
        assert (esecuzione["stato"], esecuzione["cost_cents"]) == ("conclusa", costo)
        [uso] = db.usage
        assert (uso["service"], uso["outcome"], uso["cost_cents"], uso["provider"]) == (
            "partner_call_posizioni", "success", costo, "anthropic")
        assert uso["request_meta"]["call_id"] == call["id"]

    async def test_testi_anonimizzati_con_rilievi(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        assert await job == "pronta"
        out = await leggi(db, call)
        proposta = out.ai_testi.proposta
        assert "info@rossi.it" not in proposta.descrizione_pubblica
        assert "Rossi Meccanica" not in proposta.profilo_partner_ideale
        assert "[rimosso]" in proposta.descrizione_pubblica
        assert any("riferimenti non ammessi" in a for a in proposta.avvisi)
        assert all(not r.bloccante for r in proposta.rilievi)

    async def test_update_condizionato_proposta_superata(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        attesa = asyncio.Event()
        _, job = await avvia_testi(db, FakeAi(attesa=attesa), spawned, call)
        task = asyncio.create_task(job)
        await asyncio.sleep(0)
        # un nuovo job (dopo il failsafe) ha preso il posto di questo
        db.call(call["id"])["ai_testi_esecuzione_id"] = str(uuid.uuid4())
        attesa.set()
        assert await task == "superata"
        assert db.call(call["id"])["ai_testi_stato"] == "in_corso"
        [uso] = db.usage
        assert uso["outcome"] == "error" and uso["request_meta"]["esito"] == "superata"
        assert uso["cost_cents"] == costo_cents(MODELLO, 3_000, 1_000)

    @pytest.mark.parametrize(
        ("errore", "outcome", "costo_atteso", "codice"),
        [
            (AiTimeoutError(), "timeout_unknown", "riserva", "timeout"),
            ("usage", "error", "max", "ai_risposta_non_valida"),
            (AiUpstreamError(), "error", None, "ai_non_disponibile"),
            (AiNotConfiguredError(), "error", 0, "errore_interno"),
        ],
        ids=["timeout", "risposta_non_valida", "rete", "non_chiamato"],
    )
    async def test_costi_su_ogni_ramo(self, spawned, errore, outcome, costo_atteso, codice):
        db = FakeDb()
        call = call_per_ai(db)
        if errore == "usage":
            errore = AiUpstreamError()
            errore.usage = AiUsage(input_tokens=10, output_tokens=5)
        _, job = await avvia_posizioni(db, FakeAi(errore=errore), spawned, call)
        await job
        [p] = db.chiamate("fn_partner_call_ai_prenota")
        riserva = p["p_costo_riservato_cents"]
        [esecuzione] = db.esecuzioni.values()
        attesi = {"riserva": riserva, "max": max(costo_cents(MODELLO, 10, 5), riserva),
                  None: None, 0: 0}
        assert esecuzione["cost_cents"] == attesi[costo_atteso]
        [uso] = db.usage
        assert uso["outcome"] == outcome
        assert uso["cost_cents"] == (riserva if costo_atteso is None else attesi[costo_atteso])
        out = await leggi(db, call)
        assert out.ai_posizioni.stato == "errore"
        assert db.call(call["id"])["ai_posizioni_errore"] == codice
        assert out.ai_posizioni.errore == pca.messaggio_errore(codice)

    async def test_guasto_dopo_il_modello(self, spawned, monkeypatch):
        db = FakeDb()
        call = call_per_ai(db)

        def esplode(*a, **k):
            raise RuntimeError("bug")

        monkeypatch.setattr(pca, "post_testi", esplode)
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        assert await job == "errore"
        [p] = db.chiamate("fn_partner_call_ai_prenota")
        [uso] = db.usage
        assert uso["cost_cents"] == max(costo_cents(MODELLO, 3_000, 1_000),
                                        p["p_costo_riservato_cents"])

    async def test_chiusura_non_riuscita_resta_al_failsafe(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        db.rpc_errori["fn_partner_call_ai_concludi"] = "guasto"
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        assert await job == "errore"
        assert db.usage == [] and db.call(call["id"])["ai_testi_stato"] == "in_corso"

    async def test_cancellazione_durante_la_chiamata(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_testi(db, FakeAi(attesa=asyncio.Event()), spawned, call)
        task = asyncio.create_task(job)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        [p] = db.chiamate("fn_partner_call_ai_prenota")
        [uso] = db.usage
        assert (uso["outcome"], uso["cost_cents"]) == (
            "timeout_unknown", p["p_costo_riservato_cents"])
        assert db.call(call["id"])["ai_testi_errore"] == "interrotta"
        # un secondo passaggio del failsafe non registra di nuovo
        assert db._fn_partner_call_ai_chiudi_stale({"p_minuti": 0}) == 0
        assert len(db.usage) == 1

    async def test_cancellazione_durante_il_registro(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        attesa = asyncio.Event()

        async def insert_lento():
            await attesa.wait()

        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        originale = FakeQuery.execute

        async def execute(self):
            if self.tabella == "api_usage_events" and self.op == "insert":
                risposta = await originale(self)
                await insert_lento()
                return risposta
            return await originale(self)

        FakeQuery.execute = execute
        try:
            task = asyncio.create_task(job)
            for _ in range(20):
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            FakeQuery.execute = originale
        assert len(db.usage) == 1 and db.usage[0]["outcome"] == "success"


class TestFailsafeInLettura:
    async def test_job_orfano_chiuso_in_lettura(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        job.close()
        db.call(call["id"])["ai_testi_avviata_at"] = _iso(30)
        next(iter(db.esecuzioni.values()))["avviata_at"] = _iso(30)
        out = await leggi(db, call)
        assert out.ai_testi.stato == "errore"
        assert out.ai_testi.errore == pca.messaggio_errore("interrotta")
        [uso] = db.usage
        assert uso["outcome"] == "timeout_unknown" and uso["request_meta"]["failsafe"] is True
        assert db.chiamate("fn_partner_call_ai_chiudi_stale") == [{"p_minuti": 10}]

    async def test_job_recente_nessuna_rpc(self, spawned):
        db = FakeDb()
        call = call_per_ai(db)
        _, job = await avvia_testi(db, FakeAi(), spawned, call)
        job.close()
        out = await leggi(db, call)
        assert out.ai_testi.stato == "in_corso"
        assert db.chiamate("fn_partner_call_ai_chiudi_stale") == []

    async def test_proposta_con_requisiti_non_piu_validi(self):
        db = FakeDb()
        call = call_per_ai(db)
        [requisito] = db.righe("partner_call_requisiti", call_id=call["id"])
        sparito = str(uuid.uuid4())
        db.call(call["id"]).update(ai_posizioni_stato="pronta", ai_posizioni_proposta={
            "posizioni": [{"titolo": "Partner", "requisiti_ids": [requisito["id"], sparito]}],
            "avvisi": []})
        out = await leggi(db, call)
        assert [str(i) for i in out.ai_posizioni.proposta.posizioni[0].requisiti_ids] == [
            requisito["id"]]


REQ_A = "e0000000-0000-0000-0000-00000000000a"


class TestPostElaborazione:
    def test_posizioni_codici_quote_e_avvisi(self):
        regole = RegoleConfermaIn.model_validate(
            {"regole": snapshot_minimo(partner_min={"valore": 4, "origine_voce": "modificata"},
                                       quote=[{"id": "Q1", "ambito": "per_partner",
                                               "max_percentuale": 50,
                                               "base_calcolo": "costo_totale_progetto",
                                               "effetto_violazione": "inammissibilita_progetto",
                                               "origine_voce": "modificata"}]),
             "esclusivita": False}).regole
        proposta = PropostaPosizioni(posizioni=[
            PosizioneAi(titolo="  Capofila   cercato ", ruolo="capofila", tipi_soggetto=[],
                        competenze=["sviluppo_software", "sviluppo_software"],
                        ateco_divisioni=["62", "6", "abc"], regioni=["Veneto"],
                        territorio_modalita="qualsiasi", paesi=["UK"], dimensioni=["micro"],
                        quota_ipotizzata_pct=120.0, numero=40, requisiti=[" A "],
                        motivazione="Scrivere a x@y.it"),
            PosizioneAi(titolo="ok", ruolo="partner", tipi_soggetto=[], competenze=[],
                        ateco_divisioni=[], regioni=[], territorio_modalita="sede_attuale",
                        paesi=[], dimensioni=[], quota_ipotizzata_pct=None, numero=1,
                        requisiti=[], motivazione=""),
        ])
        out = pca.post_posizioni(
            proposta, etichette={"A": REQ_A}, regioni={5: "Veneto"}, ident=None,
            ruolo_creatore="capofila", quota_creatore=Decimal("60"), regole=regole,
        )
        [posizione] = out["posizioni"]
        assert posizione["titolo"] == "Capofila cercato" and posizione["ruolo"] == "partner"
        assert posizione["competenze"] == ["sviluppo_software"]
        assert posizione["ateco_divisioni"] == ["62"]
        assert posizione["regioni"] == [5]
        assert posizione["territorio_modalita"] == "sede_entro_erogazione"
        assert posizione["paesi"] == ["GB"] and posizione["numero"] == 10
        assert posizione["quota_ipotizzata_pct"] is None
        assert posizione["requisiti_ids"] == [REQ_A]
        assert "x@y.it" not in posizione["motivazione"]
        avvisi = " ".join(out["avvisi"])
        assert "il capofila sei tu" in avvisi
        assert "1 posizioni senza un titolo valido" in avvisi
        assert "quote fuori dall'intervallo" in avvisi
        assert "supera il massimo per partner del bando (50%)" in avvisi  # la tua quota
        assert "al massimo" not in avvisi or "partner_max" not in avvisi

    def test_quote_oltre_il_cento_e_minimo_partner(self):
        regole = RegoleConfermaIn.model_validate(
            {"regole": snapshot_minimo(partner_min={"valore": 4, "origine_voce": "modificata"}),
             "esclusivita": False}).regole
        proposta = PropostaPosizioni(posizioni=[PosizioneAi(
            titolo="Partner tecnologico", ruolo="partner", tipi_soggetto=[], competenze=[],
            ateco_divisioni=[], regioni=[], territorio_modalita="qualsiasi", paesi=[],
            dimensioni=[], quota_ipotizzata_pct=30.0, numero=2, requisiti=[], motivazione="m")])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="cerco_capofila", quota_creatore=50,
                                 regole=regole)
        avvisi = " ".join(out["avvisi"])
        assert "superano il 100% (110%)" in avvisi
        assert "almeno 4 partner" in avvisi and "sareste in 3" in avvisi
        assert "aggiungi una posizione con ruolo capofila" in avvisi

    def test_quota_per_partner_con_categoria_solo_per_quel_tipo(self):
        """«Ciascuna grande impresa tra il 20% e il 50%»: nessun avviso a chi
        di sicuro è di un altro tipo, avviso condizionale a chi forse lo è."""
        regole = RegoleConfermaIn.model_validate(
            {"regole": snapshot_minimo(quote=[{
                "id": "Q1", "ambito": "per_partner", "categoria": "grande_impresa",
                "min_percentuale": 20, "max_percentuale": 50,
                "base_calcolo": "costo_totale_progetto",
                "effetto_violazione": "inammissibilita_progetto",
                "origine_voce": "modificata"}]),
             "esclusivita": False}).regole

        def posizione(titolo, tipi):
            return PosizioneAi(
                titolo=titolo, ruolo="partner", tipi_soggetto=tipi, competenze=[],
                ateco_divisioni=[], regioni=[], territorio_modalita="qualsiasi", paesi=[],
                dimensioni=[], quota_ipotizzata_pct=10.0, numero=1, requisiti=[],
                motivazione="m")

        proposta = PropostaPosizioni(posizioni=[
            posizione("Grande partner", ["grande_impresa"]),
            posizione("Centro di ricerca", ["organismo_ricerca"]),
            posizione("Partner qualsiasi", []),
            posizione("Partner misto", ["grande_impresa", "organismo_ricerca"]),
        ])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="capofila", quota_creatore=55, regole=regole)
        assert set(out["avvisi"]) == {
            "«Grande partner»: la quota è sotto il minimo del bando per ogni partner di "
            "tipo «Grande impresa» (20%)",
            "«Partner qualsiasi»: se il partner sarà di tipo «Grande impresa», la quota è "
            "sotto il minimo del bando per quel tipo di partner (20%)",
            "«Partner misto»: se il partner sarà di tipo «Grande impresa», la quota è "
            "sotto il minimo del bando per quel tipo di partner (20%)",
            "La tua quota: se la tua azienda è di tipo «Grande impresa», la quota supera "
            "il massimo del bando per quel tipo di partner (50%)",
        }

    @pytest.mark.parametrize(("categoria", "tipi", "avviso"), [
        ("pmi", ["micro_impresa"], True),  # una micro impresa è una PMI
        ("grande_impresa", ["impresa"], True),  # un'impresa può essere grande
        ("pmi", ["startup_innovativa", "cooperativa"], True),
        ("pmi", ["libero_professionista"], True),  # spesso equiparato alle PMI
        ("micro_impresa", ["libero_professionista"], True),
        ("impresa_sociale", ["ente_terzo_settore"], True),
        ("cooperativa", ["ente_terzo_settore"], True),
        ("altro", ["pmi"], True),  # «altro» è sempre incerto, come nel validatore
        ("altro", ["altro"], True),
        ("pmi", ["altro"], True),
        # sovrapposizioni fuori dalle imprese, in entrambi i versi
        ("organismo_ricerca", ["universita"], True),
        ("universita", ["organismo_ricerca"], True),
        ("ente_pubblico", ["universita"], True),
        ("ente_pubblico", ["ente_locale"], True),
        ("istituto_scolastico", ["ente_pubblico"], True),
        ("ente_terzo_settore", ["fondazione"], True),
        ("ente_terzo_settore", ["associazione_categoria"], True),
        ("ente_sportivo", ["ente_terzo_settore"], True),
        ("pmi", ["organismo_ricerca"], False),  # tipo estraneo: nessun avviso
        ("pmi", ["universita"], False),
        ("ente_locale", ["universita"], False),  # entrambi enti pubblici, ma diversi
        ("fondazione", ["pmi"], False),
    ])
    def test_quota_con_categoria_tipi_imparentati_o_altro(self, categoria, tipi, avviso):
        """Tra tipi che si sovrappongono, o con «altro», il tipo non si esclude:
        nel dubbio l'avviso è condizionale, mai assente."""
        regole = RegoleConfermaIn.model_validate(
            {"regole": snapshot_minimo(quote=[{
                "id": "Q1", "ambito": "per_partner", "categoria": categoria,
                "min_percentuale": 20, "base_calcolo": "costo_totale_progetto",
                "effetto_violazione": "inammissibilita_progetto",
                "origine_voce": "modificata"}]),
             "esclusivita": False}).regole
        proposta = PropostaPosizioni(posizioni=[PosizioneAi(
            titolo="Partner cercato", ruolo="partner", tipi_soggetto=tipi, competenze=[],
            ateco_divisioni=[], regioni=[], territorio_modalita="qualsiasi", paesi=[],
            dimensioni=[], quota_ipotizzata_pct=10.0, numero=1, requisiti=[],
            motivazione="m")])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="capofila", quota_creatore=None,
                                 regole=regole)
        etichetta = pca.voc.TIPI_SOGGETTO[categoria].etichetta
        atteso = (f"«Partner cercato»: se il partner sarà di tipo «{etichetta}», la quota è "
                  "sotto il minimo del bando per quel tipo di partner (20%)")
        assert out["avvisi"] == ([atteso] if avviso else [])


# ------------------------------------------------------------ anteprima


class TestAnteprima:
    async def test_proiezione_e_rilievi(self):
        db = FakeDb()
        call = db.call_pronta(titolo="Cerchiamo partner per Rossi Meccanica",
                              descrizione_pubblica="Contatti: 347 123 4567")
        out = await pcs.anteprima(db, FakeSecondary(), membro(), USER_MEMBRO, call["id"])
        assert out.call.stato == "bozza"
        assert out.call.creatore.denominazione == "Azienda anonima"
        assert out.call.creatore.regione == "Lombardia"
        assert "Rossi" not in out.call.titolo and "347" not in out.call.descrizione_pubblica
        campi = {(r.campo, r.tipo) for r in out.rilievi}
        assert ("titolo", "ragione_sociale") in campi
        assert ("descrizione_pubblica", "telefono") in campi
        testo = out.model_dump_json()
        for vietato in (CANARY_RISERVATI, COMPANY, OWNER, PIVA, "fascia_fatturato", "500k_2m"):
            assert vietato not in testo

    async def test_solo_l_azienda_creatrice(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=9)).isoformat())
        with pytest.raises(NotFoundError):
            await pcs.anteprima(db, FakeSecondary(), altro_owner(), USER_ALTRO, call["id"])


# ------------------------------------------------------------ pubblicazione


class TestPubblica:
    async def test_pubblica_con_scadenza_di_default(self):
        db = FakeDb()
        call = db.call_pronta()
        out = await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        [p] = db.chiamate("fn_partner_call_pubblica")
        assert p["p_scadenza_call"] == (oggi() + timedelta(days=60)).isoformat()
        assert p["p_bando_stato"] == "aperto"
        assert p["p_bando_scadenza"] == (oggi() + timedelta(days=120)).isoformat()
        assert p["p_richiedi_non_sandbox"] is True
        assert out.stato == "pubblicata" and out.versione == 1 and out.puo_pubblicare is False
        assert out.limiti.call_attive.usate == 1

    async def test_scadenza_del_bando_piu_vicina(self):
        db = FakeDb()
        call = db.call_pronta()
        vicina = (oggi() + timedelta(days=20)).isoformat()
        await pcs.pubblica(db, FakeSecondary(pubblici=[riga_bando_pubblico(data_scadenza=vicina)]),
                           titolare(), USER_OWNER, call["id"])
        assert db.chiamate("fn_partner_call_pubblica")[0]["p_scadenza_call"] == vicina

    async def test_scadenza_esplicita_oltre_il_bando(self):
        db = FakeDb()
        call = db.call_pronta()
        await attendi_codice(
            pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         oggi() + timedelta(days=200)),
            "scadenza_call_non_valida", 400,
        )

    @pytest.mark.parametrize("stato", ["chiuso", "sospeso", "revocato"])
    async def test_stato_live_del_bando(self, stato):
        db = FakeDb()
        call = db.call_pronta()
        await attendi_codice(
            pcs.pubblica(db, FakeSecondary(pubblici=[riga_bando_pubblico(stato_effettivo=stato)]),
                         titolare(), USER_OWNER, call["id"]),
            "bando_non_disponibile", 409,
        )
        assert db.chiamate("fn_partner_call_pubblica") == []

    async def test_catalogo_non_leggibile_fail_closed(self):
        db = FakeDb()
        call = db.call_pronta()
        secondary = FakeSecondary()
        secondary.guasto_pubblico = RuntimeError("rete")
        with pytest.raises(UpstreamError):
            await pcs.pubblica(db, secondary, titolare(), USER_OWNER, call["id"])

    async def test_non_ammesso_senza_motivo(self):
        db = FakeDb().con_estrazione(modalita="non_ammesso")
        call = db.call_pronta()
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "partenariato_non_ammesso", 409)

    @pytest.mark.parametrize(
        "modifiche",
        [
            {"titolo": "Partner per Rossi Meccanica srl"},
            {"profilo_partner_ideale": "Scrivici: bandi@rossimeccanica.it"},
        ],
    )
    async def test_rilievi_bloccanti(self, modifiche):
        db = FakeDb()
        call = db.call_pronta(**modifiche)
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "testo_non_conforme", 400)
        assert db.chiamate("fn_partner_call_pubblica") == []

    async def test_rilievo_nella_posizione(self):
        db = FakeDb()
        call = db.call_pronta()
        db.righe("partner_call_posizioni", call_id=call["id"])[0]["note"] = "Tel. 02 1234 5678"
        errore = await attendi_codice(
            pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
            "testo_non_conforme", 400)
        assert "La nota della posizione 1" in errore.message

    async def test_q11_regola_non_piu_nello_snapshot(self):
        db = FakeDb()
        call = db.call_pronta()
        regola = regola_rf1()
        db.con_requisito(call["id"], origine="regola_finanziaria", rif_origine="RF1",
                         criterio={"tipo": "regola_finanziaria", "regola": regola})
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "bad_request", 400)

    @pytest.mark.parametrize(
        ("preparazione", "code", "status"),
        [
            (lambda db: db.limiti.update({OWNER: 0}), "piano_non_include_call", 403),
            (lambda db: db.tabelle["company_data"][0].update(stato_impresa="Cessata"),
             "identita_non_verificata", 409),
            (lambda db: db.tabelle["company_data"][0].update(sandbox=True),
             "identita_non_verificata", 409),
        ],
        ids=["piano", "impresa_cessata", "sandbox_in_produzione"],
    )
    async def test_errori_della_rpc(self, preparazione, code, status):
        db = FakeDb()
        call = db.call_pronta()
        preparazione(db)
        errore = await attendi_codice(
            pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]), code, status)
        if code == "identita_non_verificata":
            assert "pubblicare una call" in errore.message

    async def test_limite_sul_pool_delle_aziende_dell_owner(self):
        db = FakeDb()
        db.limiti[OWNER] = 1
        db.call_pronta(company_profile_id=COMPANY_B, stato="pubblicata", pubblicata_at=_iso(),
                       scadenza_call=(oggi() + timedelta(days=9)).isoformat(), bando_id=300)
        call = db.call_pronta()
        vista = await leggi(db, call)
        assert "limite_call_raggiunto" in {m.codice for m in vista.motivi_blocco}
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "limite_call_raggiunto", 409)
        db.limiti[OWNER] = None  # illimitato
        out = await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        assert out.stato == "pubblicata"

    async def test_incompleta(self):
        db = FakeDb()
        call = db.con_call(titolo="Un titolo abbastanza lungo", descrizione_pubblica="Testo",
                           regole_partenariato=snapshot_minimo(), regole_confermate_at=_iso())
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "call_incompleta", 400)

    async def test_solo_bozza(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() + timedelta(days=9)).isoformat())
        await attendi_codice(pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"]),
                             "stato_call_non_valido", 409)

    async def test_bozza_pronta_puo_pubblicare(self):
        db = FakeDb()
        out = await leggi(db, db.call_pronta())
        assert out.motivi_blocco == [] and out.puo_pubblicare is True


# ------------------------------------------------------------ chiusura e versioni


class TestWp6DopoLeScritture:
    """WP6: dopo la pubblicazione l'indice si invalida e parte (in background)
    il fan-out; dopo una modifica della call pubblicata o la sua chiusura, chi
    l'ha salvata riceve una notifica (dedup per versione / chiusura)."""

    @staticmethod
    def _seguita_da_altri(db, call) -> None:
        db.tabelle["profiles"][1].update(email="anna@example.test", is_active=True)
        db.tabelle.setdefault("partner_call_salvate", []).append(
            {"company_profile_id": ALTRA_COMPANY, "partner_call_id": call["id"],
             "user_id": ALTRO_OWNER})

    @staticmethod
    def _notifiche(db, tipo):
        return [n for n in db.tabelle["notifications"] if n["tipo"] == tipo]

    async def test_pubblica_invalida_e_avvia_il_fan_out(self, spawned):
        from app.services import partenariato_indice

        db = FakeDb()
        call = db.call_pronta()
        generazione = partenariato_indice._STATO.generazione
        await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        assert partenariato_indice._STATO.generazione > generazione
        [job] = spawned
        assert job.cr_code.co_name == "dopo_pubblicazione"
        assert job.cr_frame.f_locals["call_id"] == call["id"]
        assert job.cr_frame.f_locals["company_id"] == COMPANY

    async def test_modifica_e_chiusura_notificano_chi_segue(self, spawned):
        db = FakeDb()
        call = db.call_pronta()
        await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        self._seguita_da_altri(db, call)
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(descrizione_pubblica="Descrizione aggiornata"))
        [modifica] = self._notifiche(db, "partenariato.call_seguita_modificata")
        assert modifica["user_id"] == ALTRO_OWNER
        assert modifica["company_profile_id"] == ALTRA_COMPANY
        assert modifica["dedup_key"] == f"partner-seguita:{call['id']}:v2:{ALTRA_COMPANY}"
        assert "Rossi" not in f"{modifica['titolo']} {modifica['corpo']}"
        await pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         ChiudiIn(esito="completata"))
        [chiusura] = self._notifiche(db, "partenariato.call_seguita_chiusa")
        assert chiusura["dedup_key"] == f"partner-seguita-chiusa:{call['id']}:{ALTRA_COMPANY}"

    @pytest.mark.parametrize(
        "modifica",
        [
            # nessun cambiamento: la RPC non scrive e non crea una versione
            {"descrizione_pubblica": "Progetto di innovazione nella logistica sostenibile."},
            # soli campi riservati: versione nuova, proiezione pubblica identica
            {"dettagli_riservati": "Altro accordo riservato con il cliente"},
            {"budget_progetto_eur": Decimal("700000")},
            {"quota_creatore_pct": Decimal("55")},
        ],
        ids=["senza_modifiche", "dettagli_riservati", "budget_esatto", "quota_creatore"],
    )
    async def test_modifiche_invisibili_ai_terzi_non_notificano(self, spawned, modifica):
        db = FakeDb()
        call = db.call_pronta()
        await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        self._seguita_da_altri(db, call)
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(**modifica))
        assert self._notifiche(db, "partenariato.call_seguita_modificata") == []

    async def test_call_passata_solo_su_invito_nessun_segnale(self, spawned):
        # Chi la segue non la vede più (404): né «modificata» né «chiusa».
        db = FakeDb()
        call = db.call_pronta()
        await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        self._seguita_da_altri(db, call)
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(visibilita="solo_invitati"))
        await pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                         ChiudiIn(esito="completata"))
        assert self._notifiche(db, "partenariato.call_seguita_modificata") == []
        assert self._notifiche(db, "partenariato.call_seguita_chiusa") == []

    async def test_chiusura_automatica_di_una_call_solo_su_invito_non_notifica(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(), visibilita="solo_invitati",
                              scadenza_call=(oggi() - timedelta(days=1)).isoformat())
        self._seguita_da_altri(db, call)
        assert await pcs.chiudi_automaticamente(db, call, "scaduta", "scadenza_call")
        assert self._notifiche(db, "partenariato.call_seguita_chiusa") == []

    async def test_una_bozza_modificata_non_notifica(self):
        db = FakeDb()
        call = db.call_pronta()
        self._seguita_da_altri(db, call)
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(descrizione_pubblica="Descrizione aggiornata"))
        assert self._notifiche(db, "partenariato.call_seguita_modificata") == []

    async def test_chiusura_automatica_notifica_chi_segue(self):
        db = FakeDb()
        call = db.call_pronta(stato="pubblicata", pubblicata_at=_iso(),
                              scadenza_call=(oggi() - timedelta(days=1)).isoformat())
        self._seguita_da_altri(db, call)
        assert await pcs.chiudi_automaticamente(db, call, "scaduta", "scadenza_call")
        [chiusura] = self._notifiche(db, "partenariato.call_seguita_chiusa")
        assert chiusura["user_id"] == ALTRO_OWNER


class TestChiudiEVersioni:
    async def test_chiudi_annullata_e_completata(self):
        db = FakeDb()
        bozza = db.con_call()
        out = await pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, bozza["id"],
                               ChiudiIn(esito="annullata"))
        assert (out.stato, out.motivo_chiusura) == ("chiusa_annullata", "creatore_annullata")
        altra = db.call_pronta(bando_id=222)
        await attendi_codice(pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, altra["id"],
                                        ChiudiIn(esito="completata")),
                             "stato_call_non_valido", 409)

    async def test_versioni_a_whitelist(self):
        db = FakeDb()
        call = db.call_pronta()
        await pcs.pubblica(db, FakeSecondary(), titolare(), USER_OWNER, call["id"])
        await pcs.aggiorna(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                           CallAggiornaIn(descrizione_pubblica="Descrizione aggiornata"))
        versioni = await pcs.versioni(db, FakeSecondary(), membro(), USER_MEMBRO, call["id"])
        assert [v.versione for v in versioni] == [2, 1]
        snapshot = versioni[0].snapshot
        assert snapshot["call"]["descrizione_pubblica"] == "Descrizione aggiornata"
        assert snapshot["call"]["dettagli_riservati"] == CANARY_RISERVATI  # azienda creatrice
        for interno in ("family_parent_id", "creato_da", "company_profile_id", "sospeso_da",
                        "partenariato_ref", "ai_check_id"):
            assert interno not in snapshot["call"]
        assert OWNER not in str(snapshot)
        assert set(snapshot["requisiti"][0]) <= {
            "id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine",
            "rif_origine", "citazione", "copertura_creatore", "copertura_fonte",
            "copertura_nota", "ordine"}

    async def test_versioni_di_un_altra_azienda_404(self):
        db = FakeDb()
        call = db.call_pronta(company_profile_id=COMPANY_B)
        with pytest.raises(NotFoundError):
            await pcs.versioni(db, FakeSecondary(), titolare(COMPANY), USER_OWNER, call["id"])


# ------------------------------------------------------------ lista


class TestLista:
    async def test_solo_l_azienda_attiva(self):
        db = FakeDb()
        mia = db.call_pronta()
        db.call_pronta(company_profile_id=COMPANY_B, bando_id=333)
        db.call_pronta(company_profile_id=ALTRA_COMPANY, family_parent_id=ALTRO_OWNER,
                       bando_id=444)
        pagina = await pcs.lista_mie(db, FakeSecondary(), membro(), USER_MEMBRO)
        assert pagina.total == 1 and [str(c.id) for c in pagina.items] == [mia["id"]]
        [card] = pagina.items
        assert (card.mia, card.posizioni_n, card.requisiti_cercati_n) == (True, 1, 1)
        assert card.creatore.denominazione == "Azienda anonima"
        assert CANARY_RISERVATI not in pagina.model_dump_json()

    async def test_senza_azienda_pagina_vuota(self):
        senza = ActiveCompany(company_id=None, owner_id=OWNER, editable=True)
        pagina = await pcs.lista_mie(FakeDb(), FakeSecondary(), senza, USER_OWNER)
        assert pagina.total == 0 and pagina.items == []


# ------------------------------------------------------------ segnalazioni


def segnalazione(oggetto_id, **campi) -> SegnalazioneIn:
    return SegnalazioneIn.model_validate({
        "oggetto_tipo": "call", "oggetto_id": oggetto_id, "motivo": "contatti_nel_testo",
        "descrizione": "Nel testo c'è un numero di telefono", "buona_fede": True, **campi})


class TestSegnalazioni:
    def call_pubblica(self, db, **modifiche):
        return db.call_pronta(**{"stato": "pubblicata", "pubblicata_at": _iso(),
                                 "scadenza_call": (oggi() + timedelta(days=9)).isoformat(),
                                 **modifiche})

    async def test_ricevuta_con_snapshot_e_conferma(self):
        db = FakeDb()
        call = self.call_pubblica(db)
        out = await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO,
                                segnalazione(call["id"]))
        assert out.stato == "ricevuta"
        [riga] = db.tabelle["partner_segnalazioni"]
        assert riga["oggetto_id"] == call["id"] and riga["segnalante_user_id"] == ALTRO_OWNER
        assert riga["segnalante_company_id"] == ALTRA_COMPANY and riga["buona_fede"] is True
        snapshot = riga["contenuto_snapshot"]
        assert snapshot["id"] == call["id"] and snapshot["creatore"]["anonima"] is True
        for vietato in (CANARY_RISERVATI, COMPANY, OWNER, PIVA):
            assert vietato not in str(snapshot)
        [notifica] = db.tabelle["notifications"]
        assert notifica["user_id"] == ALTRO_OWNER
        assert notifica["tipo"] == "partenariato.segnalazione_ricevuta"
        assert notifica["dedup_key"] == f"segnalazione:{out.id}"

    async def test_doppione_409(self):
        db = FakeDb()
        call = self.call_pubblica(db)
        await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO, segnalazione(call["id"]))
        errore = await attendi_codice(
            pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO, segnalazione(call["id"])),
            "segnalazione_gia_presente", 409)
        assert errore.message == "Hai già segnalato questo contenuto"

    @pytest.mark.parametrize(
        "modifiche",
        [{"stato": "bozza", "pubblicata_at": None}, {"visibilita": "solo_invitati"},
         {"stato": "sospesa_moderazione"}],
        ids=["bozza", "solo_invitati", "sospesa"],
    )
    async def test_non_visibile_404(self, modifiche):
        db = FakeDb()
        call = self.call_pubblica(db, **modifiche)
        with pytest.raises(NotFoundError):
            await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO,
                              segnalazione(call["id"]))
        assert db.tabelle["partner_segnalazioni"] == []

    async def test_azienda_non_viva_404(self):
        db = FakeDb()
        call = self.call_pubblica(db)
        db.tabelle["company_profiles"][0]["archived_at"] = _iso()
        with pytest.raises(NotFoundError):
            await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO,
                              segnalazione(call["id"]))

    async def test_rate_limit(self, monkeypatch):
        monkeypatch.setenv("PARTNER_SEGNALAZIONI_LIMITE_GIORNO", "1")
        from app.core.config import get_settings

        get_settings.cache_clear()
        db = FakeDb()
        call = self.call_pubblica(db)
        altra = self.call_pubblica(db, bando_id=777)
        await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO, segnalazione(call["id"]))
        await attendi_codice(
            pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO, segnalazione(altra["id"])),
            "limite_segnalazioni", 429)

    async def test_profilo_visibile(self):
        db = FakeDb()
        codice = str(uuid.uuid4())
        db.tabelle["company_partner_profiles"][0].update(
            codice_pubblico=codice, visibile_come_partner=True, anonimo=True, sospeso_at=None,
            descrizione_competenze="Sviluppo software", family_parent_id=OWNER)
        out = await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO,
                                segnalazione(codice, oggetto_tipo="profilo"))
        assert out.stato == "ricevuta"
        [riga] = db.tabelle["partner_segnalazioni"]
        assert riga["oggetto_tipo"] == "profilo" and riga["oggetto_id"] == codice
        assert riga["contenuto_snapshot"]["codice_pubblico"] == codice
        assert COMPANY not in str(riga["contenuto_snapshot"])
        db.tabelle["company_partner_profiles"][0]["visibile_come_partner"] = False
        with pytest.raises(NotFoundError):
            await pcs.segnala(db, FakeSecondary(), altro_owner(), {**USER_ALTRO, "id": MEMBRO},
                              segnalazione(codice, oggetto_tipo="profilo"))

    async def test_errore_del_db_senza_dettagli(self, caplog):
        db = FakeDb()
        call = self.call_pubblica(db)
        db.guasti[("partner_segnalazioni", "insert")] = errore_pg("23514")
        with pytest.raises(UpstreamError):
            await pcs.segnala(db, FakeSecondary(), altro_owner(), USER_ALTRO,
                              segnalazione(call["id"]))
        assert "riga (x)" not in caplog.text


# ------------------------------------------------------------ helper condivisi


class TestUltimoReady:
    async def test_ultimo_ready_dell_azienda_sul_bando(self):
        db = FakeDb()
        vecchio = db.con_ai_check({"v": 1}, created_at="2026-09-01T10:00:00+00:00")
        nuovo = db.con_ai_check({"v": 2}, created_at="2026-09-10T10:00:00+00:00")
        db.con_ai_check({"v": 3}, status="pending", created_at="2026-09-20T10:00:00+00:00")
        db.con_ai_check({"v": 4}, company_profile_id=COMPANY_B,
                        created_at="2026-09-21T10:00:00+00:00")
        db.con_ai_check({"v": 5}, bando_id=999, created_at="2026-09-22T10:00:00+00:00")
        riga = await ai_check_service.ultimo_ready(db, owner_id=OWNER, company_id=COMPANY,
                                                   bando_id=BANDO_ID)
        assert riga["id"] == nuovo["id"] and riga["id"] != vecchio["id"]
        assert await ai_check_service.ultimo_ready(db, owner_id=ALTRO_OWNER, company_id=COMPANY,
                                                   bando_id=BANDO_ID) is None
        assert all(op[1] == "select" for op in db.ops)


class TestCallAperte:
    async def test_conta_le_pubblicate_visibili_non_scadute(self):
        db = FakeDb()
        futura = (oggi() + timedelta(days=3)).isoformat()
        db.con_call(stato="pubblicata", scadenza_call=futura)
        db.con_call(stato="pubblicata", scadenza_call=oggi().isoformat(),
                    company_profile_id=COMPANY_B)
        db.con_call(stato="pubblicata", scadenza_call=(oggi() - timedelta(days=1)).isoformat())
        db.con_call(stato="pubblicata", scadenza_call=futura, visibilita="solo_invitati")
        db.con_call(stato="bozza")
        db.con_call(stato="pubblicata", scadenza_call=futura, bando_id=999)
        assert await partenariato_service.calls_aperte(db, BANDO_ID) == 2

    async def test_guasto_vale_zero(self):
        db = FakeDb()
        db.guasti[("partner_calls", "select")] = RuntimeError("giù")
        assert await partenariato_service.calls_aperte(db, BANDO_ID) == 0


class TestErroriEImpostazioni:
    @pytest.mark.parametrize(
        ("detail", "status", "code"),
        [
            ("owner_not_found", 404, "not_found"),
            ("call_not_found", 404, "not_found"),
            ("piano_non_include_call", 403, "piano_non_include_call"),
            ("limite_call_raggiunto", 409, "limite_call_raggiunto"),
            ("troppe_bozze", 409, "troppe_bozze"),
            ("call_gia_presente", 409, "call_gia_presente"),
            ("stato_call_non_valido", 409, "stato_call_non_valido"),
            ("campo_non_modificabile", 400, "campo_non_modificabile"),
            ("scadenza_call_non_valida", 400, "scadenza_call_non_valida"),
            ("call_incompleta", 400, "call_incompleta"),
            ("bando_non_disponibile", 409, "bando_non_disponibile"),
            ("ai_in_corso", 409, "ai_in_corso"),
            ("ai_limite_call", 429, "ai_limite_giornaliero"),
            ("dati_non_validi", 400, "bad_request"),
            ("regole_non_valide", 400, "bad_request"),
            ("requisiti_non_validi", 400, "bad_request"),
            ("posizioni_non_valide", 400, "bad_request"),
            ("attore_non_titolare", 403, "forbidden"),
        ],
    )
    async def test_detail_wp5_mappati(self, detail, status, code):
        db = FakeDb()
        call = db.con_call()
        db.rpc_errori["fn_partner_call_chiudi"] = detail
        errore = await attendi_codice(
            pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                       ChiudiIn(esito="annullata")), code, status)
        if detail == "attore_non_titolare":
            assert "call di partenariato" in errore.message

    @pytest.mark.parametrize("detail", ["parametri_non_validi", "versione_immutabile",
                                        "stato_non_valido"])
    async def test_detail_di_bug_restano_502(self, detail):
        db = FakeDb()
        call = db.con_call()
        db.rpc_errori["fn_partner_call_chiudi"] = detail
        with pytest.raises(UpstreamError):
            await pcs.chiudi(db, FakeSecondary(), titolare(), USER_OWNER, call["id"],
                             ChiudiIn(esito="annullata"))

    def test_default_delle_settings_wp5(self, monkeypatch):
        import os

        from app.core.config import Settings

        for chiave in list(os.environ):
            if chiave.upper().startswith("PARTNER_"):
                monkeypatch.delenv(chiave)
        s = Settings(_env_file=None, primary_supabase_url="https://dummy.supabase.co",
                     primary_supabase_service_role_key="k",
                     secondary_supabase_url="https://d2.supabase.co",
                     secondary_supabase_anon_key="k")
        attesi = {
            "partner_call_bozze_max": 5, "partner_call_ai_limite_giorno": 10,
            "partner_call_ai_limite_owner_giorno": 30, "partner_call_ai_max_tokens": 6000,
            "partner_call_ai_timeout_seconds": 90.0, "partner_call_scadenza_default_giorni": 60,
            "partner_segnalazioni_limite_giorno": 10, "partner_call_ai_stale_minuti": 10,
        }
        assert {k: getattr(s, k) for k in attesi} == attesi
