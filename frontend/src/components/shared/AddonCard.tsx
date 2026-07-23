import { Check, MessageCircle, Minus, Plus, Puzzle, ShoppingCart } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "../ui/Button";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Addon } from "../../types";

/** Bound del checkout e del grant admin (CHECK purchases.quantita, 0030). */
const QTY_MAX = 100;

/** Card di un add-on (stessa estetica di PlanCard, prezzo una tantum senza
 *  suffisso «/anno»). CTA a tre vie sul tipo_prezzo: «Acquista» (importo) e
 *  «Attiva» (gratis) passano dal punto di estensione purchaseAddon;
 *  «su richiesta» passa da requestConsultation e non è acquisibile.
 *  I consumabili a pagamento hanno lo stepper quantità (1..100): la scelta
 *  viaggia fino al checkout (`?qty=`) — il totale lo calcola solo il server.
 *  `inventario`: blocco opzionale sotto la CTA («Hai N …» + movimenti). */
export function AddonCard({
  addon,
  onAcquista,
  onRichiedi,
  loading = false,
  inventario,
}: {
  addon: Addon;
  onAcquista: (addon: Addon, quantita: number) => void;
  onRichiedi: (addon: Addon) => void;
  loading?: boolean;
  inventario?: ReactNode;
}) {
  const display = prezzoDisplay(addon.tipo_prezzo, addon.etichetta_prezzo, addon.prezzo);
  const [quantita, setQuantita] = useState(1);
  // Solo i consumabili a pagamento si comprano a quantità (un permanente è un
  // possesso binario; il server rifiuta comunque qty≠1).
  const conQuantita =
    !display.suRichiesta &&
    addon.tipo_prezzo === "importo" &&
    addon.tipo_fruizione === "consumabile";

  const cambiaQuantita = (delta: number) =>
    setQuantita((q) => Math.min(QTY_MAX, Math.max(1, q + delta)));

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
        {conQuantita && (
          <div className="mb-3 flex items-center justify-between gap-3">
            <span id={`qty-addon-${addon.id}`} className="text-sm text-slate-600">
              Quantità
            </span>
            <div
              role="group"
              aria-labelledby={`qty-addon-${addon.id}`}
              className="inline-flex items-center rounded-lg border border-slate-200"
            >
              <button
                type="button"
                aria-label="Diminuisci la quantità"
                disabled={quantita <= 1}
                onClick={() => cambiaQuantita(-1)}
                className="rounded-l-lg px-2.5 py-1.5 text-slate-600 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:text-slate-300"
              >
                <Minus className="size-4" aria-hidden />
              </button>
              <span
                aria-live="polite"
                className="min-w-10 border-x border-slate-200 px-2 py-1.5 text-center text-sm font-medium tabular-nums text-slate-900"
              >
                {quantita}
              </span>
              <button
                type="button"
                aria-label="Aumenta la quantità"
                disabled={quantita >= QTY_MAX}
                onClick={() => cambiaQuantita(1)}
                className="rounded-r-lg px-2.5 py-1.5 text-slate-600 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:text-slate-300"
              >
                <Plus className="size-4" aria-hidden />
              </button>
            </div>
          </div>
        )}
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
            onClick={() => onAcquista(addon, conQuantita ? quantita : 1)}
          >
            {addon.tipo_prezzo === "gratis" ? (
              <>
                <Check className="size-4" aria-hidden />
                Attiva
              </>
            ) : (
              <>
                <ShoppingCart className="size-4" aria-hidden />
                {conQuantita && quantita > 1 ? `Acquista ${quantita}` : "Acquista"}
              </>
            )}
          </Button>
        )}
      </div>
      {inventario}
    </div>
  );
}
