"""Bilancio ufficiale (WP2): ZIP del bilancio ottico e istanza XBRL
(services/xbrl_bilancio.py).

Le istanze in tests/fixtures/xbrl/ sono SINTETICHE, scritte a mano (società
fittizia con la P.IVA 09876543217 delle altre fixture sintetiche, tassonomia
itcc-ci 2018-11-04). Le istanze dei casi limite e gli ZIP si costruiscono in
memoria nei test. Nessuna rete, nessun file scritto su disco.
"""

import io
import random
import struct
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services import xbrl_bilancio as xb
from app.services.bilanci_mapping import CAMPI_BILANCIO, RigaFonte, a_payload_rpc
from app.services.xbrl_bilancio import (
    AllegatiNonValidi,
    ErroreContenutoBilancio,
    EsercizioXbrl,
    IstanzaXbrl,
    XbrlNonValido,
    XbrlTimeout,
    XbrlTroppoGrande,
    esito_senza_xbrl,
    estrai_allegati,
    parse_xbrl,
    righe_da_istanza,
)

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
PIVA = "09876543217"
ALTRA_PIVA = "00000000000"

PDF = b"%PDF-1.4\n% bilancio sintetico di prova\n" + b"0" * 200 + b"\n%%EOF\n"
VERBALE = b"%PDF-1.4\n% verbale sintetico di prova\n%%EOF\n"


def carica(nome: str) -> bytes:
    return (FIXTURES / nome).read_bytes()


def D(valore) -> Decimal:
    return Decimal(str(valore))


def esercizio(ist: IstanzaXbrl, anno: int) -> EsercizioXbrl:
    return next(e for e in ist.esercizi if e.anno == anno)


# ----------------------------------------------------- istanze in memoria

_TESTA = (
    '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"'
    ' xmlns:link="http://www.xbrl.org/2003/linkbase"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink"'
    ' xmlns:iso4217="http://www.xbrl.org/2003/iso4217"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:itcc-ci="http://www.infocamere.it/itnn/fr/itcc/ci/2018-11-04">'
)
_ENTITA = (
    '<xbrli:entity><xbrli:identifier scheme="http://www.infocamere.it">'
    f"{PIVA}</xbrli:identifier></xbrli:entity>"
)


def contesto_istante(cid: str, giorno: str) -> str:
    return (
        f'<xbrli:context id="{cid}">{_ENTITA}'
        f"<xbrli:period><xbrli:instant>{giorno}</xbrli:instant></xbrli:period></xbrli:context>"
    )


def contesto_durata(cid: str, inizio: str, fine: str) -> str:
    return (
        f'<xbrli:context id="{cid}">{_ENTITA}<xbrli:period>'
        f"<xbrli:startDate>{inizio}</xbrli:startDate><xbrli:endDate>{fine}</xbrli:endDate>"
        "</xbrli:period></xbrli:context>"
    )


CONTESTI_2024 = contesto_istante("i2024", "2024-12-31") + contesto_durata(
    "d2024", "2024-01-01", "2024-12-31"
)


def fatto(concetto: str, valore: str, ctx: str = "d2024", unita: str = "eur") -> str:
    return (
        f'<itcc-ci:{concetto} contextRef="{ctx}" unitRef="{unita}" decimals="0">'
        f"{valore}</itcc-ci:{concetto}>"
    )


def istanza(
    fatti: str = "",
    *,
    contesti: str = CONTESTI_2024,
    schema: str = "itcc-ci-ese-2018-11-04.xsd",
    prologo: str = "",
    cf: str | None = PIVA,
) -> bytes:
    anagrafica = (
        f'<itcc-ci:DatiAnagraficiCodiceFiscale contextRef="i2024">{cf}'
        "</itcc-ci:DatiAnagraficiCodiceFiscale>"
        if cf
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + prologo
        + _TESTA
        + f'<link:schemaRef xlink:type="simple" xlink:href="{schema}"/>'
        + contesti
        + '<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>'
        + '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>'
        + anagrafica
        + fatti
        + "</xbrli:xbrl>"
    ).encode("utf-8")


# ------------------------------------------------------------- ZIP in memoria

def crea_zip(membri: list[tuple[str, bytes]], metodo: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=metodo) as zf:
        for nome, dati in membri:
            zf.writestr(nome, dati)
    return buffer.getvalue()


def ritocca_directory_centrale(
    dati: bytes, nome: str, *, file_size: int | None = None, flag: int | None = None
) -> bytes:
    """Falsifica la voce della directory centrale (quella che zipfile usa per
    `ZipInfo`) senza toccare i dati compressi."""
    buf = bytearray(dati)
    fine_cd = buf.rfind(b"PK\x05\x06")
    voci, _dim, inizio = struct.unpack_from("<HII", buf, fine_cd + 10)
    pos = inizio
    for _ in range(voci):
        assert buf[pos:pos + 4] == b"PK\x01\x02"
        lung_nome, lung_extra, lung_commento = struct.unpack_from("<HHH", buf, pos + 28)
        if bytes(buf[pos + 46:pos + 46 + lung_nome]).decode() == nome:
            if file_size is not None:
                struct.pack_into("<I", buf, pos + 24, file_size)
            if flag is not None:
                struct.pack_into("<H", buf, pos + 8, flag)
            return bytes(buf)
        pos += 46 + lung_nome + lung_extra + lung_commento
    raise AssertionError(f"membro {nome} non trovato")


