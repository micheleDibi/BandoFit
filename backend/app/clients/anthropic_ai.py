"""Client per l'API Anthropic (AI-check).

Regole di spesa allineate al client openapi.it: MAI retry automatici su
chiamate potenzialmente addebitate (``max_retries=0`` — l'SDK di default ne
farebbe 2), timeout esplicito, errori mappati su eccezioni tipizzate.

L'output è vincolato allo schema con ``messages.parse`` (structured outputs):
la risposta torna già validata come modello Pydantic (``parsed_output``); una
risposta non conforme è trattata come guasto del provider.
"""

import logging
from dataclasses import dataclass

from app.core.errors import AiTimeoutError, AiUpstreamError
from app.schemas.ai_check import ExtractionResult, MatchingResult

logger = logging.getLogger("bandofit.ai")

# max_tokens è il tetto COMPLESSIVO di thinking + risposta: claude-sonnet-5
# ragiona in modo adattivo di default, e su un bando lungo il ragionamento
# può consumare diverse migliaia di token prima dell'output strutturato.
# Un tetto stretto troncherebbe il JSON (stop_reason=max_tokens) buttando
# una chiamata già pagata.
MAX_OUTPUT_TOKENS = 16000


@dataclass(frozen=True)
class AiUsage:
    input_tokens: int
    output_tokens: int


class AiCheckClient:
    """Wrapper sottile su AsyncAnthropic: due sole operazioni, entrambe con
    output strutturato — l'estrazione dei requisiti dal bando e il matching
    punto-punto col profilo aziendale."""

    def __init__(self, settings):
        self._api_key = settings.anthropic_api_key or ""
        self.model = settings.ai_check_model
        self._client = None
        if self._api_key:
            # Import locale: il modulo resta importabile anche senza SDK
            # installato finché la feature è disattivata.
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self._api_key,
                timeout=settings.ai_check_timeout_seconds,
                max_retries=0,
            )

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def _parse(self, system: str, user_message: str, output_format):
        import anthropic

        try:
            message = await self._client.messages.parse(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                output_format=output_format,
            )
        except anthropic.APITimeoutError as exc:
            # Esito (e addebito) ignoto: nessun retry, decide il chiamante.
            raise AiTimeoutError() from exc
        except anthropic.APIConnectionError as exc:
            raise AiUpstreamError() from exc
        except anthropic.APIStatusError as exc:
            logger.error("anthropic: errore %s — %s", exc.status_code, exc.message)
            raise AiUpstreamError() from exc

        parsed = message.parsed_output
        if parsed is None:
            logger.error(
                "anthropic: risposta senza output strutturato (stop_reason=%s, "
                "output_tokens=%s)",
                message.stop_reason,
                getattr(message.usage, "output_tokens", None),
            )
            if message.stop_reason == "max_tokens":
                # Output troncato dal tetto token: la chiamata è comunque
                # addebitata — errore chiaro, decide l'utente se riprovare.
                raise AiUpstreamError(
                    "Il bando è troppo lungo per completare l'analisi: riprova più tardi"
                )
            raise AiUpstreamError("L'analisi non ha prodotto un risultato valido, riprova")
        usage = AiUsage(
            input_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
        )
        return parsed, usage

    async def extract(
        self, system: str, bando_input: str
    ) -> tuple[ExtractionResult, AiUsage]:
        """Stadio A: requisiti obbligatori + criteri di valutazione dal bando."""
        return await self._parse(system, bando_input, ExtractionResult)

    async def match(
        self, system: str, matching_input: str
    ) -> tuple[MatchingResult, AiUsage]:
        """Stadio B: verdetti punto-punto tra estrazione e profilo azienda."""
        return await self._parse(system, matching_input, MatchingResult)
