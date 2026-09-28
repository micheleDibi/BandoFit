"""Test del servizio bilancio ufficiale (WP2): pre-check senza spesa, rami
della POST con chiusura e rimborso (Q5), avanzamento (claim, failsafe,
riconciliazione, completamento XBRL idempotente), poll-on-read, notifica,
download.

Il primario finto è a STATO e replica la semantica delle RPC della 0033
(tetti, consumo atomico, chiusura condizionata con rimborso una sola volta,
claim del poll con un solo vincitore) e i CHECK dei documenti; l'XBRL è
quello delle fixture del parser, dentro uno ZIP sintetico. Nessuna rete."""

import asyncio
import base64
import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.api.deps import ActiveCompany
from app.clients.openapi import (
    OpenapiBilancioNonDisponibileError,
    OpenapiCreditoProviderError,
    OpenapiFormaNonAmmessaError,
    OpenapiIdentificativoNonValidoError,
    OpenapiNonInviataError,
    OpenapiRispostaTroppoGrandeError,
)
from app.core.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    OpenapiNotConfiguredError,
    OpenapiTimeoutError,
    OpenapiUpstreamError,
    UpstreamError,
)
from app.services import bilancio_ufficiale_service as svc
from app.services import notification_service

FIXTURES = Path(__file__).parent / "fixtures"
OWNER = "a0000000-0000-0000-0000-000000000001"
ALTRO_OWNER = "a0000000-0000-0000-0000-000000000009"
COMPANY = "c0000000-0000-0000-0000-000000000001"
ALTRA_COMPANY = "c0000000-0000-0000-0000-000000000002"
PIVA = "09876543217"
PIVA_MASCHERATA = "098*****217"
RAGIONE_SOCIALE = "ALFA SINTETICA SRL"
ADDON_ID = 7
PROVIDER_ID = "6a4bf7252ba8a578e60896f2"
PDF = b"%PDF-1.4\n% bilancio sintetico\n" + b"0" * 2000
APERTI = ("in_invio", "in_lavorazione", "esito_ignoto")
CAMPI_CHIUSURA = {"errore_codice", "stato_provider", "anno_bilancio", "xbrl_esito", "avvisi"}


def _ora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(minuti_fa: float = 0) -> str:
    return (_ora() - timedelta(minutes=minuti_fa)).isoformat()


def _parse(valore) -> datetime:
    return datetime.fromisoformat(str(valore).replace("Z", "+00:00"))


def _api_error(detail: str | None, code: str = "P0001", message: str = "db") -> APIError:
    return APIError({"message": message, "code": code, "details": detail, "hint": None})


def xbrl(nome: str = "ordinario_2024.xbrl") -> bytes:
    return (FIXTURES / "xbrl" / nome).read_bytes()


def it_full(forma: str | None = "SC") -> dict:
    raw = json.loads((FIXTURES / "openapi" / "it_full_bilanci_sintetico.json").read_text())["data"]
    if forma is None:
        raw.pop("legalForm", None)
    else:
        raw["legalForm"]["legalForm"]["code"] = forma
    return raw


