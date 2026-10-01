import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import {
  bozzaInCorsoRecente,
  useAvviaBozzaAi,
  usePartnerProfile,
  useScartaBozzaAi,
} from "../../hooks/usePartnerProfile";
import { apiErrorMessage } from "../../lib/api";
import { PARTNER_COPY } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { BozzaProfiloAi, PartnerProfile } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Dialog } from "../ui/Dialog";
import { Spinner } from "../ui/Spinner";

/** Codici che il server può mettere in `bozza_ai.errore` al posto di una
 *  frase: si traducono qui. */
const ERRORI_BOZZA: Record<string, string> = {
  interrotta: "La preparazione si è interrotta prima di finire.",
  timeout: "La preparazione ha impiegato troppo tempo.",
  errore: "Non siamo riusciti a preparare la bozza.",
  ai_not_configured: "La bozza automatica non è disponibile in questo momento.",
};

function testoErrore(valore: string | null): string {
  const testo = valore?.trim();
  if (!testo) return PARTNER_COPY.bozzaErrore;
  if (/^[a-z0-9_]+$/.test(testo)) return ERRORI_BOZZA[testo] ?? PARTNER_COPY.bozzaErrore;
  return testo;
}

export interface SceltaBozza {
  descrizione: string | null;
  competenze: string[];
}

/** Bozza del profilo scritta dall'AI (a carico della piattaforma, con un
 *  limite giornaliero per azienda). Lavora in background: lo stato arriva col
 *  polling del profilo e si annuncia in `aria-live`. La proposta non si
 *  pubblica mai da sola: si sceglie cosa usare, «Applica» lo riporta nel form
 *  e il titolare salva con la barra. */
