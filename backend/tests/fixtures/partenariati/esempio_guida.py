"""Esempio guida del modulo partenariati (docs/partenariati.md §10, criterio 4;
progetto WP6-9 §3.1), come dati SINTETICI: nessun testo proprietario, solo
codici del vocabolario v1 (tipi di soggetto, competenze, forme, ruoli).

Le righe hanno la forma delle tabelle del DB primario (company_profiles,
company_data.derived, dossier, company_partner_profiles, company_financials,
partner_calls, partner_call_requisiti, partner_call_posizioni): la parte pura
le trasforma con i builder del matching, la parte di servizio (integrazione)
può caricarle in un FakePrimary.

Scenario. X (capofila; ATECO 62, piccola, sede in Calabria) pubblica una call
con i requisiti A (organismo di ricerca), B (ATECO 62), C (competenza
`prototipazione_testing`), D (micro, piccola o media), tutti «consorzio»: X
copre B e D, quindi cerca A e C. Vincoli di ogni membro: E (sede in Calabria,
`sede_attuale`) e F (`costo_quota / fatturato_medio_2 ≤ 0,6`, per ciascun
partner). Posizione P1: organismo di ricerca con `prototipazione_testing`.

| Azienda | Situazione | Atteso |
|---|---|---|
| Y | organismo di ricerca dichiarato, `prototipazione_testing`, ISO 13485, ATECO 72, sede legale nel Lazio e unità locale in Calabria, fatturato medio 2,5 M€, opt-in | prima: «Copri «A» e «C», che mancano al capofila.» |
| Z | come Y, sedi solo in Lombardia | esclusa: `territorio` |
| W | come Y, collegata a X (socio comune al 60%) | esclusa: `collegata` |
| V | come Y, senza opt-in | esclusa: `non_visibile` |
| U | come Y, fatturato medio 200 k€ | esclusa: `finanziaria_non_soddisfatta` |
| T | come Y, senza bilanci | sotto Y, con «Bilanci non disponibili…» e penalità 5 |

Scostamenti voluti dai numeri del piano (le regole verso terzi si valutano
sulle FASCE, Q11, e il budget è la fascia pubblica, mai quello esatto):
- budget in fascia `2m_5m` (la fascia «1,0–1,2 M€» del piano non esiste
  nell'enum di WP5) e quota di P1 al 20%: costo della quota 400 k€–1 M€;
- Y ha fatturato medio 2,5 M€ (fascia `2m_10m`: 0,6 × 2 M€ = 1,2 M€ ≥ 1 M€,
  soddisfatto su tutta la fascia); U ha 200 k€ (fascia `100k_500k`:
  0,6 × 500 k€ = 300 k€ < 400 k€, non soddisfatto su tutta la fascia). Con la
  quota al 30% o una fascia di budget più bassa l'esito di U o di Y sarebbe
  «dipende dalla fascia», non certo.

Oltre alla call della guida ci sono due call di O (altro owner): una pubblica
dove Y copre un solo requisito cercato (seconda in «Per te») e una solo su
invito (mai in «Per te» senza invito).

Punteggio di Y sulla call della guida, a mano (pesi di default):
copertura 2/2 = 1; affinità 2/3 (esperienza nel programma del bando sì,
ATECO o settore in comune con X no, regione in comune sì); complementarità
3/3 = 1 (nessuna delle 3 competenze di Y è di X); completezza 70/100;
rotazione 1 → 100·(0,50 + 0,20·2/3 + 0,10 + 0,07 + 0,10) = 90,33 → 90.
T: stesso calcolo, meno 5 di penalità → 85.
"""

from datetime import date

from app.services.partenariato_criteri import profilo_candidato_da
from app.services.partenariato_matching import (
    CallSnapshot,
    IndiceMatching,
    ProfiloMatching,
    call_snapshot_da,
    profilo_matching_da,
)

OGGI = date(2026, 10, 5)  # lunedì, settimana ISO 2026-W41

