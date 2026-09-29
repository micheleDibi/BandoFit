"""Testi della moderazione dei partenariati (WP9, W2, DSA art. 16-20).

Lo statement of reasons (DSA art. 17) si genera da un TEMPLATE versionato e
deterministico: stessi dati, stesso testo (l'anteprima dell'admin coincide
con quello inviato). È un testo SEGNAPOSTO da far rivedere al legale prima di
accendere il flag in produzione, e la prima riga lo dichiara. Contiene:
decisione ed effetto, fatti e circostanze (segnalazione ricevuta o decisione
d'ufficio, categoria della segnalazione, motivazione dell'admin), fondamento
(Termini d'uso, riferimento da completare), uso di mezzi automatizzati, vie di
ricorso (interno entro 6 mesi, più le vie esterne generiche).

MAI l'identità di chi ha segnalato, né la sua descrizione: all'autore arriva
solo la categoria della segnalazione. Le notifiche in-app hanno testi brevi
senza dati (il dettaglio si legge dalla pagina della segnalazione, con
l'autorizzazione live).

Regole per chi modifica i testi: italiano semplice, del tu, mai «Famiglia»;
ogni cambio di sostanza fa salire `SOR_VERSIONE` (il testo inviato resta
nella segnalazione come prova).
"""

import calendar
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

SOR_VERSIONE = "2026-10-bozza-1"
INTESTAZIONE_BOZZA = "[BOZZA — DA RIVEDERE CON IL LEGALE]"
# Ricorso interno (DSA art. 20): uno solo, entro 6 mesi dalla decisione, come
# `fn_partner_segnalazione_ricorso` (0041).
MESI_RICORSO = 6
_FUSO = ZoneInfo("Europe/Rome")

OggettoModerato = Literal["call", "profilo", "messaggio"]
Origine = Literal["segnalazione", "ricorso", "ufficio"]

# Decisione restrittiva per tipo di contenuto (coerenza della 0041).
DECISIONE_PER_OGGETTO: dict[str, str] = {
    "call": "call_sospesa",
    "profilo": "profilo_sospeso",
    "messaggio": "contenuto_rimosso",
}

# Come le etichette della UI (`SEGNALAZIONE.segnalaMotivi` in lib/copy.ts).
ETICHETTE_MOTIVO: dict[str, str] = {
    "contenuto_illecito": "contenuto illecito",
    "dati_personali": "dati personali di altre persone",
    "spam_pubblicita": "spam o pubblicità",
    "contatti_nel_testo": "contatti nel testo",
    "discriminatorio": "contenuto discriminatorio",
    "impersonificazione": "si spaccia per un'altra azienda",
    "altro": "altro",
}

_EFFETTO: dict[str, str] = {
    "call": (
        "Abbiamo sospeso la tua call di partenariato: finché resta sospesa non è visibile "
        "alle altre aziende e non riceve candidature."
    ),
    "profilo": (
        "Abbiamo sospeso il profilo partner della tua azienda: finché resta sospeso non "
        "compare tra i partner suggeriti e non riceve inviti."
    ),
    "messaggio": (
        "Abbiamo oscurato un tuo messaggio nella chat di partenariato: l'altra azienda non ne "
        "vede più il testo."
    ),
}

# Nomi brevi dei contenuti, per notifiche ed email.
NOME_OGGETTO: dict[str, str] = {
    "call": "la call di partenariato",
    "profilo": "il profilo partner",
    "messaggio": "un messaggio della chat",
}

_FONDAMENTO = (
    "Termini d'uso della piattaforma BandoFit, sezione «Moderazione dei contenuti» "
    "[riferimento puntuale da completare con il legale]. Se il contenuto è illecito, "
    "anche la normativa applicabile [da completare con il legale]."
)
_VIE_ESTERNE = (
    "Puoi anche rivolgerti a un organismo di risoluzione extragiudiziale delle controversie "
    "certificato (art. 21 del Regolamento UE 2022/2065, «DSA») o all'autorità giudiziaria "
    "competente."
)
_CONTATTO_RIESAME = "[indirizzo per i riesami da completare]"


