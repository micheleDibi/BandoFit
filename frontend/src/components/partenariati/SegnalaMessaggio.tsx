import { Flag } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "../ui/Button";
import { SegnalaDialog } from "./SegnalaDialog";

/** «Segnala» accanto a un messaggio dell'altra azienda. `descrizione` dà il
 *  nome accessibile al pulsante («Segnala il messaggio delle 14:32»). */
export function BottoneSegnalaMessaggio({
  descrizione,
  onClick,
}: {
  descrizione: string;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={onClick}
      aria-label={`Segnala ${descrizione}`}
      className="h-7 px-2 text-small text-ink-3 hover:text-danger"
    >
      <Flag className="size-3.5" aria-hidden />
      Segnala
    </Button>
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
