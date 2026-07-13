import { Check, Copy, Video } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { buttonClasses } from "../ui/Button";

/** «Avvia videochiamata» (stanza Jitsi dell'appuntamento, nuova scheda) +
 *  «Copia link». Sempre attivo, nessun gating orario (decisione di prodotto):
 *  l'URL esiste dal momento della prenotazione. Link esterno → <a> con le
 *  classi di Button (LinkButton supporta solo le rotte interne). */
export function VideocallButton({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard non disponibile (permessi/contesto): il link resta apribile.
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className={buttonClasses("primary", "sm")}
      >
        <Video className="size-4" aria-hidden />
        Avvia videochiamata
      </a>
      <button type="button" className={buttonClasses("ghost", "sm")} onClick={copy}>
        {copied ? (
          <Check className="size-4 text-emerald-600" aria-hidden />
        ) : (
          <Copy className="size-4" aria-hidden />
        )}
        {copied ? "Link copiato" : "Copia link"}
      </button>
    </div>
  );
}
