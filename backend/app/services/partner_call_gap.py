"""Gap analysis della call di partenariato (WP5, docs/partenariati.md C1-C3).

Modulo PURO (nessun I/O, nessun LLM). Trasforma le fonti deterministiche in
requisiti TIPIZZATI (`CriterioPartner` + ambito) e calcola la copertura del
creatore con `partenariato_criteri.valuta_criterio` in vista «proprio».

Fonti e mappature (`criterio_da_requisito`):
- regole confermate dal creatore (snapshot `partner_calls.regole_partenariato`,
  mai l'estrazione grezza): composizione → `tipo_soggetto` ambito consorzio;
  vincolo `sede_operativa_regione` → `regione` ambito ogni_membro (regioni
  del bando dal catalogo; momento «domanda» → sede attuale, altrimenti entro
  l'erogazione, che non esclude nessuno: anche con momento non indicato);
  regole finanziarie → `regola_finanziaria` (ogni_membro se valgono per
  ciascun partner);
- pre-check del catalogo: regione → `regione` ogni_membro; ATECO → `ateco`
  (con ATECO e settore insieme vale solo l'ATECO: nell'AI-check sono evidenze
  alternative, best-of); settore → `settore`; beneficiari → `tipo_soggetto`;
- AI-check: il testo libero diventa SEMPRE `manuale` (mai un criterio
  dedotto dal testo del modello); il verdetto sul creatore conta solo come
  evidenza della sua copertura.

`unisci` deduplica territoriale e settoriale (un requisito tipizzato assorbe
quelli dell'AI-check della stessa categoria) con precedenza regole >
catalogo > AI-check; sulla copertura prevale il PEGGIORE, come
`ai_check_scoring._merge_precheck`. `evidenze_assorbite` ripete lo stesso
assorbimento al salvataggio, perché la copertura salvata sia quella proposta.

Riferimenti (`rif_origine`): per l'AI-check e per le voci dello snapshot
delle regole (fonti che rinumerano le voci) l'id della voce porta
l'impronta del contenuto (`rif_con_impronta`); per il catalogo è la chiave
del facet; per le regole finanziarie l'id della regola dello snapshot (Q11).

Prova dei requisiti (WP9, completamento): `citazione.verificata` la decide
SOLO il server. Fa fede il testo dei documenti ufficiali del bando (pagine
`D<n>-p<m>`, come la post-elaborazione del WP3): le citazioni costruite dai
metadati della scheda del catalogo (`_citazione_meta`) escono non verificate,
e una voce dello snapshot solo se `confermata` e citata da una pagina
ufficiale (`_dump_citazione`). Al salvataggio `citazioni_dal_server`
ricalcola la citazione di ogni requisito inviato dal client (mai creduta).

Sicurezza dei testi: `copertura_nota` viene SOLO dai template di
`NOTE_COPERTURA` (esito + fonte + motivo macchina). Motivazioni e dati
aziendali dell'AI-check (che con il WP1 ricevono i bilanci esatti) non
entrano MAI in nessun campo prodotto qui.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.schemas.partenariato_criteri import (
    CRITERIO_ADAPTER,
    CriterioAteco,
    CriterioManuale,
    CriterioPartner,
    CriterioRegione,
    CriterioRegolaFinanziaria,
    CriterioSettore,
    CriterioTipoSoggetto,
    Intervallo,
    ProfiloCandidato,
    RegolaCriterio,
)
from app.schemas.partner_call import (
    ESTREMI_BUDGET,
    CitazioneIn,
    ComposizioneSnapshot,
    RegoleCallSnapshot,
    RequisitoOut,
    RiepilogoGapOut,
    VincoloSnapshot,
)
from app.schemas.regole_finanziarie import RegolaFinanziaria
from app.services import partenariato_vocabolario as voc
from app.services.citazioni import normalizza_sezione
from app.services.openapi_mapping import ateco_division
from app.services.partenariato_anonimato import senza_invisibili
from app.services.partenariato_criteri import criterio_da_json, valuta_criterio_dettaglio

# ------------------------------------------------------------------ costanti

TESTO_MIN, TESTO_MAX = 3, 500
RIF_MAX = 40

# Verdetto dell'AI-check sul creatore → copertura.
ESITO_DA_VERDETTO = {
    "soddisfatto": "coperto",
    "non_soddisfatto": "non_coperto",
    "dato_mancante": "dato_mancante",
    "parzialmente_soddisfatto": "incerto",
}

# Criteri di valutazione dell'AI-check che descrivono un attributo che un
# partner può portare (gli altri, es. qualità del progetto, non sono gap).
_CATEGORIE_PARTNER = frozenset({"soggettivo", "dimensionale", "territoriale", "settoriale"})

# Il peggiore prevale; `non_valutabile` non è un'evidenza (si ignora se c'è
# altro).
_GRAVITA = {"coperto": 1, "incerto": 2, "dato_mancante": 3, "non_coperto": 4}

# Vincoli WP3 che diventano requisiti di un membro; gli altri (indipendenza,
# esclusività, paesi distinti, costituzione) sono regole del partenariato:
# li controlla il validatore (WP8) sullo snapshot.
_VINCOLI_REQUISITO = frozenset({"sede_operativa_regione", "requisito_capofila", "altro"})
_AMBITI_AGGREGATI = frozenset({"media_pesata_quote", "partenariato_totale"})

_ETICHETTE_RUOLO = {
    "capofila": " come capofila",
    "partner": " come partner",
    "affiliato": " come affiliato",
    "partner_associato": " come partner associato",
    "qualsiasi": "",
}

# copertura_nota: SOLO questi testi (chiave: esito, fonte[, motivo]).
NOTE_COPERTURA: dict[tuple[str, ...], str] = {
    ("coperto", "registro"): "Coperto dal Registro Imprese",
    ("coperto", "bilanci"): "Coperto secondo i tuoi bilanci",
    ("coperto", "dichiarato"): "Coperto da quanto dichiari nel profilo partner",
    ("coperto", "ai_check"): "Coperto secondo l'AI-check",
    ("non_coperto", "registro"): "Non coperto secondo il Registro Imprese",
    ("non_coperto", "bilanci"): "Non coperto secondo i tuoi bilanci",
    ("non_coperto", "dichiarato"): "Non coperto: non risulta dal profilo partner",
    ("non_coperto", "ai_check"): "Non coperto secondo l'AI-check",
    ("dato_mancante", "registro"): "Da verificare: mancano i dati del Registro Imprese",
    ("dato_mancante", "bilanci"): "Da verificare: dato di bilancio mancante",
    ("dato_mancante", "bilanci", "costo_quota_mancante"): (
        "Da verificare: indica il budget del progetto e la tua quota"
    ),
    ("dato_mancante", "dichiarato"): "Da verificare: completa il profilo partner",
    ("dato_mancante", "ai_check"): "Da verificare: l'AI-check non ha trovato il dato",
    ("dato_mancante", "nessuna"): "Da verificare: dato mancante",
    ("incerto", "registro"): "Da verificare: serve una sede nella regione entro l'erogazione",
    ("incerto", "bilanci"): "Da verificare: dipende dal budget del progetto",
    ("incerto", "bilanci", "storico_incompleto"): (
        "Da verificare: lo storico dei bilanci è incompleto"
    ),
    ("incerto", "dichiarato"): "Da verificare: il profilo partner non basta per confermarlo",
    ("incerto", "ai_check"): "Coperto solo in parte secondo l'AI-check",
    ("non_valutabile", "nessuna"): "Da valutare a mano: requisito descritto solo a testo",
    ("non_valutabile", "nessuna", "aggregata"): "Si valuta sull'intero partenariato",
    ("non_valutabile", "nessuna", "capofila"): "Riguarda il capofila che cerchi",
}


def nota_copertura(esito: str, fonte: str, motivo: str | None = None) -> str:
    """Il template della nota: prima la variante con il motivo, poi quella
    per esito e fonte, infine per il solo esito."""
    for chiave in ((esito, fonte, motivo), (esito, fonte), (esito, "nessuna")):
        if chiave in NOTE_COPERTURA:
            return NOTE_COPERTURA[chiave]
    return NOTE_COPERTURA[("dato_mancante", "nessuna")]


# ----------------------------------------------------------- requisito bozza


@dataclass
class RequisitoBozza:
    """Requisito prodotto dai generatori, prima della copertura.

    `categoria` serve alla deduplica (territoriale, settoriale, …);
    `esiti_ai_check` sono le coperture del creatore secondo l'AI-check (già
    tradotte, mai i suoi testi); `cercato` None = decide la regola di default
    (non coperto e ambito consorzio); `premiale` = criterio di valutazione
    (mai cercato di default); `unito_con` = riferimenti assorbiti."""

    origine: str
    testo: str
    criterio: CriterioPartner | None
    ambito: str = "consorzio"
    rif_origine: str | None = None
    citazione: dict | None = None
    categoria: str | None = None
    esiti_ai_check: tuple[str, ...] = ()
    cercato: bool | None = None
    sintetico: bool = False
    premiale: bool = False
    unito_con: tuple[str, ...] = ()
    id: UUID | None = None


def _testo(valore: Any) -> str | None:
    """Testo di un requisito: spazi compattati, tagliato a 500 caratteri;
    None se più corto di 3."""
    if not isinstance(valore, str):
        return None
    compatto = " ".join(valore.split())
    if len(compatto) > TESTO_MAX:
        compatto = compatto[: TESTO_MAX - 1].rstrip() + "…"
    return compatto if len(compatto) >= TESTO_MIN else None


def _rif(valore: Any) -> str | None:
    if valore is None:
        return None
    testo = str(valore).strip()
    return testo[:RIF_MAX] or None


# Tra l'id della voce e l'impronta del suo contenuto, nel riferimento.
SEPARATORE_IMPRONTA = "~"
_CIFRE_IMPRONTA = 10


def rif_con_impronta(rif: Any, *contenuto: Any) -> str | None:
    """Riferimento di un requisito ricavato da una fonte che RINUMERA le sue
    voci: l'AI-check («R1», «R2»… a ogni nuova estrazione del bando) e le
    voci dello snapshot delle regole (ids dell'estrazione WP3 o delle voci
    aggiunte, riusabili). È l'id della voce + «~» + un'impronta (sha256) del
    suo contenuto: due voci con lo stesso id e un contenuto diverso sono
    requisiti DIVERSI, così la rigenerazione non passa id, etichetta e
    «cercato» dall'una all'altra e il verdetto dell'AI-check vale solo per
    lo stesso requisito. Al massimo `RIF_MAX` caratteri."""
    base = _rif(rif)
    if base is None:
        return None
    impronta = hashlib.sha256(
        "\x1f".join("" if parte is None else str(parte) for parte in contenuto).encode("utf-8")
    ).hexdigest()[:_CIFRE_IMPRONTA]
    return f"{base[: RIF_MAX - _CIFRE_IMPRONTA - 1]}{SEPARATORE_IMPRONTA}{impronta}"


def id_voce(rif: str | None) -> str | None:
    """L'id della voce di un riferimento, senza l'impronta."""
    return rif.split(SEPARATORE_IMPRONTA, 1)[0] if rif else rif


def _elenco(nomi: Iterable[Any]) -> str:
    return ", ".join(str(n) for n in nomi if n not in (None, ""))


def _junction(bando: Mapping | None, junction: str, inner: str) -> list[Mapping]:
    voci = []
    for riga in (bando or {}).get(junction) or []:
        voce = riga.get(inner) if isinstance(riga, Mapping) else None
        if isinstance(voce, Mapping):
            voci.append(voce)
    return voci


def _ids(voci: Iterable[Mapping]) -> list[int]:
    ids = []
    for voce in voci:
        valore = voce.get("id")
        if isinstance(valore, int) and not isinstance(valore, bool) and valore >= 1:
            ids.append(valore)
    return list(dict.fromkeys(ids))


def tipi_da_beneficiari(ids: Iterable[int]) -> list[str]:
    """Tipi di soggetto del vocabolario per gli id `beneficiari` del
    catalogo, nell'ordine del vocabolario."""
    richiesti = set(ids)
    return [
        codice
        for codice, voce in voc.TIPI_SOGGETTO.items()
        if richiesti & set(voce.beneficiari)
    ]


