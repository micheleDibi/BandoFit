"""Post-elaborazione deterministica delle regole di partenariato (WP3):
verifica delle citazioni (solo i documenti ufficiali fanno fede, la scheda del
catalogo no), controlli di coerenza e di range (la voce va `da_verificare`,
mai un'eccezione), `modalita_effettiva`, mappatura delle regioni e dei tipi di
soggetto, scrub dei domini esclusi."""

import copy

import pytest

from app.schemas.partenariato import PartenariatoEstrazione, RegolePartenariatoOut
from app.services.partenariato_regole import _da_fonte_ufficiale, mappa_regioni, post_elabora

SEZIONI = {
    "META": "Titolo: Bando reti\nBeneficiari (catalogo): PMI",
    "S1": "Possono partecipare le PMI in forma singola o associata.",
    "D1-p3": (
        "Art. 4 - Soggetti ammessi\nLe imprese possono presentare domanda in forma singola o "
        "associata mediante ATS o contratto di rete. Il partenariato è composto da almeno 3 "
        "e al massimo 6 imprese. Ciascun partner deve sostenere almeno il 10% delle spese "
        "ammis-\nsibili."
    ),
    "D1-p4": (
        "Il raggruppamento deve essere costituito prima della concessione. Il costo della quota "
        "non può superare il 60% del fatturato medio degli ultimi due esercizi. Le imprese "
        "devono avere sede operativa in Piemonte o Lombardia."
    ),
}
FONTI = [{"n": 1, "etichetta": "Avviso pubblico", "url": "https://regione.example.it/avviso.pdf"}]
# Pagina di un documento ufficiale in cui i test mettono la frase che serve:
# solo le citazioni dei documenti ufficiali possono dirsi verificate.
PAGINA = "D2-p1"
AVVISO_SCHEDA = "Dalla scheda del catalogo: da verificare sul bando ufficiale"
LOOKUPS = {"regioni": [
    {"id": 1, "nome": "Piemonte"}, {"id": 2, "nome": "Lombardia"},
    {"id": 3, "nome": "Valle d'Aosta/Vallée d'Aoste"}, {"id": 4, "nome": "Emilia-Romagna"},
    {"id": 5, "nome": "Trentino-Alto Adige/Südtirol"},
]}


def cit(sezione: str, testo: str) -> dict:
    return {"sezione": sezione, "testo": testo}


# Citazione assente nello schema compatto del modello.
NESSUNA = cit("", "")


def estrazione_base(**sostituzioni) -> dict:
    dati = {
        "modalita": "ammesso",
        "modalita_citazione": cit("D1-p3", "in forma singola o associata mediante ATS"),
        "forme_ammesse": [
            {"forma": "ats", "note": "", "citazione": cit("D1-p3", "mediante ATS")},
            {"forma": "rete_contratto", "note": "",
             "citazione": cit("[D1-P3]", "contratto di rete")},
        ],
        "costituzione": "costituita_richiesta",
        "costituzione_citazione": cit("D1-p4", "deve essere costituito prima della concessione"),
        "partner_min": "3",
        "partner_min_citazione": cit("D1-p3", "almeno 3"),
        "partner_max": "6",
        "partner_max_citazione": cit("D1-p3", "al massimo 6 imprese"),
        "conteggio_note": "",
        "composizione": [{
            "id": "C1", "tipo_soggetto": "pmi", "tipo_soggetto_testo": "",
            "minimo": "3", "massimo": "6", "ruolo": "qualsiasi",
            "regioni": ["Piemonte", "lombardia", "Atlantide"], "paesi": [],
            "vincolo_territoriale": "sede operativa in Piemonte o Lombardia",
            "citazione": cit("D1-p4", "sede operativa in Piemonte o Lombardia"),
        }],
        "quote": [{
            "id": "Q1", "ambito": "per_partner", "categoria": "",
            "min_percentuale": "10", "max_percentuale": "",
            "base_calcolo": "spese_ammissibili", "effetto_violazione": "non_indicato",
            # sillabazione a fine riga nel PDF: la verifica la ricuce
            "citazione": cit("D1-p3", "almeno il 10% delle spese ammissibili"),
        }],
        "vincoli": [{
            "id": "V1", "tipo": "sede_operativa_regione", "descrizione": "Sede in Piemonte",
            "parametro": "", "momento": "domanda",
            "citazione": cit("D1-p4", "sede operativa in Piemonte"),
        }],
        "regole_finanziarie": [{
            "id": "RF1", "descrizione": "Quota ≤ 60% del fatturato medio 2 anni",
            "ambito": "ciascun_partner", "numeratore": "costo_quota",
            "denominatore": "fatturato_medio_2", "operatore": "le", "soglia": "0.6",
            "soglia_variabile": "", "soglia_coefficiente": "", "unita": "rapporto",
            "citazione": cit("D1-p4", "non può superare il 60% del fatturato medio"),
        }],
        "documenti_richiesti": [{
            "id": "DR1", "tipo": "atto_costitutivo", "descrizione": "Atto costitutivo dell'ATS",
            "momento": "concessione",
            "citazione": cit("D1-p4", "costituito prima della concessione"),
        }],
        "fonti_insufficienti": False,
        "note": "",
    }
    dati.update(sostituzioni)
    return dati


def elabora(dati: dict, sezioni=SEZIONI, lookups=LOOKUPS) -> dict:
    return post_elabora(PartenariatoEstrazione.model_validate(dati), sezioni, FONTI, lookups)


class TestForma:
    def test_uscita_valida_per_il_dto(self):
        regole = elabora(estrazione_base())
        RegolePartenariatoOut.model_validate(regole)

    def test_accetta_anche_un_dict(self):
        regole = post_elabora(estrazione_base(), SEZIONI, FONTI, LOOKUPS)
        assert regole["modalita"]["effettiva"] == regole["modalita_effettiva"] == "ammesso"


class TestCitazioni:
    def test_verificate_con_etichetta_pagina_e_url(self):
        regole = elabora(estrazione_base())
        citazione = regole["modalita"]["citazione"]
        assert citazione == {
            "sezione": "D1-p3",
            "fonte_etichetta": "Avviso pubblico — pag. 3",
            "testo": "in forma singola o associata mediante ATS",
            "verificata": True,
            "url_documento": "https://regione.example.it/avviso.pdf",
            "pagina": 3,
        }
        assert regole["modalita"]["stato"] == "verificata"
        # sezione scritta in modo diverso: normalizzata e verificata
        assert regole["forme_ammesse"][1]["citazione"]["sezione"] == "D1-p3"
        assert regole["forme_ammesse"][1]["stato"] == "verificata"
        # sillabazione ricucita
        assert regole["quote"][0]["stato"] == "verificata"

    def test_scheda_del_bando(self):
        dati = estrazione_base(modalita_citazione=cit("S1", "in forma singola o associata"))
        modalita = elabora(dati)["modalita"]
        citazione = modalita["citazione"]
        assert citazione["fonte_etichetta"] == "Scheda del bando"
        assert citazione["url_documento"] is None and citazione["pagina"] is None
        # ritrovata alla lettera, ma la scheda non è il bando ufficiale
        assert citazione["verificata"] is True
        assert modalita["stato"] == "da_verificare"
        assert modalita["avvisi"] == [AVVISO_SCHEDA]
        assert modalita["effettiva"] == "non_determinabile"

    def test_citazione_non_ritrovata_da_verificare(self):
        dati = estrazione_base()
        dati["vincoli"][0]["citazione"] = cit("D1-p4", "sede operativa in Sicilia")
        regole = elabora(dati)
        assert regole["vincoli"][0]["stato"] == "da_verificare"
        assert regole["vincoli"][0]["citazione"]["verificata"] is False

    @pytest.mark.parametrize("sezione", ["S1", PAGINA])
    def test_sezione_sbagliata_non_verifica(self, sezione):
        # il testo è in D1-p4, la citazione indica la scheda o un'altra pagina
        # ufficiale non adiacente: non si cerca nelle altre sezioni
        dati = estrazione_base()
        dati["documenti_richiesti"][0]["citazione"] = cit(sezione, "costituito prima")
        sezioni = {**SEZIONI, PAGINA: "La domanda si presenta entro il 30 giugno."}
        voce = elabora(dati, sezioni)["documenti_richiesti"][0]
        assert voce["stato"] == "da_verificare"
        assert voce["citazione"]["verificata"] is False

    def test_citazione_a_cavallo_di_pagina(self):
        dati = estrazione_base(
            partner_max_citazione=cit(
                "D1-p3", "delle spese ammissibili. Il raggruppamento deve essere costituito"
            )
        )
        assert elabora(dati)["partner_max"]["stato"] == "verificata"

    def test_url_non_https_scartato(self):
        fonti = [{"n": 1, "etichetta": "Avviso", "url": "http://regione.example.it/a.pdf"}]
        regole = post_elabora(PartenariatoEstrazione.model_validate(estrazione_base()), SEZIONI,
                              fonti, LOOKUPS)
        assert regole["modalita"]["citazione"]["url_documento"] is None

    def test_sezione_ignota(self):
        dati = estrazione_base(modalita_citazione=cit("X9", "in forma singola"))
        modalita = elabora(dati)["modalita"]
        assert modalita["citazione"]["fonte_etichetta"] == "Fonte non riconosciuta"
        assert modalita["effettiva"] == "non_determinabile"


FRASE_REGOLE = (
    "La domanda è presentata esclusivamente in forma associata mediante ATS costituita prima "
    "della concessione, composta da almeno 3 e al massimo 6 imprese con sede operativa in "
    "Piemonte. Ciascun partner deve sostenere almeno il 10% delle spese ammissibili e la quota "
    "non può superare il 60% del fatturato medio."
)


def _estrazione_su(sezione: str) -> dict:
    """Un'estrazione con TUTTE le voci citate da `sezione` (testo:
    `FRASE_REGOLE`), valori coerenti: l'unica variabile è la fonte."""
    dati = estrazione_base(
        modalita="obbligatorio",
        modalita_citazione=cit(sezione, "La domanda è presentata esclusivamente in forma associata"),
        forme_ammesse=[{"forma": "ats", "note": "",
                        "citazione": cit(sezione, "in forma associata mediante ATS")}],
        costituzione_citazione=cit(sezione, "costituita prima della concessione"),
        partner_min_citazione=cit(sezione, "almeno 3"),
        partner_max_citazione=cit(sezione, "al massimo 6 imprese"),
    )
    dati["composizione"][0].update(regioni=["Piemonte"],
                                   citazione=cit(sezione, "sede operativa in Piemonte"))
    dati["quote"][0]["citazione"] = cit(
        sezione, "Ciascun partner deve sostenere almeno il 10% delle spese ammissibili")
    dati["vincoli"][0]["citazione"] = cit(sezione, "sede operativa in Piemonte")
    dati["regole_finanziarie"][0]["citazione"] = cit(
        sezione, "non può superare il 60% del fatturato medio")
    dati["documenti_richiesti"][0]["citazione"] = cit(
        sezione, "costituita prima della concessione")
    return dati


def _voci(regole: dict) -> dict:
    """Tutte le voci con citazione, per nome."""
    return {
        "modalita": regole["modalita"],
        "costituzione": regole["costituzione"],
        "partner_min": regole["partner_min"],
        "partner_max": regole["partner_max"],
        "forma": regole["forme_ammesse"][0],
        "composizione": regole["composizione"][0],
        "quota": regole["quote"][0],
        "vincolo": regole["vincoli"][0],
        "regola_finanziaria": regole["regole_finanziarie"][0],
        "documento": regole["documenti_richiesti"][0],
    }


class TestFontiUfficiali:
    """Fa fede solo il testo dei documenti ufficiali (pagine «D<n>-p<m>»): la
    scheda del catalogo (META, S1…) è testo generato o classificato dal
    produttore del catalogo, non estratto dall'atto. Una voce citata solo dalla
    scheda resta visibile con la sua citazione, ma è da verificare, e la
    modalità non vale per il filtro."""

    @pytest.mark.parametrize(("sezione", "attesa"), [
        ("D1-p3", True), ("[D1-P3]", True), ("D1 pag. 3", True), ("d1, pagina 3", True),
        ("D12-p140", True), ("S2", False), ("[s2]", False), ("META", False), ("meta", False),
        ("D1", False), ("DOC", False), ("X9", False), ("", False),
    ])
    def test_da_fonte_ufficiale(self, sezione, attesa):
        citazione = PartenariatoEstrazione.model_validate(
            estrazione_base(modalita_citazione=cit(sezione, "x"))
        ).modalita_citazione
        assert _da_fonte_ufficiale(citazione) is attesa

    @pytest.mark.parametrize(("citata", "chiave", "ufficiale"), [
        ("D2-p1", "D2-p1", True),
        ("[D2-P1]", "D2-p1", True),
        ("d2 pag. 1", "D2-p1", True),
        ("S2", "S2", False),
        ("[s2]", "S2", False),
        ("META", "META", False),
    ])
    def test_tutte_le_voci(self, citata, chiave, ufficiale):
        regole = elabora(_estrazione_su(citata), {**SEZIONI, chiave: FRASE_REGOLE})
        RegolePartenariatoOut.model_validate(regole)
        for nome, voce in _voci(regole).items():
            # la citazione resta, ritrovata alla lettera, in ogni caso
            assert voce["citazione"]["verificata"] is True, nome
            if ufficiale:
                assert (voce["stato"], voce["avvisi"]) == ("verificata", []), nome
                assert voce["citazione"]["fonte_etichetta"] == "Documento 2 — pag. 1", nome
            else:
                assert voce["stato"] == "da_verificare", nome
                assert voce["avvisi"] == [AVVISO_SCHEDA], nome
                assert voce["citazione"]["fonte_etichetta"] == "Scheda del bando", nome
        assert regole["modalita"]["valore"] == "obbligatorio"
        attesa = "obbligatorio" if ufficiale else "non_determinabile"
        assert regole["modalita"]["effettiva"] == regole["modalita_effettiva"] == attesa
        # l'avviso resta sulla voce, non tra quelli dell'intero risultato
        assert regole["avvisi"] == []

    def test_voce_per_voce(self):
        """Nello stesso bando la modalità citata dal documento vale, la quota
        citata dalla scheda no."""
        dati = _estrazione_su(PAGINA)
        dati["quote"][0]["citazione"] = cit(
            "S2", "Ciascun partner deve sostenere almeno il 10% delle spese ammissibili")
        regole = elabora(dati, {**SEZIONI, PAGINA: FRASE_REGOLE, "S2": FRASE_REGOLE})
        assert regole["modalita_effettiva"] == "obbligatorio"
        assert regole["partner_min"]["stato"] == "verificata"
        quota = regole["quote"][0]
        assert (quota["stato"], quota["avvisi"]) == ("da_verificare", [AVVISO_SCHEDA])
        assert quota["min_percentuale"] == 10.0  # il valore resta com'è

    @pytest.mark.parametrize("sezione", ["S2", "META"])
    def test_esclusione_nella_scheda_non_toglie_dal_filtro(self, sezione):
        frase = "Non sono ammessi raggruppamenti, consorzi o reti di imprese."
        dati = estrazione_base(
            modalita="non_ammesso", forme_ammesse=[], modalita_citazione=cit(sezione, frase),
            partner_min="", partner_min_citazione=NESSUNA,
            partner_max="", partner_max_citazione=NESSUNA,
        )
        regole = elabora(dati, {**SEZIONI, sezione: frase})
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert regole["modalita"]["avvisi"] == [AVVISO_SCHEDA]
        # la stessa frase in un documento ufficiale vale
        dati["modalita_citazione"] = cit(PAGINA, frase)
        assert elabora(dati, {**SEZIONI, PAGINA: frase})["modalita_effettiva"] == "non_ammesso"

    def test_bando_senza_documenti_ufficiali(self):
        """Solo la scheda (il caso della generazione SEO: «in forma singola o
        associata» nella scheda, l'atto non letto): tutto resta visibile, con
        le citazioni, ma tutto da verificare e la modalità non determinabile."""
        sezioni = {
            "META": "Titolo: Bando reti\nPartecipazione: in forma singola o associata",
            "S1": FRASE_REGOLE,
        }
        dati = _estrazione_su("S1")
        dati.update(modalita="ammesso",
                    modalita_citazione=cit("META", "in forma singola o associata"))
        regole = post_elabora(PartenariatoEstrazione.model_validate(dati), sezioni, [], LOOKUPS)
        RegolePartenariatoOut.model_validate(regole)
        assert regole["modalita"]["valore"] == "ammesso"
        assert regole["modalita_effettiva"] == "non_determinabile"
        for nome, voce in _voci(regole).items():
            assert voce["stato"] == "da_verificare", nome
            assert AVVISO_SCHEDA in voce["avvisi"], nome
            assert voce["citazione"]["verificata"] is True, nome
            assert voce["citazione"]["fonte_etichetta"] == "Scheda del bando", nome
        # i valori restano visibili
        assert (regole["partner_min"]["valore"], regole["partner_max"]["valore"]) == (3, 6)
        assert regole["quote"][0]["min_percentuale"] == 10.0
        assert regole["regole_finanziarie"][0]["soglia"] == "0.6"


