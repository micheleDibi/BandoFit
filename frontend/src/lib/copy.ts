/** Stringhe user-facing condivise o duplicate in più punti.
 *
 *  Il resto dell'app tiene le stringhe inline nei componenti (non c'è i18n):
 *  qui stanno SOLO quelle che comparivano — o comparirebbero — in più posti e
 *  che divergevano. Il claim sui bandi era ripetuto tre volte nella landing
 *  (fascia statistiche, hero, FAQ) e bastava aggiornarne due per renderla
 *  incoerente. Non aggiungere qui stringhe usate una volta sola. */

import type {
  AmbitoRequisitoCall,
  BudgetFasciaCall,
  CategoriaCertificazione,
  DecisioneSegnalazione,
  DimensioneImpresa,
  ErroreRichiestaBilancio,
  EsitoCoperturaCall,
  EsitoRicorso,
  EsitoVoce,
  FaseDocumentoConsorzio,
  FiltroPartenariato,
  MetodoVerificaIdentita,
  ModalitaPartenariato,
  MotivoBilanci,
  MotivoChiusuraCall,
  MotivoChiusuraCandidatura,
  MotivoIdentitaPartner,
  MotivoNominativoPartner,
  MotivoSegnalazione,
  OggettoSegnalazione,
  OrdineBacheca,
  OrigineRequisitoCall,
  RuoloCreatoreCall,
  RuoloMembro,
  RuoloPartner,
  StatoCall,
  StatoCandidatura,
  StatoDocumentoConsorzio,
  StatoIdentitaAzienda,
  StatoMembro,
  StatoRichiestaBilancio,
  StatoSegnalazione,
  TerritorioModalitaCall,
  TipoCandidatura,
  TipoCriterio,
  UserRole,
  VistaPartenariati,
  VisibilitaCall,
  XbrlEsito,
} from "../types";

/** Il conteggio è un claim di marketing, NON un dato: la landing non interroga
 *  il catalogo. Aggiornarlo qui lo aggiorna in tutti e tre i punti. */
const BANDI_MONITORATI = "4.000";

/** Versione dei testi legali del WP9 scritti nel frontend (consenso del
 *  consulto dalla call, ricorso e vie di ricorso): SEGNAPOSTO, «BOZZA — DA
 *  RIVEDERE CON IL LEGALE» prima di accendere il modulo in produzione. La
 *  motivazione formale delle decisioni la genera il backend
 *  (`partenariato_moderazione_testi.SOR_VERSIONE`). */
export const TESTI_LEGALI_WP9_VERSIONE = "2026-10-bozza-1";

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
  /** Consulto chiesto dalla call di partenariato (WP9). BOZZA — DA RIVEDERE
   *  CON IL LEGALE (versione `TESTI_LEGALI_WP9_VERSIONE`): è parte della base
   *  giuridica, come `consenso`. */
  consensoCall:
    "Chiedendo il consulto, i progettisti della piattaforma vedranno solo che hai chiesto un consulto su una call di partenariato e il titolo del bando. Il progettista che sceglierai vedrà anche la ragione sociale, la partita IVA, la tua email, il dossier certificato dell'azienda, il report dell'AI-check di questo bando se ne hai uno e la call (testi, regole, requisiti con le tue coperture, posizioni, budget e verifica del consorzio). Delle altre aziende della call vedrà solo esiti e fasce: mai i loro nomi, i contatti, i messaggi o i loro numeri.",
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
  /** Profilo salvato con il nome ma senza la verifica dell'identità di oggi
   *  (revocata o dati del registro cambiati): verso le altre aziende è anonimo. */
  nomeSalvatoNonMostrato:
    "Hai scelto di mostrare il nome, ma oggi le altre aziende non lo vedono: serve la verifica dell'identità da parte della piattaforma.",

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

  /** Perché «Mostra il nome» non si può scegliere. Dal WP9 il nome si mostra
   *  solo con l'identità verificata dalla piattaforma (la verifica del codice
   *  fiscale non basta più: resta solo un dato informativo). */
  motiviNominativo: {
    identita_non_verificata_admin:
      "Per mostrare il nome serve la verifica dell'identità da parte della piattaforma. Intanto puoi comparire in forma anonima.",
    non_disponibile:
      "Per ora le aziende compaiono solo in forma anonima: mostrare il nome sarà possibile più avanti.",
  } satisfies Record<MotivoNominativoPartner, string>,
  /** Link alla richiesta di verifica (riquadro «Verifica dell'identità»). */
  chiediVerifica: "Chiedi la verifica dell'identità",

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

