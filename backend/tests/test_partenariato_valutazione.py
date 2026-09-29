"""Valutazione delle regole di partenariato (WP3): metriche pure su output
sintetici e CLI `--offline` / `--prepara` / `--reale` SENZA rete (catalogo,
download e lettura finti; nessun modello chiamato)."""

import json
from types import SimpleNamespace

import pytest

from app.services import partenariato_valutazione as val
from app.services.bando_fonti_service import LinkDocumento
from app.services.download_sicuro import DocumentoScaricato
from app.services.pdf_testo import TestoPdf as _TestoPdf

# ------------------------------------------------------------ metriche


class TestMetricheModalita:
    def test_accuracy_e_pr_per_classe(self):
        coppie = [
            ("ammesso", "ammesso"),
            ("ammesso", "non_determinabile"),
            ("obbligatorio", "obbligatorio"),
            ("non_ammesso", "ammesso"),
            (None, "ammesso"),  # non etichettata: non conta
        ]
        m = val.metriche_modalita(coppie)
        assert m["n"] == 4 and m["accuracy"] == 0.5
        assert m["per_classe"]["ammesso"] == {"precision": 0.5, "recall": 0.5, "supporto": 2}
        assert m["per_classe"]["obbligatorio"] == {"precision": 1.0, "recall": 1.0,
                                                   "supporto": 1}
        assert m["per_classe"]["non_ammesso"]["recall"] == 0.0
        assert m["per_classe"]["non_ammesso"]["precision"] is None

    def test_vuoto(self):
        assert val.metriche_modalita([])["accuracy"] is None


class TestExactMatch:
    def test_none_vale_non_indicato(self):
        m = val.exact_match([(2, 2), (None, None), (3, None), (None, 4)])
        assert m == {"n": 4, "exact_match": 0.5}


class TestQuote:
    def test_tolleranza_e_abbinamento_uno_a_uno(self):
        attese = [[
            {"ambito": "per_partner", "categoria": None, "min": 10, "max": None},
            {"ambito": "capofila", "categoria": None, "min": 30, "max": 60},
        ]]
        predette = [[
            {"ambito": "per_partner", "categoria": None, "min": 10.4, "max": None},
            {"ambito": "per_partner", "categoria": None, "min": 10, "max": None},  # doppione
            {"ambito": "capofila", "categoria": None, "min": 31, "max": 60},  # oltre tolleranza
        ]]
        m = val.pr_quote(attese, predette)
        assert (m["tp"], m["fp"], m["fn"]) == (1, 2, 1)
        assert m["precision"] == round(1 / 3, 4) and m["recall"] == 0.5

    def test_categoria_deve_coincidere(self):
        m = val.pr_quote(
            [[{"ambito": "per_categoria", "categoria": "pmi", "min": 20, "max": None}]],
            [[{"ambito": "per_categoria", "categoria": "universita", "min": 20, "max": None}]],
        )
        assert (m["tp"], m["fp"], m["fn"]) == (0, 1, 1)


class TestCitazioniCosti:
    def test_percentuale_citazioni_verificate(self):
        regole = {
            "modalita": {"citazione": {"verificata": True}},
            "quote": [{"citazione": {"verificata": False}}, {"citazione": None}],
            "vincoli": [{"citazione": {"verificata": True}}],
        }
        assert val.citazioni_verificate([regole, None]) == {
            "verificate": 2, "totali": 3, "percentuale": round(2 / 3, 4)}

    def test_costo_latenza(self):
        m = val.costo_latenza([
            {"bando_id": 1, "cost_cents": 14, "latenza_s": 60.0},
            {"bando_id": 2, "cost_cents": 0, "latenza_s": 2.0},
        ])
        assert m["costo_totale_cents"] == 14 and m["costo_medio_cents"] == 7
        assert m["latenza_media_s"] == 31.0 and m["latenza_max_s"] == 60.0


