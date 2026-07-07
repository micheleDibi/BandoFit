import { CalendarDays, Check, Puzzle, Users } from "lucide-react";
import { useState } from "react";
import { AddonCard } from "../components/shared/AddonCard";
import { PlanCard, planFeatures } from "../components/shared/PlanCard";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useAddons } from "../hooks/useAddons";
import { useMe, useSwitchPlan } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { purchaseAddon } from "../lib/addons";
import { formatDate, formatPrezzo } from "../lib/format";
import type { Addon, Plan } from "../types";

export default function Abbonamento() {
  const { data: me, isPending, isError, error, refetch } = useMe();
  const { data: plans, isPending: plansLoading, isError: plansError, refetch: refetchPlans } =
    usePlans();
  const {
    data: addons,
    isPending: addonsLoading,
    isError: addonsError,
    refetch: refetchAddons,
  } = useAddons();
  const switchPlan = useSwitchPlan();

  const [planToConfirm, setPlanToConfirm] = useState<Plan | null>(null);
  const [switchNotice, setSwitchNotice] = useState<string | null>(null);
  // Add-on per cui è stato chiesto l'acquisto (flusso non ancora disponibile).
  const [addonInArrivo, setAddonInArrivo] = useState<Addon | null>(null);
  // Slug con acquisto "in volo": un Set, così quando purchaseAddon diventerà
  // una vera chiamata di rete i click concorrenti su card diverse non si
  // pesteranno i piedi (né riabiliteranno un bottone a metà acquisto).
  const [addonLoading, setAddonLoading] = useState<Set<string>>(new Set());

  if (isPending) {
    return (
      <div className="mx-auto max-w-5xl space-y-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-72 w-full" />
      </div>
    );
  }
  if (isError || !me) {
    return <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  }

  const currentPlanId = me.subscription?.plan.id;
  const isActiveChild = me.family?.role === "child" && me.family.status === "active";
  const activeAddons = addons ?? [];

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

  // Punto di estensione dell'acquisto: la UI passa SEMPRE da purchaseAddon
  // (lib/addons.ts); finché lo stub risponde available=false si apre il
  // dialog «In arrivo». Collegare il flusso reale = riempire quella funzione.
  const handleAcquista = async (addon: Addon) => {
    if (addonLoading.has(addon.slug)) return;
    setAddonLoading((prev) => new Set(prev).add(addon.slug));
    try {
      const esito = await purchaseAddon(addon.slug);
      if (!esito.available) setAddonInArrivo(addon);
    } finally {
      setAddonLoading((prev) => {
        const next = new Set(prev);
        next.delete(addon.slug);
        return next;
      });
    }
  };

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Abbonamento
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Il tuo piano, i piani disponibili e gli add-on per estendere BandoFit.
      </p>

      {/* Abbonamento: un figlio ATTIVO eredita il piano della famiglia */}
      {isActiveChild ? (
        <section className="mt-8">
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
      <section className="mt-8">
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

      {/* Add-on: catalogo gestito dagli admin. Nascosto solo se DAVVERO
          vuoto: un errore di caricamento mostra l'errore col retry (come la
          griglia piani sopra), non un catalogo silenziosamente sparito. */}
      {(addonsLoading || addonsError || activeAddons.length > 0) && (
        <section className="mt-10" aria-label="Add-on">
          <h2 className="inline-flex items-center gap-2 font-display text-xl font-bold tracking-tight text-slate-900">
            <Puzzle className="size-5 text-brand-500" aria-hidden />
            Add-on
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Estensioni una tantum per potenziare il tuo piano.
          </p>
          {addonsLoading ? (
            <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-48 w-full" />
              ))}
            </div>
          ) : addonsError ? (
            <div className="mt-5">
              <ErrorState
                message="Impossibile caricare gli add-on disponibili."
                onRetry={() => refetchAddons()}
              />
            </div>
          ) : (
            <div className="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              {activeAddons.map((addon) => (
                <AddonCard
                  key={addon.id}
                  addon={addon}
                  onAcquista={handleAcquista}
                  loading={addonLoading.has(addon.slug)}
                />
              ))}
            </div>
          )}
        </section>
      )}

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

      {/* Acquisto add-on: flusso non ancora disponibile */}
      <Dialog
        open={!!addonInArrivo}
        onClose={() => setAddonInArrivo(null)}
        title="Acquisto in arrivo"
        footer={
          <Button variant="secondary" onClick={() => setAddonInArrivo(null)}>
            Ho capito
          </Button>
        }
      >
        {addonInArrivo && (
          <>
            <p>
              L'acquisto degli add-on sarà disponibile a breve. Hai scelto{" "}
              <strong className="text-slate-900">{addonInArrivo.nome}</strong> (
              {formatPrezzo(addonInArrivo.prezzo)}).
            </p>
            <p className="mt-2 text-xs text-slate-400">
              Nessun addebito è stato effettuato.
            </p>
          </>
        )}
      </Dialog>
    </div>
  );
}
