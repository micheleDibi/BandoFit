import { CalendarClock, Inbox, UserRound } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  ConsulenzaStatoBadge,
  ConsultoCallBadge,
  linkCallDelConsulto,
  PropostaStatoBadge,
} from "./Consulenze";
import { orarioAppuntamento } from "../components/consulenze/formato";
import { SlotPicker } from "../components/consulenze/SlotPicker";
import { VideocallButton } from "../components/consulenze/VideocallButton";
import { Avatar } from "../components/ui/Avatar";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { FOCUS_SU_FASCIA } from "../components/shared/fascia";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Panel } from "../components/ui/Panel";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useToast } from "../components/ui/Toast";
import {
  useAccettaProposta,
  useAnnullaConsulenza,
  useAnnullaPrenotazione,
  useConsulenza,
  usePrenotaSlot,
  useRifiutaProposta,
} from "../hooks/useConsulenze";
import { useFunzioni } from "../hooks/useFunzioni";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CONSULTO_CALL_COPY } from "../lib/copy";
import { formatDate, formatDateTime } from "../lib/format";
import type { Proposta, PropostaStato } from "../types";

const INDIETRO = { label: "Consulenze", to: "/app/consulenze" };

// Bordo sinistro della card di una proposta nel colore del suo stato (lo stesso
// di `PropostaStatoBadge`): in attesa accent, accettata fit, chiusa neutra.
const BORDO_PROPOSTA: Record<PropostaStato, string> = {
  inviata: "border-l-4 border-l-accent",
  accettata: "border-l-4 border-l-fit",
  rifiutata: "border-l-4 border-l-line-control",
  superata: "border-l-4 border-l-line-control",
  ritirata: "border-l-4 border-l-line-control",
};

// Il pulsante testuale delle azioni che annullano qualcosa.
const TESTUALE_DISTRUTTIVO = "text-danger hover:bg-danger-soft";

