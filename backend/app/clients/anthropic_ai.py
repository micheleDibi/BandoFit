"""Client per l'API Anthropic (AI-check).

Regole di spesa allineate al client openapi.it: MAI retry automatici su
chiamate potenzialmente addebitate (``max_retries=0`` — l'SDK di default ne
farebbe 2), timeout esplicito, errori mappati su eccezioni tipizzate.

L'output è vincolato allo schema con ``messages.parse`` (structured outputs):
la risposta torna già validata come modello Pydantic (``parsed_output``); una
risposta non conforme è trattata come guasto del provider.

``genera`` (servizi del modulo partenariati) usa lo stesso vincolo ma valida
da sé il testo della risposta: così, quando la risposta è arrivata ma è
inutilizzabile (troncata, JSON non valido), l'eccezione porta con sé l'usage
(``AiUpstreamError.usage``) e una chiamata pagata non si registra mai a costo 0.
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel

from app.core.errors import AiNotConfiguredError, AiTimeoutError, AiUpstreamError
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


def _usage(message) -> AiUsage:
    usage = getattr(message, "usage", None)
    return AiUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
    )


def _upstream_con_usage(message: str, usage: AiUsage) -> AiUpstreamError:
    errore = AiUpstreamError(message)
    errore.usage = usage
    return errore


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

    def _client_per(self, timeout: float | None):
        """Il client condiviso, oppure una sua copia con un timeout diverso
        (``with_options`` conserva ``max_retries=0`` e il pool HTTP)."""
        if timeout is None:
            return self._client
        return self._client.with_options(timeout=timeout)

    async def _parse(
        self,
        system: str,
        user_message: str,
        output_format,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ):
        import anthropic

        try:
            message = await self._client_per(timeout).messages.parse(
                model=self.model if model is None else model,
                max_tokens=MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens,
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
                raise _upstream_con_usage(
                    "Il bando è troppo lungo per completare l'analisi: riprova più tardi",
                    _usage(message),
                )
            raise _upstream_con_usage(
                "L'analisi non ha prodotto un risultato valido, riprova", _usage(message)
            )
        return parsed, _usage(message)

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

    async def genera(
        self,
        system: str,
        user_message: str,
        output_format,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> tuple[BaseModel, AiUsage]:
        """Generazione con output strutturato per i servizi del modulo
        partenariati: restituisce ``(modello Pydantic, AiUsage)``.

        Stesso vincolo di ``messages.parse`` (``output_config.format`` con lo
        schema trasformato dall'SDK), ma il testo si valida qui: se la risposta
        è arrivata e non è utilizzabile (troncata da ``max_tokens``, rifiutata,
        JSON non conforme) l'``AiUpstreamError`` porta l'usage della chiamata,
        che è addebitata comunque. Timeout ed errori di rete → nessun usage
        (esito ignoto: decide il chiamante, di norma lasciando la riserva nel
        budget). Nessun retry, come per l'AI-check. ``model``, ``max_tokens``
        e ``timeout`` assenti = quelli del client."""
        import anthropic
        from pydantic import TypeAdapter, ValidationError

        if self._client is None:
            raise AiNotConfiguredError("Analisi automatica non configurata su questo ambiente")
        adattatore = TypeAdapter(output_format)
        formato = {
            "type": "json_schema",
            "schema": anthropic.transform_schema(adattatore.json_schema()),
        }
        modello = self.model if model is None else model
        try:
            message = await self._client_per(timeout).messages.create(
                model=modello,
                max_tokens=MAX_OUTPUT_TOKENS if max_tokens is None else max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
                output_config={"format": formato},
            )
        except anthropic.APITimeoutError as exc:
            raise AiTimeoutError() from exc
        except anthropic.APIConnectionError as exc:
            raise AiUpstreamError() from exc
        except anthropic.APIStatusError as exc:
            logger.error("anthropic: errore %s — %s", exc.status_code, exc.message)
            raise AiUpstreamError() from exc
        except anthropic.APIError as exc:
            logger.error("anthropic: risposta non interpretabile (%s)", type(exc).__name__)
            raise AiUpstreamError() from exc

        usage = _usage(message)
        for blocco in getattr(message, "content", None) or []:
            if getattr(blocco, "type", None) != "text":
                continue
            try:
                return adattatore.validate_json(blocco.text), usage
            except ValidationError:
                continue

        # Mai il testo nei log: può contenere brani dei documenti analizzati.
        logger.error(
            "anthropic: risposta senza output strutturato valido (model=%s, stop_reason=%s, "
            "input_tokens=%s, output_tokens=%s)",
            modello,
            getattr(message, "stop_reason", None),
            usage.input_tokens,
            usage.output_tokens,
        )
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise _upstream_con_usage(
                "Il testo da analizzare è troppo lungo per completare l'analisi", usage
            )
        raise _upstream_con_usage(
            "L'analisi non ha prodotto un risultato valido, riprova", usage
        )
