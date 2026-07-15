import { Building2, Check, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useActiveCompany } from "../hooks/useActiveCompany";
import { useCompanies, useCreateCompany, useDeleteCompany } from "../hooks/useCompanies";
import { apiErrorMessage } from "../lib/api";
import { isValidPartitaIva, normalizePartitaIva } from "../lib/partitaIva";
import type { CompanySummary } from "../types";

function CreateCompanyDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const create = useCreateCompany();
  const [ragioneSociale, setRagioneSociale] = useState("");
  const [partitaIva, setPartitaIva] = useState("");
  const [pivaError, setPivaError] = useState<string | undefined>();

  const reset = () => {
    setRagioneSociale("");
    setPartitaIva("");
    setPivaError(undefined);
    create.reset();
  };

  const submit = () => {
    const piva = normalizePartitaIva(partitaIva);
    if (!isValidPartitaIva(piva)) {
      setPivaError("La partita IVA deve essere di 11 cifre valide.");
      return;
    }
    setPivaError(undefined);
    create.mutate(
      { ragione_sociale: ragioneSociale.trim(), partita_iva: piva },
      {
        onSuccess: () => {
          reset();
          onClose();
        },
      },
    );
  };

  const close = () => {
    reset();
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Nuova azienda"
      footer={
        <>
          <Button variant="secondary" onClick={close}>
            Annulla
          </Button>
          <Button
            onClick={submit}
            loading={create.isPending}
            disabled={!ragioneSociale.trim() || !partitaIva.trim()}
          >
            Crea azienda
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p>
          Ragione sociale e partita IVA sono obbligatorie. Gli altri dati (import da
          P.IVA compreso) si aggiungono dopo, da «Dati azienda».
        </p>
        <TextField
          label="Ragione sociale"
          required
          value={ragioneSociale}
          onChange={(e) => setRagioneSociale(e.target.value)}
          maxLength={300}
          autoFocus
        />
        <TextField
          label="Partita IVA"
          required
          value={partitaIva}
          onChange={(e) => setPartitaIva(e.target.value)}
          error={pivaError}
          placeholder="01234567890"
          inputMode="numeric"
        />
        {create.isError && (
          <p className="text-sm text-red-600" role="alert">
            {apiErrorMessage(create.error)}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function DeleteCompanyDialog({
  company,
  onClose,
}: {
  company: CompanySummary | null;
  onClose: () => void;
}) {
  const remove = useDeleteCompany();

  const confirm = () => {
    if (!company) return;
    remove.mutate(company.id, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={company !== null}
      onClose={onClose}
      title="Rimuovi azienda"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Annulla
          </Button>
          <Button variant="danger" onClick={confirm} loading={remove.isPending}>
            Rimuovi
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p>
          Vuoi rimuovere <strong className="text-slate-900">{company?.ragione_sociale}</strong>?
          I suoi dati (bandi salvati, calendario, AI-check, dossier) restano conservati ma
          l'azienda esce dallo switcher, dagli alert e dagli export.
        </p>
        {remove.isError && (
          <p className="text-sm text-red-600" role="alert">
            {apiErrorMessage(remove.error)}
          </p>
        )}
      </div>
    </Dialog>
  );
}

function CompanyCard({ company }: { company: CompanySummary }) {
  const { activeCompanyId, setActiveCompany } = useActiveCompany();
  const [toDelete, setToDelete] = useState<CompanySummary | null>(null);
  const isActive = company.id === activeCompanyId;

  return (
    <div className="flex flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-brand-50 p-2 text-brand-500">
          <Building2 className="size-5" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-display text-base font-semibold text-slate-900">
              {company.ragione_sociale}
            </h3>
            {isActive && <Badge tone="brand">Attiva</Badge>}
          </div>
          <p className="mt-0.5 text-sm text-slate-500">P.IVA {company.partita_iva}</p>
        </div>
      </div>
      <div className="mt-4 flex items-center justify-between gap-2 border-t border-slate-100 pt-3">
        {isActive ? (
          <span className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-600">
            <Check className="size-4" aria-hidden />
            In uso
          </span>
        ) : (
          <Button variant="secondary" size="sm" onClick={() => setActiveCompany(company.id)}>
            Rendi attiva
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          className="text-red-600 hover:bg-red-50"
          onClick={() => setToDelete(company)}
          aria-label={`Rimuovi ${company.ragione_sociale}`}
        >
          <Trash2 className="size-4" aria-hidden />
          Rimuovi
        </Button>
      </div>
      <DeleteCompanyDialog company={toDelete} onClose={() => setToDelete(null)} />
    </div>
  );
}

export default function Aziende() {
  const { data, isLoading, isError, refetch } = useCompanies();
  const [creating, setCreating] = useState(false);

  const aziende = data?.aziende ?? [];
  const atLimit = data ? data.usate >= data.max_aziende : false;

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold text-slate-900">Aziende gestite</h1>
          <p className="mt-1 text-sm text-slate-500">
            {data
              ? `${data.usate} di ${data.max_aziende} aziende del tuo piano.`
              : "Le aziende clienti che gestisci, ciascuna con dati separati."}
          </p>
        </div>
        <Button onClick={() => setCreating(true)} disabled={atLimit} title={atLimit ? "Hai raggiunto il limite del piano" : undefined}>
          <Plus className="size-4" aria-hidden />
          Nuova azienda
        </Button>
      </div>

      {atLimit && aziende.length > 0 && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Hai raggiunto il numero massimo di aziende del tuo piano. Per gestirne altre
          rimuovine una o passa a un piano superiore.
        </p>
      )}

      <div className="mt-6">
        {isLoading ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="h-36" />
            <Skeleton className="h-36" />
          </div>
        ) : isError ? (
          <ErrorState onRetry={() => refetch()} />
        ) : aziende.length === 0 ? (
          <EmptyState
            title="Nessuna azienda"
            description="Crea la tua prima azienda cliente per iniziare a gestirne i bandi in modo separato."
            action={
              <Button onClick={() => setCreating(true)}>
                <Plus className="size-4" aria-hidden />
                Crea la prima azienda
              </Button>
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {aziende.map((c) => (
              <CompanyCard key={c.id} company={c} />
            ))}
          </div>
        )}
      </div>

      <CreateCompanyDialog open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}
