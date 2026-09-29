"""Collegamenti societari (services/partenariato_collegamenti.py, WP6 M2/Q17).

Proprietà difese:
- normalizzazione di CF/P.IVA e ragioni sociali (forme legali, punteggiatura,
  accenti) e chiavi HMAC confrontabili tra aziende diverse;
- chiavi da `company_profiles`, `raw` IT-full e `company_people` (fixture
  SINTETICHE in stile IT-full), senza contare due volte soci ed esponenti;
- soglie Q17 certo/possibile in ogni combinazione, simmetriche;
- solo con il flag e solo per le aziende idonee; marker tolto per primo e
  scritto per ultimo (fail-closed); backfill con limite e pulizia;
- MAI un CF, una P.IVA o un nome in chiaro negli argomenti delle scritture,
  nei filtri o nei log.
"""

import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.privacy import hmac_dominio
from app.services import partenariato_collegamenti as coll
from app.services.openapi_mapping import extract_people
from app.services.partenariato_collegamenti import (
    ALGORITMO_VERSIONE,
    DOMINIO_HMAC,
    ChiaveCollegamento,
    chiavi_collegamento,
    chiavi_da_righe,
    marker_aggiornato,
    normalizza_cf,
    normalizza_nome,
    valuta_collegamento,
)

# Dati sintetici (nessun dato reale): P.IVA di 11 cifre e CF di persona di
# 16 caratteri inventati, nomi di fantasia.
PIVA_ALFA = "10000000011"
PIVA_BETA = "20000000022"
PIVA_GAMMA = "30000000033"
PIVA_HOLDING = "40000000044"
CF_ROSSI = "RSSMRA80A01H501U"
CF_VERDI = "VRDLCU75C12F205Z"
CF_SINDACO = "BNCGNN70E15L219K"
DATI_IN_CHIARO = (
    PIVA_ALFA, PIVA_BETA, PIVA_GAMMA, PIVA_HOLDING, CF_ROSSI, CF_VERDI, CF_SINDACO,
    "ALFA MECCANICA", "Alfa Meccanica", "HOLDING BETA", "GRUPPO BETA", "GAMMA",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    for chiave, valore in {
        "PRIMARY_SUPABASE_URL": "https://dummy.supabase.co",
        "PRIMARY_SUPABASE_SERVICE_ROLE_KEY": "k",
        "SECONDARY_SUPABASE_URL": "https://d2.supabase.co",
        "SECONDARY_SUPABASE_ANON_KEY": "k",
        "RATE_LIMIT_PEPPER": "pepe-di-test-collegamenti",
    }.items():
        monkeypatch.setenv(chiave, valore)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def raw_alfa() -> dict:
    """IT-full sintetico: soci (persona e società), partecipata, controllata,
    amministratore, sindaco, gruppo con capogruppo."""
    return {
        "companyDetails": {"companyName": "ALFA MECCANICA S.R.L.", "vatCode": PIVA_ALFA,
                           "taxCode": PIVA_ALFA},
        "shareholders": [
            {"name": "MARIO", "surname": "ROSSI", "taxCode": CF_ROSSI, "percentShare": 60},
            {"companyName": "HOLDING BETA SPA", "taxCode": PIVA_HOLDING, "percentShare": 40},
        ],
        "affiliateCompanies": [
            {"companyName": "GAMMA SRL", "percentShare": 30, "taxCode": PIVA_GAMMA},
        ],
        "subsidiaries": [
            {"companyName": "GAMMA SRL", "taxCode": PIVA_GAMMA, "town": "COSENZA",
             "province": {"code": "CS", "description": "COSENZA"}},
        ],
        "managers": [
            {"name": "LUCA", "surname": "VERDI", "taxCode": CF_VERDI,
             "isLegalRepresentative": True,
             "roles": [{"role": {"code": "AMU", "description": "Sole director"},
                        "roleStartDate": "2020-01-01T00:00:00"}]},
        ],
        "auditors": [{"name": "GIOVANNI", "surname": "BIANCHI", "taxCode": CF_SINDACO}],
        "corporateGroups": {
            "belongsToGroup": True,
            "groupName": "GRUPPO BETA",
            "holdingCompanyName": "HOLDING BETA S.P.A.",
            "nationalParentCompany": {"companyName": "HOLDING BETA SPA", "town": "ROMA"},
        },
    }


def riga_alfa() -> dict:
    return {"id": "c-alfa", "partita_iva": PIVA_ALFA, "codice_fiscale": PIVA_ALFA,
            "ragione_sociale": "Alfa Meccanica Srl"}


def k(prefisso: str, valore: str) -> str:
    return hmac_dominio(DOMINIO_HMAC, f"{prefisso}:{valore}")


def per_tipo(chiavi) -> dict[str, dict[str, Decimal | None]]:
    out: dict[str, dict[str, Decimal | None]] = {}
    for c in chiavi:
        out.setdefault(c.tipo, {})[c.chiave] = c.quota
    return out


# ------------------------------------------------------------ normalizzazione


@pytest.mark.parametrize(
    ("valore", "atteso"),
    [
        (" 012 345 678 97 ", "01234567897"),
        ("IT01234567897", "01234567897"),
        ("it 0123-4567-897", "01234567897"),
        ("rssmra80a01h501u", "RSSMRA80A01H501U"),
        ("RSS MRA 80A01 H501U", "RSSMRA80A01H501U"),
        ("０１２３４５６７８９７", "01234567897"),
        (1234567897, "01234567897"),  # intero JSON: zeri iniziali persi
        ("00000000000", None),
        ("123", None),
        ("DE123456789", None),
        ("1234567890123456", None),  # 16 cifre: non è un CF di persona
        ("", None),
        (None, None),
        (True, None),
        (12.5, None),
    ],
)
def test_normalizza_cf(valore, atteso):
    assert normalizza_cf(valore) == atteso


@pytest.mark.parametrize(
    ("valore", "atteso"),
    [
        ("Rossi Meccanica S.r.l.", "ROSSI MECCANICA"),
        ("ROSSI MECCANICA SRL", "ROSSI MECCANICA"),
        ("Rossi Meccanica s.p.a.", "ROSSI MECCANICA"),
        ("ROSSI MECCANICA S.R.L.S.", "ROSSI MECCANICA"),
        ("Rossi Meccanica SRLS", "ROSSI MECCANICA"),
        ("Rossi Meccanica S.A.S.", "ROSSI MECCANICA"),
        ("Rossi Meccanica snc", "ROSSI MECCANICA"),
        ("Rossi Meccanica S.C.A.R.L.", "ROSSI MECCANICA"),
        ("Rossi Meccanica Soc. Coop.", "ROSSI MECCANICA"),
        ("Soc. Coop. Il Sole a r.l.", "IL SOLE"),
        ("Rossi & C. S.n.c.", "ROSSI"),
        ("Rossi Meccanica S.p.A. unipersonale in liquidazione", "ROSSI MECCANICA"),
        ("Caffè-Bianchi, Società a responsabilità limitata", "CAFFE BIANCHI"),
        ("  ROSSI   MECCANICA  srl ", "ROSSI MECCANICA"),
        ("A.B. Srl", None),  # troppo corto: collegherebbe aziende a caso
        ("123 S.r.l.", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizza_nome(valore, atteso):
    assert normalizza_nome(valore) == atteso


def test_forme_legali_diverse_stessa_chiave():
    varianti = ("Beta Srl", "BETA S.R.L.", "beta s.r.l.s.", "Beta SpA", "BETA SOC. COOP.")
    assert {normalizza_nome(v) for v in varianti} == {"BETA"}


# ------------------------------------------------------------------- chiavi


class TestChiavi:
    def test_chiavi_da_company_row_raw_e_people(self):
        raw = raw_alfa()
        chiavi = chiavi_collegamento(riga_alfa(), raw, extract_people(raw))
        tipi = per_tipo(chiavi)
        assert tipi["identita"] == {k("cf", PIVA_ALFA): None}  # profilo e registro coincidono
        assert tipi["nome"] == {k("nome", "ALFA MECCANICA"): None}
        assert tipi["socio"] == {k("cf", CF_ROSSI): Decimal("60.000"),
                                 k("cf", PIVA_HOLDING): Decimal("40.000")}
        assert tipi["partecipata"] == {k("cf", PIVA_GAMMA): Decimal("30.000")}
        assert tipi["controllata"] == {k("cf", PIVA_GAMMA): None}
        assert tipi["esponente"] == {k("cf", CF_VERDI): None}
        assert tipi["gruppo"] == {k("gruppo", "GRUPPO BETA"): None}
        # holdingCompanyName e nationalParentCompany: stesso nome → una chiave,
        # con il prefisso di `nome` (si confronta con la ragione sociale altrui)
        assert tipi["capogruppo"] == {k("nome", "HOLDING BETA"): None}

    def test_sindaci_esclusi(self):
        raw = raw_alfa()
        chiavi = chiavi_collegamento(riga_alfa(), raw, extract_people(raw))
        assert k("cf", CF_SINDACO) not in {c.chiave for c in chiavi}

    def test_solo_hmac_nessun_dato_in_chiaro(self):
        raw = raw_alfa()
        chiavi = chiavi_collegamento(riga_alfa(), raw, extract_people(raw))
        assert chiavi and all(HEX64.match(c.chiave) for c in chiavi)
        testo = repr(chiavi)
        for dato in DATI_IN_CHIARO:
            assert dato not in testo

    def test_people_e_raw_non_si_sommano(self):
        """company_people è estratto dallo stesso raw: con o senza le righe
        di company_people le chiavi (e le quote dei soci) sono le stesse."""
        raw = raw_alfa()
        con_people = chiavi_collegamento(riga_alfa(), raw, extract_people(raw))
        senza_people = chiavi_collegamento(riga_alfa(), raw, [])
        assert con_people == senza_people

    def test_deterministiche_e_ordinate(self):
        raw = raw_alfa()
        prima = chiavi_collegamento(riga_alfa(), raw, extract_people(raw))
        assert prima == chiavi_collegamento(riga_alfa(), copy.deepcopy(raw), extract_people(raw))
        assert prima == sorted(prima, key=lambda c: (c.tipo, c.chiave))

    def test_stesso_cf_stessa_chiave_tra_aziende(self):
        """Il confronto funziona perché lo stesso soggetto dà la stessa chiave
        in aziende diverse, qualunque sia la forma in cui è scritto."""
        a = chiavi_collegamento({"partita_iva": "IT " + PIVA_BETA}, None, None)
        b = chiavi_collegamento(None, {"companyDetails": {"vatCode": PIVA_BETA}}, None)
        assert a == b == [ChiaveCollegamento("identita", k("cf", PIVA_BETA))]

    def test_solo_company_row(self):
        chiavi = chiavi_collegamento(riga_alfa(), None, None)
        assert {c.tipo for c in chiavi} == {"identita", "nome"}

    def test_ditta_individuale_cf_di_persona(self):
        chiavi = chiavi_collegamento(
            {"partita_iva": PIVA_BETA, "codice_fiscale": CF_ROSSI,
             "ragione_sociale": "Rossi Mario Impresa Individuale"}, None, None)
        assert per_tipo(chiavi)["identita"] == {k("cf", PIVA_BETA): None,
                                                k("cf", CF_ROSSI): None}

    def test_quote_sommate_per_lo_stesso_socio(self):
        people = [
            {"kind": "shareholder", "codice_fiscale": CF_ROSSI, "quota_percentuale": 30},
            {"kind": "shareholder", "codice_fiscale": CF_ROSSI, "quota_percentuale": "25.5"},
            {"kind": "shareholder", "codice_fiscale": CF_VERDI, "quota_percentuale": 90},
            {"kind": "shareholder", "codice_fiscale": CF_VERDI, "quota_percentuale": 90},
        ]
        soci = per_tipo(chiavi_collegamento(None, None, people))["socio"]
        assert soci[k("cf", CF_ROSSI)] == Decimal("55.500")
        assert soci[k("cf", CF_VERDI)] == Decimal("100.000")  # al massimo 100

    @pytest.mark.parametrize(
        ("quote", "atteso"),
        [
            ((10, None), None),  # parte ignota e somma nota sotto soglia: ignota
            ((30, None), Decimal("30.000")),  # ≥ 25 già con la sola parte nota
            ((None,), None),
            (("25,5",), Decimal("25.500")),
            ((150,), None),  # fuori scala: ignota, mai 100
            ((-3,), None),
            (("abc",), None),
            ((True,), None),
        ],
    )
    def test_quote_ignote_o_non_plausibili(self, quote, atteso):
        people = [{"kind": "shareholder", "codice_fiscale": CF_ROSSI, "quota_percentuale": q}
                  for q in quote]
        assert per_tipo(chiavi_collegamento(None, None, people))["socio"] == {
            k("cf", CF_ROSSI): atteso}

    def test_soci_senza_cf(self):
        raw = {"shareholders": [
            {"name": "MARIO", "surname": "ROSSI", "percentShare": 50},  # persona: ignorata
            {"companyName": "Holding Beta S.p.A.", "percentShare": 50},  # società: per nome
        ]}
        assert per_tipo(chiavi_collegamento(None, raw, extract_people(raw)))["socio"] == {
            k("nome", "HOLDING BETA"): Decimal("50.000")}

    def test_gruppo_solo_se_appartiene(self):
        raw = raw_alfa()
        raw["corporateGroups"]["belongsToGroup"] = False
        tipi = {c.tipo for c in chiavi_collegamento(riga_alfa(), raw, [])}
        assert not tipi & {"gruppo", "capogruppo"}

    def test_dati_malformati_ignorati(self):
        raw = {
            "companyDetails": "non un oggetto",
            "shareholders": "non una lista",
            "affiliateCompanies": [None, 3, {"taxCode": "123", "companyName": "AB"}],
            "subsidiaries": [{"taxCode": None, "companyName": None}],
            "managers": [{"taxCode": "corto"}],
            "corporateGroups": ["x"],
        }
        assert chiavi_collegamento({"partita_iva": None}, raw, [None, "x"]) == []

    def test_chiavi_da_righe(self):
        buona = {"tipo": "socio", "chiave": "a" * 64, "quota": 60.5}
        righe = [buona, {"tipo": "ignoto", "chiave": "a" * 64},
                 {"tipo": "nome", "chiave": "corta"}, None,
                 {"tipo": "identita", "chiave": "b" * 64, "quota": None}]
        assert chiavi_da_righe(righe) == (
            ChiaveCollegamento("socio", "a" * 64, Decimal("60.500")),
            ChiaveCollegamento("identita", "b" * 64, None),
        )


# -------------------------------------------------------------- soglie Q17


def azienda(piva: str, nome: str, *, cf: str | None = None, soci=(), partecipate=(),
            controllate=(), esponenti=(), sindaci=(), gruppo: str | None = None,
            capogruppo: str | None = None) -> list[ChiaveCollegamento]:
    """Chiavi di un'azienda sintetica, passando da raw IT-full e
    company_people come all'import. `soci`/`partecipate`/`controllate`:
    coppie (cf o None, quota) o terne (cf o None, quota, denominazione)."""

    def voce(tupla):
        codice, quota, *resto = tupla
        denominazione = resto[0] if resto else None
        out = {"percentShare": quota}
        if codice:
            out["taxCode"] = codice
        if denominazione:
            out["companyName"] = denominazione
        return out

    raw: dict = {"companyDetails": {"companyName": nome, "vatCode": piva, "taxCode": cf or piva}}
    raw["shareholders"] = [voce(s) for s in soci]
    raw["affiliateCompanies"] = [voce(p) for p in partecipate]
    raw["subsidiaries"] = [voce(c) for c in controllate]
    raw["managers"] = [{"taxCode": e, "name": "X", "surname": "Y"} for e in esponenti]
    raw["auditors"] = [{"taxCode": s, "name": "X", "surname": "Z"} for s in sindaci]
    if gruppo or capogruppo:
        raw["corporateGroups"] = {"belongsToGroup": True, "groupName": gruppo,
                                  "holdingCompanyName": capogruppo}
    return chiavi_collegamento(
        {"partita_iva": piva, "codice_fiscale": cf or piva, "ragione_sociale": nome},
        raw, extract_people(raw),
    )


def B(**kw):
    return azienda(PIVA_BETA, "Beta Servizi Srl", **kw)


def A(**kw):
    return azienda(PIVA_ALFA, "Alfa Meccanica Srl", **kw)


CASI_Q17 = {
    "nessun_legame": (
        lambda: A(),
        lambda: B(),
        None,
    ),
    "stessa_identita": (
        lambda: A(),
        lambda: azienda(PIVA_ALFA, "Altro Nome Srl"),
        "certo",
    ),
    "stesso_nome_non_basta": (
        lambda: A(),
        lambda: azienda(PIVA_BETA, "ALFA MECCANICA S.P.A."),
        None,
    ),
    "stesso_gruppo": (
        lambda: A(gruppo="Gruppo Omega"),
        lambda: B(gruppo="GRUPPO OMEGA S.R.L."),
        "certo",
    ),
    "gruppi_diversi": (
        lambda: A(gruppo="Gruppo Omega"),
        lambda: B(gruppo="Gruppo Sigma"),
        None,
    ),
    "partecipata_25": (
        lambda: A(partecipate=[(PIVA_BETA, 25)]),
        lambda: B(),
        "certo",
    ),
    "partecipata_100": (
        lambda: A(partecipate=[(PIVA_BETA, 100)]),
        lambda: B(),
        "certo",
    ),
    "partecipata_sotto_25": (
        lambda: A(partecipate=[(PIVA_BETA, "24.999")]),
        lambda: B(),
        None,
    ),
    "partecipata_quota_ignota": (
        lambda: A(partecipate=[(PIVA_BETA, None)]),
        lambda: B(),
        "possibile",
    ),
    "partecipata_per_nome": (
        lambda: A(partecipate=[(None, 40, "BETA SERVIZI S.R.L.")]),
        lambda: B(),
        "certo",
    ),
    "partecipata_per_nome_ignota": (
        lambda: A(partecipate=[(None, None, "Beta Servizi")]),
        lambda: B(),
        "possibile",
    ),
    "controllata_ignota": (
        lambda: A(controllate=[(PIVA_BETA, None)]),
        lambda: B(),
        "possibile",
    ),
    "controllata_sotto_25": (
        lambda: A(controllate=[(PIVA_BETA, 10)]),
        lambda: B(),
        "possibile",
    ),
    "controllata_51": (
        lambda: A(controllate=[(PIVA_BETA, 51)]),
        lambda: B(),
        "certo",
    ),
    "socio_che_e_l_altra_30": (
        lambda: A(),
        lambda: B(soci=[(PIVA_ALFA, 30)]),
        "certo",
    ),
    "socio_che_e_l_altra_10": (
        lambda: A(),
        lambda: B(soci=[(PIVA_ALFA, 10)]),
        None,
    ),
    "socio_che_e_l_altra_ignota": (
        lambda: A(),
        lambda: B(soci=[(PIVA_ALFA, None)]),
        "possibile",
    ),
    "socio_per_nome": (
        lambda: A(),
        lambda: B(soci=[(None, 26, "Alfa Meccanica S.r.l.")]),
        "certo",
    ),
    "socio_comune_60_60": (
        lambda: A(soci=[(CF_ROSSI, 60)]),
        lambda: B(soci=[(CF_ROSSI, 60)]),
        "certo",
    ),
    "socio_comune_51_51": (
        lambda: A(soci=[(CF_ROSSI, 51)]),
        lambda: B(soci=[(CF_ROSSI, 51)]),
        "certo",
    ),
    "socio_comune_50_60": (
        lambda: A(soci=[(CF_ROSSI, 50)]),
        lambda: B(soci=[(CF_ROSSI, 60)]),
        "possibile",
    ),
    "socio_comune_30_60": (
        lambda: A(soci=[(CF_ROSSI, 30)]),
        lambda: B(soci=[(CF_ROSSI, 60)]),
        "possibile",
    ),
    "socio_comune_25_25": (
        lambda: A(soci=[(CF_ROSSI, 25)]),
        lambda: B(soci=[(CF_ROSSI, 25)]),
        "possibile",
    ),
    "socio_comune_24_60": (
        lambda: A(soci=[(CF_ROSSI, "24.999")]),
        lambda: B(soci=[(CF_ROSSI, 60)]),
        None,
    ),
    "socio_comune_10_10": (
        lambda: A(soci=[(CF_ROSSI, 10)]),
        lambda: B(soci=[(CF_ROSSI, 10)]),
        None,
    ),
    "socio_comune_ignote": (
        lambda: A(soci=[(CF_ROSSI, None)]),
        lambda: B(soci=[(CF_ROSSI, None)]),
        "possibile",
    ),
    "socio_comune_ignota_60": (
        lambda: A(soci=[(CF_ROSSI, None)]),
        lambda: B(soci=[(CF_ROSSI, 60)]),
        "possibile",
    ),
    "socio_comune_ignota_10": (
        lambda: A(soci=[(CF_ROSSI, None)]),
        lambda: B(soci=[(CF_ROSSI, 10)]),
        None,
    ),
    "socio_comune_societa": (
        lambda: A(soci=[(PIVA_HOLDING, 70)]),
        lambda: B(soci=[(PIVA_HOLDING, 80)]),
        "certo",
    ),
    "soci_diversi": (
        lambda: A(soci=[(CF_ROSSI, 90)]),
        lambda: B(soci=[(CF_VERDI, 90)]),
        None,
    ),
    "esponente_comune": (
        lambda: A(esponenti=[CF_VERDI]),
        lambda: B(esponenti=[CF_VERDI]),
        "possibile",
    ),
    "esponente_che_e_l_altra": (
        lambda: A(esponenti=[CF_ROSSI]),
        lambda: azienda(PIVA_BETA, "Rossi Mario", cf=CF_ROSSI),
        "possibile",
    ),
    "sindaco_comune_escluso": (
        lambda: A(sindaci=[CF_SINDACO]),
        lambda: B(sindaci=[CF_SINDACO]),
        None,
    ),
    "esponente_e_sindaco": (
        lambda: A(esponenti=[CF_SINDACO]),
        lambda: B(sindaci=[CF_SINDACO]),
        None,
    ),
    "capogruppo_e_l_altra": (
        lambda: A(gruppo="Gruppo Omega", capogruppo="Beta Servizi S.p.A."),
        lambda: B(),
        "possibile",
    ),
    "stessa_capogruppo": (
        lambda: A(gruppo="Gruppo Omega", capogruppo="Holding Zeta Srl"),
        lambda: B(gruppo="Gruppo Sigma", capogruppo="HOLDING ZETA S.R.L."),
        "possibile",
    ),
    "certo_prevale": (
        lambda: A(soci=[(CF_ROSSI, 30)], partecipate=[(PIVA_BETA, 30)]),
        lambda: B(soci=[(CF_ROSSI, 30)]),
        "certo",
    ),
}


@pytest.mark.parametrize(("a", "b", "atteso"), CASI_Q17.values(), ids=CASI_Q17.keys())
def test_soglie_q17(a, b, atteso):
    a, b = a(), b()
    assert valuta_collegamento(a, b) == atteso
    assert valuta_collegamento(b, a) == atteso  # simmetrica


def test_soglie_ai_bordi_esatti():
    for quota, atteso in (("24.999", None), ("25", "certo"), ("25.001", "certo")):
        assert valuta_collegamento(A(partecipate=[(PIVA_BETA, quota)]), B()) == atteso
    for qa, qb, atteso in (("50", "50.001", "possibile"), ("50.001", "50.001", "certo"),
                           ("25", "24.999", None), ("25", "25", "possibile")):
        assert valuta_collegamento(A(soci=[(CF_ROSSI, qa)]), B(soci=[(CF_ROSSI, qb)])) == atteso


def test_liste_vuote():
    assert valuta_collegamento([], []) is None
    assert valuta_collegamento(A(), []) is None


def test_collegamenti_tra_come_le_coppie():
    """L'indice inverso dà le stesse coppie del confronto di tutte le coppie."""
    aziende = {
        "x": A(soci=[(CF_ROSSI, 60)], esponenti=[CF_VERDI]),
        "w": B(soci=[(CF_ROSSI, 60)]),  # socio comune > 50%: certo
        "e": azienda(PIVA_GAMMA, "Gamma Srl", esponenti=[CF_VERDI]),  # esponente: possibile
        "y": azienda(PIVA_HOLDING, "Ypsilon Srl"),  # nessun legame
        "vuota": [],
    }
    tutte = {}
    for a in aziende:
        for b in aziende:
            if a != b and (grado := valuta_collegamento(aziende[a], aziende[b])):
                tutte.setdefault(a, {})[b] = grado
    risultato = coll.collegamenti_tra(aziende)
    assert risultato == tutte
    assert risultato == {"x": {"w": "certo", "e": "possibile"}, "w": {"x": "certo"},
                         "e": {"x": "possibile"}}


# ------------------------------------------------------------------- marker


T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("marker", "fonte", "atteso"),
    [
        (None, None, False),
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": None}, None, True),
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": T0.isoformat()},
         T0.isoformat(), True),
        ({"algoritmo_versione": ALGORITMO_VERSIONE,
          "fonte_fetched_at": "2026-09-01T08:00:00.000001+00:00"}, "2026-09-01T08:00:00Z", True),
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": T0.isoformat()},
         (T0 + timedelta(seconds=1)).isoformat(), False),  # import più recente
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": None},
         T0.isoformat(), False),  # calcolato prima dell'import
        ({"algoritmo_versione": ALGORITMO_VERSIONE - 1, "fonte_fetched_at": None}, None, False),
        ({"algoritmo_versione": ALGORITMO_VERSIONE + 1, "fonte_fetched_at": None}, None, False),
        ({"algoritmo_versione": "x"}, None, False),
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": "illeggibile"},
         T0.isoformat(), False),
        ({"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": T0}, T0, True),
    ],
)
def test_marker_aggiornato(marker, fonte, atteso):
    assert marker_aggiornato(marker, fonte) is atteso


