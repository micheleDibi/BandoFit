import { Bookmark, Inbox, MessageSquare, Plus, Search, Users } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { CallCard } from "../components/partenariati/CallCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { CandidaturaCard } from "../components/partenariati/CandidaturaCard";
import { bandoCallSospeso, linkCall, passoDa } from "../components/partenariati/callDati";
import { FiltriBacheca, paginaDa, useFiltriBacheca } from "../components/partenariati/FiltriBacheca";
import { useRientroPagina } from "../components/partenariati/useRientroPagina";
import { AvvisoLimiteCall, RiepilogoLimiteCall, statoLimite, useLimiteCall } from "../components/partenariati/LimitiCall";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { Segment } from "../components/ui/Segment";
import { Select } from "../components/ui/Select";
import { Status } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TabPanel, Tabs, type Scheda } from "../components/ui/Tabs";
import { TextLink } from "../components/ui/TextLink";
import { LINK_SU_FASCIA } from "../components/shared/fascia";
import { LINK_ESTESO, SOPRA_LINK_ESTESO } from "../components/shared/linkEsteso";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useMieCall } from "../hooks/useCallPartenariato";
import { useCandidature } from "../hooks/useCandidature";
import { useConversazioni } from "../hooks/useConversazioni";
import { useCompany } from "../hooks/useCompany";
import { useBacheca, usePerTe, useRiepilogoPartenariati } from "../hooks/usePartenariati";
import { useTab } from "../hooks/useTab";
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

/** Le schede, nell'ordine della tavola: scoperta, salvate, poi le proprie. */
const VISTE: VistaPartenariati[] = [
  "per-te",
  "tutte",
  "salvate",
  "mie",
  "candidature",
  "conversazioni",
];
const STATI_CANDIDATURA = Object.keys(CANDIDATURE_COPY.stati) as StatoCandidatura[];
/** «Per te» è la vista di partenza: anche senza visibilità come partner. */
const VISTA_PREDEFINITA: VistaPartenariati = "per-te";
const PREFISSO_SCHEDE = "partenariati";

/** Una delle tue call: con il bando sospeso e la call aperta, «Bando sospeso»
 *  accanto al bando (la call resta pubblicata ma in pausa). */
function RigaCall({ call }: { call: CallCardDati }) {
  const bozza = call.stato === "bozza";
  const passo = passoDa(null, call.wizard_passo ?? 1);
  const sospeso = (bozza || call.stato === "pubblicata") && bandoCallSospeso(call.bando);
  return (
    <li>
      <Card interattiva area="partenariati" className="relative flex items-start gap-6">
        <div className="flex min-w-0 grow flex-col gap-1">
          <h3 className="font-sans text-row-title text-ink">
            <Link to={linkCall(call)} className={`rounded-mark hover:text-accent-hover ${LINK_ESTESO}`}>
              {call.titolo || "Call senza titolo"}
            </Link>
          </h3>
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-body text-ink-2">
            <span>Bando: {call.bando.titolo}</span>
            {sospeso && <Status tono="attenzione">{CALL_COPY.bandoSospesoChip}</Status>}
          </p>
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
            <CallStatoBadge stato={call.stato} />
            {call.scadenza_call && call.stato === "pubblicata" && (
              <span>Candidature fino al {formatDate(call.scadenza_call)}</span>
            )}
            <span>
              {bozza
                ? `Bozza ferma al passo ${passo} di 7: ${CALL_COPY.passi[passo - 1]}`
                : `${call.posizioni_n} ${call.posizioni_n === 1 ? "posizione" : "posizioni"}`}
            </span>
            {!bozza && (
              <span>
                {call.requisiti_cercati_n}{" "}
                {call.requisiti_cercati_n === 1 ? "requisito cercato" : "requisiti cercati"}
              </span>
            )}
            {call.updated_at && <span>Aggiornata il {formatDate(call.updated_at)}</span>}
          </p>
        </div>
        <LinkButton
          to={linkCall(call)}
          variant="secondary"
          size="sm"
          aria-label={`${bozza ? "Riprendi" : "Apri"}: ${call.titolo || call.bando.titolo}`}
          className={SOPRA_LINK_ESTESO}
        >
          {bozza ? "Riprendi" : "Apri"}
        </LinkButton>
      </Card>
    </li>
  );
}

function ListaInCaricamento() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

