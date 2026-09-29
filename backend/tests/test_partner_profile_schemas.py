"""Schemi del profilo partner (app/schemas/partner_profile.py) e informative.

Proprietà difese:
- le regole di dominio (vocabolario, cardinalità della DDL 0035, paesi ISO,
  anni, lunghezze) rispondono 400 `bad_request` con un messaggio per l'utente,
  anche attraverso FastAPI; campi sconosciuti e tipi sbagliati restano 422;
- il client non può scrivere le origini `admin`/`sistema` del registro dei
  consensi né dichiarare i tipi di soggetto che vengono dal registro;
- lo schema dell'output del modello ha solo tipi ed enum.
"""

from datetime import date, datetime, timezone
from typing import get_args
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import BadRequestError, register_exception_handlers
from app.schemas import partner_profile as sp
from app.schemas.partenariato_vocabolario import Competenza, FormaAggregazione, TipoSoggetto
from app.schemas.partner_profile import (
    BozzaProfiloAi,
    ConsensoIn,
    EsperienzaPartner,
    PartnerProfileIn,
    PartnerProfileOut,
    ReferenteIn,
    ReferenteRispostaIn,
)
from app.services import partenariato_informativa as inf
from app.services import partenariato_vocabolario as voc

COMPETENZE = list(get_args(Competenza))
DICHIARABILI = [t for t in get_args(TipoSoggetto) if t not in sp.TIPI_SOGGETTO_DA_REGISTRO]


def errore_400(**dati) -> str:
    with pytest.raises(BadRequestError) as exc:
        PartnerProfileIn(**dati)
    assert exc.value.status_code == 400
    assert exc.value.code == "bad_request"
    return exc.value.message


# ------------------------------------------------------------ PartnerProfileIn


