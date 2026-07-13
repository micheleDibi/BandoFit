import { ArrowLeft, CalendarClock } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConsulenzaStatoBadge } from "./Consulenze";
import { SlotPicker } from "../components/consulenze/SlotPicker";
import { VideocallButton } from "../components/consulenze/VideocallButton";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import {
  useAccettaProposta,
  useAnnullaConsulenza,
  useAnnullaPrenotazione,
  useConsulenza,
  usePrenotaSlot,
  useRifiutaProposta,
} from "../hooks/useConsulenze";
import { apiErrorMessage } from "../lib/api";
import { PROPOSTA_STATO_LABELS } from "../lib/copy";
import { formatDateTime, formatSlotGiorno, formatSlotOra } from "../lib/format";
import type { Proposta, PropostaStato } from "../types";

function PropostaStatoBadge({ stato }: { stato: PropostaStato }) {
  const tone =
    stato === "accettata"
      ? "emerald"
      : stato === "inviata"
        ? "brand"
        : stato === "rifiutata"
          ? "red"
          : "slate";
  return <Badge tone={tone}>{PROPOSTA_STATO_LABELS[stato]}</Badge>;
}

export default function ConsulenzaDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: consulenza, isPending, isError, error, refetch } = useConsulenza(id);

  const accetta = useAccettaProposta(id ?? "");
  const rifiuta = useRifiutaProposta(id ?? "");
  const annulla = useAnnullaConsulenza(id ?? "");
  const prenota = usePrenotaSlot(id ?? "");
  const annullaPrenotazione = useAnnullaPrenotazione(id ?? "");

  // Dialog di accettazione (con scelta slot) e di prenotazione post-assegnazione.
  const [accepting, setAccepting] = useState<Proposta | null>(null);
  const [bookingOpen, setBookingOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (isPending) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError || !consulenza) {
    return (
      <div className="mx-auto max-w-4xl">
        <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
      </div>
    );
  }

  const { editable } = consulenza;

  const handleAccept = async (slotId: string | null) => {
    if (!accepting || accetta.isPending) return;
    setActionError(null);
    try {
      await accetta.mutateAsync({ propostaId: accepting.id, slotId });
      setAccepting(null);
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
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <Link
        to="/app/consulenze"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Tutte le consulenze
      </Link>

      <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            {consulenza.bando_titolo}
          </h1>
          <Link
            to={`/app/bandi/${consulenza.bando_slug}`}
            className="mt-1 inline-block text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            Vai al bando →
          </Link>
        </div>
        <ConsulenzaStatoBadge stato={consulenza.stato} />
      </div>

      {/* Progettista assegnato + appuntamento */}
      {consulenza.stato === "assegnata" && (
        <Card className="mt-5 p-5">
          <h2 className="font-display text-sm font-semibold text-slate-900">
            Consulenza assegnata
          </h2>
          <p className="mt-1.5 text-sm text-slate-600">
            Progettista{" "}
            <span className="font-medium text-slate-900">
              {consulenza.progettista?.nome ?? "—"}
            </span>
          </p>
          {consulenza.appuntamento ? (
            <div className="mt-3 rounded-xl bg-slate-50 px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="inline-flex items-center gap-2 text-sm font-medium text-slate-800">
                  <CalendarClock className="size-4 shrink-0 text-brand-500" aria-hidden />
                  <span>
                    <span className="capitalize">
                      {formatSlotGiorno(consulenza.appuntamento.inizio)}
                    </span>
                    , {formatSlotOra(consulenza.appuntamento.inizio)} –{" "}
                    {formatSlotOra(consulenza.appuntamento.fine)}
                  </span>
                </p>
                {editable && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-red-600 hover:bg-red-50 hover:text-red-700"
                    loading={annullaPrenotazione.isPending}
                    onClick={async () => {
                      setActionError(null);
                      try {
                        await annullaPrenotazione.mutateAsync();
                      } catch (err) {
                        setActionError(apiErrorMessage(err));
                      }
                    }}
                  >
                    Annulla appuntamento
                  </Button>
                )}
              </div>
              {/* Non gated su editable: aprire/copiare il link non è una
                  mutazione, gli account collegati partecipano alla call. */}
              {consulenza.appuntamento.videocall_url && (
                <div className="mt-3">
                  <VideocallButton url={consulenza.appuntamento.videocall_url} />
                </div>
              )}
            </div>
          ) : (
            editable && (
              <Button
                variant="secondary"
                className="mt-3"
                onClick={() => {
                  setActionError(null);
                  setBookingOpen(true);
                }}
              >
                <CalendarClock className="size-4" aria-hidden />
                Prenota un appuntamento
              </Button>
            )
          )}
        </Card>
      )}

      {/* Proposte */}
      <section className="mt-6" aria-label="Proposte ricevute">
        <h2 className="font-display text-lg font-bold tracking-tight text-slate-900">
          Proposte
        </h2>
        {consulenza.proposte.length === 0 ? (
          <div className="mt-3">
            <EmptyState
              title="Ancora nessuna proposta"
              description="I progettisti hanno ricevuto la tua richiesta: appena qualcuno si propone lo trovi qui (e ti avvisiamo con una notifica)."
            />
          </div>
        ) : (
          <div className="mt-3 space-y-3">
            {consulenza.proposte.map((proposta) => (
              <Card key={proposta.id} className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">
                      {proposta.nome_progettista ?? "Un progettista"}
                    </p>
                    <p className="mt-0.5 text-xs text-slate-400">
                      {formatDateTime(proposta.created_at)}
                    </p>
                  </div>
                  <PropostaStatoBadge stato={proposta.stato} />
                </div>
                <p className="mt-3 whitespace-pre-line text-sm text-slate-700">
                  {proposta.messaggio}
                </p>
                {editable && consulenza.stato === "nuova" && proposta.stato === "inviata" && (
                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      onClick={() => {
                        setActionError(null);
                        setAccepting(proposta);
                      }}
                    >
                      Accetta la proposta
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      loading={rifiuta.isPending}
                      onClick={async () => {
                        setActionError(null);
                        try {
                          await rifiuta.mutateAsync(proposta.id);
                        } catch (err) {
                          setActionError(apiErrorMessage(err));
                        }
                      }}
                    >
                      Rifiuta
                    </Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      </section>

      {actionError && (
        <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {actionError}
        </p>
      )}

      {/* Annullo della richiesta (solo finché è aperta) */}
      {editable && consulenza.stato === "nuova" && (
        <div className="mt-8 border-t border-slate-200 pt-4">
          <Button
            variant="ghost"
            size="sm"
            className="text-red-600 hover:bg-red-50 hover:text-red-700"
            onClick={() => setCancelOpen(true)}
          >
            Annulla la richiesta di consulto
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

      {/* Conferma annullo richiesta */}
      <Dialog
        open={cancelOpen}
        onClose={() => setCancelOpen(false)}
        title="Annullare la richiesta?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCancelOpen(false)}>
              Torna alla consulenza
            </Button>
            <Button variant="danger" loading={annulla.isPending} onClick={handleCancelRequest}>
              Annulla la richiesta
            </Button>
          </>
        }
      >
        <p>
          La richiesta uscirà dall'elenco dei progettisti e le proposte ricevute non
          saranno più accettabili. Potrai richiedere un nuovo consulto su questo bando in
          qualsiasi momento.
        </p>
      </Dialog>
    </div>
  );
}
