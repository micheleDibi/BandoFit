"""Autorizzazione cross-tenant e proiezioni delle call di partenariato
(WP5, docs/partenariati.md T3, C3).

Unico punto in cui si decide CHI vede una call e COSA vede:

- `ruolo_su_call`: `creatore` (titolare dell'azienda creatrice attiva),
  `titolare_o_membro` (membro con visibilità su quell'azienda, sola lettura),
  `pubblico` (qualunque altra azienda, solo per call pubblicate e visibili a
  tutti di aziende vive), `admin`. WP7+ aggiungerà `controparte`,
  `candidato`, `progettista`. La call dell'azienda creatrice si riconosce da
  `company_profile_id = azienda attiva` E `family_parent_id = owner`: un
  Advisor con l'azienda A attiva NON è creatore delle call di B;
- `carica_call_autorizzata`: fuori autorizzazione (o fuori dai ruoli ammessi
  dalla rotta) → 404, come una call che non esiste;
- proiezioni a WHITELIST, costruite campo per campo (`extra='forbid'` sui DTO
  pubblici): verso i terzi MAI `company_profile_id`, `family_parent_id`,
  `creato_da`, `budget_progetto_eur`, `dettagli_riservati`, coperture del
  creatore, fasce del creatore né identificativi dell'azienda. Le call sono
  per ora solo anonime: il creatore è «Azienda anonima» con regione della
  sede, sezione ATECO e classe dimensionale, SOLO dal Registro Imprese e solo
  se i dati del registro sono dell'azienda (T5). I testi liberi escono
  comunque passati da `anonimizza` (difesa in profondità: sono già stati
  controllati al salvataggio);
- controllo anti-contatti dei testi della call (`rilievi_testo`, usato dal
  servizio al salvataggio, alla pubblicazione e nell'anteprima) sulla forma
  canonica del testo, così un contatto non passa grazie a caratteri
  invisibili o a cifre a larghezza piena, e con il nome dell'azienda cercato
  anche scritto attaccato.

Modulo PURO salvo `carica_call_autorizzata` (due letture).
"""

from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from app.core.errors import NotFoundError
from app.schemas.partner_call import (
    BandoPubblicoCallOut,
    CallCardOut,
    CallPubblicaOut,
    CreatoreCallOut,
    PosizionePubblicaOut,
    RequisitoPubblicoOut,
    forma_canonica,
    senza_invisibili,
)
from app.schemas.partner_profile import AtecoSezioneOut
from app.services import partenariato_vocabolario as voc
from app.services.partenariato_anonimato import (
    SOSTITUTO,
    Identificativi,
    Rilievo,
    anonimizza,
    ha_bloccanti,
    trova_rilievi,
)
from app.services.partenariato_criteri import criterio_da_json
from app.services.partner_profilo_pubblico import CLASSI_DIMENSIONALI, ateco_sezione

Ruolo = Literal["creatore", "titolare_o_membro", "pubblico", "admin"]
# Ruoli che vedono la call dall'interno (vista del creatore).
RUOLI_AZIENDA: frozenset[str] = frozenset({"creatore", "titolare_o_membro"})
# Chi scrive: solo il titolare dell'azienda creatrice attiva (T4, Q14).
RUOLI_SCRITTURA: frozenset[str] = frozenset({"creatore"})

MSG_CALL_NON_TROVATA = "Call di partenariato non trovata"
DENOMINAZIONE_ANONIMA = "Azienda anonima"

# Colonne lette dal servizio. Mai verso terzi così come sono: passano SOLO
# dalle proiezioni di questo modulo.
CALL_SELECT = (
    "id,company_profile_id,family_parent_id,creato_da,bando_id,bando_slug,bando_titolo,"
    "bando_scadenza,bando_programma_id,bando_tipologia_id,bando_stato_effettivo,"
    "bando_verificato_at,bando_mancante_dal,ruolo_creatore,forma_aggregazione_prevista,"
    "anonima,titolo,descrizione_pubblica,dettagli_riservati,profilo_partner_ideale,"
    "budget_fascia,budget_progetto_eur,quota_creatore_pct,scadenza_call,visibilita,stato,"
    "motivo_chiusura,override_non_ammesso_motivo,partenariato_ref,regole_partenariato,"
    "regole_confermate_at,esclusivita,ai_check_id,wizard_passo,versione,pubblicata_at,"
    "chiusa_at,sospesa_at,sospeso_motivo,ai_posizioni_stato,ai_posizioni_proposta,"
    "ai_posizioni_avviata_at,ai_posizioni_esecuzione_id,ai_posizioni_errore,ai_testi_stato,"
    "ai_testi_proposta,ai_testi_avviata_at,ai_testi_esecuzione_id,ai_testi_errore,"
    "created_at,updated_at"
)
REQUISITO_SELECT = (
    "id,call_id,origine,rif_origine,etichetta,testo,criterio,ambito,copertura_creatore,"
    "copertura_fonte,copertura_nota,cercato,citazione,ordine"
)
POSIZIONE_SELECT = (
    "id,call_id,titolo,ruolo,tipi_soggetto,competenze,ateco_divisioni,regioni,"
    "territorio_modalita,paesi,dimensioni,quota_ipotizzata_pct,numero,requisiti_ids,note,ordine"
)