class TestModalitaEffettiva:
    def test_uguale_alla_dichiarata_se_verificata_e_coerente(self):
        modalita = elabora(estrazione_base())["modalita"]
        assert (modalita["valore"], modalita["effettiva"]) == ("ammesso", "ammesso")

    def test_non_determinabile_se_la_citazione_non_torna(self):
        dati = estrazione_base(modalita="obbligatorio",
                               modalita_citazione=cit("D1-p3", "solo in forma associata"))
        modalita = elabora(dati)["modalita"]
        assert modalita["effettiva"] == "non_determinabile"
        assert modalita["stato"] == "da_verificare"

    def test_non_determinabile_senza_citazione(self):
        dati = estrazione_base(modalita="obbligatorio", modalita_citazione=NESSUNA)
        modalita = elabora(dati)["modalita"]
        assert modalita["effettiva"] == "non_determinabile"
        assert modalita["avvisi"]

    def test_non_ammesso_con_forme_ammesse_incoerente(self):
        dati = estrazione_base(
            modalita="non_ammesso",
            modalita_citazione=cit("D1-p3", "in forma singola"),
        )
        regole = elabora(dati)
        assert regole["modalita"]["effettiva"] == "non_determinabile"
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert regole["modalita"]["stato"] == "da_verificare"
        assert any("contraddice" in a for a in regole["avvisi"])

    def test_obbligatorio_verificato(self):
        sezioni = {**SEZIONI, PAGINA: "La domanda è presentata esclusivamente in forma associata."}
        dati = estrazione_base(
            modalita="obbligatorio",
            modalita_citazione=cit(PAGINA, "esclusivamente in forma associata"),
        )
        assert elabora(dati, sezioni)["modalita"]["effettiva"] == "obbligatorio"

    def test_dichiarata_non_determinabile(self):
        dati = estrazione_base(modalita="non_determinabile", modalita_citazione=NESSUNA)
        assert elabora(dati)["modalita"]["effettiva"] == "non_determinabile"

    def test_citazione_frammento_non_fonda_la_modalita(self):
        """Ritrovata alla lettera ma di una parola sola: in S2 la frase dice
        il contrario. Il filtro non deve vedere «obbligatorio»."""
        sezioni = {**SEZIONI, PAGINA: "Non è ammessa la partecipazione in partenariato."}
        for frammento in ("partenariato", "in partenariato", "Il"):
            dati = estrazione_base(modalita="obbligatorio",
                                   modalita_citazione=cit(PAGINA, frammento))
            regole = elabora(dati, sezioni)
            assert regole["modalita_effettiva"] == "non_determinabile", frammento
            assert regole["modalita"]["stato"] == "da_verificare"
            assert any("troppo breve" in a for a in regole["modalita"]["avvisi"])

    def test_citazione_breve_ma_probante_accettata(self):
        sezioni = {**SEZIONI, PAGINA: "Il partenariato è composto da almeno due imprese."}
        dati = estrazione_base(modalita="obbligatorio",
                               modalita_citazione=cit(PAGINA, "almeno due imprese"))
        assert elabora(dati, sezioni)["modalita_effettiva"] == "obbligatorio"


class TestRange:
    def test_partner_min_maggiore_del_max(self):
        dati = estrazione_base(partner_min="7")
        dati["partner_min_citazione"] = cit("D1-p3", "almeno 3")
        regole = elabora(dati)
        assert regole["partner_min"]["stato"] == "da_verificare"
        assert regole["partner_max"]["stato"] == "da_verificare"

    def test_partner_zero_non_plausibile(self):
        regole = elabora(estrazione_base(partner_min="0"))
        assert regole["partner_min"]["stato"] == "da_verificare"
        assert regole["partner_min"]["valore"] == 0

    def test_percentuale_fuori_range(self):
        dati = estrazione_base()
        dati["quote"][0]["min_percentuale"] = "120"
        quota = elabora(dati)["quote"][0]
        assert quota["stato"] == "da_verificare"
        assert any("0-100" in a for a in quota["avvisi"])

    def test_percentuale_min_oltre_max(self):
        dati = estrazione_base()
        dati["quote"][0].update(min_percentuale="40", max_percentuale="30")
        assert elabora(dati)["quote"][0]["stato"] == "da_verificare"

    def test_quota_senza_percentuali(self):
        dati = estrazione_base()
        dati["quote"][0].update(min_percentuale="", max_percentuale="")
        assert elabora(dati)["quote"][0]["stato"] == "da_verificare"

    def test_composizione_min_oltre_max(self):
        dati = estrazione_base()
        dati["composizione"][0].update(minimo="5", massimo="2", regioni=[])
        assert elabora(dati)["composizione"][0]["stato"] == "da_verificare"

    def test_vincolo_parametro_negativo(self):
        dati = estrazione_base()
        dati["vincoli"][0]["parametro"] = "-3"
        assert elabora(dati)["vincoli"][0]["stato"] == "da_verificare"

    def test_regola_finanziaria_incoerente(self):
        dati = estrazione_base()
        dati["regole_finanziarie"][0]["soglia_variabile"] = "patrimonio_netto"  # due soglie
        regola = elabora(dati)["regole_finanziarie"][0]
        assert regola["stato"] == "da_verificare"
        assert any("soglia" in a for a in regola["avvisi"])

    def test_regola_finanziaria_valida(self):
        regola = elabora(estrazione_base())["regole_finanziarie"][0]
        assert regola["stato"] == "verificata"
        assert regola["numeratore"] == "costo_quota" and regola["soglia"] == "0.6"

    def test_mai_eccezioni_su_valori_estremi(self):
        dati = estrazione_base(partner_min="-5", partner_max=str(10**9))
        dati["quote"][0].update(min_percentuale="1e308", max_percentuale="-1e308")
        regole = elabora(dati)
        assert regole["quote"][0]["stato"] == "da_verificare"


class TestMappature:
    def test_regioni_su_id_ignote_scartate(self):
        comp = elabora(estrazione_base())["composizione"][0]
        assert comp["regioni"] == [1, 2]
        assert comp["regioni_nomi"] == ["Piemonte", "Lombardia"]
        assert any("Atlantide" in a for a in comp["avvisi"])
        assert comp["stato"] == "da_verificare"  # la regione scartata va controllata

    def test_varianti_dei_nomi(self):
        indice = [("valledaostavalleedaoste", 3, "Valle d'Aosta/Vallée d'Aoste"),
                  ("emiliaromagna", 4, "Emilia-Romagna"),
                  ("trentinoaltoadigesudtirol", 5, "Trentino-Alto Adige/Südtirol")]
        nomi, ids, scartate = mappa_regioni(
            ["Valle d'Aosta", "Emilia Romagna", "Trentino-Alto Adige", "Sud Italia"], indice
        )
        assert ids == [3, 4, 5]
        assert scartate == ["Sud Italia"]
        assert nomi[1] == "Emilia-Romagna"

    def test_senza_lookup_regioni_non_verificabili(self):
        comp = elabora(estrazione_base(), lookups=None)["composizione"][0]
        assert comp["regioni"] == []
        assert comp["stato"] == "da_verificare"

    def test_tipo_soggetto_su_beneficiari(self):
        comp = elabora(estrazione_base())["composizione"][0]
        assert comp["beneficiari"] == [27]
        assert comp["tipo_soggetto_etichetta"] == "PMI"

    def test_tipo_altro_senza_testo(self):
        dati = estrazione_base()
        dati["composizione"][0].update(tipo_soggetto="altro", tipo_soggetto_testo="", regioni=[])
        comp = elabora(dati)["composizione"][0]
        assert comp["stato"] == "da_verificare"
        assert any("non specificato" in a for a in comp["avvisi"])

    def test_etichette_delle_forme(self):
        forme = elabora(estrazione_base())["forme_ammesse"]
        assert forme[0]["etichetta"].startswith("Associazione temporanea di scopo")

    def test_forma_altra_senza_note(self):
        dati = estrazione_base()
        dati["forme_ammesse"][0]["forma"] = "altra"
        assert elabora(dati)["forme_ammesse"][0]["stato"] == "da_verificare"


class TestScrub:
    def test_nessun_dominio_escluso_in_uscita(self):
        dati = copy.deepcopy(estrazione_base())
        dati["note"] = "Vedi www.obiettivoeuropa.com/bando per i dettagli"
        dati["vincoli"][0]["descrizione"] = "Guida su https://obiettivoeuropa.com/x"
        regole = elabora(dati)
        assert "obiettivoeuropa" not in str(regole)

    def test_stringhe_ostili_del_modello_tagliate_in_tempo_lineare(self):
        import time

        dati = copy.deepcopy(estrazione_base())
        dati["note"] = "a" * 30_000 + "obiettivoeuropa.com" + "b" * 30_000
        dati["vincoli"][0]["descrizione"] = "c" * 60_000
        inizio = time.perf_counter()
        regole = elabora(dati)
        assert time.perf_counter() - inizio < 1.0
        assert regole["note"] == "a" * 2000
        assert len(regole["vincoli"][0]["descrizione"]) == 2000
        assert "obiettivoeuropa" not in str(regole)

    def test_url_del_documento_bloccato_scartato(self):
        fonti = [{"n": 1, "etichetta": "Avviso", "url": "https://www.obiettivoeuropa.com/a.pdf"}]
        regole = post_elabora(PartenariatoEstrazione.model_validate(estrazione_base()), SEZIONI,
                              fonti, LOOKUPS)
        assert "obiettivoeuropa" not in str(regole)


