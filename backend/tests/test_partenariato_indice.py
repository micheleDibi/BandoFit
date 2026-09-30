"""Indice in-process del matching (WP6, docs/partenariati.md M4): ricarica
paginata a keyset e a blocchi con gli embed, niente `raw` intero né CF nei
select, TTL, invalidazione, una sola ricarica concorrente, stato live dei
bandi con ripiego sullo snapshot, T5 sui dati del registro, collegamenti
calcolati dalle chiavi HMAC, pseudonimi per call, ricontrollo live delle
aziende e delle call (revoca → sparisce subito anche con l'indice fresco),
budget di query della ricarica (≤ 20 con 500 aziende e 200 call).

Qui vive anche il primario FINTO del WP6 (tabelle in memoria, select con
colonne, alias, percorsi JSON ed embed, upsert/delete, le RPC della 0038),
usato dagli altri test del WP6."""

import asyncio
import copy
import random
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.schemas.bando import LookupsOut
from app.schemas.common import LookupItem
from app.services import (
    bandi_service,
    lookup_service,
    partenariato_collegamenti,
    partenariato_indice,
)
from app.services import partenariato_matching as pm
from app.services.bilanci_mapping import CAMPI_BILANCIO
from app.services.partenariato_accesso import (
    CALL_SELECT,
    POSIZIONE_SELECT,
    REQUISITO_SELECT,
    pseudonimo,
)
from tests.fixtures.partenariati import esempio_guida as g

# ------------------------------------------------------------ primario finto

_AUTO_ID = {"partner_notifiche_proattive", "partner_digest_invii", "notifications", "audit_log"}
_CHIAVI_UNICHE = {
    "partner_digest_runs": ("settimana",),
    "company_collegamenti": ("company_profile_id", "tipo", "chiave"),
    "company_collegamenti_stato": ("company_profile_id",),
    "partner_email_settings": ("user_id",),
    "partner_digest_invii": ("user_id", "settimana"),
    "partner_call_salvate": ("company_profile_id", "partner_call_id"),
    "notifications": ("user_id", "dedup_key"),
    "partner_notifiche_proattive": ("company_profile_id", "partner_call_id"),
}
# (tabella, embed) → (tabella figlia, colonna locale, colonna remota, «uno»|«molti»)
RELAZIONI = {
    ("company_profiles", "profiles"): ("profiles", "parent_id", "id", "uno"),
    ("company_profiles", "company_data"): ("company_data", "id", "company_profile_id", "uno"),
    ("company_profiles", "company_financials"):
        ("company_financials", "id", "company_profile_id", "molti"),
    ("company_profiles", "company_financials_stato"):
        ("company_financials_stato", "id", "company_profile_id", "uno"),
    ("company_profiles", "company_collegamenti"):
        ("company_collegamenti", "id", "company_profile_id", "molti"),
    ("company_profiles", "company_collegamenti_stato"):
        ("company_collegamenti_stato", "id", "company_profile_id", "uno"),
    # WP8: la riga del consorzio nata da una candidatura (indice unico).
    ("partner_candidature", "partner_call_membri"):
        ("partner_call_membri", "id", "candidatura_id", "uno"),
}


def _adesso_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def errore_pg(code: str) -> APIError:
    return APIError({"message": "vincolo", "code": code, "hint": None, "details": None})


def _dividi(testo: str) -> list[str]:
    parti, livello, corrente = [], 0, ""
    for ch in testo:
        if ch == "(":
            livello += 1
        elif ch == ")":
            livello -= 1
        if ch == "," and livello == 0:
            parti.append(corrente)
            corrente = ""
        else:
            corrente += ch
    if corrente.strip():
        parti.append(corrente)
    return [p.strip() for p in parti if p.strip()]


def _percorso(valore, passi: list[str]):
    for passo in passi:
        if not isinstance(valore, dict):
            return None
        valore = valore.get(passo)
    return valore


class FakeQuery:
    def __init__(self, db, tabella: str):
        self.db, self.tabella = db, tabella
        self.op, self.payload, self.colonne = "select", None, "*"
        self.filtri: list[tuple[str, str, object]] = []
        self.ordine: tuple[str, bool] | None = None
        self.limite: int | None = None
        self.intervallo: tuple[int, int] | None = None
        self.conta = None
        self.on_conflict, self.ignora = None, False

    # -- costruzione
    def select(self, colonne="*", count=None, **_k):
        self.colonne, self.conta = colonne, count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False, **_k):
        self.op, self.payload = "upsert", payload
        self.on_conflict, self.ignora = on_conflict, ignore_duplicates
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _f(self, op, c, v):
        self.filtri.append((op, c, v))
        return self

    def eq(self, c, v):
        return self._f("eq", c, v)

    def in_(self, c, v):
        return self._f("in", c, [str(x) for x in v])

    def is_(self, c, v):
        return self._f("is", c, v)

    def gt(self, c, v):
        return self._f("gt", c, v)

    def gte(self, c, v):
        return self._f("gte", c, v)

    def order(self, c, desc=False, **_k):
        self.ordine = (c, desc)
        return self

    def limit(self, n):
        self.limite = n
        return self

    def range(self, a, b):
        self.intervallo = (a, b)
        return self

    # -- valutazione
    def _passa(self, riga: dict) -> bool:
        for op, c, v in self.filtri:
            a = riga.get(c)
            if op == "eq" and (a is None or str(a) != str(v)):
                return False
            if op == "in" and str(a) not in v:
                return False
            if op == "is" and v == "null" and a is not None:
                return False
            if op in ("gt", "gte"):
                if a is None:
                    return False
                if op == "gt" and not a > v:
                    return False
                if op == "gte" and not a >= v:
                    return False
        return True

    def _proietta(self, tabella: str, riga: dict, colonne: str) -> dict:
        if colonne.strip() == "*":
            return copy.deepcopy(riga)
        out: dict = {}
        for voce in _dividi(colonne):
            if "(" in voce:
                nome = voce[: voce.index("(")].split(":")[-1].split("!")[0]
                interno = voce[voce.index("(") + 1 : voce.rindex(")")]
                relazione = RELAZIONI.get((tabella, nome))
                assert relazione is not None, f"embed sconosciuto {tabella}→{nome}"
                figlia, locale, remota, tipo = relazione
                righe = [r for r in self.db.tabelle.get(figlia, [])
                         if str(r.get(remota)) == str(riga.get(locale))]
                proiettate = [self._proietta(figlia, r, interno) for r in righe]
                out[nome] = (proiettate[0] if proiettate else None) if tipo == "uno" \
                    else proiettate
                continue
            alias, _, espressione = voce.rpartition(":")
            if "->" in espressione:
                parti = re.split(r"->>|->", espressione)
                valore = _percorso(riga.get(parti[0]), parti[1:])
                out[alias or parti[-1]] = copy.deepcopy(valore)
            else:
                assert espressione in riga or espressione in self.db.colonne_note.get(
                    tabella, ()), f"colonna sconosciuta {tabella}.{espressione}"
                out[alias or espressione] = copy.deepcopy(riga.get(espressione))
        return out

    async def execute(self):
        db = self.db
        db.ops.append({"tabella": self.tabella, "op": self.op, "select": self.colonne,
                       "filtri": list(self.filtri), "payload": copy.deepcopy(self.payload)})
        guasto = db.guasti.get((self.tabella, self.op))
        if guasto is not None:
            raise guasto
        righe = db.tabelle.setdefault(self.tabella, [])
        if self.op == "select":
            trovate = [r for r in righe if self._passa(r)]
            if self.ordine:
                colonna, disc = self.ordine
                trovate.sort(key=lambda r: (r.get(colonna) is None, r.get(colonna)
                                            if r.get(colonna) is not None else 0),
                             reverse=disc)
            totale = len(trovate)
            if self.intervallo:
                trovate = trovate[self.intervallo[0]: self.intervallo[1] + 1]
            if self.limite is not None:
                trovate = trovate[: self.limite]
            if db.max_righe is not None:
                trovate = trovate[: db.max_righe]  # come il max-rows di PostgREST
            dati = [self._proietta(self.tabella, r, self.colonne) for r in trovate]
            return SimpleNamespace(data=dati, count=totale if self.conta else None)
        if self.op == "delete":
            db.tabelle[self.tabella] = [r for r in righe if not self._passa(r)]
            return SimpleNamespace(data=[], count=None)
        if self.op == "update":
            aggiornate = []
            for riga in righe:
                if self._passa(riga):
                    riga.update(copy.deepcopy(self.payload))
                    aggiornate.append(copy.deepcopy(riga))
            return SimpleNamespace(data=aggiornate, count=None)
        nuove = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op == "insert":
            return SimpleNamespace(data=[db.inserisci(self.tabella, dict(r)) for r in nuove],
                                   count=None)
        chiavi = tuple((self.on_conflict or "").split(","))
        uscita = []
        for nuova in nuove:
            esistente = next((r for r in righe
                              if all(str(r.get(c)) == str(nuova.get(c)) for c in chiavi)), None)
            if esistente is None:
                uscita.append(db.inserisci(self.tabella, dict(nuova)))
            elif not self.ignora:
                esistente.update(copy.deepcopy(nuova))
                uscita.append(copy.deepcopy(esistente))
        return SimpleNamespace(data=uscita, count=None)


