import { Check, ChevronDown, FileText, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import {
  bozzaInCorsoRecente,
  nomeFileBozza,
  pdfBozzaUrl,
  useAvviaBozza,
  useBozzePartenariato,
} from "../../hooks/useBozzePartenariato";
import { useEntitlements } from "../../hooks/useEntitlements";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { BOZZE_COPY } from "../../lib/copy";
import { formatDateTime } from "../../lib/format";
import type { BozzaDocumento, StatoBozzaDocumento, TipoBozzaDocumento } from "../../types";
import { ExportPdfButton } from "../shared/ExportPdfButton";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Checkbox } from "../ui/Checkbox";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Spinner } from "../ui/Spinner";
import { Status, type TonoStatus } from "../ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { BozzaAnteprima } from "./BozzaAnteprima";
import { SceltaRadio } from "./CampiCall";
import { statoLimite } from "./LimitiCall";

const TIPI: TipoBozzaDocumento[] = ["lettera_intenti", "nda", "term_sheet"];

/** `errore` è già il messaggio per l'utente (lo traduce il server). */
const testoErrore = (valore: string | null) => valore?.trim() || BOZZE_COPY.errore;

const recenti = (bozze: BozzaDocumento[]) =>
  [...bozze].sort((a, b) => new Date(b.avviata_at).getTime() - new Date(a.avviata_at).getTime());

const TONI: Record<StatoBozzaDocumento, TonoStatus> = {
  ready: "aperto",
  error: "errore",
  pending: "in-apertura",
};

/** Stato della bozza in parole con il punto di `Status` (mai solo colore). */
function StatoBozzaBadge({ stato }: { stato: StatoBozzaDocumento }) {
  return <Status tono={TONI[stato] ?? "neutro"}>{BOZZE_COPY.stati[stato] ?? stato}</Status>;
}

function BozzaRiga({
  callId,
  bozza,
  aperta,
  onApri,
}: {
  callId: string;
  bozza: BozzaDocumento;
  aperta: boolean;
  onApri: (aperta: boolean) => void;
}) {
  const idTesto = useId();
  const tipo = BOZZE_COPY.tipi[bozza.tipo];
  const quando = formatDateTime(bozza.avviata_at);
  const pronta = bozza.stato === "ready";
  return (
    <li className="flex flex-col gap-3 border-b border-line py-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="font-medium text-ink">{tipo}</p>
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
            <span>Richiesta il {quando}</span>
            {bozza.includi_nome_azienda && <span>{BOZZE_COPY.conNome}</span>}
          </p>
        </div>
        <StatoBozzaBadge stato={bozza.stato} />
        {pronta && (
          <div className="flex flex-wrap items-start gap-2">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={aperta}
              aria-controls={idTesto}
              onClick={() => onApri(!aperta)}
            >
              <ChevronDown className={`size-4 transition-transform ${aperta ? "rotate-180" : ""}`} aria-hidden />
              {aperta ? BOZZE_COPY.nascondi : BOZZE_COPY.mostra}
              <span className="sr-only">
                {" "}
                – {tipo} del {quando}
              </span>
            </Button>
            <ExportPdfButton
              url={pdfBozzaUrl(callId, bozza.id)}
              filename={nomeFileBozza(bozza)}
              label={BOZZE_COPY.scaricaPdf}
              busyLabel="Preparo il PDF…"
              srLabel={` – ${tipo} del ${quando}`}
              size="sm"
            />
          </div>
        )}
      </div>
      {bozza.stato === "error" && <p className="text-small text-danger">{testoErrore(bozza.errore)}</p>}
      {pronta && (
        <div id={idTesto} hidden={!aperta}>
          {aperta && <BozzaAnteprima bozza={bozza} />}
        </div>
      )}
    </li>
  );
}

/** Scheda «Bozze dei documenti» della call (WP10), per le aziende che
 *  partecipano (chi l'ha creata e i membri del consorzio): lettera d'intenti,
 *  NDA e term sheet scritti dall'AI con i ruoli e le quote del consorzio, le
 *  altre aziende sempre con segnaposto. La prepara solo il titolare, entro il
 *  limite mensile del piano; la preparazione lavora in background e il suo
 *  stato si annuncia in `aria-live`. Anteprima con il disclaimer fisso e
 *  «Scarica PDF». Cosa si può fare lo decide il server: qui si legge. */