class TestCalcolaMetriche:
    def test_su_campione_sintetico(self):
        campione = [
            {"bando_id": 1, "gruppo": "positivo", "etichetta": {
                "modalita": "ammesso", "partner_min": 2, "partner_max": None,
                "quote": [{"ambito": "per_partner", "categoria": None, "min": 20, "max": None}]}},
            {"bando_id": 2, "gruppo": "negativo", "etichetta": {
                "modalita": "non_determinabile", "partner_min": None, "partner_max": None,
                "quote": []}},
            {"bando_id": 3, "gruppo": "positivo", "etichetta": {"modalita": None}},
        ]
        regole_1 = {
            "modalita": {"valore": "ammesso", "effettiva": "ammesso",
                         "citazione": {"verificata": True}},
            "modalita_effettiva": "ammesso",
            "partner_min": {"valore": 2, "citazione": {"verificata": True}},
            "partner_max": {"valore": None, "citazione": None},
            "quote": [{"ambito": "per_partner", "categoria": None, "min_percentuale": 20.0,
                       "max_percentuale": None, "citazione": {"verificata": False}}],
        }
        risultati = {
            1: {"regole": regole_1, "cost_cents": 12, "latenza_s": 40.0},
            2: {"regole": None, "esito": "nessun_segnale", "cost_cents": 0, "latenza_s": 3.0},
            3: {"regole": None, "cost_cents": 0, "latenza_s": 1.0},
        }
        m = val.calcola_metriche(campione, risultati)
        assert m["etichettate"] == 2
        # bando 2 chiuso dalla guardia di costo (nessun_segnale) = non_determinabile:
        # la risposta giusta, non un errore del modello
        assert m["modalita"]["accuracy"] == 1.0
        assert m["partner_min"]["exact_match"] == 1.0  # 2 = 2 e None = None
        assert m["quote"]["tp"] == 1 and m["quote"]["fn"] == 0
        assert m["citazioni"]["totali"] == 3
        assert m["costi"]["costo_totale_cents"] == 12 and m["costi"]["n"] == 3

    def test_senza_regole_e_senza_nessun_segnale_resta_un_errore(self):
        campione = [{"bando_id": 1, "gruppo": "negativo", "etichetta": {
            "modalita": "non_determinabile", "quote": []}}]
        for esito in (None, "errore", "timeout"):
            m = val.calcola_metriche(campione, {1: {"regole": None, "esito": esito}})
            assert m["modalita"]["accuracy"] == 0.0, esito
        # l'esito della riga vale anche quando la pipeline non è ripartita
        m = val.calcola_metriche(
            campione, {1: {"regole": None, "esito": "fresca", "esito_riga": "nessun_segnale"}}
        )
        assert m["modalita"]["accuracy"] == 1.0

    def test_campi_non_raggiungibili_separati(self):
        campione = [
            {"bando_id": 1, "gruppo": "positivo", "etichetta": {
                "modalita": "ammesso", "partner_min": None, "partner_max": 5,
                "quote": [{"ambito": "per_partner", "categoria": None, "min": 10, "max": None}],
                "non_raggiungibili": ["partner_max", "quote"]}},
            {"bando_id": 2, "gruppo": "positivo", "etichetta": {
                "modalita": "obbligatorio", "partner_min": 2, "partner_max": None, "quote": []}},
        ]
        regole = {
            1: {"modalita_effettiva": "ammesso", "partner_min": {"valore": None},
                "partner_max": {"valore": None}, "quote": []},
            2: {"modalita_effettiva": "obbligatorio", "partner_min": {"valore": 2},
                "partner_max": {"valore": None}, "quote": []},
        }
        m = val.calcola_metriche(campione, {b: {"regole": r} for b, r in regole.items()})
        assert m["partner_max"] == {"n": 2, "exact_match": 0.5}
        assert m["quote"]["fn"] == 1
        # senza i campi che la pipeline non può leggere
        assert m["raggiungibili"]["partner_max"] == {"n": 1, "exact_match": 1.0}
        assert m["raggiungibili"]["quote"]["fn"] == 0
        assert m["raggiungibili"]["modalita"]["n"] == 2

    def test_risultato_da_regole_quote_usate_solo_verificate(self):
        regole = {"quote": [
            {"ambito": "per_partner", "categoria": None, "min_percentuale": 10.0,
             "max_percentuale": None, "stato": "verificata"},
            {"ambito": "capofila", "categoria": None, "min_percentuale": 30.0,
             "max_percentuale": None, "stato": "da_verificare"},
            {"ambito": "per_partner", "categoria": None, "min_percentuale": None,
             "max_percentuale": 50.0},  # senza stato: non usata
        ]}
        predetto = val.risultato_da_regole(regole)
        assert len(predetto["quote"]) == 3
        assert predetto["quote_usate"] == [
            {"ambito": "per_partner", "categoria": None, "min": 10.0, "max": None}]
        assert val.risultato_da_regole(None)["quote_usate"] == []

    def test_quote_usate_solo_sulle_verificate(self):
        campione = [
            {"bando_id": 1, "gruppo": "positivo", "etichetta": {
                "modalita": "ammesso", "partner_min": None, "partner_max": None,
                "quote": [
                    {"ambito": "per_partner", "categoria": None, "min": 10, "max": None},
                    {"ambito": "capofila", "categoria": None, "min": 30, "max": None},
                ]}},
            {"bando_id": 2, "gruppo": "positivo", "etichetta": {
                "modalita": "ammesso", "partner_min": None, "partner_max": None,
                "quote": [{"ambito": "per_partner", "categoria": None, "min": 5, "max": None}],
                "non_raggiungibili": ["quote"]}},
        ]

        def quota(ambito, categoria, minimo, massimo, stato):
            return {"ambito": ambito, "categoria": categoria, "min_percentuale": minimo,
                    "max_percentuale": massimo, "stato": stato}

        regole = {
            1: {"modalita_effettiva": "ammesso", "quote": [
                quota("per_partner", None, 10.0, None, "verificata"),
                # giusta ma da verificare: il prodotto non la preseleziona
                quota("capofila", None, 30.0, None, "da_verificare"),
                # sbagliata ma da verificare: non arriva al prodotto
                quota("per_categoria", "organismo_ricerca", None, 33.0, "da_verificare"),
            ]},
            2: {"modalita_effettiva": "ammesso", "quote": []},
        }
        m = val.calcola_metriche(campione, {b: {"regole": r} for b, r in regole.items()})
        assert (m["quote"]["tp"], m["quote"]["fp"], m["quote"]["fn"]) == (2, 1, 1)
        usate = m["quote_usate"]
        assert (usate["tp"], usate["fp"], usate["fn"]) == (1, 0, 2)
        assert usate["precision"] == 1.0 and usate["recall"] == round(1 / 3, 4)
        # anche senza i campi non raggiungibili, accanto a `quote`
        ragg = m["raggiungibili"]
        assert (ragg["quote_usate"]["tp"], ragg["quote_usate"]["fn"]) == (1, 1)
        assert (ragg["quote"]["tp"], ragg["quote"]["fn"]) == (2, 0)

    def test_recall_preclassificatore_sull_etichetta(self):
        analisi = [
            # gruppo «negativo» ma etichetta ammesso: è un positivo
            {"gruppo": "negativo", "modalita_attesa": "ammesso", "livello": "forte"},
            # gruppo «positivo» ma etichetta non_determinabile: è un negativo
            {"gruppo": "positivo", "modalita_attesa": "non_determinabile", "livello": "nessuno"},
            {"gruppo": "positivo", "modalita_attesa": "non_ammesso", "livello": "debole"},
        ]
        m = val.recall_preclassificatore(analisi)
        assert (m["positivi"], m["negativi"]) == (2, 1)
        assert m["recall"] == 1.0 and m["negativi_con_segnali_forti"] == 0

    def test_recall_preclassificatore(self):
        analisi = [
            {"gruppo": "positivo", "livello": "forte"},
            {"gruppo": "positivo", "livello": "debole"},
            {"gruppo": "positivo", "livello": "nessuno"},
            {"gruppo": "negativo", "livello": "forte"},
            {"gruppo": "negativo", "livello": "nessuno"},
        ]
        m = val.recall_preclassificatore(analisi)
        assert m["recall"] == round(2 / 3, 4) and m["recall_forte"] == round(1 / 3, 4)
        assert m["negativi_con_segnali_forti"] == 1


