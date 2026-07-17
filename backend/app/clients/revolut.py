"""Client per la Revolut Merchant API (pagamenti, migration 0026).

Meccanica (verificata in sandbox nella Fase 0 del modulo pagamenti, 2026-07-17):

- **Auth**: ``Authorization: Bearer <secret key>`` + header ``Revolut-Api-Version``.
  La versione è FISSATA a quella con cui il flusso è stato validato dal vivo:
  le versioni successive ridefiniscono gli schemi degli ordini e si adottano
  solo ri-validando in sandbox, mai per deriva.
- **Ordini**: ``POST /api/orders`` (amount in CENTESIMI) → ``id`` permanente +
  ``token`` (per il widget) + ``checkout_url``. Un ordine declinato torna
  ``pending`` e riaccetta tentativi; un ordine pagato RIFIUTA altri pagamenti
  (422 ``multiple_payments_for_advanced_order`` — guardia anti doppio addebito).
- **Metodi salvati**: la lista risponde ``{"payment_methods": [...]}`` (dict,
  non lista nuda). L'addebito off-session è ``POST /api/orders/{id}/payments``
  con ``initiator: "merchant"``.
- **Idempotenza**: la Merchant API NON ha Idempotency-Key su create/pay: la
  difesa sta a DB nostro (UNIQUE su revolut_order_id, un solo purchase
  in_attesa, cicli di rinnovo univoci) e nella riconciliazione via GET.

Regole di spesa: qui girano SOLDI VERI. Retry solo quando la richiesta non è
mai partita (errori di connessione); su timeout/5xx l'esito è ignoto e NON si
ritenta MAI in automatico — si riconcilia rileggendo l'ordine.
"""

import logging

import httpx

from app.core.config import Settings
from app.core.errors import (
    PaymentsNotConfiguredError,
    RevolutTimeoutError,
    RevolutUpstreamError,
)

logger = logging.getLogger("bandofit.revolut")

_HOSTS = {
    "production": "https://merchant.revolut.com",
    "sandbox": "https://sandbox-merchant.revolut.com",
}

# Validata dal vivo in Fase 0 (ordini, metodi salvati, MIT, webhook, cancel).
API_VERSION = "2024-09-01"


class RevolutDeclinedError(Exception):
    """Il provider ha rifiutato l'operazione con un esito CERTO (4xx con
    body). Porta il codice del provider: il chiamante decide cosa farne
    (es. declino su addebito → dunning, non errore di sistema)."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"{status} {code}: {message}")


class RevolutClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._key = settings.revolut_secret_key
        env = "production" if settings.revolut_env == "production" else "sandbox"
        self.env = env
        self._base = _HOSTS[env]
        self._http = http or httpx.AsyncClient(timeout=settings.revolut_timeout_seconds)

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    @property
    def sandbox(self) -> bool:
        return self.env != "production"

    async def aclose(self) -> None:
        await self._http.aclose()

    # --------------------------------------------------------------- requests

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> dict:
        if not self.enabled:
            raise PaymentsNotConfiguredError()
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Revolut-Api-Version": API_VERSION,
        }
        url = f"{self._base}{path}"
        try:
            resp = await self._http.request(method, url, headers=headers, json=json)
        except httpx.ConnectError:
            # Mai partita, non addebitata: un solo retry.
            logger.warning("revolut: errore di connessione, ritento una volta (%s)", path)
            try:
                resp = await self._http.request(method, url, headers=headers, json=json)
            except httpx.HTTPError as exc:
                raise RevolutUpstreamError() from exc
        except httpx.TimeoutException as exc:
            # Esito ignoto (possibile addebito): il chiamante riconcilia via GET.
            logger.error("revolut: timeout su %s %s", method, path)
            raise RevolutTimeoutError() from exc
        except httpx.HTTPError as exc:
            raise RevolutUpstreamError() from exc

        if resp.status_code >= 500:
            logger.error("revolut: HTTP %s su %s %s", resp.status_code, method, path)
            raise RevolutUpstreamError()
        try:
            body = resp.json() if resp.content else {}
        except ValueError as exc:
            logger.error("revolut: risposta non JSON da %s (HTTP %s)", path, resp.status_code)
            raise RevolutUpstreamError() from exc
        if resp.status_code >= 400:
            code = str(body.get("code") or body.get("errorId") or resp.status_code)
            message = str(body.get("message") or "")
            logger.warning("revolut: %s %s → %s %s", method, path, resp.status_code, code)
            raise RevolutDeclinedError(resp.status_code, code, message)
        return body

    # --------------------------------------------------------------- products

    async def create_customer(self, email: str, full_name: str | None = None) -> dict:
        payload: dict = {"email": email}
        if full_name:
            payload["full_name"] = full_name
        return await self._request("POST", "/api/customers", json=payload)

    async def create_order(
        self,
        *,
        amount_cents: int,
        currency: str,
        description: str,
        customer_id: str | None = None,
        metadata: dict | None = None,
        expire_pending_after: str | None = None,
    ) -> dict:
        payload: dict = {
            "amount": amount_cents,
            "currency": currency,
            "description": description,
        }
        if customer_id:
            payload["customer"] = {"id": customer_id}
        if metadata:
            payload["metadata"] = metadata
        if expire_pending_after:
            payload["expire_pending_after"] = expire_pending_after
        return await self._request("POST", "/api/orders", json=payload)

    async def get_order(self, order_id: str) -> dict:
        return await self._request("GET", f"/api/orders/{order_id}")

    async def cancel_order(self, order_id: str) -> dict:
        """Ammesso solo su pending (senza pagamenti riusciti) e authorised."""
        return await self._request("POST", f"/api/orders/{order_id}/cancel")

    async def pay_with_saved_method(
        self, order_id: str, method_id: str, method_type: str = "card"
    ) -> dict:
        """Addebito merchant-initiated (off-session): il metodo deve essere
        stato salvato con saved_for=merchant. Il declino arriva come
        RevolutDeclinedError o come stato declined sull'ordine."""
        return await self._request(
            "POST",
            f"/api/orders/{order_id}/payments",
            json={
                "saved_payment_method": {
                    "type": method_type,
                    "id": method_id,
                    "initiator": "merchant",
                }
            },
        )

    async def get_payment_methods(self, customer_id: str) -> list[dict]:
        body = await self._request("GET", f"/api/customers/{customer_id}/payment-methods")
        # Forma verificata in sandbox: dict con chiave payment_methods.
        if isinstance(body, list):
            return body
        return list(body.get("payment_methods") or [])
