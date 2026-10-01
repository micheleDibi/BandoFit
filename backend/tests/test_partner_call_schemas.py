"""Schemi delle call di partenariato (app/schemas/partner_call.py) e unione
discriminata dei criteri (app/schemas/partenariato_criteri.py).

Proprietà difese:
- snapshot delle regole: `confermata` solo con citazione verificata,
  `aggiunta` senza citazione, `modificata` libera; regole finanziarie solo
  confermate e coerenti (`valida_regola`); `extra='forbid'` ovunque;
- `CriterioPartner` discriminato e STRICT (niente coercizioni, niente campi
  in più, solo codici del vocabolario);
- regole di dominio → 400 `bad_request` con messaggio (anche attraverso
  FastAPI), tipi e campi sconosciuti → 422;
- Q11: nessuna regola finanziaria scritta a mano nei requisiti;
- `SegnalazioneIn.buona_fede` deve essere proprio `true`;
- proiezioni verso terzi a whitelist: i campi riservati non esistono.
"""

from datetime import date
from decimal import Decimal
from typing import get_args
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import BadRequestError, register_exception_handlers
from app.schemas import partner_call as pcs
from app.schemas.partenariato_criteri import CRITERIO_ADAPTER, CriterioRegolaFinanziaria
from app.schemas.partner_call import (
    CallAggiornaIn,
    CallCardOut,
    CallCreaIn,
    CallPubblicaOut,
    CreatoreCallOut,
    PosizioneIn,
    PosizioniIn,
    RegoleCallSnapshot,
    RegoleConfermaIn,
    RequisitiIn,
    RequisitoIn,
    SegnalazioneIn,
)

# ----------------------------------------------------------------- aiuti


def citazione(verificata=True) -> dict:
    return {"sezione": "D1-p3", "fonte_etichetta": "Avviso — pag. 3",
            "testo": "Il partenariato è composto da almeno tre soggetti", "verificata": verificata}


MODALITA = {"valore": "obbligatorio", "origine_voce": "confermata", "citazione": citazione()}


def regola(**sovrascrivi) -> dict:
    dati = {
        "id": "F1", "descrizione": "Costo della quota al massimo il 60% del fatturato medio",
        "ambito": "ciascun_partner", "numeratore": "costo_quota",
        "denominatore": "fatturato_medio_2", "operatore": "le", "soglia": "0.6",
        "soglia_variabile": None, "soglia_coefficiente": None, "unita": "rapporto",
        "origine_voce": "confermata", "citazione": citazione(),
    }
    dati.update(sovrascrivi)
    return dati


def snapshot(**voci) -> RegoleCallSnapshot:
    return RegoleCallSnapshot.model_validate({"modalita": MODALITA, **voci})


def errore_400(funzione, *args, **kwargs) -> str:
    with pytest.raises(BadRequestError) as exc:
        funzione(*args, **kwargs)
    assert exc.value.status_code == 400 and exc.value.code == "bad_request"
    return exc.value.message


# ------------------------------------------------------------- snapshot


