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
  /** Al posto di `introForm` a storico spento: arriva solo l'ultimo esercizio. */
  introFormSenzaStorico:
    "Recuperiamo i dati ufficiali della tua azienda dal Registro Imprese tramite openapi.it: anagrafica, ATECO, sede e unità locali, cariche, dipendenti, ultimo bilancio depositato e altro.",
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