_FORME_PREVISTE = frozenset(
    {"ats", "ati_rti", "rete_contratto", "rete_soggetto", "consorzio", "accordo_partenariato",
     "consorzio_ue"}
)
_TERRITORIO = frozenset({"qualsiasi", "sede_attuale", "sede_entro_erogazione"})
_RUOLI_POSIZIONE = frozenset({"capofila", "partner"})


# ------------------------------------------------------------------ utilità


def normalizza_id(valore: Any) -> str:
    """Uuid in forma canonica; un id malformato è una call inesistente
    (404, mai il 22P02 → 502 di Postgres)."""
    try:
        return str(UUID(str(valore).strip()))
    except (ValueError, AttributeError, TypeError):
        raise NotFoundError(MSG_CALL_NON_TROVATA) from None


def _campo(riga: Any, nome: str, default: Any = None) -> Any:
    if riga is None:
        return default
    valore = riga.get(nome, default) if isinstance(riga, Mapping) else getattr(riga, nome, default)
    return default if valore is None else valore


def _lista(valore: Any) -> list:
    return list(valore) if isinstance(valore, (list, tuple)) else []


# Ragione sociale o denominazione di più parole: si cerca anche scritta tutta
# attaccata («RossiCostruzioni»), se lunga almeno così (sotto, troppe parole
# comuni).
MIN_NOME_COMPATTO = 8


def _varianti(ident: Identificativi | None) -> tuple[Identificativi | None, ...]:
    """Gli identificativi da cercare: quelli dell'azienda e, per un nome di più
    parole, lo stesso nome senza spazi."""
    if ident is None:
        return (None,)
    compatti = tuple(dict.fromkeys(
        nome.replace(" ", "")
        for nome in (ident.ragione_sociale, ident.denominazione)
        if nome and " " in nome and len(nome.replace(" ", "")) >= MIN_NOME_COMPATTO
    ))
    if not compatti:
        return (ident,)
    return (
        ident,
        Identificativi(ragione_sociale=compatti[0],
                       denominazione=compatti[1] if len(compatti) > 1 else None),
    )


def rilievi_testo(testo: Any, ident: Identificativi | None, *, anonima: bool = True
                  ) -> list[Rilievo]:
    """Rilievi anti-contatti (C7) di un testo della call, sulla sua forma
    canonica (`forma_canonica`: niente caratteri invisibili né cifre a
    larghezza piena) e con il nome dell'azienda anche scritto attaccato.
    Senza doppioni (tipo, estratto), in ordine di variante e di posizione."""
    if not isinstance(testo, str) or not testo.strip():
        return []
    canonico = forma_canonica(testo)
    rilievi: list[Rilievo] = []
    visti: set[tuple[str, str]] = set()
    for variante in _varianti(ident if anonima else None):
        for rilievo in trova_rilievi(canonico, variante, anonima=anonima):
            chiave = (rilievo.tipo, rilievo.estratto.casefold())
            if chiave not in visti:
                visti.add(chiave)
                rilievi.append(rilievo)
    return rilievi


def testo_pubblico(testo: Any, ident: Identificativi | None) -> str | None:
    """Testo libero verso terzi: senza caratteri invisibili, senza contatti e,
    con `ident`, senza gli identificativi dell'azienda (anche nella forma
    canonica: se lì c'è qualcosa da togliere si mostra quella, ripulita). Un
    testo ridotto al solo «[rimosso]» sparisce."""
    if not isinstance(testo, str) or not testo.strip():
        return None
    pulito = senza_invisibili(testo).strip()
    canonico = forma_canonica(pulito)
    if canonico != pulito and ha_bloccanti(rilievi_testo(canonico, ident)):
        pulito = canonico
    for variante in _varianti(ident):
        pulito, _ = anonimizza(pulito, variante)
    pulito = pulito.strip()
    return None if not pulito or pulito == SOSTITUTO else pulito


