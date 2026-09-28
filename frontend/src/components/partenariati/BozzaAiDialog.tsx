import { Loader2, RefreshCw, Sparkles } from "lucide-react";
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
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

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
      <div className="flex items-start gap-3 rounded-lg bg-slate-50 px-4 py-3">
        {inCorsoRecente || avvia.isPending ? (
          <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-brand-600" aria-hidden />
        ) : null}
        <div>
          <p className="text-sm text-slate-700" aria-hidden>
            {statoTesto}
          </p>
          {!inCorsoRecente && !avvia.isPending && (
            <Button
              variant="secondary"
              size="sm"
              className="mt-2"
              loading={isFetching}
              onClick={() => void refetch()}
            >
              <RefreshCw className="size-4" aria-hidden />
              Aggiorna lo stato
            </Button>
          )}
        </div>
      </div>
    );
  } else if (proposta) {
    corpo = (
      <div className="space-y-4">
        {bozza?.pronta_at && (
          <p className="text-xs text-slate-400">Preparata il {formatDateTime(bozza.pronta_at)}</p>
        )}
        {proposta.descrizione_competenze.trim() && (
          <div className="rounded-lg border border-slate-200 p-3">
            <label className="flex cursor-pointer items-start gap-2 text-sm font-medium text-slate-800">
              <input
                type="checkbox"
                className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
                checked={usaDescrizione}
                onChange={(e) => setUsaDescrizione(e.target.checked)}
                aria-describedby={`${idBase}-descrizione`}
              />
              {PARTNER_COPY.bozzaUsaDescrizione}
            </label>
            <p
              id={`${idBase}-descrizione`}
              className="mt-2 whitespace-pre-line text-sm text-slate-600"
            >
              {proposta.descrizione_competenze}
            </p>
            {profilo.profilo.descrizione_competenze?.trim() && (
              <p className="mt-2 text-xs text-slate-500">
                Sostituisce la descrizione che hai già scritto.
              </p>
            )}
          </div>
        )}
        {proposta.competenze.length > 0 && (
          <fieldset>
            <legend className="text-sm font-medium text-slate-800">
              {PARTNER_COPY.bozzaCompetenzeTitolo}
            </legend>
            {nuoveCompetenze.length > 1 && (
              <button
                type="button"
                className="mt-1 cursor-pointer text-xs font-medium text-brand-600 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-brand-500"
                onClick={() =>
                  setCompetenze(
                    competenze.length >= nuoveCompetenze.length ? [] : nuoveCompetenze,
                  )
                }
              >
                {competenze.length >= nuoveCompetenze.length ? "Togli tutte" : "Scegli tutte"}
              </button>
            )}
            <ul className="mt-2 space-y-2">
              {proposta.competenze.map((codice) => {
                const gia = giaNelProfilo.has(codice);
                const perche = motivo(codice);
                const idMotivo = `${idBase}-motivo-${codice}`;
                return (
                  <li key={codice}>
                    <label
                      className={
                        gia
                          ? "flex cursor-not-allowed items-start gap-2 text-sm text-slate-400"
                          : "flex cursor-pointer items-start gap-2 text-sm text-slate-700"
                      }
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
                        checked={gia || competenze.includes(codice)}
                        disabled={gia}
                        onChange={(e) =>
                          setCompetenze((attuali) =>
                            e.target.checked
                              ? [...attuali, codice]
                              : attuali.filter((c) => c !== codice),
                          )
                        }
                        aria-describedby={perche ? idMotivo : undefined}
                      />
                      <span>
                        <span className="font-medium">{etichetta(codice)}</span>
                        {gia && <span className="text-xs"> · già nel profilo</span>}
                        {perche && (
                          <span id={idMotivo} className="block text-xs text-slate-500">
                            {perche}
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
        )}
        <p className="text-xs text-slate-500">
          È una proposta automatica: rileggila e correggila prima di salvare.
        </p>
      </div>
    );
  } else if (bozza?.stato === "errore") {
    corpo = (
      <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800" aria-hidden>
        {statoTesto}
      </p>
    );
  }

  const occupato = avvia.isPending || scarta.isPending;
  const footer = (
    <>
      {proposta ? (
        <>
          <Button variant="ghost" onClick={() => void handleScarta()} loading={scarta.isPending}>
            {PARTNER_COPY.bozzaScarta}
          </Button>
          <Button onClick={handleApplica} disabled={!qualcosaScelto || occupato}>
            {PARTNER_COPY.bozzaApplica}
          </Button>
        </>
      ) : inCorso ? (
        <Button variant="ghost" onClick={onClose}>
          Chiudi
        </Button>
      ) : (
        <>
          <Button variant="ghost" onClick={onClose} disabled={occupato}>
            {PARTNER_COPY.annulla}
          </Button>
          <Button onClick={() => void handleAvvia()} loading={avvia.isPending}>
            <Sparkles className="size-4" aria-hidden />
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
      <div className="space-y-4">
        <p ref={introRef} tabIndex={-1} className="focus:outline-none">
          {PARTNER_COPY.bozzaIntro}
        </p>
        <div role="status" aria-live="polite" className="sr-only">
          {open ? statoTesto : ""}
        </div>
        {corpo}
        {proposta && !qualcosaScelto && (
          <p className="text-xs text-slate-500">{PARTNER_COPY.bozzaNienteScelto}</p>
        )}
        {errore && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {errore}
          </p>
        )}
      </div>
    </Dialog>
  );
}
