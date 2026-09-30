import type {
  AmbitoRequisitoCall,
  BudgetFasciaCall,
  CategoriaCertificazione,
  DimensioneImpresa,
  EsitoCoperturaCall,
  MotivoChiusuraCall,
  MotivoSegnalazione,
  OrigineRequisitoCall,
  RuoloCreatoreCall,
  StatoCall,
  TerritorioModalitaCall,
  TipoCriterio,
  VisibilitaCall,
} from "../../types";

/** Call di partenariato (WP5): etichette e testi condivisi tra wizard, pagina
 *  della call, liste e card del bando. La nota sull'anonimato e i testi della
 *  segnalazione sono testi da far rivedere al legale: non riformularli senza. */
export const CALL_COPY = {
  /** Call anonima (dal WP9 il nome si mostra solo con l'identità verificata
   *  dalla piattaforma, e si rivela solo tra aziende verificate). */
  notaAnonima:
    "La call è anonima: le altre aziende non vedono il nome della tua. Dopo che accetti un'azienda, i nomi si rivelano solo se tutte e due hanno l'identità verificata dalla piattaforma.",
  /** Call con il nome (solo aziende verificate, WP9): il nome del Registro
   *  Imprese, finché l'identità resta verificata (lo decide il server). */
  notaNominativa:
    "La call mostra il nome dell'azienda registrato al Registro Imprese, finché l'identità resta verificata dalla piattaforma: se la verifica viene revocata, le altre aziende la vedono di nuovo anonima. Partita IVA, sito e contatti restano riservati; dopo che accetti un'azienda si rivelano solo se tutte e due avete l'identità verificata.",
  sceltaNomeTitolo: "Come vuoi pubblicare la call?",
  sceltaAnonima: "Anonima",
  sceltaAnonimaNota: "Le altre aziende vedono regione, settore e dimensione, non il nome.",
  sceltaNome: "Con il nome dell'azienda",
  sceltaNomeNota:
    "Le altre aziende vedono anche il nome registrato al Registro Imprese. Solo il nome: partita IVA, sito e contatti restano riservati.",
  nomeNonDisponibile:
    "Per pubblicare la call con il nome dell'azienda serve la verifica dell'identità da parte della piattaforma. Intanto la call è anonima.",
  /** Call rimasta «con il nome» senza la verifica di oggi (per esempio dopo
   *  una revoca): le altre aziende la vedono anonima e non si pubblica così. */
  nomeSenzaVerifica:
    "La call è impostata con il nome dell'azienda, ma l'identità non risulta verificata: le altre aziende non vedono il nome e per pubblicarla devi renderla anonima.",
  aziendaAnonima: "Azienda anonima",
  soloTitolare: "La call la gestisce il titolare dell'azienda: tu puoi solo consultarla.",

  /** I sette passi del wizard, nell'ordine. */
  passi: [
    "Bando",
    "Regole del bando",
    "Requisiti",
    "Posizioni",
    "Testi",
    "Anteprima",
    "Pubblicazione",
  ] as const,

  stati: {
    bozza: "Bozza",
    pubblicata: "Pubblicata",
    chiusa_completata: "Completata",
    chiusa_annullata: "Annullata",
    scaduta: "Scaduta",
    sospesa_moderazione: "Sospesa",
  } satisfies Record<StatoCall, string>,
  motiviChiusura: {
    scadenza_call: "È passata la scadenza della call.",
    bando_chiuso: "Il bando è chiuso.",
    bando_sospeso: "Il bando è stato sospeso.",
    bando_revocato: "Il bando è stato revocato.",
    bando_non_disponibile: "Il bando non è più disponibile nel catalogo.",
    azienda_non_disponibile: "L'azienda non è più attiva sulla piattaforma.",
    creatore_completata: "L'hai chiusa tu: partenariato completato.",
    creatore_annullata: "L'hai annullata tu.",
    moderazione: "È stata chiusa dalla moderazione.",
  } satisfies Record<MotivoChiusuraCall, string>,

  ruoliCreatore: {
    capofila: "Sono il capofila e cerco partner",
    cerco_capofila: "Cerco un capofila",
  } satisfies Record<RuoloCreatoreCall, string>,
  ruoliCreatoreBrevi: {
    capofila: "Capofila che cerca partner",
    cerco_capofila: "Cerca un capofila",
  } satisfies Record<RuoloCreatoreCall, string>,

  fasceBudget: {
    fino_50k: "Fino a 50.000 €",
    "50k_150k": "Da 50.000 a 150.000 €",
    "150k_300k": "Da 150.000 a 300.000 €",
    "300k_500k": "Da 300.000 a 500.000 €",
    "500k_1m": "Da 500.000 € a 1 milione",
    "1m_2m": "Da 1 a 2 milioni di €",
    "2m_5m": "Da 2 a 5 milioni di €",
    oltre_5m: "Oltre 5 milioni di €",
  } satisfies Record<BudgetFasciaCall, string>,

  visibilita: {
    pubblica: "Tutte le aziende",
    solo_invitati: "Solo le aziende che inviti",
  } satisfies Record<VisibilitaCall, string>,

  /** Copertura di un requisito da parte tua (mai mostrata agli altri). */
  esitiCopertura: {
    coperto: "Lo copri tu",
    non_coperto: "Non lo copri",
    dato_mancante: "Dato mancante",
    incerto: "Da verificare",
    non_valutabile: "Da valutare a mano",
  } satisfies Record<EsitoCoperturaCall, string>,
  origini: {
    ai_check: "Dall'AI-check",
    precheck: "Dai requisiti del bando",
    bando_partenariato: "Dalle regole di partenariato",
    regola_finanziaria: "Regola economica del bando",
    manuale: "Aggiunto da te",
  } satisfies Record<OrigineRequisitoCall, string>,
  ambiti: {
    consorzio: "Basta un partner",
    ogni_membro: "Vale per ogni partner",
  } satisfies Record<AmbitoRequisitoCall, string>,
  territorio: {
    qualsiasi: "Ovunque",
    sede_attuale: "Sede già nella regione",
    sede_entro_erogazione: "Sede nella regione entro l'erogazione",
  } satisfies Record<TerritorioModalitaCall, string>,
  dimensioni: {
    micro: "Micro",
    piccola: "Piccola",
    media: "Media",
    grande: "Grande",
  } satisfies Record<DimensioneImpresa, string>,
  certificazioni: {
    qualita: "Gestione della qualità",
    ambiente: "Gestione ambientale",
    sicurezza_lavoro: "Salute e sicurezza sul lavoro",
    sicurezza_informazioni: "Sicurezza delle informazioni",
    energia: "Gestione dell'energia",
    appalti_soa: "Attestazione SOA",
    settoriale: "Certificazione di settore",
    altro: "Altra certificazione",
  } satisfies Record<CategoriaCertificazione, string>,
  tipiCriterio: {
    tipo_soggetto: "Tipo di soggetto",
    tag: "Competenze",
    regione: "Sede in una regione",
    paese: "Paese",
    ateco: "Attività (ATECO)",
    settore: "Settore",
    dimensione: "Dimensione",
    certificazione: "Certificazioni",
    esperienza: "Esperienza in un programma",
    regola_finanziaria: "Regola economica",
    manuale: "Solo a parole",
  } satisfies Record<TipoCriterio, string>,

  /** Rilievi anti-contatti sui testi pubblici: tipo e campo in parole. */
  rilieviTipi: {
    email: "un indirizzo email",
    telefono: "un numero di telefono",
    url: "un indirizzo web",
    piva_cf: "una partita IVA o un codice fiscale",
    iban: "un IBAN",
    dominio_bloccato: "un sito non ammesso",
    ragione_sociale: "il nome dell'azienda",
    dominio_azienda: "il sito dell'azienda",
    persona: "il cognome di una persona dell'azienda",
  } as Record<string, string>,
  rilieviCampi: {
    titolo: "Titolo",
    descrizione_pubblica: "Descrizione",
    profilo_partner_ideale: "Partner ideale",
    dettagli_riservati: "Dettagli riservati",
    posizioni: "Posizioni",
    requisiti: "Requisiti",
  } as Record<string, string>,
  rilieviNota:
    "Prima dell'accettazione non si condividono contatti né dati che fanno riconoscere l'azienda: i contatti si scambiano in chat, dopo che accetti.",

  /** Limiti del piano (call pubblicate o sospese, su tutte le tue aziende). */
  pianoNonIncludeTitolo: "Il tuo piano non include la creazione di call",
  pianoNonIncludeTesto:
    "Con un piano superiore puoi pubblicare call di partenariato e trovare partner per i bandi.",
  limiteRaggiuntoTitolo: "Hai raggiunto il numero massimo di call attive del tuo piano",
  limiteRaggiuntoTesto:
    "Chiudi una call pubblicata oppure passa a un piano superiore per pubblicarne un'altra.",
  vediPiani: "Vedi i piani",
  limiteCall: (usate: number, limite: number | null) =>
    limite === null
      ? `Call attive: ${usate} (il tuo piano non ha limiti)`
      : `Call attive: ${usate} di ${limite}`,

  /** AI (posizioni e testi): a carico della piattaforma, mai salvata da sola. */
  aiNota: "La proposta dell'AI non si salva da sola: scegli tu cosa usare, poi salva.",
  aiInCorso: "Sto preparando la proposta: di solito basta meno di un minuto.",
  aiLunga: "La proposta sta impiegando più del previsto: riprova tra qualche minuto.",
  aiErrore: "Non siamo riusciti a preparare la proposta.",

  /** Segnalazione (DSA): conferma di ricezione. */
  segnalaTitolo: "Segnala questo contenuto",
  segnalaMotivi: {
    contenuto_illecito: "Contenuto illecito",
    dati_personali: "Dati personali di altre persone",
    spam_pubblicita: "Spam o pubblicità",
    contatti_nel_testo: "Contatti nel testo",
    discriminatorio: "Contenuto discriminatorio",
    impersonificazione: "Si spaccia per un'altra azienda",
    altro: "Altro",
  } satisfies Record<MotivoSegnalazione, string>,
  segnalaBuonaFede:
    "Dichiaro in buona fede che le informazioni della segnalazione sono esatte e complete.",
  segnalaRicevuta:
    "Abbiamo ricevuto la segnalazione: la esaminiamo e ti avvisiamo della decisione.",
} as const;
