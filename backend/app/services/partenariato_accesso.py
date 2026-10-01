"""Autorizzazione cross-tenant e proiezioni delle call di partenariato
(WP5, docs/partenariati.md T3, C3).

Unico punto in cui si decide CHI vede una call e COSA vede:

- `ruolo_su_call`: `creatore` (titolare dell'azienda creatrice attiva),
  `titolare_o_membro` (membro con visibilità su quell'azienda, sola lettura),
  `controparte` (WP7: l'azienda attiva ha una candidatura ACCETTATA sulla
  call, non sospesa), `candidato` / `invitato` (WP7: ha una candidatura o
  un invito IN ATTESA; call non sospesa di un'azienda viva), `pubblico` (qualunque
  altra azienda, solo per call pubblicate e visibili a tutti di aziende
  vive), `admin`. Il progettista (WP9) non è un ruolo di questa funzione:
  legge solo dalla rotta dedicata del consulto (`vista_progettista`, audit
  fail-closed in `consulting_service`). La call dell'azienda creatrice si riconosce da
  `company_profile_id = azienda attiva` E `family_parent_id = owner`: un
  Advisor con l'azienda A attiva NON è creatore delle call di B;
- `carica_call_autorizzata`: fuori autorizzazione (o fuori dai ruoli ammessi
  dalla rotta) → 404, come una call che non esiste;
- proiezioni a WHITELIST, costruite campo per campo (`extra='forbid'` sui DTO
  pubblici): verso i terzi MAI `company_profile_id`, `family_parent_id`,
  `creato_da`, `budget_progetto_eur`, `dettagli_riservati`, coperture del
  creatore, fasce del creatore né identificativi dell'azienda. Il creatore è
  «Azienda anonima» con regione della sede, sezione ATECO e classe
  dimensionale, SOLO dal Registro Imprese e solo se i dati del registro sono
  dell'azienda (T5). Call NOMINATIVA (WP9, decisione di Michele): in più la
  sola denominazione del Registro Imprese, e solo se OGGI l'azienda ha
  l'identità verificata dalla piattaforma (`denominazione_nominativa`: una
  verifica revocata la rende di nuovo anonima); mai P.IVA, sito, PEC né
  persone. I testi liberi escono comunque passati da `anonimizza` (difesa in
  profondità: sono già stati controllati al salvataggio, e anche una call
  nominativa non li usa per identificarsi);
- controllo anti-contatti dei testi della call (`rilievi_testo`, usato dal
  servizio al salvataggio, alla pubblicazione e nell'anteprima) sulla forma
  canonica del testo, così un contatto non passa grazie a caratteri
  invisibili o a cifre a larghezza piena, e con il nome dell'azienda cercato
  anche scritto attaccato.

WP6 (bacheca, «Per te», suggeriti): il ruolo `pubblico` apre la vista
pubblica delle call di altri owner (pubblicate, visibili a tutti, non
sospese); le card della bacheca portano il match dell'azienda attiva (vista
«proprio»); verso il creatore i candidati suggeriti escono con uno
PSEUDONIMO per call (`pseudonimo`), mai con `company_profile_id` né con il
`codice_pubblico` stabile, e con i soli dati del registro ammessi (Q12).

WP7 (candidature, inviti e chat): la controparte accettata vede la
`CallVistaControparteOut` (la vista pubblica più i dettagli riservati e il
budget esatto; mai i bilanci esatti di nessuno); chi si è candidato o è
stato invitato vede la vista pubblica con lo stato della propria candidatura
(`CandidaturaPropriaOut`). L'IDENTITÀ si rivela solo con la rivelazione
SIMMETRICA (WP9, decisione di Michele: interruttore
`partner_profile_service.RIVELAZIONE_IDENTITA_DISPONIBILE` acceso, audit di
rivelazione scritto dalla RPC all'accettazione perché ENTRAMBE le aziende
erano verificate dalla piattaforma, ed entrambe ancora verificate OGGI:
`partenariato_candidature_service.identita_se_rivelata`, unico punto che
produce `IdentitaRivelataOut`); altrimenti anche i dettagli riservati escono
senza gli identificativi dell'azienda.

WP8 (consorzio): i membri della call (`partner_call_membri`) escono SOLO da
`proietta_membro`: la propria azienda col suo nome, gli altri membri in
piattaforma con lo pseudonimo della call e il profilo pubblico anonimo (Q12,
senza `codice_pubblico`), chi ha creato la call come «Azienda anonima» verso
gli altri membri (per una call nominativa di un'azienda verificata oggi, con
la sola denominazione del registro, come nella vista pubblica), gli esterni
con il nome dichiarato dal creatore (ripulito
dagli identificativi del creatore verso gli altri). Mai `company_profile_id`,
candidatura, utenti. «Azienda viva» (pubblico, candidato, invitato) = non
eliminata né archiviata e con il titolare attivo, come nelle RPC (0040).
Un'azienda USCITA dal consorzio (da sé o tolta dal creatore) non è più
controparte: la sua candidatura resta accettata (non si ritira), ma la riga
uscita prevale, come per l'esclusività sul bando.

WP9 (consulto dalla call): il progettista ASSEGNATO vede la
`CallVistaProgettistaOut` (`vista_progettista`): la vista del creatore a
whitelist (regole, requisiti con la copertura del creatore, posizioni,
riservati e budget del creatore) e il consorzio nella proiezione del
creatore, con i partner in piattaforma SOLO come «Azienda anonima» con i loro
esiti (niente pseudonimo né profilo); mai contatti, messaggi, candidature,
valori esatti o id interni di altre aziende, mai i limiti del piano né i job
AI del cliente.

Modulo PURO salvo `carica_call_autorizzata`, `candidatura_su_call` e
`uscita_dal_consorzio`.
"""

