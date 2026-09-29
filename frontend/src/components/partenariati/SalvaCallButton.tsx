import { Bookmark, BookmarkCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useSalvaCall } from "../../hooks/usePartenariati";
import { apiErrorMessage } from "../../lib/api";
import { BACHECA_COPY } from "../../lib/copy";
import { Button } from "../ui/Button";

/** «Salva» una call di un'altra azienda (vale anche come «segui»: avvisi se
 *  cambia o si chiude). Solo per il titolare: chi la mostra decide. Lo stato
 *  cambia subito al click (micro-interazione) e torna quello del server se la
 *  richiesta fallisce o quando arriva il dato aggiornato. */
export function SalvaCallButton({
  id,
  titolo,
  salvata,
}: {
  id: string;
  /** Per il nome accessibile («Salva la call «…»»). */
  titolo: string;
  salvata: boolean;
}) {
  const salva = useSalvaCall();
  const [locale, setLocale] = useState<boolean | null>(null);
  // Il dato del server ha l'ultima parola appena cambia.
  useEffect(() => setLocale(null), [salvata]);
  const attuale = locale ?? salvata;

  const onClick = () => {
    if (salva.isPending) return;
    const nuovo = !attuale;
    setLocale(nuovo);
    salva.mutate({ id, salva: nuovo }, { onError: () => setLocale(null) });
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        type="button"
        variant="secondary"
        size="sm"
        aria-pressed={attuale}
        title={BACHECA_COPY.salvaAiuto}
        onClick={onClick}
      >
        {attuale ? (
          <BookmarkCheck className="size-4 text-brand-600" aria-hidden />
        ) : (
          <Bookmark className="size-4" aria-hidden />
        )}
        {attuale ? BACHECA_COPY.salvata : BACHECA_COPY.salva}
        <span className="sr-only"> la call «{titolo}»</span>
      </Button>
      {salva.isError && (
        <p className="max-w-56 text-right text-xs text-red-700" role="alert">
          {apiErrorMessage(salva.error, "Non siamo riusciti a salvare la call.")}
        </p>
      )}
    </div>
  );
}
