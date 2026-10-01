import { useEffect, useState } from "react";
import { useSegnala } from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY } from "../../lib/copy";
import type { MotivoSegnalazione, OggettoSegnalazione } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Dialog } from "../ui/Dialog";
import { SelectField } from "../ui/Field";
import { TextLink } from "../ui/TextLink";
import { TestoLungo } from "./CampiCall";
import { LIMITI_CALL } from "./callDati";

const MOTIVI = Object.keys(CALL_COPY.segnalaMotivi) as MotivoSegnalazione[];

/** Segnalazione di una call o di un profilo (DSA art. 16): motivo,
 *  descrizione e dichiarazione di buona fede (non preselezionata); la
 *  conferma di ricezione compare nella regione live. */
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
  const segnala = useSegnala();
  const [motivo, setMotivo] = useState<MotivoSegnalazione | "">("");
  const [descrizione, setDescrizione] = useState("");
  const [buonaFede, setBuonaFede] = useState(false);
  // Id della segnalazione ricevuta (conferma con il link per seguirla).
  const [ricevuta, setRicevuta] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  // A ogni apertura si riparte da zero.
  useEffect(() => {
    if (!open) return;
    setMotivo("");
    setDescrizione("");
    setBuonaFede(false);
    setRicevuta(null);
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
      const esito = await segnala.mutateAsync({
        oggetto_tipo: oggettoTipo,
        oggetto_id: oggettoId,
        motivo,
        descrizione: descrizione.trim(),
        buona_fede: true,
      });
      setRicevuta(esito.id);
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
          <Button type="button" onClick={onClose}>
            Chiudi
          </Button>
        ) : (
          <>
            <Button type="button" variant="secondary" onClick={onClose} disabled={segnala.isPending}>
              Annulla
            </Button>
            <Button
              type="button"
              variant="danger"
              onClick={() => void invia()}
              loading={segnala.isPending}
              disabled={!valida}
            >
              Invia la segnalazione
            </Button>
          </>
        )
      }
    >
      <div aria-live="polite">
        {ricevuta && (
          <Alert
            tono="ok"
            // Stato, decisione ed eventuale ricorso (WP9).
            azione={<TextLink to={`/app/partenariati/segnalazioni/${ricevuta}`}>Segui la segnalazione</TextLink>}
          >
            {CALL_COPY.segnalaRicevuta}
          </Alert>
        )}
      </div>
      {!ricevuta && (
        <div className="flex flex-col gap-4">
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
          <Checkbox
            label={CALL_COPY.segnalaBuonaFede}
            checked={buonaFede}
            onChange={(e) => setBuonaFede(e.target.checked)}
          />
          {errore && <Alert tono="errore">{errore}</Alert>}
        </div>
      )}
    </Dialog>
  );
}
