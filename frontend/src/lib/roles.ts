import type { UserRole } from "../types";

/** Parità admin ↔ progettista (decisione di prodotto, migration 0019):
 *  l'area progettista — menu, rotte, gestione slot e appuntamenti nel
 *  calendario — è di entrambi i ruoli. Un solo predicato, per non duplicare
 *  la condizione nei guard e nelle pagine. */
export function hasAreaProgettista(role: UserRole | undefined): boolean {
  return role === "progettista" || role === "admin";
}
