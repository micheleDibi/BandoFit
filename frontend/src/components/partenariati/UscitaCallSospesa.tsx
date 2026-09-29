import { LogOut } from "lucide-react";
import { useState } from "react";
import { useConsorzio, useEsciMembro } from "../../hooks/useConsorzio";
import { apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { EmptyState, Skeleton } from "../ui/states";

/** Una call che l'azienda attiva non può leggere (404). Se è sospesa per
 *  moderazione e l'azienda fa ancora parte del suo consorzio, il server
 *  restituisce del consorzio SOLO la sua riga (WP9, WP8 P14): qui si può
 *  uscire, senza vedere nient'altro della call. In ogni altro caso lo stato
 *  vuoto «Call non trovata». */
export function CallNonTrovata({ callId }: { callId: string }) {
  const consorzio = useConsorzio(callId);
  const esci = useEsciMembro(callId);
  const [conferma, setConferma] = useState(false);
  const propria = consorzio.data?.membri.find((m) => m.sei_tu) ?? null;

  const annuncio = (
    <div role="status" aria-live="polite">
      {propria?.stato === "uscito" && (
        <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {CONSORZIO_COPY.uscitaFatta}
        </p>
      )}
    </div>
  );

  if (consorzio.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (!propria || propria.stato === "uscito") {
    return (
      <div className="space-y-4">
        {annuncio}
        <EmptyState
          title="Call non trovata"
          description="Non esiste, non è più aperta oppure non è visibile alla tua azienda."
          action={<LinkButton to="/app/partenariati?vista=tutte">Tutte le call</LinkButton>}
        />
      </div>
    );
  }

  const esegui = () =>
    esci.mutate({ membroId: propria.id }, { onSuccess: () => setConferma(false) });

  return (
    <div className="space-y-4">
      {annuncio}
      <Card className="p-5">
        <h1 className="font-display text-xl font-bold tracking-tight text-slate-900">
          {CONSORZIO_COPY.nonConsultabileTitolo}
        </h1>
        <p className="mt-2 text-sm text-slate-700">{CONSORZIO_COPY.nonConsultabileTesto}</p>
        {propria.puo_uscire ? (
          <Button
            variant="secondary"
            size="sm"
            className="mt-4"
            onClick={() => {
              esci.reset();
              setConferma(true);
            }}
          >
            <LogOut className="size-4" aria-hidden />
            Esci dal consorzio
          </Button>
        ) : (
          <p className="mt-3 text-sm text-slate-500">{CONSORZIO_COPY.nonConsultabileSoloTitolare}</p>
        )}
      </Card>
      <Dialog
        open={conferma}
        onClose={() => setConferma(false)}
        dismissible={!esci.isPending}
        title={CONSORZIO_COPY.esciDialogTitolo}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConferma(false)} disabled={esci.isPending}>
              Non ora
            </Button>
            <Button variant="danger" loading={esci.isPending} onClick={esegui}>
              Esci dal consorzio
            </Button>
          </>
        }
      >
        <p>{CONSORZIO_COPY.esciDialogTesto}</p>
        {esci.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(esci.error)}
          </p>
        )}
      </Dialog>
    </div>
  );
}
