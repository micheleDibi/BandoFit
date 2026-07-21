"""Anagrafica di fatturazione (migration 0026).

Regole:
- la validazione di FORMA sta in schemas/billing.py (dipende dal tipo di
  soggetto); qui vivono le regole che richiedono I/O;
- azienda_ue: il VIES è OBBLIGATORIO e bloccante — senza prova di validità
  niente reverse charge, quindi niente salvataggio (l'esito è persistito come
  prova: vies_valid + vies_checked_at);
- il profilo è lo stato CORRENTE editabile: le fatture citeranno sempre lo
  snapshot congelato in purchases.billing_snapshot, mai questa tabella.
"""

import logging
from datetime import UTC, datetime

from app.core.errors import BadRequestError, OpenapiNotConfiguredError
from app.schemas.billing import BillingPrefillOut, BillingProfileIn, BillingProfileOut

logger = logging.getLogger("bandofit.billing")

_CAMPI = (
    "tipo_soggetto,denominazione,nome,cognome,partita_iva,codice_fiscale,"
    "paese,indirizzo,comune,provincia,cap,codice_destinatario,pec,"
    "vies_valid,vies_checked_at"
)


def _map(row: dict) -> BillingProfileOut:
    return BillingProfileOut(**{k: row.get(k) for k in _CAMPI.split(",")})


async def get_billing_profile(primary, user_id: str) -> BillingProfileOut | None:
    resp = (
        await primary.table("billing_profiles")
        .select(_CAMPI)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    return _map(resp.data[0]) if resp.data else None


async def get_prefill(primary, user_id: str) -> BillingPrefillOut:
    """Proposta di precompilazione dai dati aziendali (l'azienda più vecchia
    non cancellata, come company_service._fetch_company). Solo una proposta:
    non viene mai persistita finché l'utente non salva."""
    resp = (
        await primary.table("company_profiles")
        .select("ragione_sociale,partita_iva,codice_fiscale,indirizzo,comune,provincia,cap,pec")
        .eq("parent_id", str(user_id))
        .is_("deleted_at", "null")
        .order("created_at")
        .limit(1)
        .execute()
    )
    if not resp.data:
        return BillingPrefillOut()
    row = resp.data[0]
    return BillingPrefillOut(
        tipo_soggetto="azienda_it" if row.get("partita_iva") else None,
        denominazione=row.get("ragione_sociale"),
        partita_iva=row.get("partita_iva"),
        codice_fiscale=row.get("codice_fiscale"),
        indirizzo=row.get("indirizzo"),
        comune=row.get("comune"),
        provincia=row.get("provincia"),
        cap=row.get("cap"),
        pec=row.get("pec"),
    )


async def save_billing_profile(
    primary, openapi, user_id: str, data: BillingProfileIn
) -> BillingProfileOut:
    payload = data.model_dump()
    payload["user_id"] = str(user_id)
    payload["vies_valid"] = None
    payload["vies_checked_at"] = None
    # B2C e UE non hanno un recapito SDI proprio: si NORMALIZZA a '0000000'
    # — un pop lascerebbe a DB il codice del tipo precedente quando l'utente
    # cambia tipo di soggetto.
    if payload.get("codice_destinatario") is None:
        payload["codice_destinatario"] = "0000000"

    if data.tipo_soggetto == "azienda_ue":
        if openapi is None or not openapi.enabled:
            raise OpenapiNotConfiguredError()
        valido = await openapi.verifica_piva_ue(data.paese, data.partita_iva)
        if not valido:
            raise BadRequestError(
                "La partita IVA non risulta valida nel VIES: senza questa "
                "verifica non possiamo applicare il reverse charge. Controlla "
                "il numero o contattaci."
            )
        payload["vies_valid"] = True
        payload["vies_checked_at"] = datetime.now(UTC).isoformat()

    await (
        primary.table("billing_profiles")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    saved = await get_billing_profile(primary, user_id)
    assert saved is not None  # appena scritto
    return saved