class FakePrimary:
    """Tabelle in memoria del DB primario con le RPC della 0038 (stesse
    guardie di `fn_partner_claim_notifica` e `fn_partner_fanout_claim`) e
    `fn_email_verificate`. `ops` registra ogni lettura e scrittura."""

    def __init__(self):
        self.tabelle: dict[str, list[dict]] = {}
        self.ops: list[dict] = []
        self.rpcs: list[tuple[str, dict]] = []
        self.guasti: dict = {}
        self.rpc_guasti: dict = {}
        self.email_non_verificate: set[str] = set()
        # max-rows di PostgREST (None = nessun tetto): vale per le select e per
        # le RPC che restituiscono righe
        self.max_righe: int | None = None
        self._id = 0
        # colonne che possono mancare nelle righe di test (null)
        self.colonne_note = {
            "company_profiles": ("sito_web", "codice_fiscale", "settore_id", "ragione_sociale"),
            "company_data": ("denominazione", "stato_impresa", "fetched_at", "sandbox"),
            "partner_calls": ("sospesa_at", "fanout_claim_at", "fanout_completato_at"),
            "company_partner_profiles": ("sospeso_at", "descrizione_competenze",
                                         "competenze_libere", "infrastrutture"),
            "company_people": ("nome", "cognome", "codice_fiscale",
                               "is_legale_rappresentante"),
            "company_financials": (*CAMPI_BILANCIO, "fonte_per_campo", "data_chiusura",
                                   "tipo_bilancio"),
        }

    def table(self, nome):
        return FakeQuery(self, nome)

    def rpc(self, nome, params):
        self.rpcs.append((nome, copy.deepcopy(params)))
        db = self

        class _Rpc:
            async def execute(self_inner):
                guasto = db.rpc_guasti.get(nome)
                if guasto is not None:
                    raise guasto
                dati = getattr(db, f"_{nome}")(params)
                if isinstance(dati, list) and db.max_righe is not None:
                    dati = dati[: db.max_righe]
                return SimpleNamespace(data=dati)

        return _Rpc()

    # -- scritture
    def inserisci(self, tabella: str, riga: dict) -> dict:
        righe = self.tabelle.setdefault(tabella, [])
        chiavi = _CHIAVI_UNICHE.get(tabella)
        if chiavi and any(all(str(r.get(c)) == str(riga.get(c)) for c in chiavi) for r in righe):
            raise errore_pg("23505")
        if tabella in _AUTO_ID and "id" not in riga:
            self._id += 1
            riga["id"] = self._id
        riga.setdefault("created_at", _adesso_iso())
        if tabella == "partner_email_settings":
            riga.setdefault("digest_abilitato", True)
            riga.setdefault("eventi_abilitati", True)
            riga.setdefault("unsubscribe_token", str(uuid.uuid4()))
        if tabella == "partner_digest_invii":
            riga.setdefault("stato", "in_invio")
            riga.setdefault("errore", None)
        if tabella == "partner_notifiche_proattive":
            riga.setdefault("digest_incluso_at", None)
        righe.append(riga)
        return copy.deepcopy(riga)

    # -- aiuti
    def righe(self, tabella, **filtri) -> list[dict]:
        return [r for r in self.tabelle.get(tabella, [])
                if all(str(r.get(k)) == str(v) for k, v in filtri.items())]

    def una(self, tabella, **filtri) -> dict:
        [riga] = self.righe(tabella, **filtri)
        return riga

    def chiamate(self, nome) -> list[dict]:
        return [p for n, p in self.rpcs if n == nome]

    def letture(self) -> list[dict]:
        return [o for o in self.ops if o["op"] == "select"]

    # -- RPC della 0038
    def _fn_partner_fanout_claim(self, p):
        ttl = timedelta(seconds=p["p_ttl_secondi"])
        for call in self.righe("partner_calls", id=p["p_call"]):
            if call["stato"] != "pubblicata" or call.get("fanout_completato_at"):
                return False
            claim = call.get("fanout_claim_at")
            if claim and datetime.fromisoformat(claim) > datetime.now(timezone.utc) - ttl:
                return False
            call["fanout_claim_at"] = _adesso_iso()
            return True
        return False

    def _fn_partner_claim_notifica(self, p):
        settimana = date.fromisoformat(p["p_settimana"])
        assert settimana.isoweekday() == 1, "la settimana deve essere un lunedì"
        azienda = next((r for r in self.righe("company_profiles", id=p["p_company"])
                        if not r.get("deleted_at") and not r.get("archived_at")), None)
        if azienda is None:
            return False
        profilo = next((r for r in self.righe("company_partner_profiles",
                                              company_profile_id=p["p_company"])
                        if r.get("visibile_come_partner") and not r.get("sospeso_at")), None)
        if profilo is None:
            return False
        call = next(iter(self.righe("partner_calls", id=p["p_call"])), None)
        if (call is None or call["stato"] != "pubblicata" or call["visibilita"] != "pubblica"
                or call["company_profile_id"] == p["p_company"]
                or call["family_parent_id"] == azienda["parent_id"]):
            return False
        if self.righe("partner_notifiche_proattive", company_profile_id=p["p_company"],
                      partner_call_id=p["p_call"]):
            return False
        if len(self.righe("partner_notifiche_proattive", company_profile_id=p["p_company"],
                          settimana=p["p_settimana"])) >= p["p_tetto"]:
            return False
        self.inserisci("partner_notifiche_proattive", {
            "company_profile_id": p["p_company"], "partner_call_id": p["p_call"],
            "settimana": p["p_settimana"], "copertura": p["p_copertura"],
            "punteggio": p["p_punteggio"],
        })
        return True

    def _fn_email_verificate(self, p):
        return [u for u in p["p_user_ids"] if str(u) not in self.email_non_verificate]


