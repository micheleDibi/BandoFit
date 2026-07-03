import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "../../lib/cn";

function pagesToShow(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set<number>([1, total, current - 1, current, current + 1]);
  const sorted = [...pages].filter((p) => p >= 1 && p <= total).sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (p - prev > 1) out.push("…");
    out.push(p);
    prev = p;
  }
  return out;
}

export function Pagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;
  const btn =
    "inline-flex h-9 min-w-9 cursor-pointer items-center justify-center rounded-lg px-2 text-sm font-medium " +
    "transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-brand-500 " +
    "disabled:pointer-events-none disabled:opacity-40";
  return (
    <nav className="flex items-center justify-center gap-1" aria-label="Paginazione">
      <button
        type="button"
        className={cn(btn, "text-slate-600 hover:bg-slate-100")}
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        aria-label="Pagina precedente"
      >
        <ChevronLeft className="size-4" aria-hidden />
      </button>
      {pagesToShow(page, totalPages).map((p, i) =>
        p === "…" ? (
          <span key={`gap-${i}`} className="px-1.5 text-sm text-slate-400" aria-hidden>
            …
          </span>
        ) : (
          <button
            key={p}
            type="button"
            onClick={() => onChange(p)}
            aria-current={p === page ? "page" : undefined}
            className={cn(
              btn,
              p === page
                ? "bg-brand-500 text-white shadow-sm"
                : "text-slate-600 hover:bg-slate-100",
            )}
          >
            {p}
          </button>
        ),
      )}
      <button
        type="button"
        className={cn(btn, "text-slate-600 hover:bg-slate-100")}
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
        aria-label="Pagina successiva"
      >
        <ChevronRight className="size-4" aria-hidden />
      </button>
    </nav>
  );
}
