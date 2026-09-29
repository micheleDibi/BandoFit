import { Lock, Pencil } from "lucide-react";
import { useId, useState } from "react";
import { useSalvaBudgetConsorzio } from "../../hooks/useConsorzio";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY, CONSORZIO_COPY } from "../../lib/copy";
import { formatEur } from "../../lib/format";
import type { BudgetConsorzio as Budget, BudgetFasciaCall } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { SelectField, TextField } from "../ui/Field";
import { budgetNellaFascia, FASCE_BUDGET, fasciaDi, leggiImporto, mostraDecimale } from "./callDati";

function Modifica({
  callId,
  budget,
  onFatto,
  onAnnulla,
}: {
  callId: string;
  budget: Budget;
  onFatto: () => void;
  onAnnulla: () => void;
}) {
  const salva = useSalvaBudgetConsorzio(callId);
  const [fascia, setFascia] = useState<BudgetFasciaCall | "">(budget.fascia ?? "");
  const [esatto, setEsatto] = useState(mostraDecimale(budget.esatto));
  const [errori, setErrori] = useState<{ fascia?: string; esatto?: string }>({});
  const lettura = leggiImporto(esatto);
  const proposta = lettura.ok && lettura.valore ? fasciaDi(lettura.valore) : null;

  const conferma = () => {
    const problemi: { fascia?: string; esatto?: string } = {};
    if (!fascia) problemi.fascia = "Scegli la fascia del budget";
    if (!lettura.ok) problemi.esatto = lettura.errore;
    else if (fascia && lettura.valore && !budgetNellaFascia(fascia, lettura.valore)) {
      problemi.esatto = "Il budget esatto non rientra nella fascia scelta";
    }
    setErrori(problemi);
    if (!fascia || !lettura.ok || problemi.fascia || problemi.esatto) return;
    salva.mutate(
      { budget_fascia: fascia, budget_progetto_eur: lettura.valore },
      { onSuccess: onFatto },
    );
  };

  return (
    <form
      noValidate
      className="mt-3 space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        conferma();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <SelectField
          label="Fascia del budget (pubblica)"
          required
          value={fascia}
          onChange={(e) => setFascia(e.target.value as BudgetFasciaCall | "")}
          error={errori.fascia}
        >
          <option value="">Scegli una fascia…</option>
          {FASCE_BUDGET.map((f) => (
            <option key={f} value={f}>
              {CALL_COPY.fasceBudget[f]}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Budget esatto (riservato)"
          inputMode="decimal"
          value={esatto}
          onChange={(e) => setEsatto(e.target.value)}
          onBlur={() => {
            if (!fascia && proposta) setFascia(proposta);
          }}
          placeholder="Es. 1.200.000"
          error={errori.esatto}
          helper={lettura.ok && lettura.valore ? formatEur(lettura.valore) : "Facoltativo"}
        />
      </div>
      {salva.isError && (
        <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {apiErrorMessage(salva.error)}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" loading={salva.isPending}>
          Salva il budget
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onAnnulla} disabled={salva.isPending}>
          Annulla
        </Button>
      </div>
    </form>
  );
}

/** Budget del progetto: fascia pubblica e budget esatto riservato (C4), con
 *  la nota su chi vede l'esatto. Serve a calcolare il costo della quota di
 *  ogni membro per i requisiti economici. Lo modifica solo il titolare
 *  dell'azienda che ha creato la call (`modificabile`). */
export function BudgetConsorzio({
  callId,
  budget,
  seiCreatore,
  onAnnuncio,
}: {
  callId: string;
  budget: Budget;
  seiCreatore: boolean;
  onAnnuncio: (testo: string) => void;
}) {
  const idTitolo = useId();
  const [modifica, setModifica] = useState(false);
  return (
    <Card className="p-5">
      <section aria-labelledby={idTitolo}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id={idTitolo} className="font-display text-base font-semibold text-slate-900">
            Budget del progetto
          </h2>
          {budget.modificabile && !modifica && (
            <Button size="sm" variant="secondary" onClick={() => setModifica(true)}>
              <Pencil className="size-4" aria-hidden />
              Modifica il budget
            </Button>
          )}
        </div>
        <p className="mt-1 text-sm text-slate-600">
          Serve a calcolare il costo della quota di ogni membro per i requisiti economici del bando:
          senza il budget esatto si usa la fascia.
        </p>
        {modifica ? (
          <Modifica
            callId={callId}
            budget={budget}
            onAnnulla={() => setModifica(false)}
            onFatto={() => {
              setModifica(false);
              onAnnuncio("Budget salvato: la verifica del consorzio è aggiornata.");
            }}
          />
        ) : (
          <dl className="mt-3 grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">Fascia (pubblica)</dt>
              <dd className="mt-1 text-sm text-slate-700">
                {budget.fascia ? CALL_COPY.fasceBudget[budget.fascia] : "Non indicata"}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                Budget esatto (riservato)
              </dt>
              <dd className="mt-1 text-sm text-slate-700">
                {budget.esatto ? (
                  <span className="inline-flex items-center gap-1 tabular">
                    <Lock className="size-3.5 text-slate-400" aria-hidden />
                    {formatEur(budget.esatto)}
                  </span>
                ) : seiCreatore ? (
                  "Non indicato"
                ) : (
                  "Non disponibile"
                )}
              </dd>
            </div>
          </dl>
        )}
        <p className="mt-3 flex items-start gap-1.5 text-xs text-slate-500" role="note">
          <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {seiCreatore ? CONSORZIO_COPY.notaBudget : CONSORZIO_COPY.notaBudgetMembro}
        </p>
      </section>
    </Card>
  );
}
