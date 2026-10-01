import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { useMyAddonLedger } from "../../hooks/useMyAddons";
import { cn } from "../../lib/cn";
import { ADDON_MOVIMENTO_LABELS } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { MyAddon } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Skeleton } from "../ui/states";

const deltaConSegno = (delta: number) => (delta > 0 ? `+${delta}` : `−${Math.abs(delta)}`);

// La nota dei rimborsi automatici (WP2) porta un codice tecnico: al posto del
// codice si mostra una frase comprensibile.
const notaLeggibile = (tipo: string, note: string | null) =>
  tipo === "refund" && note?.startsWith("rimborso automatico")
    ? "Unità restituita automaticamente: la richiesta non è andata a buon fine"
    : (note ?? null);

/** Inventario di un addon posseduto: etichetta «Hai N …» e storico movimenti
 *  a scomparsa. Il ledger (ultimi 20) si carica on-demand alla prima
 *  apertura, via useMyAddonLedger. Usato dal catalogo e da «I tuoi add-on»
 *  (`mostraBadge={false}`: lì la quantità è già nell'intestazione). */
export function InventarioAddon({
  posseduto,
  mostraBadge = true,
}: {
  posseduto: MyAddon;
  mostraBadge?: boolean;
}) {
  const [aperto, setAperto] = useState(false);
  const {
    data: movimenti,
    isPending,
    isError,
  } = useMyAddonLedger(aperto ? posseduto.addon_id : undefined);

  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {mostraBadge ? (
          <Badge>
            Hai {posseduto.quantita} {posseduto.nome}
          </Badge>
        ) : (
          <span className="text-small text-ink-3">Storico dei movimenti</span>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-expanded={aperto}
          onClick={() => setAperto((v) => !v)}
        >
          {aperto ? "Nascondi i movimenti" : "Vedi i movimenti"}
          <ChevronDown className={cn("size-4", aperto && "rotate-180")} aria-hidden />
        </Button>
      </div>
      {aperto && (
        <div>
          {isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : isError ? (
            <InlineError>Non siamo riusciti a caricare i movimenti. Riapri per riprovare.</InlineError>
          ) : (movimenti?.length ?? 0) === 0 ? (
            <p className="text-small text-ink-3">Nessun movimento registrato.</p>
          ) : (
            <ul className="flex flex-col">
              {movimenti?.map((m, i) => {
                const nota = notaLeggibile(m.tipo, m.note);
                return (
                  <li
                    key={i}
                    className="flex items-baseline justify-between gap-3 border-b border-line py-1.5 text-small last:border-b-0"
                  >
                    <div className="min-w-0">
                      <span className="font-medium text-ink">{ADDON_MOVIMENTO_LABELS[m.tipo]}</span>
                      <span className="ml-2 text-ink-3">{formatDateTime(m.created_at)}</span>
                      {nota && <span className="block text-ink-3">{nota}</span>}
                    </div>
                    <span
                      className={cn(
                        "shrink-0 font-semibold tabular-nums",
                        m.delta > 0 ? "text-fit-ink" : "text-ink-2",
                      )}
                    >
                      {deltaConSegno(m.delta)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
