import { CalendarDays, Check, Puzzle, Users, X } from "lucide-react";
import { useState } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import { AddonCard } from "../components/shared/AddonCard";
import { InventarioAddon } from "../components/shared/InventarioAddon";
import { PlanCard, planFeatures } from "../components/shared/PlanCard";
import { SubscriptionManagement } from "../components/shared/SubscriptionManagement";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useAddons } from "../hooks/useAddons";
import { useAuth } from "../hooks/useAuth";
import { useEntitlements } from "../hooks/useEntitlements";
import { useMe, useSwitchPlan } from "../hooks/useMe";
import { useMyAddons } from "../hooks/useMyAddons";
import { usePlans } from "../hooks/usePlans";
import { useSessionDismissible } from "../hooks/useSessionDismissible";
import { useScheduleDowngrade } from "../hooks/useSubscriptionManagement";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { purchaseAddon } from "../lib/addons";
import { requestConsultation } from "../lib/consulenza";
import { formatDate, formatDateNumeric } from "../lib/format";
import { prezzoDisplay } from "../lib/prezzo";
import type { Addon, Plan } from "../types";

/** A pagamento = si passa dal checkout; gratis (o importo zero) = switch
 *  diretto via POST /me/subscription, come prima del modulo pagamenti. */
const aPagamento = (p: { tipo_prezzo: string; prezzo_annuale?: string | number; prezzo?: string | number }) =>
  p.tipo_prezzo === "importo" && Number(p.prezzo_annuale ?? p.prezzo ?? 0) > 0;

