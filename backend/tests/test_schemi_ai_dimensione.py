"""Budget di dimensione degli schemi di output strutturato dei servizi AI.

Perché: l'API compila lo schema di `output_config.format` in una grammatica e
rifiuta quelle troppo grandi con HTTP 400 `invalid_request_error` «The
compiled grammar is too large, which would cause performance issues. Simplify
your tool schemas or reduce the number of strict tools.». La prima
valutazione reale del WP3 (25 bandi, claude-sonnet-5) è fallita così su TUTTE
le chiamate con lo schema v1 di `PartenariatoEstrazione`: 9465 byte, 19 enum
con 187 valori, 21 campi nullable (`anyOf` con null). La sonda reale del
2026-09-29 (`scripts/sonda_schemi_ai.py`) ha rifiutato allo stesso modo anche
la v2 (6456 byte, 62 proprietà, nessun anyOf, 5 enum con 20 valori) e
accettato gli schemi WP4/WP5.

Per questo il budget STRICT qui sotto vale per gli schemi che restano in
output strutturato strict (WP4, WP5 e il riferimento AI-check), mentre
l'estrazione WP3 non usa più `output_config.format`: va come schema di uno
strumento forzato NON strict (`AiCheckClient.estrai_con_strumento`, nessuna
grammatica). Per lei resta solo un tetto più largo, come guardia contro la
crescita incontrollata dello schema (e dei token che costa a ogni chiamata).

La documentazione non dà limiti numerici (dice solo che schemi ricorsivi,
vincoli numerici e di stringa non sono supportati e che `anyOf`/enum grandi
pesano). Il riferimento è quindi l'AI-check (`ExtractionResult`,
`MatchingResult`), che in produzione funziona: circa 2,4 KB, al massimo 3
enum con 19 valori, 4 nullable, oggetti annidati su 3 livelli. Il budget
comune qui sotto è quell'inviluppo con un piccolo margine; i tetti di byte e
proprietà sono per schema (vedi `TETTI`).

Si misura lo schema ESATTO inviato: quello che `AiCheckClient.genera` mette in
`output_config.format.schema` (`anthropic.transform_schema` sullo schema
Pydantic, la stessa trasformazione di `messages.parse` usata dall'AI-check), che
è anche l'`input_schema` dello strumento di `estrai_con_strumento`. Nessuna
chiamata reale: il client dell'SDK è finto.
"""

import json
from types import SimpleNamespace
from typing import Literal

import anthropic
import pytest
from pydantic import BaseModel, TypeAdapter

from app.clients.anthropic_ai import AiCheckClient, definizione_strumento
from app.core.errors import AiUpstreamError
from app.schemas.ai_check import ExtractionResult, MatchingResult
from app.schemas.partenariato import PartenariatoEstrazione, convalida_tollerante
from app.services import partenariato_service as ps
from app.schemas.partner_profile import BozzaProfiloAi
from app.services.partner_call_prompts import BozzaTestiCall, PropostaPosizioni


def schema_inviato(tipo) -> dict:
    """Lo schema come arriva all'API (vedi `AiCheckClient.genera`)."""
    return anthropic.transform_schema(TypeAdapter(tipo).json_schema())


