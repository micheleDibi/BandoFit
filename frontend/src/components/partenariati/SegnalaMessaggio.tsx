import { Flag } from "lucide-react";
import { useEffect, useState } from "react";
import { SegnalaDialog } from "./SegnalaDialog";

/** «Segnala» accanto a un messaggio dell'altra azienda. `descrizione` dà il
 *  nome accessibile al bottone («Segnala il messaggio delle 14:32»). */
export function BottoneSegnalaMessaggio({
  descrizione,
  onClick,
}: {
  descrizione: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Segnala ${descrizione}`}
      className="inline-flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 text-xs text-slate-400 transition-colors hover:text-red-700 focus-visible:outline-2 focus-visible:outline-brand-500"
    >
      <Flag className="size-3" aria-hidden />
      Segnala
    </button>
  );
}

/** Segnalazione di un messaggio (DSA): la stessa delle call, con
 *  `oggetto_tipo='messaggio'` e l'id del messaggio. Un solo dialog per la
 *  conversazione: aperto quando `messaggioId` non è null. */
export function SegnalaMessaggio({
  messaggioId,
  onClose,
}: {
  messaggioId: number | null;
  onClose: () => void;
}) {
  // L'ultimo messaggio scelto resta mentre il dialog si chiude.
  const [ultimo, setUltimo] = useState<number | null>(messaggioId);
  useEffect(() => {
    if (messaggioId !== null) setUltimo(messaggioId);
  }, [messaggioId]);
  const mostrato = messaggioId ?? ultimo;
  if (mostrato === null) return null;
  return (
    <SegnalaDialog
      open={messaggioId !== null}
      onClose={onClose}
      oggettoTipo="messaggio"
      oggettoId={String(mostrato)}
    />
  );
}