def zip_bilancio(*, pdf: bytes | None = PDF, istanza: bytes | None = None,
                 verbale: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if pdf is not None:
            zf.writestr("6a4b_bilancio.pdf", pdf)
        zf.writestr("6a4b_bilancio.xbrl", xbrl() if istanza is None else istanza)
        if verbale:
            zf.writestr("6a4b_verbale_assemblea.pdf", b"%PDF-1.4 verbale")
    return buf.getvalue()


def zip_senza_xbrl() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("6a4b_bilancio.pdf", PDF)
    return buf.getvalue()


def allegato(zip_bytes: bytes | None = None) -> dict:
    dati = zip_bilancio() if zip_bytes is None else zip_bytes
    return {"nome": f"{PROVIDER_ID}.zip", "dimensione": len(dati),
            "file": base64.b64encode(dati).decode()}


# ------------------------------------------------------------------- finti

class FakeQuery:
    def __init__(self, db: "FakeDB", table: str):
        self._db = db
        self._table = table
        self._op = "select"
        self._payload = None
        self._kwargs: dict = {}
        self._eq: dict = {}
        self._in: dict = {}
        self._order: tuple | None = None
        self._limit: int | None = None

    def select(self, colonne="*", *_a, **_k):
        self._db.selects.append((self._table, colonne))
        return self

    def insert(self, payload, **_k):
        self._op, self._payload = "insert", payload
        return self

    def upsert(self, payload, **kwargs):
        self._op, self._payload, self._kwargs = "upsert", payload, kwargs
        return self

    def update(self, payload, **_k):
        self._op, self._payload = "update", payload
        return self

    def eq(self, column, value):
        self._eq[column] = str(value)
        return self

    def in_(self, column, values):
        self._in[column] = [str(v) for v in values]
        return self

    def order(self, column, desc: bool = False):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    @staticmethod
    def _valore(row: dict, colonna: str):
        """Colonna, o chiave di un jsonb con l'operatore `->>` di PostgREST."""
        if "->>" in colonna:
            base, chiave = colonna.split("->>", 1)
            return (row.get(base) or {}).get(chiave)
        return row.get(colonna)

    def _match(self, row: dict) -> bool:
        if any(str(self._valore(row, c)) != v for c, v in self._eq.items()):
            return False
        return all(str(self._valore(row, c)) in vs for c, vs in self._in.items())

    async def execute(self):
        db = self._db
        db.ops.append((self._table, self._op, self._payload, dict(self._eq)))
        errore = db.errors.get((self._table, self._op))
        if errore is not None:
            raise errore
        righe = db.tabelle.setdefault(self._table, [])
        if self._op == "select":
            trovate = [dict(r) for r in righe if self._match(r)]
            if self._order:
                colonna, desc = self._order
                trovate.sort(key=lambda r: str(r.get(colonna)), reverse=desc)
            if self._limit is not None:
                trovate = trovate[: self._limit]
            return SimpleNamespace(data=trovate)
        if self._op == "insert":
            nuove = self._payload if isinstance(self._payload, list) else [self._payload]
            righe.extend(dict(r) for r in nuove)
            return SimpleNamespace(data=[dict(r) for r in nuove])
        if self._op == "upsert":
            db.upsert_kwargs.append((self._table, dict(self._kwargs)))
            return SimpleNamespace(data=db.upsert(self._table, self._payload, self._kwargs))
        if self._op == "update":
            aggiornate = []
            for riga in righe:
                if self._match(riga):
                    db.vincoli_richiesta(riga, self._payload)
                    riga.update(self._payload)
                    aggiornate.append(dict(riga))
            return SimpleNamespace(data=aggiornate)
        return SimpleNamespace(data=[])


class FakeDB:
    """Primario a stato con le RPC della 0033 (e fn_bilanci_registra_fonte)."""

    def __init__(self, **tabelle):
        self.tabelle: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in tabelle.items()}
        self.ops: list = []
        self.rpcs: list = []
        self.errors: dict = {}
        self.rpc_errors: dict = {}
        self.selects: list[tuple[str, str]] = []
        self.upsert_kwargs: list[tuple[str, dict]] = []
        self.quota_openapi: dict[str, int] = {}  # fn_openapi_prenota_operazione

    def table(self, name: str) -> FakeQuery:
        return FakeQuery(self, name)

    def rpc(self, name: str, params: dict):
        self.rpcs.append((name, params))
        db = self

        class _Rpc:
            async def execute(self_inner):
                if name in db.rpc_errors:
                    raise db.rpc_errors[name]
                funzione = {
                    "fn_bilancio_richiesta_crea": db.crea,
                    "fn_bilancio_richiesta_chiudi": db.chiudi,
                    "fn_bilancio_richiesta_claim_poll": db.claim,
                    "fn_bilanci_registra_fonte": db.registra_fonte,
                    "fn_openapi_prenota_operazione": db.prenota_openapi,
                }[name]
                return SimpleNamespace(data=funzione(params))

        return _Rpc()

    # --- vincoli
    def vincoli_richiesta(self, riga: dict, modifiche: dict) -> None:
        pid = modifiche.get("provider_request_id")
        if pid and any(
            r is not riga and r.get("provider_request_id") == pid
            for r in self.tabelle.get("company_bilancio_richieste", [])
        ):
            raise _api_error(None, code="23505", message="cbr_provider_id_uniq")

    def upsert(self, table: str, payload, kwargs: dict) -> list[dict]:
        nuove = payload if isinstance(payload, list) else [payload]
        righe = self.tabelle.setdefault(table, [])
        chiave = [c.strip() for c in kwargs.get("on_conflict", "").split(",") if c.strip()]
        for nuova in nuove:
            if table == "company_bilancio_documenti":
                self.vincoli_documento(nuova)
            esistente = next(
                (r for r in righe if chiave and all(r.get(c) == nuova.get(c) for c in chiave)),
                None,
            )
            if esistente is None:
                righe.append(dict(nuova))
            elif not kwargs.get("ignore_duplicates"):
                esistente.update(nuova)
        return []

    def vincoli_documento(self, doc: dict) -> None:
        """I CHECK e la FK composta di company_bilancio_documenti (0033)."""
        contenuto = doc["contenuto"]
        assert isinstance(contenuto, str) and contenuto.startswith("\\x")
        dati = bytes.fromhex(contenuto[2:])
        tetto = 8_388_608 if doc["tipo"] == "pdf" else 10_485_760
        richiesta = next(
            r for r in self.tabelle["company_bilancio_richieste"] if r["id"] == doc["richiesta_id"]
        )
        if (
            doc["tipo"] not in ("pdf", "xbrl")
            or not 1 <= doc["dimensione"] <= tetto
            or len(dati) != doc["dimensione"]
            or hashlib.sha256(dati).hexdigest() != doc["sha256"]
            or not 1 <= len(doc["nome_file"]) <= 200
            or richiesta["company_profile_id"] != doc["company_profile_id"]
        ):
            raise _api_error(None, code="23514", message="cbd check")

    # --- RPC
    def crea(self, params: dict) -> dict:
        p = params["p_payload"]
        adesso = _ora()
        righe = self.tabelle.setdefault("company_bilancio_richieste", [])
        recenti = [r for r in righe if _parse(r["created_at"]) > adesso - timedelta(hours=24)]
        if len(recenti) >= p["max_piattaforma"]:
            raise _api_error("bilanci_limite_piattaforma")
        owner = p["family_parent_id"]
        if not any(r["id"] == owner for r in self.tabelle.get("profiles", [])):
            raise _api_error("owner_not_found")
        if len([r for r in recenti if r["family_parent_id"] == owner]) >= p["max_owner"]:
            raise _api_error("bilanci_limite_owner")
        addon = next(
            (a for a in self.tabelle.get("addons", [])
             if a["id"] == p["addon_id"] and a["is_active"] and a.get("sempre_a_pagamento")),
            None,
        )
        if addon is None:
            raise _api_error("addon_not_available")
        if not any(
            c["id"] == p["company_profile_id"] and c["parent_id"] == owner
            and not c.get("deleted_at") and not c.get("archived_at")
            for c in self.tabelle.get("company_profiles", [])
        ):
            raise _api_error("company_not_found")
        if any(r["company_profile_id"] == p["company_profile_id"] and r["stato"] in APERTI
               for r in righe):
            raise _api_error("bilancio_in_corso")
        inventario = self.inventario(owner)
        if inventario is None or inventario["quantita"] <= 0:
            raise _api_error("addon_credit_esaurito")  # e nessuna riga inserita
        riga = nuova_richiesta(
            "in_invio", minuti_fa=0, company_profile_id=p["company_profile_id"],
            family_parent_id=owner, richiesto_da=p["richiesto_da"],
            partita_iva=p["partita_iva"], anno_richiesto=p["anno_richiesto"],
            addon_id=addon["id"], addon_prezzo=addon["prezzo"], sandbox=p["sandbox"],
            provider_request_id=None, inviata_at=None,
        )
        righe.append(riga)
        inventario["quantita"] -= 1
        self.tabelle.setdefault("addon_ledger", []).append({
            "user_id": owner, "addon_id": addon["id"], "tipo": "consume", "delta": -1,
            "request_id": riga["id"],
        })
        return {"richiesta": dict(riga), "quantita_residua": inventario["quantita"]}

    def chiudi(self, params: dict) -> dict:
        stato, campi = params["p_stato"], params["p_campi"] or {}
        rimborsa = bool(params["p_rimborsa"])
        if stato not in ("completata", "non_disponibile", "annullata", "errore"):
            raise _api_error("stato_non_valido")
        if rimborsa and stato == "completata":
            raise _api_error("stato_non_rimborsabile")
        if not isinstance(campi, dict) or set(campi) - CAMPI_CHIUSURA:
            raise _api_error("campi_non_validi")
        riga = next(
            (r for r in self.tabelle.get("company_bilancio_richieste", [])
             if r["id"] == params["p_richiesta_id"] and r["stato"] in APERTI),
            None,
        )
        if riga is None:
            return {"aggiornata": False, "rimborsata": False, "quantita_residua": None}
        riga.update(stato=stato, completata_at=_ora().isoformat(), **campi)
        ledger = self.tabelle.setdefault("addon_ledger", [])
        consumo = next(
            (m for m in ledger if m["tipo"] == "consume" and m["request_id"] == riga["id"]), None
        )
        gia = any(m["tipo"] == "refund" and m["request_id"] == riga["id"] for m in ledger)
        if rimborsa and consumo and not gia:
            ledger.append({
                "user_id": consumo["user_id"], "addon_id": consumo["addon_id"], "tipo": "refund",
                "delta": 1, "request_id": riga["id"],
                "note": f"rimborso automatico: {riga.get('errore_codice') or stato}",
            })
            inventario = self.inventario(consumo["user_id"])
            inventario["quantita"] += 1
            riga["rimborsata_at"] = _ora().isoformat()
            return {"aggiornata": True, "rimborsata": True,
                    "quantita_residua": inventario["quantita"]}
        return {"aggiornata": True, "rimborsata": False, "quantita_residua": None}

    def claim(self, params: dict) -> bool:
        riga = self.richiesta(params["p_richiesta_id"])
        if riga is None or riga["stato"] not in APERTI:
            return False
        ultimo = riga.get("ultimo_poll_at")
        if ultimo and _parse(ultimo) >= _ora() - timedelta(seconds=params["p_min_secondi"]):
            return False
        riga["ultimo_poll_at"] = _ora().isoformat()
        return True

    def registra_fonte(self, params: dict) -> dict:
        fonti = self.tabelle.setdefault("company_financials_fonti", [])
        for r in params["p_righe"]:
            esistente = next(
                (f for f in fonti if f["company_profile_id"] == params["p_company_id"]
                 and f["anno"] == r["anno"] and f["fonte"] == params["p_fonte"]),
                None,
            )
            nuova = {"company_profile_id": params["p_company_id"], "anno": r["anno"],
                     "fonte": params["p_fonte"], "ruolo": r["ruolo"], "valori": r["valori"],
                     "riferimento": params["p_riferimento"]}
            if esistente is None:
                fonti.append(nuova)
            elif not (esistente["ruolo"] == "corrente" and r["ruolo"] == "comparativo"):
                esistente.update(nuova)
        return {"anni": sorted({r["anno"] for r in params["p_righe"]})}

    def prenota_openapi(self, params: dict) -> bool:
        """Tetto giornaliero delle chiamate openapi a pagamento (0032), con
        una sola azienda gestibile: p_per_azienda al giorno."""
        usate = self.quota_openapi.get(params["p_owner"], 0)
        if usate >= params["p_per_azienda"]:
            return False
        self.quota_openapi[params["p_owner"]] = usate + 1
        return True

    # --- ispezione
    def inventario(self, owner: str = OWNER) -> dict | None:
        return next(
            (i for i in self.tabelle.get("user_addon_inventory", [])
             if i["user_id"] == owner and i["addon_id"] == ADDON_ID),
            None,
        )

    def richiesta(self, rid: str) -> dict | None:
        return next(
            (r for r in self.tabelle.get("company_bilancio_richieste", []) if r["id"] == rid),
            None,
        )

    def richieste(self) -> list[dict]:
        return self.tabelle.get("company_bilancio_richieste", [])

    def ledger(self, tipo: str | None = None) -> list[dict]:
        return [m for m in self.tabelle.get("addon_ledger", []) if tipo in (None, m["tipo"])]

    def documenti(self) -> list[dict]:
        return self.tabelle.get("company_bilancio_documenti", [])

    def eventi(self, service: str | None = None) -> list[dict]:
        return [e for e in self.tabelle.get("api_usage_events", [])
                if service in (None, e["service"])]

    def rpc_names(self) -> list[str]:
        return [n for n, _ in self.rpcs]

    def chiusure(self) -> list[dict]:
        return [p for n, p in self.rpcs if n == "fn_bilancio_richiesta_chiudi"]


class FakeOpenapi:
    """I metodi visure del client. Ogni attributo di risposta è un valore o
    un'eccezione da sollevare; `chiamate` registra (metodo, argomenti)."""

    def __init__(self, *, enabled: bool = True, sandbox: bool = False):
        self.enabled = enabled
        self.sandbox = sandbox
        self.chiamate: list[tuple] = []
        self.post: object = {"id": PROVIDER_ID, "stato_richiesta": "In ricerca"}
        self.imprese: object = [
            {"id": "x", "chiamate_disponibili": ["visurecamerali.openapi.it/bilancio-ottico"]}
        ]
        self.stato: object = {"stato_richiesta": "In erogazione"}
        self.allegati: object = allegato()
        self.lista: object = []

    @staticmethod
    def _esito(valore):
        if isinstance(valore, BaseException):
            raise valore
        return valore

    async def bilancio_ottico_richiedi(self, cf_piva, anno):
        self.chiamate.append(("richiedi", cf_piva, anno))
        return self._esito(self.post)

    async def impresa(self, cf_piva):
        self.chiamate.append(("impresa", cf_piva))
        return self._esito(self.imprese)

    async def bilancio_ottico_stato(self, provider_id):
        self.chiamate.append(("stato", provider_id))
        await asyncio.sleep(0)  # cede il turno: la concorrenza è reale
        return self._esito(self.stato)

    async def bilancio_ottico_allegati(self, provider_id, *, max_bytes):
        self.chiamate.append(("allegati", provider_id, max_bytes))
        return self._esito(self.allegati)

    async def bilancio_ottico_lista(self):
        self.chiamate.append(("lista",))
        return self._esito(self.lista)

    def nomi(self) -> list[str]:
        return [c[0] for c in self.chiamate]


# ---------------------------------------------------------------- fixture

def nuova_richiesta(stato: str = "in_lavorazione", *, minuti_fa: float = 2, **extra) -> dict:
    riga = {
        "id": str(uuid.uuid4()), "company_profile_id": COMPANY, "family_parent_id": OWNER,
        "richiesto_da": OWNER, "partita_iva": PIVA, "anno_richiesto": None,
        "anno_bilancio": None, "stato": stato, "stato_provider": None,
        "provider_request_id": PROVIDER_ID if stato == "in_lavorazione" else None,
        "errore_codice": None, "xbrl_esito": None, "avvisi": [], "addon_id": ADDON_ID,
        "addon_prezzo": "7.90", "costo_provider_cents": 0, "sandbox": False,
        "ultimo_poll_at": None,
        "inviata_at": _iso(minuti_fa) if stato == "in_lavorazione" else None,
        "completata_at": None, "rimborsata_at": None, "created_at": _iso(minuti_fa),
    }
    riga.update(extra)
    return riga


