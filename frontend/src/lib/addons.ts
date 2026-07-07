/** PUNTO DI ESTENSIONE del flusso di acquisto add-on.
 *
 * Oggi è uno stub: nessuna logica di pagamento. Quando il flusso reale sarà
 * pronto, va collegato QUI (ed esclusivamente qui): la UI passa sempre da
 * questa funzione, agganciata allo slug stabile dell'add-on, e reagisce al
 * risultato — collegare l'acquisto vero richiederà solo di riempire il corpo
 * (es. chiamata a POST /me/addons/{slug}/purchase) e ampliare l'esito.
 */
export interface PurchaseResult {
  /** false = flusso non ancora disponibile (la UI mostra il dialog «In arrivo»). */
  available: boolean;
}

export async function purchaseAddon(_slug: string): Promise<PurchaseResult> {
  return { available: false };
}
