"""Convalida tollerante dell'estrazione WP3 (`schemas.partenariato.convalida_tollerante`).

L'estrazione passa da uno strumento NON strict: il modello riceve lo schema come
guida ma nessuna grammatica lo vincola. L'input si riporta alla forma di
`PartenariatoEstrazione` senza buttare una risposta pagata: campi mancanti →
valore assente convenzionale, tipi semplici sbagliati normalizzati, campi ignoti
ignorati. Un valore PRESENTE ma non leggibile non diventa mai «assente»: resta
visibile e la voce è da verificare; le voci scartate lasciano un avviso globale.
Errore solo per un input non oggetto o senza nessun campo dell'estrazione. Lo
schema inviato e la post-elaborazione restano quelli di prima."""

import copy
import json

import anthropic
import pytest
from pydantic import TypeAdapter

from app.schemas.partenariato import (
    PartenariatoEstrazione,
    RegolePartenariatoOut,
    avvisi_convalida,
    convalida_tollerante,
)
from app.services.partenariato_regole import post_elabora
from tests.test_partenariato_regole import FONTI, LOOKUPS, SEZIONI, cit, estrazione_base

NESSUNA = {"sezione": "", "testo": ""}
# Una modalità «obbligatorio» con una citazione lunga, ritrovata nel testo.
CIT_OBBLIGATORIO = cit("D1-p3", "Il partenariato è composto da almeno 3 e al massimo 6 imprese")


def elabora(estrazione: PartenariatoEstrazione) -> dict:
    return post_elabora(estrazione, SEZIONI, FONTI, LOOKUPS)


class TestInputConforme:
    def test_come_la_convalida_stretta(self):
        dati = estrazione_base()
        assert convalida_tollerante(dati) == PartenariatoEstrazione.model_validate(dati)

    def test_post_elaborazione_identica(self):
        dati = estrazione_base()
        assert elabora(convalida_tollerante(dati)) == elabora(
            PartenariatoEstrazione.model_validate(dati))

    def test_non_modifica_l_input(self):
        dati = estrazione_base(partner_min=3)
        originale = copy.deepcopy(dati)
        convalida_tollerante(dati)
        assert dati == originale

    def test_lo_schema_inviato_non_cambia(self):
        """La variante tollerante è una funzione di convalida, non un altro
        modello: lo schema è sempre quello di `PartenariatoEstrazione` (v2)."""
        schema = anthropic.transform_schema(TypeAdapter(PartenariatoEstrazione).json_schema())
        assert schema["title"] == "PartenariatoEstrazione"
        assert set(schema["required"]) == set(PartenariatoEstrazione.model_fields)
        # La v2 misurata dalla sonda del 2026-09-29: cambia solo con SCHEMA_VERSION.
        assert len(json.dumps(schema, ensure_ascii=False).encode()) == 6456


class TestCampiMancanti:
    def test_campi_tutti_assenti_estrazione_vuota(self):
        estrazione = convalida_tollerante({"modalita": None})
        assert estrazione.modalita == "non_determinabile"
        assert estrazione.modalita_citazione.model_dump() == NESSUNA
        assert (estrazione.partner_min, estrazione.partner_max, estrazione.costituzione) == (
            "", "", "")
        assert estrazione.forme_ammesse == estrazione.composizione == estrazione.quote == []
        assert estrazione.vincoli == estrazione.regole_finanziarie == []
        assert estrazione.documenti_richiesti == []
        assert estrazione.fonti_insufficienti is False and estrazione.note == ""

    def test_campi_tutti_assenti_post_elaborati_non_determinabile(self):
        regole = RegolePartenariatoOut.model_validate(
            elabora(convalida_tollerante({"modalita": None})))
        assert regole.modalita_effettiva == "non_determinabile"
        assert regole.modalita.valore == "non_determinabile"
        assert regole.partner_min.valore is None and regole.partner_max.valore is None
        assert regole.forme_ammesse == regole.composizione == regole.quote == []

    def test_campi_mancanti_nelle_voci(self):
        estrazione = convalida_tollerante({
            "modalita": "ammesso",
            "composizione": [{"tipo_soggetto": "pmi", "minimo": "2"}],
            "quote": [{"ambito": "per_partner", "min_percentuale": "10"}],
            "forme_ammesse": [{"forma": "ats"}],
        })
        [voce] = estrazione.composizione
        assert (voce.id, voce.massimo, voce.regioni, voce.paesi) == ("", "", [], [])
        assert voce.ruolo == "qualsiasi"  # nessun ruolo indicato
        assert voce.citazione.model_dump() == NESSUNA
        [quota] = estrazione.quote
        assert (quota.categoria, quota.base_calcolo, quota.effetto_violazione) == ("", "", "")
        [forma] = estrazione.forme_ammesse
        assert (forma.forma, forma.note) == ("ats", "")

    def test_voce_senza_citazione_da_verificare(self):
        """Il valore assente non inventa nulla: senza citazione la voce resta
        da verificare (post-elaborazione invariata)."""
        dati = estrazione_base()
        del dati["quote"][0]["citazione"]
        regole = elabora(convalida_tollerante(dati))
        assert regole["quote"][0]["stato"] == "da_verificare"


