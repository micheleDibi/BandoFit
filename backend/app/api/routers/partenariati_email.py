"""Disiscrizione PUBBLICA dalle email dei partenariati (WP6, Q6, RFC 8058).

Unica rotta del modulo FUORI dal flag: un link già inviato deve funzionare
anche se il modulo viene spento. Stesso schema degli alert sui bandi
(`routers/alerts.py`): il GET mostra solo una pagina con il bottone di
conferma e NON muta nulla (gli scanner antispam pre-aprono i link delle
email); il POST disiscrive ed è idempotente. Anti-enumerazione: la risposta è
sempre la stessa, con token valido, ignoto o malformato e con un `tipo`
sconosciuto.
"""

import html
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.api.deps import PrimaryClient
from app.api.routers.alerts import _pagina, _token_valido, _vuole_html
from app.core.config import get_settings
from app.services import partenariato_notifiche

router = APIRouter(prefix="/partenariati/email", tags=["partenariati"])

_TESTI = {
    "digest": ("il riepilogo settimanale delle call di partenariato per la tua azienda",
               "Disattiva il riepilogo"),
    "eventi": ("le email su inviti, candidature e messaggi dei partenariati",
               "Disattiva queste email"),
}


@router.get("/unsubscribe")
async def unsubscribe_page(
    token: str = Query(default=""), tipo: str = Query(default="digest")
) -> HTMLResponse:
    """Pagina di conferma, NON mutante."""
    cosa, etichetta = _TESTI.get(tipo, _TESTI["digest"])
    action = (
        "/api/v1/partenariati/email/unsubscribe"
        f"?token={quote(token, safe='')}&tipo={quote(tipo, safe='')}"
    )
    bottone = (
        f'<form method="post" action="{html.escape(action, quote=True)}" style="margin:0">'
        '<button type="submit" style="background:#1E5EFF;color:#fff;border:none;'
        'font-weight:600;font-size:15px;padding:12px 24px;border-radius:8px;cursor:pointer">'
        f"{html.escape(etichetta)}</button></form>"
    )
    return _pagina(
        "Email dei partenariati",
        f"Vuoi smettere di ricevere via email {cosa}?",
        bottone,
    )


@router.post("/unsubscribe")
async def unsubscribe(
    request: Request,
    primary: PrimaryClient,
    token: str = Query(default=""),
    tipo: str = Query(default="digest"),
) -> Response:
    """Disiscrizione a un clic: idempotente, sempre la stessa risposta."""
    if tipo in _TESTI and _token_valido(token):
        await partenariato_notifiche.unsubscribe_by_token(primary, token, tipo)
    if _vuole_html(request):
        preferenze = f"{get_settings().frontend_url.rstrip('/')}/app/preferenze?tab=avvisi"
        link = (
            f'<a href="{html.escape(preferenze, quote=True)}" '
            'style="font-size:14px;color:#1E5EFF">Vai alle Preferenze</a>'
        )
        return _pagina(
            "Preferenza salvata",
            "Non riceverai più queste email dei partenariati. Puoi riattivarle in "
            "qualsiasi momento dalle Preferenze della piattaforma.",
            link,
        )
    return Response(status_code=204)