/** Call di partenariato (WP5): etichette e testi condivisi tra wizard, pagina
 *  della call, liste e card del bando. La nota sull'anonimato e i testi della
 *  segnalazione sono testi da far rivedere al legale: non riformularli senza. */
export const CALL_COPY = {
  /** Call anonima (dal WP9 il nome si mostra solo con l'identità verificata
   *  dalla piattaforma, e si rivela solo tra aziende verificate). */
  notaAnonima:
    "La call è anonima: le altre aziende non vedono il nome della tua. Dopo che accetti un'azienda, i nomi si rivelano solo se tutte e due hanno l'identità verificata dalla piattaforma.",
  /** Call con il nome (solo aziende verificate, WP9). Il backend del WP9 non
   *  ha ancora la proiezione con il nome verso le altre aziende: la nota lo
   *  dice (da aggiornare quando arriva). */
  notaNominativa:
    "Hai scelto la call con il nome dell'azienda. Per ora le altre aziende la vedono ancora in forma anonima: il nome comparirà quando la piattaforma mostrerà le call con il nome.",
  sceltaNomeTitolo: "Come vuoi pubblicare la call?",
  sceltaAnonima: "Anonima",
  sceltaAnonimaNota: "Le altre aziende vedono regione, settore e dimensione, non il nome.",
  sceltaNome: "Con il nome dell'azienda",
  sceltaNomeNota:
    "Il nome registrato al Registro Imprese. Per ora la call compare comunque in forma anonima.",
  nomeNonDisponibile:
    "Per pubblicare la call con il nome dell'azienda serve la verifica dell'identità da parte della piattaforma. Intanto la call è anonima.",
  /** Call rimasta «con il nome» mentre la scelta è spenta (WP9). */
  nomeNonAncoraVisibile:
    "La call è impostata con il nome dell'azienda, ma per ora le call compaiono solo in forma anonima: puoi renderla anonima.",
  aziendaAnonima: "Azienda anonima",
  soloTitolare: "La call la gestisce il titolare dell'azienda: tu puoi solo consultarla.",

  /** I sette passi del wizard, nell'ordine. */
  passi: [
    "Bando",
    "Regole del bando",
    "Requisiti",
    "Posizioni",
    "Testi",
    "Anteprima",
    "Pubblicazione",
  ] as const,

  stati: {
    bozza: "Bozza",
    pubblicata: "Pubblicata",
    chiusa_completata: "Completata",
    chiusa_annullata: "Annullata",
    scaduta: "Scaduta",
    sospesa_moderazione: "Sospesa",
  } satisfies Record<StatoCall, string>,
  motiviChiusura: {
    scadenza_call: "È passata la scadenza della call.",
    bando_chiuso: "Il bando è chiuso.",
    bando_sospeso: "Il bando è stato sospeso.",
    bando_revocato: "Il bando è stato revocato.",
    bando_non_disponibile: "Il bando non è più disponibile nel catalogo.",
    azienda_non_disponibile: "L'azienda non è più attiva sulla piattaforma.",
    creatore_completata: "L'hai chiusa tu: partenariato completato.",
    creatore_annullata: "L'hai annullata tu.",
    moderazione: "È stata chiusa dalla moderazione.",
  } satisfies Record<MotivoChiusuraCall, string>,

  ruoliCreatore: {
    capofila: "Sono il capofila e cerco partner",
    cerco_capofila: "Cerco un capofila",
  } satisfies Record<RuoloCreatoreCall, string>,
  ruoliCreatoreBrevi: {
    capofila: "Capofila che cerca partner",
    cerco_capofila: "Cerca un capofila",
  } satisfies Record<RuoloCreatoreCall, string>,

  fasceBudget: {
    fino_50k: "Fino a 50.000 €",
    "50k_150k": "Da 50.000 a 150.000 €",
    "150k_300k": "Da 150.000 a 300.000 €",
    "300k_500k": "Da 300.000 a 500.000 €",
    "500k_1m": "Da 500.000 € a 1 milione",
    "1m_2m": "Da 1 a 2 milioni di €",
    "2m_5m": "Da 2 a 5 milioni di €",
    oltre_5m: "Oltre 5 milioni di €",
  } satisfies Record<BudgetFasciaCall, string>,

  visibilita: {
    pubblica: "Tutte le aziende",
    solo_invitati: "Solo le aziende che inviti",
  } satisfies Record<VisibilitaCall, string>,

  /** Copertura di un requisito da parte tua (mai mostrata agli altri). */
  esitiCopertura: {
    coperto: "Lo copri tu",
    non_coperto: "Non lo copri",
    dato_mancante: "Dato mancante",
    incerto: "Da verificare",
    non_valutabile: "Da valutare a mano",
  } satisfies Record<EsitoCoperturaCall, string>,
  origini: {
    ai_check: "Dall'AI-check",
    precheck: "Dai requisiti del bando",
    bando_partenariato: "Dalle regole di partenariato",
    regola_finanziaria: "Regola economica del bando",
    manuale: "Aggiunto da te",
  } satisfies Record<OrigineRequisitoCall, string>,
  ambiti: {
    consorzio: "Basta un partner",
    ogni_membro: "Vale per ogni partner",
  } satisfies Record<AmbitoRequisitoCall, string>,
  territorio: {
    qualsiasi: "Ovunque",
    sede_attuale: "Sede già nella regione",
    sede_entro_erogazione: "Sede nella regione entro l'erogazione",
  } satisfies Record<TerritorioModalitaCall, string>,
  dimensioni: {
    micro: "Micro",
    piccola: "Piccola",
    media: "Media",
    grande: "Grande",
  } satisfies Record<DimensioneImpresa, string>,
  certificazioni: {
    qualita: "Gestione della qualità",
    ambiente: "Gestione ambientale",
    sicurezza_lavoro: "Salute e sicurezza sul lavoro",
    sicurezza_informazioni: "Sicurezza delle informazioni",
    energia: "Gestione dell'energia",
    appalti_soa: "Attestazione SOA",
    settoriale: "Certificazione di settore",
    altro: "Altra certificazione",
  } satisfies Record<CategoriaCertificazione, string>,
  tipiCriterio: {
    tipo_soggetto: "Tipo di soggetto",
    tag: "Competenze",
    regione: "Sede in una regione",
    paese: "Paese",
    ateco: "Attività (ATECO)",
    settore: "Settore",
    dimensione: "Dimensione",
    certificazione: "Certificazioni",
    esperienza: "Esperienza in un programma",
    regola_finanziaria: "Regola economica",
    manuale: "Solo a parole",
  } satisfies Record<TipoCriterio, string>,

  /** Rilievi anti-contatti sui testi pubblici: tipo e campo in parole. */
  rilieviTipi: {
    email: "un indirizzo email",
    telefono: "un numero di telefono",
    url: "un indirizzo web",
    piva_cf: "una partita IVA o un codice fiscale",
    iban: "un IBAN",
    dominio_bloccato: "un sito non ammesso",
    ragione_sociale: "il nome dell'azienda",
    dominio_azienda: "il sito dell'azienda",
    persona: "il cognome di una persona dell'azienda",
  } as Record<string, string>,
  rilieviCampi: {
    titolo: "Titolo",
    descrizione_pubblica: "Descrizione",
    profilo_partner_ideale: "Partner ideale",
    dettagli_riservati: "Dettagli riservati",
    posizioni: "Posizioni",
    requisiti: "Requisiti",
  } as Record<string, string>,
  rilieviNota:
    "Prima dell'accettazione non si condividono contatti né dati che fanno riconoscere l'azienda: i contatti si scambiano in chat, dopo che accetti.",

  /** Limiti del piano (call pubblicate o sospese, su tutte le tue aziende). */
  pianoNonIncludeTitolo: "Il tuo piano non include la creazione di call",
  pianoNonIncludeTesto:
    "Con un piano superiore puoi pubblicare call di partenariato e trovare partner per i bandi.",
  limiteRaggiuntoTitolo: "Hai raggiunto il numero massimo di call attive del tuo piano",
  limiteRaggiuntoTesto:
    "Chiudi una call pubblicata oppure passa a un piano superiore per pubblicarne un'altra.",
  vediPiani: "Vedi i piani",
  limiteCall: (usate: number, limite: number | null) =>
    limite === null
      ? `Call attive: ${usate} (il tuo piano non ha limiti)`
      : `Call attive: ${usate} di ${limite}`,

  /** AI (posizioni e testi): a carico della piattaforma, mai salvata da sola. */
  aiNota: "La proposta dell'AI non si salva da sola: scegli tu cosa usare, poi salva.",
  aiInCorso: "Sto preparando la proposta: di solito basta meno di un minuto.",
  aiLunga: "La proposta sta impiegando più del previsto: riprova tra qualche minuto.",
  aiErrore: "Non siamo riusciti a preparare la proposta.",

  /** Segnalazione (DSA): conferma di ricezione. */
  segnalaTitolo: "Segnala questo contenuto",
  segnalaMotivi: {
    contenuto_illecito: "Contenuto illecito",
    dati_personali: "Dati personali di altre persone",
    spam_pubblicita: "Spam o pubblicità",
    contatti_nel_testo: "Contatti nel testo",
    discriminatorio: "Contenuto discriminatorio",
    impersonificazione: "Si spaccia per un'altra azienda",
    altro: "Altro",
  } satisfies Record<MotivoSegnalazione, string>,
  segnalaBuonaFede:
    "Dichiaro in buona fede che le informazioni della segnalazione sono esatte e complete.",
  segnalaRicevuta:
    "Abbiamo ricevuto la segnalazione: la esaminiamo e ti avvisiamo della decisione.",
} as const;

