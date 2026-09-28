export interface LookupItem {
  id: number;
  nome: string;
}

export interface AtecoItem {
  id: number;
  codice: string;
  descrizione: string | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/** Stati del catalogo (contratto DB bandi §4). Il catalogo può introdurre valori
 *  nuovi prima di noi: a runtime `stato_bando` può essere anche un'altra stringa,
 *  che `StatoBadge` mostra con un badge neutro. */
export type StatoBando =
  | "aperto"
  | "chiuso"
  | "in apertura prossimamente"
  | "sospeso"
  | "revocato";

/** Dettaglio di un requisito del pre-check. Le voci del bando sono alternative:
 *  `soddisfatta` è vera con ANCHE UNA SOLA voce in comune (`matched_ids`).
 *  `matched`/`totale` sono solo il dettaglio (voci in comune / voci elencate dal
 *  bando), non pesano sul punteggio. `nazionale`: aperto a tutte le regioni. */
export interface CompatibilitaDimensione {
  soddisfatta: boolean;
  matched: number;
  totale: number;
  matched_ids: number[];
  nazionale: boolean;
}

/** Punteggio di compatibilità a-priori azienda↔bando: requisiti soddisfatti /
 *  valutabili (es. 3/4). `punteggio` è la percentuale per la banda di colore.
 *  Calcolato dinamicamente dal backend; assente se il profilo è insufficiente. */
export interface Compatibilita {
  punteggio: number;
  matched: number;
  totale: number;
  dimensioni?: Record<string, CompatibilitaDimensione> | null;
}

export interface BandoListItem {
  id: number;
  slug: string;
  titolo: string | null;
  titolo_breve: string | null;
  descrizione_breve: string | null;
  stato_bando: StatoBando | null;
  livello: "flash_bando" | "guida_bando" | null;
  data_pubblicazione: string | null;
  data_apertura: string | null;
  data_scadenza: string | null;
  importo_totale_eur: number | null;
  importo_max_per_progetto_eur: number | null;
  ente_erogatore: string | null;
  tipologia: LookupItem | null;
  modalita_erogazione: LookupItem | null;
  regioni: LookupItem[];
  compatibilita?: Compatibilita | null;
}

export interface ContenutoSegment {
  kind: string;
  text?: string;
  href?: string;
  // Nei dati reali i segmenti `link` portano l'URL in `url`, non in `href`.
  url?: string;
}

export interface ContenutoItem {
  segments?: ContenutoSegment[];
  text?: string;
  // Voci delle sezioni `faq`: domanda + risposta.
  q?: string;
  a?: { segments?: ContenutoSegment[]; text?: string } | string;
}

export interface ContenutoSection {
  type: string;
  text?: string;
  segments?: ContenutoSegment[];
  items?: Array<string | ContenutoItem>;
}

export interface BandoDetail extends BandoListItem {
  area_geografica: string | null;
  tematica: string[];
  link_bando: string | null;
  link_candidatura: string | null;
  contenuto: { sections?: ContenutoSection[] } | null;
  allegati: Array<{ nome?: string; titolo?: string; url?: string; link?: string }>;
  programma: LookupItem | null;
  settori: LookupItem[];
  beneficiari: LookupItem[];
  codici_ateco: AtecoItem[];
}

export interface Lookups {
  regioni: LookupItem[];
  settori: LookupItem[];
  beneficiari: LookupItem[];
  codici_ateco: AtecoItem[];
  tipologie_bando: LookupItem[];
  modalita_erogazione: LookupItem[];
  programmi: LookupItem[];
}

/**
 * Come mostrare il prezzo di un piano o add-on: importo in €, «Gratis»
 * (stesso flusso di attivazione) o etichetta «su richiesta» (non attivabile
 * self-serve: la CTA diventa una richiesta di consulenza).
 */
export type TipoPrezzo = "importo" | "gratis" | "su_richiesta";

export interface Plan {
  id: number;
  nome: string;
  slug: string;
  descrizione: string | null;
  prezzo_annuale: string | number;
  tipo_prezzo: TipoPrezzo;
  etichetta_prezzo: string | null;
  ai_check: number;
  alert_attivo: boolean;
  alert_giorni_preavviso: number | null;
  /** Alert nuovi-bandi: giorni di ritardo dalla pubblicazione (null = esclusi). */
  alert_ritardo_giorni: number | null;
  num_account_aziendali: number;
  /** Numero di aziende gestibili col piano (Advisor: >1). */
  max_aziende: number;
  /** Bullet custom della card (una per riga in AdminPiani); null/vuoto =
   *  bullet derivate dai parametri del piano. */
  features_override: string[] | null;
  ordering: number;
  is_active: boolean;
  updated_at: string | null;
}

/** Impostazioni degli avvisi email sui nuovi bandi (GET/PUT /me/alert-settings). */
export interface AlertSettings {
  abilitati: boolean;
  /** Il piano EFFETTIVO (per i collegati: quello del titolare) li include? */
  piano_include_alert: boolean;
  ritardo_giorni: number | null;
}

export type UserRole = "admin" | "cliente" | "progettista";

export interface JobPosition {
  id: number;
  nome: string;
  slug: string;
}

export interface Profile {
  id: string;
  email: string;
  nome: string | null;
  cognome: string | null;
  azienda: string | null;
  telefono: string | null;
  codice_fiscale: string | null;
  cf_verified_at: string | null;
  job_position_id: number | null;
  /** Presente anche se la voce è stata disattivata (catalogo soft-disable). */
  job_position: JobPosition | null;
  /** Testo libero abbinato alla posizione «Altro». */
  job_position_altro: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface Subscription {
  id: string;
  status: "active" | "cancelled" | "expired";
  data_inizio: string;
  data_scadenza: string;
  plan: Plan;
  /** true se è l'abbonamento del titolare della famiglia (figlio attivo) */
  inherited?: boolean;
}

export type FamilyMemberStatus = "pending" | "active" | "demoted" | "removed" | "declined";

export interface FamilyMember {
  id: string; // id della membership
  member_id: string;
  denominazione: string;
  email: string;
  status: FamilyMemberStatus;
  invite_kind: "new_user" | "existing_user";
  invited_at: string;
  joined_at: string | null;
  demoted_at: string | null;
  /** Appartenenza/visibilità/budget (0031). aziende_visibili = solo le vive. */
  company_profile_id: string | null;
  company_nome: string | null;
  aziende_visibili: string[];
  /** null = illimitato; N = tetto per ciclo. */
  ai_check_budget: number | null;
  /** Consumi del membro nel ciclo corrente. */
  ai_check_usati: number;
}

export interface Family {
  limit: number;
  used: number;
  members: FamilyMember[];
}

export interface InviteMemberResult {
  family: Family;
  email_sent: boolean;
}

export interface Invitation {
  id: string;
  denominazione: string;
  parent_display_name: string;
  invited_at: string;
}

export interface MeFamily {
  role: "parent" | "child";
  // padre
  limit?: number | null;
  used?: number | null;
  // figlio
  status?: FamilyMemberStatus | null;
  denominazione?: string | null;
  parent_display_name?: string | null;
}

export interface PlanSwitchAdjustment {
  demoted: Array<{ member_id: string; denominazione: string }>;
  revoked_pending: Array<{ member_id: string; denominazione: string }>;
}

/** Attributi dell'area progettista (il codice è assegnato dal sistema; per
 *  gli admin arriva pigramente alla prima proposta — parità admin). */
export interface Progettista {
  codice: string;
}

/** Slot di disponibilità del progettista: istanti UTC, mostrati nel fuso del
 *  browser (a differenza del calendario personale, wall-clock italiano). */
export interface Slot {
  id: string;
  inizio: string;
  fine: string;
  prenotato: boolean;
  /** Serie di ricorrenza (null = slot singolo). */
  serie_id: string | null;
}

export type ConsulenzaStato = "nuova" | "assegnata" | "annullata";
export type PropostaStato = "inviata" | "accettata" | "rifiutata" | "superata" | "ritirata";

/** Come il cliente vede il progettista assegnato: per nome e cognome (il
 *  codice resta nel payload per usi interni, la UI non lo mostra). */
export interface ProgettistaPubblico {
  codice: string | null;
  nome: string | null;
}

export interface Proposta {
  id: string;
  /** Uso interno/admin: la UI del cliente mostra il nome, non il codice. */
  codice_progettista: string | null;
  nome_progettista: string | null;
  messaggio: string;
  stato: PropostaStato;
  created_at: string;
}

export interface Appuntamento {
  id: string;
  inizio: string;
  fine: string;
  stato: "confermata" | "annullata";
  /** Stanza Jitsi dedicata all'appuntamento (URL completo, derivato a server). */
  videocall_url: string | null;
}

/** Richiesta di consulto vista dal cliente. */
export interface Consulenza {
  id: string;
  stato: ConsulenzaStato;
  bando_id: number;
  bando_slug: string;
  bando_titolo: string;
  esito: AiEsito | null;
  punteggio: number | null;
  created_at: string;
  assigned_at: string | null;
  /** false per gli account collegati: vedono, non agiscono. */
  editable: boolean;
  progettista: ProgettistaPubblico | null;
  proposte_aperte: number;
  proposte: Proposta[];
  appuntamento: Appuntamento | null;
}

/** Vista PARZIALE del progettista sul pool (requisito: ragione sociale,
 *  P.IVA, denominazione utente, email, bando, esito AI-check). */
export interface RichiestaPool {
  id: string;
  stato: ConsulenzaStato;
  ragione_sociale: string | null;
  partita_iva: string | null;
  denominazione_utente: string;
  email: string | null;
  bando_id: number;
  bando_slug: string;
  bando_titolo: string;
  esito: AiEsito | null;
  punteggio: number | null;
  created_at: string;
  assegnata_a_me: boolean;
  mia_proposta_stato: PropostaStato | null;
  appuntamento: Appuntamento | null;
}

export interface RichiestaPoolDetail extends RichiestaPool {
  ai_check: AiCheck | null;
  mie_proposte: Proposta[];
}

export interface RichiestePool {
  aperte: RichiestaPool[];
  assegnate: RichiestaPool[];
}

/** Vista FULL post-assegnazione (l'accesso è registrato lato server). */
export interface FullCompany {
  company: CompanyProfile | null;
  dossier: DossierResponse;
}

export interface AppuntamentoProgettista {
  id: string;
  request_id: string;
  inizio: string;
  fine: string;
  stato: string;
  bando_titolo: string;
  ragione_sociale: string | null;
  email: string | null;
  /** Stanza Jitsi dedicata all'appuntamento (URL completo, derivato a server). */
  videocall_url: string | null;
}

export interface Notifica {
  id: number;
  tipo: string;
  titolo: string;
  corpo: string | null;
  url: string | null;
  /** Azienda a cui la notifica si riferisce (Advisor); null se generale. */
  company_profile_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface NotifichePage extends Page<Notifica> {
  /** Non lette complessive (non solo della pagina): il numero sul badge. */
  non_lette: number;
}

export interface Me {
  profile: Profile;
  subscription: Subscription | null;
  family: MeFamily | null;
  /** Valorizzato solo per gli utenti con ruolo progettista. */
  progettista?: Progettista | null;
  /** Limite EFFETTIVO di aziende gestibili (override > piano > 1; dalla 0030
   *  + addon companies): >1 = Advisor. Per un membro attivo è il SUO (=1). */
  max_aziende: number;
  /** Flag child-aware per lo switcher (0031): per un membro attivo è vero se
   *  vede più di un'azienda (visibilità ∩ vive); per gli altri max_aziende>1. */
  multi_azienda: boolean;
  /** Moduli accesi su questo ambiente (flag del backend): un modulo spento non
   *  compare da nessuna parte (menu, card, filtri). Default tutto spento. */
  funzioni: Funzioni;
  plan_switch_adjustment?: PlanSwitchAdjustment | null;
}

export interface Funzioni {
  partenariati: boolean;
}

/** Voce dell'elenco aziende gestite (Advisor multi-azienda). */
export interface CompanySummary {
  id: string;
  ragione_sociale: string;
  partita_iva: string;
  created_at: string;
  /** L'azienda attiva di default (la più vecchia viva). */
  attiva: boolean;
}

export interface Companies {
  aziende: CompanySummary[];
  max_aziende: number;
  usate: number;
}

export interface AdminFamilyInfo {
  type: "parent" | "child";
  status?: FamilyMemberStatus | null;
  parent_email?: string | null;
  members_count?: number | null;
}

export interface AdminUser {
  profile: Profile;
  subscription: Subscription | null;
  family: AdminFamilyInfo | null;
  progettista?: Progettista | null;
  /** Ragione sociale mostrata come azienda: dal dossier (P.IVA) del gruppo,
   *  con fallback al testo libero della registrazione; per i collegati attivi
   *  è quella del titolare. */
  azienda_nome: string | null;
}

export type ClasseDimensionale = "micro" | "piccola" | "media" | "grande";
export type FasciaFatturato =
  | "fino_100k"
  | "100k_500k"
  | "500k_2m"
  | "2m_10m"
  | "10m_50m"
  | "oltre_50m";

export interface CompanyProfile {
  ragione_sociale: string;
  forma_giuridica: string | null;
  partita_iva: string;
  codice_fiscale: string | null;
  ateco_id: number | null;
  ateco_codice: string | null;
  ateco_descrizione: string | null;
  settore_id: number | null;
  settore_nome: string | null;
  regione_id: number | null;
  regione_nome: string | null;
  /** Categorie di beneficiario DICHIARATE (non deducibili dalla visura), dalla
   *  lookup del catalogo. `beneficiari` è la copia col nome, come settore_nome. */
  beneficiari_ids: number[];
  beneficiari: { id: number; nome: string }[];
  anno_fondazione: number | null;
  indirizzo: string | null;
  comune: string | null;
  provincia: string | null;
  cap: string | null;
  classe_dimensionale: ClasseDimensionale | null;
  numero_dipendenti: number | null;
  fascia_fatturato: FasciaFatturato | null;
  pec: string | null;
  telefono: string | null;
  sito_web: string | null;
}

export interface CompanyResponse {
  editable: boolean;
  company: CompanyProfile | null;
}

/** Facet reali dell'azienda (id delle lookup del catalogo). Non è `CompanyProfile`:
 *  là ci sono i campi del form (una regione, un ATECO), qui tutto ciò che
 *  l'azienda è secondo i dati certificati — `regioni` copre TUTTE le sedi e
 *  `ateco` include le divisioni secondarie. `sufficiente` = P.IVA importata. */
export interface CompanyFacets {
  regioni: number[];
  ateco: number[];
  settori: number[];
  beneficiari: number[];
  sufficiente: boolean;
}

// ---- Dossier certificato (import openapi.it) -------------------------------

export interface DossierRuolo {
  code: string | null;
  description: string | null;
  start: string | null;
}

export interface DossierPerson {
  kind: "manager" | "shareholder" | "auditor";
  nome: string | null;
  cognome: string | null;
  denominazione: string | null;
  codice_fiscale: string | null;
  data_nascita: string | null;
  luogo_nascita: string | null;
  genere: string | null;
  ruoli: DossierRuolo[];
  is_legale_rappresentante: boolean;
  quota_percentuale: number | null;
  data_inizio_carica: string | null;
}

export interface DossierUnitaLocale {
  tipo: string | null;
  indirizzo: string | null;
  comune: string | null;
  provincia: string | null;
  cap: string | null;
  regione: string | null;
  stato: string | null;
}

export interface CompanyDossier {
  anagrafica: {
    denominazione: string | null;
    partita_iva: string | null;
    codice_fiscale: string | null;
    forma_giuridica: string | null;
    forma_giuridica_dettaglio: string | null;
    rea: string | null;
    cciaa: string | null;
    data_costituzione: string | null;
    data_inizio_attivita: string | null;
    stato: string | null;
    gruppo_societario: string | null;
    capogruppo: string | null;
  };
  attivita: {
    ateco: { codice: string | null; descrizione: string | null };
    ateco_2022: { codice: string | null; descrizione: string | null };
    ateco_secondari: string[];
    nace: string | null;
    sae: string | null;
  };
  sede: {
    indirizzo: string | null;
    comune: string | null;
    provincia: string | null;
    cap: string | null;
    regione: string | null;
    numero_sedi: number | null;
    unita_locali: DossierUnitaLocale[];
  };
  contatti: {
    pec: string | null;
    email: string | null;
    telefono: string | null;
    fax: string | null;
    sito_web: string | null;
  };
  dipendenti: {
    numero: number | null;
    fascia: string | null;
    trend: number | null;
    percentuali_contratti: Record<string, number | null> | null;
  };
  /** Fotografia della visura (un solo esercizio). Lo storico pluriennale sta
   *  in `BilanciOut` (GET /me/company/bilanci). */
  bilanci: {
    dimensione_impresa: string | null;
    fatturato: number | null;
    capitale_sociale: number | null;
    patrimonio_netto: number | null;
    ebitda: number | null;
    utile: number | null;
    /** Esercizio a cui si riferiscono i valori (anno della data di chiusura). */
    anno: number | null;
    data_chiusura: string | null;
    /** Anno del fatturato (`turnoverYear`): può differire da `anno`. Assente
     *  nei dossier precedenti. */
    anno_fatturato?: number | null;
  };
  partecipazioni: Array<{
    denominazione: string | null;
    codice_fiscale: string | null;
    quota: number | null;
  }>;
  flags: Record<string, boolean | null>;
}

export interface DossierResponse {
  editable: boolean;
  imported: boolean;
  fetched_at: string | null;
  sandbox: boolean | null;
  dossier: CompanyDossier | null;
  people: DossierPerson[];
  derived: Record<string, unknown>;
}

export interface ImportConflict {
  campo: string;
  valore_attuale: string | number | null;
  valore_certificato: string | number | null;
}

export interface AtecoSuggestion {
  id: number;
  codice: string;
  descrizione: string | null;
}

export interface ImportResult {
  company: CompanyResponse;
  dossier: CompanyDossier;
  people: DossierPerson[];
  autofill: { applied: string[]; conflicts: ImportConflict[] };
  suggestions: { codici_ateco: AtecoSuggestion[] };
  fetched_at: string;
  sandbox: boolean;
}

/** Il minimo per rispondere a «è la mia azienda?». `stato_impresa` intercetta
 *  le cessate e le sospese prima della conferma. */
export interface ImportPreviewAzienda {
  partita_iva: string;
  ragione_sociale: string | null;
  codice_fiscale: string | null;
  forma_giuridica: string | null;
  stato_impresa: string | null;
  sede: string | null;
  regione: string | null;
  ateco: string | null;
  legale_rappresentante: string | null;
  numero_persone: number;
}

/** Anteprima di sola lettura: nulla è ancora stato scritto sui dati aziendali.
 *  `reused: true` = il payload era già stato pagato, nessun nuovo addebito. */
export interface ImportPreview {
  azienda: ImportPreviewAzienda;
  autofill: { applied: string[]; conflicts: ImportConflict[] };
  suggestions: { codici_ateco: AtecoSuggestion[] };
  fetched_at: string;
  draft_expires_at: string;
  reused: boolean;
  sandbox: boolean;
  /** Esito del recupero dei bilanci pluriennali fatto insieme all'anteprima. */
  bilanci: ImportPreviewBilanci;
}

// ---- Bilanci per esercizio (GET /me/company/bilanci) ----------------------

/** Da dove arriva un valore: bilancio ufficiale XBRL, visura (ultimo
 *  esercizio) o storico del Registro Imprese. Il backend fonde per campo con
 *  precedenza xbrl > it_full > it_advanced. */
export type FonteBilancio = "xbrl" | "it_full" | "it_advanced";

/** Perché i bilanci non ci sono (vocabolario chiuso del backend). */
export type MotivoBilanci =
  | "nessun_bilancio"
  | "forma_senza_bilancio"
  | "errore_provider"
  | "esito_incerto"
  | "tempo_insufficiente"
  | "dati_non_corrispondenti"
  | "non_richiesto"
  | "piva_diversa";

/** Esito dell'ultimo tentativo di recupero dello storico. */
export type EsitoStoricoBilanci =
  | "ok"
  | "non_disponibili"
  | "errore"
  | "timeout"
  | "saltato"
  | "mismatch";

export type TipoBilancio = "ordinario" | "abbreviato" | "micro" | "ignoto";

/** Le 15 voci di bilancio, nello stesso ordine del backend. */
export type CampoBilancio =
  | "fatturato"
  | "valore_produzione"
  | "risultato_esercizio"
  | "patrimonio_netto"
  | "capitale_sociale"
  | "totale_attivo"
  | "debiti_totali"
  | "disponibilita_liquide"
  | "ebitda"
  | "ebit"
  | "cash_flow"
  | "oneri_finanziari"
  | "dipendenti"
  | "costo_personale"
  | "retribuzione_media_lorda";

/** Un esercizio già fuso: per ogni voce il valore della fonte più affidabile.
 *  `fonti` elenca solo le voci con un valore. */
export interface EsercizioBilancio extends Record<CampoBilancio, number | null> {
  anno: number;
  data_chiusura: string | null;
  tipo_bilancio: TipoBilancio;
  fonti: Partial<Record<CampoBilancio, FonteBilancio>>;
}

/** Indicatore calcolato dal server (il frontend non ricalcola nulla). Con
 *  `valore` null, `motivo_mancanza` spiega perché. */
export interface IndicatoreBilancio {
  chiave: string;
  etichetta: string;
  valore: number | null;
  unita: "percentuale" | "euro" | "rapporto";
  anni: number[];
  formula: string;
  motivo_mancanza: string | null;
}

/** Codici di fascia (vedi `lib/bilanci.ts` per le etichette). */
export interface FasceBilancio {
  fatturato: string | null;
  patrimonio_netto: string | null;
  dipendenti: string | null;
  trend_fatturato: string | null;
  anno_riferimento: number | null;
}

export interface BilanciOut {
  editable: boolean;
  stato: "disponibili" | "non_disponibili" | "mai_richiesti";
  motivo: MotivoBilanci | null;
  storico_esito: EsitoStoricoBilanci | null;
  ultimo_tentativo_at: string | null;
  /** Prima del quale «Recupera i bilanci» risponde 409 (cooldown); null = subito. */
  recuperabile_da: string | null;
  sandbox: boolean | null;
  /** Ordinati per anno crescente. */
  esercizi: EsercizioBilancio[];
  indicatori: IndicatoreBilancio[];
  fasce: FasceBilancio | null;
}

export interface ImportPreviewBilanci {
  stato: "disponibili" | "non_disponibili";
  motivo: MotivoBilanci | null;
  anni: number[];
}

// ---- Bilancio ufficiale on-demand (GET/POST /me/company/bilanci/ufficiale) --

/** Ciclo di vita di una richiesta. Aperti: `in_invio`, `in_lavorazione`,
 *  `esito_ignoto` (la risposta del Registro Imprese non è arrivata e si sta
 *  verificando se la richiesta è partita). Gli altri sono terminali. */
export type StatoRichiestaBilancio =
  | "in_invio"
  | "in_lavorazione"
  | "esito_ignoto"
  | "completata"
  | "non_disponibile"
  | "annullata"
  | "errore";

/** Perché una richiesta non è andata a buon fine (vocabolario chiuso). */
export type ErroreRichiestaBilancio =
  | "bilancio_non_disponibile"
  | "forma_non_ammessa"
  | "identificativo_non_valido"
  | "credito_provider"
  | "non_inviata"
  | "errore_provider"
  | "scaduta"
  | "esito_ignoto_scaduto";

/** Esito della lettura dei dati dal bilancio ufficiale (XBRL). Una richiesta
 *  `completata` con esito diverso da `ok` ha comunque il PDF, se conservato. */
export type XbrlEsito =
  | "ok"
  | "assente"
  | "firmato_non_leggibile"
  | "non_valido"
  | "consolidato"
  | "cf_non_corrispondente"
  | "troppo_grande";

/** Una richiesta di bilancio ufficiale. Il server non espone mai
 *  l'identificativo del fornitore. */
export interface BilancioRichiesta {
  id: string;
  stato: StatoRichiestaBilancio;
  /** Esercizio scelto; null = «ultimo disponibile». */
  anno_richiesto: number | null;
  /** Esercizio effettivamente ricevuto (noto a completamento). */
  anno_bilancio: number | null;
  errore_codice: ErroreRichiestaBilancio | null;
  /** Spiegazione in italiano semplice, già pronta da mostrare. */
  messaggio: string | null;
  xbrl_esito: XbrlEsito | null;
  avvisi_count: number;
  /** true = l'unità dell'addon è stata restituita automaticamente. */
  rimborsata: boolean;
  pdf_disponibile: boolean;
  created_at: string;
  completata_at: string | null;
}

/** Addon collegato, nella forma minima che serve a `prezzoDisplay`. */
export interface AddonBreve {
  slug: string;
  nome: string;
  tipo_prezzo: TipoPrezzo;
  etichetta_prezzo: string | null;
  prezzo: string | number;
}

export interface BilanciUfficiali {
  /** true = titolare: può richiedere; gli altri vedono solo lo storico. */
  editable: boolean;
  richiedibile: boolean;
  motivo_non_richiedibile: string | null;
  addon: AddonBreve | null;
  /** Unità dell'addon nell'inventario del titolare. */
  quantita: number;
  /** Esercizi già acquisiti come bilancio ufficiale. */
  anni_acquisiti: number[];
  /** Più recenti prima, al massimo 20. */
  richieste: BilancioRichiesta[];
}

// ---- Preferenze per utente -------------------------------------------------

export interface Preferences {
  regioni: number[];
  settori: number[];
  beneficiari: number[];
  codici_ateco: number[];
  tipologie: number[];
  modalita: number[];
  programmi: number[];
}

// ---- AI-check ----------------------------------------------------------------

export type AiEsito = "ammissibile" | "non_ammissibile" | "da_verificare";
export type AiTipoPunteggio = "stima" | "euristico";
export type AiVerdetto =
  | "soddisfatto"
  | "parzialmente_soddisfatto"
  | "non_soddisfatto"
  | "dato_mancante";

export interface AiRiferimentoBando {
  sezione: string;
  testo: string;
  verificata: boolean;
}

export interface AiDatoAzienda {
  campo: string;
  valore: string;
}

export interface AiRequisitoReport {
  id: string;
  testo: string;
  categoria: string;
  verdetto: AiVerdetto;
  riferimento_bando: AiRiferimentoBando;
  dato_azienda: AiDatoAzienda | null;
  motivazione: string;
}

// NON estende AiRequisitoReport: i criteri non hanno `testo` (hanno `nome`).
export interface AiCriterioReport {
  id: string;
  nome: string;
  categoria: string;
  verdetto: AiVerdetto;
  punti_max: number | null;
  punteggio_parziale: number | null;
  peso?: number | null;
  riferimento_bando: AiRiferimentoBando;
  dato_azienda: AiDatoAzienda | null;
  motivazione: string;
}

export interface AiPuntoNotevole {
  testo: string;
  ref: string | null;
}

export interface AiDatoMancante {
  campo: string | null;
  descrizione: string;
  ref: string | null;
}

export interface AiReport {
  schema_version: number;
  esito_ammissibilita: AiEsito;
  requisiti: AiRequisitoReport[];
  criteri: AiCriterioReport[];
  punteggio_totale: number | null;
  tipo_punteggio: AiTipoPunteggio;
  griglia: {
    presente: boolean;
    fonte: "contenuto" | "allegato" | "assente";
    punteggio_max_totale: number | null;
    punti_ottenuti_stimati: number | null;
    soglia_minima: number | null;
    note: string | null;
  };
  pesi_euristici: Record<string, number> | null;
  verifiche_strutturate: Record<string, { esito: string; [key: string]: unknown }>;
  punti_di_forza: AiPuntoNotevole[];
  punti_di_debolezza: AiPuntoNotevole[];
  dati_mancanti: AiDatoMancante[];
  disclaimer: string;
  meta: Record<string, unknown>;
}

export interface AiCheck {
  id: string;
  bando_id: number;
  bando_slug: string;
  bando_titolo: string;
  status: "pending" | "ready" | "error";
  error_detail: string | null;
  esito: AiEsito | null;
  punteggio: number | null;
  tipo_punteggio: AiTipoPunteggio | null;
  model: string | null;
  extraction_cached: boolean;
  created_at: string;
  ready_at: string | null;
  report: AiReport | null;
}

export interface AiQuota {
  totale: number;
  usati: number;
  rimanenti: number;
  periodo_inizio: string | null;
  periodo_fine: string | null;
}

export interface AiChecksResponse {
  editable: boolean;
  quota: AiQuota;
  items: AiCheck[];
  total: number;
}

// ---- Bandi salvati e calendario -------------------------------------------

export interface SavedBandoItem {
  bando: BandoListItem;
  disponibile: boolean;
  in_calendario: boolean;
  salvato_il: string;
}

export interface CalendarEvent {
  id: string;
  titolo: string;
  data: string; // YYYY-MM-DD (calendario italiano, wall-clock)
  tutto_il_giorno: boolean;
  ora_inizio: string | null; // HH:MM:SS
  ora_fine: string | null;
  note: string | null;
  tipo: "personale" | "bando";
  bando_id: number | null;
  bando_slug: string | null;
  created_at: string;
  updated_at: string;
}

// ---- Dati di fatturazione ---------------------------------------------------

export type TipoSoggetto = "azienda" | "privato";

/** Anagrafica di fatturazione (GET/PUT /me/billing-profile). È lo stato
 *  CORRENTE editabile: ogni fattura fotografa i dati al momento dell'acquisto. */
export interface BillingProfile {
  tipo_soggetto: TipoSoggetto;
  denominazione: string | null;
  nome: string | null;
  cognome: string | null;
  partita_iva: string | null;
  codice_fiscale: string | null;
  /** ISO 3166-1 alpha-2 (default "IT"; qualunque paese per entrambi i tipi). */
  paese: string;
  indirizzo: string;
  comune: string;
  provincia: string | null;
  cap: string;
  /** Esito verifica VIES (solo aziende con paese UE ≠ HR): true = reverse
   *  charge provato; false = P.IVA non valida (IVA 25%); null = mai
   *  verificata o VIES non raggiungibile all'ultimo salvataggio (IVA 25%). */
  vies_valid: boolean | null;
  vies_checked_at: string | null;
  completo: boolean;
}

/** Proposta di precompilazione dai dati aziendali: mai persistita da sola. */
export interface BillingPrefill {
  tipo_soggetto: TipoSoggetto | null;
  denominazione: string | null;
  partita_iva: string | null;
  codice_fiscale: string | null;
  indirizzo: string | null;
  comune: string | null;
  provincia: string | null;
  cap: string | null;
}

/** Corpo del PUT: solo i campi pertinenti al tipo di soggetto (il backend
 *  valida la coerenza in schemas/billing.py). */
export interface BillingProfileInput {
  tipo_soggetto: TipoSoggetto;
  denominazione?: string | null;
  nome?: string | null;
  cognome?: string | null;
  partita_iva?: string | null;
  codice_fiscale?: string | null;
  paese: string;
  indirizzo: string;
  comune: string;
  provincia?: string | null;
  cap: string;
}

// ---- Checkout e acquisti ----------------------------------------------------

/** Preventivo del checkout (POST /me/checkout/preview): importi in CENTESIMI,
 *  nessun effetto sul server. */
export interface CheckoutPreview {
  kind: "piano" | "addon";
  oggetto_slug: string;
  oggetto_nome: string;
  /** Unità acquistate (solo addon; 1 per i piani). listino_cents resta il
   *  prezzo UNITARIO, imponibile/totale sono già moltiplicati. */
  quantita: number;
  listino_cents: number;
  /** Credito per il periodo residuo del piano attuale (0 per gli addon). */
  credito_cents: number;
  imponibile_cents: number;
  iva_cents: number;
  /** Aliquota come stringa decimale ("25.00"; "0.00" col reverse charge). */
  iva_aliquota: string;
  /** Marcatore del reverse charge (valorizzato solo a IVA 0); null con IVA
   *  ordinaria. Le righe storiche pre-cambio conservano "N2.1". */
  natura_iva: string | null;
  totale_cents: number;
  valuta: string;
  /** Solo per i piani: la scadenza dell'abbonamento dopo l'acquisto. */
  scadenza_risultante: string | null;
  dettaglio: Record<string, unknown>;
}

/** Esito del POST /me/checkout: il token apre il widget Revolut. */
export interface CheckoutResult {
  purchase_id: string;
  revolut_order_token: string;
  checkout_url: string | null;
  totale_cents: number;
  valuta: string;
}

export type PurchaseKind = "piano" | "rinnovo" | "addon" | "cambio_admin" | "addon_admin";
export type PurchaseStatus =
  | "in_attesa"
  | "pagato"
  | "fallito"
  | "scaduto"
  | "annullato"
  | "gratuito";

export interface Purchase {
  id: string;
  kind: PurchaseKind;
  status: PurchaseStatus;
  oggetto_slug: string;
  oggetto_nome: string;
  descrizione: string;
  /** Unità dell'oggetto (solo gli addon possono superare 1). */
  quantita: number;
  imponibile_cents: number;
  iva_cents: number;
  totale_cents: number;
  iva_aliquota: string;
  natura_iva: string | null;
  valuta: string;
  decline_reason: string | null;
  /** Solo kind=cambio_admin/addon_admin: la ragione decisa dall'admin. */
  motivazione: string | null;
  created_at: string;
  paid_at: string | null;
}

// ---- Entitlement (GET /me/entitlements, migration 0030) ---------------------

export interface ResourceEntitlement {
  base: number;
  extra: number;
  effettivo: number;
  usato: number;
  residuo: number;
}

export interface AiChecksEntitlement extends ResourceEntitlement {
  periodo_inizio: string | null;
  periodo_fine: string | null;
  /** Solo per un MEMBRO attivo (WP6): il suo budget (null nel payload di un
   *  titolare; per il membro, null = illimitato) e i suoi consumi nel ciclo. */
  budget_membro: number | null;
  usati_membro: number | null;
}

/** Le quote dell'account in un'unica risposta: il frontend legge, non
 *  ricalcola. Per un collegato attivo sono quelle del titolare. */
export interface Entitlements {
  editable: boolean;
  seats: ResourceEntitlement;
  companies: ResourceEntitlement;
  ai_checks: AiChecksEntitlement;
}

// ---- Admin pagamenti (registro fatture, anomalie) ---------------------------

export type InvoiceStato =
  | "da_emettere"
  | "in_invio"
  | "inviata"
  | "consegnata"
  | "non_consegnata"
  | "scartata"
  | "errore";

export interface AdminInvoice {
  id: string;
  purchase_id: string;
  anno: number;
  serie: string;
  /** Presente solo sulle righe storiche già trasmesse (null sulle nuove). */
  numero: number | null;
  data_documento: string;
  stato: InvoiceStato;
  provider_id: string | null;
  totale_cents: number;
  tentativi: number;
  created_at: string;
  emessa_at: string | null;
}

/** GET /admin/invoices: paginata ma senza total_pages (si calcola qui). */
export interface AdminInvoicesPage {
  items: AdminInvoice[];
  total: number;
  page: number;
  page_size: number;
}

/** Incasso orfano da riconciliare (rimborso manuale in v1). */
export interface PaymentAnomaly {
  audit_id: number;
  payload: {
    revolut_order_id?: string;
    motivo?: string;
    purchase_id?: string;
    [key: string]: unknown;
  } | null;
  created_at: string;
  risolta: boolean;
}

// ---- Gestione abbonamento (rinnovo, disdetta, metodo di pagamento) ---------

export interface SavedMethod {
  presente: boolean;
  /** Es. «Carta •••• 4242»; null se nessun metodo salvato. */
  label: string | null;
}

/** Cambio piano programmato alla scadenza (motivo: disdetta | downgrade). */
export interface ScheduledChange {
  to_plan_slug: string;
  to_plan_nome: string;
  effective_date: string;
  motivo: string;
}

/** Stato della gestione abbonamento (GET /me/subscription/management). */
export interface SubscriptionManagement {
  auto_renew: boolean;
  data_scadenza: string | null;
  metodo: SavedMethod;
  cambio_programmato: ScheduledChange | null;
}

// ---- Add-on ----------------------------------------------------------------

/** consumabile = unità a quantità (si compra N volte, si consuma);
 *  permanente = possesso binario (0 o 1). Immutabile come lo slug. */
export type TipoFruizione = "consumabile" | "permanente";

export interface Addon {
  id: number;
  nome: string;
  /** Identificativo stabile: aggancerà le funzionalità future. */
  slug: string;
  descrizione: string | null;
  prezzo: string | number;
  tipo_prezzo: TipoPrezzo;
  tipo_fruizione: TipoFruizione;
  /** Risorsa entitlement estesa (0030): seats/companies; null = addon normale. */
  risorsa: "seats" | "companies" | null;
  etichetta_prezzo: string | null;
  ordering: number;
  is_active: boolean;
  /** Acquistabilità per l'utente corrente (solo tipo_prezzo 'importo'):
   *  il gate vero è nel checkout, questa pilota la CTA. */
  acquistabile: boolean;
  motivo_non_acquistabile: "solo_titolare" | "piano_non_idoneo" | null;
  updated_at: string | null;
}

/** Voce dell'inventario addon (GET /me/addons e /admin/users/{id}/addons):
 *  il backend ritorna solo le voci con quantità > 0. */
export interface MyAddon {
  addon_id: number;
  slug: string;
  nome: string;
  descrizione: string | null;
  tipo_fruizione: TipoFruizione;
  /** Risorsa entitlement (0030): seats/companies; null = addon normale. */
  risorsa: "seats" | "companies" | null;
  quantita: number;
  /** Totali storici dal ledger: accrediti (acquisti+grant), SOLI consumi (le
   *  revoche admin riducono quantita senza contare come consumo) e rimborsi
   *  automatici (unità restituite dopo un consumo, contate a parte). */
  acquistate: number;
  consumate: number;
  rimborsate: number;
  updated_at: string | null;
}

export type AddonMovimentoTipo =
  | "purchase"
  | "admin_grant"
  | "consume"
  | "refund"
  | "admin_revoke";

/** Movimento dello storico addon (GET /me/addons/ledger, più recenti prima). */
export interface AddonLedgerEntry {
  tipo: AddonMovimentoTipo;
  delta: number;
  note: string | null;
  created_at: string;
}

// ---- Partenariati: regole di partenariato per bando (WP3) ------------------

/** Come il bando ammette la partecipazione in aggregazione. */
export type ModalitaPartenariato =
  | "obbligatorio"
  | "ammesso"
  | "non_ammesso"
  | "non_determinabile";

/** Filtro della lista bandi (`GET /bandi?partenariato=`): `ammesso` comprende
 *  anche i bandi che lo rendono obbligatorio. Vale solo sui bandi già
 *  analizzati. */
export type FiltroPartenariato = "ammesso" | "obbligatorio";

export type StatoPartenariatoBando =
  | "non_estratta"
  | "in_corso"
  | "pronta"
  | "errore"
  | "nessun_segnale";

/** Avanzamento dentro `in_corso`: documenti → lettura → analisi. */
export type FasePartenariato = "documenti" | "lettura" | "analisi";

export type StatoVoceRegola = "verificata" | "da_verificare";

/** Passaggio del bando da cui viene una voce, già risolto dal server. */
export interface CitazioneRegola {
  /** Blocco citato: `META` o `S2` (scheda del bando), `D1-p3` (documento 1,
   *  pagina 3). */
  sezione: string;
  /** «Avviso pubblico — pag. 3» oppure «Scheda del bando». */
  fonte_etichetta: string;
  testo: string;
  /** Il testo è stato ritrovato alla lettera nella fonte. */
  verificata: boolean;
  /** Solo https (il server scarta il resto); null per la scheda del bando. */
  url_documento: string | null;
  pagina: number | null;
}

/** Ogni voce delle regole porta il suo stato e la sua citazione: le voci
 *  `da_verificare` (citazione non ritrovata o valore incoerente) non entrano
 *  negli usi automatici (filtro, call, matching). */
export interface VoceRegola {
  stato: StatoVoceRegola;
  citazione: CitazioneRegola | null;
  /** Perché la voce è da verificare (controlli di coerenza del server), in
   *  parole. */
  avvisi?: string[];
}

/** Voce con un solo valore (modalità, costituzione, numero di partner). */
export interface VoceValoreRegola<T> extends VoceRegola {
  valore: T;
}

/** Forme di aggregazione del vocabolario v1 (+ `altra`, residuale). */
export type FormaAggregazione =
  | "ats"
  | "ati_rti"
  | "rete_contratto"
  | "rete_soggetto"
  | "consorzio"
  | "accordo_partenariato"
  | "consorzio_ue"
  | "altra";

/** Tipi di soggetto del vocabolario v1 (il nome `TipoSoggetto` è già preso
 *  dall'anagrafica di fatturazione). */
export type TipoSoggettoPartenariato =
  | "impresa"
  | "micro_impresa"
  | "piccola_impresa"
  | "media_impresa"
  | "pmi"
  | "grande_impresa"
  | "startup_innovativa"
  | "pmi_innovativa"
  | "impresa_artigiana"
  | "cooperativa"
  | "impresa_sociale"
  | "libero_professionista"
  | "organismo_ricerca"
  | "universita"
  | "ente_pubblico"
  | "ente_locale"
  | "ente_terzo_settore"
  | "associazione_categoria"
  | "organismo_formazione"
  | "istituto_scolastico"
  | "istituto_cultura"
  | "fondazione"
  | "consorzio_rete_imprese"
  | "intermediario_finanziario"
  | "ente_sportivo"
  | "persona_fisica"
  | "altro";

export type CostituzionePartenariato =
  | "costituenda_ammessa"
  | "costituita_richiesta"
  | "non_indicato";

export type RuoloComposizione =
  | "capofila"
  | "partner"
  | "qualsiasi"
  | "affiliato"
  | "partner_associato";

/** Quando va soddisfatto un vincolo o consegnato un documento. */
export type MomentoRegola = "domanda" | "concessione" | "prima_erogazione" | "non_indicato";

export interface FormaAmmessaRegola extends VoceRegola {
  forma: FormaAggregazione;
  /** Etichetta del vocabolario, già risolta dal server. */
  etichetta?: string;
  note: string | null;
}

export interface ComposizioneRegola extends VoceRegola {
  id: string;
  tipo_soggetto: TipoSoggettoPartenariato;
  /** Etichetta del vocabolario, già risolta dal server. */
  tipo_soggetto_etichetta?: string;
  /** Descrizione libera quando `tipo_soggetto` è `altro`. */
  tipo_soggetto_testo: string | null;
  /** Id della tabella `beneficiari` del catalogo corrispondenti al tipo. */
  beneficiari?: number[];
  minimo: number | null;
  massimo: number | null;
  ruolo: RuoloComposizione;
  /** Id del lookup regioni (i nomi non riconosciuti li scarta il server,
   *  con un avviso sulla voce). */
  regioni: number[];
  /** Nomi del catalogo delle regioni riconosciute. */
  regioni_nomi?: string[];
  paesi: string[];
  vincolo_territoriale: string | null;
}

export interface QuotaRegola extends VoceRegola {
  id: string;
  ambito: "per_partner" | "per_categoria" | "capofila";
  categoria: TipoSoggettoPartenariato | null;
  /** Percentuali 0–100. */
  min_percentuale: number | null;
  max_percentuale: number | null;
  base_calcolo:
    | "costo_totale_progetto"
    | "spese_ammissibili"
    | "contributo"
    | "budget_partner"
    | "non_indicata";
  effetto_violazione:
    | "inammissibilita_progetto"
    | "esclusione_partner"
    | "perdita_maggiorazione"
    | "non_indicato";
}

export type TipoVincoloPartenariato =
  | "indipendenza"
  | "esclusivita_partenariato"
  | "paesi_distinti"
  | "sede_operativa_regione"
  | "costituzione_entro"
  | "requisito_capofila"
  | "altro";

export interface VincoloRegola extends VoceRegola {
  id: string;
  tipo: TipoVincoloPartenariato;
  descrizione: string;
  parametro: number | null;
  momento: MomentoRegola;
}

/** Contratto unico delle regole finanziarie (backend
 *  `schemas/regole_finanziarie.py`, WP1). */
export type VariabileFinanziaria =
  | "fatturato"
  | "fatturato_medio_2"
  | "fatturato_medio_3"
  | "valore_produzione"
  | "risultato_esercizio"
  | "patrimonio_netto"
  | "capitale_sociale"
  | "totale_attivo"
  | "debiti_totali"
  | "disponibilita_liquide"
  | "mol"
  | "ebit"
  | "oneri_finanziari"
  | "costo_personale"
  | "dipendenti"
  | "bilanci_approvati_n"
  | "costo_quota"
  | "contributo_quota"
  | "costo_progetto_totale";

export type AmbitoRegolaFinanziaria =
  | "ciascun_partner"
  | "capofila"
  | "media_pesata_quote"
  | "partenariato_totale";

/** Forma: `numeratore [/ denominatore] <operatore> soglia`, dove la soglia è
 *  un numero (`soglia`, decimale come stringa) OPPURE
 *  `soglia_coefficiente × soglia_variabile`. */
export interface RegolaFinanziariaRegola extends VoceRegola {
  id: string;
  descrizione: string;
  ambito: AmbitoRegolaFinanziaria;
  numeratore: VariabileFinanziaria;
  denominatore: VariabileFinanziaria | null;
  operatore: "lt" | "le" | "gt" | "ge";
  soglia: string | null;
  soglia_variabile: VariabileFinanziaria | null;
  soglia_coefficiente: string | null;
  unita: "rapporto" | "euro" | "numero";
}

/** Documenti del vocabolario v1 (backend `DocumentoPartenariato`) + `altro`. */
export type TipoDocumentoRichiesto =
  | "lettera_intenti"
  | "nda"
  | "term_sheet_mou"
  | "mandato_collettivo"
  | "atto_costitutivo"
  | "impegno_costituire"
  | "accordo_partenariato"
  | "consortium_agreement"
  | "contratto_rete"
  | "programma_rete"
  | "fondo_patrimoniale"
  | "organo_comune"
  | "iscrizione_registro_imprese"
  | "statuto"
  | "dichiarazione_sostitutiva"
  | "dichiarazioni_affiliated_entities"
  | "lettere_associated_partners"
  | "altro";

export interface DocumentoRichiestoRegola extends VoceRegola {
  id: string;
  tipo: TipoDocumentoRichiesto;
  descrizione: string;
  momento: MomentoRegola;
}

/** Regole post-elaborate dal server (`bando_partenariato.regole`): i testi
 *  vengono dal modello e vanno mostrati come testo semplice, mai come HTML
 *  né con link automatici. */
export interface RegolePartenariato {
  /** Modalità letta dal modello, con la sua citazione. */
  modalita: VoceValoreRegola<ModalitaPartenariato>;
  /** Quella da usare: coincide con `modalita.valore` solo se la citazione è
   *  verificata e coerente, altrimenti `non_determinabile`. */
  modalita_effettiva: ModalitaPartenariato;
  forme_ammesse: FormaAmmessaRegola[];
  costituzione: VoceValoreRegola<CostituzionePartenariato>;
  partner_min: VoceValoreRegola<number | null>;
  partner_max: VoceValoreRegola<number | null>;
  /** Come si contano i partner (capofila incluso o no, affiliati…). */
  conteggio_note: string | null;
  composizione: ComposizioneRegola[];
  quote: QuotaRegola[];
  vincoli: VincoloRegola[];
  regole_finanziarie: RegolaFinanziariaRegola[];
  documenti_richiesti: DocumentoRichiestoRegola[];
  /** I documenti letti non bastano per un quadro completo. */
  fonti_insufficienti: boolean;
  note: string | null;
  /** Incoerenze trovate dai controlli automatici, in parole. */
  avvisi: string[];
}

/** Esito di un documento ufficiale nella pipeline di lettura. */
export type StatoFontePartenariato =
  | "candidato"
  | "bloccato_policy"
  | "formato_non_supportato"
  | "errore_download"
  | "troppo_grande"
  | "non_pdf"
  | "schema_non_https"
  | "scaricato"
  | "letto"
  | "letto_parziale"
  | "non_leggibile"
  | "protetto"
  | "corrotto"
  | "timeout"
  | "escluso_tetto";

export interface FontePartenariato {
  /** Numero del documento nelle citazioni (`D1-p3` → 1). */
  n: number;
  etichetta: string;
  dominio: string | null;
  /** Solo https. */
  url: string | null;
  stato: StatoFontePartenariato;
  pagine_totali: number | null;
  /** Numeri delle pagine mandate all'analisi. */
  pagine_incluse: number[];
  /** Testo tagliato ai limiti di lettura. */
  troncato: boolean;
}

/** `GET /bandi/{slug}/partenariato` e `POST …/partenariato/analisi`. */
export interface PartenariatoBando {
  bando_id: number;
  bando_slug: string;
  stato: StatoPartenariatoBando;
  fase: FasePartenariato | null;
  /** Nuova analisi in corso mentre si servono le regole precedenti. */
  aggiornamento_in_corso: boolean;
  /** Le regole andrebbero rilette (catalogo cambiato, riverifica, prompt nuovo). */
  aggiornabile: boolean;
  regole: RegolePartenariato | null;
  fonti: FontePartenariato[];
  estratta_at: string | null;
  verificata_at: string | null;
  avviata_at: string | null;
  /** Messaggio dell'ultimo errore (anche con regole precedenti ancora valide). */
  errore: string | null;
  /** Prima di questo istante una nuova analisi non parte (backoff). */
  riprova_dopo: string | null;
  /** L'utente può avviare (o rilanciare) l'analisi adesso. */
  puo_avviare: boolean;
  motivo_non_avviabile: string | null;
  /** Call di partenariato aperte sul bando (dal WP5; oggi 0). */
  calls_aperte: number;
  stato_bando: string | null;
}

/** `GET /partenariati/vocabolario`: vocabolario controllato versionato. */
export interface VocabolarioTipoSoggetto {
  codice: TipoSoggettoPartenariato;
  etichetta: string;
  /** Id della tabella `beneficiari` del catalogo. */
  beneficiari: number[];
}

export interface VocabolarioCompetenza {
  codice: string;
  etichetta: string;
  /** Etichetta dell'area, per raggruppare. */
  area: string;
}

export interface VocabolarioForma {
  codice: FormaAggregazione;
  etichetta: string;
  /** Chi risponde verso l'ente; null per `altra`. */
  responsabilita: "pro_quota" | "solidale" | "singoli_partecipanti" | "consortile" | null;
  /** Costituzione tipica, in parole. */
  costituzione: string;
  /** Checklist completa: prima i documenti di base, poi gli specifici. */
  documenti: { codice: string; etichetta: string }[];
}

export interface Vocabolario {
  versione: number;
  tipi_soggetto: VocabolarioTipoSoggetto[];
  competenze: VocabolarioCompetenza[];
  forme: VocabolarioForma[];
  ruoli: { codice: "capofila" | "partner"; etichetta: string }[];
}

// ---- Partenariati: profilo partner e consensi (WP4) ------------------------

/** Ruolo che un'azienda è disposta a ricoprire in un partenariato. */
export type RuoloPartner = "capofila" | "partner";

/** Forme che un partner può accettare: le 7 del vocabolario, senza `altra`
 *  (serve solo alle regole estratte dai bandi). */
export type FormaProfiloPartner = Exclude<FormaAggregazione, "altra">;

export type EsitoEsperienzaPartner = "finanziato" | "in_valutazione" | "non_finanziato";

/** Da dove parte una scelta di consenso: il client non può inviare altro
 *  (`admin` e `sistema` li scrivono solo l'area admin e i trigger). */
export type OrigineConsensoPartner = "import_piva" | "pagina_azienda" | "wizard_call";

/** Esperienza in un programma di finanziamento. `programma_id` rimanda alla
 *  lookup `programmi` del catalogo; `programma` è il nome (obbligatorio). */
export interface EsperienzaPartner {
  programma: string;
  programma_id: number | null;
  anno: number | null;
  ruolo: RuoloPartner | null;
  titolo: string | null;
  esito: EsitoEsperienzaPartner | null;
}

/** Corpo di `PUT /me/partner-profile`: il profilo INTERO (i campi assenti
 *  tornano ai default). Non contiene visibilità, anonimato, consenso,
 *  referente né sospensione: cambiano solo con le azioni dedicate. */
export interface PartnerProfileInput {
  descrizione_competenze: string | null;
  competenze: string[];
  competenze_libere: string[];
  /** Solo i tipi DICHIARATI: quelli del Registro Imprese non si dichiarano. */
  tipi_soggetto: TipoSoggettoPartenariato[];
  ruoli_disponibili: RuoloPartner[];
  settori_interesse: number[];
  regioni_interesse: number[];
  /** ISO 3166-1 alpha-2 maiuscolo. */
  paesi_interesse: string[];
  forme_accettate: FormaProfiloPartner[];
  esperienze: EsperienzaPartner[];
  certificazioni: string[];
  infrastrutture: string | null;
  accetta_inviti: boolean;
  categorie_bando_escluse: number[];
}

export type MotivoIdentitaPartner =
  | "dati_non_importati"
  | "piva_diversa"
  | "impresa_non_attiva"
  | "dati_sandbox";

/** `non_disponibile`: il profilo nominativo è spento in questa versione (si
 *  compare solo in forma anonima), qualunque sia il titolare. */
export type MotivoNominativoPartner =
  | "cf_non_verificato"
  | "non_rappresentante"
  | "non_disponibile";

/** Identità dal Registro Imprese: senza, il consenso non si può dare. */
export interface IdentitaPartner {
  verificata: boolean;
  motivo: MotivoIdentitaPartner | null;
  denominazione_registro: string | null;
  /** Il titolare risulta legale rappresentante con CF verificato: può
   *  mostrare il nome dell'azienda. */
  puo_essere_nominativo: boolean;
  motivo_nominativo: MotivoNominativoPartner | null;
}

/** Proposta della bozza AI: diventa visibile solo dopo «Applica» e
 *  salvataggio del profilo. */
export interface BozzaProfiloAi {
  descrizione_competenze: string;
  competenze: string[];
  motivazioni: { codice: string; motivo: string }[];
}

export type StatoBozzaAiPartner = "in_corso" | "pronta" | "errore";

export interface BozzaAiPartner {
  stato: StatoBozzaAiPartner;
  avviata_at: string | null;
  pronta_at: string | null;
  errore: string | null;
  proposta: BozzaProfiloAi | null;
}

export interface ReferentePartner {
  tipo: "titolare" | "membro";
  nome: string | null;
  sei_tu: boolean;
  /** Proposta in attesa della risposta della persona scelta. */
  proposto: { nome: string | null; sei_tu: boolean } | null;
}

/** `GET /me/partner-profile`. Un membro riceve `editable=false`,
 *  `referenti_possibili=[]` e nessun id di altri utenti. */
export interface PartnerProfile {
  editable: boolean;
  esiste: boolean;
  visibile: boolean;
  anonimo: boolean;
  sospeso: boolean;
  consenso: { versione: string; at: string } | null;
  informativa_versione_corrente: string;
  /** L'informativa è cambiata dopo il consenso: si propone di riconfermarlo
   *  (il consenso dato resta valido). */
  riconsenso_suggerito: boolean;
  identita: IdentitaPartner;
  profilo: PartnerProfileInput;
  /** Tipi di soggetto ricavati dal Registro Imprese (non rimovibili). */
  tipi_soggetto_dedotti: TipoSoggettoPartenariato[];
  /** 0-100. */
  completezza: number;
  /** Parole dei testi che potrebbero far riconoscere un'azienda anonima (non
   *  bloccano il salvataggio). */
  avvisi_anonimato: string[];
  referente: ReferentePartner;
  referenti_possibili: { user_id: string; nome: string }[];
  bozza_ai: BozzaAiPartner | null;
  vocabolario_versione: number;
  aggiornato_at: string | null;
}

/** `GET /me/partner-profile/anteprima`: ciò che vedono le altre aziende
 *  (whitelist del server). Per gli anonimi niente nome né infrastrutture,
 *  esperienze con il solo programma e fasce solo di fatturato. */
export interface PartnerPubblico {
  codice_pubblico: string;
  anonimo: boolean;
  denominazione: string | null;
  regione_sede: string | null;
  regioni_interesse: string[];
  paesi_interesse: string[];
  ateco_sezione: { lettera: string; descrizione: string } | null;
  classe_dimensionale: string | null;
  /** Codici di fascia (come `FasceBilancio`), mai importi. */
  fasce: {
    fatturato: string | null;
    patrimonio_netto: string | null;
    dipendenti: string | null;
    trend: string | null;
  };
  tipi_soggetto: { codice: string; etichetta: string; fonte: "registro" | "dichiarato" }[];
  competenze: { codice: string; etichetta: string; area: string }[];
  competenze_libere: string[];
  descrizione_competenze: string | null;
  esperienze: {
    programma: string;
    anno: number | null;
    ruolo: string | null;
    titolo: string | null;
  }[];
  certificazioni: string[];
  infrastrutture: string | null;
  ruoli_disponibili: RuoloPartner[];
  forme_accettate: { codice: string; etichetta: string }[];
  completezza: number;
  accetta_inviti: boolean;
}

/** `GET /partenariati/informativa`: testi in markdown semplice (titoli,
 *  paragrafi, elenchi puntati). */
export interface InformativaPartner {
  versione: string;
  testo: string;
  referente_versione: string;
  referente_testo: string;
}

/** Corpo di `POST /me/partner-profile/consenso`. `anonimo` è obbligatorio
 *  per `concedi` e `anonimato`. */
export interface ConsensoPartnerInput {
  azione: "concedi" | "revoca" | "anonimato";
  informativa_versione: string;
  origine: OrigineConsensoPartner;
  anonimo?: boolean | null;
}

/** Corpo di `POST /me/partner-profile/referente` (solo il titolare).
 *  `annulla_proposta` azzera solo la proposta pendente: il referente in
 *  carica resta. */
export interface ReferentePartnerInput {
  azione: "proponi" | "annulla_proposta" | "rimuovi";
  user_id?: string | null;
}

/** Corpo di `POST /me/partner-profile/referente/risposta`: il membro
 *  proposto accetta o rifiuta; il referente rinuncia (`revoca`). */
export interface RispostaReferenteInput {
  azione: "accetta" | "rifiuta" | "revoca";
  informativa_versione?: string | null;
}
