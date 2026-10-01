/** Monitoraggio del catalogo bandi (`GET /admin/catalogo/monitoraggio`, solo
 *  admin). Il backend chiama l'interfaccia di monitoraggio del catalogo e
 *  restituisce l'esito della chiamata più, se riuscita, la busta così com'è.
 *  La busta è tollerante: ogni campo può mancare o valere null, e nelle
 *  enumerazioni possono comparire valori nuovi senza preavviso (per questo sono
 *  `string`): il pannello mostra solo ciò che conosce e non interpreta il resto. */

/** Esito della lettura del monitoraggio, deciso dal backend. */
export type StatoAccessoMonitoraggio =
  | "ok"
  | "non_configurato"
  | "chiave_non_valida"
  | "non_disponibile"
  | "accesso_db_non_valido"
  | "non_raggiungibile"
  | "formato_non_supportato";

export interface MonitoraggioCatalogo {
  stato_accesso: StatoAccessoMonitoraggio;
  /** Quando il backend ha letto (o deciso di non chiamare): ISO 8601. */
  letto_at: string;
  /** Solo con `stato_accesso` «ok». */
  busta: BustaMonitoraggio | null;
}

export interface BustaMonitoraggio {
  versione?: number | null;
  generato_at?: string | null;
  calcolato_at?: string | null;
  /** Riferimento per il ritardo: quando il riepilogo è stato scritto. */
  aggiornato_at?: string | null;
  minuti_dal_calcolo?: number | null;
  /** Riepilogo più vecchio della soglia o assente: banner «Dati non aggiornati». */
  in_ritardo?: boolean | null;
  /** Solo informativo. */
  orologio_disallineato?: boolean | null;
  /** null finché il primo riepilogo non è stato calcolato. */
  riepilogo?: RiepilogoMonitoraggio | null;
}

export interface RiepilogoMonitoraggio {
  /** `ok` | `attenzione` | `guasto` (o un valore nuovo). */
  stato?: string | null;
  segnali?: SegnaleMonitoraggio[] | null;
  /** Parti che oggi non si possono osservare: non cambiano lo stato. */
  non_misurati?: string[] | null;
  produttore?: ProduttoreMonitoraggio | null;
  giri?: GiroMonitoraggio[] | null;
  /** Arrivano ma il pannello non li mostra. */
  controlli?: ControlloMonitoraggio[] | null;
  /** Arrivano ma il pannello non li mostra. */
  lavorazioni?: LavorazioneMonitoraggio[] | null;
  ingresso?: IngressoMonitoraggio | null;
  eventi?: EventiMonitoraggio | null;
  /** null finché la verifica dello stato non è attiva; poi un oggetto con i
   *  conteggi (`in_apertura` e `aperto`: `{motivo: n}`). Forma libera. */
  da_verificare?: Record<string, unknown> | null;
  job_orario?: JobOrarioMonitoraggio | null;
}

export interface SegnaleMonitoraggio {
  /** Identificatore stabile (anche sconosciuto): non si mostra. */
  codice?: string | null;
  /** `allarme` | `avviso` (o un valore nuovo). */
  livello?: string | null;
  /** Frase italiana fissa, da mostrare così com'è. */
  testo?: string | null;
  /** Da quando il segnale è acceso. */
  dal?: string | null;
  /** Il significato dipende dal codice: il pannello non la mostra. */
  misura?: number | null;
}

export interface ProduttoreMonitoraggio {
  ultimo_giro_at?: string | null;
  ore_dall_ultimo_giro?: number | null;
  giri_24h?: number | null;
  riavvii_24h?: number | null;
  /** `attivo` | `non_attivo` | `non_misurato` (o un valore nuovo). */
  servizio?: string | null;
}

export interface GiroMonitoraggio {
  id?: string | number | null;
  /** `00` | `06` | `12` | `18` | `avvio` | `manuale` (o un valore nuovo). */
  giro?: string | null;
  avviato_at?: string | null;
  concluso_at?: string | null;
  durata_min?: number | null;
  /** `ok` | `errore` | `saltato` | `interrotto_per_tetto` (o un valore nuovo). */
  esito?: string | null;
  interrotto_per_tetto?: boolean | null;
  /** Nomi neutri dei passi non riusciti. */
  passi_non_ok?: string[] | null;
}

export interface ControlloMonitoraggio {
  avviato_at?: string | null;
  esito?: string | null;
  classificazioni?: number | null;
  classificazioni_fallite?: number | null;
  eventi_non_applicati?: number | null;
}

export interface LavorazioneMonitoraggio {
  nome?: string | null;
  da_min?: number | null;
  ttl_min?: number | null;
  stato?: string | null;
}

export interface IngressoMonitoraggio {
  fermi_in_ingresso?: number | null;
  fermi_in_lavorazione?: number | null;
  ultimo_bando_nuovo_at?: string | null;
}

export interface EventiMonitoraggio {
  ammessi_non_applicati?: number | null;
  proposte_7g?: number | null;
  in_attesa_pubblicazione?: number | null;
}

export interface JobOrarioMonitoraggio {
  ultimo_avvio_at?: string | null;
  /** `succeeded` | `failed` | `non_misurato` (o un valore nuovo). */
  ultimo_esito?: string | null;
  ultimo_ok_at?: string | null;
  falliti_24h?: number | null;
}
