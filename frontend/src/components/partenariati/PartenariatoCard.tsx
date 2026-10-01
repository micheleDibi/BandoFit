import { useMieCall } from "../../hooks/useCallPartenariato";
import { useCompany } from "../../hooks/useCompany";
import { useFunzioni } from "../../hooks/useFunzioni";
import { analisiInCorso, usePartenariatoBando } from "../../hooks/usePartenariatoBando";
import { apiErrorCode } from "../../lib/api";
import { PARTENARIATO_COPY } from "../../lib/copy";
import type { PartenariatoBando } from "../../types";
import { statoDelBando } from "../bandi/stato";
import { Button, LinkButton } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { Spinner } from "../ui/Spinner";
import { Skeleton } from "../ui/states";
import { PARTENARIATO_CONTENUTO_ID } from "./ancora";
import { bandoAperto, callAperta, linkCall } from "./callDati";
import { ModalitaBadge } from "./ModalitaBadge";

function Stato({ dati }: { dati: PartenariatoBando }) {
  if (dati.regole) {
    const modalita = dati.regole.modalita_effettiva;
    return (
      <div className="flex flex-col gap-1.5">
        <div>
          <ModalitaBadge modalita={modalita} />
        </div>
        <p className="text-body text-ink-2">{PARTENARIATO_COPY.modalitaSpiegazione[modalita]}</p>
        {dati.aggiornamento_in_corso && (
          <p className="inline-flex items-center gap-2 text-small text-ink-3">
            <Spinner size="sm" />
            Aggiornamento in corso…
          </p>
        )}
      </div>
    );
  }
  if (analisiInCorso(dati)) {
    return (
      <p className="inline-flex items-center gap-2 text-body font-medium text-ink">
        <Spinner size="sm" />
        Analisi in corso…
      </p>
    );
  }
  if (dati.stato === "nessun_segnale") {
    return (
      <p className="text-body text-ink-2">
        Nel testo che abbiamo letto non ci sono riferimenti a partenariati.
      </p>
    );
  }
  if (dati.stato === "errore") {
    return (
      <p className="text-body text-ink-2">L'ultima analisi delle regole non è riuscita.</p>
    );
  }
  return (
    <p className="text-body text-ink-2">
      Scopri se questo bando ammette o richiede partner: leggiamo per te i documenti ufficiali.
    </p>
  );
}

/** «Crea una call» per il titolare (bando aperto), oppure «Vai alla tua call»
 *  se l'azienda attiva ne ha già una non chiusa su questo bando. */
function CtaCall({ slug, dati }: { slug: string; dati: PartenariatoBando }) {
  const mie = useMieCall();
  const { data: azienda } = useCompany();
  const esistente = (mie.data?.items ?? []).find(
    (c) => c.mia && c.bando.slug === slug && callAperta(c.stato),
  );
  if (esistente) {
    return (
      <LinkButton to={linkCall(esistente)} variant="ghost" size="sm">
        Vai alla tua call
      </LinkButton>
    );
  }
  // Senza lo stato del bando si mostra: il wizard ricontrolla e spiega.
  const stato = statoDelBando(dati);
  const aperto = stato === null || bandoAperto(stato);
  if (mie.isPending || !azienda?.editable || !aperto) return null;
  return (
    <LinkButton
      to={`/app/partenariati/call/nuova?bando=${encodeURIComponent(slug)}`}
      variant="ghost"
      size="sm"
    >
      Crea una call
    </LinkButton>
  );
}

/** Pannello «Partenariato» nella colonna laterale della scheda del bando
 *  (rende il suo `Panel`): modalità di partecipazione in una parola, rimando
 *  alla sezione completa e la call (WP5). Rende `null` a modulo spento e con
 *  il 404 del server, così non resta un pannello vuoto. */
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

  // 404 = modulo spento lato server (o /me non ancora aggiornato): il blocco
  // sparisce invece di mostrare un errore che l'utente non può risolvere.
  if (!partenariatiAttivo || (isError && apiErrorCode(error) === "not_found")) return null;

  if (isPending) {
    return (
      <Panel titolo="Partenariato">
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-8 w-32" />
        </div>
      </Panel>
    );
  }

  if (isError) {
    return (
      <Panel titolo="Partenariato">
        <div className="flex flex-col gap-2">
          <InlineError>Non siamo riusciti a caricare le regole di partenariato.</InlineError>
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={() => refetch()}>
              Riprova
            </Button>
          </div>
        </div>
      </Panel>
    );
  }

  return (
    <Panel titolo="Partenariato">
      <div className="flex flex-col gap-3">
        <Stato dati={data} />
        {data.calls_aperte > 0 && (
          <p className="text-small text-ink-3">
            {data.calls_aperte === 1
              ? "1 call di partenariato aperta su questo bando"
              : `${data.calls_aperte} call di partenariato aperte su questo bando`}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            aria-controls={PARTENARIATO_CONTENUTO_ID}
            onClick={onVediRegole}
          >
            Vedi le regole
          </Button>
          <CtaCall slug={slug} dati={data} />
        </div>
      </div>
    </Panel>
  );
}
