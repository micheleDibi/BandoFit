import axios from "axios";
import {
  CalendarCheck,
  CalendarClock,
  CalendarPlus,
  Coins,
  FileText,
  FolderOpen,
  HandCoins,
  ListChecks,
  Sparkles,
} from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { AiCheckCard } from "../components/bandi/AiCheckCard";
import { AiCheckReport } from "../components/bandi/AiCheckReport";
import { BandoTestata, type FattoTestata } from "../components/bandi/BandoTestata";
import { CompatibilitaCard } from "../components/bandi/CompatibilitaCard";
import { ConsultoCard } from "../components/bandi/ConsultoCard";
import { ContenutoRenderer } from "../components/bandi/ContenutoRenderer";
import { SaveBandoButton } from "../components/bandi/SaveBandoButton";
import { bandoInCorso, dataConOra, statoDelBando } from "../components/bandi/stato";
import { vaiASezionePartenariato } from "../components/partenariati/ancora";
import { PartenariatoCard } from "../components/partenariati/PartenariatoCard";
import { PartenariatoSection } from "../components/partenariati/PartenariatoSection";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { statoScadenza, tempoRelativo, type StatoScadenza } from "../components/ui/Due";
import { Fit } from "../components/ui/Fit";
import { IconChip } from "../components/ui/IconChip";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { Panel } from "../components/ui/Panel";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useBando } from "../hooks/useBandi";
import { useAddBandoDeadline } from "../hooks/useCalendar";
import { useFunzioni } from "../hooks/useFunzioni";
import { useSlugCanonico } from "../hooks/useSlugCanonico";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { formatEur } from "../lib/format";

const INDIETRO = { label: "Bandi", to: "/app/bandi" };

/** Colore del tempo relativo della scadenza, come nella tessera di `Due`. */
const coloreRelativo: Record<StatoScadenza, string> = {
  passata: "text-ink-3",
  urgente: "font-semibold text-warm-ink",
  vicina: "font-semibold text-warning-ink",
  lontana: "text-ink-3",
};

