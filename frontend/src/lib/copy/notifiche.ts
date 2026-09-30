/** Campanella e pannello notifiche in-app. */
export const NOTIFICHE_COPY = {
  apri: "Notifiche",
  apriConNonLette: (n: number) => `Notifiche: ${n} non lett${n === 1 ? "a" : "e"}`,
  titoloPannello: "Notifiche",
  vuoto: "Nessuna notifica.",
  erroreCaricamento: "Impossibile caricare le notifiche.",
  segnaTutteLette: "Segna tutte come lette",
  // Centro alert (pagina /app/notifiche).
  vediTutte: "Vedi tutte",
  titoloPagina: "Notifiche",
  sottotitoloPagina: "Tutti gli avvisi della piattaforma, dai bandi compatibili agli aggiornamenti.",
  filtroTutte: "Tutte le aziende",
  filtroAria: "Filtra le notifiche per azienda",
  vuotoAzienda: "Nessuna notifica per questa azienda.",
  /** Link di una notifica (`?azienda=<id>`) verso un'azienda che l'utente non
   *  vede tra le sue: la pagina resta sull'azienda attiva. */
  aziendaNonGestita: "Questa notifica riguarda un'azienda che non gestisci.",
} as const;
