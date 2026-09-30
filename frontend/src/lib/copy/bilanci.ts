import type {
  ErroreRichiestaBilancio,
  MotivoBilanci,
  StatoRichiestaBilancio,
  XbrlEsito,
} from "../../types";

/** Bilanci per esercizio: i motivi compaiono sia nell'anteprima dell'import
 *  sia nella sezione «Bilanci» della pagina Azienda. Frasi neutre: l'invito a
 *  riprovare lo aggiunge chi può farlo (il titolare), non il motivo. */
export const BILANCI_COPY = {
  motivi: {
    nessun_bilancio: "Il Registro Imprese non ha bilanci depositati per questa azienda.",
    forma_senza_bilancio:
      "Per la forma giuridica di questa azienda non è previsto il deposito del bilancio al Registro Imprese.",
    errore_provider: "Il servizio che fornisce i bilanci non ha risposto correttamente.",
    esito_incerto: "Il servizio che fornisce i bilanci non ha risposto in tempo.",
    tempo_insufficiente:
      "L'importazione è durata più del previsto e i bilanci non sono stati recuperati.",
    dati_non_corrispondenti:
      "I bilanci ricevuti non corrispondevano alla partita IVA dell'azienda, quindi non li abbiamo salvati.",
    non_richiesto: "I bilanci degli anni passati non sono ancora stati recuperati.",
    piva_diversa:
      "La partita IVA cercata è diversa da quella dei dati aziendali, quindi non abbiamo recuperato i bilanci.",
  } satisfies Record<MotivoBilanci, string>,
  /** Al posto di `motivi.nessun_bilancio` quando un esercizio c'è già (dalla
   *  visura): «nessun bilancio depositato» contraddirebbe la tabella. */
  nessunAltroBilancio: "Lo storico del Registro Imprese non riporta altri bilanci di questa azienda.",
  /** Nessun esercizio e nessun motivo da dire (o storico spento). */
  senzaBilanci: "Al momento non ci sono bilanci per questa azienda.",
  /** Con `piva_diversa` il recupero parte solo dopo la correzione. */
  correggiPiva: "Correggi la partita IVA nei dati aziendali, poi recupera lo storico.",
  recupera: "Recupera i bilanci",
} as const;

/** Card «Bilancio ufficiale» della sezione Bilanci: il bilancio depositato,
 *  pagato con un'unità dell'addon. I messaggi di esito arrivano già pronti dal
 *  server (`messaggio`): quelli qui sotto servono solo se manca. */
