"""Alert nuovi-bandi: disiscrizione PUBBLICA (RFC 8058) e impostazioni in-app.

La disiscrizione via email e il toggle nelle Preferenze scrivono la STESSA
riga di bando_alert_settings: un'unica fonte di verità.

Anti-enumeration: il POST risponde sempre allo stesso modo, token valido,
ignoto o malformato. Il GET mostra solo una pagina con un bottone di
conferma e NON muta nulla: gli scanner antispam aziendali pre-aprono i link
GET delle email, e un GET mutante disiscriverebbe utenti a loro insaputa.
"""

import html
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.api.deps import CurrentUser, PrimaryClient
from app.core.config import get_settings
from app.schemas.alerts import AlertSettingsIn, AlertSettingsOut
from app.services import bando_alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])
me_router = APIRouter(prefix="/me/alert-settings", tags=["alerts"])


def _token_valido(token: str) -> bool:
    try:
        UUID(token)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _vuole_html(request: Request) -> bool:
    """Un form del browser accetta text/html; i client one-click (RFC 8058)
    no: a loro basta un 204."""
    return "text/html" in request.headers.get("accept", "")


def _pagina(titolo: str, corpo: str, azione_html: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titolo)} — BandoFit</title></head>
<body style="font-family:Inter,Arial,sans-serif;background:#f8fafc;margin:0;padding:40px 16px">
<div style="max-width:480px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:28px">
<div style="color:#1E5EFF;font-size:20px;font-weight:700;margin-bottom:12px">BandoFit</div>
<h1 style="font-size:18px;color:#1e293b;margin:0 0 10px">{html.escape(titolo)}</h1>
<p style="font-size:15px;color:#475569;line-height:1.6;margin:0 0 16px">{html.escape(corpo)}</p>
{azione_html}
</div></body></html>"""
    )


@router.get("/unsubscribe")
async def unsubscribe_page(token: str = Query(default="")) -> HTMLResponse:
    """Pagina di conferma, NON mutante (vedi docstring del modulo)."""
    action = f"/api/v1/alerts/unsubscribe?token={html.escape(token, quote=True)}"
    bottone = (
        f'<form method="post" action="{action}" style="margin:0">'
        '<button type="submit" style="background:#1E5EFF;color:#fff;border:none;'
        'font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px;cursor:pointer">'
        "Disattiva gli avvisi</button></form>"
    )
    return _pagina(
        "Avvisi sui nuovi bandi",
        "Vuoi smettere di ricevere via email gli avvisi sui nuovi bandi "
        "compatibili con la tua azienda?",
        bottone,
    )


@router.post("/unsubscribe")
async def unsubscribe(
    request: Request, primary: PrimaryClient, token: str = Query(default="")
) -> Response:
    """Disiscrizione a un clic (RFC 8058): idempotente, stessa risposta con
    token valido, ignoto o malformato."""
    if _token_valido(token):
        await bando_alert_service.unsubscribe_by_token(primary, token)
    if _vuole_html(request):
        preferenze = f"{get_settings().frontend_url.rstrip('/')}/app/preferenze"
        link = (
            f'<a href="{html.escape(preferenze, quote=True)}" '
            'style="font-size:14px;color:#1E5EFF">Vai alle Preferenze</a>'
        )
        return _pagina(
            "Avvisi disattivati",
            "Non riceverai più email sui nuovi bandi. Puoi riattivarle in "
            "qualsiasi momento dalle Preferenze della piattaforma.",
            link,
        )
    return Response(status_code=204)


@me_router.get("", response_model=AlertSettingsOut)
async def get_alert_settings(user: CurrentUser, primary: PrimaryClient) -> AlertSettingsOut:
    return AlertSettingsOut(
        **await bando_alert_service.alert_settings_for_user(primary, user)
    )


@me_router.put("", response_model=AlertSettingsOut)
async def put_alert_settings(
    data: AlertSettingsIn, user: CurrentUser, primary: PrimaryClient
) -> AlertSettingsOut:
    """Consentito anche se il piano non include gli alert: il gate vero è
    alla run (coerente col principio «rivaluta al momento dell'invio»)."""
    await bando_alert_service.set_abilitati(primary, user["id"], data.abilitati)
    return AlertSettingsOut(
        **await bando_alert_service.alert_settings_for_user(primary, user)
    )
