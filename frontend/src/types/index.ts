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

export type StatoBando = "aperto" | "chiuso" | "in apertura prossimamente";

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
}

export interface ContenutoSegment {
  kind: string;
  text?: string;
  href?: string;
}

export interface ContenutoSection {
  type: string;
  text?: string;
  segments?: ContenutoSegment[];
  items?: Array<string | { segments?: ContenutoSegment[]; text?: string }>;
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

export interface Plan {
  id: number;
  nome: string;
  slug: string;
  descrizione: string | null;
  prezzo_annuale: string | number;
  ai_check: number;
  alert_attivo: boolean;
  alert_giorni_preavviso: number | null;
  num_account_aziendali: number;
  ordering: number;
  is_active: boolean;
  updated_at: string | null;
}

export type UserRole = "admin" | "cliente";

export interface Profile {
  id: string;
  email: string;
  nome: string | null;
  cognome: string | null;
  azienda: string | null;
  telefono: string | null;
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

export interface Me {
  profile: Profile;
  subscription: Subscription | null;
  family: MeFamily | null;
  plan_switch_adjustment?: PlanSwitchAdjustment | null;
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
