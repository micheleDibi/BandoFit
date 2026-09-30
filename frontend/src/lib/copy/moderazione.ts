import type {
  DecisioneSegnalazione,
  EsitoRicorso,
  OggettoSegnalazione,
  StatoSegnalazione,
} from "../../types";

/** Moderazione dei partenariati (WP9, DSA art. 16-20): stati, decisioni,
 *  ricorso. I testi della motivazione e delle vie di ricorso sono SEGNAPOSTO:
 *  BOZZA — DA RIVEDERE CON IL LEGALE (`TESTI_LEGALI_WP9_VERSIONE`). */
export const MODERAZIONE_COPY = {
  stati: {
    ricevuta: "Ricevuta",
    in_esame: "In esame",
    decisa: "Decisa",
    ricorso_presentato: "Ricorso presentato",
    ricorso_deciso: "Ricorso deciso",
  } satisfies Record<StatoSegnalazione, string>,
  oggetti: {
    call: "Call di partenariato",
    profilo: "Profilo partner",
    messaggio: "Messaggio in chat",
  } satisfies Record<OggettoSegnalazione, string>,
  /** Decisione, come la legge l'admin. */
  decisioni: {
    nessuna_azione: "Nessuna azione",
    contenuto_rimosso: "Messaggio oscurato",
    call_sospesa: "Call sospesa",
    profilo_sospeso: "Profilo sospeso",
  } satisfies Record<DecisioneSegnalazione, string>,
  /** Decisione, come la legge chi ha segnalato. */
  decisioniSegnalante: {
    nessuna_azione:
      "Abbiamo esaminato il contenuto e non abbiamo trovato violazioni: resta visibile.",
    contenuto_rimosso: "Abbiamo oscurato il messaggio che hai segnalato.",
    call_sospesa: "Abbiamo sospeso la call che hai segnalato.",
    profilo_sospeso: "Abbiamo sospeso il profilo partner che hai segnalato.",
  } satisfies Record<DecisioneSegnalazione, string>,
  /** Decisione, come la legge l'azienda autrice (mai chi ha segnalato). */
  decisioniAutore: {
    nessuna_azione: "Abbiamo esaminato il contenuto e non abbiamo preso provvedimenti.",
    contenuto_rimosso: "Abbiamo oscurato un messaggio della tua azienda.",
    call_sospesa: "Abbiamo sospeso la call della tua azienda.",
    profilo_sospeso: "Abbiamo sospeso il profilo partner della tua azienda.",
  } satisfies Record<DecisioneSegnalazione, string>,
  esitiRicorso: {
    confermata: "Decisione confermata",
    riformata: "Decisione cambiata",
  } satisfies Record<EsitoRicorso, string>,
  esitiRicorsoSpiegazione: {
    confermata: "Abbiamo riesaminato il caso e la decisione resta la stessa.",
    riformata: "Abbiamo riesaminato il caso e cambiato la decisione.",
  } satisfies Record<EsitoRicorso, string>,
  inAttesa:
    "Stiamo esaminando la segnalazione: ti avvisiamo quando c'è una decisione.",
  ricorsoTitolo: "Presenta un ricorso",
  ricorsoSpiegazione:
    "Se non sei d'accordo con la decisione puoi chiederci di riesaminarla. Il ricorso si presenta una sola volta, entro 6 mesi dalla decisione, e lo esamina il nostro staff.",
  /** All'autore, quando la restrizione nasce dal ricorso accolto di chi
   *  aveva segnalato. */
  decisioneDalRicorso:
    "In un primo momento non avevamo preso provvedimenti: abbiamo cambiato la decisione dopo aver riesaminato la segnalazione.",
  ricorsoEtichetta: "Perché la decisione andrebbe cambiata",
  ricorsoMin: 20,
  ricorsoMax: 2000,
  ricorsoInviato: "Ricorso inviato: ti avvisiamo quando lo avremo esaminato.",
  ricorsoInAttesa: "Stiamo esaminando il ricorso: ti avvisiamo quando c'è una decisione.",
  /** Vie di ricorso esterne, generiche (DSA art. 20-21). */
  vieEsterne:
    "Oltre al ricorso interno puoi rivolgerti a un organismo di risoluzione extragiudiziale delle controversie certificato o all'autorità giudiziaria.",
  motivazioneMin: 20,
  motivazioneMax: 2000,
  /** Motivazione (statement of reasons) inviata all'azienda autrice. */
  sorTitolo: "Motivazione della decisione",
} as const;
