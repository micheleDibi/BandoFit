/** Traduzione dei nomi tecnici dei campi citati dall'AI-check
 *  ("derived.beneficiari[1].nome", "settore_nome", "dossier.bilanci.fatturato")
 *  in etichette italiane leggibili: l'utente non deve mai vedere codice. */

const FIELD_LABELS: Record<string, string> = {
  ragione_sociale: "Ragione sociale",
  denominazione: "Denominazione",
  forma_giuridica: "Forma giuridica",
  forma_giuridica_dettaglio: "Forma giuridica (dettaglio)",
  partita_iva: "Partita IVA",
  codice_fiscale: "Codice fiscale",
  ateco_codice: "Codice ATECO",
  ateco_descrizione: "Attività ATECO",
  ateco: "Codice ATECO",
  ateco_2022: "Codice ATECO 2022",
  ateco_principale: "Codice ATECO principale",
  ateco_divisione: "Divisione ATECO",
  ateco_secondari: "Codici ATECO secondari",
  codice: "Codice",
  descrizione: "Descrizione",
  settore_nome: "Settore",
  settore_id: "Settore",
  regione_nome: "Regione",
  regione_id: "Regione",
  regione: "Regione",
  anno_fondazione: "Anno di fondazione",
  data_costituzione: "Data di costituzione",
  data_inizio_attivita: "Inizio attività",
  indirizzo: "Indirizzo",
  comune: "Comune",
  provincia: "Provincia",
  cap: "CAP",
  sede: "Sede",
  unita_locali: "Unità locali",
  numero_sedi: "Numero sedi",
  classe_dimensionale: "Classe dimensionale",
  numero_dipendenti: "Numero dipendenti",
  dipendenti: "Dipendenti",
  numero: "Numero",
  fascia: "Fascia",
  fascia_fatturato: "Fascia di fatturato",
  fatturato: "Fatturato",
  capitale_sociale: "Capitale sociale",
  patrimonio_netto: "Patrimonio netto",
  ebitda: "EBITDA",
  utile: "Utile",
  bilanci: "Bilanci",
  dimensione_impresa: "Dimensione impresa",
  pec: "PEC",
  telefono: "Telefono",
  email: "Email",
  sito_web: "Sito web",
  contatti: "Contatti",
  beneficiari: "Categorie di beneficiari",
  stato_impresa: "Stato impresa",
  stato: "Stato impresa",
  anagrafica: "Anagrafica",
  attivita: "Attività",
  flags: "Attributi",
  startup_innovativa: "Startup innovativa",
  pmi_innovativa: "PMI innovativa",
  impresa_artigiana: "Impresa artigiana",
  esportatore: "Esportatore",
  importatore: "Importatore",
  certificazione_soa: "Certificazione SOA",
  gruppo_societario: "Gruppo societario",
  capogruppo: "Capogruppo",
  rea: "Numero REA",
  cciaa: "CCIAA",
  nace: "Codice NACE",
  sae: "Codice SAE",
  partecipazioni: "Partecipazioni",
  quota_percentuale: "Quota",
  nome: "Nome",
  cognome: "Cognome",
  ruoli: "Ruoli",
  is_legale_rappresentante: "Legale rappresentante",
};

/** Le fonti aggiungono contesto: dossier/derived = Registro Imprese. */
const SOURCE_LABELS: Record<string, string> = {
  dossier: "Registro Imprese",
  derived: "Registro Imprese",
};

/** "derived.beneficiari[1].nome" → "Categorie di beneficiari (Registro Imprese)". */
export function fieldLabel(campo: string | null | undefined): string {
  if (!campo) return "dato aziendale";
  // Più campi separati da virgola: traduci ciascuno e togli i duplicati.
  const parts = campo.split(",").map((p) => p.trim()).filter(Boolean);
  if (parts.length > 1) {
    const labels = [...new Set(parts.map((p) => fieldLabel(p)))];
    return labels.join(", ");
  }

  const segments = campo
    .replace(/\[\d+\]/g, "") // via gli indici di lista
    .split(".")
    .map((s) => s.trim())
    .filter(Boolean);
  const source = SOURCE_LABELS[segments[0]] ?? null;
  const path = source ? segments.slice(1) : segments;

  // Cerca l'etichetta più specifica risalendo il percorso: per
  // "attivita.ateco.codice" vince "ateco" (con "codice" sarebbe generico).
  let label: string | null = null;
  for (let i = path.length - 1; i >= 0; i--) {
    const candidate = FIELD_LABELS[path[i]];
    if (candidate && (label === null || ["Codice", "Descrizione", "Nome", "Numero", "Fascia"].includes(label))) {
      label = candidate;
      if (!["Codice", "Descrizione", "Nome", "Numero", "Fascia"].includes(candidate)) break;
    }
  }
  if (!label) {
    // Fallback leggibile: underscore → spazi, iniziale maiuscola.
    const last = path[path.length - 1] ?? campo;
    label = last.replaceAll("_", " ");
    label = label.charAt(0).toUpperCase() + label.slice(1);
  }
  return source ? `${label} (${source})` : label;
}
