/** Classi per i controlli che stanno sulla fascia navy di `PageHeader` con
 *  `area` (docs/design-system.md, «Gradiente»): lì il testo è bianco o
 *  bianco/80 e l'anello del focus è bianco. Il primario sulla fascia è
 *  `Button variant="inverse"`; i secondari restano `secondary` (fondo bianco). */

/** `TextLink` sulla fascia: bianco e sempre sottolineato (non c'è il colore a
 *  distinguerlo dal testo intorno). */
export const LINK_SU_FASCIA =
  "text-white underline decoration-white/60 hover:decoration-white focus-visible:outline-white";

/** `Button variant="ghost"` sulla fascia: testo bianco, fondo bianco/10 al passaggio. */
export const GHOST_SU_FASCIA = "text-white hover:bg-white/10 focus-visible:outline-white";

/** Testo secondario sulla fascia (riga sopra, note): bianco/80, ≥ 4,7:1. */
export const TESTO_SU_FASCIA = "text-white/80";

/** Pulsanti `secondary` e `danger` sulla fascia: l'anello del focus bianco
 *  (quello `accent` sul navy non si vedrebbe abbastanza). */
export const FOCUS_SU_FASCIA = "focus-visible:outline-white";
