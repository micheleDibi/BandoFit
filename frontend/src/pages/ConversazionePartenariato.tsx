import { Building2, EyeOff, Lock, MessagesSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { AntitrustBanner } from "../components/partenariati/AntitrustBanner";
import { BannerIdentitaNonRivelata } from "../components/partenariati/BannerIdentitaNonRivelata";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { ChatComposer } from "../components/partenariati/ChatComposer";
import { ChatThread } from "../components/partenariati/ChatThread";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useCompany } from "../hooks/useCompany";
import {
  useCaricaPrecedenti,
  useChiudiConversazione,
  useConversazione,
  useMessaggi,
  useSegnaLetto,
} from "../hooks/useConversazioni";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CHAT_COPY, PARTNER_COPY } from "../lib/copy";
import { formatDate } from "../lib/format";
import type { Conversazione, IdentitaRivelata } from "../types";

/** La scheda del browser è visibile (il «letto» si segna solo allora). */
function useSchedaVisibile(): boolean {
  const [visibile, setVisibile] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );
  useEffect(() => {
    const aggiorna = () => setVisibile(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", aggiorna);
    return () => document.removeEventListener("visibilitychange", aggiorna);
  }, []);
  return visibile;
}

/** Come si chiama l'altra azienda: la ragione sociale solo se l'identità è
 *  rivelata (oggi mai), altrimenti «Azienda anonima». */
function nomeControparte(c: Conversazione): string {
  return (c.identita_rivelata ? c.identita?.ragione_sociale : null) ?? PARTNER_COPY.aziendaAnonima;
}

/** Identità rivelata dopo l'accettazione (oggi spenta: non compare). Mai
 *  l'email personale del referente. */
function Identita({ identita }: { identita: IdentitaRivelata }) {
  const ruolo =
    identita.referente_ruolo === "titolare"
      ? "titolare"
      : identita.referente_ruolo === "referente"
        ? "referente"
        : null;
  return (
    <Card className="p-4">
      <h2 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Building2 className="size-4 text-brand-500" aria-hidden />
        {identita.ragione_sociale ?? "Dati dell'azienda"}
      </h2>
      <dl className="mt-2 space-y-1 text-sm">
        {identita.sito_web && (
          <div>
            <dt className="inline text-slate-500">Sito: </dt>
            <dd className="inline text-slate-700">{identita.sito_web}</dd>
          </div>
        )}
        {identita.pec && (
          <div>
            <dt className="inline text-slate-500">PEC: </dt>
            <dd className="inline text-slate-700">{identita.pec}</dd>
          </div>
        )}
        {identita.referente_nome && (
          <div>
            <dt className="inline text-slate-500">Referente: </dt>
            <dd className="inline text-slate-700">
              {identita.referente_nome}
              {ruolo ? ` (${ruolo})` : ""}
            </dd>
          </div>
        )}
      </dl>
    </Card>
  );
}

/** Chiusura (solo il titolare dell'azienda che ha creato la call). */
function ChiudiConversazione({ id }: { id: string }) {
  const chiudi = useChiudiConversazione(id);
  const [aperto, setAperto] = useState(false);
  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => setAperto(true)}>
        <Lock className="size-4" aria-hidden />
        Chiudi la conversazione
      </Button>
      <Dialog
        open={aperto}
        onClose={() => setAperto(false)}
        dismissible={!chiudi.isPending}
        title="Chiudere la conversazione?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAperto(false)} disabled={chiudi.isPending}>
              Non ora
            </Button>
            <Button
              variant="danger"
              loading={chiudi.isPending}
              onClick={() => chiudi.mutate(undefined, { onSuccess: () => setAperto(false) })}
            >
              Chiudi la conversazione
            </Button>
          </>
        }
      >
        <p>
          Nessuna delle due aziende potrà più scrivere. Lo storico dei messaggi resta consultabile.
          Non si può riaprire.
        </p>
        {chiudi.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(chiudi.error)}
          </p>
        )}
      </Dialog>
    </>
  );
}

