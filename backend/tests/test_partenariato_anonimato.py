"""Controllo anti-contatti e anonimizzazione (services/partenariato_anonimato.py).

Proprietà difese:
- nessun contatto passa, in nessun profilo (anonimo o nominativo), anche
  offuscato («nome [at] dominio», «punto it»), con separatori o con prefissi;
- i falsi positivi tipici dei profili (anni, date, importi, percentuali, ATECO,
  norme ISO, codici di programma) non bloccano;
- per i profili anonimi: ragione sociale, denominazione, P.IVA/CF e dominio
  dell'azienda bloccano, i cognomi sono solo avvisi;
- `anonimizza` lascia un testo senza rilievi bloccanti;
- tempo lineare su stringhe ostili lunghe (nessun backtracking catastrofico).
"""

import re
import time

import pytest

from app.services import partenariato_anonimato as anon
from app.services.partenariato_anonimato import (
    ETICHETTE_RILIEVO,
    SOSTITUTO,
    Identificativi,
    Rilievo,
    anonimizza,
    dominio_da_url,
    ha_bloccanti,
    identificativi_azienda,
    nome_senza_forma_legale,
    normalizza,
    trova_rilievi,
)


def tipi(testo, ident=None, *, anonima=False) -> list[str]:
    return [r.tipo for r in trova_rilievi(testo, ident, anonima=anonima)]


def estratti(testo, ident=None, *, anonima=False) -> list[tuple[str, str]]:
    return [(r.tipo, r.estratto) for r in trova_rilievi(testo, ident, anonima=anonima)]


# ------------------------------------------------------ positivi (bloccanti)

POSITIVI = [
    # email, anche offuscate
    ("Scrivici a mario.rossi@acme.it", "email", "mario.rossi@acme.it"),
    ("INFO@ACME.COM per info", "email", "INFO@ACME.COM"),
    ("mailto:info@acme-srl.eu.", "email", "info@acme-srl.eu"),
    ("mario [at] acme [dot] it", "email", "mario [at] acme [dot] it"),
    ("nome [at] dominio", "email", "nome [at] dominio"),
    ("mario(at)acme.it", "email", "mario(at)acme.it"),
    ("mario {at} acme.it", "email", "mario {at} acme.it"),
    ("mario at acme dot it", "email", "mario at acme dot it"),
    ("mario chiocciola acme punto it", "email", "mario chiocciola acme punto it"),
    ("mario @ acme.it", "email", "mario @ acme.it"),
    ("info@acme senza dominio", "email", "info@acme"),
    # telefoni italiani e internazionali, con separatori
    ("tel. 02 1234567", "telefono", "02 1234567"),
    ("Tel.0521 123456", "telefono", "0521 123456"),
    ("cell. 347 123 4567", "telefono", "347 123 4567"),
    ("3471234567", "telefono", "3471234567"),
    ("347-1234567", "telefono", "347-1234567"),
    ("347.123.4567", "telefono", "347.123.4567"),
    ("+39 347 1234567", "telefono", "+39 347 1234567"),
    ("+393471234567", "telefono", "+393471234567"),
    ("0039 06 12345678", "telefono", "0039 06 12345678"),
    ("(02) 12345678", "telefono", "(02) 12345678"),
    ("02/1234567", "telefono", "02/1234567"),
    ("02 - 1234567", "telefono", "02 - 1234567"),
    ("numero verde 800 123 456", "telefono", "800 123 456"),
    ("+44 20 7946 0958", "telefono", "+44 20 7946 0958"),
    ("+1 (555) 123-4567", "telefono", "+1 (555) 123-4567"),
    ("39 347 1234567", "telefono", "39 347 1234567"),
    ("347_123_4567", "telefono", "347_123_4567"),
    ("chiama il 3 4 7 1 2 3 4 5 6 7", "telefono", "3 4 7 1 2 3 4 5 6 7"),
    ("chiama il n. 02 1234567", "telefono", "02 1234567"),
    ("Registrati e scrivi al 347 1234567", "telefono", "347 1234567"),
    ("Albo fornitori: tel. 02 1234567", "telefono", "02 1234567"),
    ("prot. +39 347 1234567", "telefono", "+39 347 1234567"),
    # URL con e senza schema
    ("https://www.acme.it/contatti", "url", "https://www.acme.it/contatti"),
    ("vedi http:/acme.it", "url", "http:/acme.it"),
    ("www.acme.it", "url", "www.acme.it"),
    ("visita acme.it.", "url", "acme.it"),
    ("acme-srl.com/chi-siamo?x=1", "url", "acme-srl.com/chi-siamo?x=1"),
    ("sito Acme.IT", "url", "Acme.IT"),
    ("partner.fraunhofer.de", "url", "partner.fraunhofer.de"),
    ("acme dot com", "url", "acme dot com"),
    ("sito acme . it", "url", "acme . it"),
    ("Scrivici su t.me/rossimeccanica", "url", "t.me/rossimeccanica"),
    ("wa.me/393471234567", "url", "wa.me/393471234567"),
    ("bit.ly/3abcDEF", "url", "bit.ly/3abcDEF"),
    ("seguici su @rossimeccanica", "url", "@rossimeccanica"),
    # P.IVA, CF, IBAN
    ("P.IVA 01234567897", "piva_cf", "01234567897"),
    ("IT01234567897", "piva_cf", "IT01234567897"),
    ("P.IVA 012 345 678 97", "piva_cf", "012 345 678 97"),
    ("CF RSSMRA80A01H501U", "piva_cf", "RSSMRA80A01H501U"),
    ("cf rssmra80a01h501u", "piva_cf", "rssmra80a01h501u"),
    ("IBAN IT60 X054 2811 1010 0000 0123 456", "iban", "IT60 X054 2811 1010 0000 0123 456"),
    ("IT60X0542811101000000123456", "iban", "IT60X0542811101000000123456"),
    ("DE89 3704 0044 0532 0130 00", "iban", "DE89 3704 0044 0532 0130 00"),
    # domini esclusi dal catalogo
    ("obiettivoeuropa.com", "dominio_bloccato", "obiettivoeuropa.com"),
    (
        "vedi https://www.obiettivoeuropa.com/bandi",
        "dominio_bloccato",
        "https://www.obiettivoeuropa.com/bandi",
    ),
]


