import { Check, Gem, LayoutList } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useEntitlements } from "../../hooks/useEntitlements";
import { useMe, useSwitchPlan } from "../../hooks/useMe";
import { usePlans } from "../../hooks/usePlans";
import { useScheduleDowngrade, useSubscriptionManagement } from "../../hooks/useSubscriptionManagement";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { requestConsultation } from "../../lib/consulenza";
import { formatDate, formatDateNumeric } from "../../lib/format";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Plan } from "../../types";
import { planFeatures } from "../shared/PlanCard";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Dialog } from "../ui/Dialog";
import { Facts, type Fatto } from "../ui/Facts";
import { IconChip } from "../ui/IconChip";
import { InlineError } from "../ui/InlineError";
import { ProgressRing } from "../ui/ProgressRing";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Status } from "../ui/Status";
import { ErrorState, Skeleton } from "../ui/states";
import { Table, Td, Th } from "../ui/Table";

/** A pagamento = si passa dal checkout; gratis (o importo zero) = switch
 *  diretto via POST /me/subscription, come prima del modulo pagamenti. */
export const aPagamento = (p: {
  tipo_prezzo: string;
  prezzo_annuale?: string | number;
  prezzo?: string | number;
}) => p.tipo_prezzo === "importo" && Number(p.prezzo_annuale ?? p.prezzo ?? 0) > 0;

/** Avvisi nuovi-bandi nella tabella: copy onesto guidato dal ritardo del piano. */
function avvisiPiano(plan: Plan): string {
  if (plan.alert_attivo && plan.alert_ritardo_giorni != null) {
    if (plan.alert_ritardo_giorni === 0) return "Il giorno stesso della pubblicazione";
    if (plan.alert_ritardo_giorni === 1) return "Il giorno dopo la pubblicazione";
    return `Dopo ${plan.alert_ritardo_giorni} giorni dalla pubblicazione`;
  }
  return "Non inclusi";
}

function Prezzo({ plan }: { plan: Plan }) {
  const display = prezzoDisplay(plan.tipo_prezzo, plan.etichetta_prezzo, plan.prezzo_annuale);
  return (
    <span className="whitespace-nowrap">
      <span className="font-semibold text-ink">{display.testo}</span>
      {display.conSuffissoPeriodo && <span className="text-ink-3"> /anno</span>}
    </span>
  );
}

/** Scheda «Piano» dell'Abbonamento (tavola Abbonamento): il piano attuale in
 *  fatti chiave, poi il confronto dei piani in tabella. Il cambio verso un
 *  piano a pagamento passa dal checkout; verso il gratuito è una disdetta
 *  programmata; un piano «su richiesta» chiede una consulenza. Un membro attivo
 *  eredita il piano del titolare e non lo cambia. */
/** Stato del rinnovo automatico, in un sottocomponente: la chiamata a
 *  `/me/subscription/management` parte solo quando la voce si monta, cioè per
 *  chi paga e non è un membro attivo (al membro il backend risponde 403). */
function StatoRinnovo() {
  const { data, isError } = useSubscriptionManagement();
  if (isError) return <span className="text-ink-3">Non disponibile</span>;
  if (!data) return <Skeleton className="h-5 w-16" />;
  return data.auto_renew ? (
    <Status tono="aperto">Attivo</Status>
  ) : (
    <Status tono="neutro">Spento</Status>
  );
}

