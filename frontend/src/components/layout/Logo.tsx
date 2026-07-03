import eduNews24 from "../../assets/edunews24.png";
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

/** Lockup del brand: il logo BandoFit porta SEMPRE con sé l'attribuzione
 *  "powered by EduNews24" (accanto nell'orizzontale, sotto nel verticale).
 *  L'attribuzione qui non è un link: il Logo è spesso già dentro un <Link>
 *  e un anchor annidato non sarebbe valido — il link a edunews24.it vive
 *  nel componente PoweredBy usato nei footer. */
export function Logo({
  variant = "horizontal",
  className,
}: {
  variant?: Variant;
  className?: string;
}) {
  const img = (
    <img
      src={sources[variant]}
      alt="BandoFit"
      className={cn("select-none", defaultSizes[variant], className)}
      draggable={false}
    />
  );

  if (variant === "icon") return img;

  if (variant === "vertical") {
    return (
      <span className="inline-flex flex-col items-center gap-2">
        {img}
        <span className="inline-flex items-center gap-1.5">
          <span className="text-[11px] leading-none text-slate-400">powered by</span>
          <img
            src={eduNews24}
            alt="EduNews24"
            draggable={false}
            className="h-3.5 w-auto select-none opacity-90"
          />
        </span>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2.5">
      {img}
      <span className="inline-flex flex-col items-start justify-center gap-1 border-l border-slate-200 pl-2.5">
        <span className="text-[9px] leading-none text-slate-400">powered by</span>
        <img
          src={eduNews24}
          alt="EduNews24"
          draggable={false}
          className="h-3 w-auto select-none opacity-90"
        />
      </span>
    </span>
  );
}
