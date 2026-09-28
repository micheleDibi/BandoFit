"""Prezzi dei modelli Anthropic e costo delle chiamate, in CENTESIMI DI USD.

Tabella unica per i servizi LLM del modulo partenariati (docs/partenariati.md,
T6): serve sia a registrare il costo reale di una chiamata sia a prenotare il
budget giornaliero al caso peggiore PRIMA di chiamare. I valori vengono dal
listino in cache della skill claude-api del 2026-06-24 e vanno riconfermati sul
listino ufficiale. L'AI-check ha ancora i suoi prezzi cablati (fuori perimetro).

Tutto per eccesso: un costo sottostimato renderebbe il budget meno severo di
quanto dichiara, uno sovrastimato lo fa solo scattare prima.
"""

import logging
import math

logger = logging.getLogger("bandofit.ai")

# modello → (input, output) in centesimi di USD per milione di token
PREZZI_CENTS_PER_MTOK: dict[str, tuple[int, int]] = {
    "claude-sonnet-5": (200, 1000),
    "claude-sonnet-4-6": (300, 1500),
    "claude-opus-5": (500, 2500),
    "claude-haiku-4-5": (100, 500),
}

# Caratteri per token assunti per stimare l'input prima della chiamata: il
# caso PEGGIORE, non la media. Il tokenizer di claude-sonnet-5 (lo stesso di
# Opus 4.7+) produce fino a ~1,35 volte i token dei modelli precedenti a
# parità di testo (≈3,5 caratteri per token → ≈2,6), e il testo italiano dei
# PDF, con cifre, importi e codici, è ancora più denso. Da riconfermare con
# `count_tokens` sui PDF del campione (serve credito Anthropic).
CARATTERI_PER_TOKEN = 2.5

_TOKEN_PER_MTOK = 1_000_000


def _prezzi(model: str) -> tuple[int, int]:
    """Prezzi del modello; un modello sconosciuto costa come il più caro
    (massimo per componente), con un warning: meglio un budget che scatta
    prima di uno che non scatta."""
    prezzi = PREZZI_CENTS_PER_MTOK.get(model)
    if prezzi is not None:
        return prezzi
    peggiore = (
        max(p[0] for p in PREZZI_CENTS_PER_MTOK.values()),
        max(p[1] for p in PREZZI_CENTS_PER_MTOK.values()),
    )
    logger.warning(
        "ai_prezzi: modello %r senza prezzo in tabella, uso il più caro %s", model, peggiore
    )
    return peggiore


def costo_cents(model: str, input_tokens: int | None, output_tokens: int | None) -> int:
    """Costo di una chiamata in centesimi di USD, arrotondato per eccesso.

    Token assenti o negativi valgono 0. Aritmetica intera: nessun errore di
    virgola mobile sul confine del centesimo."""
    prezzo_in, prezzo_out = _prezzi(model)
    tok_in = max(int(input_tokens or 0), 0)
    tok_out = max(int(output_tokens or 0), 0)
    micro = tok_in * prezzo_in + tok_out * prezzo_out
    return -(-micro // _TOKEN_PER_MTOK)


def stima_cents(model: str, caratteri_input: int, max_output_tokens: int) -> int:
    """Costo massimo prevedibile di una chiamata, da prenotare sul budget.

    Caso peggiore: tutto l'output consentito (`max_tokens` comprende anche il
    ragionamento) e l'input stimato a `CARATTERI_PER_TOKEN` caratteri per
    token. `caratteri_input` deve comprendere TUTTO ciò che si invia: prompt
    di sistema, messaggio e schema dell'output."""
    token_in = math.ceil(max(int(caratteri_input), 0) / CARATTERI_PER_TOKEN)
    return costo_cents(model, token_in, max(int(max_output_tokens), 0))
