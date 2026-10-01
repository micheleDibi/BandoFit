import { Bookmark } from "lucide-react";
import { useEffect, useState } from "react";
import { useSalvaCall } from "../../hooks/usePartenariati";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { BACHECA_COPY } from "../../lib/copy";
import { IconButton } from "../ui/IconButton";
import { useToast } from "../ui/Toast";
import { Tooltip } from "../ui/Tooltip";

/** «Salva» una call di un'altra azienda (vale anche come «segui»: avvisi se
 *  cambia o si chiude). Solo per il titolare: chi la mostra decide. Segnalibro
 *  di sola icona con `aria-pressed`; lo stato cambia subito al click e torna
 *  quello del server se la richiesta fallisce (con una notifica) o quando
 *  arriva il dato aggiornato. */
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
  const { mostra } = useToast();
  const [locale, setLocale] = useState<boolean | null>(null);
  // Il dato del server ha l'ultima parola appena cambia.
  useEffect(() => setLocale(null), [salvata]);
  const attuale = locale ?? salvata;

  const onClick = () => {
    if (salva.isPending) return;
    const nuovo = !attuale;
    setLocale(nuovo);
    salva.mutate(
      { id, salva: nuovo },
      {
        onError: (errore) => {
          setLocale(null);
          mostra({
            testo: apiErrorMessage(errore, "Non siamo riusciti a salvare la call."),
            tono: "errore",
          });
        },
      },
    );
  };

  return (
    <Tooltip testo={attuale ? BACHECA_COPY.salvata : BACHECA_COPY.salvaAiuto}>
      <IconButton
        label={`${BACHECA_COPY.salva} la call «${titolo}»`}
        icon={<Bookmark className={cn(attuale && "fill-current text-accent")} />}
        aria-pressed={attuale}
        onClick={onClick}
      />
    </Tooltip>
  );
}
