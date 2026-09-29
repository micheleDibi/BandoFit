import { useEffect, useId, useState } from "react";
import { useSegnala } from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY } from "../../lib/copy";
import type { MotivoSegnalazione, OggettoSegnalazione } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { SelectField } from "../ui/Field";
import { TestoLungo } from "./CampiCall";
import { LIMITI_CALL } from "./callDati";

const MOTIVI = Object.keys(CALL_COPY.segnalaMotivi) as MotivoSegnalazione[];

/** Segnalazione di una call o di un profilo (DSA art. 16): motivo,
 *  descrizione e dichiarazione di buona fede (non preselezionata); la
 *  conferma di ricezione compare in `role="status"`. */
export function SegnalaDialog({
  open,
  onClose,
  oggettoTipo,
  oggettoId,
}: {
  open: boolean;
  onClose: () => void;
  oggettoTipo: OggettoSegnalazione;
  oggettoId: string;
}) {
  const id = useId();
  const segnala = useSegnala();
  const [motivo, setMotivo] = useState<MotivoSegnalazione | "">("");
  const [descrizione, setDescrizione] = useState("");
  const [buonaFede, setBuonaFede] = useState(false);
  const [ricevuta, setRicevuta] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);

  // A ogni apertura si riparte da zero.
  useEffect(() => {
    if (!open) return;
    setMotivo("");
    setDescrizione("");
    setBuonaFede(false);
    setRicevuta(false);
    setErrore(null);
    segnala.reset();
    // Solo all'apertura (`segnala` cambia a ogni render).
  }, [open]);

  const lunghezza = descrizione.trim().length;
  const valida =
    !!motivo &&
    lunghezza >= LIMITI_CALL.descrizioneSegnalazioneMin &&
    lunghezza <= LIMITI_CALL.descrizioneSegnalazioneMax &&
    buonaFede;

  const invia = async () => {
    if (!motivo || !valida) return;
    setErrore(null);
    try {
      await segnala.mutateAsync({
        oggetto_tipo: oggettoTipo,
        oggetto_id: oggettoId,
        motivo,
        descrizione: descrizione.trim(),
        buona_fede: true,
      });
      setRicevuta(true);
    } catch (err) {
      // 409: segnalazione già aperta; 429: troppe segnalazioni oggi.
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={CALL_COPY.segnalaTitolo}
      dismissible={!segnala.isPending}
      footer={
        ricevuta ? (
          <Button onClick={onClose}>Chiudi</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={segnala.isPending}>
              Annulla
            </Button>
            <Button variant="danger" onClick={() => void invia()} loading={segnala.isPending} disabled={!valida}>
              Invia la segnalazione
            </Button>
          </>
        )
      }
    >
      <div role="status" aria-live="polite">
        {ricevuta && (
          <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            {CALL_COPY.segnalaRicevuta}
          </p>
        )}
      </div>
      {!ricevuta && (
        <div className="space-y-4">
          <SelectField
            label="Motivo"
            required
            value={motivo}
            onChange={(e) => setMotivo(e.target.value as MotivoSegnalazione | "")}
          >
            <option value="">Scegli il motivo…</option>
            {MOTIVI.map((m) => (
              <option key={m} value={m}>
                {CALL_COPY.segnalaMotivi[m]}
              </option>
            ))}
          </SelectField>
          <TestoLungo
            etichetta="Cosa non va"
            aiuto={`Spiega perché lo segnali (almeno ${LIMITI_CALL.descrizioneSegnalazioneMin} caratteri).`}
            valore={descrizione}
            onChange={setDescrizione}
            massimo={LIMITI_CALL.descrizioneSegnalazioneMax}
            righe={4}
            required
          />
          <label htmlFor={`${id}-bf`} className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input
              id={`${id}-bf`}
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={buonaFede}
              onChange={(e) => setBuonaFede(e.target.checked)}
            />
            <span>{CALL_COPY.segnalaBuonaFede}</span>
          </label>
          {errore && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {errore}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}