class TestSchemaCompatto:
    """Codici e numeri arrivano come stringhe ("" = assente): la
    post-elaborazione li riporta ai tipi dei DTO senza mai sollevare."""

    def test_maiuscole_etichette_e_sinonimi(self):
        dati = estrazione_base(costituzione="Costituita richiesta")
        dati["forme_ammesse"][0]["forma"] = "ATI"
        dati["forme_ammesse"][1]["forma"] = "Contratto di rete"
        dati["composizione"][0].update(tipo_soggetto="Micro impresa", regioni=[])
        dati["quote"][0].update(categoria="PMI", base_calcolo="Spese ammissibili",
                                effetto_violazione="NON_INDICATO")
        dati["vincoli"][0].update(tipo="Sede operativa regione", momento="Domanda")
        dati["regole_finanziarie"][0].update(numeratore="Costo quota",
                                             denominatore="fatturato-medio-2", unita="Rapporto")
        dati["documenti_richiesti"][0].update(tipo="Lettera d'intenti", momento="concessione")
        regole = elabora(dati)
        assert regole["costituzione"]["valore"] == "costituita_richiesta"
        assert [f["forma"] for f in regole["forme_ammesse"]] == ["ati_rti", "rete_contratto"]
        comp = regole["composizione"][0]
        assert comp["tipo_soggetto"] == "micro_impresa" and comp["beneficiari"] == [22]
        assert comp["stato"] == "verificata"
        quota = regole["quote"][0]
        assert (quota["categoria"], quota["base_calcolo"], quota["effetto_violazione"]) == (
            "pmi", "spese_ammissibili", "non_indicato")
        assert quota["stato"] == "verificata"
        assert (regole["vincoli"][0]["tipo"], regole["vincoli"][0]["momento"]) == (
            "sede_operativa_regione", "domanda")
        regola = regole["regole_finanziarie"][0]
        assert (regola["numeratore"], regola["denominatore"], regola["unita"]) == (
            "costo_quota", "fatturato_medio_2", "rapporto")
        assert regola["stato"] == "verificata"
        assert regole["documenti_richiesti"][0]["tipo"] == "lettera_intenti"

    def test_sinonimo_di_variabile(self):
        dati = estrazione_base()
        dati["regole_finanziarie"][0].update(
            numeratore="EBITDA", denominatore="", soglia="0", operatore="gt", unita="euro")
        regola = elabora(dati)["regole_finanziarie"][0]
        assert regola["numeratore"] == "mol" and regola["denominatore"] is None

    def test_codici_ignoti_diventano_residuali_da_verificare(self):
        dati = estrazione_base(costituzione="entro 30 giorni")
        dati["forme_ammesse"][0]["forma"] = "GEIE"
        dati["composizione"][0].update(tipo_soggetto="Cluster tecnologico", regioni=[])
        dati["quote"][0].update(categoria="spin-off", base_calcolo="valore aggiunto",
                                effetto_violazione="revoca")
        dati["vincoli"][0].update(tipo="rating di legalità", momento="collaudo")
        dati["documenti_richiesti"][0]["tipo"] = "visura camerale"
        regole = elabora(dati)
        RegolePartenariatoOut.model_validate(regole)

        costituzione = regole["costituzione"]
        assert costituzione["valore"] == "non_indicato"
        assert costituzione["stato"] == "da_verificare"
        forma = regole["forme_ammesse"][0]
        assert (forma["forma"], forma["note"], forma["stato"]) == ("altra", "GEIE", "da_verificare")
        assert any("GEIE" in a for a in forma["avvisi"])
        comp = regole["composizione"][0]
        assert comp["tipo_soggetto"] == "altro"
        assert comp["tipo_soggetto_testo"] == "Cluster tecnologico"
        assert comp["stato"] == "da_verificare"
        quota = regole["quote"][0]
        assert (quota["categoria"], quota["base_calcolo"], quota["effetto_violazione"]) == (
            "altro", "non_indicata", "non_indicato")
        assert len(quota["avvisi"]) == 3 and quota["stato"] == "da_verificare"
        vincolo = regole["vincoli"][0]
        assert (vincolo["tipo"], vincolo["momento"], vincolo["stato"]) == (
            "altro", "non_indicato", "da_verificare")
        documento = regole["documenti_richiesti"][0]
        assert (documento["tipo"], documento["stato"]) == ("altro", "da_verificare")
        assert any("visura camerale" in a for a in documento["avvisi"])

    def test_valori_vuoti_sono_assenti_senza_avvisi(self):
        dati = estrazione_base(costituzione="", costituzione_citazione=NESSUNA,
                               partner_max="", partner_max_citazione=NESSUNA)
        dati["quote"][0].update(base_calcolo="", effetto_violazione="")
        dati["vincoli"][0]["momento"] = ""
        regole = elabora(dati)
        assert regole["costituzione"]["valore"] == "non_indicato"
        assert regole["costituzione"]["citazione"] is None
        assert regole["partner_max"]["valore"] is None
        assert regole["partner_max"]["citazione"] is None
        assert regole["partner_max"]["avvisi"] == []
        quota = regole["quote"][0]
        assert (quota["base_calcolo"], quota["effetto_violazione"]) == (
            "non_indicata", "non_indicato")
        assert quota["stato"] == "verificata"
        assert regole["vincoli"][0]["momento"] == "non_indicato"
        assert regole["vincoli"][0]["stato"] == "verificata"
        assert regole["conteggio_note"] is None and regole["note"] is None
        assert regole["composizione"][0]["tipo_soggetto_testo"] is None

    def test_tipo_vuoto_con_descrizione_e_altro(self):
        dati = estrazione_base()
        dati["composizione"][0].update(tipo_soggetto="", tipo_soggetto_testo="Cluster",
                                       regioni=[])
        comp = elabora(dati)["composizione"][0]
        assert (comp["tipo_soggetto"], comp["tipo_soggetto_testo"]) == ("altro", "Cluster")
        assert comp["stato"] == "verificata"

    def test_costituzione_senza_citazione(self):
        dati = estrazione_base(costituzione_citazione=NESSUNA)
        costituzione = elabora(dati)["costituzione"]
        assert costituzione["valore"] == "costituita_richiesta"
        assert costituzione["stato"] == "da_verificare"

    def test_numeri_come_stringhe(self):
        dati = estrazione_base(partner_min="3.0", partner_max=" 6 ")
        dati["quote"][0].update(min_percentuale="10%", max_percentuale="12,5")
        dati["vincoli"][0].update(tipo="costituzione_entro", parametro="30")
        regole = elabora(dati)
        assert (regole["partner_min"]["valore"], regole["partner_max"]["valore"]) == (3, 6)
        quota = regole["quote"][0]
        assert (quota["min_percentuale"], quota["max_percentuale"]) == (10.0, 12.5)
        assert regole["vincoli"][0]["parametro"] == 30.0

    def test_numeri_non_leggibili_da_verificare(self):
        dati = estrazione_base(partner_min="tre", partner_max="2.5")
        dati["composizione"][0].update(minimo="almeno 3", regioni=[])
        # «1.000»: migliaia o decimali? Meglio da verificare che sbagliato.
        dati["quote"][0].update(min_percentuale="1.000")
        dati["vincoli"][0]["parametro"] = "trenta giorni"
        regole = elabora(dati)
        for chiave in ("partner_min", "partner_max"):
            assert regole[chiave]["valore"] is None
            assert regole[chiave]["stato"] == "da_verificare"
            assert any("non leggibile" in a for a in regole[chiave]["avvisi"])
        comp = regole["composizione"][0]
        assert comp["minimo"] is None and comp["stato"] == "da_verificare"
        quota = regole["quote"][0]
        assert quota["min_percentuale"] is None and quota["stato"] == "da_verificare"
        assert "Quota senza percentuali" not in quota["avvisi"]
        vincolo = regole["vincoli"][0]
        assert vincolo["parametro"] is None and vincolo["stato"] == "da_verificare"

    def test_modalita_usa_i_numeri_letti(self):
        dati = estrazione_base(modalita="non_ammesso", forme_ammesse=[],
                               modalita_citazione=cit("D1-p3", "in forma singola"))
        regole = elabora(dati)  # partner_min "3"
        assert any("numero minimo" in a for a in regole["avvisi"])

    def test_modalita_con_conteggio_illeggibile_non_determinabile(self):
        """Il conteggio che potrebbe contraddire la modalità non si legge:
        la modalità non si conferma (è ciò che usa il filtro)."""
        dati = estrazione_base(modalita="non_ammesso", forme_ammesse=[],
                               modalita_citazione=cit("D1-p3", "in forma singola"),
                               partner_min="almeno 2")
        regole = elabora(dati)
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert regole["modalita"]["stato"] == "da_verificare"
        assert any(a.startswith("«Non ammesso»") and "minimo" in a for a in regole["avvisi"])

        sezioni = {**SEZIONI, PAGINA: "La domanda è presentata esclusivamente in forma associata."}
        dati = estrazione_base(modalita="obbligatorio",
                               modalita_citazione=cit(PAGINA, "esclusivamente in forma associata"),
                               partner_max="1 soggetto")
        regole = elabora(dati, sezioni)
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert any(a.startswith("«Obbligatorio»") and "massimo" in a for a in regole["avvisi"])

    def test_conteggio_illeggibile_non_pertinente_non_tocca_la_modalita(self):
        # «ammesso» non si contraddice con nessun conteggio.
        regole = elabora(estrazione_base(partner_min="almeno 2", partner_max="sei"))
        assert regole["modalita_effettiva"] == "ammesso"
        assert regole["avvisi"] == []

    def test_soglia_con_separatore_delle_migliaia_da_verificare(self):
        """«100.000» letto come decimale varrebbe 100: la regola resta, ma
        da verificare (esclusa dagli usi deterministici)."""
        dati = estrazione_base()
        dati["regole_finanziarie"][0].update(
            numeratore="patrimonio_netto", denominatore="", operatore="ge",
            soglia="100.000", unita="euro")
        regola = elabora(dati)["regole_finanziarie"][0]
        assert regola["soglia"] == "100.000"
        assert regola["stato"] == "da_verificare"
        assert any("ambigua" in a for a in regola["avvisi"])

        dati = estrazione_base()
        dati["regole_finanziarie"][0].update(
            numeratore="patrimonio_netto", denominatore="", operatore="ge", soglia="",
            soglia_variabile="costo_quota", soglia_coefficiente="1.500", unita="euro")
        regola = elabora(dati)["regole_finanziarie"][0]
        assert regola["stato"] == "da_verificare"
        assert any("ambiguo" in a for a in regola["avvisi"])

    def test_soglie_non_ambigue_restano_verificate(self):
        for soglia in ("100000", "0.600", "1.5", "100000,00"):
            dati = estrazione_base()
            dati["regole_finanziarie"][0].update(
                numeratore="patrimonio_netto", denominatore="", operatore="ge",
                soglia=soglia, unita="euro")
            regola = elabora(dati)["regole_finanziarie"][0]
            assert regola["stato"] == "verificata", soglia
        dati = estrazione_base()
        dati["regole_finanziarie"][0].update(
            numeratore="patrimonio_netto", denominatore="", operatore="ge", soglia="",
            soglia_variabile="costo_quota", soglia_coefficiente="0.5", unita="euro")
        assert elabora(dati)["regole_finanziarie"][0]["stato"] == "verificata"

    def test_startup_generica_non_e_startup_innovativa(self):
        """«Startup innovativa» è una categoria giuridica: una «startup»
        generica non si mappa in silenzio su di essa."""
        dati = estrazione_base()
        dati["composizione"][0].update(tipo_soggetto="startup", regioni=[])
        comp = elabora(dati)["composizione"][0]
        assert comp["tipo_soggetto"] == "altro"
        assert comp["tipo_soggetto_testo"] == "startup"
        assert comp["stato"] == "da_verificare"
        for valore in ("startup_innovativa", "Startup innovativa", "start up innovativa"):
            dati["composizione"][0]["tipo_soggetto"] = valore
            comp = elabora(dati)["composizione"][0]
            assert comp["tipo_soggetto"] == "startup_innovativa", valore
            assert comp["stato"] == "verificata", valore

    def test_vincolo_e_documento_senza_tipo_ne_descrizione(self):
        dati = estrazione_base()
        dati["vincoli"][0].update(tipo="", descrizione="")
        dati["documenti_richiesti"][0].update(tipo="", descrizione="")
        regole = elabora(dati)
        vincolo = regole["vincoli"][0]
        assert (vincolo["tipo"], vincolo["stato"]) == ("altro", "da_verificare")
        assert any("non descritto" in a for a in vincolo["avvisi"])
        documento = regole["documenti_richiesti"][0]
        assert (documento["tipo"], documento["stato"]) == ("altro", "da_verificare")
        assert any("non descritto" in a for a in documento["avvisi"])

    def test_vincolo_senza_tipo_con_parametro_da_verificare(self):
        # Il parametro ha senso solo per un tipo (paesi, giorni): senza tipo
        # non si sa che cosa conti.
        dati = estrazione_base()
        dati["vincoli"][0].update(tipo="", parametro="30")
        vincolo = elabora(dati)["vincoli"][0]
        assert vincolo["tipo"] == "altro" and vincolo["stato"] == "da_verificare"
        assert any("Tipo di vincolo non indicato" in a for a in vincolo["avvisi"])

    def test_altro_descritto_resta_verificato(self):
        dati = estrazione_base()
        dati["vincoli"][0].update(tipo="altro", descrizione="Rating di legalità")
        dati["documenti_richiesti"][0].update(tipo="", descrizione="Visura camerale")
        regole = elabora(dati)
        assert regole["vincoli"][0]["stato"] == "verificata"
        assert regole["documenti_richiesti"][0]["stato"] == "verificata"

    def test_regola_con_variabile_ignota_esclusa_con_avviso(self):
        dati = estrazione_base()
        dati["regole_finanziarie"].append({
            **dati["regole_finanziarie"][0], "id": "RF2", "numeratore": "rating bancario",
            "descrizione": "Rating almeno BB",
        })
        dati["regole_finanziarie"].append({
            **dati["regole_finanziarie"][0], "id": "RF3", "numeratore": "",
        })
        regole = elabora(dati)
        RegolePartenariatoOut.model_validate(regole)
        assert [r["id"] for r in regole["regole_finanziarie"]] == ["RF1"]
        assert any("RF2" in a and "rating bancario" in a and "Rating almeno BB" in a
                   for a in regole["avvisi"])
        assert any("RF3" in a for a in regole["avvisi"])

    def test_unita_ignota_calcolata_e_da_verificare(self):
        dati = estrazione_base()
        dati["regole_finanziarie"][0]["unita"] = "percentuale"
        regola = elabora(dati)["regole_finanziarie"][0]
        assert regola["unita"] == "rapporto" and regola["stato"] == "da_verificare"
        assert any("percentuale" in a for a in regola["avvisi"])

    def test_codici_lunghi_e_ostili_senza_eccezioni(self):
        import time

        dati = estrazione_base(costituzione="x" * 60_000, partner_min="9" * 60_000)
        dati["composizione"][0].update(tipo_soggetto="é" * 60_000, regioni=[])
        dati["regole_finanziarie"][0]["numeratore"] = "-" * 60_000
        inizio = time.perf_counter()
        regole = elabora(dati)
        assert time.perf_counter() - inizio < 1.0
        RegolePartenariatoOut.model_validate(regole)
        assert regole["costituzione"]["valore"] == "non_indicato"
        assert regole["partner_min"]["valore"] is None
        assert regole["composizione"][0]["tipo_soggetto"] == "altro"
        assert regole["regole_finanziarie"] == []