# Id della lookup del catalogo (sintetici).
CALABRIA, LAZIO, LOMBARDIA = 4, 7, 9
HORIZON, FESR = 11, 12
TIPOLOGIA_BANDO = 3
SETTORE_ICT = 47
BANDO_GUIDA, BANDO_ALTRO = 18455, 20001

NOMI = ("X", "Y", "Z", "W", "V", "U", "T", "O")
_NUMERO = {nome: i + 1 for i, nome in enumerate(NOMI)}


def _uuid(prefisso: str, n: int) -> str:
    return f"{prefisso}0000000-0000-4000-8000-{n:012d}"


OWNER = {nome: _uuid("a", _NUMERO[nome]) for nome in NOMI}
COMPANY = {nome: _uuid("c", _NUMERO[nome]) for nome in NOMI}
CODICE_PUBBLICO = {nome: _uuid("d", _NUMERO[nome]) for nome in NOMI}

CALL_GUIDA_ID = _uuid("e", 1)
CALL_ALTRA_ID = _uuid("e", 2)
CALL_RISERVATA_ID = _uuid("e", 3)

REQ = {etichetta: _uuid("f", i + 1) for i, etichetta in enumerate("ABCDEF")}
REQ_ALTRA = {"A": _uuid("f", 11), "B": _uuid("f", 12)}
REQ_RISERVATA = {"A": _uuid("f", 21)}
POS_P1 = _uuid("b", 1)
POS_ALTRA = _uuid("b", 2)
POS_RISERVATA = _uuid("b", 3)

SPIEGAZIONE_GUIDA = "Copri «A» e «C», che mancano al capofila."
PUNTEGGIO_Y = 90
PUNTEGGIO_T = 85

# Valori esatti di bilancio di Y: non devono MAI uscire verso terzi (canary).
FATTURATO_Y = (2_400_000, 2_600_000)
PATRIMONIO_NETTO_Y = 812_345


# ------------------------------------------------------------------ aziende

_FLAGS = {
    "startup_innovativa": False,
    "pmi_innovativa": False,
    "impresa_artigiana": False,
    "certificazione_soa": False,
}
_DOSSIER_SRL = {"anagrafica": {"forma_giuridica": "Società a responsabilità limitata"},
                "flags": _FLAGS}


def _derived(ateco: str, regione: int, regioni: list[int], classe: str) -> dict:
    return {
        "ateco_principale": ateco,
        "ateco_divisione": ateco[:2],
        "ateco_secondari": [],
        "regione_id": regione,
        "regioni_ids": regioni,
        "classe_dimensionale": classe,
    }


def _esercizi(fatturati: tuple[int, int], patrimonio_netto: int, dipendenti: int) -> list[dict]:
    return [
        {
            "anno": anno,
            "fatturato": f"{fatturato}.00",
            "patrimonio_netto": f"{patrimonio_netto}.00",
            "totale_attivo": f"{fatturato * 2}.00",
            "dipendenti": f"{dipendenti}.00",
            "fonte_per_campo": {"fatturato": "it_advanced"},
        }
        for anno, fatturato in zip((2023, 2024), fatturati, strict=True)
    ]


def _profilo_partner(nome: str, **extra) -> dict:
    riga = {
        "company_profile_id": COMPANY[nome],
        "family_parent_id": OWNER[nome],
        "codice_pubblico": CODICE_PUBBLICO[nome],
        "visibile_come_partner": True,
        "anonimo": True,
        "consenso_versione": "2026-10-bozza-1",
        "consenso_at": "2026-09-01T10:00:00+00:00",
        "accetta_inviti": True,
        "competenze": ["prototipazione_testing", "ricerca_industriale",
                       "dispositivi_medici_salute"],
        "tipi_soggetto": ["organismo_ricerca"],
        "ruoli_disponibili": ["partner"],
        "settori_interesse": [],
        "regioni_interesse": [CALABRIA],
        "paesi_interesse": [],
        "forme_accettate": ["ats", "ati_rti"],
        "esperienze": [
            {"programma": "Horizon Europe", "programma_id": HORIZON, "ruolo": "partner",
             "anno": 2023},
        ],
        "certificazioni": ["ISO 13485"],
        "categorie_bando_escluse": [],
        "completezza": 70,
        "sospeso_at": None,
    }
    riga.update(extra)
    return riga