class TestCampione:
    CHIAVI_VOCE = {"bando_id", "slug_prefisso", "tipologia_bando_id", "gruppo", "etichetta"}
    CHIAVI_ETICHETTA = {"modalita", "partner_min", "partner_max", "quote", "forme",
                        "verificato_da", "verificato_at", "fonte_verificata"}
    FACOLTATIVE_ETICHETTA = {"non_raggiungibili"}
    CHIAVI_QUOTA = {"ambito", "categoria", "min", "max"}

    def test_campione_versionato(self):
        """Solo id, slug troncato, gruppo ed etichette: nessun testo dei
        documenti né URL (stanno nelle note FUORI dal repo)."""
        dati = json.loads(val.CAMPIONE_PREDEFINITO.read_text())
        assert set(dati) == {"versione", "descrizione", "campione"}
        campione = val.carica_campione()
        assert len(campione) == len(dati["campione"]) == 25
        assert len({v["bando_id"] for v in campione}) == 25
        assert {v["gruppo"] for v in campione} == {"positivo", "negativo"}
        for voce in campione:
            assert set(voce) == self.CHIAVI_VOCE, voce["bando_id"]
            etichetta = voce["etichetta"]
            chiavi = set(etichetta)
            assert self.CHIAVI_ETICHETTA <= chiavi <= (
                self.CHIAVI_ETICHETTA | self.FACOLTATIVE_ETICHETTA), voce["bando_id"]
            assert etichetta["modalita"] in (*val.MODALITA, None)
            for campo in ("partner_min", "partner_max"):
                assert etichetta[campo] is None or isinstance(etichetta[campo], int)
            for quota in etichetta["quote"]:
                assert set(quota) == self.CHIAVI_QUOTA
            assert all(isinstance(f, str) and len(f) <= 40 for f in etichetta["forme"])
            assert set(etichetta.get("non_raggiungibili") or []) <= {
                "modalita", "partner_min", "partner_max", "quote", "forme"}
            assert len(voce["slug_prefisso"]) <= 80
        # nessun URL, con o senza schema, e nessun testo lungo fuori dalla descrizione
        testo = val.CAMPIONE_PREDEFINITO.read_text()
        for vietato in ("http", "://", "www.", ".pdf", ".it/", ".eu/", ".com/"):
            assert vietato not in testo.lower(), vietato

        def stringhe(nodo):
            if isinstance(nodo, str):
                yield nodo
            elif isinstance(nodo, list):
                for voce in nodo:
                    yield from stringhe(voce)
            elif isinstance(nodo, dict):
                for valore in nodo.values():
                    yield from stringhe(valore)

        assert max(len(t) for t in stringhe(dati["campione"])) <= 80


