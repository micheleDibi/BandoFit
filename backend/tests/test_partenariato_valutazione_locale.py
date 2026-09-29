"""Valutazione LOCALE delle regole di partenariato (WP3): `--reale --locale`
senza DB primario, con la spesa fail-closed in memoria.

Nessuna rete e MAI Anthropic: catalogo, download, lettura dei PDF e modello
sono finti, e `AiCheckClient.genera`/`estrai_con_strumento` veri sollevano. La cartella corrente è
temporanea: il `.env` del backend non si legge mai. Coperti: tetto rispettato
(ci si ferma prima di superarlo, una chiamata alla volta), riserva su timeout,
max(reale, riserva) su errore con usage, niente chiamate senza `--conferma`,
primario vietato (anche la strada globale `get_settings`, che in tutti i test
solleva), `--out` nel repository rifiutato, chiave mancante prima dei
download, fedeltà a `_pipeline` sui finti di test_partenariato_service,
`--solo`, `--locale` senza `--reale`, errori fuori dalle metriche di campo,
ripresa col tetto residuo e unione delle esecuzioni, arresto al primo errore
non transitorio del provider (400 compreso, col costo probabile 0) e dopo tre
errori di fila, input grezzo dello strumento salvato e `--rivaluta` (stesse
metriche a codice invariato, nessuna chiamata, nessun download)."""

import asyncio
import base64
import copy
import inspect
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.clients.anthropic_ai import AiUsage
from app.core.config import get_settings
from app.core.errors import AiTimeoutError, AiUpstreamError
from app.services import partenariato_service as ps
from app.services import partenariato_valutazione as val
from app.services import partenariato_valutazione_locale as loc
from app.services.ai_prezzi import costo_cents
from app.services.bando_fonti_service import LinkDocumento
from app.services.download_sicuro import DocumentoScaricato
from app.services.pdf_testo import TestoPdf as _TestoPdf
from tests import test_partenariato_service as servizio
from tests.test_partenariato_service import (
    BANDO_ID,
    MODELLO,
    NEUTRA_1,
    NEUTRA_2,
    PAGINA_1,
    PAGINA_2,
    FakeAi,
    FakeDb,
    bando,
)
from tests.test_partenariato_service import FakeSecondary as FakeSecondaryServizio
from tests.test_partenariato_valutazione import FakeSecondary

ANON = "anon-finta-del-catalogo"
catalogo = servizio.catalogo  # fixture: gli stessi finti della pipeline di produzione
REALE = costo_cents(MODELLO, 30_000, 6_000)  # costo dell'usage di FakeAi
_GET_SETTINGS = get_settings


def _moduli_app():
    return [m for n, m in list(sys.modules.items()) if n == "app" or n.startswith("app.")]


@contextmanager
def senza_get_settings(chiamate: list):
    """Il percorso locale non passa MAI dalle Settings globali (leggerebbero
    la configurazione del primario): `get_settings` di ogni modulo dell'app
    solleva e si registra in `chiamate`. Un modulo importato nel frattempo la
    prende da `app.core.config` (sostituita anche lei) e si ripristina
    all'uscita."""

    def vietata():
        chiamate.append("get_settings")
        raise loc.PrimarioVietatoError("get_settings() nella valutazione locale")

    vietata.cache_clear = lambda: None
    with pytest.MonkeyPatch.context() as mp:
        for modulo in _moduli_app():
            if getattr(modulo, "get_settings", None) is _GET_SETTINGS:
                mp.setattr(modulo, "get_settings", vietata)
        try:
            yield
        finally:
            for modulo in _moduli_app():
                if getattr(modulo, "get_settings", None) is vietata:
                    modulo.get_settings = _GET_SETTINGS


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path, request):
    cartella = tmp_path / "cwd"
    cartella.mkdir()
    # Il «.env» delle Settings è relativo: mai quello vero del backend.
    monkeypatch.chdir(cartella)
    for chiave in ("SECONDARY_SUPABASE_URL", "SECONDARY_SUPABASE_ANON_KEY", "ENV",
                   "PARTENARIATO_AI_MAX_TOKENS", "PARTENARIATO_AI_TIMEOUT_SECONDS"):
        monkeypatch.delenv(chiave, raising=False)
    for chiave, valore in {
        # presenti nell'ambiente, ma la modalità locale non li usa mai
        "PRIMARY_SUPABASE_URL": "https://primario-vero.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "service-role-vera",
        "SUPABASE_URL_BANDI": "https://bandi.example.co",
        "PUBLIC_SUPABASE_BANDI_ANON_KEY": ANON,
        "ANTHROPIC_API_KEY": "sk-ant-finta",
        "PARTENARIATO_AI_MODEL": MODELLO,
    }.items():
        monkeypatch.setenv(chiave, valore)
    get_settings.cache_clear()

    async def mai_anthropic(*a, **k):
        raise AssertionError("nei test non si chiama MAI Anthropic")

    async def mai_primario(*a, **k):
        raise AssertionError("la valutazione locale non crea il primario")

    monkeypatch.setattr("app.clients.anthropic_ai.AiCheckClient.genera", mai_anthropic)
    monkeypatch.setattr("app.clients.anthropic_ai.AiCheckClient.estrai_con_strumento",
                        mai_anthropic)
    monkeypatch.setattr(val, "_crea_primario_e_ai", mai_primario)
    chiamate_get_settings: list = []
    # TestFedelta la vieta solo attorno al percorso locale (la produzione la usa).
    if getattr(request.cls, "usa_la_produzione", False):
        yield chiamate_get_settings
    else:
        with senza_get_settings(chiamate_get_settings):
            yield chiamate_get_settings
    get_settings.cache_clear()
    assert chiamate_get_settings == [], "la valutazione locale ha chiamato get_settings()"


class AiFinta(FakeAi):
    """FakeAi con `aclose`, errori per chiamata e conteggio delle chiamate
    contemporanee."""

    def __init__(self, errori: list | None = None):
        super().__init__()
        self.errori = list(errori or [])
        self.in_volo = 0
        self.max_in_volo = 0
        self.chiuso = False

    async def estrai_con_strumento(self, *args, **kwargs):
        self.in_volo += 1
        self.max_in_volo = max(self.max_in_volo, self.in_volo)
        try:
            await asyncio.sleep(0)
            self.errore = self.errori.pop(0) if self.errori else None
            return await super().estrai_con_strumento(*args, **kwargs)
        finally:
            self.in_volo -= 1

    async def aclose(self):
        self.chiuso = True


class CatalogoLocale:
    """Tre bandi con un PDF ciascuno; quelli in `neutri` senza segnali."""

    def __init__(self):
        self.bandi = {}
        self.neutri: set[int] = set()
        self.download: list[str] = []
        self.secondari = 0
        for i in (1, 2, 3):
            self.aggiungi(i)

    def aggiungi(self, i: int, neutro: bool = False):
        self.bandi[i] = {**bando(pagine_con_segnali=not neutro), "id": i, "slug": f"bando-{i}"}
        if neutro:
            self.neutri.add(i)


@pytest.fixture
def cat(monkeypatch):
    stato = CatalogoLocale()

    async def crea_secondario(settings):
        stato.secondari += 1
        return FakeSecondary(list(stato.bandi.values()))

    async def fetch(secondary, slug):
        return next(b for b in stato.bandi.values() if b["slug"] == slug)

    async def link(secondary, bando_id):
        return [LinkDocumento(id=bando_id, bando_id=bando_id,
                              url=f"https://ente.example.it/{bando_id}.pdf",
                              dominio="ente.example.it", tipo="allegato", etichetta="Avviso",
                              content_type="application/pdf", ultimo_visto_at=None)]

    async def scarica(url, *, max_bytes, timeout_s, **k):
        stato.download.append(url)
        contenuto = b"%PDF-1.7 " + url.rsplit("/", 1)[1].encode()
        return DocumentoScaricato(stato="ok", sha256="x", byte=len(contenuto), contenuto=contenuto)

    async def estrai(pdf_bytes, *, max_pagine, timeout_s, **k):
        bando_id = int(pdf_bytes.split(b" ")[1].split(b".")[0])
        pagine = [(1, NEUTRA_1), (2, NEUTRA_2)] if bando_id in stato.neutri else [
            (1, PAGINA_1), (2, PAGINA_2)]
        return _TestoPdf(stato="letto", pagine_totali=2, pagine=pagine,
                         caratteri=sum(len(t) for _, t in pagine))

    async def lookups(secondary):
        return SimpleNamespace(regioni=[{"id": 1, "nome": "Piemonte"}])

    monkeypatch.setattr(loc, "crea_secondario", crea_secondario)
    monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
    monkeypatch.setattr("app.services.bando_fonti_service.leggi_link_documenti", link)
    monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
    monkeypatch.setattr("app.services.pdf_testo.estrai_testo", estrai)
    monkeypatch.setattr("app.services.lookup_service.get_lookups", lookups)
    return stato


