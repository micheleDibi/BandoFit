import type { KeyboardEvent } from "react";

/** Classi di una voce dei menu della barra laterale (account, aziende). */
export const voceMenu =
  "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-body font-medium transition-colors duration-150";

/** Frecce, Home ed End fra le voci di un menu (`role="menuitem"` o
 *  `menuitemradio`) dentro un `Popover`, che già gestisce Esc, Tab e il
 *  ritorno del focus al pulsante. Da mettere come `onKeyDown` del contenitore
 *  con `role="menu"`. */
export function spostaFocusVoce(e: KeyboardEvent<HTMLElement>) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
  const voci = Array.from(
    e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]'),
  );
  const n = voci.length;
  if (n === 0) return;
  e.preventDefault();
  const idx = voci.indexOf(document.activeElement as HTMLElement);
  let prossima: number;
  if (e.key === "Home") prossima = 0;
  else if (e.key === "End") prossima = n - 1;
  else if (e.key === "ArrowDown") prossima = idx < 0 ? 0 : (idx + 1) % n;
  else prossima = idx < 0 ? n - 1 : (idx - 1 + n) % n;
  voci[prossima]?.focus();
}
