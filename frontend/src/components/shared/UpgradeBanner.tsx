import { X } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useMe } from "../../hooks/useMe";
import { usePlans } from "../../hooks/usePlans";
import { useSessionDismissible } from "../../hooks/useSessionDismissible";
import { Alert } from "../ui/Alert";
import { LinkButton } from "../ui/Button";
import { IconButton } from "../ui/IconButton";

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
    <Alert
      tono="info"
      azione={
        <div className="flex items-center gap-1">
          <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
            Vedi i piani
          </LinkButton>
          <IconButton label="Nascondi questo avviso" icon={<X />} size="sm" onClick={dismiss} />
        </div>
      }
    >
      Sei sul piano <strong className="font-semibold">{piano.nome}</strong>: sblocca più funzioni
      con un piano superiore.
    </Alert>
  );
}
