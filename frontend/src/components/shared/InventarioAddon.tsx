import { BadgeCheck, ChevronDown } from "lucide-react";
import { useState } from "react";
import { useMyAddonLedger } from "../../hooks/useMyAddons";
import { cn } from "../../lib/cn";
import { ADDON_MOVIMENTO_LABELS } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { MyAddon } from "../../types";
import { Badge } from "../ui/Badge";
import { Skeleton } from "../ui/states";

const deltaConSegno = (delta: number) => (delta > 0 ? `+${delta}` : `−${Math.abs(delta)}`);

/** Inventario di un addon posseduto: badge «Hai N …» e storico movimenti a
 *  scomparsa. Il ledger (ultimi 20) si carica on-demand alla prima apertura,
 *  via useMyAddonLedger. Usato dal catalogo (Abbonamento) e da «I miei addon»
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
    <div className="mt-3 border-t border-slate-100 pt-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {mostraBadge ? (
          <Badge tone="emerald">
            <BadgeCheck className="size-3" aria-hidden />
            Hai {posseduto.quantita} {posseduto.nome}
          </Badge>
        ) : (
          <span className="text-xs text-slate-400">Storico movimenti</span>
        )}
        <button
          type="button"
          aria-expanded={aperto}
          onClick={() => setAperto((v) => !v)}
          className="inline-flex cursor-pointer items-center gap-0.5 text-xs font-medium text-brand-600 hover:text-brand-700"
        >
          {aperto ? "Nascondi movimenti" : "Vedi movimenti"}
          <ChevronDown
            className={cn("size-3.5 transition-transform", aperto && "rotate-180")}
            aria-hidden
          />
        </button>
      </div>
      {aperto && (
        <div className="mt-2">
          {isPending ? (
            <Skeleton className="h-16 w-full" />
          ) : isError ? (
            <p className="text-xs text-red-600" role="alert">
              Impossibile caricare i movimenti. Riapri per riprovare.
            </p>
          ) : (movimenti?.length ?? 0) === 0 ? (
            <p className="text-xs text-slate-400">Nessun movimento registrato.</p>
          ) : (
            <ul className="space-y-1.5">
              {movimenti?.map((m, i) => (
                <li
                  key={i}
                  title={m.note ?? undefined}
                  className="flex items-baseline justify-between gap-2 text-xs"
                >
                  <div className="min-w-0">
                    <span className="font-medium text-slate-700">
                      {ADDON_MOVIMENTO_LABELS[m.tipo]}
                    </span>
                    <span className="ml-1.5 text-slate-400">{formatDateTime(m.created_at)}</span>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 font-semibold tabular-nums",
                      m.delta > 0 ? "text-emerald-600" : "text-slate-500",
                    )}
                  >
                    {deltaConSegno(m.delta)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
