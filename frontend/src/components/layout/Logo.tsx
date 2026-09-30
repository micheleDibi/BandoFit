import eduNews24 from "../../assets/edunews24.png";
import logoIcona from "../../assets/logo-icona.png";
import logoOrizzontale from "../../assets/logo-orizzontale.png";
import { cn } from "../../lib/cn";

type Variant = "horizontal" | "vertical" | "icon" | "stack";

// Il brand è fornito come singolo lockup orizzontale: le varianti "vertical"
// (centrata) e "stack" (allineata a sinistra: barra laterale, barra mobile)
// riusano la stessa immagine impilando sotto l'attribuzione EduNews24.
const sources: Record<Variant, string> = {
  horizontal: logoOrizzontale,
  vertical: logoOrizzontale,
  icon: logoIcona,
  stack: logoOrizzontale,
};

/** Il PNG orizzontale (2482×918) ha un margine trasparente del 25% sopra e
 *  sotto e del 14% ai lati. Nelle varianti impilate lo si compensa con margini
 *  negativi calcolati sulla larghezza, così il riquadro dell'immagine coincide
 *  con il segno visibile e «powered by» gli sta a 6px:
 *  - stack: 176px di larghezza → immagine alta 65px, segno visibile ~32px;
 *  - vertical: 196px → immagine alta 72px, segno visibile ~36px. */
const defaultSizes: Record<Variant, string> = {
  horizontal: "h-9 w-auto",
  vertical: "-mx-7 -my-[18px] h-auto w-[196px]",
  icon: "h-9 w-auto",
  stack: "-mx-[25px] -my-4 h-auto w-44",
};

/** Attribuzione «powered by» con il marchio EduNews24 (segno visibile ~12px). */
function PoweredByEduNews24({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className="text-caption text-ink-3">powered by</span>
      <img
        src={eduNews24}
        alt="EduNews24"
        draggable={false}
        className="h-3.5 w-auto select-none"
      />
    </span>
  );
}

/** Lockup del brand: il logo BandoFit porta SEMPRE con sé l'attribuzione
 *  "powered by EduNews24" (accanto nell'orizzontale, sotto nelle impilate).
 *  L'attribuzione qui non è un link: il Logo è spesso già dentro un <Link>
 *  e un anchor annidato non sarebbe valido — il link a edunews24.it vive
 *  nel componente PoweredBy usato nella landing. */
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

  if (variant === "stack") {
    return (
      <span className="inline-flex flex-col items-start gap-1.5">
        {img}
        <PoweredByEduNews24 />
      </span>
    );
  }

  if (variant === "vertical") {
    return (
      <span className="inline-flex flex-col items-center gap-1.5">
        {img}
        <PoweredByEduNews24 />
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-2.5">
      {img}
      <span className="inline-flex flex-col items-start justify-center gap-1 border-l border-line pl-2.5">
        <span className="text-caption text-ink-3">powered by</span>
        <img
          src={eduNews24}
          alt="EduNews24"
          draggable={false}
          className="h-3 w-auto select-none"
        />
      </span>
    </span>
  );
}