/** Scoperta delle call (WP6): «Per te», bacheca, salvate, suggeriti. Il
 *  confronto tra call e azienda è calcolato con regole fisse, senza AI: le
 *  frasi di spiegazione arrivano già pronte dal server. */
export const BACHECA_COPY = {
  viste: {
    "per-te": "Per te",
    tutte: "Tutte le call",
    mie: "Le mie call",
    salvate: "Salvate",
    candidature: "Candidature",
    conversazioni: "Conversazioni",
  } satisfies Record<VistaPartenariati, string>,
  ordini: {
    affinita: "Più adatte alla tua azienda",
    recenti: "Pubblicate di recente",
    scadenza: "Scadenza più vicina",
  } satisfies Record<OrdineBacheca, string>,
  ruoli: {
    capofila: "Capofila",
    partner: "Partner",
  } satisfies Record<RuoloPartner, string>,

  /** «Salva» vale anche come «segui»: avvisi su modifiche e chiusura. */
  salva: "Salva",
  salvata: "Salvata",
  salvaAiuto: "Salvandola ti avvisiamo se la call cambia o si chiude.",

  /** Visibilità come partner («Per te» funziona anche senza, Q25). */
  optInTitolo: "Le altre aziende non ti vedono ancora",
  optInTesto:
    "Attiva la visibilità come partner per comparire tra le aziende suggerite a chi pubblica una call e per ricevere un avviso quando ne esce una adatta a te.",
  optInCta: "Attiva la visibilità",
  optInMembro: "La visibilità come partner la attiva il titolare dell'azienda.",
  sospeso:
    "Il profilo partner è sospeso dalla piattaforma: per ora non compari tra le aziende suggerite. Per chiarimenti scrivi all'assistenza.",
  completaTitolo: "Completa il profilo partner",
  completaTesto: (n: number) =>
    `Il profilo è completo al ${n}%: più è completo, più è facile trovare le call giuste e farti scegliere.`,
  completaCta: "Completa il profilo partner",

  /** Il punteggio del confronto non arriva al client: ordina le liste e
   *  basta (userebbe dati della controparte che la sua scheda non mostra). */
  daVerificareNota:
    "Le voci da verificare non escludono: mancano dati per dire se il requisito è rispettato.",
} as const;

