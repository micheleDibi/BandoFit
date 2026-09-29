"""Criteri tipizzati e `valuta_criterio` (WP5, docs/partenariati.md C1).

Proprietà difese:
- tabella tipo × esito per OGNI tipo di `CriterioPartner` (i tipi della
  tabella coincidono con l'unione);
- regola «tutte le sedi» (basta un'unità locale) e `sede_entro_erogazione`
  → `incerto` senza sede, mai `non_coperto`;
- il registro prevale sul dichiarato: dimensione e tipi del registro non si
  ricavano mai dai campi modificabili né dai tipi dichiarati;
- regole finanziarie: vista «proprio» sui valori esatti, «terzi» sugli
  intervalli di fascia; verso terzi nessun importo e nessun testo privato;
- `manuale` (e criterio assente) → `non_valutabile`;
- solo codici del vocabolario v1.
"""

from decimal import Decimal
from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas import partenariato_criteri as sc
from app.schemas.partenariato_criteri import (
    CRITERIO_ADAPTER,
    CriterioManuale,
    CriterioRegione,
    ProfiloCandidato,
    TipoCriterio,
)
from app.schemas.partenariato_vocabolario import Competenza, TipoSoggetto
from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services import partenariato_criteri as pc
from app.services import partenariato_vocabolario as voc
from app.services.bilanci_indicatori import EsercizioBilancio
from app.services.partner_profilo_pubblico import CATEGORIE_CERTIFICAZIONE

# ----------------------------------------------------------------- aiuti

LOMBARDIA, CALABRIA, LAZIO = 9, 4, 7
HORIZON, FESR = 11, 12


def D(valore) -> Decimal:
    return Decimal(str(valore))


def es(anno: int, **valori) -> EsercizioBilancio:
    return EsercizioBilancio(anno=anno, valori={k: D(v) for k, v in valori.items()})


def crit(**dati):
    return CRITERIO_ADAPTER.validate_python(dati)


def regola(numeratore, operatore, *, denominatore=None, soglia=None, soglia_variabile=None,
           coefficiente=None, ambito="ciascun_partner") -> dict:
    if denominatore is not None:
        unita = "rapporto"
    elif numeratore in ("dipendenti", "bilanci_approvati_n"):
        unita = "numero"
    else:
        unita = "euro"
    return {
        "id": "F1", "descrizione": "regola di prova", "ambito": ambito,
        "numeratore": numeratore, "denominatore": denominatore, "operatore": operatore,
        "soglia": soglia, "soglia_variabile": soglia_variabile,
        "soglia_coefficiente": coefficiente, "unita": unita,
    }


# Registro di una piccola impresa con sede legale in Lombardia e unità locale
# in Calabria, ATECO 62.01 (secondario 72), non startup, senza SOA.
DERIVED = {
    "ateco_principale": "62.01.00",
    "ateco_divisione": "62",
    "ateco_secondari": ["72.19.09"],
    "regione_id": LOMBARDIA,
    "regioni_ids": [LOMBARDIA, CALABRIA],
    "classe_dimensionale": "piccola",
}
DOSSIER = {
    "anagrafica": {"forma_giuridica": "Società a responsabilità limitata"},
    "flags": {
        "startup_innovativa": False,
        "pmi_innovativa": None,
        "impresa_artigiana": False,
        "certificazione_soa": False,
    },
}
PROFILO = {
    "tipi_soggetto": ["organismo_ricerca"],
    "competenze": ["sviluppo_software", "prototipazione_testing"],
    "certificazioni": ["ISO 9001:2015"],
    "esperienze": [
        {"programma": "Horizon Europe", "programma_id": HORIZON, "ruolo": "partner"},
    ],
}
ESERCIZI = [es(2023, fatturato=1_500_000), es(2024, fatturato=1_800_000)]


def profilo(
    *, company=None, derived=DERIVED, dossier=DOSSIER, profilo_partner=PROFILO,
    esercizi=ESERCIZI, storico_completo=True,
) -> ProfiloCandidato:
    return pc.profilo_candidato_da(
        company=company if company is not None else {"settore_id": 47},
        derived=derived,
        dossier=dossier,
        profilo_partner=profilo_partner,
        esercizi=esercizi,
        storico_completo=storico_completo,
    )