def _obbligatorio(testo: Any, ident: Identificativi | None) -> str:
    return testo_pubblico(testo, ident) or SOSTITUTO


# ----------------------------------------------------------------- ruoli


def ruolo_su_call(call: Mapping, active, user: Mapping) -> Ruolo | None:
    """Ruolo dell'utente sulla call (None = non autorizzato).

    Prima l'azienda creatrice (la call è dell'azienda ATTIVA e dell'owner),
    poi l'admin, poi il pubblico: solo call pubblicate, visibili a tutti."""
    company_id = getattr(active, "company_id", None)
    if (
        company_id
        and str(call.get("company_profile_id")) == str(company_id)
        and str(call.get("family_parent_id")) == str(getattr(active, "owner_id", ""))
    ):
        return "creatore" if getattr(active, "editable", False) else "titolare_o_membro"
    if (user or {}).get("role") == "admin":
        return "admin"
    if call.get("stato") == "pubblicata" and call.get("visibilita") == "pubblica":
        return "pubblico"
    return None


async def _azienda_viva(primary, company_id: Any) -> bool:
    resp = (
        await primary.table("company_profiles")
        .select("id")
        .eq("id", str(company_id))
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .limit(1)
        .execute()
    )
    return bool(resp.data)


async def carica_call_autorizzata(
    primary, call_id: Any, active, user: Mapping, *, ammessi: Iterable[str] | None = None
) -> tuple[dict, Ruolo]:
    """(riga della call, ruolo). Fuori autorizzazione — o con un ruolo che la
    rotta non ammette — 404 come una call inesistente. Il pubblico vede solo
    le call di aziende vive."""
    identificativo = normalizza_id(call_id)
    resp = (
        await primary.table("partner_calls")
        .select(CALL_SELECT)
        .eq("id", identificativo)
        .limit(1)
        .execute()
    )
    call = resp.data[0] if resp.data else None
    ruolo = ruolo_su_call(call, active, user) if isinstance(call, dict) else None
    if ruolo == "pubblico" and not await _azienda_viva(primary, call.get("company_profile_id")):
        ruolo = None
    consentiti = None if ammessi is None else frozenset(ammessi)
    if ruolo is None or (consentiti is not None and ruolo not in consentiti):
        raise NotFoundError(MSG_CALL_NON_TROVATA)
    return call, ruolo


# ------------------------------------------------------------- proiezioni


def nomi_regioni(lookups: Any) -> dict[int, str]:
    """id → nome delle regioni del catalogo (vuoto se le lookup mancano)."""
    nomi: dict[int, str] = {}
    for voce in _campo(lookups, "regioni") or []:
        id_ = _campo(voce, "id")
        nome = _campo(voce, "nome")
        if isinstance(id_, int) and isinstance(nome, str):
            nomi[id_] = nome
    return nomi


def _regione_sede(derived: Mapping, regioni: Mapping[int, str]) -> str | None:
    regione_id = derived.get("regione_id")
    if isinstance(regione_id, int) and regione_id in regioni:
        return regioni[regione_id]
    nome = derived.get("regione_nome")
    return nome.strip().title() if isinstance(nome, str) and nome.strip() else None


def _sezione(derived: Mapping, dossier: Mapping | None) -> AtecoSezioneOut | None:
    attivita = (dossier or {}).get("attivita") if isinstance(dossier, Mapping) else None
    ateco = attivita.get("ateco") if isinstance(attivita, Mapping) else None
    for codice in (
        derived.get("ateco_principale"),
        derived.get("ateco_divisione"),
        ateco.get("codice") if isinstance(ateco, Mapping) else None,
    ):
        sezione = ateco_sezione(codice)
        if sezione:
            return AtecoSezioneOut(lettera=sezione[0], descrizione=sezione[1])
    return None


def creatore_pubblico(
    dati_registro: Mapping | None, dossier: Mapping | None, regioni: Mapping[int, str]
) -> CreatoreCallOut:
    """Il creatore di una call anonima visto da terzi (C3): regione della
    sede, sezione ATECO e classe dimensionale dal Registro Imprese.
    `dati_registro` è la riga di `company_data` SOLO se è dell'azienda
    (`piva_fetched` = partita IVA attuale, T5); altrimenti None e non si
    mostra nulla. Mai denominazione, fasce, coperture o identificativi."""
    derived = _campo(dati_registro, "derived") or {}
    if not isinstance(derived, Mapping):
        derived = {}
    classe = derived.get("classe_dimensionale")
    classe = classe.strip().lower() if isinstance(classe, str) else None
    return CreatoreCallOut(
        anonima=True,
        denominazione=DENOMINAZIONE_ANONIMA,
        regione=_regione_sede(derived, regioni) if dati_registro else None,
        ateco_sezione=_sezione(derived, dossier) if dati_registro else None,
        classe_dimensionale=classe if dati_registro and classe in CLASSI_DIMENSIONALI else None,
    )


