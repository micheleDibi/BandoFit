import { Lock } from "lucide-react";
import { BillingProfileForm } from "../components/BillingProfileForm";
import { Card } from "../components/ui/Card";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useBillingProfile } from "../hooks/useBillingProfile";
import { apiErrorCode, apiErrorMessage } from "../lib/api";

export default function Fatturazione() {
  const { data: profile, isPending, isError, error, refetch } = useBillingProfile();

  // Account collegato attivo: piano e pagamenti (fatturazione inclusa) si
  // gestiscono sull'account titolare — il backend risponde 403.
  const forbidden = isError && apiErrorCode(error) === "forbidden";

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Dati di fatturazione
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        L'intestazione delle fatture dei tuoi acquisti su BandoFit. Ogni fattura
        fotografa i dati validi al momento dell'acquisto: qui li tieni aggiornati.
      </p>

      {isPending ? (
        <div className="mt-6 space-y-4">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : forbidden ? (
        <Card className="mt-6 flex flex-col items-center px-6 py-12 text-center">
          <div className="rounded-full bg-slate-100 p-3 text-slate-500">
            <Lock className="size-7" aria-hidden />
          </div>
          <h2 className="mt-4 font-display text-base font-semibold text-slate-900">
            Gestiti dall'account titolare
          </h2>
          <p className="mt-1 max-w-sm text-sm text-slate-500">{apiErrorMessage(error)}.</p>
        </Card>
      ) : isError ? (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      ) : (
        <Card className="mt-6 p-6">
          <BillingProfileForm profile={profile ?? null} />
        </Card>
      )}
    </div>
  );
}
