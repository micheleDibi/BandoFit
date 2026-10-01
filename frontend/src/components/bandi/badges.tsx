import { CheckCircle2, HelpCircle } from "lucide-react";
import type { AiEsito, MotivoDaVerificare } from "../../types";
import { Badge } from "../ui/Badge";
import { Status, type TonoStatus } from "../ui/Status";

/** Esito dell'AI-check. Il report è generato da un modello e può sbagliare:
 *  il linguaggio resta costruttivo — mai un «bocciato» secco. Pillola verde
 *  per «In linea col bando», ambra per «Dati da completare», sempre con la
 *  parola. Per l'esito negativo NESSUN badge (e mai rosso): il punteggio e i
 *  verdetti dei singoli requisiti dicono già tutto, un'etichetta vaga non
 *  aggiunge significato. */
export function AiEsitoBadge({ esito }: { esito: AiEsito }) {
  if (esito === "ammissibile") {
    return (
      <Badge tone="success">
        <CheckCircle2 className="size-3" aria-hidden />
        In linea col bando
      </Badge>
    );
  }
  if (esito === "da_verificare") {
    return (
      <Badge tone="warning">
        <HelpCircle className="size-3" aria-hidden />
        Dati da completare
      </Badge>
    );
  }
  return null;
}

/** Parola e tono dello stato di un bando (stringa libera del catalogo: un
 *  valore nuovo ha la parola neutra «Stato da verificare»). Un bando sospeso
 *  o revocato non va mai presentato come aperto o in apertura (contratto DB
 *  bandi §7, R0-a): sospeso in ambra (può riaprire o chiudersi), revocato come
 *  chiuso. Con un motivo da verificare un bando aperto o in apertura resta tale,
 *  con «· da verificare» nella stessa parola; negli altri stati il motivo si
 *  ignora. */
export function statoInParole(
  stato: string,
  daVerificare?: MotivoDaVerificare | null,
): { tono: TonoStatus; parola: string } {
  switch (stato) {
    case "aperto":
      return { tono: "aperto", parola: daVerificare ? "Aperto · da verificare" : "Aperto" };
    case "chiuso":
      return { tono: "chiuso", parola: "Chiuso" };
    case "in apertura prossimamente":
      return {
        tono: "in-apertura",
        parola: daVerificare ? "In apertura · da verificare" : "In apertura",
      };
    case "sospeso":
      return { tono: "attenzione", parola: "Sospeso" };
    case "revocato":
      return { tono: "chiuso", parola: "Revocato" };
    default:
      return { tono: "neutro", parola: "Stato da verificare" };
  }
}

/** Lo stato del bando in parole (`Status`: pallino + parola, un solo stato per
 *  riga). `daVerificare`: il motivo del catalogo, se c'è (`stato_da_verificare`). */
export function StatoBadge({
  stato,
  daVerificare,
}: {
  stato: string | null;
  daVerificare?: MotivoDaVerificare | null;
}) {
  if (!stato) return null;
  const { tono, parola } = statoInParole(stato, daVerificare);
  return <Status tono={tono}>{parola}</Status>;
}
