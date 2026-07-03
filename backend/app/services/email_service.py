"""Email transazionali via Resend.

Senza RESEND_API_KEY (sviluppo) le email vengono solo loggate. Gli invii NON
sollevano mai eccezioni: il canale affidabile per gli inviti è il banner
in-app; l'email è una notifica best-effort.
"""

import html
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger("bandofit.email")

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10


def _invitation_html(parent_display_name: str, denominazione: str, cta_url: str) -> str:
    parent = html.escape(parent_display_name)
    name = html.escape(denominazione)
    return f"""\
<div style="font-family:Inter,Arial,sans-serif;max-width:520px;margin:0 auto;color:#1e293b">
  <div style="background:#1E5EFF;border-radius:12px 12px 0 0;padding:20px 28px">
    <span style="color:#fff;font-size:20px;font-weight:700">BandoFit</span>
  </div>
  <div style="border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px;padding:28px">
    <h1 style="font-size:18px;margin:0 0 12px">Sei stato invitato su BandoFit</h1>
    <p style="font-size:15px;line-height:1.6;margin:0 0 8px">
      <strong>{parent}</strong> ti ha invitato a unirti alla sua famiglia di account
      come <strong>{name}</strong>.
    </p>
    <p style="font-size:15px;line-height:1.6;margin:0 0 20px">
      Entrando nella famiglia condividerai l'abbonamento e i dati aziendali del titolare.
    </p>
    <a href="{cta_url}"
       style="display:inline-block;background:#1E5EFF;color:#fff;text-decoration:none;
              font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px">
      Vai all'invito
    </a>
    <p style="font-size:13px;color:#64748b;margin:20px 0 0">
      Se non ti aspettavi questo invito puoi ignorare questa email o rifiutarlo
      dalla piattaforma.
    </p>
  </div>
</div>"""


async def send_family_invitation_email(
    to_email: str,
    parent_display_name: str,
    denominazione: str,
    cta_url: str | None = None,
) -> bool:
    """Invia (best-effort) l'email di invito famiglia.

    Ritorna True se l'invio è andato a buon fine, False altrimenti.
    Default: link al profilo (utenti esistenti, che accettano in-app);
    ``cta_url`` esplicito per i reinvii con link d'invito rigenerato.
    """
    settings = get_settings()
    cta_url = cta_url or f"{settings.frontend_url.rstrip('/')}/app/profilo"
    subject = f"{parent_display_name} ti ha invitato su BandoFit"

    if not settings.resend_api_key:
        logger.info(
            "[email dev fallback] to=%s subject=%r cta=%s", to_email, subject, cta_url
        )
        return True

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json={
                    "from": settings.email_from,
                    "to": [to_email],
                    "subject": subject,
                    "html": _invitation_html(parent_display_name, denominazione, cta_url),
                },
            )
        if resp.status_code >= 400:
            logger.error(
                "Invio email invito fallito (%s): %s", resp.status_code, resp.text[:300]
            )
            return False
        return True
    except httpx.HTTPError as exc:
        logger.error("Invio email invito fallito: %s", exc)
        return False
