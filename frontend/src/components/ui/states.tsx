import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Button } from "./Button";

/** Rettangolo su `sunken`, senza animazione: mentre arrivano i dati si vede
 *  la forma del contenuto, non un effetto. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("rounded-mark bg-sunken", className)} aria-hidden />;
}

/** Stato vuoto: che cosa manca e un'azione per cominciare, allineati a
 *  sinistra, senza icona. `icon` è accettata per compatibilità e ignorata. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex max-w-[520px] flex-col items-start gap-2 py-8">
      <h3 className="font-sans text-row-title text-ink">{title}</h3>
      {description && <p className="text-body text-ink-2">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Errore: che cosa è successo e come rimediare, con «Riprova» se c'è qualcosa
 *  da ritentare (per 404/410 non si passa `onRetry`). I default sono neutri:
 *  vale anche per errori che non sono di caricamento (chi sa di più passa `title`). */
export function ErrorState({
  title = "Qualcosa è andato storto.",
  message = "Riprova tra qualche istante.",
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex max-w-[520px] flex-col items-start gap-2 py-8" role="alert">
      <h3 className="flex items-start gap-2 font-sans text-row-title text-ink">
        <TriangleAlert className="mt-0.5 size-5 shrink-0 text-danger" aria-hidden />
        {title}
      </h3>
      <p className="text-body text-ink-2">{message}</p>
      {onRetry && (
        <Button variant="secondary" className="mt-2" onClick={onRetry}>
          Riprova
        </Button>
      )}
    </div>
  );
}
