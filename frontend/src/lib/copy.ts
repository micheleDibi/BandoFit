/** Stringhe user-facing condivise o duplicate in più punti.
 *
 *  Il resto dell'app tiene le stringhe inline nei componenti (non c'è i18n):
 *  qui stanno SOLO quelle che comparivano — o comparirebbero — in più posti e
 *  che divergevano. Il claim sui bandi era ripetuto tre volte nella landing
 *  (fascia statistiche, hero, FAQ) e bastava aggiornarne due per renderla
 *  incoerente. Non aggiungere qui stringhe usate una volta sola. */

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