PIENO = profilo()
SENZA_REGISTRO = profilo(derived=None, dossier=None)
SENZA_PROFILO = profilo(profilo_partner=None)
VUOTO = pc.profilo_candidato_da()


# ------------------------------------------------------ tabella tipo × esito

# (tipo, descrizione, criterio, profilo, vista, costo_quota, esito, fonte)
CASI = [
    # tipo_soggetto
    ("tipo_soggetto", "dimensionale dal registro",
     {"tipo": "tipo_soggetto", "valori": ["piccola_impresa"]}, PIENO, "coperto", "registro"),
    ("tipo_soggetto", "pmi dedotta dal registro",
     {"tipo": "tipo_soggetto", "valori": ["pmi"]}, PIENO, "coperto", "registro"),
    ("tipo_soggetto", "dichiarato nel profilo",
     {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]}, PIENO, "coperto", "dichiarato"),
    ("tipo_soggetto", "registro contrario",
     {"tipo": "tipo_soggetto", "valori": ["grande_impresa"]}, PIENO, "non_coperto", "registro"),
    ("tipo_soggetto", "flag del registro falso",
     {"tipo": "tipo_soggetto", "valori": ["startup_innovativa"]}, PIENO, "non_coperto",
     "registro"),
    ("tipo_soggetto", "flag del registro ignoto",
     {"tipo": "tipo_soggetto", "valori": ["pmi_innovativa"]}, PIENO, "dato_mancante",
     "registro"),
    ("tipo_soggetto", "non dichiarato con profilo",
     {"tipo": "tipo_soggetto", "valori": ["universita"]}, PIENO, "non_coperto", "dichiarato"),
    ("tipo_soggetto", "senza registro",
     {"tipo": "tipo_soggetto", "valori": ["micro_impresa"]}, SENZA_REGISTRO, "dato_mancante",
     "registro"),
    ("tipo_soggetto", "senza profilo",
     {"tipo": "tipo_soggetto", "valori": ["universita"]}, SENZA_PROFILO, "dato_mancante",
     "dichiarato"),
    ("tipo_soggetto", "basta uno dei tipi",
     {"tipo": "tipo_soggetto", "valori": ["universita", "media_impresa", "piccola_impresa"]},
     PIENO, "coperto", "registro"),
    # tag
    ("tag", "almeno uno",
     {"tipo": "tag", "tags": ["cybersecurity", "sviluppo_software"]}, PIENO, "coperto",
     "dichiarato"),
    ("tag", "tutti presenti",
     {"tipo": "tag", "tags": ["sviluppo_software", "prototipazione_testing"],
      "modalita": "tutti"}, PIENO, "coperto", "dichiarato"),
    ("tag", "tutti ma ne manca uno",
     {"tipo": "tag", "tags": ["sviluppo_software", "cybersecurity"], "modalita": "tutti"},
     PIENO, "non_coperto", "dichiarato"),
    ("tag", "nessuno",
     {"tipo": "tag", "tags": ["agroalimentare"]}, PIENO, "non_coperto", "dichiarato"),
    ("tag", "profilo senza competenze",
     {"tipo": "tag", "tags": ["agroalimentare"]}, SENZA_PROFILO, "dato_mancante", "dichiarato"),
    # regione
    ("regione", "sede legale",
     {"tipo": "regione", "regioni_ids": [LOMBARDIA]}, PIENO, "coperto", "registro"),
    ("regione", "unità locale (tutte le sedi)",
     {"tipo": "regione", "regioni_ids": [CALABRIA]}, PIENO, "coperto", "registro"),
    ("regione", "nessuna sede, sede attuale",
     {"tipo": "regione", "regioni_ids": [LAZIO]}, PIENO, "non_coperto", "registro"),
    ("regione", "nessuna sede, entro l'erogazione",
     {"tipo": "regione", "regioni_ids": [LAZIO], "modalita": "sede_entro_erogazione"}, PIENO,
     "incerto", "registro"),
    ("regione", "senza registro",
     {"tipo": "regione", "regioni_ids": [LAZIO]}, SENZA_REGISTRO, "dato_mancante", "registro"),
    # paese
    ("paese", "Italia",
     {"tipo": "paese", "paesi": ["IT", "FR"]}, PIENO, "coperto", "registro"),
    ("paese", "altro paese",
     {"tipo": "paese", "paesi": ["DE"]}, PIENO, "non_coperto", "registro"),
    ("paese", "escluso",
     {"tipo": "paese", "paesi": ["IT"], "escludi": True}, PIENO, "non_coperto", "registro"),
    ("paese", "non escluso",
     {"tipo": "paese", "paesi": ["DE"], "escludi": True}, PIENO, "coperto", "registro"),
    ("paese", "senza registro",
     {"tipo": "paese", "paesi": ["IT"]}, SENZA_REGISTRO, "dato_mancante", "registro"),
    # ateco
    ("ateco", "principale",
     {"tipo": "ateco", "divisioni": ["62"]}, PIENO, "coperto", "registro"),
    ("ateco", "secondario",
     {"tipo": "ateco", "divisioni": ["72"]}, PIENO, "coperto", "registro"),
    ("ateco", "nessuna divisione",
     {"tipo": "ateco", "divisioni": ["10"]}, PIENO, "non_coperto", "registro"),
    ("ateco", "senza registro",
     {"tipo": "ateco", "divisioni": ["62"]}, SENZA_REGISTRO, "dato_mancante", "registro"),
    # settore
    ("settore", "settore dichiarato",
     {"tipo": "settore", "settori_ids": [47, 59]}, PIENO, "coperto", "dichiarato"),
    ("settore", "altro settore",
     {"tipo": "settore", "settori_ids": [59]}, PIENO, "non_coperto", "dichiarato"),
    ("settore", "senza settore",
     {"tipo": "settore", "settori_ids": [59]}, VUOTO, "dato_mancante", "dichiarato"),
    # dimensione
    ("dimensione", "dal registro",
     {"tipo": "dimensione", "valori": ["micro", "piccola"]}, PIENO, "coperto", "registro"),
    ("dimensione", "fuori classe",
     {"tipo": "dimensione", "valori": ["grande"]}, PIENO, "non_coperto", "registro"),
    ("dimensione", "senza registro",
     {"tipo": "dimensione", "valori": ["piccola"]}, SENZA_REGISTRO, "dato_mancante",
     "registro"),
    # certificazione
    ("certificazione", "categoria dichiarata",
     {"tipo": "certificazione", "categorie": ["qualita"]}, PIENO, "coperto", "dichiarato"),
    ("certificazione", "SOA dal registro",
     {"tipo": "certificazione", "categorie": ["appalti_soa"]},
     profilo(dossier={**DOSSIER, "flags": {"certificazione_soa": True}}), "coperto",
     "registro"),
    ("certificazione", "categoria assente",
     {"tipo": "certificazione", "categorie": ["ambiente"]}, PIENO, "non_coperto", "dichiarato"),
    ("certificazione", "certificazione non classificata",
     {"tipo": "certificazione", "categorie": ["ambiente"]},
     profilo(profilo_partner={"certificazioni": ["Marchio del consorzio XY"]}), "incerto",
     "dichiarato"),
    ("certificazione", "senza certificazioni",
     {"tipo": "certificazione", "categorie": ["ambiente"]}, SENZA_PROFILO, "dato_mancante",
     "dichiarato"),
    # esperienza
    ("esperienza", "programma",
     {"tipo": "esperienza", "programmi_ids": [HORIZON]}, PIENO, "coperto", "dichiarato"),
    ("esperienza", "programma e ruolo",
     {"tipo": "esperienza", "programmi_ids": [HORIZON], "ruolo": "partner"}, PIENO, "coperto",
     "dichiarato"),
    ("esperienza", "ruolo diverso",
     {"tipo": "esperienza", "programmi_ids": [HORIZON], "ruolo": "capofila"}, PIENO,
     "non_coperto", "dichiarato"),
    ("esperienza", "esperienza senza programma del catalogo",
     {"tipo": "esperienza", "programmi_ids": [FESR]},
     profilo(profilo_partner={"esperienze": [{"programma": "POR FESR", "programma_id": None}]}),
     "incerto", "dichiarato"),
    ("esperienza", "senza esperienze",
     {"tipo": "esperienza", "programmi_ids": [FESR]}, SENZA_PROFILO, "dato_mancante",
     "dichiarato"),
    # regola finanziaria (vista proprio, valori esatti)
    ("regola_finanziaria", "soddisfatta",
     {"tipo": "regola_finanziaria", "regola": regola("fatturato", "ge", soglia="1000000")},
     PIENO, "coperto", "bilanci"),
    ("regola_finanziaria", "non soddisfatta",
     {"tipo": "regola_finanziaria", "regola": regola("fatturato", "ge", soglia="3000000")},
     PIENO, "non_coperto", "bilanci"),
    ("regola_finanziaria", "senza bilanci",
     {"tipo": "regola_finanziaria", "regola": regola("fatturato", "ge", soglia="1000000")},
     profilo(esercizi=[]), "dato_mancante", "bilanci"),
    ("regola_finanziaria", "bilanci approvati con storico incompleto",
     {"tipo": "regola_finanziaria", "regola": regola("bilanci_approvati_n", "ge", soglia="3")},
     profilo(storico_completo=False), "incerto", "bilanci"),
    # manuale
    ("manuale", "sempre non valutabile", {"tipo": "manuale"}, PIENO, "non_valutabile", None),
]


