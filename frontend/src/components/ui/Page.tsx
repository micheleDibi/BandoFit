import { createContext, useContext, type ReactNode } from "react";
import { cn } from "../../lib/cn";

export type VariantePage = "elenco" | "dettaglio" | "sezioni" | "flusso";

/** Banner globali della cornice (invito in un'azienda, piano superiore): li
 *  passa `AppShell` e li mostra `Page` in cima al suo contenitore, così sono
 *  larghi quanto la pagina. Fuori dalla cornice (vetrina) il contesto è vuoto. */
const BannerGlobali = createContext<ReactNode>(null);

export function BannerGlobaliProvider({
  banner,
  children,
}: {
  banner: ReactNode;
  children: ReactNode;
}) {
  return <BannerGlobali.Provider value={banner}>{children}</BannerGlobali.Provider>;
}

// Larghezze del contenuto (docs/design-system.md): le pagine non scrivono `max-w-*`.
const larghezze: Record<VariantePage, string> = {
  elenco: "max-w-[1280px]",
  dettaglio: "max-w-[1280px]",
  sezioni: "max-w-[1040px]",
  flusso: "max-w-[760px]",
};

export interface PageProps {
  variante: VariantePage;
  children: ReactNode;
  /** Solo in `dettaglio`: colonna laterale da 340px (`aside`), sotto il contenuto su mobile. */
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

/** Cornice di pagina: padding e larghezza per modello (elenco 1280, dettaglio
 *  1280 con laterale 340, sezioni 1040, flusso 760), contenuto centrato nel
 *  piano e in colonna con gap 24. In cima, dentro la cornice dell'app, i
 *  banner globali (`BannerGlobaliProvider`). */
export function Page({
  variante,
  children,
  laterale,
  intestazione,
  lateraleFissa = false,
  sotto,
  className,
}: PageProps) {
  const banner = useContext(BannerGlobali);
  return (
    <div className={cn("px-4 py-6 sm:px-6 lg:px-10 lg:pt-8 lg:pb-12", className)}>
      <div className={cn("mx-auto flex w-full flex-col gap-6", larghezze[variante])}>
        {/* Senza banner da mostrare il contenitore è vuoto e sparisce (`empty:hidden`),
            senza lasciare il gap. */}
        {banner && <div className="flex flex-col gap-3 empty:hidden">{banner}</div>}
        {variante === "dettaglio" ? (
          <>
            {intestazione}
            {/* 48px fra contenuto e laterale: i 24 di docs/design-system.md valgono per le colonne interne. */}
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-12">
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
