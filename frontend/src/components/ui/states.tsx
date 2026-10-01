import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { areaIcona, type Area } from "./area";
import { Button } from "./Button";
import { IconChip, type IconaChip } from "./IconChip";

/** Rettangolo su `sunken` con il riflesso (`riflesso` in `index.css`): mentre
 *  arrivano i dati si vede la forma del contenuto. Con il movimento ridotto il
 *  riflesso non c'è. */
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("riflesso rounded-mark bg-sunken", className)} aria-hidden />;
}

/** Stato vuoto: che cosa manca e un'azione per cominciare, allineati a
 *  sinistra. Con `icon` (o con la sola `area`, che porta la sua icona) un
 *  `IconChip` grande nel colore dell'area sta sopra il titolo. */
export function EmptyState({
  title,
  description,
  action,
  icon,
  area,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  /** L'icona di lucide (`Bookmark`, o anche `<Bookmark />`). */
  icon?: IconaChip;
  area?: Area;
}) {
  const Icona = icon ?? (area ? areaIcona[area] : undefined);
  return (
    <div className="flex max-w-[520px] flex-col items-start gap-2 py-8">
      {Icona && <IconChip icon={Icona} area={area} size="lg" className="mb-2" />}
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