class TestTipiSbagliatiSemplici:
    @pytest.mark.parametrize(
        ("valore", "atteso"),
        [(3, "3"), (3.0, "3"), (12.5, "12.5"), (0.6, "0.6"), ("4", "4"), (None, ""),
         ([3], "3"), (["4"], "4"), ([""], "")],
    )
    def test_numero_al_posto_della_stringa(self, valore, atteso):
        assert convalida_tollerante({"partner_min": valore}).partner_min == atteso

    @pytest.mark.parametrize(
        ("valore", "atteso"),
        [(True, "true"), (False, "false"), ({"n": 3}, '{"n": 3}'), ([1, 2], "[1, 2]"),
         ([], "[]"), ({}, "{}"), ([None], "[null]"), ([[3]], "[[3]]")],
    )
    def test_valore_presente_non_convertibile_resta_visibile(self, valore, atteso):
        """Mai «assente»: il suo JSON, che la post-elaborazione segnala."""
        assert convalida_tollerante({"partner_min": valore}).partner_min == atteso

    def test_numeri_nelle_voci_letti_dalla_post_elaborazione(self):
        dati = estrazione_base(partner_min=3, partner_max=6)
        dati["quote"][0]["min_percentuale"] = 10
        dati["regole_finanziarie"][0]["soglia"] = 0.6
        regole = elabora(convalida_tollerante(dati))
        assert elabora(PartenariatoEstrazione.model_validate(estrazione_base())) == regole

    def test_stringa_singola_al_posto_della_lista(self):
        dati = estrazione_base()
        dati["composizione"][0]["regioni"] = "Piemonte"
        dati["composizione"][0]["paesi"] = "Italia"
        [voce] = convalida_tollerante(dati).composizione
        assert (voce.regioni, voce.paesi) == (["Piemonte"], ["Italia"])

    def test_oggetto_singolo_al_posto_della_lista(self):
        dati = estrazione_base()
        dati["vincoli"] = dati["vincoli"][0]
        [vincolo] = convalida_tollerante(dati).vincoli
        assert vincolo.tipo == "sede_operativa_regione"

    def test_elementi_non_validi_delle_liste(self):
        dati = estrazione_base()
        dati["composizione"][0]["regioni"] = ["Piemonte", 7, None, {"x": 1}, True, ["Lombardia"]]
        dati["forme_ammesse"] = ["ats", dati["forme_ammesse"][0], None]
        estrazione = convalida_tollerante(dati)
        # null = nessun elemento; gli altri restano (la post-elaborazione li segnala)
        assert estrazione.composizione[0].regioni == [
            "Piemonte", "7", '{"x": 1}', "true", "Lombardia"]
        assert [f.forma for f in estrazione.forme_ammesse] == ["ats"]
        assert avvisi_convalida(estrazione) == [
            "Forme di aggregazione: una voce non considerata perché non è leggibile"]

    def test_json_serializzato_in_una_stringa(self):
        dati = estrazione_base()
        dati["composizione"] = json.dumps(dati["composizione"])
        dati["modalita_citazione"] = json.dumps(dati["modalita_citazione"])
        atteso = PartenariatoEstrazione.model_validate(estrazione_base())
        assert convalida_tollerante(dati) == atteso
        assert convalida_tollerante(json.dumps(estrazione_base())) == atteso

    def test_citazione_non_oggetto_vale_assente(self):
        dati = estrazione_base(modalita_citazione="in forma singola o associata")
        estrazione = convalida_tollerante(dati)
        assert estrazione.modalita_citazione.model_dump() == NESSUNA
        assert elabora(estrazione)["modalita_effettiva"] == "non_determinabile"

    @pytest.mark.parametrize(("valore", "atteso"), [
        (True, True), (False, False), ("true", True), ("False", False), (1, False),
        (None, False),
    ])
    def test_booleano(self, valore, atteso):
        assert convalida_tollerante({"fonti_insufficienti": valore}).fonti_insufficienti is atteso


