import { Puzzle, ShoppingCart } from "lucide-react";
import { Button } from "../ui/Button";
import { formatPrezzo } from "../../lib/format";
import type { Addon } from "../../types";

/** Card di un add-on (stessa estetica di PlanCard, prezzo una tantum senza
 *  suffisso «/anno»). Il click passa dal punto di estensione purchaseAddon. */
export function AddonCard({
  addon,
  onAcquista,
  loading = false,
}: {
  addon: Addon;
  onAcquista: (addon: Addon) => void;
  loading?: boolean;
}) {
  return (
    <div className="relative flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5 text-left shadow-card">
      <h3 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Puzzle className="size-4 text-brand-500" aria-hidden />
        {addon.nome}
      </h3>
      <p className="mt-2 font-display text-2xl font-bold text-slate-900">
        {formatPrezzo(addon.prezzo)}
      </p>
      {addon.descrizione && (
        <p className="mt-2 flex-1 text-sm leading-relaxed text-slate-500">{addon.descrizione}</p>
      )}
      <div className={addon.descrizione ? "mt-4" : "mt-4 flex-1 content-end"}>
        <Button
          type="button"
          className="w-full"
          loading={loading}
          onClick={() => onAcquista(addon)}
        >
          <ShoppingCart className="size-4" aria-hidden />
          Acquista
        </Button>
      </div>
    </div>
  );
}