/** Pagina corrente (`?page=`) con il cambio che scrive i searchParams. */
function usePagina() {
  const [params, setParams] = useSearchParams();
  const pagina = paginaDa(params);
  const scrivi = (n: number, replace: boolean) =>
    setParams(
      (prima) => {
        const dopo = new URLSearchParams(prima);
        if (n > 1) dopo.set("page", String(n));
        else dopo.delete("page");
        return dopo;
      },
      { replace },
    );
  const vaiA = (n: number) => {
    scrivi(n, false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  /** Il rientro di `useRientroPagina`: senza voce nella cronologia né scroll. */
  const rientra = (n: number) => scrivi(n, true);
  return { pagina, vaiA, rientra };
}

/** Numero dei risultati, annunciato ai lettori di schermo quando cambia. */
function Conteggio({ totale, inAggiornamento }: { totale: number; inAggiornamento: boolean }) {
  return (
    <p className="text-small text-ink-3" role="status" aria-live="polite">
      {inAggiornamento ? "Aggiornamento…" : totale === 1 ? "1 call" : `${totale} call`}
    </p>
  );
}

/** Le righe del registro: una card per riga, sul piano. */
function Registro({
  children,
  inAggiornamento = false,
}: {
  children: React.ReactNode;
  inAggiornamento?: boolean;
}) {
  return (
    <ul
      className={`flex flex-col gap-3 transition-opacity ${inAggiornamento ? "opacity-60" : ""}`}
      aria-busy={inAggiornamento}
    >
      {children}
    </ul>
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
        icon={Users}
        area="partenariati"
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
    <div className="flex flex-col gap-3">
      <Registro>
        {data.items.map((c) => (
          <RigaCall key={c.id} call={c} />
        ))}
      </Registro>
      {data.total > data.items.length && (
        <p className="text-small text-ink-3">
          Mostriamo le {data.items.length} call più recenti su {data.total}.
        </p>
      )}
    </div>
  );
}

/** «Per te»: le call che la tua azienda può aiutare a completare, anche
 *  senza visibilità come partner (con l'invito ad attivarla). */
function PerTe({ editable, onVista }: { editable: boolean; onVista: (v: VistaPartenariati) => void }) {
  const { pagina, vaiA, rientra } = usePagina();
  const perTe = usePerTe(pagina);
  const inRientro = useRientroPagina(perTe.data, pagina, perTe.isPlaceholderData, rientra);

  if (perTe.isPending || inRientro) return <ListaInCaricamento />;
  if (perTe.isError) {
    if (apiErrorCode(perTe.error) === "azienda_mancante") {
      return (
        <EmptyState
          area="azienda"
          title="Serve un'azienda"
          description="«Per te» confronta le call con i dati della tua azienda: inseriscili o importali dalla partita IVA."
          action={<LinkButton to="/app/azienda">Dati azienda</LinkButton>}
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
    <div className="flex flex-col gap-4">
      <BannerOptIn optIn={dati.opt_in} />
      {dati.items.length === 0 ? (
        <EmptyState
          icon={Search}
          area="partenariati"
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
          <p className="text-body text-ink-2">
            Le call di altre aziende che cercano qualcosa che la tua azienda ha: prima le più
            adatte.
          </p>
          <Conteggio totale={dati.total} inAggiornamento={perTe.isPlaceholderData} />
          <Registro inAggiornamento={perTe.isPlaceholderData}>
            {dati.items.map((c) => (
              <CallCard key={c.id} call={c} editable={editable} />
            ))}
          </Registro>
          <Pagination page={dati.page} totalPages={dati.total_pages} onChange={vaiA} />
        </>
      )}
    </div>
  );
}

/** «Tutte le call» (filtri e ordine nei searchParams) e «Call salvate». */
function Bacheca({ vista, editable }: { vista: "tutte" | "salvate"; editable: boolean }) {
  const { pagina, vaiA, rientra } = usePagina();
  const filtriUrl = useFiltriBacheca();
  const lista = useBacheca(vista, filtriUrl.filtri, pagina);
  const inRientro = useRientroPagina(lista.data, pagina, lista.isPlaceholderData, rientra);
  const tutte = vista === "tutte";
  const titoloBando = filtriUrl.filtri.bando ? (lista.data?.items[0]?.bando.titolo ?? null) : null;

  let corpo;
  if (lista.isPending || inRientro) {
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
        <EmptyState
          icon={Search}
          area="partenariati"
          title="Nessuna call con questi filtri"
          description="Prova a togliere qualche filtro."
        />
      ) : (
        <EmptyState
          icon={Users}
          area="partenariati"
          title="Ancora nessuna call aperta"
          description="Per ora nessun'altra azienda ha pubblicato call aperte a tutti. Torna a trovarci più avanti."
        />
      )
    ) : (
      <EmptyState
        icon={Bookmark}
        area="partenariati"
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
      <div className="flex flex-col gap-3">
        <Conteggio totale={lista.data.total} inAggiornamento={lista.isPlaceholderData} />
        <Registro inAggiornamento={lista.isPlaceholderData}>
          {lista.data.items.map((c) => (
            <CallCard key={c.id} call={c} editable={editable} />
          ))}
        </Registro>
        <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {tutte ? (
        <FiltriBacheca {...filtriUrl} titoloBando={titoloBando} />
      ) : (
        <p className="text-body text-ink-2">{BACHECA_COPY.salvaAiuto}</p>
      )}
      {corpo}
    </div>
  );
}

/** «Candidature e inviti»: ricevuti (default: ciò che c'è da decidere è lì)
 *  o mandati, con il filtro per stato; direzione, stato e pagina nei
 *  searchParams. */
function Candidature({ editable }: { editable: boolean }) {
  const [params, setParams] = useSearchParams();
  const { pagina, vaiA, rientra } = usePagina();
  const direzione: DirezioneCandidature = params.get("direzione") === "inviate" ? "inviate" : "ricevute";
  const statoRichiesto = params.get("stato") as StatoCandidatura | null;
  const stato = statoRichiesto && STATI_CANDIDATURA.includes(statoRichiesto) ? statoRichiesto : null;
  const lista = useCandidature({ direzione, stato }, pagina);
  const inRientro = useRientroPagina(lista.data, pagina, lista.isPlaceholderData, rientra);

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
  if (lista.isPending || inRientro) {
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
      <EmptyState
        icon={Inbox}
        area="partenariati"
        title="Nessuna con questo stato"
        description="Prova a scegliere «Tutte»."
      />
    ) : direzione === "ricevute" ? (
      <EmptyState
        icon={Inbox}
        area="partenariati"
        title="Niente da decidere per ora"
        description="Qui arrivano le candidature delle altre aziende alle tue call e gli inviti che la tua azienda riceve."
      />
    ) : (
      <EmptyState
        icon={Inbox}
        area="partenariati"
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
      <div className="flex flex-col gap-3">
        <p className="text-small text-ink-3" role="status" aria-live="polite">
          {lista.isPlaceholderData ? "Aggiornamento…" : lista.data.total === 1 ? "1 risultato" : `${lista.data.total} risultati`}
        </p>
        <ul
          className={`flex flex-col gap-3 transition-opacity ${lista.isPlaceholderData ? "opacity-60" : ""}`}
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
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Segment
          ariaLabel="Quali mostrare"
          opzioni={[
            { id: "ricevute", label: "Ricevute" },
            { id: "inviate", label: "Inviate" },
          ]}
          valore={direzione}
          onChange={(d: DirezioneCandidature) => aggiorna("direzione", d === "inviate" ? d : null)}
        />
        <div className="flex items-center gap-2">
          <span className="text-small text-ink-3" aria-hidden>
            Stato
          </span>
          <Select
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
          </Select>
        </div>
      </div>
      <p className="text-body text-ink-2">
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
      <Card interattiva area="partenariati" className="relative flex items-start gap-6">
        <div className="flex min-w-0 grow flex-col gap-1">
          <h3 className="font-sans text-row-title text-ink">
            <Link to={link} className={`rounded-mark hover:text-accent-hover ${LINK_ESTESO}`}>
              {nome}
              {c.controparte.pseudonimo && (
                <span className="ml-2 text-small font-normal text-ink-3">{c.controparte.pseudonimo}</span>
              )}
            </Link>
          </h3>
          <p className="text-body text-ink-2">
            {c.lato === "creatore" ? "Sulla tua call" : "Sulla call"} «{c.call.titolo || "Call senza titolo"}»
          </p>
          <p className="text-small text-ink-2">Bando: {c.call.bando.titolo}</p>
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
            {c.non_letti > 0 && (
              <Badge tone="info">{c.non_letti === 1 ? "1 messaggio non letto" : `${c.non_letti} messaggi non letti`}</Badge>
            )}
            {c.stato === "chiusa" && <Status tono="chiuso">Chiusa</Status>}
            {c.stato === "aperta" && !c.controparte.attiva && <Status tono="neutro">Sola lettura</Status>}
            <span>
              {c.ultimo_messaggio_at
                ? `Ultimo messaggio: ${formatDateTime(c.ultimo_messaggio_at)}`
                : c.created_at
                  ? `Aperta il ${formatDate(c.created_at)}, ancora senza messaggi`
                  : "Ancora senza messaggi"}
            </span>
          </p>
        </div>
        <LinkButton
          to={link}
          variant="secondary"
          size="sm"
          aria-label={`Apri la conversazione con ${nome}`}
          className={SOPRA_LINK_ESTESO}
        >
          Apri
        </LinkButton>
      </Card>
    </li>
  );
}

/** «Conversazioni»: quelle dell'azienda attiva, dalla più recente, con i
 *  non letti (si aggiornano da sole ogni minuto). */
function Conversazioni() {
  const { pagina, vaiA, rientra } = usePagina();
  const lista = useConversazioni(pagina);
  const inRientro = useRientroPagina(lista.data, pagina, lista.isPlaceholderData, rientra);
  if (lista.isPending || inRientro) return <ListaInCaricamento />;
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
        icon={MessageSquare}
        area="partenariati"
        title="Nessuna conversazione"
        description="Una conversazione si apre quando una candidatura o un invito viene accettato."
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <Registro>
        {lista.data.items.map((c) => (
          <RigaConversazione key={c.id} conversazione={c} />
        ))}
      </Registro>
      <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
    </div>
  );
}

/** Solo i numeri maggiori di zero (un contatore a zero non dice nulla). */
const seMaggioreDiZero = (n: number | undefined) => (n && n > 0 ? n : undefined);

/** Pagina del modulo partenariati. Le schede stanno in `?tab=`; il vecchio
 *  `?vista=per-te|tutte|mie|salvate|candidature|conversazioni` resta un alias. */
export default function Partenariati() {
  const { avviso } = useAziendaDaLink();
  const { data: azienda } = useCompany();
  const limite = useLimiteCall();
  const mie = useMieCall();
  const riepilogo = useRiepilogoPartenariati();
  const { tab: vista, setTab: cambiaVista } = useTab(VISTE, {
    alias: "vista",
    default: VISTA_PREDEFINITA,
  });
  const editable = azienda?.editable ?? false;
  const puoCreare = editable && statoLimite(limite) !== "non_incluso";

  const schede: Scheda<VistaPartenariati>[] = [
    { id: "per-te", label: BACHECA_COPY.viste["per-te"] },
    { id: "tutte", label: BACHECA_COPY.viste.tutte },
    { id: "salvate", label: BACHECA_COPY.viste.salvate, count: riepilogo.data?.salvate },
    { id: "mie", label: BACHECA_COPY.viste.mie, count: mie.data?.total },
    {
      id: "candidature",
      label: BACHECA_COPY.viste.candidature,
      count: seMaggioreDiZero(
        (riepilogo.data?.inviti_ricevuti ?? 0) + (riepilogo.data?.candidature_da_decidere ?? 0),
      ),
    },
    {
      id: "conversazioni",
      label: BACHECA_COPY.viste.conversazioni,
      count: seMaggioreDiZero(riepilogo.data?.messaggi_non_letti),
    },
  ];

  return (
    <Page variante="elenco">
      <PageHeader
        area="partenariati"
        titolo="Partenariati"
        descrizione="Trova le call delle aziende che cercano partner per un bando, oppure pubblica la tua in forma anonima e scegli tu con chi parlare."
        azioni={
          <>
            <TextLink to="/app/azienda#partner" className={LINK_SU_FASCIA}>
              Vedi il profilo partner
            </TextLink>
            {puoCreare && (
              <LinkButton to="/app/partenariati/call/nuova" variant="inverse">
                <Plus className="size-4" aria-hidden />
                Crea una call
              </LinkButton>
            )}
          </>
        }
      />

      {avviso && <Alert tono="attenzione">{avviso}</Alert>}

      {vista === "mie" && (
        <div className="flex flex-col gap-2">
          <RiepilogoLimiteCall limite={limite} />
          <AvvisoLimiteCall limite={limite} editable={editable} />
        </div>
      )}

      <Tabs
        tabs={schede}
        attivo={vista}
        onChange={cambiaVista}
        ariaLabel="Viste dei partenariati"
        prefisso={PREFISSO_SCHEDE}
      />
      {/* `key={vista}`: ogni scheda riparte dal suo stato. */}
      <TabPanel key={vista} id={vista} attivo={vista} prefisso={PREFISSO_SCHEDE}>
        {vista === "per-te" && <PerTe editable={editable} onVista={cambiaVista} />}
        {(vista === "tutte" || vista === "salvate") && <Bacheca vista={vista} editable={editable} />}
        {vista === "mie" && <LeMieCall />}
        {vista === "candidature" && <Candidature editable={editable} />}
        {vista === "conversazioni" && <Conversazioni />}
      </TabPanel>
    </Page>
  );
}
