"""Client Anthropic: `genera` e `estrai_con_strumento` dei servizi partenariati e
invarianza dell'AI-check.

Nessuna chiamata reale: il client dell'SDK è sostituito da un finto
(`FakeAsyncAnthropic`) oppure è il vero `AsyncAnthropic` con un trasporto httpx
finto, per verificare la richiesta HTTP che l'SDK costruirebbe davvero.

Il punto portante (docs/partenariati.md, T6): quando la risposta è arrivata ma
è inutilizzabile (troncata, JSON non valido) la chiamata è addebitata, quindi
l'eccezione deve portare l'usage."""

import inspect
import json
import logging
from types import SimpleNamespace
from typing import Literal

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage
from pydantic import BaseModel, TypeAdapter

from app.clients.anthropic_ai import (
    MAX_OUTPUT_TOKENS,
    AiCheckClient,
    AiUsage,
    definizione_strumento,
)
from app.core.errors import AiNotConfiguredError, AiTimeoutError, AiUpstreamError
from app.schemas.ai_check import ExtractionResult, MatchingResult


class Uscita(BaseModel):
    esito: Literal["si", "no"]
    note: str | None


_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _messaggio(*blocchi, stop_reason="end_turn", tok_in=1200, tok_out=345) -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-5",
        content=list(blocchi),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=tok_in, output_tokens=tok_out),
    )


def _testo(text: str) -> TextBlock:
    return TextBlock(type="text", text=text, citations=None)


class FakeMessages:
    def __init__(self, risposta=None, errore=None):
        self.risposta = risposta
        self.errore = errore
        self.chiamate: list[tuple[str, dict]] = []

    async def _rispondi(self, nome, kwargs):
        self.chiamate.append((nome, kwargs))
        if self.errore is not None:
            raise self.errore
        return self.risposta

    async def create(self, **kwargs):
        return await self._rispondi("create", kwargs)

    async def parse(self, **kwargs):
        return await self._rispondi("parse", kwargs)


class FakeAsyncAnthropic:
    """Solo ciò che il client usa: `messages` e `with_options` (che nell'SDK
    restituisce una copia con le stesse impostazioni)."""

    def __init__(self, messages: FakeMessages):
        self.messages = messages
        self.opzioni: list[dict] = []

    def with_options(self, **kwargs):
        self.opzioni.append(kwargs)
        return self


def _client(fake=None, model="claude-sonnet-5") -> AiCheckClient:
    settings = SimpleNamespace(
        anthropic_api_key="", ai_check_model=model, ai_check_timeout_seconds=120.0
    )
    client = AiCheckClient(settings)  # senza chiave: nessun SDK istanziato
    client._client = fake
    return client


def _formato_atteso(modello) -> dict:
    return {
        "format": {
            "type": "json_schema",
            "schema": anthropic.transform_schema(TypeAdapter(modello).json_schema()),
        }
    }


# --- genera: esito valido --------------------------------------------------------


class TestGeneraValido:
    async def test_restituisce_modello_e_usage(self):
        fake = FakeAsyncAnthropic(
            FakeMessages(_messaggio(_testo('{"esito": "si", "note": null}')))
        )
        risultato, usage = await _client(fake).genera("SYS", "TESTO", Uscita)
        assert risultato == Uscita(esito="si", note=None)
        assert usage == AiUsage(input_tokens=1200, output_tokens=345)

    async def test_richiesta_con_default_del_client(self):
        messages = FakeMessages(_messaggio(_testo('{"esito": "no", "note": "x"}')))
        fake = FakeAsyncAnthropic(messages)
        await _client(fake).genera("SYS", "TESTO", Uscita)
        assert messages.chiamate == [(
            "create",
            {
                "model": "claude-sonnet-5",
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": "SYS",
                "messages": [{"role": "user", "content": "TESTO"}],
                "output_config": _formato_atteso(Uscita),
            },
        )]
        assert fake.opzioni == []  # timeout del client, nessuna copia

    async def test_override_di_modello_token_e_timeout(self):
        messages = FakeMessages(_messaggio(_testo('{"esito": "si", "note": null}')))
        fake = FakeAsyncAnthropic(messages)
        await _client(fake).genera(
            "SYS", "TESTO", Uscita, model="claude-opus-5", max_tokens=4000, timeout=45.0
        )
        kwargs = messages.chiamate[0][1]
        assert (kwargs["model"], kwargs["max_tokens"]) == ("claude-opus-5", 4000)
        assert fake.opzioni == [{"timeout": 45.0}]

    async def test_blocchi_di_ragionamento_ignorati(self):
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(
            ThinkingBlock(type="thinking", thinking="ragiono…", signature="sig"),
            _testo('{"esito": "no", "note": null}'),
        )))
        risultato, _ = await _client(fake).genera("SYS", "TESTO", Uscita)
        assert risultato.esito == "no"