def _locale(istante: datetime) -> datetime:
    if istante.tzinfo is None:
        istante = istante.replace(tzinfo=timezone.utc)
    return istante.astimezone(_FUSO)


def data_it(istante: datetime) -> str:
    """«gg/mm/aaaa» nel fuso di Roma."""
    return _locale(istante).strftime("%d/%m/%Y")


def aggiungi_mesi(istante: datetime, mesi: int) -> datetime:
    """Come `timestamptz + interval 'N months'` di Postgres: stesso giorno del
    mese, o l'ultimo se il mese di arrivo è più corto."""
    totale = istante.month - 1 + mesi
    anno, mese = istante.year + totale // 12, totale % 12 + 1
    giorno = min(istante.day, calendar.monthrange(anno, mese)[1])
    return istante.replace(year=anno, month=mese, day=giorno)


def scadenza_ricorso(deciso_at: datetime) -> datetime:
    """Ultimo istante utile per il ricorso interno (6 mesi dalla decisione)."""
    return aggiungi_mesi(deciso_at, MESI_RICORSO)


def codice_breve(identificativo: object) -> str:
    """Il codice mostrato all'utente nella conferma di ricezione (8 caratteri)."""
    return str(identificativo)[:8]


def genera_statement(
    *,
    oggetto_tipo: str,
    motivazione: str,
    deciso_at: datetime,
    origine: Origine,
    riferimento: str | None = None,
    motivo_segnalazione: str | None = None,
    segnalazione_at: datetime | None = None,
) -> str:
    """Statement of reasons (DSA art. 17) di una RESTRIZIONE su `oggetto_tipo`
    (call sospesa, profilo sospeso, messaggio oscurato).

    `origine`: `segnalazione` (decisione su una segnalazione: il ricorso
    interno si presenta dalla pagina della segnalazione), `ricorso` (la
    restrizione nasce dal ricorso accolto di chi aveva segnalato: su quella
    segnalazione non resta un ricorso interno, il riesame si chiede per
    iscritto) o `ufficio` (sospensione diretta dell'admin, senza
    segnalazione). Deterministico: nessun dato oltre agli argomenti."""
    if oggetto_tipo not in _EFFETTO:
        raise ValueError(f"oggetto non moderabile: {oggetto_tipo}")
    scadenza = data_it(scadenza_ricorso(deciso_at))
    righe = [
        INTESTAZIONE_BOZZA,
        "",
        "Motivazione della decisione di moderazione",
        f"Versione {SOR_VERSIONE}"
        + (f" · Riferimento {riferimento}" if riferimento else ""),
        "",
        "Cosa abbiamo deciso",
        _EFFETTO[oggetto_tipo],
        f"Data della decisione: {data_it(deciso_at)}.",
        "",
        "Fatti e circostanze",
    ]
    categoria = ETICHETTE_MOTIVO.get(motivo_segnalazione or "")
    quando = f" il {data_it(segnalazione_at)}" if segnalazione_at else ""
    if origine == "segnalazione":
        righe.append(
            f"La decisione segue una segnalazione ricevuta{quando}"
            + (f" (categoria: {categoria})" if categoria else "")
            + ". Chi ha segnalato non viene identificato."
        )
    elif origine == "ricorso":
        righe.append(
            f"La decisione segue una segnalazione ricevuta{quando}"
            + (f" (categoria: {categoria})" if categoria else "")
            + ": in un primo momento non avevamo preso provvedimenti, poi abbiamo accolto il "
            "ricorso di chi aveva segnalato. Chi ha segnalato non viene identificato."
        )
    else:
        righe.append(
            "La decisione è stata presa d'ufficio dal nostro staff durante i controlli sulla "
            "piattaforma, senza una segnalazione."
        )
    righe += [
        f"Motivazione: {motivazione.strip()}",
        "",
        "Fondamento",
        _FONDAMENTO,
        "",
        "Uso di mezzi automatizzati",
        "La decisione è stata presa da una persona del nostro staff, senza mezzi automatizzati.",
    ]
    if motivo_segnalazione == "contatti_nel_testo":
        righe.append(
            "Per i recapiti nei testi la piattaforma usa anche controlli automatici al "
            "salvataggio; il contenuto e la decisione sono stati comunque esaminati da una "
            "persona."
        )
    righe += ["", "Come contestare la decisione"]
    if origine == "segnalazione":
        righe.append(
            "Puoi presentare un ricorso alla piattaforma una sola volta, entro il "
            f"{scadenza}, dalla pagina della segnalazione: lo esamina il nostro staff e ti "
            "comunichiamo la decisione motivata."
        )
    else:
        righe.append(
            f"Puoi chiedere un riesame scrivendo a {_CONTATTO_RIESAME} entro il {scadenza}: "
            "lo esamina il nostro staff e ti comunichiamo la decisione motivata."
        )
    righe.append(_VIE_ESTERNE)
    return "\n".join(righe)


