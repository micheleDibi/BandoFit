import { CalendarClock, FileText } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { ConsulenzaStatoBadge, PropostaStatoBadge } from "../Consulenze";
import { BadgeDaCall, titoloRichiesta } from "./Richieste";
import { CallProgettista } from "../../components/partenariati/CallProgettista";
import { AiReportBody } from "../../components/bandi/AiReportBody";
import { DossierView } from "../../components/company/dossier/DossierView";
import { orarioAppuntamento } from "../../components/consulenze/formato";
import { VideocallButton } from "../../components/consulenze/VideocallButton";
import { Button, LinkButton } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DefinitionList } from "../../components/ui/Facts";
import { TextareaField } from "../../components/ui/Field";
import { InlineError } from "../../components/ui/InlineError";
import { Page } from "../../components/ui/Page";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { Section, SectionHeader } from "../../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/states";
import { TextLink } from "../../components/ui/TextLink";
import { useToast } from "../../components/ui/Toast";
import { useFunzioni } from "../../hooks/useFunzioni";
import {
  useCallRichiesta,
  useDossierRichiesta,
  useInviaProposta,
  useRichiesta,
  useRitiraProposta,
} from "../../hooks/useProgettistaRichieste";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { formatDateTime } from "../../lib/format";
import type { Proposta } from "../../types";

const INDIETRO = { label: "Richieste di consulenza", to: "/app/progettista/richieste" };

/** Vista FULL post-assegnazione: dati aziendali + dossier certificato.
 *  Il caricamento parte su azione esplicita: ogni lettura è registrata
 *  lato server in audit_log. */
function DossierCompleto({ requestId }: { requestId: string }) {
  const [visible, setVisible] = useState(false);
  const { data, isPending, isError, error, refetch } = useDossierRichiesta(
    requestId,
    visible,
  );

  let contenuto;
  if (!visible) {
    contenuto = (
      <div className="flex flex-col items-start gap-3">
        <p className="max-w-lettura text-body text-ink-2">
          Come progettista assegnato hai accesso a tutti i dati aziendali e al dossier
          certificato del Registro Imprese. Ogni accesso viene registrato.
        </p>
        <Button type="button" variant="secondary" onClick={() => setVisible(true)}>
          Apri i dati completi
        </Button>
      </div>
    );
  } else if (isPending) {
    contenuto = (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  } else if (isError || !data) {
    contenuto = <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  } else {
    const company = data.company;
    const dati = company
      ? (
          [
            ["Ragione sociale", company.ragione_sociale],
            ["Partita IVA", company.partita_iva],
            ["Forma giuridica", company.forma_giuridica],
            ["ATECO", company.ateco_codice],
            ["Settore", company.settore_nome],
            ["Regione", company.regione_nome],
            ["Comune", company.comune],
            ["Dipendenti", company.numero_dipendenti],
            ["Classe dimensionale", company.classe_dimensionale],
            ["Fascia di fatturato", company.fascia_fatturato],
            ["PEC", company.pec],
            ["Telefono", company.telefono],
          ] as Array<[string, string | number | null]>
        ).filter(([, value]) => value !== null && value !== undefined && value !== "")
      : [];
    contenuto = (
      <>
        {company && (
          <div className="flex flex-col gap-3">
            <h3 className="font-sans text-title-group text-ink">Dati dichiarati dal titolare</h3>
            <DefinitionList
              items={dati.map(([etichetta, valore]) => ({ etichetta, valore }))}
            />
          </div>
        )}
        <div className="flex flex-col gap-3">
          <h3 className="font-sans text-title-group text-ink">
            Dossier certificato del Registro Imprese
          </h3>
          {data.dossier.imported && data.dossier.dossier ? (
            <DossierView dossier={data.dossier.dossier} people={data.dossier.people} />
          ) : (
            <p className="text-body text-ink-2">
              L'azienda non ha ancora importato il dossier certificato dal Registro Imprese.
            </p>
          )}
        </div>
      </>
    );
  }

  return (
    <Section aria-label="Dati completi dell'azienda">
      <SectionHeader titolo="Dati completi dell'azienda" />
      {contenuto}
    </Section>
  );
}

/** La call di partenariato del cliente (consulenza chiesta dalla call, WP9):
 *  solo per il progettista assegnato e su azione esplicita, perché ogni
 *  lettura è registrata PRIMA di rispondere (se la registrazione non riesce il
 *  server non manda nulla). */
function CallDelCliente({ requestId }: { requestId: string }) {
  const [visibile, setVisibile] = useState(false);
  const { data, isPending, isError, error, refetch } = useCallRichiesta(requestId, visibile);

  if (!visibile) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p className="max-w-lettura text-body text-ink-2">
          Come progettista assegnato vedi la call: testi, regole del bando, requisiti, posizioni e
          verifica del consorzio. Delle aziende partner vedi solo esiti e fasce, mai contatti,
          messaggi o numeri esatti. Ogni accesso viene registrato.
        </p>
        <Button type="button" variant="secondary" onClick={() => setVisibile(true)}>
          Apri la call
        </Button>
      </div>
    );
  }
  if (isPending) {
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    const codice = apiErrorCode(error);
    return (
      <ErrorState
        message={
          codice === "upstream_error"
            ? "Non siamo riusciti a registrare l'accesso, quindi la call non si apre. Riprova tra poco."
            : apiErrorMessage(error, "Impossibile aprire la call.")
        }
        onRetry={codice === "not_found" || codice === "forbidden" ? undefined : () => refetch()}
      />
    );
  }
  return <CallProgettista call={data} />;
}

