"""Funzioni pure del profilo partner (services/partner_profilo_pubblico.py).

Proprietà difese:
- i tipi di soggetto «dal Registro Imprese» vengono SOLO da derived, flag e
  forma giuridica del registro; quelli dichiarati non li sovrascrivono;
- la proiezione verso terzi è una whitelist: P.IVA, CF, email, telefono, PEC,
  importi esatti di bilancio, `company_profile_id`, `family_parent_id`,
  referente e (se anonimo) ragione sociale non compaiono MAI nel JSON;
- per gli anonimi valgono le riduzioni Q12.
"""

import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.schemas.bando import LookupsOut
from app.schemas.common import LookupItem
from app.schemas.partner_profile import PartnerProfileIn
from app.services import partenariato_vocabolario as voc
from app.services.bilanci_indicatori import EsercizioBilancio, calcola_fasce
from app.services.partenariato_anonimato import SOSTITUTO, identificativi_azienda
from app.services.partner_profilo_pubblico import (
    CATEGORIE_CERTIFICAZIONE,
    ateco_sezione,
    categoria_certificazione,
    completezza,
    profilo_pubblico,
    tipi_soggetto_dedotti,
)

# ----------------------------------------------------------- tipi dedotti


class TestTipiSoggettoDedotti:
    @pytest.mark.parametrize(
        ("classe", "attesi"),
        [
            ("micro", ["impresa", "micro_impresa", "pmi"]),
            ("piccola", ["impresa", "piccola_impresa", "pmi"]),
            ("media", ["impresa", "media_impresa", "pmi"]),
            ("grande", ["impresa", "grande_impresa"]),
            (" Piccola ", ["impresa", "piccola_impresa", "pmi"]),
        ],
    )
    def test_classe_dimensionale(self, classe, attesi):
        assert tipi_soggetto_dedotti({"classe_dimensionale": classe}, None, None) == attesi

    def test_flag_solo_se_true_e_ordine_del_vocabolario(self):
        flags = {
            "impresa_artigiana": True,
            "startup_innovativa": True,
            "pmi_innovativa": "true",  # valore ignoto: non conta
            "certificazione_soa": True,  # non è un tipo di soggetto
        }
        assert tipi_soggetto_dedotti({"classe_dimensionale": "micro"}, flags, None) == [
            "impresa",
            "micro_impresa",
            "pmi",
            "startup_innovativa",
            "impresa_artigiana",
        ]
        ordine = list(voc.TIPI_SOGGETTO)
        dedotti = tipi_soggetto_dedotti({"classe_dimensionale": "media"}, flags, "Cooperativa")
        assert dedotti == sorted(dedotti, key=ordine.index)

    @pytest.mark.parametrize(
        "forma", ["SOCIETA' COOPERATIVA", "Cooperative society", "società cooperativa sociale"]
    )
    def test_cooperativa_dalla_forma_giuridica(self, forma):
        assert tipi_soggetto_dedotti({}, {}, forma) == ["impresa", "cooperativa"]

    @pytest.mark.parametrize(
        ("derived", "flags", "forma"),
        [
            (None, None, None),
            ({}, {}, ""),
            ({"classe_dimensionale": "ND"}, {"startup_innovativa": None}, "Association"),
            # i campi che l'utente modifica non sono registro: ignorati
            ({"beneficiari": [27], "fascia_fatturato": "fino_100k"}, None, "S.R.L."),
        ],
    )
    def test_niente_dal_registro_niente_tipi(self, derived, flags, forma):
        assert tipi_soggetto_dedotti(derived, flags, forma) == []


# ------------------------------------------------------------------- ATECO