def addon_row(**over) -> dict:
    return {"id": ADDON_ID, "slug": "bilancio-ufficiale", "nome": "Bilancio ufficiale",
            "prezzo": "7.90", "tipo_prezzo": "importo", "etichetta_prezzo": None,
            "tipo_fruizione": "consumabile", "is_active": True, "sempre_a_pagamento": True,
            **over}


def db_base(*, forma: str | None = "SC", quantita: int = 2, **extra) -> FakeDB:
    tabelle = {
        "profiles": [{"id": OWNER}, {"id": ALTRO_OWNER}],
        "company_profiles": [
            {"id": COMPANY, "parent_id": OWNER, "ragione_sociale": RAGIONE_SOCIALE,
             "partita_iva": PIVA, "deleted_at": None, "archived_at": None},
            {"id": ALTRA_COMPANY, "parent_id": ALTRO_OWNER, "ragione_sociale": "BETA SRL",
             "partita_iva": PIVA, "deleted_at": None, "archived_at": None},
        ],
        "company_data": [{
            "company_profile_id": COMPANY, "raw": it_full(forma), "derived": {},
            "piva_fetched": PIVA, "sandbox": False, "fetch_count": 1, "fetched_at": _iso(600),
        }],
        "addons": [addon_row()],
        "user_addon_inventory": [{"user_id": OWNER, "addon_id": ADDON_ID, "quantita": quantita}],
        "addon_ledger": [],
    }
    tabelle.update(extra)
    return FakeDB(**tabelle)


def db_con(riga: dict, **extra) -> FakeDB:
    """DB con una richiesta già creata (e la sua unità già consumata)."""
    db = db_base(quantita=1, **extra)
    db.tabelle.setdefault("company_bilancio_richieste", []).append(riga)
    db.tabelle["addon_ledger"].append({
        "user_id": riga["family_parent_id"], "addon_id": ADDON_ID, "tipo": "consume",
        "delta": -1, "request_id": riga["id"],
    })
    return db


def _active(company_id: str | None = COMPANY, editable: bool = True) -> ActiveCompany:
    return ActiveCompany(company_id=company_id, owner_id=OWNER, editable=editable)


USER = {"id": OWNER}


@pytest.fixture(autouse=True)
def spawned(monkeypatch):
    """`_spawn` cattura le coroutine senza avviarle; a fine test le chiude."""
    coros: list = []
    monkeypatch.setattr(svc, "_spawn", coros.append)
    yield coros
    for coro in coros:
        coro.close()


@pytest.fixture(autouse=True)
def notifiche(monkeypatch):
    chiamate: list = []

    async def fake(primary, user_ids, **kwargs):
        chiamate.append({"user_ids": [str(u) for u in user_ids], **kwargs})

    monkeypatch.setattr(notification_service, "notify", fake)
    return chiamate


@pytest.fixture(autouse=True)
def facet_invalidati(monkeypatch):
    ids: list = []
    monkeypatch.setattr(
        "app.services.compatibility.invalidate_company_facets", lambda cid: ids.append(cid)
    )
    return ids


async def esegui(coros: list) -> None:
    """Esegue (e toglie dalla lista) le coroutine catturate da `_spawn`."""
    while coros:
        await coros.pop(0)


# ---------------------------------------------------------------- pre-check

class TestPreCheck:
    """Tutto ciò che blocca la richiesta PRIMA della spesa: nessuna RPC di
    creazione, nessuna POST, inventario intatto."""

    @staticmethod
    def nessuna_spesa(db: FakeDB, openapi: FakeOpenapi, quantita: int = 2) -> None:
        assert "fn_bilancio_richiesta_crea" not in db.rpc_names()
        assert "richiedi" not in openapi.nomi()
        assert db.inventario()["quantita"] == quantita
        assert db.ledger() == []

    async def test_openapi_non_configurato(self):
        db, openapi = db_base(), FakeOpenapi(enabled=False)
        with pytest.raises(OpenapiNotConfiguredError):
            await svc.richiedi(db, openapi, _active(), USER, None)
        self.nessuna_spesa(db, openapi)

    async def test_membro_non_editable_403(self):
        db, openapi = db_base(), FakeOpenapi()
        with pytest.raises(ForbiddenError):
            await svc.richiedi(db, openapi, _active(editable=False), USER, None)
        self.nessuna_spesa(db, openapi)

    async def test_senza_azienda_404(self):
        db, openapi = db_base(), FakeOpenapi()
        with pytest.raises(NotFoundError):
            await svc.richiedi(db, openapi, _active(company_id=None), USER, None)
        self.nessuna_spesa(db, openapi)

    async def test_senza_dati_importati_404(self):
        db, openapi = db_base(company_data=[]), FakeOpenapi()
        with pytest.raises(NotFoundError):
            await svc.richiedi(db, openapi, _active(), USER, None)
        self.nessuna_spesa(db, openapi)

    async def test_piva_del_profilo_diversa_400(self):
        db, openapi = db_base(), FakeOpenapi()
        db.tabelle["company_profiles"][0]["partita_iva"] = "01234567897"
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert exc.value.status_code == 400
        self.nessuna_spesa(db, openapi)

    async def test_societa_di_capitali_senza_impresa(self):
        db, openapi = db_base(forma="SC"), FakeOpenapi()
        out = await svc.richiedi(db, openapi, _active(), USER, None)
        assert out.stato == "in_lavorazione"
        assert "impresa" not in openapi.nomi()  # la forma di IT-full basta

    async def test_societa_di_persone_409_senza_rete(self):
        db, openapi = db_base(forma="SP"), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (409, "bilancio_non_richiedibile")
        assert openapi.chiamate == []
        self.nessuna_spesa(db, openapi)

    @pytest.mark.parametrize("forma", ["AL", None], ids=["altre_forme", "forma_ignota"])
    async def test_impresa_si(self, forma):
        db, openapi = db_base(forma=forma), FakeOpenapi()
        out = await svc.richiedi(db, openapi, _active(), USER, None)
        assert out.stato == "in_lavorazione"
        assert openapi.nomi() == ["impresa", "richiedi"]
        [evento] = db.eventi("visure-impresa")
        assert evento["outcome"] == "success" and evento["cost_cents"] == 0
        assert evento["request_meta"]["piva"] == PIVA_MASCHERATA

    async def test_impresa_no_409(self):
        db, openapi = db_base(forma="AL"), FakeOpenapi()
        openapi.imprese = [{"id": "x", "chiamate_disponibili": [
            "visurecamerali.openapi.it/ordinaria-impresa-individuale"]}]
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (409, "bilancio_non_richiedibile")
        self.nessuna_spesa(db, openapi)

    async def test_impresa_sconosciuta_409(self):
        db, openapi = db_base(forma="AL"), FakeOpenapi()
        openapi.imprese = []
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert exc.value.code == "bilancio_non_richiedibile"
        self.nessuna_spesa(db, openapi)

    @pytest.mark.parametrize(
        "errore", [OpenapiUpstreamError(), OpenapiTimeoutError(), OpenapiNonInviataError(),
                   RuntimeError("boom")],
        ids=["upstream", "timeout", "non_inviata", "inatteso"],
    )
    async def test_impresa_errore_502_senza_spendere(self, errore):
        db, openapi = db_base(forma="AL"), FakeOpenapi()
        openapi.imprese = errore
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (502, "openapi_error")
        self.nessuna_spesa(db, openapi)
        [evento] = db.eventi("visure-impresa")
        assert evento["outcome"] == "error"

    async def test_impresa_nel_tetto_giornaliero_fail_closed(self):
        # Un 409 dopo /impresa non consuma unità né crea righe: senza il tetto
        # openapi un ciclo di POST pagherebbe /impresa all'infinito.
        db, openapi = db_base(forma="AL"), FakeOpenapi()
        openapi.imprese = [{"id": "x", "chiamate_disponibili": []}]
        for _ in range(3):  # OPERAZIONI_OPENAPI_PER_AZIENDA_GIORNO con 1 azienda
            with pytest.raises(AppError) as exc:
                await svc.richiedi(db, openapi, _active(), USER, None)
            assert exc.value.code == "bilancio_non_richiedibile"
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (429, "limite_giornaliero_openapi")
        assert "importazioni" not in exc.value.message
        assert openapi.nomi() == ["impresa"] * 3  # la quarta non chiama il provider
        self.nessuna_spesa(db, openapi)

    async def test_impresa_tetto_non_verificabile_502_senza_chiamata(self):
        db, openapi = db_base(forma="AL"), FakeOpenapi()
        db.rpc_errors["fn_openapi_prenota_operazione"] = _api_error(None, code="08006")
        with pytest.raises(UpstreamError):
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert openapi.chiamate == []
        self.nessuna_spesa(db, openapi)

    async def test_societa_di_capitali_non_usa_il_tetto(self):
        db, openapi = db_base(forma="SC"), FakeOpenapi()
        await svc.richiedi(db, openapi, _active(), USER, None)
        assert "fn_openapi_prenota_operazione" not in db.rpc_names()

    async def test_ultimo_disponibile_gia_posseduto_409(self):
        ultimo = svc._adesso().astimezone(svc._FUSO_ITALIA).year - 1
        fonti = [{"company_profile_id": COMPANY, "anno": ultimo, "fonte": "xbrl",
                  "ruolo": "corrente"}]
        db, openapi = db_base(company_financials_fonti=fonti), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (409, "bilancio_gia_presente")
        assert str(ultimo) in exc.value.message
        self.nessuna_spesa(db, openapi)
        # con solo un esercizio più vecchio «ultimo disponibile» resta possibile
        fonti[0]["anno"] = ultimo - 1
        db2 = db_base(company_financials_fonti=fonti)
        out = await svc.richiedi(db2, FakeOpenapi(), _active(), USER, None)
        assert out.stato == "in_lavorazione"

    async def test_anno_gia_acquisito_409(self):
        fonti = [
            {"company_profile_id": COMPANY, "anno": 2024, "fonte": "xbrl", "ruolo": "corrente"},
            {"company_profile_id": COMPANY, "anno": 2023, "fonte": "xbrl", "ruolo": "comparativo"},
            {"company_profile_id": COMPANY, "anno": 2022, "fonte": "it_full", "ruolo": "corrente"},
        ]
        db, openapi = db_base(company_financials_fonti=fonti), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, 2024)
        assert (exc.value.status_code, exc.value.code) == (409, "bilancio_gia_presente")
        self.nessuna_spesa(db, openapi)
        # un anno solo comparativo o di altra fonte non blocca
        for anno in (2023, 2022):
            db2 = db_base(company_financials_fonti=fonti)
            out = await svc.richiedi(db2, FakeOpenapi(), _active(), USER, anno)
            assert out.anno_richiesto == anno

    async def test_cortesia_payment_required(self):
        db, openapi = db_base(quantita=0), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (409, "payment_required")
        self.nessuna_spesa(db, openapi, quantita=0)

    async def test_addon_inattivo_404(self):
        db, openapi = db_base(addons=[addon_row(is_active=False)]), FakeOpenapi()
        with pytest.raises(NotFoundError):
            await svc.richiedi(db, openapi, _active(), USER, None)
        self.nessuna_spesa(db, openapi)

    async def test_richiesta_aperta_409_prima_di_impresa(self):
        db, openapi = db_con(nuova_richiesta("in_lavorazione"), company_data=[{
            "company_profile_id": COMPANY, "raw": it_full("AL"), "piva_fetched": PIVA,
        }]), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (409, "bilancio_in_corso")
        assert openapi.chiamate == []  # nemmeno /impresa


