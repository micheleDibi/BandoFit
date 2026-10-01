"""«Per te», riepilogo per il menu e impostazioni email dei partenariati
(WP6, docs/partenariati.md M5, M6, Q6, Q25).

Router NUOVO del modulo: flag sul router e sulla route class (404 anche
senza token e con un corpo malformato, T1). «Per te» è visibile anche senza
opt-in (solo scoperta: `opt_in` false e CTA); le impostazioni email sono
dell'utente (digest settimanale ed email di evento), con la stessa fonte di
verità del link di disiscrizione.
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    ActiveCompanyDep,
    CurrentUser,
    PrimaryClient,
    RottaPartenariati,
    SecondaryClient,
    require_partenariati_attivo,
)
from app.services import partenariato_notifiche, partner_call_service
from app.services.partenariato_accesso import PerTeOut, RiepilogoOut
from app.services.partenariato_notifiche import (
    EmailSettingsPartenariatiIn,
    EmailSettingsPartenariatiOut,
)

router = APIRouter(
    tags=["partenariati"],
    dependencies=[Depends(require_partenariati_attivo)],
    route_class=RottaPartenariati,
)


@router.get("/partenariati/per-te", response_model=PerTeOut)
async def per_te(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
    page: int = Query(1, ge=1, le=100_000),
    page_size: int = Query(20, ge=1, le=50),
) -> PerTeOut:
    """Le call che l'azienda attiva completerebbe, con il proprio match.
    409 `azienda_mancante` senza un'azienda."""
    return await partner_call_service.per_te(
        primary, secondary, active, user, page=page, page_size=page_size
    )


@router.get("/partenariati/riepilogo", response_model=RiepilogoOut)
async def riepilogo(
    user: CurrentUser,
    active: ActiveCompanyDep,
    primary: PrimaryClient,
    secondary: SecondaryClient,
) -> RiepilogoOut:
    """Contatori per il badge del menu «Partenariati»."""
    return await partner_call_service.riepilogo_partenariati(primary, secondary, active, user)


@router.get("/me/partenariati/email-settings", response_model=EmailSettingsPartenariatiOut)
async def get_email_settings(user: CurrentUser, primary: PrimaryClient
                             ) -> EmailSettingsPartenariatiOut:
    return await partenariato_notifiche.settings_per_utente(primary, user["id"])


@router.put("/me/partenariati/email-settings", response_model=EmailSettingsPartenariatiOut)
async def put_email_settings(
    data: EmailSettingsPartenariatiIn, user: CurrentUser, primary: PrimaryClient
) -> EmailSettingsPartenariatiOut:
    """Le proprie email dei partenariati (anche per i membri: sono le loro
    caselle, non scritture sull'azienda)."""
    return await partenariato_notifiche.aggiorna_settings(primary, user["id"], data)