# --- genera: risposta arrivata ma inutilizzabile → usage allegato -------------------


class TestGeneraUsageSulleEccezioni:
    async def test_output_troncato_da_max_tokens(self):
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(
            _testo('{"esito": "s'), stop_reason="max_tokens", tok_in=51_000, tok_out=16_000,
        )))
        with pytest.raises(AiUpstreamError) as exc:
            await _client(fake).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage == AiUsage(input_tokens=51_000, output_tokens=16_000)
        assert "troppo lungo" in exc.value.message

    async def test_ragionamento_che_esaurisce_i_token_senza_testo(self):
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(
            ThinkingBlock(type="thinking", thinking="…", signature="sig"),
            stop_reason="max_tokens", tok_out=16_000,
        )))
        with pytest.raises(AiUpstreamError) as exc:
            await _client(fake).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage == AiUsage(input_tokens=1200, output_tokens=16_000)

    @pytest.mark.parametrize(
        "testo",
        [
            "non è JSON",
            '{"esito": "forse", "note": null}',  # JSON valido, fuori dallo schema
            '{"esito": "si"}',  # campo obbligatorio mancante
            "",
        ],
    )
    async def test_json_non_valido(self, testo):
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(_testo(testo))))
        with pytest.raises(AiUpstreamError) as exc:
            await _client(fake).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage == AiUsage(input_tokens=1200, output_tokens=345)
        assert exc.value.status_code == 502

    async def test_rifiuto_del_modello(self):
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(stop_reason="refusal", tok_out=3)))
        with pytest.raises(AiUpstreamError) as exc:
            await _client(fake).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage == AiUsage(input_tokens=1200, output_tokens=3)

    async def test_il_testo_della_risposta_non_finisce_nei_log(self, caplog):
        segreto = "BRANO RISERVATO DEL DOCUMENTO"
        fake = FakeAsyncAnthropic(FakeMessages(_messaggio(_testo(segreto))))
        with caplog.at_level(logging.DEBUG, logger="bandofit.ai"):
            with pytest.raises(AiUpstreamError):
                await _client(fake).genera("SYS", "TESTO", Uscita)
        assert segreto not in caplog.text
        assert "output_tokens=345" in caplog.text


# --- genera: risposta non arrivata → nessun usage, nessun retry ---------------------


class TestGeneraErroriDiTrasporto:
    async def test_timeout(self):
        messages = FakeMessages(errore=anthropic.APITimeoutError(request=_REQ))
        with pytest.raises(AiTimeoutError):
            await _client(FakeAsyncAnthropic(messages)).genera("SYS", "TESTO", Uscita)
        assert len(messages.chiamate) == 1

    async def test_errore_di_connessione(self):
        messages = FakeMessages(errore=anthropic.APIConnectionError(request=_REQ))
        with pytest.raises(AiUpstreamError) as exc:
            await _client(FakeAsyncAnthropic(messages)).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage is None
        assert len(messages.chiamate) == 1

    async def test_errore_http_del_provider(self):
        errore = anthropic.InternalServerError(
            message="overloaded", response=httpx.Response(529, request=_REQ), body=None
        )
        messages = FakeMessages(errore=errore)
        with pytest.raises(AiUpstreamError) as exc:
            await _client(FakeAsyncAnthropic(messages)).genera("SYS", "TESTO", Uscita)
        assert exc.value.usage is None
        assert len(messages.chiamate) == 1

    async def test_non_configurato(self):
        with pytest.raises(AiNotConfiguredError):
            await _client(None).genera("SYS", "TESTO", Uscita)

    def test_usage_di_default_e_none(self):
        assert AiUpstreamError().usage is None


