import { useEffect } from "react";
import { useAggiornaCall, useAnteprimaCall } from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { ErrorState, Skeleton } from "../ui/states";
import { CallPubblicaCard } from "./CallPubblicaCard";
import { BarraPasso } from "./CallStepper";
import { NotaAnonima } from "./PassoBando";
import { avanzamento, vuoto, type PassoProps } from "./passoComune";
import { RilieviCall } from "./RilieviCall";

/** Passo 6: «come ti vedono» — la proiezione pubblica dei dati salvati, con
 *  i rilievi sui testi. */
export function PassoAnteprima({ call, onAvanti, onIndietro, onDirty }: PassoProps) {
  const anteprima = useAnteprimaCall(call.id);
  const aggiorna = useAggiornaCall(call.id);
  useEffect(() => onDirty(false), [onDirty]);

  const continua = async () => {
    const passo = avanzamento(call, 7);
    try {
      if (!vuoto(passo)) await aggiorna.mutateAsync(passo);
      onAvanti();
    } catch {
      // mostrato nella barra
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <p className="text-body text-ink-2">
        Così vedranno la call le altre aziende: niente nome, niente budget esatto, niente dettagli
        riservati e niente di quello che sai sulla tua copertura dei requisiti.
      </p>
      <NotaAnonima anonima={call.anonima} />
      {anteprima.isPending ? (
        <Skeleton className="h-96 w-full" />
      ) : anteprima.isError || !anteprima.data ? (
        <ErrorState
          message={apiErrorMessage(anteprima.error, "Impossibile caricare l'anteprima.")}
          onRetry={() => void anteprima.refetch()}
        />
      ) : (
        <>
          <RilieviCall rilievi={anteprima.data.rilievi} />
          <CallPubblicaCard call={anteprima.data.call} />
        </>
      )}
      <BarraPasso
        onIndietro={onIndietro}
        onAvanti={() => void continua()}
        etichettaAvanti="Continua"
        inCorso={aggiorna.isPending}
        errore={aggiorna.isError ? apiErrorMessage(aggiorna.error) : null}
      />
    </div>
  );
}
