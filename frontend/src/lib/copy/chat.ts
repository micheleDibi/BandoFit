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