class TestAssenzaScrittaNeiNumeri:
    """Il modello scrive a volte l'assenza a parole al posto di "": vale
    assente (non «non leggibile»), altrimenti una modalità corretta si
    declassa per un conteggio che il bando semplicemente non fissa."""

    MARCATORI = (
        "null", "NULL", "N/A", "n/a", "NA", "nd",
        "n.d.", "N.D.", "n. d.", "Non indicato", "non indicata", "non specificato",
        "Non specificata.", "non previsto", "non prevista", "non presente", "assente",
        "non applicabile", "non definito", "nessun limite", "Senza limite", "illimitato",
        "Illimitata", "-", "–", "—", " - ", "--",
    )
    NON_MARCATORI = (
        "almeno 2", "nessun massimo indicato", "non indicato nel bando", "tre", "?", "/",
        "n", "limite", "0 (nessuno)", "- 3", "2-5",
        # JSON di un valore strutturato (convalida tollerante): presente, non assente
        "[null]", "{}", "(nessuno)",
    )

    @pytest.mark.parametrize("marcatore", MARCATORI)
    def test_marcatore_vale_assente(self, marcatore):
        dati = estrazione_base(partner_min=marcatore, partner_max=marcatore,
                               partner_min_citazione=NESSUNA, partner_max_citazione=NESSUNA)
        dati["composizione"][0].update(minimo=marcatore, massimo="4", regioni=[])
        dati["quote"][0].update(max_percentuale=marcatore)
        dati["vincoli"][0]["parametro"] = marcatore
        regole = elabora(dati)
        for chiave in ("partner_min", "partner_max"):
            assert regole[chiave]["valore"] is None
            assert regole[chiave]["avvisi"] == [], marcatore
        comp = regole["composizione"][0]
        assert comp["minimo"] is None and comp["stato"] == "verificata", marcatore
        quota = regole["quote"][0]
        assert (quota["min_percentuale"], quota["max_percentuale"]) == (10.0, None)
        assert quota["stato"] == "verificata", marcatore
        vincolo = regole["vincoli"][0]
        assert vincolo["parametro"] is None and vincolo["stato"] == "verificata", marcatore

    @pytest.mark.parametrize("valore", NON_MARCATORI)
    def test_il_resto_resta_non_leggibile(self, valore):
        regole = elabora(estrazione_base(partner_max=valore))
        assert regole["partner_max"]["valore"] is None
        assert regole["partner_max"]["avvisi"] == ["Numero massimo di partner non leggibile"]

    @pytest.mark.parametrize("marcatore", ["nessuno", "Nessuna", "nessun", "None"])
    def test_nessuno_assente_solo_sui_minimi(self, marcatore):
        """«Nessuno» su un minimo è «nessun minimo»; su un massimo può voler
        dire zero («grandi imprese: nessuna»): non leggibile, da verificare."""
        dati = estrazione_base(partner_min=marcatore, partner_max=marcatore,
                               partner_min_citazione=NESSUNA, partner_max_citazione=NESSUNA)
        dati["composizione"][0].update(minimo=marcatore, massimo="4", regioni=[])
        # il massimo compare nel passaggio citato («almeno il 10%…»)
        dati["quote"][0].update(min_percentuale=marcatore, max_percentuale="10")
        regole = elabora(dati)
        assert regole["partner_min"]["valore"] is None
        assert regole["partner_min"]["avvisi"] == []
        comp = regole["composizione"][0]
        assert comp["minimo"] is None and comp["stato"] == "verificata"
        quota = regole["quote"][0]
        assert (quota["min_percentuale"], quota["stato"]) == (None, "verificata")
        # sui massimi (e sui parametri) resta non leggibile
        assert regole["partner_max"]["avvisi"] == ["Numero massimo di partner non leggibile"]
        dati["composizione"][0].update(minimo="1", massimo=marcatore)
        dati["quote"][0].update(min_percentuale="10", max_percentuale=marcatore)
        dati["vincoli"][0]["parametro"] = marcatore
        regole = elabora(dati)
        comp = regole["composizione"][0]
        assert comp["massimo"] is None and comp["stato"] == "da_verificare"
        assert "Numero massimo non leggibile" in comp["avvisi"]
        quota = regole["quote"][0]
        assert quota["stato"] == "da_verificare" and "Percentuale non leggibile" in quota["avvisi"]
        assert "Parametro non leggibile" in regole["vincoli"][0]["avvisi"]

    def test_obbligatorio_con_massimo_scritto_a_parole_resta_obbligatorio(self):
        """Il caso della prima valutazione reale: «obbligatorio» giusto, ma il
        massimo «non indicato» lo declassava a non determinabile."""
        sezioni = {**SEZIONI, PAGINA: "La domanda è presentata esclusivamente in forma associata."}
        for marcatore in ("non indicato", "N/A", "—"):
            dati = estrazione_base(
                modalita="obbligatorio",
                modalita_citazione=cit(PAGINA, "esclusivamente in forma associata"),
                partner_max=marcatore, partner_max_citazione=NESSUNA,
            )
            regole = elabora(dati, sezioni)
            assert regole["modalita_effettiva"] == "obbligatorio", marcatore
            assert regole["avvisi"] == [], marcatore

    def test_marcatore_lungo_o_ostile_non_rallenta(self):
        import time

        dati = estrazione_base(partner_max="non indicato " * 5000, partner_min="-" * 60_000)
        inizio = time.perf_counter()
        regole = elabora(dati)
        assert time.perf_counter() - inizio < 1.0
        assert "Numero massimo di partner non leggibile" in regole["partner_max"]["avvisi"]
        assert "Numero minimo di partner non leggibile" in regole["partner_min"]["avvisi"]


def _non_ammesso(frase: str, citazione: str | None = None) -> dict:
    """Regole di un'estrazione «non_ammesso» che cita `citazione` (di default
    la frase intera) dalla pagina `PAGINA` = `frase`, senza conteggi né forme."""
    sezioni = {**SEZIONI, PAGINA: frase}
    dati = estrazione_base(
        modalita="non_ammesso", forme_ammesse=[],
        modalita_citazione=cit(PAGINA, citazione or frase),
        partner_min="", partner_min_citazione=NESSUNA,
        partner_max="", partner_max_citazione=NESSUNA,
    )
    return elabora(dati, sezioni)


AVVISO_ESCLUSIONE = "Manca un'esclusione esplicita della forma associata nel passaggio citato"


class TestNonAmmessoSoloConEsclusioneEsplicita:
    """«Non ammesso» toglie il bando dal filtro e blocca le call: vale solo
    con un'esclusione esplicita della forma associata (o l'obbligo della forma
    singola) nel passaggio citato, e se la sua frase non ammette l'aggregazione."""

    ESCLUSIONI = (
        "La domanda può essere presentata esclusivamente in forma singola.",
        "Le imprese partecipano al bando in forma singola.",
        "La domanda è presentata in forma individuale.",
        "Ciascuna impresa partecipa singolarmente.",
        "Il bando finanzia progetti presentati da singole imprese.",
        "Possono partecipare esclusivamente singole imprese.",
        "Non sono ammesse domande presentate in forma associata.",
        "Non sono ammessi raggruppamenti, consorzi o reti di imprese.",
        "Non è ammessa la partecipazione in forma aggregata.",
        "Non è possibile presentare domanda in forma congiunta.",
        "Non è ammessa la presentazione di domande da parte di raggruppamenti temporanei.",
        "Non sono ammesse ATI o ATS.",
        "Non è ammessa la partecipazione in forma associata (ATI, ATS, reti).",
        "Il bando non prevede la partecipazione in forma aggregata.",
        "È esclusa la partecipazione in forma associata.",
        "Sono esclusi i raggruppamenti temporanei di imprese.",
        "Le domande presentate in forma associata non sono ammesse.",
        "I raggruppamenti di imprese sono esclusi.",
        "Le imprese non possono associarsi.",
        "Le imprese non possono associarsi tra loro.",
        "La domanda è presentata in forma singola: non sono ammesse domande in forma associata.",
        "Non sono ammessi raggruppamenti: le imprese partecipano in forma singola.",
        "La domanda deve essere presentata in forma singola.",
        "Ciascuna impresa si candida singolarmente.",
        "Only single applicants are eligible under this call.",
        "Consortia are not eligible for funding.",
        "Proposals must be submitted by a single applicant.",
        "The proposal must be submitted individually.",
        "This is a mono-beneficiary action.",
    )
    NON_ESCLUSIONI = (
        # le quattro forme della prima valutazione reale, riscritte
        "Possono presentare domanda di agevolazione le PMI con sede nel territorio regionale.",
        "Sono destinatari del contributo le imprese in forma singola o associata.",
        "Possono richiedere il contributo le imprese e i datori di lavoro privati.",
        "È ammessa per ciascuna impresa una sola domanda di contributo.",
        # altri elenchi e limiti che non escludono l'aggregazione
        "Ogni impresa può presentare una sola domanda.",
        "Possono partecipare le imprese in forma singola, o associata.",
        "Le imprese possono partecipare singolarmente o in forma congiunta.",
        "Sia in forma singola, sia in forma collaborativa con altre imprese.",
        "Le PMI, anche in forma singola o costituite in ATI/ATS.",
        "Le imprese in forma singola possono partecipare anche in ATS.",
        "Applicants may apply individually or as a consortium.",
        # vincoli che presuppongono l'aggregazione, non la escludono
        "Non sono ammesse modifiche della composizione del raggruppamento.",
        "Non è ammessa la partecipazione dello stesso soggetto a più raggruppamenti.",
        "Il bando non prevede un numero massimo di partner nel raggruppamento.",
        "Sono esclusi dal raggruppamento i soggetti in difficoltà.",
        "Le spese del raggruppamento non sono ammissibili.",
        "Le imprese non possono partecipare ad altri raggruppamenti.",
        "Il capofila del raggruppamento non può presentare altre domande.",
        # «in forma singola», «singolarmente», «a titolo individuale» senza
        # un obbligo, o in una frase che presuppone l'aggregazione
        "Non è ammessa la partecipazione contemporanea in forma singola e associata.",
        "In caso di partecipazione in forma singola, il contributo massimo è di 50.000 euro.",
        "Il soggetto proponente in forma singola deve avere sede in Puglia.",
        "Possono presentare domanda le PMI, in forma singola e associata, con sede in Puglia.",
        "Possono presentare domanda le imprese in forma singola.",
        "Ciascuna impresa aderente alla rete presenta singolarmente la propria domanda.",
        "Le spese devono essere sostenute singolarmente da ciascun partner del raggruppamento.",
        "Il soggetto può partecipare a titolo individuale o in qualità di componente di un "
        "raggruppamento.",
        "Proposals must be submitted by a single applicant or by a consortium.",
        "Le imprese non devono partecipare in forma singola.",
        # negazioni di un TIPO di aggregazione: la regolano, non la escludono
        "Non sono ammessi raggruppamenti tra imprese collegate o controllate ai sensi "
        "dell'art. 2359 c.c.",
        "Non è ammesso il partenariato con soggetti aventi sede al di fuori del territorio "
        "regionale.",
        "Non sono ammesse aggregazioni tra imprese appartenenti al medesimo gruppo.",
        "Non è previsto un partenariato minimo.",
        "Non sono ammesse domande in forma associata per la linea A.",
        "Consortia with more than ten partners are not eligible.",
    )

    @pytest.mark.parametrize("frase", ESCLUSIONI)
    def test_esclusione_esplicita_conferma(self, frase):
        regole = _non_ammesso(frase)
        assert regole["modalita_effettiva"] == "non_ammesso"
        assert regole["modalita"]["stato"] == "verificata"
        assert regole["modalita"]["avvisi"] == []

    @pytest.mark.parametrize("frase", NON_ESCLUSIONI)
    def test_senza_esclusione_non_determinabile(self, frase):
        regole = _non_ammesso(frase)
        modalita = regole["modalita"]
        assert modalita["valore"] == "non_ammesso"
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert modalita["stato"] == "da_verificare"
        assert AVVISO_ESCLUSIONE in modalita["avvisi"]
        # la citazione resta verificata: il problema è ciò che dice
        assert modalita["citazione"]["verificata"] is True

    def test_citazione_troncata_letta_nella_sua_frase(self):
        """«le imprese in forma singola» esclude solo se la frase del testo non
        continua con «o associata»."""
        frase = "Possono partecipare le imprese in forma singola o associata mediante ATS."
        regole = _non_ammesso(frase, citazione="Possono partecipare le imprese in forma singola")
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert AVVISO_ESCLUSIONE in regole["modalita"]["avvisi"]

        frase = ("Le imprese partecipano in forma singola. Le imprese associate "
                 "possono partecipare in forma aggregata al bando collegato.")
        for citazione in ("Le imprese partecipano in forma singola",
                          # la citazione chiude la sua frase: la successiva non conta
                          "Le imprese partecipano in forma singola."):
            regole = _non_ammesso(frase, citazione=citazione)
            assert regole["modalita_effettiva"] == "non_ammesso", citazione

    def test_frase_oltre_le_abbreviazioni_e_il_punto_e_virgola(self):
        """La frase non si ferma a «Reg.», «n.», «art.» né al «;» di un elenco:
        il seguito «o in forma associata» conta."""
        for frase in (
            "Le imprese partecipano in forma singola, come definite dall'Allegato I del Reg. "
            "(UE) n. 651/2014, o in forma associata mediante ATS.",
            "Le imprese partecipano in forma singola ai sensi dell'art. 3; in alternativa, in "
            "forma associata mediante ATS.",
        ):
            regole = _non_ammesso(frase, citazione="Le imprese partecipano in forma singola")
            assert regole["modalita"]["citazione"]["verificata"] is True
            assert regole["modalita_effettiva"] == "non_determinabile", frase
            assert AVVISO_ESCLUSIONE in regole["modalita"]["avvisi"]

    def test_le_altre_modalita_non_cambiano(self):
        sezioni = {**SEZIONI, PAGINA: "Possono presentare domanda le PMI con sede in Piemonte."}
        dati = estrazione_base(modalita_citazione=cit(PAGINA, "Possono presentare domanda le PMI"))
        regole = elabora(dati, sezioni)
        assert regole["modalita_effettiva"] == "ammesso"
        assert AVVISO_ESCLUSIONE not in regole["modalita"]["avvisi"]

    def test_senza_citazione_un_solo_avviso(self):
        dati = estrazione_base(modalita="non_ammesso", forme_ammesse=[],
                               modalita_citazione=NESSUNA, partner_min="",
                               partner_min_citazione=NESSUNA)
        modalita = elabora(dati)["modalita"]
        assert modalita["avvisi"] == ["Manca il passaggio del bando che lo stabilisce"]

    def test_citazione_ostile_in_tempo_lineare(self):
        import time

        ostile = ("non sono ammesse " + "la " * 20_000 + "partecipazione ") * 3
        ostile += "in forma singola " * 3000 + "o" * 50_000
        inizio = time.perf_counter()
        regole = _non_ammesso(ostile * 2, citazione=ostile)
        assert time.perf_counter() - inizio < 1.0
        RegolePartenariatoOut.model_validate(regole)


def _non_ammesso_su(sezioni: dict, sezione: str, citazione: str) -> dict:
    dati = estrazione_base(
        modalita="non_ammesso", forme_ammesse=[],
        modalita_citazione=cit(sezione, citazione),
        partner_min="", partner_min_citazione=NESSUNA,
        partner_max="", partner_max_citazione=NESSUNA,
    )
    return elabora(dati, {**SEZIONI, **sezioni})


class TestNonAmmessoSoloSullaFraseAllaLettera:
    """Le tolleranze della verifica (spazi del PDF, pagina precedente,
    ellissi) rendono verificate citazioni che non si ritrovano alla lettera: di
    quelle la frase vera non si ricostruisce, e «non ammesso» non vale (nel
    dubbio non determinabile). A cavallo di pagina, se si ritrova alla lettera,
    vale sulla sua frase intera."""

    def _nd(self, regole):
        assert regole["modalita"]["citazione"]["verificata"] is True
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert AVVISO_ESCLUSIONE in regole["modalita"]["avvisi"]

    def test_spazi_spuri_del_pdf(self):
        self._nd(_non_ammesso_su(
            {PAGINA: "La domanda è presen tata in forma singola o associata in ATS."},
            PAGINA, "La domanda è presentata in forma singola"))

    def test_ellissi(self):
        self._nd(_non_ammesso_su(
            {PAGINA: "La domanda è presentata in forma singola o associata in ATS dalle imprese "
                   "aventi sede operativa in Puglia."},
            PAGINA, "La domanda è presentata in forma singola [...] dalle imprese aventi sede "
                  "operativa in Puglia"))

    def test_a_cavallo_con_la_pagina_precedente(self):
        pagine = {"D2-p1": "Art. 3 - Beneficiari\nLe imprese partecipano",
                  "D2-p2": "in forma singola o associata mediante ATS. Art. 4 - Spese"}
        self._nd(_non_ammesso_su(pagine, "D2-p2", "Le imprese partecipano in forma singola"))
        # la stessa esclusione, senza il seguito, vale
        pagine["D2-p2"] = "in forma singola. Art. 4 - Spese"
        regole = _non_ammesso_su(pagine, "D2-p2", "Le imprese partecipano in forma singola")
        assert regole["modalita_effettiva"] == "non_ammesso"