class TestSnapshot:
    def test_minimo_valido(self):
        s = snapshot()
        assert s.versione == 1 and s.fonte is None
        assert s.composizione == [] and s.regole_finanziarie == []

    def test_confermata_modificata_aggiunta(self):
        s = snapshot(
            costituzione={"valore": "costituenda_ammessa", "origine_voce": "modificata"},
            partner_min={"valore": 3, "origine_voce": "confermata", "citazione": citazione()},
            composizione=[
                {"id": "K1", "tipo_soggetto": "organismo_ricerca", "minimo": 1,
                 "ruolo": "partner", "origine_voce": "aggiunta"},
                {"id": "K2", "tipo_soggetto": "pmi", "minimo": 2, "ruolo": "qualsiasi",
                 "origine_voce": "modificata", "citazione": citazione(verificata=False)},
            ],
        )
        assert [c.origine_voce for c in s.composizione] == ["aggiunta", "modificata"]

    def test_confermata_senza_citazione_verificata_400(self):
        for cit in (None, citazione(verificata=False)):
            messaggio = errore_400(
                snapshot,
                partner_min={"valore": 3, "origine_voce": "confermata", "citazione": cit},
            )
            assert "modificala" in messaggio

    def test_aggiunta_con_citazione_400(self):
        errore_400(
            snapshot,
            composizione=[{"id": "K1", "tipo_soggetto": "pmi", "ruolo": "partner",
                           "origine_voce": "aggiunta", "citazione": citazione()}],
        )

    def test_modalita_non_ammesso_400(self):
        errore_400(
            RegoleCallSnapshot.model_validate,
            {"modalita": {"valore": "non_ammesso", "origine_voce": "modificata"}},
        )

    def test_regola_finanziaria_confermata_valida(self):
        s = snapshot(regole_finanziarie=[regola()])
        contratto = s.regole_finanziarie[0].regola()
        assert contratto.soglia == "0.6" and not hasattr(contratto, "origine_voce")

    def test_regola_finanziaria_non_coerente_400(self):
        # soglia e soglia_variabile insieme: valida_regola la respinge
        messaggio = errore_400(
            snapshot, regole_finanziarie=[regola(soglia_variabile="fatturato")]
        )
        assert "non valida" in messaggio
        errore_400(snapshot, regole_finanziarie=[regola(unita="euro")])
        errore_400(snapshot, regole_finanziarie=[regola(citazione=citazione(verificata=False))])

    @pytest.mark.parametrize("origine", ["modificata", "aggiunta"])
    def test_regola_finanziaria_solo_confermata_q11(self, origine):
        messaggio = errore_400(
            snapshot, regole_finanziarie=[regola(origine_voce=origine, citazione=None)]
        )
        assert "solo confermare" in messaggio
        with pytest.raises(ValidationError):
            snapshot(regole_finanziarie=[regola(origine_voce="inventata")])
        errore_400(snapshot, regole_finanziarie=[regola(citazione=None)])

    def test_extra_forbid_ovunque(self):
        with pytest.raises(ValidationError):
            RegoleCallSnapshot.model_validate({"modalita": MODALITA, "note": "x"})
        with pytest.raises(ValidationError):
            snapshot(modalita={**MODALITA, "stato": "verificata"})
        with pytest.raises(ValidationError):
            snapshot(vincoli=[{"id": "V1", "tipo": "altro", "descrizione": "Vincolo",
                               "momento": "domanda", "origine_voce": "modificata",
                               "avvisi": []}])
        with pytest.raises(ValidationError):
            snapshot(regole_finanziarie=[regola(stato="verificata")])
        with pytest.raises(ValidationError):
            snapshot(modalita={**MODALITA, "citazione": {**citazione(), "html": "<b>"}})

    def test_coerenze_400(self):
        doppio = {"id": "K1", "tipo_soggetto": "pmi", "ruolo": "partner",
                  "origine_voce": "modificata"}
        errore_400(snapshot, composizione=[doppio, doppio])
        errore_400(
            snapshot,
            partner_min={"valore": 5, "origine_voce": "modificata"},
            partner_max={"valore": 3, "origine_voce": "modificata"},
        )
        errore_400(snapshot, partner_min={"valore": 0, "origine_voce": "modificata"})
        errore_400(
            snapshot,
            quote=[{"id": "Q1", "ambito": "per_partner", "min_percentuale": 80,
                    "max_percentuale": 20, "base_calcolo": "non_indicata",
                    "effetto_violazione": "non_indicato", "origine_voce": "modificata"}],
        )
        errore_400(
            snapshot,
            quote=[{"id": "Q1", "ambito": "per_partner", "min_percentuale": 120,
                    "base_calcolo": "non_indicata", "effetto_violazione": "non_indicato",
                    "origine_voce": "modificata"}],
        )
        errore_400(
            snapshot,
            forme_ammesse=[{"forma": "ats", "origine_voce": "modificata"},
                           {"forma": "ats", "origine_voce": "modificata"}],
        )

    def test_conferma_in(self):
        dati = RegoleConfermaIn.model_validate(
            {"regole": {"modalita": MODALITA}, "esclusivita": True}
        )
        assert dati.esclusivita is True
        with pytest.raises(ValidationError):
            RegoleConfermaIn.model_validate({"regole": {"modalita": MODALITA},
                                             "esclusivita": "sì"})


# -------------------------------------------------------------- criteri


