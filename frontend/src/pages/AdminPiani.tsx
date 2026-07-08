import { BadgeCheck, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import {
  useAdminCreatePlan,
  useAdminPlans,
  useAdminUpdatePlan,
  type PlanPayload,
} from "../hooks/useAdmin";
import { apiErrorMessage } from "../lib/api";
import type { Plan, TipoPrezzo } from "../types";

interface PlanFormState {
  nome: string;
  slug: string;
  descrizione: string;
  prezzo_annuale: string;
  tipo_prezzo: TipoPrezzo;
  etichetta_prezzo: string;
  ai_check: string;
  alert_attivo: boolean;
  alert_giorni_preavviso: string;
  num_account_aziendali: string;
  ordering: string;
  is_active: boolean;
}

function toFormState(plan: Plan): PlanFormState {
  return {
    nome: plan.nome,
    slug: plan.slug,
    descrizione: plan.descrizione ?? "",
    prezzo_annuale: String(plan.prezzo_annuale ?? "0"),
    tipo_prezzo: plan.tipo_prezzo ?? "importo",
    etichetta_prezzo: plan.etichetta_prezzo ?? "",
    ai_check: String(plan.ai_check),
    alert_attivo: plan.alert_attivo,
    alert_giorni_preavviso: plan.alert_giorni_preavviso ? String(plan.alert_giorni_preavviso) : "",
    num_account_aziendali: String(plan.num_account_aziendali),
    ordering: String(plan.ordering),
    is_active: plan.is_active,
  };
}

const EMPTY_FORM: PlanFormState = {
  nome: "",
  slug: "",
  descrizione: "",
  prezzo_annuale: "0",
  tipo_prezzo: "importo",
  etichetta_prezzo: "",
  ai_check: "0",
  alert_attivo: false,
  alert_giorni_preavviso: "",
  num_account_aziendali: "1",
  ordering: "10",
  is_active: true,
};

function validate(form: PlanFormState): string | null {
  if (!form.nome.trim()) return "Il nome del piano è obbligatorio.";
  // Il prezzo conta solo in modalità «importo»: con gratis/su_richiesta il
  // campo è disabilitato, un valore residuo vuoto non deve bloccare il salvataggio.
  if (form.tipo_prezzo === "importo" && (Number(form.prezzo_annuale) < 0 || form.prezzo_annuale === ""))
    return "Il prezzo annuale non è valido.";
  if (!Number.isInteger(Number(form.ai_check)) || Number(form.ai_check) < 0)
    return "Il numero di AI-check non è valido.";
  if (form.alert_attivo && (!form.alert_giorni_preavviso || Number(form.alert_giorni_preavviso) < 1))
    return "Con gli alert attivi servono i giorni di preavviso (≥ 1).";
  if (!Number.isInteger(Number(form.num_account_aziendali)) || Number(form.num_account_aziendali) < 1)
    return "Gli account aziendali devono essere almeno 1.";
  return null;
}

function toPayload(form: PlanFormState): PlanPayload {
  return {
    nome: form.nome.trim(),
    descrizione: form.descrizione.trim() || null,
    prezzo_annuale: Number(form.prezzo_annuale),
    tipo_prezzo: form.tipo_prezzo,
    etichetta_prezzo: form.etichetta_prezzo.trim() || null,
    ai_check: Number(form.ai_check),
    alert_attivo: form.alert_attivo,
    alert_giorni_preavviso: form.alert_attivo ? Number(form.alert_giorni_preavviso) : null,
    num_account_aziendali: Number(form.num_account_aziendali),
    ordering: Number(form.ordering) || 0,
    is_active: form.is_active,
  };
}

function PlanFormFields({
  form,
  setForm,
  isNew,
}: {
  form: PlanFormState;
  setForm: (updater: (f: PlanFormState) => PlanFormState) => void;
  isNew?: boolean;
}) {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <TextField
        label="Nome"
        required
        value={form.nome}
        onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))}
      />
      <TextField
        label="Slug"
        required
        disabled={!isNew}
        helper={isNew ? "Minuscole, numeri e trattini" : "Non modificabile"}
        value={form.slug}
        onChange={(e) =>
          setForm((f) => ({ ...f, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-") }))
        }
      />
      <div className="sm:col-span-2">
        <TextField
          label="Descrizione"
          value={form.descrizione}
          onChange={(e) => setForm((f) => ({ ...f, descrizione: e.target.value }))}
        />
      </div>
      <SelectField
        label="Prezzo mostrato come"
        value={form.tipo_prezzo}
        onChange={(e) => setForm((f) => ({ ...f, tipo_prezzo: e.target.value as TipoPrezzo }))}
      >
        <option value="importo">Importo in €</option>
        <option value="gratis">Gratis</option>
        <option value="su_richiesta">Su richiesta (etichetta)</option>
      </SelectField>
      <TextField
        label="Prezzo annuale (€)"
        type="number"
        min={0}
        step="0.01"
        required
        disabled={form.tipo_prezzo !== "importo"}
        helper={
          form.tipo_prezzo !== "importo" ? "Non mostrato ai clienti con questa modalità" : undefined
        }
        value={form.prezzo_annuale}
        onChange={(e) => setForm((f) => ({ ...f, prezzo_annuale: e.target.value }))}
      />
      <div className="sm:col-span-2">
        <TextField
          label="Etichetta al posto del prezzo"
          disabled={form.tipo_prezzo !== "su_richiesta"}
          helper={
            form.tipo_prezzo === "su_richiesta"
              ? "Se vuota viene mostrato «Su richiesta». Con questa modalità il piano non è attivabile dai clienti: la CTA diventa «Richiedi una consulenza»."
              : "Usata solo con «Su richiesta»"
          }
          value={form.etichetta_prezzo}
          onChange={(e) => setForm((f) => ({ ...f, etichetta_prezzo: e.target.value }))}
        />
      </div>
      <TextField
        label="AI-check inclusi"
        type="number"
        min={0}
        required
        value={form.ai_check}
        onChange={(e) => setForm((f) => ({ ...f, ai_check: e.target.value }))}
      />
      <SelectField
        label="Alert personalizzati"
        value={form.alert_attivo ? "si" : "no"}
        onChange={(e) => setForm((f) => ({ ...f, alert_attivo: e.target.value === "si" }))}
      >
        <option value="no">Non inclusi</option>
        <option value="si">Inclusi</option>
      </SelectField>
      <TextField
        label="Giorni di preavviso"
        type="number"
        min={1}
        disabled={!form.alert_attivo}
        helper={form.alert_attivo ? undefined : "Attiva gli alert per impostarli"}
        value={form.alert_giorni_preavviso}
        onChange={(e) => setForm((f) => ({ ...f, alert_giorni_preavviso: e.target.value }))}
      />
      <TextField
        label="Account aziendali"
        type="number"
        min={1}
        required
        value={form.num_account_aziendali}
        onChange={(e) => setForm((f) => ({ ...f, num_account_aziendali: e.target.value }))}
      />
      <TextField
        label="Ordine di visualizzazione"
        type="number"
        value={form.ordering}
        onChange={(e) => setForm((f) => ({ ...f, ordering: e.target.value }))}
      />
      <div className="flex items-center gap-2 sm:col-span-2">
        <input
          id={`attivo-${form.slug || "new"}`}
          type="checkbox"
          checked={form.is_active}
          onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
          className="size-4 cursor-pointer accent-brand-500"
        />
        <label htmlFor={`attivo-${form.slug || "new"}`} className="cursor-pointer text-sm text-slate-700">
          Piano attivo (visibile in registrazione e cambio piano)
        </label>
      </div>
    </div>
  );
}

function PlanEditor({ plan }: { plan: Plan }) {
  const [form, setForm] = useState<PlanFormState>(() => toFormState(plan));
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const updatePlan = useAdminUpdatePlan();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    const problem = validate(form);
    setValidationError(problem);
    if (problem) return;
    try {
      await updatePlan.mutateAsync({ planId: plan.id, data: toPayload(form) });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch {
      // errore mostrato sotto
    }
  };

  return (
    <Card className={`p-6 ${form.is_active ? "" : "opacity-80"}`}>
      <form onSubmit={handleSubmit}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="font-display text-base font-semibold text-slate-900">{plan.nome}</h2>
          {form.is_active ? <Badge tone="emerald">Attivo</Badge> : <Badge tone="slate">Disattivato</Badge>}
        </div>
        <PlanFormFields form={form} setForm={setForm} />
        <div className="mt-5 flex items-center gap-3">
          <Button type="submit" loading={updatePlan.isPending}>
            Salva piano
          </Button>
          {saved && (
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600" role="status">
              <BadgeCheck className="size-4" aria-hidden />
              Salvato
            </span>
          )}
          {(validationError || updatePlan.isError) && (
            <span className="text-sm text-red-600" role="alert">
              {validationError ?? apiErrorMessage(updatePlan.error)}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}

export default function AdminPiani() {
  const { data: plans, isPending, isError, error, refetch } = useAdminPlans();
  const createPlan = useAdminCreatePlan();
  const [createOpen, setCreateOpen] = useState(false);
  const [newForm, setNewForm] = useState<PlanFormState>(EMPTY_FORM);
  const [createError, setCreateError] = useState<string | null>(null);

  const handleCreate = async () => {
    const problem = validate(newForm) ?? (!newForm.slug.trim() ? "Lo slug è obbligatorio." : null);
    setCreateError(problem);
    if (problem) return;
    try {
      await createPlan.mutateAsync({ ...toPayload(newForm), nome: newForm.nome.trim(), slug: newForm.slug.trim() });
      setCreateOpen(false);
      setNewForm(EMPTY_FORM);
    } catch (err) {
      setCreateError(apiErrorMessage(err));
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            Gestione abbonamenti
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Modifica parametri e prezzi dei piani. I piani non si eliminano: si disattivano.
          </p>
        </div>
        <Button
          onClick={() => {
            setCreateError(null);
            setNewForm(EMPTY_FORM);
            setCreateOpen(true);
          }}
        >
          <Plus className="size-4" aria-hidden />
          Nuovo piano
        </Button>
      </div>

      {isPending ? (
        <div className="mt-6 space-y-5">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-72 w-full" />
          ))}
        </div>
      ) : isError ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <div className="mt-6 space-y-5">
          {(plans ?? []).map((plan) => (
            <PlanEditor key={plan.id} plan={plan} />
          ))}
        </div>
      )}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Nuovo piano di abbonamento"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Annulla
            </Button>
            <Button onClick={handleCreate} loading={createPlan.isPending}>
              Crea piano
            </Button>
          </>
        }
      >
        <PlanFormFields form={newForm} setForm={setNewForm} isNew />
        {createError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {createError}
          </p>
        )}
      </Dialog>
    </div>
  );
}