class FakeSecondary:
    """Catalogo finto: `bando_pubblico` (stato live dei bandi) e
    `bando_fusione` (doppione → master)."""

    def __init__(self, stati: dict[int, str] | None = None,
                 fusi: dict[int, int] | None = None):
        self.stati = stati or {}
        self.fusi = fusi or {}
        self.guasto: Exception | None = None
        self.guasto_fusioni: Exception | None = None
        self.letture = 0
        self.letture_fusioni = 0

    def table(self, nome):
        sec = self
        assert nome in ("bando_pubblico", "bando_fusione"), nome

        class _Q:
            def __init__(self):
                self.ids: list[int] = []

            def select(self, *a, **k):
                return self

            def in_(self, c, v):
                self.ids = list(v)
                return self

            async def execute(self):
                if nome == "bando_fusione":
                    sec.letture_fusioni += 1
                    if sec.guasto_fusioni is not None:
                        raise sec.guasto_fusioni
                    return SimpleNamespace(data=[
                        {"bando_id": i, "master_id": sec.fusi[i]}
                        for i in self.ids if i in sec.fusi
                    ])
                sec.letture += 1
                if sec.guasto is not None:
                    raise sec.guasto
                return SimpleNamespace(data=[
                    {"id": i, "slug": f"bando-{i}", "stato_effettivo": sec.stati[i],
                     "data_scadenza": "2027-01-31", "ultimo_cambiamento_at": None}
                    for i in self.ids if i in sec.stati
                ])

        return _Q()


LOOKUPS = LookupsOut(
    regioni=[LookupItem(id=g.CALABRIA, nome="Calabria"), LookupItem(id=g.LAZIO, nome="Lazio"),
             LookupItem(id=g.LOMBARDIA, nome="Lombardia")],
    settori=[LookupItem(id=g.SETTORE_ICT, nome="ICT")],
    beneficiari=[], codici_ateco=[], tipologie_bando=[], modalita_erogazione=[],
    programmi=[LookupItem(id=g.HORIZON, nome="Horizon Europe"),
               LookupItem(id=g.FESR, nome="PR FESR")],
)


@pytest.fixture(autouse=True)
def ambiente_wp6(monkeypatch):
    """Flag acceso, pepper fisso, indice e cache vuoti, catalogo e «oggi»
    finti (nessuna rete)."""
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "PARTENARIATI_ATTIVO": "true",
        "RATE_LIMIT_PEPPER": "pepe-di-test-wp6",
        "ALERT_PAUSA_INVII_SECONDI": "0",
        "FRONTEND_URL": "https://app.bandofit.test",
        "API_PUBLIC_URL": "https://api.bandofit.test/api/v1",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    partenariato_indice.reset()

    async def lookups(_secondary):
        return LOOKUPS

    monkeypatch.setattr(lookup_service, "get_lookups", lookups)
    monkeypatch.setattr(bandi_service, "today_italy", lambda: g.OGGI)
    yield
    partenariato_indice.reset()
    get_settings.cache_clear()


# ------------------------------------------------------ scenario della guida

T0 = "2026-09-01T10:00:00+00:00"
PIVA = {nome: f"{10000000001 + i:011d}" for i, nome in enumerate(g.NOMI)}
EMAIL = {nome: f"titolare.{nome.lower()}@example.test" for nome in g.NOMI}
RAGIONE = {nome: f"Impresa Sintetica {nome} Srl" for nome in g.NOMI}
# Socio persona fisica comune a X e W (60% in entrambe: collegamento «certo»).
CF_SOCIO_COMUNE = "RSSMRA80A01H501U"


def _raw(nome: str, derived: dict) -> dict:
    return {
        "companyDetails": {"companyName": RAGIONE[nome].upper(), "vatCode": PIVA[nome],
                           "taxCode": PIVA[nome]},
        "legalForm": {"legalForm": {"code": "SR",
                                    "description": "Società a responsabilità limitata"}},
        "innovativeSmeAndSu": {"isInnovativeStartUp": False, "isInnovativeSme": False},
        "artisanBusinessRegistry": {"belongsToArtisanBusinessRegistry": False},
        "soaCertification": {"hasSoaCertification": False},
        "atecoClassification": {"ateco": {"code": derived["ateco_principale"]}},
        "webAndSocial": {"website": None},
    }