class TestPartnerProfileIn:
    def test_default_come_la_tabella(self):
        p = PartnerProfileIn()
        assert p.ruoli_disponibili == ["partner"]
        assert p.accetta_inviti is True
        assert p.competenze == [] and p.esperienze == [] and p.descrizione_competenze is None

    def test_extra_forbid(self):
        for campo in ("visibile_come_partner", "anonimo", "referente_user_id", "codice_pubblico"):
            with pytest.raises(ValidationError):
                PartnerProfileIn(**{campo: True})

    def test_codici_validi_dedup_e_ordine_stabile(self):
        p = PartnerProfileIn(
            competenze=["sviluppo_software", "ricerca_industriale", "sviluppo_software"],
            tipi_soggetto=["universita", "organismo_ricerca", "universita"],
            ruoli_disponibili=["partner", "capofila", "partner"],
            forme_accettate=["consorzio_ue", "ats", "ats"],
        )
        assert p.competenze == ["sviluppo_software", "ricerca_industriale"]
        assert p.tipi_soggetto == ["universita", "organismo_ricerca"]
        assert p.ruoli_disponibili == ["partner", "capofila"]
        assert p.forme_accettate == ["consorzio_ue", "ats"]

    @pytest.mark.parametrize(
        "dati",
        [
            {"competenze": ["prototipazione_rapida"]},
            {"competenze": ["Sviluppo_software"]},
            {"tipi_soggetto": ["societa_cooperativa"]},
            {"ruoli_disponibili": ["coordinatore"]},
            {"forme_accettate": ["altra"]},
            {"forme_accettate": ["ATS"]},
        ],
    )
    def test_codici_fuori_vocabolario_400(self, dati):
        assert "non riconosciuto" in errore_400(**dati)

    def test_tutto_il_vocabolario_accettato(self):
        p = PartnerProfileIn(
            competenze=COMPETENZE[:15],
            tipi_soggetto=DICHIARABILI[:8],
            forme_accettate=list(sp.FORME_PROFILO),
        )
        assert len(p.competenze) == 15 and len(p.forme_accettate) == 7

    def test_forme_del_profilo_sono_le_sette_della_ddl(self):
        assert sp.FORME_PROFILO == (
            "ats",
            "ati_rti",
            "rete_contratto",
            "rete_soggetto",
            "consorzio",
            "accordo_partenariato",
            "consorzio_ue",
        )
        assert set(sp.FORME_PROFILO) < set(get_args(FormaAggregazione))

    @pytest.mark.parametrize("codice", sorted(sp.TIPI_SOGGETTO_DA_REGISTRO))
    def test_tipi_del_registro_non_dichiarabili(self, codice):
        assert "Registro Imprese" in errore_400(tipi_soggetto=[codice])

    def test_tipi_del_registro_nel_vocabolario(self):
        assert sp.TIPI_SOGGETTO_DA_REGISTRO < set(voc.TIPI_SOGGETTO)
        assert "impresa" not in sp.TIPI_SOGGETTO_DA_REGISTRO

    def test_ruoli_non_vuoti(self):
        assert "almeno un ruolo" in errore_400(ruoli_disponibili=[])

    @pytest.mark.parametrize(
        ("campo", "massimo", "fabbrica"),
        [
            ("competenze", 15, lambda n: COMPETENZE[:n]),
            ("competenze_libere", 10, lambda n: [f"voce {i}" for i in range(n)]),
            ("tipi_soggetto", 8, lambda n: DICHIARABILI[:n]),
            ("settori_interesse", 20, lambda n: list(range(1, n + 1))),
            ("regioni_interesse", 21, lambda n: list(range(1, n + 1))),
            ("paesi_interesse", 30, lambda n: sorted(sp.PAESI_ISO2)[:n]),
            ("certificazioni", 20, lambda n: [f"ISO {9000 + i}" for i in range(n)]),
            ("categorie_bando_escluse", 50, lambda n: list(range(1, n + 1))),
            ("esperienze", 20, lambda n: [{"programma": f"P{i}"} for i in range(n)]),
        ],
    )
    def test_cardinalita(self, campo, massimo, fabbrica):
        assert len(getattr(PartnerProfileIn(**{campo: fabbrica(massimo)}), campo)) == massimo
        assert f"al massimo {massimo}" in errore_400(**{campo: fabbrica(massimo + 1)})

    def test_cardinalita_dopo_dedup(self):
        p = PartnerProfileIn(competenze=COMPETENZE[:15] + COMPETENZE[:15])
        assert len(p.competenze) == 15

    def test_paesi_iso2_maiuscoli_e_alias_ue(self):
        p = PartnerProfileIn(paesi_interesse=["de", " Fr ", "EL", "uk", "XK", "DE"])
        assert p.paesi_interesse == ["DE", "FR", "GR", "GB", "XK"]

    @pytest.mark.parametrize("paese", ["XX", "ITA", "I", "", "E1"])
    def test_paesi_non_validi(self, paese):
        assert "Codice paese" in errore_400(paesi_interesse=[paese])

    def test_elenco_iso_completo(self):
        assert len(sp.PAESI_ISO2) == 250  # 249 codici assegnati + XK
        assert all(len(c) == 2 and c.isupper() for c in sp.PAESI_ISO2)

    def test_id_lookup_positivi_e_dedup(self):
        assert PartnerProfileIn(regioni_interesse=[3, 1, 3]).regioni_interesse == [3, 1]
        errore_400(settori_interesse=[0])
        errore_400(regioni_interesse=[-2])

    def test_testi_lunghi(self):
        p = PartnerProfileIn(descrizione_competenze="  " + "a" * 2000 + "  ", infrastrutture="   ")
        assert p.descrizione_competenze == "a" * 2000
        assert p.infrastrutture is None
        assert "2.000 caratteri" in errore_400(descrizione_competenze="a" * 2001)
        errore_400(infrastrutture="a" * 2001)

    def test_voci_libere_pulite_e_dedup(self):
        p = PartnerProfileIn(
            competenze_libere=[" Stampa  3D ", "", "stampa 3d", "Robotica"],
            certificazioni=["ISO 9001:2015", "  ", "iso 9001:2015"],
        )
        assert p.competenze_libere == ["Stampa 3D", "Robotica"]
        assert p.certificazioni == ["ISO 9001:2015"]
        errore_400(competenze_libere=["a" * 81])
        errore_400(certificazioni=["a" * 201])

    def test_tipi_sbagliati_422(self):
        with pytest.raises(ValidationError):
            PartnerProfileIn(competenze="sviluppo_software")
        with pytest.raises(ValidationError):
            PartnerProfileIn(regioni_interesse=["Lombardia"])