/** Candidature spontanee e inviti (WP7): stati, motivi e messaggi ripetuti
 *  tra la pagina dei partenariati, la pagina della call e i dialog. */
export const CANDIDATURE_COPY = {
  tipi: {
    candidatura: "Candidatura",
    invito: "Invito",
  } satisfies Record<TipoCandidatura, string>,
  stati: {
    inviata: "In attesa di risposta",
    accettata: "Accettata",
    rifiutata: "Rifiutata",
    ritirata: "Ritirata",
    scaduta: "Scaduta",
  } satisfies Record<StatoCandidatura, string>,
  /** Chiusure senza decisione, uguali per le due aziende. */
  motiviChiusura: {
    call_chiusa: "La call si è chiusa prima di una risposta.",
    ttl: "L'invito è scaduto senza risposta.",
    opt_out: "Si è chiusa perché l'azienda ha tolto la visibilità come partner.",
    moderazione: "È stata chiusa dalla moderazione.",
  } satisfies Record<MotivoChiusuraCandidatura, string>,
  soloTitolare: "Le azioni le gestisce il titolare dell'azienda.",
  nonPiuDisponibile: "Azienda non più disponibile",
  nonPiuDisponibileNota:
    "L'azienda non è più visibile come partner: i suoi dati non si mostrano più.",
  dichiarato: "dichiarato",

  /** Piano e limiti (candidature al mese, su tutte le aziende del titolare). */
  gratuitoTitolo: "Il tuo piano non include le candidature",
  gratuito:
    "Con il piano Gratuito puoi ricevere inviti, accettarli e chattare. Per candidarti di tua iniziativa scegli un piano a pagamento.",
  esauriteTitolo: "Hai usato tutte le candidature di questo mese",
  esaurite:
    "Si ricomincia da zero all'inizio del mese. Con un piano superiore puoi mandarne di più.",
  vediPiani: "Vedi i piani",
  quota: (usate: number, limite: number | null) => {
    const n = `${usate} ${usate === 1 ? "candidatura" : "candidature"}`;
    return limite === null
      ? `Questo mese hai usato ${n} (il tuo piano non ha limiti)`
      : `Questo mese hai usato ${n} su ${limite}`;
  },
  quotaNota: "Contano le candidature di tutte le tue aziende.",

  /** Per candidarsi serve la visibilità come partner (Q25). */
  profiloNonAttivoTitolo: "Per candidarti rendi visibile il profilo partner",
  profiloNonAttivo:
    "Chi ha creato la call vede il profilo partner della tua azienda, in forma anonima: attiva la visibilità e poi candidati.",
  profiloCta: "Vai al profilo partner",

  /** Prima dell'accettazione niente contatti (il controllo è del server). */
  notaContatti:
    "Non scrivere contatti né dati che fanno riconoscere l'azienda: li scambierete in chat, dopo l'accettazione.",
  notaDichiarati:
    "Segna solo i requisiti che hai davvero: chi ha creato la call li vede come «dichiarato».",
} as const;

