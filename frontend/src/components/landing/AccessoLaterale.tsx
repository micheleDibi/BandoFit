import { CalendarDays, Euro, Target, type LucideIcon } from "lucide-react";
import { ACCESSO_COPY } from "../../lib/copy/landing";
import { IconChip } from "../ui/IconChip";
import { RigheEsempio } from "./RigheEsempio";

/** L'icona di ogni domanda della promessa, nello stesso ordine: fa per me
 *  (compatibilità), quanto vale (importo), entro quando (scadenza). */
const ICONE_PROMESSA: LucideIcon[] = [Target, Euro, CalendarDays];

/** Pannello laterale delle pagine di accesso (tavola «Accesso»), sulla fascia
 *  navy di `AuthLayout`: la promessa in bianco, ogni domanda con il suo
 *  `IconChip` chiaro, e due righe d'esempio del registro dei bandi come card
 *  bianche. */
export function AccessoLaterale() {
  return (
    <div className="flex flex-col gap-10">
      <h2 className="flex flex-col gap-3 text-title-bando text-white">
        {ACCESSO_COPY.promessa.map((riga, i) => (
          <span key={riga} className="flex items-center gap-4">
            <IconChip icon={ICONE_PROMESSA[i] ?? Target} size="md" inverse />
            {riga}
          </span>
        ))}
      </h2>
      <RigheEsempio />
    </div>
  );
}