class TestAtecoSezione:
    @pytest.mark.parametrize(
        ("codice", "lettera"),
        [
            ("62.01.00", "J"),
            ("620100", "J"),
            ("85592", "P"),
            ("01.11.10", "A"),
            ("1", "A"),
            ("10.11", "C"),
            ("33.20", "C"),
            ("35.11", "D"),
            ("45.20", "G"),
            ("68.10", "L"),
            ("72.19", "M"),
            ("84.11", "O"),
            ("96.02", "S"),
            ("99.00", "U"),
        ],
    )
    def test_ateco_2007(self, codice, lettera):
        assert ateco_sezione(codice)[0] == lettera

    def test_descrizione_ufficiale(self):
        assert ateco_sezione("62.01") == ("J", "Servizi di informazione e comunicazione")
        assert ateco_sezione("25.62") == ("C", "Attività manifatturiere")

    @pytest.mark.parametrize("codice", [None, "", "abc", "00", "04", "34", "40", "44", "48", "89"])
    def test_divisioni_inesistenti(self, codice):
        assert ateco_sezione(codice) is None

    def test_copertura_completa_2007(self):
        valide = {d for d in range(1, 100) if ateco_sezione(f"{d:02d}")}
        assert len(valide) == 88
        lettere = {ateco_sezione(f"{d:02d}")[0] for d in valide}
        assert lettere == set("ABCDEFGHIJKLMNOPQRSTU")

    @pytest.mark.parametrize(
        ("codice", "lettera"),
        [("85.59", "Q"), ("62.01", "K"), ("58.11", "J"), ("64.19", "L"), ("99.00", "V")],
    )
    def test_ateco_2025(self, codice, lettera):
        assert ateco_sezione(codice, versione="2025")[0] == lettera

    def test_divisione_45_solo_nel_2007(self):
        assert ateco_sezione("45.20", versione="2025") is None


# --------------------------------------------------------- certificazioni


@pytest.mark.parametrize(
    ("testo", "categoria"),
    [
        ("ISO 9001:2015", "qualita"),
        ("UNI EN ISO9001", "qualita"),
        ("Certificazione di qualità", "qualita"),
        ("UNI EN ISO 14001:2015", "ambiente"),
        ("Registrazione EMAS", "ambiente"),
        ("Sistema di gestione ambientale", "ambiente"),
        ("ISO 45001", "sicurezza_lavoro"),
        ("OHSAS 18001:2007", "sicurezza_lavoro"),
        ("ISO/IEC 27001:2022", "sicurezza_informazioni"),
        ("TISAX", "sicurezza_informazioni"),
        ("ISO 50001", "energia"),
        ("ESCo UNI CEI 11352", "energia"),
        ("Attestazione SOA OG1 classifica III", "appalti_soa"),
        ("SOA OS30", "appalti_soa"),
        ("ISO 13485", "settoriale"),
        ("Qualità ISO 13485 dispositivi medici", "settoriale"),
        ("IATF 16949", "settoriale"),
        ("HACCP", "settoriale"),
        ("ISO 9001 e ISO 14001", "qualita"),
        ("SA8000", "altro"),
        ("UNI/PdR 125:2022 parità di genere", "altro"),
        ("ISO 19001", "altro"),
        ("", "altro"),
        (None, "altro"),
    ],
)
def test_categoria_certificazione(testo, categoria):
    assert categoria_certificazione(testo) == categoria


def test_categorie_hanno_etichetta():
    assert set(CATEGORIE_CERTIFICAZIONE) == {
        "qualita", "ambiente", "sicurezza_lavoro", "sicurezza_informazioni", "energia",
        "appalti_soa", "settoriale", "altro",
    }


# ------------------------------------------------------------- completezza

PIENO = {
    "descrizione_competenze": "x" * 80,
    "competenze": ["sviluppo_software", "cybersecurity", "cloud_infrastrutture_it"],
    "tipi_soggetto": ["universita"],
    "regioni_interesse": [3],
    "esperienze": [{"programma": "Horizon Europe"}],
    "certificazioni": ["ISO 9001"],
    "ruoli_disponibili": ["partner"],
    "forme_accettate": ["ats"],
}