class TestCodiciChiusi:
    @pytest.mark.parametrize(("valore", "atteso"), [
        ("Ammesso", "ammesso"), ("non ammesso", "non_ammesso"), (" OBBLIGATORIO ", "obbligatorio"),
        ("Non-determinabile", "non_determinabile"), ("consentito", "non_determinabile"),
        ("", "non_determinabile"), (None, "non_determinabile"), (2, "non_determinabile"),
    ])
    def test_modalita(self, valore, atteso):
        assert convalida_tollerante({"modalita": valore}).modalita == atteso

    def test_modalita_ignota_non_vale_mai(self):
        regole = elabora(convalida_tollerante(estrazione_base(modalita="consentita")))
        assert regole["modalita_effettiva"] == "non_determinabile"

    def test_modalita_ignota_lascia_un_avviso(self):
        estrazione = convalida_tollerante(estrazione_base(modalita="consentita"))
        assert avvisi_convalida(estrazione) == ["Modalità «consentita» non riconosciuta"]
        assert "Modalità «consentita» non riconosciuta" in elabora(estrazione)["avvisi"]
        assert avvisi_convalida(convalida_tollerante({"modalita": None})) == []

    def test_operatore_come_simbolo(self):
        dati = estrazione_base()
        regole = [{**dati["regole_finanziarie"][0], "operatore": simbolo}
                  for simbolo in ("<=", "≤", "<", ">", ">=", "≥", "LE")]
        dati["regole_finanziarie"] = regole
        operatori = [r.operatore for r in convalida_tollerante(dati).regole_finanziarie]
        assert operatori == ["le", "le", "lt", "gt", "ge", "ge", "le"]

    def test_voci_con_codice_chiuso_ignoto_scartate(self):
        """Senza un valore assente (ambito della quota, ambito e operatore della
        regola) la voce non è rappresentabile: si scarta, le altre restano."""
        dati = estrazione_base()
        quota = dati["quote"][0]
        regola = dati["regole_finanziarie"][0]
        dati["quote"] = [{**quota, "ambito": "per_regione"}, quota, {**quota, "ambito": None}]
        dati["regole_finanziarie"] = [{**regola, "operatore": "circa"}, regola,
                                      {**regola, "ambito": ""}]
        estrazione = convalida_tollerante(dati)
        assert [q.ambito for q in estrazione.quote] == ["per_partner"]
        assert [r.operatore for r in estrazione.regole_finanziarie] == ["le"]


class TestCampiIgnoti:
    def test_ignorati_a_ogni_livello(self):
        dati = estrazione_base(motivazione="ragionamento del modello", versione=3)
        dati["composizione"][0]["priorita"] = "alta"
        dati["modalita_citazione"]["pagina"] = 3
        atteso = PartenariatoEstrazione.model_validate(estrazione_base())
        assert convalida_tollerante(dati) == atteso


class TestInputNonOggetto:
    @pytest.mark.parametrize("dati", [None, [], [{"modalita": "ammesso"}], "testo libero",
                                      "{non json", 3, True, '["a"]'])
    def test_errore(self, dati):
        with pytest.raises(ValueError, match="non è un oggetto JSON"):
            convalida_tollerante(dati)


