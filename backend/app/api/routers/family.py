from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, ParentUser, PrimaryClient
from app.schemas.family import (
    FamilyOut,
    InvitationOut,
    InviteMemberIn,
    InviteMemberOut,
    MemberUpdateIn,
)
from app.schemas.user import MeOut
from app.services import family_service, user_service

router = APIRouter(prefix="/me", tags=["family"])


@router.get("/family", response_model=FamilyOut)
async def get_family(parent: ParentUser, primary: PrimaryClient) -> FamilyOut:
    """Vista del titolare: membri correnti, posti usati e limite del piano."""
    return await family_service.get_family_overview(primary, parent["id"])


@router.post("/family/members", response_model=InviteMemberOut, status_code=201)
async def invite_member(
    data: InviteMemberIn, parent: ParentUser, primary: PrimaryClient
) -> InviteMemberOut:
    return await family_service.invite_member(
        primary, parent, str(data.email), data.denominazione.strip(),
        company_id=str(data.company_profile_id) if data.company_profile_id else None,
        ai_check_budget=data.ai_check_budget,
    )


@router.patch("/family/members/{membership_id}", response_model=FamilyOut)
async def update_member(
    membership_id: UUID, data: MemberUpdateIn, parent: ParentUser, primary: PrimaryClient
) -> FamilyOut:
    """Modifica di un membro (0031): azienda di appartenenza, aziende visibili
    (⊇ appartenenza), budget AI-check (null esplicito = illimitato). Applica
    solo i campi presenti nel body."""
    return await family_service.update_member(
        primary, parent, str(membership_id), data.model_dump(exclude_unset=True)
    )


@router.post("/family/members/{membership_id}/resend", response_model=InviteMemberOut)
async def resend_invite(
    membership_id: UUID, parent: ParentUser, primary: PrimaryClient
) -> InviteMemberOut:
    return await family_service.resend_invite(primary, parent, str(membership_id))


@router.post("/family/members/{membership_id}/reactivate", response_model=FamilyOut)
async def reactivate_member(
    membership_id: UUID, parent: ParentUser, primary: PrimaryClient
) -> FamilyOut:
    return await family_service.reactivate_member(primary, parent, str(membership_id))


@router.delete("/family/members/{membership_id}", response_model=FamilyOut)
async def remove_member(
    membership_id: UUID, parent: ParentUser, primary: PrimaryClient
) -> FamilyOut:
    return await family_service.remove_member(primary, parent, str(membership_id))


@router.get("/invitations", response_model=list[InvitationOut])
async def list_invitations(user: CurrentUser, primary: PrimaryClient) -> list[InvitationOut]:
    """Inviti in attesa ricevuti dall'utente corrente (per il banner in-app)."""
    return await family_service.list_my_invitations(primary, user["id"])


@router.post("/invitations/{membership_id}/accept", response_model=MeOut)
async def accept_invitation(
    membership_id: UUID, user: CurrentUser, primary: PrimaryClient
) -> MeOut:
    await family_service.accept_invitation(primary, user["id"], str(membership_id))
    return await user_service.get_me(primary, user["id"])


@router.post("/invitations/{membership_id}/decline", response_model=list[InvitationOut])
async def decline_invitation(
    membership_id: UUID, user: CurrentUser, primary: PrimaryClient
) -> list[InvitationOut]:
    await family_service.decline_invitation(primary, user["id"], str(membership_id))
    return await family_service.list_my_invitations(primary, user["id"])