AVVISO_NON_QUOTA = "Il passaggio citato non sembra ripartire il costo del progetto tra i partner"


def _quota(frase: str, **campi) -> dict:
    sezioni = {**SEZIONI, PAGINA: frase}
    dati = estrazione_base()
    dati["quote"][0].update(citazione=cit(PAGINA, frase), **campi)
    return elabora(dati, sezioni)["quote"][0]


class TestQuoteNonDiPartenariato:
    """Una quota ripartisce il costo del PROGETTO tra i soggetti del
    partenariato: se il passaggio non nomina il partenariato né chi sostiene la
    quota, o parla di aiuti senza nominare il partenariato, la voce resta ma è
    da verificare (mai scartata in silenzio)."""

    NON_QUOTE = (
        "Il contributo è concesso nella misura del 50% delle spese ammissibili.",
        "L'intensità di aiuto è pari al 40% dei costi ammissibili per ciascuna impresa.",
        "La percentuale di finanziamento è aumentata di 5 punti percentuali per i progetti "
        "con soggetti aggregatori.",
        "Il 60% delle risorse è riservato alle micro, piccole e medie imprese.",
        "Le spese di progettazione non possono superare il 10% del totale.",
        "Le spese sostenute per consulenze non possono superare il 20% delle spese ammissibili.",
        "At least 60% of the eligible costs must be incurred in less developed regions.",
        "Subcontracting beyond 30% of the eligible costs must be justified.",
        "Up to 20% of the EU funding may be used for financial support to third parties.",
    )
    # (frase, min, max): la percentuale deve comparire nel passaggio citato
    QUOTE = (
        ("Ciascun partner deve sostenere almeno il 10% del costo totale del progetto.",
         "10", ""),
        ("Il costo delle attività del capofila deve essere superiore al 25% del costo totale.",
         "25", ""),
        ("Nessuna impresa beneficiaria sostiene da sola più di due terzi delle spese "
         "ammissibili.", "", "66.67"),
        ("Il costo delle attività svolte complessivamente dagli organismi di ricerca non può "
         "eccedere il 30% del costo totale del progetto.", "", "30"),
        ("Le PMI devono sostenere almeno il 40% dei costi del progetto.", "40", ""),
        ("La quota di partecipazione delle grandi imprese non può superare il 30% del costo.",
         "", "30"),
        ("Each beneficiary must carry at least 10% of the eligible costs.", "10", ""),
        ("Il contributo di ciascun partner al budget non può superare il 70%.", "", "70"),
    )

    @pytest.mark.parametrize("frase", NON_QUOTE)
    def test_non_quota_resta_da_verificare(self, frase):
        quota = _quota(frase)
        assert quota["stato"] == "da_verificare"
        assert any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])
        assert quota["citazione"]["verificata"] is True
        assert quota["min_percentuale"] == 10.0  # la voce resta com'è

    @pytest.mark.parametrize(("frase", "minimo", "massimo"), QUOTE)
    def test_quota_di_partenariato_non_segnalata(self, frase, minimo, massimo):
        quota = _quota(frase, min_percentuale=minimo, max_percentuale=massimo)
        assert not any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"]), frase
        assert quota["stato"] == "verificata"

    def test_la_frase_finisce_col_punto_della_citazione(self):
        """Una citazione che chiude la sua frase non prende il soggetto dalla
        frase successiva."""
        sezioni = {**SEZIONI, PAGINA: "Il contributo è pari al 50% delle spese ammissibili. "
                                    "Ciascun partner sostiene almeno il 10% del costo."}
        dati = estrazione_base()
        dati["quote"][0].update(citazione=cit(
            PAGINA, "Il contributo è pari al 50% delle spese ammissibili."))
        quota = elabora(dati, sezioni)["quote"][0]
        assert any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])

    def test_frammento_con_il_soggetto_nella_frase(self):
        # la citazione di base è «almeno il 10% delle spese ammissibili»: il
        # soggetto («Ciascun partner») è nel resto della frase
        quota = elabora(estrazione_base())["quote"][0]
        assert quota["stato"] == "verificata" and quota["avvisi"] == []

    def test_senza_citazione_nessun_giudizio_sul_testo(self):
        for citazione in (NESSUNA, cit(PAGINA, "  ")):
            dati = estrazione_base()
            dati["quote"][0]["citazione"] = citazione
            quota = elabora(dati)["quote"][0]
            assert quota["stato"] == "da_verificare"
            assert not any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])


class TestCategoriaDellaQuota:
    """Con ambito «per_partner» o «capofila» un ruolo scritto al posto della
    categoria («qualsiasi», «capofila») vale «nessuna categoria», non un tipo
    di soggetto ignoto che manda la quota da verificare."""

    FRASE = "Ciascun partner e il capofila sostengono almeno il 10% del costo del progetto."

    @pytest.mark.parametrize(("ambito", "categoria"), [
        ("per_partner", "qualsiasi"), ("per_partner", "Tutti i partner"),
        ("per_partner", "partner"), ("capofila", "capofila"), ("capofila", "Mandataria"),
    ])
    def test_ruolo_come_categoria_vale_nessuna(self, ambito, categoria):
        quota = _quota(self.FRASE, ambito=ambito, categoria=categoria)
        assert quota["categoria"] is None
        assert quota["stato"] == "verificata" and quota["avvisi"] == []

    @pytest.mark.parametrize(("ambito", "categoria"), [
        ("per_categoria", "qualsiasi"), ("per_partner", "capofila"), ("capofila", "qualsiasi"),
    ])
    def test_ruolo_fuori_posto_resta_ignoto(self, ambito, categoria):
        quota = _quota(self.FRASE, ambito=ambito, categoria=categoria)
        assert quota["categoria"] == "altro"
        assert quota["stato"] == "da_verificare"
        assert any("non riconosciuto" in a for a in quota["avvisi"])

    def test_categoria_vera_resta(self):
        quota = _quota(self.FRASE, ambito="per_partner", categoria="PMI")
        assert quota["categoria"] == "pmi" and quota["stato"] == "verificata"


AVVISO_PERCENTUALE = "La percentuale non compare nel passaggio citato"


def _avvisi_non_quota(quota: dict) -> list[str]:
    return [a for a in quota["avvisi"] if a.startswith(AVVISO_NON_QUOTA)]


class TestEsclusioniForti:
    """Quote di adesione, d'iscrizione o associative, cofinanziamento e
    intensità di aiuto non ripartiscono il costo del progetto tra i partner,
    anche quando la frase nomina il partenariato: la voce resta, da
    verificare, con l'avviso non-quota."""

    # (frase, min, max): le percentuali sono nel testo, l'unico avviso è
    # quello della non-quota
    FRASI = (
        # 18177, D1-p30: «aderenti» nomina il partenariato
        ("L’eventuale quota di adesione richiesta alle imprese aderenti non potrà superare il "
         "50% del costo del progetto realizzato dal promotore", "", "50"),
        ("La quota d’iscrizione richiesta a ciascun partner non può superare il 20% del costo.",
         "", "20"),
        ("La quota d'iscrizione richiesta a ciascun partner non può superare il 20% del costo.",
         "", "20"),
        ("La quota di iscrizione dei partner non può superare il 20% del costo del progetto.",
         "", "20"),
        ("Le quote associative dei partner non possono superare il 10% del budget.", "", "10"),
        ("Il cofinanziamento a carico di ciascun partner è pari almeno al 20% del costo.",
         "20", ""),
        ("Il co-finanziamento del capofila non può essere inferiore al 25% del costo.", "25", ""),
        ("L’intensità dell’aiuto per ciascun partner è pari al 40% dei costi ammissibili.",
         "", "40"),
        ("L'intensità di aiuto per il capofila non supera il 50% del costo del progetto.",
         "", "50"),
        # con un aggettivo in mezzo
        ("L'intensità massima dell'aiuto per il partner è pari al 40% dei costi ammissibili.",
         "", "40"),
        ("L’intensità massima di aiuto per ciascun partner è del 50% del costo del progetto.",
         "", "50"),
        # l'accento scritto come apostrofo
        ("L'intensita' massima dell'aiuto per il partner è pari al 40% dei costi.", "", "40"),
        ("L’intensita’ di aiuto per il capofila non supera il 50% del costo.", "", "50"),
        # il contributo pubblico
        ("Il contributo concesso a ciascun partner non può superare il 50% del costo del "
         "progetto.", "", "50"),
        ("Il contributo pubblico concesso al capofila è pari al 40% delle spese ammissibili.",
         "", "40"),
        ("Il contributo a fondo perduto di ciascun partner è pari al 30% del costo del "
         "progetto.", "", "30"),
        ("Il contributo è concesso a ciascun partner nella misura del 50% del costo del "
         "progetto.", "", "50"),
        ("Il contributo viene concesso al capofila fino al 40% delle spese ammissibili.",
         "", "40"),
    )

    @pytest.mark.parametrize(("frase", "minimo", "massimo"), FRASI)
    def test_non_ripartizione_da_verificare(self, frase, minimo, massimo):
        quota = _quota(frase, min_percentuale=minimo, max_percentuale=massimo)
        assert quota["stato"] == "da_verificare"
        assert len(_avvisi_non_quota(quota)) == 1 and len(quota["avvisi"]) == 1, quota["avvisi"]
        assert quota["citazione"]["verificata"] is True
        # la voce resta com'è
        assert quota["max_percentuale"] == (float(massimo) if massimo else None)

    def test_quota_di_partecipazione_resta_una_quota(self):
        quota = _quota(
            "La quota di partecipazione di ciascun partner non può superare il 40% del costo "
            "del progetto.", min_percentuale="", max_percentuale="40")
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])


class TestPercentualeNellaFrase:
    """Ogni min/max della quota deve comparire nella frase citata: in cifre
    seguite da «%» o «per cento» (tolleranza 0,01) o come frazione (tolleranza
    0,5). Altrimenti la voce resta, da verificare, con un avviso."""

    FRASE = ("Ciascun partner deve sostenere almeno il 10% e non più del 33,33 per cento del "
             "costo del progetto.")

    @pytest.mark.parametrize(("minimo", "massimo"), [
        ("10", ""), ("", "33.33"), ("10", "33,33"), ("10%", "33.33"),
    ])
    def test_percentuale_presente(self, minimo, massimo):
        quota = _quota(self.FRASE, min_percentuale=minimo, max_percentuale=massimo)
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    @pytest.mark.parametrize(("minimo", "massimo"), [
        ("", "30"),  # numero assente dal passaggio
        ("0.1", ""),  # frazione decimale al posto di 10
        ("", "33.3"),  # oltre la tolleranza di 0,01
        ("12", "40"),  # entrambi assenti: un solo avviso
        ("10", "66.67"),  # uno dei due assente
    ])
    def test_percentuale_assente_da_verificare(self, minimo, massimo):
        quota = _quota(self.FRASE, min_percentuale=minimo, max_percentuale=massimo)
        assert quota["stato"] == "da_verificare"
        assert quota["avvisi"] == [AVVISO_PERCENTUALE]
        assert quota["citazione"]["verificata"] is True

    @pytest.mark.parametrize(("frase", "minimo", "massimo", "presente"), [
        ("Ciascun proponente sostiene almeno il 10 per cento dei costi ammissibili.",
         "10", "", True),
        ("Ciascun partner sostiene almeno il 12,5 % del costo del progetto.", "12.5", "", True),
        ("Nessun partner sostiene da solo più di due terzi del costo del progetto.",
         "", "66.67", True),
        ("Nessun partner sostiene da solo più di due terzi del costo del progetto.",
         "", "67", True),
        ("Nessun partner sostiene da solo più di due terzi del costo del progetto.",
         "", "66", False),
        ("Nessun partner sostiene da solo più dei 2/3 del costo del progetto.",
         "", "66.67", True),
        ("Il capofila sostiene almeno la metà del costo del progetto.", "50", "", True),
        ("Il capofila sostiene almeno la meta' del costo del progetto.", "50", "", True),
        # «3/5» non è tra le frazioni ammesse
        ("Il capofila sostiene almeno i 3/5 del costo del progetto.", "60", "", False),
        # una data non è una frazione
        ("Entro il 1/2/2027 il capofila sostiene almeno il 10% del costo.", "50", "", False),
        # «un terzo soggetto», «un terzo fornitore»: non sono frazioni
        ("Il capofila affida a un terzo soggetto non più del 10% del costo del progetto.",
         "", "33.33", False),
        ("Ciascun partner affida a un terzo fornitore al massimo il 10% del costo.",
         "", "33.33", False),
        ("Il capofila sostiene almeno un terzo delle spese del progetto.", "33.33", "", True),
        # ordinali seguiti da un soggetto o da una sequenza: non sono frazioni
        ("Il capofila affida a un quarto soggetto al massimo il 10% del costo.",
         "", "25", False),
        ("Ciascun partner, anche un quinto partner, sostiene almeno il 10% del costo.",
         "20", "", False),
        ("Il capofila sostiene almeno il 10% del costo di un terzo lotto.", "33.33", "", False),
        ("Il capofila sostiene almeno il 10% del costo e affida a un terzo ente le prove.",
         "", "33.33", False),
        # «un terzo entro…» resta una frazione
        ("Il capofila sostiene un terzo entro il primo anno del costo del progetto.",
         "33.33", "", True),
        # la metà in senso temporale non è una frazione
        ("A metà del periodo il capofila sostiene almeno il 10% del costo.", "50", "", False),
        ("Il capofila sostiene almeno il 10% del costo entro la metà dell'anno.", "50", "",
         False),
        # intervalli: valgono entrambi i numeri
        ("Ciascun partner sostiene tra il 20 e il 40% del costo del progetto.", "20", "40",
         True),
        ("Ciascun partner sostiene tra il 20% e il 40% del costo del progetto.", "20", "40",
         True),
        ("Ciascun partner sostiene tra il 20 ed il 40% del costo del progetto.", "20", "40",
         True),
        ("Ciascun partner sostiene dal 20 al 40% del costo del progetto.", "20", "40", True),
        ("Ciascun partner sostiene dall'8 al 12 per cento del costo.", "8", "12", True),
        ("Ciascun partner sostiene il 20-40% del costo del progetto.", "20", "40", True),
        ("Ciascun partner sostiene il 20 – 40 % del costo del progetto.", "20", "40", True),
        # un numero qualunque prima di un intervallo non vale
        ("All'articolo 20 ciascun partner sostiene il 40% del costo.", "20", "40", False),
        # il numero in lettere tra parentesi
        ("Ciascun partner sostiene almeno il 30 (trenta) per cento del costo.", "30", "",
         True),
        ("Ciascun partner sostiene almeno il 30 (trenta)% del costo.", "30", "", True),
    ])
    def test_forme_della_percentuale(self, frase, minimo, massimo, presente):
        quota = _quota(frase, min_percentuale=minimo, max_percentuale=massimo)
        assert (AVVISO_PERCENTUALE not in quota["avvisi"]) is presente, quota["avvisi"]
        assert (quota["stato"] == "verificata") is presente

    def test_senza_citazione_nessun_giudizio(self):
        for citazione in (NESSUNA, cit(PAGINA, "  ")):
            dati = estrazione_base()
            dati["quote"][0].update(citazione=citazione, max_percentuale="99")
            quota = elabora(dati)["quote"][0]
            assert quota["stato"] == "da_verificare"
            assert AVVISO_PERCENTUALE not in quota["avvisi"]

    def test_con_la_percentuale_fuori_range(self):
        dati = estrazione_base()
        dati["quote"][0]["min_percentuale"] = "120"
        quota = elabora(dati)["quote"][0]
        assert "Percentuale fuori dall'intervallo 0-100" in quota["avvisi"]
        assert AVVISO_PERCENTUALE in quota["avvisi"]


