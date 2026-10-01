import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type VariantePage = "elenco" | "dettaglio" | "sezioni" | "flusso";

// Larghezze del contenuto (docs/design-system.md): le pagine non scrivono `max-w-*`.
const larghezze: Record<VariantePage, string> = {
  elenco: "max-w-[1112px]",
  dettaglio: "max-w-[1112px]",
  sezioni: "max-w-[880px]",
  flusso: "max-w-[720px]",
};

export interface PageProps {
  variante: VariantePage;
  children: ReactNode;
  /** Solo in `dettaglio`: colonna laterale da 320px (`aside`), sotto il contenuto su mobile. */
  laterale?: ReactNode;
  /** Solo in `dettaglio`: a tutta larghezza sopra le due colonne (`PageHeader`, `Facts`). */
  intestazione?: ReactNode;
  /** Solo in `dettaglio`: da `lg` la colonna laterale resta in vista scorrendo
   *  (sotto `lg` non cambia nulla). Per colonne corte. */
  lateraleFissa?: boolean;
  /** Solo in `dettaglio`: a tutta larghezza SOTTO le due colonne (sezioni lunghe
   *  come il report AI-check). */
  sotto?: ReactNode;
  className?: string;
}

/** Cornice di pagina: padding e larghezza per modello (elenco 1112, dettaglio
 *  1112 con laterale 320, sezioni 880, flusso 720), contenuto in colonna con gap 24. */
export function Page({
  variante,
  children,
  laterale,
  intestazione,
  lateraleFissa = false,
  sotto,
  className,
}: PageProps) {
  return (
    <div className={cn("px-4 py-6 sm:px-6 lg:px-10 lg:pt-8 lg:pb-12", className)}>
      <div className={cn("flex flex-col gap-6", larghezze[variante])}>
        {variante === "dettaglio" ? (
          <>
            {intestazione}
            {/* 48px fra contenuto e laterale: i 24 di docs/design-system.md valgono per le colonne interne. */}
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-12">
              <div className="flex min-w-0 flex-col gap-6">{children}</div>
              {/* Sotto lg la laterale è `contents`: i suoi pannelli diventano celle della
                  colonna unica, con lo stesso gap, e una pagina può portarne uno
                  sopra il contenuto con `order-first lg:order-none` (tavola MobileBando)
                  senza duplicare nulla nel DOM. */}
              {laterale && (
                <aside
                  className={cn(
                    "contents lg:flex lg:min-w-0 lg:flex-col lg:gap-6",
                    lateraleFissa && "lg:sticky lg:top-8 lg:self-start",
                  )}
                >
                  {laterale}
                </aside>
              )}
            </div>
            {sotto}
          </>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
