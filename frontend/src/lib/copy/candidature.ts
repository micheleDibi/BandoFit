import type { MotivoChiusuraCandidatura, StatoCandidatura, TipoCandidatura } from "../../types";

/** Candidature spontanee e inviti (WP7): stati, motivi e messaggi ripetuti
 *  tra la pagina dei partenariati, la pagina della call e i dialog. */
export const CANDIDATURE_COPY = {
  tipi: {
    candidatura: "Candidatura",
    invito: "Invito",
  } satisfies Record<TipoCandidatura, string>,
  stati: {
    inviata: "In attesa di risposta",
    accettata: "Accettata",
    rifiutata: "Rifiutata",
    ritirata: "Ritirata",
    scaduta: "Scaduta",
  } satisfies Record<StatoCandidatura, string>,
  /** Chiusure senza decisione, uguali per le due aziende. */
  motiviChiusura: {
    call_chiusa: "La call si è chiusa prima di una risposta.",
    ttl: "L'invito è scaduto senza risposta.",
    opt_out: "Si è chiusa perché l'azienda ha tolto la visibilità come partner.",
    moderazione: "È stata chiusa dalla moderazione.",
  } satisfies Record<MotivoChiusuraCandidatura, string>,
  soloTitolare: "Le azioni le gestisce il titolare dell'azienda.",
  nonPiuDisponibile: "Azienda non più disponibile",
  nonPiuDisponibileNota:
    "L'azienda non è più visibile come partner: i suoi dati non si mostrano più.",
  dichiarato: "dichiarato",

  /** Piano e limiti (candidature al mese, su tutte le aziende del titolare). */
  gratuitoTitolo: "Il tuo piano non include le candidature",
  gratuito:
    "Con il piano Gratuito puoi ricevere inviti, accettarli e chattare. Per candidarti di tua iniziativa scegli un piano a pagamento.",
  esauriteTitolo: "Hai usato tutte le candidature di questo mese",
  esaurite:
    "Si ricomincia da zero all'inizio del mese. Con un piano superiore puoi mandarne di più.",
  vediPiani: "Vedi i piani",
  quota: (usate: number, limite: number | null) => {
    const n = `${usate} ${usate === 1 ? "candidatura" : "candidature"}`;
    return limite === null
      ? `Questo mese hai usato ${n} (il tuo piano non ha limiti)`
      : `Questo mese hai usato ${n} su ${limite}`;
  },
  quotaNota: "Contano le candidature di tutte le tue aziende.",

  /** Per candidarsi serve la visibilità come partner (Q25). */
  profiloNonAttivoTitolo: "Per candidarti rendi visibile il profilo partner",
  profiloNonAttivo:
    "Chi ha creato la call vede il profilo partner della tua azienda, in forma anonima: attiva la visibilità e poi candidati.",
  profiloCta: "Vai al profilo partner",

  /** Prima dell'accettazione niente contatti (il controllo è del server). */
  notaContatti:
    "Non scrivere contatti né dati che fanno riconoscere l'azienda: li scambierete in chat, dopo l'accettazione.",
  notaDichiarati:
    "Segna solo i requisiti che hai davvero: chi ha creato la call li vede come «dichiarato».",
} as const;
