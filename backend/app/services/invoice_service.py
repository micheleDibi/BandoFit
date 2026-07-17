"""Fatturazione elettronica SDI (Fase 4): dalla creazione della riga fattura
all'invio via openapi, con il protocollo anti doppia-trasmissione.

Protocollo di invio (dalla review adversariale):
  1. la riga fattura nasce 'da_emettere' alla conferma del pagamento, con
     data_documento = data dell'incasso (mai la data dell'invio);
  2. il worker assegna il numero e passa a 'in_invio' PRIMA della POST — il
     numero resta congelato sulla riga per sempre;
  3. su esito ignoto (timeout dopo l'invio) si cerca la fattura per riferimento
     esterno PRIMA di ritrasmettere: una fattura può essere già a SDI;
  4. lo scarto si ritrasmette con lo STESSO numero e la STESSA data.

Emettere le fatture è del payment_scheduler (passo 6): qui stanno i mattoni.
"""

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.clients.openapi import OpenapiTimeoutError
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.services import fattura_builder, pdf_service
from app.services.company_pdf_service import PdfResult
from app.services.pdf_service import fields_block, section, text_block

logger = logging.getLogger("bandofit.invoices")

_INVOICE_SELECT = (
    "id,purchase_id,anno,serie,numero,data_documento,stato,provider_id,"
    "imponibile_cents,iva_cents,totale_cents,cliente_snapshot,tentativi"
)


def _emittente_configurato() -> bool:
    s = get_settings()
    return bool(s.fattura_denominazione and s.fattura_partita_iva)


async def crea_fattura_da_purchase(primary, purchase: dict) -> str | None:
    """Crea la riga 'da_emettere' per un purchase pagato. Idempotente
    (UNIQUE su purchase_id). data_documento = paid_at in Europe/Rome. I cambi
    admin (gratuiti) e i purchase a totale 0 non generano fattura.

    Se il billing_snapshot è vuoto (non dovrebbe mai accadere: checkout e
    rinnovo lo congelano) si RIFIUTA di creare la fattura — meglio nessuna
    fattura e un allarme che un documento senza cessionario trasmesso a SDI."""
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
        "serie": get_settings().fattura_serie,
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


async def _assegna_numero(primary, inv: dict) -> int:
    """Assegna il numero progressivo (se non già assegnato) e passa a
    'in_invio'. Il numero è congelato: un reinvio riusa la stessa riga."""
    if inv.get("numero"):
        return inv["numero"]
    resp = await primary.rpc(
        "fn_next_invoice_number",
        {"p_anno": inv["anno"], "p_serie": inv.get("serie") or ""},
    ).execute()
    numero = resp.data
    await (
        primary.table("invoices")
        .update({"numero": numero, "stato": "in_invio"})
        .eq("id", inv["id"])
        .execute()
    )
    return numero


async def emetti(primary, openapi, inv: dict) -> str:
    """Emette una singola fattura ('da_emettere', 'errore' o 'scartata'):
    numero → invio → esito. Ritorna lo stato finale della riga."""
    if not _emittente_configurato() or openapi is None or not openapi.enabled:
        return inv["stato"]
    numero = await _assegna_numero(primary, {**inv})
    cliente = inv.get("cliente_snapshot") or {}
    purchase_resp = (
        await primary.table("purchases")
        .select("id,descrizione,imponibile_cents,iva_cents,totale_cents,"
                "iva_aliquota,natura_iva,valuta")
        .eq("id", inv["purchase_id"]).limit(1).execute()
    )
    if not purchase_resp.data:
        return inv["stato"]
    documento = fattura_builder.costruisci_fattura(
        settings=get_settings(), purchase=purchase_resp.data[0], cliente=cliente,
        numero=numero, serie=inv.get("serie") or "", data_documento=inv["data_documento"],
    )

    # Esito ignoto: prima di ritrasmettere, cerca per riferimento esterno.
    if inv.get("tentativi", 0) > 0 and not inv.get("provider_id"):
        try:
            trovata = await openapi.cerca_fattura(str(inv["purchase_id"]))
        except Exception:
            trovata = None
        if trovata and trovata.get("id"):
            await _aggiorna_esito(primary, inv["id"], trovata, provider_id=trovata["id"])
            return "inviata"

    try:
        data = await openapi.invia_fattura(documento)
    except OpenapiTimeoutError:
        # Esito IGNOTO: non si ritrasmette qui, si segna errore e si
        # riconcilierà al prossimo giro con cerca_fattura.
        await (
            primary.table("invoices")
            .update({"stato": "errore", "tentativi": inv.get("tentativi", 0) + 1,
                     "ultimo_esito": {"errore": "timeout_invio"}})
            .eq("id", inv["id"]).execute()
        )
        return "errore"
    except Exception as exc:
        logger.error("fattura %s: invio fallito (%s)", inv["id"], exc)
        await (
            primary.table("invoices")
            .update({"stato": "errore", "tentativi": inv.get("tentativi", 0) + 1,
                     "ultimo_esito": {"errore": str(exc)[:200]}})
            .eq("id", inv["id"]).execute()
        )
        return "errore"

    await _aggiorna_esito(primary, inv["id"], data, provider_id=data.get("id"))
    return "inviata"