@pytest.mark.parametrize(
    "tipo, descrizione, criterio, candidato, esito, fonte",
    CASI,
    ids=[f"{c[0]}-{c[1]}" for c in CASI],
)
def test_tabella_tipo_esito(tipo, descrizione, criterio, candidato, esito, fonte):
    valutato = pc.valuta_criterio(crit(**criterio), candidato, vista="proprio")
    assert valutato.esito == esito, descrizione
    assert valutato.fonte == fonte
    assert valutato.testo_pubblico.startswith(pc._NOME_CRITERIO[tipo])


def test_la_tabella_copre_ogni_tipo_di_criterio():
    assert {c[0] for c in CASI} == set(get_args(TipoCriterio))
    # ogni tipo dell'unione ha un modello e viceversa
    modelli = get_args(get_args(sc.CriterioPartner)[0])
    assert {m.model_fields["tipo"].default for m in modelli} == set(get_args(TipoCriterio))
    # per ogni tipo valutabile si raggiungono sia coperto sia non_coperto
    for tipo in set(get_args(TipoCriterio)) - {"manuale"}:
        esiti = {c[4] for c in CASI if c[0] == tipo}
        assert {"coperto", "non_coperto", "dato_mancante"} <= esiti, tipo


def test_criterio_assente_vale_manuale():
    valutato = pc.valuta_criterio(None, PIENO, vista="terzi")
    assert valutato.esito == "non_valutabile" and valutato.fonte is None
    assert pc.valuta_criterio(CriterioManuale(), VUOTO, vista="proprio").esito == (
        "non_valutabile"
    )