ETICHETTE = {
    1: {"modalita": "ammesso", "partner_min": 2, "partner_max": None,
        "quote": [{"ambito": "per_partner", "categoria": None, "min": 20, "max": None}]},
    2: {"modalita": "ammesso", "partner_min": 2, "partner_max": 5, "quote": [],
        "non_raggiungibili": ["partner_max"]},
    3: {"modalita": "non_determinabile", "partner_min": None, "partner_max": None,
        "quote": []},
}


def scrivi_campione(tmp_path) -> str:
    percorso = tmp_path / "campione.json"
    percorso.write_text(json.dumps({"campione": [
        {"bando_id": i, "gruppo": "positivo" if i < 3 else "negativo", "etichetta": e}
        for i, e in ETICHETTE.items()
    ]}))
    return str(percorso)


def cli(tmp_path, *extra: str, tetto: str = "800") -> int:
    return val.main(["--reale", "--locale", "--tetto-cents", tetto,
                     "--campione", scrivi_campione(tmp_path), *extra])


async def preparati(cat, ids=(1, 2, 3)):
    settings = loc.impostazioni_locali()
    secondary = await loc.crea_secondario(settings)
    bandi = [await loc.prepara_bando(secondary, {"bando_id": i}, settings=settings)
             for i in ids]
    return settings, secondary, bandi


# ------------------------------------------------------------ spesa


class TestSpesa:
    def test_rifiuta_oltre_il_tetto(self):
        spesa = loc.Spesa(tetto_cents=100)
        assert spesa.prenota(60) is True
        assert spesa.prenota(41) is False  # 60 aperti + 41 > 100
        spesa.chiudi(60, 30)
        assert (spesa.speso_cents, spesa.riserve_aperte_cents) == (30, 0)
        assert spesa.prenota(70) is True  # 30 + 70 = 100: al limite
        assert spesa.prenota(1) is False
        assert spesa.prenota(0) is False and spesa.prenota(-5) is False  # fail-closed
        assert spesa.chiamate == 2

    async def test_si_ferma_prima_di_superare_il_tetto(self, cat):
        settings, secondary, bandi = await preparati(cat)
        riserva = bandi[0].riserva_cents
        assert all(b.esito is None and b.riserva_cents == riserva for b in bandi)
        assert 0 < REALE < riserva
        # 1ª: 0 + R; 2ª: C + R = tetto; 3ª: 2C + R > tetto → rifiutata
        spesa = loc.Spesa(tetto_cents=REALE + riserva)
        ai = AiFinta()
        valutazione = loc.ValutazioneLocale(bandi, spesa, settings)
        await valutazione.esegui(secondary, ai)
        assert len(ai.chiamate) == 2 and ai.max_in_volo == 1  # una alla volta
        assert [b.esito for b in bandi] == ["estratta", "estratta", "non_valutato"]
        assert valutazione.fermato == "tetto_raggiunto"
        assert spesa.speso_cents == 2 * REALE <= spesa.tetto_cents
        assert spesa.riserve_aperte_cents == 0
        risultato = valutazione.risultato()
        assert risultato["fermato"] == "tetto_raggiunto"
        assert risultato["esiti"] == {"estratta": 2, "non_valutato": 1}
        # i non valutati restano fuori dalle metriche
        assert risultato["metriche"]["costi"]["n"] == 2

    async def test_tetto_sotto_la_prima_riserva_nessuna_chiamata(self, cat):
        settings, secondary, bandi = await preparati(cat, ids=(1,))
        ai = AiFinta()
        valutazione = loc.ValutazioneLocale(
            bandi, loc.Spesa(tetto_cents=bandi[0].riserva_cents - 1), settings)
        await valutazione.esegui(secondary, ai)
        assert ai.chiamate == [] and bandi[0].esito == "non_valutato"
        assert valutazione.spesa.speso_cents == 0

    async def test_timeout_costa_la_riserva(self, cat):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        spesa = loc.Spesa(tetto_cents=1000)
        ok = await loc.valuta_con_modello(secondary, AiFinta([AiTimeoutError()]), b, spesa,
                                          settings=settings)
        assert ok is True
        assert (b.esito, b.errore_codice) == ("errore", "timeout")
        assert b.costo_cents == b.riserva_cents > 0 and b.costo_ignoto is True
        assert (spesa.speso_cents, spesa.riserve_aperte_cents) == (b.riserva_cents, 0)

    async def test_errore_senza_usage_costa_la_riserva(self, cat):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        spesa = loc.Spesa(tetto_cents=1000)
        await loc.valuta_con_modello(secondary, AiFinta([AiUpstreamError()]), b, spesa,
                                     settings=settings)
        assert b.errore_codice == "ai_non_disponibile"
        assert b.costo_cents == b.riserva_cents and b.costo_ignoto is True

    @pytest.mark.parametrize("usage", [AiUsage(1_000, 100), AiUsage(400_000, 16_000)])
    async def test_errore_con_usage_costa_max_reale_riserva(self, cat, usage):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        errore = AiUpstreamError("troncata")
        errore.usage = usage
        spesa = loc.Spesa(tetto_cents=1000)
        await loc.valuta_con_modello(secondary, AiFinta([errore]), b, spesa, settings=settings)
        atteso = max(costo_cents(MODELLO, usage.input_tokens, usage.output_tokens),
                     b.riserva_cents)
        assert b.errore_codice == "ai_risposta_non_valida"
        assert b.costo_cents == atteso == spesa.speso_cents and b.costo_ignoto is False
        assert (b.input_tokens, b.output_tokens) == (usage.input_tokens, usage.output_tokens)

    async def test_guasto_dopo_la_chiamata_costa_max_reale_riserva(self, cat, monkeypatch):
        settings, secondary, [b] = await preparati(cat, ids=(1,))

        def rotto(*a, **k):
            raise RuntimeError("post-elaborazione rotta")

        monkeypatch.setattr(ps, "post_elabora", rotto)
        spesa = loc.Spesa(tetto_cents=1000)
        await loc.valuta_con_modello(secondary, AiFinta(), b, spesa, settings=settings)
        assert (b.esito, b.errore_codice, b.regole) == ("errore", "errore_interno", None)
        assert b.costo_cents == max(REALE, b.riserva_cents) == spesa.speso_cents

    async def test_cancellazione_chiude_la_riserva(self, cat):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        spesa = loc.Spesa(tetto_cents=1000)
        with pytest.raises(asyncio.CancelledError):
            await loc.valuta_con_modello(secondary, AiFinta([asyncio.CancelledError()]), b,
                                         spesa, settings=settings)
        assert b.errore_codice == "interrotta" and b.costo_ignoto is True
        assert (spesa.speso_cents, spesa.riserve_aperte_cents) == (b.riserva_cents, 0)


# ------------------------------------------------------------ arresti

GRAMMATICA = ("The compiled grammar is too large, which would cause performance issues. "
              "Simplify your tool schemas or reduce the number of strict tools.")


def errore_provider(stato: int, tipo: str = "invalid_request_error",
                    messaggio: str = GRAMMATICA) -> AiUpstreamError:
    """Come `AiCheckClient.estrai_con_strumento`: l'AiUpstreamError nasce `from` l'errore
    di stato dell'SDK (qui quello vero, con la risposta HTTP finta)."""
    corpo = {"type": "error", "error": {"type": tipo, "message": messaggio}}
    risposta = httpx.Response(stato, request=httpx.Request(
        "POST", "https://api.anthropic.com/v1/messages"))
    errore = AiUpstreamError()
    errore.__cause__ = anthropic.APIStatusError(
        f"Error code: {stato} - {corpo}", response=risposta, body=corpo)
    return errore


async def esegui_con(cat, errori: list, ids=(1, 2, 3)):
    for i in ids:
        if i not in cat.bandi:
            cat.aggiungi(i)
    settings, secondary, bandi = await preparati(cat, ids=ids)
    ai = AiFinta(errori)
    valutazione = loc.ValutazioneLocale(bandi, loc.Spesa(tetto_cents=10_000), settings)
    await valutazione.esegui(secondary, ai)
    return valutazione, ai


