/** Stringhe user-facing condivise o duplicate in più punti.
 *
 *  Il resto dell'app tiene le stringhe inline nei componenti (non c'è i18n):
 *  qui stanno SOLO quelle che comparivano — o comparirebbero — in più posti e
 *  che divergevano. Il claim sui bandi era ripetuto tre volte nella landing
 *  (fascia statistiche, hero, FAQ) e bastava aggiornarne due per renderla
 *  incoerente. Non aggiungere qui stringhe usate una volta sola. */

import type {
  ErroreRichiestaBilancio,
  FiltroPartenariato,
  ModalitaPartenariato,
  MotivoBilanci,
  MotivoIdentitaPartner,
  MotivoNominativoPartner,
  StatoRichiestaBilancio,
  UserRole,
  XbrlEsito,
} from "../types";

/** Il conteggio è un claim di marketing, NON un dato: la landing non interroga
 *  il catalogo. Aggiornarlo qui lo aggiorna in tutti e tre i punti. */
const BANDI_MONITORATI = "4.000";

export const LANDING_COPY = {
  /** Fascia statistiche: valore + etichetta. */
  bandiValore: `${BANDI_MONITORATI}+`,
  bandiEtichetta: "Bandi monitorati",
  /** Badge dell'hero. */
  bandiClaim: `Più di ${BANDI_MONITORATI} bandi monitorati`,
  /** Risposta FAQ «Quanti bandi trovo?». */
  bandiFaq: `Più di ${BANDI_MONITORATI} bandi monitorati e aggiornati di continuo, su quattro livelli: europeo, nazionale, regionale e locale.`,
} as const;

/** Avviso di quota AI-check in esaurimento. Il seguito del messaggio cambia
 *  con quello che l'utente può effettivamente fare: proporre un upgrade a chi
 *  non può comprarlo (figlio attivo) o non ha dove salire è solo rumore. */
export const QUOTA_BANNER_COPY = {
  titoloWarning: "Gli AI-check del tuo piano stanno per esaurirsi",
  titoloEsaurito: "AI-check esauriti",
  consumo: (usati: number, totale: number) =>
    `Hai usato ${usati} dei ${totale} AI-check inclusi nel tuo piano.`,
  esaurito: "Hai esaurito gli AI-check inclusi nel tuo piano per questo periodo.",
  invitoUpgrade: "Passa a un piano superiore per proseguire le analisi senza interruzioni.",
  /** A quota finita l'interruzione è già avvenuta: «senza interruzioni» suonerebbe falso. */
  invitoUpgradeEsaurito: "Passa a un piano superiore per riprendere le analisi.",
  gestitoDalTitolare: "Le quote sono condivise con l'azienda: il piano lo gestisce il titolare.",
  pianoMassimo: (rinnovo: string) =>
    `Il tuo è già il piano più completo: la quota si rinnova il ${rinnovo}.`,
  pianoMassimoSenzaData: "Il tuo è già il piano più completo.",
  cta: "Vedi i piani",
  chiudi: "Nascondi questo avviso",
} as const;

/** Etichette dei ruoli utente: compaiono nel badge della lista admin, nel
 *  filtro, nel select di cambio ruolo e nel dialog di conferma. */
export const RUOLO_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  cliente: "Cliente",
  progettista: "Progettista",
};

/** Campanella e pannello notifiche in-app. */
export const NOTIFICHE_COPY = {
  apri: "Notifiche",
  apriConNonLette: (n: number) => `Notifiche: ${n} non lett${n === 1 ? "a" : "e"}`,
  titoloPannello: "Notifiche",
  vuoto: "Nessuna notifica.",
  erroreCaricamento: "Impossibile caricare le notifiche.",
  segnaTutteLette: "Segna tutte come lette",
  // Centro alert (pagina /app/notifiche).
  vediTutte: "Vedi tutte",
  titoloPagina: "Notifiche",
  sottotitoloPagina: "Tutti gli avvisi della piattaforma, dai bandi compatibili agli aggiornamenti.",
  filtroTutte: "Tutte le aziende",
  filtroAria: "Filtra le notifiche per azienda",
  vuotoAzienda: "Nessuna notifica per questa azienda.",
  /** Link di una notifica (`?azienda=<id>`) verso un'azienda che l'utente non
   *  vede tra le sue: la pagina resta sull'azienda attiva. */
  aziendaNonGestita: "Questa notifica riguarda un'azienda che non gestisci.",
} as const;

