import { BadgeCheck, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import {
  useAdminAddons,
  useAdminCreateAddon,
  useAdminUpdateAddon,
  type AddonPayload,
} from "../hooks/useAdmin";
import { apiErrorMessage } from "../lib/api";
import type { Addon, TipoPrezzo } from "../types";

interface AddonFormState {
  nome: string;
  slug: string;
  descrizione: string;
  prezzo: string;
  tipo_prezzo: TipoPrezzo;
  etichetta_prezzo: string;
  ordering: string;
  is_active: boolean;
}

function toFormState(addon: Addon): AddonFormState {
  return {
    nome: addon.nome,
    slug: addon.slug,
    descrizione: addon.descrizione ?? "",
    prezzo: String(addon.prezzo ?? "0"),
    tipo_prezzo: addon.tipo_prezzo ?? "importo",
    etichetta_prezzo: addon.etichetta_prezzo ?? "",
    ordering: String(addon.ordering),
    is_active: addon.is_active,
  };
}

const EMPTY_FORM: AddonFormState = {
  nome: "",
  slug: "",
  descrizione: "",
  prezzo: "0",
  tipo_prezzo: "importo",
  etichetta_prezzo: "",
  ordering: "10",
  is_active: true,
};

function validate(form: AddonFormState): string | null {
  if (!form.nome.trim()) return "Il nome dell'add-on è obbligatorio.";
  // Il prezzo conta solo in modalità «importo»: con gratis/su_richiesta il
  // campo è disabilitato, un valore residuo vuoto non deve bloccare il salvataggio.
  if (form.tipo_prezzo === "importo" && (Number(form.prezzo) < 0 || form.prezzo === ""))
    return "Il prezzo non è valido.";
  return null;
}

function toPayload(form: AddonFormState): AddonPayload {
  return {
    nome: form.nome.trim(),
    descrizione: form.descrizione.trim() || null,
    prezzo: Number(form.prezzo),
    tipo_prezzo: form.tipo_prezzo,
    etichetta_prezzo: form.etichetta_prezzo.trim() || null,
    ordering: Number(form.ordering) || 0,
    is_active: form.is_active,
  };
}

function AddonFormFields({
  form,
  setForm,
  isNew,
}: {
  form: AddonFormState;
  setForm: (updater: (f: AddonFormState) => AddonFormState) => void;
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
        helper={
          isNew
            ? "Identificativo stabile (minuscole, numeri e trattini): aggancerà le funzionalità"
            : "Non modificabile: è l'identificativo stabile dell'add-on"
        }
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
        label="Prezzo (€)"
        type="number"
        min={0}
        step="0.01"
        required
        disabled={form.tipo_prezzo !== "importo"}
        helper={
          form.tipo_prezzo !== "importo" ? "Non mostrato ai clienti con questa modalità" : undefined
        }
        value={form.prezzo}
        onChange={(e) => setForm((f) => ({ ...f, prezzo: e.target.value }))}
      />
      <TextField
        label="Etichetta al posto del prezzo"
        disabled={form.tipo_prezzo !== "su_richiesta"}
        helper={
          form.tipo_prezzo === "su_richiesta"
            ? "Se vuota viene mostrato «Su richiesta»"
            : "Usata solo con «Su richiesta»"
        }
        value={form.etichetta_prezzo}
        onChange={(e) => setForm((f) => ({ ...f, etichetta_prezzo: e.target.value }))}
      />
      <TextField
        label="Ordine di visualizzazione"
        type="number"
        value={form.ordering}
        onChange={(e) => setForm((f) => ({ ...f, ordering: e.target.value }))}
      />
      <div className="flex items-center gap-2 sm:col-span-2">
        <input
          id={`addon-attivo-${form.slug || "new"}`}
          type="checkbox"
          checked={form.is_active}
          onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
          className="size-4 cursor-pointer accent-brand-500"
        />
        <label
          htmlFor={`addon-attivo-${form.slug || "new"}`}
          className="cursor-pointer text-sm text-slate-700"
        >
          Add-on attivo (visibile ai clienti nella pagina Abbonamento)
        </label>
      </div>
    </div>
  );
}

function AddonEditor({ addon }: { addon: Addon }) {
  const [form, setForm] = useState<AddonFormState>(() => toFormState(addon));
  const [validationError, setValidationError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const updateAddon = useAdminUpdateAddon();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    const problem = validate(form);
    setValidationError(problem);
    if (problem) return;
    try {
      await updateAddon.mutateAsync({ addonId: addon.id, data: toPayload(form) });
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
          <h2 className="font-display text-base font-semibold text-slate-900">{addon.nome}</h2>
          {form.is_active ? (
            <Badge tone="emerald">Attivo</Badge>
          ) : (
            <Badge tone="slate">Disattivato</Badge>
          )}
        </div>
        <AddonFormFields form={form} setForm={setForm} />
        <div className="mt-5 flex items-center gap-3">
          <Button type="submit" loading={updateAddon.isPending}>
            Salva add-on
          </Button>
          {saved && (
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600" role="status">
              <BadgeCheck className="size-4" aria-hidden />
              Salvato
            </span>
          )}
          {(validationError || updateAddon.isError) && (
            <span className="text-sm text-red-600" role="alert">
              {validationError ?? apiErrorMessage(updateAddon.error)}
            </span>
          )}
        </div>
      </form>
    </Card>
  );
}

export default function AdminAddon() {
  const { data: addons, isPending, isError, error, refetch } = useAdminAddons();
  const createAddon = useAdminCreateAddon();
  const [createOpen, setCreateOpen] = useState(false);
  const [newForm, setNewForm] = useState<AddonFormState>(EMPTY_FORM);
  const [createError, setCreateError] = useState<string | null>(null);

  const handleCreate = async () => {
    const problem = validate(newForm) ?? (!newForm.slug.trim() ? "Lo slug è obbligatorio." : null);
    setCreateError(problem);
    if (problem) return;
    try {
      await createAddon.mutateAsync({
        ...toPayload(newForm),
        nome: newForm.nome.trim(),
        slug: newForm.slug.trim(),
      });
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
            Gestione add-on
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Il catalogo mostrato ai clienti nella pagina Abbonamento. Gli add-on non si
            eliminano: si disattivano.
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
          Nuovo add-on
        </Button>
      </div>

      {isPending ? (
        <div className="mt-6 space-y-5">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-64 w-full" />
          ))}
        </div>
      ) : isError ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (addons ?? []).length === 0 ? (
        <p className="mt-8 rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center text-sm text-slate-400">
          Nessun add-on nel catalogo: creane uno con «Nuovo add-on».
        </p>
      ) : (
        <div className="mt-6 space-y-5">
          {(addons ?? []).map((addon) => (
            <AddonEditor key={addon.id} addon={addon} />
          ))}
        </div>
      )}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Nuovo add-on"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Annulla
            </Button>
            <Button onClick={handleCreate} loading={createAddon.isPending}>
              Crea add-on
            </Button>
          </>
        }
      >
        <AddonFormFields form={newForm} setForm={setNewForm} isNew />
        {createError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {createError}
          </p>
        )}
      </Dialog>
    </div>
  );
}
