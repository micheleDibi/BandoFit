/** Validazione locale della partita IVA italiana.
 *
 *  Serve a distinguere due errori che l'utente vive in modo diverso:
 *  «la partita IVA non è valida» (lo sappiamo qui, senza chiamare nessuno) e
 *  «non è presente nel Registro Imprese» (lo dice l'API, 404). Senza checksum
 *  la seconda inghiottiva anche i refusi.
 *
 *  Duplica `validate_partita_iva` in `backend/app/services/openapi_mapping.py`:
 *  il backend resta l'autorità e rifiuta comunque con 400. Se cambia lì,
 *  cambiare qui — i vettori di test sono gli stessi. */

/** Toglie spazi e prefisso IT, come fa il validator Pydantic lato server. */
export function normalizePartitaIva(input: string): string {
  return input.trim().toUpperCase().replace(/^IT/, "").replace(/\s/g, "");
}

/** Checksum ufficiale (11 cifre, Luhn-like). Attende una P.IVA già normalizzata. */
export function isValidPartitaIva(piva: string): boolean {
  if (!/^\d{11}$/.test(piva)) return false;
  const digits = [...piva].map(Number);
  let odd = 0;
  for (let i = 0; i < 10; i += 2) odd += digits[i];
  let even = 0;
  for (let i = 1; i < 10; i += 2) {
    const doubled = digits[i] * 2;
    even += doubled > 9 ? doubled - 9 : doubled;
  }
  return (10 - ((odd + even) % 10)) % 10 === digits[10];
}
