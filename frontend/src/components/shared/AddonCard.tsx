import { Minus, Plus } from "lucide-react";
import { useState, type ReactNode } from "react";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Addon } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { IconButton } from "../ui/IconButton";

/** Bound del checkout e del grant admin (CHECK purchases.quantita, 0030). */
const QTY_MAX = 100;

/** Perché la CTA «Acquista» è disabilitata (il gate vero è nel checkout). */
const MOTIVO_LABELS: Record<NonNullable<Addon["motivo_non_acquistabile"]>, string> = {
  solo_titolare: "Gli acquisti si gestiscono sull'account titolare.",
  piano_non_idoneo: "Disponibile con i piani che includono questa funzione.",
};

/** Card di un add-on del catalogo (prezzo una tantum, senza «/anno»). CTA a
 *  tre vie sul tipo_prezzo: «Acquista» (importo) e «Attiva» (gratis) passano
 *  dal punto di estensione purchaseAddon; «su richiesta» passa da
 *  requestConsultation e non è acquisibile. I consumabili a pagamento hanno
 *  la quantità (1..100): la scelta viaggia fino al checkout (`?qty=`) — il
 *  totale lo calcola solo il server. `inventario`: blocco opzionale sotto la
 *  CTA («Hai N …» + movimenti). Le CTA sono secondarie: un solo pulsante
 *  pieno per schermata. */
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
  // Non acquistabile per QUESTO utente (0030: collegato attivo, o piano che
  // non abilita la risorsa dell'addon): CTA disabilitata con il perché.
  const bloccato = addon.tipo_prezzo === "importo" && addon.acquistabile === false;
  const motivoBlocco =
    bloccato && addon.motivo_non_acquistabile
      ? MOTIVO_LABELS[addon.motivo_non_acquistabile]
      : null;
  // Solo i consumabili a pagamento si comprano a quantità (un permanente è un
  // possesso binario; il server rifiuta comunque qty≠1).
  const conQuantita =
    !display.suRichiesta &&
    !bloccato &&
    addon.tipo_prezzo === "importo" &&
    addon.tipo_fruizione === "consumabile";

  const cambiaQuantita = (delta: number) =>
    setQuantita((q) => Math.min(QTY_MAX, Math.max(1, q + delta)));

  return (
    <Card className="flex h-full flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-sans text-title-group text-ink">{addon.nome}</h3>
        <p className="shrink-0 text-figure-sm text-ink">{display.testo}</p>
      </div>
      {addon.descrizione && <p className="flex-1 text-body text-ink-2">{addon.descrizione}</p>}

      <div className="flex flex-col gap-3">
        {conQuantita && (
          <div className="flex items-center justify-between gap-3">
            <span id={`qty-addon-${addon.id}`} className="text-body text-ink-2">
              Quantità
            </span>
            <div role="group" aria-labelledby={`qty-addon-${addon.id}`} className="inline-flex items-center gap-1">
              <IconButton
                label="Diminuisci la quantità"
                icon={<Minus />}
                size="sm"
                variant="secondary"
                disabled={quantita <= 1}
                onClick={() => cambiaQuantita(-1)}
              />
              <span
                aria-live="polite"
                className="min-w-8 text-center text-body font-medium text-ink tabular-nums"
              >
                {quantita}
              </span>
              <IconButton
                label="Aumenta la quantità"
                icon={<Plus />}
                size="sm"
                variant="secondary"
                disabled={quantita >= QTY_MAX}
                onClick={() => cambiaQuantita(1)}
              />
            </div>
          </div>
        )}
        <div>
          {display.suRichiesta ? (
            <Button
              type="button"
              variant="secondary"
              loading={loading}
              onClick={() => onRichiedi(addon)}
            >
              Richiedi una consulenza
            </Button>
          ) : (
            <Button
              type="button"
              variant="secondary"
              loading={loading}
              disabled={bloccato}
              onClick={() => onAcquista(addon, conQuantita ? quantita : 1)}
            >
              {addon.tipo_prezzo === "gratis"
                ? "Attiva"
                : conQuantita && quantita > 1
                  ? `Acquista ${quantita}`
                  : "Acquista"}
            </Button>
          )}
        </div>
        {motivoBlocco && <p className="text-small text-ink-3">{motivoBlocco}</p>}
      </div>
      {inventario}
    </Card>
  );
}