@pytest.mark.parametrize(("testo", "tipo", "estratto"), POSITIVI)
def test_contatti_bloccanti_anche_nominativi(testo, tipo, estratto):
    for anonima in (False, True):
        rilievi = trova_rilievi(testo, None, anonima=anonima)
        assert [(r.tipo, r.estratto, r.bloccante) for r in rilievi] == [(tipo, estratto, True)]


# --------------------------------------------------------- falsi positivi

NEGATIVI = [
    "ISO 9001:2015",
    "UNI EN ISO 14001:2015 e ISO/IEC 27001:2022",
    "ISO 9001 14001 45001",
    "Programmazione 2021-2027",
    "2014 - 2020",
    "anni 2019 2020 2021 2022",
    "ATECO 62.01",
    "ATECO 62.01.00 e 01.11.10",
    "62.01 62.02 62.09",
    "€ 250.000",
    "fatturato 2.500.000 €",
    "1.250.000,00 euro",
    "fatturato 300000000 euro",
    "budget di 3.000.000",
    "+30%",
    "crescita del +300000 %",
    "0,5%",
    "Horizon Europe",
    "Horizon 2020 e H2020-SC5-2019",
    "PNRR M4C2 I1.1",
    "Industria 4.0",
    "01/05/2023",
    "30.06.2025",
    "03/2024",
    "dal 1998",
    "CAP 20121",
    "150 dipendenti",
    "10-49 dipendenti",
    "3 5 7 9 11 13 15",
    "TRL 4-6",
    "ore 9:00-18:00",
    "D.Lgs. 81/2008",
    "Reg. (UE) 2021/1060",
    "CUP B51H22000120006",
    "codice articolo AB0212345678",
    "lotto L3471234567Z",
    "Grant Agreement n. 101070123",
    "OG1 classifica III",
    "p.es. automotive",
    "S.r.l. e S.p.A.",
    "Ph.D. e M.Sc.",
    "node.js e ASP.NET",
    "e-commerce B2B 5G Wi-Fi R&D",
    "sviluppo.Il progetto",
    "Austria (AT) e Francia (FR)",
    "Researchers at Politecnico di Milano",
    "competenze @ livello europeo",
    # numeri di registro, fasce orarie e abbreviazioni (non sono recapiti)
    "Iscrizione Albo Gestori Ambientali n. MI/012345",
    "Iscrizione Albo Gestori Ambientali n. 012345",
    "Accreditamento regionale n. 0456789 per la formazione",
    "Autorizzazione AUA prot. 0123456/2021",
    "Certificato n. 0123456",
    "Iscrizione REA MI-1234567",
    "Reparto aperto 08-12 14-18",
    "tre turni 06-14 / 14-22 / 22-06",
    "orario 08.30-12.30 e 14.30-18.30",
    "progetti (ad.es. Horizon)",
    "fasi 1 2 3 4 5 6 7 8 9",
]