class TestValoriPresentiNonLeggibili:
    """Un valore presente ma non convertibile non vale «non indicato»: i
    controlli di coerenza pensati per un valore illeggibile scattano."""

    @staticmethod
    def obbligatorio(**sostituzioni) -> dict:
        return estrazione_base(modalita="obbligatorio", modalita_citazione=CIT_OBBLIGATORIO,
                               **sostituzioni)

    def test_premessa_obbligatorio_verificato(self):
        regole = elabora(convalida_tollerante(self.obbligatorio()))
        assert regole["modalita_effettiva"] == "obbligatorio"
        assert regole["modalita"]["stato"] == "verificata"

    @pytest.mark.parametrize("valore", ["1", 1, 1.0, [1], ["1"]])
    def test_massimo_di_uno_contraddice_anche_in_un_elenco(self, valore):
        regole = elabora(convalida_tollerante(self.obbligatorio(partner_max=valore)))
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert "«Obbligatorio» contraddice un massimo di un solo soggetto" in regole["avvisi"]

    @pytest.mark.parametrize("valore", [{"valore": 1}, True, False, [1, 2], [], {}, [None]])
    def test_massimo_presente_non_leggibile(self, valore):
        regole = elabora(convalida_tollerante(self.obbligatorio(partner_max=valore)))
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert ("«Obbligatorio» non verificabile: numero massimo di partner non leggibile"
                in regole["avvisi"])
        assert regole["partner_max"]["stato"] == "da_verificare"
        assert "Numero massimo di partner non leggibile" in regole["partner_max"]["avvisi"]

    def test_massimo_null_resta_assente(self):
        regole = elabora(convalida_tollerante(self.obbligatorio(partner_max=None)))
        assert regole["modalita_effettiva"] == "obbligatorio"
        assert regole["partner_max"]["valore"] is None

    @staticmethod
    def composizione(**campi) -> dict:
        dati = estrazione_base()
        dati["composizione"][0].update({"regioni": ["Piemonte"], **campi})
        [voce] = elabora(convalida_tollerante(dati))["composizione"]
        return voce

    def test_premessa_composizione_verificata(self):
        voce = self.composizione()
        assert (voce["stato"], voce["regioni_nomi"]) == ("verificata", ["Piemonte"])

    @pytest.mark.parametrize("regioni", [[{"nome": "Piemonte"}], {"nome": "Piemonte"}, [True]])
    def test_regione_non_testuale_segnalata(self, regioni):
        """Senza la correzione diventava [] e la voce era verificata SENZA il
        vincolo territoriale."""
        voce = self.composizione(regioni=regioni)
        assert voce["stato"] == "da_verificare" and voce["regioni"] == []
        assert any(a.startswith("Regione non riconosciuta:") for a in voce["avvisi"])

    def test_codice_come_oggetto_segnalato(self):
        voce = self.composizione(tipo_soggetto={"codice": "pmi"})
        assert voce["stato"] == "da_verificare"
        assert any(a.startswith("Tipo di soggetto non riconosciuto:") for a in voce["avvisi"])

    @pytest.mark.parametrize("minimo", [{"n": 2}, True, [2, 3]])
    def test_minimo_non_leggibile(self, minimo):
        voce = self.composizione(minimo=minimo)
        assert voce["stato"] == "da_verificare" and voce["minimo"] is None
        assert "Numero minimo non leggibile" in voce["avvisi"]


class TestRuolo:
    @pytest.mark.parametrize(("valore", "atteso"), [
        ("Capofila", "capofila"), ("Capo-fila", "capofila"), ("mandataria", "capofila"),
        ("Mandatario", "capofila"), ("capogruppo", "capofila"), ("mandante", "partner"),
        ("Partner associato", "partner_associato"), (None, "qualsiasi"), ("", "qualsiasi"),
    ])
    def test_riconosciuto_o_assente(self, valore, atteso):
        dati = estrazione_base()
        dati["composizione"][0].update(ruolo=valore, regioni=["Piemonte"])
        estrazione = convalida_tollerante(dati)
        [voce] = estrazione.composizione
        assert voce.ruolo == atteso
        assert voce.citazione.testo and avvisi_convalida(estrazione) == []
        assert elabora(estrazione)["composizione"][0]["stato"] == "verificata"

    @pytest.mark.parametrize("valore", ["coordinatrice", "università", 3, {"ruolo": "capofila"}])
    def test_ignoto_voce_da_verificare(self, valore):
        """Un ruolo scritto ma fuori vocabolario vale «qualsiasi» (vincolo più
        largo del bando): la voce resta visibile ma non si conferma."""
        dati = estrazione_base()
        voce = {**dati["composizione"][0], "regioni": ["Piemonte"]}
        dati["composizione"] = [voce, {**voce, "id": "C2", "ruolo": valore}]
        estrazione = convalida_tollerante(dati)
        prima, seconda = estrazione.composizione
        assert (prima.ruolo, seconda.ruolo) == ("qualsiasi", "qualsiasi")
        assert prima.citazione.testo and seconda.citazione.model_dump() == NESSUNA
        regole = elabora(estrazione)
        assert [v["stato"] for v in regole["composizione"]] == ["verificata", "da_verificare"]
        [avviso] = [a for a in regole["avvisi"] if a.startswith("Composizione")]
        assert avviso.startswith("Composizione: voce C2 da verificare perché il ruolo «")
        assert avviso.endswith("» non è riconosciuto")