# ----------------------------------------------------------------- sedi


class TestSedi:
    def test_tutte_le_sedi_bastano_le_unita_locali(self):
        solo_legale = profilo(derived={**DERIVED, "regioni_ids": None})
        c = CriterioRegione(regioni_ids=[CALABRIA])
        assert pc.valuta_criterio(c, PIENO, vista="terzi").esito == "coperto"
        # senza l'elenco di tutte le sedi resta la sola sede legale (Lombardia)
        assert pc.valuta_criterio(c, solo_legale, vista="terzi").esito == "non_coperto"

    def test_sede_entro_erogazione_mai_non_coperto(self):
        c = CriterioRegione(regioni_ids=[LAZIO], modalita="sede_entro_erogazione")
        valutato = pc.valuta_criterio(c, PIENO, vista="terzi")
        assert valutato.esito == "incerto"
        assert "entro l'erogazione" in valutato.testo_pubblico
        # con la sede nella regione è coperto anche in questa modalità
        c2 = CriterioRegione(regioni_ids=[CALABRIA], modalita="sede_entro_erogazione")
        assert pc.valuta_criterio(c2, PIENO, vista="terzi").esito == "coperto"

    def test_regione_dichiarata_non_conta(self):
        # la regione dei dati aziendali (modificabile) non entra nel profilo
        solo_dichiarato = profilo(company={"regione_id": LAZIO, "settore_id": 47})
        assert LAZIO not in solo_dichiarato.regioni_ids
        senza_registro = profilo(company={"regione_id": LAZIO}, derived=None)
        c = CriterioRegione(regioni_ids=[LAZIO])
        assert pc.valuta_criterio(c, senza_registro, vista="proprio").esito == "dato_mancante"