class TestFrazioni:
    """Min e max delle quote accettano le frazioni della tabella («due terzi»,
    «2/3» → 66.67); altrove una frazione resta non leggibile."""

    @pytest.mark.parametrize(("valore", "atteso"), [
        ("2/3", 66.67), ("2 / 3", 66.67), ("1/2", 50.0), ("1/3", 33.33), ("3/4", 75.0),
        ("1/4", 25.0), ("1/5", 20.0), ("metà", 50.0), ("la metà", 50.0), ("La Metà", 50.0),
        ("un terzo", 33.33), ("due terzi", 66.67), ("Due  Terzi", 66.67), ("un quarto", 25.0),
        ("tre quarti", 75.0), ("un quinto", 20.0),
    ])
    def test_frazione_convertita(self, valore, atteso):
        quota = _quota(f"Il capofila sostiene al massimo {valore} del costo del progetto.",
                       min_percentuale="", max_percentuale=valore)
        assert quota["max_percentuale"] == atteso
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    def test_frazione_sul_minimo(self):
        quota = _quota("Il capofila sostiene almeno un terzo del costo del progetto.",
                       min_percentuale="un terzo", max_percentuale="")
        assert (quota["min_percentuale"], quota["stato"]) == (33.33, "verificata")

    @pytest.mark.parametrize("valore", ["3/5", "5/7", "due quarti", "un sesto", "2/3 circa",
                                        "2/3" * 50])
    def test_fuori_tabella_non_leggibile(self, valore):
        quota = _quota("Il capofila sostiene almeno il 10% del costo del progetto.",
                       max_percentuale=valore)
        assert quota["max_percentuale"] is None
        assert quota["stato"] == "da_verificare"
        assert "Percentuale non leggibile" in quota["avvisi"]

    @pytest.mark.parametrize(("valore", "testo", "atteso"), [
        # il prompt chiede la frazione senza articolo, il modello può metterlo
        ("i due terzi", "i due terzi", 66.67),
        ("I due terzi", "i due terzi", 66.67),
        ("i 2/3", "i 2/3", 66.67),
        ("la metà", "la metà", 50.0),
        ("il 30%", "il 30%", 30.0),
        ("l'80%", "l'80%", 80.0),
        ("l’ 80", "l'80%", 80.0),
        ("lo 0,5", "lo 0,5%", 0.5),
    ])
    def test_articolo_iniziale(self, valore, testo, atteso):
        quota = _quota(f"Il capofila sostiene al massimo {testo} del costo del progetto.",
                       min_percentuale="", max_percentuale=valore)
        assert quota["max_percentuale"] == atteso
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    @pytest.mark.parametrize("valore", ["gli due terzi", "il", "l'", "il circa 30", "le 30"])
    def test_articolo_senza_valore_leggibile(self, valore):
        quota = _quota("Il capofila sostiene almeno il 10% del costo del progetto.",
                       max_percentuale=valore)
        assert quota["max_percentuale"] is None
        assert "Percentuale non leggibile" in quota["avvisi"]

    def test_solo_per_le_quote(self):
        dati = estrazione_base(partner_min="metà")
        dati["vincoli"][0].update(tipo="costituzione_entro", parametro="2/3")
        dati["composizione"][0].update(minimo="un terzo", regioni=[])
        regole = elabora(dati)
        assert regole["partner_min"]["valore"] is None
        assert regole["partner_min"]["stato"] == "da_verificare"
        assert regole["vincoli"][0]["parametro"] is None
        assert "Parametro non leggibile" in regole["vincoli"][0]["avvisi"]
        assert regole["composizione"][0]["minimo"] is None
        assert regole["composizione"][0]["stato"] == "da_verificare"

    def test_testo_lungo_senza_punti_in_tempo_lineare(self):
        import time

        ostile = ("intensità di " * 3000 + "1/" * 5000 + "12,3456789" * 2000 + " due " * 3000
                  + "quota di " * 3000)
        dati = estrazione_base()
        dati["quote"][0].update(citazione=cit(PAGINA, ostile), max_percentuale="2/3")
        inizio = time.perf_counter()
        quota = elabora(dati, {**SEZIONI, PAGINA: ostile})["quote"][0]
        assert time.perf_counter() - inizio < 1.0
        assert quota["stato"] == "da_verificare"


# 171905, D1-p10 (decreto MASE): le tre quote giuste del campione, con gli
# spazi spuri del PDF («Or ganismi», «Pr oposta»), accanto ai limiti per voce
# di spesa che NON sono quote (commi 2, 3 e 7).
PAGINA_171905 = (
    "2.  Il costo totale delle attività di Ricerca Industriale, come r iportato nella Proposta di "
    "progetto, \nnon può essere superiore al 70% del costo complessivo.   \n3.  Il costo totale "
    "delle attività di Studio di Fattibilità, come riportato nella Proposta di progetto, \nnon "
    "può essere superiore al 10% del costo complessivo.  \n4.  Il costo totale delle attività "
    "svolte complessivamente dagli Or ganismi di ricerca e diffusione \ndella conoscenza, se "
    "presenti, come riportato nella Proposta di progetto, non può eccedere il \n33% del costo "
    "totale del progetto.   \n5.  Il costo delle attività di ciascun partecipante alla compagine "
    "progettuale, come previsto nella \nProposta di progetto, deve essere pari almeno al 10% del "
    "costo totale del progetto.  \n6.  Il costo delle attività del Capofila, come riportato "
    "nella Pr oposta di progetto, deve essere \nsuperiore al 25% del costo totale del progetto.  "
    "  \n7.  Per ciascun partecipante alla compagine progettuale, compreso il Capofila, la voce "
    "di costo D \n“Costi per servizi di consulenza, acquisizione di competenze tecnic he, "
    "brevetti” non può eccedere il 35% del costo totale de lle proprie attività \npreventivate."
)
QUOTE_171905 = [
    {"id": "Q1", "ambito": "per_partner", "categoria": "", "min_percentuale": "10",
     "max_percentuale": "", "base_calcolo": "costo_totale_progetto",
     "effetto_violazione": "non_indicato",
     "citazione": cit("D1-p10", "Il costo delle attività di ciascun partecipante alla compagine "
                                "progettuale, come previsto nella Proposta di progetto, deve "
                                "essere pari almeno al 10% del costo totale del progetto.")},
    {"id": "Q2", "ambito": "capofila", "categoria": "", "min_percentuale": "25",
     "max_percentuale": "", "base_calcolo": "costo_totale_progetto",
     "effetto_violazione": "non_indicato",
     "citazione": cit("D1-p10", "Il costo delle attività del Capofila, come riportato nella "
                                "Proposta di progetto, deve essere superiore al 25% del costo "
                                "totale del progetto.")},
    {"id": "Q3", "ambito": "per_categoria", "categoria": "organismo_ricerca",
     "min_percentuale": "", "max_percentuale": "33", "base_calcolo": "costo_totale_progetto",
     "effetto_violazione": "non_indicato",
     "citazione": cit("D1-p10", "Il costo totale delle attività svolte complessivamente dagli "
                                "Organismi di ricerca e diffusione della conoscenza, se presenti, "
                                "come riportato nella Proposta di progetto, non può eccedere il "
                                "33% del costo totale del progetto.")},
]


class Test171905:
    """Non regressione sul campione: le tre quote giuste di 171905 restano
    identiche, verificate e senza avvisi."""

    def test_tre_quote_verificate(self):
        dati = estrazione_base(quote=copy.deepcopy(QUOTE_171905))
        regole = elabora(dati, {**SEZIONI, "D1-p10": PAGINA_171905})
        quote = regole["quote"]
        assert [
            (q["id"], q["ambito"], q["categoria"], q["min_percentuale"], q["max_percentuale"])
            for q in quote
        ] == [
            ("Q1", "per_partner", None, 10.0, None),
            ("Q2", "capofila", None, 25.0, None),
            ("Q3", "per_categoria", "organismo_ricerca", None, 33.0),
        ]
        for quota in quote:
            assert (quota["stato"], quota["avvisi"]) == ("verificata", []), quota["id"]
            assert quota["citazione"]["verificata"] is True
            assert quota["base_calcolo"] == "costo_totale_progetto"
        assert not any("quota" in a.lower() for a in regole["avvisi"])

    def test_nessun_doppione_ne_recupero(self):
        """Le stesse tre quote ripetute da un secondo documento e un vincolo
        «altro» sul limite per voce di spesa (comma 7): restano tre quote, le
        prime, e il vincolo non diventa una quota."""
        ripetute = copy.deepcopy(QUOTE_171905)
        for numero, quota in enumerate(ripetute, 4):
            quota.update(id=f"Q{numero}", citazione=cit("D2-p10", quota["citazione"]["testo"]))
        vincolo = {
            "id": "V9", "tipo": "altro", "descrizione": "Voce D entro il 35%", "parametro": "",
            "momento": "domanda",
            "citazione": cit("D1-p10", "Per ciascun partecipante alla compagine progettuale, "
                                       "compreso il Capofila, la voce di costo D"),
        }
        dati = estrazione_base(quote=copy.deepcopy(QUOTE_171905) + ripetute, vincoli=[vincolo])
        regole = elabora(dati, {**SEZIONI, "D1-p10": PAGINA_171905, "D2-p10": PAGINA_171905})
        assert [q["id"] for q in regole["quote"]] == ["Q1", "Q2", "Q3"]
        assert all(q["stato"] == "verificata" for q in regole["quote"])
        assert [a for a in regole["avvisi"] if "quota" in a.lower()] == [AVVISO_RIPETUTA]
        assert regole["vincoli"][0]["id"] == "V9"


AVVISO_RIPETUTA = "Una quota compariva in più documenti: è mostrata una volta sola."
AVVISO_DA_VINCOLO = (
    "Una quota è stata ricavata da un vincolo del bando (limite per singolo partner).")
AVVISO_RICAVATA = "Ricavata da un vincolo: da confermare"


def _voce_quota(id_: str, sezione: str, frase: str, **campi) -> dict:
    voce = {"id": id_, "ambito": "per_partner", "categoria": "", "min_percentuale": "10",
            "max_percentuale": "", "base_calcolo": "", "effetto_violazione": "",
            "citazione": cit(sezione, frase)}
    voce.update(campi)
    return voce


