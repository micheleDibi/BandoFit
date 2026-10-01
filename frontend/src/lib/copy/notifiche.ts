/** Notifiche in-app: la voce «Notifiche» della barra laterale (con il
 *  contatore delle non lette) e la pagina /app/notifiche. */
export const NOTIFICHE_COPY = {
  apri: "Notifiche",
  apriConNonLette: (n: number) => `Notifiche: ${n} non lett${n === 1 ? "a" : "e"}`,
  vuoto: "Nessuna notifica.",
  segnaTutteLette: "Segna tutte come lette",
  titoloPagina: "Notifiche",
  sottotitoloPagina: "Tutti gli avvisi della piattaforma, dai bandi compatibili agli aggiornamenti.",
  filtroTutte: "Tutte le aziende",
  filtroAria: "Filtra le notifiche per azienda",
  vuotoAzienda: "Nessuna notifica per questa azienda.",
  /** Prefisso per lo screen reader delle notifiche non lette (il punto è solo visivo). */
  nonLetta: "Non letta:",
  /** Link di una notifica (`?azienda=<id>`) verso un'azienda che l'utente non
   *  vede tra le sue: la pagina resta sull'azienda attiva. */
  aziendaNonGestita: "Questa notifica riguarda un'azienda che non gestisci.",
} as const;
