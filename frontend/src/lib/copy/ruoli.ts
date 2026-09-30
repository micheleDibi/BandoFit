import type { UserRole } from "../../types";

/** Etichette dei ruoli utente: compaiono nel badge della lista admin, nel
 *  filtro, nel select di cambio ruolo e nel dialog di conferma. */
export const RUOLO_LABELS: Record<UserRole, string> = {
  admin: "Admin",
  cliente: "Cliente",
  progettista: "Progettista",
};

/** Note del dialog di conferma cambio ruolo: cosa comporta la transizione.
 *  Parità admin: l'area progettista è di progettisti E amministratori, quindi
 *  si «perde» solo tornando cliente. */
export const ADMIN_RUOLO_COPY = {
  promozioneProgettista:
    "Avrà l'area progettista con un codice identificativo (assegnato ora, o riusato se già esistente), mantenendo tutte le funzionalità cliente.",
  nominaAdmin:
    "Come amministratore ha anche l'area progettista (stesse funzioni dei progettisti); il codice identificativo viene assegnato alla prima proposta inviata.",
  perditaAreaProgettista:
    "Perderà l'accesso all'area progettista. Il suo eventuale codice resta riservato: un futuro ritorno all'area lo riutilizzerà.",
} as const;
