/** Paesi (ISO 3166-1 alpha-2) per il select dell'anagrafica di fatturazione.
 *
 *  DATO condiviso (non copy): la lista serve al select E alla logica VIES,
 *  perciò vive in un modulo, non inline. Le etichette italiane arrivano da
 *  `Intl.DisplayNames("it")` (baseline dal 2021 su tutti i browser correnti):
 *  evita un array di ~250 nomi da mantenere a mano. Fallback al codice ISO se
 *  il costruttore non c'è. */

/** Codici alpha-2 (soli codici; le etichette dalle API Intl). Lista pragmatica
 *  ma ampia: tutti i membri UE + i paesi extra-UE più frequenti per un SaaS. */
export const PAESI_CODES: readonly string[] = [
  // UE-27
  "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
  "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
  "SE", "SI", "SK",
  // Extra-UE frequenti (EFTA, UK, resto d'Europa, altri comuni)
  "CH", "GB", "NO", "IS", "LI", "AL", "AD", "BA", "ME", "MK", "MD", "RS",
  "SM", "TR", "UA", "VA", "XK",
  "US", "CA", "AU", "NZ", "JP", "CN", "IN", "BR", "AR", "MX", "AE", "IL",
  "SG", "HK", "ZA",
];

/** UE-27. Il venditore è croato: il reverse charge vale per le aziende UE
 *  ≠ HR (Italia inclusa). Stessa lista di schemas/billing.py::PAESI_UE. */
export const PAESI_UE: ReadonlySet<string> = new Set([
  "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR",
  "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO",
  "SE", "SI", "SK",
]);

/** Rispecchia il gate VIES del backend (billing_service): azienda + paese UE
 *  ≠ HR (per la vendita domestica croata l'esito non cambia l'aliquota). */
export const viesApplicabile = (paese: string): boolean =>
  PAESI_UE.has(paese) && paese !== "HR";

let _displayNames: Intl.DisplayNames | null | undefined;

function displayNames(): Intl.DisplayNames | null {
  if (_displayNames === undefined) {
    try {
      _displayNames = new Intl.DisplayNames(["it"], { type: "region" });
    } catch {
      _displayNames = null; // browser antico: si mostra il codice
    }
  }
  return _displayNames;
}

/** Nome italiano del paese, o il codice se Intl non è disponibile. */
export function nomePaese(code: string): string {
  return displayNames()?.of(code) ?? code;
}

/** Codici ordinati per nome italiano, con l'Italia in testa (la maggioranza
 *  dei clienti; evita di farla cercare in mezzo alla lista). */
export function paesiOrdinati(): string[] {
  const collator = new Intl.Collator("it");
  const altri = PAESI_CODES.filter((c) => c !== "IT").sort((a, b) =>
    collator.compare(nomePaese(a), nomePaese(b)),
  );
  return ["IT", ...altri];
}
