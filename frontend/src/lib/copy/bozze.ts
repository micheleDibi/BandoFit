/** Bozze AI dei documenti del partenariato (WP10): lettera d'intenti, NDA e
 *  term sheet per chi partecipa alla call. Il disclaimer è fisso (non lo
 *  scrive il modello) ed è lo stesso testo del PDF generato dal server. */
export const BOZZE_COPY = {
  titolo: "Bozze dei documenti",
  intro:
    "Prepara una prima bozza di lettera d'intenti, accordo di riservatezza (NDA) o term sheet con i ruoli e le quote del consorzio. Le aziende compaiono con segnaposto come «[Capofila]» o «[Partner 1]»: i nomi li scrivi tu nel documento finale.",
  disclaimer:
    "Bozza generata automaticamente: non costituisce consulenza legale. Falla rivedere a un professionista prima di firmarla.",
  tipi: {
    lettera_intenti: "Lettera d'intenti",
    nda: "Accordo di riservatezza (NDA)",
    term_sheet: "Term sheet",
  } satisfies Record<import("../../types").TipoBozzaDocumento, string>,
  tipiNota: {
    lettera_intenti: "L'impegno delle aziende a partecipare insieme al bando.",
    nda: "Per scambiarvi informazioni riservate sul progetto.",
    term_sheet: "Ruoli, quote e impegni che le aziende concordano.",
  } satisfies Record<import("../../types").TipoBozzaDocumento, string>,
  sceltaTipo: "Quale documento vuoi preparare?",
  includiNome: "Includi il nome della mia azienda",
  includiNomeNota:
    "Se non lo scegli, anche la tua azienda compare con un segnaposto. I nomi delle altre aziende non entrano mai nella bozza.",
  avvia: "Prepara la bozza",
  stati: {
    pending: "In preparazione",
    ready: "Pronta",
    error: "Non riuscita",
  } satisfies Record<import("../../types").StatoBozzaDocumento, string>,
  inCorso: "Sto preparando la bozza: di solito basta meno di un minuto.",
  lunga: "La bozza sta impiegando più del previsto: aggiorna lo stato tra qualche minuto.",
  pronta: "La bozza è pronta: la trovi qui sotto.",
  /** Ripiego se il server non manda il messaggio della bozza non riuscita. */
  errore: "Non siamo riusciti a preparare la bozza.",
  scaricaPdf: "Scarica PDF",
  mostra: "Leggi la bozza",
  nascondi: "Chiudi la bozza",
  daCompletare: "Da completare prima di usarla",
  /** `avvisi` del server: ciò che il controllo automatico ha tolto o
   *  sostituito (contatti, nomi, segnaposto non previsti). */
  avvisiTitolo: "Cosa abbiamo tolto dal testo",
  conNome: "Con il nome della tua azienda",
  notaSegnaposto:
    "Le parti tra parentesi quadre, come «[Partner 1]», vanno sostituite con i dati veri.",
  /** Limite del piano: bozze al mese su tutte le aziende del titolare. */
  usate: (usate: number, limite: number | null) => {
    const n = `${usate} ${usate === 1 ? "bozza" : "bozze"}`;
    return limite === null
      ? `Questo mese hai usato ${n} (il tuo piano non ha limiti)`
      : `Questo mese hai usato ${n} su ${limite}`;
  },
  usateNota:
    "Contano le bozze di tutte le tue aziende; una bozza non riuscita può contare lo stesso.",
  nonInclusoTitolo: "Il tuo piano non include le bozze dei documenti",
  nonIncluso:
    "Con un piano superiore puoi preparare ogni mese bozze di lettera d'intenti, NDA e term sheet per i tuoi partenariati.",
  esauriteTitolo: "Hai usato tutte le bozze di questo mese",
  esaurite:
    "Si ricomincia da zero all'inizio del mese. Con un piano superiore puoi prepararne di più.",
  vediPiani: "Vedi i piani",
  soloTitolare: "Le bozze le prepara il titolare dell'azienda: tu puoi leggerle e scaricarle.",
  nessunaTitolo: "Nessuna bozza per ora",
  nessuna: "Le bozze che prepari per questa call compaiono qui, pronte da leggere e scaricare.",
  elencoTitolo: "Le bozze della tua azienda",
} as const;