def carica_guida(db: FakePrimary) -> FakePrimary:
    """Le righe dell'esempio guida nel primario finto (X…O, le tre call)."""
    for nome in g.NOMI:
        dati = g.AZIENDE[nome]
        db.tabelle.setdefault("profiles", []).append(
            {"id": g.OWNER[nome], "email": EMAIL[nome], "is_active": True})
        db.tabelle.setdefault("company_profiles", []).append({
            "id": g.COMPANY[nome], "parent_id": g.OWNER[nome],
            "ragione_sociale": RAGIONE[nome], "partita_iva": PIVA[nome],
            "codice_fiscale": PIVA[nome], "sito_web": None,
            "settore_id": dati["company"]["settore_id"], "deleted_at": None,
            "archived_at": None,
        })
        db.tabelle.setdefault("company_data", []).append({
            "company_profile_id": g.COMPANY[nome], "piva_fetched": PIVA[nome],
            "sandbox": False, "denominazione": RAGIONE[nome].upper(),
            "stato_impresa": dati["stato_impresa"], "derived": dati["derived"],
            "raw": _raw(nome, dati["derived"]), "fetched_at": T0,
        })
        if dati["profilo_partner"] is not None:
            db.tabelle.setdefault("company_partner_profiles", []).append(
                copy.deepcopy(dati["profilo_partner"]))
        for esercizio in dati["esercizi"]:
            db.tabelle.setdefault("company_financials", []).append(
                {"company_profile_id": g.COMPANY[nome], "data_chiusura": None,
                 "tipo_bilancio": "ordinario", **esercizio})
        db.tabelle.setdefault("company_financials_stato", []).append(
            {"company_profile_id": g.COMPANY[nome], "advanced_esito": "ok"})
    for nome in ("X", "W"):
        db.tabelle.setdefault("company_people", []).append({
            "company_profile_id": g.COMPANY[nome], "kind": "shareholder",
            "codice_fiscale": CF_SOCIO_COMUNE, "denominazione": None,
            "quota_percentuale": 60, "nome": "Mario", "cognome": "Rossi",
        })
    for call, requisiti, posizioni in (
        (g.CALL_GUIDA, g.REQUISITI_GUIDA, g.POSIZIONI_GUIDA),
        (g.CALL_ALTRA, g.REQUISITI_ALTRA, g.POSIZIONI_ALTRA),
        (g.CALL_RISERVATA, g.REQUISITI_RISERVATA, g.POSIZIONI_RISERVATA),
    ):
        db.tabelle.setdefault("partner_calls", []).append({
            **dict.fromkeys(CALL_SELECT.split(",")),
            **copy.deepcopy(call), "creato_da": call["family_parent_id"],
            "bando_stato_effettivo": "aperto", "versione": 1, "fanout_claim_at": None,
            "fanout_completato_at": None, "dettagli_riservati": "CANARY-RISERVATO",
            "profilo_partner_ideale": None, "descrizione_pubblica": "Descrizione sintetica.",
            "motivo_chiusura": None, "chiusa_at": None, "wizard_passo": 7,
            "regole_partenariato": None, "regole_confermate_at": T0,
            "override_non_ammesso_motivo": None, "partenariato_ref": None,
            "ai_check_id": None, "created_at": T0, "updated_at": T0,
            "bando_verificato_at": T0, "bando_mancante_dal": None, "sospeso_motivo": None,
        })
        for riga in requisiti:
            db.tabelle.setdefault("partner_call_requisiti", []).append({
                **dict.fromkeys(REQUISITO_SELECT.split(",")),
                **copy.deepcopy(riga), "copertura_creatore": "non_valutabile",
                "copertura_fonte": "nessuna", "copertura_nota": None, "citazione": None,
            })
        for riga in posizioni:
            db.tabelle.setdefault("partner_call_posizioni", []).append(
                {**dict.fromkeys(POSIZIONE_SELECT.split(",")), **copy.deepcopy(riga)})
    return db


def secondario_guida() -> FakeSecondary:
    return FakeSecondary({g.BANDO_GUIDA: "aperto", g.BANDO_ALTRO: "aperto",
                          g.BANDO_ALTRO + 1: "aperto"})


async def scenario_guida() -> tuple[FakePrimary, FakeSecondary]:
    """Primario con la guida e le chiavi dei collegamenti CALCOLATE (backfill
    vero sulle righe: W è collegata a X dal socio comune al 60%)."""
    db = carica_guida(FakePrimary())
    esito = await partenariato_collegamenti.backfill(db)
    assert esito["errori"] == 0
    db.ops.clear()
    return db, secondario_guida()


# ------------------------------------------------------ scenario sintetico


def popola_sintetico(db: FakePrimary, n_aziende: int = 500, n_call: int = 200,
                     seed: int = 42) -> tuple[FakePrimary, FakeSecondary]:
    """500 aziende con opt-in e 200 call pubblicate (dati sintetici, per il
    budget di query e per il benchmark). Chiavi e marker già scritti."""
    rnd = random.Random(seed)
    competenze = ["prototipazione_testing", "ricerca_industriale", "sviluppo_software",
                  "intelligenza_artificiale_dati", "dispositivi_medici_salute"]
    stati: dict[int, str] = {}
    for i in range(n_aziende):
        cid = f"c{i:07d}-0000-4000-8000-000000000000"
        owner = f"a{i:07d}-0000-4000-8000-000000000000"
        piva = f"{20000000001 + i:011d}"
        regione = rnd.choice([g.CALABRIA, g.LAZIO, g.LOMBARDIA])
        derived = {"ateco_principale": rnd.choice(["62.01.00", "72.19.09", "28.99.00"]),
                   "regione_id": regione, "regioni_ids": [regione],
                   "classe_dimensionale": rnd.choice(["micro", "piccola", "media"])}
        derived["ateco_divisione"] = derived["ateco_principale"][:2]
        db.tabelle.setdefault("profiles", []).append(
            {"id": owner, "email": f"u{i}@example.test", "is_active": True})
        db.tabelle.setdefault("company_profiles", []).append({
            "id": cid, "parent_id": owner, "ragione_sociale": f"Sintetica {i} Srl",
            "partita_iva": piva, "codice_fiscale": piva, "sito_web": None,
            "settore_id": None, "deleted_at": None, "archived_at": None})
        db.tabelle.setdefault("company_data", []).append({
            "company_profile_id": cid, "piva_fetched": piva, "stato_impresa": "Attiva",
            "derived": derived, "raw": {"legalForm": {"legalForm": {"description": "Srl"}}},
            "fetched_at": T0, "denominazione": f"SINTETICA {i} SRL", "sandbox": False})
        db.tabelle.setdefault("company_partner_profiles", []).append({
            "company_profile_id": cid, "codice_pubblico": str(uuid.UUID(int=i + 1)),
            "visibile_come_partner": True, "anonimo": True, "accetta_inviti": True,
            "sospeso_at": None, "competenze": rnd.sample(competenze, 2),
            "tipi_soggetto": rnd.choice([["organismo_ricerca"], []]),
            "certificazioni": [], "esperienze": [], "ruoli_disponibili": ["partner"],
            "forme_accettate": [], "categorie_bando_escluse": [], "regioni_interesse": [],
            "settori_interesse": [], "completezza": rnd.randint(30, 90)})
        for anno in (2023, 2024):
            db.tabelle.setdefault("company_financials", []).append({
                "company_profile_id": cid, "anno": anno, "data_chiusura": None,
                "tipo_bilancio": "ordinario", "fonte_per_campo": {},
                "fatturato": str(rnd.randint(100_000, 9_000_000)),
                "patrimonio_netto": str(rnd.randint(10_000, 900_000))})
        db.tabelle.setdefault("company_financials_stato", []).append(
            {"company_profile_id": cid, "advanced_esito": "ok"})
        # chiavi vere della riga (il marker le confronta con P.IVA e nome)
        for c in partenariato_collegamenti.chiavi_collegamento(
                db.tabelle["company_profiles"][-1], None, None):
            db.tabelle.setdefault("company_collegamenti", []).append({
                "company_profile_id": cid, "tipo": c.tipo, "chiave": c.chiave, "quota": None})
        db.tabelle.setdefault("company_collegamenti_stato", []).append(
            {"company_profile_id": cid, "algoritmo_versione": 1, "fonte_fetched_at": T0})
    for j in range(n_call):
        creatore = j % n_aziende
        cid = f"c{creatore:07d}-0000-4000-8000-000000000000"
        call_id = f"e{j:07d}-0000-4000-8000-000000000000"
        bando = 30000 + j
        stati[bando] = "aperto"
        db.tabelle.setdefault("partner_calls", []).append({
            "id": call_id, "company_profile_id": cid,
            "family_parent_id": f"a{creatore:07d}-0000-4000-8000-000000000000",
            "bando_id": bando, "bando_slug": f"bando-{bando}", "bando_titolo": f"Bando {bando}",
            "bando_scadenza": "2027-01-31", "bando_programma_id": g.HORIZON,
            "bando_tipologia_id": 3, "bando_stato_effettivo": "aperto",
            "ruolo_creatore": "capofila", "forma_aggregazione_prevista": "ats",
            "titolo": f"Call sintetica {j}", "budget_fascia": "1m_5m",
            "scadenza_call": "2026-12-31", "visibilita": "pubblica", "stato": "pubblicata",
            "esclusivita": False, "pubblicata_at": T0, "sospesa_at": None})
        for k, (etichetta, criterio) in enumerate((
            ("A", {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]}),
            ("B", {"tipo": "tag", "tags": [rnd.choice(competenze)], "modalita": "almeno_uno"}),
            ("C", {"tipo": "regione", "regioni_ids": [rnd.choice([g.CALABRIA, g.LAZIO])],
                   "modalita": "sede_attuale"}),
        )):
            db.tabelle.setdefault("partner_call_requisiti", []).append({
                "id": f"f{j:07d}-{k:04d}-4000-8000-000000000000", "call_id": call_id,
                "etichetta": etichetta, "criterio": criterio,
                "ambito": "ogni_membro" if etichetta == "C" else "consorzio",
                "cercato": etichetta != "C", "ordine": k})
        db.tabelle.setdefault("partner_call_posizioni", []).append({
            "id": f"b{j:07d}-0000-4000-8000-000000000000", "call_id": call_id,
            "titolo": "Partner", "ruolo": "partner", "tipi_soggetto": [], "competenze": [],
            "ateco_divisioni": [], "regioni": [], "territorio_modalita": "qualsiasi",
            "paesi": [], "dimensioni": [], "quota_ipotizzata_pct": "20.00", "numero": 1,
            "ordine": 0})
    return db, FakeSecondary(stati)


