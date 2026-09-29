"""Valutazione dei criteri tipizzati (WP5, docs/partenariati.md C1).

Modulo PURO (nessun I/O, nessun LLM). `valuta_criterio` è l'unica funzione
che dice se un'azienda copre un `CriterioPartner`: la usano la gap analysis
della call (WP5, vista «proprio» del creatore), il matching (WP6), la
valutazione delle candidature (WP7) e il validatore del consorzio (WP8).

Regole:
- «tutte le sedi»: la regione è coperta se UNA qualunque sede del registro
  (legale o unità locale, `openapi_mapping.company_regioni_ids`) è in una
  delle regioni; con `sede_entro_erogazione` una sede mancante dà `incerto`
  (la si può aprire entro l'erogazione), mai `non_coperto`;
- registro prima del dichiarato (T5, P3): dimensione, tipi dimensionali,
  startup/PMI innovativa, impresa artigiana e cooperativa vengono SOLO dal
  Registro Imprese; un tipo dichiarato che il registro dovrebbe dare non
  conta. ATECO, regioni e paese vengono solo dal registro;
- regole finanziarie tramite `bilanci_indicatori.valuta_regola_finanziaria`
  con la stessa vista: «proprio» sui valori esatti, «terzi» sulle fasce;
- `manuale` → `non_valutabile`, sempre.

Esiti: `coperto` e `non_coperto` solo su dato certo; `dato_mancante` quando
il dato che servirebbe non c'è; `incerto` quando c'è ma non decide (fascia a
cavallo della soglia, sede da aprire, certificazione non classificata…).
Nel dichiarato una lista vuota (competenze, certificazioni, esperienze) vale
`dato_mancante`, non `non_coperto`: il profilo non lo dice. I tipi di
soggetto sono l'eccezione: il profilo li chiede esplicitamente, quindi con
un profilo presente un tipo non dichiarato è `non_coperto`.
`testo_pubblico` dipende SOLO da tipo di criterio ed esito, mai dai dati
dell'azienda: si può mostrare a terzi. `testo_privato` solo in vista
«proprio».
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from pydantic import ValidationError

from app.core.errors import AppError
from app.schemas.partenariato_criteri import (
    CRITERIO_ADAPTER,
    CriterioAteco,
    CriterioCertificazione,
    CriterioDimensione,
    CriterioEsperienza,
    CriterioManuale,
    CriterioPaese,
    CriterioPartner,
    CriterioRegione,
    CriterioRegolaFinanziaria,
    CriterioSettore,
    CriterioTag,
    CriterioTipoSoggetto,
    EsitoCriterio,
    EsperienzaCandidato,
    Intervallo,
    ProfiloCandidato,
)
from app.schemas.partner_profile import TIPI_SOGGETTO_DA_REGISTRO
from app.services import partenariato_vocabolario as voc
from app.services.bilanci_indicatori import (
    MOTIVO_DIPENDE_BUDGET,
    MOTIVO_DIPENDE_FASCIA,
    MOTIVO_STORICO_INCOMPLETO,
    EsercizioBilancio,
    calcola_fasce,
    valuta_regola_finanziaria,
)
from app.services.openapi_mapping import ateco_division, company_regioni_ids
from app.services.partner_profilo_pubblico import (
    CLASSI_DIMENSIONALI,
    categoria_certificazione,
    tipi_soggetto_dedotti,
)

logger = logging.getLogger("bandofit.partenariati")

Vista = Literal["proprio", "terzi"]

# Esito della regola finanziaria → esito del criterio. «dipende dal budget /
# dalla fascia» e lo storico incompleto sono incertezze: il dato c'è.
_MOTIVI_INCERTI = frozenset(
    {MOTIVO_DIPENDE_BUDGET, MOTIVO_DIPENDE_FASCIA, MOTIVO_STORICO_INCOMPLETO}
)
MOTIVO_COSTO_QUOTA = "manca il costo della quota"
# Motivo della regola → codice macchina (per i template della gap analysis).
_CODICI_MOTIVO = {
    MOTIVO_COSTO_QUOTA: "costo_quota_mancante",
    MOTIVO_DIPENDE_BUDGET: "dipende_budget",
    MOTIVO_DIPENDE_FASCIA: "dipende_fascia",
    MOTIVO_STORICO_INCOMPLETO: "storico_incompleto",
}

# Tipi di soggetto che si deducono dalla classe dimensionale del registro.
_TIPI_DIMENSIONALI = frozenset(
    {"micro_impresa", "piccola_impresa", "media_impresa", "grande_impresa", "pmi"}
)
_TIPI_DA_FLAG = ("startup_innovativa", "pmi_innovativa", "impresa_artigiana")
_FLAG_REGISTRO = (*_TIPI_DA_FLAG, "certificazione_soa")

# Ordine di preferenza quando un criterio a più valori ha esiti diversi.
_MIGLIORE = {"coperto": 0, "incerto": 1, "dato_mancante": 2, "non_coperto": 3}
_FONTE_PREFERITA = {"registro": 0, "bilanci": 1, "dichiarato": 2, None: 3}


# -------------------------------------------------------------- testi

_NOME_CRITERIO: dict[str, str] = {
    "tipo_soggetto": "Tipo di soggetto",
    "tag": "Competenze",
    "regione": "Sede nella regione",
    "paese": "Paese",
    "ateco": "Attività (ATECO)",
    "settore": "Settore",
    "dimensione": "Dimensione",
    "certificazione": "Certificazioni",
    "esperienza": "Esperienza nei programmi",
    "regola_finanziaria": "Regola finanziaria",
    "manuale": "Requisito descritto a testo",
}

_TESTO_ESITO: dict[str, str] = {
    "coperto": "coperto",
    "non_coperto": "non coperto",
    "dato_mancante": "dato mancante",
    "incerto": "da verificare",
    "non_valutabile": "da valutare a mano",
}

_TESTO_FONTE: dict[str, str] = {
    "registro": "Registro Imprese",
    "bilanci": "bilanci",
    "dichiarato": "dichiarato",
}


def testo_pubblico(tipo: str, esito: str, fonte: str | None, motivo: str | None = None) -> str:
    """Testo mostrabile a terzi: solo tipo di criterio, esito, fonte e un
    motivo generico (costante). Mai dati dell'azienda valutata."""
    testo = f"{_NOME_CRITERIO[tipo]}: {_TESTO_ESITO[esito]}"
    dettagli = [d for d in (_TESTO_FONTE.get(fonte or ""), motivo) if d]
    if dettagli:
        testo += f" ({', '.join(dettagli)})"
    return testo


