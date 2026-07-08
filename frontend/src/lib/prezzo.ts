import type { TipoPrezzo } from "../types";
import { formatPrezzo } from "./format";

export interface PrezzoDisplay {
  /** Testo da mostrare al posto del prezzo. */
  testo: string;
  /** true solo per 'importo': i piani vi appendono « /anno». */
  conSuffissoPeriodo: boolean;
  /** true → l'item non è acquisibile self-serve: CTA «Richiedi una consulenza». */
  suRichiesta: boolean;
}

/**
 * Unico punto che decide come rendere il prezzo di un piano o add-on.
 * Con 'su_richiesta' l'etichetta è personalizzabile dall'admin; vuota o
 * assente ricade su «Su richiesta».
 */
export function prezzoDisplay(
  tipo: TipoPrezzo | undefined,
  etichetta: string | null | undefined,
  valore: string | number | null | undefined,
): PrezzoDisplay {
  if (tipo === "gratis") {
    return { testo: "Gratis", conSuffissoPeriodo: false, suRichiesta: false };
  }
  if (tipo === "su_richiesta") {
    return {
      testo: etichetta?.trim() || "Su richiesta",
      conSuffissoPeriodo: false,
      suRichiesta: true,
    };
  }
  return { testo: formatPrezzo(valore), conSuffissoPeriodo: true, suRichiesta: false };
}
