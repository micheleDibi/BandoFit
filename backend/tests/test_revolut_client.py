"""Client Revolut: header fissi, retry SOLO su connessione, timeout mai
ritentato (esito ignoto = possibile addebito), forma dei metodi salvati."""

from types import SimpleNamespace

import httpx
import pytest

from app.clients.revolut import API_VERSION, RevolutClient, RevolutDeclinedError
from app.core.errors import (
    PaymentsNotConfiguredError,
    RevolutTimeoutError,
    RevolutUpstreamError,
)


def _settings(key="sk_test", env="sandbox"):
    return SimpleNamespace(
        revolut_secret_key=key, revolut_env=env, revolut_timeout_seconds=5.0
    )


class FakeHTTP:
    """Coda di risposte/eccezioni; registra le richieste fatte."""

    def __init__(self, *esiti):
        self.esiti = list(esiti)
        self.richieste = []

    async def request(self, method, url, headers=None, json=None):
        self.richieste.append({"method": method, "url": url,
                               "headers": headers, "json": json})
        esito = self.esiti.pop(0)
        if isinstance(esito, Exception):
            raise esito
        status, body = esito
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    async def aclose(self):
        pass


def _client(*esiti, key="sk_test", env="sandbox"):
    http = FakeHTTP(*esiti)
    return RevolutClient(_settings(key, env), http=http), http


class TestBase:
    async def test_disabilitato_senza_chiave(self):
        client, _ = _client(key="")
        assert not client.enabled
        with pytest.raises(PaymentsNotConfiguredError):
            await client.get_order("x")

    async def test_header_e_host(self):
        client, http = _client((200, {"id": "o1"}))
        await client.get_order("o1")
        req = http.richieste[0]
        assert req["url"].startswith("https://sandbox-merchant.revolut.com/")
        assert req["headers"]["Revolut-Api-Version"] == API_VERSION
        assert req["headers"]["Authorization"] == "Bearer sk_test"

    async def test_produzione_usa_l_host_giusto(self):
        client, http = _client((200, {"id": "o1"}), env="production")
        await client.get_order("o1")
        assert http.richieste[0]["url"].startswith("https://merchant.revolut.com/")


class TestRetry:
    async def test_connessione_ritenta_una_volta(self):
        client, http = _client(httpx.ConnectError("boom"), (200, {"id": "o1"}))
        assert (await client.get_order("o1"))["id"] == "o1"
        assert len(http.richieste) == 2

    async def test_timeout_non_ritenta_mai(self):
        client, http = _client(httpx.ReadTimeout("slow"))
        with pytest.raises(RevolutTimeoutError):
            await client.pay_with_saved_method("o1", "pm1")
        assert len(http.richieste) == 1  # un charge con esito ignoto NON si ripete

    async def test_5xx_non_ritenta(self):
        client, http = _client((502, {}))
        with pytest.raises(RevolutUpstreamError):
            await client.create_order(
                amount_cents=100, currency="EUR", description="x"
            )
        assert len(http.richieste) == 1


class TestEsiti:
    async def test_4xx_porta_il_codice_del_provider(self):
        client, _ = _client((422, {"code": "multiple_payments_for_advanced_order",
                                   "message": "già pagato"}))
        with pytest.raises(RevolutDeclinedError) as exc:
            await client.pay_with_saved_method("o1", "pm1")
        assert exc.value.code == "multiple_payments_for_advanced_order"

    async def test_metodi_salvati_forma_dict(self):
        # Forma verificata in sandbox: {"payment_methods": [...]}
        client, _ = _client((200, {"payment_methods": [{"id": "pm1", "type": "card"}]}))
        metodi = await client.get_payment_methods("c1")
        assert metodi == [{"id": "pm1", "type": "card"}]

    async def test_mit_payload(self):
        client, http = _client((200, {"state": "authorisation_passed"}))
        await client.pay_with_saved_method("o1", "pm1")
        assert http.richieste[0]["json"] == {
            "saved_payment_method": {"type": "card", "id": "pm1",
                                     "initiator": "merchant"}
        }

    async def test_create_order_payload(self):
        client, http = _client((201, {"id": "o1", "token": "t"}))
        await client.create_order(
            amount_cents=36478, currency="EUR", description="Abbonamento Pro",
            customer_id="c1", metadata={"purchase_id": "p1"},
            expire_pending_after="PT1H",
        )
        body = http.richieste[0]["json"]
        assert body["amount"] == 36478 and body["customer"] == {"id": "c1"}
        assert body["expire_pending_after"] == "PT1H"
