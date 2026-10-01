import { ACCESSO_COPY } from "../../lib/copy/landing";
import { RigheEsempio } from "./RigheEsempio";

/** Pannello laterale delle pagine di accesso (tavola «Accesso»): la promessa e
 *  due righe d'esempio del registro dei bandi. */
export function AccessoLaterale() {
  return (
    <div className="flex flex-col gap-12">
      <h2 className="text-title-bando text-ink">
        {ACCESSO_COPY.promessa.map((riga) => (
          <span key={riga} className="block">
            {riga}
          </span>
        ))}
      </h2>
      <RigheEsempio />
    </div>
  );
}