class TestErroriRpc:
    @pytest.mark.parametrize(
        ("detail", "status", "code"),
        [
            ("addon_credit_esaurito", 409, "payment_required"),
            ("addon_not_available", 404, "not_found"),
            ("bilancio_in_corso", 409, "bilancio_in_corso"),
            ("bilanci_limite_owner", 429, "limite_bilanci_giornaliero"),
            ("bilanci_limite_piattaforma", 503, "bilanci_sospesi"),
            ("company_not_found", 404, "not_found"),
            ("owner_not_found", 404, "not_found"),
            ("sconosciuto", 502, "upstream_error"),
        ],
    )
    async def test_mappatura(self, detail, status, code):
        db, openapi = db_base(), FakeOpenapi()
        db.rpc_errors["fn_bilancio_richiesta_crea"] = _api_error(detail)
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert (exc.value.status_code, exc.value.code) == (status, code)
        assert exc.value.message
        assert "richiedi" not in openapi.nomi()

    async def test_tetto_per_owner_dal_db(self):
        vecchie = [nuova_richiesta("errore", minuti_fa=10 * i, company_profile_id=ALTRA_COMPANY)
                   for i in range(1, 6)]
        db, openapi = db_base(company_bilancio_richieste=vecchie), FakeOpenapi()
        with pytest.raises(AppError) as exc:
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert exc.value.code == "limite_bilanci_giornaliero"
        assert db.inventario()["quantita"] == 2  # rollback: nessun consumo

    async def test_payload_della_rpc(self):
        db, openapi = db_base(), FakeOpenapi(sandbox=True)
        await svc.richiedi(db, openapi, _active(), USER, 2023)
        [(_, params)] = [(n, p) for n, p in db.rpcs if n == "fn_bilancio_richiesta_crea"]
        assert params["p_payload"] == {
            "company_profile_id": COMPANY, "family_parent_id": OWNER, "richiesto_da": OWNER,
            "partita_iva": PIVA, "anno_richiesto": 2023, "addon_id": ADDON_ID, "sandbox": True,
            "max_piattaforma": 30, "max_owner": 5,
        }


# ------------------------------------------------------------------- POST

class TestPost:
    async def test_accettata(self, spawned):
        db, openapi = db_base(), FakeOpenapi()
        out = await svc.richiedi(db, openapi, _active(), USER, 2024)
        [riga] = db.richieste()
        assert riga["stato"] == "in_lavorazione"
        assert riga["provider_request_id"] == PROVIDER_ID
        assert riga["costo_provider_cents"] == 450 and riga["inviata_at"]
        assert openapi.chiamate == [("richiedi", PIVA, 2024)]
        # consumo atomico: una unità
        assert db.inventario()["quantita"] == 1 and len(db.ledger("consume")) == 1
        # registro: la POST a costo pieno, con l'id del provider e la P.IVA mascherata
        [evento] = db.eventi("bilancio-ottico")
        assert (evento["outcome"], evento["cost_cents"]) == ("success", 450)
        assert (evento["user_id"], evento["family_parent_id"]) == (OWNER, OWNER)
        assert evento["request_meta"]["provider_request_id"] == PROVIDER_ID
        assert evento["request_meta"]["piva"] == PIVA_MASCHERATA
        # risposta senza id del provider; follower avviato
        assert out.stato == "in_lavorazione" and out.id == riga["id"]
        assert PROVIDER_ID not in out.model_dump_json()
        assert len(spawned) == 1
        # audit senza P.IVA
        [audit] = db.tabelle["audit_log"]
        assert audit["action"] == "company.bilancio_ufficiale_richiesto"
        assert PIVA not in json.dumps(audit)

    async def test_sandbox_costo_zero(self):
        db = db_base()
        await svc.richiedi(db, FakeOpenapi(sandbox=True), _active(), USER, None)
        assert db.richieste()[0]["costo_provider_cents"] == 0
        assert db.eventi("bilancio-ottico")[0]["cost_cents"] == 0

    @pytest.mark.parametrize(
        ("errore", "stato", "codice"),
        [
            (OpenapiBilancioNonDisponibileError("278"), "non_disponibile",
             "bilancio_non_disponibile"),
            (OpenapiFormaNonAmmessaError("213"), "non_disponibile", "forma_non_ammessa"),
            (OpenapiIdentificativoNonValidoError("275"), "non_disponibile",
             "identificativo_non_valido"),
            (OpenapiNonInviataError(), "errore", "non_inviata"),
            (OpenapiCreditoProviderError("611"), "errore", "credito_provider"),
        ],
        ids=["278", "213", "275", "non_inviata", "credito_provider"],
    )
    async def test_rifiuti_chiusi_con_rimborso(self, errore, stato, codice, spawned, notifiche):
        db, openapi = db_base(), FakeOpenapi()
        openapi.post = errore
        out = await svc.richiedi(db, openapi, _active(), USER, None)
        [riga] = db.richieste()
        assert (riga["stato"], riga["errore_codice"]) == (stato, codice)
        assert riga["rimborsata_at"] and out.rimborsata is True
        assert out.stato == stato and out.messaggio
        # unità restituita una volta, all'owner, legata alla richiesta
        assert db.inventario()["quantita"] == 2
        [refund] = db.ledger("refund")
        assert refund["request_id"] == riga["id"] and refund["user_id"] == OWNER
        [evento] = db.eventi("bilancio-ottico")
        assert (evento["outcome"], evento["cost_cents"]) == ("error", 0)
        assert openapi.nomi() == ["richiedi"]  # mai un secondo invio
        assert spawned == [] and notifiche == []  # esito sincrono: l'utente è presente

    @pytest.mark.parametrize(
        "errore", [OpenapiTimeoutError(), OpenapiUpstreamError(), RuntimeError("boh")],
        ids=["timeout", "5xx_o_non_json", "inatteso"],
    )
    async def test_esito_ignoto_senza_rimborso(self, errore, spawned):
        db, openapi = db_base(), FakeOpenapi()
        openapi.post = errore
        out = await svc.richiedi(db, openapi, _active(), USER, None)
        [riga] = db.richieste()
        assert riga["stato"] == "esito_ignoto" and riga["rimborsata_at"] is None
        assert out.stato == "esito_ignoto"
        assert db.inventario()["quantita"] == 1 and db.ledger("refund") == []
        [evento] = db.eventi("bilancio-ottico")
        assert (evento["outcome"], evento["cost_cents"]) == ("timeout_unknown", 450)
        assert openapi.nomi() == ["richiedi"]  # MAI retry
        assert len(spawned) == 1  # il follower riconcilia
        assert "fn_bilancio_richiesta_chiudi" not in db.rpc_names()

    async def test_update_dopo_accettazione_fallito_resta_rintracciabile(self, spawned):
        db, openapi = db_base(), FakeOpenapi()
        db.errors[("company_bilancio_richieste", "update")] = _api_error(None, code="08006")
        out = await svc.richiedi(db, openapi, _active(), USER, None)
        # la riga resta in_invio (failsafe → riconciliazione), l'id pagato è nel registro
        assert out.stato == "in_invio"
        assert db.eventi("bilancio-ottico")[0]["request_meta"]["provider_request_id"] == (
            PROVIDER_ID
        )
        assert len(spawned) == 1

    async def test_chiusura_fallita_sale_come_502(self):
        db, openapi = db_base(), FakeOpenapi()
        openapi.post = OpenapiBilancioNonDisponibileError("278")
        db.rpc_errors["fn_bilancio_richiesta_chiudi"] = _api_error("campi_non_validi")
        with pytest.raises(UpstreamError):
            await svc.richiedi(db, openapi, _active(), USER, None)
        assert db.richieste()[0]["stato"] == "in_invio"  # la chiuderà il failsafe


