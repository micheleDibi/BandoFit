"""Anagrafica di fatturazione (migration 0026, tipi 0029).

Regole:
- la validazione di FORMA sta in schemas/billing.py (dipende dal tipo di
  soggetto); qui vivono le regole che richiedono I/O;
- VIES (solo aziende con paese UE ≠ HR, l'Italia inclusa — il venditore è
  croato): NON bloccante — fail-open sul salvataggio (VIES giù o P.IVA non
  valida: si salva comunque), fail-closed sull'aliquota (il pricing concede
  lo 0% reverse charge solo con vies_valid=True persistito);
- il profilo è lo stato CORRENTE editabile: le fatture citeranno sempre lo
  snapshot congelato in purchases.billing_snapshot, mai questa tabella.
"""

import logging
from datetime import UTC, datetime

from app.schemas.billing import (
    PAESI_UE,
    BillingPrefillOut,
    BillingProfileIn,
    BillingProfileOut,
)

logger = logging.getLogger("bandofit.billing")

PAESE_VENDITORE = "HR"

_CAMPI = (
    "tipo_soggetto,denominazione,nome,cognome,partita_iva,codice_fiscale,"
    "paese,indirizzo,comune,provincia,cap,vies_valid,vies_checked_at"
)

# Tolleranza per il deploy: la rimappatura dei valori vecchi la fa la
# migration 0029 (manuale), ma il backend non deve rompersi se gira prima.
_TIPI_LEGACY = {"azienda_it": "azienda", "azienda_ue": "azienda", "privato_it": "privato"}


def _map(row: dict) -> BillingProfileOut:
    dati = {k: row.get(k) for k in _CAMPI.split(",")}
    dati["tipo_soggetto"] = _TIPI_LEGACY.get(dati["tipo_soggetto"], dati["tipo_soggetto"])
    return BillingProfileOut(**dati)


def _vies_applicabile(data: BillingProfileIn) -> bool:
    """Il VIES copre solo la UE; per il paese del venditore l'esito non
    cambierebbe l'aliquota (vendita domestica al 25%): niente chiamata."""
    return (
        data.tipo_soggetto == "azienda"
        and data.paese in PAESI_UE
        and data.paese != PAESE_VENDITORE
    )


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
        .select("ragione_sociale,partita_iva,codice_fiscale,indirizzo,comune,provincia,cap")
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
        tipo_soggetto="azienda" if row.get("partita_iva") else None,
        denominazione=row.get("ragione_sociale"),
        partita_iva=row.get("partita_iva"),
        codice_fiscale=row.get("codice_fiscale"),
        indirizzo=row.get("indirizzo"),
        comune=row.get("comune"),
        provincia=row.get("provincia"),
        cap=row.get("cap"),
    )


async def save_billing_profile(
    primary, openapi, user_id: str, data: BillingProfileIn
) -> BillingProfileOut:
    """VIES prima, poi UN SOLO upsert: mai un profilo a metà (senza esito
    leggibile) visibile a un checkout concorrente. Il salvataggio riesce
    SEMPRE: un guasto del VIES lascia vies_valid a NULL (→ IVA 25%) e
    l'utente ritenta ri-salvando (idempotente)."""
    payload = data.model_dump()
    payload["user_id"] = str(user_id)
    payload["vies_valid"] = None
    payload["vies_checked_at"] = None

    if _vies_applicabile(data):
        if openapi is None or not openapi.enabled:
            logger.warning(
                "VIES saltato per %s: openapi non configurato (vies_valid=NULL)",
                user_id,
            )
        else:
            try:
                valido = await openapi.verifica_piva_ue(data.paese, data.partita_iva)
            except Exception:
                # Fail-open sul salvataggio: QUALSIASI guasto del VIES (timeout,
                # 5xx, envelope inatteso del provider…) non blocca né il
                # salvataggio né gli acquisti. Fail-closed sull'aliquota: senza
                # prova, niente 0%. Except ampio DELIBERATO: la garanzia «il
                # salvataggio riesce sempre» vale anche per bug a monte.
                logger.warning(
                    "VIES non raggiungibile per %s: salvo senza esito",
                    user_id, exc_info=True,
                )
            else:
                # Anche l'esito negativo si salva: la validità VIES non è un
                # requisito per comprare, seleziona solo l'aliquota (25%).
                payload["vies_valid"] = bool(valido)
                payload["vies_checked_at"] = datetime.now(UTC).isoformat()

    await (
        primary.table("billing_profiles")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    saved = await get_billing_profile(primary, user_id)
    assert saved is not None  # appena scritto
    return saved