export default function BandoDetail() {
  const { slug } = useParams<{ slug: string }>();
  const { data: bando, isPending, isError, error, refetch } = useBando(slug);
  const { partenariatiAttivo } = useFunzioni();
  const addDeadline = useAddBandoDeadline();
  // Sezione «Regole di partenariato» aperta per QUESTO bando: la pagina resta
  // montata passando da un bando all'altro, e la sezione riparte chiusa.
  const [partenariatoApertoPer, setPartenariatoApertoPer] = useState<string | null>(null);

  useSlugCanonico(slug, bando);

  if (isPending) {
    return (
      <Page
        variante="dettaglio"
        intestazione={
          <div className="flex flex-col gap-4" aria-hidden>
            <Skeleton className="h-44 w-full rounded-panel" />
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full rounded-panel" />
              ))}
            </div>
          </div>
        }
        laterale={<Skeleton className="h-64 w-full rounded-panel" />}
      >
        <Card className="flex flex-col gap-3 p-6 sm:p-8" aria-hidden>
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-11/12" />
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="mt-4 h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </Card>
      </Page>
    );
  }

  if (isError || !bando) {
    // Ritirato (410) e non trovato (404) sono definitivi: stato neutro, senza
    // «Riprova». Lo status copre anche un 404 senza il corpo d'errore del backend.
    const codice = apiErrorCode(error);
    const nonTrovato =
      codice === "not_found" || (axios.isAxiosError(error) && error.response?.status === 404);
    const tornaAiBandi = (
      <LinkButton to="/app/bandi" variant="secondary">
        Torna ai bandi
      </LinkButton>
    );
    return (
      <Page variante="sezioni">
        {codice === "bando_ritirato" ? (
          <EmptyState
            title="Questo bando non è più disponibile."
            description="L'ente che lo aveva pubblicato lo ha ritirato: puoi cercarne altri nell'elenco."
            area="bandi"
            action={tornaAiBandi}
          />
        ) : nonTrovato ? (
          <EmptyState
            title="Bando non trovato."
            description="L'indirizzo non corrisponde a nessun bando del catalogo."
            area="bandi"
            action={tornaAiBandi}
          />
        ) : (
          <>
            <ErrorState
              title="Non siamo riusciti a caricare il bando."
              message={apiErrorMessage(error, "Riprova tra qualche istante.")}
              onRetry={() => refetch()}
            />
            <div>{tornaAiBandi}</div>
          </>
        )}
      </Page>
    );
  }

  const titolo = bando.titolo ?? bando.titolo_breve ?? "Bando";
  const stato = statoDelBando(bando);
  const inCorso = bandoInCorso(stato);
  // Pulsanti e allegati arrivano già scelti e filtrati dal backend: qui si
  // decide solo cosa mostrare. Il pulsante principale solo a bando in corso;
  // la fonte se diversa da lui (su un bando chiuso resta anche se coincideva).
  const cta = inCorso ? bando.cta : null;
  const fonte =
    bando.link_fonte && bando.link_fonte.url !== cta?.url ? bando.link_fonte : null;

  // Solo i fatti con un dato reale: niente «—». Ognuno in una piccola card con
  // l'icona colorata: la scadenza nel corallo delle scadenze, il resto nel blu
  // dell'area bandi.
  const fatti: FattoTestata[] = [];
  if (bando.data_scadenza) {
    const urgenza = statoScadenza(bando.data_scadenza);
    fatti.push({
      etichetta: "Scadenza",
      valore: dataConOra(bando.data_scadenza, bando.ora_scadenza),
      nota: inCorso ? (
        <span className={urgenza ? coloreRelativo[urgenza] : undefined}>
          {tempoRelativo(bando.data_scadenza)}
        </span>
      ) : undefined,
      icon: CalendarClock,
      area: "scadenze",
    });
  }
  if (bando.importo_totale_eur !== null) {
    fatti.push({
      etichetta: "Dotazione",
      valore: formatEur(bando.importo_totale_eur),
      icon: Coins,
      area: "bandi",
    });
  }
  if (bando.importo_max_per_progetto_eur !== null) {
    fatti.push({
      etichetta: "Contributo massimo",
      valore: formatEur(bando.importo_max_per_progetto_eur),
      icon: HandCoins,
      area: "bandi",
    });
  }
  if (bando.data_apertura) {
    fatti.push({
      etichetta: "Apertura",
      valore: dataConOra(bando.data_apertura, bando.ora_apertura),
      icon: CalendarPlus,
      area: "bandi",
    });
  }

  // «Aggiungi la scadenza al calendario»: solo a bando in corso, con una data.
  const azioneCalendario =
    bando.data_scadenza && inCorso ? (
      <div className="flex flex-col items-end gap-1">
        {addDeadline.isSuccess ? (
          <LinkButton
            to={`/app/calendario?m=${bando.data_scadenza.slice(0, 7)}`}
            variant="ghost"
            size="sm"
          >
            <CalendarCheck className="size-4" aria-hidden />
            Nel calendario
          </LinkButton>
        ) : (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            loading={addDeadline.isPending}
            onClick={() => addDeadline.mutate(bando.slug)}
          >
            <CalendarPlus className="size-4" aria-hidden />
            Aggiungi la scadenza al calendario
          </Button>
        )}
        {addDeadline.isError && <InlineError>{apiErrorMessage(addDeadline.error)}</InlineError>}
      </div>
    ) : undefined;

  const laterale = (
    <>
      {/* Su mobile il pannello sta sopra il testo (tavola MobileBando). */}
      <Panel
        titolo="Fa per te?"
        icon={ListChecks}
        area="bandi"
        azione={
          bando.compatibilita && (
            <Fit soddisfatti={bando.compatibilita.matched} totale={bando.compatibilita.totale} />
          )
        }
        className="order-first lg:order-none"
      >
        <CompatibilitaCard bando={bando} />
        <div className="border-t border-line pt-3">
          <AiCheckCard slug={bando.slug} />
        </div>
        <ConsultoCard slug={bando.slug} />
      </Panel>

      {(fonte || bando.allegati.length > 0) && (
        <Panel titolo="Documenti" icon={FolderOpen} area="bandi" className="lg:order-last">
          <ul className="flex flex-col">
            {fonte && (
              <li className="border-b border-line py-2.5 last:border-b-0">
                <TextLink href={fonte.url} esterno>
                  {fonte.origine === "fonte_ufficiale" && fonte.host
                    ? `Fonte ufficiale, ${fonte.host}`
                    : "Fonte ufficiale"}
                </TextLink>
              </li>
            )}
            {bando.allegati.map((allegato) => (
              <li
                key={allegato.url}
                className="flex items-start gap-2 border-b border-line py-2.5 last:border-b-0"
              >
                <FileText className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
                <TextLink href={allegato.url} esterno>
                  {allegato.etichetta}
                  {allegato.formato ? ` (${allegato.formato.toUpperCase()})` : ""}
                </TextLink>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* Modulo partenariati: il pannello «Partenariato» lo rende la card, che
          sparisce del tutto a modulo spento o con il 404 del server. */}
      {partenariatiAttivo && (
        <PartenariatoCard
          key={bando.slug}
          slug={bando.slug}
          onVediRegole={() => {
            setPartenariatoApertoPer(bando.slug);
            vaiASezionePartenariato();
          }}
        />
      )}
    </>
  );

  return (
    <Page
      variante="dettaglio"
      intestazione={
        <BandoTestata
          indietro={INDIETRO}
          titolo={titolo}
          stato={stato}
          tipologia={bando.tipologia?.nome}
          modalita={bando.modalita_erogazione?.nome}
          programma={bando.programma?.nome}
          ente={bando.ente_erogatore}
          cta={cta}
          notaSenzaCta={
            inCorso ? undefined : "Il bando non è aperto: la candidatura non è disponibile."
          }
          azioni={<SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} variant="fascia" />}
          fatti={fatti}
          azioneFatti={azioneCalendario}
        />
      }
      laterale={laterale}
      sotto={
        <>
          <Card className="p-6 sm:p-8">
            <Section id="ai-check-report" aria-label="Report AI-check" className="scroll-mt-16">
              <SectionHeader
                className="items-center"
                titolo={
                  <span className="flex items-center gap-3">
                    <IconChip icon={Sparkles} area="aicheck" size="sm" />
                    Report AI-check
                  </span>
                }
              />
              <AiCheckReport slug={bando.slug} />
            </Section>
          </Card>
          {/* key=slug: guardia dell'avvio automatico ed errori ripartono per bando. */}
          <PartenariatoSection
            key={bando.slug}
            slug={bando.slug}
            open={partenariatoApertoPer === bando.slug}
            onOpenChange={(aperta) => setPartenariatoApertoPer(aperta ? bando.slug : null)}
          />
        </>
      }
    >
      <Card className="p-6 sm:p-8">
        <article className="flex max-w-lettura flex-col gap-4">
          {bando.descrizione_breve && (
            <p className="text-prose font-semibold text-ink">{bando.descrizione_breve}</p>
          )}
          {bando.contenuto?.sections?.length ? (
            <ContenutoRenderer sections={bando.contenuto.sections} />
          ) : (
            <p className="text-body text-ink-2">
              {cta || fonte
                ? "La scheda dettagliata non è ancora disponibile: consulta il bando ufficiale."
                : "La scheda dettagliata non è ancora disponibile."}
            </p>
          )}
          {bando.tematica.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 pt-2">
              <span className="text-small text-ink-3">Temi</span>
              {bando.tematica.map((t) => (
                <Badge key={t} area="bandi">
                  {t}
                </Badge>
              ))}
            </div>
          )}
        </article>
      </Card>
    </Page>
  );
}