# ------------------------------------------------------------------ avanza

class TestAvanzaClaim:
    async def test_claim_concorrente_un_solo_download(self):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        await asyncio.gather(svc.avanza(db, openapi, dict(riga)), svc.avanza(db, openapi, dict(riga)))
        assert openapi.nomi().count("allegati") == 1
        assert openapi.nomi().count("stato") == 1
        assert db.richiesta(riga["id"])["stato"] == "completata"

    async def test_claim_recente_nessun_lavoro(self):
        riga = nuova_richiesta("in_lavorazione", ultimo_poll_at=_iso(0.5))
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert openapi.chiamate == []

    async def test_riga_chiusa_ignorata(self):
        riga = nuova_richiesta("completata")
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert openapi.chiamate == [] and db.rpcs == []


class TestFailsafeInvio:
    async def test_in_invio_fresca_resta(self):
        riga = nuova_richiesta("in_invio", minuti_fa=2)
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_invio"
        assert openapi.chiamate == []  # la POST può essere ancora in volo

    async def test_in_invio_stantia_diventa_esito_ignoto(self):
        riga = nuova_richiesta("in_invio", minuti_fa=6)
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert openapi.nomi() == ["lista"]  # riconciliazione tentata subito
        assert "fn_bilancio_richiesta_chiudi" not in db.rpc_names()  # < 30 minuti


class TestRiconciliazione:
    @staticmethod
    def voce(minuti_fa: float = 10, **over) -> dict:
        """Richiesta del provider nata `minuti_fa` minuti fa (le righe dei test
        nascono negli stessi minuti: dentro la finestra della POST)."""
        return {"id": "prov-x", "cf_piva_id": PIVA, "anno_chiusura": None,
                "tipo": "bilancio-ottico", "stato_richiesta": "In erogazione",
                "timestamp_creation": int((_ora() - timedelta(minutes=minuti_fa)).timestamp()),
                **over}

    async def test_trovata_diventa_in_lavorazione(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=10)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [self.voce()]
        await svc.avanza(db, openapi, dict(riga))
        aggiornata = db.richiesta(riga["id"])
        assert aggiornata["stato"] == "in_lavorazione"
        assert aggiornata["provider_request_id"] == "prov-x"
        assert aggiornata["costo_provider_cents"] == 450
        # e subito lo stato del provider di quella richiesta
        assert ("stato", "prov-x") in openapi.chiamate
        assert db.ledger("refund") == []

    async def test_anno_e_timestamp_iso(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=10, anno_richiesto=2023)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [
            self.voce(id="anno-sbagliato", anno_chiusura="2024"),
            self.voce(id="giusta", anno_chiusura="2023",
                      timestamp_creation=(_ora() - timedelta(minutes=10)).isoformat()),
        ]
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["provider_request_id"] == "giusta"

    async def test_non_trovata_dopo_30_minuti_non_inviata_con_rimborso(self, notifiche):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [
            self.voce(31, id="altra-piva", cf_piva_id="01234567897"),
            self.voce(180, id="troppo-vecchia"),  # prima di created_at − 1 min
        ]
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "non_inviata")
        assert chiusa["rimborsata_at"] and db.inventario()["quantita"] == 2
        [n] = notifiche
        assert n["tipo"] == "bilancio_ufficiale.non_disponibile"
        assert "restituita" in n["corpo"]

    @pytest.mark.parametrize(
        "dubbia",
        [
            {"id": "altro-anno", "anno_chiusura": "2023"},
            {"id": "altro-prodotto", "tipo": "ordinaria-societa-capitale"},
            {"id": "fuori-finestra", "minuti_fa": 1},
            {"id": "data-illeggibile", "timestamp_creation": "ieri"},
            {"id": None},
        ],
        ids=["altro_anno", "altro_tipo", "dopo_la_finestra", "data_illeggibile", "senza_id"],
    )
    async def test_voce_della_stessa_piva_non_attribuita_nessun_rimborso(self, dubbia):
        # Una richiesta della stessa P.IVA, non di altre righe, che potrebbe
        # essere la POST: l'assenza non è provata, quindi niente rimborso
        # (dopo 24 ore esito ignoto scaduto, all'admin).
        def voce_dubbia(eta_riga: float) -> dict:
            campi = dict(dubbia)
            minuti = campi.pop("minuti_fa", eta_riga)
            voce = self.voce(minuti, **campi)
            if voce["id"] is None:
                del voce["id"]
            return voce

        riga = nuova_richiesta("esito_ignoto", minuti_fa=31, anno_richiesto=2024)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [voce_dubbia(31)]
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert db.ledger("refund") == []

        eta = 24 * 60 + 5
        vecchia = nuova_richiesta("esito_ignoto", minuti_fa=eta, anno_richiesto=2024)
        db, openapi = db_con(vecchia), FakeOpenapi()
        openapi.lista = [voce_dubbia(eta)]
        await svc.avanza(db, openapi, dict(vecchia))
        chiusa = db.richiesta(vecchia["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "esito_ignoto_scaduto")
        assert chiusa["rimborsata_at"] is None and db.ledger("refund") == []

    async def test_ultimo_disponibile_riconciliato_con_l_anno_risolto(self):
        # «Ultimo disponibile» (anno None): il provider scrive l'anno che ha
        # risolto. È la nostra POST, pagata: si riconcilia, niente rimborso.
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [self.voce(31, id="risolta", anno_chiusura="2024")]
        await svc.avanza(db, openapi, dict(riga))
        aggiornata = db.richiesta(riga["id"])
        assert aggiornata["provider_request_id"] == "risolta"
        assert aggiornata["stato"] == "in_lavorazione" and db.ledger("refund") == []

    async def test_lista_paginata_mai_prova_di_assenza(self):
        from app.clients.openapi import OpenapiListaParzialeError

        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = OpenapiListaParzialeError([])
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert db.ledger("refund") == []
        # ma le voci lette servono comunque a riconciliare
        db.richiesta(riga["id"])["ultimo_poll_at"] = _iso(2)
        openapi.lista = OpenapiListaParzialeError([self.voce(31, id="in-pagina")])
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["provider_request_id"] == "in-pagina"

    async def test_id_annotato_nel_registro_prima_della_lista(self):
        # POST accettata e pagata, ma l'update della riga è fallito: l'id del
        # provider è nel registro consumi. Una lista vuota NON deve rimborsare.
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga, api_usage_events=[{
            "user_id": OWNER, "family_parent_id": OWNER, "service": "bilancio-ottico",
            "outcome": "success", "cost_cents": 450, "created_at": _iso(31),
            "request_meta": {"piva": PIVA_MASCHERATA, "richiesta_id": riga["id"],
                             "provider_request_id": "prov-registro"},
        }]), FakeOpenapi()
        openapi.lista = []
        await svc.avanza(db, openapi, dict(riga))
        aggiornata = db.richiesta(riga["id"])
        assert aggiornata["stato"] == "in_lavorazione"
        assert aggiornata["provider_request_id"] == "prov-registro"
        assert "lista" not in openapi.nomi() and db.ledger("refund") == []
        assert len(db.eventi("bilancio-ottico")) == 1  # nessun doppio conteggio

    async def test_post_accettata_e_riga_non_annotata_fino_al_completamento(self, spawned):
        db, openapi = db_base(), FakeOpenapi()
        db.errors[("company_bilancio_richieste", "update")] = _api_error(None, code="08006")
        await svc.richiedi(db, openapi, _active(), USER, None)
        del db.errors[("company_bilancio_richieste", "update")]
        [riga] = db.richieste()
        riga["created_at"] = _iso(31)
        openapi.lista = []  # la lista non la mostra (ancora): nessun rimborso
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["provider_request_id"]) == ("completata", PROVIDER_ID)
        assert db.ledger("refund") == []

    async def test_riconciliata_registra_la_spesa_mancante(self):
        # Riavvio durante la POST: nessun evento. La riconciliazione annota la
        # spesa reale una volta sola.
        riga = nuova_richiesta("esito_ignoto", minuti_fa=10)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = [self.voce()]
        await svc.avanza(db, openapi, dict(riga))
        [evento] = db.eventi("bilancio-ottico")
        assert (evento["outcome"], evento["cost_cents"]) == ("success", 450)
        assert evento["request_meta"]["richiesta_id"] == riga["id"]
        assert evento["request_meta"]["provider_request_id"] == "prov-x"
        assert PIVA not in json.dumps(evento)

    async def test_riconciliata_dopo_timeout_non_conta_due_volte(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=10)
        db, openapi = db_con(riga, api_usage_events=[{
            "user_id": OWNER, "family_parent_id": OWNER, "service": "bilancio-ottico",
            "outcome": "timeout_unknown", "cost_cents": 450, "created_at": _iso(10),
            "request_meta": {"piva": PIVA_MASCHERATA, "richiesta_id": riga["id"]},
        }]), FakeOpenapi()
        openapi.lista = [self.voce()]
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert [e["outcome"] for e in db.eventi("bilancio-ottico")] == ["timeout_unknown"]

    def test_tracce(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31, anno_richiesto=2024)
        voci = [
            self.voce(31, id="ok"), self.voce(31, id="altro-anno", anno_chiusura="2023"),
            self.voce(1, id="dopo"), self.voce(180, id="prima"),
            self.voce(31, id="altra-piva", cf_piva_id="01234567897"),
            self.voce(31, id="illeggibile", timestamp_creation=None),
            {"cf_piva_id": PIVA, "timestamp_creation": 1},  # senza id e vecchia
            {"cf_piva_id": PIVA},  # senza id né data
        ]
        assert svc._tracce(voci, riga) == ["ok", "altro-anno", "dopo", "illeggibile", None]

    def test_finestra_e_filtri(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        voci = [
            self.voce(31, id="ok"), self.voce(31, id="con-prefisso", cf_piva_id=f"IT{PIVA}"),
            self.voce(31, id="altra-piva", cf_piva_id="01234567897"),
            self.voce(31, id="altro-anno", anno_chiusura="2023"),
            self.voce(180, id="troppo-vecchia"), self.voce(1, id="troppo-nuova"),
            {"cf_piva_id": PIVA, "timestamp_creation": 1},  # senza id
        ]
        # «ultimo disponibile»: va bene anche l'anno risolto, dopo quelli identici
        assert [pid for _nata, pid in svc._compatibili(voci, riga)] == [
            "con-prefisso", "ok", "altro-anno",
        ]
        # anno esplicito: prima l'identico, poi le voci senza anno; mai un altro anno
        riga_2024 = {**riga, "anno_richiesto": 2024}
        voci_2024 = [*voci, self.voce(31, id="stesso-anno", anno_chiusura="2024")]
        assert [pid for _nata, pid in svc._compatibili(voci_2024, riga_2024)] == [
            "stesso-anno", "con-prefisso", "ok",
        ]

    async def test_non_trovata_prima_dei_30_minuti_attende(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=20)
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert "fn_bilancio_richiesta_chiudi" not in db.rpc_names()

    async def test_esclude_gli_id_gia_usati(self):
        altra = nuova_richiesta("completata", company_profile_id=ALTRA_COMPANY,
                                family_parent_id=ALTRO_OWNER, provider_request_id="usata")
        riga = nuova_richiesta("esito_ignoto", minuti_fa=31)
        db, openapi = db_con(riga), FakeOpenapi()
        db.tabelle["company_bilancio_richieste"].append(altra)
        openapi.lista = [self.voce(31, id="usata")]  # compatibile per P.IVA, anno e orario
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        # l'unica compatibile è di un'altra richiesta: questa non è mai partita
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "non_inviata")
        assert chiusa["provider_request_id"] is None
        assert altra["provider_request_id"] == "usata"

    async def test_sceglie_la_libera_tra_due(self):
        altra = nuova_richiesta("completata", company_profile_id=ALTRA_COMPANY,
                                family_parent_id=ALTRO_OWNER, provider_request_id="usata")
        riga = nuova_richiesta("esito_ignoto", minuti_fa=10)
        db, openapi = db_con(riga), FakeOpenapi()
        db.tabelle["company_bilancio_richieste"].append(altra)
        # «usata» è la più vicina alla creazione: senza l'esclusione vincerebbe
        openapi.lista = [self.voce(10.5, id="usata"), self.voce(9.5, id="libera")]
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["provider_request_id"] == "libera"

    async def test_lista_irraggiungibile_oltre_24_ore_scaduta_senza_rimborso(self, notifiche):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=24 * 60 + 5)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = OpenapiUpstreamError()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "esito_ignoto_scaduto")
        assert chiusa["rimborsata_at"] is None and db.ledger("refund") == []
        assert db.inventario()["quantita"] == 1
        assert notifiche[0]["tipo"] == "bilancio_ufficiale.non_disponibile"
        assert "restituita" not in notifiche[0]["corpo"]

    async def test_chiusura_senza_rimborso_log_error_e_assistenza(self, caplog):
        import logging

        riga = nuova_richiesta("esito_ignoto", minuti_fa=24 * 60 + 5)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = OpenapiUpstreamError()
        caplog.set_level(logging.ERROR, logger="bandofit.bilancio_ufficiale")
        await svc.avanza(db, openapi, dict(riga))
        errori = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(riga["id"] in m and "esito_ignoto_scaduto" in m for m in errori)
        assert not any(PIVA in m for m in errori)
        out = svc._richiesta_out(db.richiesta(riga["id"]), pdf_disponibile=False)
        assert "assistenza" in out.messaggio
        scaduta = nuova_richiesta("errore", errore_codice="scaduta")
        assert "assistenza" in svc._richiesta_out(scaduta, pdf_disponibile=False).messaggio

    async def test_chiusura_con_rimborso_nessun_log_error(self, caplog):
        import logging

        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Annullata"}
        caplog.set_level(logging.ERROR, logger="bandofit.bilancio_ufficiale")
        await svc.avanza(db, openapi, dict(riga))
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_lista_irraggiungibile_prima_di_24_ore_attende(self):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=60)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.lista = OpenapiTimeoutError()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "esito_ignoto"
        assert db.eventi("bilancio-ottico-stato")[0]["outcome"] == "error"