@dataclass(frozen=True)
class Valutazione:
    """Esito con il motivo macchina (per i template della gap analysis)."""

    esito: EsitoCriterio
    # Solo per le regole finanziarie: costo_quota_mancante | dipende_budget |
    # dipende_fascia | storico_incompleto; None altrimenti.
    motivo: str | None = None


def _esito(
    criterio_tipo: str,
    esito: str,
    fonte: str | None,
    vista: Vista,
    privato: str | None = None,
    motivo_pubblico: str | None = None,
) -> EsitoCriterio:
    return EsitoCriterio(
        esito=esito,
        fonte=fonte,
        testo_pubblico=testo_pubblico(criterio_tipo, esito, fonte, motivo_pubblico),
        testo_privato=privato if vista == "proprio" else None,
    )


def _etichette_tipi(codici: Iterable[str]) -> str:
    return ", ".join(voc.TIPI_SOGGETTO[c].etichetta for c in codici if c in voc.TIPI_SOGGETTO)


# ------------------------------------------------------------ builder


def _campo(riga: Any, *percorso: str) -> Any:
    valore = riga
    for chiave in percorso:
        if not isinstance(valore, Mapping):
            return None
        valore = valore.get(chiave)
    return valore


def _stringhe(valore: Any) -> list[str]:
    if not isinstance(valore, (list, tuple)):
        return []
    return [v for v in valore if isinstance(v, str)]