@pytest.mark.parametrize("testo", NEGATIVI)
def test_falsi_positivi_esclusi(testo):
    assert trova_rilievi(testo, None, anonima=False) == []


def test_data_seguita_da_telefono():
    assert estratti("il 01.05.2023 chiama 3471234567") == [("telefono", "3471234567")]


def test_piu_rilievi_in_ordine_e_senza_doppioni():
    testo = "Mail a@b.it, tel 02 1234567, ancora a@b.it e www.x.it"
    assert estratti(testo) == [("email", "a@b.it"), ("telefono", "02 1234567"), ("url", "www.x.it")]


def test_email_vince_sul_dominio_contenuto():
    assert tipi("info@acme.it") == ["email"]


def test_iban_vince_sulle_cifre_interne():
    assert tipi("IT60 X054 2811 1010 0000 0123 456") == ["iban"]


def test_iban_con_checksum_sbagliato_non_e_un_iban():
    # le cifre restano comunque un recapito numerico («0044 …» ha la forma di
    # un prefisso internazionale): basta che non sia etichettato come IBAN
    assert "iban" not in tipi("DE00 3704 0044 0532 0130 00")
    assert tipi("AB12 CDEF GHIJ KLMN OPQR") == []


def _con_checksum(paese: str, bban: str) -> str:
    """IBAN con cifre di controllo valide (mod 97) per un paese qualunque."""
    numero = int("".join(str(int(ch, 36)) for ch in bban + paese + "00"))
    return f"{paese}{98 - numero % 97:02d}{bban}"


def test_iban_solo_per_paesi_noti():
    noto = _con_checksum("IT", "X0542811101000000123456")
    assert noto == "IT60X0542811101000000123456"
    ignoto = _con_checksum("QQ", "X0542811101000000123456")
    assert "iban" not in tipi(f"conto {ignoto} presso")


def test_testi_vuoti_o_non_stringhe():
    for valore in (None, "", "   ", 42, ["a@b.it"]):
        assert trova_rilievi(valore, None, anonima=True) == []  # type: ignore[arg-type]
    assert anonimizza(None, None) == ("", [])
    assert anonimizza("", None) == ("", [])


def test_estratto_troncato():
    url = "https://acme.it/" + "x" * 200
    (rilievo,) = trova_rilievi(url, None, anonima=False)
    assert len(rilievo.estratto) == anon.MAX_ESTRATTO
    assert rilievo.estratto.endswith("…")


def test_etichette_per_ogni_tipo():
    assert set(ETICHETTE_RILIEVO) == set(anon.TipoRilievo.__args__)
    assert not ha_bloccanti([Rilievo("persona", "Rossi", False)])
    assert ha_bloccanti([Rilievo("persona", "Rossi", False), Rilievo("url", "a.it", True)])


# ------------------------------------------------------- identificativi

