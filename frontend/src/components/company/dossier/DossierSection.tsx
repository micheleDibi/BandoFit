import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "../../../lib/cn";
import { Card } from "../../ui/Card";

interface DossierSectionProps {
  title: string;
  icon: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** Card collassabile per una sezione del dossier aziendale. */
export function DossierSection({ title, icon, defaultOpen = true, children }: DossierSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card className="overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full cursor-pointer items-center justify-between px-6 py-4 text-left transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-500"
      >
        <span className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <span className="text-brand-500">{icon}</span>
          {title}
        </span>
        <ChevronDown
          className={cn("size-4 text-slate-400 transition-transform", open && "rotate-180")}
          aria-hidden
        />
      </button>
      {open && <div className="border-t border-slate-100 px-6 py-5">{children}</div>}
    </Card>
  );
}

/** Riga label/valore: non renderizza nulla se il valore è vuoto. */
export function DossierRow({
  label,
  value,
}: {
  label: string;
  value: ReactNode;
}) {
  if (value === null || value === undefined || value === "" || value === "—") return null;
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 text-sm text-slate-800">{value}</dd>
    </div>
  );
}

export function DossierGrid({ children }: { children: ReactNode }) {
  return <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">{children}</dl>;
}
