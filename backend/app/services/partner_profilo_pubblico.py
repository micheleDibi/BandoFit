"""Funzioni PURE del profilo partner (WP4): tipi di soggetto dal registro,
sezione ATECO, categorie di certificazione, completezza e proiezione pubblica.

Nessun I/O: il servizio (`partner_profile_service`) legge le righe e le passa
qui. La proiezione verso terzi (`profilo_pubblico`) è una WHITELIST: costruisce
`PartnerPubblicoOut` campo per campo e non copia mai una riga intera, così
P.IVA, CF, contatti, importi di bilancio, `company_profile_id`,
`family_parent_id` e referente non possono uscire nemmeno per sbaglio.

Da dove viene ciò che vedono i terzi (T5): nome, classe dimensionale, ATECO e
regione della sede SOLO dal registro (`company_data.denominazione`,
`company_data.derived`, dossier), le fasce SOLO da `calcola_fasce` sui bilanci,
mai dai campi di `company_profiles` che l'utente modifica.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from app.schemas.partner_profile import (
    PAESI_ISO2,
    TIPI_SOGGETTO_DA_REGISTRO,
    AtecoSezioneOut,
    CompetenzaPubblicaOut,
    EsperienzaPubblicaOut,
    FascePubblicheOut,
    FormaPubblicaOut,
    PartnerPubblicoOut,
    TipoSoggettoPubblicoOut,
)
from app.services import partenariato_vocabolario as voc
from app.services.openapi_mapping import ateco_division
from app.services.partenariato_anonimato import (
    SOSTITUTO,
    Identificativi,
    anonimizza,
    normalizza,
)

# ------------------------------------------------------------------ utilità


def _campo(sorgente: Any, nome: str, default: Any = None) -> Any:
    """Campo di una riga (dict) o di un modello/dataclass."""
    if sorgente is None:
        return default
    if isinstance(sorgente, Mapping):
        valore = sorgente.get(nome, default)
    else:
        valore = getattr(sorgente, nome, default)
    return default if valore is None else valore


def _lista(valore: Any) -> list:
    return list(valore) if isinstance(valore, (list, tuple)) else []


def _testo(valore: Any) -> str | None:
    if not isinstance(valore, str):
        return None
    pulito = valore.strip()
    return pulito or None


# ----------------------------------------------------- tipi di soggetto

CLASSI_DIMENSIONALI: dict[str, str] = {
    "micro": "micro_impresa",
    "piccola": "piccola_impresa",
    "media": "media_impresa",
    "grande": "grande_impresa",
}
_FLAG_TIPI = ("startup_innovativa", "pmi_innovativa", "impresa_artigiana")


def tipi_soggetto_dedotti(
    derived: Mapping | None, flags: Mapping | None, forma_giuridica: str | None
) -> list[str]:
    """Tipi di soggetto ricavati SOLO dal registro, nell'ordine del vocabolario.

    - `derived.classe_dimensionale` (micro/piccola/media/grande) → il tipo
      dimensionale, più `pmi` se non è grande;
    - flag del dossier `startup_innovativa`, `pmi_innovativa`,
      `impresa_artigiana` (solo se `True`: un valore ignoto non conta);
    - forma giuridica del registro che contiene «cooperativ» → `cooperativa`;
    - `impresa` se almeno uno dei precedenti c'è."""
    dedotti: set[str] = set()
    classe = _campo(derived, "classe_dimensionale")
    if isinstance(classe, str) and classe.strip().lower() in CLASSI_DIMENSIONALI:
        classe = classe.strip().lower()
        dedotti.add(CLASSI_DIMENSIONALI[classe])
        if classe != "grande":
            dedotti.add("pmi")
    for flag in _FLAG_TIPI:
        if _campo(flags, flag) is True:
            dedotti.add(flag)
    if isinstance(forma_giuridica, str) and "cooperativ" in forma_giuridica.lower():
        dedotti.add("cooperativa")
    if dedotti:
        dedotti.add("impresa")
    return [codice for codice in voc.TIPI_SOGGETTO if codice in dedotti]


# ------------------------------------------------------------------- ATECO