# ---------------------------------------------------------------- esperienze


class TestEsperienza:
    def test_minima_e_completa(self):
        assert EsperienzaPartner(programma=" Horizon  Europe ").programma == "Horizon Europe"
        e = EsperienzaPartner(
            programma="Horizon Europe",
            programma_id=7,
            anno=2023,
            ruolo="capofila",
            titolo="  Progetto X ",
            esito="finanziato",
        )
        assert e.titolo == "Progetto X" and e.ruolo == "capofila"

    def test_anno_nell_intervallo(self):
        prossimo = date.today().year + 1
        assert EsperienzaPartner(programma="P", anno=1990).anno == 1990
        assert EsperienzaPartner(programma="P", anno=prossimo).anno == prossimo
        for anno in (1989, prossimo + 1):
            with pytest.raises(BadRequestError):
                EsperienzaPartner(programma="P", anno=anno)

    @pytest.mark.parametrize(
        "dati",
        [
            {"programma": "   "},
            {"programma": "a" * 121},
            {"programma": "P", "programma_id": 0},
            {"programma": "P", "ruolo": "coordinatore"},
            {"programma": "P", "esito": "vinto"},
            {"programma": "P", "titolo": "a" * 201},
        ],
    )
    def test_regole_400(self, dati):
        with pytest.raises(BadRequestError):
            EsperienzaPartner(**dati)

    def test_extra_e_tipi_422(self):
        with pytest.raises(ValidationError):
            EsperienzaPartner(programma="P", importo=1000)
        with pytest.raises(ValidationError):
            PartnerProfileIn(esperienze=[{"programma": "P", "extra": 1}])

    def test_titolo_vuoto_diventa_null(self):
        assert EsperienzaPartner(programma="P", titolo="  ").titolo is None


# ------------------------------------------------------- consenso e referente


class TestConsensoIn:
    @pytest.mark.parametrize("origine", ["import_piva", "pagina_azienda", "wizard_call"])
    def test_origini_ammesse_dal_client(self, origine):
        c = ConsensoIn(azione="concedi", informativa_versione="v1", origine=origine, anonimo=True)
        assert c.origine == origine

    @pytest.mark.parametrize("origine", ["admin", "sistema", "altro"])
    def test_origini_riservate_rifiutate(self, origine):
        with pytest.raises(ValidationError):
            ConsensoIn(azione="concedi", informativa_versione="v1", origine=origine)

    def test_anonimo_booleano_stretto(self):
        for valore in ("true", 1, "si"):
            with pytest.raises(ValidationError):
                ConsensoIn(
                    azione="concedi",
                    informativa_versione="v1",
                    origine="pagina_azienda",
                    anonimo=valore,
                )

    def test_anonimo_facoltativo_lo_controlla_la_rpc(self):
        c = ConsensoIn(azione="concedi", informativa_versione="v1", origine="pagina_azienda")
        assert c.anonimo is None

    @pytest.mark.parametrize(
        "dati",
        [
            {"azione": "cancella", "informativa_versione": "v1", "origine": "pagina_azienda"},
            {"azione": "revoca", "informativa_versione": "", "origine": "pagina_azienda"},
            {"azione": "revoca", "informativa_versione": "v" * 51, "origine": "pagina_azienda"},
            {
                "azione": "revoca",
                "informativa_versione": "v1",
                "origine": "pagina_azienda",
                "attore_user_id": str(uuid4()),
            },
        ],
    )
    def test_invalidi(self, dati):
        with pytest.raises(ValidationError):
            ConsensoIn(**dati)


