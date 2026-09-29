import { AlertTriangle, CheckCircle2, ChevronDown, FileText, Loader2, Lock, RefreshCw, Sparkles } from "lucide-react";
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
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { BozzaAnteprima } from "./BozzaAnteprima";
import { SceltaRadio } from "./CampiCall";
import { statoLimite } from "./LimitiCall";

const TIPI: TipoBozzaDocumento[] = ["lettera_intenti", "nda", "term_sheet"];

/** `errore` è già il messaggio per l'utente (lo traduce il server). */
const testoErrore = (valore: string | null) => valore?.trim() || BOZZE_COPY.errore;

const recenti = (bozze: BozzaDocumento[]) =>
  [...bozze].sort((a, b) => new Date(b.avviata_at).getTime() - new Date(a.avviata_at).getTime());

/** Stato della bozza con icona E testo (mai solo colore). */
function StatoBozzaBadge({ stato }: { stato: StatoBozzaDocumento }) {
  if (stato === "ready") {
    return (
      <Badge tone="emerald">
        <CheckCircle2 className="size-3.5" aria-hidden />
        {BOZZE_COPY.stati.ready}
      </Badge>
    );
  }
  if (stato === "error") {
    return (
      <Badge tone="red">
        <AlertTriangle className="size-3.5" aria-hidden />
        {BOZZE_COPY.stati.error}
      </Badge>
    );
  }
  return (
    <Badge tone="amber">
      <Loader2 className="size-3.5" aria-hidden />
      {BOZZE_COPY.stati.pending}
    </Badge>
  );
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
    <li className="rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
        <FileText className="size-5 shrink-0 text-slate-400" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-800">{tipo}</p>
          <p className="text-xs text-slate-500">
            Richiesta il {quando}
            {bozza.includi_nome_azienda ? ` · ${BOZZE_COPY.conNome}` : ""}
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
              <ChevronDown
                className={`size-4 transition-transform ${aperta ? "rotate-180" : ""}`}
                aria-hidden
              />
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
      {bozza.stato === "error" && (
        <p className="border-t border-slate-100 px-4 py-2.5 text-sm text-red-700">
          {testoErrore(bozza.errore)}
        </p>
      )}
      {pronta && (
        <div id={idTesto} hidden={!aperta} className="border-t border-slate-100 px-4 py-4">
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
      <span className="inline-flex items-center gap-1.5 text-brand-800">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        Avvio la bozza…
      </span>
    );
  } else if (inCorsoRecenti.length > 0) {
    avanzamento = (
      <span className="inline-flex items-center gap-1.5 text-brand-800">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        {BOZZE_COPY.inCorso}
      </span>
    );
  } else if (inCorso.length > 0) {
    avanzamento = <span className="text-amber-800">{BOZZE_COPY.lunga}</span>;
  }
  let esito: ReactNode = null;
  if (annuncio) {
    esito = annuncio.errore ? (
      <span className="inline-flex items-start gap-1.5 text-red-700">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        {annuncio.testo}
      </span>
    ) : (
      <span className="inline-flex items-center gap-1.5 text-emerald-800">
        <CheckCircle2 className="size-4" aria-hidden />
        {annuncio.testo}
      </span>
    );
  }

  const bloccoPiano = statoPiano === "non_incluso" || statoPiano === "esaurito";
  let azioni: ReactNode;
  if (!editable) {
    azioni = <p className="text-sm text-slate-500">{BOZZE_COPY.soloTitolare}</p>;
  } else if (bloccoPiano) {
    const nonIncluso = statoPiano === "non_incluso";
    azioni = (
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-brand-900">
        <div className="flex min-w-0 items-start gap-2 text-sm">
          <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
          <div>
            <p className="font-medium">
              {nonIncluso ? BOZZE_COPY.nonInclusoTitolo : BOZZE_COPY.esauriteTitolo}
            </p>
            <p className="mt-0.5">{nonIncluso ? BOZZE_COPY.nonIncluso : BOZZE_COPY.esaurite}</p>
          </div>
        </div>
        <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
          {BOZZE_COPY.vediPiani}
        </LinkButton>
      </div>
    );
  } else {
    azioni = (
      <div className="space-y-4">
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
        <div>
          <label className="flex cursor-pointer items-start gap-2 text-sm font-medium text-slate-800">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={includiNome}
              onChange={(e) => setIncludiNome(e.target.checked)}
              disabled={avvia.isPending}
              aria-describedby={`${idElenco}-nome`}
            />
            {BOZZE_COPY.includiNome}
          </label>
          <p id={`${idElenco}-nome`} className="mt-1 pl-6 text-xs text-slate-500">
            {BOZZE_COPY.includiNomeNota}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            onClick={() => void handleAvvia()}
            loading={avvia.isPending}
            disabled={tipoInCorso}
            aria-describedby={tipoInCorso ? idMotivo : undefined}
          >
            <Sparkles className="size-4" aria-hidden />
            {BOZZE_COPY.avvia}
          </Button>
          {tipoInCorso ? (
            <p id={idMotivo} className="text-xs text-slate-500">
              La bozza di questo documento è già in preparazione.
            </p>
          ) : (
            limite?.limite != null && (
              <p className="text-xs text-slate-500">Userai una delle bozze del mese.</p>
            )
          )}
        </div>
        {erroreAvvio && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {erroreAvvio}
          </p>
        )}
      </div>
    );
  }

  let corpoElenco: ReactNode;
  if (bozze.isPending) {
    corpoElenco = (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  } else if (bozze.isError) {
    corpoElenco =
      apiErrorCode(bozze.error) === "not_found" ? (
        <EmptyState
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
    corpoElenco = <EmptyState title={BOZZE_COPY.nessunaTitolo} description={BOZZE_COPY.nessuna} />;
  } else {
    corpoElenco = (
      <ul className="space-y-3">
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
    <div className="space-y-4">
      <Card className="p-5">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <Sparkles className="size-4 text-brand-500" aria-hidden />
          {BOZZE_COPY.titolo}
        </h2>
        <p className="mt-2 text-sm text-slate-600">{BOZZE_COPY.intro}</p>
        {editable && limite && limite.limite !== 0 && (
          <p className="mt-3 text-sm text-slate-700 tabular">
            {BOZZE_COPY.usate(limite.usate, limite.limite)}
            <span className="block text-xs text-slate-500">{BOZZE_COPY.usateNota}</span>
          </p>
        )}
        <div className="mt-4">{azioni}</div>
        <div role="status" aria-live="polite" className="mt-3 space-y-1 text-sm empty:mt-0">
          {esito && <p>{esito}</p>}
          {avanzamento && <p>{avanzamento}</p>}
        </div>
        {inCorso.length > 0 && inCorsoRecenti.length === 0 && (
          <Button
            variant="secondary"
            size="sm"
            className="mt-2"
            loading={bozze.isFetching}
            onClick={() => void bozze.refetch()}
          >
            <RefreshCw className="size-4" aria-hidden />
            Aggiorna lo stato
          </Button>
        )}
      </Card>

      <section aria-labelledby={`${idElenco}-titolo`} className="space-y-3">
        <h2
          id={`${idElenco}-titolo`}
          className="font-display text-base font-semibold text-slate-900"
        >
          {BOZZE_COPY.elencoTitolo}
        </h2>
        {corpoElenco}
      </section>
    </div>
  );
}