class TestDeduplica:
    """Quote con la stessa tupla (ambito, categoria, min, max) si tengono una
    volta sola, al posto della prima: la prima verificata, altrimenti la
    prima. Un solo avviso globale, senza id interni. Le quote illeggibili,
    senza percentuali o senza una categoria nota non si confrontano: restano
    tutte."""

    FRASE = "Ciascun partner deve sostenere almeno il 10% del costo totale del progetto."
    SEZIONI_DOPPIE = {**SEZIONI, "D2-p1": FRASE, "D3-p5": FRASE, "S2": FRASE}

    def _regole(self, *voci) -> dict:
        regole = elabora(estrazione_base(quote=list(voci)), self.SEZIONI_DOPPIE)
        RegolePartenariatoOut.model_validate(regole)
        return regole

    def test_ripetuta_in_due_documenti(self):
        regole = self._regole(_voce_quota("Q1", "D2-p1", self.FRASE),
                              _voce_quota("Q2", "D3-p5", self.FRASE))
        assert [(q["id"], q["stato"]) for q in regole["quote"]] == [("Q1", "verificata")]
        assert regole["avvisi"] == [AVVISO_RIPETUTA]

    def test_tiene_la_prima_verificata_al_posto_della_prima(self):
        regole = self._regole(
            _voce_quota("Q1", "S2", self.FRASE),  # dalla scheda: da verificare
            _voce_quota("Q2", "D2-p1", self.FRASE, max_percentuale="", ambito="capofila"),
            _voce_quota("Q3", "D3-p5", self.FRASE),
            _voce_quota("Q4", "D2-p1", self.FRASE),
        )
        assert [(q["id"], q["stato"]) for q in regole["quote"]] == [
            ("Q3", "verificata"), ("Q2", "verificata")]
        # un solo avviso anche con due voci tolte
        assert regole["avvisi"] == [AVVISO_RIPETUTA]

    def test_nessuna_verificata_tiene_la_prima(self):
        regole = self._regole(_voce_quota("Q1", "S2", self.FRASE),
                              _voce_quota("Q2", "D2-p1", "non ritrovata nel testo"))
        assert [(q["id"], q["stato"]) for q in regole["quote"]] == [("Q1", "da_verificare")]
        assert regole["avvisi"] == [AVVISO_RIPETUTA]

    @pytest.mark.parametrize(("primo", "secondo"), [
        ({"min_percentuale": "tre quinti"}, {"min_percentuale": "3/5"}),
        ({"min_percentuale": "circa un quarto"}, {"min_percentuale": "significativa"}),
        # stesso minimo, ma un massimo illeggibile
        ({"max_percentuale": "circa 30"}, {}),
        # senza percentuali
        ({"min_percentuale": ""}, {"min_percentuale": ""}),
        # per categoria senza categoria
        ({"ambito": "per_categoria"}, {"ambito": "per_categoria"}),
        # due categorie ignote diverse (entrambe «altro»)
        ({"categoria": "spin-off"}, {"categoria": "cluster tecnologico"}),
    ])
    def test_non_confrontabili_restano_tutte(self, primo, secondo):
        regole = self._regole(_voce_quota("Q1", "D2-p1", self.FRASE, **primo),
                              _voce_quota("Q2", "D3-p5", self.FRASE, **secondo))
        assert [q["id"] for q in regole["quote"]] == ["Q1", "Q2"]
        assert AVVISO_RIPETUTA not in regole["avvisi"]

    def test_tuple_diverse_restano(self):
        frase = ("Ciascun partner e il capofila, anche PMI, sostengono almeno il 10% e al "
                 "massimo il 40% del costo totale del progetto.")
        sezioni = {**SEZIONI, "D2-p1": frase}
        voci = [
            _voce_quota("Q1", "D2-p1", frase),
            _voce_quota("Q2", "D2-p1", frase, min_percentuale="", max_percentuale="10"),
            _voce_quota("Q3", "D2-p1", frase, categoria="pmi"),
            _voce_quota("Q4", "D2-p1", frase, ambito="capofila"),
            _voce_quota("Q5", "D2-p1", frase, max_percentuale="40"),
        ]
        regole = elabora(estrazione_base(quote=voci), sezioni)
        assert [q["id"] for q in regole["quote"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
        assert regole["avvisi"] == []

    def test_senza_codice(self):
        regole = self._regole(_voce_quota("", "D2-p1", self.FRASE),
                              _voce_quota("", "D3-p5", self.FRASE))
        assert len(regole["quote"]) == 1
        assert regole["avvisi"] == [AVVISO_RIPETUTA]

    def test_base_o_effetto_indicati_e_diversi_restano(self):
        """Stessa soglia ma effetto (inammissibilità o perdita della
        maggiorazione) o base di calcolo indicati e diversi: regole diverse,
        restano tutte."""
        regole = self._regole(
            _voce_quota("Q1", "D2-p1", self.FRASE, base_calcolo="spese_ammissibili",
                        effetto_violazione="inammissibilita_progetto"),
            _voce_quota("Q2", "D3-p5", self.FRASE, base_calcolo="spese_ammissibili",
                        effetto_violazione="perdita_maggiorazione"),
            _voce_quota("Q3", "D2-p1", self.FRASE, base_calcolo="costo_totale_progetto",
                        effetto_violazione="inammissibilita_progetto"),
        )
        assert [q["id"] for q in regole["quote"]] == ["Q1", "Q2", "Q3"]
        assert regole["avvisi"] == []

    @pytest.mark.parametrize(("primo", "secondo", "tenuta"), [
        # la prima ha la base, la seconda l'effetto: stessa completezza, la prima
        ({"base_calcolo": "spese_ammissibili"},
         {"effetto_violazione": "inammissibilita_progetto"}, "Q1"),
        # la seconda è più completa: tenuta al posto della prima
        ({}, {"base_calcolo": "spese_ammissibili",
              "effetto_violazione": "inammissibilita_progetto"}, "Q2"),
        # una base non riconosciuta vale «non indicata»
        ({"base_calcolo": "valore aggiunto"}, {"base_calcolo": "spese_ammissibili"}, "Q2"),
    ])
    def test_non_indicato_compatibile_si_unisce(self, primo, secondo, tenuta):
        """«Non indicato» è compatibile con qualunque base o effetto: la stessa
        quota letta in due documenti, uno più completo dell'altro, si unisce."""
        regole = self._regole(_voce_quota("Q1", "D2-p1", self.FRASE, **primo),
                              _voce_quota("Q2", "D3-p5", self.FRASE, **secondo))
        assert [q["id"] for q in regole["quote"]] == [tenuta]
        assert regole["avvisi"] == [AVVISO_RIPETUTA]

    def test_la_verificata_prima_della_piu_completa(self):
        regole = self._regole(
            _voce_quota("Q1", "S2", self.FRASE, base_calcolo="spese_ammissibili",
                        effetto_violazione="inammissibilita_progetto"),  # dalla scheda
            _voce_quota("Q2", "D2-p1", self.FRASE),
        )
        assert [(q["id"], q["stato"]) for q in regole["quote"]] == [("Q2", "verificata")]

    def test_compatibilita_non_transitiva(self):
        """Q1 (base non indicata, verificata) è compatibile con Q2 (budget del
        partner) e con Q3 (contributo), ma Q2 e Q3 sono regole diverse: nessuna
        si perde. Q2 entra nel gruppo di Q1, che resta (verificata); Q3 no."""
        regole = self._regole(
            _voce_quota("Q1", "D2-p1", self.FRASE),
            _voce_quota("Q2", "S2", self.FRASE, base_calcolo="budget_partner"),
            _voce_quota("Q3", "S2", self.FRASE, base_calcolo="contributo"),
        )
        assert [(q["id"], q["base_calcolo"]) for q in regole["quote"]] == [
            ("Q1", "non_indicata"), ("Q3", "contributo")]
        assert regole["avvisi"] == [AVVISO_RIPETUTA]


# 18446, D1-p40 (e identico in D2-p16, l'avviso consolidato): la regola di
# quota che il modello ha messo tra i vincoli «altro» (V6).
PAGINA_18446 = (
    "4. Nel caso di progetti candidati da raggruppamenti di imprese, il progetto candidato "
    "all’agevolazione deve prevedere \nla collaborazione effettiva e d il coinvolgimento tra le "
    "imprese  aderenti al Raggruppamento. Ciò si verifica \nesclusivamente quando nessuna "
    "impresa beneficiaria sostiene da sola più di due terzi  del totale delle spese e dei \n"
    "costi valutati ammissibili. \n5. Qualora il progetto, per la sua particolarità, preveda"
)
CITAZIONE_18446 = (
    "Ciò si verifica esclusivamente quando nessuna impresa beneficiaria sostiene da sola più di "
    "due terzi del totale delle spese e dei costi valutati ammissibili."
)


def _vincolo_altro(id_: str, sezione: str, testo: str, tipo: str = "altro") -> dict:
    return {"id": id_, "tipo": tipo, "descrizione": "Vincolo del bando", "parametro": "",
            "momento": "domanda", "citazione": cit(sezione, testo)}


def _recupero(vincoli: list[dict], sezioni: dict, quote: list[dict] | None = None) -> dict:
    dati = estrazione_base(quote=quote or [], vincoli=vincoli)
    regole = elabora(dati, {**SEZIONI, **sezioni})
    RegolePartenariatoOut.model_validate(regole)
    return regole


def _ricavate(regole: dict) -> list[str]:
    return [a for a in regole["avvisi"] if a == AVVISO_DA_VINCOLO]


class TestQuoteDaiVincoli:
    """Una quota scritta come vincolo «altro» («nessuna impresa sostiene da
    sola più di due terzi delle spese») si recupera come quota per partner,
    SOLO nella forma stretta, con la citazione verificata su un documento
    ufficiale e una frase su spese o costi senza parole di aiuto."""

    def test_18446(self):
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446)],
                           {"D1-p40": PAGINA_18446})
        assert len(regole["quote"]) == 1
        quota = regole["quote"][0]
        assert (quota["id"], quota["ambito"], quota["categoria"], quota["min_percentuale"],
                quota["max_percentuale"]) == ("Q-V6", "per_partner", None, None, 66.67)
        # ricavata dal codice, non dal modello: sempre da confermare (mai
        # preselezionata); l'unico avviso è quello della provenienza
        assert (quota["stato"], quota["avvisi"]) == ("da_verificare", [AVVISO_RICAVATA])
        assert quota["citazione"]["verificata"] is True
        assert quota["citazione"]["sezione"] == "D1-p40"
        assert quota["citazione"]["testo"] == CITAZIONE_18446
        assert regole["avvisi"] == [AVVISO_DA_VINCOLO]
        # il vincolo resta com'è
        vincolo = regole["vincoli"][0]
        assert (vincolo["id"], vincolo["tipo"], vincolo["stato"]) == ("V6", "altro", "verificata")

    def test_ripetuta_in_due_documenti_una_sola_quota(self):
        regole = _recupero(
            [_vincolo_altro("V6", "D1-p40", CITAZIONE_18446),
             _vincolo_altro("V7", "D2-p16", CITAZIONE_18446)],
            {"D1-p40": PAGINA_18446, "D2-p16": PAGINA_18446},
        )
        assert [q["id"] for q in regole["quote"]] == ["Q-V6"]
        assert _ricavate(regole) == [AVVISO_DA_VINCOLO]
        assert len(regole["vincoli"]) == 2

    def test_quota_gia_estratta_entro_la_tolleranza(self):
        """66.66 (scritto dal modello) e 66.67 («due terzi») sono la stessa
        quota: nessun doppione."""
        estratta = _voce_quota("Q1", "D2-p16", CITAZIONE_18446, min_percentuale="",
                               max_percentuale="66.66")
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446)],
                           {"D1-p40": PAGINA_18446, "D2-p16": PAGINA_18446}, [estratta])
        assert [(q["id"], q["max_percentuale"]) for q in regole["quote"]] == [("Q1", 66.66)]
        assert _ricavate(regole) == []

    def test_id_unico_anche_dopo_il_taglio(self):
        """Due vincoli con id lunghi che differiscono solo in fondo, e una quota
        del modello che usa già «Q-V1»: gli id restano unici e di 20
        caratteri al massimo."""
        frasi = {
            "D2-p1": "Nessun partner sostiene da solo più del 70% del costo totale del progetto.",
            "D2-p2": "Nessun partner sostiene da solo più del 60% del costo totale del progetto.",
            "D2-p3": "Nessun partner sostiene da solo più del 50% del costo totale del progetto.",
        }
        vincoli = [
            _vincolo_altro("V-ABCDEFGHIJKLMNOP-1", "D2-p1", frasi["D2-p1"]),
            _vincolo_altro("V-ABCDEFGHIJKLMNOP-2", "D2-p2", frasi["D2-p2"]),
            _vincolo_altro("V1", "D2-p3", frasi["D2-p3"]),
        ]
        estratta = _voce_quota("Q-V1", "D2-p1", frasi["D2-p1"], min_percentuale="10")
        regole = _recupero(vincoli, frasi, [estratta])
        ids = [q["id"] for q in regole["quote"]]
        assert ids == ["Q-V1", "Q-V-ABCDEFGHIJKLMNOP", "Q-V-ABCDEFGHIJKLMN-2", "Q-V1-2"]
        assert len(set(ids)) == len(ids) and all(len(i) <= 20 for i in ids)
        assert [q["max_percentuale"] for q in regole["quote"][1:]] == [70.0, 60.0, 50.0]
        assert _ricavate(regole) == [AVVISO_DA_VINCOLO]

    def test_quota_gia_estratta_nessun_doppione(self):
        estratta = _voce_quota("Q1", "D2-p16", CITAZIONE_18446, min_percentuale="",
                               max_percentuale="2/3")
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446)],
                           {"D1-p40": PAGINA_18446, "D2-p16": PAGINA_18446}, [estratta])
        assert [(q["id"], q["max_percentuale"]) for q in regole["quote"]] == [("Q1", 66.67)]
        assert _ricavate(regole) == []

    def test_quota_gia_estratta_da_verificare_nessun_doppione(self):
        estratta = _voce_quota("Q1", "S2", CITAZIONE_18446, min_percentuale="",
                               max_percentuale="66.67")
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446)],
                           {"D1-p40": PAGINA_18446, "S2": PAGINA_18446}, [estratta])
        assert [(q["id"], q["stato"]) for q in regole["quote"]] == [("Q1", "da_verificare")]
        assert _ricavate(regole) == []

    def test_dalla_scheda_nessun_recupero(self):
        """Il testo si ritrova alla lettera nella scheda del catalogo, ma la
        scheda non è il bando ufficiale: nessuna quota."""
        for sezione in ("S2", "META"):
            regole = _recupero([_vincolo_altro("V6", sezione, CITAZIONE_18446)],
                               {sezione: PAGINA_18446})
            vincolo = regole["vincoli"][0]
            assert vincolo["citazione"]["verificata"] is True  # ritrovata
            assert vincolo["stato"] == "da_verificare"
            assert regole["quote"] == [] and _ricavate(regole) == [], sezione

    def test_citazione_non_ritrovata_nessun_recupero(self):
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446)],
                           {"D1-p40": "Art. 8 - Modalità di presentazione della domanda."})
        assert regole["quote"] == [] and _ricavate(regole) == []

    def test_senza_citazione_nessun_recupero(self):
        regole = _recupero([_vincolo_altro("V6", "", "")], {"D1-p40": PAGINA_18446})
        assert regole["quote"] == []

    def test_vincolo_di_altro_tipo_nessun_recupero(self):
        regole = _recupero([_vincolo_altro("V6", "D1-p40", CITAZIONE_18446, "indipendenza")],
                           {"D1-p40": PAGINA_18446})
        assert regole["quote"] == []

    @pytest.mark.parametrize(("frase", "massimo"), [
        ("Nessun partner può sostenere più del 70% del costo totale del progetto.", 70.0),
        ("Nessun soggetto sostenga da solo oltre il 40 per cento del budget.", 40.0),
        ("Nessuna impresa sostiene da sola più della metà delle spese ammissibili.", 50.0),
        ("Nessun componente del raggruppamento sostiene più di 2/3 dei costi ammissibili.",
         66.67),
        ("Nessun partecipante sostiene da solo oltre i tre quarti delle spese complessive.",
         75.0),
        ("Nessun membro sostiene piu' del 60,5% dei costi ammissibili.", 60.5),
        ("Nessun partner sostiene da solo più del 30 (trenta) per cento dei costi del "
         "progetto.", 30.0),
    ])
    def test_forme_ammesse(self, frase, massimo):
        regole = _recupero([_vincolo_altro("V1", PAGINA, frase)], {PAGINA: frase})
        assert [(q["id"], q["max_percentuale"], q["stato"], q["avvisi"])
                for q in regole["quote"]] == [
            ("Q-V1", massimo, "da_verificare", [AVVISO_RICAVATA])]

    # Negativi: i falsi agganci del campione (17509 D1-p45, 18071 D1-p10,
    # 18177 D1-p39) e frasi nella forma giusta che non sono quote.
    NEGATIVI = (
        ("D1-p45", "almeno i due terzi da persone di età compresa \ntra i diciotto e i "
                   "trentacinque anni. \nG \nAlmeno 2 imprese partecipanti a \nprevalente "
                   "partecipazione femminile",
         "almeno i due terzi da persone di età compresa tra i diciotto e i trentacinque anni."),
        ("D1-p10", "Le norme introdotte dal DL 345/2020, sopra citato hanno modificato il comma "
                   "1 dell’art. 76 dello \nstesso DPR con la previsione che la sanzione "
                   "ordinariamente prevista dal Codice penale per le \ndichiarazioni mendaci è "
                   "aumentata da un terzo alla metà. \n",
         "la sanzione ordinariamente prevista dal Codice penale per le dichiarazioni mendaci è "
         "aumentata da un terzo alla metà."),
        ("D1-p39", "3. Sono considerate associate le imprese, non identificabili come imprese "
                   "collegate ai sensi \ndel successivo comma 4), tra le quali esiste la "
                   "seguente relazione: un'impresa detiene, da \nsola oppure insieme ad una o "
                   "più imprese collegate, il 25% o più del capitale o dei diritti di \nvoto di "
                   "un'altra impresa. La quota del 25% può essere raggiunta o superata",
         "un'impresa detiene, da sola oppure insieme ad una o più imprese collegate, il 25% o "
         "più del capitale o dei diritti di voto di un'altra impresa."),
        # parole di aiuto
        (PAGINA, "Nessuna impresa sostiene da sola più di due terzi del contributo e delle "
                 "spese ammissibili.", None),
        # senza spese, costi o budget
        (PAGINA, "Nessuna impresa sostiene da sola più di due terzi delle attività del "
                 "progetto.", None),
        # esclusione forte
        (PAGINA, "Nessun partner sostiene da solo più del 50% delle quote associative e delle "
                 "spese ammissibili.", None),
        # soglia senza percentuale né frazione
        (PAGINA, "Nessuna impresa sostiene da sola più di 100.000 euro di spese.", None),
        # il soggetto non è un partner («nessuno dei», «nessun costo»)
        (PAGINA, "Nessun costo sostenuto da un partner supera il 30% delle spese.", None),
        # «sostiene» di un altro soggetto, in un'altra proposizione (è un minimo)
        (PAGINA, "Nessuna impresa può partecipare a più di un progetto, e ciascun partner "
                 "sostiene più del 10% del costo totale del progetto.", None),
        (PAGINA, "Nessun soggetto presenta più domande e ognuna delle imprese sostiene oltre "
                 "il 20% delle spese ammissibili.", None),
        (PAGINA, "Nessun partner è escluso e tutti i partecipanti sostengono più del 15% dei "
                 "costi ammissibili, ogni partner sostiene oltre il 15% dei costi ammissibili.",
         None),
        # una singola voce di spesa, o nessuna base complessiva: non è una quota
        (PAGINA, "Nessun partner sostiene da solo più del 20% dei costi per consulenze "
                 "esterne.", None),
        (PAGINA, "Nessuna impresa sostiene da sola più del 30% delle spese di personale.", None),
        (PAGINA, "Nessun partner sostiene più del 20% delle spese ammissibili per consulenze.",
         None),
        (PAGINA, "Nessuna impresa sostiene da sola più di due terzi dei costi.", None),
        # citazione con ellissi: la frase vera (spese di personale) non si
        # ricostruisce, e l'ellissi attacca una base complessiva di un'altra frase
        ("D1-p40", "Nessuna impresa sostiene da sola più del 30% delle spese di personale "
                   "previste. Il costo totale del progetto non può superare un milione di euro.",
         "Nessuna impresa sostiene da sola più del 30% [...] il costo totale del progetto"),
        # un'eccezione o un obbligo negato: non è un tetto per ogni partner
        (PAGINA, "Nessun partner, ad eccezione del capofila, sostiene da solo più del 50% del "
                 "costo totale del progetto.", None),
        (PAGINA, "Nessun partner, salvo il capofila, sostiene più del 50% del costo totale del "
                 "progetto.", None),
        (PAGINA, "Nessun partner, tranne il mandatario, sostiene oltre il 50% delle spese "
                 "ammissibili.", None),
        (PAGINA, "Nessuna impresa, eccetto la capofila, sostiene da sola più del 60% dei costi "
                 "ammissibili.", None),
        (PAGINA, "Nessun partner sostiene da solo più del 50% del costo totale del progetto, "
                 "tranne il capofila.", None),
        (PAGINA, "Nessun partner è tenuto a sostenere più del 50% del costo totale del "
                 "progetto.", None),
        (PAGINA, "Nessuna impresa sarà tenuta a sostenere oltre il 40% delle spese "
                 "ammissibili.", None),
        (PAGINA, "Nessuna impresa è obbligata a sostenere più del 40% delle spese "
                 "ammissibili.", None),
    )

    @pytest.mark.parametrize(("sezione", "pagina", "citazione"), NEGATIVI)
    def test_negativi(self, sezione, pagina, citazione):
        regole = _recupero([_vincolo_altro("V3", sezione, citazione or pagina)],
                           {sezione: pagina})
        assert regole["vincoli"][0]["citazione"]["verificata"] is True
        assert regole["quote"] == [] and _ricavate(regole) == []

    def test_frase_lunga_senza_punti_in_tempo_lineare(self):
        import time

        ostile = ("nessuna impresa beneficiaria " * 400 + "sostiene da sola " * 400
                  + "più di " * 400 + "spese " * 400) * 2
        assert len(ostile) >= 10_000
        inizio = time.perf_counter()
        regole = _recupero([_vincolo_altro("V1", PAGINA, ostile)], {PAGINA: ostile})
        assert time.perf_counter() - inizio < 1.0
        assert regole["quote"] == []

    def test_valore_con_tre_decimali(self):
        """«33,333%» non ripassa da `_numero` come «33.333» (ambiguo, migliaia):
        il valore si arrotonda a due decimali."""
        frase = "Nessun partner sostiene da solo più del 33,333% del costo totale del progetto."
        regole = _recupero([_vincolo_altro("V1", PAGINA, frase)], {PAGINA: frase})
        assert [(q["max_percentuale"], q["stato"], q["avvisi"]) for q in regole["quote"]] == [
            (33.33, "da_verificare", [AVVISO_RICAVATA])]