# Tetto della motivazione di una sospensione d'ufficio: sta per intero nella
# notifica in-app (`statement_breve`, notifications.corpo ≤ 1000) ed è quella
# che resta sull'oggetto (`sospeso_motivo`, ≤ 500).
MOTIVAZIONE_UFFICIO_MAX = 500
_CORPO_MAX = 1000


def statement_breve(*, oggetto_tipo: str, motivazione: str, deciso_at: datetime) -> str:
    """Lo statement of reasons di una sospensione D'UFFICIO nella forma breve
    della notifica in-app (≤ 1000 caratteri): decisione e data, nessuna
    segnalazione e nessun mezzo automatizzato, motivazione per intero (≤ 500),
    fondamento, riesame con il termine e vie esterne. Il testo completo
    (`genera_statement`, origine `ufficio`) va per email al titolare e resta
    nell'audit; per le decisioni su una segnalazione il testo completo si legge
    dalla pagina della segnalazione. Deterministico."""
    if oggetto_tipo not in _EFFETTO:
        raise ValueError(f"oggetto non moderabile: {oggetto_tipo}")
    scadenza = data_it(scadenza_ricorso(deciso_at))

    def componi(testo: str) -> str:
        return (
            f"{INTESTAZIONE_BOZZA} Decisione d'ufficio del {data_it(deciso_at)}, presa da una "
            "persona del nostro staff senza una segnalazione e senza mezzi automatizzati. "
            f"Motivazione: {testo} Fondamento: Termini d'uso, sezione «Moderazione dei "
            f"contenuti». Per contestarla chiedi un riesame scrivendo a {_CONTATTO_RIESAME} "
            f"entro il {scadenza}; puoi anche rivolgerti a un organismo extragiudiziale "
            "certificato (art. 21 DSA) o al giudice."
        )

    testo = " ".join(motivazione.split())
    if testo and testo[-1] not in ".!?…":
        testo += "."
    corpo = componi(testo)
    if len(corpo) > _CORPO_MAX:  # difesa: il servizio limita la motivazione
        eccesso = len(corpo) - _CORPO_MAX + 1
        corpo = componi(testo[: len(testo) - eccesso].rstrip() + "…")
    return corpo


# ------------------------------------------------------------- notifiche
#
# Testi brevi e senza dati (notifications.corpo ≤ 1000): il dettaglio si legge
# dalla pagina della segnalazione.

TIPO_DECISIONE = "moderazione.decisione"
TIPO_RICORSO_RICEVUTO = "moderazione.ricorso_ricevuto"
TIPO_RICORSO_DECISO = "moderazione.ricorso_deciso"
TIPO_RIPRISTINO = "moderazione.ripristino"


def titolo_restrizione(oggetto_tipo: str) -> str:
    return {
        "call": "La tua call di partenariato è stata sospesa",
        "profilo": "Il profilo partner della tua azienda è stato sospeso",
        "messaggio": "Un tuo messaggio è stato oscurato",
    }[oggetto_tipo]