class TestCompletezza:
    def test_vuoto_e_pieno(self):
        assert completezza({}, []) == 0
        assert completezza(PIENO, []) == 100
        assert completezza(PartnerProfileIn(**PIENO), []) == 100

    @pytest.mark.parametrize(
        ("modifica", "punti"),
        [
            ({"descrizione_competenze": "x" * 79}, 80),
            ({"descrizione_competenze": "  " + "x" * 79 + "  "}, 80),
            ({"competenze": ["sviluppo_software"]}, 87),
            ({"competenze": []}, 75),
            ({"tipi_soggetto": []}, 90),
            ({"regioni_interesse": [], "paesi_interesse": ["DE"]}, 100),
            ({"regioni_interesse": []}, 90),
            ({"esperienze": []}, 85),
            ({"certificazioni": [], "infrastrutture": "Laboratorio prove"}, 100),
            ({"certificazioni": [], "infrastrutture": "  "}, 90),
            ({"forme_accettate": []}, 90),
            ({"ruoli_disponibili": []}, 90),
        ],
    )
    def test_voci(self, modifica, punti):
        assert completezza({**PIENO, **modifica}, []) == punti

    def test_tipi_dedotti_contano(self):
        assert completezza({**PIENO, "tipi_soggetto": []}, ["impresa", "pmi"]) == 100


# ------------------------------------------------------- profilo pubblico

COMPANY_PROFILE_ID = str(uuid4())
FAMILY_PARENT_ID = str(uuid4())
REFERENTE_ID = str(uuid4())
UPDATED_BY = str(uuid4())
CODICE_PUBBLICO = str(uuid4())
PIVA = "01234567897"
CF_PERSONA = "RSSMRA80A01H501U"
EMAIL = "segreteria@rossimeccanica.it"
PEC = "rossimeccanica@pec.it"
TELEFONO = "030 7654321"

LOOKUPS = LookupsOut(
    regioni=[LookupItem(id=3, nome="Lombardia"), LookupItem(id=5, nome="Veneto")],
    settori=[],
    beneficiari=[],
    codici_ateco=[],
    tipologie_bando=[],
    modalita_erogazione=[],
    programmi=[LookupItem(id=7, nome="Horizon Europe")],
)

COMPANY = {
    "id": COMPANY_PROFILE_ID,
    "parent_id": FAMILY_PARENT_ID,
    "ragione_sociale": "Rossi Meccanica S.r.l.",
    "partita_iva": PIVA,
    "codice_fiscale": PIVA,
    "sito_web": "www.rossimeccanica.it",
    "pec": PEC,
    "telefono": TELEFONO,
    "classe_dimensionale": "grande",  # campo modificabile: NON deve contare
}
COMPANY_DATA = {
    "company_profile_id": COMPANY_PROFILE_ID,
    "piva_fetched": PIVA,
    "sandbox": False,
    "denominazione": "ROSSI MECCANICA SRL",
    "stato_impresa": "Attiva",
    "derived": {
        "ateco_principale": "25.62.00",
        "ateco_divisione": "25",
        "regione_nome": "LOMBARDIA",
        "regione_id": 3,
        "classe_dimensionale": "piccola",
        "fascia_fatturato": "500k_2m",
    },
    "raw": {
        "companyDetails": {"vatCode": PIVA, "taxCode": PIVA},
        "mail": {"email": EMAIL},
        "pec": PEC,
        "contacts": {"telephoneNumber": TELEFONO},
    },
}
DOSSIER = {
    "anagrafica": {
        "denominazione": "ROSSI MECCANICA SRL",
        "partita_iva": PIVA,
        "codice_fiscale": PIVA,
        "forma_giuridica": "SOCIETA' DI CAPITALE",
        "forma_giuridica_dettaglio": "SOCIETA' A RESPONSABILITA' LIMITATA",
    },
    "attivita": {"ateco": {"codice": "25.62.00", "descrizione": "Lavori di meccanica"}},
    "contatti": {"pec": PEC, "email": EMAIL, "telefono": TELEFONO, "sito_web": "rossimeccanica.it"},
    "bilanci": {"fatturato": 1234567.89, "patrimonio_netto": 345678.12},
    "flags": {"startup_innovativa": False, "pmi_innovativa": True, "impresa_artigiana": None},
}
PEOPLE = [
    {
        "nome": "Mario",
        "cognome": "Bianchi",
        "codice_fiscale": CF_PERSONA,
        "is_legale_rappresentante": True,
    },
]
FASCE = calcola_fasce(
    [
        EsercizioBilancio(
            anno=2022,
            valori={"fatturato": Decimal("1100000.55"), "patrimonio_netto": Decimal("300000")},
        ),
        EsercizioBilancio(
            anno=2023,
            valori={
                "fatturato": Decimal("1234567.89"),
                "patrimonio_netto": Decimal("345678.12"),
                "dipendenti": Decimal("23"),
            },
        ),
    ]
)