/** Conversazioni (WP7). Il banner antitrust e quello sull'identità sono
 *  testi fissati dal piano: non riformularli senza il legale. */
export const CHAT_COPY = {
  antitrust:
    "Non scambiate informazioni su prezzi, offerte o strategie commerciali: la collaborazione riguarda solo il progetto del bando.",
  /** Dal WP9 la rivelazione è simmetrica: solo tra due aziende verificate. */
  identitaNonRivelata:
    "L'identità si rivela solo tra aziende verificate dalla piattaforma: finché una delle due non lo è, restate anonime. Presentatevi in chat e verificate i dati sul Registro Imprese prima di condividere informazioni riservate.",
  identitaVerificaCta: "Verifica l'identità della tua azienda",
  nuoviMessaggi: "Nuovi messaggi",
  oscurato: "Messaggio oscurato dalla moderazione.",
  tuaAzienda: "La tua azienda",
  vuota: "Ancora nessun messaggio: presentatevi e partite dal progetto del bando.",
  chiusa: "La conversazione è chiusa: puoi rileggerla, ma non si possono più mandare messaggi.",
  controparteNonAttiva:
    "L'altra azienda non è più attiva sulla piattaforma: la conversazione resta in sola lettura.",
  soloTitolare: "Scrive solo il titolare dell'azienda: tu puoi leggere la conversazione.",
  solaLettura: "La conversazione è in sola lettura.",
  scorciatoia: (mac: boolean) => `${mac ? "⌘" : "Ctrl"} + Invio per inviare`,
} as const;

/** Consorzio della call (WP8): ruoli, stati ed esiti ripetuti tra l'elenco
 *  dei membri, i dialog, la verifica delle regole e la matrice. Il disclaimer
 *  della verifica e la nota sul budget riservato sono testi fissati dal piano:
 *  non riformularli senza rivederli. */