class TestReferente:
    def test_proponi_richiede_la_persona(self):
        uid = uuid4()
        assert ReferenteIn(azione="proponi", user_id=uid).user_id == uid
        with pytest.raises(BadRequestError):
            ReferenteIn(azione="proponi")
        assert ReferenteIn(azione="rimuovi").user_id is None
        # Annulla solo la proposta pendente: nessuna persona da indicare.
        assert ReferenteIn(azione="annulla_proposta").user_id is None

    def test_referente_invalido(self):
        with pytest.raises(ValidationError):
            ReferenteIn(azione="proponi", user_id="non-un-uuid")
        with pytest.raises(ValidationError):
            ReferenteIn(azione="accetta")

    def test_risposta(self):
        assert ReferenteRispostaIn(azione="accetta", informativa_versione="v1").azione == "accetta"
        assert ReferenteRispostaIn(azione="rifiuta").informativa_versione is None
        assert ReferenteRispostaIn(azione="revoca").azione == "revoca"
        for versione in (None, "  "):
            with pytest.raises(BadRequestError):
                ReferenteRispostaIn(azione="accetta", informativa_versione=versione)
        with pytest.raises(ValidationError):
            ReferenteRispostaIn(azione="proponi")


# ------------------------------------------------------ schema del modello


class TestBozzaProfiloAi:
    VINCOLI = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
               "minLength", "maxLength", "minItems", "maxItems", "pattern")

    def _schema(self):
        import anthropic
        from pydantic import TypeAdapter

        return anthropic.transform_schema(TypeAdapter(BozzaProfiloAi).json_schema())

    def _nodi(self, nodo):
        if isinstance(nodo, dict):
            yield nodo
            for valore in nodo.values():
                yield from self._nodi(valore)
        elif isinstance(nodo, list):
            for valore in nodo:
                yield from self._nodi(valore)

    def test_solo_tipi_ed_enum(self):
        for nodo in self._nodi(self._schema()):
            assert not set(self.VINCOLI) & set(nodo), nodo

    def test_strict_tutti_i_campi_obbligatori(self):
        for nodo in self._nodi(self._schema()):
            if nodo.get("type") == "object" and "properties" in nodo:
                assert nodo.get("additionalProperties") is False
                assert set(nodo["required"]) == set(nodo["properties"])

    def test_codici_come_stringhe_senza_enum(self):
        """Niente enum da 42 valori (grammatica troppo grande, vedi
        tests/test_schemi_ai_dimensione.py): i codici sono stringhe."""
        schema = self._schema()
        assert not [n for n in self._nodi(schema) if "enum" in n]
        assert schema["properties"]["competenze"]["items"]["type"] == "string"

    def test_codici_ignoti_accettati_dallo_schema_e_scartati_dopo(self):
        from app.services.partner_profile_prompts import pulisci_bozza

        bozza = BozzaProfiloAi(
            descrizione_competenze="x", competenze=["inventata", "sviluppo_software"],
            motivazioni=[{"codice": "inventata", "motivo": "m"},
                         {"codice": "sviluppo_software", "motivo": "m"}],
        )
        pulita = pulisci_bozza(bozza, None)
        assert pulita["competenze"] == ["sviluppo_software"]
        assert [m["codice"] for m in pulita["motivazioni"]] == ["sviluppo_software"]
        assert all(c in voc.COMPETENZE for c in pulita["competenze"])


# ----------------------------------------------------------------- output


def test_partner_profile_out_forma_del_contratto():
    out = PartnerProfileOut(
        editable=False,
        esiste=True,
        visibile=True,
        anonimo=True,
        sospeso=False,
        consenso={"versione": "2026-10-bozza-1", "at": datetime(2026, 10, 1, tzinfo=timezone.utc)},
        informativa_versione_corrente="2026-10-bozza-1",
        riconsenso_suggerito=False,
        identita={"verificata": True, "denominazione_registro": "ACME SRL"},
        profilo={"competenze": ["sviluppo_software"], "esperienze": [{"programma": "P"}]},
        completezza=37,
        referente={"tipo": "titolare", "nome": "Mario", "sei_tu": False},
        bozza_ai={
            "stato": "pronta",
            "proposta": {
                "descrizione_competenze": "d",
                "competenze": ["cybersecurity"],
                "motivazioni": [{"codice": "cybersecurity", "motivo": "m"}],
            },
        },
        vocabolario_versione=1,
    )
    dati = out.model_dump(mode="json")
    assert set(dati) == {
        "editable", "esiste", "visibile", "anonimo", "sospeso", "consenso",
        "informativa_versione_corrente", "riconsenso_suggerito", "identita", "profilo",
        "tipi_soggetto_dedotti", "completezza", "avvisi_anonimato", "referente",
        "referenti_possibili", "bozza_ai", "vocabolario_versione", "aggiornato_at",
    }
    assert set(dati["profilo"]) == set(PartnerProfileIn.model_fields)
    assert set(dati["identita"]) == {
        "verificata", "motivo", "denominazione_registro", "puo_essere_nominativo",
        "motivo_nominativo", "verifica",
    }
    # WP9: stato della verifica dell'identità da parte della piattaforma, mai
    # chi ha verificato né come.
    assert dati["identita"]["verifica"] == {
        "stato": "non_richiesta", "verificata": False, "richiesta_at": None,
        "verificata_at": None, "puo_richiedere": False, "motivo_non_richiedibile": None,
    }
    assert set(dati["referente"]) == {"tipo", "nome", "sei_tu", "proposto"}
    assert set(dati["bozza_ai"]) == {"stato", "avviata_at", "pronta_at", "errore", "proposta"}
    assert dati["referenti_possibili"] == []


