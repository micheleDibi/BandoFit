import { CalendarClock, CheckCircle2, Clock3, HelpCircle, XCircle } from "lucide-react";
import { daysUntil, formatDate } from "../../lib/format";
import type { AiEsito, StatoBando } from "../../types";
import { Badge } from "../ui/Badge";

/** Esito di ammissibilità dell'AI-check. */
export function AiEsitoBadge({ esito }: { esito: AiEsito }) {
  if (esito === "ammissibile") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3" aria-hidden />
        Ammissibile
      </Badge>
    );
  }
  if (esito === "non_ammissibile") {
    return (
      <Badge tone="red">
        <XCircle className="size-3" aria-hidden />
        Non ammissibile
      </Badge>
    );
  }
  return (
    <Badge tone="amber">
      <HelpCircle className="size-3" aria-hidden />
      Da verificare
    </Badge>
  );
}

export function StatoBadge({ stato }: { stato: StatoBando | null }) {
  if (!stato) return null;
  if (stato === "aperto") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3" aria-hidden />
        Aperto
      </Badge>
    );
  }
  if (stato === "chiuso") {
    return (
      <Badge tone="slate">
        <XCircle className="size-3" aria-hidden />
        Chiuso
      </Badge>
    );
  }
  return (
    <Badge tone="amber">
      <Clock3 className="size-3" aria-hidden />
      In apertura
    </Badge>
  );
}

export function ScadenzaBadge({ dataScadenza }: { dataScadenza: string | null }) {
  const giorni = daysUntil(dataScadenza);
  if (giorni === null) return null;

  if (giorni < 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <CalendarClock className="size-3.5" aria-hidden />
        Scaduto il {formatDate(dataScadenza)}
      </span>
    );
  }

  const urgente = giorni <= 7;
  const vicino = giorni <= 30;
  return (
    <span
      className={
        urgente
          ? "inline-flex items-center gap-1 text-xs font-semibold text-red-600"
          : vicino
            ? "inline-flex items-center gap-1 text-xs font-medium text-amber-600"
            : "inline-flex items-center gap-1 text-xs text-slate-500"
      }
    >
      <CalendarClock className="size-3.5" aria-hidden />
      {giorni === 0
        ? "Scade oggi"
        : giorni === 1
          ? "Scade domani"
          : `Scade tra ${giorni} giorni`}
      <span className="text-slate-400">· {formatDate(dataScadenza)}</span>
    </span>
  );
}