# ------------------------------------------------------------------ test


async def test_ricarica_dalla_guida_stesso_ordine_della_parte_pura():
    db, sec = await scenario_guida()
    idx = await partenariato_indice.indice(db, sec)
    # nell'indice: le call su bandi aperti, i candidati con opt-in (V no)
    assert set(idx.matching.calls) == {g.CALL_GUIDA_ID, g.CALL_ALTRA_ID, g.CALL_RISERVATA_ID}
    assert set(idx.matching.candidati) == {g.COMPANY[n] for n in g.CANDIDATI_INDICE}
    # W collegata a X dalle chiavi HMAC (socio comune al 60%), non a mano
    assert g.COMPANY["W"] in idx.profili[g.COMPANY["X"]].collegate
    assert idx.profili[g.COMPANY["W"]].collegate == {g.COMPANY["X"]}
    risultati = pm.suggeriti_per_call(idx.matching, g.CALL_GUIDA_ID, oggi=g.OGGI)
    assert [m.company_id for m in risultati] == [g.COMPANY["Y"], g.COMPANY["T"]]
    assert risultati[0].spiegazione == g.SPIEGAZIONE_GUIDA
    assert (risultati[0].punteggio, risultati[1].punteggio) == (g.PUNTEGGIO_Y, g.PUNTEGGIO_T)
    per_te = pm.per_te(idx.matching, g.COMPANY["Y"], oggi=g.OGGI)
    assert [m.call_id for m in per_te] == [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]


async def test_select_senza_raw_intero_ne_cf_ne_riservati():
    db, sec = await scenario_guida()
    await partenariato_indice.indice(db, sec)
    letture = db.letture()
    assert letture
    for op in letture:
        voci = [v.strip() for v in _dividi(op["select"])]
        assert "raw" not in voci and "*" not in voci, op
        for vietato in ("codice_fiscale", "budget_progetto_eur", "dettagli_riservati",
                        "quota_creatore_pct", "creato_da", "testo", "copertura_creatore"):
            assert vietato not in re.split(r"[,():>\-]+", op["select"]), (vietato, op)
    # la P.IVA e il sito del registro si leggono SOLO per i creatori
    per_dati = [op for op in letture if op["tabella"] == "company_data"]
    for op in per_dati:
        if "r_piva" in op["select"]:
            [(_, _, ids)] = [f for f in op["filtri"] if f[0] == "in"]
            assert set(ids) <= {g.COMPANY["X"], g.COMPANY["O"]}


async def test_indice_non_conserva_piva_ne_testi_riservati():
    db, sec = await scenario_guida()
    idx = await partenariato_indice.indice(db, sec)
    testo = repr(idx)
    for nome in g.NOMI:
        assert PIVA[nome] not in testo
    assert "CANARY-RISERVATO" not in testo and CF_SOCIO_COMUNE not in testo


async def test_ttl_e_invalidazione(monkeypatch):
    db, sec = await scenario_guida()
    orologio = [1000.0]
    monkeypatch.setattr(partenariato_indice.time, "monotonic", lambda: orologio[0])
    primo = await partenariato_indice.indice(db, sec)
    letture = len(db.letture())
    assert await partenariato_indice.indice(db, sec) is primo
    assert len(db.letture()) == letture  # fresco: nessuna lettura
    orologio[0] += 59
    assert await partenariato_indice.indice(db, sec) is primo
    orologio[0] += 2  # oltre i 60 s
    secondo = await partenariato_indice.indice(db, sec)
    assert secondo is not primo and len(db.letture()) > letture
    partenariato_indice.invalida()
    terzo = await partenariato_indice.indice(db, sec)
    assert terzo is not secondo


async def test_ttl_dalle_settings(monkeypatch):
    monkeypatch.setenv("PARTENARIATO_INDICE_TTL_SECONDS", "0")
    from app.core.config import get_settings

    get_settings.cache_clear()
    db, sec = await scenario_guida()
    primo = await partenariato_indice.indice(db, sec)
    assert await partenariato_indice.indice(db, sec) is not primo