# ------------------------------------------------ registro prima del dichiarato


class TestRegistroPrevale:
    def test_dimensione_solo_dal_registro(self):
        # company_profiles.classe_dimensionale (modificabile) è ignorata
        dichiarato = profilo(company={"classe_dimensionale": "micro"})
        c = crit(tipo="dimensione", valori=["micro"])
        assert pc.valuta_criterio(c, dichiarato, vista="terzi").esito == "non_coperto"
        senza = profilo(company={"classe_dimensionale": "micro"}, derived=None)
        assert pc.valuta_criterio(c, senza, vista="terzi").esito == "dato_mancante"

    def test_tipo_da_registro_dichiarato_non_conta(self):
        # una riga con un tipo del registro tra i dichiarati (dato vecchio o
        # manomesso) non lo rende vero
        manomesso = profilo(
            profilo_partner={"tipi_soggetto": ["micro_impresa", "startup_innovativa"]}
        )
        assert "micro_impresa" not in manomesso.tipi_dichiarati
        for codice in ("micro_impresa", "startup_innovativa"):
            valutato = pc.valuta_criterio(
                crit(tipo="tipo_soggetto", valori=[codice]), manomesso, vista="terzi"
            )
            assert valutato.esito == "non_coperto" and valutato.fonte == "registro"

    def test_con_piu_tipi_preferisce_il_registro(self):
        doppio = profilo(profilo_partner={"tipi_soggetto": ["impresa_sociale"]})
        c = crit(tipo="tipo_soggetto", valori=["impresa_sociale", "piccola_impresa"])
        valutato = pc.valuta_criterio(c, doppio, vista="proprio")
        assert valutato.esito == "coperto" and valutato.fonte == "registro"

    def test_cooperativa_dalla_forma_giuridica(self):
        coop = profilo(
            dossier={**DOSSIER, "anagrafica": {"forma_giuridica": "Società cooperativa"}}
        )
        c = crit(tipo="tipo_soggetto", valori=["cooperativa"])
        assert pc.valuta_criterio(c, coop, vista="terzi").esito == "coperto"
        assert pc.valuta_criterio(c, PIENO, vista="terzi").esito == "non_coperto"
        senza_forma = profilo(dossier={"flags": {}})
        assert pc.valuta_criterio(c, senza_forma, vista="terzi").esito == "dato_mancante"

    def test_impresa_nel_registro_senza_tipi_deducibili(self):
        muto = profilo(derived={"regioni_ids": [LAZIO]}, dossier={}, profilo_partner=None)
        c = crit(tipo="tipo_soggetto", valori=["impresa"])
        # non si può escludere: dato mancante, non «non coperto»
        assert pc.valuta_criterio(c, muto, vista="terzi").esito == "dato_mancante"
        assert pc.valuta_criterio(c, PIENO, vista="terzi").esito == "coperto"


# ------------------------------------------------------ regole finanziarie