def profilo(**modifiche) -> dict:
    riga = {
        "company_profile_id": COMPANY_PROFILE_ID,
        "family_parent_id": FAMILY_PARENT_ID,
        "codice_pubblico": CODICE_PUBBLICO,
        "visibile_come_partner": True,
        "anonimo": False,
        "consenso_versione": "2026-10-bozza-1",
        "consenso_at": "2026-10-01T10:00:00+00:00",
        "accetta_inviti": True,
        "descrizione_competenze": (
            "Lavorazioni meccaniche di precisione per l'automotive, prototipi e piccole serie "
            f"(scrivi a {EMAIL} o chiama il {TELEFONO})."
        ),
        "competenze": ["meccanica_meccatronica", "prototipazione_testing", "codice_ignoto"],
        "competenze_libere": ["Stampa 3D", "www.rossimeccanica.it"],
        "tipi_soggetto": ["organismo_ricerca", "grande_impresa", "pmi_innovativa", "ignoto"],
        "ruoli_disponibili": ["partner", "capofila"],
        "settori_interesse": [4],
        "regioni_interesse": [5, 3, 99],
        "paesi_interesse": ["DE", "FR", "zz"],
        "forme_accettate": ["ats", "consorzio_ue", "altra_ignota"],
        "esperienze": [
            {
                "programma": "Horizon",
                "programma_id": 7,
                "anno": 2023,
                "ruolo": "capofila",
                "titolo": "Progetto Alfa",
                "esito": "finanziato",
            },
            {"programma": "POR FESR Lombardia", "anno": 2021, "ruolo": "partner"},
            {"programma": "Horizon Europe", "anno": 2019, "ruolo": "partner"},
        ],
        "certificazioni": ["ISO 9001:2015", "ISO 14001", "IATF 16949"],
        "infrastrutture": "Laboratorio prove con banco a tre assi",
        "categorie_bando_escluse": [2],
        "referente_user_id": REFERENTE_ID,
        "referente_proposto_user_id": None,
        "sospeso_at": None,
        "bozza_ai": {"descrizione_competenze": "bozza", "competenze": []},
        "bozza_ai_errore": None,
        "completezza": 12,  # valore salvato: il pubblico lo ricalcola
        "updated_by": UPDATED_BY,
    }
    riga.update(modifiche)
    return riga


def pubblico(**modifiche):
    ident = identificativi_azienda(COMPANY, COMPANY_DATA, PEOPLE)
    return profilo_pubblico(
        profilo(**modifiche), COMPANY_DATA, DOSSIER, FASCE, LOOKUPS, ident=ident
    )


