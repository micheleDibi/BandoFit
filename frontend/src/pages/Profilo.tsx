import { BadgeCheck, CalendarDays } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { PlanCard } from "../components/shared/PlanCard";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { TextField } from "../components/ui/Field";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useMe, useSwitchPlan, useUpdateProfile } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import type { Plan } from "../types";

export default function Profilo() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const { data: plans } = usePlans();
  const updateProfile = useUpdateProfile();
  const switchPlan = useSwitchPlan();

  const [form, setForm] = useState({ nome: "", cognome: "", azienda: "", telefono: "" });
  const [saved, setSaved] = useState(false);
  const [planToConfirm, setPlanToConfirm] = useState<Plan | null>(null);

  useEffect(() => {
    if (me) {
      setForm({
        nome: me.profile.nome ?? "",
        cognome: me.profile.cognome ?? "",
        azienda: me.profile.azienda ?? "",
        telefono: me.profile.telefono ?? "",
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

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    setSaved(false);
    await updateProfile.mutateAsync({
      nome: form.nome.trim(),
      cognome: form.cognome.trim(),
      azienda: form.azienda.trim(),
      telefono: form.telefono.trim(),
    });
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  const handleSwitch = async () => {
    if (!planToConfirm) return;
    try {
      await switchPlan.mutateAsync(planToConfirm.id);
      setPlanToConfirm(null);
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

      {/* Abbonamento */}
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
          </div>
        </div>

        <div className="mt-6 grid gap-5 pt-3 sm:grid-cols-2 lg:grid-cols-4">
          {(plans ?? []).map((plan) => {
            const isCurrent = plan.id === currentPlanId;
            return (
              <PlanCard
                key={plan.id}
                plan={plan}
                selected={isCurrent}
                highlighted={plan.slug === "pro"}
                badge={isCurrent ? "Piano attuale" : plan.slug === "pro" ? "Consigliato" : undefined}
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
        <p className="mt-4 text-xs text-slate-400">
          Il cambio piano è immediato e la durata riparte da oggi per un anno. In questa fase non è
          previsto alcun pagamento.
        </p>
      </section>

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