def metriche(schema: dict) -> dict[str, int]:
    """Metriche che pesano sulla grammatica: enum e loro valori, `anyOf`
    (nullable compresi), profondità degli oggetti annidati ($ref risolti),
    proprietà e byte del JSON compatto."""
    definizioni = schema.get("$defs", {})
    conta = {"byte": len(json.dumps(schema, ensure_ascii=False, separators=(",", ":"))),
             "enum": 0, "valori_enum": 0, "anyOf": 0, "nullable": 0, "oggetti": 0,
             "proprieta": 0}

    def visita(nodo):
        if isinstance(nodo, dict):
            if "enum" in nodo:
                conta["enum"] += 1
                conta["valori_enum"] += len(nodo["enum"])
            if "anyOf" in nodo:
                conta["anyOf"] += 1
                if any(isinstance(v, dict) and v.get("type") == "null" for v in nodo["anyOf"]):
                    conta["nullable"] += 1
            if nodo.get("type") == "object":
                conta["oggetti"] += 1
                conta["proprieta"] += len(nodo.get("properties", {}))
            for valore in nodo.values():
                visita(valore)
        elif isinstance(nodo, list):
            for valore in nodo:
                visita(valore)

    def profondita(nodo, visti: tuple[str, ...] = ()) -> int:
        if not isinstance(nodo, dict):
            return 0
        rif = nodo.get("$ref")
        if rif:
            nome = rif.rsplit("/", 1)[-1]
            assert nome not in visti, f"schema ricorsivo: {nome}"  # non supportati
            return profondita(definizioni[nome], (*visti, nome))
        figli = [*nodo.get("properties", {}).values(), *nodo.get("anyOf", [])]
        if "items" in nodo:
            figli.append(nodo["items"])
        sotto = max((profondita(f, visti) for f in figli), default=0)
        return sotto + (1 if nodo.get("type") == "object" else 0)

    visita(schema)
    conta["profondita"] = profondita(schema)
    return conta


# Inviluppo dell'AI-check (in produzione) con un piccolo margine: sono le
# metriche che fanno crescere la grammatica per combinazione (alternative di
# enum e di anyOf) o per annidamento.
BUDGET_COMUNE = {"anyOf": 4, "enum": 6, "valori_enum": 24, "profondita": 3}

# Tetti su byte (JSON compatto, titoli di Pydantic compresi) e proprietà, che
# crescono in modo LINEARE con i campi obbligatori (ordine fisso, nessuna
# combinazione). Strict (WP4/WP5): come l'AI-check (2,4 KB, 20 proprietà).
TETTO_AI_CHECK = {"byte": 2500, "proprieta": 24}

# Gli schemi che restano in output strutturato strict (`genera`).
SCHEMI_STRICT = [BozzaProfiloAi, PropostaPosizioni, BozzaTestiCall]

# WP3, strumento NON strict: nessuna grammatica, quindi nessun budget comune;
# solo una guardia contro la crescita incontrollata, circa un terzo sopra il
# valore attuale (5976 byte, 62 proprietà: 62 campi obbligatori, tre volte
# l'AI-check) e senza schemi ricorsivi (`metriche` li rifiuta).
TETTO_STRUMENTO_WP3 = {"byte": 8000, "proprieta": 80}


def _fuori_budget(misure: dict[str, int], tetti=None) -> dict[str, tuple[int, int]]:
    """Le metriche oltre il budget strict (comune + tetti dell'AI-check)."""
    limiti = {**BUDGET_COMUNE, **(TETTO_AI_CHECK if tetti is None else tetti)}
    return {chiave: (misure[chiave], tetto) for chiave, tetto in limiti.items()
            if misure[chiave] > tetto}


# ------------------------------------------------------------ misura