/** Note del dialog di conferma cambio ruolo: cosa comporta la transizione.
 *  Parità admin: l'area progettista è di progettisti E amministratori, quindi
 *  si «perde» solo tornando cliente. */
export const ADMIN_RUOLO_COPY = {
  promozioneProgettista:
    "Avrà l'area progettista con un codice identificativo (assegnato ora, o riusato se già esistente), mantenendo tutte le funzionalità cliente.",
  nominaAdmin:
    "Come amministratore ha anche l'area progettista (stesse funzioni dei progettisti); il codice identificativo viene assegnato alla prima proposta inviata.",
  perditaAreaProgettista:
    "Perderà l'accesso all'area progettista. Il suo eventuale codice resta riservato: un futuro ritorno all'area lo riutilizzerà.",
} as const;

/** Stati del flusso consulenze: compaiono nei badge di liste e dettagli, sia
 *  lato cliente sia lato progettista. */
export const CONSULENZA_STATO_LABELS: Record<
  import("../types").ConsulenzaStato,
  string
> = {
  nuova: "In attesa di proposte",
  assegnata: "Assegnata",
  annullata: "Annullata",
};

/** Etichette degli stati di un acquisto: badge dello storico utente
 *  (/app/acquisti) e della vista admin pagamenti. */
export const PURCHASE_STATO_LABELS: Record<import("../types").PurchaseStatus, string> = {
  in_attesa: "In attesa",
  pagato: "Pagato",
  fallito: "Fallito",
  scaduto: "Scaduto",
  annullato: "Annullato",
  gratuito: "Gratuito",
};

/** Etichette dei tipi di acquisto: filtro della vista admin pagamenti e badge
 *  degli acquisti d'origine amministrativa (cambio piano, accredito addon). */
export const PURCHASE_KIND_LABELS: Record<import("../types").PurchaseKind, string> = {
  piano: "Piano",
  rinnovo: "Rinnovo",
  addon: "Add-on",
  cambio_admin: "Cambio amministratore",
  addon_admin: "Accredito addon",
};

/** Etichette dei movimenti del ledger addon (Abbonamento + I miei addon). */
export const ADDON_MOVIMENTO_LABELS: Record<import("../types").AddonMovimentoTipo, string> = {
  purchase: "Acquisto",
  admin_grant: "Accredito dall'assistenza",
  consume: "Utilizzo",
  refund: "Rimborso",
  admin_revoke: "Rettifica",
};

export const PROPOSTA_STATO_LABELS: Record<import("../types").PropostaStato, string> = {
  inviata: "Inviata",
  accettata: "Accettata",
  rifiutata: "Rifiutata",
  superata: "Superata",
  ritirata: "Ritirata",
};

/** Flusso consulenze: stringhe condivise tra CTA, dettaglio cliente e area
 *  progettista. Il testo di consenso è parte della base giuridica del
 *  trattamento: non riformularlo senza rivedere l'informativa privacy. */
export const CONSULENZE_COPY = {
  consenso:
    "Attivando il consulto, i progettisti della piattaforma vedranno la ragione sociale, la partita IVA, la tua email e il report completo dell'AI-check di questo bando, comprese le informazioni aziendali citate nelle sue verifiche. Il dossier certificato e gli altri dati aziendali restano riservati: li vedrà solo il progettista che sceglierai.",
  fusoOrario: "Gli orari sono mostrati nel tuo fuso orario.",
} as const;

