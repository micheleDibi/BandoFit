import { useId, type ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Larghezza della landing: la stessa dell'elenco (`Page variante="elenco"`,
 *  1112px), ma centrata, perché qui non c'è la barra laterale. Unico `max-w`
 *  della landing. */
export function Contenitore({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("mx-auto w-full max-w-[1112px] px-4 sm:px-6 lg:px-10", className)}>
      {children}
    </div>
  );
}

/** Una sezione della landing: titolo `title-page` (h2) con una riga di
 *  spiegazione, a sinistra; `fondo="desk"` la stacca con il piano e due filetti.
 *  `id` è l'ancora della navigazione (resta nel DOM, con lo scarto per la
 *  testata fissa). */
export function Sezione({
  id,
  titolo,
  sottotitolo,
  fondo = "sheet",
  children,
}: {
  id?: string;
  titolo: string;
  sottotitolo?: string;
  fondo?: "sheet" | "desk";
  children: ReactNode;
}) {
  const titoloId = useId();
  return (
    <section
      id={id}
      aria-labelledby={titoloId}
      className={cn("scroll-mt-16", fondo === "desk" && "border-y border-line bg-desk")}
    >
      <Contenitore className="flex flex-col gap-10 py-16 sm:py-20">
        <div className="flex max-w-lettura flex-col gap-2">
          <h2 id={titoloId} className="text-title-page text-ink">
            {titolo}
          </h2>
          {sottotitolo && <p className="text-prose text-ink-2">{sottotitolo}</p>}
        </div>
        {children}
      </Contenitore>
    </section>
  );
}
