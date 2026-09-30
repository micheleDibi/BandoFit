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
