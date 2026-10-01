import { useState } from "react";
import { useSalvaBudgetConsorzio } from "../../hooks/useConsorzio";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY, CONSORZIO_COPY } from "../../lib/copy";
import { formatEur } from "../../lib/format";
import type { BudgetConsorzio as Budget, BudgetFasciaCall } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { DefinitionList } from "../ui/Facts";
import { SelectField, TextField } from "../ui/Field";
import { Section, SectionHeader } from "../ui/SectionHeader";
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
      className="flex flex-col gap-4"
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
      {salva.isError && <Alert tono="errore">{apiErrorMessage(salva.error)}</Alert>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" loading={salva.isPending}>
          Salva il budget
        </Button>
        <Button type="button" variant="secondary" onClick={onAnnulla} disabled={salva.isPending}>
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
  const [modifica, setModifica] = useState(false);
  return (
    <Section>
      <SectionHeader
        titolo="Budget del progetto"
        azione={
          budget.modificabile && !modifica ? (
            <Button size="sm" variant="secondary" onClick={() => setModifica(true)}>
              Modifica il budget
            </Button>
          ) : undefined
        }
      />
      <p className="text-body text-ink-2">
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
        <DefinitionList
          items={[
            {
              etichetta: "Fascia (pubblica)",
              valore: budget.fascia ? CALL_COPY.fasceBudget[budget.fascia] : "Non indicata",
            },
            {
              etichetta: "Budget esatto (riservato)",
              valore: budget.esatto
                ? formatEur(budget.esatto)
                : seiCreatore
                  ? "Non indicato"
                  : "Non disponibile",
            },
          ]}
        />
      )}
      <p className="text-small text-ink-3" role="note">
        {seiCreatore ? CONSORZIO_COPY.notaBudget : CONSORZIO_COPY.notaBudgetMembro}
      </p>
    </Section>
  );
}
