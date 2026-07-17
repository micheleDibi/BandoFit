import type { Mode } from "@revolut/checkout";

// 'sandbox' finché VITE_REVOLUT_MODE non dice altro: il default sicuro è
// quello che NON muove denaro vero. Condiviso da checkout e gestione metodo.
export const REVOLUT_MODE: Mode =
  (import.meta.env.VITE_REVOLUT_MODE as Mode | undefined) ?? "sandbox";
