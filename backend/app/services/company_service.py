"""Dati aziendali della famiglia: modificabili dal padre, letti dai figli attivi.

I riferimenti ad ATECO, settore, regione e beneficiari puntano alle lookup del
DB SECONDARIO: alla scrittura si risolvono gli id e si denormalizzano le copie
testuali (nessuna FK cross-database)."""

from postgrest.exceptions import APIError

from app.core.errors import BadRequestError, ForbiddenError
from app.schemas.company import (
    CompaniesOut,
    CompanyCreate,
    CompanyIn,
    CompanyOut,
    CompanyResponse,
    CompanySummary,
)
from app.services import family_service, lookup_service

COMPANY_SELECT = (
    "ragione_sociale,forma_giuridica,partita_iva,codice_fiscale,"
    "ateco_id,ateco_codice,ateco_descrizione,settore_id,settore_nome,"
    "regione_id,regione_nome,beneficiari,anno_fondazione,indirizzo,comune,"
    "provincia,cap,classe_dimensionale,numero_dipendenti,fascia_fatturato,"
    "pec,telefono,sito_web"
)


def _map_company(row: dict | None) -> CompanyOut | None:
    if not row:
        return None
    row = dict(row)
    # In colonna c'è solo [{id, nome}]: gli id per il form si ricavano da lì.
    row["beneficiari_ids"] = [b["id"] for b in (row.get("beneficiari") or [])]
    return CompanyOut(**row)


async def _fetch_company(primary, parent_id: str) -> CompanyOut | None:
    # Owner-scoped (consulenze, bootstrap import): con il multi-azienda un owner
    # può avere più righe, quindi si sceglie deterministicamente la più vecchia
    # NON cancellata (per i non-Advisor è l'unica).
    resp = (
        await primary.table("company_profiles")
        .select(COMPANY_SELECT)
        .eq("parent_id", str(parent_id))
        .is_("deleted_at", "null")
        .order("created_at")
        .limit(1)
        .execute()
    )
    return _map_company(resp.data[0] if resp.data else None)


async def _fetch_company_by_id(primary, company_id: str) -> CompanyOut | None:
    resp = (
        await primary.table("company_profiles")
        .select(COMPANY_SELECT)
        .eq("id", str(company_id))
        .limit(1)
        .execute()
    )
    return _map_company(resp.data[0] if resp.data else None)


async def get_company_for_owner(primary, owner_id: str) -> CompanyOut | None:
    """Dati aziendali del titolare indicato, SENZA regole di visibilità: il
    chiamante ha già autorizzato l'accesso (nel flusso consulenze: progettista
    assegnato, con audit)."""
    return await _fetch_company(primary, owner_id)


async def get_company(primary, active) -> CompanyResponse:
    """Dati dell'azienda attiva. `editable` viene dal resolver (un figlio
    attivo legge in sola lettura i dati della famiglia); `company_id` è None
    se il titolare non ha ancora alcuna azienda."""
    company = (
        await _fetch_company_by_id(primary, active.company_id)
        if active.company_id
        else None
    )
    return CompanyResponse(editable=active.editable, company=company)


async def company_response_for_owner(
    primary, owner_id: str, *, editable: bool = True
) -> CompanyResponse:
    """CompanyResponse dei dati del titolare indicato, per i flussi
    owner-scoped (es. l'import da P.IVA), senza passare dal resolver
    dell'azienda attiva."""
    return CompanyResponse(editable=editable, company=await _fetch_company(primary, owner_id))


async def company_response_for_id(
    primary, company_id: str, *, editable: bool = True
) -> CompanyResponse:
    """CompanyResponse di una specifica azienda per `id` (multi-azienda):
    l'import ritorna i dati dell'azienda appena scritta, non della più vecchia."""
    return CompanyResponse(editable=editable, company=await _fetch_company_by_id(primary, company_id))


def resolve_lookups(data: CompanyIn, lookups) -> dict:
    """Risolve ateco/settore/regione/beneficiari contro le lookup del DB
    secondario e ritorna il payload con le copie denormalizzate. 400 se un id
    non esiste."""
    payload = data.model_dump(mode="json")
    # `beneficiari_ids` è solo l'input: in colonna va [{id, nome}].
    beneficiari_ids = payload.pop("beneficiari_ids", [])
    payload.update(
        {
            "ateco_codice": None,
            "ateco_descrizione": None,
            "settore_nome": None,
            "regione_nome": None,
        }
    )
    if data.ateco_id is not None:
        match = next((a for a in lookups.codici_ateco if a.id == data.ateco_id), None)
        if match is None:
            raise BadRequestError("Codice ATECO non valido")
        payload["ateco_codice"] = match.codice
        payload["ateco_descrizione"] = match.descrizione
    if data.settore_id is not None:
        match = next((s for s in lookups.settori if s.id == data.settore_id), None)
        if match is None:
            raise BadRequestError("Settore non valido")
        payload["settore_nome"] = match.nome
    if data.regione_id is not None:
        match = next((r for r in lookups.regioni if r.id == data.regione_id), None)
        if match is None:
            raise BadRequestError("Regione non valida")
        payload["regione_nome"] = match.nome

    beneficiari = []
    for beneficiario_id in beneficiari_ids:
        match = next((b for b in lookups.beneficiari if b.id == beneficiario_id), None)
        if match is None:
            raise BadRequestError("Categoria di beneficiario non valida")
        beneficiari.append({"id": match.id, "nome": match.nome})
    payload["beneficiari"] = beneficiari
    return payload


