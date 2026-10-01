import { useEffect, useState } from "react";
import {
  useAggiornaCall,
  useAnteprimaCall,
  useProponiTesti,
} from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY } from "../../lib/copy";
import type { CallAggiornaInput } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { TextField } from "../ui/Field";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { BarraPasso } from "./CallStepper";
import { LIMITI_CALL } from "./callDati";
import { TestoLungo } from "./CampiCall";
import { JobAiStato } from "./JobAiStato";
import { avanzamento, soloCambiati, vuoto, type PassoProps } from "./passoComune";
import { RilieviCall } from "./RilieviCall";

type CampoTesto = "titolo" | "descrizione_pubblica" | "profilo_partner_ideale";

const NOMI: Record<CampoTesto, string> = {
  titolo: "Titolo",
  descrizione_pubblica: "Descrizione del progetto",
  profilo_partner_ideale: "Partner ideale",
};

const testo = (v: string) => (v.trim() ? v.trim() : null);

/** Passo 5: i testi pubblici (con la bozza dell'AI, già anonimizzata) e i
 *  dettagli riservati, visibili solo alle aziende che accetti. */
export function PassoTesti({ call, onAvanti, onIndietro, onDirty }: PassoProps) {
  const aggiorna = useAggiornaCall(call.id);
  const proponi = useProponiTesti(call.id);
  const anteprima = useAnteprimaCall(call.id);
  const bozza = call.stato === "bozza";

  const [valori, setValori] = useState({
    titolo: call.titolo ?? "",
    descrizione_pubblica: call.descrizione_pubblica ?? "",
    profilo_partner_ideale: call.profilo_partner_ideale ?? "",
    dettagli_riservati: call.dettagli_riservati ?? "",
  });
  const [errori, setErrori] = useState<string[]>([]);
  const [annuncio, setAnnuncio] = useState<string | null>(null);

  const campi: CallAggiornaInput = soloCambiati(call, {
    // Dopo la pubblicazione il titolo non si cambia (fuori whitelist).
    ...(bozza ? { titolo: testo(valori.titolo) } : {}),
    descrizione_pubblica: testo(valori.descrizione_pubblica),
    profilo_partner_ideale: testo(valori.profilo_partner_ideale),
    dettagli_riservati: testo(valori.dettagli_riservati),
  });
  const dirty = !vuoto(campi);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const job = call.ai_testi;
  const proposta = job.stato === "pronta" ? job.proposta : null;

  const usa = (campo: CampoTesto, valore: string | null) => {
    if (!valore) return;
    if (campo === "titolo" && !bozza) return;
    setValori((v) => ({ ...v, [campo]: valore }));
    setAnnuncio(`${NOMI[campo]}: abbiamo riportato il testo proposto nel campo. Controllalo e salva.`);
  };

  const salva = async () => {
    setErrori([]);
    const problemi: string[] = [];
    const t = valori.titolo.trim();
    if (bozza && t && (t.length < LIMITI_CALL.titoloMin || t.length > LIMITI_CALL.titoloMax)) {
      problemi.push(`Il titolo deve avere tra ${LIMITI_CALL.titoloMin} e ${LIMITI_CALL.titoloMax} caratteri.`);
    }
    if (problemi.length) {
      setErrori(problemi);
      return;
    }
    try {
      const corpo = { ...campi, ...avanzamento(call, 6) };
      if (!vuoto(corpo)) await aggiorna.mutateAsync(corpo);
      onAvanti();
    } catch {
      // mostrato nella barra (il server nomina campo e tipo del rilievo)
    }
  };

  return (
    <div className="flex flex-col gap-6">
      {bozza && (
        <JobAiStato
          job={job}
          descrizione="L'AI può scrivere una bozza del titolo e dei testi, senza dati che fanno riconoscere l'azienda."
          etichettaAvvia="Scrivi con l'AI"
          etichettaRigenera="Nuova bozza"
          onAvvia={() => proponi.mutate()}
          avvioInCorso={proponi.isPending}
          erroreAvvio={proponi.isError ? apiErrorMessage(proponi.error) : null}
        >
          {proposta && (
            <div className="flex flex-col gap-3">
              {proposta.avvisi.length > 0 && (
                <Alert tono="attenzione">
                  <ul className="list-disc pl-5">
                    {proposta.avvisi.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </Alert>
              )}
              <RilieviCall rilievi={proposta.rilievi} titolo="Nella bozza è rimasto qualcosa da togliere" />
              <ul className="flex flex-col border-t border-line">
                {(["titolo", "descrizione_pubblica", "profilo_partner_ideale"] as const).map((campo) =>
                  proposta[campo] ? (
                    <li key={campo} className="flex flex-wrap items-start justify-between gap-3 border-b border-line py-3">
                      <div className="min-w-0 flex-1 flex flex-col gap-1">
                        <p className="text-small font-medium text-ink-3">{NOMI[campo]}</p>
                        <p className="whitespace-pre-line text-body text-ink">{proposta[campo]}</p>
                      </div>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => usa(campo, proposta[campo])}
                        disabled={campo === "titolo" && !bozza}
                      >
                        Usa questo testo
                      </Button>
                    </li>
                  ) : null,
                )}
              </ul>
            </div>
          )}
        </JobAiStato>
      )}

      <div aria-live="polite">{annuncio && <Alert tono="ok">{annuncio}</Alert>}</div>
      <p className="text-small text-ink-3">{CALL_COPY.rilieviNota}</p>
      <TextField
        label="Titolo della call"
        required
        maxLength={LIMITI_CALL.titoloMax}
        value={valori.titolo}
        disabled={!bozza}
        onChange={(e) => setValori((v) => ({ ...v, titolo: e.target.value }))}
        helper={
          bozza
            ? `Tra ${LIMITI_CALL.titoloMin} e ${LIMITI_CALL.titoloMax} caratteri: si legge nella bacheca.`
            : "Dopo la pubblicazione il titolo non si cambia."
        }
      />
      <TestoLungo
        etichetta="Descrizione del progetto"
        aiuto="Cosa volete fare e perché cercate partner. Visibile a tutte le aziende che vedono la call."
        valore={valori.descrizione_pubblica}
        onChange={(v) => setValori((x) => ({ ...x, descrizione_pubblica: v }))}
        massimo={LIMITI_CALL.descrizioneMax}
        righe={7}
        required
      />
      <TestoLungo
        etichetta="Il partner ideale (facoltativo)"
        aiuto="Chi cerchi, in parole: competenze, esperienze, modo di lavorare."
        valore={valori.profilo_partner_ideale}
        onChange={(v) => setValori((x) => ({ ...x, profilo_partner_ideale: v }))}
        massimo={LIMITI_CALL.profiloMax}
        righe={4}
      />
      <Section>
        <SectionHeader titolo="Dettagli riservati" livello={3} />
        <TestoLungo
          etichetta="Dettagli per le aziende che accetti (facoltativi)"
          aiuto="Li vede solo chi accetti nel partenariato. Qui puoi nominare l'azienda, ma niente email, telefoni o siti: i contatti si scambiano in chat."
          valore={valori.dettagli_riservati}
          onChange={(v) => setValori((x) => ({ ...x, dettagli_riservati: v }))}
          massimo={LIMITI_CALL.riservatiMax}
          righe={4}
        />
      </Section>

      {!dirty && anteprima.data && anteprima.data.rilievi.length > 0 && (
        <div className="flex flex-col gap-2">
          <p className="text-small font-medium text-ink">Controllo dei testi salvati</p>
          <RilieviCall rilievi={anteprima.data.rilievi} />
        </div>
      )}

      {errori.length > 0 && <Alert tono="errore">{errori.join(" ")}</Alert>}
      <BarraPasso
        onIndietro={onIndietro}
        onAvanti={() => void salva()}
        inCorso={aggiorna.isPending}
        errore={aggiorna.isError ? apiErrorMessage(aggiorna.error) : null}
      />
    </div>
  );
}
