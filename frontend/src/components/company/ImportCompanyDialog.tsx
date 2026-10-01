import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, TriangleAlert } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useConfirmImport, usePreviewImport } from "../../hooks/useCompanyDossier";
import { useFunzioni } from "../../hooks/useFunzioni";
import { PARTNER_PROFILE_ROOT, usePartnerProfile } from "../../hooks/usePartnerProfile";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../../hooks/usePreferences";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { intervalloAnni } from "../../lib/bilanci";
import { BILANCI_COPY, IMPORT_COPY, PARTNER_COPY } from "../../lib/copy";
import { isValidPartitaIva, normalizePartitaIva } from "../../lib/partitaIva";
import type {
  ImportPreview,
  ImportPreviewBilanci,
  ImportResult,
  MotivoBilanci,
} from "../../types";
import { ConsensoPartnerDialog } from "../partenariati/ConsensoPartnerDialog";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { TextField } from "../ui/Field";
import { Spinner } from "../ui/Spinner";
import { Skeleton } from "../ui/states";
import { Status } from "../ui/Status";
import { TextLink } from "../ui/TextLink";

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

/** `partner` (modulo partenariati): passo facoltativo DOPO l'esito, per
 *  comparire come partner; c'è solo se il titolare può farlo adesso. */
type Step = "form" | "anteprima" | "conferma-annulla" | "esito" | "partner";

interface ImportCompanyDialogProps {
  open: boolean;
  onClose: () => void;
  defaultPiva?: string | null;
}

function Riga({ etichetta, valore }: { etichetta: string; valore: string | null }) {
  if (!valore) return null;
  return (
    <div className="flex gap-3 py-1.5">
      <dt className="w-40 shrink-0 text-small text-ink-3">{etichetta}</dt>
      <dd className="text-body text-ink">{valore}</dd>
    </div>
  );
}

/** Perché manca lo storico e, se si può, come recuperarlo. `trovati`: almeno
 *  un esercizio arriva comunque (dalla visura). A storico spento nessun
 *  invito, e nessuna nota se lo storico non è stato nemmeno chiesto. */
function notaStorico(motivo: MotivoBilanci, trovati: boolean, storicoAttivo: boolean): string {
  if (motivo === "nessun_bilancio") {
    return trovati ? BILANCI_COPY.nessunAltroBilancio : BILANCI_COPY.motivi[motivo];
  }
  if (motivo === "forma_senza_bilancio") return BILANCI_COPY.motivi[motivo];
  if (!storicoAttivo) return motivo === "non_richiesto" ? "" : BILANCI_COPY.motivi[motivo];
  if (motivo === "piva_diversa") return `${BILANCI_COPY.motivi[motivo]} ${BILANCI_COPY.correggiPiva}`;
  const invito = trovati ? IMPORT_COPY.bilanciStoricoRecuperabile : IMPORT_COPY.bilanciRecuperabili;
  return `${BILANCI_COPY.motivi[motivo]} ${invito}`;
}

/** Bilanci pluriennali recuperati insieme all'anteprima. Non bloccano mai
 *  l'import: se lo storico manca, il titolare lo recupera poi dalla pagina
 *  Azienda (tranne quando la forma giuridica non prevede il deposito o il
 *  Registro Imprese non ha bilanci, o lo storico è spento). */
