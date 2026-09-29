import { ArrowRight, CalendarClock, Handshake, Plus } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { CallCard } from "../components/partenariati/CallCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { linkCall, passoDa } from "../components/partenariati/callDati";
import { FiltriBacheca, paginaDa, useFiltriBacheca } from "../components/partenariati/FiltriBacheca";
import { AvvisoLimiteCall, RiepilogoLimiteCall, statoLimite, useLimiteCall } from "../components/partenariati/LimitiCall";
import { Schede } from "../components/partenariati/Schede";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useMieCall } from "../hooks/useCallPartenariato";
import { useCompany } from "../hooks/useCompany";
import { useBacheca, usePerTe, useRiepilogoPartenariati } from "../hooks/usePartenariati";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { BACHECA_COPY, CALL_COPY } from "../lib/copy";
import { formatDate } from "../lib/format";
import type { CallCard as CallCardDati, VistaPartenariati } from "../types";

const VISTE: VistaPartenariati[] = ["per-te", "tutte", "mie", "salvate"];
/** «Per te» è la vista di partenza: anche senza visibilità come partner. */
const VISTA_PREDEFINITA: VistaPartenariati = "per-te";

function RigaCall({ call }: { call: CallCardDati }) {
  const bozza = call.stato === "bozza";
  const passo = passoDa(null, call.wizard_passo ?? 1);
  return (
    <li>
      <Card className="p-4 transition-colors hover:border-brand-300">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <CallStatoBadge stato={call.stato} />
              {call.scadenza_call && call.stato === "pubblicata" && (
                <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                  <CalendarClock className="size-3.5" aria-hidden />
                  Candidature fino al {formatDate(call.scadenza_call)}
                </span>
              )}
            </div>
            <h3 className="mt-1.5 font-display text-base font-semibold text-slate-900">
              <Link
                to={linkCall(call)}
                className="rounded hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                {call.titolo || "Call senza titolo"}
              </Link>
            </h3>
            <p className="mt-0.5 text-sm text-slate-600">Bando: {call.bando.titolo}</p>
            <p className="mt-1 text-xs text-slate-500">
              {bozza
                ? `Bozza ferma al passo ${passo} di 7: ${CALL_COPY.passi[passo - 1]}`
                : `${call.posizioni_n} ${call.posizioni_n === 1 ? "posizione" : "posizioni"} · ${call.requisiti_cercati_n} ${call.requisiti_cercati_n === 1 ? "requisito cercato" : "requisiti cercati"}`}
              {call.updated_at ? ` · aggiornata il ${formatDate(call.updated_at)}` : ""}
            </p>
          </div>
          <LinkButton to={linkCall(call)} variant="secondary" size="sm" aria-label={`${bozza ? "Riprendi" : "Apri"}: ${call.titolo || call.bando.titolo}`}>
            {bozza ? "Riprendi" : "Apri"}
            <ArrowRight className="size-4" aria-hidden />
          </LinkButton>
        </div>
      </Card>
    </li>
  );
}

function ListaInCaricamento() {
  return (
    <div className="space-y-3" aria-hidden>
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-28 w-full" />
    </div>
  );
}

