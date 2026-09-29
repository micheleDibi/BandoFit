import { useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Clock, Download, ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useIdentitaAzienda, useRichiediIdentita } from "../../hooks/useIdentitaAzienda";
import { PARTNER_PROFILE_ROOT } from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { IDENTITA_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { MotivoIdentitaPartner, StatoIdentitaAzienda } from "../../types";
import { Badge, type BadgeProps } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";
import { TestoLungo } from "./CampiCall";

/** Ancora del riquadro (link di «Mostra il nome», del wizard e della chat). */
export const ANCORA_IDENTITA = "identita";

const STATO_UI: Record<
  StatoIdentitaAzienda,
  { tono: BadgeProps["tone"]; icona: typeof ShieldCheck }
> = {
  non_richiesta: { tono: "slate", icona: ShieldQuestion },
  richiesta: { tono: "amber", icona: Clock },
  verificata: { tono: "emerald", icona: BadgeCheck },
  rifiutata: { tono: "red", icona: ShieldAlert },
};

/** Badge dello stato della verifica: icona E testo, mai il solo colore. */
export function StatoIdentitaBadge({ stato }: { stato: StatoIdentitaAzienda }) {
  const { tono, icona: Icona } = STATO_UI[stato] ?? STATO_UI.non_richiesta;
  return (
    <Badge tone={tono}>
      <Icona className="size-3.5" aria-hidden />
      {IDENTITA_COPY.stati[stato] ?? stato}
    </Badge>
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
      <div className="mt-3 space-y-2" aria-hidden>
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-9 w-40" />
      </div>
    );
  } else if (isError || !data) {
    corpo = (
      <div className="mt-3">
        <p className="text-sm text-red-700" role="alert">
          {apiErrorMessage(error, "Impossibile caricare lo stato della verifica.")}
        </p>
        <Button variant="secondary" size="sm" className="mt-2" onClick={() => void refetch()}>
          Riprova
        </Button>
      </div>
    );
  } else {
    const motivo = data.motivo_non_richiedibile;
    // Verificata dall'admin ma i dati del registro non sono più coerenti:
    // oggi il nome non si sblocca.
    const sospesa = data.stato === "verificata" && !data.verificata;
    corpo = (
      <>
        <p className="mt-2 text-sm text-slate-700">
          {IDENTITA_COPY.descrizioneStato[data.stato]}
          {data.stato === "verificata" && data.verificata_at
            ? ` Verificata il ${formatDate(data.verificata_at)}.`
            : ""}
          {data.stato === "richiesta" && data.richiesta_at
            ? ` Richiesta del ${formatDate(data.richiesta_at)}.`
            : ""}
        </p>
        {data.stato !== "verificata" && (
          <div className="mt-3">
            <p className="text-sm font-medium text-slate-700">{IDENTITA_COPY.sbloccaTitolo}</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-slate-600">
              {IDENTITA_COPY.sblocca.map((voce) => (
                <li key={voce}>{voce}</li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">{IDENTITA_COPY.spiegazione}</p>
          </div>
        )}
        {sospesa && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900" role="note">
            {motivoRegistro
              ? PARTNER_COPY.motiviIdentita[motivoRegistro]
              : IDENTITA_COPY.registroNonCoerente}
          </p>
        )}
        {data.puo_richiedere ? (
          <Button
            className="mt-4"
            variant={data.stato === "rifiutata" ? "secondary" : "primary"}
            onClick={() => {
              setErrore(null);
              setAperto(true);
            }}
          >
            <ShieldCheck className="size-4" aria-hidden />
            {data.stato === "rifiutata" ? IDENTITA_COPY.chiediDiNuovo : IDENTITA_COPY.chiedi}
          </Button>
        ) : motivo === "dati_registro" ? (
          <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <p className="min-w-0 flex-1">
              {motivoRegistro
                ? PARTNER_COPY.motiviIdentita[motivoRegistro]
                : IDENTITA_COPY.servonoDati}
            </p>
            {onImporta && (
              <Button variant="secondary" size="sm" onClick={onImporta}>
                <Download className="size-4" aria-hidden />
                {PARTNER_COPY.importa}
              </Button>
            )}
          </div>
        ) : motivo === "solo_titolare" && data.stato !== "verificata" ? (
          <p className="mt-3 text-xs text-slate-500">{IDENTITA_COPY.soloTitolare}</p>
        ) : null}
        {errore && !aperto && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {errore}
          </p>
        )}
      </>
    );
  }

  return (
    <Card id={ANCORA_IDENTITA} className="scroll-mt-24 p-5">
      <section aria-labelledby={idTitolo}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3
            id={idTitolo}
            className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900"
          >
            <ShieldCheck className="size-4 text-brand-500" aria-hidden />
            {IDENTITA_COPY.titolo}
          </h3>
          {data && <StatoIdentitaBadge stato={data.stato} />}
        </div>
        {/* Sempre montata: l'esito della richiesta va annunciato anche se il
            bottone che l'ha causato sparisce. */}
        <div role="status" aria-live="polite">
          {annuncio && (
            <p className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              {annuncio}
            </p>
          )}
        </div>
        {corpo}
      </section>

      <Dialog
        open={aperto}
        onClose={() => setAperto(false)}
        dismissible={!richiedi.isPending}
        title={IDENTITA_COPY.richiestaTitolo}
        footer={
          <>
            <Button variant="ghost" onClick={() => setAperto(false)} disabled={richiedi.isPending}>
              {PARTNER_COPY.annulla}
            </Button>
            <Button loading={richiedi.isPending} onClick={() => void invia()}>
              Invia la richiesta
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p>{IDENTITA_COPY.spiegazione}</p>
          <TestoLungo
            etichetta={IDENTITA_COPY.notaEtichetta}
            aiuto={IDENTITA_COPY.notaAiuto}
            valore={nota}
            onChange={setNota}
            massimo={IDENTITA_COPY.notaMax}
            righe={3}
          />
          {errore && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {errore}
            </p>
          )}
        </div>
      </Dialog>
    </Card>
  );
}
