/** Stati del flusso consulenze: compaiono nei badge di liste e dettagli, sia
 *  lato cliente sia lato progettista. */
export const CONSULENZA_STATO_LABELS: Record<
  import("../../types").ConsulenzaStato,
  string
> = {
  nuova: "In attesa di proposte",
  assegnata: "Assegnata",
  annullata: "Annullata",
};

/** Consulto chiesto dalla call di partenariato (WP9) nell'area Consulenze
 *  del cliente: badge in elenco e nel dettaglio, link alla call. */
export const CONSULTO_CALL_COPY = {
  badge: "Dalla call di partenariato",
  vaiAllaCall: "Vai alla call",
} as const;

export const PROPOSTA_STATO_LABELS: Record<import("../../types").PropostaStato, string> = {
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