/** Pagina corrente (`?page=`) con il cambio che scrive i searchParams. */
function usePagina() {
  const [params, setParams] = useSearchParams();
  const pagina = paginaDa(params);
  const vaiA = (n: number) => {
    setParams((prima) => {
      const dopo = new URLSearchParams(prima);
      if (n > 1) dopo.set("page", String(n));
      else dopo.delete("page");
      return dopo;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  return { pagina, vaiA };
}

/** Numero dei risultati, annunciato ai lettori di schermo quando cambia. */
function Conteggio({ totale, inAggiornamento }: { totale: number; inAggiornamento: boolean }) {
  return (
    <p className="text-sm text-slate-500" role="status" aria-live="polite">
      {inAggiornamento ? "Aggiornamento…" : totale === 1 ? "1 call" : `${totale} call`}
    </p>
  );
}

function LeMieCall() {
  const { data, isPending, isError, error, refetch } = useMieCall();
  if (isPending) return <ListaInCaricamento />;
  if (isError) {
    return (
      <ErrorState
        message={apiErrorMessage(error, "Impossibile caricare le tue call.")}
        onRetry={() => void refetch()}
      />
    );
  }
  if (data.items.length === 0) {
    return (
      <EmptyState
        title="Non hai ancora creato call"
        description="Una call di partenariato ti aiuta a trovare le aziende con cui partecipare a un bando. Parti dal bando che ti interessa."
        action={
          <LinkButton to="/app/bandi?partenariato=ammesso" variant="secondary">
            Cerca un bando
          </LinkButton>
        }
      />
    );
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-3">
        {data.items.map((c) => (
          <RigaCall key={c.id} call={c} />
        ))}
      </ul>
      {data.total > data.items.length && (
        <p className="text-xs text-slate-500">
          Mostriamo le {data.items.length} call più recenti su {data.total}.
        </p>
      )}
    </div>
  );
}

/** «Per te»: le call che la tua azienda può aiutare a completare, anche
 *  senza visibilità come partner (con l'invito ad attivarla). */
function PerTe({ editable, onVista }: { editable: boolean; onVista: (v: VistaPartenariati) => void }) {
  const { pagina, vaiA } = usePagina();
  const perTe = usePerTe(pagina);

  if (perTe.isPending) return <ListaInCaricamento />;
  if (perTe.isError) {
    if (apiErrorCode(perTe.error) === "azienda_mancante") {
      return (
        <EmptyState
          title="Serve un'azienda"
          description="«Per te» confronta le call con i dati della tua azienda: inseriscili o importali dalla partita IVA."
          action={<LinkButton to="/app/azienda">Dati aziendali</LinkButton>}
        />
      );
    }
    return (
      <ErrorState
        message={apiErrorMessage(perTe.error, "Impossibile caricare le call per te.")}
        onRetry={() => void perTe.refetch()}
      />
    );
  }
  const dati = perTe.data;
  return (
    <div className="space-y-4">
      <BannerOptIn optIn={dati.opt_in} />
      {dati.items.length === 0 ? (
        <EmptyState
          title="Per ora nessuna call cerca le tue competenze"
          description={
            dati.opt_in
              ? "Ti avvisiamo quando ne arriva una. Intanto puoi guardare tutte le call aperte."
              : "Attiva la visibilità come partner per ricevere un avviso quando ne arriva una. Intanto puoi guardare tutte le call aperte."
          }
          action={
            <Button variant="secondary" onClick={() => onVista("tutte")}>
              Vedi tutte le call
            </Button>
          }
        />
      ) : (
        <>
          <p className="text-sm text-slate-500">
            Le call di altre aziende che cercano qualcosa che la tua azienda ha: prima le più
            adatte.
          </p>
          <Conteggio totale={dati.total} inAggiornamento={perTe.isPlaceholderData} />
          <ul
            className={`space-y-3 transition-opacity ${perTe.isPlaceholderData ? "opacity-60" : ""}`}
            aria-busy={perTe.isPlaceholderData}
          >
            {dati.items.map((c) => (
              <CallCard key={c.id} call={c} editable={editable} />
            ))}
          </ul>
          <Pagination page={dati.page} totalPages={dati.total_pages} onChange={vaiA} />
        </>
      )}
    </div>
  );
}

/** «Tutte le call» (filtri e ordine nei searchParams) e «Salvate». */
function Bacheca({ vista, editable }: { vista: "tutte" | "salvate"; editable: boolean }) {
  const { pagina, vaiA } = usePagina();
  const filtriUrl = useFiltriBacheca();
  const lista = useBacheca(vista, filtriUrl.filtri, pagina);
  const tutte = vista === "tutte";
  const titoloBando = filtriUrl.filtri.bando ? (lista.data?.items[0]?.bando.titolo ?? null) : null;

  let corpo;
  if (lista.isPending) {
    corpo = <ListaInCaricamento />;
  } else if (lista.isError) {
    corpo = (
      <ErrorState
        message={apiErrorMessage(lista.error, "Impossibile caricare le call.")}
        onRetry={() => void lista.refetch()}
      />
    );
  } else if (lista.data.items.length === 0) {
    corpo = tutte ? (
      filtriUrl.attivi > 0 ? (
        <EmptyState title="Nessuna call con questi filtri" description="Prova a togliere qualche filtro." />
      ) : (
        <EmptyState
          title="Ancora nessuna call aperta"
          description="Per ora nessun'altra azienda ha pubblicato call aperte a tutti. Torna a trovarci più avanti."
        />
      )
    ) : (
      <EmptyState
        title="Nessuna call salvata"
        description={
          editable
            ? `Salva una call da «Tutte le call» o da «Per te». ${BACHECA_COPY.salvaAiuto}`
            : "Qui compaiono le call salvate dal titolare dell'azienda."
        }
      />
    );
  } else {
    corpo = (
      <div className="space-y-3">
        <Conteggio totale={lista.data.total} inAggiornamento={lista.isPlaceholderData} />
        <ul
          className={`space-y-3 transition-opacity ${lista.isPlaceholderData ? "opacity-60" : ""}`}
          aria-busy={lista.isPlaceholderData}
        >
          {lista.data.items.map((c) => (
            <CallCard key={c.id} call={c} editable={editable} />
          ))}
        </ul>
        <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {tutte ? (
        <FiltriBacheca {...filtriUrl} titoloBando={titoloBando} />
      ) : (
        <p className="text-sm text-slate-500">{BACHECA_COPY.salvaAiuto}</p>
      )}
      {corpo}
    </div>
  );
}

/** Pagina del modulo partenariati (`?vista=per-te|tutte|mie|salvate`). */
export default function Partenariati() {
  const [params, setParams] = useSearchParams();
  const { avviso } = useAziendaDaLink();
  const { data: azienda } = useCompany();
  const limite = useLimiteCall();
  const mie = useMieCall();
  const riepilogo = useRiepilogoPartenariati();
  const richiesta = params.get("vista") as VistaPartenariati | null;
  const vista: VistaPartenariati = richiesta && VISTE.includes(richiesta) ? richiesta : VISTA_PREDEFINITA;
  const editable = azienda?.editable ?? false;
  const puoCreare = editable && statoLimite(limite) !== "non_incluso";

  const cambiaVista = (v: VistaPartenariati) =>
    setParams(
      (p) => {
        const nuovi = new URLSearchParams(p);
        nuovi.set("vista", v);
        // La pagina è della vista: si riparte dalla prima.
        nuovi.delete("page");
        return nuovi;
      },
      { replace: true },
    );

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            <Handshake className="size-6 text-brand-500" aria-hidden />
            Partenariati
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Trova le call delle aziende che cercano partner per un bando, oppure pubblica la tua in
            forma anonima e scegli tu con chi parlare.{" "}
            <Link to="/app/azienda#partner" className="font-medium text-brand-600 hover:text-brand-700">
              Il tuo profilo partner
            </Link>
          </p>
        </div>
        {puoCreare && (
          <LinkButton to="/app/partenariati/call/nuova">
            <Plus className="size-4" aria-hidden />
            Crea una call
          </LinkButton>
        )}
      </div>

      {avviso && (
        <p role="status" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {avviso}
        </p>
      )}

      {vista === "mie" && (
        <div className="space-y-2">
          <RiepilogoLimiteCall limite={limite} />
          <AvvisoLimiteCall limite={limite} editable={editable} />
        </div>
      )}

      <Schede
        etichetta="Viste dei partenariati"
        schede={[
          { id: "per-te", etichetta: BACHECA_COPY.viste["per-te"] },
          { id: "tutte", etichetta: BACHECA_COPY.viste.tutte },
          { id: "mie", etichetta: BACHECA_COPY.viste.mie, conteggio: mie.data?.total },
          { id: "salvate", etichetta: BACHECA_COPY.viste.salvate, conteggio: riepilogo.data?.salvate },
        ]}
        attiva={vista}
        onCambia={cambiaVista}
      >
        {vista === "per-te" && <PerTe editable={editable} onVista={cambiaVista} />}
        {(vista === "tutte" || vista === "salvate") && (
          <Bacheca key={vista} vista={vista} editable={editable} />
        )}
        {vista === "mie" && <LeMieCall />}
      </Schede>
    </div>
  );
}
