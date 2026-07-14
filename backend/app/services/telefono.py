"""Validazione locale del numero di telefono (memorizzazione in E.164).

Serve solo il FORMATO (niente OTP): normalizza l'input di un utente italiano
(«347 1234567» → «+393471234567»; i fissi MANTENGONO lo zero: «02 5551234» →
«+39025551234») e accetta i numeri internazionali con prefisso esplicito
(«+» o «00»). Nessuna pretesa di conoscere i piani di numerazione esteri:
lì vale la sola regola E.164 generale.

Duplica `frontend/src/lib/telefono.ts`: il backend resta l'autorità e rifiuta
comunque con 422. Se cambia lì, cambiare qui — i vettori di test sono gli
stessi.
"""

import re

_SEPARATORI = re.compile(r"[\s./()\-]")
# E.164: prefisso paese che non inizia per 0, max 15 cifre totali.
# re.ASCII: senza, \d accetterebbe qualsiasi cifra Unicode (es. ３４７ o ٣٤٧)
# che il gemello JS rifiuta — i due validatori devono restare identici.
_E164 = re.compile(r"^\+[1-9]\d{5,14}$", re.ASCII)


def normalize_telefono(value: str) -> str:
    """Porta l'input in forma E.164 (best-effort, non valida)."""
    cleaned = _SEPARATORI.sub("", value.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        # Default Italia. Lo zero iniziale dei fissi NON si rimuove:
        # l'E.164 italiano lo conserva (02… → +3902…).
        cleaned = "+39" + cleaned
    return cleaned


def is_valid_telefono(value: str) -> bool:
    """True se ``value`` (già normalizzato) è un E.164 plausibile."""
    if not _E164.fullmatch(value):
        return False
    if value.startswith("+39"):
        # Sanity-check per il default: 6-11 cifre nel numero nazionale.
        return 6 <= len(value) - len("+39") <= 11
    return True