/** Chiude la frase senza raddoppiare il punto: le ragioni sociali finiscono
 *  quasi sempre per «S.R.L.» o «S.P.A.». */
const chiudi = (frase: string) => (frase.endsWith(".") ? frase : `${frase}.`);

/** Import dei dati aziendali via P.IVA. Ogni stato ha un messaggio: il
 *  silenzio, in un'operazione che costa credito e può durare minuti, si legge
 *  come «non funziona». */
export const IMPORT_COPY = {
  titoloForm: "Importa da P.IVA",
  titoloAttesa: "Recupero in corso",
  titoloAnteprima: "Conferma l'importazione",
  titoloEsito: "Dati importati",

  introForm:
    "Recuperiamo i dati ufficiali della tua azienda dal Registro Imprese tramite openapi.it: anagrafica, ATECO, sede e unità locali, cariche, dipendenti, bilanci degli ultimi anni e altro.",
  attesa:
    "Recupero dei dati ufficiali e dei bilanci dal Registro Imprese in corso. L'operazione può richiedere qualche minuto: non chiudere questa finestra.",

  /** L'anteprima non salva nulla: il testo lo dice prima che l'utente lo chieda. */
  anteprimaTrovata: (piva: string, ragioneSociale: string) =>
    chiudi(`Per la partita IVA ${piva} risulta registrata ${ragioneSociale}`),
  anteprimaSenzaNome: (piva: string) =>
    `Per la partita IVA ${piva} è stata trovata un'azienda nel Registro Imprese.`,
  anteprimaIstruzioni:
    "Verifica i dati e conferma per importarli nel profilo aziendale. I campi già compilati non verranno sovrascritti.",
  anteprimaRiusata:
    "Stai vedendo i dati recuperati poco fa: confermarli non comporta un nuovo addebito.",
  /** Un'azienda cessata o sospesa è quasi sempre una P.IVA sbagliata. */
  anteprimaStatoAnomalo: (stato: string) =>
    `Il Registro Imprese riporta questa azienda come «${stato}». Verifica che la partita IVA sia quella corretta.`,
  campiCompilati: "Campi che verranno compilati",
  campiNonToccati: "Campi già compilati che non verranno modificati",
  nessunCampo:
    "Il profilo aziendale è già completo: la conferma aggiorna solo i dati certificati e il dossier.",

  confermaImporta: "Conferma e importa",
  annulla: "Annulla",
  /** Chiedere conferma dell'annullamento evita di buttare via un fetch pagato. */
  annullaTitolo: "Annullare l'importazione?",
  annullaTesto:
    "I dati recuperati non verranno salvati. Potrai riavviare l'importazione senza un nuovo addebito nei prossimi 30 minuti.",
  annullaConferma: "Annulla importazione",
  annullaRipensamento: "Torna all'anteprima",

  pivaInvalida: "La partita IVA non è valida: verifica le 11 cifre.",
  esitoImportato: (ragioneSociale: string) =>
    `Dati ufficiali di «${ragioneSociale}» importati dal Registro Imprese.`,

  /** Blocco «Bilanci» dell'anteprima. */
  bilanciTitolo: "Bilanci",
  bilanciTrovati: (n: number, anni: string) =>
    n === 1 ? `Trovato 1 esercizio (${anni})` : `Trovati ${n} esercizi (${anni})`,
  bilanciTrovatiNota: "Dopo la conferma li trovi, anno per anno, nella pagina Azienda.",
  bilanciNonTrovati: "Bilanci non disponibili",
  bilanciRecuperabili: "Potrai recuperarli dalla pagina Azienda.",
  bilanciStoricoRecuperabile: "Potrai recuperare lo storico dalla pagina Azienda.",
} as const;

/** Bilanci per esercizio: i motivi compaiono sia nell'anteprima dell'import
 *  sia nella sezione «Bilanci» della pagina Azienda. Frasi neutre: l'invito a
 *  riprovare lo aggiunge chi può farlo (il titolare), non il motivo. */