def _forma_giuridica(dossier: Mapping | None) -> str | None:
    parti = [
        _campo(dossier, "anagrafica", "forma_giuridica"),
        _campo(dossier, "anagrafica", "forma_giuridica_dettaglio"),
    ]
    testo = " ".join(p.strip() for p in parti if isinstance(p, str) and p.strip())
    return testo or None


def _divisioni(derived: Mapping, dossier: Mapping | None) -> tuple[str, ...]:
    codici: list[Any] = [
        derived.get("ateco_divisione"),
        derived.get("ateco_principale"),
        *(_stringhe(derived.get("ateco_secondari"))),
        _campo(dossier, "attivita", "ateco", "codice"),
        _campo(dossier, "attivita", "ateco_2022", "codice"),
    ]
    divisioni = []
    for codice in codici:
        divisione = ateco_division(codice)
        if divisione and divisione.isdigit() and len(divisione) == 2:
            divisioni.append(divisione)
    return tuple(dict.fromkeys(divisioni))


def _esercizi(righe: Iterable[Any]) -> tuple[EsercizioBilancio, ...]:
    esercizi = [
        r if isinstance(r, EsercizioBilancio) else EsercizioBilancio.da_riga(r)
        for r in righe or ()
    ]
    return tuple(sorted(esercizi, key=lambda e: e.anno))


def profilo_candidato_da(
    *,
    company: Mapping | None = None,
    derived: Mapping | None = None,
    dossier: Mapping | None = None,
    profilo_partner: Mapping | None = None,
    esercizi: Iterable[Any] = (),
    storico_completo: bool = False,
) -> ProfiloCandidato:
    """`ProfiloCandidato` dalle righe GIÀ LETTE (funzione pura, la usa anche
    il WP6 per l'indice):

    - `company`: riga di `company_profiles` (serve solo `settore_id`, il
      settore dichiarato);
    - `derived`: `company_data.derived`; None = nessun dato del registro. Il
      chiamante lo passa solo se i dati del registro sono dell'azienda (T5);
    - `dossier`: `build_dossier(company_data.raw)` (flag delle sezioni
      speciali e SOA, forma giuridica, ATECO di ripiego);
    - `profilo_partner`: riga di `company_partner_profiles` o None;
    - `esercizi`: `EsercizioBilancio` o righe di `company_financials`;
    - `storico_completo`: `bilanci_service.storico_completo(stato)`.

    Dal registro NON si prende nulla dei campi di `company_profiles` che
    l'utente modifica (classe dimensionale, regione, ATECO)."""
    registro = derived is not None
    derived = derived if isinstance(derived, Mapping) else {}

    classe = derived.get("classe_dimensionale")
    classe = classe.strip().lower() if isinstance(classe, str) else None
    classe = classe if classe in CLASSI_DIMENSIONALI else None

    flags: dict[str, bool] = {}
    if registro:
        for nome in _FLAG_REGISTRO:
            valore = _campo(dossier, "flags", nome)
            if isinstance(valore, bool):
                flags[nome] = valore
    forma = _forma_giuridica(dossier) if registro else None
    tipi_registro = (
        tuple(tipi_soggetto_dedotti(derived, _campo(dossier, "flags"), forma)) if registro else ()
    )

    presente = isinstance(profilo_partner, Mapping)
    profilo = profilo_partner if presente else {}
    dichiarati = {
        c
        for c in _stringhe(profilo.get("tipi_soggetto"))
        if c in voc.TIPI_SOGGETTO and c not in TIPI_SOGGETTO_DA_REGISTRO and c not in tipi_registro
    }
    competenze = [c for c in _stringhe(profilo.get("competenze")) if c in voc.COMPETENZE]
    categorie = [
        categoria_certificazione(c) for c in _stringhe(profilo.get("certificazioni")) if c.strip()
    ]
    esperienze = []
    for voce in profilo.get("esperienze") or []:
        if not isinstance(voce, Mapping):
            continue
        programma_id = voce.get("programma_id")
        ruolo = voce.get("ruolo")
        esperienze.append(
            EsperienzaCandidato(
                programma_id=programma_id
                if isinstance(programma_id, int) and not isinstance(programma_id, bool)
                else None,
                ruolo=ruolo if ruolo in voc.RUOLI else None,
            )
        )

    settore = _campo(company, "settore_id")
    lista_esercizi = _esercizi(esercizi)
    return ProfiloCandidato(
        registro_presente=registro,
        classe_dimensionale=classe,
        flags=flags,
        forma_giuridica=forma,
        tipi_registro=tipi_registro,
        regioni_ids=frozenset(company_regioni_ids(None, derived)) if registro else frozenset(),
        paese="IT" if registro else None,
        ateco_divisioni=_divisioni(derived, dossier) if registro else (),
        profilo_presente=presente,
        tipi_dichiarati=tuple(c for c in voc.TIPI_SOGGETTO if c in dichiarati),
        competenze=tuple(dict.fromkeys(competenze)),
        certificazioni_categorie=tuple(dict.fromkeys(categorie)),
        esperienze=tuple(esperienze),
        settori_ids=frozenset(
            {settore} if isinstance(settore, int) and not isinstance(settore, bool) else ()
        ),
        esercizi=lista_esercizi,
        storico_completo=bool(storico_completo),
        fasce=calcola_fasce(lista_esercizi) if lista_esercizi else None,
    )