def _citazione_meta(testo: str) -> dict:
    """Citazione di un pre-check, costruita dai metadati della scheda del
    catalogo (regioni, ATECO, settori, beneficiari): testo generato o
    classificato dal produttore del catalogo, non estratto dall'atto. Resta
    come riferimento visibile al creatore, ma NON verificata (fa fede solo il
    testo dei documenti ufficiali, `da_pagina_ufficiale`). Il requisito e la
    sua copertura non cambiano: la copertura dipende dal criterio."""
    return {
        "sezione": "META",
        "testo": testo[:2000],
        "verificata": False,
        "fonte_etichetta": "Scheda del bando",
    }


# Pagina di un documento ufficiale del bando, nella forma canonica di
# `citazioni.normalizza_sezione` («D1-p3»).
_PAGINA_UFFICIALE = re.compile(r"D\d+-p\d+")


def da_pagina_ufficiale(citazione: Any) -> bool:
    """True se la citazione punta a una pagina di un documento ufficiale del
    bando («D1-p3», anche scritta «[D1-P3]» o «D1 pag. 3»). La scheda del
    catalogo (META, S1…) e ogni altra sezione non fanno fede (WP3, commit
    «regole verificate solo dai documenti ufficiali»)."""
    if isinstance(citazione, Mapping):
        sezione = citazione.get("sezione")
    else:
        sezione = getattr(citazione, "sezione", None)
    if not isinstance(sezione, str):
        return False
    return _PAGINA_UFFICIALE.fullmatch(normalizza_sezione(sezione)) is not None


