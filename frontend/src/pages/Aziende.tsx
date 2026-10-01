import { Plus } from "lucide-react";
import { useState } from "react";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Status } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Table, Td, Th } from "../components/ui/Table";
import { TextLink } from "../components/ui/TextLink";
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
          <Button type="button" variant="secondary" onClick={close}>
            Annulla
          </Button>
          <Button
            type="button"
            onClick={submit}
            loading={create.isPending}
            disabled={!ragioneSociale.trim() || !partitaIva.trim()}
          >
            Crea azienda
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p>
          Ragione sociale e partita IVA sono obbligatorie. Gli altri dati (import da P.IVA
          compreso) si aggiungono dopo, da «Dati azienda».
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
        {create.isError && <InlineError>{apiErrorMessage(create.error)}</InlineError>}
      </div>
    </Dialog>
  );
}

/** Conferma della rimozione (tavola Stati): la domanda nel titolo, le
 *  conseguenze nel testo, il pulsante distruttivo a destra. */
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
    <ConfirmDialog
      open={company !== null}
      titolo="Rimuovere l'azienda dalle aziende gestite?"
      conferma="Rimuovi"
      distruttiva
      inCorso={remove.isPending}
      onConferma={confirm}
      onAnnulla={() => {
        // Una sola finestra per tutte le righe: l'errore di una rimozione
        // fallita non deve ricomparire aprendo la conferma per un'altra azienda.
        remove.reset();
        onClose();
      }}
    >
      <p>
        <strong className="text-ink">{company?.ragione_sociale}</strong> esce dal selettore
        dell'azienda, dagli avvisi e dagli export. I suoi dati (bandi salvati, calendario,
        AI-check, dossier) restano conservati.
      </p>
      {remove.isError && <InlineError className="mt-3">{apiErrorMessage(remove.error)}</InlineError>}
    </ConfirmDialog>
  );
}

/** «Aziende gestite» (Advisor): le aziende clienti in tabella, con quella in
 *  uso segnata in parole; «Nuova azienda» è l'unico pulsante pieno. */
export default function Aziende() {
  const { data, isLoading, isError, refetch } = useCompanies();
  const { activeCompanyId, setActiveCompany } = useActiveCompany();
  const [creating, setCreating] = useState(false);
  const [toDelete, setToDelete] = useState<CompanySummary | null>(null);

  const aziende = data?.aziende ?? [];
  const atLimit = data ? data.usate >= data.max_aziende : false;

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Aziende gestite"
        descrizione={
          data
            ? `${data.usate} di ${data.max_aziende} aziende del tuo piano.`
            : "Le aziende clienti che gestisci, ciascuna con dati separati."
        }
        azioni={
          <Button type="button" onClick={() => setCreating(true)} disabled={atLimit}>
            <Plus className="size-4" aria-hidden />
            Nuova azienda
          </Button>
        }
      />

      {atLimit && aziende.length > 0 && (
        <Alert
          tono="attenzione"
          azione={
            <TextLink to="/app/abbonamento" className="text-small font-medium">
              Vedi i piani
            </TextLink>
          }
        >
          Hai raggiunto il numero massimo di aziende del tuo piano. Per gestirne altre rimuovine
          una o passa a un piano superiore.
        </Alert>
      )}

      {isLoading ? (
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : isError ? (
        <ErrorState
          title="Non siamo riusciti a caricare le aziende gestite."
          onRetry={() => refetch()}
        />
      ) : aziende.length === 0 ? (
        <EmptyState
          title="Non gestisci ancora nessuna azienda."
          description="Crea la tua prima azienda cliente per iniziare a gestirne i bandi in modo separato."
          action={
            <Button type="button" variant="secondary" onClick={() => setCreating(true)}>
              Crea la prima azienda
            </Button>
          }
        />
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Azienda</Th>
              <Th>Stato</Th>
              <Th>
                <span className="sr-only">Azioni</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {aziende.map((c) => {
              const isActive = c.id === activeCompanyId;
              return (
                <tr key={c.id}>
                  <Td>
                    <span className="block font-semibold text-ink">{c.ragione_sociale}</span>
                    <span className="block text-small text-ink-2 tabular-nums">
                      P.IVA {c.partita_iva}
                    </span>
                  </Td>
                  <Td>{isActive && <Status tono="aperto">In uso</Status>}</Td>
                  <Td className="text-right">
                    <div className="flex flex-wrap justify-end gap-2">
                      {!isActive && (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => setActiveCompany(c.id)}
                        >
                          Rendi attiva
                        </Button>
                      )}
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => setToDelete(c)}
                        aria-label={`Rimuovi ${c.ragione_sociale}`}
                      >
                        Rimuovi
                      </Button>
                    </div>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}

      <CreateCompanyDialog open={creating} onClose={() => setCreating(false)} />
      <DeleteCompanyDialog company={toDelete} onClose={() => setToDelete(null)} />
    </Page>
  );
}
