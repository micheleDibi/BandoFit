import type { ReactNode } from "react";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export interface ConfirmDialogProps {
  open: boolean;
  /** La domanda («Rimuovere l'azienda dalle aziende gestite?»). */
  titolo: string;
  /** Le conseguenze, in una o due frasi. */
  children: ReactNode;
  /** Testo del pulsante di conferma: un verbo, lo stesso dell'azione («Rimuovi»). */
  conferma: string;
  annulla?: string;
  /** Azione che non si annulla: il pulsante di conferma diventa `danger`. */
  distruttiva?: boolean;
  /** Richiesta in volo: pulsanti bloccati e finestra non chiudibile finché non finisce. */
  inCorso?: boolean;
  onConferma: () => void;
  onAnnulla: () => void;
}

/** Finestra di conferma sopra `Dialog`: la domanda nel titolo, le conseguenze nel
 *  testo, il pulsante che agisce a destra. I pulsanti hanno `onClick` esplicito:
 *  nessun submit implicito del `<dialog>`. */
export function ConfirmDialog({
  open,
  titolo,
  children,
  conferma,
  annulla = "Annulla",
  distruttiva = false,
  inCorso = false,
  onConferma,
  onAnnulla,
}: ConfirmDialogProps) {
  // Il `close` nativo arriva anche quando è il genitore a mettere `open=false`
  // (dopo «Annulla» o dopo la conferma): in quel caso `open` è già falso e non
  // si richiama `onAnnulla`. Con `open` ancora vero la chiusura viene
  // dall'utente (Esc, velo, X) ed è un annullamento; con la richiesta in volo
  // non si annulla (la finestra non è chiudibile: `dismissible` è falso).
  const chiusuraDaUtente = () => {
    if (open && !inCorso) onAnnulla();
  };
  return (
    <Dialog
      open={open}
      onClose={chiusuraDaUtente}
      title={titolo}
      dismissible={!inCorso}
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onAnnulla} disabled={inCorso}>
            {annulla}
          </Button>
          <Button
            type="button"
            variant={distruttiva ? "danger" : "primary"}
            onClick={onConferma}
            loading={inCorso}
          >
            {conferma}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