import base64
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, create_model

from app.core.errors import NotFoundError
from app.core.privacy import hmac_dominio
from app.schemas.partenariato_consorzio import (
    ConsorzioOut,
    MembroOut,
    PosizioneMembroOut,
    ProfiloMembroOut,
)
from app.schemas.partner_call import (
    BandoCallOut,
    BandoPubblicoCallOut,
    BudgetFascia,
    CallCardOut,
    CallPubblicaOut,
    CallVistaCreatoreOut,
    CreatoreCallOut,
    FormaPrevista,
    MotivoChiusura,
    PartenariatoGapOut,
    PosizioneOut,
    PosizionePubblicaOut,
    RegoleCallSnapshot,
    RequisitoOut,
    RequisitoPubblicoOut,
    RiepilogoGapOut,
    RuoloCreatore,
    StatoCall,
    Visibilita,
)
from app.schemas.partner_profile import AtecoSezioneOut, PartnerPubblicoOut
from app.services import partenariato_vocabolario as voc
from app.services.partenariato_anonimato import (  # noqa: F401 — riesportati
    MIN_NOME_COMPATTO,
    SOSTITUTO,
    Identificativi,
    Rilievo,
    anonimizza,
    forma_canonica,
    senza_invisibili,
    trova_rilievi,
)
from app.services.partenariato_criteri import criterio_da_json
from app.services.partenariato_matching import MatchInterno, MatchOut, proietta_match
from app.services.openapi_mapping import build_dossier
from app.services.partner_profilo_pubblico import CLASSI_DIMENSIONALI, ateco_sezione

Ruolo = Literal[
    "creatore", "titolare_o_membro", "controparte", "candidato", "invitato", "pubblico", "admin"
]
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


def rilievi_testo(testo: Any, ident: Identificativi | None, *, anonima: bool = True
                  ) -> list[Rilievo]:
    """Rilievi anti-contatti (C7) di un testo della call: `trova_rilievi`,
    che lavora sulla forma canonica (`forma_canonica`: niente caratteri
    invisibili né cifre a larghezza piena) e cerca il nome dell'azienda anche
    scritto attaccato. Senza doppioni (tipo, estratto), in ordine di
    posizione."""
    if not isinstance(testo, str) or not testo.strip():
        return []
    return trova_rilievi(testo, ident, anonima=anonima)


def testo_pubblico(testo: Any, ident: Identificativi | None) -> str | None:
    """Testo libero verso terzi: senza caratteri invisibili, senza contatti e,
    con `ident`, senza gli identificativi dell'azienda (anche nella forma
    canonica: se lì c'è qualcosa da togliere si mostra quella, ripulita, vedi
    `anonimizza`). Un testo ridotto al solo «[rimosso]» sparisce."""
    if not isinstance(testo, str) or not testo.strip():
        return None
    pulito, _ = anonimizza(senza_invisibili(testo).strip(), ident)
    pulito = pulito.strip()
    return None if not pulito or pulito == SOSTITUTO else pulito


def _obbligatorio(testo: Any, ident: Identificativi | None) -> str:
    return testo_pubblico(testo, ident) or SOSTITUTO


# ----------------------------------------------------------------- ruoli


def ruolo_su_call(
    call: Mapping, active, user: Mapping, *, candidatura: Mapping | None = None
) -> Ruolo | None:
    """Ruolo dell'utente sulla call (None = non autorizzato).

    Prima l'azienda creatrice (la call è dell'azienda ATTIVA e dell'owner),
    poi la riga di `partner_candidature` dell'azienda attiva sulla call
    (`candidatura`, WP7), solo su una call non sospesa: accettata →
    `controparte`; ancora in attesa (e, se invito, non scaduta) → `invitato`
    o `candidato`. Una riga chiusa (rifiutata, ritirata, scaduta) non dà
    nessun ruolo: vale il resto, cioè l'admin, poi il pubblico (solo call
    pubblicate, visibili a tutti e non sospese). Così un invito ritirato o
    scaduto non lascia leggere una call solo su invito, e una call sospesa
    per moderazione non si legge nemmeno dalla controparte."""
    company_id = getattr(active, "company_id", None)
    if (
        company_id
        and str(call.get("company_profile_id")) == str(company_id)
        and str(call.get("family_parent_id")) == str(getattr(active, "owner_id", ""))
    ):
        return "creatore" if getattr(active, "editable", False) else "titolare_o_membro"
    if (
        candidatura is not None
        and company_id
        and str(candidatura.get("company_profile_id")) == str(company_id)
        and str(candidatura.get("partner_call_id")) == str(call.get("id"))
        and call.get("stato") not in ("bozza", "sospesa_moderazione")
        and call.get("sospesa_at") is None
    ):
        if candidatura.get("stato") == "accettata":
            return "controparte"
        if candidatura.get("stato") == "inviata" and not invito_scaduto(candidatura):
            return "invitato" if candidatura.get("tipo") == "invito" else "candidato"
    if (user or {}).get("role") == "admin":
        return "admin"
    if pubblicamente_visibile(call):
        return "pubblico"
    return None