export const BILANCI_COPY = {
  motivi: {
    nessun_bilancio: "Il Registro Imprese non ha bilanci depositati per questa azienda.",
    forma_senza_bilancio:
      "Per la forma giuridica di questa azienda non è previsto il deposito del bilancio al Registro Imprese.",
    errore_provider: "Il servizio che fornisce i bilanci non ha risposto correttamente.",
    esito_incerto: "Il servizio che fornisce i bilanci non ha risposto in tempo.",
    tempo_insufficiente:
      "L'importazione è durata più del previsto e i bilanci non sono stati recuperati.",
    dati_non_corrispondenti:
      "I bilanci ricevuti non corrispondevano alla partita IVA dell'azienda, quindi non li abbiamo salvati.",
    non_richiesto: "I bilanci degli anni passati non sono ancora stati recuperati.",
    piva_diversa:
      "La partita IVA cercata è diversa da quella dei dati aziendali, quindi non abbiamo recuperato i bilanci.",
  } satisfies Record<MotivoBilanci, string>,
  /** Al posto di `motivi.nessun_bilancio` quando un esercizio c'è già (dalla
   *  visura): «nessun bilancio depositato» contraddirebbe la tabella. */
  nessunAltroBilancio: "Lo storico del Registro Imprese non riporta altri bilanci di questa azienda.",
  /** Con `piva_diversa` il recupero parte solo dopo la correzione. */
  correggiPiva: "Correggi la partita IVA nei dati aziendali, poi recupera lo storico.",
  recupera: "Recupera i bilanci",
} as const;

/** Card «Bilancio ufficiale» della sezione Bilanci: il bilancio depositato,
 *  pagato con un'unità dell'addon. I messaggi di esito arrivano già pronti dal
 *  server (`messaggio`): quelli qui sotto servono solo se manca. */
