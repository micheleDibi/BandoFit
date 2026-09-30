import {
  Ban,
  CalendarClock,
  CheckCircle2,
  Clock3,
  HelpCircle,
  PauseCircle,
  XCircle,
} from "lucide-react";
import { daysUntil } from "../../lib/format";
import type { AiEsito } from "../../types";
import { Badge } from "../ui/Badge";
import { dataConOra } from "./stato";

/** Esito dell'AI-check. Il report è generato da un modello e può sbagliare:
 *  il linguaggio resta costruttivo — mai un «bocciato» secco. Per l'esito
 *  negativo NESSUN badge: il colore del punteggio e i verdetti dei singoli
 *  requisiti dicono già tutto, un'etichetta vaga non aggiunge significato. */
export function AiEsitoBadge({ esito }: { esito: AiEsito }) {
  if (esito === "ammissibile") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3" aria-hidden />
        In linea col bando
      </Badge>
    );
  }
  if (esito === "da_verificare") {
    return (
      <Badge tone="amber">
        <HelpCircle className="size-3" aria-hidden />
        Dati da completare
      </Badge>
    );
  }
  return null;
}

/** Stringa libera: un valore nuovo del catalogo ha il badge neutro in fondo. */
export function StatoBadge({ stato }: { stato: string | null }) {
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
  if (stato === "in apertura prossimamente") {
    return (
      <Badge tone="amber">
        <Clock3 className="size-3" aria-hidden />
        In apertura
      </Badge>
    );
  }
  // Tutto il resto ha un badge neutro: un bando sospeso o revocato non va mai
  // presentato come aperto o in apertura (contratto DB bandi §7, R0-a).
  if (stato === "sospeso") {
    return (
      <Badge tone="slate">
        <PauseCircle className="size-3" aria-hidden />
        Sospeso
      </Badge>
    );
  }
  if (stato === "revocato") {
    return (
      <Badge tone="slate">
        <Ban className="size-3" aria-hidden />
        Revocato
      </Badge>
    );
  }
  return (
    <Badge tone="slate">
      <HelpCircle className="size-3" aria-hidden />
      Stato da verificare
    </Badge>
  );
}

/** Scadenza con il conto alla rovescia. Con `conConto` falso (bando non aperto
 *  né in apertura, vedi `bandoInCorso`) resta solo la data, neutra: un bando
 *  chiuso, sospeso o revocato non ha un «Scade tra…» né i colori d'urgenza.
 *  Una data passata dice sempre «Scaduto il…». */
export function ScadenzaBadge({
  dataScadenza,
  oraScadenza,
  conConto = true,
}: {
  dataScadenza: string | null;
  oraScadenza?: string | null;
  conConto?: boolean;
}) {
  const giorni = daysUntil(dataScadenza);
  if (giorni === null) return null;
  const quando = dataConOra(dataScadenza, oraScadenza);

  if (giorni < 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-400">
        <CalendarClock className="size-3.5" aria-hidden />
        Scaduto il {quando}
      </span>
    );
  }

  if (!conConto) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
        <CalendarClock className="size-3.5" aria-hidden />
        Scadenza: {quando}
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
      <span className="text-slate-400">· {quando}</span>
    </span>
  );
}