# ------------------------------------------------------------ CLI senza rete

PAGINA = ("Art. 2 - Soggetti ammessi. Le imprese partecipano in forma singola o associata "
          "mediante ATS. ") * 5


class FakeSecondary:
    def __init__(self, bandi):
        self.bandi = bandi

    def table(self, nome):
        secondario = self

        class _Q:
            def __init__(self):
                self.filtri = {}

            def select(self, *a, **k):
                return self

            def eq(self, c, v):
                self.filtri[c] = v
                return self

            def limit(self, *a):
                return self

            async def execute(self):
                dati = [b for b in secondario.bandi
                        if all(b.get(c) == v for c, v in self.filtri.items() if c in ("id", "slug"))]
                return SimpleNamespace(data=dati)

        return _Q()


@pytest.fixture
def senza_rete(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
    }.items():
        monkeypatch.setenv(chiave, valore)
    from app.core.config import get_settings

    get_settings.cache_clear()
    bandi = [
        {"id": 1, "slug": "uno", "titolo": "Bando uno", "contenuto": None, "allegati": []},
        {"id": 2, "slug": "due", "titolo": "Bando due", "contenuto": None, "allegati": []},
    ]
    download: list[str] = []

    async def crea_secondario():
        return FakeSecondary(bandi)

    async def fetch(secondary, slug):
        return next(b for b in bandi if b["slug"] == slug)

    async def link(secondary, bando_id):
        if bando_id == 2:
            return []
        return [LinkDocumento(id=1, bando_id=1, url="https://ente.example.it/avviso.pdf",
                              dominio="ente.example.it", tipo="allegato", etichetta="Avviso",
                              content_type="application/pdf", ultimo_visto_at=None)]

    async def scarica(url, *, max_bytes, timeout_s, **k):
        download.append(url)
        return DocumentoScaricato(stato="ok", sha256="x", byte=10, contenuto=b"%PDF-1.7")

    async def estrai(pdf_bytes, *, max_pagine, timeout_s, **k):
        return _TestoPdf(stato="letto", pagine_totali=1, pagine=[(1, PAGINA)],
                         caratteri=len(PAGINA))

    monkeypatch.setattr(val, "_crea_secondario", crea_secondario)
    monkeypatch.setattr("app.services.bandi_service.fetch_bando_for_ai", fetch)
    monkeypatch.setattr("app.services.bando_fonti_service.leggi_link_documenti", link)
    monkeypatch.setattr("app.services.download_sicuro.scarica_pdf", scarica)
    monkeypatch.setattr("app.services.pdf_testo.estrai_testo", estrai)
    yield download
    get_settings.cache_clear()


