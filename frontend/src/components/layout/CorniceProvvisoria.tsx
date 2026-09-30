import { Outlet } from "react-router-dom";

/** Cornice di transizione per le pagine non ancora rifatte: dà loro il
 *  contenitore e i margini che avevano sotto la vecchia barra in alto (le
 *  pagine nuove usano `Page`, che ha i suoi). Si toglie quando l'ultima pagina
 *  è migrata. */
export function CorniceProvvisoria() {
  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <Outlet />
    </div>
  );
}
