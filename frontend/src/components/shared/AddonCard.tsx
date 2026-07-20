import { Check, MessageCircle, Puzzle, ShoppingCart } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "../ui/Button";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Addon } from "../../types";

/** Card di un add-on (stessa estetica di PlanCard, prezzo una tantum senza
 *  suffisso «/anno»). CTA a tre vie sul tipo_prezzo: «Acquista» (importo) e
 *  «Attiva» (gratis) passano dal punto di estensione purchaseAddon;
 *  «su richiesta» passa da requestConsultation e non è acquisibile.
 *  `inventario`: blocco opzionale sotto la CTA («Hai N …» + movimenti). */
export function AddonCard({
  addon,
  onAcquista,
  onRichiedi,
  loading = false,
  inventario,
}: {
  addon: Addon;
  onAcquista: (addon: Addon) => void;
  onRichiedi: (addon: Addon) => void;
  loading?: boolean;
  inventario?: ReactNode;
}) {
  const display = prezzoDisplay(addon.tipo_prezzo, addon.etichetta_prezzo, addon.prezzo);

  return (
    <div className="relative flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5 text-left shadow-card">
      <h3 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Puzzle className="size-4 text-brand-500" aria-hidden />
        {addon.nome}
      </h3>
      <p className="mt-2 font-display text-2xl font-bold text-slate-900">{display.testo}</p>
      {addon.descrizione && (
        <p className="mt-2 flex-1 text-sm leading-relaxed text-slate-500">{addon.descrizione}</p>
      )}
      <div className={addon.descrizione ? "mt-4" : "mt-4 flex-1 content-end"}>
        {display.suRichiesta ? (
          <Button
            type="button"
            variant="secondary"
            className="w-full"
            loading={loading}
            onClick={() => onRichiedi(addon)}
          >
            <MessageCircle className="size-4" aria-hidden />
            Richiedi una consulenza
          </Button>
        ) : (
          <Button
            type="button"
            className="w-full"
            loading={loading}
            onClick={() => onAcquista(addon)}
          >
            {addon.tipo_prezzo === "gratis" ? (
              <>
                <Check className="size-4" aria-hidden />
                Attiva
              </>
            ) : (
              <>
                <ShoppingCart className="size-4" aria-hidden />
                Acquista
              </>
            )}
          </Button>
        )}
      </div>
      {inventario}
    </div>
  );
}
