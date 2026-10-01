import type {
  FiltroPartenariato,
  ModalitaPartenariato,
  MotivoIdentitaPartner,
  MotivoNominativoPartner,
} from "../../types";

/** Regole di partenariato del bando (WP3): card e sezione di BandoDetail,
 *  filtro e chip della lista bandi. */
export const PARTENARIATO_COPY = {
  /** Avvertenza fissa sotto le regole estratte: non riformulare senza il
   *  legale (docs/partenariati.md §6, testi legali in copy.ts). */
  disclaimer:
    "Estratto automaticamente dai documenti ufficiali: verifica sempre sul testo del bando prima di decidere.",
  /** Badge della modalità: sempre in parole, il colore non basta da solo. */
  modalita: {
    obbligatorio: "Partenariato obbligatorio",
    ammesso: "Partenariato ammesso",
    non_ammesso: "Solo partecipazione singola",
    non_determinabile: "Modalità non chiara",
  } satisfies Record<ModalitaPartenariato, string>,
  modalitaSpiegazione: {
    obbligatorio:
      "Si partecipa solo insieme ad altri soggetti (raggruppamento, rete, consorzio o simili).",
    ammesso: "Puoi partecipare da solo oppure insieme ad altri soggetti.",
    non_ammesso: "Il bando non ammette partenariati: si partecipa da soli.",
    non_determinabile: "Il testo disponibile non basta per stabilirlo: controlla il bando.",
  } satisfies Record<ModalitaPartenariato, string>,
  /** Filtro della lista bandi e relativo chip. */
  filtroTitolo: "Partenariato",
  filtroTutti: "Tutti",
  filtro: {
    ammesso: "Ammette partenariato",
    obbligatorio: "Richiede partenariato",
  } satisfies Record<FiltroPartenariato, string>,
  filtroNota: "Solo tra i bandi già analizzati: l'elenco cresce man mano.",
} as const;

/** Profilo partner e consensi (WP4): sezione della pagina Azienda, dialog di
 *  consenso (anche nel passo finale dell'import) e referente. Le frasi del
 *  consenso e della revoca sono testi legali: non riformularle senza rivedere
 *  l'informativa (il testo dell'informativa arriva dal server, versionato). */