def spia_parser(monkeypatch, prima_del_feed=None) -> dict:
    """Sostituisce ET.XMLParser con un involucro che conta i byte passati a
    expat (e, se serve, fa avanzare un orologio finto)."""
    originale = ET.XMLParser
    stato = {"alimentati": 0}

    class Spia:
        def __init__(self, **kwargs):
            self._parser = originale(**kwargs)

        def feed(self, dati):
            stato["alimentati"] += len(dati)
            if prima_del_feed is not None:
                prima_del_feed(len(dati))
            return self._parser.feed(dati)

        def close(self):
            return self._parser.close()

    monkeypatch.setattr(xb.ET, "XMLParser", Spia)
    return stato


# ================================================================ fixture

class TestFixture:
    def test_ordinario_due_annualita(self):
        ist = parse_xbrl(carica("ordinario_2024.xbrl"))
        assert ist.forma == "ordinario"
        assert ist.tassonomia == "2018-11-04"
        assert (ist.cf, ist.piva) == (PIVA, PIVA)
        assert ist.avvisi == []
        assert [(e.anno, e.ruolo) for e in ist.esercizi] == [
            (2024, "corrente"),
            (2023, "comparativo"),
        ]
        corrente = esercizio(ist, 2024)
        assert (corrente.inizio, corrente.fine) == (date(2024, 1, 1), date(2024, 12, 31))
        assert set(corrente.valori) == set(CAMPI_BILANCIO)
        assert corrente.valori == {
            "fatturato": D("1250000.00"),
            "valore_produzione": D("1300000.00"),
            "risultato_esercizio": D("48000.00"),
            "patrimonio_netto": D("610000.00"),
            "capitale_sociale": D("100000.00"),
            "totale_attivo": D("2150000.00"),
            "debiti_totali": D("1210000.00"),
            "disponibilita_liquide": D("180000.00"),
            "ebitda": None,  # mai derivato dall'XBRL in v1
            "ebit": D("85000.00"),
            "cash_flow": None,
            "oneri_finanziari": D("12500.00"),
            "dipendenti": D("12.00"),
            "costo_personale": D("420000.00"),
            "retribuzione_media_lorda": None,
        }
        comparativo = esercizio(ist, 2023)
        assert comparativo.valori["fatturato"] == D("1100000")
        assert comparativo.valori["ebit"] == D("70000")
        assert comparativo.valori["dipendenti"] == D("11.50")

    def test_ordinario_istanti_di_inizio_esercizio_non_usati(self):
        """RF e NI riusano gli stessi concept a inizio esercizio: il 2023
        prende i valori al 2023-12-31, mai quelli al 2022-12-31."""
        ist = parse_xbrl(carica("ordinario_2024.xbrl"))
        assert [e.anno for e in ist.esercizi] == [2024, 2023]  # nessun esercizio 2022
        comparativo = esercizio(ist, 2023)
        assert comparativo.valori["disponibilita_liquide"] == D("150000")  # non 120000
        assert comparativo.valori["patrimonio_netto"] == D("562000")  # non 527000
        assert comparativo.valori["capitale_sociale"] == D("100000")  # non 90000
        assert comparativo.valori["debiti_totali"] == D("1150000")  # non 1090000
        assert comparativo.valori["totale_attivo"] == D("1980000")  # non 1870000
        # la liquidità d'inizio 2024 (= fine 2023) non sporca la chiusura 2024
        assert esercizio(ist, 2024).valori["disponibilita_liquide"] == D("180000")

    def test_ordinario_tupla_e_textblock_non_raccolti(self):
        ist = parse_xbrl(carica("ordinario_2024.xbrl"))
        # la tupla DebitiAreaGeografica contiene TotaleDebiti = 999
        assert esercizio(ist, 2024).valori["debiti_totali"] == D("1210000")
        assert not any("discordante" in avviso for avviso in ist.avvisi)

    def test_abbreviato(self):
        ist = parse_xbrl(carica("abbreviato_2024.xbrl"))
        assert ist.forma == "abbreviato"  # schemaRef con URL completo
        assert ist.tassonomia == "2018-11-04"
        assert ist.piva == PIVA  # «IT09876543217» normalizzata
        assert [(e.anno, e.ruolo) for e in ist.esercizi] == [
            (2024, "corrente"),
            (2023, "comparativo"),
        ]
        corrente = esercizio(ist, 2024)
        assert corrente.valori["risultato_esercizio"] == D("-15400")  # perdita
        assert corrente.valori["ebit"] == D("-9800")
        assert corrente.valori["dipendenti"] == D("3")
        assert ist.avvisi == []

    def test_micro_senza_dipendenti(self):
        ist = parse_xbrl(carica("micro_2024.xbrl"))
        assert ist.forma == "micro"
        assert ist.piva is None and ist.cf == PIVA
        assert [(e.anno, e.ruolo) for e in ist.esercizi] == [(2024, "corrente")]
        valori = ist.esercizi[0].valori
        assert valori["dipendenti"] is None
        assert valori["fatturato"] == D("98700")
        assert valori["disponibilita_liquide"] is None  # non presente nell'istanza
        assert ist.avvisi == []

    def test_xsi_nil_vale_assente_non_zero(self):
        ist = parse_xbrl(carica("nil_2024.xbrl"))
        valori = ist.esercizi[0].valori
        assert valori["fatturato"] is None
        assert valori["disponibilita_liquide"] is None
        assert valori["dipendenti"] is None
        assert valori["valore_produzione"] == D("77000")
        # utile di CE nil (xsi:nil="1"): ripiego sull'utile di stato patrimoniale
        assert valori["risultato_esercizio"] == D("2300")
        assert ist.avvisi == []

    def test_contesti_con_segment_o_scenario_ignorati(self):
        ist = parse_xbrl(carica("segment_2024.xbrl"))
        # la durata 2023 è usata solo da un contesto con segment: niente esercizio
        assert [e.anno for e in ist.esercizi] == [2024]
        valori = ist.esercizi[0].valori
        assert valori["fatturato"] == D("500000")  # non 1 (segment)
        assert valori["risultato_esercizio"] == D("21000")
        assert valori["totale_attivo"] == D("640000")  # non 3 (scenario)
        assert valori["patrimonio_netto"] is None  # solo nello scenario
        assert "contesto_con_dimensioni" in ist.avvisi

    def test_unita_non_eur_scartate(self):
        ist = parse_xbrl(carica("unita_non_eur_2024.xbrl"))
        valori = ist.esercizi[0].valori
        assert valori["fatturato"] == D("330000")  # «valuta:EUR» → namespace iso4217
        for campo in (
            "totale_attivo",  # USD
            "patrimonio_netto",  # prefisso iso4217 ridefinito
            "debiti_totali",  # unità composta
            "disponibilita_liquide",  # unità inesistente
            "dipendenti",  # un numero in euro
        ):
            assert valori[campo] is None, campo
        assert "unita_non_eur: TotaleAttivo" in ist.avvisi
        assert "unita_non_eur: TotalePatrimonioNetto" in ist.avvisi
        assert "unita_non_eur: TotaleDebiti" in ist.avvisi
        assert "unita_non_valida: TotaleDipendentiNumeroMedio" in ist.avvisi

    def test_tuple_della_nota_integrativa_non_raccolte(self):
        ist = parse_xbrl(carica("tuple_nota_integrativa_2024.xbrl"))
        valori = ist.esercizi[0].valori
        assert valori["debiti_totali"] is None
        assert valori["disponibilita_liquide"] is None
        assert valori["dipendenti"] is None
        assert valori["fatturato"] == D("210000")
        assert ist.avvisi == []

    def test_consolidato(self):
        ist = parse_xbrl(carica("consolidato_2024.xbrl"))
        assert ist.forma == "consolidato"
        assert ist.esercizi[0].valori["fatturato"] == D("52000000")

    def test_cf_diverso(self):
        ist = parse_xbrl(carica("cf_diverso_2024.xbrl"))
        assert (ist.cf, ist.piva) == (ALTRA_PIVA, ALTRA_PIVA)

    @pytest.mark.parametrize("nome", sorted(p.name for p in FIXTURES.glob("*.xbrl")))
    def test_ogni_fixture_solo_decimal_e_campi_noti(self, nome):
        ist = parse_xbrl(carica(nome))
        assert ist.esercizi, nome
        for es in ist.esercizi:
            assert list(es.valori) == list(CAMPI_BILANCIO)
            for valore in es.valori.values():
                assert valore is None or type(valore) is Decimal
                assert not isinstance(valore, float)
            assert es.valori["ebitda"] is None