AVVISO_NON_RICOSTRUIBILE = "Passaggio citato non ricostruibile per intero"


def _quota_citata(pagina: str, citazione: str, **campi) -> dict:
    dati = estrazione_base()
    dati["quote"][0].update(citazione=cit(PAGINA, citazione), **campi)
    return elabora(dati, {**SEZIONI, PAGINA: pagina})["quote"][0]


class TestCitazioniConEllissi:
    """Con un'ellissi interna la frase si ricostruisce solo se i frammenti
    stanno nella STESSA frase del documento; altrimenti esclusioni e
    percentuale non si possono controllare sul passaggio intero: la voce
    resta, da verificare. Un'ellissi solo iniziale o finale si toglie."""

    DUE_FRASI = ("Ciascun partner deve sostenere una parte dei costi di personale. Il capofila "
                 "sostiene il 40% del costo del progetto.")

    @pytest.mark.parametrize("citazione", [
        # il 40% è del capofila, in un'altra frase
        "Ciascun partner deve sostenere una parte [...] il 40% del costo del progetto",
        "Ciascun partner deve sostenere una parte … il 40% del costo del progetto",
    ])
    def test_frammenti_in_frasi_diverse_da_verificare(self, citazione):
        quota = _quota_citata(self.DUE_FRASI, citazione, min_percentuale="40")
        assert quota["citazione"]["verificata"] is True  # i frammenti ci sono
        assert quota["stato"] == "da_verificare"
        assert AVVISO_NON_RICOSTRUIBILE in quota["avvisi"]

    def test_esclusione_fuori_dai_frammenti_vista_sulla_frase(self):
        """«quota di adesione» è nella frase ma fuori dai frammenti citati: la
        frase ricostruita la vede."""
        quota = _quota_citata(
            "La quota di adesione a carico di ciascun partner è pari al 5% del costo del "
            "progetto.", "a carico di ciascun partner [...] al 5% del costo del progetto",
            min_percentuale="5")
        assert quota["stato"] == "da_verificare"
        assert any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])
        assert AVVISO_NON_RICOSTRUIBILE not in quota["avvisi"]

    def test_frammenti_nella_stessa_frase_ricostruita(self):
        """Nella stessa frase la citazione con ellissi vale come la frase intera
        (la percentuale è una prova di presenza, come per la frase citata per
        intero)."""
        quota = _quota_citata(
            "Ciascun partner deve sostenere almeno il 10% dei costi ammissibili del progetto.",
            "Ciascun partner deve sostenere [...] dei costi ammissibili del progetto")
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    @pytest.mark.parametrize("citazione", [
        "[...] deve sostenere una quota dei costi del progetto pari almeno al 10%",
        "... deve sostenere una quota dei costi del progetto pari almeno al 10%",
        "Ciascun partner deve sostenere una quota dei costi del progetto [...]",
        "Ciascun partner deve sostenere una quota dei costi del progetto…",
    ])
    def test_ellissi_iniziale_o_finale_ricostruita(self, citazione):
        quota = _quota_citata(
            "Ciascun partner deve sostenere una quota dei costi del progetto pari almeno al "
            "10%, pena l'esclusione.", citazione)
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    def test_spazi_spuri_senza_ellissi_restano_verificati(self):
        quota = _quota_citata(
            "Ciascun partner deve sost enere almeno il 10% del costo totale del progetto.",
            "Ciascun partner deve sostenere almeno il 10% del costo totale del progetto.")
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])


class TestFraseRicostruitaConLeTolleranze:
    """La frase della quota si ricostruisce con le stesse tolleranze della
    verifica della citazione (spazi, bordi, punteggiatura finale): esclusioni
    e percentuale si controllano sulla frase intera, non sul frammento."""

    ADESIONE = ("La quota di adesione a carico di ciascun partner è pari al 5% del costo del "
                "progetto.")

    @pytest.mark.parametrize(("pagina", "citazione"), [
        # uno spazio in meno o in più nella citazione del modello
        (ADESIONE, "a carico di ciascun partner è pari al 5% del costodel progetto"),
        (ADESIONE, "a car ico di ciascun partner è pari al 5% del costo del progetto"),
        # uno spazio spurio nel PDF, citazione pulita
        (ADESIONE.replace("costo", "cos to"),
         "a carico di ciascun partner è pari al 5% del costo del progetto"),
        # punteggiatura finale o virgolette ai bordi
        (ADESIONE, "a carico di ciascun partner è pari al 5%."),
        (ADESIONE, "a carico di ciascun partner è pari al 5%,"),
        (ADESIONE, "«a carico di ciascun partner è pari al 5% del costo del progetto»"),
    ])
    def test_esclusione_vista_sulla_frase_intera(self, pagina, citazione):
        quota = _quota_citata(pagina, citazione, min_percentuale="5")
        assert quota["citazione"]["verificata"] is True
        assert quota["stato"] == "da_verificare"
        assert any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])
        assert AVVISO_NON_RICOSTRUIBILE not in quota["avvisi"]

    def test_punto_finale_nessun_falso_avviso(self):
        quota = _quota_citata(
            "Ciascun partner deve sostenere una quota dei costi del progetto pari almeno al "
            "10%, pena l'esclusione.",
            "Ciascun partner deve sostenere una quota dei costi del progetto pari almeno al "
            "10%.")
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    @pytest.mark.parametrize("testo", [
        "La quota di ammis- sibilità è del 10 % , cioè (MPMI) , il 651/ 2014 e «A ccordo».",
        "abc-def 2 - 5 e 2 5 x-y 1-a",
        "",
    ])
    def test_forme_compatte_come_la_verifica(self, testo):
        """Le forme con la mappa coincidono con quelle di `citazioni`."""
        from app.services import partenariato_regole as regole_mod
        from app.services.citazioni import _pagliaio

        norm, compatto, nudo = _pagliaio(testo)
        mio_compatto, mappa_c, mio_nudo, mappa_n = regole_mod._forme_compatte(norm)
        assert (mio_compatto, mio_nudo) == (compatto, nudo)
        assert "".join(norm[k] for k in mappa_c) == compatto
        assert "".join(norm[k] for k in mappa_n) == nudo


class TestConfiniDiRiga:
    """Nel testo dei PDF una voce di elenco o la riga dopo un titolo aprono una
    frase nuova anche senza punto: la frase della quota non prende né il
    titolo né la voce successiva."""

    QUOTA = "Ciascun partner deve sostenere almeno il 10% del costo totale del progetto."

    @pytest.mark.parametrize("titolo", [
        "Art. 6 - Contributo concesso e ripartizione tra i partner",
        "Articolo 6 Contributo concesso e ripartizione tra i partner",
        "CONTRIBUTO CONCESSO E RIPARTIZIONE TRA I PARTNER",
        "6.2 Contributo concesso ai partner",
    ])
    def test_titolo_sopra_una_quota_vera(self, titolo):
        quota = _quota_citata(f"{titolo}\n{self.QUOTA}", self.QUOTA)
        assert (quota["stato"], quota["avvisi"]) == ("verificata", [])

    def test_prosa_a_capo_prima_di_un_nome_resta_una_frase(self):
        """«…richiesta dal\\nCapofila…» non è un titolo: l'esclusione della
        riga prima resta nella frase."""
        quota = _quota_citata(
            "La quota di adesione richiesta dal\nCapofila alle imprese non può superare il 50% "
            "del costo del progetto.",
            "Capofila alle imprese non può superare il 50% del costo del progetto",
            min_percentuale="", max_percentuale="50")
        assert quota["stato"] == "da_verificare"
        assert any(a.startswith(AVVISO_NON_QUOTA) for a in quota["avvisi"])

    @pytest.mark.parametrize("marcatore", ["•", "-", "–", "*", "a)", "1.", "1)"])
    def test_voce_di_elenco_non_prende_la_successiva(self, marcatore):
        pagina = (f"{marcatore} ciascun partner deve sostenere una quota dei costi del progetto\n"
                  f"{marcatore} il capofila deve sostenere almeno il 30% del costo totale")
        quota = _quota_citata(
            pagina, "ciascun partner deve sostenere una quota dei costi del progetto",
            min_percentuale="30")
        assert quota["stato"] == "da_verificare"
        assert quota["avvisi"] == [AVVISO_PERCENTUALE]

    def test_ellissi_tra_due_voci_non_ricostruibile(self):
        pagina = ("• ciascun partner deve sostenere una quota dei costi del progetto\n"
                  "• il capofila deve sostenere almeno il 30% del costo totale")
        quota = _quota_citata(
            pagina, "ciascun partner deve sostenere una quota [...] almeno il 30% del costo totale",
            min_percentuale="30")
        assert quota["citazione"]["verificata"] is True
        assert quota["stato"] == "da_verificare"
        assert AVVISO_NON_RICOSTRUIBILE in quota["avvisi"]

    def test_confini_sul_testo_normalizzato(self):
        """I confini calcolati sulle righe cadono all'inizio della riga giusta
        del testo normalizzato (maiuscole, «**», spazi, sillabazione)."""
        from app.services import partenariato_regole as regole_mod

        testo = ("**Art. 6** -  Ripartizione\nIl capofila so-\nstiene il 30%:\n"
                 "a) la prima voce\n\n  b)   la seconda voce\nfine")
        pagliaio = regole_mod._pagina_normalizzata(testo)
        confini = regole_mod._confini_di_riga(testo)
        assert [pagliaio[c:c + 12] for c in confini] == [
            "il capofila ", "a) la prima ", "b) la second"]