class TestVociScartateNegliAvvisi:
    def test_input_conforme_nessun_avviso(self):
        estrazione = convalida_tollerante(estrazione_base())
        assert avvisi_convalida(estrazione) == []
        stretta = PartenariatoEstrazione.model_validate(estrazione_base())
        assert elabora(estrazione)["avvisi"] == elabora(stretta)["avvisi"]

    def test_scarti_visibili_negli_avvisi_globali(self):
        dati = estrazione_base()
        quota, regola = dati["quote"][0], dati["regole_finanziarie"][0]
        dati["quote"] = [{**quota, "id": "Q2", "ambito": "per_regione"}, quota]
        dati["vincoli"] = ["sede in Piemonte", *dati["vincoli"]]
        dati["regole_finanziarie"] = [
            {**regola, "id": "RF2", "operatore": "circa"}, regola,
            {**regola, "id": "RF3", "ambito": ""}, {**regola, "id": "", "operatore": "="},
        ]
        regole = elabora(convalida_tollerante(dati))
        assert [q["id"] for q in regole["quote"]] == ["Q1"]
        assert [v["id"] for v in regole["vincoli"]] == ["V1"]
        assert [r["id"] for r in regole["regole_finanziarie"]] == ["RF1"]
        assert regole["avvisi"] == [
            "Quote: voce Q2 non considerata perché l'ambito «per_regione» non è riconosciuto",
            "Vincoli: una voce non considerata perché non è leggibile",
            "Regole finanziarie: voce RF2 non considerata perché l'operatore «circa» non è "
            "riconosciuto",
            "Regole finanziarie: voce RF3 non considerata perché manca l'ambito",
            "Regole finanziarie: una voce non considerata perché l'operatore «=» non è "
            "riconosciuto",
        ]
        RegolePartenariatoOut.model_validate(regole)

    def test_avvisi_fuori_dal_dump_e_dallo_schema(self):
        estrazione = convalida_tollerante(estrazione_base(modalita="consentita"))
        assert avvisi_convalida(estrazione)
        dump = estrazione.model_dump(mode="json")
        assert set(dump) == set(PartenariatoEstrazione.model_fields)
        # riletta dal DB (dump) non porta avvisi: la post-elaborazione resta quella
        assert avvisi_convalida(PartenariatoEstrazione.model_validate(dump)) == []


class TestInvolucroEOggettiEstranei:
    @pytest.mark.parametrize("chiave", ["regole", "input", "estrazione"])
    def test_involucro_di_una_chiave_sciolto(self, chiave):
        atteso = PartenariatoEstrazione.model_validate(estrazione_base())
        assert convalida_tollerante({chiave: estrazione_base()}) == atteso
        assert convalida_tollerante({chiave: json.dumps(estrazione_base())}) == atteso

    @pytest.mark.parametrize("dati", [
        {"risultato": "ok"},
        {"regole": {"a": 1}},
        {"regole": "{non json"},
        {"regole": [estrazione_base()]},
        # due chiavi ignote: non si indovina quale sia l'estrazione
        {"regole": estrazione_base(), "motivazione": "..."},
    ])
    def test_oggetto_senza_campi_dell_estrazione_errore(self, dati):
        """Non un'estrazione vuota (che si salverebbe come «estratta» e si
        riuserebbe): una risposta illeggibile, errore con l'usage."""
        with pytest.raises(ValueError, match="nessun campo dell'estrazione"):
            convalida_tollerante(dati)

    def test_basta_un_campo_dell_estrazione(self):
        estrazione = convalida_tollerante({"note": "solo una nota", "regole": estrazione_base()})
        assert (estrazione.note, estrazione.modalita) == ("solo una nota", "non_determinabile")

    @pytest.mark.parametrize("dati", [{}, "{}", {"estrazione": {}}])
    def test_oggetto_vuoto_errore(self, dati):
        """Una risposta vuota non si salva come «estratta» (si riuserebbe
        fino a un «forza»): errore con l'usage, come un input illeggibile."""
        with pytest.raises(ValueError, match="vuoto|nessun campo"):
            convalida_tollerante(dati)


class TestCitazioniSoloTesto:
    def test_premessa_una_cifra_come_testo_si_ritrova(self):
        """«3» compare nella sezione: come testo la citazione è verificata."""
        dati = estrazione_base(partner_min_citazione=cit("D1-p3", "3"))
        assert elabora(convalida_tollerante(dati))["partner_min"]["stato"] == "verificata"

    @pytest.mark.parametrize("testo", [3, 3.0, True, ["almeno 3"], {"testo": "almeno 3"}])
    def test_testo_non_stringa_citazione_assente(self, testo):
        dati = estrazione_base(partner_min_citazione={"sezione": "D1-p3", "testo": testo})
        estrazione = convalida_tollerante(dati)
        assert estrazione.partner_min_citazione.model_dump() == {"sezione": "D1-p3", "testo": ""}
        assert elabora(estrazione)["partner_min"]["stato"] == "da_verificare"

    def test_sezione_non_stringa_assente(self):
        dati = estrazione_base(partner_min_citazione={"sezione": 3, "testo": "almeno 3"})
        estrazione = convalida_tollerante(dati)
        assert estrazione.partner_min_citazione.sezione == ""
        assert elabora(estrazione)["partner_min"]["stato"] == "da_verificare"