async def upsert_company(primary, secondary, active, data: CompanyIn) -> CompanyResponse:
    # Stessa regola di editabilità di get_company: solo un figlio ATTIVO è
    # bloccato (eredita i dati della famiglia), e il resolver lo codifica in
    # `active.editable`; pending e retrocessi scrivono i propri.
    if not active.editable:
        raise ForbiddenError("I dati aziendali li gestisce il titolare dell'azienda")

    lookups = await lookup_service.get_lookups(secondary)
    payload = resolve_lookups(data, lookups)

    # Scrittura per `id` (multi-azienda): si aggiorna l'azienda ATTIVA. Se
    # l'owner non ne ha ancora nessuna (company_id None) è il bootstrap della
    # prima azienda → insert.
    company_id = active.company_id
    if company_id is not None:
        await primary.table("company_profiles").update(payload).eq(
            "id", str(company_id)
        ).execute()
    else:
        payload["parent_id"] = active.owner_id
        resp = await primary.table("company_profiles").insert(payload).execute()
        company_id = str(resp.data[0]["id"]) if resp.data else None

    # Il punteggio di compatibilità legge questi campi da una cache TTL (per id azienda).
    from app.services.compatibility import invalidate_company_facets  # import locale: evita cicli

    invalidate_company_facets(company_id)

    await primary.table("audit_log").insert(
        {
            "actor_id": active.owner_id,
            "action": "company.updated",
            "target_user_id": active.owner_id,
            "family_parent_id": active.owner_id,
            "payload": {
                "company_profile_id": company_id,
                "ragione_sociale": data.ragione_sociale,
            },
        }
    ).execute()

    company = await _fetch_company_by_id(primary, company_id) if company_id else None
    return CompanyResponse(editable=True, company=company)


# ---------------------------------------------------------- gestione aziende

async def effective_max_aziende(primary, owner_id: str) -> int:
    """Limite effettivo di aziende (override utente > piano > 1), dalla RPC."""
    resp = await primary.rpc(
        "fn_effective_max_aziende", {"p_user_id": str(owner_id)}
    ).execute()
    try:
        return int(resp.data)
    except (TypeError, ValueError):
        return 1


async def list_companies(primary, owner_id: str) -> CompaniesOut:
    """Aziende VIVE gestite dall'owner (le cancellate/archiviate sono escluse),
    con il limite effettivo e quante ne sono in uso. La prima (più vecchia) è
    quella che il resolver userebbe di default: la marchiamo `attiva`."""
    resp = (
        await primary.table("company_profiles")
        .select("id,ragione_sociale,partita_iva,created_at")
        .eq("parent_id", str(owner_id))
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .order("created_at")
        .execute()
    )
    rows = resp.data or []
    aziende = [
        CompanySummary(
            id=row["id"],
            ragione_sociale=row["ragione_sociale"],
            partita_iva=row["partita_iva"],
            created_at=row["created_at"],
            attiva=(index == 0),
        )
        for index, row in enumerate(rows)
    ]
    return CompaniesOut(
        aziende=aziende,
        max_aziende=await effective_max_aziende(primary, owner_id),
        usate=len(rows),
    )


async def create_company(primary, owner_id: str, data: CompanyCreate) -> CompanySummary:
    """Crea un'azienda via `fn_create_company` (limite race-free, P.IVA e
    ragione sociale obbligatorie). Gli errori della RPC diventano AppError."""
    try:
        resp = await primary.rpc(
            "fn_create_company",
            {
                "p_owner_id": str(owner_id),
                "p_ragione_sociale": data.ragione_sociale,
                "p_partita_iva": data.partita_iva,
            },
        ).execute()
    except APIError as exc:
        family_service.raise_from_rpc(exc)

    company_id = resp.data
    created = (
        await primary.table("company_profiles")
        .select("id,ragione_sociale,partita_iva,created_at")
        .eq("id", str(company_id))
        .limit(1)
        .execute()
    )
    row = created.data[0]
    return CompanySummary(
        id=row["id"],
        ragione_sociale=row["ragione_sociale"],
        partita_iva=row["partita_iva"],
        created_at=row["created_at"],
    )


async def soft_delete_company(primary, owner_id: str, company_id: str) -> None:
    """Soft-delete di un'azienda dell'owner via `fn_soft_delete_company`. I dati
    collegati restano (cascade solo su purge fisica); il resolver la rifiuta."""
    from app.services.compatibility import invalidate_company_facets  # import locale

    try:
        await primary.rpc(
            "fn_soft_delete_company",
            {"p_owner_id": str(owner_id), "p_company_id": str(company_id)},
        ).execute()
    except APIError as exc:
        family_service.raise_from_rpc(exc)
    invalidate_company_facets(company_id)
