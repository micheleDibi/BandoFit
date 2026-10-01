import { MessageSquare } from "lucide-react";
import { useMemo } from "react";
import { useAppuntamenti, useRichiestePool } from "../../hooks/useProgettistaRichieste";
import { formatSlotGiorno, formatSlotOra } from "../../lib/format";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { IconChip } from "../ui/IconChip";
import { InlineError } from "../ui/InlineError";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

/** Quanti appuntamenti in Home. */
const PROSSIMI = 3;

/** «Richieste e appuntamenti» per progettisti e admin: le richieste di
 *  consulenza aperte e assegnate, e i prossimi appuntamenti confermati.
 *  Montato solo per chi ha l'area progettista: gli hook partono solo allora. */
export function RichiesteAppuntamenti() {
  const richieste = useRichiestePool();
  const appuntamenti = useAppuntamenti();

  const prossimi = useMemo(() => {
    const adesso = Date.now();
    return [...(appuntamenti.data ?? [])]
      .filter((a) => Date.parse(a.inizio) >= adesso)
      .sort((a, b) => a.inizio.localeCompare(b.inizio))
      .slice(0, PROSSIMI);
  }, [appuntamenti.data]);

  const aperte = richieste.data?.aperte.length ?? 0;
  const assegnate = richieste.data?.assegnate.length ?? 0;

  return (
    <Card>
      <Section aria-label="Richieste e appuntamenti">
        <SectionHeader
          className="items-center"
          titolo={
            <span className="flex items-center gap-3">
              <IconChip icon={MessageSquare} area="consulenze" size="sm" />
              Richieste e appuntamenti
            </span>
          }
          azione={
            <TextLink to="/app/progettista/richieste" className="text-small font-medium">
              Vedi le richieste di consulenza
            </TextLink>
          }
        />
        {richieste.isPending ? (
          <Skeleton className="h-4 w-2/3" />
        ) : richieste.isError ? (
          <div className="flex flex-col gap-2">
            <InlineError>Non siamo riusciti a caricare le richieste di consulenza.</InlineError>
            <div>
              <Button type="button" variant="secondary" size="sm" onClick={() => richieste.refetch()}>
                Riprova
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-body text-ink-2">
            {aperte === 0
              ? "Nessuna richiesta aperta"
              : `${aperte} ${aperte === 1 ? "richiesta aperta" : "richieste aperte"}`}
            , {assegnate} {assegnate === 1 ? "assegnata" : "assegnate"} a te.
          </p>
        )}

        {appuntamenti.isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : appuntamenti.isError ? (
          <div className="flex flex-col gap-2">
            <InlineError>Non siamo riusciti a caricare gli appuntamenti.</InlineError>
            <div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => appuntamenti.refetch()}
              >
                Riprova
              </Button>
            </div>
          </div>
        ) : prossimi.length === 0 ? (
          <p className="text-body text-ink-2">
            Nessun appuntamento in programma.{" "}
            <TextLink to="/app/calendario">Vai al calendario</TextLink>
          </p>
        ) : (
          <>
            <ul className="flex flex-col">
              {prossimi.map((a) => (
                <li
                  key={a.id}
                  className="flex flex-col gap-0.5 border-b border-line py-3 last:border-b-0 sm:flex-row sm:items-baseline sm:gap-4"
                >
                  <span className="shrink-0 text-small font-medium text-ink tabular-nums">
                    {formatSlotGiorno(a.inizio)}, ore {formatSlotOra(a.inizio)}
                  </span>
                  <span className="min-w-0 text-body text-ink-2">
                    {a.ragione_sociale ?? a.email ?? "Azienda"}: {a.bando_titolo}
                  </span>
                </li>
              ))}
            </ul>
            <p className="text-small">
              <TextLink to="/app/calendario" className="font-medium">
                Vai al calendario
              </TextLink>
            </p>
          </>
        )}
      </Section>
    </Card>
  );
}