# ============================================================ avvisi TEBENI

class TestAvvisiTebeni:
    def test_x8_x9_xz_sono_avvisi_non_errori(self):
        ist = parse_xbrl(carica("incoerente_2024.xbrl"))
        assert ist.avvisi == [
            "X8 2024: utile dello stato patrimoniale diverso da quello del conto economico",
            "X9 2024: totale attivo diverso dal totale passivo",
            "XZ 2024: esercizio di 549 giorni (massimo 425)",
        ]
        # i valori restano: l'avviso non blocca
        corrente = esercizio(ist, 2024)
        assert corrente.valori["risultato_esercizio"] == D("50000")  # quello di CE
        assert corrente.valori["totale_attivo"] == D("1000000")
        # esercizio non solare: anno = anno di chiusura
        comparativo = esercizio(ist, 2023)
        assert comparativo.fine == date(2023, 6, 30)
        righe, esito = righe_da_istanza(ist, {PIVA})
        assert esito == "ok" and len(righe) == 2

    def test_xz_una_annualita_ammette_fino_a_731_giorni(self):
        lungo = contesto_istante("i2024", "2024-12-31") + contesto_durata(
            "d2024", "2023-02-01", "2024-12-31"
        )
        ist = parse_xbrl(istanza(fatto("TotaleValoreProduzione", "1000"), contesti=lungo))
        assert not any(avviso.startswith("XZ") for avviso in ist.avvisi)  # 699 giorni
        troppo = contesto_istante("i2024", "2024-12-31") + contesto_durata(
            "d2024", "2022-12-01", "2024-12-31"
        )
        ist = parse_xbrl(istanza(fatto("TotaleValoreProduzione", "1000"), contesti=troppo))
        assert "XZ 2024: esercizio di 761 giorni (massimo 731)" in ist.avvisi

    def test_coerente_nessun_avviso(self):
        for nome in ("ordinario_2024.xbrl", "abbreviato_2024.xbrl", "micro_2024.xbrl"):
            assert parse_xbrl(carica(nome)).avvisi == [], nome


# ========================================================= sicurezza XML