# --- genera con il vero SDK su un trasporto finto --------------------------------------


def _sdk_con_trasporto(risposte: list[dict], richieste: list[dict]) -> anthropic.AsyncAnthropic:
    def handler(request: httpx.Request) -> httpx.Response:
        richieste.append(json.loads(request.content))
        return httpx.Response(200, json=risposte.pop(0))

    return anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        timeout=5.0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _corpo_messaggio(text: str, stop_reason="end_turn") -> dict:
    return {
        "id": "msg_x",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


class TestGeneraSulVeroSdk:
    async def test_stesso_vincolo_di_messages_parse(self):
        """genera non usa messages.parse (che perderebbe l'usage sul JSON non
        valido), ma deve inviare lo STESSO corpo che parse invierebbe."""
        richieste: list[dict] = []
        valido = '{"esito": "si", "note": null}'
        sdk = _sdk_con_trasporto(
            [_corpo_messaggio(valido), _corpo_messaggio(valido)], richieste
        )
        client = _client(sdk)

        risultato, usage = await client.genera("SYS", "TESTO", Uscita)
        await sdk.messages.parse(
            model="claude-sonnet-5",
            max_tokens=MAX_OUTPUT_TOKENS,
            system="SYS",
            messages=[{"role": "user", "content": "TESTO"}],
            output_format=Uscita,
        )
        assert richieste[0] == richieste[1]
        assert richieste[0]["output_config"]["format"]["type"] == "json_schema"
        assert risultato == Uscita(esito="si", note=None)
        assert usage == AiUsage(input_tokens=100, output_tokens=20)

    async def test_json_troncato_porta_l_usage(self):
        richieste: list[dict] = []
        sdk = _sdk_con_trasporto(
            [_corpo_messaggio('{"esito": ', stop_reason="max_tokens")], richieste
        )
        with pytest.raises(AiUpstreamError) as exc:
            await _client(sdk).genera("SYS", "TESTO", Uscita, timeout=30.0)
        assert exc.value.usage == AiUsage(input_tokens=100, output_tokens=20)
        assert len(richieste) == 1  # max_retries=0: nessun secondo tentativo


# --- extract / match: invariati ------------------------------------------------------


class TestExtractMatchInvariati:
    def test_firme_invariate(self):
        assert list(inspect.signature(AiCheckClient.extract).parameters) == [
            "self", "system", "bando_input",
        ]
        assert list(inspect.signature(AiCheckClient.match).parameters) == [
            "self", "system", "matching_input",
        ]

    @pytest.mark.parametrize(
        ("metodo", "schema"), [("extract", ExtractionResult), ("match", MatchingResult)]
    )
    async def test_stessa_chiamata_di_prima(self, metodo, schema):
        parsed = object()
        risposta = SimpleNamespace(
            parsed_output=parsed,
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=10, output_tokens=7),
        )
        messages = FakeMessages(risposta)
        fake = FakeAsyncAnthropic(messages)
        risultato, usage = await getattr(_client(fake), metodo)("SYS", "INPUT")
        assert risultato is parsed
        assert usage == AiUsage(input_tokens=10, output_tokens=7)
        assert messages.chiamate == [(
            "parse",
            {
                "model": "claude-sonnet-5",
                "max_tokens": MAX_OUTPUT_TOKENS,
                "system": "SYS",
                "messages": [{"role": "user", "content": "INPUT"}],
                "output_format": schema,
            },
        )]
        assert fake.opzioni == []  # timeout del client (ai_check_timeout_seconds)

    async def test_troncato_stesso_messaggio_e_ora_anche_usage(self):
        risposta = SimpleNamespace(
            parsed_output=None,
            stop_reason="max_tokens",
            usage=SimpleNamespace(input_tokens=40_000, output_tokens=16_000),
        )
        with pytest.raises(AiUpstreamError) as exc:
            await _client(FakeAsyncAnthropic(FakeMessages(risposta))).extract("SYS", "INPUT")
        assert exc.value.message == (
            "Il bando è troppo lungo per completare l'analisi: riprova più tardi"
        )
        assert exc.value.usage == AiUsage(input_tokens=40_000, output_tokens=16_000)

    async def test_senza_output_stesso_messaggio(self):
        risposta = SimpleNamespace(
            parsed_output=None, stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=2),
        )
        with pytest.raises(AiUpstreamError) as exc:
            await _client(FakeAsyncAnthropic(FakeMessages(risposta))).match("SYS", "INPUT")
        assert exc.value.message == "L'analisi non ha prodotto un risultato valido, riprova"

    async def test_timeout_resta_ai_timeout(self):
        messages = FakeMessages(errore=anthropic.APITimeoutError(request=_REQ))
        with pytest.raises(AiTimeoutError):
            await _client(FakeAsyncAnthropic(messages)).extract("SYS", "INPUT")

    async def test_parse_accetta_gli_override(self):
        risposta = SimpleNamespace(
            parsed_output="ok", stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
        messages = FakeMessages(risposta)
        fake = FakeAsyncAnthropic(messages)
        await _client(fake)._parse(
            "SYS", "INPUT", Uscita, model="claude-haiku-4-5", max_tokens=100, timeout=9.0
        )
        kwargs = messages.chiamate[0][1]
        assert (kwargs["model"], kwargs["max_tokens"]) == ("claude-haiku-4-5", 100)
        assert fake.opzioni == [{"timeout": 9.0}]

    def test_client_reale_senza_retry(self):
        client = AiCheckClient(SimpleNamespace(
            anthropic_api_key="sk-test", ai_check_model="claude-sonnet-5",
            ai_check_timeout_seconds=120.0,
        ))
        assert client._client.max_retries == 0
        assert client._client.with_options(timeout=45.0).max_retries == 0


# --- estrai_con_strumento: strumento forzato NON strict (estrazione WP3) --------------
# Col vero SDK su un trasporto finto: si verifica il corpo HTTP che partirebbe
# davvero (tools senza strict, tool_choice forzato, nessun output_config).

STRUMENTO = "registra_esito"
DESCRIZIONE = "Registra l'esito della verifica"


def _sdk_con_handler(handler, richieste: list[dict]) -> anthropic.AsyncAnthropic:
    def registra(request: httpx.Request) -> httpx.Response:
        richieste.append(json.loads(request.content))
        return handler(request)

    return anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        timeout=5.0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(registra)),
    )