class TestArresti:
    async def test_400_ferma_subito_e_costa_la_riserva(self, cat):
        valutazione, ai = await esegui_con(cat, [errore_provider(400)])
        primo = valutazione.bandi[0]
        assert len(ai.chiamate) == 1
        assert valutazione.fermato == "errore_non_transitorio"
        assert [b.esito for b in valutazione.bandi] == ["errore", "non_valutato", "non_valutato"]
        assert primo.errore_codice == "ai_richiesta_rifiutata"
        assert (primo.errore_http, primo.errore_tipo) == (400, "invalid_request_error")
        # fail-closed come prima: registrata la riserva, nessuna aperta
        assert primo.costo_cents == primo.riserva_cents > 0 and primo.costo_ignoto is True
        spesa = valutazione.spesa
        assert (spesa.speso_cents, spesa.riserve_aperte_cents, spesa.chiamate) == (
            primo.riserva_cents, 0, 1)
        risultato = valutazione.risultato()
        assert risultato["fermato"] == "errore_non_transitorio"
        uscita = risultato["bandi"][0]
        assert uscita["costo_cents"] == primo.riserva_cents
        assert uscita["costo_probabile_cents"] == 0
        assert uscita["nota_costo"] == "richiesta rifiutata prima della generazione"
        assert (uscita["errore_http"], uscita["errore_tipo"]) == (400, "invalid_request_error")
        assert GRAMMATICA not in json.dumps(risultato)  # mai il messaggio del provider
        assert risultato["spesa"]["speso_cents"] == primo.riserva_cents
        assert risultato["spesa"]["speso_probabile_cents"] == 0
        assert risultato["metriche"]["errori"]["per_codice"] == {"ai_richiesta_rifiutata": 1}
        # dopo la correzione si rifanno tutti, il rifiutato compreso
        assert loc.da_rifare(risultato) == [1, 2, 3]

    @pytest.mark.parametrize("stato,tipo", [(401, "authentication_error"),
                                            (403, "permission_error"),
                                            (404, "not_found_error"),
                                            (413, "request_too_large")])
    async def test_altri_4xx_non_transitori_fermano(self, cat, stato, tipo):
        valutazione, ai = await esegui_con(cat, [errore_provider(stato, tipo, "no")])
        assert len(ai.chiamate) == 1 and valutazione.fermato == "errore_non_transitorio"
        primo = valutazione.risultato()["bandi"][0]
        assert primo["errore_codice"] == "ai_richiesta_rifiutata"
        # come in produzione (costo 0 per ogni 4xx non transitorio): registrata
        # la riserva (fail-closed), probabile 0 con la sua nota
        assert primo["costo_cents"] == primo["riserva_cents"] > 0
        assert primo["costo_probabile_cents"] == 0
        assert primo["nota_costo"] == "richiesta rifiutata prima della generazione"
        risultato = valutazione.risultato()
        assert risultato["spesa"]["speso_cents"] == primo["riserva_cents"]
        assert risultato["spesa"]["speso_probabile_cents"] == 0

    @pytest.mark.parametrize("stato,tipo", [(408, "timeout_error"), (409, "conflict_error"),
                                            (429, "rate_limit_error"), (500, "api_error"),
                                            (529, "overloaded_error")])
    async def test_errori_transitori_non_fermano(self, cat, stato, tipo):
        valutazione, ai = await esegui_con(cat, [errore_provider(stato, tipo, "riprova")])
        assert len(ai.chiamate) == 3 and valutazione.fermato is None
        assert [b.esito for b in valutazione.bandi] == ["errore", "estratta", "estratta"]
        primo = valutazione.bandi[0]
        assert (primo.errore_codice, primo.errore_http) == ("ai_non_disponibile", stato)
        assert primo.costo_probabile_cents is None and primo.costo_cents == primo.riserva_cents

    async def test_tre_errori_di_fila_fermano(self, cat):
        errori = [AiTimeoutError(), AiUpstreamError(), errore_provider(500, "api_error", "x")]
        valutazione, ai = await esegui_con(cat, errori, ids=(1, 2, 3, 4))
        assert len(ai.chiamate) == 3
        assert valutazione.fermato == "errori_consecutivi"
        assert [b.esito for b in valutazione.bandi] == ["errore"] * 3 + ["non_valutato"]
        # ogni errore senza usage costa la sua riserva
        assert valutazione.spesa.speso_cents == sum(
            b.riserva_cents for b in valutazione.bandi[:3])

    async def test_un_successo_azzera_il_conteggio(self, cat):
        errori = [AiTimeoutError(), AiTimeoutError(), None, AiTimeoutError(), AiTimeoutError()]
        valutazione, ai = await esegui_con(cat, errori, ids=(1, 2, 3, 4, 5))
        assert len(ai.chiamate) == 5 and valutazione.fermato is None
        assert [b.esito for b in valutazione.bandi] == [
            "errore", "errore", "estratta", "errore", "errore"]

    async def test_errore_con_usage_conta_tra_i_consecutivi(self, cat):
        troncata = AiUpstreamError("troncata")
        troncata.usage = AiUsage(1_000, 100)
        errori = [troncata, AiTimeoutError(), AiTimeoutError()]
        valutazione, _ = await esegui_con(cat, errori, ids=(1, 2, 3, 4))
        assert valutazione.fermato == "errori_consecutivi"
        assert valutazione.bandi[0].errore_codice == "ai_risposta_non_valida"
        assert valutazione.bandi[3].esito == "non_valutato"
        # da rifare: i due timeout (nessuna risposta) e il mai valutato; non
        # la risposta arrivata e pagata
        assert loc.da_rifare(valutazione.risultato()) == [2, 3, 4]

    async def test_arresto_per_errori_rifa_gli_errori_senza_risposta(self, cat):
        """Tre 529 di fila: i bandi che hanno causato l'arresto vanno rifatti
        con gli altri, o resterebbero `errore` per sempre nelle metriche."""
        errori = [errore_provider(529, "overloaded_error", "x"), AiTimeoutError(),
                  AiUpstreamError()]
        valutazione, _ = await esegui_con(cat, errori, ids=(1, 2, 3, 4))
        assert valutazione.fermato == "errori_consecutivi"
        assert loc.da_rifare(valutazione.risultato()) == [1, 2, 3, 4]

    async def test_errore_transitorio_prima_del_400_da_rifare(self, cat):
        errori = [AiTimeoutError(), errore_provider(400)]
        valutazione, _ = await esegui_con(cat, errori, ids=(1, 2, 3))
        assert valutazione.fermato == "errore_non_transitorio"
        assert loc.da_rifare(valutazione.risultato()) == [1, 2, 3]

    async def test_senza_arresto_per_errori_i_transitori_restano(self, cat):
        # Esecuzione completata: un errore isolato è una misura, non si rifà.
        valutazione, _ = await esegui_con(cat, [AiTimeoutError()], ids=(1, 2))
        assert valutazione.fermato is None
        assert loc.da_rifare(valutazione.risultato()) == []

    def test_cli_400_fermata_e_da_rifare(self, cat, tmp_path, monkeypatch, capsys):
        cat.aggiungi(3, neutro=True)  # chiude senza modello: mai da rifare
        ai = AiFinta([errore_provider(400)])
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "fermata.json"
        assert cli(tmp_path, "--conferma", "--out", str(out)) == 0
        risultato = json.loads(out.read_text())
        assert risultato["fermato"] == "errore_non_transitorio" and ai.chiuso is True
        assert [b["esito"] for b in risultato["bandi"]] == ["errore", "non_valutato",
                                                             "nessun_segnale"]
        errori = capsys.readouterr().err
        assert "Valutazione fermata (errore_non_transitorio)" in errori
        assert "--solo 1,2" in errori
        assert "probabile 0: richiesta rifiutata prima della generazione" in errori
        assert "[HTTP 400 invalid_request_error]" in errori
        riserva = risultato["bandi"][0]["riserva_cents"]
        assert f"Speso {riserva} cent su un tetto di 800 (probabile 0)." in errori


# ------------------------------------------------------------ configurazione


