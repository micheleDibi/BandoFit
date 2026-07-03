"""Dati aziendali della famiglia: modificabili dal padre, letti dai figli attivi.

I riferimenti ad ATECO, settore e regione puntano alle lookup del DB
SECONDARIO: alla scrittura si risolvono gli id e si denormalizzano le copie
testuali (nessuna FK cross-database)."""

from app.core.errors import BadRequestError
from app.schemas.company import CompanyIn, CompanyOut, CompanyResponse
from app.services import family_service, lookup_service

COMPANY_SELECT = (
    "ragione_sociale,forma_giuridica,partita_iva,codice_fiscale,"
    "ateco_id,ateco_codice,ateco_descrizione,settore_id,settore_nome,"
    "regione_id,regione_nome,anno_fondazione,indirizzo,comune,provincia,cap,"
    "classe_dimensionale,numero_dipendenti,fascia_fatturato,pec,telefono,sito_web"
)


async def _fetch_company(primary, parent_id: str) -> CompanyOut | None:
    resp = (
        await primary.table("company_profiles")
        .select(COMPANY_SELECT)
        .eq("parent_id", str(parent_id))
        .limit(1)
        .execute()
    )
    return CompanyOut(**resp.data[0]) if resp.data else None


async def get_company(primary, requester: dict) -> CompanyResponse:
    """Il padre (o un utente singolo) vede e modifica i propri dati; un figlio
    ATTIVO vede in sola lettura quelli della famiglia."""
    membership = await family_service.get_membership(primary, requester["id"])
    if membership and membership["status"] == "active":
        return CompanyResponse(
            editable=False,
            company=await _fetch_company(primary, membership["parent_id"]),
        )
    if membership:
        # pending/demoted: account (ancora/di nuovo) indipendente, dati propri.
        return CompanyResponse(
            editable=True, company=await _fetch_company(primary, requester["id"])
        )
    return CompanyResponse(
        editable=True, company=await _fetch_company(primary, requester["id"])
    )


def resolve_lookups(data: CompanyIn, lookups) -> dict:
    """Risolve ateco/settore/regione contro le lookup del DB secondario e
    ritorna il payload con le copie denormalizzate. 400 se un id non esiste."""
    payload = data.model_dump(mode="json")
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
    return payload


async def upsert_company(primary, secondary, parent: dict, data: CompanyIn) -> CompanyResponse:
    lookups = await lookup_service.get_lookups(secondary)
    payload = resolve_lookups(data, lookups)
    payload["parent_id"] = parent["id"]

    await primary.table("company_profiles").upsert(
        payload, on_conflict="parent_id"
    ).execute()

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