COMPANY = {
    "ragione_sociale": "Rossi Meccanica S.r.l.",
    "partita_iva": "01234567897",
    "codice_fiscale": "01234567897",
    "sito_web": "https://www.rossimeccanica.it/chi-siamo",
}
COMPANY_DATA = {
    "denominazione": "OFFICINE ROSSI MECCANICA SRL",
    "piva_fetched": "01234567897",
    "raw": {
        "companyDetails": {"vatCode": "01234567897", "taxCode": "01234567897"},
        "webAndSocial": {"website": "www.rm-group.eu"},
    },
}
PEOPLE = [
    {"kind": "manager", "nome": "Mario", "cognome": "Bianchi"},
    {"kind": "shareholder", "nome": "Anna", "cognome": "De Luca"},
    {"kind": "shareholder", "denominazione": "Holding Srl", "cognome": None},
    {"kind": "auditor", "cognome": "Re"},
    {"kind": "manager", "cognome": "D'Angelo"},
]


@pytest.fixture()
def ident() -> Identificativi:
    return identificativi_azienda(COMPANY, COMPANY_DATA, PEOPLE)


def test_identificativi_azienda(ident):
    assert ident.ragione_sociale == "rossi meccanica"
    assert ident.denominazione == "officine rossi meccanica"
    assert ident.partite_iva == ("01234567897",)
    assert ident.codici_fiscali == ("01234567897",)
    assert ident.domini == ("rossimeccanica.it", "rm-group.eu")
    # «Re» è troppo corto per essere cercato; i soci persona giuridica non sono cognomi
    assert ident.cognomi == ("bianchi", "de luca", "d angelo")


def test_identificativi_senza_dati_del_registro():
    ident = identificativi_azienda({"ragione_sociale": "Alfa S.p.A."}, None, None)
    assert ident == Identificativi(ragione_sociale="alfa")


@pytest.mark.parametrize(
    ("nome", "atteso"),
    [
        ("Rossi Meccanica S.r.l.", "rossi meccanica"),
        ("ROSSI MECCANICA SRL", "rossi meccanica"),
        ("Rossi & C. S.n.c.", "rossi"),
        ("Soc. Coop. Il Sole a r.l.", "il sole"),
        ("F.lli Rossi S.p.A. unipersonale in liquidazione", "f lli rossi"),
        ("Società Agricola Verdi S.S.", "verdi"),
        ("Caffè Bianchi Società a responsabilità limitata semplificata", "caffe bianchi"),
        ("Vitamina C Srl", "vitamina c"),
        ("A.B. Srl", None),
        ("123 S.r.l.", None),
        (None, None),
    ],
)
def test_nome_senza_forma_legale(nome, atteso):
    assert nome_senza_forma_legale(nome) == atteso


@pytest.mark.parametrize(
    ("url", "atteso"),
    [
        ("https://www.Acme.it/chi-siamo", "acme.it"),
        ("acme.it", "acme.it"),
        ("http://shop.acme.co.uk:8080/x", "shop.acme.co.uk"),
        ("www2.acme.eu", "acme.eu"),
        ("non un sito", None),
        ("localhost", None),
        (None, None),
    ],
)
def test_dominio_da_url(url, atteso):
    assert dominio_da_url(url) == atteso


ANONIMI = [
    ("Siamo la Rossi Meccanica di Brescia", "ragione_sociale", "Rossi Meccanica"),
    ("ROSSI-MECCANICA è leader", "ragione_sociale", "ROSSI-MECCANICA"),
    ("Officine Rossi Meccanica, dal 1960", "ragione_sociale", "Officine Rossi Meccanica"),
    # l'estratto ha gli spazi compattati
    ("Caso: rossi   meccanica", "ragione_sociale", "rossi meccanica"),
    ("P.IVA 012 3456 7897", "piva_cf", "012 3456 7897"),
    ("codice 0123-4567-897", "piva_cf", "0123-4567-897"),
    ("rossimeccanica.it", "dominio_azienda", "rossimeccanica.it"),
    ("vedi Rossimeccanica.IT", "dominio_azienda", "Rossimeccanica.IT"),
    ("https://www.rm-group.eu/x", "dominio_azienda", "https://www.rm-group.eu/x"),
]


@pytest.mark.parametrize(("testo", "tipo", "estratto"), ANONIMI)
def test_identificativi_bloccanti_se_anonima(ident, testo, tipo, estratto):
    rilievi = trova_rilievi(testo, ident, anonima=True)
    assert [(r.tipo, r.estratto, r.bloccante) for r in rilievi] == [(tipo, estratto, True)]


