import { Sparkles, X } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useMe } from "../../hooks/useMe";
import { usePlans } from "../../hooks/usePlans";
import { useSessionDismissible } from "../../hooks/useSessionDismissible";
import { LinkButton } from "../ui/Button";

/** Banner globale per chi è su un piano gratuito e può salire: invito sobrio
 *  a vedere i piani a pagamento. Non compare per chi il piano lo EREDITA dal
 *  titolare (non può comprare), né dove sarebbe ridondante (Abbonamento e
 *  checkout). Il «chiudi» vale per la sessione: alla prossima visita torna. */
export function UpgradeBanner() {
  const { data: me } = useMe();
  const { data: plans } = usePlans();
  const { pathname } = useLocation();
  const { dismissed, dismiss } = useSessionDismissible("upgrade-banner");

  if (dismissed) return null;
  if (pathname.startsWith("/app/checkout") || pathname === "/app/abbonamento") return null;

  const piano = me?.subscription?.plan;
  if (!piano || piano.tipo_prezzo !== "gratis" || me?.subscription?.inherited) return null;

  // «Esiste un piano superiore acquistabile»: stesso criterio di `ordering`
  // di QuotaUpgradeBanner, ristretto ai piani a pagamento self-serve — un
  // piano «su richiesta» qui non basta, l'invito è a comprare.
  const esistePianoSuperiore = (plans ?? []).some(
    (p) =>
      p.is_active &&
      p.tipo_prezzo === "importo" &&
      Number(p.prezzo_annuale) > 0 &&
      p.ordering > piano.ordering,
  );
  if (!esistePianoSuperiore) return null;

  return (
    <div className="border-b border-brand-100 bg-brand-50">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-500 text-white">
          <Sparkles className="size-4" aria-hidden />
        </span>
        <p className="min-w-0 flex-1 text-sm text-brand-900">
          Sei sul piano <strong>{piano.nome}</strong>: sblocca più funzioni con un piano superiore.
        </p>
        <div className="flex shrink-0 items-center gap-1">
          <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
            Vedi i piani
          </LinkButton>
          <button
            type="button"
            onClick={dismiss}
            title="Nascondi questo avviso"
            aria-label="Nascondi questo avviso"
            className="rounded-lg p-1.5 text-brand-400 transition-colors hover:bg-brand-100 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
