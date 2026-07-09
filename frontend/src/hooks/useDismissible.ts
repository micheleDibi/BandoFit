import { useCallback, useEffect, useState } from "react";

/** Prefisso di tutte le chiavi: `localStorage` è condiviso con l'origine. */
const PREFIX = "bandofit:";

/** `localStorage` può lanciare (modalità privata di Safari, storage disabilitato,
 *  quota piena): un avviso che non si ricorda di essere stato chiuso è molto
 *  meglio di una pagina che non si monta. */
function leggi(key: string): boolean {
  try {
    return window.localStorage.getItem(PREFIX + key) !== null;
  } catch {
    return false;
  }
}

function scrivi(key: string): void {
  try {
    window.localStorage.setItem(PREFIX + key, "1");
  } catch {
    // niente persistenza: l'avviso ricomparirà al prossimo caricamento.
  }
}

/** Ricorda che l'utente ha chiuso qualcosa. La `key` è il PATTO: cambiarla
 *  (nuovo periodo, nuovo livello di gravità) ripropone l'avviso, ed è quello
 *  che si vuole — un «chiudi» non deve zittire per sempre un messaggio diverso. */
export function useDismissible(key: string): { dismissed: boolean; dismiss: () => void } {
  const [dismissed, setDismissed] = useState(() => leggi(key));

  useEffect(() => {
    setDismissed(leggi(key));
  }, [key]);

  const dismiss = useCallback(() => {
    scrivi(key);
    setDismissed(true);
  }, [key]);

  return { dismissed, dismiss };
}
