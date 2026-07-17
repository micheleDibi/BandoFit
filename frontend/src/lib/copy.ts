/** Stringhe user-facing condivise o duplicate in più punti.
 *
 *  Il resto dell'app tiene le stringhe inline nei componenti (non c'è i18n):
 *  qui stanno SOLO quelle che comparivano — o comparirebbero — in più posti e
 *  che divergevano. Il claim sui bandi era ripetuto tre volte nella landing
 *  (fascia statistiche, hero, FAQ) e bastava aggiornarne due per renderla
 *  incoerente. Non aggiungere qui stringhe usate una volta sola. */

import type { UserRole } from "../types";

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
    "Recuperiamo i dati ufficiali della tua azienda dal Registro Imprese tramite openapi.it: anagrafica, ATECO, sede e unità locali, cariche, dipendenti e altro.",
  notaCosto:
    "L'operazione utilizza il credito del servizio dati (circa 0,30 € + IVA per importazione).",
  attesa:
    "Recupero dei dati ufficiali dal Registro Imprese in corso. L'operazione può richiedere fino a un paio di minuti: non chiudere questa finestra.",

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
} as const;
