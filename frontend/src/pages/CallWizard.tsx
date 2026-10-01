import { useCallback, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { CallStepper } from "../components/partenariati/CallStepper";
import { callModificabile, NUMERO_PASSI, passoDa } from "../components/partenariati/callDati";
import { PassoAnteprima } from "../components/partenariati/PassoAnteprima";
import { PassoBando, PassoBandoNuova } from "../components/partenariati/PassoBando";
import type { PassoProps } from "../components/partenariati/passoComune";
import { PassoGap } from "../components/partenariati/PassoGap";
import { PassoPosizioni } from "../components/partenariati/PassoPosizioni";
import { PassoPubblica } from "../components/partenariati/PassoPubblica";
import { PassoRegole } from "../components/partenariati/PassoRegole";
import { PassoTesti } from "../components/partenariati/PassoTesti";
import { Alert } from "../components/ui/Alert";
import { LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { LINK_SU_FASCIA } from "../components/shared/fascia";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { isVistaCreatore, useCall } from "../hooks/useCallPartenariato";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CALL_COPY } from "../lib/copy";

const PASSI: Record<number, (p: PassoProps) => ReactElement> = {
  1: PassoBando,
  2: PassoRegole,
  3: PassoGap,
  4: PassoPosizioni,
  5: PassoTesti,
  6: PassoAnteprima,
  7: PassoPubblica,
};

const DESCRIZIONI: Record<number, string> = {
  1: "Il bando per cui cerchi partner e il tuo ruolo.",
  2: "Conferma o correggi le regole di partenariato del bando.",
  3: "Budget, requisiti del bando e cosa cerchi nei partner.",
  4: "Le posizioni che vuoi coprire con i partner.",
  5: "I testi che vedranno le altre aziende.",
  6: "La call come la vedranno le altre aziende.",
  7: "Scadenza, visibilità e pubblicazione.",
};

const RITORNO = { label: "Partenariati", to: "/app/partenariati?tab=mie" };

/** Intestazione del passo: «Passo N di 7», il titolo (che riceve il focus al
 *  cambio di passo) e la descrizione. */
function TitoloPasso({
  passo,
  titoloRef,
}: {
  passo: number;
  titoloRef?: React.RefObject<HTMLHeadingElement>;
}) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-small font-medium text-area-partenariati-ink">
        Passo {passo} di {NUMERO_PASSI}
      </p>
      <h2 ref={titoloRef} tabIndex={-1} className="text-title-section text-ink outline-none">
        {CALL_COPY.passi[passo - 1]}
      </h2>
      <p className="text-body text-ink-2">{DESCRIZIONI[passo]}</p>
    </div>
  );
}

/** Wizard in 7 passi della call di partenariato. Lo stato vive nella bozza
 *  sul server (regge il ricaricamento); il passo è nei searchParams
 *  (`?passo=`) e a ogni cambio il focus va sul titolo del passo. Ogni passo
 *  salva prima di andare avanti; uscire con modifiche non salvate chiede
 *  conferma. Deep link `?azienda=` come le altre pagine del modulo. */