# ATECO 2007 (aggiornamento 2022): la classificazione di `atecoClassification.
# ateco` di IT-full, quella salvata in `company_data.derived.ateco_principale`.
_SEZIONI_ATECO_2007: tuple[tuple[str, tuple[int, ...], str], ...] = (
    ("A", (1, 2, 3), "Agricoltura, silvicoltura e pesca"),
    ("B", (5, 6, 7, 8, 9), "Estrazione di minerali da cave e miniere"),
    ("C", tuple(range(10, 34)), "Attività manifatturiere"),
    ("D", (35,), "Fornitura di energia elettrica, gas, vapore e aria condizionata"),
    (
        "E",
        (36, 37, 38, 39),
        "Fornitura di acqua; reti fognarie, attività di gestione dei rifiuti e risanamento",
    ),
    ("F", (41, 42, 43), "Costruzioni"),
    (
        "G",
        (45, 46, 47),
        "Commercio all'ingrosso e al dettaglio; riparazione di autoveicoli e motocicli",
    ),
    ("H", (49, 50, 51, 52, 53), "Trasporto e magazzinaggio"),
    ("I", (55, 56), "Attività dei servizi di alloggio e di ristorazione"),
    ("J", (58, 59, 60, 61, 62, 63), "Servizi di informazione e comunicazione"),
    ("K", (64, 65, 66), "Attività finanziarie e assicurative"),
    ("L", (68,), "Attività immobiliari"),
    ("M", (69, 70, 71, 72, 73, 74, 75), "Attività professionali, scientifiche e tecniche"),
    (
        "N",
        (77, 78, 79, 80, 81, 82),
        "Noleggio, agenzie di viaggio, servizi di supporto alle imprese",
    ),
    ("O", (84,), "Amministrazione pubblica e difesa; assicurazione sociale obbligatoria"),
    ("P", (85,), "Istruzione"),
    ("Q", (86, 87, 88), "Sanità e assistenza sociale"),
    (
        "R",
        (90, 91, 92, 93),
        "Attività artistiche, sportive, di intrattenimento e divertimento",
    ),
    ("S", (94, 95, 96), "Altre attività di servizi"),
    (
        "T",
        (97, 98),
        "Attività di famiglie e convivenze come datori di lavoro per personale domestico; "
        "produzione di beni e servizi indifferenziati per uso proprio da parte di famiglie "
        "e convivenze",
    ),
    ("U", (99,), "Organizzazioni ed organismi extraterritoriali"),
)

# ATECO 2025 (NACE Rev. 2.1): dalla divisione 61 in poi le lettere cambiano e la
# divisione 45 non esiste più. IT-full la espone in `firstLevel.ateco2025`, ma
# con descrizioni in inglese. Titoli italiani da riconfermare sul sito Istat.
_SEZIONI_ATECO_2025: tuple[tuple[str, tuple[int, ...], str], ...] = (
    ("A", (1, 2, 3), "Agricoltura, silvicoltura e pesca"),
    ("B", (5, 6, 7, 8, 9), "Attività estrattiva"),
    ("C", tuple(range(10, 34)), "Attività manifatturiere"),
    ("D", (35,), "Fornitura di energia elettrica, gas, vapore e aria condizionata"),
    (
        "E",
        (36, 37, 38, 39),
        "Fornitura di acqua; reti fognarie, attività di gestione dei rifiuti e risanamento",
    ),
    ("F", (41, 42, 43), "Costruzioni"),
    ("G", (46, 47), "Commercio all'ingrosso e al dettaglio"),
    ("H", (49, 50, 51, 52, 53), "Trasporto e magazzinaggio"),
    ("I", (55, 56), "Attività dei servizi di alloggio e di ristorazione"),
    (
        "J",
        (58, 59, 60),
        "Attività editoriali, di trasmissione e di produzione e distribuzione di contenuti",
    ),
    (
        "K",
        (61, 62, 63),
        "Telecomunicazioni, programmazione e consulenza informatica, infrastrutture "
        "informatiche e altri servizi d'informazione",
    ),
    ("L", (64, 65, 66), "Attività finanziarie e assicurative"),
    ("M", (68,), "Attività immobiliari"),
    ("N", (69, 70, 71, 72, 73, 74, 75), "Attività professionali, scientifiche e tecniche"),
    ("O", (77, 78, 79, 80, 81, 82), "Attività amministrative e di servizi di supporto"),
    ("P", (84,), "Amministrazione pubblica e difesa; assicurazione sociale obbligatoria"),
    ("Q", (85,), "Istruzione"),
    ("R", (86, 87, 88), "Attività dei servizi sanitari e di assistenza sociale"),
    ("S", (90, 91, 92, 93), "Attività artistiche, sportive e di intrattenimento"),
    ("T", (94, 95, 96), "Altre attività di servizi"),
    (
        "U",
        (97, 98),
        "Attività di famiglie e convivenze come datori di lavoro per personale domestico; "
        "produzione di beni e servizi indifferenziati per uso proprio da parte di famiglie "
        "e convivenze",
    ),
    ("V", (99,), "Attività di organizzazioni e organismi extraterritoriali"),
)


