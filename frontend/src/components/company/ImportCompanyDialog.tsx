import { CheckCircle2, Loader2, Plus, Sparkles, TriangleAlert } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useConfirmImport, usePreviewImport } from "../../hooks/useCompanyDossier";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../../hooks/usePreferences";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { IMPORT_COPY } from "../../lib/copy";
import { isValidPartitaIva, normalizePartitaIva } from "../../lib/partitaIva";
import type { ImportPreview, ImportResult } from "../../types";
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

/** Il registro usa «Attiva», ma anche «Attiva» con code diversi: qualunque
 *  altro stato (cessata, sospesa, in liquidazione) merita un avviso. */
const isStatoAttivo = (stato: string | null) => !stato || /^attiv/i.test(stato);

type Step = "form" | "anteprima" | "conferma-annulla" | "esito";

interface ImportCompanyDialogProps {
  open: boolean;
  onClose: () => void;
  defaultPiva?: string | null;
}

function Riga({ etichetta, valore }: { etichetta: string; valore: string | null }) {
  if (!valore) return null;
  return (
    <div className="flex gap-3 py-1.5">
      <dt className="w-40 shrink-0 text-xs uppercase tracking-wide text-slate-400">{etichetta}</dt>
      <dd className="text-sm text-slate-700">{valore}</dd>
    </div>
  );
}

/** Import dei dati aziendali da openapi.it, in due fasi: il recupero (a
 *  pagamento) mostra un'anteprima di sola lettura, e solo la conferma scrive.
 *  Durante le chiamate la modale non è chiudibile: Esc o un click fuori
 *  butterebbero via l'esito di un'operazione già pagata. */
