import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** tailwind-merge conosce solo le classi standard: senza questa estensione
 *  gli stili di testo del design system (`text-body`, `text-caption`…) passano
 *  per colori e vengono scartati quando nella stessa chiamata c'è anche
 *  `text-ink`; i raggi, le ombre, la fascia, le animazioni e l'andamento con un
 *  nome resterebbero senza risoluzione (o passerebbero per colori). */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        {
          text: [
            "title-hero",
            "title-bando",
            "title-page",
            "title-section",
            "figure",
            "figure-sm",
            "due-day",
            "prose",
            "row-title",
            "body",
            "title-group",
            "small",
            "caption",
          ],
        },
      ],
      rounded: [{ rounded: ["mark", "control", "panel", "pill"] }],
      shadow: [{ shadow: ["card", "card-hover", "overlay"] }],
      // La fascia è un gradiente (`background-image`): non sostituisce un `bg-*` di colore.
      "bg-image": [{ bg: ["banda"] }],
      animate: [
        {
          animate: ["shimmer", "entrata", "entrata-sinistra", "entrata-destra", "crescita"],
        },
      ],
      ease: [{ ease: ["uscita"] }],
    },
  },
});

/** Unisce classi Tailwind risolvendo i conflitti: le classi passate per ultime
 *  (es. override via `className`) vincono sui default dei variant. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
