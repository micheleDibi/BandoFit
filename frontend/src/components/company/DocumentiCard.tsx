import { Clock3, Download, FileText, ScrollText } from "lucide-react";
import { useState } from "react";
import {
  downloadDocumentFile,
  useCompanyDocuments,
  useRequestDocument,
} from "../../hooks/useCompanyDocuments";
import { apiErrorMessage } from "../../lib/api";
import { formatDateNumeric } from "../../lib/format";
import type { CompanyDocument } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";

function StatusBadge({ doc }: { doc: CompanyDocument }) {
  if (doc.status === "ready") return <Badge tone="emerald">Pronta</Badge>;
  if (doc.status === "pending")
    return (
      <Badge tone="amber">
        <Clock3 className="size-3" aria-hidden />
        In lavorazione
      </Badge>
    );
  return <Badge tone="red">Errore</Badge>;
}

/** Documenti ufficiali dell'azienda: visura camerale dal Registro Imprese. */
export function DocumentiCard() {
  const { data, isPending } = useCompanyDocuments();
  const requestDocument = useRequestDocument();

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  if (isPending) {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="mt-4 h-16 w-full" />
      </Card>
    );
  }
  if (!data) return null;

  const hasPending = data.documents.some((d) => d.status === "pending");

  const handleRequest = async () => {
    setActionError(null);
    try {
      await requestDocument.mutateAsync();
      setConfirmOpen(false);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleDownload = async (doc: CompanyDocument) => {
    setActionError(null);
    setDownloadingId(doc.id);
    try {
      await downloadDocumentFile(doc.id, doc.file_name ?? "visura.pdf");
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setDownloadingId(null);
    }
  };

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
            <ScrollText className="size-4 text-brand-500" aria-hidden />
            Documenti ufficiali
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            La visura camerale ufficiale del Registro Imprese: PDF da allegare alle
            candidature, con oggetto sociale e poteri che alimenteranno l'AI-check.
          </p>
        </div>
        {data.editable && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => {
              setActionError(null);
              setConfirmOpen(true);
            }}
            disabled={hasPending}
            title={hasPending ? "C'è già una visura in lavorazione" : undefined}
          >
            <FileText className="size-4" aria-hidden />
            Richiedi visura
          </Button>
        )}
      </div>

      {actionError && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {actionError}
        </p>
      )}

      {data.documents.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">
          {data.editable
            ? "Nessun documento richiesto finora."
            : "Il titolare non ha ancora richiesto documenti."}
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100">
          {data.documents.map((doc) => (
            <li key={doc.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-slate-900">
                  Visura camerale ordinaria
                  {doc.sandbox && (
                    <span className="ml-2 align-middle">
                      <Badge tone="amber">Dati di test</Badge>
                    </span>
                  )}
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  Richiesta il {formatDateNumeric(doc.created_at)}
                  {doc.status === "ready" && doc.pages !== null && ` · ${doc.pages} pagine`}
                  {doc.status === "pending" &&
                    " · il Registro sta preparando il documento (di solito pochi secondi)"}
                  {doc.status === "error" && doc.error_detail && ` · ${doc.error_detail}`}
                </p>
              </div>
              <StatusBadge doc={doc} />
              {doc.status === "ready" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleDownload(doc)}
                  loading={downloadingId === doc.id}
                >
                  <Download className="size-3.5" aria-hidden />
                  Scarica PDF
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Richiedi la visura camerale"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Annulla
            </Button>
            <Button onClick={handleRequest} loading={requestDocument.isPending}>
              Richiedi ora
            </Button>
          </>
        }
      >
        <p>
          Richiediamo al Registro Imprese la <strong>visura ordinaria ufficiale</strong>{" "}
          della tua azienda tramite openapi.it. Il documento arriva di solito in pochi
          secondi e resta scaricabile da qui.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          L'operazione utilizza il credito del servizio dati: da circa 2,90 € + IVA
          (imprese individuali ed enti) a circa 4,90 € + IVA (società).
        </p>
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </Card>
  );
}
