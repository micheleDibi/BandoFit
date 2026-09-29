"""Sonda degli schemi AI (`scripts/sonda_schemi_ai.py`): MAI Anthropic.

Il client è quello vero dell'app (`AiCheckClient.genera` e, per l'estrazione WP3,
`estrai_con_strumento`: gli stessi percorsi della produzione) con l'SDK sostituito da
un finto che registra le richieste. La cartella corrente è temporanea: il `.env` del
backend non si legge mai. Coperti: nessuna chiamata senza `--conferma`, modo stampato
(strumento non strict per l'estrazione, strict per gli altri), classificazione (400
«grammar» → RIFIUTATO, altro 400 → ERRORE, risposta anche troncata → ACCETTATO,
timeout → IGNOTO), richiesta minima con lo stesso `output_config.format` o lo stesso
strumento della produzione, tetto fail-closed, arresto su 401, chiave solo
dall'ambiente e mai stampata, modello dalle Settings o da `--model`, `--solo`."""

import json
from types import SimpleNamespace
from typing import Literal

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from app.clients.anthropic_ai import AiCheckClient
from app.core.config import Settings
from app.services.ai_prezzi import costo_cents
from scripts import sonda_schemi_ai as sonda

CHIAVE = "sk-ant-sonda-finta-da-non-stampare"
MODELLO_DEFAULT = Settings.model_fields["partenariato_ai_model"].default
GRAMMATICA = ("The compiled grammar is too large, which would cause performance issues. "
              "Simplify your tool schemas or reduce the number of strict tools.")
_RICHIESTA = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def errore_http(stato: int, tipo: str, messaggio: str) -> anthropic.APIStatusError:
    corpo = {"type": "error", "error": {"type": tipo, "message": messaggio}}
    return anthropic.APIStatusError(f"Error code: {stato} - {corpo}",
                                    response=httpx.Response(stato, request=_RICHIESTA),
                                    body=corpo)


def risposta(testo: str, stop_reason: str = "end_turn", tok_in: int = 900, tok_out: int = 20):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=testo)],
                           stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=tok_in, output_tokens=tok_out))


def risposta_strumento(input_, nome: str = "registra_regole_partenariato",
                       stop_reason: str = "tool_use", tok_in: int = 900, tok_out: int = 20):
    return SimpleNamespace(content=[SimpleNamespace(type="tool_use", id="toolu_1", name=nome,
                                                    input=input_)],
                           stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=tok_in, output_tokens=tok_out))


TESTI_OK = json.dumps({"titolo": "", "descrizione_pubblica": "", "profilo_partner_ideale": ""})
TRONCATA = risposta('{"posizioni": [', stop_reason="max_tokens", tok_in=1_500, tok_out=64)


class SdkFinto:
    """Al posto di AsyncAnthropic: `messages.create` registra la richiesta e
    restituisce (o solleva) la prossima risposta del copione."""

    def __init__(self, copione: list):
        self.copione = list(copione)
        self.richieste: list[dict] = []
        self.timeout: list = []
        self.chiuso = False
        self.messages = SimpleNamespace(create=self._create)

    def with_options(self, *, timeout):
        self.timeout.append(timeout)
        return self

    async def _create(self, **kwargs):
        self.richieste.append(kwargs)
        esito = self.copione.pop(0)
        if isinstance(esito, BaseException):
            raise esito
        return esito

    async def close(self):
        self.chiuso = True


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    cartella = tmp_path / "cwd"
    cartella.mkdir()
    monkeypatch.chdir(cartella)  # mai il .env vero del backend
    monkeypatch.delenv("PARTENARIATO_AI_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", CHIAVE)

    def mai(*a, **k):
        raise AssertionError("nei test la sonda non crea MAI il client vero")

    monkeypatch.setattr(sonda, "crea_client", mai)


