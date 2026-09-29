"""Errori delle funzioni SQL del modulo partenariati (docs/partenariati.md, T2).

Mappa UNICA per tutto il modulo: le RPC sollevano con `detail` = codice
macchina e qui diventa un `AppError(status, code, messaggio)`. I `code` sono
specifici perché il frontend ci ramifica sopra (niente `rate_limited`
generico). Ogni WP successivo AGGIUNGE voci a `RPC_ERRORS`, senza cambiare
quelle esistenti: un detail non mappato è un guasto interno (log + 502).
"""

import logging
from typing import NoReturn

from postgrest.exceptions import APIError

from app.core.errors import AppError, UpstreamError

logger = logging.getLogger("bandofit.partenariati")

_LIMITE_GIORNALIERO = "Hai raggiunto il numero di analisi di oggi: riprova domani"

# detail della RPC → (status HTTP, code macchina, messaggio per l'utente)
RPC_ERRORS: dict[str, tuple[int, str, str]] = {
    # WP3 — estrazione delle regole di partenariato e budget AI del modulo
    "partenariato_cooldown": (
        429,
        "partenariato_cooldown",
        "Le regole di questo bando sono state analizzate da poco: riprova più tardi",
    ),
    "ai_limite_utente": (429, "ai_limite_giornaliero", _LIMITE_GIORNALIERO),
    "ai_limite_owner": (429, "ai_limite_giornaliero", _LIMITE_GIORNALIERO),
    "ai_budget_esaurito": (
        429,
        "ai_sospesa_oggi",
        "L'analisi automatica è sospesa per oggi: riprova domani",
    ),
    # WP4 — profilo partner, consensi, referente e bozza AI (migration 0035)
    "company_not_found": (404, "not_found", "Azienda non trovata"),
    "azione_non_valida": (400, "bad_request", "Operazione non valida"),
    "origine_non_valida": (400, "bad_request", "Origine del consenso non valida"),
    "versione_non_valida": (400, "bad_request", "Versione dell'informativa non valida"),
    "attore_non_titolare": (
        403,
        "forbidden",
        "Il profilo partner lo gestisce il titolare dell'azienda",
    ),
    "anonimato_obbligatorio": (
        400,
        "anonimato_obbligatorio",
        "Scegli se mostrare il nome dell'azienda o restare anonima",
    ),
    "identita_non_verificata": (
        409,
        "identita_non_verificata",
        "Per comparire come partner importa prima i dati ufficiali dell'azienda dalla "
        "partita IVA: l'impresa deve risultare attiva nel Registro Imprese",
    ),
    "rappresentante_non_verificato": (
        409,
        "rappresentante_non_verificato",
        "Per mostrare il nome dell'azienda devi risultarne legale rappresentante: verifica "
        "il tuo codice fiscale nel profilo. Puoi comunque comparire in forma anonima",
    ),
    "profilo_sospeso": (
        409,
        "profilo_sospeso",
        "Il profilo partner è sospeso: per ora non può tornare visibile",
    ),
    "referente_non_valido": (
        400,
        "referente_non_valido",
        "La persona scelta deve essere un membro attivo con accesso a questa azienda",
    ),
    "nessuna_proposta_referente": (
        409,
        "nessuna_proposta_referente",
        "Non c'è nessuna proposta di referente da confermare",
    ),
    "bozza_in_corso": (
        409,
        "bozza_in_corso",
        "La bozza del profilo è già in preparazione: attendi qualche istante",
    ),
    "ai_limite_azienda": (
        429,
        "ai_limite_giornaliero",
        "Hai raggiunto le bozze di oggi per questa azienda: riprova domani",
    ),
    # WP5 — call di partenariato (migration 0036-0037). Restano NON mappati di
    # proposito (sono bug del backend, non errori dell'utente → 502):
    # parametri_non_validi, versione_immutabile, stato_non_valido.
    "owner_not_found": (404, "not_found", "Account del titolare non trovato"),
    "call_not_found": (404, "not_found", "Call di partenariato non trovata"),
    "piano_non_include_call": (
        403,
        "piano_non_include_call",
        "Il tuo piano non include la creazione di call di partenariato",
    ),
    "limite_call_raggiunto": (
        409,
        "limite_call_raggiunto",
        "Hai raggiunto il numero massimo di call attive del tuo piano",
    ),
    "troppe_bozze": (
        409,
        "troppe_bozze",
        "Hai troppe call in bozza per questa azienda: completane o annullane una",
    ),
    "call_gia_presente": (409, "call_gia_presente", "Hai già una call per questo bando"),
    "stato_call_non_valido": (
        409,
        "stato_call_non_valido",
        "La call non si può modificare in questo stato",
    ),
    "campo_non_modificabile": (
        400,
        "campo_non_modificabile",
        "Dopo la pubblicazione questo campo non si può più modificare",
    ),
    "scadenza_call_non_valida": (
        400,
        "scadenza_call_non_valida",
        "La scadenza della call deve essere tra oggi e la scadenza del bando",
    ),
    "call_incompleta": (
        400,
        "call_incompleta",
        "La call non è completa: servono titolo, descrizione, regole del bando confermate, "
        "almeno una posizione e almeno un requisito cercato",
    ),
    "bando_non_disponibile": (
        409,
        "bando_non_disponibile",
        "Il bando non è aperto: per ora non si possono pubblicare call",
    ),
    "ai_in_corso": (
        409,
        "ai_in_corso",
        "La proposta è già in preparazione: attendi qualche istante",
    ),
    "ai_limite_call": (
        429,
        "ai_limite_giornaliero",
        "Hai raggiunto le proposte di oggi per questa call: riprova domani",
    ),
    "dati_non_validi": (400, "bad_request", "Dati della call non validi"),
    "regole_non_valide": (400, "bad_request", "Regole di partenariato non valide"),
    "requisiti_non_validi": (
        400,
        "bad_request",
        "Requisiti non validi: le regole finanziarie devono coincidere con quelle del bando "
        "confermate e ogni etichetta va usata una sola volta",
    ),
    "posizioni_non_valide": (
        400,
        "bad_request",
        "Posizioni non valide: controlla i dati e i requisiti collegati",
    ),
    # WP7 — candidature, inviti e chat (migration 0039). Resta NON mappato di
    # proposito, come nel WP5, `parametri_non_validi`: il servizio valida
    # prima della RPC, quindi arriva solo da un bug del backend (→ 502).
    "azienda_non_disponibile": (404, "not_found", "Azienda non disponibile"),
    "call_non_attiva": (409, "call_non_attiva", "La call non è più aperta"),
    "call_solo_invitati": (
        409,
        "call_solo_invitati",
        "Questa call accetta solo aziende invitate",
    ),
    "stesso_gruppo": (
        409,
        "stesso_gruppo",
        "Non puoi collaborare con un'altra azienda del tuo stesso account",
    ),
    "profilo_partner_non_attivo": (
        409,
        "profilo_partner_non_attivo",
        "Per candidarti attiva la visibilità come partner della tua azienda",
    ),
    "funzione_non_inclusa": (
        409,
        "funzione_non_inclusa",
        "Il tuo piano non include le candidature di tua iniziativa: puoi comunque ricevere "
        "inviti e accettarli",
    ),
    "candidature_esaurite": (
        409,
        "candidature_esaurite",
        "Hai usato tutte le candidature di questo mese",
    ),
    "posizione_non_valida": (400, "bad_request", "Posizione non valida per questa call"),
    "candidatura_gia_attiva": (
        409,
        "candidatura_gia_attiva",
        "C'è già una candidatura attiva per questa call",
    ),
    "partner_non_disponibile": (
        409,
        "partner_non_disponibile",
        "L'azienda non è al momento disponibile per un invito",
    ),
    "inviti_esauriti_call": (
        409,
        "inviti_esauriti_call",
        "Hai troppi inviti in attesa di risposta per questa call",
    ),
    "invito_gia_attivo": (409, "invito_gia_attivo", "C'è già un invito attivo per questa call"),
    "candidatura_non_trovata": (404, "not_found", "Candidatura non trovata"),
    "candidatura_gia_decisa": (
        409,
        "candidatura_gia_decisa",
        "La richiesta è già stata gestita",
    ),
    "invito_scaduto": (409, "invito_scaduto", "L'invito è scaduto"),
    "esclusivita_violata": (
        409,
        "esclusivita_violata",
        "Il bando ammette un solo partenariato per azienda e l'azienda è già impegnata su "
        "questo bando",
    ),
    "conversazione_non_trovata": (404, "not_found", "Conversazione non trovata"),
    "conversazione_chiusa": (409, "conversazione_chiusa", "La conversazione è chiusa"),
    "controparte_non_disponibile": (
        409,
        "controparte_non_disponibile",
        "L'altra azienda non è più disponibile",
    ),
    "posizione_con_candidature": (
        409,
        "posizione_con_candidature",
        "Una posizione con candidature attive non si può rimuovere",
    ),
    "messaggio_immutabile": (409, "messaggio_immutabile", "I messaggi non si modificano"),
    "posizione_con_membri": (
        409,
        "posizione_con_membri",
        "Una posizione assegnata a un membro del consorzio non si può rimuovere: spostalo "
        "prima su un'altra posizione dalla scheda Consorzio",
    ),
    # WP8 — consorzio della call e checklist documentale (migration 0040).
    # `parametri_non_validi` resta NON mappato (il servizio valida prima).
    "membro_non_trovato": (404, "not_found", "Membro del consorzio non trovato"),
    "call_non_modificabile": (
        409,
        "call_non_modificabile",
        "Il consorzio di questa call non si può più modificare",
    ),
    "capofila_gia_presente": (
        409,
        "capofila_gia_presente",
        "Il consorzio ha già un capofila: cambia prima il suo ruolo",
    ),
    "quota_mancante": (
        409,
        "quota_mancante",
        "Per confermare serve la quota del membro",
    ),
    "membro_non_rimovibile": (
        409,
        "membro_non_rimovibile",
        "Chi ha creato la call resta nel proprio consorzio",
    ),
    "membro_uscito": (409, "membro_uscito", "Il membro è uscito dal consorzio"),
    "membro_modificato": (
        409,
        "membro_modificato",
        "Ruolo, posizione o quota sono cambiati nel frattempo: controlla i nuovi valori e "
        "conferma di nuovo",
    ),
    "limite_membri": (
        409,
        "limite_membri",
        "Il consorzio ha raggiunto il numero massimo di membri",
    ),
    "ruolo_non_ammesso": (
        409,
        "ruolo_non_ammesso",
        "Chi ha creato la call partecipa come capofila o come partner",
    ),
    "documento_non_valido": (
        400,
        "documento_non_valido",
        "Documento non previsto dalla checklist di questa call",
    ),
}


def raise_from_rpc(exc: APIError) -> NoReturn:
    """Traduce l'errore di una funzione SQL del modulo in `AppError`.

    Detail non mappato (o assente) → log con code e detail, poi
    `UpstreamError`: il chiamante non deve mai vedere il testo grezzo di
    Postgres."""
    detail = (exc.details or "").strip()
    mappato = RPC_ERRORS.get(detail)
    if mappato:
        raise AppError(*mappato) from exc
    logger.error(
        "partenariati: errore RPC non mappato (code=%s, detail=%s)", exc.code, detail or None
    )
    raise UpstreamError() from exc
