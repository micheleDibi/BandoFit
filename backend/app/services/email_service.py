"""Email transazionali con provider configurabile da env.

Priorità: SMTP (``SMTP_HOST`` valorizzato, es. casella OVH) → Resend
(``RESEND_API_KEY``) → fallback log-only (sviluppo). Gli invii NON sollevano
mai eccezioni: il canale affidabile per gli inviti è il banner in-app;
l'email è una notifica best-effort.
"""

import html
import logging
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger("bandofit.email")

_RESEND_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 15


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


def _plain_text(parent_display_name: str, denominazione: str, cta_url: str) -> str:
    return (
        f"{parent_display_name} ti ha invitato a unirti alla sua famiglia di account "
        f"su BandoFit come «{denominazione}».\n\n"
        f"Vai all'invito: {cta_url}\n\n"
        "Se non ti aspettavi questo invito puoi ignorare questa email."
    )


def _sanitize_header(value: str) -> str:
    """Le intestazioni email non devono contenere newline (header injection)."""
    return value.replace("\r", " ").replace("\n", " ").strip()


async def _send_via_smtp(
    settings: Settings, to_email: str, subject: str, html_body: str, text_body: str
) -> bool:
    import aiosmtplib

    display_name, from_address = parseaddr(settings.email_from)
    message = EmailMessage()
    if from_address and "@" in from_address:
        user, _, domain = from_address.partition("@")
        message["From"] = Address(display_name or "BandoFit", user, domain)
    else:
        message["From"] = settings.email_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    # Porta 465 = TLS implicito; 587 (o altre) = STARTTLS.
    use_tls = settings.smtp_port == 465
    await aiosmtplib.send(
        message,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        use_tls=use_tls,
        start_tls=not use_tls,
        timeout=_TIMEOUT_SECONDS,
    )
    return True


async def _send_via_resend(
    settings: Settings, to_email: str, subject: str, html_body: str
) -> bool:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.email_from,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            },
        )
    if resp.status_code >= 400:
        logger.error("Resend ha rifiutato l'invio (%s): %s", resp.status_code, resp.text[:300])
        return False
    return True


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
    subject = _sanitize_header(f"{parent_display_name} ti ha invitato su BandoFit")
    html_body = _invitation_html(parent_display_name, denominazione, cta_url)
    text_body = _plain_text(parent_display_name, denominazione, cta_url)

    try:
        if settings.smtp_host:
            return await _send_via_smtp(settings, to_email, subject, html_body, text_body)
        if settings.resend_api_key:
            return await _send_via_resend(settings, to_email, subject, html_body)
    except Exception as exc:
        logger.error("Invio email a %s fallito: %s", to_email, exc)
        return False

    logger.info("[email dev fallback] to=%s subject=%r cta=%s", to_email, subject, cta_url)
    return True
