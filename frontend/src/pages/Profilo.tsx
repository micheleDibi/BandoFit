import { BadgeCheck, CalendarDays, Check, ShieldCheck, Users } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { AziendaTeaser } from "../components/company/AziendaTeaser";
import { FamilyCard } from "../components/family/FamilyCard";
import { PreferenzeTeaser } from "../components/preferences/PreferenzeTeaser";
import { PlanCard, planFeatures } from "../components/shared/PlanCard";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useMe, useSwitchPlan, useUpdateProfile, useVerifyCf } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import type { Plan } from "../types";

export default function Profilo() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const { data: plans, isPending: plansLoading, isError: plansError, refetch: refetchPlans } =
    usePlans();
  const updateProfile = useUpdateProfile();
  const switchPlan = useSwitchPlan();
  const verifyCf = useVerifyCf();

  const [form, setForm] = useState({
    nome: "",
    cognome: "",
    azienda: "",
    telefono: "",
    codice_fiscale: "",
  });
  const [saved, setSaved] = useState(false);
  const [planToConfirm, setPlanToConfirm] = useState<Plan | null>(null);
  const [switchNotice, setSwitchNotice] = useState<string | null>(null);
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

  const currentPlanId = me.subscription?.plan.id;
  const isActiveChild = me.family?.role === "child" && me.family.status === "active";

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

  const handleSwitch = async () => {
    if (!planToConfirm) return;
    setSwitchNotice(null);
    try {
      const result = await switchPlan.mutateAsync(planToConfirm.id);
      setPlanToConfirm(null);
      const adjustment = result.plan_switch_adjustment;
      if (adjustment && (adjustment.demoted.length || adjustment.revoked_pending.length)) {
        const parts: string[] = [];
        if (adjustment.demoted.length) {
          parts.push(
            `${adjustment.demoted.length} account ${adjustment.demoted.length === 1 ? "retrocesso" : "retrocessi"} al piano Gratuito (${adjustment.demoted.map((d) => d.denominazione).join(", ")})`,
          );
        }
        if (adjustment.revoked_pending.length) {
          parts.push(
            `${adjustment.revoked_pending.length} ${adjustment.revoked_pending.length === 1 ? "invito revocato" : "inviti revocati"}`,
          );
        }
        setSwitchNotice(`Piano aggiornato. ${parts.join(" e ")}.`);
      }
    } catch {
      // l'errore è mostrato nel dialog
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

      {/* Gestione account collegati: solo per il titolare con piano multi-account */}
      {me.family?.role === "parent" && (
        <div className="mt-6">
          <FamilyCard />
        </div>
      )}

      {/* Abbonamento: un figlio ATTIVO eredita il piano della famiglia */}
      {isActiveChild ? (
        <section className="mt-10">
          <h2 className="font-display text-xl font-bold tracking-tight text-slate-900">
            Il tuo abbonamento
          </h2>
          <Card className="mt-4 max-w-lg border-brand-200 p-6">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
                <Users className="size-3.5" aria-hidden />
                Piano ereditato da {me.family?.parent_display_name ?? "il titolare"}
              </span>
            </div>
            {me.subscription && (
              <>
                <p className="mt-3 font-display text-2xl font-bold text-slate-900">
                  {me.subscription.plan.nome}
                </p>
                <p className="mt-1 inline-flex items-center gap-1.5 text-sm text-slate-500">
                  <CalendarDays className="size-4" aria-hidden />
                  Attivo fino al {formatDate(me.subscription.data_scadenza)}
                </p>
                <ul className="mt-4 space-y-2">
                  {planFeatures(me.subscription.plan).map((feature) => (
                    <li key={feature} className="flex items-start gap-2 text-sm text-slate-600">
                      <Check className="mt-0.5 size-4 shrink-0 text-brand-500" aria-hidden />
                      {feature}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
              Le quote del piano sono condivise con tutta l'azienda. Solo il titolare può
              cambiare l'abbonamento.
            </p>
          </Card>
        </section>
      ) : (
      <section className="mt-10">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="font-display text-xl font-bold tracking-tight text-slate-900">
              Il tuo abbonamento
            </h2>
            {me.subscription && (
              <p className="mt-1 inline-flex items-center gap-1.5 text-sm text-slate-500">
                <CalendarDays className="size-4" aria-hidden />
                Piano <strong className="text-slate-700">{me.subscription.plan.nome}</strong> attivo
                fino al {formatDate(me.subscription.data_scadenza)}
              </p>
            )}
            {me.family?.role === "child" && me.family.status === "demoted" && (
              <p className="mt-1 text-sm text-amber-600">
                Il tuo account è stato retrocesso dall'azienda: hai un piano indipendente
                finché il titolare non ti riattiva.
              </p>
            )}
          </div>
        </div>

        {switchNotice && (
          <p className="mt-3 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800" role="status">
            {switchNotice}
          </p>
        )}

        {plansLoading ? (
          <div className="mt-6 grid gap-5 pt-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-72 w-full" />
            ))}
          </div>
        ) : plansError ? (
          <div className="mt-6">
            <ErrorState
              message="Impossibile caricare i piani disponibili."
              onRetry={() => refetchPlans()}
            />
          </div>
        ) : (
          <div className="mt-6 grid gap-5 pt-3 sm:grid-cols-2 lg:grid-cols-4">
            {(plans ?? []).map((plan) => {
              const isCurrent = plan.id === currentPlanId;
              return (
                <PlanCard
                  key={plan.id}
                  plan={plan}
                  selected={isCurrent}
                  highlighted={plan.slug === "pro"}
                  badge={
                    isCurrent ? "Piano attuale" : plan.slug === "pro" ? "Consigliato" : undefined
                  }
                  footer={
                    isCurrent ? (
                      <Button variant="secondary" className="w-full" disabled>
                        Attivo
                      </Button>
                    ) : (
                      <Button
                        variant={plan.slug === "pro" ? "primary" : "secondary"}
                        className="w-full"
                        onClick={() => setPlanToConfirm(plan)}
                      >
                        Passa a {plan.nome}
                      </Button>
                    )
                  }
                />
              );
            })}
          </div>
        )}
        <p className="mt-4 text-xs text-slate-400">
          Il cambio piano è immediato e la durata riparte da oggi per un anno. In questa fase non è
          previsto alcun pagamento.
        </p>
      </section>
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

      {/* Conferma cambio piano */}
      <Dialog
        open={!!planToConfirm}
        onClose={() => setPlanToConfirm(null)}
        title="Confermi il cambio di piano?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPlanToConfirm(null)}>
              Annulla
            </Button>
            <Button onClick={handleSwitch} loading={switchPlan.isPending}>
              Conferma
            </Button>
          </>
        }
      >
        {planToConfirm && (
          <>
            <p>
              Stai per passare da{" "}
              <strong className="text-slate-900">{me.subscription?.plan.nome ?? "—"}</strong> a{" "}
              <strong className="text-slate-900">{planToConfirm.nome}</strong>. Il nuovo
              abbonamento annuale parte da oggi.
            </p>
            {me.family?.role === "parent" &&
              (me.family.used ?? 1) > (planToConfirm.num_account_aziendali ?? 1) && (
                <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-amber-800">
                  Attenzione: il piano {planToConfirm.nome} prevede al massimo{" "}
                  {planToConfirm.num_account_aziendali} account (incluso il tuo). Gli account più
                  recenti oltre il limite verranno retrocessi al piano Gratuito.
                </p>
              )}
            {switchPlan.isError && (
              <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
                {apiErrorMessage(switchPlan.error)}
              </p>
            )}
          </>
        )}
      </Dialog>
    </div>
  );
}
