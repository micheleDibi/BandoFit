"""Post-elaborazione deterministica delle regole di partenariato (WP3):
verifica delle citazioni, controlli di coerenza e di range (la voce va
`da_verificare`, mai un'eccezione), `modalita_effettiva`, mappatura delle
regioni e dei tipi di soggetto, scrub dei domini esclusi."""

import copy

from app.schemas.partenariato import PartenariatoEstrazione, RegolePartenariatoOut
from app.services.partenariato_regole import mappa_regioni, post_elabora

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
        citazione = elabora(dati)["modalita"]["citazione"]
        assert citazione["fonte_etichetta"] == "Scheda del bando"
        assert citazione["url_documento"] is None and citazione["pagina"] is None

    def test_citazione_non_ritrovata_da_verificare(self):
        dati = estrazione_base()
        dati["vincoli"][0]["citazione"] = cit("D1-p4", "sede operativa in Sicilia")
        regole = elabora(dati)
        assert regole["vincoli"][0]["stato"] == "da_verificare"
        assert regole["vincoli"][0]["citazione"]["verificata"] is False

    def test_sezione_sbagliata_non_verifica(self):
        dati = estrazione_base()
        # il testo è in D1-p4, la citazione indica la scheda
        dati["documenti_richiesti"][0]["citazione"] = cit("S1", "costituito prima")
        assert elabora(dati)["documenti_richiesti"][0]["stato"] == "da_verificare"

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
            modalita_citazione=cit("S1", "in forma singola"),
        )
        regole = elabora(dati)
        assert regole["modalita"]["effettiva"] == "non_determinabile"
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert regole["modalita"]["stato"] == "da_verificare"
        assert any("contraddice" in a for a in regole["avvisi"])

    def test_obbligatorio_verificato(self):
        sezioni = {**SEZIONI, "S2": "La domanda è presentata esclusivamente in forma associata."}
        dati = estrazione_base(
            modalita="obbligatorio",
            modalita_citazione=cit("S2", "esclusivamente in forma associata"),
        )
        assert elabora(dati, sezioni)["modalita"]["effettiva"] == "obbligatorio"

    def test_dichiarata_non_determinabile(self):
        dati = estrazione_base(modalita="non_determinabile", modalita_citazione=NESSUNA)
        assert elabora(dati)["modalita"]["effettiva"] == "non_determinabile"

    def test_citazione_frammento_non_fonda_la_modalita(self):
        """Ritrovata alla lettera ma di una parola sola: in S2 la frase dice
        il contrario. Il filtro non deve vedere «obbligatorio»."""
        sezioni = {**SEZIONI, "S2": "Non è ammessa la partecipazione in partenariato."}
        for frammento in ("partenariato", "in partenariato", "Il"):
            dati = estrazione_base(modalita="obbligatorio",
                                   modalita_citazione=cit("S2", frammento))
            regole = elabora(dati, sezioni)
            assert regole["modalita_effettiva"] == "non_determinabile", frammento
            assert regole["modalita"]["stato"] == "da_verificare"
            assert any("troppo breve" in a for a in regole["modalita"]["avvisi"])

    def test_citazione_breve_ma_probante_accettata(self):
        sezioni = {**SEZIONI, "S2": "Il partenariato è composto da almeno due imprese."}
        dati = estrazione_base(modalita="obbligatorio",
                               modalita_citazione=cit("S2", "almeno due imprese"))
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
                               modalita_citazione=cit("S1", "in forma singola"))
        regole = elabora(dati)  # partner_min "3"
        assert any("numero minimo" in a for a in regole["avvisi"])

    def test_modalita_con_conteggio_illeggibile_non_determinabile(self):
        """Il conteggio che potrebbe contraddire la modalità non si legge:
        la modalità non si conferma (è ciò che usa il filtro)."""
        dati = estrazione_base(modalita="non_ammesso", forme_ammesse=[],
                               modalita_citazione=cit("S1", "in forma singola"),
                               partner_min="almeno 2")
        regole = elabora(dati)
        assert regole["modalita_effettiva"] == "non_determinabile"
        assert regole["modalita"]["stato"] == "da_verificare"
        assert any(a.startswith("«Non ammesso»") and "minimo" in a for a in regole["avvisi"])

        sezioni = {**SEZIONI, "S2": "La domanda è presentata esclusivamente in forma associata."}
        dati = estrazione_base(modalita="obbligatorio",
                               modalita_citazione=cit("S2", "esclusivamente in forma associata"),
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