def _indice(sezioni) -> dict[int, tuple[str, str]]:
    return {div: (lettera, desc) for lettera, divisioni, desc in sezioni for div in divisioni}


_PER_DIVISIONE = {"2007": _indice(_SEZIONI_ATECO_2007), "2025": _indice(_SEZIONI_ATECO_2025)}


def ateco_sezione(
    codice: Any, *, versione: Literal["2007", "2025"] = "2007"
) -> tuple[str, str] | None:
    """(lettera, descrizione ufficiale) della sezione ATECO a partire dalla
    divisione del codice («62.01.00», «620100», «85592»). None per codici
    vuoti o divisioni che non esistono (es. 04, 34, 40)."""
    divisione = ateco_division(codice)
    if divisione is None or not divisione.isdigit():
        return None
    return _PER_DIVISIONE[versione].get(int(divisione))


# --------------------------------------------------------- certificazioni

CATEGORIE_CERTIFICAZIONE: dict[str, str] = {
    "qualita": "Gestione della qualità",
    "ambiente": "Gestione ambientale",
    "sicurezza_lavoro": "Salute e sicurezza sul lavoro",
    "sicurezza_informazioni": "Sicurezza delle informazioni",
    "energia": "Gestione dell'energia",
    "appalti_soa": "Attestazione SOA per gli appalti pubblici",
    "settoriale": "Certificazione di settore",
    "altro": "Altra certificazione",
}


def _parole(*alternative: str) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(alternative) + r")\b")


def _numeri(*norme: str) -> re.Pattern:
    # Confini di cifra: «ISO9001» incollato vale, «19001» no.
    return re.compile(r"(?<![0-9])(?:" + "|".join(norme) + r")(?![0-9])")


# Due livelli: prima i codici specifici (norme, sigle), poi le parole generiche.
# Nel livello vince la menzione che compare PRIMA nel testo: «Qualità ISO
# 13485» è una certificazione di settore, non un generico sistema qualità.
_CERT_SPECIFICHE: tuple[tuple[str, re.Pattern], ...] = (
    ("appalti_soa", _parole("soa", "og ?[0-9]{1,2}", "os ?[0-9]{1,2}")),
    (
        "sicurezza_informazioni",
        _numeri("27001", "27017", "27018", "27701", "22301"),
    ),
    ("sicurezza_informazioni", _parole("tisax", "soc ?2")),
    ("sicurezza_lavoro", _numeri("45001", "18001")),
    ("sicurezza_lavoro", _parole("ohsas")),
    ("energia", _numeri("50001", "11352", "11339")),
    ("energia", _parole("esco", "ege")),
    ("ambiente", _numeri("14001", "14064", "14067")),
    ("ambiente", _parole("emas", "fsc", "pefc", "ecolabel", "epd")),
    ("qualita", _numeri("9001")),
    (
        "settoriale",
        _numeri(
            "13485", "16949", "9100", "22000", "17025", "3834", "1090", "15838", "22716", "20000"
        ),
    ),
    ("settoriale", _parole("iatf", "fssc", "brc", "ifs", "haccp", "gmp", "globalgap", "f ?gas")),
)
_CERT_GENERICHE: tuple[tuple[str, re.Pattern], ...] = (
    ("appalti_soa", _parole("appalti pubblici", "appalti")),
    ("sicurezza_informazioni", _parole("sicurezza (?:delle )?informazioni", "cyber\\w*")),
    ("sicurezza_lavoro", _parole("sicurezza sul lavoro", "salute e sicurezza")),
    ("energia", _parole("energi\\w*")),
    ("ambiente", _parole("ambient\\w*", "carbon footprint")),
    ("qualita", _parole("qualita", "quality")),
    ("settoriale", _parole("marcatura ce", "biologic\\w*", "dop", "igp", "accredia")),
)


