import { useConsulenze } from "../../hooks/useConsulenze";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useNotifications } from "../../hooks/useNotifications";
import { useRiepilogoPartenariati } from "../../hooks/usePartenariati";
import { Button } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

interface Voce {
  numero: number;
  frase: string;
  dove: string;
  to: string;
}

function plurale(n: number, uno: string, tanti: string): string {
  return n === 1 ? uno : tanti;
}

/** «Da fare» della Home: le cose che aspettano una decisione, con il numero
 *  e dove si trovano. Partenariati solo a modulo acceso (l'hook non chiama
 *  l'endpoint a modulo spento). Ogni fonte ha il suo errore: le altre voci
 *  restano. */
export function DaFare() {
  const { partenariatiAttivo } = useFunzioni();
  const riepilogo = useRiepilogoPartenariati();
  const consulenze = useConsulenze();
  const notifiche = useNotifications();

  const voci: Voce[] = [];
  if (partenariatiAttivo && riepilogo.data) {
    const r = riepilogo.data;
    const candidature = r.candidature_da_decidere ?? 0;
    const inviti = r.inviti_ricevuti ?? 0;
    const messaggi = r.messaggi_non_letti ?? 0;
    if (candidature > 0) {
      voci.push({
        numero: candidature,
        frase: plurale(candidature, "candidatura da valutare", "candidature da valutare"),
        dove: "Partenariati",
        to: "/app/partenariati?tab=candidature",
      });
    }
    if (inviti > 0) {
      voci.push({
        numero: inviti,
        frase: plurale(inviti, "invito a cui rispondere", "inviti a cui rispondere"),
        dove: "Partenariati",
        to: "/app/partenariati?tab=candidature",
      });
    }
    if (messaggi > 0) {
      voci.push({
        numero: messaggi,
        frase: plurale(messaggi, "messaggio non letto", "messaggi non letti"),
        dove: "Partenariati",
        to: "/app/partenariati?tab=conversazioni",
      });
    }
  }
  const proposte = (consulenze.data ?? [])
    .filter((c) => c.stato === "nuova")
    .reduce((somma, c) => somma + c.proposte_aperte, 0);
  if (proposte > 0) {
    voci.push({
      numero: proposte,
      frase: plurale(proposte, "proposta da valutare", "proposte da valutare"),
      dove: "Consulenze",
      to: "/app/consulenze",
    });
  }
  const nonLette = notifiche.data?.non_lette ?? 0;
  if (nonLette > 0) {
    voci.push({
      numero: nonLette,
      frase: plurale(nonLette, "notifica non letta", "notifiche non lette"),
      dove: "Notifiche",
      to: "/app/notifiche",
    });
  }

  const inCaricamento =
    consulenze.isPending || notifiche.isPending || (partenariatiAttivo && riepilogo.isPending);
  const errore =
    consulenze.isError || notifiche.isError || (partenariatiAttivo && riepilogo.isError);

  return (
    <Panel titolo="Da fare">
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
              <span className="shrink-0 text-small text-ink-3">{voce.dove}</span>
            </li>
          ))}
        </ul>
      )}
      {errore && (
        <div className="flex flex-col gap-2">
          <InlineError>Alcune voci non sono aggiornate.</InlineError>
          <div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => {
                if (consulenze.isError) void consulenze.refetch();
                if (notifiche.isError) void notifiche.refetch();
                if (riepilogo.isError) void riepilogo.refetch();
              }}
            >
              Riprova
            </Button>
          </div>
        </div>
      )}
    </Panel>
  );
}
