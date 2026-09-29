import {
  AlertTriangle,
  CheckCircle2,
  HelpCircle,
  Loader2,
  MinusCircle,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useAiChecksForBando, useRequestAiCheck } from "../../hooks/useAiCheck";
import {
  useAggiornaCall,
  useGeneraRequisiti,
  useSalvaRequisiti,
} from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { CALL_COPY } from "../../lib/copy";
import { formatDate, formatEur } from "../../lib/format";
import type {
  AmbitoRequisitoCall,
  BudgetFasciaCall,
  CallAggiornaInput,
  CriterioPartner,
  EsitoCoperturaCall,
  GapCall,
  RequisitoCall,
} from "../../types";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { TextField } from "../ui/Field";
import { Skeleton } from "../ui/states";
import { BarraPasso } from "./CallStepper";
import {
  budgetNellaFascia,
  descriviCriterio,
  FASCE_BUDGET,
  fasciaDi,
  firma,
  leggiImporto,
  leggiPercentuale,
  LIMITI_CALL,
  mostraDecimale,
  requisitoInput,
} from "./callDati";
import { SceltaRadio, TestoLungo } from "./CampiCall";
import { CriterioEditor, erroreCriterio } from "./CriterioEditor";
import { avanzamento, soloCambiati, vuoto, type PassoProps } from "./passoComune";
import { useNomiCall } from "./useNomiCall";

/** Requisito nella lista di lavoro: `chiave` per React, `modificato` se la
 *  copertura mostrata non vale più (si ricalcola al salvataggio). */
interface RequisitoLocale extends RequisitoCall {
  chiave: string;
  modificato: boolean;
}

let contatore = 0;
const nuovaChiave = () => `r${++contatore}`;
const locali = (requisiti: RequisitoCall[]): RequisitoLocale[] =>
  requisiti.map((r) => ({ ...r, chiave: r.id ?? nuovaChiave(), modificato: false }));

const ICONE: Record<EsitoCoperturaCall, { icona: typeof CheckCircle2; classe: string }> = {
  coperto: { icona: CheckCircle2, classe: "text-emerald-700 bg-emerald-50 ring-emerald-200" },
  non_coperto: { icona: XCircle, classe: "text-red-700 bg-red-50 ring-red-200" },
  dato_mancante: { icona: HelpCircle, classe: "text-amber-800 bg-amber-50 ring-amber-200" },
  incerto: { icona: AlertTriangle, classe: "text-amber-800 bg-amber-50 ring-amber-200" },
  non_valutabile: { icona: MinusCircle, classe: "text-slate-600 bg-slate-100 ring-slate-200" },
};

/** La tua copertura di un requisito: icona E testo (mai il solo colore). */
export function CoperturaBadge({ esito }: { esito: EsitoCoperturaCall }) {
  const { icona: Icona, classe } = ICONE[esito] ?? ICONE.non_valutabile;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        classe,
      )}
    >
      <Icona className="size-3.5" aria-hidden />
      {CALL_COPY.esitiCopertura[esito] ?? esito}
    </span>
  );
}