# -------------------------------------------------------- criterio da fonte


def criterio_da_requisito(
    origine: str, voce: Any = None, *, regioni_ids: Sequence[int] = ()
) -> tuple[CriterioPartner, str]:
    """(criterio, ambito) di un requisito secondo la sua fonte.

    - `ai_check`: testo libero → `manuale`, consorzio;
    - `precheck`: `voce` = {"chiave": regione|ateco|settore|beneficiari, e
      "ids" (regione, settore, beneficiari) o "divisioni" (ateco)};
    - `bando_partenariato`: `voce` = voce di composizione o vincolo dello
      snapshot; per `sede_operativa_regione` servono le regioni del bando
      (`regioni_ids`), senza diventa `manuale`;
    - `regola_finanziaria`: `voce` = regola (contratto WP1, anche della voce
      dello snapshot);
    - `manuale` (o fonte ignota): `manuale`, consorzio."""
    if origine == "precheck" and isinstance(voce, Mapping):
        chiave = voce.get("chiave")
        ids = [i for i in voce.get("ids") or [] if isinstance(i, int) and i >= 1]
        if chiave == "regione":
            if ids:
                return CriterioRegione(regioni_ids=ids, modalita="sede_attuale"), "ogni_membro"
            return CriterioManuale(), "ogni_membro"
        if chiave == "ateco":
            divisioni = [d for d in voce.get("divisioni") or [] if isinstance(d, str)]
            if divisioni:
                return CriterioAteco(divisioni=divisioni), "consorzio"
        if chiave == "settore" and ids:
            return CriterioSettore(settori_ids=ids), "consorzio"
        if chiave == "beneficiari":
            tipi = tipi_da_beneficiari(ids)
            if tipi and tipi != ["altro"]:
                return CriterioTipoSoggetto(valori=tipi), "consorzio"
        return CriterioManuale(), "consorzio"

    if origine == "bando_partenariato":
        if isinstance(voce, ComposizioneSnapshot):
            if voce.tipo_soggetto == "altro":
                return CriterioManuale(), "consorzio"
            return CriterioTipoSoggetto(valori=[voce.tipo_soggetto]), "consorzio"
        if isinstance(voce, VincoloSnapshot):
            if voce.tipo == "sede_operativa_regione":
                ids = [i for i in regioni_ids if isinstance(i, int) and i >= 1]
                if not ids:
                    return CriterioManuale(), "ogni_membro"
                modalita = (
                    "sede_entro_erogazione"
                    if voce.momento in ("concessione", "prima_erogazione", "non_indicato")
                    else "sede_attuale"
                )
                return CriterioRegione(regioni_ids=ids, modalita=modalita), "ogni_membro"
            return CriterioManuale(), "consorzio"

    if origine == "regola_finanziaria" and isinstance(voce, RegolaFinanziaria):
        regola = RegolaCriterio(
            **{nome: getattr(voce, nome) for nome in RegolaFinanziaria.model_fields}
        )
        ambito = "ogni_membro" if voce.ambito == "ciascun_partner" else "consorzio"
        return CriterioRegolaFinanziaria(regola=regola), ambito

    return CriterioManuale(), "consorzio"


# -------------------------------------------------------------- generatori


def _citazione_report(riferimento: Any) -> dict | None:
    """Citazione di una voce dell'AI-check. Il suo `verificata` dice solo che
    il passaggio è stato ritrovato nel testo letto; l'AI-check legge la scheda
    del catalogo (META, S1…), testo generato o classificato: fa fede solo da
    una pagina di un documento ufficiale (`da_pagina_ufficiale`), come per i
    pre-check (`_citazione_meta`) e le regole. Il requisito e la sua
    copertura non cambiano."""
    if not isinstance(riferimento, Mapping):
        return None
    testo = riferimento.get("testo")
    sezione = riferimento.get("sezione")
    if not isinstance(testo, str) or not isinstance(sezione, str):
        return None
    citazione = {"sezione": sezione.strip()[:40], "testo": testo[:2000]}
    citazione["verificata"] = (
        riferimento.get("verificata") is True and da_pagina_ufficiale(citazione)
    )
    return citazione


def requisiti_da_ai_check(report: Mapping | None) -> list[RequisitoBozza]:
    """Requisiti dal report dell'ultimo AI-check `ready` (`ai_checks.report`).

    Si usano SOLO: id, testo del requisito estratto dal bando (o nome del
    criterio), categoria, citazione del bando e il verdetto (tradotto in
    copertura). `motivazione`, `dato_azienda` e ogni altro testo del
    matching NON si leggono: contengono dati dell'azienda (bilanci esatti).
    Criteri di valutazione: solo quelli su attributi che un partner può
    portare (soggettivo, dimensionale, territoriale, settoriale)."""
    if not isinstance(report, Mapping):
        return []
    bozze: list[RequisitoBozza] = []
    for voce in report.get("requisiti") or []:
        if not isinstance(voce, Mapping):
            continue
        testo = _testo(voce.get("testo"))
        if testo is None:
            continue
        citazione = _citazione_report(voce.get("riferimento_bando"))
        esito = ESITO_DA_VERDETTO.get(voce.get("verdetto"))
        criterio, ambito = criterio_da_requisito("ai_check", voce)
        bozze.append(
            RequisitoBozza(
                origine="ai_check",
                testo=testo,
                criterio=criterio,
                ambito=ambito,
                rif_origine=rif_con_impronta(voce.get("id"), testo),
                citazione=citazione,
                categoria=voce.get("categoria") if isinstance(voce.get("categoria"), str) else None,
                esiti_ai_check=(esito,) if esito else (),
                # Voce sintetica dello scoring (vincolo del catalogo in
                # contraddizione): ripete un pre-check.
                sintetico=bool(citazione and citazione["sezione"] == "META")
                and testo.startswith("Vincolo di catalogo"),
            )
        )
    for voce in report.get("criteri") or []:
        if not isinstance(voce, Mapping) or voce.get("categoria") not in _CATEGORIE_PARTNER:
            continue
        nome = _testo(voce.get("nome"))
        if nome is None:
            continue
        esito = ESITO_DA_VERDETTO.get(voce.get("verdetto"))
        criterio, ambito = criterio_da_requisito("ai_check", voce)
        testo = _testo(f"Criterio di valutazione: {nome}") or nome
        bozze.append(
            RequisitoBozza(
                origine="ai_check",
                testo=testo,
                criterio=criterio,
                ambito=ambito,
                rif_origine=rif_con_impronta(voce.get("id"), testo),
                citazione=_citazione_report(voce.get("riferimento_bando")),
                categoria=voce["categoria"],
                esiti_ai_check=(esito,) if esito else (),
                premiale=True,
            )
        )
    return bozze