def pubblicamente_visibile(call: Mapping) -> bool:
    """Una call che le altre aziende possono vedere: pubblicata, visibile a
    tutti (le `solo_invitati` arrivano con gli inviti del WP7), non
    sospesa."""
    return (
        call.get("stato") == "pubblicata"
        and call.get("visibilita") == "pubblica"
        and call.get("sospesa_at") is None
    )


async def _azienda_viva(primary, company_id: Any) -> bool:
    """Azienda viva: non eliminata né archiviata E con il titolare attivo —
    la stessa definizione di `fn_partner_call_aperta` (0040), della
    controparte nelle RPC del WP7 e di `partenariato_indice.aziende_vive`."""
    resp = (
        await primary.table("company_profiles")
        .select("id,parent_id")
        .eq("id", str(company_id))
        .is_("deleted_at", "null")
        .is_("archived_at", "null")
        .limit(1)
        .execute()
    )
    owner = resp.data[0].get("parent_id") if resp.data else None
    if not owner:
        return False
    titolare = (
        await primary.table("profiles")
        .select("id,is_active")
        .eq("id", str(owner))
        .limit(1)
        .execute()
    )
    return bool(titolare.data and titolare.data[0].get("is_active") is True)


CANDIDATURA_PROPRIA_SELECT = (
    "id,partner_call_id,company_profile_id,tipo,stato,posizione_id,scade_at,motivo_chiusura,"
    "motivo_rifiuto,conversazione_id,decisa_at,chiusa_at,created_at"
)
# Quale riga conta se l'azienda ne ha più di una sulla stessa call.
_PRIORITA_STATO = {"accettata": 0, "inviata": 1}


async def candidatura_su_call(primary, call_id: Any, company_id: Any) -> dict | None:
    """La riga di `partner_candidature` dell'azienda sulla call che ne decide
    il ruolo: l'accettata, altrimenti quella in attesa, altrimenti la più
    recente (rifiutata, ritirata o scaduta). None se non ce ne sono."""
    if not company_id:
        return None
    resp = (
        await primary.table("partner_candidature")
        .select(CANDIDATURA_PROPRIA_SELECT)
        .eq("partner_call_id", str(call_id))
        .eq("company_profile_id", str(company_id))
        .execute()
    )
    righe = [r for r in resp.data or [] if isinstance(r, dict)]
    if not righe:
        return None
    righe.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return min(righe, key=lambda r: _PRIORITA_STATO.get(r.get("stato"), 2))


async def uscita_dal_consorzio(primary, call_id: Any, company_id: Any) -> bool:
    """L'azienda ha una riga USCITA nel consorzio della call (WP8). Una
    candidatura accettata senza riga (dati precedenti al backfill della
    0040) non conta come uscita."""
    resp = (
        await primary.table("partner_call_membri")
        .select("stato")
        .eq("partner_call_id", str(call_id))
        .eq("company_profile_id", str(company_id))
        .limit(1)
        .execute()
    )
    riga = resp.data[0] if resp.data else None
    return isinstance(riga, dict) and riga.get("stato") == "uscito"


async def carica_call_autorizzata(
    primary, call_id: Any, active, user: Mapping, *, ammessi: Iterable[str] | None = None
) -> tuple[dict, Ruolo]:
    """(riga della call, ruolo). Fuori autorizzazione — o con un ruolo che la
    rotta non ammette — 404 come una call inesistente. Il pubblico (e chi ha
    una candidatura o un invito in attesa) vede solo le call di aziende vive;
    la controparte accettata anche dopo (la conversazione resta, in sola
    lettura), ma non una call sospesa per moderazione né dopo essere uscita
    dal consorzio (WP8: da lì vale come un'azienda qualunque)."""
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
    company_id = getattr(active, "company_id", None)
    if isinstance(call, dict) and ruolo not in RUOLI_AZIENDA and company_id:
        candidatura = await candidatura_su_call(primary, identificativo, company_id)
        if candidatura is not None:
            ruolo = ruolo_su_call(call, active, user, candidatura=candidatura)
        if ruolo == "controparte" and await uscita_dal_consorzio(
            primary, identificativo, company_id
        ):
            # WP8: chi è uscito (o è stato tolto) dal consorzio non è più
            # controparte, anche se la candidatura resta accettata (non si
            # ritira): niente riservati, budget esatto né consorzio.
            ruolo = ruolo_su_call(call, active, user)
    if ruolo in ("pubblico", "candidato", "invitato") and not await _azienda_viva(
        primary, call.get("company_profile_id")
    ):
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


DENOMINAZIONE_MAX = 200