def test_marker_aggiornato_con_le_chiavi_d_identita(monkeypatch):
    # Il marker vale solo se le chiavi che la riga dell'azienda genera ADESSO
    # sono tra quelle salvate: identità cambiata senza import o chiave HMAC
    # ruotata → non aggiornato (fail-closed, il backfill ricalcola).
    from app.core.config import get_settings

    marker = {"algoritmo_versione": ALGORITMO_VERSIONE, "fonte_fetched_at": T0.isoformat()}
    salvate = chiavi_collegamento(riga_alfa(), raw_alfa(), extract_people(raw_alfa()))
    assert marker_aggiornato(marker, T0.isoformat(), azienda=riga_alfa(), chiavi=salvate)
    rinominata = {**riga_alfa(), "ragione_sociale": "Alfa Meccanica Nuova Srl"}
    assert not marker_aggiornato(marker, T0.isoformat(), azienda=rinominata, chiavi=salvate)
    altra_piva = {**riga_alfa(), "partita_iva": PIVA_BETA}
    assert not marker_aggiornato(marker, T0.isoformat(), azienda=altra_piva, chiavi=salvate)
    # senza campi d'identità nulla da confrontare
    assert marker_aggiornato(marker, T0.isoformat(), azienda={}, chiavi=())
    monkeypatch.setenv("RATE_LIMIT_PEPPER", "pepper-dopo-la-rotazione-di-prova")
    get_settings.cache_clear()
    try:
        assert not marker_aggiornato(marker, T0.isoformat(), azienda=riga_alfa(), chiavi=salvate)
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------ primario finto


