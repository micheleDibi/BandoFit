import { BadgeCheck, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { AziendaTeaser } from "../components/company/AziendaTeaser";
import { FamilyCard } from "../components/family/FamilyCard";
import { PreferenzeTeaser } from "../components/preferences/PreferenzeTeaser";
import { AbbonamentoTeaser } from "../components/shared/AbbonamentoTeaser";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useMe, useUpdateProfile, useVerifyCf } from "../hooks/useMe";
import { apiErrorMessage } from "../lib/api";

export default function Profilo() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const updateProfile = useUpdateProfile();
  const verifyCf = useVerifyCf();

  const [form, setForm] = useState({
    nome: "",
    cognome: "",
    azienda: "",
    telefono: "",
    codice_fiscale: "",
  });
  const [saved, setSaved] = useState(false);
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [cfError, setCfError] = useState<string | null>(null);

  useEffect(() => {
    if (me) {
      setForm({
        nome: me.profile.nome ?? "",
        cognome: me.profile.cognome ?? "",
        azienda: me.profile.azienda ?? "",
        telefono: me.profile.telefono ?? "",
        codice_fiscale: me.profile.codice_fiscale ?? "",
      });
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

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    setCfError(null);
    // Validazione locale: un CF incompleto non deve bloccare il salvataggio
    // degli altri campi con un errore generico del backend.
    if (cfInput && cfInput.length !== 16) {
      setCfError("Il codice fiscale deve avere 16 caratteri (o lascialo vuoto).");
      return;
    }
    await updateProfile.mutateAsync({
      nome: form.nome.trim(),
      cognome: form.cognome.trim(),
      azienda: form.azienda.trim(),
      telefono: form.telefono.trim(),
      codice_fiscale: cfInput || null,
    });
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
          />
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

      {/* Dati aziendali: si gestiscono nella pagina Azienda */}
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

      {/* Gestione account collegati: solo per il titolare con piano multi-account */}
      {me.family?.role === "parent" && (
        <div className="mt-6">
          <FamilyCard />
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