class TestMisura:
    def test_conta_enum_nullable_profondita(self):
        class Foglia(BaseModel):
            x: Literal["a", "b", "c"]

        class Voce(BaseModel):
            foglia: Foglia
            nota: str | None

        class Radice(BaseModel):
            voci: list[Voce]
            n: int | None

        misure = metriche(schema_inviato(Radice))
        assert (misure["enum"], misure["valori_enum"]) == (1, 3)
        assert misure["anyOf"] == misure["nullable"] == 2
        assert (misure["oggetti"], misure["proprieta"]) == (3, 5)
        assert misure["profondita"] == 3

    @pytest.mark.parametrize("tipo", SCHEMI_STRICT, ids=lambda t: t.__name__)
    async def test_e_lo_schema_che_genera_invia(self, tipo):
        """Si misura proprio ciò che parte: `output_config.format.schema`."""
        inviate: list[dict] = []

        class Messaggi:
            async def create(self, **kwargs):
                inviate.append(kwargs)
                return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)

        client = AiCheckClient(SimpleNamespace(
            anthropic_api_key="", ai_check_model="claude-test", ai_check_timeout_seconds=1.0))
        client._client = SimpleNamespace(messages=Messaggi())
        with pytest.raises(AiUpstreamError):  # risposta vuota: conta solo la richiesta
            await client.genera("SYS", "TESTO", tipo)
        [richiesta] = inviate
        assert richiesta["output_config"]["format"] == {
            "type": "json_schema", "schema": schema_inviato(tipo)}

    async def test_estrazione_wp3_non_usa_output_config_format(self):
        """La pipeline WP3 (`genera_estrazione`, la stessa della valutazione
        locale) invia lo schema come strumento forzato NON strict: nessun
        `output_config`, nessuno `strict`, stesso schema misurato qui."""
        inviate: list[dict] = []

        class Messaggi:
            async def create(self, **kwargs):
                inviate.append(kwargs)
                return SimpleNamespace(content=[], stop_reason="end_turn", usage=None)

        class Sdk:
            messages = Messaggi()

            def with_options(self, **kwargs):
                return self

        client = AiCheckClient(SimpleNamespace(
            anthropic_api_key="", ai_check_model="claude-test", ai_check_timeout_seconds=1.0))
        client._client = Sdk()
        settings = SimpleNamespace(partenariato_ai_model="claude-test",
                                   partenariato_ai_max_tokens=16000,
                                   partenariato_ai_timeout_seconds=180.0)
        with pytest.raises(AiUpstreamError):  # risposta vuota: conta solo la richiesta
            await ps.genera_estrazione(client, "TESTO", settings=settings)
        [richiesta] = inviate
        assert "output_config" not in richiesta
        [strumento] = richiesta["tools"]
        assert "strict" not in strumento
        assert strumento["input_schema"] == schema_inviato(PartenariatoEstrazione)
        assert strumento == definizione_strumento(
            PartenariatoEstrazione, ps.STRUMENTO_ESTRAZIONE, ps.DESCRIZIONE_STRUMENTO_ESTRAZIONE)
        assert richiesta["tool_choice"] == {"type": "tool", "name": ps.STRUMENTO_ESTRAZIONE,
                                            "disable_parallel_tool_use": True}
        # e la stima dei costi conta proprio lo strumento inviato
        assert json.loads(ps._schema_json()) == strumento
        assert convalida_tollerante({"modalita": None}).modalita == "non_determinabile"


# ------------------------------------------------------------ budget


class TestBudget:
    @pytest.mark.parametrize("tipo", [ExtractionResult, MatchingResult],
                             ids=lambda t: t.__name__)
    def test_riferimento_ai_check_nel_budget(self, tipo):
        """Il budget comprende ciò che funziona in produzione."""
        misure = metriche(schema_inviato(tipo))
        limiti = {**BUDGET_COMUNE, **TETTO_AI_CHECK}
        assert all(misure[k] <= v for k, v in limiti.items()), misure

    @pytest.mark.parametrize("tipo", SCHEMI_STRICT, ids=lambda t: t.__name__)
    def test_schemi_strict_nel_budget(self, tipo):
        misure = metriche(schema_inviato(tipo))
        assert _fuori_budget(misure) == {}, misure

    def test_estrazione_wp3_nel_tetto_dello_strumento(self):
        """Guardia larga: lo strumento non compila una grammatica, ma ogni
        byte dello schema si paga come input a ogni chiamata."""
        misure = metriche(schema_inviato(PartenariatoEstrazione))
        limiti = TETTO_STRUMENTO_WP3
        assert all(misure[k] <= v for k, v in limiti.items()), misure

    def test_estrazione_wp3_senza_nullable(self):
        """WP3: assenza = "" / lista vuota / citazione vuota, mai null."""
        misure = metriche(schema_inviato(PartenariatoEstrazione))
        assert misure["anyOf"] == misure["nullable"] == 0

    def test_versioni_wp3_rifiutate_sarebbero_fuori_dal_budget_strict(self):
        """La v1 (400 su tutte le chiamate) e la v2 attuale (400 alla sonda
        del 2026-09-29) violano il budget strict: per questo l'estrazione non
        usa l'output strutturato strict."""
        v1 = {"byte": 9465, "enum": 19, "valori_enum": 187, "anyOf": 21, "nullable": 21,
              "oggetti": 8, "proprieta": 62, "profondita": 3}
        assert set(_fuori_budget(v1)) == {"byte", "proprieta", "enum", "valori_enum", "anyOf"}
        v2 = metriche(schema_inviato(PartenariatoEstrazione))
        assert set(_fuori_budget(v2)) == {"byte", "proprieta"}