export const PARTNER_COPY = {
  titoloSezione: "Profilo partner",
  descrizioneSezione:
    "Fatti trovare dalle altre aziende che cercano con chi partecipare a un bando. Decidi tu cosa mostrare, e se mostrare il nome.",
  statoVisibile: "Visibile come partner",
  statoNonVisibile: "Non visibile",
  statoSospeso: "Sospeso",
  conNome: "Con il nome dell'azienda",
  anonima: "Anonima",
  soloTitolare: "Il profilo partner lo gestisce il titolare dell'azienda.",
  nessunProfilo:
    "Il titolare non ha ancora compilato il profilo partner di questa azienda.",
  sospeso:
    "Il profilo è sospeso dalla piattaforma: per ora non compare tra i partner suggeriti. Per chiarimenti scrivi all'assistenza.",
  /** Profilo salvato con il nome ma senza la verifica dell'identità di oggi
   *  (revocata o dati del registro cambiati): verso le altre aziende è anonimo. */
  nomeSalvatoNonMostrato:
    "Hai scelto di mostrare il nome, ma oggi le altre aziende non lo vedono: serve la verifica dell'identità da parte della piattaforma.",

  /** Dialog di consenso: la checkbox NON è mai preselezionata. */
  consensoTitolo: "Comparire come partner",
  consensoIntro:
    "Leggi l'informativa: spiega cosa vedono le altre aziende e cosa succede quando accetti un contatto.",
  consensoInformativa: "Informativa sulla visibilità come partner",
  consensoCheckbox: "Acconsento a comparire come partner suggerito alle altre aziende",
  sceltaNomeTitolo: "Come vuoi comparire?",
  sceltaNome: "Mostra il nome dell'azienda",
  sceltaNomeNota: "Le altre aziende vedono il nome registrato al Registro Imprese.",
  sceltaAnonima: "Resta anonima",
  sceltaAnonimaNota:
    "Niente nome: le altre aziende vedono meno dettagli (solo la fascia di fatturato, esperienze senza anno né ruolo, certificazioni per categoria).",
  attiva: "Attiva",
  nonOra: "Non ora",
  /** Passo facoltativo alla fine dell'import (solo se si può attivare). */
  passoImport:
    "Ultimo passo, facoltativo: vuoi comparire come partner per le altre aziende che cercano con chi partecipare a un bando? Premi «Continua» per leggere come funziona.",
  continua: "Continua",
  annulla: "Annulla",
  attivato: "Fatto: ora compari tra i partner suggeriti. Puoi revocare quando vuoi.",
  informativaNonCaricata: "Impossibile caricare l'informativa.",
  informativaAggiornata:
    "L'informativa è stata aggiornata nel frattempo: leggi il nuovo testo e conferma di nuovo.",

  /** Perché «Mostra il nome» non si può scegliere. Dal WP9 il nome si mostra
   *  solo con l'identità verificata dalla piattaforma (la verifica del codice
   *  fiscale non basta più: resta solo un dato informativo). */
  motiviNominativo: {
    identita_non_verificata_admin:
      "Per mostrare il nome serve la verifica dell'identità da parte della piattaforma. Intanto puoi comparire in forma anonima.",
    non_disponibile:
      "Per ora le aziende compaiono solo in forma anonima: mostrare il nome sarà possibile più avanti.",
  } satisfies Record<MotivoNominativoPartner, string>,
  /** Link alla richiesta di verifica (riquadro «Verifica dell'identità»). */
  chiediVerifica: "Chiedi la verifica dell'identità",

  /** Perché il consenso non si può ancora dare. */
  motiviIdentita: {
    dati_non_importati:
      "Per comparire come partner importa prima i dati ufficiali dell'azienda dalla partita IVA.",
    piva_diversa:
      "La partita IVA dei dati aziendali è diversa da quella dei dati importati: aggiorna i dati dal Registro Imprese.",
    impresa_non_attiva:
      "Nel Registro Imprese l'azienda non risulta attiva: per comparire come partner deve esserlo.",
    dati_sandbox:
      "I dati importati sono di prova: per comparire come partner servono quelli reali del Registro Imprese.",
  } satisfies Record<MotivoIdentitaPartner, string>,
  identitaTitolo: "Servono i dati ufficiali dell'azienda",
  importa: "Importa da P.IVA",

  riconsensoTitolo: "L'informativa è cambiata",
  riconsensoTesto:
    "Il tuo consenso resta valido, ma ti chiediamo di rileggere la nuova informativa e confermarlo.",
  riconsensoCta: "Rileggi e conferma",

  /** Revoca: la frase del dialog è fissata dal piano (docs/partenariati.md §6). */
  revoca: "Revoca la visibilità",
  revocaTitolo: "Revocare la visibilità?",
  revocaTesto:
    "La revoca è immediata: non comparirai più nei suggerimenti. Le conversazioni già avviate restano.",
  revocaConferma: "Revoca",
  revocata: "Visibilità revocata: non compari più tra i partner suggeriti.",
  /** Il trigger del database revoca da solo la visibilità se cambiano questi dati. */
  revocaSuCambioAzienda:
    "Se cambi la ragione sociale o la partita IVA dell'azienda, la visibilità si revoca da sola: dovrai riattivarla.",
  attivaVisibilita: "Attiva la visibilità",

  anonimatoTitolo: "Nome dell'azienda",
  anonimatoCambiaInNome: "Mostra il nome",
  anonimatoCambiaInAnonima: "Rendi anonima",
  anonimatoOraConNome: "Ora le altre aziende vedono il nome dell'azienda.",
  anonimatoOraAnonima: "Ora l'azienda compare in forma anonima.",

  completezza: (n: number) => `Profilo completo al ${n}%`,
  completezzaNota: "Più il profilo è completo, più è facile che le altre aziende ti scelgano.",

  /** Bozza AI (generata a carico della piattaforma, mai pubblicata da sola). */
  bozzaCta: "Scrivi una bozza con l'AI",
  bozzaTitolo: "Bozza del profilo con l'AI",
  bozzaIntro:
    "Partiamo dai dati del Registro Imprese (attività e settore) e da quello che hai già scritto: l'AI propone una descrizione e alcune competenze. Non viene pubblicato nulla: scegli tu cosa usare, poi salvi il profilo.",
  bozzaAvvia: "Genera la bozza",
  bozzaRigenera: "Genera una nuova bozza",
  bozzaInCorso: "Sto preparando la bozza: di solito basta meno di un minuto.",
  bozzaLunga: "La bozza sta impiegando più del previsto.",
  bozzaPronta: "La bozza è pronta: scegli cosa usare.",
  bozzaProntaBreve: "La bozza dell'AI è pronta.",
  bozzaRivedi: "Rivedi la bozza",
  bozzaErrore: "Non siamo riusciti a preparare la bozza.",
  bozzaApplica: "Applica al profilo",
  bozzaApplicata:
    "Abbiamo riportato le parti scelte nel profilo qui sotto: controllale e salva.",
  bozzaScarta: "Scarta la bozza",
  bozzaUsaDescrizione: "Usa questa descrizione",
  bozzaCompetenzeTitolo: "Competenze proposte",
  bozzaNienteScelto: "Scegli almeno una parte da usare.",

  /** Referente per i partenariati. */
  referenteTitolo: "Referente per i partenariati",
  referenteDescrizione:
    "La persona che le altre aziende vedono (nome e ruolo) quando accetti un contatto, se entrambe le aziende hanno l'identità verificata dalla piattaforma. L'email personale non viene mai mostrata.",
  referenteTu: "Tu (titolare)",
  referenteAttuale: (nome: string) => `Referente attuale: ${nome}`,
  referenteInAttesa: (nome: string) =>
    `Proposta inviata a ${nome}: diventa referente solo quando accetta.`,
  referenteProponi: "Proponi come referente",
  referenteAnnullaProposta: "Annulla la proposta",
  referentePropostaAnnullata: (nome: string | null) =>
    nome
      ? `Proposta annullata: il referente resta ${nome}.`
      : "Proposta annullata: il referente resti tu.",
  referenteTorna: "Torna referente tu",
  referenteNessunMembro:
    "Per proporre un'altra persona invitala prima tra gli account collegati, con accesso a questa azienda.",
  referenteSceltaLabel: "Persona da proporre",
  referenteProposto: "Ti hanno proposto come referente per i partenariati",
  referenteInformativa: "Informativa per il referente",
  referenteCheckbox: "Ho letto l'informativa per il referente",
  referenteAccetta: "Accetta",
  referenteRifiuta: "Rifiuta",
  referenteSeiTu: "Sei il referente per i partenariati di questa azienda.",
  referenteRinuncia: "Rinuncia",
  referenteRinunciaTitolo: "Rinunciare al ruolo di referente?",
  referenteRinunciaTesto:
    "Da subito il referente torna il titolare dell'azienda. Potrà proporti di nuovo in futuro.",

  /** Anteprima «come ti vedono». */
  anteprimaTitolo: "Come ti vedono le altre aziende",
  anteprimaNota: "Anteprima del profilo salvato: le modifiche compaiono dopo il salvataggio.",
  anteprimaNonVisibile:
    "Oggi il profilo non è visibile: le altre aziende lo vedranno così quando attivi la visibilità.",
  aziendaAnonima: "Azienda anonima",

  /** Barra di salvataggio del profilo. */
  modificheNonSalvate: "Hai modifiche non salvate al profilo partner",
  salva: "Salva il profilo",
  salvato: "Profilo salvato",
} as const;
