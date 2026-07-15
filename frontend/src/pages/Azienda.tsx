import { Download, FileDown, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useCompany } from "../hooks/useCompany";
import { useCompanyDossier } from "../hooks/useCompanyDossier";
import { CompanyCard } from "../components/company/CompanyCard";
import { ImportCompanyDialog } from "../components/company/ImportCompanyDialog";
import { DossierView } from "../components/company/dossier/DossierView";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { downloadFile } from "../lib/download";
import { formatDateNumeric } from "../lib/format";

/** Bottone di export PDF: scarica un blob autenticato con stato di caricamento
 *  e messaggio d'errore inline (con responseType blob l'errore del backend non
 *  è JSON leggibile, quindi il testo è generico). */
function ExportPdfButton({
  url,
  filename,
  label,
}: {
  url: string;
  filename: string;
  label: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="secondary"
        loading={busy}
        aria-busy={busy}
        onClick={async () => {
          setError(null);
          setBusy(true);
          try {
            await downloadFile(url, filename);
          } catch (e) {
            setError(e instanceof Error ? e.message : "Download non riuscito. Riprova.");
          } finally {
            setBusy(false);
          }
        }}
      >
        {!busy && <FileDown className="size-4" aria-hidden />}
        {busy ? "Esportazione…" : label}
      </Button>
      {error && (
        <span className="text-xs text-red-600" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

export default function Azienda() {
  const { data, isPending, isError, refetch } = useCompanyDossier();
  const { data: companyData } = useCompany();
  const [importOpen, setImportOpen] = useState(false);

  if (isPending) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="mx-auto max-w-5xl">
        <ErrorState
          message="Impossibile caricare il dossier aziendale."
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  const defaultPiva = companyData?.company?.partita_iva ?? null;
  const titolo = data.imported
    ? (data.dossier?.anagrafica.denominazione ?? "Azienda")
    : (companyData?.company?.ragione_sociale ?? "Azienda");
  // Stessa normalizzazione dello slug backend (`_slug`): NFKD + rimozione dei
  // diacritici, così il nome del file coincide con il Content-Disposition.
  const slug =
    (companyData?.company?.ragione_sociale ?? titolo)
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "azienda";

  const renderDossier = () => (
    <DossierView dossier={data.dossier!} people={data.people} />
  );

  return (
    <div className="mx-auto max-w-5xl">
      {/* Intestazione */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            {titolo}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Tutto ciò che riguarda la tua azienda in un unico posto: i dati che compili tu, il
            dossier certificato del Registro Imprese e i documenti ufficiali.
          </p>
        </div>
        {companyData?.company && (
          <ExportPdfButton
            url="/me/company/export/pdf"
            filename={`scheda-${slug}.pdf`}
            label="Esporta scheda PDF"
          />
        )}
      </div>

      {/* 1. Dati aziendali (compilati dall'utente: riepilogo + modifica) */}
      <div className="mt-6">
        <CompanyCard />
      </div>

      {/* 2. Dossier certificato (Registro Imprese) */}
      <section className="mt-10" aria-label="Dossier certificato">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-display text-xl font-bold tracking-tight text-slate-900">
              Dossier certificato
            </h2>
            {data.imported && data.dossier ? (
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                {data.dossier.anagrafica.stato && (
                  <Badge tone={data.dossier.anagrafica.stato === "Attiva" ? "emerald" : "amber"}>
                    {data.dossier.anagrafica.stato}
                  </Badge>
                )}
                {data.dossier.flags?.startup_innovativa && (
                  <Badge tone="brand">Startup innovativa</Badge>
                )}
                {data.sandbox && <Badge tone="amber">Dati di test</Badge>}
                <span className="text-xs text-slate-400">
                  Registro Imprese · aggiornato il {formatDateNumeric(data.fetched_at)}
                </span>
              </div>
            ) : (
              <p className="mt-1 text-sm text-slate-500">
                La visura completa dal Registro Imprese: anagrafica, ATECO, sedi, cariche e
                dati economici certificati.
              </p>
            )}
          </div>
          {data.imported && data.dossier && (
            <div className="flex flex-wrap items-start gap-2">
              <ExportPdfButton
                url="/me/company/dossier/pdf"
                filename={`dossier-${slug}.pdf`}
                label="Esporta dossier PDF"
              />
              {data.editable && (
                <Button variant="secondary" onClick={() => setImportOpen(true)}>
                  <RefreshCw className="size-4" aria-hidden />
                  Aggiorna
                </Button>
              )}
            </div>
          )}
        </div>

        {data.imported ? (
          renderDossier()
        ) : (
          <div className="mt-4">
            <EmptyState
              title="Nessun dato importato"
              description={
                data.editable
                  ? "Importa la visura completa della tua azienda dal Registro Imprese: anagrafica, ATECO, sedi, cariche e molto altro."
                  : "Il titolare non ha ancora importato i dati aziendali."
              }
              action={
                data.editable ? (
                  <Button onClick={() => setImportOpen(true)}>
                    <Download className="size-4" aria-hidden />
                    Importa da P.IVA
                  </Button>
                ) : undefined
              }
            />
          </div>
        )}
      </section>

      <p className="mt-8 pb-2 text-xs text-slate-400">
        Dati provenienti da fonti pubbliche (Registro Imprese) tramite openapi.it, per uso
        esclusivo del titolare e degli account collegati.
      </p>

      <ImportCompanyDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        defaultPiva={defaultPiva}
      />
    </div>
  );
}