class TestConfigurazione:
    def test_qualunque_uso_del_primario_solleva(self):
        settings = loc.impostazioni_locali()
        for nome in ("primary_supabase_url", "primary_supabase_service_role_key",
                     "primary_supabase_jwt_secret", "jwt_issuer", "jwks_url"):
            with pytest.raises(loc.PrimarioVietatoError):
                getattr(settings, nome)
        # il segnaposto vince sulle variabili del primario presenti nell'ambiente
        assert settings.__dict__["primary_supabase_url"] == loc.PRIMARIO_URL_SEGNAPOSTO
        from app.clients.supabase import create_primary_client

        with pytest.raises(loc.PrimarioVietatoError):
            asyncio.run(create_primary_client(settings))

    async def test_get_settings_nel_percorso_locale_si_vede(self, cat, ambiente, monkeypatch):
        # La guardia dei test: un helper che passasse da get_settings() (la
        # configurazione globale, primario compreso) non resterebbe nascosto
        # dietro l'`except` di prepara_bando.
        originale = ps.candidati_documenti

        def con_settings(*args, **kwargs):
            ps.get_settings()
            return originale(*args, **kwargs)

        monkeypatch.setattr(ps, "candidati_documenti", con_settings)
        _, _, [b] = await preparati(cat, ids=(1,))
        assert (b.esito, b.errore_codice) == ("errore", "errore_interno")
        assert ambiente == ["get_settings"]
        ambiente.clear()

    def test_catalogo_dalle_variabili_del_catalogo(self):
        settings = loc.impostazioni_locali()
        assert settings.secondary_supabase_url == "https://bandi.example.co"
        assert settings.secondary_supabase_anon_key == ANON
        assert settings.partenariato_ai_model == MODELLO
        assert settings.anthropic_api_key == "sk-ant-finta"

    def test_secondary_prima_e_file_env(self, monkeypatch):
        monkeypatch.setenv("SECONDARY_SUPABASE_URL", "https://secondario.example.co")
        monkeypatch.setenv("SECONDARY_SUPABASE_ANON_KEY", "anon-secondario")
        assert loc.impostazioni_locali().secondary_supabase_url == (
            "https://secondario.example.co")
        for chiave in ("SECONDARY_SUPABASE_URL", "SECONDARY_SUPABASE_ANON_KEY",
                       "SUPABASE_URL_BANDI", "PUBLIC_SUPABASE_BANDI_ANON_KEY",
                       "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(chiave)
        Path(".env").write_text("SUPABASE_URL_BANDI=https://dal-file.example.co\n"
                                "PUBLIC_SUPABASE_BANDI_ANON_KEY=anon-dal-file\n"
                                "ANTHROPIC_API_KEY=sk-dal-file\n")
        settings = loc.impostazioni_locali()
        assert settings.secondary_supabase_url == "https://dal-file.example.co"
        assert settings.secondary_supabase_anon_key == "anon-dal-file"
        assert settings.anthropic_api_key == "sk-dal-file"

    def test_rifiuta_una_chiave_service_role_per_il_catalogo(self, monkeypatch):
        payload = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode())
        monkeypatch.setenv("PUBLIC_SUPABASE_BANDI_ANON_KEY",
                           f"eyJhbGciOiJIUzI1NiJ9.{payload.decode().rstrip('=')}.firma")
        with pytest.raises(loc.ConfigurazioneLocaleError, match="anon key"):
            loc.impostazioni_locali()

    def test_catalogo_mancante(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL_BANDI")
        with pytest.raises(loc.ConfigurazioneLocaleError, match="SECONDARY_SUPABASE_URL"):
            loc.impostazioni_locali()


# ------------------------------------------------------------ fedeltà


async def _produzione(catalogo, ai) -> tuple[str, dict]:
    db = FakeDb()
    esito = await ps.esegui_per_bando(db, FakeSecondaryServizio([catalogo.bando]), ai,
                                      catalogo.bando, origine="valutazione", budget_cents=500)
    return esito, db.righe[BANDO_ID]


async def _locale(catalogo, ai) -> loc.BandoValutato:
    chiamate: list = []
    with senza_get_settings(chiamate):  # la produzione sì, il percorso locale mai
        settings = loc.impostazioni_locali()
        secondary = FakeSecondaryServizio([catalogo.bando])
        b = await loc.prepara_bando(secondary, {"bando_id": BANDO_ID}, settings=settings)
        if b.esito is None:
            assert await loc.valuta_con_modello(secondary, ai, b, loc.Spesa(tetto_cents=1000),
                                                settings=settings)
    assert chiamate == []
    return b


def _convalida_originale(chiamate: list[dict]) -> list[dict]:
    return [{**c, "convalida": inspect.unwrap(c["convalida"])} for c in chiamate]


def _fonti(fonti: list[dict]) -> list[tuple]:
    return [(f["n"], f["stato"], f["pagine_totali"], f["pagine_incluse"], f["troncato"])
            for f in fonti]


class TestFedelta:
    usa_la_produzione = True  # `_produzione` usa get_settings, `_locale` no

    @pytest.fixture(autouse=True)
    def primario_per_la_produzione(self, monkeypatch):
        # la pipeline di produzione vuole le Settings complete
        monkeypatch.setenv("SECONDARY_SUPABASE_URL", "https://d2.supabase.co")
        monkeypatch.setenv("SECONDARY_SUPABASE_ANON_KEY", "k")
        get_settings.cache_clear()

    async def test_stesse_regole_della_pipeline(self, catalogo):
        ai_prod, ai_loc = FakeAi(), AiFinta()
        esito, riga = await _produzione(catalogo, ai_prod)
        b = await _locale(catalogo, ai_loc)
        assert esito == b.esito == "estratta"
        assert b.regole == riga["regole"]
        assert b.regole["modalita_effettiva"] == riga["modalita_effettiva"] == "ammesso"
        # stesso input, modello, max_tokens, timeout, schema e convalida: la
        # locale la avvolge solo per registrare l'input grezzo
        assert ai_loc.chiamate[0]["convalida"] is not ai_prod.chiamate[0]["convalida"]
        assert _convalida_originale(ai_loc.chiamate) == ai_prod.chiamate
        assert _fonti(b.fonti) == _fonti(riga["fonti_usate"])
        assert (b.costo_cents, b.input_tokens, b.output_tokens) == (
            riga["cost_cents"], riga["input_tokens"], riga["output_tokens"])

    async def test_nessun_segnale_senza_modello(self, catalogo):
        catalogo.bando = bando(pagine_con_segnali=False)
        catalogo.pagine = [(1, NEUTRA_1), (2, NEUTRA_2)]
        ai_prod, ai_loc = FakeAi(), AiFinta()
        esito, riga = await _produzione(catalogo, ai_prod)
        b = await _locale(catalogo, ai_loc)
        assert esito == b.esito == "nessun_segnale"
        assert ai_prod.chiamate == ai_loc.chiamate == []
        assert (b.riserva_cents, b.costo_cents) == (0, 0)
        assert _fonti(b.fonti) == _fonti(riga["fonti_usate"])

    async def test_documenti_non_raggiungibili(self, catalogo, monkeypatch):
        catalogo.bando = bando(pagine_con_segnali=False)

        async def scarica(url, *, max_bytes, timeout_s, **kwargs):
            return DocumentoScaricato(stato="errore_download", motivo="timeout")

        monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
        esito, riga = await _produzione(catalogo, FakeAi())
        b = await _locale(catalogo, AiFinta())
        assert esito == b.esito == "errore"
        assert b.errore_codice == riga["errore_codice"] == "documenti_non_raggiungibili"
        assert b.costo_cents == 0


# ------------------------------------------------------------ CLI


class TestCli:
    def test_locale_senza_reale_rifiutato(self, cat, tmp_path):
        campione = scrivi_campione(tmp_path)
        assert val.main(["--offline", "--locale", "--campione", campione]) == 2
        assert val.main(["--prepara", str(tmp_path / "f"), "--locale",
                         "--campione", campione]) == 2
        assert val.main(["--reale", "--locale", "--campione", campione]) == 2  # senza tetto
        assert val.main(["--offline", "--solo", "1", "--campione", campione]) == 2
        assert cat.download == [] and cat.secondari == 0

    def test_senza_conferma_nessuna_chiamata(self, cat, tmp_path, monkeypatch, capsys):
        def vietato(settings):
            raise AssertionError("senza --conferma il modello non si crea")

        monkeypatch.setattr(loc, "crea_ai", vietato)
        cat.aggiungi(3, neutro=True)
        out = tmp_path / "out.json"
        assert cli(tmp_path, "--out", str(out)) == 0
        assert not out.exists()
        errori = capsys.readouterr().err
        assert "Stima:" in errori and "Nessuna spesa" in errori
        assert "2 bandi con il modello su 3" in errori
        # i documenti si leggono per la stima esatta: nessuna spesa
        assert len(cat.download) == 3

    def test_conferma_senza_out_rifiutata(self, cat, tmp_path, monkeypatch, capsys):
        # il risultato contiene le sezioni inviate (testi dei documenti e del
        # catalogo): mai su stdout, da dove finirebbe in un file del repo
        def vietato(settings):
            raise AssertionError("senza --out il modello non si crea")

        monkeypatch.setattr(loc, "crea_ai", vietato)
        assert cli(tmp_path, "--conferma") == 2
        assert cat.download == [] and cat.secondari == 0
        errori = capsys.readouterr()
        assert "--out" in errori.err and errori.out == ""
        # senza --conferma (solo la stima, nessuna sezione in uscita) resta facoltativo
        assert cli(tmp_path) == 0

    def test_chiave_mancante_prima_dei_download(self, cat, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("ANTHROPIC_API_KEY")
        assert cli(tmp_path, "--conferma", "--out", str(tmp_path / "x.json")) == 2
        assert cat.download == [] and cat.secondari == 0
        errori = capsys.readouterr().err
        assert "ANTHROPIC_API_KEY" in errori and ANON not in errori

    def test_out_nel_repository_rifiutato(self, cat, tmp_path, monkeypatch, capsys):
        def mai(*a, **k):
            raise AssertionError("nel repository non si scrive nulla")

        monkeypatch.setattr(loc, "_scrivi", mai)
        dentro = val._REPO / "valutazione_locale_non_creare.json"
        assert cli(tmp_path, "--conferma", "--out", str(dentro)) == 2
        assert "fuori dal repository" in capsys.readouterr().err
        assert not dentro.exists()
        assert cat.download == [] and cat.secondari == 0

    def test_out_esistente_rifiutato(self, cat, tmp_path):
        esistente = tmp_path / "gia.json"
        esistente.write_text("{}")
        assert cli(tmp_path, "--conferma", "--out", str(esistente)) == 2
        assert esistente.read_text() == "{}" and cat.download == []

    def test_con_conferma_risultato_e_metriche(self, cat, tmp_path, monkeypatch):
        cat.aggiungi(3, neutro=True)
        ai = AiFinta()
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "locale.json"
        assert cli(tmp_path, "--conferma", "--out", str(out)) == 0
        testo = out.read_text()
        risultato = json.loads(testo)
        assert risultato["fermato"] is None and ai.chiuso is True
        assert len(ai.chiamate) == 2
        assert risultato["stima"]["con_modello"] == 2
        assert risultato["stima"]["costo_max_totale_cents"] == 2 * risultato["bandi"][0][
            "riserva_cents"]
        uno, _, tre = risultato["bandi"]
        assert uno["esito"] == "estratta"
        assert (uno["modalita"], uno["modalita_effettiva"]) == ("ammesso", "ammesso")
        assert uno["modalita_citazione_verificata"] is True
        assert (uno["partner_min"], uno["partner_max"]) == (2, None)
        assert uno["quote"] == [{"ambito": "per_partner", "categoria": None, "min": 20.0,
                                 "max": None}]
        assert uno["quote_usate"] == uno["quote"]  # la quota è verificata
        assert tre["quote_usate"] == []  # nessun_segnale: nessuna regola
        assert uno["forme"] == ["ats"]
        assert uno["fonti"] == [{"n": 1, "etichetta": "Avviso", "dominio": "ente.example.it",
                                 "stato": "letto", "pagine_totali": 2,
                                 "pagine_incluse": [1, 2], "troncato": False}]
        assert uno["costo_cents"] == REALE and uno["riserva_cents"] > REALE
        assert (uno["input_tokens"], uno["output_tokens"]) == (30_000, 6_000)
        assert uno["latenza_s"] is not None and uno["latenza_modello_s"] is not None
        assert tre["esito"] == "nessun_segnale" and tre["costo_cents"] == 0
        assert (tre["modalita"], tre["modalita_effettiva"]) == (None, "non_determinabile")
        assert risultato["spesa"] == {"speso_cents": 2 * REALE, "riserve_aperte_cents": 0,
                                      "chiamate": 2, "speso_probabile_cents": 2 * REALE}
        assert (uno["costo_probabile_cents"], uno["nota_costo"]) == (REALE, None)
        assert (uno["errore_http"], uno["errore_tipo"]) == (None, None)
        metriche = risultato["metriche"]
        assert metriche["etichettate"] == 3 and metriche["modalita"]["accuracy"] == 1.0
        assert metriche["partner_max"]["exact_match"] == round(2 / 3, 4)
        assert metriche["raggiungibili"]["partner_max"]["exact_match"] == 1.0
        assert metriche["quote"]["tp"] == 1
        # le quote predette sono tutte verificate: quelle usate coincidono con tutte
        assert metriche["quote_usate"] == metriche["quote"]
        assert metriche["raggiungibili"]["quote_usate"]["tp"] == 1
        assert metriche["citazioni"]["percentuale"] is not None
        assert metriche["costi"]["costo_totale_cents"] == 2 * REALE
        # AiFinta non passa dalla convalida: nessun input grezzo, quindi niente
        # `rivalutazione` e mai le pagine intere dei documenti
        assert all("rivalutazione" not in b for b in risultato["bandi"])
        for pagina in (PAGINA_1, PAGINA_2, NEUTRA_1, NEUTRA_2):
            assert pagina not in testo

    def test_solo_filtra(self, cat, tmp_path, monkeypatch):
        ai = AiFinta()
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "solo.json"
        assert cli(tmp_path, "--conferma", "--solo", "2", "--out", str(out)) == 0
        risultato = json.loads(out.read_text())
        assert [b["bando_id"] for b in risultato["bandi"]] == [2]
        assert cat.download == ["https://ente.example.it/2.pdf"] and len(ai.chiamate) == 1
        for sbagliato in ("99", "x", ","):
            assert cli(tmp_path, "--conferma", "--solo", sbagliato,
                       "--out", str(tmp_path / "x.json")) == 2
        assert len(cat.download) == 1

    def test_errori_fuori_dalle_metriche_di_campo(self, cat, tmp_path, monkeypatch):
        # Bando 1 (partner_max atteso None) va in timeout: il suo None non deve
        # contare come exact match; l'errore si conta a parte e si paga.
        cat.aggiungi(3, neutro=True)
        ai = AiFinta([AiTimeoutError()])
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "errori.json"
        assert cli(tmp_path, "--conferma", "--out", str(out)) == 0
        risultato = json.loads(out.read_text())
        assert [b["esito"] for b in risultato["bandi"]] == ["errore", "estratta",
                                                             "nessun_segnale"]
        metriche = risultato["metriche"]
        assert metriche["etichettate"] == 2  # le sole risposte: 2 e 3
        assert metriche["modalita"] == {**metriche["modalita"], "n": 2, "accuracy": 1.0}
        # bando 2 sbaglia partner_max (5 → None), bando 3 lo azzecca (None)
        assert metriche["partner_max"] == {"n": 2, "exact_match": 0.5}
        assert metriche["raggiungibili"]["partner_max"] == {"n": 1, "exact_match": 1.0}
        assert metriche["errori"] == {"n": 1, "tasso": round(1 / 3, 4),
                                      "per_codice": {"timeout": 1}}
        riserva = risultato["bandi"][0]["riserva_cents"]
        assert metriche["costi"]["n"] == 3
        assert metriche["costi"]["costo_totale_cents"] == riserva + REALE

    def test_ripresa_col_tetto_residuo_e_unione(self, cat, tmp_path, monkeypatch):
        # Le istruzioni di ripresa: `da_rifare` per `--solo`, tetto = totale
        # meno lo speso di TUTTE le esecuzioni, metriche sull'unione uguali a
        # quelle di un'esecuzione unica.
        cat.aggiungi(3, neutro=True)
        ai = AiFinta()
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        unica, prima, seconda = (tmp_path / f"{n}.json" for n in ("unica", "prima", "seconda"))
        assert cli(tmp_path, "--conferma", "--out", str(unica)) == 0
        intera = json.loads(unica.read_text())
        riserva = intera["bandi"][0]["riserva_cents"]
        # prima esecuzione: il tetto basta per una sola chiamata
        assert cli(tmp_path, "--conferma", "--out", str(prima), tetto=str(riserva)) == 0
        parziale = json.loads(prima.read_text())
        assert parziale["fermato"] == "tetto_raggiunto"
        assert loc.da_rifare(parziale) == [2]
        speso = parziale["spesa"]["speso_cents"] + parziale["spesa"]["riserve_aperte_cents"]
        assert speso == REALE
        tetto_residuo = REALE + riserva - speso  # «tetto totale» di questo test
        assert cli(tmp_path, "--conferma", "--solo", "2", "--out", str(seconda),
                   tetto=str(tetto_residuo)) == 0
        ripresa = json.loads(seconda.read_text())
        assert ripresa["fermato"] is None and ripresa["spesa"]["speso_cents"] == REALE
        campione = val.carica_campione(scrivi_campione(tmp_path))
        unite = loc.unisci_esecuzioni([parziale, ripresa])
        assert [b["esito"] for b in unite] == ["estratta", "estratta", "nessun_segnale"]
        metriche = loc.metriche_locali(campione, unite)
        attese = intera["metriche"]
        for chiave in ("etichettate", "modalita", "partner_min", "partner_max", "quote",
                       "quote_usate", "raggiungibili", "citazioni", "errori"):
            assert metriche[chiave] == attese[chiave], chiave
        assert metriche["costi"]["costo_totale_cents"] == attese["costi"][
            "costo_totale_cents"] == 2 * REALE

    def test_interruzione_da_rifare(self, cat, tmp_path, monkeypatch):
        ai = AiFinta([None, asyncio.CancelledError()])
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "interrotta.json"
        with pytest.raises(asyncio.CancelledError):
            cli(tmp_path, "--conferma", "--out", str(out))
        # il bando interrotto e quelli mai valutati; non quello già pagato
        assert loc.da_rifare(json.loads(out.read_text())) == [2, 3]

    def test_interruzione_scrive_quanto_pagato(self, cat, tmp_path, monkeypatch):
        ai = AiFinta([None, asyncio.CancelledError()])
        monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
        out = tmp_path / "interrotta.json"
        with pytest.raises(asyncio.CancelledError):
            cli(tmp_path, "--conferma", "--out", str(out))
        risultato = json.loads(out.read_text())
        assert risultato["fermato"] == "interrotta" and ai.chiuso is True
        riserva = risultato["bandi"][1]["riserva_cents"]
        assert risultato["spesa"]["speso_cents"] == REALE + riserva
        assert [b["esito"] for b in risultato["bandi"]] == ["estratta", "errore",
                                                             "non_valutato"]


# ------------------------------------------------------------ rivalutazione


def input_grezzo(**modifiche) -> dict:
    """L'input dello strumento come arriverebbe dal modello, prima della
    convalida tollerante: un numero al posto della stringa di cifre, un
    codice in maiuscolo e un campo ignoto."""
    grezzo = servizio.estrazione_valida().model_dump()
    grezzo.update(partner_min=2, modalita="Ammesso", campo_ignoto="x")
    # una regione: la post-elaborazione la mappa con le lookup del catalogo
    grezzo["composizione"] = [{
        "id": "C1", "tipo_soggetto": "pmi", "tipo_soggetto_testo": "", "minimo": "2",
        "massimo": "", "ruolo": "qualsiasi", "regioni": ["Piemonte"], "paesi": [],
        "vincolo_territoriale": "",
        "citazione": {"sezione": "D1-p2", "testo": "almeno 2 imprese"},
    }]
    grezzo.update(modifiche)
    return grezzo


class AiGrezza(AiFinta):
    """Come il client vero: l'input del blocco tool_use passa per la
    convalida data dal chiamante; se la convalida lo respinge, errore con
    l'usage (risposta pagata)."""

    def __init__(self, errori: list | None = None, grezzi: list | None = None):
        super().__init__(errori)
        self.grezzi = list(grezzi or [])

    async def estrai_con_strumento(self, system, user_message, output_model, *,
                                   nome_strumento, descrizione, convalida=None, model=None,
                                   max_tokens=None, timeout=None):
        self.chiamate.append({"testo": user_message, "convalida": convalida})
        self.in_volo += 1
        self.max_in_volo = max(self.max_in_volo, self.in_volo)
        try:
            await asyncio.sleep(0)
            errore = self.errori.pop(0) if self.errori else None
            if errore is not None:
                raise errore
            grezzo = self.grezzi.pop(0) if self.grezzi else input_grezzo()
            usage = AiUsage(input_tokens=30_000, output_tokens=6_000)
            try:
                return convalida(grezzo), usage
            except (ValueError, TypeError):
                errore = AiUpstreamError("input respinto")
                errore.usage = usage
                raise errore from None
        finally:
            self.in_volo -= 1


def _vietati(monkeypatch) -> list:
    """Nella rivalutazione niente modello, catalogo, download, lettura dei
    PDF, configurazione. Ogni tentativo si registra nella lista restituita,
    che il test verifica vuota: `ps._lookups` inghiotte le eccezioni (anche
    l'AssertionError), quindi sollevare non basterebbe."""
    tentativi: list = []

    def mai(*a, **k):
        tentativi.append("chiamata")
        raise AssertionError("la rivalutazione non chiama modello, rete o configurazione")

    async def mai_async(*a, **k):
        mai()

    async def lookup_vietate(*a, **k):
        tentativi.append("lookup")
        raise AssertionError("la rivalutazione non legge il catalogo")

    monkeypatch.setattr(loc, "crea_ai", mai)
    monkeypatch.setattr(loc, "crea_secondario", mai_async)
    monkeypatch.setattr(loc, "impostazioni_locali", mai)
    monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", mai_async)
    monkeypatch.setattr("app.services.pdf_testo.estrai_testo", mai_async)
    monkeypatch.setattr("app.services.lookup_service.get_lookups", lookup_vietate)
    monkeypatch.setattr(ps, "_lookups", lookup_vietate)
    return tentativi


def rivaluta(tmp_path, *origini: Path, out: Path, campione: str | None = None) -> int:
    argomenti = []
    for origine in origini:
        argomenti += ["--rivaluta", str(origine)]
    return val.main([*argomenti, "--campione", campione or scrivi_campione(tmp_path),
                     "--out", str(out)])


def _esegui_con_grezzi(cat, tmp_path, monkeypatch, nome="locale.json", *extra, ai=None,
                       tetto="800") -> dict:
    ai = ai or AiGrezza()
    monkeypatch.setattr(loc, "crea_ai", lambda settings: ai)
    out = tmp_path / nome
    assert cli(tmp_path, "--conferma", "--out", str(out), *extra, tetto=tetto) == 0
    return json.loads(out.read_text())


CAMPI_DI_CONFRONTO = ("bando_id", "esito", "errore_codice", "modalita", "modalita_effettiva",
                      "modalita_citazione_verificata", "partner_min", "partner_max", "quote",
                      "quote_usate", "forme", "regole", "costo_cents", "latenza_s", "atteso",
                      "gruppo")


def test_campi_predetti_quote_usate_solo_verificate():
    """Per ogni bando l'uscita espone anche le quote che la call preseleziona."""
    regole = {"quote": [
        {"ambito": "per_partner", "categoria": None, "min_percentuale": 10.0,
         "max_percentuale": None, "stato": "verificata"},
        {"ambito": "capofila", "categoria": None, "min_percentuale": 30.0,
         "max_percentuale": None, "stato": "da_verificare"},
    ]}
    campi = loc._campi_predetti(regole, "estratta")
    assert len(campi["quote"]) == 2
    assert campi["quote_usate"] == [
        {"ambito": "per_partner", "categoria": None, "min": 10.0, "max": None}]
    assert loc._campi_predetti(None, "nessun_segnale")["quote_usate"] == []


class TestInputGrezzo:
    async def test_registra_input_grezzo_e_sezioni_inviate(self, cat):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        sezioni = dict(b.ingresso.sezioni)
        ai = AiGrezza()
        await loc.valuta_con_modello(secondary, ai, b, loc.Spesa(tetto_cents=1000),
                                     settings=settings)
        assert b.esito == "estratta" and b.input_registrato is True
        # grezzo: prima della convalida tollerante
        assert b.input_grezzo == input_grezzo()
        assert b.input_grezzo["partner_min"] == 2 and "campo_ignoto" in b.input_grezzo
        assert b.regole["partner_min"]["valore"] == 2
        uscita = loc._uscita_bando(b)["rivalutazione"]
        assert uscita["input_strumento"] == input_grezzo()
        assert uscita["sezioni"] == sezioni
        assert any(PAGINA_1 in testo for testo in uscita["sezioni"].values())
        assert uscita["fonti"][0]["url"] == "https://ente.example.it/1.pdf"
        # la convalida è quella di produzione, avvolta
        assert inspect.unwrap(ai.chiamate[0]["convalida"]) is ps.convalida_tollerante

    async def test_input_respinto_registrato(self, cat):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        ai = AiGrezza(grezzi=[{"nessun_campo": 1}])
        await loc.valuta_con_modello(secondary, ai, b, loc.Spesa(tetto_cents=1000),
                                     settings=settings)
        assert (b.esito, b.errore_codice) == ("errore", "ai_risposta_non_valida")
        assert loc._uscita_bando(b)["rivalutazione"]["input_strumento"] == {"nessun_campo": 1}

    @pytest.mark.parametrize("errore", [AiTimeoutError(), AiUpstreamError()])
    async def test_senza_risposta_nessun_dato(self, cat, errore):
        settings, secondary, [b] = await preparati(cat, ids=(1,))
        await loc.valuta_con_modello(secondary, AiGrezza([errore]), b,
                                     loc.Spesa(tetto_cents=1000), settings=settings)
        assert b.esito == "errore" and b.input_registrato is False
        assert "rivalutazione" not in loc._uscita_bando(b)

    def test_regioni_da_lookups(self):
        from app.schemas.common import LookupItem

        oggetto = SimpleNamespace(regioni=[LookupItem(id=1, nome="Piemonte")])
        assert loc.regioni_da_lookups(oggetto) == [{"id": 1, "nome": "Piemonte"}]
        assert loc.regioni_da_lookups({"regioni": [{"id": 2, "nome": "Lazio"}]}) == [
            {"id": 2, "nome": "Lazio"}]
        assert loc.regioni_da_lookups(None) is None
        assert loc.regioni_da_lookups(SimpleNamespace()) is None

    def test_risultato_con_versioni_e_regioni_per_bando(self, cat, tmp_path, monkeypatch):
        cat.aggiungi(3, neutro=True)
        risultato = _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        assert risultato["rivalutazione"] == {"formato": 2}
        assert risultato["versioni"] == loc.versioni_attuali() == {
            "prompt": ps.PARTENARIATO_PROMPT_VERSION, "schema": ps.SCHEMA_VERSION,
            "vocabolario": loc.VOCABOLARIO_VERSIONE}
        uno, _, tre = risultato["bandi"]
        assert uno["rivalutazione"]["input_strumento"] == input_grezzo()
        assert uno["rivalutazione"]["regioni"] == [{"id": 1, "nome": "Piemonte"}]
        assert uno["regole"]["composizione"][0]["regioni"] == [1]
        assert "rivalutazione" not in tre  # nessun segnale: nessun modello

    def test_regioni_del_bando_quelle_della_sua_post_elaborazione(self, cat, tmp_path,
                                                                  monkeypatch):
        # Il catalogo non risponde a una lettura su due: ogni bando registra
        # le regioni che la SUA post-elaborazione ha usato (anche None), e la
        # rivalutazione ridà le stesse regole.
        letture = []

        async def lookups(secondary):
            letture.append(1)
            if len(letture) % 2:
                raise RuntimeError("catalogo non disponibile")
            return SimpleNamespace(regioni=[{"id": 1, "nome": "Piemonte"}])

        monkeypatch.setattr("app.services.lookup_service.get_lookups", lookups)
        originale = _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        uno, due, _ = originale["bandi"]
        assert uno["rivalutazione"]["regioni"] is None
        assert "Regioni non verificabili sul catalogo" in uno["regole"]["composizione"][0][
            "avvisi"]
        assert due["rivalutazione"]["regioni"] == [{"id": 1, "nome": "Piemonte"}]
        assert due["regole"]["composizione"][0]["regioni"] == [1]
        tentativi = _vietati(monkeypatch)
        out = tmp_path / "rivalutato.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out) == 0
        nuovo = json.loads(out.read_text())
        for prima, dopo in zip(originale["bandi"], nuovo["bandi"], strict=True):
            assert dopo["regole"] == prima["regole"], prima["bando_id"]
        assert tentativi == []


class TestRivaluta:
    def test_stesse_metriche_a_codice_invariato(self, cat, tmp_path, monkeypatch, capsys):
        cat.aggiungi(3, neutro=True)
        ai = AiGrezza(errori=[None, AiTimeoutError()])
        originale = _esegui_con_grezzi(cat, tmp_path, monkeypatch, ai=ai)
        assert [b["esito"] for b in originale["bandi"]] == [
            "estratta", "errore", "nessun_segnale"]
        tentativi = _vietati(monkeypatch)
        chiamate = len(ai.chiamate)
        out = tmp_path / "rivalutato.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out) == 0
        assert len(ai.chiamate) == chiamate
        nuovo = json.loads(out.read_text())
        assert nuovo["modalita"] == "rivalutazione"  # la sua spesa non si somma
        assert nuovo["origini"] == [str(tmp_path / "locale.json")]
        assert tentativi == []  # nessuna lettura del catalogo, del modello o della rete
        assert nuovo["versioni"] == nuovo["versioni_codice"] == loc.versioni_attuali()
        # le regioni salvate del bando: la regione della composizione è mappata
        assert nuovo["bandi"][0]["regole"]["composizione"][0]["regioni"] == [1]
        assert nuovo["metriche"] == originale["metriche"] == nuovo["metriche_origine"]
        assert nuovo["esiti"] == originale["esiti"]
        for prima, dopo in zip(originale["bandi"], nuovo["bandi"], strict=True):
            for campo in CAMPI_DI_CONFRONTO:
                assert dopo[campo] == prima[campo], (prima["bando_id"], campo)
            assert "rivalutazione" not in dopo  # le sezioni restano nel file originale
        testo = out.read_text()
        for pagina in (PAGINA_1, PAGINA_2):
            assert pagina not in testo
        errori = capsys.readouterr().err
        assert "Rivalutazione senza modello: 3 bandi" in errori
        assert "Prima:" in errori and "Dopo:" in errori
        # la metrica principale delle quote è quella sulle quote usate
        assert "quote usate P 1.0 R 1.0 (tutte P 1.0 R 1.0)" in errori
        assert "Nota:" not in errori  # stesse versioni del codice

    def test_usa_le_regioni_salvate_del_bando(self, cat, tmp_path, monkeypatch):
        originale = _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        tentativi = _vietati(monkeypatch)
        for regioni, attese, avviso in (
            ([{"id": 7, "nome": "Piemonte"}], [7], None),  # non quelle di oggi (id 1)
            (None, [], "Regioni non verificabili sul catalogo"),
        ):
            modificato = copy.deepcopy(originale)
            modificato["bandi"][0]["rivalutazione"]["regioni"] = regioni
            origine = tmp_path / f"regioni_{attese}.json"
            origine.write_text(json.dumps(modificato))
            out = tmp_path / f"rivalutato_{attese}.json"
            assert rivaluta(tmp_path, origine, out=out) == 0
            composizione = json.loads(out.read_text())["bandi"][0]["regole"]["composizione"][0]
            assert composizione["regioni"] == attese
            assert (avviso in composizione["avvisi"]) if avviso else composizione["avvisi"] == []
        assert tentativi == []

    def test_misura_una_modifica_delle_regole(self, cat, tmp_path, monkeypatch):
        originale = _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        assert originale["metriche"]["modalita"]["accuracy"] == round(2 / 3, 4)
        _vietati(monkeypatch)
        post_elabora = ps.post_elabora

        def piu_prudente(*args, **kwargs):
            regole = post_elabora(*args, **kwargs)
            regole["modalita"]["effettiva"] = regole["modalita_effettiva"] = "non_determinabile"
            return regole

        monkeypatch.setattr(ps, "post_elabora", piu_prudente)
        out = tmp_path / "modificato.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out) == 0
        nuovo = json.loads(out.read_text())
        assert [b["modalita_effettiva"] for b in nuovo["bandi"]] == ["non_determinabile"] * 3
        assert nuovo["metriche"]["modalita"]["accuracy"] == round(1 / 3, 4)
        assert nuovo["metriche_origine"] == originale["metriche"]

    def test_usa_la_convalida_attuale(self, cat, tmp_path, monkeypatch):
        _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        _vietati(monkeypatch)

        def respinge(dati):
            raise ValueError("convalida più severa")

        monkeypatch.setattr(ps, "convalida_tollerante", respinge)
        out = tmp_path / "severa.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out) == 0
        nuovo = json.loads(out.read_text())
        assert {(b["esito"], b["errore_codice"]) for b in nuovo["bandi"]} == {
            ("errore", "ai_risposta_non_valida")}
        assert nuovo["metriche"]["errori"]["n"] == 3

    def test_input_prima_respinto_poi_accettato(self, cat, tmp_path, monkeypatch):
        ai = AiGrezza(grezzi=[{"nessun_campo": 1}])
        originale = _esegui_con_grezzi(cat, tmp_path, monkeypatch, ai=ai)
        assert originale["bandi"][0]["errore_codice"] == "ai_risposta_non_valida"
        _vietati(monkeypatch)
        convalida = ps.convalida_tollerante

        def con_involucro(dati):  # una convalida corretta che ora lo capisce
            return convalida(input_grezzo() if dati == {"nessun_campo": 1} else dati)

        monkeypatch.setattr(ps, "convalida_tollerante", con_involucro)
        out = tmp_path / "corretta.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out) == 0
        primo = json.loads(out.read_text())["bandi"][0]
        assert (primo["esito"], primo["errore_codice"]) == ("estratta", None)
        assert primo["modalita_effettiva"] == "ammesso"

    def test_etichette_attuali_del_campione(self, cat, tmp_path, monkeypatch):
        _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        _vietati(monkeypatch)
        percorso = Path(scrivi_campione(tmp_path))
        dati = json.loads(percorso.read_text())
        dati["campione"][2]["etichetta"]["modalita"] = "ammesso"  # etichetta corretta
        percorso.write_text(json.dumps(dati))
        tentativi = _vietati(monkeypatch)
        out = tmp_path / "etichette.json"
        assert rivaluta(tmp_path, tmp_path / "locale.json", out=out,
                        campione=str(percorso)) == 0
        nuovo = json.loads(out.read_text())
        assert nuovo["bandi"][2]["atteso"]["modalita"] == "ammesso"
        assert nuovo["metriche"]["modalita"]["accuracy"] == 1.0
        assert nuovo["metriche_origine"]["modalita"]["accuracy"] == 1.0
        assert tentativi == []

    def test_piu_esecuzioni_come_una(self, cat, tmp_path, monkeypatch):
        cat.aggiungi(3, neutro=True)
        intera = _esegui_con_grezzi(cat, tmp_path, monkeypatch, "unica.json")
        riserva = intera["bandi"][0]["riserva_cents"]
        parziale = _esegui_con_grezzi(cat, tmp_path, monkeypatch, "prima.json",
                                      tetto=str(riserva))
        assert loc.da_rifare(parziale) == [2]
        _esegui_con_grezzi(cat, tmp_path, monkeypatch, "seconda.json", "--solo", "2")
        tentativi = _vietati(monkeypatch)
        out = tmp_path / "unite.json"
        assert rivaluta(tmp_path, tmp_path / "prima.json", tmp_path / "seconda.json",
                        out=out) == 0
        nuovo = json.loads(out.read_text())
        assert [b["esito"] for b in nuovo["bandi"]] == ["estratta", "estratta",
                                                         "nessun_segnale"]
        for chiave in ("etichettate", "modalita", "partner_min", "partner_max", "quote",
                       "quote_usate", "raggiungibili", "citazioni", "errori"):
            assert nuovo["metriche"][chiave] == intera["metriche"][chiave], chiave
        assert tentativi == []

    def test_versioni_diverse_non_si_uniscono(self, cat, tmp_path, monkeypatch, capsys):
        cat.aggiungi(3, neutro=True)
        _esegui_con_grezzi(cat, tmp_path, monkeypatch, "prima.json", "--solo", "1")
        seconda = _esegui_con_grezzi(cat, tmp_path, monkeypatch, "seconda.json", "--solo", "2")
        _vietati(monkeypatch)
        seconda["versioni"]["prompt"] = ps.PARTENARIATO_PROMPT_VERSION - 1
        (tmp_path / "seconda.json").write_text(json.dumps(seconda))
        out = tmp_path / "unite.json"
        assert rivaluta(tmp_path, tmp_path / "prima.json", tmp_path / "seconda.json",
                        out=out) == 2
        assert "versioni diverse" in capsys.readouterr().err and not out.exists()
        # da sola si rivaluta, con una nota: le versioni del codice sono altre
        assert rivaluta(tmp_path, tmp_path / "seconda.json", out=out) == 0
        nuovo = json.loads(out.read_text())
        assert nuovo["versioni"] == seconda["versioni"] != nuovo["versioni_codice"]
        assert "Nota:" in capsys.readouterr().err


class TestRivalutaCli:
    @pytest.fixture
    def originale(self, cat, tmp_path, monkeypatch) -> Path:
        _esegui_con_grezzi(cat, tmp_path, monkeypatch)
        _vietati(monkeypatch)
        return tmp_path / "locale.json"

    def test_out_obbligatorio_fuori_dal_repo_e_nuovo(self, originale, tmp_path, capsys):
        campione = scrivi_campione(tmp_path)
        assert val.main(["--rivaluta", str(originale), "--campione", campione]) == 2
        assert "--out" in capsys.readouterr().err
        dentro = val._REPO / "rivalutazione_non_creare.json"
        assert rivaluta(tmp_path, originale, out=dentro) == 2
        assert not dentro.exists()
        assert "fuori dal repository" in capsys.readouterr().err
        assert rivaluta(tmp_path, originale, out=originale) == 2  # esiste già
        assert json.loads(originale.read_text())["modalita"] == "locale"

    def test_argomenti_che_spendono_rifiutati(self, originale, tmp_path):
        campione = scrivi_campione(tmp_path)
        out = str(tmp_path / "x.json")
        for extra in (["--conferma"], ["--tetto-cents", "10"], ["--locale"], ["--solo", "1"]):
            assert val.main(["--rivaluta", str(originale), "--campione", campione,
                             "--out", out, *extra]) == 2
        with pytest.raises(SystemExit) as uscita:
            val.main(["--rivaluta", str(originale), "--reale", "--out", out])
        assert uscita.value.code == 2
        assert not Path(out).exists()

    def test_file_senza_output_grezzo(self, originale, tmp_path, capsys):
        vecchio = json.loads(originale.read_text())
        for b in vecchio["bandi"]:
            b.pop("rivalutazione", None)
        vecchio.pop("rivalutazione")
        percorso = tmp_path / "vecchio.json"
        percorso.write_text(json.dumps(vecchio))
        out = tmp_path / "x.json"
        assert rivaluta(tmp_path, percorso, out=out) == 2
        errori = capsys.readouterr().err
        assert "non contiene l'output grezzo" in errori and "[1, 2, 3]" in errori
        assert not out.exists()

    @pytest.mark.parametrize("modifica", ["senza_versioni", "formato_1"])
    def test_formato_precedente_rifiutato(self, originale, tmp_path, capsys, modifica):
        # formato 1: regioni globali e nessuna versione
        vecchio = json.loads(originale.read_text())
        if modifica == "senza_versioni":
            vecchio.pop("versioni")
        else:
            vecchio["rivalutazione"] = {"formato": 1, "regioni": None}
        percorso = tmp_path / "vecchio.json"
        percorso.write_text(json.dumps(vecchio))
        assert rivaluta(tmp_path, percorso, out=tmp_path / "x.json") == 2
        assert "non contiene l'output grezzo" in capsys.readouterr().err

    @pytest.mark.parametrize("contenuto", ["non json", "[]", '{"modalita": "rivalutazione"}'])
    def test_file_non_valido(self, originale, tmp_path, capsys, contenuto):
        percorso = tmp_path / "strano.json"
        percorso.write_text(contenuto)
        assert rivaluta(tmp_path, percorso, out=tmp_path / "x.json") == 2
        assert "--rivaluta" in capsys.readouterr().err

    def test_file_mancante(self, originale, tmp_path, capsys):
        assert rivaluta(tmp_path, tmp_path / "manca.json", out=tmp_path / "x.json") == 2
        assert "illeggibile" in capsys.readouterr().err
