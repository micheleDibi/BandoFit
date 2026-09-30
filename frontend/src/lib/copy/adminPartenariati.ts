/** Pannello admin dei partenariati (WP9): schede, metriche e costi. */
export const ADMIN_PARTENARIATI_COPY = {
  schede: {
    segnalazioni: "Segnalazioni",
    identita: "Identità",
    call: "Call",
    metriche: "Metriche",
    costi: "Costi",
    estrazioni: "Estrazioni",
  },
  providers: {
    anthropic: "Anthropic (AI)",
    openapi: "openapi.it",
  } as Record<string, string>,
  servizi: {
    partenariato_estrazione: "Estrazione delle regole dei bandi",
    partner_profilo_ai: "Bozza del profilo partner",
    partner_call_posizioni: "Posizioni proposte per le call",
    partner_call_testi: "Testi proposti per le call",
    partner_bozza: "Bozze dei documenti",
    "IT-advanced": "Bilanci (IT-advanced)",
    "bilancio-ottico": "Bilancio ufficiale",
    "bilancio-ottico-stato": "Stato del bilancio ufficiale",
    "visure-impresa": "Visura dell'impresa",
  } as Record<string, string>,
  esiti: {
    success: "Riuscite",
    error: "Errore",
    timeout_unknown: "Esito incerto",
  } as Record<string, string>,
  notaValute:
    "Gli importi sono nella valuta del fornitore: euro per openapi.it, dollari per Anthropic. Non si sommano tra loro.",
  notaMetriche:
    "Le metriche riguardano le call pubblicate nel periodo; candidature e inviti di quelle call contano in qualunque momento.",
  motivazioneMin: 20,
  motivazioneMax: 2000,
  /** Sospensione d'ufficio: la motivazione arriva per intero all'azienda
   *  nella notifica (e resta sul contenuto sospeso). */
  motivazioneSospensioneMax: 500,
  motivazioneSospensioneAiuto:
    "La legge l'azienda nella notifica, insieme a come contestare la decisione: niente dati di altre persone.",
  motivoRevocaMax: 500,
} as const;
