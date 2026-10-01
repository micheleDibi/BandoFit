import { Bookmark } from "lucide-react";
import { useSavedIds, useToggleSaved } from "../../hooks/useSavedBandi";
import { cn } from "../../lib/cn";
import { Button } from "../ui/Button";
import { IconButton } from "../ui/IconButton";

interface SaveBandoButtonProps {
  bando: { id: number; slug: string };
  /** riga = segnalibro di sola icona nel registro; inline = bottone con la
   *  parola (scheda del bando). */
  variant?: "inline" | "riga";
}

/** Toggle salva/rimuovi dai bandi salvati, con stato ottimista sul Set degli
 *  id. Da salvato il segnalibro è pieno e in `accent`. */
export function SaveBandoButton({ bando, variant = "riga" }: SaveBandoButtonProps) {
  const { data: savedIds } = useSavedIds();
  const toggle = useToggleSaved();
  const saved = savedIds?.has(bando.id) ?? false;

  const handleClick = () => toggle.mutate({ bando, save: !saved });
  const label = saved ? "Rimuovi dai bandi salvati" : "Salva il bando";

  if (variant === "inline") {
    return (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-pressed={saved}
        onClick={handleClick}
      >
        <Bookmark className={cn("size-4", saved && "fill-current text-accent")} aria-hidden />
        {saved ? "Salvato" : "Salva il bando"}
      </Button>
    );
  }

  return (
    <IconButton
      label={label}
      icon={<Bookmark className={cn(saved && "fill-current")} />}
      aria-pressed={saved}
      onClick={handleClick}
      className={cn("self-start", saved && "text-accent hover:text-accent-hover")}
    />
  );
}