def _applicabile(prechecks: Mapping | None, chiave: str, bando_voci: list) -> bool:
    if isinstance(prechecks, Mapping) and isinstance(prechecks.get(chiave), Mapping):
        return prechecks[chiave].get("esito") not in (None, "non_applicabile")
    return bool(bando_voci)


def _nomi_precheck(prechecks: Mapping | None, chiave: str) -> list:
    voce = (prechecks or {}).get(chiave) if isinstance(prechecks, Mapping) else None
    valori = voce.get("bando") if isinstance(voce, Mapping) else None
    return list(valori) if isinstance(valori, list) else []


def requisiti_da_precheck(
    prechecks: Mapping | None, *, bando: Mapping | None = None
) -> list[RequisitoBozza]:
    """Requisiti dai facet del catalogo: `prechecks` è `facet_prechecks(...)`
    o `report["verifiche_strutturate"]`; `bando` è la riga del catalogo con
    le tabelle di collegamento (`bando_regioni`, `bando_codici_ateco`,
    `bando_settori`, `bando_beneficiari`), da cui vengono gli id. Senza id
    (solo i nomi del pre-check) il requisito resta `manuale`. Un facet
    `non_applicabile` non genera nulla."""
    bozze: list[RequisitoBozza] = []

    regioni = _junction(bando, "bando_regioni", "regioni")
    if _applicabile(prechecks, "regione", regioni):
        nomi = [r.get("nome") for r in regioni] or _nomi_precheck(prechecks, "regione")
        testo = f"Sede in una delle regioni ammesse dal bando: {_elenco(nomi)}"
        criterio, ambito = criterio_da_requisito(
            "precheck", {"chiave": "regione", "ids": _ids(regioni)}
        )
        bozze.append(
            RequisitoBozza(
                origine="precheck", testo=_testo(testo) or testo[:TESTO_MAX],
                criterio=criterio, ambito=ambito, rif_origine="regione",
                citazione=_citazione_meta(f"Regioni ammesse: {_elenco(nomi)}"),
                categoria="territoriale",
            )
        )

    codici = _junction(bando, "bando_codici_ateco", "codici_ateco")
    ateco = _applicabile(prechecks, "ateco", codici)
    if ateco:
        divisioni = [ateco_division(c.get("codice")) for c in codici]
        divisioni = [d for d in divisioni if d and d.isdigit() and len(d) == 2]
        if not divisioni:
            divisioni = [
                d for d in _nomi_precheck(prechecks, "ateco")
                if isinstance(d, str) and d.isdigit() and len(d) == 2
            ]
        divisioni = list(dict.fromkeys(divisioni))
        testo = f"Attività in una delle divisioni ATECO ammesse dal bando: {_elenco(divisioni)}"
        criterio, ambito = criterio_da_requisito(
            "precheck", {"chiave": "ateco", "divisioni": divisioni}
        )
        bozze.append(
            RequisitoBozza(
                origine="precheck", testo=_testo(testo) or testo[:TESTO_MAX],
                criterio=criterio, ambito=ambito, rif_origine="ateco",
                citazione=_citazione_meta(f"Codici ATECO ammessi: {_elenco(divisioni)}"),
                categoria="settoriale",
            )
        )

    settori = _junction(bando, "bando_settori", "settori")
    # ATECO e settore sono evidenze alternative (best-of dell'AI-check): con
    # entrambi vale l'ATECO, il filtro giuridicamente preciso.
    if not ateco and _applicabile(prechecks, "settore", settori):
        nomi = [s.get("nome") for s in settori] or _nomi_precheck(prechecks, "settore")
        testo = f"Attività in uno dei settori del bando: {_elenco(nomi)}"
        criterio, ambito = criterio_da_requisito(
            "precheck", {"chiave": "settore", "ids": _ids(settori)}
        )
        bozze.append(
            RequisitoBozza(
                origine="precheck", testo=_testo(testo) or testo[:TESTO_MAX],
                criterio=criterio, ambito=ambito, rif_origine="settore",
                citazione=_citazione_meta(f"Settori: {_elenco(nomi)}"),
                categoria="settoriale",
            )
        )

    beneficiari = _junction(bando, "bando_beneficiari", "beneficiari")
    if _applicabile(prechecks, "beneficiari", beneficiari):
        nomi = [b.get("nome") for b in beneficiari] or _nomi_precheck(prechecks, "beneficiari")
        testo = f"Tipo di beneficiario ammesso dal bando: {_elenco(nomi)}"
        criterio, ambito = criterio_da_requisito(
            "precheck", {"chiave": "beneficiari", "ids": _ids(beneficiari)}
        )
        bozze.append(
            RequisitoBozza(
                origine="precheck", testo=_testo(testo) or testo[:TESTO_MAX],
                criterio=criterio, ambito=ambito, rif_origine="beneficiari",
                citazione=_citazione_meta(f"Beneficiari: {_elenco(nomi)}"),
                categoria="soggettivo",
            )
        )
    return bozze


def _dump_citazione(voce: Any) -> dict | None:
    """La citazione di una voce dello snapshot per il suo requisito. Fa fede
    (`verificata`) solo per una voce `confermata` citata da una pagina di un
    documento ufficiale (`da_pagina_ufficiale`): una voce `modificata` (tra
    cui ogni voce della scheda del catalogo, che entra solo così) la porta come
    riferimento, e il validatore attribuisce il requisito al creatore, come
    `partenariato_validatore._origine_voce` sullo snapshot. Anche uno snapshot
    confermato prima della regola dei documenti ufficiali, con una voce
    `confermata` citata dalla scheda, non dà requisiti verificati."""
    citazione: CitazioneIn | None = voce.citazione
    if citazione is None:
        return None
    dati = citazione.model_dump(mode="json", exclude_none=True)
    if voce.origine_voce != "confermata" or not da_pagina_ufficiale(dati):
        dati["verificata"] = False
    return dati


def _testo_composizione(voce: ComposizioneSnapshot) -> str:
    if voce.tipo_soggetto == "altro":
        tipo = voce.tipo_soggetto_testo or "altro"
    else:
        etichetta = voc.TIPI_SOGGETTO[voce.tipo_soggetto].etichetta
        # «Organismo di ricerca» → «organismo di ricerca», ma «PMI» resta.
        if len(etichetta) > 1 and etichetta[1].islower():
            etichetta = etichetta[0].lower() + etichetta[1:]
        tipo = etichetta
        if voce.tipo_soggetto_testo:
            tipo += f" ({voce.tipo_soggetto_testo})"
    if voce.minimo and voce.minimo > 1:
        quanti = f"Almeno {voce.minimo} soggetti di tipo"
    else:
        quanti = "Almeno un soggetto di tipo"
    testo = f"{quanti} {tipo}{_ETICHETTE_RUOLO.get(voce.ruolo, '')}"
    if voce.massimo is not None:
        testo += f" (al massimo {voce.massimo})"
    if voce.regioni or voce.vincolo_territoriale:
        testo += ", con vincolo territoriale"
    return testo