_BILLION_LAUGHS = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE xbrli:xbrl [\n"
    ' <!ENTITY lol "lol">\n'
    + "".join(
        f' <!ENTITY lol{i} "{("&lol" + (str(i - 1) if i > 1 else "") + ";") * 10}">\n'
        for i in range(1, 10)
    )
    + "]>\n"
    + '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">&lol9;</xbrli:xbrl>'
).encode()

_XXE = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE xbrli:xbrl [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
    '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance">&xxe;</xbrli:xbrl>'
).encode()


class TestSicurezzaXml:
    def test_doctype_semplice_rifiutato(self):
        dati = istanza(prologo="<!DOCTYPE xbrli:xbrl>\n")
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(dati)
        assert info.value.codice == "doctype"
        assert info.value.esito == "non_valido"

    def test_billion_laughs_rifiutato_prima_dell_espansione(self, monkeypatch):
        stato = spia_parser(monkeypatch)
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(_BILLION_LAUGHS)
        assert info.value.codice == "doctype"
        # expat non ha mai visto le entità annidate né il riferimento &lol9;
        assert stato["alimentati"] < _BILLION_LAUGHS.index(b"<!ENTITY lol2")
        assert stato["alimentati"] < _BILLION_LAUGHS.index(b"&lol9;")

    def test_xxe_rifiutato_prima_dell_espansione(self, monkeypatch):
        stato = spia_parser(monkeypatch)
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(_XXE)
        assert info.value.codice == "doctype"
        assert stato["alimentati"] < _XXE.index(b"&xxe;")

    def test_dtd_esterna_rifiutata(self):
        dati = istanza(prologo='<!DOCTYPE xbrli:xbrl SYSTEM "http://127.0.0.1:9/finta.dtd">\n')
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(dati)
        assert info.value.codice == "doctype"

    def test_doctype_in_utf16_rifiutato(self):
        """Il controllo sta nel parser, dopo la decodifica: una ricerca di
        «<!DOCTYPE» nei byte non lo vedrebbe in UTF-16."""
        testo = istanza(prologo="<!DOCTYPE xbrli:xbrl>\n").decode("utf-8")
        dati = testo.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
        assert b"<!DOCTYPE" not in dati
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(dati)
        assert info.value.codice == "doctype"

    def test_senza_doctype_utf16_si_legge(self):
        testo = istanza(fatto("TotaleValoreProduzione", "4200")).decode("utf-8")
        dati = testo.replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
        ist = parse_xbrl(dati)
        assert ist.esercizi[0].valori["valore_produzione"] == D("4200")

    @pytest.mark.parametrize(
        "dati",
        [
            b"",
            b"<xbrli:xbrl",
            b'<?xml version="1.0"?><a>&entita_non_dichiarata;</a>',
            b'<?xml version="1.0" encoding="codifica-inesistente"?><a/>',
            b"\x00\x01\x02 non xml",
        ],
    )
    def test_xml_malformato(self, dati):
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(dati)
        assert info.value.codice in ("xml_malformato", "radice_non_xbrl")

    def test_radice_non_xbrl(self):
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(b'<?xml version="1.0"?><bilancio><TotaleAttivo>1</TotaleAttivo></bilancio>')
        assert info.value.codice == "radice_non_xbrl"

    def test_prologo_troppo_lungo(self):
        dati = istanza(prologo="<!--" + "x" * (xb._MAX_PROLOGO + 10) + "-->\n")
        with pytest.raises(XbrlNonValido) as info:
            parse_xbrl(dati)
        assert info.value.codice == "prologo_troppo_lungo"

    def test_commento_breve_prima_della_radice_ammesso(self):
        dati = istanza(
            fatto("TotaleValoreProduzione", "10"), prologo="<!-- generato dal software X -->\n"
        )
        assert parse_xbrl(dati).esercizi[0].valori["valore_produzione"] == D("10")

    def test_troppo_grande(self, monkeypatch):
        monkeypatch.setattr(xb, "MAX_XBRL_BYTES", 100)
        with pytest.raises(XbrlTroppoGrande) as info:
            parse_xbrl(carica("micro_2024.xbrl"))
        assert info.value.esito == "troppo_grande"
        assert isinstance(info.value, ErroreContenutoBilancio)


# ================================================================ deadline

