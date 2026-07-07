import { Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { useAiChecksForBando, useRequestAiCheck } from "../../hooks/useAiCheck";
import { apiErrorMessage } from "../../lib/api";
import { scoreColorClasses } from "../../lib/scoreColor";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";
import { AiEsitoBadge } from "./badges";

/** Card nella sidebar del dettaglio bando: avvio dell'AI-check, stato
 *  dell'analisi in corso ed esito sintetico dell'ultimo report. */
export function AiCheckCard({ slug }: { slug: string }) {
  const { data, isPending, isError, refetch } = useAiChecksForBando(slug);
  const requestCheck = useRequestAiCheck(slug);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const latest = data?.items[0];
  const quota = data?.quota;
  const editable = data?.editable ?? false;
  const hasPending = data?.items.some((c) => c.status === "pending") ?? false;
  const quotaEsaurita = (quota?.rimanenti ?? 0) <= 0;

  const handleRequest = async () => {
    setActionError(null);
    try {
      await requestCheck.mutateAsync();
      setConfirmOpen(false);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const ctaDisabled = hasPending || quotaEsaurita;
  // Spiegazione VISIBILE (un `title` su un bottone disabilitato non è
  // raggiungibile: pointer-events-none, e resta invisibile a tastiera/SR).
  const ctaHint = hasPending
    ? "C'è già un'analisi in corso."
    : quotaEsaurita && quota && quota.totale > 0
      ? "Hai esaurito gli AI-check del tuo piano."
      : null;

  return (
    <Card className="border-brand-200 bg-gradient-to-b from-brand-50/70 to-white p-5">
      <h2 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-slate-900">
        <Sparkles className="size-4 text-brand-500" aria-hidden />
        AI-check di compatibilità
      </h2>

      {isPending ? (
        <div className="mt-3 space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      ) : isError ? (
        <div className="mt-3">
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            Impossibile caricare lo stato dell'AI-check.
          </p>
          <Button variant="secondary" size="sm" className="mt-2 w-full" onClick={() => refetch()}>
            Riprova
          </Button>
        </div>
      ) : latest?.status === "pending" ? (
        <div className="mt-3">
          <p className="inline-flex items-center gap-2 text-sm font-medium text-amber-700">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Analisi in corso…
          </p>
          <p className="mt-1.5 text-xs text-slate-500">
            Richiede uno o due minuti: confrontiamo i requisiti del bando con i dati della tua
            azienda. Puoi restare su questa pagina.
          </p>
        </div>
      ) : latest?.status === "ready" && latest.esito ? (
        <div className="mt-3">
          <div className="flex flex-wrap items-center gap-2">
            <AiEsitoBadge esito={latest.esito} />
            {latest.punteggio !== null && (
              <span
                className={`tabular font-display text-lg font-bold ${scoreColorClasses(latest.punteggio).text}`}
              >
                {latest.punteggio}
                <span className="text-xs font-medium text-slate-400">/100</span>
              </span>
            )}
          </div>
          <a
            href="#ai-check-report"
            className="mt-2 inline-block text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            Vedi il report completo ↓
          </a>
          {editable && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="mt-2 w-full"
                disabled={ctaDisabled}
                onClick={() => {
                  setActionError(null);
                  setConfirmOpen(true);
                }}
              >
                Nuova analisi
              </Button>
              {ctaDisabled && ctaHint && (
                <p className="mt-1.5 text-xs text-slate-500">{ctaHint}</p>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="mt-3">
          {latest?.status === "error" && (
            <p className="mb-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {latest.error_detail ?? "Analisi non riuscita: riprova"}
            </p>
          )}
          <p className="text-sm text-slate-600">
            Scopri se la tua azienda è ammissibile e quanto è compatibile con questo bando:
            l'AI confronta ogni requisito con i tuoi dati, citando i passaggi del bando.
          </p>
          {editable ? (
            <>
              <Button
                className="mt-3 w-full"
                disabled={ctaDisabled}
                onClick={() => {
                  setActionError(null);
                  setConfirmOpen(true);
                }}
              >
                <Sparkles className="size-4" aria-hidden />
                Verifica compatibilità
              </Button>
              {ctaDisabled && ctaHint && (
                <p className="mt-1.5 text-xs text-slate-500">{ctaHint}</p>
              )}
            </>
          ) : (
            <p className="mt-3 text-xs text-slate-500">
              L'AI-check lo avvia il titolare dell'azienda.
            </p>
          )}
        </div>
      )}

      {quota && (
        <p className="mt-3 border-t border-brand-100 pt-2.5 text-xs text-slate-500">
          {quota.totale === 0
            ? "Il tuo piano non include AI-check: passa a un piano superiore per usarli."
            : `Ti restano ${quota.rimanenti} AI-check su ${quota.totale} per quest'anno.`}
        </p>
      )}

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        title="Avvia l'AI-check"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Annulla
            </Button>
            <Button loading={requestCheck.isPending} onClick={handleRequest}>
              Avvia l'analisi
            </Button>
          </>
        }
      >
        <p>
          L'analisi confronta i requisiti e i criteri di questo bando con i dati della tua
          azienda (compresi il dossier certificato e la visura, se presenti) e produce un
          report con esito di ammissibilità e punteggio di compatibilità.
        </p>
        <p className="mt-2 text-xs text-slate-400">
          Consuma 1 dei tuoi {quota?.totale ?? 0} AI-check annuali e richiede uno o due
          minuti. Più i dati aziendali sono completi, più l'analisi è affidabile.
        </p>
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </Card>
  );
}
