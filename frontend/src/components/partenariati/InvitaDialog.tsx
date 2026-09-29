import { EyeOff } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useInvita } from "../../hooks/useCandidature";
import { apiErrorMessage } from "../../lib/api";
import { CANDIDATURE_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { Candidatura, PartnerSuggerito, PosizioneCall } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextareaField } from "../ui/Field";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";

/** Messaggio facoltativo dell'invito (come il server). */
export const MESSAGGIO_INVITO_MAX = 1000;

/** Invito a un'azienda suggerita (solo il titolare dell'azienda che ha
 *  creato la call). L'azienda si indica con lo pseudonimo della call, mai
 *  con il suo id: il server lo risolve e ricontrolla che sia ancora tra le
 *  suggerite (altrimenti un rifiuto neutro, «non disponibile»). Posizione e
 *  messaggio facoltativi; nel messaggio niente contatti. Dopo l'invio la
 *  conferma resta nel dialog, con la scadenza. */
export function InvitaDialog({
  open,
  onClose,
  callId,
  suggerito,
  posizioni,
  onInvitata,
}: {
  open: boolean;
  onClose: () => void;
  callId: string;
  suggerito: PartnerSuggerito | null;
  posizioni: PosizioneCall[];
  onInvitata?: (pseudonimo: string) => void;
}) {
  const id = useId();
  const invita = useInvita(callId);
  const [posizione, setPosizione] = useState("");
  const [messaggio, setMessaggio] = useState("");
  const [inviato, setInviato] = useState<Candidatura | null>(null);

  // A ogni apertura si riparte da zero, con la posizione più adatta.
  useEffect(() => {
    if (!open) return;
    setPosizione(suggerito?.match.posizioni_compatibili[0]?.id ?? "");
    setMessaggio("");
    setInviato(null);
    invita.reset();
    // Solo all'apertura (`invita` cambia a ogni render).
  }, [open]);

  if (!suggerito) return null;
  const { profilo, pseudonimo } = suggerito;
  const classe = profilo.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[profilo.classe_dimensionale] ?? profilo.classe_dimensionale)
    : null;
  const dove = [classe, profilo.regione_sede].filter(Boolean).join(" · ");
  const nome = profilo.denominazione ?? PARTNER_COPY.aziendaAnonima;

  const conferma = () => {
    invita.mutate(
      {
        pseudonimo,
        posizione_id: posizione || null,
        messaggio: messaggio.trim() || null,
      },
      {
        onSuccess: (candidatura) => {
          setInviato(candidatura);
          onInvitata?.(pseudonimo);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      dismissible={!invita.isPending}
      title="Invita questa azienda"
      footer={
        inviato ? (
          <Button onClick={onClose}>Chiudi</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={invita.isPending}>
              Annulla
            </Button>
            <Button onClick={conferma} loading={invita.isPending}>
              Invia l'invito
            </Button>
          </>
        )
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2.5">
          <EyeOff className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
          <div className="min-w-0 text-sm">
            <p className="font-medium text-slate-900">{nome}</p>
            {dove && <p className="text-xs text-slate-500">{dove}</p>}
            <p className="text-xs text-slate-400">
              Riferimento per questa call: <span className="font-mono tracking-wide">{pseudonimo}</span>
            </p>
          </div>
        </div>

        <div role="status" aria-live="polite">
          {inviato && (
            <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
              Invito inviato. L'azienda lo trova tra le sue candidature
              {inviato.scade_at ? ` e può rispondere fino al ${formatDate(inviato.scade_at)}` : ""}.
              {inviato.inviti
                ? ` Inviti in attesa di risposta su questa call: ${inviato.inviti.attivi} su ${inviato.inviti.massimo}.`
                : ""}
            </p>
          )}
        </div>

        {!inviato && (
          <>
            <p>
              L'azienda vede la call in forma anonima, come le altre: non sa chi sei finché non
              accetta. Se accetta, si apre una conversazione; se non risponde, l'invito scade da
              solo.
            </p>
            {posizioni.length > 0 && (
              <SelectField
                label="Per quale posizione (facoltativo)"
                value={posizione}
                onChange={(e) => setPosizione(e.target.value)}
              >
                <option value="">Nessuna in particolare</option>
                {posizioni.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.titolo}
                  </option>
                ))}
              </SelectField>
            )}
            <div>
              <TextareaField
                label="Messaggio (facoltativo)"
                rows={4}
                maxLength={MESSAGGIO_INVITO_MAX}
                value={messaggio}
                onChange={(e) => setMessaggio(e.target.value)}
                aria-describedby={`${id}-aiuto ${id}-contatore`}
              />
              <p id={`${id}-aiuto`} className="mt-1 text-xs text-slate-500">
                {CANDIDATURE_COPY.notaContatti}
              </p>
              <p id={`${id}-contatore`} className="mt-1 text-right text-xs text-slate-400 tabular">
                {messaggio.length.toLocaleString("it-IT")} caratteri su{" "}
                {MESSAGGIO_INVITO_MAX.toLocaleString("it-IT")}
              </p>
            </div>
            {invita.isError && (
              <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                {apiErrorMessage(invita.error)}
              </p>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}
