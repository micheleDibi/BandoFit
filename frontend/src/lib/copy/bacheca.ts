import type { OrdineBacheca, RuoloPartner, VistaPartenariati } from "../../types";

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