class TestDeadline:
    def test_deadline_tra_un_blocco_e_l_altro(self, monkeypatch):
        """Parsing lento (1 s finto ogni 64 KB) su un textBlock da 1 MB che
        segue tutti gli elementi: scade il controllo tra i blocchi."""
        orologio = {"t": 0.0}
        monkeypatch.setattr(xb, "_adesso", lambda: orologio["t"])
        stato = spia_parser(
            monkeypatch,
            prima_del_feed=lambda n: orologio.__setitem__("t", orologio["t"] + n / xb.CHUNK),
        )
        testo = "&lt;p&gt;" + "x" * 1_000_000 + "&lt;/p&gt;"
        dati = istanza(
            fatto("TotaleValoreProduzione", "10")
            + f'<itcc-ci:TestoNotaIntegrativa contextRef="d2024">{testo}'
            "</itcc-ci:TestoNotaIntegrativa>"
        )
        with pytest.raises(XbrlTimeout) as info:
            parse_xbrl(dati, deadline_s=5.0)
        assert info.value.esito == "non_valido"
        assert "start" not in [voce.name for voce in info.traceback]
        assert stato["alimentati"] < len(dati) // 2  # interrotto, non letto tutto

    def test_deadline_controllata_dentro_start(self, monkeypatch):
        """Un'istanza piccola sta in un solo blocco: il controllo tra i
        blocchi non basterebbe, scatta quello in start()."""
        chiamate = iter([0.0])
        monkeypatch.setattr(xb, "_adesso", lambda: next(chiamate, 100.0))
        with pytest.raises(XbrlTimeout) as info:
            parse_xbrl(carica("ordinario_2024.xbrl"))
        assert "start" in [voce.name for voce in info.traceback]

    def test_istanza_da_8_mb_con_textblock_entro_la_deadline(self):
        """Il target scarta il testo dei textBlock senza accumularlo: 8 MB
        passano con l'orologio vero e la deadline di produzione."""
        blocco = "&lt;p&gt;" + "Testo della nota integrativa, riga di esempio. " * 600 + "&lt;/p&gt;"
        testi = "".join(
            f'<itcc-ci:TestoNota{i % 7} contextRef="d1">{blocco}</itcc-ci:TestoNota{i % 7}>'
            for i in range(290)
        )
        base = carica("ordinario_2024.xbrl")
        dati = base.replace(b"</xbrli:xbrl>", testi.encode() + b"</xbrli:xbrl>")
        assert 8_000_000 < len(dati) < xb.MAX_XBRL_BYTES
        ist = parse_xbrl(dati)
        assert ist.esercizi == parse_xbrl(base).esercizi


# ================================================================ decimali

class TestDecimali:
    def test_nessun_float_2_alla_53_piu_1(self):
        """2**53 + 1 non è rappresentabile in float: un passaggio da float
        darebbe ...992."""
        ist = parse_xbrl(
            istanza(
                fatto("TotaleAttivo", "9007199254740993", ctx="i2024")
                + fatto("TotaleValoreProduzione", "0.1")
            )
        )
        valori = ist.esercizi[0].valori
        assert valori["totale_attivo"] == Decimal("9007199254740993.00")
        assert valori["valore_produzione"] == Decimal("0.10")
        righe, esito = righe_da_istanza(ist, {PIVA})
        assert esito == "ok"
        assert a_payload_rpc(righe)[0]["valori"]["totale_attivo"] == "9007199254740993.00"

    def test_arrotondamento_al_centesimo(self):
        ist = parse_xbrl(istanza(fatto("TotaleValoreProduzione", "1234.565")))
        assert ist.esercizi[0].valori["valore_produzione"] == Decimal("1234.57")

    @pytest.mark.parametrize("testo", ["1E3", "NaN", "Infinity", "1.000,50", "", "12 345", "0x10"])
    def test_valori_non_numerici_scartati(self, testo):
        ist = parse_xbrl(
            istanza(fatto("TotaleValoreProduzione", "500") + fatto("UtilePerditaEsercizio", testo))
        )
        assert ist.esercizi[0].valori["risultato_esercizio"] is None
        assert "valore_non_valido: UtilePerditaEsercizio" in ist.avvisi

    def test_segno_impossibile_e_fuori_scala_scartati(self):
        ist = parse_xbrl(
            istanza(
                fatto("ValoreProduzioneRicaviVenditePrestazioni", "-5")
                + fatto("TotaleAttivo", "1" + "0" * 17, ctx="i2024")
                + fatto("UtilePerditaEsercizio", "-5")
            )
        )
        valori = ist.esercizi[0].valori
        assert valori["fatturato"] is None
        assert valori["totale_attivo"] is None
        assert valori["risultato_esercizio"] == D("-5")  # una perdita è legittima
        assert "valore_negativo: ValoreProduzioneRicaviVenditePrestazioni" in ist.avvisi
        assert "valore_fuori_scala: TotaleAttivo" in ist.avvisi

    def test_fatto_duplicato_discordante_diventa_assente(self):
        doppio = CONTESTI_2024 + contesto_durata("d2024bis", "2024-01-01", "2024-12-31")
        ist = parse_xbrl(
            istanza(
                fatto("ValoreProduzioneRicaviVenditePrestazioni", "100")
                + fatto("ValoreProduzioneRicaviVenditePrestazioni", "200", ctx="d2024bis")
                + fatto("TotaleValoreProduzione", "300")
                + fatto("TotaleValoreProduzione", "300", ctx="d2024bis")
            ,
                contesti=doppio,
            )
        )
        valori = ist.esercizi[0].valori
        assert valori["fatturato"] is None
        assert valori["valore_produzione"] == D("300")  # duplicato coerente: tenuto
        assert "fatto_discordante: ValoreProduzioneRicaviVenditePrestazioni 2024" in ist.avvisi


# ==================================================================== ZIP

def _zip_bilancio(*extra: tuple[str, bytes]) -> bytes:
    return crea_zip(
        [
            ("bilancio_2024.pdf", PDF),
            ("12345_2024.xbrl", carica("ordinario_2024.xbrl")),
            ("verbale_assemblea.pdf", VERBALE),
            *extra,
        ]
    )