def test_partner_pubblico_out_whitelist():
    assert set(sp.PartnerPubblicoOut.model_fields) == {
        "codice_pubblico", "anonimo", "denominazione", "regione_sede", "regioni_interesse",
        "paesi_interesse", "ateco_sezione", "classe_dimensionale", "fasce", "tipi_soggetto",
        "competenze", "competenze_libere", "descrizione_competenze", "esperienze",
        "certificazioni", "infrastrutture", "ruoli_disponibili", "forme_accettate",
        "completezza", "accetta_inviti",
    }
    with pytest.raises(ValidationError):
        sp.PartnerPubblicoOut(codice_pubblico=uuid4(), anonimo=True, company_profile_id=uuid4())


# ------------------------------------------------- attraverso FastAPI: 400


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.put("/profilo")
    async def salva(dati: PartnerProfileIn) -> dict:
        return {"competenze": dati.competenze}

    @app.post("/consenso")
    async def consenso(dati: ConsensoIn) -> dict:
        return {"azione": dati.azione}

    return TestClient(app)


def test_api_codice_fuori_vocabolario_400(client):
    r = client.put("/profilo", json={"competenze": ["inventata"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"
    assert "inventata" in r.json()["error"]["message"]


def test_api_esperienza_annidata_400(client):
    r = client.put("/profilo", json={"esperienze": [{"programma": "P", "anno": 1900}]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_api_campo_protetto_422(client):
    r = client.put("/profilo", json={"visibile_come_partner": True})
    assert r.status_code == 422


def test_api_origine_riservata_422(client):
    r = client.post(
        "/consenso",
        json={
            "azione": "concedi",
            "informativa_versione": "v",
            "origine": "admin",
            "anonimo": True,
        },
    )
    assert r.status_code == 422


def test_api_valido_200(client):
    r = client.put("/profilo", json={"competenze": ["cybersecurity", "cybersecurity"]})
    assert r.status_code == 200
    assert r.json() == {"competenze": ["cybersecurity"]}


# ---------------------------------------------------------------- informative


class TestInformative:
    def test_versioni(self):
        # v2 (WP9): verifica dell'identità, rivelazione solo tra verificate,
        # moderazione dei messaggi segnalati, conservazione.
        assert inf.INFORMATIVA_PARTNER_VERSIONE == "2026-10-bozza-2"
        assert inf.INFORMATIVA_REFERENTE_VERSIONE == "2026-10-bozza-2"
        for versione, testo in (
            (inf.INFORMATIVA_PARTNER_VERSIONE, inf.INFORMATIVA_PARTNER_TESTO),
            (inf.INFORMATIVA_REFERENTE_VERSIONE, inf.INFORMATIVA_REFERENTE_TESTO),
        ):
            assert f"Versione {versione}." in testo

    @pytest.mark.parametrize(
        "tema",
        [
            # la verifica la fa la piattaforma, su richiesta
            "## Verifica dell'identità",
            "la verifica la chiede il titolare",
            "recapiti ufficiali del registro imprese",
            # il nome solo se scelto E verificato; la revoca lo spegne
            "solo se scegli di mostrarlo e solo se la piattaforma ha verificato l'identità",
            "il nome non si mostra più",
            # rivelazione simmetrica, altrimenti anonime e presentazione in chat
            "solo se entrambe le aziende hanno l'identità verificata",
            "restate anonime l'una per l'altra",
            "in chat potete presentarvi voi",
            # moderazione: finestra di contesto, intera solo con motivazione
            "## Segnalazioni e moderazione",
            "una finestra di messaggi vicini",
            "la conversazione intera solo se serve a decidere, indicando una motivazione",
            # conservazione
            "per 24 mesi",
            "registro delle verifiche dell'identità",
        ],
    )
    def test_temi_della_versione_2(self, tema):
        assert tema.lower() in inf.INFORMATIVA_PARTNER_TESTO.lower()

    def test_referente_versione_2(self):
        testo = inf.INFORMATIVA_REFERENTE_TESTO.lower()
        assert "solo se entrambe le aziende hanno l'identità verificata" in testo
        assert "staff di moderazione" in testo

    def test_promette_solo_cio_che_il_sistema_fa(self):
        """Correzione WP9: si registrano gli accessi al contesto dei messaggi
        segnalati e le decisioni, non ogni lettura della coda; il nome che
        un'azienda verificata sceglie di mostrare resta visibile anche a
        un'azienda non verificata, che invece non vede sito, PEC e referente."""
        testo = " ".join(inf.INFORMATIVA_PARTNER_TESTO.lower().split())
        assert "ogni accesso" not in testo
        assert "gli accessi ai messaggi vicini e alla conversazione intera" in testo
        assert "sito, pec e referente non si rivelano" in testo
        assert "salvo il nome che un'azienda verificata ha scelto di mostrare" in testo

    @pytest.mark.parametrize(
        "testo", [inf.INFORMATIVA_PARTNER_TESTO, inf.INFORMATIVA_REFERENTE_TESTO]
    )
    def test_bozza_dichiarata_e_linguaggio(self, testo):
        assert testo.splitlines()[0] == "[BOZZA — DA RIVEDERE CON IL LEGALE]"
        assert "famiglia" not in testo.lower()
        assert "\\" not in testo  # continuazioni di riga risolte

    @pytest.mark.parametrize(
        "tema",
        [
            "titolare del trattamento",
            "fasce",
            "solo se scegli di mostrarlo",
            "nessuno vede i tuoi recapiti",
            "ragione sociale",
            "PEC",
            "email personale",
            "referente",
            "accetta",
            "consulent",
            "non reversibili",
            "Base giuridica",
            "revocare il consenso in qualunque momento",
            "immediata",
            "5 anni dopo la revoca",
            "diritti",
        ],
    )
    def test_temi_coperti(self, tema):
        assert tema.lower() in inf.INFORMATIVA_PARTNER_TESTO.lower()

    def test_informativa_out(self):
        out = inf.informativa_out().model_dump()
        assert out == {
            "versione": inf.INFORMATIVA_PARTNER_VERSIONE,
            "testo": inf.INFORMATIVA_PARTNER_TESTO,
            "referente_versione": inf.INFORMATIVA_REFERENTE_VERSIONE,
            "referente_testo": inf.INFORMATIVA_REFERENTE_TESTO,
        }


def test_motivi_nominativo_e_verifica_dell_identita():
    """WP9: il CF del titolare non è più un motivo; la nota della richiesta
    di verifica è facoltativa e al massimo 500 caratteri, senza campi extra."""
    assert set(get_args(sp.MotivoNominativo)) == {"non_disponibile",
                                                   "identita_non_verificata_admin"}
    assert set(get_args(sp.StatoVerificaIdentita)) == {"non_richiesta", "richiesta",
                                                        "verificata", "rifiutata"}
    assert sp.VerificaIdentitaIn().nota is None
    assert sp.VerificaIdentitaIn(nota="x" * 500).nota == "x" * 500
    with pytest.raises(ValidationError):
        sp.VerificaIdentitaIn(nota="x" * 501)
    with pytest.raises(ValidationError):
        sp.VerificaIdentitaIn.model_validate({"nota": None, "metodo": "pec"})
