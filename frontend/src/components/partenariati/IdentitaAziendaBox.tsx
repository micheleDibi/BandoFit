import { useQueryClient } from "@tanstack/react-query";
import { Download, ShieldCheck } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useIdentitaAzienda, useRichiediIdentita } from "../../hooks/useIdentitaAzienda";
import { PARTNER_PROFILE_ROOT } from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { IDENTITA_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { MotivoIdentitaPartner, StatoIdentitaAzienda } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Panel } from "../ui/Panel";
import { Skeleton } from "../ui/states";
import { Status, type TonoStatus } from "../ui/Status";
import { TestoLungo } from "./CampiCall";

/** Ancora del riquadro (link di «Mostra il nome», del wizard e della chat). */
export const ANCORA_IDENTITA = "identita";

const TONO_STATO: Record<StatoIdentitaAzienda, TonoStatus> = {
  non_richiesta: "neutro",
  richiesta: "in-apertura",
  verificata: "aperto",
  rifiutata: "attenzione",
};

/** Stato della verifica in parole (`Status`): punto e testo, mai il solo
 *  colore. Il nome resta per chi lo importa (tabella dell'admin). */
export function StatoIdentitaBadge({ stato }: { stato: StatoIdentitaAzienda }) {
  return (
    <Status tono={TONO_STATO[stato] ?? "neutro"}>{IDENTITA_COPY.stati[stato] ?? stato}</Status>
  );
}

/** Riquadro «Verifica dell'identità» nella sezione partner dell'Azienda (WP9,
 *  decisione di Michele): stato, cosa sblocca e, per il titolare, «Chiedi la
 *  verifica» con una nota facoltativa su come essere contattato. Servono i
 *  dati ufficiali del Registro Imprese (T5: `motivoRegistro` dal profilo
 *  partner dice cosa manca). Quando lo stato cambia (decisione dell'admin,
 *  revoca) si rileggono i profili partner, perché «Mostra il nome» dipende
 *  dalla verifica. */