def denominazione_nominativa(
    call: Mapping, dati_registro: Mapping | None, *, verificata_oggi: bool
) -> str | None:
    """La denominazione del Registro Imprese con cui il creatore di una call
    NOMINATIVA (`anonima` false) si mostra ai terzi (WP9, decisione di
    Michele), SOLO se oggi l'azienda ha l'identità verificata dalla
    piattaforma (`verificata_oggi`, `fn_partenariato_identita_forte`) e i dati
    del registro sono suoi (`dati_registro` già coerente, T5). Altrimenti
    None: il creatore resta «Azienda anonima» (una verifica revocata spegne il
    nome nelle viste successive). Mai P.IVA, sito, PEC né persone."""
    if call.get("anonima") is not False or not verificata_oggi:
        return None
    nome = _campo(dati_registro, "denominazione")
    if not isinstance(nome, str):
        return None
    nome = " ".join(senza_invisibili(nome).split())[:DENOMINAZIONE_MAX]
    return nome or None


def creatore_pubblico(
    dati_registro: Mapping | None, dossier: Mapping | None, regioni: Mapping[int, str],
    *, denominazione: str | None = None,
) -> CreatoreCallOut:
    """Il creatore di una call visto da terzi (C3): regione della sede,
    sezione ATECO e classe dimensionale dal Registro Imprese.
    `dati_registro` è la riga di `company_data` SOLO se è dell'azienda
    (`piva_fetched` = partita IVA attuale, T5); altrimenti None e non si
    mostra nulla. Anonima (default): «Azienda anonima». Con `denominazione`
    (da `denominazione_nominativa`: call nominativa di un'azienda verificata
    oggi) il nome del registro al posto di «Azienda anonima». Mai fasce,
    coperture o identificativi."""
    derived = _campo(dati_registro, "derived") or {}
    if not isinstance(derived, Mapping):
        derived = {}
    classe = derived.get("classe_dimensionale")
    classe = classe.strip().lower() if isinstance(classe, str) else None
    nome = denominazione.strip() if isinstance(denominazione, str) else ""
    return CreatoreCallOut(
        anonima=not nome,
        denominazione=nome or DENOMINAZIONE_ANONIMA,
        regione=_regione_sede(derived, regioni) if dati_registro else None,
        ateco_sezione=_sezione(derived, dossier) if dati_registro else None,
        classe_dimensionale=classe if dati_registro and classe in CLASSI_DIMENSIONALI else None,
    )