class TestProfiloPubblicoNominativo:
    def test_dati_dal_registro(self):
        out = pubblico()
        assert str(out.codice_pubblico) == CODICE_PUBBLICO
        assert out.anonimo is False
        assert out.denominazione == "ROSSI MECCANICA SRL"
        assert out.regione_sede == "Lombardia"
        assert out.ateco_sezione.model_dump() == {
            "lettera": "C",
            "descrizione": "Attività manifatturiere",
        }
        # dal registro «piccola», non la «grande» di company_profiles
        assert out.classe_dimensionale == "piccola"

    def test_fasce_da_calcola_fasce(self):
        out = pubblico()
        assert out.fasce.model_dump() == {
            "fatturato": FASCE.fatturato,
            "patrimonio_netto": FASCE.patrimonio_netto,
            "dipendenti": FASCE.dipendenti,
            "trend": FASCE.trend_fatturato,
        }
        assert out.fasce.fatturato == "500k_2m"
        assert out.fasce.trend == "crescita"

    def test_tipi_soggetto_registro_prima_poi_dichiarati(self):
        tipi = [(t.codice, t.fonte) for t in pubblico().tipi_soggetto]
        assert tipi == [
            ("impresa", "registro"),
            ("piccola_impresa", "registro"),
            ("pmi", "registro"),
            ("pmi_innovativa", "registro"),
            ("organismo_ricerca", "dichiarato"),
        ]
        # «grande_impresa» dichiarata (tipo del registro) non esce mai
        assert "grande_impresa" not in [c for c, _ in tipi]

    def test_etichette_dal_vocabolario_e_codici_ignoti_scartati(self):
        out = pubblico()
        assert [(c.codice, c.etichetta, c.area) for c in out.competenze] == [
            ("meccanica_meccatronica", "Meccanica e meccatronica", "Produzione"),
            ("prototipazione_testing", "Prototipazione e test", "Ricerca e innovazione"),
        ]
        assert [(f.codice, f.etichetta) for f in out.forme_accettate] == [
            ("ats", voc.FORME["ats"].etichetta),
            ("consorzio_ue", voc.FORME["consorzio_ue"].etichetta),
        ]
        assert out.regioni_interesse == ["Veneto", "Lombardia"]
        assert out.paesi_interesse == ["DE", "FR"]
        assert out.ruoli_disponibili == ["capofila", "partner"]

    def test_esperienze_complete_con_nome_del_catalogo(self):
        esperienze = [e.model_dump() for e in pubblico().esperienze]
        assert esperienze == [
            {
                "programma": "Horizon Europe",
                "anno": 2023,
                "ruolo": "capofila",
                "titolo": "Progetto Alfa",
            },
            {"programma": "POR FESR Lombardia", "anno": 2021, "ruolo": "partner", "titolo": None},
            {"programma": "Horizon Europe", "anno": 2019, "ruolo": "partner", "titolo": None},
        ]

    def test_certificazioni_e_infrastrutture_testuali(self):
        out = pubblico()
        assert out.certificazioni == ["ISO 9001:2015", "ISO 14001", "IATF 16949"]
        assert out.infrastrutture == "Laboratorio prove con banco a tre assi"

    def test_contatti_tolti_dai_testi_anche_se_nominativo(self):
        out = pubblico()
        assert EMAIL not in out.descrizione_competenze
        assert TELEFONO not in out.descrizione_competenze
        assert out.descrizione_competenze.count(SOSTITUTO) == 2
        # voce libera fatta solo di un contatto: sparisce
        assert out.competenze_libere == ["Stampa 3D"]

    def test_completezza_ricalcolata(self):
        assert pubblico().completezza == 100

    def test_senza_dati_opzionali(self):
        out = profilo_pubblico(
            {"codice_pubblico": CODICE_PUBBLICO, "anonimo": False}, None, None, None, None
        )
        assert out.denominazione is None and out.regione_sede is None
        assert out.ateco_sezione is None and out.classe_dimensionale is None
        assert out.fasce.model_dump() == {
            "fatturato": None, "patrimonio_netto": None, "dipendenti": None, "trend": None
        }
        assert out.tipi_soggetto == [] and out.completezza == 0 and out.accetta_inviti is True

    def test_regione_senza_lookup_dal_registro(self):
        out = profilo_pubblico(profilo(), COMPANY_DATA, DOSSIER, FASCE, None)
        assert out.regione_sede == "Lombardia"
        assert out.regioni_interesse == []


