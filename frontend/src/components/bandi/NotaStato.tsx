import type { MotivoDaVerificare } from "../../types";
import { Alert } from "../ui/Alert";
import { bandoInCorso } from "./stato";

/** Perché lo stato va controllato, in una riga per motivo. */
const TESTO_MOTIVO: Record<MotivoDaVerificare, string> = {
  data_apertura_passata: "La data di apertura è passata, ma l'apertura non è ancora confermata.",
  smentito_dalla_fonte: "La pagina ufficiale indica uno stato diverso.",
  previsione_scaduta: "Il periodo di apertura previsto è passato senza conferme.",
  senza_conferma: "Non c'è una conferma recente dalla pagina ufficiale.",
  termine_passato: "Un termine indicato da una fonte non ufficiale è già passato.",
};

/** Nota sotto la testata della scheda per gli stati che chiedono una parola in
 *  più: sospeso, revocato, oppure un bando in corso con un motivo da
 *  verificare. Negli altri stati nulla (il chiuso tiene la nota generica della
 *  testata). Contenuto statico: `status`, mai un annuncio immediato. */
export function NotaStatoBando({
  stato,
  daVerificare,
}: {
  stato: string | null;
  daVerificare?: MotivoDaVerificare | null;
}) {
  if (stato === "sospeso") {
    return (
      <Alert tono="attenzione" ruolo="status">
        Il bando è sospeso: per ora non si possono presentare domande. Può riaprire o chiudersi:
        controlla la fonte ufficiale.
      </Alert>
    );
  }
  if (stato === "revocato") {
    return (
      <Alert tono="info" ruolo="status">
        Il bando è stato revocato dall'ente: non è più possibile partecipare.
      </Alert>
    );
  }
  if (stato !== null && bandoInCorso(stato) && daVerificare) {
    // Un motivo nuovo, non ancora tradotto, lascia solo l'invito a controllare.
    const motivo = TESTO_MOTIVO[daVerificare] as string | undefined;
    return (
      <Alert tono="attenzione" ruolo="status">
        {motivo && <p>{motivo}</p>}
        <p>Controlla sul sito dell'ente prima di candidarti.</p>
      </Alert>
    );
  }
  return null;
}