async def test_una_sola_ricarica_concorrente(monkeypatch):
    db, sec = await scenario_guida()
    ricariche = []
    originale = partenariato_indice._ricarica

    async def lenta(primary, secondary):
        ricariche.append(1)
        await asyncio.sleep(0.01)
        return await originale(primary, secondary)

    monkeypatch.setattr(partenariato_indice, "_ricarica", lenta)
    risultati = await asyncio.gather(*(partenariato_indice.indice(db, sec) for _ in range(5)))
    assert len(ricariche) == 1 and all(r is risultati[0] for r in risultati)


async def test_invalidazione_durante_la_ricarica_non_vale_come_fresca(monkeypatch):
    db, sec = await scenario_guida()
    originale = partenariato_indice._ricarica

    async def con_scrittura(primary, secondary):
        indice = await originale(primary, secondary)
        partenariato_indice.invalida()  # una scrittura arrivata a metà ricarica
        return indice

    monkeypatch.setattr(partenariato_indice, "_ricarica", con_scrittura)
    primo = await partenariato_indice.indice(db, sec)
    monkeypatch.setattr(partenariato_indice, "_ricarica", originale)
    assert await partenariato_indice.indice(db, sec) is not primo


async def test_paginazione_a_keyset(monkeypatch):
    monkeypatch.setattr(partenariato_indice, "PAGINA", 2)
    db, sec = await scenario_guida()
    idx = await partenariato_indice.indice(db, sec)
    assert len(idx.matching.candidati) == len(g.CANDIDATI_INDICE)
    assert len(idx.matching.calls[g.CALL_GUIDA_ID].requisiti) == len(g.REQUISITI_GUIDA)
    pagine = [op for op in db.letture() if op["tabella"] == "company_partner_profiles"
              and any(f[0] == "gt" for f in op["filtri"])]
    assert pagine  # la seconda pagina parte dall'ultima chiave letta


@pytest.mark.parametrize("stato", ["chiuso", "sospeso", "revocato", None])
async def test_bando_non_aperto_o_sparito_esclude_la_call(stato):
    db, _ = await scenario_guida()
    stati = {g.BANDO_ALTRO: "aperto", g.BANDO_ALTRO + 1: "aperto"}
    if stato is not None:
        stati[g.BANDO_GUIDA] = stato
    idx = await partenariato_indice.indice(db, FakeSecondary(stati))
    assert g.CALL_GUIDA_ID not in idx.matching.calls and g.CALL_GUIDA_ID not in idx.bacheca
    assert g.CALL_ALTRA_ID in idx.matching.calls
    # l'impegno sul bando resta (esclusività)
    assert g.BANDO_GUIDA in idx.impegni[g.COMPANY["X"]]


async def test_catalogo_in_errore_ripiega_sullo_snapshot_e_la_cache_vale_10_minuti(monkeypatch):
    db, _ = await scenario_guida()
    sec = secondario_guida()
    sec.guasto = RuntimeError("catalogo giù")
    db.una("partner_calls", id=g.CALL_ALTRA_ID)["bando_stato_effettivo"] = "chiuso"
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID in idx.matching.calls  # snapshot «aperto»
    assert g.CALL_ALTRA_ID not in idx.matching.calls  # snapshot «chiuso»
    sec.guasto = None
    partenariato_indice.invalida()
    await partenariato_indice.indice(db, sec)
    letture = sec.letture
    partenariato_indice.invalida()
    await partenariato_indice.indice(db, sec)
    assert sec.letture == letture  # stato dei bandi in cache


def _secondario_con_guida_fusa() -> FakeSecondary:
    """Il bando della call guida è un doppione fuso: assente dalla vista,
    presente in `bando_fusione`."""
    return FakeSecondary({g.BANDO_ALTRO: "aperto", g.BANDO_ALTRO + 1: "aperto"},
                         fusi={g.BANDO_GUIDA: g.BANDO_ALTRO})


async def test_bando_fuso_non_fa_sparire_la_call_e_vale_lo_snapshot():
    db, _ = await scenario_guida()
    sec = _secondario_con_guida_fusa()
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID in idx.matching.calls and g.CALL_GUIDA_ID in idx.bacheca
    assert sec.letture_fusioni == 1
    # lo snapshot decide: una call fusa con lo snapshot «chiuso» resta fuori
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["bando_stato_effettivo"] = "chiuso"
    partenariato_indice.reset()
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID not in idx.matching.calls


async def test_bando_fuso_in_cache_come_gli_altri_stati():
    db, _ = await scenario_guida()
    sec = _secondario_con_guida_fusa()
    await partenariato_indice.indice(db, sec)
    partenariato_indice.invalida()
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID in idx.matching.calls
    assert sec.letture_fusioni == 1


async def test_bando_fusione_illeggibile_non_vale_sparito_e_non_resta_in_cache():
    db, _ = await scenario_guida()
    sec = _secondario_con_guida_fusa()
    sec.guasto_fusioni = RuntimeError("bando_fusione giù")
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID in idx.matching.calls  # snapshot «aperto»
    assert g.CALL_ALTRA_ID in idx.matching.calls  # gli altri bandi dalla vista
    # alla ricarica dopo si riprova: ora il bando risulta sparito davvero
    sec.guasto_fusioni, sec.fusi = None, {}
    letture = sec.letture
    partenariato_indice.invalida()
    idx = await partenariato_indice.indice(db, sec)
    assert g.CALL_GUIDA_ID not in idx.matching.calls
    assert sec.letture_fusioni == 2
    assert sec.letture == letture + 1  # solo il bando non verificato si rilegge


async def test_budget_conta_la_lettura_delle_fusioni():
    db, _ = await scenario_guida()
    idx_vista = await partenariato_indice.indice(db, secondario_guida())
    partenariato_indice.reset()
    idx_fusa = await partenariato_indice.indice(db, _secondario_con_guida_fusa())
    assert idx_fusa.query == idx_vista.query + 1


async def test_t5_registro_di_un_altra_piva_non_conta():
    db, sec = await scenario_guida()
    db.una("company_data", company_profile_id=g.COMPANY["Y"])["piva_fetched"] = "99999999999"
    idx = await partenariato_indice.indice(db, sec)
    y = idx.profili[g.COMPANY["Y"]]
    assert y.registro_presente is False and y.regioni_ids == frozenset()
    assert y.stato_impresa is None
    assert idx.vetrine[g.COMPANY["Y"]].registro is None


