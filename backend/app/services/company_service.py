"""Dati aziendali della famiglia: modificabili dal padre, letti dai figli attivi.

I riferimenti ad ATECO, settore, regione e beneficiari puntano alle lookup del
DB SECONDARIO: alla scrittura si risolvono gli id e si denormalizzano le copie
testuali (nessuna FK cross-database)."""

from app.core.errors import BadRequestError, ForbiddenError
from app.schemas.company import CompanyIn, CompanyOut, CompanyResponse
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
    resp = (
        await primary.table("company_profiles")
        .select(COMPANY_SELECT)
        .eq("parent_id", str(parent_id))
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


async def upsert_company(primary, secondary, parent: dict, data: CompanyIn) -> CompanyResponse:
    # Stessa regola di editabilità di get_company: solo un figlio ATTIVO è
    # bloccato (eredita i dati della famiglia); pending e retrocessi scrivono
    # i propri.
    membership = await family_service.get_membership(primary, parent["id"])
    if membership and membership["status"] == "active":
        raise ForbiddenError("I dati aziendali li gestisce il titolare dell'azienda")

    lookups = await lookup_service.get_lookups(secondary)
    payload = resolve_lookups(data, lookups)
    payload["parent_id"] = parent["id"]

    await primary.table("company_profiles").upsert(
        payload, on_conflict="parent_id"
    ).execute()

    # Il punteggio di compatibilità legge questi campi da una cache TTL.
    from app.services.compatibility import invalidate_company_facets  # import locale: evita cicli

    invalidate_company_facets(parent["id"])

    await primary.table("audit_log").insert(
        {
            "actor_id": parent["id"],
            "action": "company.updated",
            "target_user_id": parent["id"],
            "family_parent_id": parent["id"],
            "payload": {"ragione_sociale": data.ragione_sociale},
        }
    ).execute()

    return CompanyResponse(editable=True, company=await _fetch_company(primary, parent["id"]))