export function BozzaAiDialog({
  open,
  onClose,
  profilo,
  onApplica,
}: {
  open: boolean;
  onClose: () => void;
  profilo: PartnerProfile;
  onApplica: (scelta: SceltaBozza) => void;
}) {
  const vocabolario = usePartenariatiVocabolario();
  const { refetch, isFetching } = usePartnerProfile();
  const avvia = useAvviaBozzaAi();
  const scarta = useScartaBozzaAi();
  const [errore, setErrore] = useState<string | null>(null);
  const [usaDescrizione, setUsaDescrizione] = useState(false);
  const [competenze, setCompetenze] = useState<string[]>([]);
  const idBase = useId();
  const introRef = useRef<HTMLParagraphElement>(null);

  const bozza = profilo.bozza_ai;
  const inCorso = bozza?.stato === "in_corso";
  const inCorsoRecente = bozzaInCorsoRecente(profilo);
  const proposta: BozzaProfiloAi | null =
    bozza?.stato === "pronta" ? (bozza.proposta ?? null) : null;
  const giaNelProfilo = new Set(profilo.profilo.competenze);

  // A ogni apertura, o quando arriva una proposta nuova, nessuna parte è
  // scelta: si accetta voce per voce.
  const chiaveProposta = bozza?.pronta_at ?? null;
  useEffect(() => {
    if (!open) return;
    setUsaDescrizione(false);
    setCompetenze([]);
  }, [open, chiaveProposta]);

  useEffect(() => {
    if (!open) return;
    setErrore(null);
    // Focus sul testo introduttivo: dice subito cosa fa la finestra.
    const frame = requestAnimationFrame(() => introRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  const etichetta = (codice: string) =>
    vocabolario.data?.competenze.find((c) => c.codice === codice)?.etichetta ?? codice;
  const motivo = (codice: string) =>
    proposta?.motivazioni.find((m) => m.codice === codice)?.motivo ?? null;

  const handleAvvia = async () => {
    setErrore(null);
    try {
      await avvia.mutateAsync();
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const handleScarta = async () => {
    setErrore(null);
    try {
      await scarta.mutateAsync();
      onClose();
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const handleApplica = () => {
    if (!proposta) return;
    onApplica({
      descrizione: usaDescrizione ? proposta.descrizione_competenze : null,
      competenze: competenze.filter((c) => !giaNelProfilo.has(c)),
    });
  };

  const nuoveCompetenze = (proposta?.competenze ?? []).filter((c) => !giaNelProfilo.has(c));
  const qualcosaScelto =
    (usaDescrizione && !!proposta?.descrizione_competenze.trim()) ||
    competenze.some((c) => !giaNelProfilo.has(c));

  // Stato per i lettori di schermo (sempre montato, cambia solo il testo).
  const statoTesto = avvia.isPending
    ? "Avvio la bozza…"
    : inCorso
      ? inCorsoRecente
        ? PARTNER_COPY.bozzaInCorso
        : PARTNER_COPY.bozzaLunga
      : proposta
        ? PARTNER_COPY.bozzaPronta
        : bozza?.stato === "errore"
          ? testoErrore(bozza.errore)
          : "";

  let corpo: ReactNode = null;
  if (inCorso || avvia.isPending) {
    corpo = (
      <div className="flex items-start gap-3">
        {(inCorsoRecente || avvia.isPending) && <Spinner size="md" className="mt-0.5" />}
        <div className="flex flex-col gap-2">
          <p className="text-body text-ink-2" aria-hidden>
            {statoTesto}
          </p>
          {!inCorsoRecente && !avvia.isPending && (
            <div>
              <Button variant="secondary" size="sm" loading={isFetching} onClick={() => void refetch()}>
                Aggiorna lo stato
              </Button>
            </div>
          )}
        </div>
      </div>
    );
  } else if (proposta) {
    corpo = (
      <div className="flex flex-col gap-4">
        {bozza?.pronta_at && (
          <p className="text-small text-ink-3">Preparata il {formatDateTime(bozza.pronta_at)}</p>
        )}
        {proposta.descrizione_competenze.trim() && (
          <div className="flex flex-col gap-2 border-b border-line pb-4">
            <Checkbox
              label={<span className="font-medium">{PARTNER_COPY.bozzaUsaDescrizione}</span>}
              checked={usaDescrizione}
              onChange={(e) => setUsaDescrizione(e.target.checked)}
              aria-describedby={`${idBase}-descrizione`}
            />
            <p id={`${idBase}-descrizione`} className="whitespace-pre-line text-body text-ink-2">
              {proposta.descrizione_competenze}
            </p>
            {profilo.profilo.descrizione_competenze?.trim() && (
              <p className="text-small text-ink-3">Sostituisce la descrizione che hai già scritto.</p>
            )}
          </div>
        )}
        {proposta.competenze.length > 0 && (
          <fieldset className="flex flex-col gap-2">
            <legend className="text-small font-medium text-ink">
              {PARTNER_COPY.bozzaCompetenzeTitolo}
            </legend>
            {nuoveCompetenze.length > 1 && (
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setCompetenze(
                      competenze.length >= nuoveCompetenze.length ? [] : nuoveCompetenze,
                    )
                  }
                >
                  {competenze.length >= nuoveCompetenze.length ? "Togli tutte" : "Scegli tutte"}
                </Button>
              </div>
            )}
            <ul className="flex flex-col gap-2">
              {proposta.competenze.map((codice) => {
                const gia = giaNelProfilo.has(codice);
                const perche = motivo(codice);
                return (
                  <li key={codice}>
                    <Checkbox
                      label={
                        <>
                          <span className="font-medium">{etichetta(codice)}</span>
                          {gia && <span className="text-small text-ink-3"> (già nel profilo)</span>}
                        </>
                      }
                      descrizione={perche ?? undefined}
                      checked={gia || competenze.includes(codice)}
                      disabled={gia}
                      onChange={(e) =>
                        setCompetenze((attuali) =>
                          e.target.checked
                            ? [...attuali, codice]
                            : attuali.filter((c) => c !== codice),
                        )
                      }
                    />
                  </li>
                );
              })}
            </ul>
          </fieldset>
        )}
        <p className="text-small text-ink-3">
          È una proposta automatica: rileggila e correggila prima di salvare.
        </p>
      </div>
    );
  } else if (bozza?.stato === "errore") {
    // Lo stesso testo è nella regione live sr-only: qui solo a vista.
    corpo = (
      <div aria-hidden>
        <Alert tono="attenzione">{statoTesto}</Alert>
      </div>
    );
  }

  const occupato = avvia.isPending || scarta.isPending;
  const footer = (
    <>
      {proposta ? (
        <>
          <Button type="button" variant="secondary" onClick={() => void handleScarta()} loading={scarta.isPending}>
            {PARTNER_COPY.bozzaScarta}
          </Button>
          <Button type="button" onClick={handleApplica} disabled={!qualcosaScelto || occupato}>
            {PARTNER_COPY.bozzaApplica}
          </Button>
        </>
      ) : inCorso ? (
        <Button type="button" variant="secondary" onClick={onClose}>
          Chiudi
        </Button>
      ) : (
        <>
          <Button type="button" variant="secondary" onClick={onClose} disabled={occupato}>
            {PARTNER_COPY.annulla}
          </Button>
          <Button type="button" onClick={() => void handleAvvia()} loading={avvia.isPending}>
            {bozza?.stato === "errore" ? PARTNER_COPY.bozzaRigenera : PARTNER_COPY.bozzaAvvia}
          </Button>
        </>
      )}
    </>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={PARTNER_COPY.bozzaTitolo}
      size="lg"
      dismissible={!occupato}
      footer={footer}
    >
      <div className="flex flex-col gap-4">
        <p ref={introRef} tabIndex={-1} className="outline-none">
          {PARTNER_COPY.bozzaIntro}
        </p>
        <div role="status" aria-live="polite" className="sr-only">
          {open ? statoTesto : ""}
        </div>
        {corpo}
        {proposta && !qualcosaScelto && (
          <p className="text-small text-ink-3">{PARTNER_COPY.bozzaNienteScelto}</p>
        )}
        {errore && <Alert tono="errore">{errore}</Alert>}
      </div>
    </Dialog>
  );
}
