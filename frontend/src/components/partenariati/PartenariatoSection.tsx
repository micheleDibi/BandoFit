import { ChevronDown, RefreshCw, Users } from "lucide-react";
import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import {
  analisiInCorso,
  avviataDiRecente,
  PARTENARIATO_FINESTRA_POLLING_MS,
  useAvviaAnalisiPartenariato,
  usePartenariatoBando,
} from "../../hooks/usePartenariatoBando";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTENARIATO_COPY } from "../../lib/copy";
import { formatDate, formatDateTime } from "../../lib/format";
import type { FasePartenariato, PartenariatoBando } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { IconChip } from "../ui/IconChip";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Spinner } from "../ui/Spinner";
import { ErrorState, Skeleton } from "../ui/states";
import {
  PARTENARIATO_ANCORA,
  PARTENARIATO_CONTENUTO_ID,
  PARTENARIATO_TOGGLE_ID,
  vaiASezionePartenariato,
} from "./ancora";
import { ModalitaBadge } from "./ModalitaBadge";
import { Avanzamento, DocumentiAnalizzati, Regole } from "./PartenariatoRegole";

const TITOLO_ID = "partenariato-titolo";

// ---- Stato dell'analisi ---------------------------------------------------

const FASE_IN_CORSO: Record<FasePartenariato, string> = {
  documenti: "Scarico i documenti ufficiali…",
  lettura: "Leggo i PDF…",
  analisi: "Analizzo le regole…",
};

/** Codici che il server può mandare al posto di un messaggio (errori della
 *  POST, `errore` e `motivo_non_avviabile`): si traducono qui. */
const MESSAGGI_CODICE: Record<string, string> = {
  partenariato_cooldown:
    "Le regole di questo bando sono state analizzate da poco: riprova più tardi",
  ai_limite_giornaliero: "Hai raggiunto il numero di analisi di oggi: riprova domani",
  ai_sospesa_oggi: "L'analisi automatica è sospesa per oggi: riprova domani",
  ai_not_configured: "L'analisi automatica non è disponibile in questo momento",
  email_non_verificata: "Conferma il tuo indirizzo email per avviare l'analisi",
  forza_non_ammessa: "L'analisi completa si può chiedere una sola volta per questo bando",
  timeout: "L'analisi ha impiegato troppo tempo",
  interrotta: "L'analisi si è interrotta prima di finire",
  // `motivo_non_avviabile` della GET
  ai_non_configurata: "L'analisi automatica non è disponibile in questo momento",
  cooldown: "Le regole di questo bando sono state analizzate da poco: riprova più tardi",
  aggiornata:
    "L'analisi è recente: la ripeteremo da sola quando il bando cambia o tra qualche giorno",
  in_corso: "L'analisi è già in corso",
};

/** Errori della POST per cui riprovare subito è inutile: niente bottone. */
const ERRORI_SENZA_RIPROVA = new Set([
  "partenariato_cooldown",
  "ai_limite_giornaliero",
  "ai_sospesa_oggi",
  "ai_not_configured",
  "email_non_verificata",
  "forza_non_ammessa",
  "not_found",
]);

/** Un messaggio del server resta com'è; un codice macchina (snake_case) si
 *  traduce, o lascia il posto al ripiego. */
function testoPerUtente(valore: string | null | undefined, ripiego: string): string {
  const testo = valore?.trim();
  if (!testo) return ripiego;
  if (/^[a-z0-9_]+$/.test(testo)) return MESSAGGI_CODICE[testo] ?? ripiego;
  return testo;
}

const NON_RILANCIABILE = "Per ora non è possibile rilanciare l'analisi";

function nelFuturo(iso: string | null): boolean {
  if (!iso) return false;
  const t = new Date(iso).getTime();
  return Number.isFinite(t) && t > Date.now();
}

// ---- Sezione ---------------------------------------------------------------

interface ErroreAvvio {
  testo: string;
  codice: string | undefined;
  /** Avvio partito da solo all'apertura (non chiesto con un bottone). */
  automatico: boolean;
}

/** Messaggio per i lettori di schermo alla fine di un'analisi seguita da
 *  qui: anche un tentativo fallito con un risultato precedente ancora
 *  servito (regole o «nessun riferimento») va detto come tale. */
