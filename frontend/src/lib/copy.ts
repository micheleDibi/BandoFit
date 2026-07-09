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
