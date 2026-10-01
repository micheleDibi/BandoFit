import { ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Button, LinkButton } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { SelectField, TextField } from "../components/ui/Field";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { ErrorState, Skeleton } from "../components/ui/states";
import { Status } from "../components/ui/Status";
import { useToast } from "../components/ui/Toast";
import { useJobPositions } from "../hooks/useJobPositions";
import { useMe, useUpdateProfile, useVerifyCf } from "../hooks/useMe";
import { apiErrorMessage } from "../lib/api";
import { isValidTelefono, normalizeTelefono } from "../lib/telefono";

/** «Profilo»: i dati personali dell'account e, per il titolare, il rimando agli
 *  account collegati. Dati azienda, preferenze e abbonamento hanno le loro voci
 *  di menu: qui non si ripetono. */
export default function Profilo() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const {
    data: positions,
    isError: positionsError,
    refetch: refetchPositions,
  } = useJobPositions();
  const updateProfile = useUpdateProfile();
  const verifyCf = useVerifyCf();
  const { mostra } = useToast();

  const [form, setForm] = useState({
    nome: "",
    cognome: "",
    azienda: "",
    telefono: "",
    codice_fiscale: "",
  });
  const [positionId, setPositionId] = useState<number | null>(null);
  const [posizioneAltro, setPosizioneAltro] = useState("");
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [cfError, setCfError] = useState<string | null>(null);
  const [telefonoError, setTelefonoError] = useState<string | null>(null);

  useEffect(() => {
    if (me) {
      setForm({
        nome: me.profile.nome ?? "",
        cognome: me.profile.cognome ?? "",
        azienda: me.profile.azienda ?? "",
        telefono: me.profile.telefono ?? "",
        codice_fiscale: me.profile.codice_fiscale ?? "",
      });
      setPositionId(me.profile.job_position_id);
      setPosizioneAltro(me.profile.job_position_altro ?? "");
    }
  }, [me]);

  if (isPending) {
    return (
      <Page variante="sezioni">
        <PageHeader titolo="Profilo" />
        <div className="flex flex-col gap-6" aria-hidden>
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </Page>
    );
  }
  if (isError || !me) {
    return (
      <Page variante="sezioni">
        <PageHeader titolo="Profilo" />
        <ErrorState
          title="Non siamo riusciti a caricare il tuo profilo."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      </Page>
    );
  }

  const cfInput = form.codice_fiscale.trim().toUpperCase();
  const cfVerified =
    !!me?.profile.cf_verified_at && cfInput === (me.profile.codice_fiscale ?? "");
  const cfDaVerificare = !cfVerified && cfInput.length === 16;

  // Una posizione DISATTIVATA dopo la scelta resta visibile a chi la aveva:
  // si aggiunge in testa alle opzioni se non è più nel catalogo attivo.
  const positionOptions = (positions ?? []).map((p) => ({ id: p.id, label: p.nome }));
  const currentPosition = me.profile.job_position;
  if (currentPosition && !positionOptions.some((o) => o.id === currentPosition.id)) {
    positionOptions.unshift({ id: currentPosition.id, label: currentPosition.nome });
  }
  const selectedSlug =
    positions?.find((p) => p.id === positionId)?.slug ??
    (currentPosition && currentPosition.id === positionId ? currentPosition.slug : null);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    setCfError(null);
    setTelefonoError(null);
    // Validazione locale: un CF incompleto non deve bloccare il salvataggio
    // degli altri campi con un errore generico del backend.
    if (cfInput && cfInput.length !== 16) {
      setCfError("Il codice fiscale deve avere 16 caratteri (o lascialo vuoto).");
      return;
    }

    const payload: Parameters<typeof updateProfile.mutateAsync>[0] = {
      nome: form.nome.trim(),
      cognome: form.cognome.trim(),
      azienda: form.azienda.trim(),
      codice_fiscale: cfInput || null,
    };

    // Telefono: la chiave viaggia solo se il valore è cambiato, così un
    // numero pre-esistente non in E.164 non blocca il resto del form.
    const telefonoRaw = form.telefono.trim();
    if (telefonoRaw !== (me.profile.telefono ?? "")) {
      if (telefonoRaw === "") {
        payload.telefono = null;
      } else {
        const normalized = normalizeTelefono(telefonoRaw);
        if (!isValidTelefono(normalized)) {
          setTelefonoError("Inserisci un numero di telefono valido (es. 347 1234567).");
          return;
        }
        payload.telefono = normalized;
      }
    }

    // Posizione e testo «Altro»: stesse regole (chiave omessa se invariata).
    const altroTrim = posizioneAltro.trim();
    const posizioneCambiata = positionId !== me.profile.job_position_id;
    if (posizioneCambiata) {
      payload.job_position_id = positionId;
      payload.job_position_altro = selectedSlug === "altro" ? altroTrim || null : null;
    } else if (altroTrim !== (me.profile.job_position_altro ?? "")) {
      payload.job_position_altro = altroTrim || null;
    }

    try {
      await updateProfile.mutateAsync(payload);
      mostra({ testo: "Profilo salvato" });
    } catch {
      // errore mostrato sotto il pulsante (updateProfile.isError)
    }
  };

  const apriVerifica = () => {
    verifyCf.reset();
    setVerifyOpen(true);
  };

  const handleVerifyCf = async () => {
    try {
      await verifyCf.mutateAsync(cfInput);
      setVerifyOpen(false);
      mostra({ testo: "Codice fiscale verificato" });
    } catch {
      // errore mostrato nella finestra
    }
  };

  return (
    <Page variante="sezioni">
      <PageHeader titolo="Profilo" descrizione={me.profile.email} />

      <Section aria-labelledby="profilo-dati-titolo">
        <SectionHeader id="profilo-dati-titolo" titolo="Dati personali" />
        <form onSubmit={handleSave} className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Nome"
            value={form.nome}
            onChange={(e) => setForm((f) => ({ ...f, nome: e.target.value }))}
            autoComplete="given-name"
          />
          <TextField
            label="Cognome"
            value={form.cognome}
            onChange={(e) => setForm((f) => ({ ...f, cognome: e.target.value }))}
            autoComplete="family-name"
          />
          <TextField
            label="Azienda"
            value={form.azienda}
            onChange={(e) => setForm((f) => ({ ...f, azienda: e.target.value }))}
            autoComplete="organization"
          />
          <TextField
            label="Telefono"
            type="tel"
            value={form.telefono}
            onChange={(e) => setForm((f) => ({ ...f, telefono: e.target.value }))}
            autoComplete="tel"
            error={telefonoError ?? undefined}
            placeholder="347 1234567"
            helper="Prefisso +39 automatico"
          />
          <div className="flex flex-col gap-1.5">
            <SelectField
              label="Posizione in azienda"
              value={positionId === null ? "" : String(positionId)}
              onChange={(e) => setPositionId(e.target.value ? Number(e.target.value) : null)}
              disabled={!positions && positionOptions.length === 0}
            >
              <option value="">Scegli la tua posizione</option>
              {positionOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </SelectField>
            {positionsError && (
              <div className="flex flex-wrap items-center gap-x-2">
                <InlineError>Impossibile caricare le posizioni.</InlineError>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => refetchPositions()}
                >
                  Riprova
                </Button>
              </div>
            )}
          </div>
          {selectedSlug === "altro" && (
            <TextField
              label="Specifica la posizione"
              value={posizioneAltro}
              onChange={(e) => setPosizioneAltro(e.target.value)}
              helper="Facoltativa"
              maxLength={100}
            />
          )}
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-full sm:w-80">
                <TextField
                  label="Codice fiscale"
                  placeholder="16 caratteri"
                  value={form.codice_fiscale}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, codice_fiscale: e.target.value.toUpperCase() }))
                  }
                  autoComplete="off"
                  aria-invalid={!!cfError}
                  aria-describedby={
                    cfError ? "profilo-cf-errore" : cfDaVerificare ? "profilo-cf-aiuto" : undefined
                  }
                />
              </div>
              {cfVerified ? (
                <Status tono="aperto" className="h-10" role="status">
                  Verificato
                </Status>
              ) : (
                cfDaVerificare && (
                  <Button type="button" variant="secondary" onClick={apriVerifica}>
                    <ShieldCheck className="size-4" aria-hidden />
                    Verifica
                  </Button>
                )
              )}
            </div>
            {cfError ? (
              <InlineError id="profilo-cf-errore">{cfError}</InlineError>
            ) : (
              cfDaVerificare && (
                <p id="profilo-cf-aiuto" className="text-small text-ink-3">
                  Da verificare: conferma il codice fiscale all'Anagrafe Tributaria.
                </p>
              )
            )}
          </div>
          <div className="flex flex-col gap-3 sm:col-span-2">
            <div>
              <Button type="submit" loading={updateProfile.isPending}>
                Salva le modifiche
              </Button>
            </div>
            {updateProfile.isError && (
              <InlineError>{apiErrorMessage(updateProfile.error)}</InlineError>
            )}
          </div>
        </form>
      </Section>

      {/* La GESTIONE degli account collegati vive nella loro pagina; `id="collegati"`
          resta come bersaglio dei vecchi link (che `RedirectLegacy` porta comunque
          a /app/collegati). */}
      {me.family?.role === "parent" && (
        <Section id="collegati" aria-labelledby="profilo-collegati-titolo" className="scroll-mt-16">
          <SectionHeader id="profilo-collegati-titolo" titolo="Account collegati" />
          <p className="text-body text-ink-2">
            {me.family.used ?? 1} di {me.family.limit ?? 1} account usati (incluso il tuo).
            Inviti, aziende visibili e budget AI-check si gestiscono dalla pagina dedicata.
          </p>
          <div>
            <LinkButton to="/app/collegati" variant="secondary">
              Gestisci gli account collegati
            </LinkButton>
          </div>
        </Section>
      )}

      <ConfirmDialog
        open={verifyOpen}
        titolo="Verificare il codice fiscale?"
        conferma="Verifica ora"
        inCorso={verifyCf.isPending}
        onConferma={handleVerifyCf}
        onAnnulla={() => setVerifyOpen(false)}
      >
        <div className="flex flex-col gap-2">
          <p>
            Verifichiamo che <strong className="text-ink">{cfInput}</strong> sia registrato
            all'Anagrafe Tributaria (Agenzia delle Entrate) tramite openapi.it.
          </p>
          <p className="text-small text-ink-3">
            La verifica utilizza il credito del servizio dati (circa 0,05 € + IVA).
          </p>
          {verifyCf.isError && (
            <InlineError className="mt-1">{apiErrorMessage(verifyCf.error)}</InlineError>
          )}
        </div>
      </ConfirmDialog>
    </Page>
  );
}