class TestRegoleFinanziarie:
    def test_proprio_esatto_terzi_sulla_fascia(self):
        # fatturato 1,8 M€: esatto ≥ 1,5 M€; la fascia 500k_2m sta a cavallo
        c = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="1500000"))
        proprio = pc.valuta_criterio(c, PIENO, vista="proprio")
        terzi = pc.valuta_criterio(c, PIENO, vista="terzi")
        assert proprio.esito == "coperto"
        assert terzi.esito == "incerto"
        assert "dipende dalla fascia" in terzi.testo_pubblico

    def test_terzi_tutta_la_fascia(self):
        sotto = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="400000"))
        sopra = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="3000000"))
        assert pc.valuta_criterio(sotto, PIENO, vista="terzi").esito == "coperto"
        assert pc.valuta_criterio(sopra, PIENO, vista="terzi").esito == "non_coperto"

    def test_costo_quota_come_intervallo(self):
        # costo quota / fatturato medio 2 ≤ 0,6: fatturato medio 1,65 M€
        c = crit(
            tipo="regola_finanziaria",
            regola=regola("costo_quota", "le", denominatore="fatturato_medio_2", soglia="0.6"),
        )
        quota = pc.intervallo_costo_quota((D(1_000_000), D(1_200_000)), D(30))
        assert quota == (D(300_000), D(360_000))
        assert pc.valuta_criterio(c, PIENO, costo_quota=quota, vista="proprio").esito == "coperto"
        # sui terzi la media cade nella fascia 500k_2m: 360k/500k > 0,6 → incerto
        assert pc.valuta_criterio(c, PIENO, costo_quota=quota, vista="terzi").esito == "incerto"
        # senza costo della quota: dato mancante
        mancante = pc.valuta_criterio_dettaglio(c, PIENO, vista="proprio")
        assert mancante.esito.esito == "dato_mancante"
        assert mancante.motivo == "costo_quota_mancante"
        # budget a cavallo della soglia anche sui valori esatti → incerto
        largo = pc.intervallo_costo_quota((D(1), D(10_000_000)), D(30))
        assert pc.valuta_criterio(c, PIENO, costo_quota=largo, vista="proprio").esito == "incerto"

    def test_verso_terzi_nessun_importo_ne_testo_privato(self):
        c = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="1500000"))
        proprio = pc.valuta_criterio(c, PIENO, vista="proprio")
        terzi = pc.valuta_criterio(c, PIENO, vista="terzi")
        assert terzi.testo_privato is None
        assert proprio.testo_privato and "1.800.000" in proprio.testo_privato
        for testo in (terzi.testo_pubblico, proprio.testo_pubblico):
            assert "1.800.000" not in testo and "1800000" not in testo

    def test_testo_pubblico_non_dipende_dall_azienda(self):
        # due aziende diverse con lo stesso esito hanno lo stesso testo pubblico
        altra = profilo(
            derived={**DERIVED, "regioni_ids": [CALABRIA], "ateco_principale": "72.19"},
            profilo_partner={"competenze": ["cybersecurity"], "tipi_soggetto": ["universita"]},
        )
        for criterio in (
            {"tipo": "regione", "regioni_ids": [CALABRIA]},
            {"tipo": "ateco", "divisioni": ["72"]},
            {"tipo": "tipo_soggetto", "valori": ["universita", "organismo_ricerca"]},
        ):
            a = pc.valuta_criterio(crit(**criterio), PIENO, vista="terzi")
            b = pc.valuta_criterio(crit(**criterio), altra, vista="terzi")
            assert a.esito == b.esito == "coperto"
            assert a.testo_pubblico == b.testo_pubblico
            assert a.testo_privato is None and b.testo_privato is None


# ------------------------------------------------------------ vocabolario


class TestVocabolario:
    def test_solo_codici_del_vocabolario_v1(self):
        assert list(get_args(TipoSoggetto)) == list(voc.TIPI_SOGGETTO)
        assert list(get_args(Competenza)) == list(voc.COMPETENZE)
        assert list(get_args(sc.CategoriaCertificazione)) == list(CATEGORIE_CERTIFICAZIONE)
        assert set(get_args(sc.Dimensione)) == {"micro", "piccola", "media", "grande"}

    @pytest.mark.parametrize(
        "dati",
        [
            {"tipo": "tag", "tags": ["prototipazione_rapida"]},
            {"tipo": "tipo_soggetto", "valori": ["startup"]},
            {"tipo": "certificazione", "categorie": ["iso_9001"]},
            {"tipo": "dimensione", "valori": ["piccolissima"]},
            {"tipo": "esperienza", "programmi_ids": [1], "ruolo": "coordinatore"},
        ],
    )
    def test_codici_fuori_vocabolario_rifiutati(self, dati):
        with pytest.raises(ValidationError):
            crit(**dati)


# ----------------------------------------------------------------- builder


