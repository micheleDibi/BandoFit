import { CheckCircle2, Plus, Sparkles } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useImportCompany } from "../../hooks/useCompanyDossier";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../../hooks/usePreferences";
import { apiErrorMessage } from "../../lib/api";
import type { ImportResult } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { TextField } from "../ui/Field";

const FIELD_LABELS: Record<string, string> = {
  ragione_sociale: "Ragione sociale",
  partita_iva: "Partita IVA",
  codice_fiscale: "Codice fiscale",
  forma_giuridica: "Forma giuridica",
  ateco_id: "Codice ATECO",
  regione_id: "Regione",
  indirizzo: "Indirizzo",
  comune: "Comune",
  provincia: "Provincia",
  cap: "CAP",
  anno_fondazione: "Anno di fondazione",
  numero_dipendenti: "Numero dipendenti",
  classe_dimensionale: "Classe dimensionale",
  fascia_fatturato: "Fascia di fatturato",
  pec: "PEC",
  telefono: "Telefono",
  sito_web: "Sito web",
};

const fieldLabel = (campo: string) => FIELD_LABELS[campo] ?? campo;

interface ImportCompanyDialogProps {
  open: boolean;
  onClose: () => void;
  defaultPiva?: string | null;
}

/** Dialog di importazione della visura da openapi.it: conferma con nota
 * costo → esito con campi compilati, differenze e ATECO suggeriti. */
export function ImportCompanyDialog({ open, onClose, defaultPiva }: ImportCompanyDialogProps) {
  const importCompany = useImportCompany();
  const { data: preferences } = usePreferences();
  const savePreferences = useSavePreferences();

  const [piva, setPiva] = useState(defaultPiva ?? "");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addedAteco, setAddedAteco] = useState<number[]>([]);

  // Reset SOLO all'apertura: durante il primo import defaultPiva cambia
  // (viene creato il profilo aziendale) e non deve azzerare l'esito appena
  // mostrato.
  useEffect(() => {
    if (open) {
      setPiva(defaultPiva ?? "");
      setResult(null);
      setError(null);
      setAddedAteco([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const cleaned = piva.trim().toUpperCase().replace(/^IT/, "").replace(/\s/g, "");
    if (!/^\d{11}$/.test(cleaned)) {
      setError("La partita IVA deve essere composta da 11 cifre.");
      return;
    }
    try {
      setResult(await importCompany.mutateAsync(cleaned));
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const handleAddAteco = async (id: number) => {
    const current = preferences ?? EMPTY_PREFERENCES;
    if (current.codici_ateco.includes(id) || addedAteco.includes(id)) return;
    try {
      await savePreferences.mutateAsync({
        ...current,
        codici_ateco: [...current.codici_ateco, id],
      });
      setAddedAteco((prev) => [...prev, id]);
    } catch {
      // errore non bloccante: l'utente può riprovare dalle preferenze
    }
  };

  const suggested = (result?.suggestions.codici_ateco ?? []).filter(
    (s) => !(preferences?.codici_ateco ?? []).includes(s.id) || addedAteco.includes(s.id),
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={result ? "Dati importati" : "Importa da P.IVA"}
      footer={
        result ? (
          <Button onClick={onClose}>Chiudi</Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={importCompany.isPending}>
              Annulla
            </Button>
            <Button
              type="submit"
              form="import-company-form"
              loading={importCompany.isPending}
            >
              Importa i dati
            </Button>
          </>
        )
      }
    >
      {result ? (
        <div className="space-y-4">
          <p className="inline-flex items-start gap-2 text-sm text-emerald-700">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            Dati ufficiali di «{result.dossier.anagrafica.denominazione}» importati dal
            Registro Imprese.
          </p>

          {result.autofill.applied.length > 0 && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                Campi compilati automaticamente
              </p>
              <p className="mt-1 text-sm text-slate-700">
                {result.autofill.applied.map(fieldLabel).join(", ")}
              </p>
            </div>
          )}

          {result.autofill.conflicts.length > 0 && (
            <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <p className="font-medium">Alcuni dati certificati differiscono dai tuoi:</p>
              <ul className="mt-1 space-y-0.5">
                {result.autofill.conflicts.map((c) => (
                  <li key={c.campo}>
                    <span className="font-medium">{fieldLabel(c.campo)}</span>: hai «
                    {c.valore_attuale ?? "—"}», il registro riporta «{c.valore_certificato ?? "—"}
                    ». I tuoi valori non sono stati toccati.
                  </li>
                ))}
              </ul>
            </div>
          )}

          {suggested.length > 0 && (
            <div>
              <p className="inline-flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">
                <Sparkles className="size-3.5 text-brand-500" aria-hidden />
                ATECO secondari trovati
              </p>
              <p className="mt-1 text-sm text-slate-500">
                L'azienda opera anche in altri settori: aggiungili alle tue preferenze per
                vederli tra i bandi consigliati.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {suggested.map((s) => {
                  const added = addedAteco.includes(s.id);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => handleAddAteco(s.id)}
                      disabled={added || savePreferences.isPending}
                      className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700 transition-colors hover:bg-brand-100 disabled:cursor-default disabled:opacity-70"
                    >
                      {added ? (
                        <CheckCircle2 className="size-3.5" aria-hidden />
                      ) : (
                        <Plus className="size-3.5" aria-hidden />
                      )}
                      {s.codice} {s.descrizione ? `— ${s.descrizione}` : ""}
                      {added && " ✓ aggiunto"}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <p className="text-sm">
            <Link
              to="/app/azienda"
              onClick={onClose}
              className="font-medium text-brand-600 hover:text-brand-700"
            >
              Vedi il dossier completo →
            </Link>
          </p>
        </div>
      ) : (
        <form id="import-company-form" onSubmit={handleSubmit} className="space-y-4">
          <p>
            Recuperiamo i dati ufficiali della tua azienda dal Registro Imprese tramite
            openapi.it: anagrafica, ATECO, sede e unità locali, cariche, dipendenti e
            altro. I campi già compilati <strong>non verranno sovrascritti</strong>.
          </p>
          <p className="text-xs text-slate-400">
            L'operazione utilizza il credito del servizio dati (circa 0,30 € + IVA per
            importazione) e può richiedere fino a un minuto.
          </p>
          <TextField
            label="Partita IVA"
            required
            inputMode="numeric"
            placeholder="11 cifre"
            value={piva}
            onChange={(e) => setPiva(e.target.value)}
          />
          {error && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {error}
            </p>
          )}
        </form>
      )}
    </Dialog>
  );
}
