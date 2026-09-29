import { EyeOff, ShieldAlert, UserRoundPen } from "lucide-react";
import { useId, type ReactNode } from "react";
import { useCompany } from "../../hooks/useCompany";
import { usePartnerProfile } from "../../hooks/usePartnerProfile";
import { cn } from "../../lib/cn";
import { BACHECA_COPY } from "../../lib/copy";
import { LinkButton } from "../ui/Button";

/** Sotto questa completezza del profilo partner si invita a completarlo. */
const COMPLETEZZA_CONSIGLIATA = 60;
const SEZIONE_PARTNER = "/app/azienda#partner";

function Riquadro({
  icona,
  titolo,
  tono,
  children,
  azione,
}: {
  icona: ReactNode;
  titolo: string;
  tono: "brand" | "amber";
  children: ReactNode;
  azione?: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className={cn(
        "flex flex-wrap items-start gap-3 rounded-xl border px-4 py-3",
        tono === "brand" ? "border-brand-200 bg-brand-50/60" : "border-amber-200 bg-amber-50",
      )}
    >
      <div className={cn("mt-0.5", tono === "brand" ? "text-brand-600" : "text-amber-700")}>{icona}</div>
      <div className="min-w-0 flex-1">
        <h2 id={id} className="text-sm font-semibold text-slate-900">
          {titolo}
        </h2>
        <div className="mt-0.5 text-sm text-slate-600">{children}</div>
      </div>
      {azione && <div className="shrink-0 self-center">{azione}</div>}
    </section>
  );
}

/** Invito alla visibilità come partner, per «Per te» e per la vista pubblica
 *  di una call. «Per te» funziona anche senza (Q25): qui si spiega cosa
 *  cambia attivandola. Legge il profilo partner dell'azienda attiva; finché
 *  non arriva vale `optIn` del server («Per te»). Il bottone solo al
 *  titolare: i membri leggono chi può attivarla. Nulla da dire → niente. */
export function BannerOptIn({ optIn }: { optIn?: boolean }) {
  const profiloQ = usePartnerProfile();
  const { data: azienda } = useCompany();
  const profilo = profiloQ.data;
  const editable = profilo?.editable ?? azienda?.editable ?? false;
  const visibile = profilo ? profilo.visibile : optIn;

  if (profilo?.sospeso) {
    return (
      <Riquadro icona={<ShieldAlert className="size-5" aria-hidden />} titolo="Profilo partner sospeso" tono="amber">
        {BACHECA_COPY.sospeso}
      </Riquadro>
    );
  }
  if (visibile === false) {
    return (
      <Riquadro
        icona={<EyeOff className="size-5" aria-hidden />}
        titolo={BACHECA_COPY.optInTitolo}
        tono="brand"
        azione={
          editable ? (
            <LinkButton to={SEZIONE_PARTNER} size="sm">
              {BACHECA_COPY.optInCta}
            </LinkButton>
          ) : undefined
        }
      >
        <p>{BACHECA_COPY.optInTesto}</p>
        {!editable && <p className="mt-1 text-xs text-slate-500">{BACHECA_COPY.optInMembro}</p>}
      </Riquadro>
    );
  }
  if (profilo && profilo.visibile && profilo.completezza < COMPLETEZZA_CONSIGLIATA) {
    return (
      <Riquadro
        icona={<UserRoundPen className="size-5" aria-hidden />}
        titolo={BACHECA_COPY.completaTitolo}
        tono="brand"
        azione={
          editable ? (
            <LinkButton to={SEZIONE_PARTNER} size="sm" variant="secondary">
              {BACHECA_COPY.completaCta}
            </LinkButton>
          ) : undefined
        }
      >
        {BACHECA_COPY.completaTesto(profilo.completezza)}
      </Riquadro>
    );
  }
  return null;
}
