import { FileDown } from "lucide-react";
import { useState } from "react";
import { downloadFile } from "../../lib/download";
import { Button } from "../ui/Button";

/** Bottone di download di un PDF autenticato (`downloadFile`), con stato di
 *  caricamento e messaggio d'errore inline: il messaggio del backend, se
 *  leggibile, altrimenti uno generico. `srLabel` si aggiunge al nome
 *  accessibile (solo per i lettori di schermo) quando più bottoni uguali
 *  stanno nella stessa lista. */
export function ExportPdfButton({
  url,
  filename,
  label,
  busyLabel = "Esportazione…",
  srLabel,
  size = "md",
}: {
  url: string;
  filename: string;
  label: string;
  busyLabel?: string;
  srLabel?: string;
  size?: "sm" | "md";
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        size={size}
        loading={busy}
        aria-busy={busy}
        onClick={async () => {
          setError(null);
          setBusy(true);
          try {
            await downloadFile(url, filename);
          } catch (e) {
            setError(e instanceof Error ? e.message : "Download non riuscito. Riprova.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {!busy && <FileDown className="size-4" aria-hidden />}
        {busy ? busyLabel : label}
        {srLabel && <span className="sr-only">{srLabel}</span>}
      </Button>
      {error && (
        <span className="text-small text-danger" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
