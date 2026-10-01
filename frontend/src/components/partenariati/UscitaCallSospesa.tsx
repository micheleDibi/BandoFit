import { useState } from "react";
import { useConsorzio, useEsciMembro } from "../../hooks/useConsorzio";
import { apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import { Alert } from "../ui/Alert";
import { Button, LinkButton } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ErrorState, Skeleton } from "../ui/states";

/** Una call che l'azienda attiva non può leggere (404). Se è sospesa per
 *  moderazione e l'azienda fa ancora parte del suo consorzio, il server
 *  restituisce del consorzio SOLO la sua riga (WP9, WP8 P14): qui si può
 *  uscire, senza vedere nient'altro della call. In ogni altro caso «Call non
 *  trovata», senza «Riprova». */
export function CallNonTrovata({ callId }: { callId: string }) {
  const consorzio = useConsorzio(callId);
  const esci = useEsciMembro(callId);
  const [conferma, setConferma] = useState(false);
  const propria = consorzio.data?.membri.find((m) => m.sei_tu) ?? null;

  // Regione live sempre montata: l'esito dell'uscita arriva dopo.
  const annuncio = (
    <div aria-live="polite">
      {propria?.stato === "uscito" && <Alert tono="ok">{CONSORZIO_COPY.uscitaFatta}</Alert>}
    </div>
  );

  if (consorzio.isLoading) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (!propria || propria.stato === "uscito") {
    return (
      <div className="flex flex-col gap-4">
        {annuncio}
        <ErrorState
          title="Call non trovata"
          message="Non esiste, non è più aperta oppure non è visibile alla tua azienda."
        />
        <div>
          <LinkButton to="/app/partenariati?tab=tutte" variant="secondary">
            Tutte le call
          </LinkButton>
        </div>
      </div>
    );
  }

  const esegui = () =>
    esci.mutate({ membroId: propria.id }, { onSuccess: () => setConferma(false) });

  return (
    <div className="flex max-w-[680px] flex-col gap-4">
      {annuncio}
      <h1 className="text-title-page text-ink">{CONSORZIO_COPY.nonConsultabileTitolo}</h1>
      <p className="text-body text-ink-2">{CONSORZIO_COPY.nonConsultabileTesto}</p>
      {propria.puo_uscire ? (
        <div>
          <Button
            variant="secondary"
            onClick={() => {
              esci.reset();
              setConferma(true);
            }}
          >
            Esci dal consorzio
          </Button>
        </div>
      ) : (
        <p className="text-small text-ink-3">{CONSORZIO_COPY.nonConsultabileSoloTitolare}</p>
      )}
      <ConfirmDialog
        open={conferma}
        titolo={CONSORZIO_COPY.esciDialogTitolo}
        conferma="Esci dal consorzio"
        annulla="Non ora"
        distruttiva
        inCorso={esci.isPending}
        onConferma={esegui}
        onAnnulla={() => setConferma(false)}
      >
        <div className="flex flex-col gap-3">
          <p>{CONSORZIO_COPY.esciDialogTesto}</p>
          {esci.isError && <Alert tono="errore">{apiErrorMessage(esci.error)}</Alert>}
        </div>
      </ConfirmDialog>
    </div>
  );
}