export const BILANCIO_UFFICIALE_COPY = {
  titolo: "Bilancio ufficiale",
  descrizione:
    "Il bilancio depositato al Registro Imprese, in PDF. I suoi numeri si aggiungono ai bilanci di questa pagina e hanno la precedenza sulle altre fonti.",
  prezzo: (testo: string) => `Prezzo: ${testo} a bilancio`,
  disponibili: (n: number) => (n === 1 ? "Ne hai 1 disponibile." : `Ne hai ${n} disponibili.`),
  esercizio: "Esercizio",
  ultimoDisponibile: "Ultimo disponibile",
  giaAcquisiti: (anni: string) => `Hai già il bilancio ufficiale di: ${anni}.`,
  richiedi: "Richiedi il bilancio ufficiale",
  inCorso: "Hai già una richiesta in corso: potrai farne un'altra quando si conclude.",
  senzaUnita: (prezzo: string) =>
    `Non hai bilanci ufficiali disponibili: acquistane uno (${prezzo}) per procedere.`,
  acquista: "Acquista un bilancio ufficiale",
  soloTitolare: "Il bilancio ufficiale lo richiede il titolare dell'azienda.",
  erroreCaricamento: "Impossibile caricare i bilanci ufficiali.",

  confermaTitolo: "Richiedi il bilancio ufficiale",
  conferma: (n: number) =>
    `Userai 1 bilancio ufficiale (ne hai ${n}). Di solito arriva entro 15 minuti: ti avvisiamo.`,
  confermaEsercizio: (anno: number | null) =>
    anno === null ? "Esercizio: l'ultimo disponibile." : `Esercizio: ${anno}.`,
  confermaRimborso:
    "Se il Registro Imprese non ha questo bilancio, l'unità ti viene restituita.",
  confermaInvia: "Conferma e richiedi",
  annulla: "Annulla",

  storicoTitolo: "Storico delle richieste",
  esercizioTitolo: (anno: number | null) =>
    anno === null ? "Ultimo esercizio disponibile" : `Esercizio ${anno}`,
  richiestoIl: (quando: string) => `Richiesto il ${quando}`,
  prontoIl: (quando: string) => `pronto il ${quando}`,
  /** Stato in parole: il colore del badge non basta da solo. */
  stati: {
    in_invio: "In invio",
    in_lavorazione: "In preparazione",
    esito_ignoto: "In verifica",
    completata: "Pronto",
    non_disponibile: "Non disponibile",
    annullata: "Annullata",
    errore: "Non riuscita",
  } satisfies Record<StatoRichiestaBilancio, string>,
  attesa: "Di solito arriva entro 15 minuti: ti avvisiamo quando è pronto.",
  verifica:
    "Non sappiamo ancora se la richiesta è arrivata al Registro Imprese: lo verifichiamo noi, non serve rifarla.",
  completataOk: "I suoi numeri sono ora nei bilanci dell'azienda.",
  unitaRestituita: "Unità restituita",
  unitaRestituitaFrase: "L'unità ti è stata restituita.",
  scaricaPdf: "Scarica PDF",
  /** Coda solo per lettori di schermo: distingue i bottoni della lista. */
  scaricaPdfContesto: (anno: number | null) =>
    anno === null ? " del bilancio" : ` del bilancio ${anno}`,
  scaricamento: "Download…",
  avvisi: (n: number) =>
    n === 1
      ? "C'è 1 avviso sulla lettura dei numeri: confrontali con il PDF."
      : `Ci sono ${n} avvisi sulla lettura dei numeri: confrontali con il PDF.`,
  /** Stessi avvisi quando il PDF non è stato conservato: niente rimando al PDF. */
  avvisiSenzaPdf: (n: number) =>
    n === 1 ? "C'è 1 avviso sulla lettura dei numeri." : `Ci sono ${n} avvisi sulla lettura dei numeri.`,
  /** Completata ma senza PDF né numeri (troppo grande, illeggibile): non è «Pronto». */
  statoNonUtilizzabile: "Non utilizzabile",
  senzaPdf: "Il PDF non è stato conservato: se ti serve, scrivi all'assistenza.",
  esitiLettura: {
    assente:
      "Il documento non contiene i numeri in un formato leggibile: trovi il bilancio nel PDF.",
    firmato_non_leggibile:
      "I numeri sono in un file firmato che non riusciamo a leggere: trovi il bilancio nel PDF.",
    non_valido: "Non siamo riusciti a leggere i numeri dal documento: trovi il bilancio nel PDF.",
    consolidato:
      "È il bilancio consolidato di un gruppo: i suoi numeri non entrano nei bilanci dell'azienda.",
    cf_non_corrispondente:
      "Il documento riporta un codice fiscale diverso da quello dell'azienda, quindi non abbiamo usato i suoi numeri.",
    troppo_grande: "Il documento ricevuto è troppo grande per essere elaborato per intero.",
  } satisfies Record<Exclude<XbrlEsito, "ok">, string>,
  errori: {
    bilancio_non_disponibile: "Il Registro Imprese non ha questo bilancio.",
    forma_non_ammessa:
      "Per la forma giuridica di questa azienda il bilancio ufficiale non è disponibile.",
    identificativo_non_valido: "Il Registro Imprese non riconosce la partita IVA dell'azienda.",
    credito_provider: "Il servizio non è disponibile in questo momento: riprova più tardi.",
    non_inviata: "La richiesta non è partita: riprova più tardi.",
    errore_provider: "Il servizio che fornisce i bilanci ha avuto un problema.",
    scaduta:
      "Il bilancio non è arrivato entro 24 ore. Scrivi all'assistenza: verifichiamo e, se serve, ti restituiamo l'unità.",
    esito_ignoto_scaduto:
      "Non abbiamo ricevuto risposta dal Registro Imprese. Scrivi all'assistenza: verifichiamo e, se serve, ti restituiamo l'unità.",
  } satisfies Record<ErroreRichiestaBilancio, string>,

  /** Esito annunciato subito dopo la conferma. */
  esitoInviata: "Richiesta inviata. Di solito arriva entro 15 minuti: ti avvisiamo quando è pronto.",
  esitoSenzaConferma:
    "Non abbiamo ricevuto la conferma in tempo. Controlla le richieste qui sotto: se compare, è partita e non devi rifarla.",
} as const;