def _bando_pubblico(call: Mapping) -> BandoPubblicoCallOut:
    return BandoPubblicoCallOut(
        slug=str(call.get("bando_slug") or ""),
        titolo=str(call.get("bando_titolo") or ""),
        scadenza=call.get("bando_scadenza"),
        stato_effettivo=call.get("bando_stato_effettivo"),
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


# ------------------------------------------------------- bacheca (WP6)

DOMINIO_PSEUDONIMO = "partenariati.pseudonimo.v1"
LUNGHEZZA_PSEUDONIMO = 16


def pseudonimo(call_id: Any, codice_pubblico: Any) -> str:
    """Handle opaco di un candidato verso il creatore di UNA call (T3):
    `HMAC(call_id|codice_pubblico)` in base32, 16 caratteri. Cambia da una
    call all'altra (un anonimo non si correla tra call diverse), non è mai
    il `company_profile_id` né il `codice_pubblico`; lo risolve solo il
    server (`partenariato_indice.risolvi_pseudonimo`, per gli inviti)."""
    digest = hmac_dominio(DOMINIO_PSEUDONIMO, f"{call_id}|{codice_pubblico}")
    return base64.b32encode(bytes.fromhex(digest)).decode("ascii")[:LUNGHEZZA_PSEUDONIMO]


class _Uscita(BaseModel):
    # Whitelist anche nella costruzione, come le proiezioni del WP5.
    model_config = ConfigDict(extra="forbid")


class CallBachecaOut(CallCardOut):
    """Una call nelle liste (le mie, bacheca, salvate, «Per te»): la card del
    WP5 più il match dell'azienda attiva con la call (vista «proprio»; null
    se non è compatibile, se l'azienda attiva manca o se la call è sua), se
    l'azienda l'ha salvata, le candidature spontanee ricevute (in attesa o
    accettate, `partner_call_service.candidature_ricevute`) e i posti (somma
    dei partner cercati dalle posizioni)."""

    match: MatchOut | None = None
    salvata: bool = False
    candidature_ricevute: int = 0
    posti: int = 0


StatoCandidatura = Literal["inviata", "accettata", "rifiutata", "ritirata", "scaduta"]


class CandidaturaPropriaOut(_Uscita):
    """La candidatura (o l'invito) dell'azienda attiva su una call di altri
    (WP7), come la vede lei: stato (un invito in attesa oltre la scadenza vale
    già «scaduta»), scadenza dell'invito, motivo del rifiuto (è rivolto a
    lei), conversazione se accettata, e cosa può fare adesso il titolare."""

    id: UUID
    tipo: Literal["candidatura", "invito"]
    stato: StatoCandidatura
    posizione_id: UUID | None = None
    scade_at: datetime | None = None
    motivo_chiusura: str | None = None
    motivo_rifiuto: str | None = None
    conversazione_id: UUID | None = None
    created_at: datetime | None = None
    decisa_at: datetime | None = None
    chiusa_at: datetime | None = None
    puo_decidere: bool = False
    puo_ritirare: bool = False


class RequisitoDichiarabileOut(_Uscita):
    """Un requisito visibile della call (cercato o valido per ogni membro) che
    chi si candida può dichiarare di avere (WP7): l'id da mandare in
    `requisiti_dichiarati` e l'etichetta, la stessa di `requisiti`."""

    id: UUID
    etichetta: str


def requisiti_dichiarabili(
    requisiti: Iterable[Mapping], ident: Identificativi | None
) -> list[RequisitoDichiarabileOut]:
    return [
        RequisitoDichiarabileOut(id=r["id"], etichetta=_obbligatorio(r.get("etichetta"), ident))
        for r in requisiti
        if isinstance(r, Mapping) and r.get("id") and requisito_visibile(r)
    ]


class CallPubblicaDettaglioOut(CallPubblicaOut):
    """GET /partenariati/call/{id} per un'azienda che non l'ha creata: la
    proiezione pubblica, il proprio match (vista «proprio»), se l'ha salvata
    e se ha l'opt-in visibile (per candidarsi serve, Q25). WP7: la propria
    candidatura (se c'è) e i requisiti che si possono dichiarare
    candidandosi, con il loro id (gli stessi di `requisiti`, anche quando il
    match manca)."""

    match: MatchOut | None = None
    salvata: bool = False
    opt_in: bool = False
    candidatura: CandidaturaPropriaOut | None = None
    requisiti_dichiarabili: list[RequisitoDichiarabileOut] = []


class IdentitaRivelataOut(_Uscita):
    """Identità della controparte, SOLO con la rivelazione accesa e l'audit
    scritto all'accettazione (K2, Q13): ragione sociale, sito e PEC dal
    Registro Imprese (se i dati importati sono dell'azienda, T5), nome e
    ruolo del referente. MAI l'email personale del referente."""

    ragione_sociale: str | None = None
    sito_web: str | None = None
    pec: str | None = None
    referente_nome: str | None = None
    referente_ruolo: Literal["titolare", "referente"] | None = None


class CallVistaControparteOut(CallPubblicaOut):
    """GET /partenariati/call/{id} per la controparte ACCETTATA (WP7): la
    proiezione pubblica più i dettagli riservati e il budget esatto del
    progetto (mai i bilanci esatti di nessuno, mai id interni). Con la
    rivelazione spenta il creatore resta «Azienda anonima» e i dettagli
    riservati escono senza gli identificativi dell'azienda."""

    vista: Literal["controparte"] = "controparte"
    dettagli_riservati: str | None = None
    budget_progetto_eur: Decimal | None = None
    identita_rivelata: bool = False
    identita: IdentitaRivelataOut | None = None
    candidatura: CandidaturaPropriaOut


class PerTeOut(_Uscita):
    """GET /partenariati/per-te: pagina di call con il proprio match. Anche
    senza opt-in (solo scoperta, Q25): `opt_in` false → CTA per attivarlo."""

    items: list[CallBachecaOut]
    total: int
    page: int
    page_size: int
    total_pages: int
    opt_in: bool = False


# Il profilo pubblico del WP4 (whitelist, Q12 per gli anonimi) SENZA il
# `codice_pubblico`: stabile tra le call, permetterebbe al creatore di
# riconoscere lo stesso anonimo su call diverse. Verso il creatore l'handle è
# solo lo pseudonimo della call.
ProfiloSuggeritoOut = create_model(
    "ProfiloSuggeritoOut",
    __base__=_Uscita,
    **{
        nome: (campo.annotation, campo)
        for nome, campo in PartnerPubblicoOut.model_fields.items()
        if nome != "codice_pubblico"
    },
)


class CandidatoSuggeritoOut(_Uscita):
    """Un'azienda suggerita al creatore di una call (T3, Q12): pseudonimo
    della call (mai `company_profile_id` né `codice_pubblico`), profilo
    pubblico del WP4 (per gli anonimi: niente denominazione, sola fascia di
    fatturato, esperienze col solo programma, certificazioni per categoria)
    e match in vista «terzi» (solo fasce ed esiti). WP7: `stato_contatto`
    della call con lei (accettata, in attesa, invito rifiutato), null se non
    c'è un contatto che impedisca un invito."""

    pseudonimo: str
    profilo: ProfiloSuggeritoOut  # type: ignore[valid-type]
    match: MatchOut
    stato_contatto: Literal["accettata", "inviata", "rifiutata"] | None = None


class SuggeritiOut(_Uscita):
    """GET /partenariati/call/{id}/suggeriti."""

    items: list[CandidatoSuggeritoOut]
    total: int
    page: int
    page_size: int
    total_pages: int


class RiepilogoOut(_Uscita):
    """GET /partenariati/riepilogo (badge del menu): call «Per te» pubblicate
    negli ultimi 7 giorni, call pubblicate dell'azienda attiva, call salvate
    ancora visibili; dal WP7 inviti ricevuti in attesa (non scaduti),
    candidature ricevute da decidere e messaggi non letti dall'utente."""

    per_te_nuove: int = 0
    call_attive: int = 0
    salvate: int = 0
    inviti_ricevuti: int = 0
    candidature_da_decidere: int = 0
    messaggi_non_letti: int = 0


def call_bacheca(
    card: CallCardOut,
    *,
    match: MatchOut | None = None,
    salvata: bool = False,
    posti: int = 0,
    candidature_ricevute: int = 0,
) -> CallBachecaOut:
    return CallBachecaOut(
        **card.model_dump(),
        match=match,
        salvata=salvata,
        posti=posti,
        candidature_ricevute=candidature_ricevute,
    )


def candidato_suggerito(
    m: MatchInterno, *, call_id: Any, profilo: PartnerPubblicoOut,
    stato_contatto: str | None = None,
) -> CandidatoSuggeritoOut:
    """Proiezione del candidato verso il creatore: `profilo` è la proiezione
    pubblica del WP4 (`partner_profilo_pubblico.profilo_pubblico`)."""
    return CandidatoSuggeritoOut(
        pseudonimo=pseudonimo(call_id, m.codice_pubblico),
        profilo=ProfiloSuggeritoOut(**profilo.model_dump(exclude={"codice_pubblico"})),
        match=proietta_match(m, vista="terzi"),
        stato_contatto=stato_contatto,
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


# ------------------------------------------------------------ WP7


def _istante(valore: Any) -> datetime | None:
    if isinstance(valore, datetime):
        return valore if valore.tzinfo else valore.replace(tzinfo=timezone.utc)
    if not valore:
        return None
    try:
        letto = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None
    return letto if letto.tzinfo else letto.replace(tzinfo=timezone.utc)


def invito_scaduto(riga: Mapping, adesso: datetime | None = None) -> bool:
    """Un invito ancora `inviata` oltre la sua scadenza (lo scheduler o la
    lettura pigra non l'hanno ancora marcato): vale già come scaduto."""
    scade = _istante(riga.get("scade_at"))
    return (
        riga.get("tipo") == "invito"
        and riga.get("stato") == "inviata"
        and scade is not None
        and scade <= (adesso or datetime.now(timezone.utc))
    )


def stato_effettivo(riga: Mapping, adesso: datetime | None = None) -> str:
    return "scaduta" if invito_scaduto(riga, adesso) else str(riga.get("stato"))


def candidatura_propria(
    riga: Mapping, *, editable: bool, adesso: datetime | None = None
) -> CandidaturaPropriaOut:
    """La riga dell'azienda che si è candidata o è stata invitata (Y), vista
    da lei: decide gli inviti, ritira le candidature (solo il titolare)."""
    stato = stato_effettivo(riga, adesso)
    in_attesa = stato == "inviata"
    return CandidaturaPropriaOut(
        id=riga["id"],
        tipo=riga["tipo"],
        stato=stato,
        posizione_id=riga.get("posizione_id"),
        scade_at=riga.get("scade_at"),
        motivo_chiusura=riga.get("motivo_chiusura")
        or ("ttl" if stato != riga.get("stato") else None),
        motivo_rifiuto=testo_pubblico(riga.get("motivo_rifiuto"), None),
        conversazione_id=riga.get("conversazione_id"),
        created_at=riga.get("created_at"),
        decisa_at=riga.get("decisa_at"),
        chiusa_at=riga.get("chiusa_at"),
        puo_decidere=bool(editable and in_attesa and riga.get("tipo") == "invito"),
        puo_ritirare=bool(editable and in_attesa and riga.get("tipo") == "candidatura"),
    )


def identita_rivelata(
    company: Mapping | None, company_data: Mapping | None, *, referente_nome: str | None,
    referente_ruolo: str | None,
) -> IdentitaRivelataOut:
    """Dati d'impresa della controparte dal Registro Imprese, solo se i dati
    importati sono dell'azienda (T5); mai recapiti diversi da sito e PEC."""
    piva = (company or {}).get("partita_iva")
    coerente = bool(company_data and piva and company_data.get("piva_fetched") == piva)
    contatti: Mapping = {}
    if coerente and isinstance(company_data.get("raw"), dict):
        contatti = build_dossier(company_data["raw"]).get("contatti") or {}
    ruolo = referente_ruolo if referente_ruolo in ("titolare", "referente") else None
    return IdentitaRivelataOut(
        ragione_sociale=(str(company_data.get("denominazione") or "").strip() or None)
        if coerente else None,
        sito_web=contatti.get("sito_web") if coerente else None,
        pec=contatti.get("pec") if coerente else None,
        referente_nome=referente_nome,
        referente_ruolo=ruolo,
    )


def vista_controparte(
    pubblica: CallPubblicaOut,
    call: Mapping,
    candidatura: CandidaturaPropriaOut,
    *,
    ident: Identificativi | None,
    identita: IdentitaRivelataOut | None,
) -> CallVistaControparteOut:
    """La call per la controparte accettata (whitelist): la proiezione
    pubblica più riservati e budget esatto. Senza identità rivelata i
    riservati passano da `testo_pubblico` con gli identificativi del creatore
    (`ident`): nessuna identità in nessuna vista, nemmeno se il creatore
    l'aveva scritta lì; con l'identità rivelata solo senza caratteri
    invisibili (i contatti sono già vietati al salvataggio)."""
    riservati = call.get("dettagli_riservati")
    if identita is None:
        riservati = testo_pubblico(riservati, ident)
    elif isinstance(riservati, str):
        riservati = senza_invisibili(riservati).strip() or None
    budget = call.get("budget_progetto_eur")
    return CallVistaControparteOut(
        **pubblica.model_dump(),
        dettagli_riservati=riservati if isinstance(riservati, str) else None,
        budget_progetto_eur=Decimal(str(budget)) if budget is not None else None,
        identita_rivelata=identita is not None,
        identita=identita,
        candidatura=candidatura,
    )


# ------------------------------------------------------------ WP8

# Stati della call in cui il consorzio si modifica (come le RPC della 0040):
# anche scaduta, perché il partenariato può essere andato avanti dopo la
# scadenza della ricerca di partner (come una call completata).
STATI_CONSORZIO_MODIFICABILE: frozenset[str] = frozenset(
    {"pubblicata", "scaduta", "chiusa_completata"}
)
NOME_ESTERNO_RIMOSSO = "Membro esterno"


def _percentuale(valore: Any) -> Decimal | None:
    """Quota numeric(5,2) (PostgREST la restituisce come numero) con due
    decimali; illeggibile → None."""
    if valore is None or isinstance(valore, bool):
        return None
    try:
        numero = Decimal(str(valore))
    except (ArithmeticError, ValueError):
        return None
    return numero.quantize(Decimal("0.01")) if numero.is_finite() else None


def permessi_membro(
    riga: Mapping, *, call: Mapping, viewer_company_id: Any, sei_creatore: bool, editable: bool
) -> tuple[bool, bool, bool]:
    """(può modificare, può confermare, può uscire) il destinatario su UNA
    riga del consorzio, con le regole delle RPC della 0040: modifica e
    rimozione solo il creatore (titolare) e solo con la call pubblicata,
    scaduta o chiusa come completata (un esterno uscito si ripropone
    modificandolo);
    conferma la propria riga (il creatore anche quella degli esterni) se è
    proposta e ha la quota (un partner associato, che non riceve budget,
    anche senza); si esce dalla propria riga in qualunque stato della call,
    mai dalla riga del creatore."""
    if not editable:
        return False, False, False
    modificabile = call.get("stato") in STATI_CONSORZIO_MODIFICABILE
    company = riga.get("company_profile_id")
    esterno = company is None
    propria = not esterno and viewer_company_id is not None and (
        str(company) == str(viewer_company_id)
    )
    del_creatore = not esterno and str(company) == str(call.get("company_profile_id"))
    attivo = riga.get("stato") != "uscito"
    puo_modificare = sei_creatore and modificabile and (attivo or esterno)
    puo_confermare = (
        modificabile
        and riga.get("stato") == "proposto"
        and (riga.get("quota_percentuale") is not None
             or riga.get("ruolo") == "associated_partner")
        and (propria or (sei_creatore and esterno))
    )
    puo_uscire = attivo and not del_creatore and (propria or (sei_creatore and modificabile))
    return puo_modificare, puo_confermare, puo_uscire


def proietta_membro(
    riga: Mapping,
    *,
    call: Mapping,
    viewer_company_id: Any,
    sei_creatore: bool,
    editable: bool,
    ident_creatore: Identificativi | None,
    nome_proprio: str | None = None,
    nome_creatore: str | None = None,
    pseudonimo_membro: str | None = None,
    profilo: ProfiloMembroOut | None = None,  # type: ignore[valid-type]
    posizione: Mapping | None = None,
) -> MembroOut:
    """Una riga di `partner_call_membri` per il destinatario (T3, Q11,
    whitelist). `sei_creatore` = il destinatario è l'azienda che ha creato
    la call (titolare o membro con visibilità). Il nome:
    - propria azienda: `nome_proprio` (il suo nome);
    - esterno: il nome dichiarato dal creatore, verso gli altri senza gli
      identificativi del creatore né contatti;
    - altre aziende in piattaforma: «Azienda anonima» con lo pseudonimo della
      call (`pseudonimo_membro`) e il profilo pubblico anonimo (`profilo`);
      chi ha creato la call senza pseudonimo né profilo (come nella chat del
      WP7: è «il creatore della call»), con `nome_creatore` se la call è
      nominativa e l'azienda verificata oggi (`denominazione_nominativa`,
      lo stesso nome della vista pubblica).
    Testi del creatore (titolo della posizione) ripuliti verso gli altri."""
    company = riga.get("company_profile_id")
    esterno = company is None
    propria = not esterno and viewer_company_id is not None and (
        str(company) == str(viewer_company_id)
    )
    del_creatore = not esterno and str(company) == str(call.get("company_profile_id"))
    ident = None if sei_creatore else ident_creatore
    if esterno:
        denominazione = riga.get("esterno_denominazione")
        nome = (
            (denominazione.strip() if isinstance(denominazione, str) else None)
            if sei_creatore else testo_pubblico(denominazione, ident)
        ) or NOME_ESTERNO_RIMOSSO
    elif propria:
        nome = (nome_proprio or "").strip() or DENOMINAZIONE_ANONIMA
    elif del_creatore:
        nome = (nome_creatore or "").strip() or DENOMINAZIONE_ANONIMA
    else:
        nome = DENOMINAZIONE_ANONIMA
    titolo = None
    if isinstance(posizione, Mapping) and str(posizione.get("call_id")) == str(call.get("id")):
        titolo = (
            posizione.get("titolo") if sei_creatore else testo_pubblico(posizione.get("titolo"),
                                                                         ident)
        ) or "Posizione"
    modifica, conferma, uscita = permessi_membro(
        riga, call=call, viewer_company_id=viewer_company_id, sei_creatore=sei_creatore,
        editable=editable,
    )
    paese = riga.get("esterno_paese") if esterno else None
    return MembroOut(
        id=riga["id"],
        esterno=esterno,
        creatore=del_creatore,
        sei_tu=propria,
        nome=nome,
        pseudonimo=pseudonimo_membro if not (esterno or propria or del_creatore) else None,
        profilo=profilo if not (esterno or propria or del_creatore) else None,
        paese=paese if isinstance(paese, str) else None,
        tipi_soggetto=[t for t in _lista(riga.get("esterno_tipi_soggetto"))
                       if t in voc.TIPI_SOGGETTO] if esterno else [],
        ruolo=riga.get("ruolo") or "partner",
        posizione=PosizioneMembroOut(id=posizione["id"], titolo=titolo)
        if titolo is not None else None,
        quota_percentuale=_percentuale(riga.get("quota_percentuale")),
        stato=riga.get("stato") or "proposto",
        confermato_at=riga.get("confermato_at"),
        puo_modificare=modifica,
        puo_confermare=conferma,
        puo_uscire=uscita,
    )


# ------------------------------------------------------------ WP9


class CallVistaProgettistaOut(_Uscita):
    """GET /progettista/richieste/{id}/call (WP9, W1, Q19): la call di un
    consulto chiesto dalla call, per il progettista ASSEGNATO. È la vista del
    creatore a whitelist (il progettista lavora per lui): regole confermate,
    requisiti con la copertura del creatore, posizioni, dettagli riservati e
    budget del creatore, più il consorzio nella proiezione del creatore
    (validazione e matrice) con i partner in piattaforma SOLO come «Azienda
    anonima» con i loro esiti e fasce: niente pseudonimo né profilo pubblico,
    mai contatti, messaggi, candidature, valori esatti o id interni di altre
    aziende. Fuori anche i limiti del piano, i job AI e i motivi di blocco del
    cliente (non servono al consulto)."""

    richiesta_id: UUID
    id: UUID
    stato: StatoCall
    motivo_chiusura: MotivoChiusura | None = None
    bando: BandoCallOut
    ruolo_creatore: RuoloCreatore
    forma_aggregazione_prevista: FormaPrevista | None = None
    anonima: bool = True
    titolo: str | None = None
    descrizione_pubblica: str | None = None
    dettagli_riservati: str | None = None
    profilo_partner_ideale: str | None = None
    budget_fascia: BudgetFascia | None = None
    budget_progetto_eur: Decimal | None = None
    quota_creatore_pct: Decimal | None = None
    scadenza_call: date | None = None
    visibilita: Visibilita = "pubblica"
    regole_partenariato: RegoleCallSnapshot | None = None
    regole_confermate_at: datetime | None = None
    esclusivita: bool = False
    posizioni: list[PosizioneOut] = []
    requisiti: list[RequisitoOut] = []
    riepilogo: RiepilogoGapOut = RiepilogoGapOut()
    partenariato: PartenariatoGapOut = PartenariatoGapOut()
    pubblicata_at: datetime | None = None
    chiusa_at: datetime | None = None
    # None per una bozza (il consorzio nasce alla pubblicazione).
    consorzio: ConsorzioOut | None = None


# I campi della vista del creatore che passano al progettista (whitelist).
CAMPI_VISTA_PROGETTISTA: frozenset[str] = frozenset({
    "id", "stato", "motivo_chiusura", "bando", "ruolo_creatore",
    "forma_aggregazione_prevista", "anonima", "titolo", "descrizione_pubblica",
    "dettagli_riservati", "profilo_partner_ideale", "budget_fascia", "budget_progetto_eur",
    "quota_creatore_pct", "scadenza_call", "visibilita", "regole_partenariato",
    "regole_confermate_at", "esclusivita", "posizioni", "pubblicata_at", "chiusa_at",
})


def consorzio_per_progettista(consorzio: ConsorzioOut) -> ConsorzioOut:
    """Il consorzio nella proiezione del CREATORE (validazione, matrice,
    documenti e budget del creatore: dei partner già solo esiti e fasce),
    ridotto per il progettista: gli altri membri in piattaforma senza
    pseudonimo né profilo pubblico, nessuna azione possibile, nessun «sei tu»
    (il progettista non è un membro)."""
    membri = [
        m.model_copy(update={
            "sei_tu": False, "pseudonimo": None, "profilo": None,
            "puo_modificare": False, "puo_confermare": False, "puo_uscire": False,
        })
        for m in consorzio.membri
    ]
    return consorzio.model_copy(update={
        "membri": membri,
        "editable": False,
        "modificabile": False,
        "budget": consorzio.budget.model_copy(update={"modificabile": False}),
    })


def vista_progettista(
    richiesta_id: Any, creatore: CallVistaCreatoreOut, consorzio: ConsorzioOut | None
) -> CallVistaProgettistaOut:
    """`CallVistaProgettistaOut` dalla vista del creatore (letta con
    un'azienda attiva in sola lettura) e dal consorzio nella proiezione del
    creatore: whitelist campo per campo."""
    return CallVistaProgettistaOut(
        richiesta_id=richiesta_id,
        **{campo: getattr(creatore, campo) for campo in CAMPI_VISTA_PROGETTISTA},
        requisiti=creatore.gap.requisiti,
        riepilogo=creatore.gap.riepilogo,
        partenariato=creatore.gap.partenariato,
        consorzio=consorzio_per_progettista(consorzio) if consorzio is not None else None,
    )