class FakeQuery:
    def __init__(self, db, tabella):
        self.db, self.tabella = db, tabella
        self.op, self.payload, self.on_conflict = "select", None, None
        self.filtri: list[tuple[str, str, object]] = []
        self.ordine: str | None = None
        self.limite: int | None = None

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, **_k):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, c, v):
        self.filtri.append(("eq", c, v))
        return self

    def in_(self, c, v):
        self.filtri.append(("in", c, list(v)))
        return self

    def gt(self, c, v):
        self.filtri.append(("gt", c, v))
        return self

    def order(self, c, **_k):
        self.ordine = c
        return self

    def limit(self, n):
        self.limite = n
        return self

    def _passa(self, riga) -> bool:
        for op, c, v in self.filtri:
            valore = riga.get(c)
            if op == "eq" and valore != v:
                return False
            if op == "in" and valore not in v:
                return False
            if op == "gt" and not (valore is not None and valore > v):
                return False
        return True

    async def execute(self):
        self.db.ops.append((self.tabella, self.op, copy.deepcopy(self.payload),
                            list(self.filtri), self.on_conflict))
        guasto = self.db.guasti.get((self.tabella, self.op))
        if guasto is not None:
            raise guasto
        righe = self.db.tabelle.setdefault(self.tabella, [])
        if self.op == "select":
            out = [dict(r) for r in righe if self._passa(r)]
            if self.ordine:
                out.sort(key=lambda r: r[self.ordine])
            if self.limite is not None:
                out = out[: self.limite]
            return SimpleNamespace(data=out)
        if self.op == "delete":
            self.db.tabelle[self.tabella] = [r for r in righe if not self._passa(r)]
            return SimpleNamespace(data=[])
        nuove = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op == "insert":
            if self.tabella == "company_collegamenti":
                esistenti = {(r["company_profile_id"], r["tipo"], r["chiave"]) for r in righe}
                for r in nuove:
                    if (r["company_profile_id"], r["tipo"], r["chiave"]) in esistenti:
                        raise APIError({"message": "duplicate key", "code": "23505",
                                        "hint": None, "details": None})
            righe.extend(dict(r) for r in nuove)
            return SimpleNamespace(data=nuove)
        colonne = (self.on_conflict or "").split(",")
        for nuova in nuove:
            for riga in righe:
                if all(riga.get(c) == nuova.get(c) for c in colonne):
                    riga.update(nuova)
                    break
            else:
                righe.append(dict(nuova))
        return SimpleNamespace(data=nuove)