def scrivi_campione(tmp_path):
    percorso = tmp_path / "campione.json"
    percorso.write_text(json.dumps({"campione": [
        {"bando_id": 1, "gruppo": "positivo", "etichetta": {"modalita": None}},
        {"bando_id": 2, "gruppo": "negativo", "etichetta": {"modalita": None}},
    ]}))
    return percorso


class TestCli:
    def test_offline_senza_rete(self, senza_rete, tmp_path):
        out = tmp_path / "offline.json"
        codice = val.main(["--offline", "--campione", str(scrivi_campione(tmp_path)),
                           "--out", str(out)])
        assert codice == 0
        risultato = json.loads(out.read_text())
        assert risultato["preclassificatore"]["recall"] == 1.0
        assert risultato["preclassificatore"]["negativi_con_segnali_forti"] == 0
        assert risultato["copertura"] == {"bandi": 2, "con_documenti_letti": 1,
                                          "senza_documenti": 1, "errori": 0}
        primo = risultato["bandi"][0]
        assert primo["documenti"]["letti"] == 1 and primo["token_input_stimati"] > 0
        assert primo["costo_max_cents"] >= primo["costo_tipico_cents"] > 0
        assert "_sezioni" not in primo and "segnali_dettaglio" not in primo
        assert senza_rete == ["https://ente.example.it/avviso.pdf"]

    def test_prepara_rifiuta_una_cartella_nel_repository(self, senza_rete, tmp_path):
        dentro = val._REPO / "tmp_valutazione_non_creare"
        codice = val.main(["--prepara", str(dentro), "--campione",
                           str(scrivi_campione(tmp_path))])
        assert codice == 2
        assert not dentro.exists()

    def test_prepara_fuori_dal_repository(self, senza_rete, tmp_path):
        cartella = tmp_path / "fogli"
        codice = val.main(["--prepara", str(cartella), "--campione",
                           str(scrivi_campione(tmp_path))])
        assert codice == 0
        foglio = json.loads((cartella / "bando_1.json").read_text())
        sezioni = [p["sezione"] for p in foglio["pagine_rilevanti"]]
        assert "D1-p1" in sezioni and "META" in sezioni
        assert (cartella / "bando_2.json").exists()

    def test_reale_senza_conferma_non_spende(self, senza_rete, tmp_path, monkeypatch, capsys):
        async def vietato():
            raise AssertionError("senza --conferma non si crea né il primario né il modello")

        monkeypatch.setattr(val, "_crea_primario_e_ai", vietato)
        codice = val.main(["--reale", "--tetto-cents", "50", "--campione",
                           str(scrivi_campione(tmp_path))])
        assert codice == 0
        errori = capsys.readouterr().err
        assert "Stima:" in errori and "Nessuna spesa" in errori

    def test_reale_richiede_il_tetto(self, senza_rete, tmp_path):
        assert val.main(["--reale", "--campione", str(scrivi_campione(tmp_path))]) == 2
        assert val.main(["--reale", "--tetto-cents", "0", "--campione",
                         str(scrivi_campione(tmp_path))]) == 2

    def test_reale_con_conferma_si_ferma_al_tetto(self, senza_rete, tmp_path, monkeypatch):
        from app.core.errors import AppError

        class Ai:
            enabled = True
            chiuso = False

            async def aclose(self):
                Ai.chiuso = True

        class Primario:
            pass

        async def crea():
            return Primario(), Ai()

        chiamate: list = []

        async def esegui(primary, secondary, ai, bando, *, origine, budget_cents):
            chiamate.append((bando["id"], origine, budget_cents))
            if bando["id"] == 2:
                raise AppError(429, "ai_sospesa_oggi", "sospesa")
            return "estratta"

        async def leggi(primary, bando_id):
            return {"regole": {"modalita_effettiva": "ammesso"}, "cost_cents": 9}

        monkeypatch.setattr(val, "_crea_primario_e_ai", crea)
        monkeypatch.setattr("app.services.partenariato_service.esegui_per_bando", esegui)
        monkeypatch.setattr("app.services.partenariato_service._leggi_riga", leggi)
        out = tmp_path / "reale.json"
        codice = val.main(["--reale", "--tetto-cents", "50", "--conferma", "--campione",
                           str(scrivi_campione(tmp_path)), "--out", str(out)])
        assert codice == 0
        assert chiamate == [(1, "valutazione", 50), (2, "valutazione", 50)]
        reale = json.loads(out.read_text())["reale"]
        assert reale["fermato"] == "tetto_raggiunto"
        assert reale["metriche"]["costi"]["costo_totale_cents"] == 9
        assert Ai.chiuso is True