def test_nominativo_puo_citare_il_proprio_nome(ident):
    assert trova_rilievi("Siamo la Rossi Meccanica di Brescia", ident, anonima=False) == []
    # ma i contatti restano vietati anche a chi mostra il nome
    assert tipi("rossimeccanica.it", ident, anonima=False) == ["url"]


def test_nome_a_confini_di_parola(ident):
    assert trova_rilievi("Rossi Meccanicaaa e Grossi Meccanica", ident, anonima=True) == []


def test_cognomi_solo_avvisi(ident):
    testo = "Lavoriamo con Bianchi e De Luca; D'Angelo ha fondato"
    rilievi = trova_rilievi(testo, ident, anonima=True)
    assert [(r.tipo, r.estratto, r.bloccante) for r in rilievi] == [
        ("persona", "Bianchi", False),
        ("persona", "De Luca", False),
        ("persona", "D'Angelo", False),
    ]
    assert not ha_bloccanti(rilievi)
    assert trova_rilievi("Bianchini e Bianchetti", ident, anonima=True) == []
    assert trova_rilievi("Lavoriamo con Bianchi", ident, anonima=False) == []


def test_accenti_e_maiuscole_insensibili():
    ident = identificativi_azienda(
        {"ragione_sociale": "Caffè Né Srl"}, None, [{"cognome": "Niccolò"}]
    )
    assert estratti("il CAFFE NE di NICCOLO", ident, anonima=True) == [
        ("ragione_sociale", "CAFFE NE"),
        ("persona", "NICCOLO"),
    ]


def test_normalizza():
    assert normalizza("  Caffè-Bianchi, S.r.l. ") == "caffe bianchi s r l"
    assert normalizza("ﬁnanza") == "finanza"
    assert normalizza(None) == ""


# ----------------------------------------------------------- anonimizza


def test_anonimizza_toglie_identificativi_e_contatti(ident):
    testo = (
        "La Rossi Meccanica (www.rossimeccanica.it, info@rossimeccanica.it, tel 030 1234567) "
        "lavora con Bianchi."
    )
    pulito, rimossi = anonimizza(testo, ident)
    assert pulito == (
        f"La {SOSTITUTO} ({SOSTITUTO}, {SOSTITUTO}, tel {SOSTITUTO}) lavora con Bianchi."
    )
    assert rimossi == [
        "Rossi Meccanica",
        "www.rossimeccanica.it",
        "info@rossimeccanica.it",
        "030 1234567",
    ]


def test_anonimizza_senza_ident_toglie_solo_i_contatti():
    pulito, rimossi = anonimizza("Rossi Meccanica, mario [at] acme [dot] it", None)
    assert pulito == f"Rossi Meccanica, {SOSTITUTO}"
    assert rimossi == ["mario [at] acme [dot] it"]


def test_anonimizza_senza_rilievi_restituisce_il_testo():
    testo = "Sviluppo software e ISO 9001:2015 dal 2019"
    assert anonimizza(testo, None) == (testo, [])


@pytest.mark.parametrize("testo", [t for t, _, _ in POSITIVI] + [t for t, _, _ in ANONIMI])
def test_dopo_anonimizza_nessun_bloccante(ident, testo):
    pulito, rimossi = anonimizza(testo, ident)
    assert rimossi
    assert not ha_bloccanti(trova_rilievi(pulito, ident, anonima=True))


# ------------------------------------------------------ linearità e regex

OSTILI = {
    "lettere": "a" * 100_000,
    "cifre": "0" * 100_000,
    "cifre_singole": "1 " * 50_000,
    "coppie_di_cifre": "12 " * 35_000,
    "trattini": "-1" * 50_000,
    "chiocciole": "a@" * 50_000,
    "punti": "a." * 50_000,
    "domini_corti": "a.it " * 20_000,
    "offuscamenti": "[at]" * 25_000,
    "parole_at": "x at y dot it " * 7_000,
    "schemi": "http:" * 20_000,
    "iban": "IT60 " * 20_000,
    "handle": "@aa " * 25_000,
    "telefoni": "02 1234567, " * 8_000,
    "parentesi": "(" * 50_000 + ")" * 50_000,
    "parentesi_aperte": "(" * 100_000,
    "parentesi_chiuse": "1" + ")" * 100_000,
    "misto": "Rossi Meccanica 02 1234567 a@b.it www.x.it IT60X0542811101000000123456 " * 1_500,
}