class FakePrimary:
    """Tabelle in memoria; `ops` registra OGNI chiamata (tabella, operazione,
    payload, filtri): serve a verificare che nessun dato in chiaro esca."""

    def __init__(self):
        self.tabelle: dict[str, list[dict]] = {}
        self.ops: list[tuple] = []
        self.guasti: dict[tuple[str, str], Exception] = {}

    def table(self, nome):
        return FakeQuery(self, nome)

    def scritture(self, tabella: str | None = None) -> list[tuple]:
        return [o for o in self.ops if o[1] != "select" and (tabella is None or o[0] == tabella)]

    def azienda(self, cid, *, visibile=False, stati_call=(), raw=None, people=None,
                fetched_at=T0.isoformat(), riga=None, deleted=False, archived=False):
        self.tabelle.setdefault("company_profiles", []).append({
            **(riga or {"partita_iva": PIVA_ALFA, "codice_fiscale": PIVA_ALFA,
                        "ragione_sociale": "Alfa Meccanica Srl"}),
            "id": cid, "deleted_at": T0.isoformat() if deleted else None,
            "archived_at": T0.isoformat() if archived else None,
        })
        self.tabelle.setdefault("company_partner_profiles", []).append(
            {"company_profile_id": cid, "visibile_come_partner": visibile})
        for n, stato in enumerate(stati_call):
            self.tabelle.setdefault("partner_calls", []).append(
                {"id": f"{cid}-call-{n}", "company_profile_id": cid, "stato": stato})
        if raw is not None:
            self.tabelle.setdefault("company_data", []).append(
                {"company_profile_id": cid, "raw": raw, "fetched_at": fetched_at})
        for persona in people if people is not None else (extract_people(raw) if raw else []):
            self.tabelle.setdefault("company_people", []).append(
                {"company_profile_id": cid, **persona})
        return self

    def marker(self, cid, versione=ALGORITMO_VERSIONE, fonte=T0.isoformat()):
        self.tabelle.setdefault("company_collegamenti_stato", []).append(
            {"company_profile_id": cid, "algoritmo_versione": versione,
             "fonte_fetched_at": fonte, "calcolato_at": T0.isoformat()})
        return self

    def chiavi_calcolate(self, cid):
        """Le chiavi che `ricostruisci` scriverebbe adesso (azienda già
        calcolata, per i test del backfill)."""
        [riga] = [r for r in self.tabelle["company_profiles"] if r["id"] == cid]
        dati = next((r for r in self.tabelle.get("company_data", [])
                     if r["company_profile_id"] == cid), {})
        persone = [p for p in self.tabelle.get("company_people", [])
                   if p["company_profile_id"] == cid]
        for c in chiavi_collegamento(riga, dati.get("raw"), persone):
            self.tabelle.setdefault("company_collegamenti", []).append(
                {"company_profile_id": cid, "tipo": c.tipo, "chiave": c.chiave,
                 "quota": None if c.quota is None else str(c.quota)})
        return self

    def chiavi_di(self, cid) -> list[dict]:
        return [r for r in self.tabelle.get("company_collegamenti", [])
                if r["company_profile_id"] == cid]

    def marker_di(self, cid) -> dict | None:
        return next((r for r in self.tabelle.get("company_collegamenti_stato", [])
                     if r["company_profile_id"] == cid), None)