function BloccoBilanci({
  bilanci,
  storicoAttivo,
}: {
  bilanci: ImportPreviewBilanci | undefined;
  storicoAttivo: boolean;
}) {
  const anni = bilanci?.anni ?? [];
  const trovati = bilanci?.stato === "disponibili" && anni.length > 0;
  // null = storico recuperato; un blocco assente vale «non richiesto».
  const motivo = bilanci ? bilanci.motivo : "non_richiesto";
  const nota = motivo ? notaStorico(motivo, trovati, storicoAttivo) : "";
  return (
    <div>
      <p className="text-small font-medium text-ink-3">
        {IMPORT_COPY.bilanciTitolo}
      </p>
      <div className="mt-1.5">
        <Status tono={trovati ? "aperto" : "neutro"}>
          {trovati
            ? IMPORT_COPY.bilanciTrovati(anni.length, intervalloAnni(anni))
            : IMPORT_COPY.bilanciNonTrovati}
        </Status>
      </div>
      {(trovati || nota) && (
        <p className="mt-1.5 text-body text-ink-2">
          {trovati && IMPORT_COPY.bilanciTrovatiNota}
          {trovati && nota && " "}
          {nota}
        </p>
      )}
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
  const queryClient = useQueryClient();
  const { partenariatiAttivo, bilanciStoricoAttivo } = useFunzioni();

  const [step, setStep] = useState<Step>("form");
  const [piva, setPiva] = useState(defaultPiva ?? "");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addedAteco, setAddedAteco] = useState<number[]>([]);
  const [partnerAttivato, setPartnerAttivato] = useState(false);

  // Profilo partner: serve solo dall'esito in poi (a modulo acceso), per
  // sapere se proporre il passo del consenso.
  const partner = usePartnerProfile(
    open && partenariatiAttivo && (step === "esito" || step === "partner"),
  );
  const profiloPartner = partner.data;
  // Si propone solo a chi può davvero attivarla adesso: titolare, profilo non
  // visibile né sospeso, identità verificata dal registro appena importato.
  // Durante il refetch dopo la conferma i dati in cache sono quelli di prima.
  const passoPartner =
    partenariatiAttivo &&
    !!result?.company.editable &&
    !!profiloPartner &&
    !partner.isFetching &&
    profiloPartner.editable &&
    !profiloPartner.visibile &&
    !profiloPartner.sospeso &&
    profiloPartner.identita.verificata;

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
      setPartnerAttivato(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleSubmit = async (e?: FormEvent) => {
    e?.preventDefault();
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
      // Anche `piva_diversa_da_importata` (409: la P.IVA è già legata a
      // un'altra azienda) e `limite_giornaliero_openapi` (429) arrivano con un
      // messaggio del server già pensato per l'utente: lo mostriamo com'è.
      setError(apiErrorMessage(err));
    }
  };

  const handleConfirm = async () => {
    if (confirmImport.isPending || !preview) return;
    setError(null);
    try {
      setResult(await confirmImport.mutateAsync(preview.azienda.partita_iva));
      // L'import cambia l'identità verificata del profilo partner.
      queryClient.invalidateQueries({ queryKey: PARTNER_PROFILE_ROOT });
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
    : step === "partner"
      ? PARTNER_COPY.consensoTitolo
      : step === "esito"
        ? IMPORT_COPY.titoloEsito
        : step === "conferma-annulla"
          ? IMPORT_COPY.annullaTitolo
          : step === "anteprima"
            ? IMPORT_COPY.titoloAnteprima
            : IMPORT_COPY.titoloForm;

  const erroreBox = error && (
    <p className="rounded-control border border-danger-line bg-danger-soft px-3 py-2 text-small text-danger" role="alert">
      {error}
    </p>
  );

  const footer = () => {
    if (previewImport.isPending) return null; // niente da premere: si aspetta
    if (step === "partner")
      return partnerAttivato ? <Button onClick={onClose}>Chiudi</Button> : null;
    if (step === "esito")
      return passoPartner ? (
        <>
          <Button variant="ghost" onClick={onClose}>
            Chiudi
          </Button>
          <Button onClick={() => setStep("partner")}>{PARTNER_COPY.continua}</Button>
        </>
      ) : (
        <Button onClick={onClose}>Chiudi</Button>
      );
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
        {/* onClick esplicito, non l'associazione `form=`: quest'ultima, con il
            bottone fuori dal form e dentro un <dialog> modale, in Safari valida
            per errore i campi obbligatori (inerti) del form azienda sottostante
            e annulla il submit senza mostrare nulla. */}
        <Button type="button" onClick={() => handleSubmit()}>
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
      size={step === "anteprima" || step === "partner" ? "lg" : "md"}
      footer={footer()}
    >
      {previewImport.isPending ? (
        <div className="flex items-start gap-3 py-2">
          <Spinner className="mt-0.5 text-accent" />
          <p aria-live="polite">{IMPORT_COPY.attesa}</p>
        </div>
      ) : step === "conferma-annulla" ? (
        <p>{IMPORT_COPY.annullaTesto}</p>
      ) : step === "partner" ? (
        !profiloPartner ? (
          <Skeleton className="h-40 w-full" />
        ) : partnerAttivato ? (
          <p className="inline-flex items-start gap-2 text-body text-fit-ink" role="status">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            {PARTNER_COPY.attivato}
          </p>
        ) : (
          <ConsensoPartnerDialog
            inline
            profilo={profiloPartner}
            origine="import_piva"
            onClose={onClose}
            onAttivato={() => setPartnerAttivato(true)}
            etichettaAnnulla={PARTNER_COPY.nonOra}
          />
        )
      ) : step === "esito" && result ? (
        <div className="space-y-4">
          <p className="inline-flex items-start gap-2 text-body text-fit-ink">
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            {IMPORT_COPY.esitoImportato(result.dossier.anagrafica.denominazione ?? "—")}
          </p>

          {result.autofill.applied.length > 0 && (
            <div>
              <p className="text-small font-medium text-ink-3">
                Campi compilati automaticamente
              </p>
              <p className="mt-1 text-body text-ink">
                {result.autofill.applied.map(fieldLabel).join(", ")}
              </p>
            </div>
          )}

          {suggested.length > 0 && (
            <div>
              <p className="text-small font-medium text-ink-3">ATECO secondari trovati</p>
              <p className="mt-1 text-body text-ink-2">
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
                      className="inline-flex cursor-pointer items-center gap-1.5 rounded-pill border border-accent-line bg-accent-soft px-3 py-1 text-small font-medium text-accent-hover transition-colors hover:bg-accent-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-default disabled:opacity-70"
                    >
                      {added ? (
                        <CheckCircle2 className="size-4" aria-hidden />
                      ) : (
                        <Plus className="size-4" aria-hidden />
                      )}
                      {s.codice} {s.descrizione ? `— ${s.descrizione}` : ""}
                      {added && " (aggiunto)"}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          <p>
            <TextLink to="/app/azienda?tab=dossier" onClick={onClose} className="font-medium">
              Vedi il dossier completo
            </TextLink>
          </p>

          {passoPartner && (
            <Alert tono="info">{PARTNER_COPY.passoImport}</Alert>
          )}
        </div>
      ) : step === "anteprima" && preview ? (
        <div className="space-y-4">
          <div>
            <p className="text-body font-medium text-ink">
              {preview.azienda.ragione_sociale
                ? IMPORT_COPY.anteprimaTrovata(
                    preview.azienda.partita_iva,
                    preview.azienda.ragione_sociale,
                  )
                : IMPORT_COPY.anteprimaSenzaNome(preview.azienda.partita_iva)}
            </p>
            <p className="mt-1">{IMPORT_COPY.anteprimaIstruzioni}</p>
            {preview.reused && (
              <p className="mt-1 text-small text-ink-3">{IMPORT_COPY.anteprimaRiusata}</p>
            )}
          </div>

          {!isStatoAttivo(preview.azienda.stato_impresa) && (
            <p
              className="flex items-start gap-2 rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-small text-ink"
              role="alert"
            >
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
              {IMPORT_COPY.anteprimaStatoAnomalo(preview.azienda.stato_impresa ?? "")}
            </p>
          )}

          <dl className="divide-y divide-line border-y border-line">
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

          <BloccoBilanci bilanci={preview.bilanci} storicoAttivo={bilanciStoricoAttivo} />

          {preview.autofill.applied.length > 0 && (
            <div>
              <p className="text-small font-medium text-ink-3">
                {IMPORT_COPY.campiCompilati}
              </p>
              <p className="mt-1 text-body text-ink">
                {preview.autofill.applied.map(fieldLabel).join(", ")}
              </p>
            </div>
          )}

          {preview.autofill.conflicts.length > 0 && (
            <div className="rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-small text-ink">
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
            <p className="text-body text-ink-2">{IMPORT_COPY.nessunCampo}</p>
          )}

          {erroreBox}
        </div>
      ) : (
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          <p>
            {bilanciStoricoAttivo ? IMPORT_COPY.introForm : IMPORT_COPY.introFormSenzaStorico} I
            campi già compilati{" "}
            <strong>non verranno sovrascritti</strong>.
          </p>
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
