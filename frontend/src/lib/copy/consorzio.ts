import type {
  EsitoCoperturaCall,
  EsitoVoce,
  FaseDocumentoConsorzio,
  RuoloMembro,
  StatoDocumentoConsorzio,
  StatoMembro,
} from "../../types";

/** Consorzio della call (WP8): ruoli, stati ed esiti ripetuti tra l'elenco
 *  dei membri, i dialog, la verifica delle regole e la matrice. Il disclaimer
 *  della verifica e la nota sul budget riservato sono testi fissati dal piano:
 *  non riformularli senza rivederli. */
export const CONSORZIO_COPY = {
  ruoli: {
    capofila: "Capofila",
    partner: "Partner",
    affiliated_entity: "Entità affiliata",
    associated_partner: "Partner associato",
  } satisfies Record<RuoloMembro, string>,
  /** Spiegazione breve di ogni ruolo, in parole semplici. */
  ruoliSpiegazione: {
    capofila: "Guida il progetto e tiene i rapporti con l'ente che finanzia il bando.",
    partner: "Svolge una parte del progetto e riceve una parte del contributo.",
    affiliated_entity:
      "Un soggetto legato a un partner (per esempio una sua controllata) che svolge parte del lavoro e riceve contributi. Non conta nel numero di partner.",
    associated_partner:
      "Partecipa al progetto senza ricevere contributi. Non conta nel numero di partner.",
  } satisfies Record<RuoloMembro, string>,
  stati: {
    proposto: "Da confermare",
    confermato: "Confermato",
    uscito: "Uscito",
  } satisfies Record<StatoMembro, string>,
  esiti: {
    verde: "In regola",
    rosso: "Da sistemare",
    grigio: "Da verificare",
  } satisfies Record<EsitoVoce, string>,
  esitoComplessivo: {
    verde: "Il consorzio rispetta tutte le regole controllate.",
    rosso: "Il consorzio non rispetta ancora alcune regole: guarda le voci da sistemare.",
    grigio: "Nessuna regola violata, ma alcune voci vanno verificate a mano.",
  } satisfies Record<EsitoVoce, string>,
  /** Copertura di un requisito da parte di un membro (nella matrice). */
  copertura: {
    coperto: "Coperto",
    non_coperto: "Non coperto",
    dato_mancante: "Dato mancante",
    incerto: "Da verificare",
    non_valutabile: "Da valutare a mano",
  } satisfies Record<EsitoCoperturaCall, string>,
  statiDocumento: {
    da_fare: "Da fare",
    in_corso: "In corso",
    fatto: "Fatto",
    non_applicabile: "Non serve",
  } satisfies Record<StatoDocumentoConsorzio, string>,
  fasiDocumento: {
    accordo_preliminare: "Prima della domanda, tra i partner",
    domanda: "Con la domanda",
    concessione: "Prima della concessione",
    prima_erogazione: "Entro la prima erogazione",
  } satisfies Record<FaseDocumentoConsorzio, string>,
  disclaimerValidatore:
    "Verifica automatica sulle regole estratte dal bando e confermate da chi ha creato la call: non sostituisce la lettura del testo ufficiale.",
  notaBudget: "Il budget esatto lo vedi solo tu e le aziende che accetti.",
  notaBudgetMembro: "Il budget esatto è riservato alle aziende accettate nella call.",
  dichiarato: "dichiarato",
  dichiaratoNota:
    "«Dichiarato» vuol dire che il dato non viene dal Registro Imprese: l'ha indicato l'azienda nel suo profilo o chi ha creato la call per un membro esterno.",
  soloTitolare: "Il consorzio lo gestisce il titolare dell'azienda: tu puoi solo consultarlo.",
  tuaAzienda: "La tua azienda",
  creatoreCall: "Chi ha creato la call",
  esterno: "Fuori dalla piattaforma",
  notaEsterni:
    "Per un membro esterno nome, paese e tipo di soggetto li dichiari tu: la piattaforma non li verifica e non ne controlla collegamenti né bilanci.",
  /** Call che la tua azienda non può consultare (sospesa per moderazione)
   *  ma di cui fa ancora parte del consorzio (WP9): si può solo uscire. */
  nonConsultabileTitolo: "Questa call al momento non è consultabile",
  nonConsultabileTesto:
    "La tua azienda fa ancora parte del suo consorzio. Se vuoi, puoi uscirne: da quel momento non sarai più impegnata su questo bando.",
  nonConsultabileSoloTitolare: "Per uscire dal consorzio serve il titolare dell'azienda.",
  esciDialogTitolo: "Uscire dal consorzio?",
  esciDialogTesto:
    "La tua azienda non farà più parte del consorzio di questa call. La conversazione resta; per rientrare servirà un nuovo accordo con chi ha creato la call.",
  uscitaFatta: "La tua azienda è uscita dal consorzio.",
} as const;