async def test_owner_non_attivo_o_azienda_archiviata_non_viva():
    db, sec = await scenario_guida()
    db.una("profiles", id=g.OWNER["Y"])["is_active"] = False
    db.una("company_profiles", id=g.COMPANY["T"])["archived_at"] = T0
    idx = await partenariato_indice.indice(db, sec)
    assert idx.profili[g.COMPANY["Y"]].viva is False
    assert idx.profili[g.COMPANY["T"]].viva is False
    assert pm.suggeriti_per_call(idx.matching, g.CALL_GUIDA_ID, oggi=g.OGGI) == []


async def test_marker_vecchio_collegamenti_non_calcolati():
    db, sec = await scenario_guida()
    db.una("company_collegamenti_stato", company_profile_id=g.COMPANY["Y"])[
        "fonte_fetched_at"] = "2026-01-01T00:00:00+00:00"
    idx = await partenariato_indice.indice(db, sec)
    assert idx.profili[g.COMPANY["Y"]].collegamenti_ok is False
    y = idx.matching.candidati[g.COMPANY["Y"]]
    assert pm.esclusione(idx.matching.calls[g.CALL_GUIDA_ID], y, oggi=g.OGGI) == (
        "collegamenti_non_calcolati")


async def test_identita_cambiata_senza_import_collegamenti_non_calcolati():
    # Ragione sociale cambiata dalla scheda dell'azienda (nessun import): le
    # chiavi salvate non sono più quelle attuali → esclusa finché il backfill
    # non ricalcola (fail-closed), poi di nuovo suggeribile.
    db, sec = await scenario_guida()
    db.una("company_profiles", id=g.COMPANY["Y"])["ragione_sociale"] = "Laboratori Riuniti Srl"
    idx = await partenariato_indice.indice(db, sec)
    assert idx.profili[g.COMPANY["Y"]].collegamenti_ok is False
    assert idx.profili[g.COMPANY["T"]].collegamenti_ok is True
    assert (await partenariato_collegamenti.backfill(db))["ricalcolate"] == 1
    partenariato_indice.invalida()
    idx = await partenariato_indice.indice(db, sec)
    assert idx.profili[g.COMPANY["Y"]].collegamenti_ok is True


async def test_card_della_bacheca_con_titolo_ripulito_e_contatori():
    db, sec = await scenario_guida()
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["titolo"] = (
        "Cerchiamo un organismo di ricerca: scrivi a info@esempio.it")
    idx = await partenariato_indice.indice(db, sec)
    card = idx.bacheca[g.CALL_GUIDA_ID]
    assert "info@esempio.it" not in card.riga["titolo"]
    assert (card.posizioni_n, card.posti, card.requisiti_cercati_n) == (1, 1, 2)
    assert card.regioni_ids == {g.CALABRIA}
    assert card.ruoli == {"partner"}
    assert "budget_progetto_eur" not in card.riga and "dettagli_riservati" not in card.riga


async def test_filtro_per_regione_solo_sui_requisiti_visibili():
    # Un requisito che copre il creatore (consorzio, non cercato: i terzi non
    # lo vedono) su una sede in Lombardia non deve far trovare la call con il
    # filtro «Lombardia»: rivelerebbe una sede del creatore. Un requisito
    # visibile (cercato) sulla stessa regione sì.
    db, sec = await scenario_guida()
    nascosto = g.riga_requisito(
        "f0000000-0000-4000-8000-000000000099", g.CALL_GUIDA_ID, "L", 9,
        {"tipo": "regione", "regioni_ids": [g.LOMBARDIA], "modalita": "sede_attuale"})
    db.tabelle["partner_call_requisiti"].append(nascosto)
    idx = await partenariato_indice.indice(db, sec)
    assert idx.bacheca[g.CALL_GUIDA_ID].regioni_ids == {g.CALABRIA}
    nascosto["cercato"] = True
    partenariato_indice.invalida()
    idx = await partenariato_indice.indice(db, sec)
    assert idx.bacheca[g.CALL_GUIDA_ID].regioni_ids == {g.CALABRIA, g.LOMBARDIA}


async def test_profilo_azienda_senza_opt_in_letto_al_volo():
    db, sec = await scenario_guida()
    idx = await partenariato_indice.indice(db, sec)
    assert g.COMPANY["V"] not in idx.profili
    v = await partenariato_indice.profilo_azienda(db, idx, g.COMPANY["V"])
    assert v is not None and v.visibile is False and v.viva is True
    # «Per te» anche senza opt-in (Q25)
    assert [m.call_id for m in pm.per_te(idx.matching, v, oggi=g.OGGI)] == [
        g.CALL_GUIDA_ID, g.CALL_ALTRA_ID]
    assert await partenariato_indice.profilo_azienda(db, idx, str(uuid.uuid4())) is None


async def test_profilo_al_volo_vede_i_collegamenti_con_le_chiavi_dell_indice():
    db, sec = await scenario_guida()
    # W revoca l'opt-in ma ha ancora le chiavi (es. una call non chiusa)
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["W"])[
        "visibile_come_partner"] = False
    idx = await partenariato_indice.indice(db, sec)
    w = await partenariato_indice.profilo_azienda(db, idx, g.COMPANY["W"])
    assert w.collegate == {g.COMPANY["X"]}
    assert g.CALL_GUIDA_ID not in [m.call_id for m in pm.per_te(idx.matching, w, oggi=g.OGGI)]


async def test_ricontrollo_live():
    db, sec = await scenario_guida()
    ids = [g.COMPANY[n] for n in ("Y", "T", "U", "Z", "V")]
    assert set(await partenariato_indice.ricontrollo_live(db, ids)) == {
        g.COMPANY[n] for n in ("Y", "T", "U", "Z")}
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
        "visibile_come_partner"] = False
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["T"])["sospeso_at"] = T0
    db.una("company_profiles", id=g.COMPANY["U"])["deleted_at"] = T0
    db.una("profiles", id=g.OWNER["Z"])["is_active"] = False
    assert await partenariato_indice.ricontrollo_live(db, ids) == {}
    assert await partenariato_indice.ricontrollo_live(db, []) == {}


async def test_ricontrollo_live_riporta_accetta_inviti():
    db, _ = await scenario_guida()
    db.una("company_partner_profiles", company_profile_id=g.COMPANY["Y"])[
        "accetta_inviti"] = False
    vivi = await partenariato_indice.ricontrollo_live(db, [g.COMPANY["Y"], g.COMPANY["T"]])
    assert vivi == {g.COMPANY["Y"]: {"accetta_inviti": False},
                    g.COMPANY["T"]: {"accetta_inviti": True}}