export function ImportCompanyDialog({ open, onClose, defaultPiva }: ImportCompanyDialogProps) {
  const previewImport = usePreviewImport();
  const confirmImport = useConfirmImport();
  const { data: preferences } = usePreferences();
  const savePreferences = useSavePreferences();

  const [step, setStep] = useState<Step>("form");
  const [piva, setPiva] = useState(defaultPiva ?? "");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addedAteco, setAddedAteco] = useState<number[]>([]);

  // Chiudibile tranne quando chiudere costerebbe qualcosa: durante una chiamata
  // (l'esito, già pagato, andrebbe perso) e sulla domanda di annullamento, dove
  // una X sceglierebbe «sì, annulla» al posto dell'utente. Chiudere
  // dall'anteprima è invece innocuo: il draft resta valido 30 minuti.
  const busy = previewImport.isPending || confirmImport.isPending;
  const dismissible = !busy && step !== "conferma-annulla";

  // Reset SOLO all'apertura: durante il primo import defaultPiva cambia
  // (viene creato il profilo aziendale) e non deve azzerare l'esito appena
  // mostrato.
  useEffect(() => {
    if (open) {
      setStep("form");
      setPiva(defaultPiva ?? "");
      setPreview(null);
      setResult(null);
      setError(null);
      setAddedAteco([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (previewImport.isPending) return; // doppio submit: la chiamata costa
    setError(null);
    const cleaned = normalizePartitaIva(piva);
    // Checksum locale: distingue il refuso dalla P.IVA assente dal registro,
    // e non spende credito per scoprirlo.
    if (!isValidPartitaIva(cleaned)) {
      setError(IMPORT_COPY.pivaInvalida);
      return;
    }
    try {
      setPreview(await previewImport.mutateAsync(cleaned));
      setStep("anteprima");
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const handleConfirm = async () => {
    if (confirmImport.isPending || !preview) return;
    setError(null);
    try {
      setResult(await confirmImport.mutateAsync(preview.azienda.partita_iva));
      setStep("esito");
    } catch (err) {
      setError(apiErrorMessage(err));
      // L'anteprima non è più spendibile: riportiamo l'utente al punto di
      // partenza invece di lasciargli un bottone che fallirà sempre.
      const code = apiErrorCode(err);
      if (code === "draft_not_found" || code === "draft_mismatch") {
        setPreview(null);
        setStep("form");
      }
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

  const titolo = previewImport.isPending
    ? IMPORT_COPY.titoloAttesa
    : step === "esito"
      ? IMPORT_COPY.titoloEsito
      : step === "conferma-annulla"
        ? IMPORT_COPY.annullaTitolo
        : step === "anteprima"
          ? IMPORT_COPY.titoloAnteprima
          : IMPORT_COPY.titoloForm;

  const erroreBox = error && (
    <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
      {error}
    </p>
  );

  const footer = () => {
    if (previewImport.isPending) return null; // niente da premere: si aspetta
    if (step === "esito") return <Button onClick={onClose}>Chiudi</Button>;
    if (step === "conferma-annulla")
      return (
        <>
          <Button variant="ghost" onClick={() => setStep("anteprima")}>
            {IMPORT_COPY.annullaRipensamento}
          </Button>
          <Button variant="danger" onClick={onClose}>
            {IMPORT_COPY.annullaConferma}
          </Button>
        </>
      );
    if (step === "anteprima")
      return (
        <>
          <Button
            variant="ghost"
            onClick={() => setStep("conferma-annulla")}
            disabled={confirmImport.isPending}
          >
            {IMPORT_COPY.annulla}
          </Button>
          <Button onClick={handleConfirm} loading={confirmImport.isPending}>
            {IMPORT_COPY.confermaImporta}
          </Button>
        </>
      );
    return (
      <>
        <Button variant="ghost" onClick={onClose}>
          {IMPORT_COPY.annulla}
        </Button>
        <Button type="submit" form="import-company-form">
          Importa i dati
        </Button>
      </>
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={titolo}
      dismissible={dismissible}
      size={step === "anteprima" ? "lg" : "md"}
      footer={footer()}
    >
      {previewImport.isPending ? (
        <div className="flex items-start gap-3 py-2">
          <Loader2 className="mt-0.5 size-5 shrink-0 animate-spin text-brand-600" aria-hidden />
          <p aria-live="polite">{IMPORT_COPY.attesa}</p>
        </div>
      ) : step === "conferma-annulla" ? (
        <p>{IMPORT_COPY.annullaTesto}</p>
      ) : step === "esito" && result ? (
        <div className="space-y-4">
          <p className="inline-flex items-start gap-2 text-sm text-emerald-700">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            {IMPORT_COPY.esitoImportato(result.dossier.anagrafica.denominazione ?? "—")}
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
      ) : step === "anteprima" && preview ? (
        <div className="space-y-4">
          <div>
            <p className="text-sm font-medium text-slate-900">
              {preview.azienda.ragione_sociale
                ? IMPORT_COPY.anteprimaTrovata(
                    preview.azienda.partita_iva,
                    preview.azienda.ragione_sociale,
                  )
                : IMPORT_COPY.anteprimaSenzaNome(preview.azienda.partita_iva)}
            </p>
            <p className="mt-1">{IMPORT_COPY.anteprimaIstruzioni}</p>
            {preview.reused && (
              <p className="mt-1 text-xs text-slate-400">{IMPORT_COPY.anteprimaRiusata}</p>
            )}
          </div>

          {!isStatoAttivo(preview.azienda.stato_impresa) && (
            <p
              className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800"
              role="alert"
            >
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              {IMPORT_COPY.anteprimaStatoAnomalo(preview.azienda.stato_impresa ?? "")}
            </p>
          )}

          <dl className="divide-y divide-slate-100 border-y border-slate-100">
            <Riga etichetta="Partita IVA" valore={preview.azienda.partita_iva} />
            <Riga etichetta="Codice fiscale" valore={preview.azienda.codice_fiscale} />
            <Riga etichetta="Forma giuridica" valore={preview.azienda.forma_giuridica} />
            <Riga etichetta="Stato" valore={preview.azienda.stato_impresa} />
            <Riga etichetta="Sede legale" valore={preview.azienda.sede} />
            <Riga etichetta="Regione" valore={preview.azienda.regione} />
            <Riga etichetta="ATECO" valore={preview.azienda.ateco} />
            <Riga
              etichetta="Legale rappresentante"
              valore={preview.azienda.legale_rappresentante}
            />
            <Riga
              etichetta="Cariche e soci"
              valore={
                preview.azienda.numero_persone > 0
                  ? `${preview.azienda.numero_persone} persone registrate`
                  : null
              }
            />
          </dl>

          {preview.autofill.applied.length > 0 && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                {IMPORT_COPY.campiCompilati}
              </p>
              <p className="mt-1 text-sm text-slate-700">
                {preview.autofill.applied.map(fieldLabel).join(", ")}
              </p>
            </div>
          )}

          {preview.autofill.conflicts.length > 0 && (
            <div className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              <p className="font-medium">{IMPORT_COPY.campiNonToccati}</p>
              <ul className="mt-1 space-y-0.5">
                {preview.autofill.conflicts.map((c) => (
                  <li key={c.campo}>
                    <span className="font-medium">{fieldLabel(c.campo)}</span>: hai «
                    {c.valore_attuale ?? "—"}», il registro riporta «{c.valore_certificato ?? "—"}
                    ».
                  </li>
                ))}
              </ul>
            </div>
          )}

          {preview.autofill.applied.length === 0 && preview.autofill.conflicts.length === 0 && (
            <p className="text-sm text-slate-500">{IMPORT_COPY.nessunCampo}</p>
          )}

          {erroreBox}
        </div>
      ) : (
        <form id="import-company-form" onSubmit={handleSubmit} className="space-y-4">
          <p>
            {IMPORT_COPY.introForm} I campi già compilati{" "}
            <strong>non verranno sovrascritti</strong>.
          </p>
          <p className="text-xs text-slate-400">{IMPORT_COPY.notaCosto}</p>
          <TextField
            label="Partita IVA"
            required
            inputMode="numeric"
            placeholder="11 cifre"
            value={piva}
            onChange={(e) => setPiva(e.target.value)}
          />
          {erroreBox}
        </form>
      )}
    </Dialog>
  );
}
