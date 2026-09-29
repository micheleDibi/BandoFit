"""Checklist documentale del consorzio per forma (WP8, docs/partenariati.md V3,
appendice A).

Proprietà difese:
- per OGNI forma del vocabolario: prima i documenti di base, poi gli specifici
  della forma, nell'ordine e con i titoli del vocabolario (unica fonte);
- codici compatibili con la CHECK di `partner_call_documenti.codice`;
- fasi: accordi preliminari, domanda, concessione secondo la costituzione
  (costituita alla domanda / costituenda / non indicata);
- documenti chiesti dal bando: obbligatori, con il momento del bando,
  aggiunti se la forma non li prevede; le voci «altro» restano fuori;
- stato per documento unito alla checklist.
"""

import re
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.partenariato_consorzio import DocumentoConsorzioOut, DocumentoOut
from app.schemas.partenariato_vocabolario import DocumentoPartenariato
from app.schemas.partner_call import DocumentoSnapshot
from app.services import partenariato_documenti as pd
from app.services import partenariato_vocabolario as voc

CIT = {"sezione": "S3", "testo": "Alla domanda va allegato l'accordo", "verificata": True}


def codici(lista) -> list[str]:
    return [d.codice for d in lista]


def per_codice(lista) -> dict:
    return {d.codice: d for d in lista}


@pytest.mark.parametrize("forma", list(voc.FORME))
def test_checklist_per_ogni_forma_dal_vocabolario(forma):
    lista = pd.checklist(forma, None)
    attesi = voc.documenti_forma(forma)
    assert codici(lista) == attesi
    assert codici(lista)[: len(voc.DOCUMENTI_BASE)] == list(voc.DOCUMENTI_BASE)
    for doc in lista:
        assert doc.titolo == voc.DOCUMENTI[doc.codice]
        assert re.fullmatch(pd.CODICE_DOCUMENTO_RE, doc.codice)
        assert doc.fonte == ("base" if doc.codice in voc.DOCUMENTI_BASE else "forma")
        assert doc.fase in ("accordo_preliminare", "domanda", "concessione")
    # Ogni specifico della forma ha una fase definita (non il ripiego generico).
    specifici = [d for d in lista if d.fonte == "forma"]
    assert len(specifici) == len(voc.FORME[forma].documenti)


def test_tutti_i_documenti_del_vocabolario_compaiono_e_hanno_codice_valido():
    visti = set()
    for forma in voc.FORME:
        visti.update(codici(pd.checklist(forma, None)))
    assert visti == set(voc.DOCUMENTI)
    for codice in voc.DOCUMENTI:
        assert re.fullmatch(pd.CODICE_DOCUMENTO_RE, codice), codice


@pytest.mark.parametrize("forma", [None, "altra", "inesistente"])
def test_senza_forma_solo_i_documenti_di_base(forma):
    assert codici(pd.checklist(forma, "non_indicato")) == list(voc.DOCUMENTI_BASE)


def test_documenti_di_base():
    base = per_codice(pd.checklist(None, None))
    for codice in ("nda", "lettera_intenti", "term_sheet_mou"):
        assert base[codice].fase == "accordo_preliminare"
        assert base[codice].obbligatorio is False
        assert base[codice].nota
    assert base["dichiarazione_sostitutiva"].fase == "domanda"
    assert base["dichiarazione_sostitutiva"].obbligatorio is True


@pytest.mark.parametrize("costituzione,fase", [
    ("costituita_richiesta", "domanda"),
    ("costituenda_ammessa", "concessione"),
    ("non_indicato", "concessione"),  # ATS: costituita prima della concessione
    (None, "concessione"),
])
def test_fasi_ats_secondo_la_costituzione(costituzione, fase):
    docs = per_codice(pd.checklist("ats", costituzione))
    assert docs["atto_costitutivo"].fase == fase
    assert docs["mandato_collettivo"].fase == fase
    assert docs["atto_costitutivo"].obbligatorio is True
    assert "concessione" in docs["atto_costitutivo"].nota


def test_fasi_reti_consorzi_e_consorzio_ue():
    rete = per_codice(pd.checklist("rete_soggetto", None))
    assert {rete[c].fase for c in voc.FORME["rete_soggetto"].documenti} == {"domanda"}
    rete = per_codice(pd.checklist("rete_contratto", "costituenda_ammessa"))
    assert rete["contratto_rete"].fase == "concessione"
    consorzio = per_codice(pd.checklist("consorzio", "costituenda_ammessa"))
    # Il consorzio partecipa già costituito: atto e statuto con la domanda.
    assert consorzio["statuto"].fase == "domanda"
    ue = per_codice(pd.checklist("consorzio_ue", "costituita_richiesta"))
    assert ue["consortium_agreement"].fase == "concessione"
    accordo = per_codice(pd.checklist("accordo_partenariato", None))
    assert accordo["accordo_partenariato"].fase == "domanda"
    assert "MIMIT" in accordo["accordo_partenariato"].nota


