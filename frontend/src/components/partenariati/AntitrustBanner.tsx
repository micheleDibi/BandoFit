import { CHAT_COPY } from "../../lib/copy";
import { Alert } from "../ui/Alert";

/** Avvertenza antitrust fissa della chat (non si chiude): tra aziende che
 *  possono essere concorrenti non si scambiano prezzi, offerte o strategie.
 *  Testo fissato dal piano (`CHAT_COPY.antitrust`), parola per parola. */
export function AntitrustBanner({ className }: { className?: string }) {
  return (
    // Fisso in ogni conversazione: non va annunciato come allarme a ogni apertura.
    <Alert
      tono="attenzione"
      ruolo="none"
      titolo="Regole della conversazione"
      className={className}
    >
      {CHAT_COPY.antitrust}
    </Alert>
  );
}
