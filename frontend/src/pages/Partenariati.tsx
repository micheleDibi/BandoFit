import { ArrowRight, CalendarClock, EyeOff, Handshake, MessagesSquare, Plus } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { CallCard } from "../components/partenariati/CallCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { CandidaturaCard, SceltaDirezione } from "../components/partenariati/CandidaturaCard";
import { linkCall, passoDa } from "../components/partenariati/callDati";
import { FiltriBacheca, paginaDa, useFiltriBacheca } from "../components/partenariati/FiltriBacheca";
import { AvvisoLimiteCall, RiepilogoLimiteCall, statoLimite, useLimiteCall } from "../components/partenariati/LimitiCall";
import { Schede } from "../components/partenariati/Schede";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { SelectField } from "../components/ui/Field";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useMieCall } from "../hooks/useCallPartenariato";
import { useCandidature } from "../hooks/useCandidature";
import { useConversazioni } from "../hooks/useConversazioni";
import { useCompany } from "../hooks/useCompany";
import { useBacheca, usePerTe, useRiepilogoPartenariati } from "../hooks/usePartenariati";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { BACHECA_COPY, CALL_COPY, CANDIDATURE_COPY, PARTNER_COPY } from "../lib/copy";
import { formatDate, formatDateTime } from "../lib/format";
import type {
  CallCard as CallCardDati,
  ConversazioneCard,
  DirezioneCandidature,
  StatoCandidatura,
  VistaPartenariati,
} from "../types";

const VISTE: VistaPartenariati[] = [
  "per-te",
  "tutte",
  "mie",
  "salvate",
  "candidature",
  "conversazioni",
];
const STATI_CANDIDATURA = Object.keys(CANDIDATURE_COPY.stati) as StatoCandidatura[];
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

/** «Candidature»: candidature e inviti ricevuti (default: ciò che c'è da
 *  decidere è lì) o mandati, con il filtro per stato; direzione, stato e
 *  pagina nei searchParams. */
