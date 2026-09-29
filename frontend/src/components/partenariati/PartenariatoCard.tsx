import { Handshake, Loader2 } from "lucide-react";
import { useMieCall } from "../../hooks/useCallPartenariato";
import { useCompany } from "../../hooks/useCompany";
import { useFunzioni } from "../../hooks/useFunzioni";
import { analisiInCorso, usePartenariatoBando } from "../../hooks/usePartenariatoBando";
import { apiErrorCode } from "../../lib/api";
import { PARTENARIATO_COPY } from "../../lib/copy";
import type { PartenariatoBando } from "../../types";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/states";
import { PARTENARIATO_CONTENUTO_ID } from "./ancora";
import { bandoAperto, callAperta, linkCall } from "./callDati";
import { ModalitaBadge } from "./ModalitaBadge";

function Stato({ dati }: { dati: PartenariatoBando }) {
  if (dati.regole) {
    const modalita = dati.regole.modalita_effettiva;
    return (
      <>
        <ModalitaBadge modalita={modalita} />
        <p className="mt-1.5 text-xs text-slate-500">
          {PARTENARIATO_COPY.modalitaSpiegazione[modalita]}
        </p>
        {dati.aggiornamento_in_corso && (
          <p className="mt-1.5 inline-flex items-center gap-1.5 text-xs text-amber-700">
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
            Aggiornamento in corso…
          </p>
        )}
      </>
    );
  }
  if (analisiInCorso(dati)) {
    return (
      <p className="inline-flex items-center gap-2 text-sm font-medium text-amber-700">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Analisi in corso…
      </p>
    );
  }
  if (dati.stato === "nessun_segnale") {
    return (
      <p className="text-sm text-slate-600">
        Nel testo che abbiamo letto non ci sono riferimenti a partenariati.
      </p>
    );
  }
  if (dati.stato === "errore") {
    return <p className="text-sm text-slate-600">L'ultima analisi delle regole non è riuscita.</p>;
  }
  return (
    <p className="text-sm text-slate-600">
      Scopri se questo bando ammette o richiede partner: leggiamo per te i documenti ufficiali.
    </p>
  );
}

/** «Crea call» per il titolare (bando aperto), oppure «Vai alla tua call» se
 *  l'azienda attiva ne ha già una non chiusa su questo bando. */
function CtaCall({ slug, dati }: { slug: string; dati: PartenariatoBando }) {
  const mie = useMieCall();
  const { data: azienda } = useCompany();
  const esistente = (mie.data?.items ?? []).find(
    (c) => c.mia && c.bando.slug === slug && callAperta(c.stato),
  );
  if (esistente) {
    return (
      <LinkButton to={linkCall(esistente)} size="sm" className="mt-2 w-full">
        Vai alla tua call
      </LinkButton>
    );
  }
  // Senza lo stato del bando si mostra: il wizard ricontrolla e spiega.
  const aperto = dati.stato_bando === null || bandoAperto(dati.stato_bando);
  if (mie.isPending || !azienda?.editable || !aperto) return null;
  return (
    <LinkButton
      to={`/app/partenariati/call/nuova?bando=${encodeURIComponent(slug)}`}
      size="sm"
      className="mt-2 w-full"
    >
      Crea call
    </LinkButton>
  );
}

/** Card compatta nella sidebar di BandoDetail: modalità di partecipazione in
 *  una parola, rimando alla sezione completa e CTA della call (WP5). Non
 *  esiste a modulo spento. «Cerca partner» arriva con il WP6. */
export function PartenariatoCard({
  slug,
  onVediRegole,
}: {
  slug: string;
  /** Apre la sezione «Regole di partenariato» e ci porta il focus. */
  onVediRegole: () => void;
}) {
  const { partenariatiAttivo } = useFunzioni();
  const { data, isPending, isError, error, refetch } = usePartenariatoBando(slug);

  // 404 = modulo spento lato server (o /me non ancora aggiornato): la card
  // sparisce invece di mostrare un errore che l'utente non può risolvere.
  if (!partenariatiAttivo || (isError && apiErrorCode(error) === "not_found")) return null;

  return (
    <Card className="border-brand-200 bg-gradient-to-b from-brand-50/70 to-white p-5">
      <h2 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-slate-900">
        <Handshake className="size-4 text-brand-500" aria-hidden />
        Partenariato
      </h2>

      {isPending ? (
        <div className="mt-3 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : isError ? (
        <div className="mt-3">
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            Impossibile caricare le regole di partenariato.
          </p>
          <Button variant="secondary" size="sm" className="mt-2 w-full" onClick={() => refetch()}>
            Riprova
          </Button>
        </div>
      ) : (
        <div className="mt-3">
          <Stato dati={data} />
          {data.calls_aperte > 0 && (
            <p className="mt-1.5 text-xs text-slate-500">
              {data.calls_aperte === 1
                ? "1 call di partenariato aperta su questo bando"
                : `${data.calls_aperte} call di partenariato aperte su questo bando`}
            </p>
          )}
          <Button
            variant="secondary"
            size="sm"
            className="mt-3 w-full"
            aria-controls={PARTENARIATO_CONTENUTO_ID}
            onClick={onVediRegole}
          >
            Vedi regole
          </Button>
          <CtaCall slug={slug} dati={data} />
        </div>
      )}
    </Card>
  );
}