/** Messaggi, «letto» e scrittura: montato quando la conversazione è nota. */
function Chat({ conversazione, puoScrivere }: { conversazione: Conversazione; puoScrivere: boolean }) {
  const messaggiQ = useMessaggi(conversazione.id);
  const precedenti = useCaricaPrecedenti(conversazione.id);
  const segnaLetto = useSegnaLetto(conversazione.id);
  const visibile = useSchedaVisibile();
  const lettoInviato = useRef(conversazione.letto_fino_a_id);
  const items = messaggiQ.data?.items ?? [];
  const ultimoAltri = items.reduce((massimo, m) => (!m.propria && m.id > massimo ? m.id : massimo), 0);
  const ultimo = items.length ? items[items.length - 1].id : 0;

  // Si segna letto fino all'ultimo messaggio in pagina, solo a scheda
  // visibile e solo se c'è qualcosa di nuovo dell'altra azienda.
  const { mutate: segna } = segnaLetto;
  useEffect(() => {
    if (!visibile || ultimo === 0 || ultimoAltri <= lettoInviato.current) return;
    lettoInviato.current = ultimo;
    segna(ultimo);
  }, [visibile, ultimo, ultimoAltri, segna]);

  // L'errore a tutta pagina solo se non ci sono ancora messaggi: un polling
  // fallito (rete che cade per qualche secondo) non smonta thread e
  // composer, così la bozza e la posizione restano.
  if (!messaggiQ.data) {
    if (messaggiQ.isError) {
      return (
        <ErrorState
          message={apiErrorMessage(messaggiQ.error, "Impossibile caricare i messaggi.")}
          onRetry={() => void messaggiQ.refetch()}
        />
      );
    }
    return (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-16 w-2/3" />
        <Skeleton className="ml-auto h-16 w-2/3" />
        <Skeleton className="h-16 w-1/2" />
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {messaggiQ.isRefetchError && (
        <p className="rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-800" role="status">
          Non riesco ad aggiornare i messaggi: riprovo tra poco.
        </p>
      )}
      <ChatThread
        messaggi={items}
        lettoFinoAId={conversazione.letto_fino_a_id}
        controparte={nomeControparte(conversazione)}
        haPrecedenti={messaggiQ.data.ha_precedenti}
        onCaricaPrecedenti={() => precedenti.mutate()}
        caricandoPrecedenti={precedenti.isPending}
        errorePrecedenti={precedenti.isError ? apiErrorMessage(precedenti.error) : null}
      />
      {puoScrivere && <ChatComposer conversazioneId={conversazione.id} />}
    </div>
  );
}

/** Pagina della conversazione (`/app/partenariati/conversazioni/:id`): le due
 *  aziende di una candidatura accettata si scrivono. Banner antitrust fisso
 *  e nota sull'identità non rivelata; scrive solo il titolare, e solo se la
 *  conversazione è aperta ed entrambe le aziende sono ancora attive (sennò
 *  sola lettura con il motivo). La chiude chi ha creato la call. */
export default function ConversazionePartenariato() {
  const { id } = useParams();
  const location = useLocation();
  const { avviso } = useAziendaDaLink();
  const { data: azienda } = useCompany();
  const conversazioneQ = useConversazione(id);
  const editable = azienda?.editable ?? false;
  const annuncioArrivo = (location.state as { annuncio?: string } | null)?.annuncio ?? null;
  // Una regione live già piena al montaggio non viene letta: il messaggio
  // arriva dopo, quando la regione (vuota) è già nella pagina.
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  useEffect(() => {
    setAnnuncio(null);
    if (!annuncioArrivo) return;
    const timer = window.setTimeout(() => setAnnuncio(annuncioArrivo), 150);
    return () => window.clearTimeout(timer);
  }, [annuncioArrivo, location.key]);

  let corpo;
  if (!conversazioneQ.data && !conversazioneQ.isError) {
    corpo = (
      <div className="space-y-4" aria-hidden>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  } else if (!conversazioneQ.data) {
    // Errore a tutta pagina solo al primo caricamento: una rilettura fallita
    // lascia la conversazione (e la bozza nel composer) com'era.
    corpo =
      apiErrorCode(conversazioneQ.error) === "not_found" ? (
        <EmptyState
          title="Conversazione non trovata"
          description="Non esiste oppure non riguarda la tua azienda. Se gestisci più aziende, controlla quella attiva."
          action={<LinkButton to="/app/partenariati?vista=conversazioni">Le tue conversazioni</LinkButton>}
        />
      ) : (
        <ErrorState
          message={apiErrorMessage(conversazioneQ.error, "Impossibile caricare la conversazione.")}
          onRetry={() => void conversazioneQ.refetch()}
        />
      );
  } else {
    const c = conversazioneQ.data;
    const nome = nomeControparte(c);
    const aperta = c.stato === "aperta";
    const attiva = c.controparte.attiva !== false;
    // Cosa si può fare lo decide il server; qui solo il perché della sola
    // lettura, in parole.
    const puoScrivere = c.puo_scrivere;
    const puoChiudere = c.puo_chiudere;
    const solaLettura = puoScrivere
      ? null
      : !aperta
        ? CHAT_COPY.chiusa
        : !attiva
          ? CHAT_COPY.controparteNonAttiva
          : !editable
            ? CHAT_COPY.soloTitolare
            : CHAT_COPY.solaLettura;

    corpo = (
      <div className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={aperta ? "emerald" : "slate"}>
                <span className="sr-only">Stato: </span>
                {aperta ? "Aperta" : "Chiusa"}
              </Badge>
              <CallStatoBadge stato={c.call.stato} />
            </div>
            <h1 className="mt-2 inline-flex flex-wrap items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
              {c.identita_rivelata ? (
                <Building2 className="size-6 text-brand-500" aria-hidden />
              ) : (
                <EyeOff className="size-6 text-slate-400" aria-hidden />
              )}
              {nome}
              {c.controparte.pseudonimo && (
                <span className="font-mono text-sm font-normal tracking-wide text-slate-400">
                  <span className="sr-only">riferimento </span>
                  {c.controparte.pseudonimo}
                </span>
              )}
            </h1>
            {c.lato === "partner" && (
              <p className="mt-0.5 text-sm text-slate-500">L'azienda che ha creato la call</p>
            )}
            <p className="mt-1 text-sm text-slate-600">
              {c.lato === "creatore" ? "Sulla tua call " : "Sulla call "}
              <Link
                to={`/app/partenariati/call/${c.call.id}`}
                className="font-medium text-brand-600 hover:text-brand-700"
              >
                {c.call.titolo || "Call senza titolo"}
              </Link>{" "}
              per il bando {c.call.bando.titolo}
              {c.chiusa_at ? ` · chiusa il ${formatDate(c.chiusa_at)}` : ""}
            </p>
          </div>
          {puoChiudere && <ChiudiConversazione id={c.id} />}
        </div>

        <AntitrustBanner />
        {c.identita_rivelata && c.identita ? (
          <Identita identita={c.identita} />
        ) : (
          <BannerIdentitaNonRivelata />
        )}
        {solaLettura && (
          <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700" role="note">
            {solaLettura}
          </p>
        )}

        <Chat key={c.id} conversazione={c} puoScrivere={puoScrivere} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <p className="text-sm text-slate-500">
        <Link
          to="/app/partenariati?vista=conversazioni"
          className="inline-flex items-center gap-1.5 font-medium text-brand-600 hover:text-brand-700"
        >
          <MessagesSquare className="size-4" aria-hidden />
          Conversazioni
        </Link>
      </p>
      {avviso && (
        <p role="status" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {avviso}
        </p>
      )}
      <div role="status" aria-live="polite">
        {annuncio && (
          <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{annuncio}</p>
        )}
      </div>
      {corpo}
    </div>
  );
}
