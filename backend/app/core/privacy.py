"""Minimizzazione dei dati personali e d'impresa nei log, nei registri e nei
payload verso terzi (piano partenariati, T2/T8).

Un solo posto per mascherare P.IVA, codici fiscali ed email: i percorsi nuovi
non scrivono mai questi dati in chiaro in log, `request_meta` o audit.

`hmac_dominio` produce identificativi opachi e stabili (es. per confrontare
collegamenti societari o costruire pseudonimi) senza conservare il valore in
chiaro. Il dominio separa gli usi: lo stesso valore in domini diversi dà
digest diversi, quindi un identificativo di un contesto non si può
correlare con quello di un altro.
"""

import hashlib
import hmac
import logging

from app.core.config import get_settings

logger = logging.getLogger("bandofit.privacy")

# Chiave di ripiego SOLO per lo sviluppo: in produzione config.py rifiuta di
# partire senza RATE_LIMIT_PEPPER, quindi questa chiave non firma mai dati reali.
_CHIAVE_SVILUPPO = b"bandofit-privacy-chiave-di-sviluppo"
_ETICHETTA_CHIAVE = b"bandofit.privacy.hmac_dominio.v1"

_avviso_pepper_emesso = False


def _testo(valore: object) -> str:
    return "" if valore is None else str(valore).strip()


def mask_piva(valore: object) -> str:
    """P.IVA mascherata: prime 3 cifre + `*****` + ultime 3 (es. `123*****789`).

    Valori troppo corti per lasciare una parte nascosta diventano `***`."""
    testo = _testo(valore).replace(" ", "")
    if len(testo) < 7:
        return "***"
    return testo[:3] + "*****" + testo[-3:]


def mask_cf(valore: object) -> str:
    """Codice fiscale mascherato.

    - persona fisica (16 caratteri): prime 6 + 7 asterischi + ultimi 3, come il
      `_mask_cf` storico di openapi_service (stesso formato nei registri);
    - persona giuridica (11 cifre): come la P.IVA;
    - altro: `***`."""
    testo = _testo(valore).replace(" ", "").upper()
    if len(testo) == 16:
        return testo[:6] + "*" * 7 + testo[13:]
    if len(testo) == 11 and testo.isdigit():
        return mask_piva(testo)
    return "***"


def mask_email(valore: object) -> str:
    """Email mascherata: prima lettera della parte locale + `***@dominio`.

    Il dominio resta leggibile (serve a diagnosticare i problemi di recapito),
    la parte locale che identifica la persona no."""
    testo = _testo(valore)
    locale, chiocciola, dominio = testo.rpartition("@")
    if not chiocciola or not locale or not dominio:
        return "***"
    return f"{locale[0]}***@{dominio}"


def _chiave_base() -> bytes:
    """Chiave derivata dal pepper: mai il pepper grezzo, così gli HMAC di
    questo modulo non collidono con i bucket di rate_limit_service, che usano
    il pepper direttamente."""
    global _avviso_pepper_emesso
    pepper = get_settings().rate_limit_pepper.strip()
    if not pepper:
        if not _avviso_pepper_emesso:
            logger.warning(
                "rate_limit_pepper non configurato: hmac_dominio usa la chiave di sviluppo"
            )
            _avviso_pepper_emesso = True
        return _CHIAVE_SVILUPPO
    return hmac.new(pepper.encode("utf-8"), _ETICHETTA_CHIAVE, hashlib.sha256).digest()


def hmac_dominio(dominio: str, valore: str) -> str:
    """HMAC-SHA256 esadecimale di `valore`, separato per `dominio`.

    Deterministico a parità di pepper. La chiave del singolo dominio è a sua
    volta un HMAC della chiave base: domini diversi non possono collidere per
    concatenazione (`"a:b" + "c"` contro `"a" + "b:c"`)."""
    if not dominio:
        raise ValueError("hmac_dominio: il dominio è obbligatorio")
    chiave_dominio = hmac.new(
        _chiave_base(), b"dominio:" + dominio.encode("utf-8"), hashlib.sha256
    ).digest()
    return hmac.new(chiave_dominio, valore.encode("utf-8"), hashlib.sha256).hexdigest()