CORPO_RESTRIZIONE = (
    "Abbiamo preso una decisione di moderazione su un contenuto della tua azienda. Apri la "
    "notifica per leggere la motivazione e sapere come contestarla."
)
TITOLO_ESITO_SEGNALANTE = "La tua segnalazione è stata esaminata"


def corpo_esito_segnalante(decisione: str, codice: str) -> str:
    if decisione == "nessuna_azione":
        esito = "Non abbiamo preso provvedimenti sul contenuto segnalato."
    else:
        esito = "Abbiamo preso provvedimenti sul contenuto segnalato."
    return (f"{esito} Codice della segnalazione: {codice}. Nella pagina della segnalazione "
            "trovi la motivazione e, se non sei d'accordo, puoi presentare ricorso.")


TITOLO_RICORSO_RICEVUTO = "Abbiamo ricevuto il tuo ricorso"


def corpo_ricorso_ricevuto(codice: str) -> str:
    return (f"Lo esaminiamo e ti comunichiamo la decisione motivata. Codice della "
            f"segnalazione: {codice}.")


def titolo_ricorso_deciso(esito: str) -> str:
    return ("Il tuo ricorso è stato accolto" if esito == "riformata"
            else "Il tuo ricorso non è stato accolto")


def corpo_ricorso_deciso(codice: str, *, mantenuto: bool = False) -> str:
    """`mantenuto`: ricorso accolto, ma il contenuto resta sospeso per un'altra
    decisione ancora valida (o una sospensione d'ufficio)."""
    resta = (" Il contenuto resta comunque sospeso per un'altra decisione di moderazione."
             if mantenuto else "")
    return (f"Nella pagina della segnalazione trovi la motivazione della decisione sul "
            f"ricorso.{resta} Codice della segnalazione: {codice}.")


def titolo_ripristino(oggetto_tipo: str) -> str:
    return {
        "call": "La tua call di partenariato non è più sospesa",
        "profilo": "Il profilo partner della tua azienda non è più sospeso",
        "messaggio": "Un tuo messaggio è di nuovo visibile",
    }[oggetto_tipo]


CORPO_RIPRISTINO = "Abbiamo annullato la decisione di moderazione su un contenuto della tua azienda."

# Identità verificata dall'admin (decisione di Michele, Q9 rivista).
TIPO_IDENTITA = "partenariato.identita_azienda"
_TITOLI_IDENTITA = {
    "verificata": "L'identità della tua azienda è verificata",
    "rifiutata": "Non abbiamo potuto verificare l'identità della tua azienda",
    "revocata": "La verifica dell'identità della tua azienda è stata revocata",
}
_CORPI_IDENTITA = {
    "verificata": (
        "Ora puoi mostrare il nome dell'azienda nel profilo partner, e dopo un'accettazione "
        "le identità si rivelano tra aziende verificate."
    ),
    "rifiutata": (
        "Puoi chiedere di nuovo la verifica dalla pagina Azienda. Intanto puoi continuare a "
        "usare i partenariati in forma anonima."
    ),
    "revocata": (
        "Il nome dell'azienda non si mostra più alle altre aziende. Puoi chiedere di nuovo la "
        "verifica dalla pagina Azienda."
    ),
}


def titolo_identita(esito: str) -> str:
    return _TITOLI_IDENTITA[esito]


def corpo_identita(esito: str) -> str:
    return _CORPI_IDENTITA[esito]


def adesso() -> datetime:
    return datetime.now(timezone.utc)


def entro_ricorso(deciso_at: datetime | None, istante: datetime | None = None) -> bool:
    """True se il termine del ricorso interno non è ancora passato (la RPC
    rifiuta un ricorso con `deciso_at < now() - 6 mesi`)."""
    if deciso_at is None:
        return False
    return (istante or adesso()) <= scadenza_ricorso(deciso_at)
