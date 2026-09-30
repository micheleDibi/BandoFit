import type { MetodoVerificaIdentita, StatoIdentitaAzienda } from "../../types";

/** Verifica dell'identità dell'azienda da parte della piattaforma (WP9,
 *  decisione di Michele): cosa sblocca, stati e metodi in parole. */
export const IDENTITA_COPY = {
  titolo: "Verifica dell'identità",
  spiegazione:
    "Un amministratore della piattaforma controlla che tu rappresenti davvero l'azienda, per esempio con una telefonata alla sede o una PEC. Non costa nulla.",
  sblocca: [
    "mostrare il nome dell'azienda nel profilo partner;",
    "pubblicare call di partenariato con il nome dell'azienda;",
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
