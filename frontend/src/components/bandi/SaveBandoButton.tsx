import { Bookmark, BookmarkCheck } from "lucide-react";
import { useSavedIds, useToggleSaved } from "../../hooks/useSavedBandi";
import { cn } from "../../lib/cn";
import { Button } from "../ui/Button";

interface SaveBandoButtonProps {
  bando: { id: number; slug: string };
  /** overlay = bottone rotondo da sovrapporre alla card; inline = bottone testuale. */
  variant?: "overlay" | "inline";
}

/** Toggle salva/rimuovi dai preferiti, con stato ottimista sul Set degli id. */
export function SaveBandoButton({ bando, variant = "overlay" }: SaveBandoButtonProps) {
  const { data: savedIds } = useSavedIds();
  const toggle = useToggleSaved();
  const saved = savedIds?.has(bando.id) ?? false;

  const handleClick = () => toggle.mutate({ bando, save: !saved });
  const label = saved ? "Rimuovi dai salvati" : "Salva bando";

  if (variant === "inline") {
    return (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-pressed={saved}
        onClick={handleClick}
      >
        {saved ? (
          <BookmarkCheck className="size-4 text-brand-600" aria-hidden />
        ) : (
          <Bookmark className="size-4" aria-hidden />
        )}
        {saved ? "Salvato" : "Salva"}
      </Button>
    );
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      aria-pressed={saved}
      aria-label={label}
      title={label}
      className={cn(
        "absolute right-3 top-3 z-10 cursor-pointer rounded-full border p-2 shadow-sm transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500",
        saved
          ? "border-brand-200 bg-brand-50 text-brand-600 hover:bg-brand-100"
          : "border-slate-200 bg-white/95 text-slate-400 hover:bg-slate-50 hover:text-brand-600",
      )}
    >
      {saved ? (
        <BookmarkCheck className="size-4" aria-hidden />
      ) : (
        <Bookmark className="size-4" aria-hidden />
      )}
    </button>
  );
}