class TestCriterioStrict:
    @pytest.mark.parametrize(
        "dati",
        [
            {"tipo": "inventato"},
            {"valori": ["pmi"]},  # manca il discriminante
            {"tipo": "regione", "regioni_ids": ["9"]},  # niente coercizione
            {"tipo": "regione", "regioni_ids": [True]},
            {"tipo": "regione", "regioni_ids": [0]},
            {"tipo": "regione", "regioni_ids": []},
            {"tipo": "regione", "regioni_ids": [9], "modalita": "ovunque"},
            {"tipo": "ateco", "divisioni": ["620"]},
            {"tipo": "ateco", "divisioni": [62]},
            {"tipo": "paese", "paesi": ["XX"]},
            {"tipo": "paese", "paesi": ["IT"], "escludi": "no"},
            {"tipo": "tag", "tags": ["prototipazione_rapida"]},
            {"tipo": "tag", "tags": ["cybersecurity"], "modalita": "qualcuno"},
            {"tipo": "manuale", "testo": "extra"},
            {"tipo": "tipo_soggetto", "valori": ["pmi"], "ambito": "consorzio"},
            {"tipo": "regola_finanziaria", "regola": {"id": "F1"}},
        ],
    )
    def test_rifiutati(self, dati):
        with pytest.raises(ValidationError):
            CRITERIO_ADAPTER.validate_python(dati)

    def test_normalizzazioni(self):
        paese = CRITERIO_ADAPTER.validate_python({"tipo": "paese", "paesi": ["el", "IT", "it"]})
        assert paese.paesi == ["GR", "IT"]
        tag = CRITERIO_ADAPTER.validate_python(
            {"tipo": "tag", "tags": ["cybersecurity", "cybersecurity"]}
        )
        assert tag.tags == ["cybersecurity"] and tag.modalita == "almeno_uno"

    def test_andata_e_ritorno_json(self):
        dati = {"tipo": "esperienza", "programmi_ids": [3, 4], "ruolo": "capofila"}
        criterio = CRITERIO_ADAPTER.validate_python(dati)
        testo = CRITERIO_ADAPTER.dump_json(criterio)
        assert CRITERIO_ADAPTER.validate_json(testo) == criterio

    def test_regola_nel_criterio_senza_campi_in_piu(self):
        contratto = {k: v for k, v in regola().items() if k not in ("origine_voce", "citazione")}
        c = CRITERIO_ADAPTER.validate_python({"tipo": "regola_finanziaria", "regola": contratto})
        assert isinstance(c, CriterioRegolaFinanziaria)
        with pytest.raises(ValidationError):
            CRITERIO_ADAPTER.validate_python(
                {"tipo": "regola_finanziaria", "regola": {**contratto, "citazione": citazione()}}
            )


# ------------------------------------------------------------- requisiti


