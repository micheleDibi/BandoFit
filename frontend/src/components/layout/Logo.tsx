import logoIcona from "../../assets/logo-icona.png";
import logoOrizzontale from "../../assets/logo-orizzontale.png";
import logoVerticale from "../../assets/logo-verticale.png";
import { cn } from "../../lib/cn";

type Variant = "horizontal" | "vertical" | "icon";

const sources: Record<Variant, string> = {
  horizontal: logoOrizzontale,
  vertical: logoVerticale,
  icon: logoIcona,
};

const defaultSizes: Record<Variant, string> = {
  horizontal: "h-9 w-auto",
  vertical: "h-24 w-auto",
  icon: "h-9 w-auto",
};

export function Logo({
  variant = "horizontal",
  className,
}: {
  variant?: Variant;
  className?: string;
}) {
  return (
    <img
      src={sources[variant]}
      alt="BandoFit"
      className={cn("select-none", defaultSizes[variant], className)}
      draggable={false}
    />
  );
}