/** Regole di partenariato del bando (WP3): card e sezione di BandoDetail,
 *  filtro e chip della lista bandi. */
export const PARTENARIATO_COPY = {
  /** Avvertenza fissa sotto le regole estratte: non riformulare senza il
   *  legale (docs/partenariati.md §6, testi legali in copy.ts). */
  disclaimer:
    "Estratto automaticamente dai documenti ufficiali: verifica sempre sul testo del bando prima di decidere.",
  /** Badge della modalità: sempre in parole, il colore non basta da solo. */
  modalita: {
    obbligatorio: "Partenariato obbligatorio",
    ammesso: "Partenariato ammesso",
    non_ammesso: "Solo partecipazione singola",
    non_determinabile: "Modalità non chiara",
  } satisfies Record<ModalitaPartenariato, string>,
  modalitaSpiegazione: {
    obbligatorio:
      "Si partecipa solo insieme ad altri soggetti (raggruppamento, rete, consorzio o simili).",
    ammesso: "Puoi partecipare da solo oppure insieme ad altri soggetti.",
    non_ammesso: "Il bando non ammette partenariati: si partecipa da soli.",
    non_determinabile: "Il testo disponibile non basta per stabilirlo: controlla il bando.",
  } satisfies Record<ModalitaPartenariato, string>,
  /** Filtro della lista bandi e relativo chip. */
  filtroTitolo: "Partenariato",
  filtroTutti: "Tutti",
  filtro: {
    ammesso: "Ammette partenariato",
    obbligatorio: "Richiede partenariato",
  } satisfies Record<FiltroPartenariato, string>,
  filtroNota: "Solo tra i bandi già analizzati: l'elenco cresce man mano.",
} as const;

/** Profilo partner e consensi (WP4): sezione della pagina Azienda, dialog di
 *  consenso (anche nel passo finale dell'import) e referente. Le frasi del
 *  consenso e della revoca sono testi legali: non riformularle senza rivedere
 *  l'informativa (il testo dell'informativa arriva dal server, versionato). */
