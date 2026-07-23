"""DTO del modulo entitlement (migration 0030): lo snapshot unico delle quote.

`base` viene dal piano attivo del titolare (per companies include l'eventuale
override admin), `extra` dalle unità di addon allocativi possedute, `effettivo`
dalla formula unica SQL (`fn_entitlement_detail`, dormienza inclusa). Il
frontend legge questi numeri, non li ricalcola mai.
"""

from pydantic import BaseModel


class ResourceEntitlement(BaseModel):
    base: int
    extra: int
    effettivo: int
    usato: int
    residuo: int


class AiChecksEntitlement(ResourceEntitlement):
    # Finestra del ciclo di abbonamento attivo (ISO date); None senza ciclo.
    periodo_inizio: str | None = None
    periodo_fine: str | None = None


class EntitlementsOut(BaseModel):
    """Risposta di GET /me/entitlements. Per un collegato ATTIVO lo snapshot è
    quello del titolare (pool condiviso) e ``editable`` è False."""

    editable: bool
    seats: ResourceEntitlement
    companies: ResourceEntitlement
    ai_checks: AiChecksEntitlement
