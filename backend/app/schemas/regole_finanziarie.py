"""Contratto UNICO delle regole finanziarie del modulo partenariati.

Creato in WP1 e riusato senza ridefinirlo da WP3 (estrazione dal bando, anche
come schema di output dell'LLM), WP6 (matching) e WP8 (validatore del
consorzio). La valutazione vive in `services/bilanci_indicatori.py`.

Forma della regola: `numeratore [/ denominatore] <operatore> soglia`, dove la
soglia è un numero fisso (`soglia`) OPPURE un'altra variabile per un
coefficiente (`soglia_coefficiente × soglia_variabile`). Esempi:
- «costo della quota / fatturato medio 2 anni ≤ 0,6»: numeratore
  `costo_quota`, denominatore `fatturato_medio_2`, `le`, soglia `"0.6"`;
- «patrimonio netto > metà del costo»: numeratore `patrimonio_netto`, `gt`,
  soglia_variabile `costo_quota`, soglia_coefficiente `"0.5"`.

Nessun vincolo numerico nello schema: serve anche come output strutturato
dell'LLM, dove un valore fuori range farebbe fallire una chiamata già pagata.
La coerenza (esattamente una tra `soglia` e `soglia_variabile`, decimali
leggibili, unità coerente) la verifica `bilanci_indicatori.valida_regola`.
I decimali sono STRINGHE (es. `"0.6"`): mai float, altrimenti 0,6 ≤ 0,6
potrebbe risultare falso.
"""

from typing import Literal

from pydantic import BaseModel

# Nomi allineati alle colonne di company_financials (`risultato_esercizio`,
# non «utile»); `mol` è la colonna `ebitda`. `costo_quota`, `contributo_quota`
# e `costo_progetto_totale` sono parametri del partenariato, non di bilancio.
VariabileFinanziaria = Literal[
    "fatturato",
    "fatturato_medio_2",
    "fatturato_medio_3",
    "valore_produzione",
    "risultato_esercizio",
    "patrimonio_netto",
    "capitale_sociale",
    "totale_attivo",
    "debiti_totali",
    "disponibilita_liquide",
    "mol",
    "ebit",
    "oneri_finanziari",
    "costo_personale",
    "dipendenti",
    "bilanci_approvati_n",
    "costo_quota",
    "contributo_quota",
    "costo_progetto_totale",
]

Operatore = Literal["lt", "le", "gt", "ge"]

# A chi si applica la regola: la valutazione del singolo partner non dipende
# dall'ambito, che serve all'aggregazione del validatore (WP8).
AmbitoRegola = Literal["ciascun_partner", "capofila", "media_pesata_quote", "partenariato_totale"]

UnitaRegola = Literal["rapporto", "euro", "numero"]


class RegolaFinanziaria(BaseModel):
    id: str
    descrizione: str
    ambito: AmbitoRegola
    numeratore: VariabileFinanziaria
    # Campi nullable ma OBBLIGATORI (nessun default), come gli schemi LLM
    # dell'AI-check: l'output strutturato li riporta sempre, anche a null.
    denominatore: VariabileFinanziaria | None
    operatore: Operatore
    soglia: str | None
    soglia_variabile: VariabileFinanziaria | None
    soglia_coefficiente: str | None
    unita: UnitaRegola