function annuncioFine(dati: PartenariatoBando): string {
  if (dati.errore) {
    if (dati.regole) {
      return "L'aggiornamento non è riuscito: restano le regole lette in precedenza.";
    }
    if (dati.stato === "nessun_segnale") return "L'analisi completa non è riuscita.";
    return "L'analisi delle regole non è riuscita.";
  }
  if (dati.stato === "errore") return "L'analisi delle regole non è riuscita.";
  if (dati.stato === "nessun_segnale") {
    return "Analisi completata: nel testo non ci sono riferimenti a partenariati.";
  }
  return "Analisi completata: le regole sono qui sotto.";
}

function sottotitolo(dati: PartenariatoBando | undefined): string {
  if (dati?.regole && dati.estratta_at) {
    const verificata =
      dati.verificata_at && formatDate(dati.verificata_at) !== formatDate(dati.estratta_at)
        ? ` e ricontrollate il ${formatDate(dati.verificata_at)}`
        : "";
    return `Lette dai documenti ufficiali il ${formatDate(dati.estratta_at)}${verificata}.`;
  }
  return "Chi può partecipare insieme a chi: forme ammesse, numero di partner, quote e documenti.";
}

/** Sezione a tutta larghezza di BandoDetail con le regole di partenariato
 *  estratte dai documenti ufficiali. Chiusa di default: all'apertura legge lo
 *  stato e, se le regole mancano o vanno aggiornate, avvia UNA volta l'analisi
 *  (a carico della piattaforma, con i limiti giornalieri del server). Non
 *  esiste a modulo spento. */