class TestZip:
    def test_zip_completo(self):
        allegati = estrai_allegati(_zip_bilancio())
        assert allegati.pdf == PDF
        assert allegati.xbrl == carica("ordinario_2024.xbrl")
        assert allegati.ignorati == ["verbale_assemblea.pdf"]
        assert allegati.firmati == [] and allegati.avvisi == []

    def test_verbale_scartato_anche_se_primo_e_pdf_successivi_ignorati(self):
        allegati = estrai_allegati(
            crea_zip(
                [
                    ("Verbale.PDF", VERBALE),
                    ("cartella/bilancio.pdf", PDF),
                    ("altro.pdf", b"%PDF-1.4 secondo documento"),
                ]
            )
        )
        assert allegati.pdf == PDF
        assert allegati.ignorati == ["Verbale.PDF", "altro.pdf"]
        assert allegati.xbrl is None

    def test_nomi_opachi_si_tiene_il_pdf_piu_grande(self):
        # Allegati reali con nomi come «<id>_0.pdf»: il verbale può venire per
        # primo; il bilancio (con la nota integrativa) è il più grande.
        allegati = estrai_allegati(
            crea_zip([("6a4b_0.pdf", VERBALE), ("6a4b_1.pdf", PDF)], metodo=zipfile.ZIP_STORED)
        )
        assert allegati.pdf == PDF
        assert allegati.ignorati == ["6a4b_0.pdf"]

    def test_nome_con_bilancio_prima_della_dimensione(self):
        grande = b"%PDF-1.4 nota integrativa " + b"0" * (len(PDF) * 2)
        allegati = estrai_allegati(
            crea_zip([("nota.pdf", grande), ("Bilancio_2024.pdf", PDF),
                      ("assemblea_soci.pdf", grande)])
        )
        assert allegati.pdf == PDF
        assert allegati.ignorati == ["assemblea_soci.pdf", "nota.pdf"]

    def test_pdf_senza_intestazione_rifiutato(self):
        allegati = estrai_allegati(
            crea_zip([("bilancio.pdf", b"<html>non un pdf</html>"), ("b.xbrl", carica("micro_2024.xbrl"))])
        )
        assert allegati.pdf is None
        assert "pdf_non_valido" in allegati.avvisi
        assert allegati.xbrl is not None  # l'altro documento resta

    def test_p7m_in_firmati_non_letti(self):
        allegati = estrai_allegati(
            crea_zip([("bilancio.pdf", PDF), ("12345_2024.xbrl.p7m", b"\x30\x80firmato")])
        )
        assert allegati.firmati == ["12345_2024.xbrl.p7m"]
        assert allegati.xbrl is None
        assert esito_senza_xbrl(allegati) == "firmato_non_leggibile"

    def test_solo_pdf_firmato_e_xbrl_assente(self):
        allegati = estrai_allegati(crea_zip([("bilancio.pdf.p7m", b"\x30\x80firmato")]))
        assert allegati.pdf is None and allegati.xbrl is None
        assert esito_senza_xbrl(allegati) == "assente"

    def test_xbrl_assente(self):
        allegati = estrai_allegati(crea_zip([("bilancio.pdf", PDF), ("leggimi.txt", b"ciao")]))
        assert allegati.pdf == PDF
        assert allegati.xbrl is None
        assert allegati.ignorati == ["leggimi.txt"]
        assert esito_senza_xbrl(allegati) == "assente"

    def test_estensione_xbrl_prima_di_xml_generico(self):
        metadati = b'<?xml version="1.0"?><metadati/>'
        allegati = estrai_allegati(
            crea_zip([("metadati.xml", metadati), ("istanza.xbrl", carica("micro_2024.xbrl"))])
        )
        assert allegati.xbrl == carica("micro_2024.xbrl")
        assert allegati.ignorati == ["metadati.xml"]

    def test_xbrl_con_estensione_xml_e_bom(self):
        con_bom = b"\xef\xbb\xbf" + carica("micro_2024.xbrl")
        allegati = estrai_allegati(
            crea_zip([("finto.xml", b"non sono xml"), ("bilancio.xml", con_bom)])
        )
        assert allegati.xbrl == con_bom
        assert allegati.ignorati == ["finto.xml"]
        assert parse_xbrl(allegati.xbrl).forma == "micro"

    def test_xbrl_senza_dichiarazione_con_spazi_iniziali(self):
        micro = carica("micro_2024.xbrl")
        senza_dichiarazione = b"\n  " + micro[micro.index(b"<xbrli:xbrl"):]
        allegati = estrai_allegati(crea_zip([("bilancio.xbrl", senza_dichiarazione)]))
        assert allegati.xbrl == senza_dichiarazione
        assert parse_xbrl(allegati.xbrl).forma == "micro"

    def test_bomba_per_rapporto_di_compressione(self):
        bomba = crea_zip([("bilancio.pdf", PDF), ("bilancio.xbrl", b"\0" * 5_000_000)])
        assert len(bomba) < 50_000
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(bomba)
        assert info.value.codice == "zip_bomba"
        assert info.value.esito == "non_valido"

    def test_troppi_membri(self):
        membri = [(f"file_{i}.txt", b"x") for i in range(xb.MAX_MEMBRI + 1)]
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(crea_zip(membri))
        assert info.value.codice == "zip_troppi_membri"

    def test_somma_decompressa_oltre_il_tetto(self, monkeypatch):
        monkeypatch.setattr(xb, "MAX_TOTALE_DECOMPRESSO", 10_000)
        casuale = random.Random(7)
        membri = [(f"parte_{i}.bin", casuale.randbytes(4_000)) for i in range(3)]
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(crea_zip(membri))
        assert info.value.codice == "zip_decompresso_eccessivo"
        assert info.value.esito == "troppo_grande"

    def test_somma_dichiarata_controllata_prima_di_leggere(self):
        """Con i tetti veri: due membri minuscoli che DICHIARANO 25 MB l'uno."""
        dati = crea_zip([("a.pdf", PDF), ("b.xbrl", carica("micro_2024.xbrl"))])
        dati = ritocca_directory_centrale(dati, "a.pdf", file_size=25_000_000)
        dati = ritocca_directory_centrale(dati, "b.xbrl", file_size=25_000_000)
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(dati)
        assert info.value.codice == "zip_decompresso_eccessivo"

    def test_file_size_falso_piu_piccolo(self):
        """La directory centrale dichiara 100 byte per un XBRL di ~3 KB:
        zipfile si ferma a 100 byte e il CRC non torna. Il membro si scarta,
        il PDF resta."""
        dati = crea_zip([("bilancio.pdf", PDF), ("bilancio.xbrl", carica("micro_2024.xbrl"))])
        dati = ritocca_directory_centrale(dati, "bilancio.xbrl", file_size=100)
        allegati = estrai_allegati(dati)
        assert allegati.pdf == PDF
        assert allegati.xbrl is None
        assert "xbrl_illeggibile" in allegati.avvisi
        assert esito_senza_xbrl(allegati) == "non_valido"

    def test_file_size_falso_piu_grande(self):
        """Membro non compresso che dichiara più byte di quelli che ha."""
        xbrl = carica("micro_2024.xbrl")
        dati = crea_zip(
            [("bilancio.pdf", PDF), ("bilancio.xbrl", xbrl)], metodo=zipfile.ZIP_STORED
        )
        dati = ritocca_directory_centrale(dati, "bilancio.xbrl", file_size=len(xbrl) + 500)
        allegati = estrai_allegati(dati)
        assert allegati.xbrl is None
        assert "membro_dimensione_falsa" in allegati.avvisi
        assert allegati.pdf == PDF

    def test_lettura_limitata_a_massimo_piu_uno(self, monkeypatch):
        """Anche se lo stream restituisse più della file_size dichiarata, non
        si legge mai oltre il tetto + 1 e il membro si scarta."""
        dati = crea_zip([("bilancio.xbrl", b"<?xml version='1.0'?><x/>")])
        monkeypatch.setattr(xb, "MAX_XBRL_BYTES", 1_000)
        richieste: list[int] = []

        class FlussoBugiardo(io.BytesIO):
            def read(self, n=-1):
                richieste.append(n)
                return super().read(n)

        monkeypatch.setattr(
            zipfile.ZipFile,
            "open",
            lambda self, info, *a, **k: FlussoBugiardo(b"<?xml " + b"x" * 50_000),
        )
        allegati = estrai_allegati(dati)
        assert allegati.xbrl is None
        assert "membro_dimensione_falsa" in allegati.avvisi
        # prima lo sniff dell'intestazione, poi il contenuto con read(tetto + 1)
        assert richieste == [xb._PREFISSO_SNIFF, 1_001]

    def test_membro_cifrato_non_letto(self):
        dati = crea_zip([("bilancio.pdf", PDF), ("bilancio.xbrl", carica("micro_2024.xbrl"))])
        dati = ritocca_directory_centrale(dati, "bilancio.xbrl", flag=0x1)
        allegati = estrai_allegati(dati)
        assert allegati.xbrl is None
        assert allegati.ignorati == ["bilancio.xbrl"]
        assert "membro_cifrato" in allegati.avvisi

    def test_pdf_oltre_il_tetto_non_conservato(self, monkeypatch):
        monkeypatch.setattr(xb, "MAX_PDF_BYTES", 100)
        allegati = estrai_allegati(
            crea_zip([("bilancio.pdf", PDF), ("b.xbrl", carica("micro_2024.xbrl"))])
        )
        assert allegati.pdf is None
        assert "pdf_troppo_grande" in allegati.avvisi
        assert allegati.xbrl is not None

    def test_xbrl_oltre_il_tetto(self, monkeypatch):
        monkeypatch.setattr(xb, "MAX_XBRL_BYTES", 100)
        allegati = estrai_allegati(
            crea_zip([("bilancio.pdf", PDF), ("b.xbrl", carica("micro_2024.xbrl"))])
        )
        assert allegati.xbrl is None
        assert "xbrl_troppo_grande" in allegati.avvisi
        assert esito_senza_xbrl(allegati) == "troppo_grande"

    def test_zip_oltre_il_tetto(self, monkeypatch):
        monkeypatch.setattr(xb, "MAX_ZIP_BYTES", 100)
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(_zip_bilancio())
        assert (info.value.codice, info.value.esito) == ("zip_troppo_grande", "troppo_grande")

    def test_non_zip(self):
        with pytest.raises(AllegatiNonValidi) as info:
            estrai_allegati(b"PK\x03\x04 troncato")
        assert info.value.codice == "zip_non_valido"

    def test_pdf_consegnato_senza_zip(self):
        allegati = estrai_allegati(PDF)
        assert allegati.pdf == PDF and allegati.xbrl is None
        assert allegati.avvisi == ["allegato_non_zip"]

    def test_nomi_ripuliti(self):
        allegati = estrai_allegati(
            crea_zip([("../../etc/verbale\x07.pdf", VERBALE), ("bilancio.pdf", PDF)])
        )
        assert allegati.ignorati == ["verbale.pdf"]


