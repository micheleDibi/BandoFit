import { SendHorizontal } from "lucide-react";
import { useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { nuovoClientMsgId, useInviaMessaggio } from "../../hooks/useConversazioni";
import { apiErrorMessage } from "../../lib/api";
import { CHAT_COPY } from "../../lib/copy";
import { Button } from "../ui/Button";

/** Lunghezza massima di un messaggio (come il server). */
export const MESSAGGIO_MAX = 5000;

const suMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad|iPod/.test(navigator.userAgent);

/** Scrittura di un messaggio (solo il titolare, conversazione aperta): testo
 *  fino a 5000 caratteri, invio con il bottone o con Ctrl/Cmd+Invio (Invio da
 *  solo va a capo). La chiave di idempotenza resta la stessa finché il testo
 *  non cambia: ritentare dopo un errore di rete non duplica il messaggio. Il
 *  testo resta nel campo se l'invio non riesce. */
export function ChatComposer({ conversazioneId }: { conversazioneId: string }) {
  const id = useId();
  const invia = useInviaMessaggio(conversazioneId);
  const [testo, setTesto] = useState("");
  const tentativo = useRef<{ testo: string; clientMsgId: string } | null>(null);
  const campo = useRef<HTMLTextAreaElement>(null);
  const mac = suMac();
  const pulito = testo.trim();

  const manda = () => {
    if (!pulito || invia.isPending) return;
    const corrente =
      tentativo.current?.testo === pulito
        ? tentativo.current
        : { testo: pulito, clientMsgId: nuovoClientMsgId() };
    tentativo.current = corrente;
    invia.mutate(
      { testo: pulito, client_msg_id: corrente.clientMsgId },
      {
        onSuccess: () => {
          tentativo.current = null;
          setTesto("");
          campo.current?.focus();
        },
      },
    );
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    manda();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      manda();
    }
  };

  return (
    <form onSubmit={onSubmit} className="space-y-2" aria-busy={invia.isPending}>
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        Scrivi un messaggio
      </label>
      <textarea
        ref={campo}
        id={id}
        rows={3}
        maxLength={MESSAGGIO_MAX}
        value={testo}
        onChange={(e) => {
          setTesto(e.target.value);
          if (invia.isError) invia.reset();
        }}
        onKeyDown={onKeyDown}
        aria-describedby={`${id}-aiuto ${id}-contatore`}
        aria-keyshortcuts="Control+Enter Meta+Enter"
        className="block min-h-20 w-full resize-y rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-relaxed text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
        placeholder="Scrivi all'altra azienda…"
      />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          <span id={`${id}-aiuto`}>{CHAT_COPY.scorciatoia(mac)}.</span>{" "}
          <span id={`${id}-contatore`} className="tabular">
            {testo.length.toLocaleString("it-IT")} caratteri su {MESSAGGIO_MAX.toLocaleString("it-IT")}
          </span>
        </p>
        <Button type="submit" size="sm" loading={invia.isPending} disabled={!pulito}>
          <SendHorizontal className="size-4" aria-hidden />
          Invia
        </Button>
      </div>
      {invia.isError && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {apiErrorMessage(invia.error, "Il messaggio non è partito: riprova.")}
        </p>
      )}
    </form>
  );
}
