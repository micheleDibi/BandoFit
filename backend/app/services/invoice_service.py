"""Registro fatture interno: una riga in `invoices` per ogni purchase pagato,
con data_documento = data dell'incasso e il billing_snapshot congelato come
cliente_snapshot. L'emissione fiscale NON passa dalla piattaforma: la fa il
titolare coi suoi strumenti, usando questo registro come lista di lavoro
(tab admin «Fatture», in sola lettura).

La creazione è incondizionata (nessuna config emittente): best-effort al
completamento del pagamento, con recupera_fatture_mancanti come rete di
sicurezza nel passo «fatture» del payment_scheduler.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("bandofit.invoices")


async def crea_fattura_da_purchase(primary, purchase: dict) -> str | None:
    """Crea la riga 'da_emettere' per un purchase pagato. Idempotente
    (UNIQUE su purchase_id). data_documento = paid_at in Europe/Rome. I cambi
    admin (gratuiti) e i purchase a totale 0 non generano fattura.

    Se il billing_snapshot è vuoto (non dovrebbe mai accadere: checkout e
    rinnovo lo congelano) si RIFIUTA di creare la riga — meglio nessuna riga
    e un allarme che una fattura registrata senza cessionario."""
    if purchase.get("kind") == "cambio_admin" or purchase.get("totale_cents", 0) <= 0:
        return None
    if not (purchase.get("billing_snapshot") or {}):
        logger.error(
            "fattura NON creata per purchase %s: billing_snapshot vuoto "
            "(anomalia: verificare il profilo di fatturazione dell'utente)",
            purchase.get("id"),
        )
        return None
    paid_at = purchase.get("paid_at")
    if isinstance(paid_at, str):
        paid_at = datetime.fromisoformat(paid_at)
    giorno = (paid_at or datetime.now(ZoneInfo("UTC"))).astimezone(
        ZoneInfo("Europe/Rome")
    ).date()
    riga = {
        "purchase_id": purchase["id"],
        "anno": giorno.year,
        "data_documento": giorno.isoformat(),
        "imponibile_cents": purchase["imponibile_cents"],
        "iva_cents": purchase["iva_cents"],
        "totale_cents": purchase["totale_cents"],
        "cliente_snapshot": purchase.get("billing_snapshot") or {},
    }
    try:
        resp = await primary.table("invoices").insert(riga).execute()
    except Exception as exc:
        if "invoices_purchase_id_key" in str(exc) or "23505" in str(exc):
            return None  # già creata
        raise
    return str(resp.data[0]["id"])


async def recupera_fatture_mancanti(primary, limite: int = 100) -> int:
    """Rete di sicurezza per il difetto "riga persa": _crea_fattura è
    best-effort al completamento del pagamento; se fallisce (rete/DB), il
    purchase resta pagato senza riga nel registro. Qui si scansionano i pagati
    recenti fatturabili e si (ri)crea la riga mancante — idempotente
    (crea_fattura_da_purchase salta i purchase che già ce l'hanno)."""
    pagati = (
        await primary.table("purchases")
        .select("id,kind,totale_cents,imponibile_cents,iva_cents,billing_snapshot,paid_at")
        .eq("status", "pagato")
        .neq("kind", "cambio_admin")
        .gt("totale_cents", 0)
        .order("paid_at", desc=True)
        .limit(limite)
        .execute()
    )
    if not pagati.data:
        return 0
    ids = [p["id"] for p in pagati.data]
    esistenti = (
        await primary.table("invoices").select("purchase_id")
        .in_("purchase_id", ids).execute()
    )
    con_fattura = {r["purchase_id"] for r in (esistenti.data or [])}
    creati = 0
    for p in pagati.data:
        if p["id"] in con_fattura:
            continue
        try:
            if await crea_fattura_da_purchase(primary, p):
                creati += 1
        except Exception:
            logger.error("recupero fattura fallito per purchase %s", p["id"], exc_info=True)
    return creati