def _prima_menzione(testo: str, regole) -> str | None:
    migliore: tuple[int, str] | None = None
    for categoria, regex in regole:
        m = regex.search(testo)
        if m and (migliore is None or m.start() < migliore[0]):
            migliore = (m.start(), categoria)
    return migliore[1] if migliore else None


def categoria_certificazione(testo: Any) -> str:
    """Categoria di una certificazione scritta dall'utente (per i profili
    anonimi, che mostrano solo le categorie). `altro` se non si riconosce."""
    if not isinstance(testo, str):
        return "altro"
    norm = normalizza(testo)
    return (
        _prima_menzione(norm, _CERT_SPECIFICHE)
        or _prima_menzione(norm, _CERT_GENERICHE)
        or "altro"
    )


# ------------------------------------------------------------- completezza

_SOGLIA_DESCRIZIONE = 80


def completezza(profilo: Any, dedotti: Iterable[str] | None) -> int:
    """Punteggio 0-100 del profilo (per la barra e l'ordinamento):
    descrizione ≥ 80 caratteri 20; competenze ≥ 3 → 25, 1-2 → 12; tipi di
    soggetto dedotti o dichiarati 10; regioni o paesi d'interesse 10;
    almeno un'esperienza 15; certificazioni o infrastrutture 10; ruoli e
    forme accettate 10. `profilo` è una riga (dict) o un `PartnerProfileIn`."""
    punti = 0
    descrizione = _testo(_campo(profilo, "descrizione_competenze")) or ""
    if len(descrizione) >= _SOGLIA_DESCRIZIONE:
        punti += 20
    competenze = len(_lista(_campo(profilo, "competenze")))
    if competenze >= 3:
        punti += 25
    elif competenze >= 1:
        punti += 12
    if list(dedotti or ()) or _lista(_campo(profilo, "tipi_soggetto")):
        punti += 10
    if _lista(_campo(profilo, "regioni_interesse")) or _lista(_campo(profilo, "paesi_interesse")):
        punti += 10
    if _lista(_campo(profilo, "esperienze")):
        punti += 15
    if _lista(_campo(profilo, "certificazioni")) or _testo(_campo(profilo, "infrastrutture")):
        punti += 10
    if _lista(_campo(profilo, "ruoli_disponibili")) and _lista(_campo(profilo, "forme_accettate")):
        punti += 10
    return punti


# -------------------------------------------------------- profilo pubblico


def _nomi_lookup(lookups: Any, attributo: str) -> dict[int, str]:
    voci = _campo(lookups, attributo) or []
    nomi: dict[int, str] = {}
    for voce in voci:
        id_ = _campo(voce, "id")
        nome = _campo(voce, "nome")
        if isinstance(id_, int) and isinstance(nome, str):
            nomi[id_] = nome
    return nomi


def _pulisci(testo: Any, ident: Identificativi | None) -> str | None:
    """Difesa in profondità: i testi liberi sono già stati controllati al
    salvataggio, ma verso terzi escono comunque senza contatti (e, se anonimo,
    senza identificativi). Un testo ridotto al solo «[rimosso]» sparisce."""
    pulito = _testo(testo)
    if pulito is None:
        return None
    pulito, _ = anonimizza(pulito, ident)
    pulito = pulito.strip()
    return None if not pulito or pulito == SOSTITUTO else pulito