@pytest.fixture
def sdk(monkeypatch):
    """Installa un SDK finto dietro il client VERO dell'app; il copione si
    imposta nel test (`sdk.copione = [...]`)."""
    finto = SdkFinto([])
    creati: list = []

    def crea_client(api_key, modello_ai, timeout):
        assert api_key == CHIAVE
        client = AiCheckClient(SimpleNamespace(anthropic_api_key="", ai_check_model=modello_ai,
                                               ai_check_timeout_seconds=timeout))
        client._client = finto  # il percorso di genera resta quello vero
        creati.append(client)
        return client

    monkeypatch.setattr(sonda, "crea_client", crea_client)
    finto.creati = creati
    return finto


def esiti(nomi=("estrazione", "bozza_profilo", "posizioni", "testi"), max_tokens=64):
    voci = [v for v in sonda.schemi_del_modulo() if v.nome in nomi]
    return sonda.prepara(voci, MODELLO_DEFAULT, max_tokens)


async def sonda_con(sdk, lista, **kwargs):
    ai = sonda.crea_client(CHIAVE, MODELLO_DEFAULT, 60.0)
    kwargs.setdefault("tetto_cents", sonda.TETTO_CENTS)
    fermato = await sonda.esegui_sonde(ai, lista, modello_ai=MODELLO_DEFAULT, max_tokens=64,
                                       timeout=60.0, **kwargs)
    return fermato


# ------------------------------------------------------------ senza conferma


