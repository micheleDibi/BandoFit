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
}

export interface Me {
  profile: Profile;
  subscription: Subscription | null;
}

export interface AdminUser {
  profile: Profile;
  subscription: Subscription | null;
}
