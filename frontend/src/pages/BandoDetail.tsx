import axios from "axios";
import { CalendarCheck, CalendarPlus, FileText } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { AiCheckCard } from "../components/bandi/AiCheckCard";
import { AiCheckReport } from "../components/bandi/AiCheckReport";
import { BandoTestata } from "../components/bandi/BandoTestata";
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
import { tempoRelativo } from "../components/ui/Due";
import type { Fatto } from "../components/ui/Facts";
import { Fit } from "../components/ui/Fit";
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
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-9 w-3/4" />
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-16 w-full" />
          </div>
        }
        laterale={<Skeleton className="h-64 w-full" />}
      >
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-11/12" />
          <Skeleton className="h-5 w-4/5" />
          <Skeleton className="mt-4 h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </div>
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
            action={tornaAiBandi}
          />
        ) : nonTrovato ? (
          <EmptyState
            title="Bando non trovato."
            description="L'indirizzo non corrisponde a nessun bando del catalogo."
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

  // Solo i fatti con un dato reale: niente «—».
  const fatti: Fatto[] = [];
  if (bando.data_scadenza) {
    fatti.push({
      etichetta: "Scadenza",
      valore: dataConOra(bando.data_scadenza, bando.ora_scadenza),
      nota: inCorso ? tempoRelativo(bando.data_scadenza) : undefined,
    });
  }
  if (bando.importo_totale_eur !== null) {
    fatti.push({ etichetta: "Dotazione", valore: formatEur(bando.importo_totale_eur) });
  }
  if (bando.importo_max_per_progetto_eur !== null) {
    fatti.push({
      etichetta: "Contributo massimo",
      valore: formatEur(bando.importo_max_per_progetto_eur),
    });
  }
  if (bando.data_apertura) {
    fatti.push({
      etichetta: "Apertura",
      valore: dataConOra(bando.data_apertura, bando.ora_apertura),
    });
  }

  // «Aggiungi la scadenza al calendario»: solo a bando in corso, con una data.
  const azioneCalendario =
    bando.data_scadenza && inCorso ? (
      <div className="flex flex-col items-start gap-1 sm:items-end">
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
        <Panel titolo="Documenti" className="lg:order-last">
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
          azioni={<SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} variant="inline" />}
          fatti={fatti}
          azioneFatti={azioneCalendario}
        />
      }
      laterale={laterale}
      sotto={
        <>
          <Section id="ai-check-report" aria-label="Report AI-check" className="scroll-mt-16 pt-6">
            <SectionHeader titolo="Report AI-check" />
            <AiCheckReport slug={bando.slug} />
          </Section>
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
              <Badge key={t}>{t}</Badge>
            ))}
          </div>
        )}
      </article>
    </Page>
  );
}
