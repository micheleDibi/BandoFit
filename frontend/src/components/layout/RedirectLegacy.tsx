import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

/** Redirect di un vecchio URL a quello nuovo, conservando `search` e `hash`
 *  (i link generati dal backend e i segnalibri devono arrivare al contenuto
 *  giusto). Con `quandoHash` il redirect scatta solo se l'hash combacia
 *  (`/app/profilo#collegati` → `/app/collegati`): altrimenti rende `children`,
 *  cioè la pagina di sempre. */
export function RedirectLegacy({
  to,
  quandoHash,
  children,
}: {
  to: string;
  quandoHash?: string;
  children?: ReactNode;
}) {
  const location = useLocation();
  if (quandoHash !== undefined) {
    if (location.hash === quandoHash) {
      return <Navigate replace to={{ pathname: to, search: location.search }} />;
    }
    return <>{children}</>;
  }
  return (
    <Navigate replace to={{ pathname: to, search: location.search, hash: location.hash }} />
  );
}