@pytest.fixture
def flag(monkeypatch):
    stato = SimpleNamespace(partenariati_attivo=True)
    monkeypatch.setattr(coll, "get_settings", lambda: stato)
    return stato


@pytest.fixture
def log(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def nessun_dato_in_chiaro(db: FakePrimary, caplog) -> None:
    """Né nelle scritture (payload e filtri), né nei filtri delle letture, né
    nei log: solo id, codici e HMAC."""
    for op in db.ops:
        tabella, operazione, payload, filtri, _ = op
        testo = repr((payload, filtri))
        for dato in DATI_IN_CHIARO:
            assert dato not in testo, (tabella, operazione)
    for dato in DATI_IN_CHIARO:
        assert dato not in caplog.text


# ------------------------------------------------------------- ricostruisci


class TestRicostruisci:
    async def test_flag_spento_nessuna_lettura_ne_scrittura(self, flag):
        flag.partenariati_attivo = False
        db = FakePrimary().azienda("c1", visibile=True, raw=raw_alfa())
        assert await coll.ricostruisci(db, "c1") is False
        assert db.ops == []

    async def test_idonea_con_opt_in(self, flag, log):
        db = FakePrimary().azienda("c1", visibile=True, raw=raw_alfa(), riga=riga_alfa())
        assert await coll.ricostruisci(db, "c1") is True
        # marker tolto per primo, riscritto per ultimo (fail-closed)
        assert [(o[0], o[1]) for o in db.scritture()] == [
            ("company_collegamenti_stato", "delete"),
            ("company_collegamenti", "delete"),
            ("company_collegamenti", "insert"),
            ("company_collegamenti_stato", "upsert"),
        ]
        righe = db.chiavi_di("c1")
        attese = chiavi_collegamento(riga_alfa(), raw_alfa(), extract_people(raw_alfa()))
        assert chiavi_da_righe(righe) == tuple(attese)
        assert all(HEX64.match(r["chiave"]) for r in righe)
        assert {r["quota"] for r in righe if r["tipo"] == "socio"} == {"60.000", "40.000"}
        marker = db.marker_di("c1")
        assert marker["algoritmo_versione"] == ALGORITMO_VERSIONE
        assert marker["fonte_fetched_at"] == T0.isoformat()
        assert marker_aggiornato(marker, T0.isoformat())
        nessun_dato_in_chiaro(db, log)

    async def test_ricalcolo_sostituisce_le_chiavi(self, flag):
        db = FakePrimary().azienda("c1", visibile=True, raw=raw_alfa())
        await coll.ricostruisci(db, "c1")
        db.tabelle["company_data"][0]["raw"] = {"companyDetails": {"vatCode": PIVA_BETA}}
        db.tabelle["company_people"] = []
        await coll.ricostruisci(db, "c1")
        assert {r["tipo"] for r in db.chiavi_di("c1")} == {"identita", "nome"}

    @pytest.mark.parametrize("stato", ["bozza", "pubblicata", "sospesa_moderazione"])
    async def test_idonea_con_una_call_non_chiusa(self, flag, stato):
        db = FakePrimary().azienda("c1", stati_call=[stato], raw=raw_alfa())
        assert await coll.ricostruisci(db, "c1") is True

    @pytest.mark.parametrize(
        "kw",
        [{}, {"stati_call": ["scaduta", "chiusa_completata", "chiusa_annullata"]},
         {"visibile": True, "deleted": True}, {"visibile": True, "archived": True}],
        ids=["senza_opt_in", "call_chiuse", "cancellata", "archiviata"],
    )
    async def test_non_idonea_cancella(self, flag, kw):
        db = FakePrimary().azienda("c1", raw=raw_alfa(), **kw).marker("c1")
        db.tabelle["company_collegamenti"] = [
            {"company_profile_id": "c1", "tipo": "nome", "chiave": "a" * 64, "quota": None},
            {"company_profile_id": "c2", "tipo": "nome", "chiave": "a" * 64, "quota": None},
        ]
        assert await coll.ricostruisci(db, "c1") is False
        assert [(o[0], o[1]) for o in db.scritture()] == [
            ("company_collegamenti", "delete"), ("company_collegamenti_stato", "delete")]
        assert db.chiavi_di("c1") == [] and db.marker_di("c1") is None
        assert len(db.chiavi_di("c2")) == 1  # le altre aziende non si toccano

    async def test_senza_import_solo_anagrafica(self, flag):
        db = FakePrimary().azienda("c1", visibile=True, riga=riga_alfa())
        assert await coll.ricostruisci(db, "c1") is True
        assert {r["tipo"] for r in db.chiavi_di("c1")} == {"identita", "nome"}
        assert db.marker_di("c1")["fonte_fetched_at"] is None

    async def test_errore_a_meta_lascia_non_calcolata(self, flag, log):
        db = FakePrimary().azienda("c1", visibile=True, raw=raw_alfa()).marker("c1")
        db.guasti[("company_collegamenti", "insert")] = APIError(
            {"message": "boom", "code": "57014", "hint": None, "details": None})
        with pytest.raises(APIError):
            await coll.ricostruisci(db, "c1")
        # il marker vecchio non c'è più: il matching la esclude (fail-closed)
        assert db.marker_di("c1") is None
        nessun_dato_in_chiaro(db, log)

    async def test_rimuovi(self, flag):
        db = FakePrimary().marker("c1")
        db.tabelle["company_collegamenti"] = [
            {"company_profile_id": "c1", "tipo": "nome", "chiave": "a" * 64, "quota": None}]
        await coll.rimuovi(db, "c1")
        assert [(o[0], o[1], o[3]) for o in db.scritture()] == [
            ("company_collegamenti", "delete", [("eq", "company_profile_id", "c1")]),
            ("company_collegamenti_stato", "delete", [("eq", "company_profile_id", "c1")]),
        ]
        assert db.tabelle["company_collegamenti"] == []

    async def test_chiavi_scritte_confrontabili_tra_aziende(self, flag):
        """Giro completo: A partecipa al 30% in B; le chiavi scritte da
        `ricostruisci` e rilette dicono «certo»."""
        raw_a = {"companyDetails": {"vatCode": PIVA_ALFA},
                 "affiliateCompanies": [{"taxCode": PIVA_BETA, "percentShare": 30}]}
        raw_b = {"companyDetails": {"vatCode": PIVA_BETA, "companyName": "Beta Servizi Srl"}}
        db = (FakePrimary()
              .azienda("a", visibile=True, raw=raw_a, riga={"partita_iva": PIVA_ALFA})
              .azienda("b", stati_call=["pubblicata"], raw=raw_b,
                       riga={"partita_iva": PIVA_BETA}))
        assert await coll.ricostruisci(db, "a") and await coll.ricostruisci(db, "b")
        a = chiavi_da_righe(db.chiavi_di("a"))
        b = chiavi_da_righe(db.chiavi_di("b"))
        assert valuta_collegamento(a, b) == "certo"


# ----------------------------------------------------------------- backfill


class TestBackfill:
    def _db(self) -> FakePrimary:
        dopo = (T0 + timedelta(days=1)).isoformat()
        return (
            FakePrimary()
            .azienda("a-senza-marker", visibile=True, raw=raw_alfa())
            .azienda("b-aggiornata", visibile=True, raw=raw_alfa()).marker("b-aggiornata")
            .chiavi_calcolate("b-aggiornata")
            .azienda("c-versione-vecchia", stati_call=["bozza"], raw=raw_alfa())
            .marker("c-versione-vecchia", versione=ALGORITMO_VERSIONE + 1)
            .azienda("d-import-nuovo", visibile=True, raw=raw_alfa(), fetched_at=dopo)
            .marker("d-import-nuovo")
            .azienda("e-non-idonea", raw=raw_alfa()).marker("e-non-idonea")
            .azienda("f-cancellata", visibile=True, raw=raw_alfa(), deleted=True)
            .marker("f-cancellata")
        )

    async def test_ricalcola_e_ripulisce(self, flag, log):
        db = self._db()
        esito = await coll.backfill(db)
        assert esito == {"ricalcolate": 3, "rimosse": 2, "errori": 0}
        for cid in ("a-senza-marker", "c-versione-vecchia", "d-import-nuovo"):
            assert db.chiavi_di(cid), cid
            fonte = db.tabelle["company_data"]
            fetched = next(r["fetched_at"] for r in fonte if r["company_profile_id"] == cid)
            assert marker_aggiornato(db.marker_di(cid), fetched), cid
        # già aggiornata: non si tocca (nessuna scrittura sulle sue chiavi)
        assert not [o for o in db.scritture() if ("eq", "company_profile_id", "b-aggiornata")
                    in o[3]]
        assert db.marker_di("e-non-idonea") is None and db.marker_di("f-cancellata") is None
        nessun_dato_in_chiaro(db, log)
        # una seconda passata non ha più nulla da fare
        assert await coll.backfill(db) == {"ricalcolate": 0, "rimosse": 0, "errori": 0}

    async def test_identita_cambiata_senza_import_ricalcola(self, flag, log):
        # Ragione sociale corretta dalla scheda dell'azienda, senza un nuovo
        # import: versione e fetched_at non cambiano, ma la chiave «nome»
        # salvata è vecchia e un collegamento per nome sfuggirebbe.
        db = (FakePrimary().azienda("b", visibile=True, raw=raw_alfa()).marker("b")
              .chiavi_calcolate("b"))
        assert await coll.backfill(db) == {"ricalcolate": 0, "rimosse": 0, "errori": 0}
        db.tabelle["company_profiles"][0]["ragione_sociale"] = "Alfa Meccanica Nuova Srl"
        assert (await coll.backfill(db))["ricalcolate"] == 1
        nuova = k("nome", coll.normalizza_nome("Alfa Meccanica Nuova Srl"))
        assert nuova in {r["chiave"] for r in db.chiavi_di("b") if r["tipo"] == "nome"}
        nessun_dato_in_chiaro(db, log)

    async def test_rotazione_della_chiave_hmac_ricalcola(self, flag, monkeypatch):
        from app.core.config import get_settings

        db = (FakePrimary().azienda("b", visibile=True, raw=raw_alfa()).marker("b")
              .chiavi_calcolate("b"))
        vecchie = {r["chiave"] for r in db.chiavi_di("b")}
        monkeypatch.setenv("RATE_LIMIT_PEPPER", "pepper-dopo-la-rotazione-di-prova")
        get_settings.cache_clear()
        try:
            assert (await coll.backfill(db))["ricalcolate"] == 1
            assert {r["chiave"] for r in db.chiavi_di("b")}.isdisjoint(vecchie)
            assert await coll.backfill(db) == {"ricalcolate": 0, "rimosse": 0, "errori": 0}
        finally:
            get_settings.cache_clear()

    async def test_limite(self, flag):
        db = self._db()
        assert await coll.backfill(db, limite=1) == {"ricalcolate": 1, "rimosse": 1, "errori": 0}
        assert db.chiavi_di("a-senza-marker") and not db.chiavi_di("c-versione-vecchia")

    async def test_flag_spento(self, flag):
        flag.partenariati_attivo = False
        db = self._db()
        assert await coll.backfill(db) == {"ricalcolate": 0, "rimosse": 0, "errori": 0}
        assert db.ops == []

    async def test_errori_isolati_senza_dati_nei_log(self, flag, log):
        db = self._db()
        db.guasti[("company_people", "select")] = APIError(
            {"message": f"errore su {CF_ROSSI}", "code": "XX000", "hint": None,
             "details": f"riga ({PIVA_ALFA})"})
        db.guasti[("company_collegamenti_stato", "delete")] = RuntimeError(CF_VERDI)
        esito = await coll.backfill(db)
        assert esito["errori"] == 5 and esito["ricalcolate"] == 0 and esito["rimosse"] == 0
        assert "XX000" in log.text and "RuntimeError" in log.text
        for dato in DATI_IN_CHIARO:
            assert dato not in log.text

    async def test_pagine_a_keyset(self, flag, monkeypatch):
        monkeypatch.setattr(coll, "PAGINA", 2)
        monkeypatch.setattr(coll, "BLOCCO_ID", 2)
        db = FakePrimary()
        for n in range(5):
            db.azienda(f"v{n}", visibile=True, raw=raw_alfa())
            db.azienda(f"k{n}", stati_call=["pubblicata", "bozza"])
        esito = await coll.backfill(db)
        assert esito == {"ricalcolate": 10, "rimosse": 0, "errori": 0}
        pagine = [o for o in db.ops if o[0] == "company_partner_profiles" and o[1] == "select"
                  and any(f[0] == "gt" for f in o[3])]
        assert pagine, "la seconda pagina parte dall'ultimo id letto"
