"""Aritmetica monetaria del modulo pagamenti. SOLO funzioni pure.

Regole (upgrade decise il 2026-07-16; regime IVA croato deciso il 2026-07-21):
- upgrade: paga ``prezzo_nuovo − credito_residuo``; il credito è
  ``min(prezzo_vecchio × giorni_residui/365, prezzo_vecchio)`` — il clamp
  superiore copre i cicli da 366 giorni (upgrade su upgrade attraverso un
  29 febbraio), quello inferiore i piani scaduti non ancora processati;
- listino IVA ESCLUSA: il venditore è croato (ADVENTUS CONSULTING j.d.o.o.),
  IVA standard 25% sull'imponibile; **0% reverse charge** (art. 194/196
  Dir. 2006/112/CE, natura ``RC-UE``) SOLO per le aziende UE ≠ HR con
  ``vies_valid=True`` persistito a DB — tutto il resto (HR domestica, UE
  senza prova VIES, extra-UE prudenziale, privati, anagrafica assente)
  paga il 25%;
- arrotondamenti ``ROUND_HALF_UP`` ai centesimi, sempre su Decimal (mai
  float): il listino è numeric(10,2), i record transazionali sono centesimi.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.billing import PAESI_UE

IVA_ALIQUOTA = Decimal("25.00")
PAESE_VENDITORE = "HR"
# Marcatore del reverse charge sulle righe nuove (le storiche pre-cambio
# conservano l'italiano 'N2.1', immutabile per costruzione).
NATURA_REVERSE_CHARGE = "RC-UE"
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


def iva_per_soggetto(
    imponibile_cents: int,
    *,
    tipo_soggetto: str | None = None,
    paese: str | None = None,
    vies_valid: bool | None = None,
) -> tuple[int, Decimal, str | None]:
    """(iva_cents, aliquota, natura). Reverse charge 0% SOLO per le aziende
    UE ≠ HR con prova VIES persistita; ogni altro caso paga il 25% croato.
    Il VIES non si interroga mai qui: si legge l'esito salvato dal profilo
    (fail-closed sull'aliquota)."""
    if (
        tipo_soggetto == "azienda"
        and paese in PAESI_UE
        and paese != PAESE_VENDITORE
        and vies_valid is True
    ):
        return 0, Decimal("0.00"), NATURA_REVERSE_CHARGE
    iva = int(
        (Decimal(imponibile_cents) * IVA_ALIQUOTA / 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return iva, IVA_ALIQUOTA, None
