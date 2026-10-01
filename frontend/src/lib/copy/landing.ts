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

/** Pannello laterale delle pagine di accesso (tavola «Accesso»): la promessa e
 *  due righe d'ESEMPIO del registro dei bandi, non dati del catalogo. La
 *  scadenza è a `giorni` da oggi, così l'esempio non scade mai (per questo i
 *  titoli non portano l'anno). */
export const ACCESSO_COPY = {
  promessa: ["Fa per me?", "Quanto vale?", "Entro quando?"],
  esempiEtichetta: "Esempio di bandi come li vedi su BandoFit",
  esempi: [
    {
      titolo: "Contributi Piemonte per Società di Mutuo Soccorso storiche",
      giorni: 31,
      importo: 200_000,
      nota: "dotazione",
      soddisfatti: 2,
      totale: 4,
    },
    {
      titolo: "Contributi a fondo perduto per investimenti irrigui in Piemonte",
      giorni: 61,
      importo: 571_686,
      nota: "dotazione",
      soddisfatti: 4,
      totale: 4,
    },
  ],
} as const;