export function PartenariatoSection({
  slug,
  open,
  onOpenChange,
}: {
  slug: string;
  open: boolean;
  onOpenChange: (aperta: boolean) => void;
}) {
  const { partenariatiAttivo } = useFunzioni();
  const { data, isPending, isError, error, refetch, isFetching } = usePartenariatoBando(slug);
  const vocabolario = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const avvio = useAvviaAnalisiPartenariato(slug);
  const [erroreAvvio, setErroreAvvio] = useState<ErroreAvvio | null>(null);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  // Un solo avvio automatico per bando (la sezione è montata con key=slug):
  // niente ritentativi da soli, né dopo un rifiuto né dopo un errore.
  const avvioAutomaticoFatto = useRef(false);
  const eraInCorso = useRef(false);
  const [, ridisegna] = useReducer((n: number) => n + 1, 0);
  const { hash } = useLocation();

  const inCorso = !!data && analisiInCorso(data);
  const oltreFinestra = inCorso && !!data && !avviataDiRecente(data);

  const avvia = async (forza: boolean, automatico: boolean) => {
    setErroreAvvio(null);
    try {
      await avvio.mutateAsync({ forza });
    } catch (err) {
      setErroreAvvio({ testo: apiErrorMessage(err), codice: apiErrorCode(err), automatico });
    }
  };

  // Link diretto a #partenariato (anche da altre pagine): apre la sezione.
  useEffect(() => {
    if (!partenariatiAttivo || hash !== `#${PARTENARIATO_ANCORA}`) return;
    onOpenChange(true);
    vaiASezionePartenariato();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hash, partenariatiAttivo]);

  // Avvio automatico all'apertura: regole mai estratte o da aggiornare, e il
  // server dice che si può (limiti, attesa, analisi già in corso).
  useEffect(() => {
    if (!open || !data || avvioAutomaticoFatto.current) return;
    if (analisiInCorso(data) || !data.puo_avviare) return;
    if (data.stato !== "non_estratta" && !data.aggiornabile) return;
    avvioAutomaticoFatto.current = true;
    void avvia(false, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, data]);

  // Fine di un'analisi seguita da qui: lo si annuncia ai lettori di schermo
  // (il contenuto cambia, ma senza un messaggio il cambio passa inosservato).
  useEffect(() => {
    if (!data) return;
    if (inCorso) {
      eraInCorso.current = true;
      setAnnuncio(null);
      return;
    }
    if (!eraInCorso.current) return;
    eraInCorso.current = false;
    setAnnuncio(annuncioFine(data));
  }, [data, inCorso]);

  // A finestra scaduta il polling si ferma: si ridisegna una volta per
  // proporre «Aggiorna lo stato» invece di un'attesa senza fine.
  useEffect(() => {
    if (!inCorso || !data?.avviata_at) return;
    const inizio = new Date(data.avviata_at).getTime();
    const resta = inizio + PARTENARIATO_FINESTRA_POLLING_MS - Date.now();
    if (!Number.isFinite(resta) || resta <= 0) return;
    const timer = window.setTimeout(ridisegna, resta + 1_000);
    return () => window.clearTimeout(timer);
  }, [inCorso, data?.avviata_at]);

  // 404 = modulo spento lato server: la sezione sparisce, come la card.
  if (!partenariatiAttivo || (isError && apiErrorCode(error) === "not_found")) return null;

  const testoAvanzamento = avvio.isPending
    ? "Avvio l'analisi…"
    : data && inCorso && !oltreFinestra
      ? data.regole
        ? "Aggiornamento in corso: intanto vedi le regole lette in precedenza."
        : ((data.fase && FASE_IN_CORSO[data.fase]) ?? "Analisi in corso…")
      : null;

  const puoRiprovareDopoErrore =
    !!erroreAvvio &&
    !ERRORI_SENZA_RIPROVA.has(erroreAvvio.codice ?? "") &&
    !!data?.puo_avviare &&
    !inCorso;

  const avvisoAvvio = (conBottone: boolean) =>
    erroreAvvio &&
    (erroreAvvio.automatico && data?.regole ? (
      // Aggiornamento partito da solo e rifiutato: le regole restano valide,
      // basta dirlo senza allarmare.
      <p className="text-small text-ink-3">
        Non è stato possibile aggiornare le regole adesso: {erroreAvvio.testo}
      </p>
    ) : (
      <Alert
        tono="errore"
        azione={
          conBottone && puoRiprovareDopoErrore ? (
            <Button
              variant="secondary"
              size="sm"
              loading={avvio.isPending}
              onClick={() => void avvia(false, false)}
            >
              Riprova
            </Button>
          ) : undefined
        }
      >
        {erroreAvvio.testo}
      </Alert>
    ));

  const bloccoFermo = (
    <Alert
      tono="attenzione"
      azione={
        <Button variant="secondary" size="sm" loading={isFetching} onClick={() => void refetch()}>
          <RefreshCw className="size-4" aria-hidden />
          Aggiorna lo stato
        </Button>
      }
    >
      L'analisi sta impiegando più del previsto.
    </Alert>
  );

  let corpo: ReactNode;
  if (isPending) {
    corpo = (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
      </div>
    );
  } else if (isError || !data) {
    corpo = (
      <ErrorState
        message={apiErrorMessage(error, "Impossibile caricare le regole di partenariato.")}
        onRetry={() => void refetch()}
      />
    );
  } else if (data.regole) {
    corpo = (
      <>
        {oltreFinestra && bloccoFermo}
        {data.errore && !inCorso && (
          <p className="text-small text-ink-3">
            L'ultimo aggiornamento non è riuscito
            {data.estratta_at && `: ti mostriamo le regole lette il ${formatDate(data.estratta_at)}`}
            .
          </p>
        )}
        {avvisoAvvio(false)}
        {vocabolario.isPending ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        ) : (
          <Regole
            regole={data.regole}
            fonti={data.fonti ?? []}
            vocabolario={vocabolario.data}
            lookups={lookups}
          />
        )}
        <DocumentiAnalizzati fonti={data.fonti ?? []} />
      </>
    );
  } else if (inCorso) {
    corpo = oltreFinestra ? bloccoFermo : <Avanzamento fase={data.fase} />;
  } else if (data.stato === "nessun_segnale") {
    corpo = (
      <>
        <div className="flex flex-col items-start gap-2 rounded-panel bg-desk p-5">
          <p className="text-body text-ink">
            Nel testo che abbiamo letto non ci sono riferimenti a partenariati o aggregazioni.
          </p>
          {data.errore ? (
            // «Analizza comunque» (o un aggiornamento) chiesto e fallito: va
            // detto, altrimenti sembra che l'analisi completa abbia confermato.
            <Alert tono="errore" className="self-stretch">
              L'ultima analisi non è riuscita.{" "}
              {testoPerUtente(data.errore, "Riprova più tardi.")}
            </Alert>
          ) : (
            <p className="text-small text-ink-3">
              Se pensi che il bando li preveda, possiamo analizzarlo comunque: ti mostreremo i
              passaggi da cui ricaviamo le regole.
            </p>
          )}
          {data.puo_avviare ? (
            <Button
              variant="secondary"
              size="sm"
              className="mt-1"
              loading={avvio.isPending}
              onClick={() => void avvia(true, false)}
            >
              Analizza comunque
            </Button>
          ) : (
            data.motivo_non_avviabile && (
              <p className="text-small text-ink-3">
                {testoPerUtente(data.motivo_non_avviabile, NON_RILANCIABILE)}
              </p>
            )
          )}
        </div>
        {avvisoAvvio(false)}
        <DocumentiAnalizzati fonti={data.fonti ?? []} />
      </>
    );
  } else if (data.stato === "errore") {
    const inAttesa = nelFuturo(data.riprova_dopo);
    const suggerimento = inAttesa
      ? `Potrai riprovare dal ${formatDateTime(data.riprova_dopo)}.`
      : !data.puo_avviare
        ? testoPerUtente(data.motivo_non_avviabile, NON_RILANCIABILE)
        : null;
    corpo = (
      <>
        <ErrorState message={testoPerUtente(data.errore, "L'analisi delle regole non è riuscita")} />
        <div className="flex flex-col items-center gap-1.5">
          <Button
            variant="secondary"
            disabled={inAttesa || !data.puo_avviare}
            loading={avvio.isPending}
            onClick={() => void avvia(false, false)}
          >
            Riprova
          </Button>
          {suggerimento && <p className="text-small text-ink-3">{suggerimento}</p>}
        </div>
        {avvisoAvvio(false)}
        {(data.fonti ?? []).length > 0 && <DocumentiAnalizzati fonti={data.fonti} />}
      </>
    );
  } else {
    // Mai analizzate: l'avvio automatico parte all'apertura. Qui restano i
    // casi in cui non è partito (limiti, attesa), è stato rifiutato, oppure il
    // server ha risposto senza avviarlo: allora si offre l'avvio a mano.
    corpo = erroreAvvio ? (
      avvisoAvvio(true)
    ) : !data.puo_avviare && !avvio.isPending ? (
      <p className="text-body text-ink-2">
        {testoPerUtente(
          data.motivo_non_avviabile,
          "Per ora non possiamo analizzare le regole di questo bando: riprova più tardi",
        )}
      </p>
    ) : avvio.isSuccess ? (
      <div>
        <Button variant="secondary" size="sm" onClick={() => void avvia(false, false)}>
          Avvia l'analisi
        </Button>
      </div>
    ) : null;
  }

  // Stessa cornice della sezione «Report AI-check» che la precede (card sul
  // piano, titolo con l'`IconChip` e il filetto, `scroll-mt-16`): le due ancore
  // della scheda si fermano allo stesso punto. Il titolo è il pulsante che apre
  // e chiude la sezione.
  return (
    <Card className="p-6 sm:p-8">
      <Section id={PARTENARIATO_ANCORA} aria-labelledby={TITOLO_ID} className="scroll-mt-16">
        <SectionHeader
          id={TITOLO_ID}
          titolo={
            <button
              id={PARTENARIATO_TOGGLE_ID}
              type="button"
              aria-expanded={open}
              aria-controls={PARTENARIATO_CONTENUTO_ID}
              onClick={() => onOpenChange(!open)}
              className="inline-flex cursor-pointer items-center gap-3 rounded-mark text-left transition-colors duration-150 ease-uscita hover:text-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <IconChip icon={Users} area="partenariati" size="sm" />
              Regole di partenariato
              <ChevronDown
                className={cn(
                  "size-5 shrink-0 text-ink-3 motion-safe:transition-transform motion-safe:duration-250 motion-safe:ease-uscita",
                  open && "rotate-180",
                )}
                aria-hidden
              />
            </button>
          }
          azione={data?.regole && <ModalitaBadge modalita={data.regole.modalita_effettiva} />}
        />
        <p className="text-small text-ink-3">{sottotitolo(data)}</p>

        <div id={PARTENARIATO_CONTENUTO_ID} hidden={!open}>
          {open && (
            <div className="flex flex-col gap-5">
              <div role="status" aria-live="polite">
                {testoAvanzamento ? (
                  <p className="inline-flex items-center gap-2 text-body font-medium text-ink-2">
                    <Spinner size="sm" />
                    {testoAvanzamento}
                  </p>
                ) : annuncio ? (
                  <p className="sr-only">{annuncio}</p>
                ) : null}
              </div>
              <p role="note" className="text-small text-ink-3">
                {PARTENARIATO_COPY.disclaimer}
              </p>
              {corpo}
            </div>
          )}
        </div>
      </Section>
    </Card>
  );
}