class TestLavorazione:
    async def test_annullata_rimborso(self, notifiche):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Annullata"}
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert chiusa["stato"] == "annullata" and chiusa["rimborsata_at"]
        assert chiusa["stato_provider"] == "Annullata"
        assert db.inventario()["quantita"] == 2
        [refund] = db.ledger("refund")
        assert refund["note"] == "rimborso automatico: annullata"
        assert notifiche[0]["tipo"] == "bilancio_ufficiale.non_disponibile"

    async def test_in_corso_resta_aperta(self):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert "allegati" not in openapi.nomi()
        [evento] = db.eventi("bilancio-ottico-stato")
        assert (evento["outcome"], evento["cost_cents"]) == ("success", 0)
        assert evento["request_meta"]["operazione"] == "stato"

    async def test_oltre_24_ore_scaduta_senza_rimborso(self, notifiche):
        riga = nuova_richiesta("in_lavorazione", minuti_fa=24 * 60 + 1)
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "scaduta")
        assert chiusa["rimborsata_at"] is None and db.ledger("refund") == []
        assert len(notifiche) == 1

    async def test_guasto_oltre_24_ore_non_chiude_subito(self):
        # Un solo errore transitorio del provider oltre le 24 ore non chiude
        # una richiesta forse pronta e pagata: si chiude dopo 72 ore.
        riga = nuova_richiesta("in_lavorazione", minuti_fa=24 * 60 + 1)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = OpenapiUpstreamError()
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        vecchia = nuova_richiesta("in_lavorazione", minuti_fa=72 * 60 + 1)
        db = db_con(vecchia)
        await svc.avanza(db, openapi, dict(vecchia))
        assert db.richiesta(vecchia["id"])["errore_codice"] == "scaduta"

    async def test_senza_openapi_solo_failsafe(self):
        fresca = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(fresca), FakeOpenapi(enabled=False)
        await svc.avanza(db, openapi, dict(fresca))
        assert db.richiesta(fresca["id"])["stato"] == "in_lavorazione"
        vecchia = nuova_richiesta("in_lavorazione", minuti_fa=24 * 60 + 1)
        db = db_con(vecchia)
        await svc.avanza(db, openapi, dict(vecchia))
        assert db.richiesta(vecchia["id"])["errore_codice"] == "scaduta"
        assert openapi.chiamate == []