export default function CallWizard() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const { avviso } = useAziendaDaLink();
  const callQ = useCall(id);
  const [dirty, setDirty] = useState(false);
  const [uscita, setUscita] = useState<number | null>(null);
  const titoloPasso = useRef<HTMLHeadingElement>(null);
  const passoPrecedente = useRef<number | null>(null);

  const call = isVistaCreatore(callQ.data) ? callQ.data : undefined;
  const passo = passoDa(params.get("passo"), call?.wizard_passo ?? 1);
  const avvisoLink: ReactNode = avviso ? <Alert tono="attenzione">{avviso}</Alert> : null;

  // Focus sul titolo del passo quando cambia (non al primo caricamento).
  useEffect(() => {
    if (!call) return;
    if (passoPrecedente.current !== null && passoPrecedente.current !== passo) {
      titoloPasso.current?.focus();
      window.scrollTo({ top: 0 });
    }
    passoPrecedente.current = passo;
  }, [passo, call]);

  // Ricaricare o chiudere la scheda con modifiche non salvate: avviso del browser.
  useEffect(() => {
    if (!dirty) return;
    const avvisa = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", avvisa);
    return () => window.removeEventListener("beforeunload", avvisa);
  }, [dirty]);

  const vaiSubito = useCallback(
    (n: number) => {
      setDirty(false);
      setParams((p) => {
        const nuovi = new URLSearchParams(p);
        nuovi.set("passo", String(n));
        return nuovi;
      });
    },
    [setParams],
  );
  const vai = (n: number) => {
    if (n === passo) return;
    if (dirty) setUscita(n);
    else vaiSubito(n);
  };

  // ---- Nuova call: solo il passo 1, senza id ----
  if (!id) {
    return (
      <Page variante="flusso">
        <PageHeader area="partenariati" indietro={RITORNO} titolo="Nuova call di partenariato" />
        {avvisoLink}
        <CallStepper passo={1} salvatiFinoA={1} abilitato={(n) => n === 1} onVai={() => undefined} />
        <Card className="flex flex-col gap-6 sm:p-8">
          <TitoloPasso passo={1} />
          <PassoBandoNuova
            slug={params.get("bando")}
            onScegliBando={(slug) =>
              setParams((p) => {
                const nuovi = new URLSearchParams(p);
                if (slug) nuovi.set("bando", slug);
                else nuovi.delete("bando");
                return nuovi;
              })
            }
          />
        </Card>
      </Page>
    );
  }

  if (callQ.isPending) {
    return (
      <Page variante="flusso">
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </Page>
    );
  }
  if (callQ.isError) {
    return (
      <Page variante="flusso">
        {avvisoLink}
        {apiErrorCode(callQ.error) === "not_found" ? (
          <>
            <ErrorState
              title="Call non trovata"
              message="Non esiste, oppure è di un'altra azienda: controlla l'azienda attiva."
            />
            <div>
              <LinkButton to="/app/partenariati?tab=mie" variant="secondary">
                Le tue call
              </LinkButton>
            </div>
          </>
        ) : (
          <ErrorState
            message={apiErrorMessage(callQ.error, "Impossibile caricare la call.")}
            onRetry={() => void callQ.refetch()}
          />
        )}
      </Page>
    );
  }
  // Non è la vista del creatore (dal WP6: la call di un'altra azienda).
  // La query string resta (`?azienda=`, `?passo=`): se il deep link non è
  // ancora risolto (call letta con l'azienda attiva di prima), lo risolve la
  // pagina della call.
  if (!call) {
    return (
      <Navigate to={{ pathname: `/app/partenariati/call/${id}`, search: location.search }} replace />
    );
  }

  if (!callModificabile(call)) {
    return (
      <Page variante="flusso">
        <PageHeader area="partenariati" indietro={RITORNO} titolo={call.titolo || call.bando.titolo} />
        {avvisoLink}
        <EmptyState
          area="partenariati"
          title={call.editable ? "Questa call non si può più modificare" : "Non puoi modificare questa call"}
          description={
            call.editable
              ? `Stato: ${CALL_COPY.stati[call.stato] ?? call.stato}.`
              : CALL_COPY.soloTitolare
          }
          action={
            <LinkButton to={`/app/partenariati/call/${call.id}?tab=panoramica`} variant="secondary">
              Vai alla call
            </LinkButton>
          }
        />
      </Page>
    );
  }

  const Passo = PASSI[passo];
  const bozza = call.stato === "bozza";

  return (
    <Page variante="flusso">
      <PageHeader
        area="partenariati"
        indietro={RITORNO}
        titolo={bozza ? "Crea la call di partenariato" : "Modifica la call"}
        descrizione={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <CallStatoBadge stato={call.stato} />
            <span>
              Per il bando{" "}
              <TextLink to={`/app/bandi/${call.bando.slug}`} className={LINK_SU_FASCIA}>
                {call.bando.titolo}
              </TextLink>
            </span>
          </span>
        }
        azioni={
          !bozza ? (
            <TextLink to={`/app/partenariati/call/${call.id}?tab=panoramica`} className={LINK_SU_FASCIA}>
              Torna alla call
            </TextLink>
          ) : undefined
        }
      />
      {avvisoLink}
      <CallStepper
        passo={passo}
        salvatiFinoA={bozza ? call.wizard_passo : NUMERO_PASSI + 1}
        // In bozza si va avanti fino all'ultimo passo raggiunto; dopo la
        // pubblicazione tutti i passi sono consultabili.
        abilitato={(n) => !bozza || n <= Math.max(call.wizard_passo, passo)}
        onVai={vai}
      />
      <Card className="flex flex-col gap-6 sm:p-8">
        <TitoloPasso passo={passo} titoloRef={titoloPasso} />
        <Passo
          // Un passo nuovo riparte dai dati salvati (niente stato ereditato).
          key={`${call.id}-${passo}`}
          call={call}
          onAvanti={() => vaiSubito(Math.min(passo + 1, NUMERO_PASSI))}
          onIndietro={() => vai(Math.max(passo - 1, 1))}
          onDirty={setDirty}
          onVai={vai}
        />
      </Card>

      <ConfirmDialog
        open={uscita !== null}
        titolo="Uscire dal passo senza salvare?"
        conferma="Esci senza salvare"
        annulla="Resta qui"
        distruttiva
        onConferma={() => {
          const n = uscita;
          setUscita(null);
          if (n !== null) vaiSubito(n);
        }}
        onAnnulla={() => setUscita(null)}
      >
        <p>Le modifiche di questo passo non sono ancora salvate: se esci le perdi.</p>
      </ConfirmDialog>
    </Page>
  );
}
