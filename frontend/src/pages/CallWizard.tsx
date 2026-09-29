import { Handshake } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
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
import { Button, LinkButton } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
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

function Intestazione({ titolo, sotto }: { titolo: string; sotto?: ReactNode }) {
  return (
    <div>
      <p className="text-sm text-slate-500">
        <Link to="/app/partenariati?vista=mie" className="font-medium text-brand-600 hover:text-brand-700">
          Partenariati
        </Link>{" "}
        / Call
      </p>
      <h1 className="mt-1 inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
        <Handshake className="size-6 text-brand-500" aria-hidden />
        {titolo}
      </h1>
      {sotto}
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
  const avvisoLink = avviso ? (
    <p role="status" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
      {avviso}
    </p>
  ) : null;

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
      <div className="mx-auto max-w-4xl space-y-5">
        <Intestazione
          titolo="Nuova call di partenariato"
          sotto={<p className="mt-1 text-sm text-slate-500">{DESCRIZIONI[1]}</p>}
        />
        {avvisoLink}
        <CallStepper passo={1} salvatiFinoA={1} abilitato={(n) => n === 1} onVai={() => undefined} />
        <h2 className="font-display text-lg font-semibold text-slate-900">
          Passo 1 di {NUMERO_PASSI}: {CALL_COPY.passi[0]}
        </h2>
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
      </div>
    );
  }

  if (callQ.isPending) {
    return (
      <div className="mx-auto max-w-4xl space-y-4" aria-hidden>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (callQ.isError) {
    return (
      <div className="mx-auto max-w-4xl space-y-5">
        {avvisoLink}
        {apiErrorCode(callQ.error) === "not_found" ? (
          <EmptyState
            title="Call non trovata"
            description="Non esiste, oppure è di un'altra azienda: controlla l'azienda attiva."
            action={<LinkButton to="/app/partenariati?vista=mie">Le tue call</LinkButton>}
          />
        ) : (
          <ErrorState
            message={apiErrorMessage(callQ.error, "Impossibile caricare la call.")}
            onRetry={() => void callQ.refetch()}
          />
        )}
      </div>
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
      <div className="mx-auto max-w-4xl space-y-5">
        <Intestazione titolo={call.titolo || call.bando.titolo} />
        {avvisoLink}
        <EmptyState
          title={call.editable ? "Questa call non si può più modificare" : "Non puoi modificare questa call"}
          description={
            call.editable
              ? `Stato: ${CALL_COPY.stati[call.stato] ?? call.stato}.`
              : CALL_COPY.soloTitolare
          }
          action={<LinkButton to={`/app/partenariati/call/${call.id}?tab=panoramica`}>Vai alla call</LinkButton>}
        />
      </div>
    );
  }

  const Passo = PASSI[passo];
  const bozza = call.stato === "bozza";

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <Intestazione
        titolo={bozza ? "Crea la call di partenariato" : "Modifica la call"}
        sotto={
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <CallStatoBadge stato={call.stato} />
            <span>
              Per il bando{" "}
              <Link to={`/app/bandi/${call.bando.slug}`} className="font-medium text-brand-600 hover:text-brand-700">
                {call.bando.titolo}
              </Link>
            </span>
            {!bozza && (
              <Link
                to={`/app/partenariati/call/${call.id}?tab=panoramica`}
                className="font-medium text-brand-600 hover:text-brand-700"
              >
                Torna alla call
              </Link>
            )}
          </div>
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
      <div>
        <h2
          ref={titoloPasso}
          tabIndex={-1}
          className="font-display text-lg font-semibold text-slate-900 focus:outline-none"
        >
          Passo {passo} di {NUMERO_PASSI}: {CALL_COPY.passi[passo - 1]}
        </h2>
        <p className="mt-0.5 text-sm text-slate-500">{DESCRIZIONI[passo]}</p>
      </div>
      <Passo
        // Un passo nuovo riparte dai dati salvati (niente stato ereditato).
        key={`${call.id}-${passo}`}
        call={call}
        onAvanti={() => vaiSubito(Math.min(passo + 1, NUMERO_PASSI))}
        onIndietro={() => vai(Math.max(passo - 1, 1))}
        onDirty={setDirty}
        onVai={vai}
      />

      <Dialog
        open={uscita !== null}
        onClose={() => setUscita(null)}
        title="Uscire dal passo senza salvare?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setUscita(null)}>
              Resta qui
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                const n = uscita;
                setUscita(null);
                if (n !== null) vaiSubito(n);
              }}
            >
              Esci senza salvare
            </Button>
          </>
        }
      >
        <p>Le modifiche di questo passo non sono ancora salvate: se esci le perdi.</p>
      </Dialog>
    </div>
  );
}
