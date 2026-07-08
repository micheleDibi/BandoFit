import { ChevronDown } from "lucide-react";

export interface FaqItem {
  q: string;
  a: string;
}

/** Accordion FAQ basato su <details>/<summary> nativi: accessibile da
 *  tastiera e screen reader senza JavaScript, e coerente con
 *  prefers-reduced-motion (gestito globalmente). */
export function Faq({ items }: { items: FaqItem[] }) {
  return (
    <div className="mx-auto mt-10 max-w-3xl divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      {items.map((item) => (
        <details key={item.q} className="group">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 font-display text-base font-semibold text-slate-900 transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500 [&::-webkit-details-marker]:hidden">
            {item.q}
            <ChevronDown
              className="size-5 shrink-0 text-slate-400 transition-transform duration-150 group-open:rotate-180"
              aria-hidden
            />
          </summary>
          <p className="px-5 pb-4 text-sm leading-relaxed text-slate-600">{item.a}</p>
        </details>
      ))}
    </div>
  );
}
