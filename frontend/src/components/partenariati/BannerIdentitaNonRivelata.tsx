import { ShieldQuestion } from "lucide-react";
import { cn } from "../../lib/cn";
import { CHAT_COPY } from "../../lib/copy";

/** Nota fissa della conversazione finché la piattaforma non rivela
 *  l'identità delle aziende (oggi sempre: la rivelazione è spenta, come il
 *  profilo con il nome). Testo fissato dal piano (`CHAT_COPY`). */
export function BannerIdentitaNonRivelata({ className }: { className?: string }) {
  return (
    <p
      role="note"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-700",
        className,
      )}
    >
      <ShieldQuestion className="mt-0.5 size-4 shrink-0 text-slate-500" aria-hidden />
      <span>{CHAT_COPY.identitaNonRivelata}</span>
    </p>
  );
}
