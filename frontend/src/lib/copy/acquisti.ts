/** Etichette degli stati di un acquisto: stato in parole dello storico utente
 *  (Abbonamento › Acquisti) e della vista admin pagamenti. */
export const PURCHASE_STATO_LABELS: Record<import("../../types").PurchaseStatus, string> = {
  in_attesa: "In attesa",
  pagato: "Pagato",
  fallito: "Fallito",
  scaduto: "Scaduto",
  annullato: "Annullato",
  gratuito: "Gratuito",
};

/** Etichette dei tipi di acquisto: filtro della vista admin pagamenti e badge
 *  degli acquisti d'origine amministrativa (cambio piano, accredito addon). */
export const PURCHASE_KIND_LABELS: Record<import("../../types").PurchaseKind, string> = {
  piano: "Piano",
  rinnovo: "Rinnovo",
  addon: "Add-on",
  cambio_admin: "Cambio amministratore",
  addon_admin: "Accredito addon",
};

/** Etichette dei movimenti del ledger addon (Abbonamento + I miei addon). */
export const ADDON_MOVIMENTO_LABELS: Record<import("../../types").AddonMovimentoTipo, string> = {
  purchase: "Acquisto",
  admin_grant: "Accredito dall'assistenza",
  consume: "Utilizzo",
  refund: "Rimborso",
  admin_revoke: "Rettifica",
};