# --------------------------------------------------------- valutazioni


def _tipo_singolo(codice: str, p: ProfiloCandidato) -> tuple[str, str | None]:
    """(esito, fonte) di un solo tipo di soggetto."""
    if codice in p.tipi_registro:
        return "coperto", "registro"
    if codice in TIPI_SOGGETTO_DA_REGISTRO:
        # Solo dal registro: il dichiarato non conta.
        if not p.registro_presente:
            return "dato_mancante", "registro"
        if codice in _TIPI_DIMENSIONALI:
            return ("non_coperto" if p.classe_dimensionale else "dato_mancante"), "registro"
        if codice in _TIPI_DA_FLAG:
            return ("non_coperto" if p.flags.get(codice) is False else "dato_mancante"), "registro"
        # cooperativa: dalla forma giuridica
        return ("non_coperto" if p.forma_giuridica else "dato_mancante"), "registro"
    if codice in p.tipi_dichiarati:
        return "coperto", "dichiarato"
    if codice == "impresa" and p.registro_presente:
        # Nel registro ma senza tipi deducibili: non si può escludere.
        return "dato_mancante", "registro"
    if p.profilo_presente:
        return "non_coperto", "dichiarato"
    return "dato_mancante", "dichiarato"


def _migliore(esiti: list[tuple[str, str | None]]) -> tuple[str, str | None]:
    return min(esiti, key=lambda e: (_MIGLIORE[e[0]], _FONTE_PREFERITA.get(e[1], 3)))


