import { ChevronUp } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { cn } from "../../lib/cn";
import { CHAT_COPY } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { Messaggio } from "../../types";
import { Button } from "../ui/Button";
import { BottoneSegnalaMessaggio, SegnalaMessaggio } from "./SegnalaMessaggio";

/** Distanza dal fondo sotto cui si considera «in fondo» (px): lì un
 *  messaggio nuovo fa scorrere la conversazione, altrimenti no. */
const SOGLIA_FONDO_PX = 80;

const primo = (items: readonly Messaggio[]) => (items.length ? items[0].id : 0);
const ultimo = (items: readonly Messaggio[]) => (items.length ? items[items.length - 1].id : 0);

function Bolla({
  messaggio,
  autore,
  separatoreRef,
  conSeparatore,
  onSegnala,
}: {
  messaggio: Messaggio;
  autore: string;
  separatoreRef: RefObject<HTMLDivElement>;
  conSeparatore: boolean;
  onSegnala: (id: number) => void;
}) {
  const quando = messaggio.created_at ? formatDateTime(messaggio.created_at) : "";
  return (
    <>
      {conSeparatore && (
        <div ref={separatoreRef} className="flex items-center gap-3 py-1" role="separator" aria-label={CHAT_COPY.nuoviMessaggi}>
          <span className="h-px flex-1 bg-brand-200" aria-hidden />
          <span className="text-xs font-semibold uppercase tracking-wide text-brand-700">
            {CHAT_COPY.nuoviMessaggi}
          </span>
          <span className="h-px flex-1 bg-brand-200" aria-hidden />
        </div>
      )}
      <div className={cn("flex max-w-[85%] flex-col gap-1", messaggio.propria ? "ml-auto items-end" : "mr-auto items-start")}>
        <p className="text-xs text-slate-500">
          <span className="font-medium text-slate-700">{autore}</span>
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          {messaggio.created_at && <time dateTime={messaggio.created_at}>{quando}</time>}
        </p>
        {messaggio.nascosto || messaggio.testo === null ? (
          <p className="rounded-2xl border border-dashed border-slate-300 px-3.5 py-2 text-sm italic text-slate-500">
            {CHAT_COPY.oscurato}
          </p>
        ) : (
          <p
            className={cn(
              "whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
              messaggio.propria ? "bg-brand-500 text-white" : "bg-slate-100 text-slate-800",
            )}
          >
            {messaggio.testo}
          </p>
        )}
        {!messaggio.propria && !messaggio.nascosto && messaggio.testo !== null && (
          <BottoneSegnalaMessaggio
            descrizione={quando ? `il messaggio di ${autore} del ${quando}` : `il messaggio di ${autore}`}
            onClick={() => onSegnala(messaggio.id)}
          />
        )}
      </div>
    </>
  );
}

/** Messaggi della conversazione, dal più vecchio al più recente.
 *  - I messaggi presenti all'apertura e quelli che arrivano dopo stanno in un
 *    `role="log"` (`aria-live="polite"`, `aria-relevant="additions"`): i
 *    lettori di schermo annunciano solo i nuovi. I messaggi più vecchi
 *    caricati a richiesta vanno sopra, FUORI dalla regione live (non si
 *    annunciano).
 *  - Separatore «Nuovi messaggi» prima del primo messaggio dell'altra azienda
 *    oltre `lettoFinoAId`, fissato all'apertura (non salta quando si segna
 *    come letto).
 *  - Scorrimento: all'apertura al separatore o in fondo; un messaggio nuovo
 *    fa scorrere solo se eri già in fondo (o se l'hai scritto tu); caricando
 *    i precedenti la posizione resta. Va montato quando i messaggi sono già
 *    arrivati. */
