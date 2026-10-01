import { useEffect, useId, useState } from "react";
import { useInvita } from "../../hooks/useCandidature";
import { apiErrorMessage } from "../../lib/api";
import { CANDIDATURE_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { Candidatura, PartnerSuggerito, PosizioneCall } from "../../types";
import { Alert } from "../ui/Alert";
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
  const dove = [classe, profilo.regione_sede].filter(Boolean) as string[];
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
          <Button type="button" onClick={onClose}>
            Chiudi
          </Button>
        ) : (
          <>
            <Button type="button" variant="secondary" onClick={onClose} disabled={invita.isPending}>
              Annulla
            </Button>
            <Button type="button" onClick={conferma} loading={invita.isPending}>
              Invia l'invito
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-0.5">
          <p className="font-medium text-ink">{nome}</p>
          {dove.length > 0 && (
            <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
              {dove.map((d) => (
                <span key={d}>{d}</span>
              ))}
            </p>
          )}
          <p className="text-small text-ink-3">Riferimento per questa call: {pseudonimo}</p>
        </div>

        <div aria-live="polite">
          {inviato && (
            <Alert tono="ok">
              Invito inviato. L'azienda lo trova tra le sue candidature
              {inviato.scade_at ? ` e può rispondere fino al ${formatDate(inviato.scade_at)}` : ""}.
              {inviato.inviti
                ? ` Inviti in attesa di risposta su questa call: ${inviato.inviti.attivi} su ${inviato.inviti.massimo}.`
                : ""}
            </Alert>
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
            <div className="flex flex-col gap-1">
              <TextareaField
                label="Messaggio (facoltativo)"
                rows={4}
                maxLength={MESSAGGIO_INVITO_MAX}
                value={messaggio}
                onChange={(e) => setMessaggio(e.target.value)}
                aria-describedby={`${id}-aiuto ${id}-contatore`}
              />
              <p id={`${id}-aiuto`} className="text-small text-ink-3">
                {CANDIDATURE_COPY.notaContatti}
              </p>
              <p id={`${id}-contatore`} className="text-right text-small text-ink-3 tabular-nums">
                {messaggio.length.toLocaleString("it-IT")} caratteri su{" "}
                {MESSAGGIO_INVITO_MAX.toLocaleString("it-IT")}
              </p>
            </div>
            {invita.isError && <Alert tono="errore">{apiErrorMessage(invita.error)}</Alert>}
          </>
        )}
      </div>
    </Dialog>
  );
}