function RequisitoEditor({
  iniziale,
  onFatto,
  onAnnulla,
}: {
  iniziale: RequisitoLocale;
  onFatto: (r: RequisitoLocale) => void;
  onAnnulla: () => void;
}) {
  const id = useId();
  const [testo, setTesto] = useState(iniziale.testo);
  const [criterio, setCriterio] = useState<CriterioPartner | null>(iniziale.criterio);
  const [ambito, setAmbito] = useState<AmbitoRequisitoCall>(iniziale.ambito);
  const [cercato, setCercato] = useState(iniziale.cercato);
  const [errori, setErrori] = useState<string[]>([]);
  const fatto = () => {
    const problemi: string[] = [];
    const t = testo.trim();
    if (t.length < LIMITI_CALL.testoRequisitoMin || t.length > LIMITI_CALL.testoRequisitoMax) {
      problemi.push(
        `Il testo del requisito deve avere tra ${LIMITI_CALL.testoRequisitoMin} e ${LIMITI_CALL.testoRequisitoMax} caratteri.`,
      );
    }
    const errCriterio = erroreCriterio(criterio);
    if (errCriterio) problemi.push(errCriterio);
    if (problemi.length) return setErrori(problemi);
    const cambiato =
      t !== iniziale.testo || firma(criterio) !== firma(iniziale.criterio) || ambito !== iniziale.ambito;
    onFatto({
      ...iniziale,
      testo: t,
      criterio: criterio && criterio.tipo === "manuale" ? null : criterio,
      ambito,
      cercato,
      modificato: iniziale.modificato || cambiato,
    });
  };
  return (
    <div className="mt-3 space-y-4 rounded-lg border border-brand-200 bg-brand-50/30 p-4">
      <TestoLungo
        etichetta="Il requisito, in parole"
        aiuto="Visibile alle altre aziende se lo cerchi: niente contatti né dati che fanno riconoscere l'azienda."
        valore={testo}
        onChange={setTesto}
        massimo={LIMITI_CALL.testoRequisitoMax}
        righe={2}
        required
      />
      <CriterioEditor valore={criterio} onChange={setCriterio} />
      <SceltaRadio
        legenda="Chi lo deve avere"
        nome={`${id}-ambito`}
        valore={ambito}
        onChange={setAmbito}
        opzioni={[
          { valore: "consorzio", etichetta: CALL_COPY.ambiti.consorzio, nota: "Basta che lo abbia un'azienda del partenariato." },
          { valore: "ogni_membro", etichetta: CALL_COPY.ambiti.ogni_membro, nota: "Ogni azienda del partenariato lo deve avere." },
        ]}
      />
      <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
          checked={cercato}
          onChange={(e) => setCercato(e.target.checked)}
        />
        <span>
          Lo cerco nei partner
          <span className="block text-xs text-slate-500">
            I requisiti che cerchi compaiono nella call e guidano i suggerimenti.
          </span>
        </span>
      </label>
      {errori.length > 0 && (
        <ul className="list-disc rounded-lg bg-red-50 py-2 pl-8 pr-3 text-sm text-red-700" role="alert">
          {errori.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onAnnulla}>
          Annulla
        </Button>
        <Button onClick={fatto}>Fatto</Button>
      </div>
    </div>
  );
}

function Riepilogo({ requisiti }: { requisiti: RequisitoLocale[] }) {
  const conta = (e: EsitoCoperturaCall) =>
    requisiti.filter((r) => !r.modificato && r.copertura_creatore === e).length;
  const cercati = requisiti.filter((r) => r.cercato).length;
  const voci: Array<[string, number]> = [
    ["Li copri tu", conta("coperto")],
    ["Non li copri", conta("non_coperto")],
    ["Da verificare", conta("dato_mancante") + conta("incerto")],
    ["Da valutare a mano", conta("non_valutabile")],
  ];
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-slate-600">
      {voci.map(([t, n]) => (
        <span key={t}>
          {t}: <span className="font-semibold text-slate-900 tabular">{n}</span>
        </span>
      ))}
      <span>
        Cerchi nei partner: <span className="font-semibold text-brand-700 tabular">{cercati}</span>
      </span>
    </div>
  );
}

/** AI-check del bando: se manca, si propone di lanciarlo (consuma la quota). */
function BoxAiCheck({
  slug,
  gap,
  onRicalcola,
  ricalcoloInCorso,
}: {
  slug: string;
  gap: GapCall;
  onRicalcola: () => void;
  ricalcoloInCorso: boolean;
}) {
  const storico = useAiChecksForBando(slug);
  const lancia = useRequestAiCheck(slug);
  const [conferma, setConferma] = useState(false);
  const ultimo = storico.data?.items[0];
  const rimanenti = storico.data?.quota.rimanenti ?? null;
  const puoLanciare = storico.data?.editable !== false;

  let testo: ReactNode;
  let azione: ReactNode = null;
  if (ultimo?.status === "pending") {
    testo = (
      <span className="inline-flex items-center gap-1.5">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        AI-check in corso: di solito servono 1-2 minuti. Poi ricalcola i requisiti.
      </span>
    );
  } else if (gap.ai_check.disponibile) {
    const piuRecente = ultimo?.status === "ready" && ultimo.id !== gap.ai_check.id;
    testo = piuRecente
      ? "C'è un AI-check più recente di quello usato: ricalcola i requisiti per usarlo."
      : `I requisiti tengono conto del tuo AI-check${gap.ai_check.data ? ` del ${formatDate(gap.ai_check.data)}` : ""}.`;
    if (piuRecente) {
      azione = (
        <Button variant="secondary" size="sm" onClick={onRicalcola} loading={ricalcoloInCorso}>
          Ricalcola
        </Button>
      );
    }
  } else if (ultimo?.status === "ready") {
    testo = "Il tuo AI-check è pronto: ricalcola i requisiti per usarlo.";
    azione = (
      <Button variant="secondary" size="sm" onClick={onRicalcola} loading={ricalcoloInCorso}>
        Ricalcola
      </Button>
    );
  } else {
    testo = (
      <>
        Per requisiti più completi lancia un AI-check su questo bando: consuma 1 AI-check del tuo
        piano
        {rimanenti !== null ? ` (te ne restano ${rimanenti})` : ""}.
      </>
    );
    azione =
      rimanenti === 0 ? (
        <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
          {CALL_COPY.vediPiani}
        </LinkButton>
      ) : puoLanciare ? (
        <Button variant="secondary" size="sm" onClick={() => setConferma(true)}>
          Lancia l'AI-check
        </Button>
      ) : null;
  }

  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
      <p className="min-w-0 flex-1 text-sm text-slate-700" role="status" aria-live="polite">
        {testo}
      </p>
      {azione}
      {lancia.isError && (
        <p className="w-full text-sm text-red-700" role="alert">
          {apiErrorMessage(lancia.error)}
        </p>
      )}
      <Dialog
        open={conferma}
        onClose={() => setConferma(false)}
        title="Lanciare un AI-check?"
        dismissible={!lancia.isPending}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConferma(false)} disabled={lancia.isPending}>
              Annulla
            </Button>
            <Button
              loading={lancia.isPending}
              onClick={() =>
                lancia.mutate(undefined, {
                  onSettled: () => setConferma(false),
                })
              }
            >
              Lancia l'AI-check
            </Button>
          </>
        }
      >
        <p>
          Consuma 1 AI-check del tuo piano{rimanenti !== null ? ` (te ne restano ${rimanenti})` : ""}.
          Quando è pronto, torna qui e ricalcola i requisiti.
        </p>
      </Dialog>
    </div>
  );
}