async def _aggiorna_esito(primary, invoice_id: str, data: dict, provider_id: str | None) -> None:
    stato = _stato_da_provider(data)
    await (
        primary.table("invoices")
        .update({
            "stato": stato, "provider_id": provider_id,
            "sdi_identificativo": data.get("id_sdi") or data.get("sdi_id"),
            "ultimo_esito": data, "emessa_at": "now()",
        })
        .eq("id", invoice_id).execute()
    )


def _stato_da_provider(data: dict) -> str:
    """Mappa lo stato openapi/SDI sui nostri stati."""
    grezzo = str(data.get("stato") or data.get("status") or "").lower()
    mappa = {
        "delivered": "consegnata", "consegnata": "consegnata",
        "not_delivered": "non_consegnata", "mancata_consegna": "non_consegnata",
        "error": "scartata", "scartata": "scartata", "rejected": "scartata",
        "sent": "inviata", "booked": "inviata", "inviata": "inviata",
    }
    return mappa.get(grezzo, "inviata")


async def recupera_fatture_mancanti(primary, limite: int = 100) -> int:
    """Rete di sicurezza per il difetto "fattura persa": _crea_fattura è
    best-effort al completamento del pagamento; se fallisce (rete/DB), il
    purchase resta pagato senza riga fattura. Qui si scansionano i pagati
    recenti fatturabili e si (ri)crea la fattura mancante — idempotente
    (crea_fattura_da_purchase salta i purchase che già ce l'hanno)."""
    pagati = (
        await primary.table("purchases")
        .select("id,kind,totale_cents,billing_snapshot,paid_at")
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


async def _recupera_in_invio_stantie(primary, minuti: int = 10) -> int:
    """Fatture bloccate in 'in_invio' (processo ucciso tra il set 'in_invio' e
    l'esito): dopo `minuti` si riportano a 'errore' con tentativi+1, così
    emetti() le riprende e — avendo tentativi>0 e provider_id NULL — passa dal
    ramo cerca_fattura che riconcilia senza ritrasmettere due volte a SDI."""
    from datetime import timedelta

    soglia = (datetime.now(ZoneInfo("UTC")) - timedelta(minutes=minuti)).isoformat()
    resp = (
        await primary.table("invoices")
        .select("id,tentativi")
        .eq("stato", "in_invio")
        .lte("updated_at", soglia)
        .execute()
    )
    for inv in resp.data or []:
        await (
            primary.table("invoices")
            .update({"stato": "errore", "tentativi": (inv.get("tentativi") or 0) + 1})
            .eq("id", inv["id"]).execute()
        )
    return len(resp.data or [])


async def emetti_pendenti(primary, openapi, limite: int = 50) -> dict:
    """Passo 6 dello scheduler: recupera le fatture mancanti dei pagati e le
    'in_invio' stantie, poi emette le 'da_emettere', riconcilia gli 'errore',
    ritrasmette gli 'scartata'. Segnala le fatture vecchie di >8 giorni ancora
    non consegnate (deadline fiscale 12)."""
    esiti: dict[str, int] = {"emesse": 0, "errori": 0}
    esiti["recuperate"] = await recupera_fatture_mancanti(primary)
    esiti["in_invio_riprese"] = await _recupera_in_invio_stantie(primary)
    resp = (
        await primary.table("invoices")
        .select(_INVOICE_SELECT)
        .in_("stato", ["da_emettere", "errore", "scartata"])
        .order("created_at")
        .limit(limite)
        .execute()
    )
    for inv in resp.data or []:
        try:
            stato = await emetti(primary, openapi, inv)
            esiti["emesse" if stato not in ("errore",) else "errori"] += 1
        except Exception:
            logger.error("fattura %s: emissione fallita", inv["id"], exc_info=True)
            esiti["errori"] += 1
    await _allarme_scadute(primary)
    return esiti


# ------------------------------------------------------------ documento PDF


def _euro_cents(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f} €".replace(".", ",")


def build_documento_doc(inv: dict, purchase: dict) -> "pdf_service.PdfDoc":
    """PDF di CORTESIA: l'originale fiscale è l'XML trasmesso a SDI. Consuma
    solo campi noti (nessun leak di dati grezzi)."""
    cliente = inv.get("cliente_snapshot") or {}
    intestatario = (
        cliente.get("denominazione")
        or " ".join(filter(None, [cliente.get("nome"), cliente.get("cognome")]))
        or "—"
    )
    numero = inv.get("numero")
    riferimento = (
        f"{inv.get('serie') or ''}{numero}/{inv['anno']}" if numero else "in emissione"
    )
    natura = purchase.get("natura_iva")
    sezioni = [
        section("Documento", [
            fields_block([
                ("Numero", riferimento),
                ("Data", inv["data_documento"]),
                ("Tipo", "Fattura elettronica (TD01)"),
            ]),
        ]),
        section("Cliente", [
            fields_block([
                ("Intestatario", intestatario),
                ("Partita IVA", cliente.get("partita_iva")),
                ("Codice fiscale", cliente.get("codice_fiscale")),
                ("Indirizzo", " ".join(filter(None, [
                    cliente.get("indirizzo"), cliente.get("cap"),
                    cliente.get("comune"), cliente.get("provincia"),
                ]))),
            ]),
        ]),
        section("Importi", [
            fields_block([
                ("Descrizione", purchase.get("descrizione")),
                ("Imponibile", _euro_cents(inv["imponibile_cents"])),
                ("IVA", "Reverse charge art. 7-ter (N2.1)" if natura
                 else f"{_euro_cents(inv['iva_cents'])} ({purchase.get('iva_aliquota')}%)"),
                ("Totale", _euro_cents(inv["totale_cents"])),
            ]),
        ]),
        section("", [
            text_block(
                "Copia di cortesia. Il documento fiscale valido è la fattura "
                "elettronica trasmessa al Sistema di Interscambio (SDI); la trovi "
                "anche nel tuo cassetto fiscale."
            ),
        ]),
    ]
    return pdf_service.PdfDoc(
        title="Fattura",
        subtitle=get_settings().fattura_denominazione or "BandoFit",
        sections=[s for s in sezioni if s is not None],
    )


async def documento_pdf(primary, user_id: str, purchase_id: str) -> PdfResult:
    """PDF di cortesia dell'acquisto (per lo storico). 404 se il purchase non
    è dell'utente o non è pagato."""
    p = (
        await primary.table("purchases")
        .select("id,user_id,status,descrizione,imponibile_cents,iva_cents,"
                "totale_cents,iva_aliquota,natura_iva,billing_snapshot,paid_at,kind")
        .eq("id", purchase_id).eq("user_id", str(user_id)).limit(1).execute()
    )
    if not p.data:
        raise NotFoundError("Acquisto non trovato")
    purchase = p.data[0]
    if purchase["status"] != "pagato":
        raise NotFoundError("Nessun documento disponibile per questo acquisto")

    inv_resp = (
        await primary.table("invoices").select(_INVOICE_SELECT)
        .eq("purchase_id", purchase_id).limit(1).execute()
    )
    if inv_resp.data:
        inv = inv_resp.data[0]
    else:
        # Fattura non ancora creata: ricevuta con i dati del purchase.
        paid = purchase.get("paid_at")
        giorno = (
            datetime.fromisoformat(paid).astimezone(ZoneInfo("Europe/Rome")).date().isoformat()
            if isinstance(paid, str) else date.today().isoformat()
        )
        inv = {
            "numero": None, "serie": "", "anno": date.today().year,
            "data_documento": giorno, "imponibile_cents": purchase["imponibile_cents"],
            "iva_cents": purchase["iva_cents"], "totale_cents": purchase["totale_cents"],
            "cliente_snapshot": purchase.get("billing_snapshot") or {},
        }
    doc = build_documento_doc(inv, purchase)
    content = await asyncio.to_thread(pdf_service.render, doc)
    return PdfResult(content=content, filename=f"fattura-{purchase_id[:8]}.pdf")


async def _allarme_scadute(primary) -> None:
    """Fatture non ancora consegnate con incasso più vecchio di 8 giorni: la
    scadenza fiscale (12 giorni dall'incasso) è vicina."""
    limite = (date.today().toordinal() - 8)
    soglia = date.fromordinal(limite).isoformat()
    resp = (
        await primary.table("invoices")
        .select("id,data_documento,stato")
        .in_("stato", ["da_emettere", "in_invio", "errore", "scartata"])
        .lte("data_documento", soglia)
        .execute()
    )
    for inv in resp.data or []:
        logger.error(
            "FATTURA IN RITARDO: %s (data %s, stato %s) — deadline fiscale 12 giorni",
            inv["id"], inv["data_documento"], inv["stato"],
        )
