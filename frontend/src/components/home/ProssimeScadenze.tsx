import { CalendarDays } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { daysUntil, todayItalyIso } from "../../lib/format";
import type { SavedBandoItem } from "../../types";
import { BarChart } from "../ui/BarChart";
import { LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Due, statoScadenza } from "../ui/Due";
import { IconChip } from "../ui/IconChip";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { PAGINA_SALVATI, useScadenzeSalvate } from "./datiHome";

/** Quante settimane nel grafico. */
const SETTIMANE = 6;

const giornoMese = new Intl.DateTimeFormat("it-IT", { day: "numeric", month: "short" });

/** Primo giorno (da oggi) della settimana `i` del grafico. */
const inizioSettimana = (i: number) => (i === 0 ? 0 : i * 7 + 1);

/** Le prossime sei settimane a partire da oggi (in Italia): per ognuna il primo
 *  giorno e quanti bandi salvati scadono in quella settimana. La prima va da
 *  oggi a 7 giorni compresi, come la fascia «urgente» di `Due` (la tessera
 *  corallo) e la card «In scadenza entro 7 giorni» della Home; le altre sono di
 *  sette giorni (8-14, 15-21…). Solo dai dati già caricati per l'elenco qui
 *  sotto. */
function scadenzePerSettimana(righe: SavedBandoItem[]) {
  const [anno, mese, giorno] = todayItalyIso().split("-").map(Number);
  const settimane = Array.from({ length: SETTIMANE }, (_, i) => ({
    etichetta: giornoMese.format(new Date(anno, mese - 1, giorno + inizioSettimana(i))),
    valore: 0,
  }));
  for (const item of righe) {
    const giorni = daysUntil(item.bando.data_scadenza);
    if (giorni === null || giorni < 0) continue;
    const indice =
      statoScadenza(item.bando.data_scadenza) === "urgente" ? 0 : Math.ceil(giorni / 7) - 1;
    if (indice < SETTIMANE) settimane[indice].valore += 1;
  }
  return settimane;
}

/** «Prossime scadenze» della Home: solo i bandi salvati, disponibili, in corso
 *  e con una data, dalla scadenza più vicina; sopra, quante scadono settimana
 *  per settimana. Il limite ai 20 salvati più di recente si dice sotto
 *  l'elenco. */
export function ProssimeScadenze() {
  const { data, isPending, isError, refetch, righe } = useScadenzeSalvate();
  const settimane = useMemo(() => scadenzePerSettimana(righe), [righe]);
  const descrizioneGrafico = `Bandi salvati in scadenza nelle prossime ${SETTIMANE} settimane: ${settimane
    .map((s) => `settimana dal ${s.etichetta}, ${s.valore}`)
    .join("; ")}.`;

  return (
    <Card>
      <Section aria-label="Prossime scadenze">
        <SectionHeader
          className="items-center"
          titolo={
            <span className="flex items-center gap-3">
              <IconChip icon={CalendarDays} area="scadenze" size="sm" />
              Prossime scadenze
            </span>
          }
          azione={
            <TextLink to="/app/salvati" className="text-small font-medium">
              Vedi i bandi salvati
            </TextLink>
          }
        />
        {isPending ? (
          <div className="flex flex-col gap-4" aria-hidden>
            <Skeleton className="h-36 w-full rounded-control" />
            <ul className="flex flex-col">
              {Array.from({ length: 3 }).map((_, i) => (
                <li
                  key={i}
                  className="flex items-start gap-4 border-b border-line py-4 last:border-b-0"
                >
                  <Skeleton className="h-16 w-18 shrink-0 rounded-control" />
                  <div className="flex flex-1 flex-col gap-2">
                    <Skeleton className="h-5 w-3/5" />
                    <Skeleton className="h-3 w-32" />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : isError ? (
          <ErrorState
            title="Non siamo riusciti a caricare le scadenze."
            onRetry={() => refetch()}
          />
        ) : righe.length === 0 ? (
          <EmptyState
            title="Non hai bandi salvati con una scadenza."
            description="Salva un bando aperto e lo ritrovi qui, in ordine di scadenza."
            icon={CalendarDays}
            area="scadenze"
            action={
              <LinkButton to="/app/bandi" variant="secondary">
                Cerca nei bandi
              </LinkButton>
            }
          />
        ) : (
          <>
            <figure className="flex flex-col gap-2 border-b border-line pb-4">
              <figcaption className="text-small text-ink-2">
                Scadenze settimana per settimana
              </figcaption>
              <BarChart
                dati={settimane}
                tono="scadenze"
                altezza={140}
                ariaLabel={descrizioneGrafico}
              />
            </figure>
            <ul className="flex flex-col">
              {righe.map((item) => (
                <li
                  key={item.bando.id}
                  className="flex items-start gap-4 border-b border-line py-4 last:border-b-0 md:gap-6"
                >
                  <Due data={item.bando.data_scadenza} />
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <Link
                      to={`/app/bandi/${item.bando.slug}`}
                      className="self-start rounded-mark text-row-title text-ink hover:text-accent-hover"
                    >
                      {item.bando.titolo ?? item.bando.titolo_breve ?? "Bando senza titolo"}
                    </Link>
                    {item.bando.ente_erogatore && (
                      <p className="text-small text-ink-2">{item.bando.ente_erogatore}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
        {data && data.total > PAGINA_SALVATI && (
          <p className="text-small text-ink-3">
            Contiamo i {PAGINA_SALVATI} bandi salvati più di recente.{" "}
            <TextLink to="/app/salvati">Vedi tutti i bandi salvati</TextLink>
          </p>
        )}
      </Section>
    </Card>
  );
}