@pytest.mark.parametrize("nome", list(OSTILI))
def test_stringhe_ostili_in_tempo_lineare(ident, nome):
    testo = OSTILI[nome]
    inizio = time.perf_counter()
    trova_rilievi(testo, ident, anonima=True)
    anonimizza(testo, ident)
    # ~100.000 caratteri: con un backtracking quadratico servirebbero minuti.
    assert time.perf_counter() - inizio < 5.0, nome


def _regex_del_modulo():
    return [valore for valore in vars(anon).values() if isinstance(valore, re.Pattern)]


def _gruppi_ripetuti(pattern: str) -> bool:
    """True se un gruppo `(...)` è seguito da `+`, `*` o `{`: classi di
    caratteri e caratteri escapati si tolgono prima di cercare."""
    senza_classi = re.sub(r"\[(?:\\.|[^\]\\])*\]", "C", pattern)
    senza_escape = re.sub(r"\\.", "E", senza_classi)
    return re.search(r"\)[+*{]", senza_escape) is not None


def test_controllo_dei_gruppi_ripetuti():
    assert _gruppi_ripetuti(r"(a+)+")
    assert _gruppi_ripetuti(r"(?:\.[a-z]{1,63}){1,8}")
    assert not _gruppi_ripetuti(r"[0-9()+./\-]+")
    assert not _gruppi_ripetuti(r"\(?[0-9]+\)+")
    assert not _gruppi_ripetuti(r"(?:IT)?[0-9]{11}")


def test_nessuna_regex_con_gruppi_ripetuti_o_spazi_ripetuti():
    """Niente quantificatori annidati: i gruppi possono essere solo opzionali
    (`?`), mai `+`, `*` o `{n,m}`; e nessun `\\s*`/`\\s+`."""
    regex = _regex_del_modulo()
    assert len(regex) >= 10
    for r in regex:
        assert not _gruppi_ripetuti(r.pattern), r.pattern
        assert "\\s*" not in r.pattern, r.pattern
        assert "\\s+" not in r.pattern, r.pattern


def test_regex_dinamica_con_separatori_lineare():
    regex = anon._regex_con_separatori("01234567897")
    assert not _gruppi_ripetuti(regex.pattern)
    assert regex.search("P.IVA 012.345.678.97") is not None
    assert regex.search("P.IVA 1012 345 678 97") is None


# --------------------------------------------- forma canonica (WP6, C7)

NASCOSTI = [
    # contatti che si leggono solo nella forma canonica
    ("Contattaci: 347​1234567", "telefono"),
    ("Chiamate il 347 123 4567", "telefono"),
    ("Chiamate il ３４７１２３４５６７", "telefono"),
    ("Scrivete a mario​@​gmail​.com", "email"),
    ("Scrivete a mario＠gmail．com", "email"),
    ("Vedi www​.altrosito⁠.it", "url"),
]
NASCOSTI_ANONIMI = [
    ("Siamo la Ros​si Meccanica di Brescia", "ragione_sociale"),
    ("Siamo la RossiMeccanica di Brescia", "ragione_sociale"),
    ("Le OFFICINEROSSIMECCANICA", "ragione_sociale"),
    ("Vedi rossimeccanica​.it", "dominio_azienda"),
    ("P.IVA ０１２３４５６７８９７", "piva_cf"),
]


def test_senza_invisibili_e_forma_canonica():
    assert anon.senza_invisibili("a​b c d­﻿e") == "ab c\nde"
    # i caratteri visibili restano: niente NFKC nel testo da salvare
    assert anon.senza_invisibili("1º ３") == "1º ３"
    assert anon.forma_canonica("３４７​1") == "3471"
    assert anon.forma_canonica("ﬁnanza x") == "finanza x"


