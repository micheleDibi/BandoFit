import type { CallAggiornaInput, CallVistaCreatore } from "../../types";

/** Props comuni dei passi del wizard (la call è già salvata come bozza). */
export interface PassoProps {
  call: CallVistaCreatore;
  /** Va al passo successivo (dopo il salvataggio). */
  onAvanti: () => void;
  onIndietro: () => void;
  /** Il passo ha modifiche non salvate (per l'avviso all'uscita). */
  onDirty: (dirty: boolean) => void;
  /** Va a un passo qualsiasi (dai motivi di blocco della pubblicazione). */
  onVai: (passo: number) => void;
}

/** In bozza il wizard ricorda l'ultimo passo raggiunto (`wizard_passo`), così
 *  la call si riprende da lì; dopo la pubblicazione il campo non si manda (il
 *  server lo rifiuterebbe: non è nella whitelist). */
export function avanzamento(call: CallVistaCreatore, prossimo: number): CallAggiornaInput {
  return call.stato === "bozza" && call.wizard_passo < prossimo ? { wizard_passo: prossimo } : {};
}

// Decimali del server («1200000.00»): si confrontano come numeri.
const DECIMALI = new Set(["budget_progetto_eur", "quota_creatore_pct"]);

/** Solo i campi davvero cambiati (confronto per valore). */
export function soloCambiati(
  call: CallVistaCreatore,
  campi: CallAggiornaInput,
): CallAggiornaInput {
  const out: Record<string, unknown> = {};
  for (const [chiave, valore] of Object.entries(campi)) {
    const attuale = (call as unknown as Record<string, unknown>)[chiave] ?? null;
    const nuovo = valore ?? null;
    const uguali =
      DECIMALI.has(chiave) && nuovo !== null && attuale !== null
        ? Number(nuovo) === Number(attuale)
        : nuovo === attuale;
    if (!uguali) out[chiave] = valore;
  }
  return out as CallAggiornaInput;
}

export const vuoto = (o: object) => Object.keys(o).length === 0;