export default function RichiestaDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: richiesta, isPending, isError, error, refetch } = useRichiesta(id);
  const invia = useInviaProposta(id ?? "");
  const ritira = useRitiraProposta();
  const { partenariatiAttivo } = useFunzioni();
  const toast = useToast();

  const [messaggio, setMessaggio] = useState("");
  const [ritirando, setRitirando] = useState<Proposta | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (isPending) {
    return (
      <Page
        variante="dettaglio"
        intestazione={
          <div className="flex flex-col gap-4" aria-hidden>
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-8 w-2/3" />
          </div>
        }
        laterale={<Skeleton className="h-32 w-full" />}
      >
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Page>
    );
  }
  if (isError || !richiesta) {
    // Non più visibile (affidata a un altro progettista o annullata): definitivo,
    // senza «Riprova».
    const tornaAlleRichieste = (
      <LinkButton to="/app/progettista/richieste" variant="secondary">
        Torna alle richieste
      </LinkButton>
    );
    return (
      <Page variante="sezioni">
        {apiErrorCode(error) === "not_found" ? (
          <EmptyState
            area="consulenze"
            title="Questa richiesta non è più disponibile."
            description="Il cliente l'ha affidata a un altro progettista oppure l'ha annullata."
            action={tornaAlleRichieste}
          />
        ) : (
          <>
            <ErrorState
              title="Non siamo riusciti a caricare la richiesta."
              message={apiErrorMessage(error)}
              onRetry={() => refetch()}
            />
            <div>{tornaAlleRichieste}</div>
          </>
        )}
      </Page>
    );
  }

  const propostaAperta = richiesta.mie_proposte.find((p) => p.stato === "inviata");
  const report = richiesta.ai_check?.report ?? null;
  // Consulenza dalla call non ancora affidata a chi guarda: niente dati
  // dell'azienda (li manda il server solo dopo l'assegnazione).
  const senzaDatiAzienda =
    !!richiesta.da_call && !richiesta.assegnata_a_me && !richiesta.ragione_sociale;

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invia.isPending || !messaggio.trim()) return;
    setActionError(null);
    try {
      await invia.mutateAsync(messaggio.trim());
      setMessaggio("");
      toast.mostra({ testo: "Proposta inviata" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleWithdraw = async () => {
    if (!ritirando || ritira.isPending) return;
    setActionError(null);
    try {
      await ritira.mutateAsync(ritirando.id);
      setRitirando(null);
      toast.mostra({ testo: "Proposta ritirata" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const laterale = (
    <>
      {/* L'appuntamento, con la videochiamata: su mobile sopra il resto. */}
      {richiesta.appuntamento && (
        <Panel
          titolo="Appuntamento"
          icon={CalendarClock}
          area="consulenze"
          className="order-first lg:order-none"
        >
          <p className="inline-flex items-start gap-2 text-body text-ink tabular-nums">
            <CalendarClock className="mt-0.5 size-4 shrink-0 text-area-consulenze-ink" aria-hidden />
            <time dateTime={richiesta.appuntamento.inizio}>
              {orarioAppuntamento(richiesta.appuntamento)}
            </time>
          </p>
          {richiesta.appuntamento.videocall_url && (
            <VideocallButton url={richiesta.appuntamento.videocall_url} />
          )}
        </Panel>
      )}
      <Panel titolo="Bando" icon={FileText} area="bandi">
        <p className="text-body text-ink">{richiesta.bando_titolo}</p>
        <p className="text-small">
          <TextLink to={`/app/bandi/${richiesta.bando_slug}`}>Vai al bando</TextLink>
        </p>
      </Panel>
    </>
  );

  return (
    <Page
      variante="dettaglio"
      intestazione={
        <PageHeader
          area="consulenze"
          indietro={INDIETRO}
          sopra={
            <>
              <ConsulenzaStatoBadge stato={richiesta.stato} />
              {richiesta.da_call && <BadgeDaCall />}
            </>
          }
          titolo={titoloRichiesta(richiesta)}
          descrizione={
            senzaDatiAzienda ? (
              "I dati dell'azienda li vedi se il titolare ti affida la consulenza."
            ) : (
              <span className="flex flex-wrap gap-x-4 gap-y-1">
                {richiesta.partita_iva && (
                  <span className="tabular-nums">P.IVA {richiesta.partita_iva}</span>
                )}
                <span>{richiesta.denominazione_utente}</span>
                {richiesta.email && <span>{richiesta.email}</span>}
              </span>
            )
          }
        />
      }
      laterale={laterale}
    >
      {/* AI-check ricevuto dal cliente (requisito punto 3) */}
      <Card>
        <Section aria-label="AI-check del cliente">
          <SectionHeader titolo="AI-check del cliente" />
          {report ? (
            <>
              {richiesta.ai_check?.ready_at && (
                <p className="text-small text-ink-3">
                  Generato il {formatDateTime(richiesta.ai_check.ready_at)}
                </p>
              )}
              <AiReportBody report={report} mostraAzioni={false} />
            </>
          ) : (
            <p className="max-w-lettura text-body text-ink-2">
              {senzaDatiAzienda
                ? "La consulenza è stata chiesta dalla call di partenariato: i dettagli li vedi se il titolare ti affida la consulenza."
                : richiesta.da_call
                  ? richiesta.esito
                    ? "La consulenza è stata chiesta dalla call di partenariato: esito e punteggio sono quelli dell'ultimo AI-check del cliente su questo bando."
                    : "La consulenza è stata chiesta dalla call di partenariato, senza un AI-check."
                  : "Il report AI-check non è più disponibile; esito e punteggio della richiesta restano quelli registrati alla creazione."}
            </p>
          )}
        </Section>
      </Card>

      {/* Consulenza dalla call (WP9): la call solo per l'assegnato, e solo a
          modulo partenariati acceso (da spento la sua rotta non esiste). */}
      {partenariatiAttivo && richiesta.da_call && (richiesta.assegnata_a_me || !senzaDatiAzienda) && (
        <Card>
          <Section aria-label="Call di partenariato del cliente">
            <SectionHeader titolo="La call di partenariato del cliente" />
            {richiesta.assegnata_a_me ? (
              <CallDelCliente requestId={richiesta.id} />
            ) : (
              <p className="max-w-lettura text-body text-ink-2">
                Il cliente ha chiesto una consulenza sulla sua call di partenariato: la vedrai se ti
                affida la consulenza.
              </p>
            )}
          </Section>
        </Card>
      )}

      {/* Proposta */}
      <Card>
        <Section aria-label="La tua proposta">
          <SectionHeader titolo="La tua proposta" />
          {richiesta.mie_proposte.length > 0 && (
            <ul className="flex flex-col">
              {richiesta.mie_proposte.map((proposta) => (
                <li key={proposta.id} className="flex flex-col gap-2 border-b border-line px-2 py-4">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-small text-ink-3">{formatDateTime(proposta.created_at)}</p>
                    <PropostaStatoBadge stato={proposta.stato} />
                  </div>
                  <p className="max-w-lettura whitespace-pre-line text-body text-ink">
                    {proposta.messaggio}
                  </p>
                  {proposta.stato === "inviata" && (
                    <div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="text-danger hover:bg-danger-soft"
                        onClick={() => {
                          setActionError(null);
                          setRitirando(proposta);
                        }}
                      >
                        Ritira la proposta
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          {richiesta.stato === "nuova" && !propostaAperta && (
            <form onSubmit={handleSend} className="flex max-w-lettura flex-col gap-3">
              <TextareaField
                label="Messaggio per il titolare"
                required
                rows={5}
                maxLength={4000}
                value={messaggio}
                onChange={(e) => setMessaggio(e.target.value)}
                helper="Presentati e spiega come puoi aiutare su questo bando: il titolare sceglie tra le proposte ricevute."
              />
              <div>
                <Button type="submit" loading={invia.isPending}>
                  Invia la proposta
                </Button>
              </div>
              {actionError && !ritirando && <InlineError>{actionError}</InlineError>}
            </form>
          )}
        </Section>
      </Card>

      {/* Vista full: solo per l'assegnato */}
      {richiesta.assegnata_a_me && (
        <Card>
          <DossierCompleto requestId={richiesta.id} />
        </Card>
      )}

      <ConfirmDialog
        open={!!ritirando}
        titolo="Ritirare la proposta?"
        conferma="Ritira la proposta"
        distruttiva
        inCorso={ritira.isPending}
        onConferma={handleWithdraw}
        onAnnulla={() => setRitirando(null)}
      >
        <p>
          Il titolare non potrà più accettarla. Finché la richiesta è aperta potrai inviarne una
          nuova.
        </p>
        {actionError && <InlineError className="mt-3">{actionError}</InlineError>}
      </ConfirmDialog>
    </Page>
  );
}