def requisiti_da_regole(
    snapshot: RegoleCallSnapshot | Mapping | None, *, regioni_bando: Sequence[int] = ()
) -> list[RequisitoBozza]:
    """Requisiti dalle regole CONFERMATE dal creatore (snapshot C2).

    - composizione → `tipo_soggetto` ambito consorzio (il numero minimo e
      il ruolo li controlla il validatore sullo snapshot);
    - vincoli: `sede_operativa_regione` → `regione` ogni_membro sulle regioni
      del bando (`regioni_bando`, dal catalogo); `requisito_capofila` e
      `altro` → `manuale`; gli altri restano regole del partenariato;
    - regole finanziarie → `regola_finanziaria` (ogni_membro se per ciascun
      partner, altrimenti consorzio);
    - la citazione fa fede solo per le voci `confermata` (`_dump_citazione`)."""
    if snapshot is None:
        return []
    if not isinstance(snapshot, RegoleCallSnapshot):
        snapshot = RegoleCallSnapshot.model_validate(snapshot)
    bozze: list[RequisitoBozza] = []
    for voce in snapshot.composizione:
        criterio, ambito = criterio_da_requisito("bando_partenariato", voce)
        testo = _testo_composizione(voce)
        bozze.append(
            RequisitoBozza(
                origine="bando_partenariato", testo=_testo(testo) or testo[:TESTO_MAX],
                criterio=criterio, ambito=ambito,
                # identità: il tipo di soggetto e il ruolo (numeri e vincoli
                # si possono ritoccare senza cambiare requisito)
                rif_origine=rif_con_impronta(voce.id, voce.tipo_soggetto,
                                             voce.tipo_soggetto_testo, voce.ruolo),
                citazione=_dump_citazione(voce), categoria="composizione",
            )
        )
    for voce in snapshot.vincoli:
        if voce.tipo not in _VINCOLI_REQUISITO:
            continue
        criterio, ambito = criterio_da_requisito(
            "bando_partenariato", voce, regioni_ids=regioni_bando
        )
        bozze.append(
            RequisitoBozza(
                origine="bando_partenariato",
                testo=_testo(voce.descrizione) or "Vincolo del bando",
                criterio=criterio, ambito=ambito,
                rif_origine=rif_con_impronta(voce.id, voce.tipo, voce.descrizione),
                citazione=_dump_citazione(voce),
                categoria="territoriale" if voce.tipo == "sede_operativa_regione" else "altro",
            )
        )
    for voce in snapshot.regole_finanziarie:
        criterio, ambito = criterio_da_requisito("regola_finanziaria", voce)
        bozze.append(
            RequisitoBozza(
                origine="regola_finanziaria",
                testo=_testo(voce.descrizione) or "Regola finanziaria del bando",
                criterio=criterio, ambito=ambito, rif_origine=_rif(voce.id),
                citazione=_dump_citazione(voce), categoria="economico",
            )
        )
    return bozze


# ------------------------------------------------------------------ unisci


def _json_criterio(criterio: CriterioPartner | None) -> Any:
    return None if criterio is None else CRITERIO_ADAPTER.dump_python(criterio, mode="json")


def _tipizzato(bozza: RequisitoBozza) -> bool:
    return bozza.criterio is not None and not isinstance(bozza.criterio, CriterioManuale)


def _assorbi(destinazione: RequisitoBozza, fonte: RequisitoBozza) -> None:
    destinazione.esiti_ai_check = (*destinazione.esiti_ai_check, *fonte.esiti_ai_check)
    rif = fonte.rif_origine or fonte.origine
    destinazione.unito_con = (*destinazione.unito_con, rif, *fonte.unito_con)


def _stessa_regione(a: RequisitoBozza, b: RequisitoBozza) -> bool:
    return (
        isinstance(a.criterio, CriterioRegione)
        and isinstance(b.criterio, CriterioRegione)
        and a.ambito == b.ambito
        and set(a.criterio.regioni_ids) == set(b.criterio.regioni_ids)
    )


def _bersaglio(risultato: list[RequisitoBozza], bozza: RequisitoBozza) -> RequisitoBozza | None:
    """Il requisito tipizzato che assorbe una voce dell'AI-check."""
    if bozza.premiale:
        return None
    if bozza.categoria == "territoriale":
        return next((r for r in risultato if isinstance(r.criterio, CriterioRegione)), None)
    if bozza.categoria == "settoriale":
        return next(
            (r for r in risultato if isinstance(r.criterio, (CriterioAteco, CriterioSettore))),
            None,
        )
    if bozza.sintetico and bozza.categoria in ("soggettivo", "dimensionale"):
        # La voce sintetica dei beneficiari ripete il pre-check.
        return next(
            (
                r for r in risultato
                if r.origine == "precheck" and isinstance(r.criterio, CriterioTipoSoggetto)
            ),
            None,
        )
    return None


def unisci(
    *,
    da_regole: Iterable[RequisitoBozza] = (),
    da_precheck: Iterable[RequisitoBozza] = (),
    da_ai_check: Iterable[RequisitoBozza] = (),
) -> list[RequisitoBozza]:
    """Un'unica lista senza doppioni, nell'ordine regole → catalogo →
    AI-check (è anche la precedenza: resta il criterio della fonte più
    affidabile, le altre diventano evidenze della copertura).

    - due requisiti tipizzati identici (stesso criterio e ambito), o due
      `regione` sulle stesse regioni e con lo stesso ambito, diventano uno;
    - una voce dell'AI-check territoriale o settoriale (o una sua voce
      sintetica di catalogo) è assorbita dal requisito tipizzato della
      stessa categoria, se c'è; il suo verdetto resta come evidenza e sulla
      copertura prevale il peggiore;
    - i criteri di valutazione (premiali) non si assorbono mai."""
    risultato: list[RequisitoBozza] = []
    for bozza in (*da_regole, *da_precheck):
        bozza = replace(bozza)
        if _tipizzato(bozza):
            chiave = _json_criterio(bozza.criterio)
            doppione = next(
                (
                    r for r in risultato
                    if _tipizzato(r)
                    and (
                        (r.ambito == bozza.ambito and _json_criterio(r.criterio) == chiave)
                        or _stessa_regione(r, bozza)
                    )
                ),
                None,
            )
            if doppione is not None:
                _assorbi(doppione, bozza)
                continue
        risultato.append(bozza)
    for bozza in da_ai_check:
        bozza = replace(bozza)
        bersaglio = _bersaglio(risultato, bozza)
        if bersaglio is not None:
            _assorbi(bersaglio, bozza)
            continue
        risultato.append(bozza)
    return risultato