def _come_y(nome: str, **modifiche) -> dict:
    azienda = {
        "company": {"id": COMPANY[nome], "settore_id": None},
        "derived": _derived("72.19.09", LAZIO, [LAZIO, CALABRIA], "piccola"),
        "dossier": _DOSSIER_SRL,
        "profilo_partner": _profilo_partner(nome),
        "esercizi": _esercizi(FATTURATO_Y, PATRIMONIO_NETTO_Y, 18),
        "stato_impresa": "Attiva",
        "collegate": [],
        "collegamenti_ok": True,
        "impegni_bando": [],
    }
    azienda.update(modifiche)
    return azienda


AZIENDE: dict[str, dict] = {
    "X": {
        "company": {"id": COMPANY["X"], "settore_id": SETTORE_ICT},
        "derived": _derived("62.01.00", CALABRIA, [CALABRIA], "piccola"),
        "dossier": _DOSSIER_SRL,
        "profilo_partner": _profilo_partner(
            "X",
            competenze=["sviluppo_software", "intelligenza_artificiale_dati"],
            tipi_soggetto=[],
            ruoli_disponibili=["capofila", "partner"],
            esperienze=[],
            certificazioni=["ISO 9001"],
            completezza=60,
        ),
        "esercizi": _esercizi((3_100_000, 3_300_000), 1_500_000, 25),
        "stato_impresa": "Attiva",
        "collegate": ["W"],
        "collegamenti_ok": True,
        "impegni_bando": [BANDO_GUIDA],
    },
    "Y": _come_y("Y"),
    "Z": _come_y(
        "Z", derived=_derived("72.19.09", LOMBARDIA, [LOMBARDIA], "piccola")
    ),
    # Socio al 60% in comune con X: collegamento «certo» (Q17). L'insieme
    # delle collegate lo calcola l'indice con partenariato_collegamenti.
    "W": _come_y("W", collegate=["X"]),
    # Senza opt-in: niente consenso, collegamenti non calcolati (M2).
    "V": _come_y(
        "V",
        profilo_partner=_profilo_partner(
            "V", visibile_come_partner=False, consenso_versione=None, consenso_at=None
        ),
        collegamenti_ok=False,
    ),
    "U": _come_y("U", esercizi=_esercizi((190_000, 210_000), 60_000, 3)),
    "T": _come_y("T", esercizi=[]),
    # Creatore delle altre due call: nessun profilo partner (non candidabile).
    "O": {
        "company": {"id": COMPANY["O"], "settore_id": None},
        "derived": _derived("28.99.00", LOMBARDIA, [LOMBARDIA], "media"),
        "dossier": _DOSSIER_SRL,
        "profilo_partner": None,
        "esercizi": _esercizi((9_000_000, 9_500_000), 4_000_000, 80),
        "stato_impresa": "Attiva",
        "collegate": [],
        "collegamenti_ok": True,
        "impegni_bando": [BANDO_ALTRO],
    },
}


def profilo(nome: str, **modifiche) -> ProfiloMatching:
    """`ProfiloMatching` dell'azienda `nome`, con eventuali modifiche alle
    righe (stesse chiavi di `AZIENDE[nome]`)."""
    dati = {**AZIENDE[nome], **modifiche}
    base = profilo_candidato_da(
        company=dati["company"],
        derived=dati["derived"],
        dossier=dati["dossier"],
        profilo_partner=dati["profilo_partner"],
        esercizi=dati["esercizi"],
        storico_completo=True,
    )
    return profilo_matching_da(
        base,
        company_id=COMPANY[nome],
        owner_id=OWNER[nome],
        viva=True,
        profilo_partner=dati["profilo_partner"],
        stato_impresa=dati["stato_impresa"],
        collegate=[COMPANY[c] for c in dati["collegate"]],
        collegamenti_ok=dati["collegamenti_ok"],
        impegni_bando=dati["impegni_bando"],
        esposizioni_7g=0,
    )


