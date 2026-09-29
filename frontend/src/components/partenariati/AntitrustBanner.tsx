import { Scale } from "lucide-react";
import { cn } from "../../lib/cn";
import { CHAT_COPY } from "../../lib/copy";

/** Avvertenza antitrust fissa della chat (non si chiude): tra aziende che
 *  possono essere concorrenti non si scambiano prezzi, offerte o strategie.
 *  Testo fissato dal piano (`CHAT_COPY.antitrust`). */
export function AntitrustBanner({ className }: { className?: string }) {
  return (
    <p
      role="note"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-900",
        className,
      )}
    >
      <Scale className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>
        <span className="font-semibold">Regole della conversazione: </span>
        {CHAT_COPY.antitrust}
      </span>
    </p>
  );
}