def test_ati_rti_impegno_e_nota_della_forma():
    costituenda = per_codice(pd.checklist("ati_rti", "costituenda_ammessa"))
    assert costituenda["impegno_costituire"].obbligatorio is True
    assert costituenda["impegno_costituire"].fase == "domanda"
    assert costituenda["mandato_collettivo"].fase == "concessione"
    assert "più raggruppamenti" in costituenda["impegno_costituire"].nota
    costituita = per_codice(pd.checklist("ati_rti", "costituita_richiesta"))
    assert costituita["impegno_costituire"].obbligatorio is False
    assert costituita["mandato_collettivo"].fase == "domanda"


def test_consorzio_ue_documenti_solo_con_i_ruoli():
    senza = per_codice(pd.checklist("consorzio_ue", None))
    assert senza["dichiarazioni_affiliated_entities"].obbligatorio is False
    assert senza["lettere_associated_partners"].obbligatorio is False
    con = per_codice(pd.checklist("consorzio_ue", None,
                                  ruoli=["capofila", "partner", "affiliated_entity"]))
    assert con["dichiarazioni_affiliated_entities"].obbligatorio is True
    assert con["lettere_associated_partners"].obbligatorio is False
    assert con["consortium_agreement"].obbligatorio is True


def test_documenti_chiesti_dal_bando():
    richiesti = [
        DocumentoSnapshot(id="D1", tipo="lettera_intenti", descrizione="Lettera d'intenti",
                          momento="domanda", origine_voce="confermata", citazione=CIT),
        {"id": "D2", "tipo": "accordo_partenariato", "descrizione": "Accordo",
         "momento": "prima_erogazione"},
        {"id": "D3", "tipo": "altro", "descrizione": "Piano di comunicazione",
         "momento": "domanda"},
        {"id": "D4", "tipo": "atto_costitutivo", "descrizione": "Atto", "momento": "non_indicato"},
    ]
    lista = pd.checklist("ats", "costituenda_ammessa", richiesti=richiesti)
    docs = per_codice(lista)
    assert docs["lettera_intenti"].obbligatorio is True
    assert docs["lettera_intenti"].fonte == "bando"
    assert docs["lettera_intenti"].fase == "domanda"
    # Non previsto dalla forma: aggiunto in coda, con il momento del bando.
    assert codici(lista)[-1] == "accordo_partenariato"
    assert docs["accordo_partenariato"].fase == "prima_erogazione"
    assert docs["accordo_partenariato"].fonte == "bando"
    # Momento non indicato: resta la fase della forma.
    assert docs["atto_costitutivo"].fase == "concessione"
    assert docs["atto_costitutivo"].fonte == "bando"
    assert "altro" not in docs
    assert len(lista) == len(set(codici(lista)))


def test_con_stato():
    lista = pd.checklist("ats", None)
    righe = [
        {"codice": "nda", "stato": "fatto", "note": "Firmato da tutti",
         "updated_at": "2026-10-02T10:00:00+00:00"},
        {"codice": "atto_costitutivo", "stato": "in_corso", "note": None},
        {"codice": "statuto", "stato": "fatto"},  # non più nella checklist
        {"codice": "lettera_intenti", "stato": "sconosciuto"},
    ]
    uscita = pd.con_stato(lista, righe)
    assert codici(uscita) == codici(lista)
    stati = per_codice(uscita)
    assert stati["nda"].stato == "fatto" and stati["nda"].note == "Firmato da tutti"
    assert stati["nda"].updated_at == datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
    assert stati["atto_costitutivo"].stato == "in_corso"
    assert stati["lettera_intenti"].stato == "da_fare"
    assert stati["term_sheet_mou"].stato == "da_fare" and stati["term_sheet_mou"].note is None
    assert "statuto" not in stati
    assert all(isinstance(d, DocumentoConsorzioOut) for d in uscita)


def test_documento_out_whitelist_e_alias():
    assert DocumentoOut is DocumentoConsorzioOut
    assert DocumentoConsorzioOut.__name__ != "DocumentoOut"  # niente doppione nello schema
    with pytest.raises(ValidationError):
        DocumentoConsorzioOut(codice="nda", titolo="NDA", fase="domanda", obbligatorio=True,
                              aggiornato_da_user_id="u1")


def test_documenti_del_vocabolario_coincidono_con_il_literal():
    from typing import get_args

    assert set(get_args(DocumentoPartenariato)) == set(voc.DOCUMENTI)
