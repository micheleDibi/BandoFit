import { Check, MapPin } from "lucide-react";
import { scoreColorClasses } from "../../lib/scoreColor";
import { cn } from "../../lib/cn";

/** Anello di punteggio (donut SVG vettoriale) colorato secondo le bande
 *  dell'AI-check. r = 15.9155 → circonferenza ≈ 100, così il valore è anche
 *  la lunghezza dell'arco. */
function ScoreRing({ value }: { value: number }) {
  const { text } = scoreColorClasses(value);
  return (
    <div className={cn("relative size-16 shrink-0", text)}>
      <svg viewBox="0 0 36 36" className="size-16 -rotate-90">
        <circle cx="18" cy="18" r="15.9155" fill="none" className="stroke-slate-200" strokeWidth="3" />
        <circle
          cx="18"
          cy="18"
          r="15.9155"
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${value} 100`}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="tabular font-display text-lg font-bold leading-none text-slate-900">
          {value}
        </span>
        <span className="text-[9px] font-medium leading-none text-slate-400">/100</span>
      </div>
    </div>
  );
}

/** Mock di prodotto per l'hero (illustrativo, dati d'esempio): una scheda
 *  bando con sopra il widget dell'AI-check. Costruito solo con i token del
 *  design system — non è uno screenshot, nessun dato reale. */
export function HeroShowcase() {
  return (
    <div className="relative mx-auto w-full max-w-md lg:mx-0" aria-hidden>
      {/* Bagliore decorativo dietro le card (primo nel DOM → dipinto sotto),
          per dare profondità sul fondo scuro */}
      <div className="pointer-events-none absolute -inset-6 rounded-full bg-brand-400/20 blur-3xl" />

      {/* Scheda bando */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xl">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
            <span className="size-1.5 rounded-full bg-emerald-500" />
            Aperto
          </span>
          <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200">
            Nazionale
          </span>
        </div>
        {/* <p> non <h3>: il mock è aria-hidden e non deve entrare nella
            gerarchia dei heading della pagina. */}
        <p className="mt-3 font-display text-base font-semibold leading-snug text-slate-900">
          Transizione 5.0 — Digitalizzazione delle PMI
        </p>
        <p className="mt-1 inline-flex items-center gap-1 text-xs text-slate-500">
          <MapPin className="size-3.5" />
          Ministero delle Imprese e del Made in Italy
        </p>
        <div className="mt-4 grid grid-cols-2 gap-3">
          <div className="rounded-lg bg-surface px-3 py-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Dotazione</p>
            <p className="tabular mt-0.5 font-display text-sm font-bold text-slate-900">6,3 mld €</p>
          </div>
          <div className="rounded-lg bg-surface px-3 py-2">
            <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Scadenza</p>
            <p className="tabular mt-0.5 font-display text-sm font-bold text-slate-900">30/09/2026</p>
          </div>
        </div>
      </div>

      {/* Widget AI-check sovrapposto */}
      <div className="absolute -bottom-8 -left-4 w-64 rounded-2xl border border-brand-200 bg-white p-4 shadow-xl sm:-left-8">
        <div className="flex items-center gap-3">
          <ScoreRing value={82} />
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">AI-check</p>
            <p className="font-display text-sm font-semibold text-slate-900">Compatibilità alta</p>
            <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
              <Check className="size-3" />
              Ammissibile
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
