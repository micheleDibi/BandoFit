"""Validazione locale (gratuita) del codice fiscale delle persone fisiche.

Struttura + carattere di controllo secondo l'algoritmo ufficiale (DM 23/12/1976).
Le sostituzioni per omocodia (cifre → L,M,N,P,Q,R,S,T,U,V nelle posizioni
numeriche) sono coperte naturalmente: le tabelle di conversione del checksum
includono le lettere, e la struttura ammette [A-Z0-9] nelle posizioni numeriche.

La verifica di ESISTENZA all'Anagrafe Tributaria è un'altra cosa (a pagamento,
via openapi.it): questa validazione serve a non pagare per input malformati.
"""

import re

# Struttura: 6 lettere (cognome+nome), anno (2), mese (lettera), giorno (2),
# comune (lettera + 3), carattere di controllo. Posizioni numeriche in
# [A-Z0-9] per l'omocodia.
_STRUCTURE = re.compile(
    r"^[A-Z]{6}[A-Z0-9]{2}[ABCDEHLMPRST][A-Z0-9]{2}[A-Z][A-Z0-9]{3}[A-Z]$"
)

_ODD = {
    "0": 1, "1": 0, "2": 5, "3": 7, "4": 9, "5": 13, "6": 15, "7": 17, "8": 19,
    "9": 21, "A": 1, "B": 0, "C": 5, "D": 7, "E": 9, "F": 13, "G": 15, "H": 17,
    "I": 19, "J": 21, "K": 2, "L": 4, "M": 18, "N": 20, "O": 11, "P": 3,
    "Q": 6, "R": 8, "S": 12, "T": 14, "U": 16, "V": 10, "W": 22, "X": 25,
    "Y": 24, "Z": 23,
}
_EVEN = {
    **{str(d): d for d in range(10)},
    **{chr(ord("A") + i): i for i in range(26)},
}


def normalize_cf(value: str | None) -> str:
    return (value or "").strip().upper()


def is_valid_cf(value: str | None) -> bool:
    """True se il CF è strutturalmente valido e il checksum torna."""
    cf = normalize_cf(value)
    if not _STRUCTURE.fullmatch(cf):
        return False
    total = 0
    for index, char in enumerate(cf[:15]):
        # posizioni 1-indexed: dispari = indice pari
        total += _ODD[char] if index % 2 == 0 else _EVEN[char]
    return chr(ord("A") + total % 26) == cf[15]