class TestRequisitoIn:
    def test_valido_e_default(self):
        r = RequisitoIn(testo="  Almeno un organismo di ricerca  ",
                        criterio={"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]})
        assert r.testo == "Almeno un organismo di ricerca"
        assert r.origine == "manuale" and r.ambito == "consorzio" and r.cercato is False

    def test_regola_finanziaria_manuale_vietata_q11(self):
        contratto = {k: v for k, v in regola().items() if k not in ("origine_voce", "citazione")}
        criterio = {"tipo": "regola_finanziaria", "regola": contratto}
        errore_400(RequisitoIn, testo="Regola scritta a mano", criterio=criterio)
        errore_400(RequisitoIn, testo="Regola senza riferimento", criterio=criterio,
                   origine="regola_finanziaria")
        errore_400(RequisitoIn, testo="Origine senza regola", origine="regola_finanziaria",
                   rif_origine="F1", criterio={"tipo": "manuale"})
        ok = RequisitoIn(testo="Regola del bando", criterio=criterio,
                         origine="regola_finanziaria", rif_origine="F1")
        assert ok.criterio.regola.soglia == "0.6"

    def test_domini_400_e_tipi_422(self):
        errore_400(RequisitoIn, testo="ab")
        errore_400(RequisitoIn, testo="x" * 501)
        errore_400(RequisitoIn, testo="Testo valido", etichetta="E" * 61)
        errore_400(RequisitoIn, testo="Testo valido", rif_origine="R" * 41)
        for extra in ({"copertura_creatore": "coperto"}, {"copertura_nota": "x"},
                      {"cercato": "true"}, {"ambito": "tutti"}):
            with pytest.raises(ValidationError):
                RequisitoIn(testo="Testo valido", **extra)
        with pytest.raises(ValidationError):
            RequisitoIn(testo="Testo valido",
                        citazione={**citazione(), "url_documento": "http://x.it/a.pdf"})

    def test_lista(self):
        uno = str(uuid4())
        errore_400(RequisitiIn.model_validate,
                   {"requisiti": [{"id": uno, "testo": "Primo"}, {"id": uno, "testo": "Bis"}]})
        errore_400(RequisitiIn.model_validate,
                   {"requisiti": [{"testo": f"Requisito {i}"} for i in range(41)]})


# ------------------------------------------------------------- posizioni


class TestPosizioneIn:
    def test_valida(self):
        p = PosizioneIn(
            titolo="Organismo di ricerca", tipi_soggetto=["organismo_ricerca"] * 2,
            competenze=["prototipazione_testing"], ateco_divisioni=["72"], regioni=[4, 4],
            territorio_modalita="sede_entro_erogazione", paesi=["it", "EL"],
            dimensioni=["piccola"], quota_ipotizzata_pct="30.50", numero=2,
        )
        assert p.tipi_soggetto == ["organismo_ricerca"] and p.regioni == [4]
        assert p.paesi == ["IT", "GR"] and p.quota_ipotizzata_pct == Decimal("30.50")

    def test_territorio_coerente(self):
        errore_400(PosizioneIn, titolo="Partner", territorio_modalita="sede_attuale")
        errore_400(PosizioneIn, titolo="Partner", regioni=[4])

    def test_domini(self):
        errore_400(PosizioneIn, titolo="ab")
        errore_400(PosizioneIn, titolo="Partner", ateco_divisioni=["620"])
        errore_400(PosizioneIn, titolo="Partner", paesi=["XX"])
        errore_400(PosizioneIn, titolo="Partner", quota_ipotizzata_pct="0")
        errore_400(PosizioneIn, titolo="Partner", quota_ipotizzata_pct="100.5")
        errore_400(PosizioneIn, titolo="Partner", quota_ipotizzata_pct="10.123")
        errore_400(PosizioneIn, titolo="Partner",
                   tipi_soggetto=["pmi", "universita", "impresa", "fondazione", "altro",
                                  "ente_locale"])
        errore_400(PosizioneIn, titolo="Partner", requisiti_ids=[uuid4() for _ in range(21)])
        with pytest.raises(ValidationError):
            PosizioneIn(titolo="Partner", competenze=["inventata"])
        with pytest.raises(ValidationError):
            PosizioneIn(titolo="Partner", numero=11)
        with pytest.raises(ValidationError):
            PosizioneIn(titolo="Partner", candidature=3)

    def test_lista(self):
        errore_400(PosizioniIn.model_validate,
                   {"posizioni": [{"titolo": f"Posizione {i}"} for i in range(11)]})


# ---------------------------------------------------------------- call


class TestCallIn:
    def test_crea(self):
        c = CallCreaIn(bando_slug="bando-x", ruolo_creatore="capofila")
        assert c.anonima is True and c.override_non_ammesso_motivo is None
        # false passa lo schema: il servizio risponde 409 nominativo_non_disponibile
        assert CallCreaIn(bando_slug="b", ruolo_creatore="capofila", anonima=False).anonima is False
        errore_400(CallCreaIn, bando_slug="b", ruolo_creatore="capofila",
                   override_non_ammesso_motivo="troppo corto")
        for extra in ({"company_profile_id": str(uuid4())}, {"stato": "pubblicata"}):
            with pytest.raises(ValidationError):
                CallCreaIn(bando_slug="b", ruolo_creatore="capofila", **extra)
        with pytest.raises(ValidationError):
            CallCreaIn(
                bando_slug="b", ruolo_creatore="capofila", forma_aggregazione_prevista="altra"
            )

    def test_testi_senza_caratteri_invisibili(self):
        """Nel DB finisce il testo senza caratteri di formato invisibili e con
        gli spazi Unicode normali; i caratteri visibili restano (NFKC solo nei
        controlli)."""
        a = CallAggiornaIn(
            titolo="​Cerco partner per la logistica⁠",
            descrizione_pubblica="Riga uno riga due ­senza trattino ﻿1º posto",
        )
        assert a.titolo == "Cerco partner per la logistica"
        assert a.descrizione_pubblica == "Riga uno\nriga due senza trattino 1º posto"
        assert pcs.forma_canonica("３４７​1") == "3471"
        # solo invisibili: come vuoto
        assert CallAggiornaIn(descrizione_pubblica="​​").descrizione_pubblica is None

    def test_aggiorna_parziale(self):
        a = CallAggiornaIn.model_validate(
            {"titolo": "  Cerco partner per un progetto  ", "descrizione_pubblica": None,
             "budget_progetto_eur": "1200000.50", "scadenza_call": "2026-12-01"}
        )
        assert a.campi() == {
            "titolo": "Cerco partner per un progetto",
            "descrizione_pubblica": None,
            "budget_progetto_eur": "1200000.50",
            "scadenza_call": "2026-12-01",
        }
        assert a.scadenza_call == date(2026, 12, 1)
        assert CallAggiornaIn().campi() == {}

    def test_aggiorna_null_su_obbligatori_422(self):
        for campo in ("visibilita", "ruolo_creatore", "anonima", "wizard_passo"):
            with pytest.raises(ValidationError):
                CallAggiornaIn.model_validate({campo: None})

    def test_aggiorna_domini(self):
        errore_400(CallAggiornaIn, titolo="Corto")
        errore_400(CallAggiornaIn, descrizione_pubblica="x" * 3001)
        errore_400(CallAggiornaIn, dettagli_riservati="x" * 5001)
        errore_400(CallAggiornaIn, profilo_partner_ideale="x" * 2001)
        errore_400(CallAggiornaIn, budget_progetto_eur="0")
        errore_400(CallAggiornaIn, budget_progetto_eur="10.001")
        errore_400(CallAggiornaIn, budget_progetto_eur="1000000000000")
        errore_400(CallAggiornaIn, quota_creatore_pct="101")
        messaggio = errore_400(CallAggiornaIn, budget_fascia="50k_150k",
                               budget_progetto_eur="200000")
        assert "fascia" in messaggio
        assert CallAggiornaIn(budget_fascia="50k_150k", budget_progetto_eur="150000")
        with pytest.raises(ValidationError):
            CallAggiornaIn(wizard_passo=8)
        for extra in ({"stato": "pubblicata"}, {"versione": 3}, {"regole_partenariato": {}},
                      {"esclusivita": True}, {"family_parent_id": str(uuid4())}):
            with pytest.raises(ValidationError):
                CallAggiornaIn.model_validate(extra)

    def test_fasce_di_budget(self):
        assert list(pcs.ESTREMI_BUDGET) == list(get_args(pcs.BudgetFascia))
        assert pcs.budget_nella_fascia("fino_50k", Decimal("50000"))
        assert not pcs.budget_nella_fascia("50k_150k", Decimal("50000"))
        assert pcs.budget_nella_fascia("oltre_5m", Decimal("9999999"))


# ----------------------------------------------------------- segnalazioni


class TestSegnalazioneIn:
    BASE = {"oggetto_tipo": "call", "motivo": "contatti_nel_testo",
            "descrizione": "Nel testo c'è un numero di telefono"}

    def test_buona_fede_vera(self):
        s = SegnalazioneIn(**self.BASE, oggetto_id=str(uuid4()).upper(), buona_fede=True)
        assert s.buona_fede is True and s.oggetto_id == s.oggetto_id.lower()

    def test_buona_fede_falsa_400(self):
        messaggio = errore_400(SegnalazioneIn, **self.BASE, oggetto_id=str(uuid4()),
                               buona_fede=False)
        assert "buona fede" in messaggio

    @pytest.mark.parametrize("valore", [1, "true", "sì", None])
    def test_buona_fede_non_booleana_422(self, valore):
        with pytest.raises(ValidationError):
            SegnalazioneIn(**self.BASE, oggetto_id=str(uuid4()), buona_fede=valore)

    def test_buona_fede_obbligatoria(self):
        with pytest.raises(ValidationError):
            SegnalazioneIn(**self.BASE, oggetto_id=str(uuid4()))

    def test_domini(self):
        errore_400(SegnalazioneIn, **self.BASE, oggetto_id="42", buona_fede=True)
        errore_400(SegnalazioneIn, **{**self.BASE, "descrizione": "breve"},
                   oggetto_id=str(uuid4()), buona_fede=True)
        with pytest.raises(ValidationError):  # dal WP7 «messaggio» è ammesso
            SegnalazioneIn(**{**self.BASE, "oggetto_tipo": "conversazione"},
                           oggetto_id=str(uuid4()), buona_fede=True)
        with pytest.raises(ValidationError):
            SegnalazioneIn(**self.BASE, oggetto_id=str(uuid4()), buona_fede=True,
                           segnalante_user_id=str(uuid4()))


# ---------------------------------------------------- proiezioni per terzi


RISERVATI = {
    "company_profile_id", "family_parent_id", "creato_da", "budget_progetto_eur",
    "dettagli_riservati", "copertura_creatore", "copertura_nota", "copertura_fonte",
    "fasce", "override_non_ammesso_motivo", "regole_partenariato", "quota_creatore_pct",
}


def _campi_ricorsivi(modello, visti=None) -> set[str]:
    visti = visti if visti is not None else set()
    nomi: set[str] = set()
    for nome, campo in modello.model_fields.items():
        nomi.add(nome)
        for tipo in (campo.annotation, *get_args(campo.annotation)):
            for interno in (tipo, *get_args(tipo)):
                if isinstance(interno, type) and hasattr(interno, "model_fields") \
                        and interno not in visti:
                    visti.add(interno)
                    nomi |= _campi_ricorsivi(interno, visti)
    return nomi


class TestProiezioni:
    def test_whitelist_senza_campi_riservati(self):
        for modello in (CallPubblicaOut, CallCardOut):
            assert not (_campi_ricorsivi(modello) & RISERVATI), modello.__name__

    def test_campi_non_previsti_rifiutati_in_costruzione(self):
        base = {
            "id": uuid4(), "stato": "pubblicata",
            "bando": {"slug": "b", "titolo": "Bando"},
            "creatore": {"regione": "Calabria"}, "ruolo_creatore": "capofila",
        }
        call = CallPubblicaOut.model_validate(base)
        assert call.creatore.denominazione == "Azienda anonima" and call.creatore.anonima
        for extra in ({"company_profile_id": uuid4()}, {"budget_progetto_eur": 10},
                      {"dettagli_riservati": "x"}):
            with pytest.raises(ValidationError):
                CallPubblicaOut.model_validate({**base, **extra})
        with pytest.raises(ValidationError):
            CreatoreCallOut(fasce={"fatturato": "fino_100k"})
        with pytest.raises(ValidationError):
            CallPubblicaOut.model_validate(
                {**base, "requisiti": [{"etichetta": "A", "testo": "T",
                                        "copertura_creatore": "coperto"}]}
            )


# ------------------------------------------------- attraverso FastAPI: 400


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.put("/requisiti")
    async def requisiti(dati: RequisitiIn) -> dict:
        return {"n": len(dati.requisiti)}

    @app.post("/regole")
    async def regole(dati: RegoleConfermaIn) -> dict:
        return {"voci": len(dati.regole.regole_finanziarie)}

    @app.post("/segnalazioni")
    async def segnala(dati: SegnalazioneIn) -> dict:
        return {"ok": dati.buona_fede}

    return TestClient(app)


def test_api_regola_scritta_a_mano_400(client):
    contratto = {k: v for k, v in regola().items() if k not in ("origine_voce", "citazione")}
    r = client.put("/requisiti", json={"requisiti": [
        {"testo": "Regola", "criterio": {"tipo": "regola_finanziaria", "regola": contratto}},
    ]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_api_criterio_non_strict_422(client):
    r = client.put("/requisiti", json={"requisiti": [
        {"testo": "Sede", "criterio": {"tipo": "regione", "regioni_ids": ["9"]}},
    ]})
    assert r.status_code == 422


def test_api_criterio_valido_200(client):
    r = client.put("/requisiti", json={"requisiti": [
        {"testo": "Sede", "criterio": {"tipo": "regione", "regioni_ids": [9]},
         "ambito": "ogni_membro"},
    ]})
    assert r.status_code == 200 and r.json() == {"n": 1}


def test_api_regola_incoerente_nello_snapshot_400(client):
    r = client.post("/regole", json={
        "regole": {"modalita": MODALITA,
                   "regole_finanziarie": [regola(soglia_variabile="fatturato")]},
        "esclusivita": False,
    })
    assert r.status_code == 400


def test_api_buona_fede(client):
    base = {"oggetto_tipo": "profilo", "oggetto_id": str(uuid4()), "motivo": "altro",
            "descrizione": "Descrizione sufficientemente lunga"}
    assert client.post("/segnalazioni", json={**base, "buona_fede": True}).status_code == 200
    assert client.post("/segnalazioni", json={**base, "buona_fede": False}).status_code == 400
    assert client.post("/segnalazioni", json={**base, "buona_fede": 1}).status_code == 422


# ---------------------------------------- citazioni in uscita (filtro dei link)


class TestCitazioneSnapshotOut:
    """In uscita (requisiti, voci e regole finanziarie dello snapshot) l'URL
    del documento passa dal filtro dei link della scheda, anche sulle righe
    salvate prima del filtro: un URL non ammesso diventa None, mai un errore.
    `RequisitoIn` resta con il solo controllo https."""

    NON_AMMESSI = ["https://www.fasi.eu/a.pdf", "https://x.it/a\x01.pdf",
                   "https://x.it/a.pdf https://fasi.eu/b.pdf", "https://x.it:99999/a.pdf"]

    @pytest.mark.parametrize("url", NON_AMMESSI)
    def test_voce_dello_snapshot_con_url_non_ammesso(self, url):
        out = snapshot(modalita={**MODALITA, "citazione": {**citazione(), "url_documento": url}})
        assert isinstance(out.modalita.citazione, pcs.CitazioneSnapshotOut)
        assert out.modalita.citazione.url_documento is None
        assert out.modalita.citazione.testo == citazione()["testo"]
        assert out.modalita.citazione.verificata is True

    def test_voce_dello_snapshot_con_url_ammesso(self):
        url = "https://regione.example.it/Avviso pubblico.pdf"
        out = snapshot(modalita={**MODALITA, "citazione": {**citazione(), "url_documento": url}})
        assert out.modalita.citazione.url_documento == (
            "https://regione.example.it/Avviso%20pubblico.pdf"
        )

    def test_http_resta_un_errore_anche_nello_snapshot(self):
        with pytest.raises(ValidationError):
            snapshot(modalita={**MODALITA, "citazione": {**citazione(),
                                                          "url_documento": "http://x.it/a.pdf"}})

    @pytest.mark.parametrize("url", NON_AMMESSI)
    def test_requisito_out_accetta_una_citazione_in_e_la_filtra(self, url):
        # La gap analysis costruisce `RequisitoOut` con una `CitazioneIn`.
        cit = pcs.CitazioneIn(**citazione(), url_documento=url)
        out = pcs.RequisitoOut(testo="Testo", origine="manuale", citazione=cit)
        assert isinstance(out.citazione, pcs.CitazioneSnapshotOut)
        assert out.citazione.url_documento is None
        assert out.citazione.sezione == "D1-p3"
        assert out.model_dump()["citazione"]["url_documento"] is None

    def test_requisito_out_con_url_ammesso_invariato(self):
        cit = pcs.CitazioneIn(**citazione(), url_documento="https://regione.example.it/a.pdf")
        out = pcs.RequisitoOut(testo="Testo", origine="manuale", citazione=cit)
        assert out.citazione.url_documento == "https://regione.example.it/a.pdf"

    def test_requisito_in_invariato(self):
        with pytest.raises(ValidationError):
            pcs.RequisitoIn(testo="Testo valido",
                            citazione={**citazione(), "url_documento": "http://x.it/a.pdf"})
        dentro = pcs.RequisitoIn(testo="Testo valido", citazione=citazione())
        assert type(dentro.citazione) is pcs.CitazioneIn

    def test_regola_finanziaria_dello_snapshot(self):
        s = snapshot(regole_finanziarie=[
            regola(citazione={**citazione(), "url_documento": "https://www.fasi.eu/a.pdf"}),
        ])
        [regola_out] = s.regole_finanziarie
        assert isinstance(regola_out.citazione, pcs.CitazioneSnapshotOut)
        assert regola_out.citazione.url_documento is None
        assert regola_out.origine_voce == "confermata"