def _v_tipo_soggetto(c: CriterioTipoSoggetto, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    esiti = [(codice, *_tipo_singolo(codice, p)) for codice in c.valori]
    esito, fonte = _migliore([(e, f) for _, e, f in esiti])
    coperti = [codice for codice, e, f in esiti if e == "coperto" and f == fonte]
    if esito == "coperto":
        origine = (
            "Dal Registro Imprese" if fonte == "registro" else "Dichiarato nel profilo partner"
        )
        privato = f"{origine}: {_etichette_tipi(coperti)}"
    elif esito == "non_coperto":
        privato = "Nessuno dei tipi richiesti risulta dal registro o dal profilo partner"
    else:
        privato = (
            "Importa i dati dal Registro Imprese"
            if fonte == "registro"
            else "Indica i tuoi tipi di soggetto nel profilo partner"
        )
    return _esito("tipo_soggetto", esito, fonte, vista, privato)


def _v_tag(c: CriterioTag, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.competenze:
        return _esito(
            "tag", "dato_mancante", "dichiarato", vista,
            "Indica le tue competenze nel profilo partner",
        )
    presenti = [t for t in c.tags if t in p.competenze]
    coperto = len(presenti) == len(c.tags) if c.modalita == "tutti" else bool(presenti)
    privato = (
        f"Competenze del profilo che corrispondono: {len(presenti)} su {len(c.tags)}"
    )
    return _esito("tag", "coperto" if coperto else "non_coperto", "dichiarato", vista, privato)


def _v_regione(c: CriterioRegione, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.regioni_ids:
        return _esito(
            "regione", "dato_mancante", "registro", vista,
            "Le sedi dell'azienda non risultano: importa i dati dal Registro Imprese",
        )
    # Regola «tutte le sedi»: basta una sede (legale o unità locale).
    if p.regioni_ids & set(c.regioni_ids):
        return _esito(
            "regione", "coperto", "registro", vista,
            "Almeno una sede dell'azienda è in una delle regioni richieste",
        )
    if c.modalita == "sede_entro_erogazione":
        return _esito(
            "regione", "incerto", "registro", vista,
            "Nessuna sede nelle regioni richieste: andrà aperta entro l'erogazione",
            motivo_pubblico="sede da aprire entro l'erogazione",
        )
    return _esito(
        "regione", "non_coperto", "registro", vista,
        "Nessuna sede dell'azienda è nelle regioni richieste",
    )


def _v_paese(c: CriterioPaese, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.paese:
        return _esito("paese", "dato_mancante", "registro", vista, "Paese della sede non noto")
    dentro = p.paese in c.paesi
    coperto = not dentro if c.escludi else dentro
    return _esito(
        "paese", "coperto" if coperto else "non_coperto", "registro", vista,
        f"Paese della sede: {p.paese}",
    )


def _v_ateco(c: CriterioAteco, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.ateco_divisioni:
        return _esito(
            "ateco", "dato_mancante", "registro", vista,
            "Codice ATECO non disponibile: importa i dati dal Registro Imprese",
        )
    comuni = [d for d in p.ateco_divisioni if d in c.divisioni]
    if comuni:
        return _esito(
            "ateco", "coperto", "registro", vista, f"Divisione ATECO {comuni[0]} del registro"
        )
    return _esito(
        "ateco", "non_coperto", "registro", vista,
        "Nessuna divisione ATECO dell'azienda (principale o secondarie) è tra quelle richieste",
    )


def _v_settore(c: CriterioSettore, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.settori_ids:
        return _esito(
            "settore", "dato_mancante", "dichiarato", vista,
            "Indica il settore nei dati dell'azienda",
        )
    coperto = bool(p.settori_ids & set(c.settori_ids))
    return _esito(
        "settore", "coperto" if coperto else "non_coperto", "dichiarato", vista,
        "Settore indicato nei dati dell'azienda",
    )


def _v_dimensione(c: CriterioDimensione, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.classe_dimensionale:
        return _esito(
            "dimensione", "dato_mancante", "registro", vista,
            "Classe dimensionale non disponibile dal Registro Imprese",
        )
    coperto = p.classe_dimensionale in c.valori
    return _esito(
        "dimensione", "coperto" if coperto else "non_coperto", "registro", vista,
        f"Classe dimensionale dal registro: {p.classe_dimensionale}",
    )


def _v_certificazione(
    c: CriterioCertificazione, p: ProfiloCandidato, vista: Vista
) -> EsitoCriterio:
    soa = p.flags.get("certificazione_soa")
    if "appalti_soa" in c.categorie and soa is True:
        return _esito(
            "certificazione", "coperto", "registro", vista,
            "Attestazione SOA risultante dal registro",
        )
    presenti = [k for k in c.categorie if k in p.certificazioni_categorie]
    if presenti:
        return _esito(
            "certificazione", "coperto", "dichiarato", vista,
            "Certificazione dichiarata nel profilo partner",
        )
    if "altro" in p.certificazioni_categorie:
        return _esito(
            "certificazione", "incerto", "dichiarato", vista,
            "Una certificazione del profilo non è classificabile: verifica se è quella richiesta",
            motivo_pubblico="certificazione non classificata",
        )
    if p.certificazioni_categorie:
        return _esito(
            "certificazione", "non_coperto", "dichiarato", vista,
            "Nessuna certificazione del profilo è della categoria richiesta",
        )
    if soa is False and set(c.categorie) == {"appalti_soa"}:
        return _esito(
            "certificazione", "non_coperto", "registro", vista,
            "Il registro non riporta un'attestazione SOA",
        )
    return _esito(
        "certificazione", "dato_mancante", "dichiarato", vista,
        "Indica le certificazioni nel profilo partner",
    )


def _v_esperienza(c: CriterioEsperienza, p: ProfiloCandidato, vista: Vista) -> EsitoCriterio:
    if not p.esperienze:
        return _esito(
            "esperienza", "dato_mancante", "dichiarato", vista,
            "Indica le esperienze nel profilo partner",
        )
    programmi = set(c.programmi_ids)
    nel_programma = [e for e in p.esperienze if e.programma_id in programmi]
    if any(c.ruolo is None or e.ruolo == c.ruolo for e in nel_programma):
        return _esito(
            "esperienza", "coperto", "dichiarato", vista,
            "Esperienza dichiarata nel profilo partner",
        )
    ruolo_ignoto = any(e.ruolo is None for e in nel_programma)
    senza_programma = any(e.programma_id is None for e in p.esperienze)
    if ruolo_ignoto or senza_programma:
        return _esito(
            "esperienza", "incerto", "dichiarato", vista,
            "Nel profilo ci sono esperienze senza programma del catalogo o senza ruolo",
            motivo_pubblico="esperienze non classificate",
        )
    return _esito(
        "esperienza", "non_coperto", "dichiarato", vista,
        "Nessuna esperienza del profilo nei programmi richiesti",
    )


def _v_regola(
    c: CriterioRegolaFinanziaria,
    p: ProfiloCandidato,
    vista: Vista,
    costo_quota: Intervallo | None,
) -> Valutazione:
    esito_regola = valuta_regola_finanziaria(
        c.regola,
        list(p.esercizi),
        costo_quota,
        vista=vista,
        storico_completo=p.storico_completo,
    )
    motivo = esito_regola.motivo
    if esito_regola.esito == "soddisfatto":
        esito = "coperto"
    elif esito_regola.esito == "non_soddisfatto":
        esito = "non_coperto"
    elif motivo in _MOTIVI_INCERTI:
        esito = "incerto"
    else:
        esito = "dato_mancante"
    # Motivo pubblico solo se è una costante generica.
    pubblico = motivo if motivo in _MOTIVI_INCERTI else None
    codice = _CODICI_MOTIVO.get(motivo or "")
    return Valutazione(
        _esito(
            "regola_finanziaria", esito, "bilanci", vista,
            esito_regola.spiegazione_titolare, motivo_pubblico=pubblico,
        ),
        codice,
    )


def valuta_criterio_dettaglio(
    criterio: CriterioPartner | None,
    profilo: ProfiloCandidato,
    *,
    costo_quota: Intervallo | None = None,
    vista: Vista,
) -> Valutazione:
    """Come `valuta_criterio`, con il motivo macchina (per i template)."""
    if criterio is None or isinstance(criterio, CriterioManuale):
        return Valutazione(
            _esito(
                "manuale", "non_valutabile", None, vista,
                "Requisito descritto a testo: si verifica a mano",
            )
        )
    if isinstance(criterio, CriterioRegolaFinanziaria):
        return _v_regola(criterio, profilo, vista, costo_quota)
    valutatori = {
        CriterioTipoSoggetto: _v_tipo_soggetto,
        CriterioTag: _v_tag,
        CriterioRegione: _v_regione,
        CriterioPaese: _v_paese,
        CriterioAteco: _v_ateco,
        CriterioSettore: _v_settore,
        CriterioDimensione: _v_dimensione,
        CriterioCertificazione: _v_certificazione,
        CriterioEsperienza: _v_esperienza,
    }
    return Valutazione(valutatori[type(criterio)](criterio, profilo, vista))


def valuta_criterio(
    criterio: CriterioPartner | None,
    profilo: ProfiloCandidato,
    *,
    costo_quota: Intervallo | None = None,
    vista: Vista,
) -> EsitoCriterio:
    """Esito del criterio sull'azienda descritta da `profilo`.

    - `costo_quota`: intervallo del costo della quota (per le regole
      finanziarie che usano `costo_quota`), None se non noto;
    - `vista`: «proprio» per l'azienda stessa (valori esatti, testo privato),
      «terzi» per chi guarda un'altra azienda (bilanci sulle fasce, nessun
      testo privato).

    `criterio=None` vale come `manuale`."""
    return valuta_criterio_dettaglio(
        criterio, profilo, costo_quota=costo_quota, vista=vista
    ).esito


# ------------------------------------------------------------- utilità


def criterio_da_json(dato: Any) -> CriterioPartner | None:
    """Criterio riletto dal jsonb. None resta None; un criterio non valido
    (dato corrotto o vocabolario cambiato) diventa `manuale`: non deve mai
    contare in automatico. Non solleva."""
    if dato is None:
        return None
    try:
        return CRITERIO_ADAPTER.validate_python(dato)
    except (ValidationError, AppError, ValueError, TypeError):
        logger.warning("partenariati: criterio non valido riletto come manuale")
        return CriterioManuale()


def criterio_json(criterio: CriterioPartner | None) -> dict | None:
    """Forma jsonb del criterio (per le RPC)."""
    return None if criterio is None else CRITERIO_ADAPTER.dump_python(criterio, mode="json")


def criteri_da_posizione(posizione: Any) -> list[CriterioPartner]:
    """Vincoli tipizzati di una posizione della call (riga, `PosizioneIn` o
    `PosizioneOut`): li deve rispettare chi la occupa. Liste vuote = nessun
    vincolo; `territorio_modalita='qualsiasi'` = nessun vincolo di regione."""

    def campo(nome: str) -> Any:
        if isinstance(posizione, Mapping):
            return posizione.get(nome)
        return getattr(posizione, nome, None)

    criteri: list[CriterioPartner] = []
    tipi = [t for t in campo("tipi_soggetto") or [] if t in voc.TIPI_SOGGETTO]
    if tipi:
        criteri.append(CriterioTipoSoggetto(valori=tipi))
    competenze = [t for t in campo("competenze") or [] if t in voc.COMPETENZE]
    if competenze:
        criteri.append(CriterioTag(tags=competenze, modalita="almeno_uno"))
    divisioni = list(campo("ateco_divisioni") or [])
    if divisioni:
        criteri.append(CriterioAteco(divisioni=divisioni))
    regioni = list(campo("regioni") or [])
    modalita = campo("territorio_modalita") or "qualsiasi"
    if regioni and modalita in ("sede_attuale", "sede_entro_erogazione"):
        criteri.append(CriterioRegione(regioni_ids=regioni, modalita=modalita))
    paesi = list(campo("paesi") or [])
    if paesi:
        criteri.append(CriterioPaese(paesi=paesi))
    dimensioni = list(campo("dimensioni") or [])
    if dimensioni:
        criteri.append(CriterioDimensione(valori=dimensioni))
    return criteri


def intervallo_costo_quota(
    budget: Intervallo | None, quota_pct: Decimal | float | str | None
) -> Intervallo | None:
    """Intervallo del costo della quota = budget × quota / 100. None se manca
    uno dei due. `budget` è un intervallo (un punto se esatto)."""
    if budget is None or quota_pct is None:
        return None
    quota = Decimal(str(quota_pct)) / Decimal(100)
    minimo, massimo = budget
    return (Decimal(str(minimo)) * quota, Decimal(str(massimo)) * quota)