export const PARTNER_COPY = {
  titoloSezione: "Visibilità come partner",
  descrizioneSezione:
    "Fatti trovare dalle altre aziende che cercano con chi partecipare a un bando. Decidi tu cosa mostrare, e se mostrare il nome.",
  statoVisibile: "Visibile come partner",
  statoNonVisibile: "Non visibile",
  statoSospeso: "Sospeso",
  conNome: "Con il nome dell'azienda",
  anonima: "Anonima",
  soloTitolare: "Il profilo partner lo gestisce il titolare dell'azienda.",
  nessunProfilo:
    "Il titolare non ha ancora compilato il profilo partner di questa azienda.",
  sospeso:
    "Il profilo è sospeso dalla piattaforma: per ora non compare tra i partner suggeriti. Per chiarimenti scrivi all'assistenza.",

  /** Dialog di consenso: la checkbox NON è mai preselezionata. */
  consensoTitolo: "Comparire come partner",
  consensoIntro:
    "Leggi l'informativa: spiega cosa vedono le altre aziende e cosa succede quando accetti un contatto.",
  consensoInformativa: "Informativa sulla visibilità come partner",
  consensoCheckbox: "Acconsento a comparire come partner suggerito alle altre aziende",
  sceltaNomeTitolo: "Come vuoi comparire?",
  sceltaNome: "Mostra il nome dell'azienda",
  sceltaNomeNota: "Le altre aziende vedono il nome registrato al Registro Imprese.",
  sceltaAnonima: "Resta anonima",
  sceltaAnonimaNota:
    "Niente nome: le altre aziende vedono meno dettagli (solo la fascia di fatturato, esperienze senza anno né ruolo, certificazioni per categoria).",
  attiva: "Attiva",
  nonOra: "Non ora",
  /** Passo facoltativo alla fine dell'import (solo se si può attivare). */
  passoImport:
    "Ultimo passo, facoltativo: vuoi comparire come partner per le altre aziende che cercano con chi partecipare a un bando? Premi «Continua» per leggere come funziona.",
  continua: "Continua",
  annulla: "Annulla",
  attivato: "Fatto: ora compari tra i partner suggeriti. Puoi revocare quando vuoi.",
  informativaNonCaricata: "Impossibile caricare l'informativa.",
  informativaAggiornata:
    "L'informativa è stata aggiornata nel frattempo: leggi il nuovo testo e conferma di nuovo.",

  /** Perché «Mostra il nome» non si può scegliere. */
  motiviNominativo: {
    cf_non_verificato:
      "Per mostrare il nome devi verificare il tuo codice fiscale nel profilo: così controlliamo che tu sia legale rappresentante dell'azienda.",
    non_rappresentante:
      "Per mostrare il nome devi risultare legale rappresentante dell'azienda nel Registro Imprese. Puoi comunque comparire in forma anonima.",
    non_disponibile:
      "Per ora le aziende compaiono solo in forma anonima: mostrare il nome sarà possibile più avanti, con una verifica di chi rappresenta l'impresa.",
  } satisfies Record<MotivoNominativoPartner, string>,
  verificaCf: "Verifica il codice fiscale",

  /** Perché il consenso non si può ancora dare. */
  motiviIdentita: {
    dati_non_importati:
      "Per comparire come partner importa prima i dati ufficiali dell'azienda dalla partita IVA.",
    piva_diversa:
      "La partita IVA dei dati aziendali è diversa da quella dei dati importati: aggiorna i dati dal Registro Imprese.",
    impresa_non_attiva:
      "Nel Registro Imprese l'azienda non risulta attiva: per comparire come partner deve esserlo.",
    dati_sandbox:
      "I dati importati sono di prova: per comparire come partner servono quelli reali del Registro Imprese.",
  } satisfies Record<MotivoIdentitaPartner, string>,
  identitaTitolo: "Servono i dati ufficiali dell'azienda",
  importa: "Importa da P.IVA",

  riconsensoTitolo: "L'informativa è cambiata",
  riconsensoTesto:
    "Il tuo consenso resta valido, ma ti chiediamo di rileggere la nuova informativa e confermarlo.",
  riconsensoCta: "Rileggi e conferma",

  /** Revoca: la frase del dialog è fissata dal piano (docs/partenariati.md §6). */
  revoca: "Revoca la visibilità",
  revocaTitolo: "Revocare la visibilità?",
  revocaTesto:
    "La revoca è immediata: non comparirai più nei suggerimenti. Le conversazioni già avviate restano.",
  revocaConferma: "Revoca",
  revocata: "Visibilità revocata: non compari più tra i partner suggeriti.",
  /** Il trigger del database revoca da solo la visibilità se cambiano questi dati. */
  revocaSuCambioAzienda:
    "Se cambi la ragione sociale o la partita IVA dell'azienda, la visibilità si revoca da sola: dovrai riattivarla.",
  attivaVisibilita: "Attiva la visibilità",

  anonimatoTitolo: "Nome dell'azienda",
  anonimatoCambiaInNome: "Mostra il nome",
  anonimatoCambiaInAnonima: "Rendi anonima",
  anonimatoOraConNome: "Ora le altre aziende vedono il nome dell'azienda.",
  anonimatoOraAnonima: "Ora l'azienda compare in forma anonima.",

  completezza: (n: number) => `Profilo completo al ${n}%`,
  completezzaNota: "Più il profilo è completo, più è facile che le altre aziende ti scelgano.",

  /** Bozza AI (generata a carico della piattaforma, mai pubblicata da sola). */
  bozzaCta: "Scrivi una bozza con l'AI",
  bozzaTitolo: "Bozza del profilo con l'AI",
  bozzaIntro:
    "Partiamo dai dati del Registro Imprese (attività e settore) e da quello che hai già scritto: l'AI propone una descrizione e alcune competenze. Non viene pubblicato nulla: scegli tu cosa usare, poi salvi il profilo.",
  bozzaAvvia: "Genera la bozza",
  bozzaRigenera: "Genera una nuova bozza",
  bozzaInCorso: "Sto preparando la bozza: di solito basta meno di un minuto.",
  bozzaLunga: "La bozza sta impiegando più del previsto.",
  bozzaPronta: "La bozza è pronta: scegli cosa usare.",
  bozzaProntaBreve: "La bozza dell'AI è pronta.",
  bozzaRivedi: "Rivedi la bozza",
  bozzaErrore: "Non siamo riusciti a preparare la bozza.",
  bozzaApplica: "Applica al profilo",
  bozzaApplicata:
    "Abbiamo riportato le parti scelte nel profilo qui sotto: controllale e salva.",
  bozzaScarta: "Scarta la bozza",
  bozzaUsaDescrizione: "Usa questa descrizione",
  bozzaCompetenzeTitolo: "Competenze proposte",
  bozzaNienteScelto: "Scegli almeno una parte da usare.",

  /** Referente per i partenariati. */
  referenteTitolo: "Referente per i partenariati",
  referenteDescrizione:
    "La persona che le altre aziende vedono (nome e ruolo) quando accetti un contatto. L'email personale non viene mai mostrata.",
  referenteTu: "Tu (titolare)",
  referenteAttuale: (nome: string) => `Referente attuale: ${nome}`,
  referenteInAttesa: (nome: string) =>
    `Proposta inviata a ${nome}: diventa referente solo quando accetta.`,
  referenteProponi: "Proponi come referente",
  referenteAnnullaProposta: "Annulla la proposta",
  referentePropostaAnnullata: (nome: string | null) =>
    nome
      ? `Proposta annullata: il referente resta ${nome}.`
      : "Proposta annullata: il referente resti tu.",
  referenteTorna: "Torna referente tu",
  referenteNessunMembro:
    "Per proporre un'altra persona invitala prima tra gli account collegati, con accesso a questa azienda.",
  referenteSceltaLabel: "Persona da proporre",
  referenteProposto: "Ti hanno proposto come referente per i partenariati",
  referenteInformativa: "Informativa per il referente",
  referenteCheckbox: "Ho letto l'informativa per il referente",
  referenteAccetta: "Accetta",
  referenteRifiuta: "Rifiuta",
  referenteSeiTu: "Sei il referente per i partenariati di questa azienda.",
  referenteRinuncia: "Rinuncia",
  referenteRinunciaTitolo: "Rinunciare al ruolo di referente?",
  referenteRinunciaTesto:
    "Da subito il referente torna il titolare dell'azienda. Potrà proporti di nuovo in futuro.",

  /** Anteprima «come ti vedono». */
  anteprimaTitolo: "Come ti vedono le altre aziende",
  anteprimaNota: "Anteprima del profilo salvato: le modifiche compaiono dopo il salvataggio.",
  anteprimaNonVisibile:
    "Oggi il profilo non è visibile: le altre aziende lo vedranno così quando attivi la visibilità.",
  aziendaAnonima: "Azienda anonima",

  /** Barra di salvataggio del profilo. */
  modificheNonSalvate: "Hai modifiche non salvate al profilo partner",
  salva: "Salva il profilo",
  salvato: "Profilo salvato ✓",
} as const;