# Origini dei requisiti che in `unisci` possono assorbire le voci dell'AI-check.
_ORIGINI_TIPIZZATE = frozenset({"bando_partenariato", "precheck", "regola_finanziaria"})


def evidenze_assorbite(
    requisiti: Sequence[RequisitoBozza], da_ai_check: Iterable[RequisitoBozza]
) -> None:
    """Al SALVATAGGIO dei requisiti: attacca ai requisiti tipizzati (regole e
    catalogo) i verdetti delle voci dell'AI-check che `unisci` avrebbe
    assorbito in loro, con la stessa regola (`_bersaglio`). Così la copertura
    salvata è quella della proposta (prevale il peggiore) anche se il client
    non rimanda le evidenze. Modifica `requisiti` sul posto."""
    candidati = [r for r in requisiti if r.origine in _ORIGINI_TIPIZZATE]
    for voce in da_ai_check:
        bersaglio = _bersaglio(candidati, voce)
        if bersaglio is not None:
            bersaglio.esiti_ai_check = (*bersaglio.esiti_ai_check, *voce.esiti_ai_check)


# Origini la cui citazione può far fede, se il requisito è ancora quello che il
# server genera dalla stessa fonte.
_ORIGINI_CON_PROVA = frozenset({"bando_partenariato", "regola_finanziaria", "ai_check"})


def _compatto(testo: Any) -> str:
    # Come lo schema del client (`RequisitoIn`): senza caratteri invisibili.
    return " ".join(senza_invisibili(testo).split()) if isinstance(testo, str) else ""


def _stesso_requisito(richiesto: Any, bozza: RequisitoBozza) -> bool:
    """Il requisito inviato è ancora quello generato dalla fonte: stesso
    testo (a meno degli spazi), stesso criterio e stesso ambito. Un requisito
    riscritto dal creatore è suo, anche se conserva origine e riferimento."""
    criterio = getattr(richiesto, "criterio", None) or CriterioManuale()
    return (
        _compatto(getattr(richiesto, "testo", None)) == _compatto(bozza.testo)
        and getattr(richiesto, "ambito", None) == bozza.ambito
        and _json_criterio(criterio) == _json_criterio(bozza.criterio or CriterioManuale())
    )


def _non_verificata(citazione: Any) -> dict | None:
    if isinstance(citazione, BaseModel):
        citazione = citazione.model_dump(mode="json")
    if not isinstance(citazione, Mapping):
        return None
    return {**citazione, "verificata": False}


def citazioni_dal_server(
    requisiti: Sequence[Any],
    *,
    da_regole: Iterable[RequisitoBozza] = (),
    da_ai_check: Iterable[RequisitoBozza] = (),
) -> list[dict | None]:
    """La citazione da SALVARE per ogni requisito inviato dal client
    (`RequisitoIn`), con `verificata` decisa dal server: quella del client
    non conta mai.

    - `bando_partenariato` e `regola_finanziaria`: la citazione della voce
      dello snapshot confermato con lo stesso riferimento (`da_regole` =
      `requisiti_da_regole` sullo snapshot della call), verificata solo se la
      voce è `confermata` e citata da una pagina ufficiale
      (`_dump_citazione`);
    - `ai_check`: la citazione della voce dell'ultimo AI-check `ready` con lo
      stesso riferimento (id~impronta, `da_ai_check`), come nella proposta;
    - in entrambi i casi il requisito deve essere ancora quello generato
      (`_stesso_requisito`); altrimenti, e per `manuale` e `precheck`, la
      citazione del client resta come riferimento ma NON verificata (il
      validatore attribuisce il requisito al creatore; un pre-check vale come
      «dati del catalogo» solo se lo conferma `origini_dal_server`)."""
    fonti: dict[tuple[str, str], RequisitoBozza] = {}
    for bozza in (*da_regole, *da_ai_check):
        if bozza.rif_origine:
            fonti.setdefault((bozza.origine, bozza.rif_origine), bozza)
    uscita: list[dict | None] = []
    for richiesto in requisiti:
        origine = getattr(richiesto, "origine", None)
        rif = getattr(richiesto, "rif_origine", None)
        bozza = fonti.get((origine, rif)) if origine in _ORIGINI_CON_PROVA and rif else None
        if bozza is not None and _stesso_requisito(richiesto, bozza):
            uscita.append(dict(bozza.citazione) if isinstance(bozza.citazione, Mapping)
                          else None)
            continue
        uscita.append(_non_verificata(getattr(richiesto, "citazione", None)))
    return uscita


def origini_dal_server(
    requisiti: Sequence[Any], *, da_precheck: Iterable[RequisitoBozza] = ()
) -> list[tuple[str, str | None]]:
    """(origine, rif_origine) da SALVARE per ogni requisito inviato dal
    client (`RequisitoIn`): anche l'origine dichiarata non fa fede. Un
    pre-check resta tale solo se è ancora uno di quelli che il server genera
    ora dai facet del bando (`da_precheck` = `requisiti_da_precheck`), con lo
    stesso riferimento e lo stesso contenuto (`_stesso_requisito`); inventato,
    riscritto o senza catalogo leggibile (fail-closed) è del creatore:
    `manuale`, senza riferimento (criterio e copertura non cambiano). Le
    altre origini restano quelle inviate: la loro prova la decide
    `citazioni_dal_server`."""
    generati: dict[str, RequisitoBozza] = {}
    for bozza in da_precheck:
        if bozza.rif_origine:
            generati.setdefault(bozza.rif_origine, bozza)
    uscita: list[tuple[str, str | None]] = []
    for richiesto in requisiti:
        origine = getattr(richiesto, "origine", None) or "manuale"
        rif = getattr(richiesto, "rif_origine", None)
        if origine == "precheck":
            bozza = generati.get(rif) if rif else None
            if bozza is None or not _stesso_requisito(richiesto, bozza):
                uscita.append(("manuale", None))
                continue
        uscita.append((origine, rif))
    return uscita


# ---------------------------------------------------------------- copertura


def etichetta_breve(indice: int) -> str:
    """Etichette stabili in ordine: A…Z, poi AA, AB, … (come la RPC)."""
    lettere = ""
    n = indice + 1
    while n > 0:
        n, resto = divmod(n - 1, 26)
        lettere = chr(ord("A") + resto) + lettere
    return lettere


def _peggiore(evidenze: list[tuple[str, str, str | None]]) -> tuple[str, str, str | None]:
    """(esito, fonte, motivo) peggiore; `non_valutabile` solo se non c'è
    altro. A parità vince la prima evidenza (il criterio tipizzato)."""
    valutabili = [e for e in evidenze if e[0] in _GRAVITA]
    if not valutabili:
        return evidenze[0]
    return max(valutabili, key=lambda e: _GRAVITA[e[0]])