def _regione_sede(derived: Any, regioni: dict[int, str]) -> str | None:
    regione_id = _campo(derived, "regione_id")
    if isinstance(regione_id, int) and regione_id in regioni:
        return regioni[regione_id]
    nome = _testo(_campo(derived, "regione_nome"))
    return nome.title() if nome else None


def _sezione(derived: Any, dossier: Any) -> AtecoSezioneOut | None:
    for codice in (
        _campo(derived, "ateco_principale"),
        _campo(derived, "ateco_divisione"),
        _campo(_campo(_campo(dossier, "attivita"), "ateco"), "codice"),
    ):
        sezione = ateco_sezione(codice)
        if sezione:
            return AtecoSezioneOut(lettera=sezione[0], descrizione=sezione[1])
    return None


def _forma_giuridica_registro(dossier: Any) -> str | None:
    anagrafica = _campo(dossier, "anagrafica")
    parti = [
        _testo(_campo(anagrafica, "forma_giuridica")),
        _testo(_campo(anagrafica, "forma_giuridica_dettaglio")),
    ]
    return " ".join(p for p in parti if p) or None


def _tipi_pubblici(profilo: Any, dedotti: list[str]) -> list[TipoSoggettoPubblicoOut]:
    dichiarati = {
        codice
        for codice in _lista(_campo(profilo, "tipi_soggetto"))
        if codice in voc.TIPI_SOGGETTO
        and codice not in TIPI_SOGGETTO_DA_REGISTRO
        and codice not in dedotti
    }
    tipi = [
        TipoSoggettoPubblicoOut(
            codice=codice, etichetta=voc.TIPI_SOGGETTO[codice].etichetta, fonte="registro"
        )
        for codice in dedotti
    ]
    tipi.extend(
        TipoSoggettoPubblicoOut(codice=codice, etichetta=voce.etichetta, fonte="dichiarato")
        for codice, voce in voc.TIPI_SOGGETTO.items()
        if codice in dichiarati
    )
    return tipi


def _esperienze_pubbliche(
    profilo: Any, programmi: dict[int, str], anonimo: bool, ident: Identificativi | None
) -> list[EsperienzaPubblicaOut]:
    esperienze: list[EsperienzaPubblicaOut] = []
    for voce in _lista(_campo(profilo, "esperienze")):
        programma_id = _campo(voce, "programma_id")
        programma = (
            programmi.get(programma_id) if isinstance(programma_id, int) else None
        ) or _pulisci(_campo(voce, "programma"), ident)
        if not programma:
            continue
        if anonimo:
            esperienza = EsperienzaPubblicaOut(programma=programma)
            if esperienza not in esperienze:  # solo il programma: niente doppioni
                esperienze.append(esperienza)
            continue
        anno = _campo(voce, "anno")
        ruolo = _campo(voce, "ruolo")
        esperienze.append(
            EsperienzaPubblicaOut(
                programma=programma,
                anno=anno if isinstance(anno, int) and not isinstance(anno, bool) else None,
                ruolo=ruolo if ruolo in voc.RUOLI else None,
                titolo=_pulisci(_campo(voce, "titolo"), ident),
            )
        )
    return esperienze


def _certificazioni_pubbliche(
    profilo: Any, anonimo: bool, ident: Identificativi | None
) -> list[str]:
    voci = [v for v in _lista(_campo(profilo, "certificazioni")) if isinstance(v, str)]
    if anonimo:
        categorie = {categoria_certificazione(v) for v in voci if v.strip()}
        return [etichetta for c, etichetta in CATEGORIE_CERTIFICAZIONE.items() if c in categorie]
    pulite = (_pulisci(v, ident) for v in voci)
    return list(dict.fromkeys(v for v in pulite if v))


def _fasce_pubbliche(fasce: Any, anonimo: bool) -> FascePubblicheOut:
    def codice(nome: str) -> str | None:
        valore = _campo(fasce, nome)
        return valore if isinstance(valore, str) else None

    if anonimo:  # Q12: solo la fascia di fatturato
        return FascePubblicheOut(fatturato=codice("fatturato"))
    return FascePubblicheOut(
        fatturato=codice("fatturato"),
        patrimonio_netto=codice("patrimonio_netto"),
        dipendenti=codice("dipendenti"),
        trend=codice("trend_fatturato") or codice("trend"),
    )