/** Passo 3: budget, quota e requisiti (gap analysis deterministica: la
 *  copertura la calcola il server, qui si decide cosa cercare). */
export function PassoGap({ call, onAvanti, onIndietro, onDirty }: PassoProps) {
  const nomi = useNomiCall();
  const genera = useGeneraRequisiti(call.id);
  const salvaRequisiti = useSalvaRequisiti(call.id);
  const aggiorna = useAggiornaCall(call.id);
  const idFascia = useId();

  const [requisiti, setRequisiti] = useState<RequisitoLocale[]>(() => locali(call.gap.requisiti));
  const [gap, setGap] = useState<GapCall>(call.gap);
  const [generati, setGenerati] = useState(false);
  const [modifica, setModifica] = useState<{ chiave: string; nuovo: boolean } | null>(null);
  const [confermaRicalcolo, setConfermaRicalcolo] = useState(false);
  const [fascia, setFascia] = useState<BudgetFasciaCall | "">(call.budget_fascia ?? "");
  const [budget, setBudget] = useState(mostraDecimale(call.budget_progetto_eur));
  const [quota, setQuota] = useState(mostraDecimale(call.quota_creatore_pct));
  const [errori, setErrori] = useState<string[]>([]);
  const avviato = useRef(false);

  const letturaBudget = leggiImporto(budget);
  const letturaQuota = leggiPercentuale(quota);
  const campiValidi = letturaBudget.ok && letturaQuota.ok;
  const campi: CallAggiornaInput =
    letturaBudget.ok && letturaQuota.ok
      ? soloCambiati(call, {
          budget_fascia: fascia || null,
          budget_progetto_eur: letturaBudget.valore,
          quota_creatore_pct: letturaQuota.valore,
        })
      : {};
  const requisitiCambiati =
    generati ||
    firma(requisiti.map(requisitoInput)) !== firma(call.gap.requisiti.map(requisitoInput));
  const dirty = requisitiCambiati || !vuoto(campi) || !campiValidi;
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const ricalcola = async () => {
    setConfermaRicalcolo(false);
    try {
      const nuovo = await genera.mutateAsync();
      setGap(nuovo);
      setRequisiti(locali(nuovo.requisiti));
      setGenerati(true);
      setModifica(null);
    } catch {
      // mostrato sotto
    }
  };

  // Prima volta, senza requisiti salvati: li proponiamo subito (è gratuito).
  useEffect(() => {
    if (avviato.current || call.gap.requisiti.length > 0) return;
    avviato.current = true;
    void ricalcola();
    // Una volta sola, all'apertura del passo.
  }, []);

  const chiediRicalcolo = () => {
    if (requisiti.length > 0) setConfermaRicalcolo(true);
    else void ricalcola();
  };

  const cambiaRequisito = (chiave: string, fn: (r: RequisitoLocale) => RequisitoLocale) =>
    setRequisiti((lista) => lista.map((r) => (r.chiave === chiave ? fn(r) : r)));
  const rimuovi = (chiave: string) => setRequisiti((lista) => lista.filter((r) => r.chiave !== chiave));
  const aggiungi = () => {
    const chiave = nuovaChiave();
    setRequisiti((lista) => [
      ...lista,
      {
        chiave,
        modificato: true,
        id: null,
        etichetta: null,
        testo: "",
        criterio: null,
        ambito: "consorzio",
        cercato: true,
        origine: "manuale",
        rif_origine: null,
        citazione: null,
        copertura_creatore: null,
        copertura_fonte: null,
        copertura_nota: null,
        ordine: lista.length,
      },
    ]);
    setModifica({ chiave, nuovo: true });
  };

  const salva = async () => {
    setErrori([]);
    const problemi: string[] = [];
    if (modifica) problemi.push("Completa o annulla il requisito che stai modificando.");
    if (!letturaBudget.ok) problemi.push(letturaBudget.errore);
    if (!letturaQuota.ok) problemi.push(letturaQuota.errore);
    if (letturaBudget.ok && letturaBudget.valore && fascia && !budgetNellaFascia(fascia, letturaBudget.valore)) {
      problemi.push("Il budget del progetto non rientra nella fascia scelta.");
    }
    if (requisiti.length > LIMITI_CALL.requisitiMax) {
      problemi.push(`Puoi indicare al massimo ${LIMITI_CALL.requisitiMax} requisiti.`);
    }
    if (problemi.length) {
      setErrori(problemi);
      return;
    }
    try {
      const corpo = { ...campi, ...avanzamento(call, 4) };
      if (!vuoto(corpo)) await aggiorna.mutateAsync(corpo);
      // Budget o quota cambiati: la copertura delle regole economiche va
      // ricalcolata, e si ricalcola risalvando i requisiti.
      if (requisitiCambiati || !vuoto(campi)) {
        await salvaRequisiti.mutateAsync(requisiti.map(requisitoInput));
      }
      onAvanti();
    } catch {
      // mostrato nella barra
    }
  };

  const erroreServer = aggiorna.isError
    ? apiErrorMessage(aggiorna.error)
    : salvaRequisiti.isError
      ? apiErrorMessage(salvaRequisiti.error)
      : null;
  const fasciaProposta = letturaBudget.ok && letturaBudget.valore ? fasciaDi(letturaBudget.valore) : null;

  return (
    <div className="space-y-4">
      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Budget del progetto e tua quota</h3>
          <p className="text-xs text-slate-500">
            Servono a valutare i requisiti economici. Alle altre aziende mostriamo solo la fascia.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="space-y-1.5">
            <label htmlFor={idFascia} className="block text-sm font-medium text-slate-700">
              Fascia del budget (pubblica)
            </label>
            <select
              id={idFascia}
              value={fascia}
              onChange={(e) => setFascia(e.target.value as BudgetFasciaCall | "")}
              className="h-10 w-full cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
            >
              <option value="">Non indicata</option>
              {FASCE_BUDGET.map((f) => (
                <option key={f} value={f}>
                  {CALL_COPY.fasceBudget[f]}
                </option>
              ))}
            </select>
          </div>
          <TextField
            label="Budget esatto (riservato)"
            inputMode="decimal"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            onBlur={() => {
              if (!fascia && fasciaProposta) setFascia(fasciaProposta);
            }}
            placeholder="Es. 1.200.000"
            error={letturaBudget.ok ? undefined : letturaBudget.errore}
            helper={
              letturaBudget.ok && letturaBudget.valore
                ? `${formatEur(letturaBudget.valore)} — lo vedi solo tu`
                : "Facoltativo: lo vedi solo tu"
            }
          />
          <TextField
            label="La tua quota (%)"
            inputMode="decimal"
            value={quota}
            onChange={(e) => setQuota(e.target.value)}
            placeholder="Es. 60"
            error={letturaQuota.ok ? undefined : letturaQuota.errore}
            helper="La parte del progetto che fai tu"
          />
        </div>
      </Card>

      <Card className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-800">Requisiti del bando e cosa cerchi</h3>
            <p className="max-w-2xl text-xs text-slate-500">
              Li ricaviamo da regole confermate, requisiti del bando e AI-check, e controlliamo quali
              copri già tu. Scegli quelli che cerchi nei partner: la copertura la vedi solo tu.
            </p>
          </div>
          <Button variant="secondary" size="sm" onClick={chiediRicalcolo} loading={genera.isPending}>
            <RefreshCw className="size-4" aria-hidden />
            {requisiti.length ? "Ricalcola dal bando" : "Ricava i requisiti dal bando"}
          </Button>
        </div>

        <BoxAiCheck
          slug={call.bando.slug}
          gap={gap}
          onRicalcola={chiediRicalcolo}
          ricalcoloInCorso={genera.isPending}
        />

        <div aria-live="polite">
          {genera.isPending && requisiti.length === 0 ? (
            <div className="space-y-2" aria-hidden>
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : genera.isError ? (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {apiErrorMessage(genera.error, "Non siamo riusciti a ricavare i requisiti.")}
            </p>
          ) : generati ? (
            <p className="text-sm text-emerald-700">Requisiti ricalcolati: controllali e salva.</p>
          ) : null}
        </div>

        {requisiti.length > 0 && <Riepilogo requisiti={requisiti} />}

        {requisiti.length === 0 && !genera.isPending ? (
          <p className="text-sm text-slate-500">
            Nessun requisito ancora. Ricavali dal bando o aggiungili tu.
          </p>
        ) : (
          <ul className="space-y-2">
            {requisiti.map((r) => (
              <li key={r.chiave} className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
                <div className="flex flex-wrap items-start gap-3">
                  {r.etichetta && (
                    <Badge tone="brand" className="shrink-0 tabular">
                      <span className="sr-only">Requisito </span>
                      {r.etichetta}
                    </Badge>
                  )}
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="text-sm font-medium text-slate-800">{r.testo || "Nuovo requisito"}</p>
                    <p className="text-xs text-slate-500">
                      {descriviCriterio(r.criterio, nomi)} · {CALL_COPY.ambiti[r.ambito]} ·{" "}
                      {CALL_COPY.origini[r.origine]}
                    </p>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      {r.modificato || !r.copertura_creatore ? (
                        <span>La tua copertura si calcola quando salvi.</span>
                      ) : (
                        <>
                          <CoperturaBadge esito={r.copertura_creatore} />
                          {r.copertura_nota && <span>{r.copertura_nota}</span>}
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-1">
                    <Button
                      variant={r.cercato ? "primary" : "secondary"}
                      size="sm"
                      aria-pressed={r.cercato}
                      aria-label={`Cercalo nei partner: requisito ${r.etichetta ?? r.testo}`}
                      onClick={() => cambiaRequisito(r.chiave, (x) => ({ ...x, cercato: !x.cercato }))}
                    >
                      {r.cercato ? "Lo cerchi" : "Cercalo"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setModifica({ chiave: r.chiave, nuovo: false })}
                      aria-label={`Modifica il requisito ${r.etichetta ?? r.testo}`}
                    >
                      <Pencil className="size-4" aria-hidden />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => rimuovi(r.chiave)}
                      aria-label={`Rimuovi il requisito ${r.etichetta ?? r.testo}`}
                    >
                      <Trash2 className="size-4" aria-hidden />
                    </Button>
                  </div>
                </div>
                {modifica?.chiave === r.chiave && (
                  <RequisitoEditor
                    iniziale={r}
                    onFatto={(nuovo) => {
                      cambiaRequisito(r.chiave, () => nuovo);
                      setModifica(null);
                    }}
                    onAnnulla={() => {
                      if (modifica.nuovo) rimuovi(r.chiave);
                      setModifica(null);
                    }}
                  />
                )}
              </li>
            ))}
          </ul>
        )}
        <Button
          variant="secondary"
          size="sm"
          onClick={aggiungi}
          disabled={!!modifica || requisiti.length >= LIMITI_CALL.requisitiMax}
        >
          <Plus className="size-4" aria-hidden />
          Aggiungi un requisito
        </Button>

        {errori.length > 0 && (
          <ul className="list-disc rounded-lg bg-red-50 py-2 pl-8 pr-3 text-sm text-red-700" role="alert">
            {errori.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        )}
        <BarraPasso
          onIndietro={onIndietro}
          onAvanti={() => void salva()}
          inCorso={aggiorna.isPending || salvaRequisiti.isPending}
          errore={erroreServer}
        />
      </Card>

      <Dialog
        open={confermaRicalcolo}
        onClose={() => setConfermaRicalcolo(false)}
        title="Ricalcolare i requisiti?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfermaRicalcolo(false)}>
              Annulla
            </Button>
            <Button onClick={() => void ricalcola()}>Ricalcola</Button>
          </>
        }
      >
        <p>
          Ricaviamo di nuovo i requisiti dal bando. Quelli già salvati che il bando ripropone restano
          con le tue scelte, quelli scritti a mano restano in fondo e quelli che non derivano più dal
          bando vengono tolti, anche dalle posizioni che li coprivano. Le modifiche non ancora salvate
          si perdono. Nulla cambia finché non salvi.
        </p>
      </Dialog>
    </div>
  );
}