class TestProfiloPubblicoAnonimo:
    def test_riduzioni_q12(self):
        out = pubblico(anonimo=True)
        assert out.anonimo is True
        assert out.denominazione is None
        assert out.infrastrutture is None
        assert out.fasce.model_dump() == {
            "fatturato": FASCE.fatturato,
            "patrimonio_netto": None,
            "dipendenti": None,
            "trend": None,
        }
        # esperienze con il solo programma, senza doppioni
        assert [e.model_dump() for e in out.esperienze] == [
            {"programma": "Horizon Europe", "anno": None, "ruolo": None, "titolo": None},
            {"programma": "POR FESR Lombardia", "anno": None, "ruolo": None, "titolo": None},
        ]
        # certificazioni solo per categoria, nell'ordine delle categorie
        assert out.certificazioni == [
            CATEGORIE_CERTIFICAZIONE["qualita"],
            CATEGORIE_CERTIFICAZIONE["ambiente"],
            CATEGORIE_CERTIFICAZIONE["settoriale"],
        ]
        # restano classe dimensionale, sezione ATECO e regione del registro
        assert out.classe_dimensionale == "piccola"
        assert out.ateco_sezione.lettera == "C"

    def test_anonimo_di_default(self):
        riga = profilo()
        del riga["anonimo"]
        out = profilo_pubblico(riga, COMPANY_DATA, DOSSIER, FASCE, LOOKUPS)
        assert out.anonimo is True and out.denominazione is None

    def test_identificativi_tolti_dai_testi(self):
        out = pubblico(
            anonimo=True,
            descrizione_competenze="La Rossi Meccanica lavora dal 1960 con Bianchi.",
            competenze_libere=["Officina ROSSI MECCANICA", "Stampa 3D"],
        )
        assert out.descrizione_competenze == f"La {SOSTITUTO} lavora dal 1960 con Bianchi."
        assert out.competenze_libere == [f"Officina {SOSTITUTO}", "Stampa 3D"]


# ---------------------------------------------------------------- canary

CANARINI = [
    PIVA,
    "012 345 678 97",
    CF_PERSONA,
    EMAIL,
    PEC,
    TELEFONO,
    "7654321",
    "1234567",  # fatturato esatto
    "1234567.89",
    "345678",  # patrimonio netto esatto
    COMPANY_PROFILE_ID,
    FAMILY_PARENT_ID,
    REFERENTE_ID,
    UPDATED_BY,
    "company_profile_id",
    "family_parent_id",
    "referente",
    "partita_iva",
    "codice_fiscale",
    "bozza",
    "sospeso",
    "consenso",
]


@pytest.mark.parametrize("anonimo", [False, True])
def test_canary_mai_nel_json(anonimo):
    testo_ostile = (
        f"Contatti: {EMAIL}, PEC {PEC}, tel. {TELEFONO}, P.IVA {PIVA} "
        f"(anche 012 345 678 97), CF {CF_PERSONA}."
    )
    out = pubblico(
        anonimo=anonimo,
        descrizione_competenze=testo_ostile,
        infrastrutture=testo_ostile,
        competenze_libere=[testo_ostile],
        certificazioni=[f"ISO 9001 - {EMAIL}"],
        esperienze=[{"programma": f"Horizon {PEC}", "titolo": testo_ostile, "anno": 2022}],
    )
    serializzato = json.dumps(out.model_dump(mode="json"), ensure_ascii=False)
    for canarino in CANARINI:
        assert canarino.lower() not in serializzato.lower(), canarino
    ragione_sociale_visibile = "rossi meccanica" in serializzato.lower()
    assert ragione_sociale_visibile is (not anonimo)


def test_canary_anonimo_senza_ragione_sociale_nei_testi():
    out = pubblico(
        anonimo=True,
        descrizione_competenze="Siamo la Rossi Meccanica, sito rossimeccanica.it",
        esperienze=[{"programma": "Bando Rossi Meccanica 2020"}],
    )
    serializzato = json.dumps(out.model_dump(mode="json"), ensure_ascii=False).lower()
    assert "rossi meccanica" not in serializzato
    assert "rossimeccanica" not in serializzato
