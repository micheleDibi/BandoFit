"""Bandi salvati (preferiti) per utente.

I preferiti sono RIFERIMENTI al catalogo del DB secondario, non copie:
si salva bando_id + uno snapshot minimo (slug/titolo/scadenza/stato) che fa
da fallback di visualizzazione se il bando sparisce dal catalogo. La lista
pagina sul PRIMARIO (ordine di salvataggio) e idrata i dati vivi dal
secondario con una sola query per pagina (≤ 50 id: dentro il timeout di 3s
del ruolo anon).
"""

import logging

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, NotFoundError
from app.schemas.bando import BandoListItem
from app.schemas.common import Page
from app.schemas.saved_bando import SavedBandoItem, SavedIdsOut
from app.services import company_scope
from app.services.bandi_service import LIST_SELECT, map_list_item

logger = logging.getLogger("bandofit.saved_bandi")

MAX_SAVED = 200

SAVED_SELECT = "id,bando_id,bando_slug,bando_titolo,data_scadenza,stato_bando,created_at"


def _snapshot_titolo(bando: dict) -> str:
    return bando.get("titolo_breve") or bando.get("titolo") or bando["slug"]


def fallback_item(row: dict) -> BandoListItem:
    """Card di ripiego dallo snapshot, per i bandi spariti dal catalogo."""
    return BandoListItem(
        id=row["bando_id"],
        slug=row["bando_slug"],
        titolo=row["bando_titolo"],
        titolo_breve=row["bando_titolo"],
        descrizione_breve=None,
        stato_bando=row.get("stato_bando"),
        livello=None,
        data_pubblicazione=None,
        data_apertura=None,
        data_scadenza=row.get("data_scadenza"),
        importo_totale_eur=None,
        importo_max_per_progetto_eur=None,
        ente_erogatore=None,
        tipologia=None,
        modalita_erogazione=None,
        regioni=[],
    )


async def _fetch_live_bando(secondary, slug: str) -> dict:
    resp = (
        await secondary.table("bando")
        .select(LIST_SELECT)
        .eq("slug", slug)
        .eq("stato_processing", "completed")
        .limit(1)
        .execute()
    )
    if not resp.data:
        raise NotFoundError("Bando non trovato")
    return resp.data[0]


async def _existing_row(primary, user_id: str, active, bando_id: int) -> dict | None:
    resp = (
        await company_scope.filter_read(
            primary.table("saved_bandi")
            .select(SAVED_SELECT)
            .eq("user_id", str(user_id))
            .eq("bando_id", bando_id),
            active,
        )
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


async def _in_calendar_ids(primary, user_id: str, active, bando_ids: list[int]) -> set[int]:
    """Bandi (tra quelli dati) che hanno già l'evento scadenza in calendario
    dell'azienda attiva."""
    if not bando_ids:
        return set()
    resp = (
        await company_scope.filter_read(
            primary.table("calendar_events")
            .select("bando_id")
            .eq("user_id", str(user_id))
            .eq("tipo", "bando")
            .in_("bando_id", bando_ids),
            active,
        ).execute()
    )
    return {row["bando_id"] for row in (resp.data or [])}


def _to_item(
    saved_row: dict, live: dict | None, in_calendar: set[int]
) -> SavedBandoItem:
    return SavedBandoItem(
        bando=map_list_item(live) if live else fallback_item(saved_row),
        disponibile=live is not None,
        in_calendario=saved_row["bando_id"] in in_calendar,
        salvato_il=saved_row["created_at"],
    )


async def save_bando(primary, secondary, user_id: str, active, slug: str) -> SavedBandoItem:
    """Salva un bando tra i preferiti dell'azienda attiva. Idempotente: se è
    già salvato ritorna la riga esistente senza errori (è un toggle)."""
    bando = await _fetch_live_bando(secondary, slug)
    bando_id = bando["id"]

    existing = await _existing_row(primary, user_id, active, bando_id)
    if existing is None:
        count_resp = (
            await company_scope.filter_read(
                primary.table("saved_bandi")
                .select("id", count="exact")
                .eq("user_id", str(user_id)),
                active,
            )
            .limit(1)
            .execute()
        )
        if (count_resp.count or 0) >= MAX_SAVED:
            raise BadRequestError(
                f"Hai raggiunto il limite di {MAX_SAVED} bandi salvati: "
                "rimuovine qualcuno per salvarne altri"
            )
        row = {
            "user_id": str(user_id),
            "company_profile_id": company_scope.scope_value(active),
            "bando_id": bando_id,
            "bando_slug": bando["slug"],
            "bando_titolo": _snapshot_titolo(bando),
            "data_scadenza": bando.get("data_scadenza"),
            "stato_bando": bando.get("stato_bando"),
        }
        try:
            insert = await primary.table("saved_bandi").insert(row).execute()
            existing = insert.data[0]
        except APIError as exc:
            if exc.code != "23505":
                raise
            # Corsa tra due salvataggi: l'indice unico ha deciso, rileggiamo.
            existing = await _existing_row(primary, user_id, active, bando_id)
            if existing is None:  # pragma: no cover — solo per robustezza
                raise

    in_calendar = await _in_calendar_ids(primary, user_id, active, [bando_id])
    return _to_item(existing, bando, in_calendar)


async def remove_bando(primary, user_id: str, active, bando_id: int) -> None:
    """Rimuove il bando dai preferiti dell'azienda attiva. Idempotente (toggle):
    nessun errore se non era salvato. L'eventuale evento in calendario resta."""
    await (
        company_scope.filter_read(
            primary.table("saved_bandi")
            .delete()
            .eq("user_id", str(user_id))
            .eq("bando_id", bando_id),
            active,
        ).execute()
    )


async def list_saved(
    primary, secondary, user_id: str, active, page: int, page_size: int
) -> Page[SavedBandoItem]:
    """Elenco paginato dei preferiti dell'azienda attiva, dal salvataggio più
    recente. I dati vivi arrivano dal catalogo; i bandi spariti restano
    visibili dallo snapshot con disponibile=False."""
    offset = (page - 1) * page_size
    resp = (
        await company_scope.filter_read(
            primary.table("saved_bandi")
            .select(SAVED_SELECT, count="exact")
            .eq("user_id", str(user_id)),
            active,
        )
        .order("created_at", desc=True)
        .range(offset, offset + page_size - 1)
        .execute()
    )
    rows = resp.data or []
    total = resp.count or 0
    if not rows:
        return Page.build([], total, page, page_size)

    ids = [row["bando_id"] for row in rows]
    live_resp = (
        await secondary.table("bando")
        .select(LIST_SELECT)
        .in_("id", ids)
        .eq("stato_processing", "completed")
        .not_.is_("slug", "null")
        .execute()
    )
    live_by_id = {row["id"]: row for row in (live_resp.data or [])}
    in_calendar = await _in_calendar_ids(primary, user_id, active, ids)

    items = [_to_item(row, live_by_id.get(row["bando_id"]), in_calendar) for row in rows]
    return Page.build(items, total, page, page_size)


async def saved_ids(primary, user_id: str, active) -> SavedIdsOut:
    resp = (
        await company_scope.filter_read(
            primary.table("saved_bandi")
            .select("bando_id")
            .eq("user_id", str(user_id)),
            active,
        ).execute()
    )
    return SavedIdsOut(bando_ids=sorted(row["bando_id"] for row in (resp.data or [])))