# -------------------------------------------------------------------- call

REGOLA_F = {
    "id": "F1",
    "descrizione": "Il costo della quota non supera il 60% del fatturato medio degli ultimi due "
                   "esercizi",
    "ambito": "ciascun_partner",
    "numeratore": "costo_quota",
    "denominatore": "fatturato_medio_2",
    "operatore": "le",
    "soglia": "0.6",
    "soglia_variabile": None,
    "soglia_coefficiente": None,
    "unita": "rapporto",
}

CALL_GUIDA = {
    "id": CALL_GUIDA_ID,
    "company_profile_id": COMPANY["X"],
    "family_parent_id": OWNER["X"],
    "bando_id": BANDO_GUIDA,
    "bando_slug": "bando-sintetico-ricerca-sviluppo",
    "bando_titolo": "Bando sintetico di ricerca e sviluppo",
    "bando_scadenza": "2027-01-31",
    "bando_programma_id": HORIZON,
    "bando_tipologia_id": TIPOLOGIA_BANDO,
    "ruolo_creatore": "capofila",
    "forma_aggregazione_prevista": "ats",
    "anonima": True,
    "titolo": "Cerchiamo un organismo di ricerca per la prototipazione",
    # Il budget esatto è RISERVATO: il matching usa solo la fascia.
    "budget_fascia": "2m_5m",
    "budget_progetto_eur": "3100000.00",
    "quota_creatore_pct": "80.00",
    "scadenza_call": "2026-12-15",
    "visibilita": "pubblica",
    "stato": "pubblicata",
    "esclusivita": False,
    "sospesa_at": None,
    "pubblicata_at": "2026-10-01T09:00:00+00:00",
}


def riga_requisito(id_: str, call_id: str, etichetta: str, ordine: int, criterio: dict, *,
               ambito: str = "consorzio", cercato: bool = False, origine: str = "manuale",
               testo: str = "Requisito della call") -> dict:
    return {
        "id": id_, "call_id": call_id, "origine": origine, "rif_origine": None,
        "etichetta": etichetta, "testo": testo, "criterio": criterio, "ambito": ambito,
        "cercato": cercato, "ordine": ordine,
    }


REQUISITI_GUIDA = [
    riga_requisito(REQ["A"], CALL_GUIDA_ID, "A", 0,
               {"tipo": "tipo_soggetto", "valori": ["organismo_ricerca"]},
               cercato=True, testo="Un organismo di ricerca nel partenariato"),
    riga_requisito(REQ["B"], CALL_GUIDA_ID, "B", 1, {"tipo": "ateco", "divisioni": ["62"]},
               testo="Attività di produzione di software"),
    riga_requisito(REQ["C"], CALL_GUIDA_ID, "C", 2,
               {"tipo": "tag", "tags": ["prototipazione_testing"], "modalita": "almeno_uno"},
               cercato=True, testo="Competenze di prototipazione e test"),
    riga_requisito(REQ["D"], CALL_GUIDA_ID, "D", 3,
               {"tipo": "dimensione", "valori": ["micro", "piccola", "media"]},
               testo="Almeno una PMI"),
    riga_requisito(REQ["E"], CALL_GUIDA_ID, "E", 4,
               {"tipo": "regione", "regioni_ids": [CALABRIA], "modalita": "sede_attuale"},
               ambito="ogni_membro", origine="bando_partenariato",
               testo="Sede operativa in Calabria"),
    riga_requisito(REQ["F"], CALL_GUIDA_ID, "F", 5,
               {"tipo": "regola_finanziaria", "regola": REGOLA_F},
               ambito="ogni_membro", origine="regola_finanziaria",
               testo="Costo della quota entro il 60% del fatturato medio"),
]


