import { AlertTriangle, CheckCircle2, CircleDashed, CircleDot, Target } from "lucide-react";
import type { MatchOut } from "../../types";
import { Badge } from "../ui/Badge";

/** Chi è il soggetto del confronto: «tu» (la tua azienda con una call) o
 *  «lei» (il proponente che guarda un'azienda suggerita). */
export type PersonaMatch = "tu" | "lei";

/** Il testo del badge: quanti dei requisiti cercati sono coperti. Senza
 *  requisiti cercati, le posizioni a cui l'azienda corrisponde. */
export function testoCopertura(match: MatchOut, persona: PersonaMatch): string {
  const { coperti, cercati } = match.copertura;
  if (cercati > 0) {
    const verbo = persona === "tu" ? "Copri" : "Copre";
    return `${verbo} ${coperti} ${coperti === 1 ? "requisito" : "requisiti"} su ${cercati}`;
  }
  const posizioni = match.posizioni_compatibili.length;
  if (posizioni > 0) {
    const verbo = persona === "tu" ? "Corrispondi" : "Corrisponde";
    return posizioni === 1 ? `${verbo} a una posizione` : `${verbo} a ${posizioni} posizioni`;
  }
  return "Nessun requisito cercato";
}

/** Copertura dei requisiti cercati: icona **e** testo (mai il solo colore),
 *  es. «Copri 2 requisiti su 2». */
export function MatchBadge({
  match,
  persona = "tu",
  className,
}: {
  match: MatchOut;
  persona?: PersonaMatch;
  className?: string;
}) {
  const { coperti, cercati } = match.copertura;
  const tutti = cercati > 0 && coperti === cercati;
  const alcuni = cercati > 0 ? coperti > 0 : match.posizioni_compatibili.length > 0;
  const Icona = tutti ? CheckCircle2 : cercati === 0 && alcuni ? Target : alcuni ? CircleDot : CircleDashed;
  return (
    <Badge tone={tutti ? "emerald" : alcuni ? "brand" : "slate"} className={className}>
      <Icona className="size-3.5" aria-hidden />
      {testoCopertura(match, persona)}
    </Badge>
  );
}

/** Quante voci restano da verificare (dati mancanti): non escludono. */
export function AttenzioneBadge({ match, className }: { match: MatchOut; className?: string }) {
  const n = match.attenzione.length;
  if (n === 0) return null;
  return (
    <Badge tone="amber" className={className}>
      <AlertTriangle className="size-3.5" aria-hidden />
      {n === 1 ? "1 voce da verificare" : `${n} voci da verificare`}
    </Badge>
  );
}
