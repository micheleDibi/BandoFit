import { useCallback, useEffect, useState } from "react";

/** Prefisso di tutte le chiavi: `sessionStorage` è condiviso con l'origine. */
const PREFIX = "bandofit:";

/** `sessionStorage` può lanciare (modalità privata di Safari, storage
 *  disabilitato, quota piena): un avviso che non si ricorda di essere stato
 *  chiuso è molto meglio di una pagina che non si monta. */
function leggi(key: string): boolean {
  try {
    return window.sessionStorage.getItem(PREFIX + key) !== null;
  } catch {
    return false;
  }
}

function scrivi(key: string): void {
  try {
    window.sessionStorage.setItem(PREFIX + key, "1");
  } catch {
    // niente persistenza: l'avviso ricomparirà al prossimo caricamento.
  }
}

/** Come `useDismissible`, ma il «chiudi» dura solo la SESSIONE del browser
 *  (`sessionStorage`): alla prossima visita l'avviso si ripropone. La `key`
 *  resta il patto: cambiarla (nuovo periodo, nuovo livello di gravità)
 *  ripropone l'avviso anche nella stessa sessione, ed è quello che si vuole. */
export function useSessionDismissible(key: string): { dismissed: boolean; dismiss: () => void } {
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
