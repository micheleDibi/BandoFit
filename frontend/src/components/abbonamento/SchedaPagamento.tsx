import { ReceiptText } from "lucide-react";
import { useBillingProfile } from "../../hooks/useBillingProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { BillingProfileForm } from "../BillingProfileForm";
import { SubscriptionManagement } from "../shared/SubscriptionManagement";
import { Card } from "../ui/Card";
import { IconChip } from "../ui/IconChip";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";

/** Scheda «Pagamento e fatturazione»: metodo di pagamento, rinnovo, disdetta
 *  e cambio programmato (`SubscriptionManagement`, solo per chi gestisce il
 *  piano) e l'anagrafica di fatturazione. */
export function SchedaPagamento({
  mostraGestione,
  pianoAPagamento,
  pianoNome,
}: {
  /** Falso per un membro attivo: piano e pagamenti si gestiscono sul titolare. */
  mostraGestione: boolean;
  pianoAPagamento: boolean;
  pianoNome: string | null;
}) {
  const { data: profile, isPending, isError, error, refetch } = useBillingProfile();

  // Account collegato attivo: piano e pagamenti (fatturazione inclusa) si
  // gestiscono sull'account titolare — il backend risponde 403.
  const forbidden = isError && apiErrorCode(error) === "forbidden";

  return (
    <>
      {mostraGestione && (
        <Card>
          <SubscriptionManagement pianoAPagamento={pianoAPagamento} pianoNome={pianoNome} />
        </Card>
      )}

      <Card>
        <Section aria-label="Dati di fatturazione">
          <SectionHeader
            titolo={
              <span className="flex items-center gap-3">
                <IconChip icon={ReceiptText} area="account" size="sm" />
                Dati di fatturazione
              </span>
            }
          />
          <p className="text-body text-ink-2">
            L'intestazione delle fatture dei tuoi acquisti su BandoFit. Ogni fattura fotografa i
            dati validi al momento dell'acquisto: qui li tieni aggiornati.
          </p>
          {isPending ? (
            <div className="flex flex-col gap-3" aria-hidden>
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-2/3" />
            </div>
          ) : forbidden ? (
            <EmptyState
              area="account"
              title="Gestiti dall'account titolare."
              description={`${apiErrorMessage(error)}.`}
            />
          ) : isError ? (
            <ErrorState
              title="Non siamo riusciti a caricare i dati di fatturazione."
              message={apiErrorMessage(error)}
              onRetry={() => refetch()}
            />
          ) : (
            <BillingProfileForm profile={profile ?? null} />
          )}
        </Section>
      </Card>
    </>
  );
}