class TestCompletamento:
    @staticmethod
    def pronta(**over) -> tuple[FakeDB, FakeOpenapi, dict]:
        riga = nuova_richiesta("in_lavorazione", **over)
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        return db, openapi, riga

    async def test_completata_con_fonte_xbrl_e_documenti(self, notifiche, facet_invalidati):
        db, openapi, riga = self.pronta()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert chiusa["stato"] == "completata" and chiusa["xbrl_esito"] == "ok"
        assert chiusa["anno_bilancio"] == 2024 and chiusa["stato_provider"] == "Dati disponibili"
        assert chiusa["rimborsata_at"] is None and db.inventario()["quantita"] == 1
        # fonte xbrl: 2024 corrente, 2023 comparativo, riferimento alla richiesta
        [(_, params)] = [(n, p) for n, p in db.rpcs if n == "fn_bilanci_registra_fonte"]
        assert params["p_fonte"] == "xbrl" and params["p_company_id"] == COMPANY
        assert params["p_riferimento"] == f"bilancio:{riga['id']}"
        assert params["p_sostituisci"] is False
        assert [(r["anno"], r["ruolo"]) for r in params["p_righe"]] == [
            (2023, "comparativo"), (2024, "corrente"),
        ]
        # documenti: PDF (non il verbale) e XBRL, nomi generati, sha256
        docs = {d["tipo"]: d for d in db.documenti()}
        assert set(docs) == {"pdf", "xbrl"}
        assert docs["pdf"]["nome_file"] == "bilancio-2024.pdf"
        assert bytes.fromhex(docs["pdf"]["contenuto"][2:]) == PDF
        assert docs["pdf"]["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert docs["xbrl"]["nome_file"] == "bilancio-2024.xbrl"
        assert bytes.fromhex(docs["xbrl"]["contenuto"][2:]) == xbrl()
        # un solo upsert idempotente, senza eco del contenuto
        [(_, kwargs)] = [u for u in db.upsert_kwargs if u[0] == "company_bilancio_documenti"]
        assert kwargs["on_conflict"] == "richiesta_id,tipo"
        assert kwargs["ignore_duplicates"] is True
        assert kwargs["returning"].value == "minimal"
        # notifica e facet
        [n] = notifiche
        assert n["tipo"] == "bilancio_ufficiale.pronto"
        assert facet_invalidati == [COMPANY]
        # registro: stato e allegati, a costo 0
        operazioni = [e["request_meta"]["operazione"] for e in db.eventi("bilancio-ottico-stato")]
        assert operazioni == ["stato", "allegati"]
        assert all(e["cost_cents"] == 0 for e in db.eventi())
        # tetto passato al client
        assert ("allegati", PROVIDER_ID, svc.MAX_ALLEGATI_BYTES) in openapi.chiamate

    @pytest.mark.parametrize("stato", ["Dati disponbili", "VISURA EVASA", " dati disponibili "])
    async def test_stati_pronti(self, stato):
        db, openapi, riga = self.pronta()
        openapi.stato = {"stato_richiesta": stato}
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "completata"

    async def test_secondo_completamento_non_esplode(self, notifiche):
        db, openapi, riga = self.pronta()
        # 1° giro: documenti e fonte scritti, chiusura fallita (guasto DB)
        db.rpc_errors["fn_bilancio_richiesta_chiudi"] = _api_error(None, code="08006")
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert len(db.documenti()) == 2
        # 2° giro, dopo il claim: stessi documenti (upsert ignora i duplicati)
        del db.rpc_errors["fn_bilancio_richiesta_chiudi"]
        db.richiesta(riga["id"])["ultimo_poll_at"] = _iso(2)
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "completata"
        assert len(db.documenti()) == 2 and len(notifiche) == 1
        # un completamento tardivo su una riga già chiusa: nessun effetto
        assert await svc._completa(db, openapi, dict(riga), "Dati disponibili") is True
        assert len(db.documenti()) == 2 and len(notifiche) == 1

    async def test_errore_di_parsing_completata_con_pdf(self):
        db, openapi, riga = self.pronta()
        rotto = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><xbrli:xbrl/>'
        openapi.allegati = allegato(zip_bilancio(istanza=rotto))
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["xbrl_esito"]) == ("completata", "non_valido")
        assert chiusa["avvisi"] and chiusa["rimborsata_at"] is None
        assert {d["tipo"] for d in db.documenti()} == {"pdf", "xbrl"}  # XBRL conservato
        assert "fn_bilanci_registra_fonte" not in db.rpc_names()
        assert db.ledger("refund") == []
        out = svc._richiesta_out(chiusa, pdf_disponibile=True)
        assert "PDF" in out.messaggio and out.avvisi_count >= 1

    async def test_cf_non_corrispondente_nessuna_fonte(self):
        db, openapi, riga = self.pronta()
        openapi.allegati = allegato(zip_bilancio(istanza=xbrl("cf_diverso_2024.xbrl")))
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert chiusa["xbrl_esito"] == "cf_non_corrispondente"
        assert "fn_bilanci_registra_fonte" not in db.rpc_names()
        assert {d["tipo"] for d in db.documenti()} == {"pdf", "xbrl"}

    async def test_zip_senza_xbrl_assente(self):
        db, openapi, riga = self.pronta(anno_richiesto=2022)
        openapi.allegati = allegato(zip_senza_xbrl())
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert chiusa["xbrl_esito"] == "assente" and chiusa["anno_bilancio"] is None
        [doc] = db.documenti()
        assert doc["tipo"] == "pdf" and doc["nome_file"] == "bilancio-2022.pdf"

    async def test_base64_non_valido(self):
        db, openapi, riga = self.pronta()
        openapi.allegati = {"nome": "x.zip", "dimensione": 3, "file": "@@@non base64@@@"}
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["xbrl_esito"]) == ("completata", "non_valido")
        assert db.documenti() == []

    async def test_allegati_troppo_grandi(self):
        db, openapi, riga = self.pronta()
        openapi.allegati = OpenapiRispostaTroppoGrandeError()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["xbrl_esito"]) == ("completata", "troppo_grande")
        assert chiusa["avvisi"] == ["allegati_troppo_grandi"]
        assert db.documenti() == []

    async def test_allegati_non_ancora_pronti_attende(self):
        db, openapi, riga = self.pronta()
        openapi.allegati = None
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert "fn_bilancio_richiesta_chiudi" not in db.rpc_names()

    @pytest.mark.parametrize("guasto", ["download", "documenti", "fonte"])
    async def test_errore_infrastrutturale_resta_aperta(self, guasto, notifiche):
        db, openapi, riga = self.pronta()
        if guasto == "download":
            openapi.allegati = OpenapiUpstreamError()
        elif guasto == "documenti":
            db.errors[("company_bilancio_documenti", "upsert")] = _api_error(None, code="57014")
        else:
            db.rpc_errors["fn_bilanci_registra_fonte"] = _api_error("righe_non_valide")
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        assert "fn_bilancio_richiesta_chiudi" not in db.rpc_names()
        assert notifiche == []

    @pytest.mark.parametrize("guasto", ["download", "documenti"])
    async def test_errore_infrastrutturale_oltre_24_ore(self, guasto):
        def prepara(db, openapi):
            if guasto == "download":
                openapi.allegati = OpenapiUpstreamError()
            else:
                db.errors[("company_bilancio_documenti", "upsert")] = _api_error(
                    None, code="57014"
                )

        # oltre 24 ore: un guasto non chiude (il documento può essere pronto)
        db, openapi, riga = self.pronta(minuti_fa=24 * 60 + 1)
        prepara(db, openapi)
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["stato"] == "in_lavorazione"
        # oltre 72 ore: scaduta, senza rimborso
        db, openapi, riga = self.pronta(minuti_fa=72 * 60 + 1)
        prepara(db, openapi)
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["errore_codice"]) == ("errore", "scaduta")
        assert chiusa["rimborsata_at"] is None

    async def test_allegati_non_pronti_oltre_24_ore_scaduta(self):
        # Il provider RISPONDE (422/273) ma il documento non arriva: 24 ore.
        db, openapi, riga = self.pronta(minuti_fa=24 * 60 + 1)
        openapi.allegati = None
        await svc.avanza(db, openapi, dict(riga))
        assert db.richiesta(riga["id"])["errore_codice"] == "scaduta"

    async def test_base64_a_righe_come_mime(self):
        db, openapi, riga = self.pronta()
        testo = base64.b64encode(zip_bilancio()).decode()
        a_righe = "\r\n".join(testo[i:i + 76] for i in range(0, len(testo), 76))
        openapi.allegati = {"nome": "x.zip", "dimensione": 3, "file": a_righe + "\n"}
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["xbrl_esito"]) == ("completata", "ok")
        assert {d["tipo"] for d in db.documenti()} == {"pdf", "xbrl"}

    async def test_pdf_troppo_grande_lo_dice(self, monkeypatch, notifiche):
        monkeypatch.setattr("app.services.xbrl_bilancio.MAX_PDF_BYTES", 100)
        db, openapi, riga = self.pronta()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert (chiusa["stato"], chiusa["xbrl_esito"]) == ("completata", "ok")
        assert "pdf_troppo_grande" in chiusa["avvisi"]
        assert {d["tipo"] for d in db.documenti()} == {"xbrl"}
        out = svc._richiesta_out(chiusa, pdf_disponibile=False)
        assert "PDF non è stato conservato" in out.messaggio
        # i numeri ci sono: la notifica resta «pronto»
        assert notifiche[0]["tipo"] == "bilancio_ufficiale.pronto"

    async def test_senza_pdf_ne_numeri_notifica_non_utilizzabile(self, notifiche):
        db, openapi, riga = self.pronta()
        openapi.allegati = OpenapiRispostaTroppoGrandeError()
        await svc.avanza(db, openapi, dict(riga))
        chiusa = db.richiesta(riga["id"])
        assert chiusa["stato"] == "completata" and db.documenti() == []
        [n] = notifiche
        assert n["tipo"] == "bilancio_ufficiale.non_disponibile"
        assert n["titolo"] == "Bilancio ufficiale non utilizzabile"
        out = svc._richiesta_out(chiusa, pdf_disponibile=False)
        assert "troppo grande" in out.messaggio and "PDF non è stato conservato" in out.messaggio


# ------------------------------------------------------------ poll-on-read

class TestPollOnRead:
    async def test_lista_fa_solo_claim_e_spawn(self, spawned):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        out = await svc.lista(db, openapi, _active())
        # nella GET: nessuna chiamata al provider, nessuna chiusura
        assert openapi.chiamate == []
        assert db.rpc_names() == ["fn_bilancio_richiesta_claim_poll"]
        assert out.richieste[0].stato == "in_lavorazione"
        assert len(spawned) == 1
        # il lavoro vero avviene nel task, col claim già preso
        await esegui(spawned)
        assert db.richiesta(riga["id"])["stato"] == "completata"
        assert db.rpc_names().count("fn_bilancio_richiesta_claim_poll") == 1

    async def test_seconda_lettura_ravvicinata_nessuno_spawn(self, spawned):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        await svc.lista(db, openapi, _active())
        await svc.lista(db, openapi, _active())
        assert len(spawned) == 1

    async def test_righe_chiuse_nessun_claim(self, spawned):
        db = db_con(nuova_richiesta("completata"))
        await svc.lista(db, FakeOpenapi(), _active())
        assert db.rpcs == [] and spawned == []

    async def test_dettaglio_fa_solo_claim_e_spawn(self, spawned):
        riga = nuova_richiesta("esito_ignoto", minuti_fa=40)
        db, openapi = db_con(riga), FakeOpenapi()
        out = await svc.dettaglio(db, openapi, _active(), riga["id"])
        assert out.stato == "esito_ignoto" and openapi.chiamate == []
        assert len(spawned) == 1