def _come_bozza(requisito: Any) -> RequisitoBozza:
    """Normalizza RequisitoBozza, `RequisitoIn`/`RequisitoOut` o una riga di
    `partner_call_requisiti` (criterio jsonb riletto con criterio_da_json;
    una copertura salvata con fonte `ai_check` resta come evidenza)."""
    if isinstance(requisito, RequisitoBozza):
        return requisito
    if isinstance(requisito, BaseModel):
        dati = requisito.model_dump()
        criterio = getattr(requisito, "criterio", None)
    elif isinstance(requisito, Mapping):
        dati = dict(requisito)
        criterio = criterio_da_json(dati.get("criterio"))
    else:
        raise TypeError("requisito non riconosciuto")
    esiti: tuple[str, ...] = tuple(dati.get("esiti_ai_check") or ())
    if (
        not esiti
        and dati.get("copertura_fonte") == "ai_check"
        and dati.get("copertura_creatore") in _GRAVITA
    ):
        esiti = (dati["copertura_creatore"],)
    identificativo = dati.get("id")
    if identificativo is not None and not isinstance(identificativo, UUID):
        try:
            identificativo = UUID(str(identificativo))
        except ValueError:
            identificativo = None
    return RequisitoBozza(
        origine=dati.get("origine") or "manuale",
        testo=dati.get("testo") or "",
        criterio=criterio,
        ambito=dati.get("ambito") or "consorzio",
        rif_origine=dati.get("rif_origine"),
        citazione=dati.get("citazione"),
        esiti_ai_check=esiti,
        cercato=dati.get("cercato"),
        id=identificativo,
    )


def _citazione_out(dato: Any) -> CitazioneIn | None:
    if isinstance(dato, CitazioneIn):
        return dato
    if not isinstance(dato, Mapping):
        return None
    url = dato.get("url_documento")
    pagina = dato.get("pagina")
    try:
        return CitazioneIn(
            sezione=str(dato.get("sezione") or "")[:40],
            testo=str(dato.get("testo") or "")[:2000],
            verificata=dato.get("verificata") is True,
            fonte_etichetta=(str(dato["fonte_etichetta"])[:300]
                             if dato.get("fonte_etichetta") else None),
            url_documento=url if isinstance(url, str) and url.lower().startswith("https://")
            else None,
            pagina=pagina if isinstance(pagina, int) and pagina >= 1 else None,
        )
    except ValueError:
        return None


def _speciale(bozza: RequisitoBozza, ruolo_creatore: str | None) -> str | None:
    """Regole finanziarie che non si valutano sul solo creatore."""
    if not isinstance(bozza.criterio, CriterioRegolaFinanziaria):
        return None
    ambito = bozza.criterio.regola.ambito
    if ambito in _AMBITI_AGGREGATI:
        return "aggregata"
    if ambito == "capofila" and ruolo_creatore == "cerco_capofila":
        return "capofila"
    return None


def copertura_creatore(
    requisiti: Iterable[Any],
    profilo_creatore: ProfiloCandidato,
    costo_quota: Intervallo | None = None,
    *,
    ruolo_creatore: str | None = None,
) -> list[RequisitoOut]:
    """Copertura del creatore per ogni requisito (vista «proprio»), con
    etichette brevi «A», «B», … nell'ordine ricevuto.

    Evidenze: l'esito di `valuta_criterio` sul criterio (un `manuale` non
    decide nulla) e i verdetti dell'AI-check assorbiti; prevale il peggiore.
    Le regole finanziarie aggregate (media pesata, partenariato totale) e
    quelle del capofila in una call «cerco capofila» non si valutano sul
    solo creatore. `cercato`: quello indicato, altrimenti sì per ciò che il
    creatore non copre con ambito consorzio (mai per i criteri premiali).
    `copertura_nota` solo da `NOTE_COPERTURA`."""
    risultato: list[RequisitoOut] = []
    for indice, grezzo in enumerate(requisiti):
        bozza = _come_bozza(grezzo)
        speciale = _speciale(bozza, ruolo_creatore)
        if speciale:
            evidenze: list[tuple[str, str, str | None]] = [("non_valutabile", "nessuna", speciale)]
        else:
            valutazione = valuta_criterio_dettaglio(
                bozza.criterio, profilo_creatore, costo_quota=costo_quota, vista="proprio"
            )
            evidenze = [
                (
                    valutazione.esito.esito,
                    valutazione.esito.fonte or "nessuna",
                    valutazione.motivo,
                )
            ]
        evidenze.extend((esito, "ai_check", None) for esito in bozza.esiti_ai_check)
        esito, fonte, motivo = _peggiore(evidenze)
        cercato = bozza.cercato
        if cercato is None:
            cercato = (
                esito == "non_coperto" and bozza.ambito == "consorzio" and not bozza.premiale
            )
        risultato.append(
            RequisitoOut(
                id=bozza.id,
                etichetta=etichetta_breve(indice),
                testo=bozza.testo,
                criterio=bozza.criterio,
                ambito=bozza.ambito,
                cercato=cercato,
                origine=bozza.origine,
                rif_origine=_rif(bozza.rif_origine),
                citazione=_citazione_out(bozza.citazione),
                copertura_creatore=esito,
                copertura_fonte=fonte,
                copertura_nota=nota_copertura(esito, fonte, motivo),
                ordine=indice,
            )
        )
    return risultato


# Chiavi accettate da fn_partner_call_sostituisci_requisiti (0037).
CHIAVI_RPC_REQUISITO = (
    "id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine", "rif_origine",
    "citazione", "copertura_creatore", "copertura_fonte", "copertura_nota",
)


def payload_requisito(requisito: RequisitoOut, *, con_etichetta: bool = False) -> dict:
    """Elemento di `p_requisiti` per la RPC di sostituzione: solo le sue
    chiavi, in forma JSON, senza `id` nullo. L'etichetta si manda solo se
    richiesto (di norma la assegna la RPC: quella della proposta è solo
    provvisoria)."""
    dati = requisito.model_dump(mode="json", exclude_none=False)
    payload = {chiave: dati[chiave] for chiave in CHIAVI_RPC_REQUISITO if chiave in dati}
    if payload.get("id") is None:
        payload.pop("id", None)
    if not con_etichetta:
        payload.pop("etichetta", None)
    return payload


def riepilogo(requisiti: Iterable[RequisitoOut]) -> RiepilogoGapOut:
    conteggi = {"coperto": 0, "non_coperto": 0, "dato_mancante": 0, "incerto": 0,
                "non_valutabile": 0}
    for requisito in requisiti:
        if requisito.copertura_creatore in conteggi:
            conteggi[requisito.copertura_creatore] += 1
    return RiepilogoGapOut(
        coperti=conteggi["coperto"],
        non_coperti=conteggi["non_coperto"],
        dato_mancante=conteggi["dato_mancante"],
        incerto=conteggi["incerto"],
        non_valutabile=conteggi["non_valutabile"],
    )