export default function Abbonamento() {
  const navigate = useNavigate();
  const { data: me, isPending, isError, error, refetch } = useMe();
  const { data: plans, isPending: plansLoading, isError: plansError, refetch: refetchPlans } =
    usePlans();
  const {
    data: addons,
    isPending: addonsLoading,
    isError: addonsError,
    refetch: refetchAddons,
  } = useAddons();
  // Inventario addon: se una voce fallisse o fosse vuota, il catalogo resta
  // intatto — l'inventario è un arricchimento, non un requisito.
  const { data: mieiAddon } = useMyAddons();
  // Extra seats posseduti (0030): servono SOLO all'avviso di downgrade — il
  // limite effettivo del piano di destinazione è base + extra (dormienti se
  // base=1, specchio di fn_entitlement_detail; l'arbitro resta il server).
  const entitlements = useEntitlements();
  const seatTargetEffettivo = (plan: Plan) => {
    const base = plan.num_account_aziendali ?? 1;
    return base > 1 ? base + (entitlements.data?.seats.extra ?? 0) : base;
  };
  const switchPlan = useSwitchPlan();
  const scheduleDowngrade = useScheduleDowngrade();
  // Intento d'acquisto dalla registrazione: il ?piano= scelto sulla landing
  // viaggia come plan_slug nello user_metadata di Supabase (lo scrive
  // auth_service alla creazione dell'utente) — da qui lo si rilegge senza
  // toccare il backend.
  const { session } = useAuth();
  const intento = useSessionDismissible("intento-piano");

  const [planToConfirm, setPlanToConfirm] = useState<Plan | null>(null);
  const [switchNotice, setSwitchNotice] = useState<string | null>(null);
  // Add-on per cui è stato chiesto l'acquisto (flusso non ancora disponibile).
  const [addonInArrivo, setAddonInArrivo] = useState<Addon | null>(null);
  // Piano o add-on «su richiesta» per cui è stata chiesta una consulenza
  // (flusso di contatto non ancora disponibile).
  const [consulenzaInArrivo, setConsulenzaInArrivo] = useState<{ nome: string } | null>(null);
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

  // Callout «volevi il piano X»: solo se il piano desiderato in registrazione
  // esiste ancora, è a pagamento acquistabile, e l'utente NON è già su un
  // piano pagato — a quel punto l'intento è soddisfatto (o superato) e
  // l'avviso sparisce da solo.
  const metaSlug: unknown = session?.user?.user_metadata?.plan_slug;
  const pianoIntento =
    typeof metaSlug === "string" ? plans?.find((p) => p.slug === metaSlug) ?? null : null;
  const mostraIntento =
    !intento.dismissed &&
    !!pianoIntento &&
    pianoIntento.is_active &&
    aPagamento(pianoIntento) &&
    me.subscription?.plan.tipo_prezzo !== "importo";

  // Da un piano a pagamento verso uno gratuito si passa dall'endpoint di
  // downgrade programmato (fase 3): il cambio avviene alla scadenza e lo
  // stato vive nel backend — il banner compare in «Pagamento e rinnovo».
  const currentPaid = !!me.subscription && aPagamento(me.subscription.plan);
  const isDisdetta = (plan: Plan) => !aPagamento(plan) && currentPaid;

  const handleSwitch = async () => {
    if (!planToConfirm) return;
    setSwitchNotice(null);
    if (isDisdetta(planToConfirm)) {
      try {
        const stato = await scheduleDowngrade.mutateAsync(planToConfirm.slug);
        setPlanToConfirm(null);
        const cambio = stato.cambio_programmato;
        setSwitchNotice(
          cambio
            ? `Disdetta programmata: resterai su ${me.subscription?.plan.nome} fino al ` +
              `${formatDateNumeric(cambio.effective_date)}, poi passerai a ${cambio.to_plan_nome}.`
            : "Disdetta programmata.",
        );
      } catch {
        // errore mostrato nel dialog
      }
      return;
    }
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
    } catch (err) {
      // Piano a pagamento: 409 payment_required — la strada è il checkout
      // (fallback: la CTA delle card a pagamento ci porta già direttamente).
      if (planToConfirm && apiErrorCode(err) === "payment_required") {
        navigate(`/app/checkout?piano=${planToConfirm.slug}`);
        return;
      }
      // altri errori: mostrati nel dialog
    }
  };

  // Add-on a pagamento: si comprano dal checkout (modulo pagamenti). Il caso
  // gratis resta sul punto di estensione purchaseAddon (lib/addons.ts): finché
  // lo stub risponde available=false si apre il dialog «In arrivo».
  const handleAcquista = async (addon: Addon, quantita = 1) => {
    if (aPagamento(addon)) {
      navigate(
        `/app/checkout?addon=${addon.slug}${quantita > 1 ? `&qty=${quantita}` : ""}`,
      );
      return;
    }
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

  // Punto di estensione della richiesta di consulenza (piani e add-on «su
  // richiesta»): la UI passa SEMPRE da requestConsultation (lib/consulenza.ts);
  // finché lo stub risponde available=false si apre il dialog «In arrivo».
  const handleRichiedi = async (kind: "plan" | "addon", item: { slug: string; nome: string }) => {
    const esito = await requestConsultation({ kind, slug: item.slug });
    if (!esito.available) setConsulenzaInArrivo({ nome: item.nome });
  };

  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Abbonamento
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        Il tuo piano, i piani disponibili e gli add-on per estendere BandoFit.
      </p>

      {mostraIntento && pianoIntento && (
        <div
          role="status"
          className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-3"
        >
          <p className="min-w-0 flex-1 text-sm text-brand-900">
            Volevi il piano <strong>{pianoIntento.nome}</strong>: completa l'acquisto quando vuoi.
          </p>
          <div className="flex shrink-0 items-center gap-1">
            <LinkButton to={`/app/checkout?piano=${pianoIntento.slug}`} size="sm">
              Completa l'acquisto
            </LinkButton>
            <button
              type="button"
              onClick={intento.dismiss}
              title="Nascondi questo avviso"
              aria-label="Nascondi questo avviso"
              className="rounded-lg p-1.5 text-brand-400 transition-colors hover:bg-brand-100 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        </div>
      )}

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
                    ) : plan.tipo_prezzo === "su_richiesta" ? (
                      // Non attivabile self-serve (il backend rifiuta comunque
                      // lo switch): la CTA diventa una richiesta di contatto.
                      <Button
                        variant="secondary"
                        className="w-full"
                        onClick={() => handleRichiedi("plan", plan)}
                      >
                        Richiedi una consulenza
                      </Button>
                    ) : aPagamento(plan) ? (
                      // A pagamento: si passa dal checkout, che mostra
                      // differenza per l'anno, credito residuo e IVA.
                      <Button
                        variant={plan.slug === "pro" ? "primary" : "secondary"}
                        className="w-full"
                        onClick={() => navigate(`/app/checkout?piano=${plan.slug}`)}
                      >
                        Passa a {plan.nome}
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
          Passando a un piano superiore paghi al checkout la differenza per l'anno: il credito del
          periodo residuo viene scalato dal totale. Il passaggio a Gratuito diventa effettivo alla
          scadenza del piano attuale.
        </p>
      </section>
      )}

      {/* Rinnovo, disdetta e metodo di pagamento: solo per chi gestisce un
          piano a pagamento (i collegati attivi ereditano dal titolare) */}
      {!isActiveChild && (
        <SubscriptionManagement
          pianoAPagamento={currentPaid}
          pianoNome={me.subscription?.plan.nome ?? null}
        />
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
            Estensioni una tantum per potenziare il tuo piano.{" "}
            <RouterLink to="/app/addon" className="font-medium text-brand-600 hover:underline">
              Vedi i tuoi addon
            </RouterLink>
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
              {activeAddons.map((addon) => {
                const posseduto = mieiAddon?.find((m) => m.addon_id === addon.id);
                return (
                  <AddonCard
                    key={addon.id}
                    addon={addon}
                    onAcquista={handleAcquista}
                    onRichiedi={(a) => handleRichiedi("addon", a)}
                    loading={addonLoading.has(addon.slug)}
                    inventario={
                      posseduto && posseduto.quantita > 0 ? (
                        <InventarioAddon posseduto={posseduto} />
                      ) : undefined
                    }
                  />
                );
              })}
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
            <Button
              onClick={handleSwitch}
              loading={switchPlan.isPending || scheduleDowngrade.isPending}
            >
              Conferma
            </Button>
          </>
        }
      >
        {planToConfirm && (
          <>
            {isDisdetta(planToConfirm) ? (
              // Disdetta programmata: niente effetto immediato, il piano
              // pagato resta fino alla scadenza.
              <p>
                Resterai su{" "}
                <strong className="text-slate-900">{me.subscription?.plan.nome}</strong> fino al{" "}
                <strong className="text-slate-900">
                  {formatDateNumeric(me.subscription?.data_scadenza)}
                </strong>
                , poi passerai a{" "}
                <strong className="text-slate-900">{planToConfirm.nome}</strong>. Non perdi nulla
                del periodo già pagato e puoi annullare la disdetta fino a quel giorno.
              </p>
            ) : (
              <p>
                Stai per passare da{" "}
                <strong className="text-slate-900">{me.subscription?.plan.nome ?? "—"}</strong> a{" "}
                <strong className="text-slate-900">{planToConfirm.nome}</strong>. Il nuovo
                abbonamento annuale parte da oggi.
              </p>
            )}
            {me.family?.role === "parent" &&
              (me.family.used ?? 1) > seatTargetEffettivo(planToConfirm) && (
                <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-amber-800">
                  Attenzione: il piano {planToConfirm.nome} prevede al massimo{" "}
                  {seatTargetEffettivo(planToConfirm)} account (incluso il tuo
                  {seatTargetEffettivo(planToConfirm) >
                  (planToConfirm.num_account_aziendali ?? 1)
                    ? " e gli add-on che possiedi"
                    : ""}
                  ). Gli account più recenti oltre il limite verranno retrocessi al piano
                  Gratuito.
                </p>
              )}
            {(switchPlan.isError || scheduleDowngrade.isError) && (
              <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
                {apiErrorMessage(switchPlan.isError ? switchPlan.error : scheduleDowngrade.error)}
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
              {
                prezzoDisplay(
                  addonInArrivo.tipo_prezzo,
                  addonInArrivo.etichetta_prezzo,
                  addonInArrivo.prezzo,
                ).testo
              }
              ).
            </p>
            <p className="mt-2 text-xs text-slate-400">
              Nessun addebito è stato effettuato.
            </p>
          </>
        )}
      </Dialog>

      {/* Richiesta di consulenza: flusso di contatto non ancora disponibile */}
      <Dialog
        open={!!consulenzaInArrivo}
        onClose={() => setConsulenzaInArrivo(null)}
        title="Richiesta in arrivo"
        footer={
          <Button variant="secondary" onClick={() => setConsulenzaInArrivo(null)}>
            Ho capito
          </Button>
        }
      >
        {consulenzaInArrivo && (
          <p>
            <strong className="text-slate-900">{consulenzaInArrivo.nome}</strong> si attiva su
            richiesta: la richiesta di consulenza dall'app sarà disponibile a breve. Nel frattempo
            contattaci per maggiori informazioni.
          </p>
        )}
      </Dialog>
    </div>
  );
}