class TestLista:
    async def test_forma_e_contenuti(self):
        completata = nuova_richiesta("completata", minuti_fa=60, anno_bilancio=2024,
                                     xbrl_esito="ok", avvisi=["X8 2024: …"],
                                     completata_at=_iso(50))
        rimborsata = nuova_richiesta("non_disponibile", minuti_fa=120,
                                     errore_codice="bilancio_non_disponibile",
                                     rimborsata_at=_iso(119), provider_request_id=None)
        db = db_con(completata, company_financials_fonti=[
            {"company_profile_id": COMPANY, "anno": 2024, "fonte": "xbrl", "ruolo": "corrente"},
            {"company_profile_id": COMPANY, "anno": 2023, "fonte": "xbrl", "ruolo": "comparativo"},
        ])
        db.tabelle["company_bilancio_richieste"].append(rimborsata)
        # una richiesta di un'altra azienda non compare mai
        db.tabelle["company_bilancio_richieste"].append(
            nuova_richiesta("completata", company_profile_id=ALTRA_COMPANY)
        )
        db.tabelle["company_bilancio_documenti"] = [{
            "richiesta_id": completata["id"], "company_profile_id": COMPANY, "tipo": "pdf",
            "nome_file": "bilancio-2024.pdf", "dimensione": len(PDF),
            "sha256": hashlib.sha256(PDF).hexdigest(), "contenuto": "\\x" + PDF.hex(),
        }]
        out = await svc.lista(db, FakeOpenapi(), _active())
        assert out.editable is True and out.richiedibile is True
        assert out.motivo_non_richiedibile is None
        assert out.addon.slug == "bilancio-ufficiale" and str(out.addon.prezzo) == "7.90"
        assert out.quantita == 1 and out.anni_acquisiti == [2024]
        assert [r.id for r in out.richieste] == [completata["id"], rimborsata["id"]]
        primo, secondo = out.richieste
        assert primo.pdf_disponibile is True and primo.avvisi_count == 1
        assert primo.anno_bilancio == 2024 and primo.completata_at
        assert secondo.rimborsata is True and secondo.pdf_disponibile is False
        assert secondo.messaggio == "Il Registro Imprese non ha questo bilancio."
        testo = out.model_dump_json()
        assert PROVIDER_ID not in testo and PIVA not in testo and "contenuto" not in testo
        # la lista non legge mai il contenuto dei documenti
        colonne_doc = [c for tab, c in db.selects if tab == "company_bilancio_documenti"]
        assert colonne_doc and all("contenuto" not in c for c in colonne_doc)

    async def test_massimo_20_piu_recenti(self):
        righe = [nuova_richiesta("errore", minuti_fa=60 * 25 + i) for i in range(25)]
        db = db_base(company_bilancio_richieste=righe)
        out = await svc.lista(db, FakeOpenapi(), _active())
        assert len(out.richieste) == 20
        assert out.richieste[0].id == righe[0]["id"]

    @pytest.mark.parametrize(
        ("prepara", "frammento"),
        [
            (lambda db, a: None, "titolare"),
            (lambda db, a: db.tabelle["company_data"][0]["raw"]["legalForm"]["legalForm"]
             .update(code="SP"), "società di persone"),
            (lambda db, a: db.tabelle["addons"][0].update(is_active=False), "non è al momento"),
            (lambda db, a: db.tabelle.setdefault("company_bilancio_richieste", []).append(
                nuova_richiesta("in_lavorazione", ultimo_poll_at=_iso(0))), "in corso"),
            (lambda db, a: db.tabelle.update(company_data=[]), "importa prima"),
        ],
        ids=["membro", "sp", "addon_inattivo", "aperta", "senza_import"],
    )
    async def test_non_richiedibile(self, prepara, frammento):
        db = db_base()
        prepara(db, None)
        membro = frammento == "titolare"
        out = await svc.lista(db, FakeOpenapi(), _active(editable=not membro))
        assert out.richiedibile is False
        assert frammento in out.motivo_non_richiedibile
        if frammento == "non è al momento":
            assert out.addon is None and out.quantita == 0

    async def test_senza_azienda(self):
        out = await svc.lista(db_base(), FakeOpenapi(), _active(company_id=None))
        assert out.richiedibile is False and out.richieste == [] and out.anni_acquisiti == []
        assert out.addon is not None and out.quantita == 2


# ----------------------------------------------------------------- notifica

class TestNotifica:
    async def test_pronto_senza_dati_di_terzi(self, notifiche):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Visura evasa"}
        await svc.avanza(db, openapi, dict(riga))
        [n] = notifiche
        assert n["user_ids"] == [OWNER]
        assert n["tipo"] == "bilancio_ufficiale.pronto"
        assert n["titolo"] == "Il bilancio ufficiale è pronto"
        assert n["url"] == f"/app/azienda?azienda={COMPANY}#bilanci"
        assert n["dedup_key"] == f"bilancio-ufficiale:{riga['id']}"
        assert n["company_profile_id"] == COMPANY
        testo = f"{n['titolo']} {n['corpo']}"
        assert PIVA not in testo and RAGIONE_SOCIALE not in testo and "ALFA" not in testo

    async def test_non_disponibile_con_rimborso(self, notifiche):
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Annullata"}
        await svc.avanza(db, openapi, dict(riga))
        [n] = notifiche
        assert n["titolo"] == "Bilancio ufficiale non disponibile"
        assert "L'unità ti è stata restituita" in n["corpo"]
        assert PIVA not in n["corpo"] and RAGIONE_SOCIALE not in n["corpo"]


# ------------------------------------------------------------------ download

class TestDownload:
    @staticmethod
    def con_pdf(company_id: str = COMPANY) -> tuple[FakeDB, dict]:
        riga = nuova_richiesta("completata", anno_bilancio=2024, company_profile_id=company_id)
        db = db_con(riga)
        db.tabelle["company_bilancio_documenti"] = [{
            "richiesta_id": riga["id"], "company_profile_id": company_id, "tipo": "pdf",
            "nome_file": "bilancio-2024.pdf", "dimensione": len(PDF),
            "sha256": hashlib.sha256(PDF).hexdigest(), "contenuto": "\\x" + PDF.hex(),
        }]
        return db, riga

    async def test_scarica(self):
        db, riga = self.con_pdf()
        contenuto, nome = await svc.scarica_pdf(db, _active(), riga["id"], user={"id": OWNER})
        assert contenuto == PDF and nome == "bilancio-2024.pdf"
        [audit] = db.tabelle["audit_log"]
        assert audit["action"] == "company.bilancio_ufficiale_scaricato"
        assert PIVA not in json.dumps(audit)

    async def test_fuori_azienda_404(self):
        db, riga = self.con_pdf(company_id=ALTRA_COMPANY)
        with pytest.raises(NotFoundError):
            await svc.scarica_pdf(db, _active(), riga["id"])
        assert "audit_log" not in db.tabelle

    @pytest.mark.parametrize("rid", ["non-un-uuid", str(uuid.uuid4())])
    async def test_id_malformato_o_inesistente_404(self, rid):
        db, _riga = self.con_pdf()
        with pytest.raises(NotFoundError):
            await svc.scarica_pdf(db, _active(), rid)

    async def test_senza_pdf_409(self):
        riga = nuova_richiesta("completata", xbrl_esito="troppo_grande")
        db = db_con(riga)
        with pytest.raises(AppError) as exc:
            await svc.scarica_pdf(db, _active(), riga["id"])
        assert (exc.value.status_code, exc.value.code) == (409, "documento_non_disponibile")

    async def test_contenuto_corrotto_502(self):
        db, riga = self.con_pdf()
        db.documenti()[0]["contenuto"] = "\\x" + (PDF[:-1] + b"X").hex()
        with pytest.raises(UpstreamError):
            await svc.scarica_pdf(db, _active(), riga["id"])


# ------------------------------------------------------------------ follower

class TestFollower:
    async def test_cadenza_e_durata(self, monkeypatch):
        orologio = {"t": 0.0}
        attese: list[float] = []

        async def attendi(secondi):
            attese.append(secondi)
            orologio["t"] += secondi

        monkeypatch.setattr(svc, "_orologio", lambda: orologio["t"])
        monkeypatch.setattr(svc, "_attendi", attendi)
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        avanzate: list = []

        async def avanza(primary, openapi_, r, **_k):
            avanzate.append(r["id"])

        monkeypatch.setattr(svc, "avanza", avanza)
        await svc._segui(db, openapi, riga["id"])
        assert attese == [60] * 20 + [300] * 4  # 20 minuti a 60 s, poi fino a 40 a 300 s
        assert len(avanzate) == 24

    async def test_errore_transitorio_di_lettura_non_ferma_il_follower(self, monkeypatch):
        monkeypatch.setattr(svc, "_attendi", lambda s: asyncio.sleep(0))
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        vera = svc._leggi_riga
        guasti = {"n": 1}

        async def leggi(primary, rid, company_id=None):
            if guasti["n"]:
                guasti["n"] -= 1
                raise _api_error(None, code="08006")
            return await vera(primary, rid, company_id)

        monkeypatch.setattr(svc, "_leggi_riga", leggi)
        await svc._segui(db, openapi, riga["id"])
        assert guasti["n"] == 0
        assert db.richiesta(riga["id"])["stato"] == "completata"

    async def test_si_ferma_a_richiesta_chiusa(self, monkeypatch):
        monkeypatch.setattr(svc, "_attendi", lambda s: asyncio.sleep(0))
        riga = nuova_richiesta("in_lavorazione")
        db, openapi = db_con(riga), FakeOpenapi()
        openapi.stato = {"stato_richiesta": "Dati disponibili"}
        await svc._segui(db, openapi, riga["id"])
        assert db.richiesta(riga["id"])["stato"] == "completata"
        assert openapi.nomi().count("stato") == 1  # dopo la chiusura nessun altro poll