export default function ConsulenzaDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: consulenza, isPending, isError, error, refetch } = useConsulenza(id);
  const { partenariatiAttivo } = useFunzioni();
  const toast = useToast();

  const accetta = useAccettaProposta(id ?? "");
  const rifiuta = useRifiutaProposta(id ?? "");
  const annulla = useAnnullaConsulenza(id ?? "");
  const prenota = usePrenotaSlot(id ?? "");
  const annullaPrenotazione = useAnnullaPrenotazione(id ?? "");

  // Finestre: accettazione (con scelta slot), prenotazione post-assegnazione,
  // e le conferme di rifiuto, annullo dell'appuntamento e della richiesta.
  // L'errore di un'azione si mostra nella sua finestra e si azzera alla chiusura.
  const [accepting, setAccepting] = useState<Proposta | null>(null);
  const [bookingOpen, setBookingOpen] = useState(false);
  const [rejecting, setRejecting] = useState<Proposta | null>(null);
  const [cancelBookingOpen, setCancelBookingOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (isPending) {
    return (
      <Page
        variante="dettaglio"
        intestazione={
          <div className="flex flex-col gap-4" aria-hidden>
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-8 w-3/4" />
          </div>
        }
        laterale={<Skeleton className="h-40 w-full" />}
      >
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </Page>
    );
  }
  if (isError || !consulenza) {
    // Non trovata (anche di un'azienda che l'utente non vede): definitivo, senza «Riprova».
    const tornaAlleConsulenze = (
      <LinkButton to="/app/consulenze" variant="secondary">
        Torna alle consulenze
      </LinkButton>
    );
    return (
      <Page variante="sezioni">
        {apiErrorCode(error) === "not_found" ? (
          <EmptyState
            area="consulenze"
            title="Consulenza non trovata."
            description="L'indirizzo non corrisponde a nessuna delle tue consulenze."
            action={tornaAlleConsulenze}
          />
        ) : (
          <>
            <ErrorState
              title="Non siamo riusciti a caricare la consulenza."
              message={apiErrorMessage(error)}
              onRetry={() => refetch()}
            />
            <div>{tornaAlleConsulenze}</div>
          </>
        )}
      </Page>
    );
  }

  const { editable } = consulenza;
  const linkCall = linkCallDelConsulto(consulenza, partenariatiAttivo);
  const nomeProgettista = consulenza.progettista?.nome ?? null;

  const apri = (apriFinestra: () => void) => {
    setActionError(null);
    apriFinestra();
  };

  const handleAccept = async (slotId: string | null) => {
    if (!accepting || accetta.isPending) return;
    setActionError(null);
    try {
      await accetta.mutateAsync({ propostaId: accepting.id, slotId });
      setAccepting(null);
      toast.mostra({
        testo: slotId ? "Proposta accettata e appuntamento prenotato" : "Proposta accettata",
      });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleBook = async (slotId: string | null) => {
    if (!slotId || prenota.isPending) return;
    setActionError(null);
    try {
      await prenota.mutateAsync(slotId);
      setBookingOpen(false);
      toast.mostra({ testo: "Appuntamento prenotato" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleReject = async () => {
    if (!rejecting || rifiuta.isPending) return;
    setActionError(null);
    try {
      await rifiuta.mutateAsync(rejecting.id);
      setRejecting(null);
      toast.mostra({ testo: "Proposta rifiutata" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleCancelBooking = async () => {
    if (annullaPrenotazione.isPending) return;
    setActionError(null);
    try {
      await annullaPrenotazione.mutateAsync();
      setCancelBookingOpen(false);
      toast.mostra({ testo: "Appuntamento annullato" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleCancelRequest = async () => {
    if (annulla.isPending) return;
    setActionError(null);
    try {
      await annulla.mutateAsync();
      setCancelOpen(false);
      toast.mostra({ testo: "Richiesta annullata" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const errore = actionError && <InlineError className="mt-3">{actionError}</InlineError>;

  // Progettista e appuntamento: solo a consulenza assegnata. Su mobile sopra
  // le proposte: è la cosa da fare.
  const laterale =
    consulenza.stato === "assegnata" ? (
      <Panel
        titolo="Il tuo progettista"
        icon={UserRound}
        area="consulenze"
        className="order-first lg:order-none"
      >
        <div className="flex items-center gap-3">
          {nomeProgettista && <Avatar nome={nomeProgettista} />}
          <p className="font-semibold text-ink">{nomeProgettista ?? "—"}</p>
        </div>
        <div className="flex flex-col gap-2 border-t border-line pt-3">
          <h4 className="font-sans text-title-group text-ink">Appuntamento</h4>
          {consulenza.appuntamento ? (
            <>
              <p className="inline-flex items-start gap-2 text-body text-ink tabular-nums">
                <CalendarClock className="mt-0.5 size-4 shrink-0 text-area-consulenze-ink" aria-hidden />
                <time dateTime={consulenza.appuntamento.inizio}>
                  {orarioAppuntamento(consulenza.appuntamento)}
                </time>
              </p>
              {/* Non gated su editable: aprire/copiare il link non è una
                  mutazione, gli account collegati partecipano alla call. */}
              {consulenza.appuntamento.videocall_url && (
                <VideocallButton url={consulenza.appuntamento.videocall_url} />
              )}
              {editable && (
                <div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className={TESTUALE_DISTRUTTIVO}
                    onClick={() => apri(() => setCancelBookingOpen(true))}
                  >
                    Annulla l'appuntamento
                  </Button>
                </div>
              )}
            </>
          ) : (
            <>
              <p className="text-body text-ink-2">Nessun appuntamento prenotato.</p>
              {editable && (
                <div>
                  <Button type="button" onClick={() => apri(() => setBookingOpen(true))}>
                    <CalendarClock className="size-4" aria-hidden />
                    Prenota un appuntamento
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </Panel>
    ) : undefined;

  return (
    <Page
      variante="dettaglio"
      intestazione={
        <PageHeader
          area="consulenze"
          indietro={INDIETRO}
          sopra={
            <>
              <ConsulenzaStatoBadge stato={consulenza.stato} />
              {linkCall && <ConsultoCallBadge />}
            </>
          }
          titolo={consulenza.bando_titolo}
          descrizione={`Richiesta di consulenza del ${formatDate(consulenza.created_at)}`}
          azioni={
            <>
              <LinkButton
                to={`/app/bandi/${consulenza.bando_slug}`}
                variant="secondary"
                className={FOCUS_SU_FASCIA}
              >
                Vai al bando
              </LinkButton>
              {linkCall && (
                <LinkButton to={linkCall} variant="secondary" className={FOCUS_SU_FASCIA}>
                  {CONSULTO_CALL_COPY.vaiAllaCall}
                </LinkButton>
              )}
            </>
          }
        />
      }
      laterale={laterale}
    >
      <Section aria-label="Proposte ricevute">
        <SectionHeader titolo="Proposte" />
        {consulenza.proposte.length === 0 ? (
          <EmptyState
            icon={Inbox}
            area="consulenze"
            title="Ancora nessuna proposta"
            description={
              consulenza.stato === "nuova"
                ? "I progettisti hanno ricevuto la tua richiesta: appena qualcuno si propone lo trovi qui (e ti avvisiamo con una notifica)."
                : undefined
            }
          />
        ) : (
          <ul className="flex flex-col gap-3">
            {consulenza.proposte.map((proposta) => (
              <li key={proposta.id}>
                <Card className={`flex flex-col gap-3 ${BORDO_PROPOSTA[proposta.stato] ?? ""}`}>
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    {/* Il cliente vede il progettista per nome e cognome, mai il codice. */}
                    <div className="flex items-center gap-3">
                      {proposta.nome_progettista && <Avatar nome={proposta.nome_progettista} />}
                      <div className="flex flex-col">
                        <p className="font-semibold text-ink">
                          {proposta.nome_progettista ?? "Un progettista"}
                        </p>
                        <p className="text-small text-ink-3">
                          {formatDateTime(proposta.created_at)}
                        </p>
                      </div>
                    </div>
                    <PropostaStatoBadge stato={proposta.stato} />
                  </div>
                  <p className="max-w-lettura whitespace-pre-line text-body text-ink">
                    {proposta.messaggio}
                  </p>
                  {/* Più proposte possono essere accettabili insieme: «Accetta» è
                      secondario, il primario della pagina resta uno. */}
                  {editable && consulenza.stato === "nuova" && proposta.stato === "inviata" && (
                    <div className="flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => apri(() => setAccepting(proposta))}
                      >
                        Accetta la proposta
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => apri(() => setRejecting(proposta))}
                      >
                        Rifiuta
                      </Button>
                    </div>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Annullo della richiesta (solo finché è aperta) */}
      {editable && consulenza.stato === "nuova" && (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className={TESTUALE_DISTRUTTIVO}
            onClick={() => apri(() => setCancelOpen(true))}
          >
            Annulla la richiesta di consulenza
          </Button>
        </div>
      )}

      {/* Accettazione: scelta slot opzionale */}
      {accepting && (
        <SlotPicker
          open={!!accepting}
          onClose={() => setAccepting(null)}
          requestId={consulenza.id}
          propostaId={accepting.id}
          title={`Accetta la proposta di ${accepting.nome_progettista ?? "questo progettista"}`}
          confirmLabel="Accetta"
          allowSkip
          busy={accetta.isPending}
          error={actionError}
          onConfirm={handleAccept}
        />
      )}

      {/* Prenotazione post-assegnazione */}
      <SlotPicker
        open={bookingOpen}
        onClose={() => setBookingOpen(false)}
        requestId={consulenza.id}
        propostaId={null}
        title="Prenota un appuntamento"
        confirmLabel="Prenota"
        allowSkip={false}
        busy={prenota.isPending}
        error={actionError}
        onConfirm={handleBook}
      />

      <ConfirmDialog
        open={!!rejecting}
        titolo="Rifiutare la proposta?"
        conferma="Rifiuta"
        distruttiva
        inCorso={rifiuta.isPending}
        onConferma={handleReject}
        onAnnulla={() => setRejecting(null)}
      >
        <p>
          Non potrai più accettare la proposta di{" "}
          {rejecting?.nome_progettista ?? "questo progettista"}. Le altre proposte restano
          valide.
        </p>
        {errore}
      </ConfirmDialog>

      <ConfirmDialog
        open={cancelBookingOpen}
        titolo="Annullare l'appuntamento?"
        conferma="Annulla l'appuntamento"
        annulla="Tieni l'appuntamento"
        distruttiva
        inCorso={annullaPrenotazione.isPending}
        onConferma={handleCancelBooking}
        onAnnulla={() => setCancelBookingOpen(false)}
      >
        <p>
          Il progettista riceverà una notifica e l'orario tornerà libero. Potrai prenotare un
          altro appuntamento da questa pagina.
        </p>
        {errore}
      </ConfirmDialog>

      <ConfirmDialog
        open={cancelOpen}
        titolo="Annullare la richiesta?"
        conferma="Annulla la richiesta"
        annulla="Torna alla consulenza"
        distruttiva
        inCorso={annulla.isPending}
        onConferma={handleCancelRequest}
        onAnnulla={() => setCancelOpen(false)}
      >
        <p>
          La richiesta uscirà dall'elenco dei progettisti e le proposte ricevute non saranno
          più accettabili. Potrai richiedere una nuova consulenza su questo bando in qualsiasi
          momento.
        </p>
        {errore}
      </ConfirmDialog>
    </Page>
  );
}