export function SchedaPiano({ vaiAPagamento }: { vaiAPagamento: () => void }) {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const plans = usePlans();
  const entitlements = useEntitlements();
  const switchPlan = useSwitchPlan();
  const scheduleDowngrade = useScheduleDowngrade();

  const [planToConfirm, setPlanToConfirm] = useState<Plan | null>(null);
  const [switchNotice, setSwitchNotice] = useState<string | null>(null);
  // Piano «su richiesta» per cui è stata chiesta una consulenza (flusso di
  // contatto non ancora disponibile).
  const [consulenzaInArrivo, setConsulenzaInArrivo] = useState<{ nome: string } | null>(null);

  if (!me) return null;

  const currentPlanId = me.subscription?.plan.id;
  const isActiveChild = me.family?.role === "child" && me.family.status === "active";
  // Da un piano a pagamento verso uno gratuito si passa dall'endpoint di
  // downgrade programmato (fase 3): il cambio avviene alla scadenza e lo
  // stato vive nel backend — compare in «Pagamento e fatturazione».
  const currentPaid = !!me.subscription && aPagamento(me.subscription.plan);
  const isDisdetta = (plan: Plan) => !aPagamento(plan) && currentPaid;
  // Extra seats posseduti (0030): servono SOLO all'avviso di downgrade — il
  // limite effettivo del piano di destinazione è base + extra (dormienti se
  // base=1, specchio di fn_entitlement_detail; l'arbitro resta il server).
  const seatTargetEffettivo = (plan: Plan) => {
    const base = plan.num_account_aziendali ?? 1;
    return base > 1 ? base + (entitlements.data?.seats.extra ?? 0) : base;
  };

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
        // errore mostrato nella finestra
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
      } else {
        setSwitchNotice("Piano aggiornato.");
      }
    } catch (err) {
      // Piano a pagamento: 409 payment_required — la strada è il checkout
      // (la CTA dei piani a pagamento ci porta già direttamente).
      if (planToConfirm && apiErrorCode(err) === "payment_required") {
        navigate(`/app/checkout?piano=${planToConfirm.slug}`);
        return;
      }
      // altri errori: mostrati nella finestra
    }
  };

  // Punto di estensione della richiesta di consulenza (piani «su richiesta»):
  // la UI passa SEMPRE da requestConsultation (lib/consulenza.ts); finché lo
  // stub risponde available=false si apre la finestra «In arrivo».
  const handleRichiedi = async (plan: Plan) => {
    const esito = await requestConsultation({ kind: "plan", slug: plan.slug });
    if (!esito.available) setConsulenzaInArrivo({ nome: plan.nome });
  };

  const quota = entitlements.data?.ai_checks;
  const fatti: Fatto[] = [];
  if (me.subscription) {
    fatti.push({ etichetta: "Attivo fino al", valore: formatDate(me.subscription.data_scadenza) });
  }
  if (quota && quota.effettivo > 0) {
    fatti.push({
      etichetta: "AI-check disponibili",
      valore: (
        <span className="inline-flex items-center gap-2">
          {/* Decorativo: il numero «N su M» sta scritto accanto. */}
          <span aria-hidden className="flex">
            <ProgressRing
              value={quota.residuo}
              max={quota.effettivo}
              size={32}
              tono="aicheck"
              label={`AI-check disponibili: ${quota.residuo} su ${quota.effettivo}`}
            >
              {""}
            </ProgressRing>
          </span>
          {`${quota.residuo} su ${quota.effettivo}`}
        </span>
      ),
    });
  }
  if (isActiveChild) {
    fatti.push({
      etichetta: "Ereditato da",
      valore: me.family?.parent_display_name ?? "il titolare",
    });
  } else if (currentPaid) {
    fatti.push({ etichetta: "Rinnovo automatico", valore: <StatoRinnovo /> });
  }

  return (
    <>
      <Card area="account">
        <Section>
          <SectionHeader
            titolo={
              <span className="flex items-center gap-3">
                <IconChip icon={Gem} area="account" size="sm" />
                {`Piano ${me.subscription?.plan.nome ?? ""}`.trim()}
              </span>
            }
          />
          {me.family?.role === "child" && me.family.status === "demoted" && (
            <Alert tono="attenzione">
              Il tuo account è stato retrocesso dall'azienda: hai un piano indipendente finché il
              titolare non ti riattiva.
            </Alert>
          )}
          {fatti.length > 0 && (
            <Facts
              items={fatti}
              azione={
                !isActiveChild && currentPaid ? (
                  // Solo `setTab` (replace): un link farebbe anche un push dello
                  // stesso URL, e il tasto Indietro sembrerebbe non fare nulla.
                  <Button type="button" variant="secondary" onClick={vaiAPagamento}>
                    Gestisci pagamento e rinnovo
                  </Button>
                ) : undefined
              }
            />
          )}
          {isActiveChild && me.subscription && (
            <>
              <ul className="flex flex-col gap-1.5">
                {planFeatures(me.subscription.plan).map((feature) => (
                  <li key={feature} className="flex items-start gap-2 text-body text-ink-2">
                    <Check className="mt-0.5 size-4 shrink-0 text-fit-ink" aria-hidden />
                    {feature}
                  </li>
                ))}
              </ul>
              <p className="text-small text-ink-3">
                Le quote del piano sono condivise con tutta l'azienda. Solo il titolare può cambiare
                l'abbonamento.
              </p>
            </>
          )}
          {switchNotice && <Alert tono="ok">{switchNotice}</Alert>}
        </Section>
      </Card>

      {!isActiveChild && (
        <Card>
          <Section aria-label="Confronta i piani">
            <SectionHeader
              titolo={
                <span className="flex items-center gap-3">
                  <IconChip icon={LayoutList} area="account" size="sm" />
                  Confronta i piani
                </span>
              }
            />
            {plans.isPending ? (
              <div className="flex flex-col gap-3" aria-hidden>
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-16 w-full" />
                <Skeleton className="h-16 w-full" />
              </div>
            ) : plans.isError ? (
              <ErrorState
                title="Non siamo riusciti a caricare i piani."
                onRetry={() => plans.refetch()}
              />
            ) : (
              <Table>
                <thead>
                  <tr>
                    <Th>Piano</Th>
                    <Th>AI-check all'anno</Th>
                    <Th>Avvisi email sui nuovi bandi</Th>
                    <Th>Account aziendali</Th>
                    <Th numerica>Prezzo</Th>
                    <Th>
                      <span className="sr-only">Azione</span>
                    </Th>
                  </tr>
                </thead>
                <tbody>
                  {(plans.data ?? []).map((plan) => {
                    const isCurrent = plan.id === currentPlanId;
                    const suMisura = !!plan.features_override && plan.features_override.length > 0;
                    return (
                      // Il piano attuale si distingue: fondo tenue, bordo sinistro
                      // accent e lo stato «Piano attuale» in verde (con la parola).
                      <tr key={plan.id} className={isCurrent ? "bg-accent-soft/40" : undefined}>
                        <Td className={isCurrent ? "border-l-4 border-l-accent" : undefined}>
                          <span className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold text-ink">{plan.nome}</span>
                            {/* Il piano consigliato; il piano attuale vince. */}
                            {plan.slug === "pro" && !isCurrent && <Badge tone="info">Consigliato</Badge>}
                          </span>
                          {plan.descrizione && (
                            <span className="block text-small text-ink-2">{plan.descrizione}</span>
                          )}
                        </Td>
                        {suMisura ? (
                          <Td colSpan={3}>
                            <ul className="flex flex-col gap-1 text-ink-2">
                              {plan.features_override!.map((f) => (
                                <li key={f}>{f}</li>
                              ))}
                            </ul>
                          </Td>
                        ) : (
                          <>
                            <Td className="tabular-nums">
                              {plan.ai_check > 0 ? plan.ai_check : "Non inclusi"}
                            </Td>
                            <Td>{avvisiPiano(plan)}</Td>
                            <Td className="tabular-nums">
                              {plan.num_account_aziendali === 1
                                ? "1"
                                : `Fino a ${plan.num_account_aziendali}`}
                            </Td>
                          </>
                        )}
                        <Td numerica>
                          <Prezzo plan={plan} />
                        </Td>
                        <Td className="text-right">
                          {isCurrent ? (
                            <Status tono="aperto">Piano attuale</Status>
                          ) : plan.tipo_prezzo === "su_richiesta" ? (
                            // Non attivabile self-serve (il backend rifiuta comunque
                            // lo switch): la CTA diventa una richiesta di contatto.
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => handleRichiedi(plan)}
                            >
                              Richiedi una consulenza
                            </Button>
                          ) : aPagamento(plan) ? (
                            // A pagamento: si passa dal checkout, che mostra
                            // differenza per l'anno, credito residuo e IVA.
                            <LinkButton
                              to={`/app/checkout?piano=${plan.slug}`}
                              variant="secondary"
                              size="sm"
                            >
                              Passa a {plan.nome}
                            </LinkButton>
                          ) : (
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => setPlanToConfirm(plan)}
                            >
                              Passa a {plan.nome}
                            </Button>
                          )}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            )}
            <p className="text-small text-ink-2">
              Passando a un piano superiore paghi al checkout la differenza per l'anno: il credito
              del periodo residuo viene scalato dal totale. Il passaggio a Gratuito diventa effettivo
              alla scadenza del piano attuale.
            </p>
          </Section>
        </Card>
      )}

      {/* Conferma cambio piano (solo verso piani gratuiti: i pagati vanno al checkout) */}
      <ConfirmDialog
        open={!!planToConfirm}
        titolo="Confermi il cambio di piano?"
        conferma="Conferma"
        inCorso={switchPlan.isPending || scheduleDowngrade.isPending}
        onConferma={handleSwitch}
        onAnnulla={() => setPlanToConfirm(null)}
      >
        {planToConfirm && (
          <div className="flex flex-col gap-3">
            {isDisdetta(planToConfirm) ? (
              // Disdetta programmata: niente effetto immediato, il piano
              // pagato resta fino alla scadenza.
              <p>
                Resterai su <strong className="text-ink">{me.subscription?.plan.nome}</strong>{" "}
                fino al{" "}
                <strong className="text-ink">
                  {formatDateNumeric(me.subscription?.data_scadenza)}
                </strong>
                , poi passerai a <strong className="text-ink">{planToConfirm.nome}</strong>. Non
                perdi nulla del periodo già pagato e puoi annullare la disdetta fino a quel giorno.
              </p>
            ) : (
              <p>
                Stai per passare da{" "}
                <strong className="text-ink">{me.subscription?.plan.nome ?? "—"}</strong> a{" "}
                <strong className="text-ink">{planToConfirm.nome}</strong>. Il nuovo abbonamento
                annuale parte da oggi.
              </p>
            )}
            {me.family?.role === "parent" &&
              (me.family.used ?? 1) > seatTargetEffettivo(planToConfirm) && (
                <Alert tono="attenzione">
                  Attenzione: il piano {planToConfirm.nome} prevede al massimo{" "}
                  {seatTargetEffettivo(planToConfirm)} account (incluso il tuo
                  {seatTargetEffettivo(planToConfirm) > (planToConfirm.num_account_aziendali ?? 1)
                    ? " e gli add-on che possiedi"
                    : ""}
                  ). Gli account più recenti oltre il limite verranno retrocessi al piano Gratuito.
                </Alert>
              )}
            {(switchPlan.isError || scheduleDowngrade.isError) && (
              <InlineError>
                {apiErrorMessage(switchPlan.isError ? switchPlan.error : scheduleDowngrade.error)}
              </InlineError>
            )}
          </div>
        )}
      </ConfirmDialog>

      {/* Richiesta di consulenza: flusso di contatto non ancora disponibile */}
      <Dialog
        open={!!consulenzaInArrivo}
        onClose={() => setConsulenzaInArrivo(null)}
        title="Richiesta in arrivo"
        footer={
          <Button type="button" variant="secondary" onClick={() => setConsulenzaInArrivo(null)}>
            Ho capito
          </Button>
        }
      >
        {consulenzaInArrivo && (
          <p>
            <strong className="text-ink">{consulenzaInArrivo.nome}</strong> si attiva su
            richiesta: la richiesta di consulenza dall'app sarà disponibile a breve. Nel frattempo
            contattaci per maggiori informazioni.
          </p>
        )}
      </Dialog>
    </>
  );
}