function Candidature({ editable }: { editable: boolean }) {
  const [params, setParams] = useSearchParams();
  const { pagina, vaiA } = usePagina();
  const direzione: DirezioneCandidature = params.get("direzione") === "inviate" ? "inviate" : "ricevute";
  const statoRichiesto = params.get("stato") as StatoCandidatura | null;
  const stato = statoRichiesto && STATI_CANDIDATURA.includes(statoRichiesto) ? statoRichiesto : null;
  const lista = useCandidature({ direzione, stato }, pagina);

  const aggiorna = (chiave: string, valore: string | null) =>
    setParams(
      (prima) => {
        const dopo = new URLSearchParams(prima);
        if (valore) dopo.set(chiave, valore);
        else dopo.delete(chiave);
        // I filtri cambiano la lista: si riparte dalla prima pagina.
        dopo.delete("page");
        return dopo;
      },
      { replace: true },
    );

  let corpo;
  if (lista.isPending) {
    corpo = <ListaInCaricamento />;
  } else if (lista.isError) {
    corpo = (
      <ErrorState
        message={apiErrorMessage(lista.error, "Impossibile caricare candidature e inviti.")}
        onRetry={() => void lista.refetch()}
      />
    );
  } else if (lista.data.items.length === 0) {
    corpo = stato ? (
      <EmptyState title="Nessuna con questo stato" description="Prova a scegliere «Tutte»." />
    ) : direzione === "ricevute" ? (
      <EmptyState
        title="Niente da decidere per ora"
        description="Qui arrivano le candidature delle altre aziende alle tue call e gli inviti che la tua azienda riceve."
      />
    ) : (
      <EmptyState
        title="Non hai ancora mandato candidature né inviti"
        description="Candidati alle call delle altre aziende, oppure invita le aziende suggerite dalle tue call."
        action={
          <LinkButton to="/app/partenariati?vista=per-te" variant="secondary">
            Vedi le call per te
          </LinkButton>
        }
      />
    );
  } else {
    corpo = (
      <div className="space-y-3">
        <p className="text-sm text-slate-500" role="status" aria-live="polite">
          {lista.isPlaceholderData ? "Aggiornamento…" : lista.data.total === 1 ? "1 risultato" : `${lista.data.total} risultati`}
        </p>
        <ul
          className={`space-y-3 transition-opacity ${lista.isPlaceholderData ? "opacity-60" : ""}`}
          aria-busy={lista.isPlaceholderData}
        >
          {lista.data.items.map((c) => (
            <CandidaturaCard key={c.id} candidatura={c} />
          ))}
        </ul>
        <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <SceltaDirezione
          valore={direzione}
          onChange={(d) => aggiorna("direzione", d === "inviate" ? d : null)}
          etichette={{ ricevute: "Ricevute", inviate: "Inviate" }}
        />
        <div className="w-full sm:w-56">
          <SelectField
            label="Stato"
            value={stato ?? ""}
            onChange={(e) => aggiorna("stato", e.target.value || null)}
          >
            <option value="">Tutti</option>
            {STATI_CANDIDATURA.map((s) => (
              <option key={s} value={s}>
                {CANDIDATURE_COPY.stati[s]}
              </option>
            ))}
          </SelectField>
        </div>
      </div>
      <p className="text-sm text-slate-500">
        {direzione === "ricevute"
          ? "Le candidature arrivate alle tue call e gli inviti ricevuti dalla tua azienda."
          : "Le candidature che hai mandato e gli inviti fatti dalle tue call."}
        {!editable && ` ${CANDIDATURE_COPY.soloTitolare}`}
      </p>
      {corpo}
    </div>
  );
}

/** Una conversazione nella lista: l'altra azienda (anonima), la call, i
 *  messaggi non letti in parole e numero. La riga è un `<li>`. */
function RigaConversazione({ conversazione: c }: { conversazione: ConversazioneCard }) {
  const nome = PARTNER_COPY.aziendaAnonima;
  const link = `/app/partenariati/conversazioni/${c.id}`;
  return (
    <li>
      <Card className="p-4 transition-colors hover:border-brand-300">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              {c.non_letti > 0 && (
                <Badge tone="brand">
                  {c.non_letti === 1 ? "1 messaggio non letto" : `${c.non_letti} messaggi non letti`}
                </Badge>
              )}
              {c.stato === "chiusa" && <Badge tone="slate">Chiusa</Badge>}
              {c.stato === "aperta" && !c.controparte.attiva && (
                <Badge tone="slate">Sola lettura</Badge>
              )}
            </div>
            <h3 className="mt-1.5 font-display text-base font-semibold text-slate-900">
              <Link
                to={link}
                className="inline-flex items-center gap-1.5 rounded hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                <EyeOff className="size-4 shrink-0 text-slate-400" aria-hidden />
                {nome}
                {c.controparte.pseudonimo && (
                  <span className="font-mono text-xs font-normal tracking-wide text-slate-400">
                    {c.controparte.pseudonimo}
                  </span>
                )}
              </Link>
            </h3>
            <p className="mt-0.5 text-sm text-slate-600">
              {c.lato === "creatore" ? "Sulla tua call" : "Sulla call"} «{c.call.titolo || "Call senza titolo"}» · Bando:{" "}
              {c.call.bando.titolo}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {c.ultimo_messaggio_at
                ? `Ultimo messaggio: ${formatDateTime(c.ultimo_messaggio_at)}`
                : c.created_at
                  ? `Aperta il ${formatDate(c.created_at)}, ancora senza messaggi`
                  : "Ancora senza messaggi"}
            </p>
          </div>
          <LinkButton to={link} variant="secondary" size="sm" aria-label={`Apri la conversazione con ${nome}`}>
            <MessagesSquare className="size-4" aria-hidden />
            Apri
          </LinkButton>
        </div>
      </Card>
    </li>
  );
}

/** «Conversazioni»: quelle dell'azienda attiva, dalla più recente, con i
 *  non letti (si aggiornano da sole ogni minuto). */
function Conversazioni() {
  const { pagina, vaiA } = usePagina();
  const lista = useConversazioni(pagina);
  if (lista.isPending) return <ListaInCaricamento />;
  if (lista.isError) {
    return (
      <ErrorState
        message={apiErrorMessage(lista.error, "Impossibile caricare le conversazioni.")}
        onRetry={() => void lista.refetch()}
      />
    );
  }
  if (lista.data.items.length === 0) {
    return (
      <EmptyState
        title="Nessuna conversazione"
        description="Una conversazione si apre quando una candidatura o un invito viene accettato."
      />
    );
  }
  return (
    <div className="space-y-3">
      <ul className="space-y-3">
        {lista.data.items.map((c) => (
          <RigaConversazione key={c.id} conversazione={c} />
        ))}
      </ul>
      <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
    </div>
  );
}

/** Solo i numeri maggiori di zero (un contatore a zero non dice nulla). */
const seMaggioreDiZero = (n: number | undefined) => (n && n > 0 ? n : undefined);

/** Pagina del modulo partenariati
 *  (`?vista=per-te|tutte|mie|salvate|candidature|conversazioni`). */
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
          {
            id: "candidature",
            etichetta: BACHECA_COPY.viste.candidature,
            conteggio: seMaggioreDiZero(
              (riepilogo.data?.inviti_ricevuti ?? 0) + (riepilogo.data?.candidature_da_decidere ?? 0),
            ),
          },
          {
            id: "conversazioni",
            etichetta: BACHECA_COPY.viste.conversazioni,
            conteggio: seMaggioreDiZero(riepilogo.data?.messaggi_non_letti),
          },
        ]}
        attiva={vista}
        onCambia={cambiaVista}
      >
        {vista === "per-te" && <PerTe editable={editable} onVista={cambiaVista} />}
        {(vista === "tutte" || vista === "salvate") && (
          <Bacheca key={vista} vista={vista} editable={editable} />
        )}
        {vista === "mie" && <LeMieCall />}
        {vista === "candidature" && <Candidature editable={editable} />}
        {vista === "conversazioni" && <Conversazioni />}
      </Schede>
    </div>
  );
}