# ------------------------------------------------ codici usciti dallo schema


class TestCodiciFuoriVocabolario:
    """I codici che lo schema non vincola più (stringhe) li filtra la
    post-elaborazione: fuori vocabolario → scartati, mai un'eccezione. WP3:
    tests/test_partenariato_regole.py; WP4: test_partner_profile_schemas.py."""

    def test_posizioni_wp5(self):
        from app.services import partner_call_ai as pca
        from app.services.partner_call_prompts import PosizioneAi

        proposta = PropostaPosizioni(posizioni=[PosizioneAi(
            titolo="Partner tecnologico", ruolo="partner",
            tipi_soggetto=["pmi", "impresa inventata", "universita"],
            competenze=["codice_ignoto", "sviluppo_software"], ateco_divisioni=[],
            regioni=[], territorio_modalita="qualsiasi", paesi=[], dimensioni=[],
            quota_ipotizzata_pct=None, numero=1, requisiti=[], motivazione="m",
        )])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="capofila", quota_creatore=None, regole=None)
        [posizione] = out["posizioni"]
        assert posizione["tipi_soggetto"] == ["pmi", "universita"]
        assert posizione["competenze"] == ["sviluppo_software"]

    def test_posizioni_wp5_scarto_segnalato(self):
        """Una lista vuota vuol dire «nessun vincolo»: se lo scarto la svuota
        la posizione si allarga, e il creatore deve saperlo."""
        from app.services import partner_call_ai as pca
        from app.services.partner_call_prompts import PosizioneAi

        proposta = PropostaPosizioni(posizioni=[PosizioneAi(
            titolo="Partner di ricerca", ruolo="partner",
            tipi_soggetto=["Università", ""], competenze=["Ricerca e sviluppo"],
            ateco_divisioni=[], regioni=[], territorio_modalita="qualsiasi", paesi=[],
            dimensioni=[], quota_ipotizzata_pct=None, numero=1, requisiti=[],
            motivazione="m",
        )])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="capofila", quota_creatore=None, regole=None)
        [posizione] = out["posizioni"]
        assert (posizione["tipi_soggetto"], posizione["competenze"]) == ([], [])
        avvisi = " ".join(out["avvisi"])
        assert "1 tipi di soggetto non riconosciuti sono stati scartati" in avvisi
        assert "1 competenze non riconosciute sono state scartate" in avvisi

    def test_posizioni_wp5_codici_validi_senza_avvisi(self):
        from app.services import partner_call_ai as pca
        from app.services.partner_call_prompts import PosizioneAi

        proposta = PropostaPosizioni(posizioni=[PosizioneAi(
            titolo="Partner di ricerca", ruolo="partner",
            tipi_soggetto=["universita"], competenze=["ricerca_industriale"],
            ateco_divisioni=[], regioni=[], territorio_modalita="qualsiasi", paesi=[],
            dimensioni=[], quota_ipotizzata_pct=None, numero=1, requisiti=[],
            motivazione="m",
        )])
        out = pca.post_posizioni(proposta, etichette={}, regioni={}, ident=None,
                                 ruolo_creatore="capofila", quota_creatore=None, regole=None)
        assert out["avvisi"] == []
