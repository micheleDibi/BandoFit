import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

/** La query di partenza con `tab` impostato: quello della rotta vince su uno
 *  eventualmente già presente e, se non c'era, va in coda; gli altri parametri
 *  restano nella query, e quali usare lo decide la scheda. */
function conScheda(search: string, tab: string): string {
  const params = new URLSearchParams(search);
  params.set("tab", tab);
  return `?${params.toString()}`;
}

/** Redirect di un vecchio URL a quello nuovo, conservando `search` e `hash`
 *  (i link generati dal backend e i segnalibri devono arrivare al contenuto
 *  giusto). Con `tab` la destinazione è una scheda: `?tab=` si aggiunge alla
 *  query esistente, in coda (`/app/acquisti?azienda=a1#x` →
 *  `/app/abbonamento?azienda=a1&tab=acquisti#x`). La scheda Acquisti tiene la
 *  pagina nel suo stato e riparte dalla prima: un vecchio `page` resta
 *  nell'URL ma non si applica.
 *  Con `quandoHash` il redirect scatta solo se l'hash combacia
 *  (`/app/profilo#collegati` → `/app/collegati`): altrimenti rende `children`,
 *  cioè la pagina di sempre. */
export function RedirectLegacy({
  to,
  tab,
  quandoHash,
  children,
}: {
  to: string;
  tab?: string;
  quandoHash?: string;
  children?: ReactNode;
}) {
  const location = useLocation();
  const search = tab ? conScheda(location.search, tab) : location.search;
  if (quandoHash !== undefined) {
    if (location.hash === quandoHash) {
      return <Navigate replace to={{ pathname: to, search }} />;
    }
    return <>{children}</>;
  }
  return <Navigate replace to={{ pathname: to, search, hash: location.hash }} />;
}
