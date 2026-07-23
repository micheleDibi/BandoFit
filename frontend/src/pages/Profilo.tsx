import { BadgeCheck, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useLocation } from "react-router-dom";
import { AziendaTeaser } from "../components/company/AziendaTeaser";
import { PreferenzeTeaser } from "../components/preferences/PreferenzeTeaser";
import { AbbonamentoTeaser } from "../components/shared/AbbonamentoTeaser";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Combobox } from "../components/ui/Combobox";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useJobPositions } from "../hooks/useJobPositions";
import { useMe, useUpdateProfile, useVerifyCf } from "../hooks/useMe";
import { apiErrorMessage } from "../lib/api";
import { isValidTelefono, normalizeTelefono } from "../lib/telefono";

export default function Profilo() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const {
    data: positions,
    isError: positionsError,
    refetch: refetchPositions,
  } = useJobPositions();
  const updateProfile = useUpdateProfile();
  const verifyCf = useVerifyCf();
  const location = useLocation();

  // Deep-link «Account collegati» (voce del menu Account): una volta caricato il
  // profilo, se l'hash è #collegati porta in vista la gestione dei collegati.
  useEffect(() => {
    if (location.hash !== "#collegati") return;
    document.getElementById("collegati")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [location.hash, me]);

  const [form, setForm] = useState({
    nome: "",
    cognome: "",
    azienda: "",
    telefono: "",
    codice_fiscale: "",
  });
  const [positionId, setPositionId] = useState<number | null>(null);
  const [posizioneAltro, setPosizioneAltro] = useState("");
  const [saved, setSaved] = useState(false);
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
      <div className="mx-auto max-w-4xl space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (isError || !me) {
    return <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  }

  const cfInput = form.codice_fiscale.trim().toUpperCase();
  const cfVerified =
    !!me?.profile.cf_verified_at && cfInput === (me.profile.codice_fiscale ?? "");

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
    setSaved(false);
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

    await updateProfile.mutateAsync(payload);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const handleVerifyCf = async () => {
    try {
      await verifyCf.mutateAsync(cfInput);
      setVerifyOpen(false);
    } catch {
      // errore mostrato nel dialog
    }
  };

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Il tuo profilo
      </h1>
      <p className="mt-1 text-sm text-slate-500">{me.profile.email}</p>

      {/* Anagrafica */}
      <Card className="mt-6 p-6">
        <h2 className="font-display text-base font-semibold text-slate-900">Dati personali</h2>
        <form onSubmit={handleSave} className="mt-4 grid gap-4 sm:grid-cols-2">
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
            helper={!telefonoError ? "Prefisso +39 automatico" : undefined}
          />
          <div>
            <Combobox
              label="Posizione in azienda"
              options={positionOptions}
              value={positionId}
              onChange={setPositionId}
              placeholder="Cerca la tua posizione…"
              disabled={!positions && positionOptions.length === 0}
            />
            {positionsError && (
              <p className="mt-1.5 text-sm text-red-600" role="alert">
                Impossibile caricare le posizioni.{" "}
                <button
                  type="button"
                  onClick={() => refetchPositions()}
                  className="cursor-pointer font-medium underline underline-offset-2"
                >
                  Riprova
                </button>
              </p>
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
          <div className="sm:col-span-2">
            <div className="flex items-end gap-2">
              <div className="max-w-xs flex-1">
                <TextField
                  label="Codice fiscale"
                  placeholder="16 caratteri"
                  value={form.codice_fiscale}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, codice_fiscale: e.target.value.toUpperCase() }))
                  }
                  autoComplete="off"
                />
              </div>
              {cfVerified ? (
                <span
                  className="mb-2 inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600"
                  role="status"
                >
                  <ShieldCheck className="size-4" aria-hidden />
                  Verificato
                </span>
              ) : (
                cfInput.length === 16 && (
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setVerifyOpen(true)}
                  >
                    <ShieldCheck className="size-4" aria-hidden />
                    Verifica
                  </Button>
                )
              )}
            </div>
            {!cfVerified && cfInput.length === 16 && (
              <p className="mt-1 text-xs text-slate-400">
                Da verificare: conferma il codice fiscale all'Anagrafe Tributaria.
              </p>
            )}
            {cfError && (
              <p className="mt-1 text-xs text-red-600" role="alert">
                {cfError}
              </p>
            )}
          </div>
          <div className="flex items-center gap-3 sm:col-span-2">
            <Button type="submit" loading={updateProfile.isPending}>
              Salva modifiche
            </Button>
            {saved && (
              <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600" role="status">
                <BadgeCheck className="size-4" aria-hidden />
                Salvato
              </span>
            )}
            {updateProfile.isError && (
              <span className="text-sm text-red-600" role="alert">
                {apiErrorMessage(updateProfile.error)}
              </span>
            )}
          </div>
        </form>
      </Card>

      {/* Dati aziendali: si gestiscono in «Dati azienda» */}
      <div className="mt-6">
        <AziendaTeaser />
      </div>

      {/* Preferenze bandi: l'editor completo vive in /app/preferenze */}
      <div className="mt-6">
        <PreferenzeTeaser />
      </div>

      {/* Abbonamento e add-on: si gestiscono in /app/abbonamento */}
      <div className="mt-6">
        <AbbonamentoTeaser />
      </div>

      {/* Account collegati: la GESTIONE vive nella pagina dedicata (WP8).
          id="collegati" resta come bersaglio dei vecchi deep-link. */}
      {me.family?.role === "parent" && (
        <div id="collegati" className="mt-6 scroll-mt-24">
          <Card className="flex flex-wrap items-center justify-between gap-3 p-5">
            <div>
              <h2 className="font-display text-base font-semibold text-slate-900">
                Account collegati
              </h2>
              <p className="mt-0.5 text-sm text-slate-500">
                {me.family.used ?? 1} di {me.family.limit ?? 1} account usati (incluso il
                tuo). Inviti, aziende visibili e budget AI-check si gestiscono dalla
                pagina dedicata.
              </p>
            </div>
            <LinkButton to="/app/collegati" variant="secondary">
              Gestisci collegati
            </LinkButton>
          </Card>
        </div>
      )}

      {/* Verifica codice fiscale */}
      <Dialog
        open={verifyOpen}
        onClose={() => setVerifyOpen(false)}
        title="Verifica il codice fiscale"
        footer={
          <>
            <Button variant="ghost" onClick={() => setVerifyOpen(false)}>
              Annulla
            </Button>
            <Button onClick={handleVerifyCf} loading={verifyCf.isPending}>
              Verifica ora
            </Button>
          </>
        }
      >
        <p>
          Verifichiamo che <strong className="text-slate-900">{cfInput}</strong> sia
          registrato all'Anagrafe Tributaria (Agenzia delle Entrate) tramite openapi.it.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          La verifica utilizza il credito del servizio dati (circa 0,05 € + IVA).
        </p>
        {verifyCf.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {apiErrorMessage(verifyCf.error)}
          </p>
        )}
      </Dialog>

    </div>
  );
}
