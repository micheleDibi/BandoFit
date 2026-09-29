import { ShieldQuestion } from "lucide-react";
import { Link } from "react-router-dom";
import { useIdentitaAzienda } from "../../hooks/useIdentitaAzienda";
import { cn } from "../../lib/cn";
import { CHAT_COPY } from "../../lib/copy";
import { ANCORA_IDENTITA } from "./IdentitaAziendaBox";

/** Nota fissa della conversazione quando le identità non sono rivelate. Dal
 *  WP9 la rivelazione è simmetrica: avviene all'accettazione solo se tutte e
 *  due le aziende hanno l'identità verificata dalla piattaforma. Al titolare
 *  di un'azienda non verificata propone la verifica (per le prossime
 *  accettazioni). Testo fissato dal piano (`CHAT_COPY`). */
export function BannerIdentitaNonRivelata({ className }: { className?: string }) {
  const { data: identita } = useIdentitaAzienda();
  // Solo al titolare che può chiederla adesso (non già verificata né in attesa).
  const proponiVerifica = identita?.puo_richiedere === true;
  return (
    <p
      role="note"
      className={cn(
        "flex items-start gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-sm text-slate-700",
        className,
      )}
    >
      <ShieldQuestion className="mt-0.5 size-4 shrink-0 text-slate-500" aria-hidden />
      <span>
        {CHAT_COPY.identitaNonRivelata}
        {proponiVerifica && (
          <>
            {" "}
            <Link
              to={`/app/azienda#${ANCORA_IDENTITA}`}
              className="font-medium text-brand-600 hover:text-brand-700"
            >
              {CHAT_COPY.identitaVerificaCta} →
            </Link>
          </>
        )}
      </span>
    </p>
  );
}
