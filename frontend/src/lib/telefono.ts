/** Validazione locale del numero di telefono (memorizzazione in E.164).
 *
 *  Serve solo il FORMATO (niente OTP): normalizza l'input di un utente
 *  italiano («347 1234567» → «+393471234567»; i fissi MANTENGONO lo zero:
 *  «02 5551234» → «+39025551234») e accetta i numeri internazionali con
 *  prefisso esplicito («+» o «00»).
 *
 *  Duplica `backend/app/services/telefono.py`: il backend resta l'autorità
 *  e rifiuta comunque con 422. Se cambia lì, cambiare qui — i vettori di
 *  test sono gli stessi. */

/** Porta l'input in forma E.164 (best-effort, non valida). */
export function normalizeTelefono(input: string): string {
  let cleaned = input.trim().replace(/[\s./()-]/g, "");
  if (cleaned.startsWith("00")) cleaned = "+" + cleaned.slice(2);
  // Default Italia. Lo zero iniziale dei fissi NON si rimuove:
  // l'E.164 italiano lo conserva (02… → +3902…).
  if (!cleaned.startsWith("+")) cleaned = "+39" + cleaned;
  return cleaned;
}

/** True se `value` (già normalizzato) è un E.164 plausibile. */
export function isValidTelefono(value: string): boolean {
  // E.164: prefisso paese che non inizia per 0, max 15 cifre totali.
  if (!/^\+[1-9]\d{5,14}$/.test(value)) return false;
  if (value.startsWith("+39")) {
    // Sanity-check per il default: 6-11 cifre nel numero nazionale.
    const nazionali = value.length - "+39".length;
    return nazionali >= 6 && nazionali <= 11;
  }
  return true;
}