export const CONSORZIO_COPY = {
  ruoli: {
    capofila: "Capofila",
    partner: "Partner",
    affiliated_entity: "Entità affiliata",
    associated_partner: "Partner associato",
  } satisfies Record<RuoloMembro, string>,
  /** Spiegazione breve di ogni ruolo, in parole semplici. */
  ruoliSpiegazione: {
    capofila: "Guida il progetto e tiene i rapporti con l'ente che finanzia il bando.",
    partner: "Svolge una parte del progetto e riceve una parte del contributo.",
    affiliated_entity:
      "Un soggetto legato a un partner (per esempio una sua controllata) che svolge parte del lavoro e riceve contributi. Non conta nel numero di partner.",
    associated_partner:
      "Partecipa al progetto senza ricevere contributi. Non conta nel numero di partner.",
  } satisfies Record<RuoloMembro, string>,
  stati: {
    proposto: "Da confermare",
    confermato: "Confermato",
    uscito: "Uscito",
  } satisfies Record<StatoMembro, string>,
  esiti: {
    verde: "In regola",
    rosso: "Da sistemare",
    grigio: "Da verificare",
  } satisfies Record<EsitoVoce, string>,
  esitoComplessivo: {
    verde: "Il consorzio rispetta tutte le regole controllate.",
    rosso: "Il consorzio non rispetta ancora alcune regole: guarda le voci da sistemare.",
    grigio: "Nessuna regola violata, ma alcune voci vanno verificate a mano.",
  } satisfies Record<EsitoVoce, string>,
  /** Copertura di un requisito da parte di un membro (nella matrice). */
  copertura: {
    coperto: "Coperto",
    non_coperto: "Non coperto",
    dato_mancante: "Dato mancante",
    incerto: "Da verificare",
    non_valutabile: "Da valutare a mano",
  } satisfies Record<EsitoCoperturaCall, string>,
  statiDocumento: {
    da_fare: "Da fare",
    in_corso: "In corso",
    fatto: "Fatto",
    non_applicabile: "Non serve",
  } satisfies Record<StatoDocumentoConsorzio, string>,
  fasiDocumento: {
    accordo_preliminare: "Prima della domanda, tra i partner",
    domanda: "Con la domanda",
    concessione: "Prima della concessione",
    prima_erogazione: "Entro la prima erogazione",
  } satisfies Record<FaseDocumentoConsorzio, string>,
  disclaimerValidatore:
    "Verifica automatica sulle regole estratte dal bando e confermate da chi ha creato la call: non sostituisce la lettura del testo ufficiale.",
  notaBudget: "Il budget esatto lo vedi solo tu e le aziende che accetti.",
  notaBudgetMembro: "Il budget esatto è riservato alle aziende accettate nella call.",
  dichiarato: "dichiarato",
  dichiaratoNota:
    "«Dichiarato» vuol dire che il dato non viene dal Registro Imprese: l'ha indicato l'azienda nel suo profilo o chi ha creato la call per un membro esterno.",
  soloTitolare: "Il consorzio lo gestisce il titolare dell'azienda: tu puoi solo consultarlo.",
  tuaAzienda: "La tua azienda",
  creatoreCall: "Chi ha creato la call",
  esterno: "Fuori dalla piattaforma",
  notaEsterni:
    "Per un membro esterno nome, paese e tipo di soggetto li dichiari tu: la piattaforma non li verifica e non ne controlla collegamenti né bilanci.",
  /** Call che la tua azienda non può consultare (sospesa per moderazione)
   *  ma di cui fa ancora parte del consorzio (WP9): si può solo uscire. */
  nonConsultabileTitolo: "Questa call al momento non è consultabile",
  nonConsultabileTesto:
    "La tua azienda fa ancora parte del suo consorzio. Se vuoi, puoi uscirne: da quel momento non sarai più impegnata su questo bando.",
  nonConsultabileSoloTitolare: "Per uscire dal consorzio serve il titolare dell'azienda.",
  esciDialogTitolo: "Uscire dal consorzio?",
  esciDialogTesto:
    "La tua azienda non farà più parte del consorzio di questa call. La conversazione resta; per rientrare servirà un nuovo accordo con chi ha creato la call.",
  uscitaFatta: "La tua azienda è uscita dal consorzio.",
} as const;

/** Verifica dell'identità dell'azienda da parte della piattaforma (WP9,
 *  decisione di Michele): cosa sblocca, stati e metodi in parole. */