export function ChatThread({
  messaggi,
  lettoFinoAId,
  controparte,
  haPrecedenti,
  onCaricaPrecedenti,
  caricandoPrecedenti,
  errorePrecedenti,
}: {
  messaggi: readonly Messaggio[];
  lettoFinoAId: number;
  /** Come si chiama l'altra azienda («Azienda anonima»…). */
  controparte: string;
  haPrecedenti: boolean;
  onCaricaPrecedenti: () => void;
  caricandoPrecedenti: boolean;
  errorePrecedenti: string | null;
}) {
  const idTitolo = useId();
  const contenitore = useRef<HTMLDivElement>(null);
  const separatore = useRef<HTMLDivElement>(null);
  // Fissati all'apertura: confine tra storico (caricato dopo) e log, e fin
  // dove avevi letto.
  const [confine] = useState(() => primo(messaggi));
  const [lettoAllApertura] = useState(() => lettoFinoAId);
  const [segnalato, setSegnalato] = useState<number | null>(null);
  const inFondo = useRef(true);
  const precedente = useRef<{ primo: number; ultimo: number; altezza: number } | null>(null);

  const idSeparatore = messaggi.find((m) => !m.propria && m.id > lettoAllApertura)?.id ?? null;
  const storico = confine > 0 ? messaggi.filter((m) => m.id < confine) : [];
  const recenti = confine > 0 ? messaggi.filter((m) => m.id >= confine) : messaggi;
  const primoId = primo(messaggi);
  const ultimoId = ultimo(messaggi);
  const ultimoMio = messaggi.length > 0 && messaggi[messaggi.length - 1].propria;

  useLayoutEffect(() => {
    const el = contenitore.current;
    if (!el) return;
    const prima = precedente.current;
    if (!prima) {
      if (separatore.current) {
        // Il contenitore è `relative`: offsetTop è misurato da lì.
        el.scrollTop = Math.max(0, separatore.current.offsetTop - 16);
      } else {
        el.scrollTop = el.scrollHeight;
      }
    } else if (primoId < prima.primo) {
      // Caricati i precedenti: la vista resta sul messaggio che leggevi.
      el.scrollTop += el.scrollHeight - prima.altezza;
    } else if (ultimoId > prima.ultimo && (inFondo.current || ultimoMio)) {
      el.scrollTop = el.scrollHeight;
    }
    precedente.current = { primo: primoId, ultimo: ultimoId, altezza: el.scrollHeight };
  }, [primoId, ultimoId, ultimoMio]);

  const autore = (m: Messaggio) => (m.propria ? CHAT_COPY.tuaAzienda : controparte);
  const bolla = (m: Messaggio) => (
    <Bolla
      key={m.id}
      messaggio={m}
      autore={autore(m)}
      separatoreRef={separatore}
      conSeparatore={m.id === idSeparatore}
      onSegnala={setSegnalato}
    />
  );

  return (
    <section aria-labelledby={idTitolo}>
      <h2 id={idTitolo} className="sr-only">
        Messaggi
      </h2>
      <div
        ref={contenitore}
        tabIndex={0}
        aria-label="Messaggi della conversazione"
        onScroll={(e) => {
          const el = e.currentTarget;
          inFondo.current = el.scrollHeight - el.scrollTop - el.clientHeight < SOGLIA_FONDO_PX;
        }}
        className="relative max-h-[60vh] min-h-48 overflow-y-auto rounded-lg border border-slate-200 bg-white p-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 sm:p-4"
      >
        {haPrecedenti && (
          <div className="mb-3 flex flex-col items-center gap-1">
            <Button variant="ghost" size="sm" onClick={onCaricaPrecedenti} loading={caricandoPrecedenti}>
              <ChevronUp className="size-4" aria-hidden />
              Messaggi precedenti
            </Button>
            {errorePrecedenti && (
              <p className="text-xs text-red-700" role="alert">
                {errorePrecedenti}
              </p>
            )}
          </div>
        )}
        {storico.length > 0 && <div className="mb-3 space-y-3">{storico.map(bolla)}</div>}
        <div role="log" aria-live="polite" aria-relevant="additions" aria-label="Nuovi messaggi della conversazione" className="space-y-3">
          {recenti.map(bolla)}
        </div>
        {messaggi.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-500">{CHAT_COPY.vuota}</p>
        )}
      </div>
      <SegnalaMessaggio messaggioId={segnalato} onClose={() => setSegnalato(null)} />
    </section>
  );
}