def profilo_pubblico(
    profilo: Mapping,
    company_data_row: Mapping | None,
    dossier: Mapping | None,
    fasce: Any,
    lookups: Any,
    *,
    ident: Identificativi | None = None,
) -> PartnerPubblicoOut:
    """Proiezione a whitelist del profilo verso le altre aziende (e anteprima
    «come ti vedono», anche se il profilo non è visibile).

    - `profilo`: riga di `company_partner_profiles`;
    - `company_data_row`: riga di `company_data` (denominazione, derived);
    - `dossier`: dossier del registro (flag, forma giuridica, ATECO di ripiego);
    - `fasce`: risultato di `bilanci_indicatori.calcola_fasce` (o None);
    - `lookups`: `LookupsOut` del catalogo (nomi di regioni e programmi);
    - `ident`: identificativi dell'azienda; per gli anonimi vengono tolti dai
      testi liberi (i contatti si tolgono sempre).

    Anonimi (Q12): niente denominazione né infrastrutture, fasce solo di
    fatturato, esperienze con il solo programma, certificazioni per categoria."""
    anonimo = _campo(profilo, "anonimo", True) is not False
    ident_testi = ident if anonimo else None
    derived = _campo(company_data_row, "derived") or {}
    regioni = _nomi_lookup(lookups, "regioni")
    programmi = _nomi_lookup(lookups, "programmi")

    dedotti = tipi_soggetto_dedotti(
        derived, _campo(dossier, "flags"), _forma_giuridica_registro(dossier)
    )
    classe = _campo(derived, "classe_dimensionale")
    competenze = [
        CompetenzaPubblicaOut(
            codice=codice,
            etichetta=voc.COMPETENZE[codice].etichetta,
            area=voc.AREE_COMPETENZE[voc.COMPETENZE[codice].area],
        )
        for codice in dict.fromkeys(_lista(_campo(profilo, "competenze")))
        if codice in voc.COMPETENZE
    ]
    libere = (_pulisci(v, ident_testi) for v in _lista(_campo(profilo, "competenze_libere")))

    return PartnerPubblicoOut(
        codice_pubblico=_campo(profilo, "codice_pubblico"),
        anonimo=anonimo,
        denominazione=None if anonimo else _testo(_campo(company_data_row, "denominazione")),
        regione_sede=_regione_sede(derived, regioni),
        regioni_interesse=[
            regioni[i] for i in _lista(_campo(profilo, "regioni_interesse")) if i in regioni
        ],
        paesi_interesse=[p for p in _lista(_campo(profilo, "paesi_interesse")) if p in PAESI_ISO2],
        ateco_sezione=_sezione(derived, dossier),
        classe_dimensionale=classe if classe in CLASSI_DIMENSIONALI else None,
        fasce=_fasce_pubbliche(fasce, anonimo),
        tipi_soggetto=_tipi_pubblici(profilo, dedotti),
        competenze=competenze,
        competenze_libere=list(dict.fromkeys(v for v in libere if v)),
        descrizione_competenze=_pulisci(_campo(profilo, "descrizione_competenze"), ident_testi),
        esperienze=_esperienze_pubbliche(profilo, programmi, anonimo, ident_testi),
        certificazioni=_certificazioni_pubbliche(profilo, anonimo, ident_testi),
        infrastrutture=None
        if anonimo
        else _pulisci(_campo(profilo, "infrastrutture"), ident_testi),
        ruoli_disponibili=[
            r for r in voc.RUOLI if r in _lista(_campo(profilo, "ruoli_disponibili"))
        ],
        forme_accettate=[
            FormaPubblicaOut(codice=codice, etichetta=voc.FORME[codice].etichetta)
            for codice in dict.fromkeys(_lista(_campo(profilo, "forme_accettate")))
            if codice in voc.FORME
        ],
        completezza=completezza(profilo, dedotti),
        accetta_inviti=_campo(profilo, "accetta_inviti", True) is not False,
    )
