/** PUNTO DI ESTENSIONE della richiesta di consulenza (piani e add-on
 * «su richiesta») — gemello di purchaseAddon in lib/addons.ts.
 *
 * Oggi è uno stub: il flusso di contatto reale (form in-app + email al team,
 * mailto o link esterno) arriverà in una fase successiva e andrà collegato
 * QUI (ed esclusivamente qui): la UI passa sempre da questa funzione con il
 * tipo e lo slug stabile dell'item, e reagisce al risultato. I piani e gli
 * add-on «su richiesta» restano comunque NON attivabili self-serve: il blocco
 * è applicato dal backend (cambio piano e registrazione).
 */
export interface ConsultationRequest {
  kind: "plan" | "addon";
  slug: string;
}

export interface ConsultationResult {
  /** false = flusso non ancora disponibile (la UI mostra il dialog «In arrivo»). */
  available: boolean;
}

export async function requestConsultation(
  _req: ConsultationRequest,
): Promise<ConsultationResult> {
  return { available: false };
}
