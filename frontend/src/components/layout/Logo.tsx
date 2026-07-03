import { cn } from "../../lib/cn";

export function Logo({ className, dark }: { className?: string; dark?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className="flex size-8 items-center justify-center rounded-lg bg-brand-500 font-display text-lg font-bold text-white">
        B
      </span>
      <span
        className={cn(
          "font-display text-lg font-bold tracking-tight",
          dark ? "text-white" : "text-slate-900",
        )}
      >
        Bando<span className="text-brand-500">Fit</span>
      </span>
    </span>
  );
}