def riga_posizione(id_: str, call_id: str, titolo: str, *, tipi=(), competenze=(), quota="30.00",
               requisiti_ids=(), ruolo: str = "partner") -> dict:
    return {
        "id": id_, "call_id": call_id, "titolo": titolo, "ruolo": ruolo,
        "tipi_soggetto": list(tipi), "competenze": list(competenze), "ateco_divisioni": [],
        "regioni": [], "territorio_modalita": "qualsiasi", "paesi": [], "dimensioni": [],
        "quota_ipotizzata_pct": quota, "numero": 1, "requisiti_ids": list(requisiti_ids),
        "note": None, "ordine": 0,
    }


POSIZIONI_GUIDA = [
    riga_posizione(POS_P1, CALL_GUIDA_ID, "Ricerca e prototipazione", tipi=["organismo_ricerca"],
               competenze=["prototipazione_testing"], quota="20.00",
               requisiti_ids=[REQ["A"], REQ["C"]]),
]

# Call pubblica di O: Y copre solo «B» (esperienza nel programma).
CALL_ALTRA = {
    **CALL_GUIDA,
    "id": CALL_ALTRA_ID,
    "company_profile_id": COMPANY["O"],
    "family_parent_id": OWNER["O"],
    "bando_id": BANDO_ALTRO,
    "bando_slug": "bando-sintetico-innovazione",
    "bando_titolo": "Bando sintetico per l'innovazione",
    "bando_programma_id": FESR,
    "forma_aggregazione_prevista": None,
    "titolo": "Partner per un progetto di innovazione digitale",
    "budget_fascia": "500k_1m",
    "budget_progetto_eur": None,
    "quota_creatore_pct": "70.00",
}
REQUISITI_ALTRA = [
    riga_requisito(REQ_ALTRA["A"], CALL_ALTRA_ID, "A", 0,
               {"tipo": "tag", "tags": ["intelligenza_artificiale_dati"],
                "modalita": "almeno_uno"}, cercato=True),
    riga_requisito(REQ_ALTRA["B"], CALL_ALTRA_ID, "B", 1,
               {"tipo": "esperienza", "programmi_ids": [HORIZON], "ruolo": None}, cercato=True),
]
POSIZIONI_ALTRA = [riga_posizione(POS_ALTRA, CALL_ALTRA_ID, "Partner tecnologico")]

# Call di O solo su invito: Y la coprirebbe, ma senza invito non la vede.
CALL_RISERVATA = {
    **CALL_ALTRA,
    "id": CALL_RISERVATA_ID,
    "bando_id": BANDO_ALTRO + 1,
    "titolo": "Partner su invito per un progetto riservato",
    "visibilita": "solo_invitati",
}
REQUISITI_RISERVATA = [
    riga_requisito(REQ_RISERVATA["A"], CALL_RISERVATA_ID, "A", 0,
               {"tipo": "tag", "tags": ["prototipazione_testing"], "modalita": "almeno_uno"},
               cercato=True),
]
POSIZIONI_RISERVATA = [riga_posizione(POS_RISERVATA, CALL_RISERVATA_ID, "Partner su invito")]


def call_guida(**modifiche) -> CallSnapshot:
    return call_snapshot_da(
        {**CALL_GUIDA, **modifiche}, REQUISITI_GUIDA, POSIZIONI_GUIDA, profilo("X")
    )


def calls() -> list[CallSnapshot]:
    return [
        call_guida(),
        call_snapshot_da(CALL_ALTRA, REQUISITI_ALTRA, POSIZIONI_ALTRA, profilo("O")),
        call_snapshot_da(CALL_RISERVATA, REQUISITI_RISERVATA, POSIZIONI_RISERVATA, profilo("O")),
    ]


# Nell'indice ci sono solo le aziende con opt-in visibile (V no).
CANDIDATI_INDICE = ("X", "Y", "Z", "W", "U", "T")


def indice() -> IndiceMatching:
    return IndiceMatching.da_liste(calls(), [profilo(n) for n in CANDIDATI_INDICE])