export const BILANCIO_UFFICIALE_COPY = {
  titolo: "Bilancio ufficiale",
  descrizione:
    "Il bilancio depositato al Registro Imprese, in PDF. I suoi numeri si aggiungono ai bilanci di questa pagina e hanno la precedenza sulle altre fonti.",
  prezzo: (testo: string) => `Prezzo: ${testo} a bilancio`,
  disponibili: (n: number) => (n === 1 ? "Ne hai 1 disponibile." : `Ne hai ${n} disponibili.`),
  esercizio: "Esercizio",
  ultimoDisponibile: "Ultimo disponibile",
  giaAcquisiti: (anni: string) => `Hai già il bilancio ufficiale di: ${anni}.`,
  richiedi: "Richiedi il bilancio ufficiale",
  inCorso: "Hai già una richiesta in corso: potrai farne un'altra quando si conclude.",
  senzaUnita: (prezzo: string) =>
    `Non hai bilanci ufficiali disponibili: acquistane uno (${prezzo}) per procedere.`,
  acquista: "Acquista un bilancio ufficiale",
  soloTitolare: "Il bilancio ufficiale lo richiede il titolare dell'azienda.",
  erroreCaricamento: "Impossibile caricare i bilanci ufficiali.",

  confermaTitolo: "Richiedi il bilancio ufficiale",
  conferma: (n: number) =>
    `Userai 1 bilancio ufficiale (ne hai ${n}). Di solito arriva entro 15 minuti: ti avvisiamo.`,
  confermaEsercizio: (anno: number | null) =>
    anno === null ? "Esercizio: l'ultimo disponibile." : `Esercizio: ${anno}.`,
  confermaRimborso:
    "Se il Registro Imprese non ha questo bilancio, l'unità ti viene restituita.",
  confermaInvia: "Conferma e richiedi",
  annulla: "Annulla",

  storicoTitolo: "Storico delle richieste",
  esercizioTitolo: (anno: number | null) =>
    anno === null ? "Ultimo esercizio disponibile" : `Esercizio ${anno}`,
  richiestoIl: (quando: string) => `Richiesto il ${quando}`,
  prontoIl: (quando: string) => `pronto il ${quando}`,
  /** Stato in parole: il colore del badge non basta da solo. */
  stati: {
    in_invio: "In invio",
    in_lavorazione: "In preparazione",
    esito_ignoto: "In verifica",
    completata: "Pronto",
    non_disponibile: "Non disponibile",
    annullata: "Annullata",
    errore: "Non riuscita",
  } satisfies Record<StatoRichiestaBilancio, string>,
  attesa: "Di solito arriva entro 15 minuti: ti avvisiamo quando è pronto.",
  verifica:
    "Non sappiamo ancora se la richiesta è arrivata al Registro Imprese: lo verifichiamo noi, non serve rifarla.",
  completataOk: "I suoi numeri sono ora nei bilanci dell'azienda.",
  unitaRestituita: "Unità restituita",
  unitaRestituitaFrase: "L'unità ti è stata restituita.",
  scaricaPdf: "Scarica PDF",
  /** Coda solo per lettori di schermo: distingue i bottoni della lista. */
  scaricaPdfContesto: (anno: number | null) =>
    anno === null ? " del bilancio" : ` del bilancio ${anno}`,
  scaricamento: "Download…",
  avvisi: (n: number) =>
    n === 1
      ? "C'è 1 avviso sulla lettura dei numeri: confrontali con il PDF."
      : `Ci sono ${n} avvisi sulla lettura dei numeri: confrontali con il PDF.`,
  /** Stessi avvisi quando il PDF non è stato conservato: niente rimando al PDF. */
  avvisiSenzaPdf: (n: number) =>
    n === 1 ? "C'è 1 avviso sulla lettura dei numeri." : `Ci sono ${n} avvisi sulla lettura dei numeri.`,
  /** Completata ma senza PDF né numeri (troppo grande, illeggibile): non è «Pronto». */
  statoNonUtilizzabile: "Non utilizzabile",
  senzaPdf: "Il PDF non è stato conservato: se ti serve, scrivi all'assistenza.",
  esitiLettura: {
    assente:
      "Il documento non contiene i numeri in un formato leggibile: trovi il bilancio nel PDF.",
    firmato_non_leggibile:
      "I numeri sono in un file firmato che non riusciamo a leggere: trovi il bilancio nel PDF.",
    non_valido: "Non siamo riusciti a leggere i numeri dal documento: trovi il bilancio nel PDF.",
    consolidato:
      "È il bilancio consolidato di un gruppo: i suoi numeri non entrano nei bilanci dell'azienda.",
    cf_non_corrispondente:
      "Il documento riporta un codice fiscale diverso da quello dell'azienda, quindi non abbiamo usato i suoi numeri.",
    troppo_grande: "Il documento ricevuto è troppo grande per essere elaborato per intero.",
  } satisfies Record<Exclude<XbrlEsito, "ok">, string>,
  errori: {
    bilancio_non_disponibile: "Il Registro Imprese non ha questo bilancio.",
    forma_non_ammessa:
      "Per la forma giuridica di questa azienda il bilancio ufficiale non è disponibile.",
    identificativo_non_valido: "Il Registro Imprese non riconosce la partita IVA dell'azienda.",
    credito_provider: "Il servizio non è disponibile in questo momento: riprova più tardi.",
    non_inviata: "La richiesta non è partita: riprova più tardi.",
    errore_provider: "Il servizio che fornisce i bilanci ha avuto un problema.",
    scaduta:
      "Il bilancio non è arrivato entro 24 ore. Scrivi all'assistenza: verifichiamo e, se serve, ti restituiamo l'unità.",
    esito_ignoto_scaduto:
      "Non abbiamo ricevuto risposta dal Registro Imprese. Scrivi all'assistenza: verifichiamo e, se serve, ti restituiamo l'unità.",
  } satisfies Record<ErroreRichiestaBilancio, string>,

  /** Esito annunciato subito dopo la conferma. */
  esitoInviata: "Richiesta inviata. Di solito arriva entro 15 minuti: ti avvisiamo quando è pronto.",
  esitoSenzaConferma:
    "Non abbiamo ricevuto la conferma in tempo. Controlla le richieste qui sotto: se compare, è partita e non devi rifarla.",
} as const;
