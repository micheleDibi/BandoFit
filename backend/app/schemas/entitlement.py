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
    # Solo per un MEMBRO attivo (WP6): il suo budget (None nel payload di un
    # titolare; per il membro, null = illimitato) e i suoi consumi nel ciclo.
    budget_membro: int | None = None
    usati_membro: int | None = None


class PartenariatiLimite(BaseModel):
    """Un limite del modulo partenariati (migration 0036/0037). A differenza
    delle risorse sopra: ``limite`` None = illimitato (e allora ``residuo`` è
    None), 0 = funzione non inclusa nel piano."""

    limite: int | None
    usate: int
    residuo: int | None


class PartenariatiLimiteMese(PartenariatiLimite):
    # Mese solare Europe/Rome (ISO date); None se il DB non lo fornisce.
    periodo_inizio: str | None = None
    periodo_fine: str | None = None


class PartenariatiEntitlement(BaseModel):
    """Snapshot di `fn_partenariati_snapshot` (pool del titolare su tutte le
    sue aziende): call attive (pubblicate o sospese), candidature del mese e
    bozze AI dei documenti del mese (0042, WP10)."""

    call_attive: PartenariatiLimite
    candidature_mese: PartenariatiLimiteMese
    # Facoltativo: uno snapshot anteriore alla 0042 non ha la chiave e non
    # deve invalidare gli altri due limiti. None = dato non disponibile (non
    # «illimitato»): la UI non mostra il contatore, il limite lo applica
    # comunque la RPC di prenotazione della bozza.
    bozze_mese: PartenariatiLimiteMese | None = None


class EntitlementsOut(BaseModel):
    """Risposta di GET /me/entitlements. Per un collegato ATTIVO lo snapshot è
    quello del titolare (pool condiviso) e ``editable`` è False."""

    editable: bool
    seats: ResourceEntitlement
    companies: ResourceEntitlement
    ai_checks: AiChecksEntitlement
    # Solo con il modulo partenariati acceso e lo snapshot leggibile: None a
    # flag spento o se la RPC fallisce (la risposta non si rompe mai per lui).
    partenariati: PartenariatiEntitlement | None = None
