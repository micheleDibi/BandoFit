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
 *  nuovi prima di noi: a runtime lo stato può essere anche un'altra stringa,
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
  /** Solo informativo: lo stato da mostrare è `stato_effettivo`. */
  stato_bando: StatoBando | null;
  /** Stato calcolato dal catalogo alla lettura (per esempio chiuso dopo la
   *  scadenza): è quello da mostrare, con `stato_bando` come ripiego. */
  stato_effettivo: StatoBando | null;
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

/** Da dove viene un link della scheda (lo sceglie il backend). */
export type OrigineLinkScheda =
  | "candidatura"
  | "link_candidatura"
  | "fonte_ufficiale"
  | "portale"
  | "link_bando";

/** Link della scheda già scelto e filtrato dal backend: il frontend lo rende
 *  così com'è, senza ricalcolarlo. */
export interface LinkScheda {
  url: string;
  host: string | null;
  origine: OrigineLinkScheda;
}

/** Allegato della scheda, normalizzato dal backend (senza doppioni, già filtrato). */
export interface AllegatoScheda {
  url: string;
  etichetta: string | null;
  /** `atto`, `allegato` o un altro tipo del catalogo. */
  tipo: string | null;
  formato: string | null;
}

export interface BandoDetail extends BandoListItem {
  area_geografica: string | null;
  tematica: string[];
  contenuto: { sections?: ContenutoSection[] } | null;
  /** Pulsante principale della scheda. */
  cta: LinkScheda | null;
  /** Pulsante secondario «Fonte ufficiale»: si mostra solo se diverso da `cta`. */
  link_fonte: LinkScheda | null;
  allegati: AllegatoScheda[];
  /** Orari "HH:MM:SS", se il catalogo li conosce. */
  ora_apertura: string | null;
  ora_scadenza: string | null;
  data_pubblicazione_verificata: boolean | null;
  data_apertura_verificata: boolean | null;
  data_scadenza_verificata: boolean | null;
  fonte_ufficiale_e_atto: boolean | null;
  /** Fonte ufficiale del bando (sito dell'ente o portale pubblico): la UI usa
   *  `cta` e `link_fonte`, che la includono già. Stringhe libere: un valore
   *  nuovo del catalogo non deve rompere nulla. */
  fonte_ufficiale_url: string | null;
  fonte_ufficiale_host: string | null;
  /** `ente` | `portale_pubblico` */
  fonte_ufficiale_tipo: string | null;
  /** `trovata` | `in_verifica` | `non_trovata` */
  fonte_ufficiale_stato: string | null;
  /** Data ISO dell'ultima verifica della fonte. */
  fonte_ufficiale_verificata_at: string | null;
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
  /** Partenariati (0036): call attive (pubblicate o sospese) su tutte le aziende
   *  del titolare. null = illimitate, 0 = non incluse (OPPOSTO di
   *  alert_ritardo_giorni, dove null = esclusi). */
  partner_calls_attive_max: number | null;
  /** Partenariati (0036): candidature al mese solare. null = illimitate, 0 = non incluse. */
  partner_candidature_mese: number | null;
  /** Partenariati (0042): bozze AI dei documenti (lettera d'intenti, NDA,
   *  term sheet) al mese solare. null = illimitate, 0 = non incluse. */
  partner_bozze_mese: number | null;
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
  /** Consulto chiesto dalla call di partenariato (WP9): l'AI-check è
   *  facoltativo, esito e punteggio ci sono solo se ce n'era uno pronto. */
  partner_call_id: string | null;
  /** L'azienda della richiesta: il link alla call porta `?azienda=` (la call
   *  si apre solo con l'azienda creatrice attiva). Assente sui server vecchi. */
  company_profile_id?: string | null;
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
  /** Consulto chiesto dalla call di partenariato (WP9): finché non è
   *  assegnata a chi guarda, niente dati dell'azienda (solo il bando). */
  da_call: boolean;
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
  /** Storico dei bilanci e bilancio ufficiale (assente = spento). */
  bilanci_storico?: boolean;
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
  /** Solo per i non disponibili: slug della scheda che ha preso il posto del
   *  bando (bando unito a un altro). */
  slug_aggiornato?: string | null;
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

/** Un limite del modulo partenariati (0036/0037). A differenza delle risorse
 *  sopra: `limite` null = illimitato (e allora `residuo` è null), 0 = non
 *  incluso nel piano. */
export interface PartenariatiLimite {
  limite: number | null;
  usate: number;
  residuo: number | null;
}

export interface PartenariatiLimiteMese extends PartenariatiLimite {
  /** Mese solare (Europe/Rome), date ISO. */
  periodo_inizio: string | null;
  periodo_fine: string | null;
}

/** Limiti di partenariato del titolare, su tutte le sue aziende. */
export interface PartenariatiEntitlement {
  /** Call pubblicate o sospese (le bozze non contano). */
  call_attive: PartenariatiLimite;
  candidature_mese: PartenariatiLimiteMese;
  /** Bozze AI dei documenti del mese (0042; gli errori pagati contano).
   *  null = dato non disponibile (NON «illimitate»: quello è `limite`
   *  null): la UI non mostra il contatore, il limite lo applica il server. */
  bozze_mese: PartenariatiLimiteMese | null;
}

/** Le quote dell'account in un'unica risposta: il frontend legge, non
 *  ricalcola. Per un collegato attivo sono quelle del titolare. */
export interface Entitlements {
  editable: boolean;
  seats: ResourceEntitlement;
  companies: ResourceEntitlement;
  ai_checks: AiChecksEntitlement;
  /** null con il modulo spento o se il dato non è disponibile: in quel caso
   *  la UI non mostra i limiti (li applica comunque il server). */
  partenariati: PartenariatiEntitlement | null;
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
  /** Stato calcolato dal catalogo: se c'è, vale lui (ripiego su `stato_bando`). */
  stato_effettivo?: string | null;
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

/** Perché il nome dell'azienda non si può mostrare: dal WP9 serve la verifica
 *  dell'identità da parte della piattaforma (`identita_non_verificata_admin`,
 *  anche quando i dati del registro non sono più coerenti);
 *  `non_disponibile` = interruttore globale spento. */
export type MotivoNominativoPartner = "non_disponibile" | "identita_non_verificata_admin";

/** Identità dal Registro Imprese: senza, il consenso non si può dare. */
export interface IdentitaPartner {
  verificata: boolean;
  motivo: MotivoIdentitaPartner | null;
  denominazione_registro: string | null;
  /** L'azienda ha l'identità verificata dalla piattaforma (WP9): può
   *  mostrare il nome. */
  puo_essere_nominativo: boolean;
  motivo_nominativo: MotivoNominativoPartner | null;
  /** Verifica dell'identità da parte della piattaforma (WP9). */
  verifica: VerificaIdentita;
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

// ---- Partenariati: call di partenariato (WP5) --------------------------------
// Specchio di `backend/app/schemas/partner_call.py` e `partenariato_criteri.py`
// (fonte unica dei DTO). Gli importi e le percentuali (`Decimal` nel backend)
// arrivano come STRINGHE decimali, come il prezzo dei piani.

/** Chi crea la call: guida il progetto (capofila) o cerca chi lo guidi. */
export type RuoloCreatoreCall = "capofila" | "cerco_capofila";

/** Forma prevista dal creatore: le 7 del vocabolario, senza `altra`. */
export type FormaPrevistaCall = Exclude<FormaAggregazione, "altra">;

export type StatoCall =
  | "bozza"
  | "pubblicata"
  | "chiusa_completata"
  | "chiusa_annullata"
  | "scaduta"
  | "sospesa_moderazione";

export type MotivoChiusuraCall =
  | "scadenza_call"
  | "bando_chiuso"
  | "bando_sospeso"
  | "bando_revocato"
  | "bando_non_disponibile"
  | "azienda_non_disponibile"
  | "creatore_completata"
  | "creatore_annullata"
  | "moderazione";

export type VisibilitaCall = "pubblica" | "solo_invitati";

/** Fascia PUBBLICA del budget di progetto (il budget esatto è riservato). */
export type BudgetFasciaCall =
  | "fino_50k"
  | "50k_150k"
  | "150k_300k"
  | "300k_500k"
  | "500k_1m"
  | "1m_2m"
  | "2m_5m"
  | "oltre_5m";

export type OrigineRequisitoCall =
  | "ai_check"
  | "precheck"
  | "bando_partenariato"
  | "regola_finanziaria"
  | "manuale";

export type EsitoCoperturaCall =
  | "coperto"
  | "non_coperto"
  | "dato_mancante"
  | "incerto"
  | "non_valutabile";

export type FonteCoperturaCall = "registro" | "bilanci" | "dichiarato" | "ai_check" | "nessuna";

/** `consorzio` = basta un membro (i requisiti «cercati»); `ogni_membro` =
 *  vale per ciascun membro (filtro rigido sui candidati). */
export type AmbitoRequisitoCall = "consorzio" | "ogni_membro";

export type TerritorioModalitaCall = "qualsiasi" | "sede_attuale" | "sede_entro_erogazione";

export type StatoJobAiCall = "nessuno" | "in_corso" | "pronta" | "errore";

/** Come una voce entra nelle regole confermate dal creatore. */
export type OrigineVoceRegola = "confermata" | "modificata" | "aggiunta";

/** Classe dimensionale del Registro Imprese. */
export type DimensioneImpresa = "micro" | "piccola" | "media" | "grande";

export type CategoriaCertificazione =
  | "qualita"
  | "ambiente"
  | "sicurezza_lavoro"
  | "sicurezza_informazioni"
  | "energia"
  | "appalti_soa"
  | "settoriale"
  | "altro";

// Criteri tipizzati: unione discriminata su `tipo`, STRICT lato server (niente
// campi in più, liste non vuote, id interi).

export interface CriterioTipoSoggetto {
  tipo: "tipo_soggetto";
  valori: TipoSoggettoPartenariato[];
}
export interface CriterioTag {
  tipo: "tag";
  /** Codici delle competenze del vocabolario. */
  tags: string[];
  modalita: "almeno_uno" | "tutti";
}
export interface CriterioRegione {
  tipo: "regione";
  regioni_ids: number[];
  modalita: "sede_attuale" | "sede_entro_erogazione";
}
export interface CriterioPaese {
  tipo: "paese";
  /** ISO 3166-1 alpha-2. */
  paesi: string[];
  escludi: boolean;
}
export interface CriterioAteco {
  tipo: "ateco";
  /** Divisioni a 2 cifre. */
  divisioni: string[];
}
export interface CriterioSettore {
  tipo: "settore";
  settori_ids: number[];
}
export interface CriterioDimensione {
  tipo: "dimensione";
  valori: DimensioneImpresa[];
}
export interface CriterioCertificazione {
  tipo: "certificazione";
  categorie: CategoriaCertificazione[];
}
export interface CriterioEsperienza {
  tipo: "esperienza";
  programmi_ids: number[];
  ruolo: RuoloPartner | null;
}
/** Contratto unico WP1 (`schemas/regole_finanziarie.py`), senza i campi di
 *  stato e citazione delle regole estratte. */
export interface RegolaFinanziaria {
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
/** Solo dalle regole del bando confermate (Q11): mai scritta a mano. */
export interface CriterioRegolaFinanziaria {
  tipo: "regola_finanziaria";
  regola: RegolaFinanziaria;
}
/** Requisito descritto solo a parole: non si valuta mai in automatico. */
export interface CriterioManuale {
  tipo: "manuale";
}

export type CriterioPartner =
  | CriterioTipoSoggetto
  | CriterioTag
  | CriterioRegione
  | CriterioPaese
  | CriterioAteco
  | CriterioSettore
  | CriterioDimensione
  | CriterioCertificazione
  | CriterioEsperienza
  | CriterioRegolaFinanziaria
  | CriterioManuale;

export type TipoCriterio = CriterioPartner["tipo"];

/** Passaggio del bando a cui si ancora un requisito o una voce delle regole
 *  (`CitazioneIn`): stessa forma di `CitazioneRegola` del WP3. */
export interface CitazioneCall {
  sezione: string;
  testo: string;
  verificata: boolean;
  fonte_etichetta?: string | null;
  url_documento?: string | null;
  pagina?: number | null;
}

// Snapshot delle regole confermate dal creatore (`partner_calls.regole_partenariato`).
// `confermata` solo per voci verificate dell'estrazione, uguali e con la loro
// citazione; `modificata` = responsabilità del creatore; `aggiunta` senza
// citazione. Nessuna chiave in più (il server le rifiuta).

export interface VoceSnapshotRegola {
  origine_voce: OrigineVoceRegola;
  citazione: CitazioneCall | null;
}
export interface ModalitaSnapshot extends VoceSnapshotRegola {
  valore: ModalitaPartenariato;
}
export interface CostituzioneSnapshot extends VoceSnapshotRegola {
  valore: CostituzionePartenariato;
}
export interface ConteggioSnapshot extends VoceSnapshotRegola {
  valore: number;
}
export interface FormaAmmessaSnapshot extends VoceSnapshotRegola {
  forma: FormaAggregazione;
  note: string | null;
}
export interface ComposizioneSnapshot extends VoceSnapshotRegola {
  id: string;
  tipo_soggetto: TipoSoggettoPartenariato;
  tipo_soggetto_testo: string | null;
  minimo: number | null;
  massimo: number | null;
  ruolo: RuoloComposizione;
  regioni: number[];
  paesi: string[];
  vincolo_territoriale: string | null;
}
export interface QuotaSnapshot extends VoceSnapshotRegola {
  id: string;
  ambito: QuotaRegola["ambito"];
  categoria: TipoSoggettoPartenariato | null;
  min_percentuale: number | null;
  max_percentuale: number | null;
  base_calcolo: QuotaRegola["base_calcolo"];
  effetto_violazione: QuotaRegola["effetto_violazione"];
}
export interface VincoloSnapshot extends VoceSnapshotRegola {
  id: string;
  tipo: TipoVincoloPartenariato;
  descrizione: string;
  parametro: number | null;
  momento: MomentoRegola;
}
/** Q11: solo `confermata`, con la citazione verificata. */
export interface RegolaFinanziariaSnapshot extends VoceSnapshotRegola, RegolaFinanziaria {}
export interface DocumentoSnapshot extends VoceSnapshotRegola {
  id: string;
  tipo: TipoDocumentoRichiesto;
  descrizione: string;
  momento: MomentoRegola;
}
/** Estrazione di partenza: la scrive il server (quella del client si ignora). */
export interface FonteSnapshotRegole {
  estratta_at: string | null;
  prompt_version: number | null;
  modalita_effettiva: ModalitaPartenariato;
}
export interface RegoleCallSnapshot {
  versione: 1;
  fonte: FonteSnapshotRegole | null;
  modalita: ModalitaSnapshot;
  forme_ammesse: FormaAmmessaSnapshot[];
  costituzione: CostituzioneSnapshot | null;
  partner_min: ConteggioSnapshot | null;
  partner_max: ConteggioSnapshot | null;
  composizione: ComposizioneSnapshot[];
  quote: QuotaSnapshot[];
  vincoli: VincoloSnapshot[];
  regole_finanziarie: RegolaFinanziariaSnapshot[];
  documenti_richiesti: DocumentoSnapshot[];
}

// ---- Input (corpi delle richieste; il server rifiuta i campi in più) ----------

/** `POST /partenariati/call`. `anonima: false` (call con il nome) solo per
 *  un'azienda con l'identità verificata dalla piattaforma (WP9), altrimenti
 *  409 `identita_non_verificata_admin`. */
export interface CallCreaInput {
  bando_slug: string;
  ruolo_creatore: RuoloCreatoreCall;
  forma_aggregazione_prevista?: FormaPrevistaCall | null;
  anonima?: boolean;
  /** Solo se le regole del bando dicono «non ammesso» (almeno 20 caratteri). */
  override_non_ammesso_motivo?: string | null;
}

/** `PATCH /partenariati/call/{id}`: SOLO i campi da cambiare. Dopo la
 *  pubblicazione si possono cambiare solo testi (non il titolo), scadenza,
 *  visibilità, budget e quota; `wizard_passo` mai. */
export interface CallAggiornaInput {
  titolo?: string | null;
  descrizione_pubblica?: string | null;
  dettagli_riservati?: string | null;
  profilo_partner_ideale?: string | null;
  /** YYYY-MM-DD. */
  scadenza_call?: string | null;
  visibilita?: VisibilitaCall;
  budget_fascia?: BudgetFasciaCall | null;
  /** Decimale come stringa, riservato (lo vedi solo tu). */
  budget_progetto_eur?: string | null;
  /** Decimale come stringa, (0, 100]. */
  quota_creatore_pct?: string | null;
  ruolo_creatore?: RuoloCreatoreCall;
  forma_aggregazione_prevista?: FormaPrevistaCall | null;
  /** Solo in bozza; `false` solo con l'identità verificata (WP9). */
  anonima?: boolean;
  wizard_passo?: number;
  override_non_ammesso_motivo?: string | null;
}

export interface RegoleConfermaInput {
  regole: RegoleCallSnapshot;
  esclusivita: boolean;
}

/** Un requisito (replace-all con `PUT …/requisiti`): la copertura la calcola
 *  il server, l'etichetta breve la assegna lui. */
export interface RequisitoInput {
  id?: string | null;
  etichetta?: string | null;
  testo: string;
  criterio: CriterioPartner | null;
  ambito: AmbitoRequisitoCall;
  cercato: boolean;
  origine: OrigineRequisitoCall;
  rif_origine?: string | null;
  citazione?: CitazioneCall | null;
}

/** Una posizione cercata (replace-all con `PUT …/posizioni`). */
export interface PosizioneInput {
  id?: string | null;
  titolo: string;
  ruolo: RuoloPartner;
  tipi_soggetto: TipoSoggettoPartenariato[];
  competenze: string[];
  ateco_divisioni: string[];
  regioni: number[];
  territorio_modalita: TerritorioModalitaCall;
  paesi: string[];
  dimensioni: DimensioneImpresa[];
  /** Decimale come stringa, (0, 100]. */
  quota_ipotizzata_pct: string | null;
  numero: number;
  requisiti_ids: string[];
  note: string | null;
}

export interface ChiudiCallInput {
  esito: "completata" | "annullata";
}

/** `messaggio` dal WP7: `oggetto_id` è l'id del messaggio (numero, come testo). */
export type OggettoSegnalazione = "call" | "profilo" | "messaggio";
export type MotivoSegnalazione =
  | "contenuto_illecito"
  | "dati_personali"
  | "spam_pubblicita"
  | "contatti_nel_testo"
  | "discriminatorio"
  | "impersonificazione"
  | "altro";
export type StatoSegnalazione =
  | "ricevuta"
  | "in_esame"
  | "decisa"
  | "ricorso_presentato"
  | "ricorso_deciso";

/** `POST /partenariati/segnalazioni` (DSA): la buona fede è obbligatoria. */
export interface SegnalazioneInput {
  oggetto_tipo: OggettoSegnalazione;
  oggetto_id: string;
  motivo: MotivoSegnalazione;
  descrizione: string;
  buona_fede: true;
}

export interface SegnalazioneRicevuta {
  id: string;
  stato: StatoSegnalazione;
  created_at: string;
}

// ---- Vista del creatore ----------------------------------------------------------

export interface BandoCall {
  id: number;
  slug: string;
  titolo: string;
  scadenza: string | null;
  programma_id: number | null;
  tipologia_id: number | null;
  stato_effettivo: string | null;
  verificato_at: string | null;
  mancante_dal: string | null;
}

/** Requisito con la copertura del creatore (solo per lui: mai verso terzi).
 *  `id` null = proposta della gap analysis non ancora salvata. */
export interface RequisitoCall {
  id: string | null;
  etichetta: string | null;
  testo: string;
  criterio: CriterioPartner | null;
  ambito: AmbitoRequisitoCall;
  cercato: boolean;
  origine: OrigineRequisitoCall;
  rif_origine: string | null;
  citazione: CitazioneCall | null;
  copertura_creatore: EsitoCoperturaCall | null;
  copertura_fonte: FonteCoperturaCall | null;
  /** Solo da template deterministici del server. */
  copertura_nota: string | null;
  ordine: number;
}

export interface PosizioneCall {
  id: string;
  titolo: string;
  ruolo: RuoloPartner;
  tipi_soggetto: TipoSoggettoPartenariato[];
  competenze: string[];
  ateco_divisioni: string[];
  regioni: number[];
  territorio_modalita: TerritorioModalitaCall;
  paesi: string[];
  dimensioni: DimensioneImpresa[];
  quota_ipotizzata_pct: string | null;
  numero: number;
  requisiti_ids: string[];
  note: string | null;
  ordine: number;
}

export interface RiepilogoGap {
  coperti: number;
  non_coperti: number;
  dato_mancante: number;
  incerto: number;
  non_valutabile: number;
}

export interface GapCall {
  requisiti: RequisitoCall[];
  riepilogo: RiepilogoGap;
  /** L'ultimo AI-check pronto dell'azienda sul bando. */
  ai_check: { disponibile: boolean; id: string | null; data: string | null };
  partenariato: {
    stato: StatoPartenariatoBando | null;
    modalita_effettiva: ModalitaPartenariato | null;
  };
}

/** Rilievo anti-contatti su un testo pubblico: nomina campo e tipo. */
export interface RilievoCall {
  campo: string;
  tipo: string;
  estratto: string;
  bloccante: boolean;
}

/** Posizione proposta dall'AI, già post-validata dal server: si salva solo se
 *  la confermi con `PUT …/posizioni`. */
export interface PosizioneProposta {
  titolo: string;
  ruolo: RuoloPartner;
  tipi_soggetto: TipoSoggettoPartenariato[];
  competenze: string[];
  ateco_divisioni: string[];
  regioni: number[];
  territorio_modalita: TerritorioModalitaCall;
  paesi: string[];
  dimensioni: DimensioneImpresa[];
  quota_ipotizzata_pct: string | null;
  numero: number;
  requisiti_ids: string[];
  note: string | null;
  motivazione: string | null;
}

export interface PropostaPosizioni {
  posizioni: PosizioneProposta[];
  /** Avvisi deterministici (quote oltre il 100%, numero di partner…). */
  avvisi: string[];
}

/** Bozza dei testi pubblici, già anonimizzata, con i rilievi rimasti. */
export interface PropostaTesti {
  titolo: string | null;
  descrizione_pubblica: string | null;
  profilo_partner_ideale: string | null;
  rilievi: RilievoCall[];
  avvisi: string[];
}

/** Job AI asincrono (202 + stato riletto con il dettaglio della call). */
export interface JobAiCall<P> {
  stato: StatoJobAiCall;
  avviata_at: string | null;
  errore: string | null;
  proposta: P | null;
}

export interface MotivoBloccoCall {
  codice: string;
  messaggio: string;
}

/** `GET /partenariati/call/{id}` per l'azienda creatrice (titolare e membri
 *  con visibilità; `editable` solo per il titolare). */
export interface CallVistaCreatore {
  id: string;
  company_profile_id: string;
  editable: boolean;
  stato: StatoCall;
  motivo_chiusura: MotivoChiusuraCall | null;
  versione: number;
  wizard_passo: number;
  bando: BandoCall;
  ruolo_creatore: RuoloCreatoreCall;
  forma_aggregazione_prevista: FormaPrevistaCall | null;
  anonima: boolean;
  titolo: string | null;
  descrizione_pubblica: string | null;
  dettagli_riservati: string | null;
  profilo_partner_ideale: string | null;
  budget_fascia: BudgetFasciaCall | null;
  budget_progetto_eur: string | null;
  quota_creatore_pct: string | null;
  scadenza_call: string | null;
  visibilita: VisibilitaCall;
  override_non_ammesso_motivo: string | null;
  regole_partenariato: RegoleCallSnapshot | null;
  regole_confermate_at: string | null;
  esclusivita: boolean;
  posizioni: PosizioneCall[];
  gap: GapCall;
  ai_posizioni: JobAiCall<PropostaPosizioni>;
  ai_testi: JobAiCall<PropostaTesti>;
  /** Pool del titolare; null se non leggibile (lo applica comunque il server). */
  limiti: PartenariatiEntitlement | null;
  puo_pubblicare: boolean;
  motivi_blocco: MotivoBloccoCall[];
  pubblicata_at: string | null;
  chiusa_at: string | null;
  sospesa_at: string | null;
  sospeso_motivo: string | null;
  created_at: string | null;
  updated_at: string | null;
}

// ---- Proiezioni verso le altre aziende (whitelist del server) --------------------

/** Il creatore visto dagli altri: «Azienda anonima» con regione, sezione
 *  ATECO e classe dimensionale (dal Registro Imprese). Call con il nome di
 *  un'azienda verificata oggi (WP9): `anonima` false e `denominazione` = il
 *  nome del Registro Imprese, nient'altro. */
export interface CreatoreCall {
  anonima: boolean;
  denominazione: string;
  regione: string | null;
  ateco_sezione: { lettera: string; descrizione: string } | null;
  classe_dimensionale: DimensioneImpresa | null;
}

export interface BandoPubblicoCall {
  slug: string;
  titolo: string;
  scadenza: string | null;
}

/** Requisito CERCATO: niente copertura del creatore, niente citazione. */
export interface RequisitoPubblicoCall {
  etichetta: string;
  testo: string;
  criterio: CriterioPartner | null;
  ambito: AmbitoRequisitoCall;
}

export interface PosizionePubblicaCall {
  id: string;
  titolo: string;
  ruolo: RuoloPartner;
  tipi_soggetto: TipoSoggettoPartenariato[];
  competenze: string[];
  ateco_divisioni: string[];
  regioni: number[];
  regioni_nomi: string[];
  territorio_modalita: TerritorioModalitaCall;
  paesi: string[];
  dimensioni: DimensioneImpresa[];
  quota_ipotizzata_pct: string | null;
  numero: number;
  /** Etichette dei requisiti cercati («A», «C»). */
  requisiti: string[];
  note: string | null;
}

/** La call vista dalle altre aziende (e l'anteprima «come ti vedono»). */
export interface CallPubblica {
  id: string;
  stato: StatoCall;
  bando: BandoPubblicoCall;
  creatore: CreatoreCall;
  ruolo_creatore: RuoloCreatoreCall;
  forma_aggregazione_prevista: FormaPrevistaCall | null;
  titolo: string | null;
  descrizione_pubblica: string | null;
  profilo_partner_ideale: string | null;
  budget_fascia: BudgetFasciaCall | null;
  scadenza_call: string | null;
  visibilita: VisibilitaCall;
  esclusivita: boolean;
  pubblicata_at: string | null;
  requisiti: RequisitoPubblicoCall[];
  posizioni: PosizionePubblicaCall[];
}

/** Una call nelle liste (`GET /partenariati/call?vista=mie`). */
export interface CallCard {
  id: string;
  stato: StatoCall;
  titolo: string | null;
  bando: BandoPubblicoCall;
  creatore: CreatoreCall;
  ruolo_creatore: RuoloCreatoreCall;
  budget_fascia: BudgetFasciaCall | null;
  scadenza_call: string | null;
  pubblicata_at: string | null;
  posizioni_n: number;
  requisiti_cercati_n: number;
  /** Call dell'azienda attiva: solo allora `wizard_passo` e `updated_at`. */
  mia: boolean;
  wizard_passo: number | null;
  updated_at: string | null;
}

/** `GET /partenariati/call/{id}/anteprima`. */
export interface AnteprimaCall {
  call: CallPubblica;
  rilievi: RilievoCall[];
}

/** `GET /partenariati/call/{id}/versioni` (solo per il creatore). */
export interface VersioneCall {
  versione: number;
  created_at: string;
  snapshot: Record<string, unknown>;
}

// ---- Partenariati: matching, «Per te», bacheca, suggeriti (WP6) ------------------

/** Un requisito CERCATO dal proponente, per etichetta («A», «C»). */
export interface MatchRequisito {
  requisito_id: string;
  etichetta: string;
}

/** Voce «da verificare» (dato mancante o esito incerto): non esclude, pesa
 *  sul punteggio. `testo` è già in parole, dal server. */
export interface MatchAttenzione {
  codice: string;
  testo: string;
}

export interface MatchPosizione {
  id: string;
  titolo: string;
}

/** Codici di fascia (come `FasceBilancio`), mai importi. */
export interface MatchFasce {
  fatturato: string | null;
  patrimonio_netto: string | null;
  dipendenti: string | null;
  trend: string | null;
}

/** `MatchOut` del server (`partenariato_matching.MatchOut`): confronto
 *  deterministico tra una call e un'azienda, senza AI.
 *  - vista «proprio» (la tua azienda con una call): `dettaglio` con i tuoi
 *    valori nelle regole economiche;
 *  - vista «terzi» (il proponente che guarda un'azienda suggerita): solo fasce
 *    ed esiti, `dettaglio` null; per le aziende anonime la sola fascia di
 *    fatturato.
 *  Il punteggio non arriva mai: il server lo usa solo per ordinare. */
export interface MatchOut {
  copertura: { coperti: number; cercati: number };
  copre: MatchRequisito[];
  non_copre: MatchRequisito[];
  attenzione: MatchAttenzione[];
  posizioni_compatibili: MatchPosizione[];
  fasce: MatchFasce | null;
  /** Frase da template («Copri «A» e «C», che mancano al capofila.»). */
  spiegazione: string;
  dettaglio?: string[] | null;
}

export type VistaPartenariati =
  | "per-te"
  | "tutte"
  | "mie"
  | "salvate"
  | "candidature"
  | "conversazioni";
export type OrdineBacheca = "affinita" | "recenti" | "scadenza";

/** Filtri della bacheca (`vista=tutte`), nei searchParams. */
export interface FiltriBacheca {
  /** Slug del bando nel catalogo. */
  bando: string | null;
  /** Id della regione (lookups del catalogo). */
  regione: number | null;
  forma: FormaPrevistaCall | null;
  /** Ruolo della posizione offerta: capofila o partner. */
  ruolo: RuoloPartner | null;
  ordine: OrdineBacheca;
}

/** Una call nella bacheca, in «Per te» e tra le salvate
 *  (`GET /partenariati/call?vista=tutte|salvate`, `GET /partenariati/per-te`):
 *  la card del WP5 più il confronto con l'azienda attiva (null se non è
 *  idonea), lo stato «salvata» e i contatori. */
export interface CallBacheca extends CallCard {
  match: MatchOut | null;
  salvata: boolean;
  /** Candidature ricevute (0 fino alle candidature). */
  candidature_ricevute: number;
  /** Posti cercati nella call. */
  posti: number;
}

/** `GET /partenariati/per-te`: visibile anche senza visibilità come partner
 *  (`opt_in: false`), calcolato sui dati dell'azienda attiva. */
export interface PerTePage extends Page<CallBacheca> {
  opt_in: boolean;
}

/** La call vista da un'altra azienda (`GET /partenariati/call/{id}`): il
 *  confronto con l'azienda attiva (vista «proprio», con i tuoi numeri; null
 *  se non è compatibile), lo stato «salvata» e se l'azienda attiva è visibile
 *  come partner (per candidarsi serve). */
export interface CallPubblicaDettaglio extends CallPubblica {
  match: MatchOut | null;
  salvata: boolean;
  opt_in: boolean;
}

/** Un'azienda suggerita al proponente (`GET /partenariati/call/{id}/suggeriti`):
 *  pseudonimo per QUESTA call (mai l'id dell'azienda), profilo pubblico SENZA
 *  il codice pubblico (stabile tra le call: il server non lo manda) e
 *  confronto in vista «terzi». */
export interface PartnerSuggerito {
  pseudonimo: string;
  profilo: Omit<PartnerPubblico, "codice_pubblico">;
  match: MatchOut;
}

/** `GET /partenariati/riepilogo`: numeri per il badge del menu. Dal WP7
 *  anche le cose da fare (facoltativi: un server del WP6 non li manda). */
export interface RiepilogoPartenariati {
  per_te_nuove: number;
  call_attive: number;
  salvate: number;
  /** Inviti ricevuti ancora da decidere. */
  inviti_ricevuti?: number;
  /** Candidature spontanee ricevute sulle tue call, ancora da decidere. */
  candidature_da_decidere?: number;
  /** Messaggi non letti da te, su tutte le conversazioni dell'azienda. */
  messaggi_non_letti?: number;
}

/** `GET/PUT /me/partenariati/email-settings` (per utente). */
export interface PartnerEmailSettings {
  /** Riepilogo settimanale delle call per te. */
  digest_abilitato: boolean;
  /** Email sulle attività (inviti, candidature, risposte, messaggi). */
  eventi_abilitati: boolean;
}

// ---- Partenariati: candidature, inviti e chat (WP7) --------------------------------
// Una sola entità per candidature spontanee e inviti (`tipo`). Verso l'altra
// azienda mai l'id interno dell'azienda: l'azienda candidata o invitata ha lo
// pseudonimo della call, chi ha creato la call resta «Azienda anonima» (la
// rivelazione dell'identità è spenta, docs/partenariati.md §16).

export type TipoCandidatura = "candidatura" | "invito";
export type StatoCandidatura = "inviata" | "accettata" | "rifiutata" | "ritirata" | "scaduta";
/** Perché una candidatura o un invito si è chiuso senza una decisione. */
export type MotivoChiusuraCandidatura = "call_chiusa" | "ttl" | "opt_out" | "moderazione";
/** Filtro delle liste: quelle mandate dalla tua azienda o quelle ricevute. */
export type DirezioneCandidature = "inviate" | "ricevute";
/** Da che parte sta l'azienda attiva: ha creato la call (`creatore`) oppure
 *  è l'azienda candidata o invitata (`partner`). */
export type LatoPartenariato = "creatore" | "partner";

/** La call in breve, dentro candidature e conversazioni (`CallRiferimentoOut`:
 *  solo dati pubblici, mai il creatore). */
export interface CallRiferimento {
  id: string;
  titolo: string | null;
  bando: BandoPubblicoCall;
  stato: StatoCall;
  scadenza_call: string | null;
  visibilita: VisibilitaCall;
}

/** Profilo pubblico dell'azienda candidata o invitata, come nei suggeriti
 *  (senza il codice pubblico). */
export type ProfiloPartnerCall = Omit<PartnerPubblico, "codice_pubblico">;

/** L'azienda candidata o invitata vista dal creatore (`CandidatoOut`): lo
 *  pseudonimo della call e, solo nel dettaglio, il profilo pubblico finché
 *  è visibile. `disponibile` false: ha tolto la visibilità o non è più
 *  attiva («Azienda non più disponibile», senza dati). */
export interface CandidatoCandidatura {
  pseudonimo: string;
  disponibile: boolean;
  profilo?: ProfiloPartnerCall | null;
}

/** Requisito cercato che l'azienda dichiara di avere (una dichiarazione,
 *  non una verifica). */
export interface RequisitoDichiarato {
  requisito_id: string;
  etichetta: string;
}

/** `CandidaturaOut`: una candidatura o un invito come lo vede l'azienda
 *  attiva (`lato`). Dal lato del creatore l'altra azienda è solo `candidato`
 *  (pseudonimo) con la `valutazione` salvata all'invio (vista «terzi»: esiti
 *  e fasce) finché è disponibile; `compatibile` false = non risultava
 *  compatibile con i filtri della call. Dal lato partner nessun dato del
 *  creatore. `puo_decidere` / `puo_ritirare` li calcola il server (titolare,
 *  in attesa). Un invito in attesa oltre la scadenza arriva già `scaduta`. */
export interface Candidatura {
  id: string;
  tipo: TipoCandidatura;
  lato: LatoPartenariato;
  stato: StatoCandidatura;
  motivo_chiusura: MotivoChiusuraCandidatura | null;
  call: CallRiferimento;
  posizione: { id: string; titolo: string } | null;
  messaggio: string | null;
  requisiti_dichiarati: RequisitoDichiarato[];
  candidato: CandidatoCandidatura | null;
  valutazione: MatchOut | null;
  compatibile: boolean | null;
  motivo_rifiuto: string | null;
  scade_at: string | null;
  decisa_at: string | null;
  chiusa_at: string | null;
  created_at: string | null;
  /** Dopo l'accettazione. */
  conversazione_id: string | null;
  puo_decidere: boolean;
  puo_ritirare: boolean;
  /** Solo nella risposta dell'invio: candidature del mese (null = illimitate). */
  quota?: { usate: number; limite: number | null } | null;
  /** Solo nella risposta dell'invito: inviti in attesa sulla call. */
  inviti?: { attivi: number; massimo: number } | null;
}

/** `POST /partenariati/call/{id}/candidature` (solo il titolare). */
export interface CandidaturaInput {
  posizione_id: string;
  /** Da 50 a 2000 caratteri, senza contatti. */
  messaggio: string;
  /** Id dei requisiti visibili della call (cercati o validi per ogni membro). */
  requisiti_dichiarati: string[];
}

/** `POST /partenariati/call/{id}/inviti` (solo il titolare della call):
 *  l'azienda si indica con lo pseudonimo dei suggeriti. */
export interface InvitoInput {
  pseudonimo: string;
  posizione_id: string | null;
  /** Facoltativo, fino a 1000 caratteri, senza contatti. */
  messaggio: string | null;
}

/** `POST /partenariati/candidature/{id}/rifiuta`: motivo facoltativo. */
export interface RifiutoInput {
  motivo: string | null;
}

/** Filtri della lista `GET /partenariati/candidature`. */
export interface FiltriCandidature {
  direzione: DirezioneCandidature;
  stato: StatoCandidatura | null;
  /** Solo le candidature di una call (scheda della call del creatore). */
  call_id?: string | null;
}

/** `CandidaturaPropriaOut`: la candidatura (o l'invito) della tua azienda
 *  su una call di altri, dentro il dettaglio della call. Un invito in attesa
 *  oltre la scadenza arriva già come `scaduta`. `puo_decidere` (inviti) e
 *  `puo_ritirare` (candidature) li calcola il server per il titolare. */
export interface CandidaturaPropria {
  id: string;
  tipo: TipoCandidatura;
  stato: StatoCandidatura;
  posizione_id: string | null;
  scade_at: string | null;
  motivo_chiusura: MotivoChiusuraCandidatura | null;
  /** Il motivo del rifiuto è rivolto a te. */
  motivo_rifiuto: string | null;
  conversazione_id: string | null;
  created_at: string | null;
  decisa_at: string | null;
  /** Quando si è chiusa senza decisione (ritirata o scaduta). */
  chiusa_at?: string | null;
  puo_decidere: boolean;
  puo_ritirare: boolean;
}

/** Requisito visibile della call che chi si candida può dichiarare: l'id da
 *  mandare e l'etichetta (la stessa di `requisiti`). */
export interface RequisitoDichiarabile {
  id: string;
  etichetta: string;
}

/** La call vista da un'altra azienda (`GET /partenariati/call/{id}`):
 *  - `CallPubblicaDettaglioOut` (pubblico, candidata o invitata): confronto,
 *    «salvata», visibilità come partner, i requisiti dichiarabili e la
 *    propria candidatura (in attesa; una già chiusa solo finché la call è
 *    visibile a tutti, altrimenti la call è 404);
 *  - `CallVistaControparteOut` (`vista: "controparte"`, candidatura
 *    accettata): in più dettagli riservati e budget esatto, l'identità solo
 *    con la rivelazione accesa (oggi spenta); niente confronto né «salvata».
 *  Mai i bilanci di nessuno. */
export interface CallDettaglioAltraAzienda extends CallPubblica {
  match?: MatchOut | null;
  salvata?: boolean;
  opt_in?: boolean;
  candidatura?: CandidaturaPropria | null;
  /** Requisiti che si possono dichiarare candidandosi (non nella vista
   *  della controparte). */
  requisiti_dichiarabili?: RequisitoDichiarabile[];
  vista?: "controparte";
  dettagli_riservati?: string | null;
  /** Decimale come stringa. */
  budget_progetto_eur?: string | null;
  identita_rivelata?: boolean;
  identita?: IdentitaRivelata | null;
}

/** Un'azienda suggerita con lo stato del contatto sulla call
 *  (`stato_contatto`, WP7): `accettata`, `inviata` (candidatura o invito in
 *  attesa) o `rifiutata` (un tuo invito che ha rifiutato: non si reinvita);
 *  null se si può invitare. Facoltativo: un server del WP6 non lo manda. */
export interface PartnerSuggeritoContatto extends PartnerSuggerito {
  stato_contatto?: "accettata" | "inviata" | "rifiutata" | null;
}

export type StatoConversazione = "aperta" | "chiusa";

/** `IdentitaRivelataOut`: identità rivelata dopo l'accettazione (oggi
 *  spenta: sempre null). Dati d'impresa dal Registro Imprese, nome e ruolo
 *  del referente; mai l'email personale del referente. */
export interface IdentitaRivelata {
  ragione_sociale: string | null;
  sito_web: string | null;
  pec: string | null;
  referente_nome: string | null;
  referente_ruolo: "titolare" | "referente" | null;
}

/** L'altra azienda della conversazione (`ControparteOut`): per il creatore
 *  lo pseudonimo dell'azienda partner per quella call; per l'azienda
 *  partner nessun handle (è «chi ha creato la call»). `attiva` false:
 *  eliminata o archiviata (conversazione in sola lettura). */
export interface ControparteConversazione {
  pseudonimo: string | null;
  attiva: boolean;
}

/** Una conversazione nella lista (`GET /partenariati/conversazioni`). */
export interface ConversazioneCard {
  id: string;
  stato: StatoConversazione;
  lato: LatoPartenariato;
  call: CallRiferimento;
  controparte: ControparteConversazione;
  /** Messaggi dell'altra azienda che non hai ancora letto. */
  non_letti: number;
  ultimo_messaggio_at: string | null;
  created_at: string | null;
  chiusa_at: string | null;
  /** Chiusa, una delle due aziende non più attiva, o non sei il titolare. */
  sola_lettura: boolean;
}

/** `GET /partenariati/conversazioni/{id}` (solo le due aziende). */
export interface Conversazione extends ConversazioneCard {
  candidatura_id: string;
  identita_rivelata: boolean;
  identita: IdentitaRivelata | null;
  /** Fin dove hai letto (id dell'ultimo messaggio letto, 0 = nulla): serve
   *  al separatore «Nuovi messaggi». */
  letto_fino_a_id: number;
  puo_scrivere: boolean;
  /** Solo il titolare dell'azienda che ha creato la call. */
  puo_chiudere: boolean;
}

/** Un messaggio (`MessaggioOut`): immutabile; oscurato dalla moderazione →
 *  `testo` null e `nascosto` true. Mai chi l'ha scritto. */
export interface Messaggio {
  id: number;
  /** Scritto dalla tua azienda. */
  propria: boolean;
  testo: string | null;
  nascosto: boolean;
  created_at: string | null;
  /** Solo per i propri. */
  client_msg_id?: string | null;
}

/** `GET …/conversazioni/{id}/messaggi?dopo=&prima=&limite=`: in ordine di
 *  id crescente; `ha_altri` = ce ne sono altri oltre il blocco (più vecchi
 *  senza cursore o con `prima`, più nuovi con `dopo`). */
export interface MessaggiPage {
  items: Messaggio[];
  ha_altri: boolean;
}

/** `POST …/conversazioni/{id}/messaggi`: `client_msg_id` rende l'invio
 *  idempotente (un secondo invio con la stessa chiave non duplica). */
export interface MessaggioInput {
  testo: string;
  client_msg_id: string;
}

// ---- Partenariati: consorzio della call e validatore (WP8) ---------------------------
// Specchio di `backend/app/schemas/partenariato_consorzio.py` (fonte unica dei
// DTO). Id dei membri = `partner_call_membri.id` (mai l'id di un'azienda);
// quote e budget come STRINGHE decimali, il rapporto di copertura come numero.

/** Ruolo nel consorzio (nella UI «Entità affiliata» e «Partner associato»). */
export type RuoloMembro = "capofila" | "partner" | "affiliated_entity" | "associated_partner";
/** `proposto ⇄ confermato → uscito → proposto`. */
export type StatoMembro = "proposto" | "confermato" | "uscito";
export type EsitoVoce = "verde" | "rosso" | "grigio";
/** Chi ha stabilito la regola di una voce: il bando (voce confermata, con la
 *  sua citazione) o chi ha creato la call. */
export type FonteRegolaVoce = "bando" | "creatore";
export type CodiceVoce =
  | "regole"
  | "numero_partner"
  | "composizione"
  | "somma_quote"
  | "quota_partner"
  | "quota_categoria"
  | "quota_capofila"
  | "indipendenza"
  | "paesi_distinti"
  | "regola_finanziaria"
  | "media_pesata"
  | "esclusivita"
  | "vincolo_membro"
  | "vincolo_da_verificare"
  | "membri_attivi";
export type StatoDocumentoConsorzio = "da_fare" | "in_corso" | "fatto" | "non_applicabile";
/** Quando serve il documento. */
export type FaseDocumentoConsorzio =
  | "accordo_preliminare"
  | "domanda"
  | "concessione"
  | "prima_erogazione";
/** Da dove viene il documento: base (ogni forma), forma, richiesto dal bando. */
export type FonteDocumentoConsorzio = "base" | "forma" | "bando";

export interface PosizioneMembro {
  id: string;
  titolo: string;
}

/** `MembroOut`: un membro come lo vede chi guarda. `nome` = denominazione
 *  dichiarata per gli esterni, il nome della propria azienda, lo pseudonimo
 *  della call per gli altri membri in piattaforma (con i soli dati anonimi di
 *  `profilo`). `tipi_soggetto` solo per gli esterni (dichiarati dal creatore).
 *  Mai id di aziende, contatti o importi di bilancio. */
export interface MembroConsorzio {
  id: string;
  esterno: boolean;
  creatore: boolean;
  sei_tu: boolean;
  nome: string;
  pseudonimo: string | null;
  profilo: ProfiloPartnerCall | null;
  paese: string | null;
  tipi_soggetto: TipoSoggettoPartenariato[];
  ruolo: RuoloMembro;
  posizione: PosizioneMembro | null;
  quota_percentuale: string | null;
  stato: StatoMembro;
  confermato_at: string | null;
  puo_modificare: boolean;
  puo_confermare: boolean;
  puo_uscire: boolean;
}

/** Origine della regola di una voce: `citazione` solo per le voci del bando
 *  confermate con il passaggio ritrovato. */
export interface RegolaOrigineVoce {
  fonte: FonteRegolaVoce;
  citazione: CitazioneCall | null;
}

/** Esito di una voce su un membro (sulle fasce per gli altri membri, sui
 *  propri numeri per te). */
export interface EsitoMembroVoce {
  membro_id: string;
  esito: EsitoVoce;
  dichiarato: boolean;
}

/** `VoceOut`: una voce della checklist. `dettaglio_privato` esiste solo per i
 *  tuoi dati; `membri_coinvolti` = membri per cui la voce non è in regola (o
 *  che la determinano). */
export interface VoceValidazione {
  id: string;
  codice: CodiceVoce;
  esito: EsitoVoce;
  titolo: string;
  dettaglio_pubblico: string;
  dettaglio_privato: string | null;
  regola: RegolaOrigineVoce | null;
  membri_coinvolti: string[];
  esiti_membri: EsitoMembroVoce[];
  dichiarato: boolean;
}

export interface RiepilogoValidazione {
  verde: number;
  rosso: number;
  grigio: number;
}

/** `ValidazioneOut`: rosso se c'è un rosso, altrimenti grigio se c'è un
 *  grigio, altrimenti verde. */
export interface ValidazioneConsorzio {
  esito: EsitoVoce;
  voci: VoceValidazione[];
  riepilogo: RiepilogoValidazione;
}

/** `CellaMatriceOut`: copertura di un requisito da parte di un membro;
 *  `testo_privato` solo nella tua colonna; `si_applica` falso per i
 *  requisiti «di ogni membro» sui partner associati. */
export interface CellaMatrice {
  membro_id: string;
  esito: EsitoCoperturaCall;
  fonte: "registro" | "bilanci" | "dichiarato" | null;
  testo: string;
  testo_privato: string | null;
  si_applica: boolean;
}

export interface RigaMatrice {
  requisito_id: string;
  etichetta: string;
  testo: string | null;
  ambito: AmbitoRequisitoCall;
  cercato: boolean;
  esito: EsitoVoce;
  celle: CellaMatrice[];
}

/** `MatriceOut`: requisiti × membri; `copertura_gap_ratio` = requisiti
 *  cercati coperti / cercati (null senza requisiti cercati). */
export interface MatriceCoperturaConsorzio {
  membri: string[];
  righe: RigaMatrice[];
  copertura_gap_ratio: number | null;
}

/** `DocumentoConsorzioOut`: documento della checklist per forma con il suo
 *  stato (lo cambia solo il creatore). */
export interface DocumentoConsorzio {
  codice: string;
  titolo: string;
  fase: FaseDocumentoConsorzio;
  obbligatorio: boolean;
  nota: string | null;
  fonte: FonteDocumentoConsorzio;
  stato: StatoDocumentoConsorzio;
  note: string | null;
  updated_at: string | null;
}

/** `BudgetOut`: fascia pubblica ed esatto riservato (null per chi non lo vede). */
export interface BudgetConsorzio {
  fascia: BudgetFasciaCall | null;
  esatto: string | null;
  modificabile: boolean;
}

/** `GET /partenariati/call/{id}/consorzio` (creatore e controparti). */
export interface Consorzio {
  membri: MembroConsorzio[];
  validazione: ValidazioneConsorzio;
  matrice: MatriceCoperturaConsorzio;
  documenti: DocumentoConsorzio[];
  budget: BudgetConsorzio;
  forma: FormaAggregazione | null;
  editable: boolean;
  sei_creatore: boolean;
  /** Titolare dell'azienda creatrice e call in uno stato in cui il consorzio
   *  si modifica (pubblicata, scaduta, completata): esterni e documenti. */
  modificabile: boolean;
  validazione_at: string | null;
  membri_max: number;
}

/** `PUT …/consorzio/membri/{mid}` (solo il creatore): i tre campi insieme. */
export interface MembroAggiornaInput {
  posizione_id: string | null;
  ruolo: RuoloMembro;
  quota_percentuale: string | null;
}

/** `POST …/consorzio/membri/{mid}/conferma`: ruolo, posizione e quota COME LI
 *  MOSTRA la pagina. Se nel frattempo sono cambiati il server risponde 409
 *  `membro_modificato` (si rilegge il consorzio e si conferma di nuovo). */
export interface MembroConfermaInput {
  ruolo: RuoloMembro;
  posizione_id: string | null;
  quota_percentuale: string | null;
}

/** `POST …/consorzio/esterni` e `PUT …/consorzio/esterni/{mid}` (solo il
 *  creatore): membro non in piattaforma, con i dati che dichiari tu. */
export interface EsternoInput {
  denominazione: string;
  /** ISO 3166-1 alpha-2. */
  paese: string;
  /** Da 1 a 5. */
  tipi_soggetto: TipoSoggettoPartenariato[];
  ruolo: RuoloMembro;
  posizione_id: string | null;
  quota_percentuale: string | null;
}

/** `PUT …/consorzio/budget` (solo il creatore). */
export interface BudgetConsorzioInput {
  budget_fascia: BudgetFasciaCall;
  budget_progetto_eur: string | null;
}

/** `PUT …/consorzio/documenti/{codice}` (solo il creatore). */
export interface DocumentoStatoInput {
  stato: StatoDocumentoConsorzio;
  /** Fino a 500 caratteri. */
  note: string | null;
}

// ---- WP9: consulto dalla call, moderazione DSA, admin e identità verificata --------

/** Stato della verifica dell'identità dell'azienda da parte della
 *  piattaforma (0041, decisione di Michele): sblocca il profilo con il nome,
 *  le call con il nome e la rivelazione dell'identità dopo un'accettazione
 *  (solo tra due aziende verificate). */
export type StatoIdentitaAzienda = "non_richiesta" | "richiesta" | "verificata" | "rifiutata";

/** Come l'admin ha verificato l'identità (obbligatorio con `verificata`). */
export type MetodoVerificaIdentita =
  | "telefonata_sede"
  | "documento_legale_rappresentante"
  | "pec"
  | "altro";

/** Perché il titolare non può chiedere la verifica adesso. */
export type MotivoVerificaNonRichiedibile =
  | "solo_titolare"
  | "dati_registro"
  | "gia_richiesta"
  | "gia_verificata";

/** `GET/POST /me/partner-profile/identita` (anche `identita.verifica` del
 *  profilo partner): la verifica dell'azienda attiva. `verificata` è
 *  l'identità FORTE di oggi (stato verificata e dati del Registro Imprese
 *  ancora coerenti): è quella che sblocca il nome. */
export interface VerificaIdentita {
  stato: StatoIdentitaAzienda;
  verificata: boolean;
  richiesta_at: string | null;
  verificata_at: string | null;
  puo_richiedere: boolean;
  motivo_non_richiedibile: MotivoVerificaNonRichiedibile | null;
}

/** `POST /me/partner-profile/identita`: la nota dice come preferisci essere
 *  contattato (facoltativa, al massimo 500 caratteri, niente dati personali
 *  obbligatori). */
export interface VerificaIdentitaInput {
  nota: string | null;
}

/** Dati del Registro Imprese per la verifica (recapiti della SEDE dal
 *  registro, mai quelli inseriti dall'utente). */
export interface RegistroIdentitaAdmin {
  denominazione: string | null;
  partita_iva: string | null;
  stato_impresa: string | null;
  comune: string | null;
  provincia: string | null;
  pec: string | null;
  telefono: string | null;
}

/** Riga della coda admin delle verifiche (`GET /admin/partenariati/identita`). */
export interface IdentitaAdmin {
  company_profile_id: string;
  ragione_sociale: string | null;
  denominazione_registro: string | null;
  partita_iva: string | null;
  stato: StatoIdentitaAzienda;
  metodo: MetodoVerificaIdentita | null;
  richiesta_at: string | null;
  verificata_at: string | null;
  aggiornato_at: string | null;
  /** Nota del titolare all'ultima richiesta. */
  nota: string | null;
  titolare: { nome: string | null; email: string | null } | null;
  /** Dati del registro coerenti (T5): altrimenti non si verifica. */
  registro_ok: boolean;
  registro_motivo: MotivoIdentitaPartner | null;
  registro: RegistroIdentitaAdmin;
}

/** Filtro della coda delle verifiche (`tutte` = qualunque stato). */
export type FiltroIdentitaAdmin = StatoIdentitaAzienda | "tutte";

/** `POST /admin/partenariati/identita/{company_id}/decidi`. */
export interface IdentitaDecisioneInput {
  esito: "verificata" | "rifiutata";
  /** Obbligatorio con `verificata`, ignorato con `rifiutata`. */
  metodo: MetodoVerificaIdentita | null;
  /** Al massimo 500 caratteri, mai dati personali oltre il necessario. */
  nota: string | null;
}

/** `POST /admin/partenariati/identita/{company_id}/revoca` (motivo 1..500). */
export interface IdentitaRevocaInput {
  motivo: string;
}

/** Esito di una decisione o di una revoca dell'admin. */
export interface IdentitaEsito {
  company_profile_id: string;
  stato: StatoIdentitaAzienda;
  metodo: MetodoVerificaIdentita | null;
  verificata_at: string | null;
  modificato: boolean;
}

/** Decisione motivata su una segnalazione, coerente con l'oggetto (call →
 *  `call_sospesa`, messaggio → `contenuto_rimosso`, profilo →
 *  `profilo_sospeso`, oppure `nessuna_azione`). */
export type DecisioneSegnalazione =
  | "nessuna_azione"
  | "contenuto_rimosso"
  | "call_sospesa"
  | "profilo_sospeso";

export type EsitoRicorso = "confermata" | "riformata";

/** Il ricorso di una segnalazione (uno solo, entro 6 mesi dalla decisione).
 *  `testo` solo a chi l'ha presentato e all'admin. */
export interface RicorsoSegnalazione {
  da: "autore" | "segnalante";
  testo: string | null;
  at: string | null;
  esito: EsitoRicorso | null;
  motivazione: string | null;
  deciso_at: string | null;
}

/** `GET /partenariati/segnalazioni/{id}`: la segnalazione vista da chi l'ha
 *  fatta o dall'azienda autrice del contenuto (solo se una restrizione l'ha
 *  riguardata). Mai l'identità dell'altra parte. */
export interface SegnalazioneEsito {
  id: string;
  /** Codice breve (quello della notifica di ricezione). */
  codice: string;
  ruolo: "segnalante" | "autore";
  oggetto_tipo: OggettoSegnalazione;
  motivo: MotivoSegnalazione;
  stato: StatoSegnalazione;
  created_at: string | null;
  /** Solo a chi ha segnalato: la sua descrizione. */
  descrizione: string | null;
  decisione: DecisioneSegnalazione | null;
  /** La decisione dopo il ricorso: un ricorso accolto la ribalta. Per
   *  l'autore di un contenuto sospeso dal ricorso di chi aveva segnalato è la
   *  restrizione, mentre `decisione` resta `nessuna_azione`. */
  decisione_effettiva: DecisioneSegnalazione | null;
  motivazione: string | null;
  deciso_at: string | null;
  /** Solo all'autore: la motivazione formale (statement of reasons). */
  sor_testo: string | null;
  ricorso: RicorsoSegnalazione | null;
  /** Chi guarda può presentare il ricorso adesso. */
  ricorso_possibile: boolean;
  /** Termine del ricorso, per chi ne ha diritto. */
  ricorso_entro: string | null;
  /** Chi guarda può agire (il segnalante; per l'autore solo il titolare). */
  editable: boolean;
}

/** `POST /partenariati/segnalazioni/{id}/ricorso` (20..2000 caratteri). */
export interface RicorsoInput {
  testo: string;
}

/** Filtro della coda admin: `aperte` = ricevute, in esame e con un ricorso
 *  da decidere (default), `tutte` = qualunque stato. */
export type FiltroCodaSegnalazioni = "aperte" | StatoSegnalazione | "tutte";

/** Riga della coda admin (`GET /admin/partenariati/segnalazioni`). Lo
 *  snapshot è ciò che il segnalante vedeva quando ha segnalato; mai id di
 *  utenti. */
export interface SegnalazioneAdmin {
  id: string;
  codice: string;
  oggetto_tipo: OggettoSegnalazione;
  oggetto_id: string;
  motivo: MotivoSegnalazione;
  descrizione: string;
  contenuto_snapshot: Record<string, unknown>;
  stato: StatoSegnalazione;
  created_at: string | null;
  autore: { company_profile_id: string; ragione_sociale: string | null } | null;
  decisione: DecisioneSegnalazione | null;
  /** La decisione dopo il ricorso (vedi `SegnalazioneEsito`). */
  decisione_effettiva: DecisioneSegnalazione | null;
  motivazione: string | null;
  /** Lo statement of reasons inviato all'autore (anche quello della
   *  restrizione nata dal ricorso di chi aveva segnalato). */
  sor_testo: string | null;
  deciso_at: string | null;
  ricorso_entro: string | null;
  ricorso: RicorsoSegnalazione | null;
  /** Effetto sull'oggetto dell'ultima operazione (solo nelle risposte alle
   *  POST): applicato, gia_applicato, annullato, mantenuto, … */
  effetto: string | null;
}

/** `POST /admin/partenariati/segnalazioni/{id}/decidi` e `…/anteprima`
 *  (motivazione 20..2000). */
export interface DecisioneSegnalazioneInput {
  decisione: DecisioneSegnalazione;
  motivazione: string;
}

/** `POST /admin/partenariati/segnalazioni/{id}/anteprima`: la motivazione
 *  formale che riceverebbe l'autore (null con `nessuna_azione`). */
export interface AnteprimaStatement {
  versione: string;
  testo: string | null;
}

/** `POST /admin/partenariati/segnalazioni/{id}/ricorso/decidi` (20..2000). */
export interface DecisioneRicorsoInput {
  esito: EsitoRicorso;
  motivazione: string;
}

/** Un messaggio del contesto di una segnalazione: dell'azienda autrice del
 *  messaggio segnalato o dell'altra (mai id di utenti o aziende). */
export interface MessaggioContesto {
  id: number;
  lato: "autore" | "altra";
  testo: string | null;
  oscurato: boolean;
  segnalato: boolean;
  created_at: string | null;
}

/** `GET /admin/partenariati/segnalazioni/{id}/contesto?completo=&motivazione=`:
 *  ±10 messaggi o, con una motivazione (20..2000) registrata, la
 *  conversazione intera. */
export interface ContestoSegnalazione {
  completo: boolean;
  messaggi: MessaggioContesto[];
  /** La finestra non arriva all'inizio / alla fine della conversazione. */
  altri_prima: boolean;
  altri_dopo: boolean;
  /** La conversazione intera supera il tetto di lettura. */
  troncato: boolean;
}

/** Oggetto che l'admin sospende o ripristina direttamente. */
export type OggettoModerazione = OggettoSegnalazione;

/** `POST /admin/partenariati/{oggetto_tipo}/{id}/sospendi|ripristina`
 *  (motivazione 20..2000). */
export interface SospensioneInput {
  motivazione: string;
}

export interface EsitoSospensione {
  oggetto_tipo: OggettoSegnalazione;
  oggetto_id: string;
  esito: string;
  stato: string | null;
  modificato: boolean;
}

/** Riga dell'elenco admin delle call (`GET /admin/partenariati/call`). */
export interface CallAdmin {
  id: string;
  titolo: string | null;
  stato: StatoCall;
  stato_prima_sospensione: StatoCall | null;
  visibilita: VisibilitaCall | null;
  anonima: boolean | null;
  bando: { id: number | null; slug: string | null; titolo: string | null };
  creatore: { company_profile_id: string; ragione_sociale: string | null };
  pubblicata_at: string | null;
  scadenza_call: string | null;
  created_at: string | null;
  sospesa_at: string | null;
  sospeso_motivo: string | null;
  validazione_esito: EsitoVoce | null;
  /** Candidature spontanee e inviti (in qualunque stato). */
  candidature: number;
  inviti: number;
  /** Membri del consorzio non usciti. */
  membri: number;
  segnalazioni_aperte: number;
}

/** Tasso di accettazione di un tipo (candidature spontanee o inviti). */
export interface AccettazionePartenariati {
  accettate: number;
  rifiutate: number;
  /** accettate / (accettate + rifiutate), tra 0 e 1; null senza decisioni. */
  tasso: number | null;
}

/** `GET /admin/partenariati/metriche?da=&a=` (coorte delle call pubblicate
 *  nel periodo, giorni Europe/Rome, estremi compresi). */
export interface MetrichePartenariati {
  da: string;
  a: string;
  call_pubblicate: number;
  candidature: number;
  inviti: number;
  candidature_per_call: number | null;
  /** Call pubblicate da almeno 30 giorni (le sole osservabili). */
  call_osservabili_30_giorni: number;
  call_con_candidatura_30_giorni: number;
  /** Già in percentuale (0..100). */
  percentuale_call_con_candidatura_30_giorni: number | null;
  accettazione: Partial<Record<"candidatura" | "invito", AccettazionePartenariati>> | null;
  ore_mediane_prima_candidatura: number | null;
  /** Media dei requisiti cercati coperti, tra 0 e 1. */
  copertura_media_gap: number | null;
  consorzi_validati: number;
  consorzi_validati_verde: number;
}

export type ValutaCosti = "EUR" | "USD";

export interface VoceCostoPartenariati {
  provider: string;
  service: string;
  outcome: string;
  valuta: ValutaCosti;
  eventi: number;
  /** Centesimi nella valuta della voce. */
  cost_cents: number;
}

export interface TotaleCostoPartenariati {
  valuta: ValutaCosti;
  eventi: number;
  cost_cents: number;
}

/** `GET /admin/partenariati/costi?da=&a=`: per provider, servizio, esito e
 *  valuta; i totali sono per valuta (mai sommati tra EUR e USD). */
export interface CostiPartenariati {
  da: string;
  a: string;
  voci: VoceCostoPartenariati[];
  totali: TotaleCostoPartenariati[];
}

/** `GET /admin/partenariati/estrazioni` (WP3): stato dell'estrazione delle
 *  regole per bando (mai output grezzo). */
export interface EstrazioneAdmin {
  bando_id: number;
  bando_slug: string;
  bando_titolo: string;
  stato: "in_corso" | "pronta" | "errore";
  esito: "estratta" | "nessun_segnale" | null;
  fase: FasePartenariato | null;
  modalita_effettiva: ModalitaPartenariato | null;
  model: string | null;
  cost_cents: number;
  input_tokens: number;
  output_tokens: number;
  estratta_at: string | null;
  verificata_at: string | null;
  ultima_esecuzione_at: string | null;
  errore_codice: string | null;
  tentativi_falliti: number;
  prossimo_tentativo_at: string | null;
  updated_at: string | null;
}

/** `POST /admin/partenariati/run`: esiti dei passi dello scheduler. */
export interface PartenariatiRun {
  giorno: string;
  riepilogo: Record<string, unknown>;
}

/** `GET /progettista/richieste/{id}/call`: la call del cliente per il
 *  progettista ASSEGNATO (ogni lettura è registrata; senza registrazione il
 *  server non risponde). È la vista del creatore a whitelist; nel consorzio
 *  le altre aziende in piattaforma sono solo «Azienda anonima» con esiti e
 *  fasce (niente pseudonimo né profilo), nessuna azione possibile. */
export interface CallVistaProgettista {
  richiesta_id: string;
  id: string;
  stato: StatoCall;
  motivo_chiusura: MotivoChiusuraCall | null;
  bando: BandoCall;
  ruolo_creatore: RuoloCreatoreCall;
  forma_aggregazione_prevista: FormaPrevistaCall | null;
  anonima: boolean;
  titolo: string | null;
  descrizione_pubblica: string | null;
  dettagli_riservati: string | null;
  profilo_partner_ideale: string | null;
  budget_fascia: BudgetFasciaCall | null;
  budget_progetto_eur: string | null;
  quota_creatore_pct: string | null;
  scadenza_call: string | null;
  visibilita: VisibilitaCall;
  regole_partenariato: RegoleCallSnapshot | null;
  regole_confermate_at: string | null;
  esclusivita: boolean;
  posizioni: PosizioneCall[];
  requisiti: RequisitoCall[];
  riepilogo: RiepilogoGap;
  partenariato: GapCall["partenariato"];
  pubblicata_at: string | null;
  chiusa_at: string | null;
  /** null per una bozza (il consorzio nasce alla pubblicazione). */
  consorzio: Consorzio | null;
}

// ---- WP10: bozze AI dei documenti del partenariato -----------------------------------

export type TipoBozzaDocumento = "lettera_intenti" | "nda" | "term_sheet";

/** `pending` → `ready | error`: una sola bozza in preparazione per azienda ×
 *  call × tipo. */
export type StatoBozzaDocumento = "pending" | "ready" | "error";

export interface SezioneBozzaDocumento {
  titolo: string;
  testo: string;
}

/** Una bozza di documento dell'azienda attiva su una call (whitelist del
 *  server). Titolo, sezioni e note solo quando è pronta, `errore` (il
 *  messaggio per l'utente) solo quando non è riuscita. Le aziende compaiono
 *  con segnaposto («[Capofila]», «[Partner 1]», …) che l'utente sostituisce
 *  nel documento finale; il nome della propria azienda solo se l'ha chiesto.
 *  `avvisi` = ciò che il controllo automatico ha tolto o sostituito. */
export interface BozzaDocumento {
  id: string;
  tipo: TipoBozzaDocumento;
  stato: StatoBozzaDocumento;
  avviata_at: string;
  conclusa_at: string | null;
  errore: string | null;
  includi_nome_azienda: boolean;
  titolo: string | null;
  sezioni: SezioneBozzaDocumento[];
  note_per_l_utente: string[];
  avvisi: string[];
  /** Il testo fisso della piattaforma, mai generato dal modello. */
  disclaimer: string;
}

/** `GET /partenariati/call/{id}/bozze`: le bozze dell'azienda attiva sulla
 *  call, dalla più recente. `editable` = può avviarne di nuove (titolare);
 *  il contatore del mese sta in `/me/entitlements.partenariati.bozze_mese`. */
export interface BozzeCall {
  bozze: BozzaDocumento[];
  editable: boolean;
  disclaimer: string;
}

/** `POST /partenariati/call/{id}/bozze` (solo il titolare, 202). */
export interface AvviaBozzaInput {
  tipo: TipoBozzaDocumento;
  /** Il nome della PROPRIA azienda nella bozza: solo se scelto (default no). */
  includi_nome_azienda: boolean;
}
