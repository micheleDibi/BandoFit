"""Webhook Revolut: firma HMAC (anche multipla), anti-replay, dedup
differenziato per cardinalità, elaborazione in background."""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.routers import webhooks
from app.core.errors import register_exception_handlers
from app.services import payment_service

SECRET = "wsk_test_secret"


class FakeQuery:
    def __init__(self, owner):
        self.owner = owner
        self.payload = None
        self.op = None

    def insert(self, payload):
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, *_a):
        return self

    async def execute(self):
        if self.op == "insert":
            if self.owner.unique_violation:
                raise Exception('violates "webhook_events_dedup_order"')
            self.owner.eventi.append(self.payload)
            return SimpleNamespace(data=[{"id": f"ev-{len(self.owner.eventi)}"}])
        self.owner.aggiornamenti.append(self.payload)
        return SimpleNamespace(data=[])


class FakePrimary:
    def __init__(self):
        self.eventi = []
        self.aggiornamenti = []
        self.unique_violation = False

    def table(self, _name):
        return FakeQuery(self)


def _firma(raw: bytes, ts: str, secret: str = SECRET) -> str:
    return "v1=" + hmac.new(
        secret.encode(), b"v1." + ts.encode() + b"." + raw, hashlib.sha256
    ).hexdigest()


@pytest.fixture
def ambiente(monkeypatch):
    primary = FakePrimary()
    elaborati = []

    async def fake_elabora(_primary, _revolut, order_id):
        elaborati.append(order_id)
        return {"esito": "applicato"}

    monkeypatch.setattr(payment_service, "elabora_ordine", fake_elabora)
    monkeypatch.setattr(
        webhooks, "get_settings",
        lambda: SimpleNamespace(revolut_webhook_secret=SECRET),
    )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(webhooks.router, prefix="/api/v1")
    app.state.primary = primary
    app.state.revolut = SimpleNamespace(enabled=True)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return SimpleNamespace(client=client, primary=primary, elaborati=elaborati)


def _post(ambiente, payload: dict, *, ts: str | None = None, firma: str | None = None):
    raw = json.dumps(payload).encode()
    ts = ts or str(int(time.time() * 1000))
    firma = firma if firma is not None else _firma(raw, ts)
    return ambiente.client.post(
        "/api/v1/webhooks/revolut",
        content=raw,
        headers={"Revolut-Request-Timestamp": ts, "Revolut-Signature": firma,
                 "Content-Type": "application/json"},
    )


EVENTO = {"event": "ORDER_COMPLETED", "order_id": "ord-1"}


class TestFirma:
    async def test_firma_valida_registra_ed_elabora(self, ambiente):
        resp = await _post(ambiente, EVENTO)
        assert resp.status_code == 204
        assert ambiente.primary.eventi[0]["resource_id"] == "ord-1"
        assert ambiente.elaborati == ["ord-1"]  # background eseguito dall'ASGI
        assert ambiente.primary.aggiornamenti[0]["esito"] == "applicato"

    async def test_firma_sbagliata_401_senza_registrare(self, ambiente):
        resp = await _post(ambiente, EVENTO, firma="v1=deadbeef")
        assert resp.status_code == 401
        assert ambiente.primary.eventi == [] and ambiente.elaborati == []

    async def test_firme_multiple_basta_una_valida(self, ambiente):
        raw = json.dumps(EVENTO).encode()
        ts = str(int(time.time() * 1000))
        firma = "v1=nonvalida," + _firma(raw, ts)
        resp = await _post(ambiente, EVENTO, ts=ts, firma=firma)
        assert resp.status_code == 204

    async def test_timestamp_vecchio_respinto(self, ambiente):
        vecchio = str(int(time.time() * 1000) - 6 * 60 * 1000)
        resp = await _post(ambiente, EVENTO, ts=vecchio)
        assert resp.status_code == 401

    async def test_senza_secret_503(self, ambiente, monkeypatch):
        monkeypatch.setattr(
            webhooks, "get_settings",
            lambda: SimpleNamespace(revolut_webhook_secret=""),
        )
        resp = await _post(ambiente, EVENTO)
        assert resp.status_code == 503


class TestDedupERouting:
    async def test_duplicato_order_level_ack_senza_rielaborare(self, ambiente):
        ambiente.primary.unique_violation = True
        resp = await _post(ambiente, EVENTO)
        assert resp.status_code == 204
        assert ambiente.elaborati == []  # già visto: nessuna elaborazione

    async def test_secondo_declino_sempre_elaborato(self, ambiente):
        declino = {"event": "ORDER_PAYMENT_DECLINED", "order_id": "ord-2"}
        assert (await _post(ambiente, declino)).status_code == 204
        assert (await _post(ambiente, declino)).status_code == 204
        assert ambiente.elaborati == ["ord-2", "ord-2"]  # N declini = N eventi veri

    async def test_evento_ignoto_ack_senza_registrare(self, ambiente):
        resp = await _post(ambiente, {"event": "PAYOUT_COMPLETED", "order_id": "x"})
        assert resp.status_code == 204
        assert ambiente.primary.eventi == []

    async def test_elaborazione_fallita_non_perde_l_ack(self, ambiente, monkeypatch):
        async def esplode(_p, _r, _o):
            raise RuntimeError("boom")

        monkeypatch.setattr(payment_service, "elabora_ordine", esplode)
        resp = await _post(ambiente, EVENTO)
        assert resp.status_code == 204  # ack comunque: ritenterà il poll/sync
        assert ambiente.primary.aggiornamenti[0]["esito"] == "errore"