def test_forma_canonica_unica_per_call_e_accesso():
    """Le call e il modulo d'accesso usano le stesse funzioni (niente copie
    che possano divergere)."""
    from app.schemas import partner_call
    from app.services import partenariato_accesso

    assert partner_call.forma_canonica is anon.forma_canonica
    assert partner_call.senza_invisibili is anon.senza_invisibili
    assert partenariato_accesso.forma_canonica is anon.forma_canonica
    assert partenariato_accesso.MIN_NOME_COMPATTO == anon.MIN_NOME_COMPATTO


@pytest.mark.parametrize(("testo", "tipo"), NASCOSTI)
@pytest.mark.parametrize("anonima", [True, False])
def test_contatti_nascosti_sempre_bloccanti(ident, testo, tipo, anonima):
    rilievi = trova_rilievi(testo, ident, anonima=anonima)
    assert tipo in {r.tipo for r in rilievi if r.bloccante}
    # l'estratto è quello della forma canonica, senza caratteri invisibili
    assert all("​" not in r.estratto and "⁠" not in r.estratto for r in rilievi)


@pytest.mark.parametrize(("testo", "tipo"), NASCOSTI_ANONIMI)
def test_identificativi_nascosti_bloccanti_se_anonima(ident, testo, tipo):
    assert tipo in {r.tipo for r in trova_rilievi(testo, ident, anonima=True) if r.bloccante}


def test_nome_attaccato_solo_anonimo_e_abbastanza_lungo(ident):
    assert trova_rilievi("Siamo la RossiMeccanica", ident, anonima=False) == []
    corto = Identificativi(ragione_sociale="alfa beta")  # «alfabeta»: 8 caratteri
    cortissimo = Identificativi(ragione_sociale="ab cd")
    assert tipi("Siamo Alfabeta", corto, anonima=True) == ["ragione_sociale"]
    assert trova_rilievi("Siamo Abcd", cortissimo, anonima=True) == []
    # a confini di parola, come il nome con gli spazi
    assert trova_rilievi("Siamo Alfabetari", corto, anonima=True) == []


def test_nessun_doppione_tra_nome_attaccato_e_sito(ident):
    # il dominio contiene il nome attaccato: un solo rilievo, quello del sito
    assert estratti("rossimeccanica.it", ident, anonima=True) == [
        ("dominio_azienda", "rossimeccanica.it")
    ]


def test_anonimizza_esce_senza_invisibili():
    pulito, rimossi = anonimizza("Partner​ per la ‮logistica", None)
    assert (pulito, rimossi) == ("Partner per la logistica", [])
    # nessun contatto: i caratteri visibili restano quelli dell'utente
    assert anonimizza("Il 1º classificato, 20 m²", None) == ("Il 1º classificato, 20 m²", [])


@pytest.mark.parametrize(
    ("testo", "atteso"),
    [
        ("Contattaci: 333​1234567", f"Contattaci: {SOSTITUTO}"),
        ("Contattaci: ３３３１２３４５６７",
         f"Contattaci: {SOSTITUTO}"),
        ("Scrivi a mario＠gmail．com", f"Scrivi a {SOSTITUTO}"),
        ("Siamo la Ros​si Meccanica", f"Siamo la {SOSTITUTO}"),
        ("Siamo la RossiMeccanica", f"Siamo la {SOSTITUTO}"),
        ("Vedi rossimeccanica​.it", f"Vedi {SOSTITUTO}"),
    ],
)
def test_anonimizza_sulla_forma_canonica(ident, testo, atteso):
    pulito, rimossi = anonimizza(testo, ident)
    assert pulito == atteso and rimossi
    assert not ha_bloccanti(trova_rilievi(pulito, ident, anonima=True))


@pytest.mark.parametrize("testo", [t for t, _ in NASCOSTI + NASCOSTI_ANONIMI])
def test_dopo_anonimizza_nessun_bloccante_anche_nascosto(ident, testo):
    pulito, rimossi = anonimizza(testo, ident)
    assert rimossi
    assert not ha_bloccanti(trova_rilievi(pulito, ident, anonima=True))


def test_invisibili_ostili_in_tempo_lineare(ident):
    testo = "3​4​7１ " * 20_000
    inizio = time.perf_counter()
    trova_rilievi(testo, ident, anonima=True)
    anonimizza(testo, ident)
    assert time.perf_counter() - inizio < 5.0