async def test_ricontrollo_call_live():
    db, _ = await scenario_guida()
    ids = [g.CALL_GUIDA_ID, g.CALL_ALTRA_ID, g.CALL_RISERVATA_ID]
    assert await partenariato_indice.ricontrollo_call_live(db, ids, oggi=g.OGGI) == {
        g.CALL_GUIDA_ID, g.CALL_ALTRA_ID}
    db.una("partner_calls", id=g.CALL_GUIDA_ID)["stato"] = "chiusa_completata"
    db.una("partner_calls", id=g.CALL_ALTRA_ID)["scadenza_call"] = "2026-10-01"
    assert await partenariato_indice.ricontrollo_call_live(db, ids, oggi=g.OGGI) == set()
    assert await partenariato_indice.ricontrollo_call_live(
        db, [g.CALL_RISERVATA_ID], oggi=g.OGGI, solo_pubbliche=False) == {g.CALL_RISERVATA_ID}
    db.una("company_profiles", id=g.COMPANY["O"])["deleted_at"] = T0
    assert await partenariato_indice.ricontrollo_call_live(
        db, [g.CALL_RISERVATA_ID], oggi=g.OGGI, solo_pubbliche=False) == set()


async def test_pseudonimi_per_call_e_risoluzione_lato_server():
    db, sec = await scenario_guida()
    idx = await partenariato_indice.indice(db, sec)
    codice = g.CODICE_PUBBLICO["Y"]
    uno = pseudonimo(g.CALL_GUIDA_ID, codice)
    assert len(uno) == 16 and re.fullmatch(r"[A-Z2-7]{16}", uno)
    assert uno == pseudonimo(g.CALL_GUIDA_ID, codice)
    assert uno != pseudonimo(g.CALL_ALTRA_ID, codice)
    assert codice not in uno and g.COMPANY["Y"] not in uno
    assert partenariato_indice.risolvi_pseudonimo(idx, g.CALL_GUIDA_ID, uno) == g.COMPANY["Y"]
    assert partenariato_indice.risolvi_pseudonimo(idx, g.CALL_ALTRA_ID, uno) is None
    assert partenariato_indice.risolvi_pseudonimo(idx, g.CALL_GUIDA_ID, "x" * 16) is None
    assert partenariato_indice.risolvi_pseudonimo(idx, g.CALL_GUIDA_ID, None) is None


async def test_errore_di_lettura_propaga_senza_indice_a_meta():
    db, sec = await scenario_guida()
    db.guasti[("company_profiles", "select")] = errore_pg("57014")
    with pytest.raises(APIError):
        await partenariato_indice.indice(db, sec)
    del db.guasti[("company_profiles", "select")]
    idx = await partenariato_indice.indice(db, sec)
    assert idx.matching.candidati


async def test_budget_di_query_con_500_aziende_e_200_call():
    db, sec = popola_sintetico(FakePrimary())
    idx = await partenariato_indice.indice(db, sec)
    assert len(idx.matching.calls) == 200 and len(idx.matching.candidati) == 500
    # chiavi e marker validi: il benchmark misura un matching vero
    assert all(p.collegamenti_ok for p in idx.matching.candidati.values())
    assert idx.query <= 20, idx.query
    letture_primario = len(db.letture())
    assert letture_primario + sec.letture + sec.letture_fusioni <= 20


class TestHookDeiCollegamenti:
    async def test_ricostruisci_best_effort_invalida_sempre(self, caplog):
        db, sec = await scenario_guida()
        primo = await partenariato_indice.indice(db, sec)
        db.guasti[("company_collegamenti", "delete")] = errore_pg("XX000")
        await partenariato_indice.ricostruisci_collegamenti(db, g.COMPANY["Y"])
        assert await partenariato_indice.indice(db, sec) is not primo
        # marker tolto per primo: Y ora è «non calcolata» (fail-closed)
        assert db.righe("company_collegamenti_stato", company_profile_id=g.COMPANY["Y"]) == []
        assert PIVA["Y"] not in caplog.text and g.COMPANY["Y"] in caplog.text

    async def test_rimozione_dopo_la_revoca_solo_senza_call_non_chiuse(self):
        db, _ = await scenario_guida()
        for nome in ("Y", "X"):
            db.una("company_partner_profiles", company_profile_id=g.COMPANY[nome])[
                "visibile_come_partner"] = False
        await partenariato_indice.rimuovi_collegamenti_se_non_idonea(db, g.COMPANY["Y"])
        await partenariato_indice.rimuovi_collegamenti_se_non_idonea(db, g.COMPANY["X"])
        assert db.righe("company_collegamenti", company_profile_id=g.COMPANY["Y"]) == []
        assert db.righe("company_collegamenti_stato", company_profile_id=g.COMPANY["Y"]) == []
        # X ha una call pubblicata: resta idonea, chiavi intatte
        assert db.righe("company_collegamenti", company_profile_id=g.COMPANY["X"])

    async def test_rimozione_non_solleva(self):
        db, _ = await scenario_guida()
        db.guasti[("company_partner_profiles", "select")] = errore_pg("XX000")
        await partenariato_indice.rimuovi_collegamenti_se_non_idonea(db, g.COMPANY["Y"])


class TestHookDellImport:
    """`openapi_service._persist_import` chiama il ricalcolo dei collegamenti
    SOLO con il flag acceso, dopo le persone (import locale, best-effort)."""

    @pytest.fixture
    def conferma(self, monkeypatch):
        from app.schemas.company import CompanyResponse
        from app.services import company_service, family_service, openapi_service
        from tests import test_openapi_service as t

        async def nessuna(primary, user_id):
            return None

        async def risposta(*_a, **_k):
            return CompanyResponse(editable=True, company=None)

        async def lookups(_secondary):
            return SimpleNamespace(codici_ateco=[], regioni=[], beneficiari=[], settori=[],
                                   tipologie=[], modalita=[], programmi=[])

        monkeypatch.setattr(family_service, "get_membership", nessuna)
        monkeypatch.setattr(company_service, "company_response_for_id", risposta)
        monkeypatch.setattr(lookup_service, "get_lookups", lookups)
        chiamate: list = []

        async def ricostruisci(primary, company_id):
            chiamate.append((company_id, [o[0] for o in primary.ops]))

        monkeypatch.setattr(partenariato_indice, "ricostruisci_collegamenti", ricostruisci)

        async def esegui():
            primary = t.FakePrimary(selects={
                "company_profiles": [t.COMPANY_ROW],
                "company_data": [{"fetched_at": _adesso_iso(), "fetch_count": 1}],
                "company_import_drafts": [t.draft_row()],
            })
            await openapi_service.confirm_import(primary, None, t._active(), t.PIVA)
            return chiamate

        return esegui

    async def test_flag_acceso(self, conferma):
        [(company_id, tabelle)] = await conferma()
        assert company_id == "c0000000-0000-0000-0000-000000000001"
        assert "company_people" in tabelle and "audit_log" not in tabelle

    async def test_flag_spento(self, conferma, monkeypatch):
        monkeypatch.setenv("PARTENARIATI_ATTIVO", "false")
        from app.core.config import get_settings

        get_settings.cache_clear()
        assert await conferma() == []
