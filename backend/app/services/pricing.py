"""Aritmetica monetaria del modulo pagamenti. SOLO funzioni pure.

Regole (decise con l'utente il 2026-07-16, vedi piano):
- upgrade: paga ``prezzo_nuovo − credito_residuo``; il credito è
  ``min(prezzo_vecchio × giorni_residui/365, prezzo_vecchio)`` — il clamp
  superiore copre i cicli da 366 giorni (upgrade su upgrade attraverso un
  29 febbraio), quello inferiore i piani scaduti non ancora processati;
- listino IVA ESCLUSA: l'IVA (22%) si aggiunge sull'imponibile; per le
  aziende UE reverse charge art. 7-ter → IVA zero, natura N2.1;
- arrotondamenti ``ROUND_HALF_UP`` ai centesimi, sempre su Decimal (mai
  float): il listino è numeric(10,2), i record transazionali sono centesimi.
"""

from decimal import ROUND_HALF_UP, Decimal

IVA_ALIQUOTA = Decimal("22.00")
GIORNI_ANNO = 365


def in_cents(euro: Decimal) -> int:
    return int((euro * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def credito_residuo(prezzo_vecchio: Decimal, giorni_residui: int) -> Decimal:
    giorni = max(0, giorni_residui)
    credito = (prezzo_vecchio * giorni / GIORNI_ANNO).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return min(credito, prezzo_vecchio)


def imponibile_upgrade(prezzo_nuovo: Decimal, prezzo_vecchio: Decimal, giorni_residui: int) -> tuple[Decimal, Decimal]:
    """(imponibile, credito). L'imponibile può risultare ≤ 0 solo con listini
    admin anomali (pari prezzo a ordering diverso): il chiamante rifiuta."""
    credito = credito_residuo(prezzo_vecchio, giorni_residui)
    return prezzo_nuovo - credito, credito


def iva_per_soggetto(imponibile_cents: int, tipo_soggetto: str | None) -> tuple[int, Decimal, str | None]:
    """(iva_cents, aliquota, natura). Reverse charge SOLO per azienda_ue."""
    if tipo_soggetto == "azienda_ue":
        return 0, Decimal("0.00"), "N2.1"
    iva = int(
        (Decimal(imponibile_cents) * IVA_ALIQUOTA / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return iva, IVA_ALIQUOTA, None