export function IdentitaAziendaBox({
  onImporta,
  motivoRegistro,
}: {
  onImporta?: () => void;
  motivoRegistro?: MotivoIdentitaPartner | null;
}) {
  const { data, isPending, isError, error, refetch } = useIdentitaAzienda();
  const richiedi = useRichiediIdentita();
  const queryClient = useQueryClient();
  const idTitolo = useId();
  const [aperto, setAperto] = useState(false);
  const [nota, setNota] = useState("");
  const [errore, setErrore] = useState<string | null>(null);
  const [annuncio, setAnnuncio] = useState<string | null>(null);

  // Stato cambiato rispetto all'ultima lettura: il nome si può (o non si può
  // più) mostrare, quindi il profilo partner va riletto.
  const statoVisto = useRef<StatoIdentitaAzienda | null>(null);
  const stato = data?.stato ?? null;
  useEffect(() => {
    if (stato === null) return;
    if (statoVisto.current !== null && statoVisto.current !== stato) {
      void queryClient.invalidateQueries({ queryKey: PARTNER_PROFILE_ROOT });
    }
    statoVisto.current = stato;
  }, [stato, queryClient]);

  // Il modulo spento (o nessuna azienda) risponde 404: il riquadro sparisce.
  if (isError && apiErrorCode(error) === "not_found") return null;

  const invia = async () => {
    setErrore(null);
    try {
      await richiedi.mutateAsync({ nota: nota.trim() || null });
      setAperto(false);
      setNota("");
      setAnnuncio(IDENTITA_COPY.inviata);
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  let corpo;
  if (isPending) {
    corpo = (
      <div className="flex flex-col gap-2" aria-hidden>
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-9 w-40" />
      </div>
    );
  } else if (isError || !data) {
    corpo = (
      <Alert
        tono="errore"
        azione={
          <Button variant="secondary" size="sm" onClick={() => void refetch()}>
            Riprova
          </Button>
        }
      >
        {apiErrorMessage(error, "Impossibile caricare lo stato della verifica.")}
      </Alert>
    );
  } else {
    const motivo = data.motivo_non_richiedibile;
    // Verificata dall'admin ma i dati del registro non sono più coerenti:
    // oggi il nome non si sblocca.
    const sospesa = data.stato === "verificata" && !data.verificata;
    corpo = (
      <>
        <p className="text-body text-ink-2">
          {IDENTITA_COPY.descrizioneStato[data.stato]}
          {data.stato === "verificata" && data.verificata_at
            ? ` Verificata il ${formatDate(data.verificata_at)}.`
            : ""}
          {data.stato === "richiesta" && data.richiesta_at
            ? ` Richiesta del ${formatDate(data.richiesta_at)}.`
            : ""}
        </p>
        {data.stato !== "verificata" && (
          <div className="flex flex-col gap-1">
            <p className="text-title-group text-ink">{IDENTITA_COPY.sbloccaTitolo}</p>
            <ul className="flex list-disc flex-col gap-0.5 pl-5 text-body text-ink-2">
              {IDENTITA_COPY.sblocca.map((voce) => (
                <li key={voce}>{voce}</li>
              ))}
            </ul>
            <p className="mt-1 text-small text-ink-3">{IDENTITA_COPY.spiegazione}</p>
          </div>
        )}
        {sospesa && (
          <Alert tono="attenzione">
            {motivoRegistro
              ? PARTNER_COPY.motiviIdentita[motivoRegistro]
              : IDENTITA_COPY.registroNonCoerente}
          </Alert>
        )}
        {data.puo_richiedere ? (
          // Secondario: nella scheda il pulsante pieno è «Attiva la visibilità».
          <Button
            className="self-start"
            variant="secondary"
            onClick={() => {
              setErrore(null);
              setAperto(true);
            }}
          >
            <ShieldCheck className="size-4" aria-hidden />
            {data.stato === "rifiutata" ? IDENTITA_COPY.chiediDiNuovo : IDENTITA_COPY.chiedi}
          </Button>
        ) : motivo === "dati_registro" ? (
          <Alert
            tono="attenzione"
            azione={
              onImporta ? (
                <Button variant="secondary" size="sm" onClick={onImporta}>
                  <Download className="size-4" aria-hidden />
                  {PARTNER_COPY.importa}
                </Button>
              ) : undefined
            }
          >
            {motivoRegistro
              ? PARTNER_COPY.motiviIdentita[motivoRegistro]
              : IDENTITA_COPY.servonoDati}
          </Alert>
        ) : motivo === "solo_titolare" && data.stato !== "verificata" ? (
          <p className="text-small text-ink-3">{IDENTITA_COPY.soloTitolare}</p>
        ) : null}
        {errore && !aperto && <Alert tono="errore">{errore}</Alert>}
      </>
    );
  }

  return (
    <Panel
      id={ANCORA_IDENTITA}
      aria-labelledby={idTitolo}
      className="scroll-mt-16"
      titolo={<span id={idTitolo}>{IDENTITA_COPY.titolo}</span>}
      azione={data ? <StatoIdentitaBadge stato={data.stato} /> : undefined}
    >
      {/* Sempre montata: l'esito della richiesta va annunciato anche se il
          bottone che l'ha causato sparisce. */}
      <div role="status" aria-live="polite">
        {annuncio && <Alert tono="ok">{annuncio}</Alert>}
      </div>
      {corpo}

      <Dialog
        open={aperto}
        onClose={() => setAperto(false)}
        dismissible={!richiedi.isPending}
        title={IDENTITA_COPY.richiestaTitolo}
        footer={
          <>
            <Button variant="secondary" onClick={() => setAperto(false)} disabled={richiedi.isPending}>
              {PARTNER_COPY.annulla}
            </Button>
            <Button loading={richiedi.isPending} onClick={() => void invia()}>
              Invia la richiesta
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <p>{IDENTITA_COPY.spiegazione}</p>
          <TestoLungo
            etichetta={IDENTITA_COPY.notaEtichetta}
            aiuto={IDENTITA_COPY.notaAiuto}
            valore={nota}
            onChange={setNota}
            massimo={IDENTITA_COPY.notaMax}
            righe={3}
          />
          {errore && <Alert tono="errore">{errore}</Alert>}
        </div>
      </Dialog>
    </Panel>
  );
}