def _corpo_strumento(*blocchi: dict, stop_reason="tool_use") -> dict:
    return {
        "id": "msg_t",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": list(blocchi),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def _tool_use(input_, nome=STRUMENTO, id_="toolu_1") -> dict:
    return {"type": "tool_use", "id": id_, "name": nome, "input": input_}


async def _estrai(sdk, modello=Uscita, **kwargs):
    return await _client(sdk).estrai_con_strumento(
        "SYS", "TESTO", modello, nome_strumento=STRUMENTO, descrizione=DESCRIZIONE, **kwargs
    )


class TestEstraiConStrumentoSulVeroSdk:
    async def test_corpo_http_strumento_forzato_non_strict(self):
        richieste: list[dict] = []
        risposta = _corpo_strumento(_tool_use({"esito": "si", "note": None}))
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=risposta), richieste)
        risultato, usage = await _estrai(sdk)
        assert risultato == Uscita(esito="si", note=None)
        assert usage == AiUsage(input_tokens=100, output_tokens=20)
        [corpo] = richieste
        assert set(corpo) == {"model", "max_tokens", "system", "messages", "tools",
                              "tool_choice"}
        assert "output_config" not in corpo
        assert corpo["tools"] == [definizione_strumento(Uscita, STRUMENTO, DESCRIZIONE)]
        assert "strict" not in corpo["tools"][0]
        # esattamente UNA chiamata: mai un'estrazione divisa in più blocchi, di
        # cui il primo, parziale, passerebbe la convalida tollerante
        assert corpo["tool_choice"] == {"type": "tool", "name": STRUMENTO,
                                        "disable_parallel_tool_use": True}
        assert (corpo["model"], corpo["max_tokens"]) == ("claude-sonnet-5", MAX_OUTPUT_TOKENS)
        assert corpo["system"] == "SYS"
        assert corpo["messages"] == [{"role": "user", "content": "TESTO"}]

    def test_schema_dello_strumento_come_quello_dell_output_strutturato(self):
        """Stessa trasformazione dell'SDK (niente vincoli non supportati): lo
        schema inviato non cambia passando dall'output strict allo strumento."""
        strumento = definizione_strumento(Uscita, STRUMENTO, DESCRIZIONE)
        assert strumento == {
            "name": STRUMENTO, "description": DESCRIZIONE,
            "input_schema": _formato_atteso(Uscita)["format"]["schema"],
        }

    async def test_legge_il_primo_blocco_con_quel_nome(self):
        richieste: list[dict] = []
        risposta = _corpo_strumento(
            {"type": "text", "text": "Ecco l'esito."},
            _tool_use({"esito": "no", "note": None}, nome="altro", id_="toolu_0"),
            _tool_use({"esito": "si", "note": "primo"}, id_="toolu_1"),
            _tool_use({"esito": "no", "note": "secondo"}, id_="toolu_2"),
        )
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=risposta), richieste)
        risultato, _ = await _estrai(sdk)
        assert risultato == Uscita(esito="si", note="primo")

    async def test_convalida_del_chiamante(self):
        """L'estrazione WP3 passa la convalida tollerante: tipi semplici
        sbagliati e campi mancanti non buttano una risposta pagata."""
        from app.schemas.partenariato import PartenariatoEstrazione, convalida_tollerante

        richieste: list[dict] = []
        risposta = _corpo_strumento(_tool_use({"modalita": "Ammesso", "partner_min": 3,
                                               "campo_ignoto": True}))
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=risposta), richieste)
        risultato, usage = await _estrai(sdk, PartenariatoEstrazione,
                                         convalida=convalida_tollerante)
        assert isinstance(risultato, PartenariatoEstrazione)
        assert (risultato.modalita, risultato.partner_min, risultato.quote) == (
            "ammesso", "3", [])
        assert usage == AiUsage(input_tokens=100, output_tokens=20)
        schema = richieste[0]["tools"][0]["input_schema"]
        assert schema == anthropic.transform_schema(
            TypeAdapter(PartenariatoEstrazione).json_schema())

    @pytest.mark.parametrize(
        ("corpo", "convalida", "messaggio"),
        [
            # troncato da max_tokens: l'input può essere parziale, non si usa
            (_corpo_strumento(_tool_use({"esito": "si", "note": None}),
                              stop_reason="max_tokens"), None, "troppo lungo"),
            (_corpo_strumento(stop_reason="refusal"), None, "non ha prodotto"),
            # nessun blocco tool_use (solo testo)
            (_corpo_strumento({"type": "text", "text": '{"esito": "si"}'},
                              stop_reason="end_turn"), None, "non ha prodotto"),
            # blocco di un altro strumento
            (_corpo_strumento(_tool_use({"esito": "si", "note": None}, nome="altro")),
             None, "non ha prodotto"),
            # input che la convalida stretta di default respinge
            (_corpo_strumento(_tool_use({"esito": "forse", "note": None})), None,
             "non ha prodotto"),
            # input non oggetto con la convalida tollerante
            (_corpo_strumento(_tool_use(["non", "un", "oggetto"])), "tollerante",
             "non ha prodotto"),
            # oggetto senza nessun campo dell'estrazione: non un'estrazione vuota
            # (che si salverebbe e si riuserebbe), una risposta illeggibile
            (_corpo_strumento(_tool_use({"risultato": {"esito": "ammesso"}})), "tollerante",
             "non ha prodotto"),
        ],
        ids=["max_tokens", "rifiuto", "senza_tool_use", "altro_nome", "input_non_valido",
             "input_non_oggetto", "input_senza_campi_noti"],
    )
    async def test_risposta_inutilizzabile_porta_l_usage(self, corpo, convalida, messaggio):
        from app.schemas.partenariato import PartenariatoEstrazione, convalida_tollerante

        richieste: list[dict] = []
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=corpo), richieste)
        kwargs = ({"convalida": convalida_tollerante} if convalida else {})
        modello = PartenariatoEstrazione if convalida else Uscita
        with pytest.raises(AiUpstreamError) as exc:
            await _estrai(sdk, modello, timeout=30.0, **kwargs)
        assert exc.value.usage == AiUsage(input_tokens=100, output_tokens=20)
        assert messaggio in exc.value.message
        assert len(richieste) == 1  # max_retries=0

    async def test_convalida_che_solleva_type_error(self):
        def rotta(dati):
            raise TypeError("input illeggibile")

        richieste: list[dict] = []
        corpo = _corpo_strumento(_tool_use({"esito": "si", "note": None}))
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=corpo), richieste)
        with pytest.raises(AiUpstreamError) as exc:
            await _estrai(sdk, convalida=rotta)
        assert exc.value.usage == AiUsage(input_tokens=100, output_tokens=20)

    @pytest.mark.parametrize("stato", [400, 401, 404, 429, 500, 529])
    async def test_errore_http_senza_usage_con_lo_stato(self, stato):
        richieste: list[dict] = []
        corpo = {"type": "error", "error": {"type": "invalid_request_error",
                                            "message": "tool_choice non valido"}}
        sdk = _sdk_con_handler(lambda r: httpx.Response(stato, json=corpo), richieste)
        with pytest.raises(AiUpstreamError) as exc:
            await _estrai(sdk)
        assert exc.value.usage is None
        # il chiamante legge lo stato dall'eccezione dell'SDK (4xx non transitori)
        assert exc.value.__cause__.status_code == stato
        assert len(richieste) == 1  # nessun retry, nemmeno su 429/5xx

    async def test_timeout(self):
        def lento(request):
            raise httpx.ReadTimeout("lento", request=request)

        richieste: list[dict] = []
        with pytest.raises(AiTimeoutError):
            await _estrai(_sdk_con_handler(lento, richieste))
        assert len(richieste) == 1

    async def test_errore_di_connessione(self):
        def giu(request):
            raise httpx.ConnectError("giù", request=request)

        richieste: list[dict] = []
        with pytest.raises(AiUpstreamError) as exc:
            await _estrai(_sdk_con_handler(giu, richieste))
        assert exc.value.usage is None and len(richieste) == 1

    async def test_non_configurato(self):
        with pytest.raises(AiNotConfiguredError):
            await _estrai(None)

    async def test_override_di_modello_token_e_timeout(self):
        messages = FakeMessages(_messaggio(
            ToolUseBlock(type="tool_use", id="toolu_1", name=STRUMENTO,
                         input={"esito": "no", "note": None}),
            stop_reason="tool_use",
        ))
        fake = FakeAsyncAnthropic(messages)
        risultato, usage = await _client(fake).estrai_con_strumento(
            "SYS", "TESTO", Uscita, nome_strumento=STRUMENTO, descrizione=DESCRIZIONE,
            model="claude-opus-5", max_tokens=4000, timeout=45.0,
        )
        assert risultato.esito == "no" and usage == AiUsage(1200, 345)
        [(metodo, kwargs)] = messages.chiamate
        assert metodo == "create"
        assert (kwargs["model"], kwargs["max_tokens"]) == ("claude-opus-5", 4000)
        assert fake.opzioni == [{"timeout": 45.0}]

    async def test_l_input_non_finisce_nei_log(self, caplog):
        segreto = "BRANO RISERVATO DEL DOCUMENTO"
        richieste: list[dict] = []
        corpo = _corpo_strumento(_tool_use({"esito": segreto, "note": segreto}))
        sdk = _sdk_con_handler(lambda r: httpx.Response(200, json=corpo), richieste)
        with caplog.at_level(logging.DEBUG, logger="bandofit.ai"):
            with pytest.raises(AiUpstreamError):
                await _estrai(sdk)
        assert segreto not in caplog.text
        assert "input_respinto=True" in caplog.text and "output_tokens=20" in caplog.text


class TestGeneraInvariato:
    async def test_genera_resta_output_strutturato_strict(self):
        """WP4/WP5 restano su `output_config.format`: niente tools."""
        messages = FakeMessages(_messaggio(_testo('{"esito": "si", "note": null}')))
        await _client(FakeAsyncAnthropic(messages)).genera("SYS", "TESTO", Uscita)
        [(_, kwargs)] = messages.chiamate
        assert "tools" not in kwargs and "tool_choice" not in kwargs
        assert kwargs["output_config"] == _formato_atteso(Uscita)