def _bando_pubblico(call: Mapping) -> BandoPubblicoCallOut:
    return BandoPubblicoCallOut(
        slug=str(call.get("bando_slug") or ""),
        titolo=str(call.get("bando_titolo") or ""),
        scadenza=call.get("bando_scadenza"),
    )


def requisito_visibile(requisito: Mapping) -> bool:
    """Requisiti che vedono i terzi: quelli CERCATI (i gap del consorzio) e
    quelli che valgono per OGNI membro (vincoli del bando che anche il
    candidato deve rispettare). Mai la copertura del creatore."""
    return requisito.get("cercato") is True or requisito.get("ambito") == "ogni_membro"


def requisiti_pubblici(
    requisiti: Iterable[Mapping], ident: Identificativi | None
) -> list[RequisitoPubblicoOut]:
    uscita: list[RequisitoPubblicoOut] = []
    for riga in requisiti:
        if not isinstance(riga, Mapping) or not requisito_visibile(riga):
            continue
        uscita.append(
            RequisitoPubblicoOut(
                etichetta=_obbligatorio(riga.get("etichetta"), ident),
                testo=_obbligatorio(riga.get("testo"), ident),
                criterio=criterio_da_json(riga.get("criterio")),
                ambito="ogni_membro" if riga.get("ambito") == "ogni_membro" else "consorzio",
            )
        )
    return uscita


def posizioni_pubbliche(
    posizioni: Iterable[Mapping],
    requisiti: Iterable[Mapping],
    ident: Identificativi | None,
    regioni: Mapping[int, str],
) -> list[PosizionePubblicaOut]:
    """Posizioni verso terzi; i requisiti collegati per etichetta, solo tra
    quelli visibili."""
    etichette = {
        str(r.get("id")): str(r.get("etichetta"))
        for r in requisiti
        if isinstance(r, Mapping) and requisito_visibile(r) and r.get("etichetta")
    }
    uscita: list[PosizionePubblicaOut] = []
    for riga in posizioni:
        if not isinstance(riga, Mapping):
            continue
        ids_regioni = [i for i in _lista(riga.get("regioni")) if isinstance(i, int)]
        modalita = riga.get("territorio_modalita")
        uscita.append(
            PosizionePubblicaOut(
                id=riga["id"],
                titolo=_obbligatorio(riga.get("titolo"), ident),
                ruolo=riga.get("ruolo") if riga.get("ruolo") in _RUOLI_POSIZIONE else "partner",
                tipi_soggetto=[t for t in _lista(riga.get("tipi_soggetto"))
                               if t in voc.TIPI_SOGGETTO],
                competenze=[c for c in _lista(riga.get("competenze")) if c in voc.COMPETENZE],
                ateco_divisioni=[d for d in _lista(riga.get("ateco_divisioni"))
                                 if isinstance(d, str)],
                regioni=ids_regioni,
                regioni_nomi=[regioni[i] for i in ids_regioni if i in regioni],
                territorio_modalita=modalita if modalita in _TERRITORIO else "qualsiasi",
                paesi=[p for p in _lista(riga.get("paesi")) if isinstance(p, str)],
                dimensioni=[d for d in _lista(riga.get("dimensioni")) if d in CLASSI_DIMENSIONALI],
                quota_ipotizzata_pct=riga.get("quota_ipotizzata_pct"),
                numero=riga.get("numero") if isinstance(riga.get("numero"), int) else 1,
                requisiti=[
                    etichette[str(i)] for i in _lista(riga.get("requisiti_ids"))
                    if str(i) in etichette
                ],
                note=testo_pubblico(riga.get("note"), ident),
            )
        )
    return uscita