def test_senza_conferma_nessuna_chiamata(capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # senza --conferma non serve
    assert sonda.main([]) == 0
    out = capsys.readouterr().out
    for nome in ("estrazione", "bozza_profilo", "posizioni", "testi"):
        assert f"[{nome}]" in out
    assert "schema inviato:" in out and "byte" in out
    assert "Nessuna chiamata" in out and "RIFIUTATO" not in out
    # il modo di ciascuno schema: l'estrazione WP3 come strumento non strict
    assert out.count("modo: strumento forzato non strict") == 1
    assert out.count("modo: output strutturato strict") == 3
    blocco_estrazione = out.split("[estrazione]", 1)[1].split("[bozza_profilo]", 1)[0]
    assert "modo: strumento forzato non strict" in blocco_estrazione


def test_schemi_importati_dal_codice():
    from app.schemas.partenariato import PartenariatoEstrazione
    from app.schemas.partner_profile import BozzaProfiloAi
    from app.services.partner_call_prompts import BozzaTestiCall, PropostaPosizioni

    assert [v.modello for v in sonda.schemi_del_modulo()] == [
        PartenariatoEstrazione, BozzaProfiloAi, PropostaPosizioni, BozzaTestiCall]
    # l'estrazione con lo stesso strumento e la stessa convalida della produzione
    from app.schemas.partenariato import convalida_tollerante
    from app.services.partenariato_prompts import (
        DESCRIZIONE_STRUMENTO_ESTRAZIONE,
        STRUMENTO_ESTRAZIONE,
    )

    estrazione, *altri = sonda.schemi_del_modulo()
    assert estrazione.strumento == {"nome_strumento": STRUMENTO_ESTRAZIONE,
                                    "descrizione": DESCRIZIONE_STRUMENTO_ESTRAZIONE,
                                    "convalida": convalida_tollerante}
    assert all(v.strumento is None for v in altri)
    for e in esiti():
        # la riserva conta lo schema due volte e il margine
        assert e.riserva_cents >= costo_cents(
            MODELLO_DEFAULT, 2 * e.byte // 3, 64) + sonda.MARGINE_RISERVA_CENTS


class _Voce(BaseModel):
    tipo: Literal["a", "b", "c"]
    nota: str | None


class _Prova(BaseModel):
    voci: list[_Voce]
    principale: _Voce | None
    etichette: list[str]


def test_struttura_dello_schema():
    # indipendente dagli schemi del modulo, che cambiano
    s = sonda.struttura(sonda.schema_inviato(_Prova))
    assert (s["oggetti"], s["proprieta"], s["defs"]) == (2, 5, 1)
    assert (s["enum"], s["valori_enum"], s["array"]) == (1, 3, 2)
    assert (s["anyOf"], s["nullable"], s["rami_anyOf"]) == (2, 2, 4)
    assert s["ref"] == 2


# ------------------------------------------------------------ classificazione


async def test_classificazione(sdk):
    sdk.copione = [
        errore_http(400, "invalid_request_error", GRAMMATICA),
        errore_http(400, "invalid_request_error", "max_tokens: field required"),
        TRONCATA,
        risposta(TESTI_OK),
    ]
    lista = esiti()
    assert await sonda_con(sdk, lista) is None
    estrazione, profilo, posizioni, testi = lista
    assert [e.esito for e in lista] == ["RIFIUTATO", "ERRORE", "ACCETTATO", "ACCETTATO"]
    assert estrazione.stato_http == 400 and "grammar is too large" in estrazione.dettaglio
    assert "invalid_request_error" in estrazione.dettaglio
    # 400 senza usage: contata la riserva, probabile 0
    assert estrazione.costo_cents == estrazione.riserva_cents
    assert estrazione.costo_probabile_cents == 0 and estrazione.input_tokens is None
    assert profilo.stato_http == 400 and "field required" in profilo.dettaglio
    assert posizioni.dettaglio.startswith("risposta arrivata")
    assert posizioni.costo_cents == costo_cents(MODELLO_DEFAULT, 1_500, 64)
    assert (posizioni.input_tokens, posizioni.output_tokens) == (1_500, 64)
    assert posizioni.costo_probabile_cents is None
    assert testi.dettaglio == "risposta conforme allo schema"
    assert testi.costo_cents == costo_cents(MODELLO_DEFAULT, 900, 20)


async def test_richiesta_minima_con_lo_stesso_formato(sdk):
    from app.clients.anthropic_ai import definizione_strumento
    from app.services.partenariato_prompts import (
        DESCRIZIONE_STRUMENTO_ESTRAZIONE,
        STRUMENTO_ESTRAZIONE,
    )

    sdk.copione = [risposta_strumento({"modalita": None})] + [risposta(TESTI_OK)] * 3
    lista = esiti()
    await sonda_con(sdk, lista)
    assert len(sdk.richieste) == 4 and sdk.timeout == [60.0] * 4
    for e, richiesta in zip(lista, sdk.richieste, strict=True):
        assert richiesta["model"] == MODELLO_DEFAULT and richiesta["max_tokens"] == 64
        assert richiesta["system"] == "Rispondi con un JSON conforme allo schema con valori vuoti"
        assert richiesta["messages"] == [{"role": "user", "content": "prova"}]
    estrazione, *strict = zip(lista, sdk.richieste, strict=True)
    # estrazione WP3: strumento forzato NON strict, nessun output_config
    e, richiesta = estrazione
    assert set(richiesta) == {"model", "max_tokens", "system", "messages", "tools",
                              "tool_choice"}
    [strumento] = richiesta["tools"]
    assert strumento == definizione_strumento(
        e.voce.modello, STRUMENTO_ESTRAZIONE, DESCRIZIONE_STRUMENTO_ESTRAZIONE)
    assert "strict" not in strumento
    assert richiesta["tool_choice"] == {"type": "tool", "name": STRUMENTO_ESTRAZIONE,
                                        "disable_parallel_tool_use": True}
    # lo schema inviato è quello misurato dalla sonda
    assert strumento["input_schema"] == sonda.schema_inviato(e.voce.modello)
    assert sonda.byte_schema(strumento["input_schema"]) == e.byte
    # {} con la convalida tollerante è un'estrazione vuota valida
    assert e.esito == "ACCETTATO" and e.dettaglio == "risposta conforme allo schema"
    # WP4/WP5: output strutturato strict, come prima
    for e, richiesta in strict:
        formato = richiesta["output_config"]["format"]
        assert formato["type"] == "json_schema"
        assert formato["schema"] == sonda.schema_inviato(e.voce.modello)
        assert sonda.byte_schema(formato["schema"]) == e.byte
        assert set(richiesta) == {"model", "max_tokens", "system", "messages", "output_config"}


async def test_estrazione_troncata_accettata_con_l_usage(sdk):
    """Con 64 token l'input dello strumento arriva troncato: lo schema è
    accettato (nessun 400) e il costo è quello dell'usage."""
    sdk.copione = [risposta_strumento({"modalita": "non_det"}, stop_reason="max_tokens",
                                      tok_in=4_000, tok_out=64)]
    lista = esiti(("estrazione",))
    await sonda_con(sdk, lista)
    [e] = lista
    assert e.esito == "ACCETTATO" and e.dettaglio.startswith("risposta arrivata")
    assert e.costo_cents == costo_cents(MODELLO_DEFAULT, 4_000, 64)


async def test_timeout_ed_errore_di_rete(sdk):
    sdk.copione = [anthropic.APITimeoutError(request=_RICHIESTA),
                   anthropic.APIConnectionError(request=_RICHIESTA)]
    lista = esiti(("estrazione", "testi"))
    await sonda_con(sdk, lista)
    timeout, rete = lista
    assert timeout.esito == "IGNOTO" and timeout.costo_cents == timeout.riserva_cents
    assert rete.esito == "ERRORE" and rete.stato_http is None
    assert rete.dettaglio.startswith("nessuna risposta HTTP")
    assert rete.costo_cents == rete.riserva_cents and rete.costo_probabile_cents is None


async def test_chiave_sbagliata_ferma_le_altre(sdk):
    sdk.copione = [errore_http(401, "authentication_error", "invalid x-api-key")]
    lista = esiti()
    fermato = await sonda_con(sdk, lista)
    assert "HTTP 401" in fermato and len(sdk.richieste) == 1
    assert [e.esito for e in lista] == ["ERRORE"] + ["NON_PROVATO"] * 3
    assert all(e.chiamata is False for e in lista[1:])
    # 4xx non transitorio senza usage: riserva contata, probabile 0 (come in
    # produzione e nella valutazione locale, non solo per il 400)
    assert lista[0].costo_cents == lista[0].riserva_cents
    assert lista[0].costo_probabile_cents == 0


@pytest.mark.parametrize(("stato", "tipo"), [(408, "timeout_error"), (409, "conflict_error"),
                                             (429, "rate_limit_error"), (500, "api_error"),
                                             (529, "overloaded_error")])
async def test_errori_transitori_senza_probabile_zero(sdk, stato, tipo):
    sdk.copione = [errore_http(stato, tipo, "riprova")]
    lista = esiti(("testi",))
    await sonda_con(sdk, lista)
    [e] = lista
    assert e.esito == "ERRORE" and e.stato_http == stato
    assert e.costo_cents == e.riserva_cents and e.costo_probabile_cents is None


# ------------------------------------------------------------ tetto


async def test_tetto_fail_closed(sdk):
    sdk.copione = [errore_http(400, "invalid_request_error", GRAMMATICA)]
    lista = esiti(("estrazione", "testi"))
    primo, secondo = lista
    # la seconda riserva ci starebbe solo se il 400 costasse il probabile 0
    tetto = primo.riserva_cents + secondo.riserva_cents - 1
    fermato = await sonda_con(sdk, lista, tetto_cents=tetto)
    assert len(sdk.richieste) == 1 and fermato.startswith(f"tetto di {tetto} cent")
    assert (primo.esito, secondo.esito) == ("RIFIUTATO", "NON_PROVATO")


async def test_tetto_vale_il_costo_reale_se_noto(sdk):
    sdk.copione = [risposta("{}", tok_in=10, tok_out=1), risposta(TESTI_OK)]
    lista = esiti(("estrazione", "testi"))
    primo, secondo = lista
    await sonda_con(sdk, lista, tetto_cents=primo.riserva_cents + secondo.riserva_cents - 1)
    assert len(sdk.richieste) == 2 and secondo.esito == "ACCETTATO"


async def test_riserva_oltre_il_tetto_nessuna_chiamata(sdk):
    lista = esiti(("estrazione",))
    fermato = await sonda_con(sdk, lista, tetto_cents=lista[0].riserva_cents - 1)
    assert sdk.richieste == [] and fermato and lista[0].esito == "NON_PROVATO"


def test_tetto_interno_di_20_centesimi():
    assert sonda.TETTO_CENTS == 20
    assert sonda.esegui_sonde.__kwdefaults__["tetto_cents"] == 20
    # anche col massimo di max_tokens, tutte le riserve insieme stanno nel tetto
    assert sum(e.riserva_cents for e in esiti(max_tokens=sonda.MAX_TOKENS_MASSIMO)) <= 20


# ------------------------------------------------------------ CLI


def test_cli_con_conferma(sdk, capsys):
    sdk.copione = [errore_http(400, "invalid_request_error", GRAMMATICA),
                   risposta("{}"), TRONCATA, risposta(TESTI_OK)]
    assert sonda.main(["--conferma"]) == 1  # uno schema rifiutato
    catturato = capsys.readouterr()
    out = catturato.out
    assert "modo: strumento forzato non strict" in out
    assert "esito: RIFIUTATO — HTTP 400 invalid_request_error: The compiled grammar" in out
    assert "probabile 0: richiesta rifiutata prima della generazione" in out
    assert "Riepilogo: estrazione RIFIUTATO; bozza_profilo ACCETTATO; posizioni ACCETTATO; " \
           "testi ACCETTATO" in out
    assert "tetto di 20" in out and "chiamate 4" in out
    assert CHIAVE not in out + catturato.err
    assert sdk.chiuso is True


def test_cli_tutti_accettati(sdk, capsys):
    sdk.copione = [risposta(TESTI_OK)]
    assert sonda.main(["--conferma", "--solo", "testi"]) == 0
    assert len(sdk.richieste) == 1


def test_cli_tetto_superato(sdk, capsys, monkeypatch):
    monkeypatch.setattr(sonda, "TETTO_CENTS", 1)
    assert sonda.main(["--conferma"]) == 1
    assert sdk.richieste == [] and "fermata: tetto di 1 cent" in capsys.readouterr().out


def test_chiave_solo_dall_ambiente(capsys, monkeypatch):
    # nel .env c'è, ma la sonda la vuole dall'ambiente: nessun client, codice 2
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with open(".env", "w", encoding="utf-8") as f:
        f.write(f"ANTHROPIC_API_KEY={CHIAVE}\nPARTENARIATO_AI_MODEL=claude-haiku-4-5\n")
    assert sonda.main(["--conferma"]) == 2
    catturato = capsys.readouterr()
    assert "ANTHROPIC_API_KEY mancante" in catturato.err
    assert CHIAVE not in catturato.out + catturato.err
    # il modello invece si risolve come nelle Settings, .env compreso
    assert "modello claude-haiku-4-5" in catturato.out


def test_modello(sdk, capsys, monkeypatch):
    assert sonda.modello_dalle_settings() == MODELLO_DEFAULT
    monkeypatch.setenv("PARTENARIATO_AI_MODEL", "claude-opus-5")
    assert sonda.modello_dalle_settings() == "claude-opus-5"
    sdk.copione = [risposta(TESTI_OK)]
    assert sonda.main(["--conferma", "--solo", "testi", "--model", "claude-haiku-4-5"]) == 0
    assert sdk.richieste[0]["model"] == "claude-haiku-4-5"


@pytest.mark.parametrize("argomenti", [["--solo", "ignoto"], ["--solo", ","],
                                       ["--max-tokens", "0"], ["--max-tokens", "5000"],
                                       ["--timeout", "0"]])
def test_argomenti_non_validi(sdk, argomenti):
    assert sonda.main(["--conferma", *argomenti]) == 2
    assert sdk.richieste == [] and sdk.creati == []