export const IDENTITA_COPY = {
  titolo: "Verifica dell'identità",
  spiegazione:
    "Un amministratore della piattaforma controlla che tu rappresenti davvero l'azienda, per esempio con una telefonata alla sede o una PEC. Non costa nulla.",
  sblocca: [
    "mostrare il nome dell'azienda nel profilo partner;",
    "rivelare i nomi dopo un'accettazione, quando anche l'altra azienda è verificata.",
  ],
  sbloccaTitolo: "Cosa sblocca",
  stati: {
    non_richiesta: "Non verificata",
    richiesta: "Verifica in corso",
    verificata: "Identità verificata",
    rifiutata: "Verifica non riuscita",
  } satisfies Record<StatoIdentitaAzienda, string>,
  descrizioneStato: {
    non_richiesta: "L'identità dell'azienda non è ancora verificata.",
    richiesta:
      "Hai chiesto la verifica: un amministratore ti contatterà. Ti avvisiamo appena l'avrà controllata.",
    verificata:
      "L'identità dell'azienda è verificata. Se cambi ragione sociale o partita IVA, la verifica si revoca da sola.",
    rifiutata:
      "Non siamo riusciti a verificare l'identità dell'azienda. Puoi chiedere di nuovo la verifica.",
  } satisfies Record<StatoIdentitaAzienda, string>,
  metodi: {
    telefonata_sede: "Telefonata alla sede",
    documento_legale_rappresentante: "Documento del legale rappresentante",
    pec: "PEC dell'azienda",
    altro: "Altro",
  } satisfies Record<MetodoVerificaIdentita, string>,
  chiedi: "Chiedi la verifica",
  chiediDiNuovo: "Chiedi di nuovo la verifica",
  richiestaTitolo: "Chiedi la verifica dell'identità",
  notaEtichetta: "Come preferisci essere contattato? (facoltativo)",
  notaAiuto:
    "Per esempio: «di mattina, al numero della sede». Non servono dati personali: useremo i recapiti ufficiali dell'azienda.",
  notaMax: 500,
  inviata: "Richiesta inviata: ti avvisiamo appena l'avremo controllata.",
  servonoDati:
    "Per chiedere la verifica servono i dati ufficiali dell'azienda dal Registro Imprese.",
  registroNonCoerente:
    "I dati dell'azienda non corrispondono più al Registro Imprese: finché non li aggiorni, il nome non si può mostrare.",
  soloTitolare: "La verifica la chiede il titolare dell'azienda.",
} as const;

/** Moderazione dei partenariati (WP9, DSA art. 16-20): stati, decisioni,
 *  ricorso. I testi della motivazione e delle vie di ricorso sono SEGNAPOSTO:
 *  BOZZA — DA RIVEDERE CON IL LEGALE (`TESTI_LEGALI_WP9_VERSIONE`). */
