import { CircleDashed, Target, TriangleAlert } from "lucide-react";
import { cn } from "../../lib/cn";
import type { MatchOut } from "../../types";
import { Fit } from "../ui/Fit";

/** Chi è il soggetto del confronto: «tu» (la tua azienda con una call) o
 *  «lei» (il proponente che guarda un'azienda suggerita). */
export type PersonaMatch = "tu" | "lei";

/** Il testo del confronto: quanti dei requisiti cercati sono coperti. Senza
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

/** Copertura dei requisiti cercati: le barre di `Fit` con «N su M» e la parola
 *  accanto («requisiti coperti»); la frase intera («Copri 2 requisiti su 3») è
 *  il nome per lo screen reader. Senza requisiti cercati, solo parole. Una
 *  copertura bassa non è un errore: barre vuote, mai rosso. */
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
  const testo = testoCopertura(match, persona);
  if (cercati > 0) {
    return (
      <span
        className={cn(
          "inline-flex flex-wrap items-center gap-x-2 gap-y-0.5 text-small text-ink-2",
          className,
        )}
      >
        <Fit soddisfatti={coperti} totale={cercati} label={testo} />
        <span aria-hidden>{cercati === 1 ? "requisito coperto" : "requisiti coperti"}</span>
      </span>
    );
  }
  const Icona = match.posizioni_compatibili.length > 0 ? Target : CircleDashed;
  return (
    <span
      className={cn("inline-flex items-center gap-1.5 text-small font-medium text-ink-2", className)}
    >
      <Icona className="size-4 shrink-0" aria-hidden />
      {testo}
    </span>
  );
}

/** Quante voci restano da verificare (dati mancanti): non escludono. */
export function AttenzioneBadge({ match, className }: { match: MatchOut; className?: string }) {
  const n = match.attenzione.length;
  if (n === 0) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-small font-medium text-warning-ink",
        className,
      )}
    >
      <TriangleAlert className="size-4 shrink-0" aria-hidden />
      {n === 1 ? "1 voce da verificare" : `${n} voci da verificare`}
    </span>
  );
}