export function BozzeTab({
  callId,
  editable: editableIniziale,
}: {
  callId: string;
  /** Titolare dell'azienda (dalla call o dall'azienda): vale finché non
   *  arriva la risposta del server, che poi decide. */
  editable: boolean;
}) {
  const idElenco = useId();
  const idMotivo = useId();
  const bozze = useBozzePartenariato(callId);
  const { data: entitlements } = useEntitlements();
  const avvia = useAvviaBozza(callId);
  const [tipo, setTipo] = useState<TipoBozzaDocumento | null>(null);
  const [includiNome, setIncludiNome] = useState(false);
  const [erroreAvvio, setErroreAvvio] = useState<string | null>(null);
  // Bozza aperta: `undefined` finché non si sceglie = la più recente pronta.
  const [scelta, setScelta] = useState<string | null | undefined>(undefined);
  // Esito di una bozza che si è vista in preparazione: si annuncia quando
  // cambia (al primo caricamento no, sarebbe una notizia vecchia), anche se
  // un'altra bozza è ancora in preparazione; più esiti insieme, un annuncio.
  const [annuncio, setAnnuncio] = useState<{ testo: string; errore: boolean } | null>(null);
  const precedenti = useRef<Map<string, StatoBozzaDocumento> | null>(null);

  const lista = bozze.data?.bozze;
  useEffect(() => {
    if (!lista) return;
    const prima = precedenti.current;
    precedenti.current = new Map(lista.map((b) => [b.id, b.stato] as const));
    if (!prima) return;
    const concluse = recenti(lista).filter(
      (b) => prima.get(b.id) === "pending" && b.stato !== "pending",
    );
    if (concluse.length === 0) return;
    const testi = concluse.map(
      (b) =>
        `${BOZZE_COPY.tipi[b.tipo]}: ${b.stato === "ready" ? BOZZE_COPY.pronta : testoErrore(b.errore)}`,
    );
    setAnnuncio({ testo: testi.join(" "), errore: concluse.some((b) => b.stato !== "ready") });
    const pronta = concluse.find((b) => b.stato === "ready");
    if (pronta) setScelta(pronta.id);
  }, [lista]);

  const editable = bozze.data?.editable ?? editableIniziale;
  const limite = entitlements?.partenariati?.bozze_mese ?? null;
  const statoPiano = statoLimite(limite);
  const elenco = recenti(lista ?? []);
  const inCorso = elenco.filter((b) => b.stato === "pending");
  const inCorsoRecenti = inCorso.filter((b) => bozzaInCorsoRecente(b));
  const tipoInCorso = tipo !== null && inCorsoRecenti.some((b) => b.tipo === tipo);
  const aperta =
    scelta === undefined ? (elenco.find((b) => b.stato === "ready")?.id ?? null) : scelta;

  const handleAvvia = async () => {
    setErroreAvvio(null);
    setAnnuncio(null);
    if (!tipo) {
      setErroreAvvio("Scegli il documento da preparare.");
      return;
    }
    try {
      await avvia.mutateAsync({ tipo, includi_nome_azienda: includiNome });
    } catch (err) {
      // Il 404 vuol dire che la tua azienda non partecipa più alla call.
      setErroreAvvio(
        apiErrorCode(err) === "not_found"
          ? "La tua azienda non partecipa più a questa call: non puoi prepararne le bozze."
          : apiErrorMessage(err),
      );
    }
  };

  // Stato per tutti, sempre montato: cambia solo il testo. L'esito di una
  // bozza resta visibile (e si annuncia) anche con altre in preparazione.
  let avanzamento: ReactNode = null;
  if (avvia.isPending) {
    avanzamento = (
      <span className="inline-flex items-center gap-2 text-ink-2">
        <Spinner size="sm" />
        Avvio la bozza…
      </span>
    );
  } else if (inCorsoRecenti.length > 0) {
    avanzamento = (
      <span className="inline-flex items-center gap-2 text-ink-2">
        <Spinner size="sm" />
        {BOZZE_COPY.inCorso}
      </span>
    );
  } else if (inCorso.length > 0) {
    avanzamento = <span className="text-warning-ink">{BOZZE_COPY.lunga}</span>;
  }
  let esito: ReactNode = null;
  if (annuncio) {
    esito = annuncio.errore ? (
      <span className="inline-flex items-start gap-2 text-danger">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        {annuncio.testo}
      </span>
    ) : (
      <span className="inline-flex items-center gap-2 text-fit-ink">
        <Check className="size-4" aria-hidden />
        {annuncio.testo}
      </span>
    );
  }

  const bloccoPiano = statoPiano === "non_incluso" || statoPiano === "esaurito";
  let azioni: ReactNode;
  if (!editable) {
    azioni = <p className="text-small text-ink-3">{BOZZE_COPY.soloTitolare}</p>;
  } else if (bloccoPiano) {
    const nonIncluso = statoPiano === "non_incluso";
    azioni = (
      <Alert
        tono="attenzione"
        titolo={nonIncluso ? BOZZE_COPY.nonInclusoTitolo : BOZZE_COPY.esauriteTitolo}
        azione={<TextLink to="/app/abbonamento">{BOZZE_COPY.vediPiani}</TextLink>}
      >
        {nonIncluso ? BOZZE_COPY.nonIncluso : BOZZE_COPY.esaurite}
      </Alert>
    );
  } else {
    azioni = (
      <div className="flex flex-col gap-4">
        <SceltaRadio
          legenda={BOZZE_COPY.sceltaTipo}
          nome={`${idElenco}-tipo`}
          opzioni={TIPI.map((t) => ({
            valore: t,
            etichetta: BOZZE_COPY.tipi[t],
            nota: BOZZE_COPY.tipiNota[t],
          }))}
          valore={tipo}
          onChange={(t) => {
            setTipo(t);
            setErroreAvvio(null);
          }}
          disabled={avvia.isPending}
        />
        <Checkbox
          label={BOZZE_COPY.includiNome}
          descrizione={BOZZE_COPY.includiNomeNota}
          checked={includiNome}
          onChange={(e) => setIncludiNome(e.target.checked)}
          disabled={avvia.isPending}
        />
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => void handleAvvia()}
            loading={avvia.isPending}
            disabled={tipoInCorso}
            aria-describedby={tipoInCorso ? idMotivo : undefined}
          >
            {BOZZE_COPY.avvia}
          </Button>
          {tipoInCorso ? (
            <p id={idMotivo} className="text-small text-ink-3">
              La bozza di questo documento è già in preparazione.
            </p>
          ) : (
            limite?.limite != null && (
              <p className="text-small text-ink-3">Userai una delle bozze del mese.</p>
            )
          )}
        </div>
        {erroreAvvio && <Alert tono="errore">{erroreAvvio}</Alert>}
      </div>
    );
  }

  let corpoElenco: ReactNode;
  if (bozze.isPending) {
    corpoElenco = (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  } else if (bozze.isError) {
    corpoElenco =
      apiErrorCode(bozze.error) === "not_found" ? (
        <EmptyState
          icon={FileText}
          area="partenariati"
          title="Bozze non disponibili"
          description="Le bozze dei documenti le preparano l'azienda che ha creato la call e le aziende del suo consorzio."
        />
      ) : (
        <ErrorState
          message={apiErrorMessage(bozze.error, "Impossibile caricare le bozze.")}
          onRetry={() => void bozze.refetch()}
        />
      );
  } else if (elenco.length === 0) {
    corpoElenco = (
      <EmptyState
        icon={FileText}
        area="partenariati"
        title={BOZZE_COPY.nessunaTitolo}
        description={BOZZE_COPY.nessuna}
      />
    );
  } else {
    corpoElenco = (
      <ul className="flex flex-col border-t border-line">
        {elenco.map((b) => (
          <BozzaRiga
            key={b.id}
            callId={callId}
            bozza={b}
            aperta={aperta === b.id}
            onApri={(apri) => setScelta(apri ? b.id : null)}
          />
        ))}
      </ul>
    );
  }

  return (
    <Card className="flex flex-col gap-8 sm:p-8">
      <Section>
        <SectionHeader titolo={BOZZE_COPY.titolo} />
        <p className="text-body text-ink-2">{BOZZE_COPY.intro}</p>
        {editable && limite && limite.limite !== 0 && (
          <p className="text-body text-ink tabular-nums">
            {BOZZE_COPY.usate(limite.usate, limite.limite)}
            <span className="block text-small text-ink-3">{BOZZE_COPY.usateNota}</span>
          </p>
        )}
        {azioni}
        <div role="status" aria-live="polite" className="flex flex-col gap-1 text-body">
          {esito && <p>{esito}</p>}
          {avanzamento && <p>{avanzamento}</p>}
        </div>
        {inCorso.length > 0 && inCorsoRecenti.length === 0 && (
          <div>
            <Button variant="secondary" size="sm" loading={bozze.isFetching} onClick={() => void bozze.refetch()}>
              Aggiorna lo stato
            </Button>
          </div>
        )}
      </Section>

      <Section aria-labelledby={`${idElenco}-titolo`}>
        <SectionHeader titolo={BOZZE_COPY.elencoTitolo} id={`${idElenco}-titolo`} />
        {corpoElenco}
      </Section>
    </Card>
  );
}
