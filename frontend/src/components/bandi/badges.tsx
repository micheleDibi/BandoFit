import { CheckCircle2, HelpCircle } from "lucide-react";
import type { AiEsito } from "../../types";
import { Badge } from "../ui/Badge";
import { Status, type TonoStatus } from "../ui/Status";

/** Esito dell'AI-check. Il report è generato da un modello e può sbagliare:
 *  il linguaggio resta costruttivo — mai un «bocciato» secco. Per l'esito
 *  negativo NESSUN badge: il colore del punteggio e i verdetti dei singoli
 *  requisiti dicono già tutto, un'etichetta vaga non aggiunge significato. */
export function AiEsitoBadge({ esito }: { esito: AiEsito }) {
  if (esito === "ammissibile") {
    return (
      <Badge>
        <CheckCircle2 className="size-3" aria-hidden />
        In linea col bando
      </Badge>
    );
  }
  if (esito === "da_verificare") {
    return (
      <Badge>
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
 *  bandi §7, R0-a). */
export function statoInParole(stato: string): { tono: TonoStatus; parola: string } {
  switch (stato) {
    case "aperto":
      return { tono: "aperto", parola: "Aperto" };
    case "chiuso":
      return { tono: "chiuso", parola: "Chiuso" };
    case "in apertura prossimamente":
      return { tono: "in-apertura", parola: "In apertura" };
    case "sospeso":
      return { tono: "neutro", parola: "Sospeso" };
    case "revocato":
      return { tono: "neutro", parola: "Revocato" };
    default:
      return { tono: "neutro", parola: "Stato da verificare" };
  }
}

/** Lo stato del bando in parole (`Status`: punto + parola, mai un badge
 *  colorato). Stesse props di prima: lo usano anche le pagine non ancora rifatte. */
export function StatoBadge({ stato }: { stato: string | null }) {
  if (!stato) return null;
  const { tono, parola } = statoInParole(stato);
  return <Status tono={tono}>{parola}</Status>;
}