class TestProfiloCandidato:
    def test_dal_registro_e_dal_profilo(self):
        p = PIENO
        assert p.registro_presente and p.profilo_presente
        assert p.classe_dimensionale == "piccola"
        assert p.tipi_registro == ("impresa", "piccola_impresa", "pmi")
        assert p.regioni_ids == frozenset({LOMBARDIA, CALABRIA})
        assert p.paese == "IT"
        assert p.ateco_divisioni == ("62", "72")
        assert p.tipi_dichiarati == ("organismo_ricerca",)
        assert p.certificazioni_categorie == ("qualita",)
        assert p.esperienze[0].programma_id == HORIZON
        assert p.settori_ids == frozenset({47})
        assert p.fasce is not None and p.fasce.fatturato == "500k_2m"
        assert [e.anno for e in p.esercizi] == [2023, 2024]

    def test_esercizi_da_righe_del_db(self):
        righe = [
            {"anno": 2024, "fatturato": "1800000.00", "fonte_per_campo": {"fatturato": "xbrl"}},
            {"anno": 2023, "fatturato": 1500000},
        ]
        p = profilo(esercizi=righe)
        assert [e.anno for e in p.esercizi] == [2023, 2024]
        assert p.esercizi[1].valore("fatturato") == D("1800000.00")

    def test_vuoto(self):
        assert VUOTO == ProfiloCandidato()
        assert VUOTO.fasce is None and not VUOTO.registro_presente

    def test_dati_sporchi_ignorati(self):
        p = profilo(
            profilo_partner={
                "competenze": ["inventata", "cybersecurity", 3],
                "tipi_soggetto": ["boh", "universita"],
                "esperienze": [{"programma_id": True, "ruolo": "capo"}, "x"],
                "certificazioni": [None, "  "],
            },
            company={"settore_id": "47"},
        )
        assert p.competenze == ("cybersecurity",)
        assert p.tipi_dichiarati == ("universita",)
        assert p.esperienze[0].programma_id is None and p.esperienze[0].ruolo is None
        assert p.certificazioni_categorie == ()
        assert p.settori_ids == frozenset()


# ------------------------------------------------------------------ utilità


class TestUtilita:
    def test_criterio_da_json(self):
        assert pc.criterio_da_json(None) is None
        c = pc.criterio_da_json({"tipo": "ateco", "divisioni": ["62"]})
        assert c.tipo == "ateco"
        # corrotto → manuale (non conta mai in automatico)
        assert isinstance(pc.criterio_da_json({"tipo": "ateco", "divisioni": "62"}),
                          CriterioManuale)
        assert isinstance(pc.criterio_da_json({"tipo": "sconosciuto"}), CriterioManuale)

    def test_criterio_json_andata_e_ritorno(self):
        c = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="1000"))
        dato = pc.criterio_json(c)
        assert dato["tipo"] == "regola_finanziaria" and dato["regola"]["soglia"] == "1000"
        assert pc.criterio_da_json(dato) == c
        assert pc.criterio_json(None) is None

    def test_criteri_da_posizione(self):
        criteri = pc.criteri_da_posizione(
            {
                "tipi_soggetto": ["organismo_ricerca"],
                "competenze": ["prototipazione_testing"],
                "ateco_divisioni": ["72"],
                "regioni": [CALABRIA],
                "territorio_modalita": "sede_attuale",
                "paesi": ["IT"],
                "dimensioni": ["piccola"],
            }
        )
        assert [c.tipo for c in criteri] == [
            "tipo_soggetto", "tag", "ateco", "regione", "paese", "dimensione",
        ]
        # «qualsiasi» = nessun vincolo di regione
        assert pc.criteri_da_posizione({"regioni": [CALABRIA]}) == []

    def test_intervallo_costo_quota(self):
        assert pc.intervallo_costo_quota(None, 30) is None
        assert pc.intervallo_costo_quota((D(100), D(100)), None) is None
        minimo, massimo = pc.intervallo_costo_quota((D(5_000_000), Decimal("Infinity")), "20")
        assert minimo == D(1_000_000) and massimo == Decimal("Infinity")

    def test_regola_nel_criterio_deve_essere_coerente(self):
        incoerente = regola("fatturato", "ge", soglia="1", soglia_variabile="costo_quota")
        with pytest.raises(ValidationError):
            crit(tipo="regola_finanziaria", regola=incoerente)
        # la regola del criterio resta il contratto WP1
        c = crit(tipo="regola_finanziaria", regola=regola("fatturato", "ge", soglia="1"))
        assert isinstance(c.regola, RegolaFinanziaria)
