import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export interface UseTabOpzioni<T extends string> {
  /** Vecchio nome del parametro (es. `vista` dei partenariati), letto come alias
   *  permanente e sostituito da `tab` al primo cambio scheda. */
  alias?: string;
  /** Scheda di partenza se `tab` manca o non è valido; default il primo id. */
  default?: T;
}

/** Stato delle schede nell'URL: un solo parametro `?tab=<id>`. Assente o non
 *  valido = scheda predefinita. `setTab` fa `replace` (il tasto Indietro non
 *  scorre le schede) e toglie `page`: la pagina è della scheda. */
export function useTab<T extends string>(
  ids: readonly T[],
  opts: UseTabOpzioni<T> = {},
): { tab: T; setTab: (id: T) => void } {
  const [params, setParams] = useSearchParams();
  const { alias, default: predefinito } = opts;

  const richiesto = params.get("tab") ?? (alias ? params.get(alias) : null);
  const tab =
    richiesto !== null && (ids as readonly string[]).includes(richiesto)
      ? (richiesto as T)
      : (predefinito ?? ids[0]);

  const setTab = useCallback(
    (id: T) => {
      // Stessa scheda: niente `replace` dell'URL (e niente `page` tolto per nulla).
      if (id === tab) return;
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("tab", id);
          if (alias) next.delete(alias);
          next.delete("page");
          return next;
        },
        { replace: true },
      );
    },
    [setParams, alias, tab],
  );

  return { tab, setTab };
}
