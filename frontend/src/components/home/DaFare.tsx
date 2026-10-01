import { ListTodo } from "lucide-react";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { useVociDaFare } from "./datiHome";

/** «Da fare» della Home: le cose che aspettano una decisione, con il numero
 *  e dove si trovano (pillola nel colore dell'area). Partenariati solo a
 *  modulo acceso (l'hook non chiama l'endpoint a modulo spento). Ogni fonte ha
 *  il suo errore: le altre voci restano. */
export function DaFare() {
  const { voci, inCaricamento, errore, riprova } = useVociDaFare();

  return (
    <Panel titolo="Da fare" icon={ListTodo} area="home">
      {inCaricamento ? (
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-4 w-4/5" />
          <Skeleton className="h-4 w-3/5" />
        </div>
      ) : voci.length === 0 ? (
        // Con un errore il «niente» non è certo: resta solo l'avviso sotto.
        !errore && <p className="text-body text-ink-2">Niente da fare per ora.</p>
      ) : (
        <ul className="flex flex-col">
          {voci.map((voce) => (
            <li
              key={`${voce.to}-${voce.frase}`}
              className="flex items-center justify-between gap-3 border-b border-line py-2.5 last:border-b-0"
            >
              <TextLink to={voce.to}>
                <span className="font-semibold tabular-nums">{voce.numero}</span> {voce.frase}
              </TextLink>
              <Badge area={voce.area} className="shrink-0">
                {voce.dove}
              </Badge>
            </li>
          ))}
        </ul>
      )}
      {errore && (
        <div className="flex flex-col gap-2">
          <InlineError>Alcune voci non sono aggiornate.</InlineError>
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={riprova}>
              Riprova
            </Button>
          </div>
        </div>
      )}
    </Panel>
  );
}
