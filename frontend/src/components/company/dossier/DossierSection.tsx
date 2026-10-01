import type { ReactNode } from "react";

/** Riga etichetta/valore del dossier: non rende nulla se il valore è vuoto.
 *  Le sezioni richiudibili le fa `Accordion` (in `DossierView`). */
export function DossierRow({ label, value }: { label: string; value: ReactNode }) {
  if (value === null || value === undefined || value === "" || value === "—") return null;
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-small text-ink-3">{label}</dt>
      <dd className="text-body text-ink">{value}</dd>
    </div>
  );
}

export function DossierGrid({ children }: { children: ReactNode }) {
  return <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">{children}</dl>;
}