# ======================================================= righe_da_istanza

def _istanza_manuale(**kwargs) -> IstanzaXbrl:
    base = {
        "forma": "ordinario",
        "tassonomia": "2018-11-04",
        "cf": PIVA,
        "piva": None,
        "esercizi": [],
        "avvisi": [],
    }
    base.update(kwargs)
    return IstanzaXbrl(**base)


def _esercizio(anno: int, ruolo: str, **valori) -> EsercizioXbrl:
    tutti = dict.fromkeys(CAMPI_BILANCIO)
    tutti.update({k: D(v) for k, v in valori.items()})
    return EsercizioXbrl(
        anno=anno,
        inizio=date(anno, 1, 1),
        fine=date(anno, 12, 31),
        valori=tutti,
        ruolo=ruolo,
    )


class TestRigheDaIstanza:
    def test_corrente_e_comparativo(self):
        righe, esito = righe_da_istanza(parse_xbrl(carica("ordinario_2024.xbrl")), {PIVA})
        assert esito == "ok"
        assert [(r.anno, r.ruolo, r.tipo_bilancio, r.data_chiusura) for r in righe] == [
            (2023, "comparativo", "ordinario", date(2023, 12, 31)),
            (2024, "corrente", "ordinario", date(2024, 12, 31)),
        ]
        assert all(isinstance(r, RigaFonte) for r in righe)
        payload = a_payload_rpc(righe)
        assert payload[1]["valori"]["fatturato"] == "1250000.00"
        assert payload[1]["valori"]["dipendenti"] == "12.00"
        assert "ebitda" not in payload[1]["valori"]
        assert all(isinstance(v, str) for riga in payload for v in riga["valori"].values())

    def test_tipo_bilancio_dalla_forma(self):
        for nome, tipo in (("abbreviato_2024.xbrl", "abbreviato"), ("micro_2024.xbrl", "micro")):
            righe, esito = righe_da_istanza(parse_xbrl(carica(nome)), {PIVA})
            assert esito == "ok"
            assert {r.tipo_bilancio for r in righe} == {tipo}

    def test_forma_ignota_diventa_tipo_ignoto(self):
        ist = parse_xbrl(istanza(fatto("TotaleValoreProduzione", "5"), schema="altro.xsd"))
        assert ist.forma == "ignoto"
        assert ist.tassonomia == "2018-11-04"  # dal namespace dei concept
        assert "forma_non_riconosciuta" in ist.avvisi
        righe, esito = righe_da_istanza(ist, {PIVA})
        assert esito == "ok" and righe[0].tipo_bilancio == "ignoto"

    def test_identificativi_normalizzati(self):
        ist = parse_xbrl(carica("ordinario_2024.xbrl"))
        assert righe_da_istanza(ist, {" it09876543217 "})[1] == "ok"
        assert righe_da_istanza(ist, ["IT 09876543217", "altro"])[1] == "ok"

    def test_basta_uno_tra_cf_e_piva(self):
        ist = _istanza_manuale(cf="RSSMRA80A01H501U", piva=PIVA, esercizi=[
            _esercizio(2024, "corrente", fatturato="10")
        ])
        assert righe_da_istanza(ist, {PIVA})[1] == "ok"
        assert righe_da_istanza(ist, {"rssmra80a01h501u"})[1] == "ok"

    def test_consolidato_nessuna_riga(self):
        ist = parse_xbrl(carica("consolidato_2024.xbrl"))
        assert righe_da_istanza(ist, {PIVA}) == ([], "consolidato")

    def test_cf_non_corrispondente(self):
        ist = parse_xbrl(carica("cf_diverso_2024.xbrl"))
        assert righe_da_istanza(ist, {PIVA}) == ([], "cf_non_corrispondente")

    def test_istanza_senza_identificativi(self):
        ist = parse_xbrl(istanza(fatto("TotaleValoreProduzione", "5"), cf=None))
        assert (ist.cf, ist.piva) == (None, None)
        assert righe_da_istanza(ist, {PIVA}) == ([], "cf_non_corrispondente")

    def test_nessun_attesa(self):
        ist = parse_xbrl(carica("micro_2024.xbrl"))
        assert righe_da_istanza(ist, set()) == ([], "cf_non_corrispondente")

    def test_nessun_valore_core_non_valido(self):
        ist = _istanza_manuale(
            esercizi=[_esercizio(2024, "corrente", ebit="10", dipendenti="3", debiti_totali="7")]
        )
        assert righe_da_istanza(ist, {PIVA}) == ([], "non_valido")

    def test_nessun_esercizio_non_valido(self):
        assert righe_da_istanza(_istanza_manuale(), {PIVA}) == ([], "non_valido")

    def test_nessun_core_dall_istanza_vera(self):
        ist = parse_xbrl(istanza(fatto("DifferenzaValoreCostiProduzione", "10")))
        assert righe_da_istanza(ist, {PIVA}) == ([], "non_valido")

    def test_esercizio_senza_core_saltato(self):
        ist = _istanza_manuale(
            esercizi=[
                _esercizio(2024, "corrente", ebit="10"),
                _esercizio(2023, "comparativo", fatturato="500"),
            ]
        )
        righe, esito = righe_da_istanza(ist, {PIVA})
        assert esito == "ok"
        assert [(r.anno, r.ruolo) for r in righe] == [(2023, "comparativo")]

    def test_dallo_zip_alle_righe(self):
        allegati = estrai_allegati(_zip_bilancio())
        righe, esito = righe_da_istanza(parse_xbrl(allegati.xbrl), {PIVA})
        assert esito == "ok"
        assert [r.anno for r in righe] == [2023, 2024]