def call_pubblica(
    call: Mapping,
    requisiti: Iterable[Mapping],
    posizioni: Iterable[Mapping],
    creatore: CreatoreCallOut,
    *,
    ident: Identificativi | None,
    regioni: Mapping[int, str],
) -> CallPubblicaOut:
    """La call come la vedono le altre aziende (e l'anteprima «come ti
    vedono»). Whitelist campo per campo: niente riservati, niente
    identificativi, niente snapshot delle regole né quota del creatore."""
    requisiti = [r for r in requisiti if isinstance(r, Mapping)]
    forma = call.get("forma_aggregazione_prevista")
    return CallPubblicaOut(
        id=call["id"],
        stato=call["stato"],
        bando=_bando_pubblico(call),
        creatore=creatore,
        ruolo_creatore=call["ruolo_creatore"],
        forma_aggregazione_prevista=forma if forma in _FORME_PREVISTE else None,
        titolo=testo_pubblico(call.get("titolo"), ident),
        descrizione_pubblica=testo_pubblico(call.get("descrizione_pubblica"), ident),
        profilo_partner_ideale=testo_pubblico(call.get("profilo_partner_ideale"), ident),
        budget_fascia=call.get("budget_fascia"),
        scadenza_call=call.get("scadenza_call"),
        visibilita=call.get("visibilita") or "pubblica",
        esclusivita=call.get("esclusivita") is True,
        pubblicata_at=call.get("pubblicata_at"),
        requisiti=requisiti_pubblici(requisiti, ident),
        posizioni=posizioni_pubbliche(posizioni, requisiti, ident, regioni),
    )


def call_card(
    call: Mapping,
    creatore: CreatoreCallOut,
    *,
    posizioni_n: int,
    requisiti_cercati_n: int,
    mia: bool,
    ident: Identificativi | None,
) -> CallCardOut:
    """Una call nelle liste. Passo del wizard e ultimo aggiornamento solo
    per le call dell'azienda attiva (`mia`)."""
    return CallCardOut(
        id=call["id"],
        stato=call["stato"],
        titolo=testo_pubblico(call.get("titolo"), ident),
        bando=_bando_pubblico(call),
        creatore=creatore,
        ruolo_creatore=call["ruolo_creatore"],
        budget_fascia=call.get("budget_fascia"),
        scadenza_call=call.get("scadenza_call"),
        pubblicata_at=call.get("pubblicata_at"),
        posizioni_n=posizioni_n,
        requisiti_cercati_n=requisiti_cercati_n,
        mia=mia,
        wizard_passo=call.get("wizard_passo") if mia else None,
        updated_at=call.get("updated_at") if mia else None,
    )


# ------------------------------------------------------------ versioni

# Snapshot di partner_call_versioni (fn_partner_call_contenuto): contiene la
# riga intera. Verso l'azienda creatrice escono solo questi campi: mai owner,
# autore, moderatore né riferimenti interni.
CAMPI_VERSIONE_CALL: tuple[str, ...] = (
    "id", "stato", "motivo_chiusura", "bando_id", "bando_slug", "bando_titolo",
    "bando_scadenza", "bando_programma_id", "bando_tipologia_id", "bando_stato_effettivo",
    "ruolo_creatore", "forma_aggregazione_prevista", "anonima", "titolo",
    "descrizione_pubblica", "dettagli_riservati", "profilo_partner_ideale", "budget_fascia",
    "budget_progetto_eur", "quota_creatore_pct", "scadenza_call", "visibilita",
    "override_non_ammesso_motivo", "regole_partenariato", "regole_confermate_at",
    "esclusivita", "pubblicata_at", "chiusa_at",
)
CAMPI_VERSIONE_REQUISITO: tuple[str, ...] = (
    "id", "etichetta", "testo", "criterio", "ambito", "cercato", "origine", "rif_origine",
    "citazione", "copertura_creatore", "copertura_fonte", "copertura_nota", "ordine",
)
CAMPI_VERSIONE_POSIZIONE: tuple[str, ...] = (
    "id", "titolo", "ruolo", "tipi_soggetto", "competenze", "ateco_divisioni", "regioni",
    "territorio_modalita", "paesi", "dimensioni", "quota_ipotizzata_pct", "numero",
    "requisiti_ids", "note", "ordine",
)


def _solo(riga: Any, campi: tuple[str, ...]) -> dict:
    if not isinstance(riga, Mapping):
        return {}
    return {campo: riga.get(campo) for campo in campi if campo in riga}


def proietta_versione(snapshot: Any) -> dict:
    """Snapshot di una versione per l'azienda creatrice, a whitelist."""
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    return {
        "call": _solo(snapshot.get("call"), CAMPI_VERSIONE_CALL),
        "requisiti": [
            _solo(r, CAMPI_VERSIONE_REQUISITO) for r in _lista(snapshot.get("requisiti"))
        ],
        "posizioni": [
            _solo(p, CAMPI_VERSIONE_POSIZIONE) for p in _lista(snapshot.get("posizioni"))
        ],
    }
