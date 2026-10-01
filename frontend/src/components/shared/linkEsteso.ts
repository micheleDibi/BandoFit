/** Card intera cliccabile senza annidare elementi interattivi: il link del
 *  titolo si allarga su tutta la card con uno pseudo-elemento (`after:`), la
 *  card ha `relative` (e `interattiva`), gli altri controlli della card stanno
 *  sopra con `SOPRA_LINK_ESTESO`. Il DOM non cambia: resta un solo link. */
export const LINK_ESTESO = "after:absolute after:inset-0 after:rounded-panel";

/** Pulsanti e link secondari dentro una card con `LINK_ESTESO`. */
export const SOPRA_LINK_ESTESO = "relative z-10";
