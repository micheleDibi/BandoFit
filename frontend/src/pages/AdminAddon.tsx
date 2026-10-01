import { Package, Plus } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Checkbox } from "../components/ui/Checkbox";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextField } from "../components/ui/Field";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Status } from "../components/ui/Status";
import { useToast } from "../components/ui/Toast";
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
      <Checkbox
        className="sm:col-span-2"
        label="Add-on attivo (visibile ai clienti nella pagina Abbonamento)"
        checked={form.is_active}
        onChange={(e) => setForm((f) => ({ ...f, is_active: e.target.checked }))}
      />
    </div>
  );
}

function AddonEditor({ addon }: { addon: Addon }) {
  const [form, setForm] = useState<AddonFormState>(() => toFormState(addon));
  const [validationError, setValidationError] = useState<string | null>(null);
  const updateAddon = useAdminUpdateAddon();
  const toast = useToast();
  const idTitolo = useId();

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const problem = validate(form);
    setValidationError(problem);
    if (problem) return;
    try {
      await updateAddon.mutateAsync({ addonId: addon.id, data: toPayload(form) });
      toast.mostra({ testo: `Add-on «${form.nome.trim()}» salvato` });
    } catch {
      // errore mostrato sotto
    }
  };

  // Un add-on per sezione: titolo con il filetto e lo stato in parole; il
  // pulsante pieno della pagina è «Nuovo add-on», qui «Salva» è secondario.
  return (
    <Card className="sm:p-6">
      <Section aria-labelledby={idTitolo}>
        <SectionHeader
          id={idTitolo}
          titolo={addon.nome}
          azione={
            form.is_active ? (
              <Status tono="aperto">Attivo</Status>
            ) : (
              <Status tono="chiuso">Disattivato</Status>
            )
          }
        />
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <AddonFormFields form={form} setForm={setForm} />
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="secondary" loading={updateAddon.isPending}>
              Salva add-on
            </Button>
            {(validationError || updateAddon.isError) && (
              <InlineError>{validationError ?? apiErrorMessage(updateAddon.error)}</InlineError>
            )}
          </div>
        </form>
      </Section>
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

  const apriCreazione = () => {
    setCreateError(null);
    setNewForm(EMPTY_FORM);
    setCreateOpen(true);
  };

  return (
    <Page variante="sezioni">
      <PageHeader
        titolo="Add-on"
        descrizione="Il catalogo mostrato ai clienti nella pagina Abbonamento. Gli add-on non si eliminano: si disattivano."
        area="admin"
        azioni={
          <Button variant="inverse" onClick={apriCreazione}>
            <Plus className="size-4" aria-hidden />
            Nuovo add-on
          </Button>
        }
      />

      {isPending ? (
        <div className="flex flex-col gap-6" aria-hidden>
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-64 w-full rounded-panel" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
      ) : (addons ?? []).length === 0 ? (
        <Card>
          <EmptyState
            title="Nessun add-on nel catalogo"
            description="Creane uno con «Nuovo add-on»."
            icon={Package}
            area="admin"
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-6">
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
            <Button type="button" variant="secondary" onClick={() => setCreateOpen(false)}>
              Annulla
            </Button>
            <Button type="button" onClick={handleCreate} loading={createAddon.isPending}>
              Crea add-on
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <AddonFormFields form={newForm} setForm={setNewForm} isNew />
          {createError && <Alert tono="errore">{createError}</Alert>}
        </div>
      </Dialog>
    </Page>
  );
}