export const MODERAZIONE_COPY = {
  stati: {
    ricevuta: "Ricevuta",
    in_esame: "In esame",
    decisa: "Decisa",
    ricorso_presentato: "Ricorso presentato",
    ricorso_deciso: "Ricorso deciso",
  } satisfies Record<StatoSegnalazione, string>,
  oggetti: {
    call: "Call di partenariato",
    profilo: "Profilo partner",
    messaggio: "Messaggio in chat",
  } satisfies Record<OggettoSegnalazione, string>,
  /** Decisione, come la legge l'admin. */
  decisioni: {
    nessuna_azione: "Nessuna azione",
    contenuto_rimosso: "Messaggio oscurato",
    call_sospesa: "Call sospesa",
    profilo_sospeso: "Profilo sospeso",
  } satisfies Record<DecisioneSegnalazione, string>,
  /** Decisione, come la legge chi ha segnalato. */
  decisioniSegnalante: {
    nessuna_azione:
      "Abbiamo esaminato il contenuto e non abbiamo trovato violazioni: resta visibile.",
    contenuto_rimosso: "Abbiamo oscurato il messaggio che hai segnalato.",
    call_sospesa: "Abbiamo sospeso la call che hai segnalato.",
    profilo_sospeso: "Abbiamo sospeso il profilo partner che hai segnalato.",
  } satisfies Record<DecisioneSegnalazione, string>,
  /** Decisione, come la legge l'azienda autrice (mai chi ha segnalato). */
  decisioniAutore: {
    nessuna_azione: "Abbiamo esaminato il contenuto e non abbiamo preso provvedimenti.",
    contenuto_rimosso: "Abbiamo oscurato un messaggio della tua azienda.",
    call_sospesa: "Abbiamo sospeso la call della tua azienda.",
    profilo_sospeso: "Abbiamo sospeso il profilo partner della tua azienda.",
  } satisfies Record<DecisioneSegnalazione, string>,
  esitiRicorso: {
    confermata: "Decisione confermata",
    riformata: "Decisione cambiata",
  } satisfies Record<EsitoRicorso, string>,
  esitiRicorsoSpiegazione: {
    confermata: "Abbiamo riesaminato il caso e la decisione resta la stessa.",
    riformata: "Abbiamo riesaminato il caso e cambiato la decisione.",
  } satisfies Record<EsitoRicorso, string>,
  inAttesa:
    "Stiamo esaminando la segnalazione: ti avvisiamo quando c'è una decisione.",
  ricorsoTitolo: "Presenta un ricorso",
  ricorsoSpiegazione:
    "Se non sei d'accordo con la decisione puoi chiederci di riesaminarla. Il ricorso si presenta una sola volta, entro 6 mesi dalla decisione, e lo esamina il nostro staff.",
  /** All'autore, quando la restrizione nasce dal ricorso accolto di chi
   *  aveva segnalato. */
  decisioneDalRicorso:
    "In un primo momento non avevamo preso provvedimenti: abbiamo cambiato la decisione dopo aver riesaminato la segnalazione.",
  ricorsoEtichetta: "Perché la decisione andrebbe cambiata",
  ricorsoMin: 20,
  ricorsoMax: 2000,
  ricorsoInviato: "Ricorso inviato: ti avvisiamo quando lo avremo esaminato.",
  ricorsoInAttesa: "Stiamo esaminando il ricorso: ti avvisiamo quando c'è una decisione.",
  /** Vie di ricorso esterne, generiche (DSA art. 20-21). */
  vieEsterne:
    "Oltre al ricorso interno puoi rivolgerti a un organismo di risoluzione extragiudiziale delle controversie certificato o all'autorità giudiziaria.",
  motivazioneMin: 20,
  motivazioneMax: 2000,
  /** Motivazione (statement of reasons) inviata all'azienda autrice. */
  sorTitolo: "Motivazione della decisione",
} as const;

/** Pannello admin dei partenariati (WP9): schede, metriche e costi. */
export const ADMIN_PARTENARIATI_COPY = {
  schede: {
    segnalazioni: "Segnalazioni",
    identita: "Identità",
    call: "Call",
    metriche: "Metriche",
    costi: "Costi",
    estrazioni: "Estrazioni",
  },
  providers: {
    anthropic: "Anthropic (AI)",
    openapi: "openapi.it",
  } as Record<string, string>,
  servizi: {
    partenariato_estrazione: "Estrazione delle regole dei bandi",
    partner_profilo_ai: "Bozza del profilo partner",
    partner_call_posizioni: "Posizioni proposte per le call",
    partner_call_testi: "Testi proposti per le call",
    partner_bozza: "Bozze dei documenti",
    "IT-advanced": "Bilanci (IT-advanced)",
    "bilancio-ottico": "Bilancio ufficiale",
    "bilancio-ottico-stato": "Stato del bilancio ufficiale",
    "visure-impresa": "Visura dell'impresa",
  } as Record<string, string>,
  esiti: {
    success: "Riuscite",
    error: "Errore",
    timeout_unknown: "Esito incerto",
  } as Record<string, string>,
  notaValute:
    "Gli importi sono nella valuta del fornitore: euro per openapi.it, dollari per Anthropic. Non si sommano tra loro.",
  notaMetriche:
    "Le metriche riguardano le call pubblicate nel periodo; candidature e inviti di quelle call contano in qualunque momento.",
  motivazioneMin: 20,
  motivazioneMax: 2000,
  /** Sospensione d'ufficio: la motivazione arriva per intero all'azienda
   *  nella notifica (e resta sul contenuto sospeso). */
  motivazioneSospensioneMax: 500,
  motivazioneSospensioneAiuto:
    "La legge l'azienda nella notifica, insieme a come contestare la decisione: niente dati di altre persone.",
  motivoRevocaMax: 500,
} as const;