# ------------------------------------------------------------ budget e regole


def intervallo_budget(
    budget_fascia: str | None, budget_progetto_eur: Decimal | float | str | None = None
) -> Intervallo | None:
    """Budget del progetto come intervallo: il punto esatto se c'è (RISERVATO:
    solo per la vista «proprio» del creatore o per chi ne ha diritto),
    altrimenti gli estremi della fascia pubblica (`oltre_5m` senza tetto:
    Infinity). None se non c'è né l'uno né l'altra."""
    if budget_progetto_eur is not None:
        valore = Decimal(str(budget_progetto_eur))
        return (valore, valore)
    if budget_fascia in ESTREMI_BUDGET:
        minimo, massimo = ESTREMI_BUDGET[budget_fascia]
        return (
            Decimal(minimo),
            Decimal("Infinity") if massimo is None else Decimal(massimo),
        )
    return None


# Campi di dominio di ogni sezione dello snapshot: una voce `confermata` deve
# coincidere su questi campi (e sulla citazione) con la voce `verificata`
# dell'estrazione WP3 corrente.
_CAMPI_CONFERMA: dict[str, tuple[str, ...]] = {
    "composizione": (
        "tipo_soggetto", "tipo_soggetto_testo", "minimo", "massimo", "ruolo", "regioni",
        "paesi", "vincolo_territoriale",
    ),
    "quote": (
        "ambito", "categoria", "min_percentuale", "max_percentuale", "base_calcolo",
        "effetto_violazione",
    ),
    "vincoli": ("tipo", "descrizione", "parametro", "momento"),
    "regole_finanziarie": tuple(RegolaFinanziaria.model_fields),
    "documenti_richiesti": ("tipo", "descrizione", "momento"),
}
_NOMI_SEZIONE = {
    "modalita": "la modalità",
    "costituzione": "la costituzione",
    "partner_min": "il numero minimo di partner",
    "partner_max": "il numero massimo di partner",
    "forme_ammesse": "una forma ammessa",
    "composizione": "una voce della composizione",
    "quote": "una quota",
    "vincoli": "un vincolo",
    "regole_finanziarie": "una regola finanziaria",
    "documenti_richiesti": "un documento richiesto",
}


def _stessa_citazione(snapshot_voce: dict, estratta: Mapping) -> bool:
    mia = snapshot_voce.get("citazione") or {}
    sua = estratta.get("citazione") or {}
    return (
        estratta.get("stato") == "verificata"
        and isinstance(sua, Mapping)
        and sua.get("verificata") is True
        and mia.get("sezione") == sua.get("sezione")
        and mia.get("testo") == sua.get("testo")
    )


def errori_voci_confermate(
    snapshot: RegoleCallSnapshot | Mapping, regole_estratte: BaseModel | Mapping | None
) -> list[str]:
    """Una voce `confermata` dello snapshot deve esistere nell'estrazione WP3
    CORRENTE (`bando_partenariato.regole`), essere `verificata` e coincidere
    con lei (stessi campi, stessa citazione): altrimenti va marcata
    `modificata`. Restituisce gli errori (vuota = ok). Senza estrazione nessuna
    voce può dirsi confermata."""
    if not isinstance(snapshot, RegoleCallSnapshot):
        snapshot = RegoleCallSnapshot.model_validate(snapshot)
    if isinstance(regole_estratte, BaseModel):
        estratte: Mapping = regole_estratte.model_dump(mode="json")
    else:
        estratte = regole_estratte if isinstance(regole_estratte, Mapping) else {}
    mio = snapshot.model_dump(mode="json")
    errori: list[str] = []

    def errore(sezione: str) -> None:
        errori.append(
            f"Non puoi confermare così com'è {_NOMI_SEZIONE[sezione]}: nel bando non risulta "
            "verificata o è cambiata. Segnala che l'hai modificata"
        )

    for sezione in ("modalita", "costituzione", "partner_min", "partner_max"):
        voce = mio.get(sezione)
        if not voce or voce.get("origine_voce") != "confermata":
            continue
        estratta = estratte.get(sezione)
        if not (
            isinstance(estratta, Mapping)
            and estratta.get("valore") == voce.get("valore")
            and _stessa_citazione(voce, estratta)
        ):
            errore(sezione)

    forme = {
        f.get("forma"): f for f in estratte.get("forme_ammesse") or [] if isinstance(f, Mapping)
    }
    for voce in mio.get("forme_ammesse") or []:
        if voce.get("origine_voce") != "confermata":
            continue
        estratta = forme.get(voce.get("forma"))
        if not (
            isinstance(estratta, Mapping)
            and estratta.get("note") == voce.get("note")
            and _stessa_citazione(voce, estratta)
        ):
            errore("forme_ammesse")

    for sezione, campi in _CAMPI_CONFERMA.items():
        per_id = {
            e.get("id"): e for e in estratte.get(sezione) or [] if isinstance(e, Mapping)
        }
        for voce in mio.get(sezione) or []:
            if voce.get("origine_voce") != "confermata":
                continue
            estratta = per_id.get(voce.get("id"))
            if not (
                isinstance(estratta, Mapping)
                and all(estratta.get(campo) == voce.get(campo) for campo in campi)
                and _stessa_citazione(voce, estratta)
            ):
                errore(sezione)
    return errori


def errori_regole_finanziarie(
    requisiti: Iterable[Any], snapshot: RegoleCallSnapshot | Mapping | None
) -> list[str]:
    """Q11: ogni requisito `regola_finanziaria` deve essere una regola
    confermata dello snapshot, identica (stesso `rif_origine` = id e stessi
    campi del contratto WP1). Restituisce gli errori (vuota = ok)."""
    regole: dict[str, dict] = {}
    if snapshot is not None:
        if not isinstance(snapshot, RegoleCallSnapshot):
            snapshot = RegoleCallSnapshot.model_validate(snapshot)
        regole = {r.id: r.regola().model_dump(mode="json") for r in snapshot.regole_finanziarie}
    errori: list[str] = []
    for requisito in requisiti:
        bozza = _come_bozza(requisito)
        if not isinstance(bozza.criterio, CriterioRegolaFinanziaria):
            continue
        attesa = regole.get(bozza.rif_origine or "")
        ricevuta = RegolaFinanziaria(
            **{
                nome: getattr(bozza.criterio.regola, nome)
                for nome in RegolaFinanziaria.model_fields
            }
        ).model_dump(mode="json")
        if attesa is None or attesa != ricevuta:
            errori.append(
                f"La regola finanziaria «{(bozza.rif_origine or '')[:20]}» non è tra le regole "
                "del bando confermate"
            )
    return errori
