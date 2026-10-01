import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useAiChecksForBando, useRequestAiCheck } from "../../hooks/useAiCheck";
import { useEntitlements } from "../../hooks/useEntitlements";
import { apiErrorMessage } from "../../lib/api";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { InlineError } from "../ui/InlineError";
import { Spinner } from "../ui/Spinner";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { AiEsitoBadge } from "./badges";

/** AI-check nel pannello «Fa per te?» della scheda del bando: avvio
 *  dell'analisi, stato dell'analisi in corso ed esito sintetico dell'ultimo
 *  report, con il rimando al report completo in fondo alla pagina. */
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

  // WP6 (0031): anche un MEMBRO attivo avvia l'AI-check (sulle aziende a lui
  // visibili), entro il budget assegnato dal titolare. Il vincolo del singolo
  // check è min(residuo membro, residuo dell'azienda): lo si dice, non lo si
  // nasconde — l'arbitro resta il server.
  const entitlements = useEntitlements();
  const membro =
    !editable && entitlements.data && !entitlements.data.editable
      ? entitlements.data.ai_checks
      : null;
  const budgetResiduo =
    membro && membro.budget_membro !== null
      ? Math.max(0, membro.budget_membro - (membro.usati_membro ?? 0))
      : null; // null = illimitato
  const budgetEsaurito = membro !== null && budgetResiduo !== null && budgetResiduo <= 0;
  const puoAvviare = editable || membro !== null;

  const handleRequest = async () => {
    setActionError(null);
    try {
      await requestCheck.mutateAsync();
      setConfirmOpen(false);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const ctaDisabled = hasPending || quotaEsaurita || budgetEsaurito;
  // Spiegazione VISIBILE (un `title` su un bottone disabilitato non è
  // raggiungibile: pointer-events-none, e resta invisibile a tastiera/SR).
  const ctaHint = hasPending
    ? "C'è già un'analisi in corso."
    : quotaEsaurita && quota && quota.totale > 0
      ? membro
        ? "L'azienda ha esaurito gli AI-check del piano."
        : "Hai esaurito gli AI-check del tuo piano."
      : budgetEsaurito
        ? "Hai esaurito il budget di AI-check assegnato dal titolare."
        : null;

  const apriConferma = () => {
    setActionError(null);
    setConfirmOpen(true);
  };

  const fraseQuota = quota
    ? quota.totale === 0
      ? null
      : membro
        ? budgetResiduo === null
          ? `Alla tua azienda restano ${quota.rimanenti} AI-check su ${quota.totale}; il tuo budget è senza limite.`
          : `Puoi avviarne ancora ${Math.min(budgetResiduo, quota.rimanenti)} (il tuo budget: ${budgetResiduo}; all'azienda ne restano ${quota.rimanenti} su ${quota.totale}).`
        : `Ti restano ${quota.rimanenti} AI-check su ${quota.totale} quest'anno.`
    : null;

  return (
    <div className="flex flex-col gap-3">
      {isPending ? (
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      ) : isError ? (
        <>
          <InlineError>Non siamo riusciti a caricare lo stato dell'AI-check.</InlineError>
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={() => refetch()}>
              Riprova
            </Button>
          </div>
        </>
      ) : latest?.status === "pending" ? (
        <>
          <p className="inline-flex items-center gap-2 text-body font-medium text-ink">
            <Spinner size="sm" />
            AI-check in corso…
          </p>
          <p className="text-small text-ink-3">
            Richiede uno o due minuti: confrontiamo i requisiti del bando con i dati della tua
            azienda. Puoi restare su questa pagina.
          </p>
        </>
      ) : latest?.status === "ready" && latest.esito ? (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <AiEsitoBadge esito={latest.esito} />
            {latest.punteggio !== null && (
              <span className="text-figure-sm text-ink">
                {latest.punteggio}
                <span className="font-sans text-small font-normal text-ink-3">/100</span>
              </span>
            )}
          </div>
          <p className="text-small text-ink-2">
            <TextLink href="#ai-check-report">Vedi il report AI-check</TextLink>
          </p>
          {puoAvviare && (
            <div className="flex flex-col gap-1.5">
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={ctaDisabled}
                  onClick={apriConferma}
                >
                  <Sparkles className="size-4" aria-hidden />
                  Nuovo AI-check
                </Button>
              </div>
              {ctaDisabled && ctaHint && <p className="text-small text-ink-3">{ctaHint}</p>}
            </div>
          )}
          {/* La quota anche con un report pronto: quanti ne restano prima di
              «Nuovo AI-check». */}
          {fraseQuota && <p className="text-small text-ink-3">{fraseQuota}</p>}
        </>
      ) : (
        <>
          {latest?.status === "error" && (
            <InlineError>{latest.error_detail ?? "Analisi non riuscita: riprova."}</InlineError>
          )}
          {puoAvviare ? (
            <>
              <Button
                type="button"
                variant="secondary"
                className="w-full"
                disabled={ctaDisabled}
                onClick={apriConferma}
              >
                <Sparkles className="size-4" aria-hidden />
                Avvia AI-check
              </Button>
              {ctaDisabled && ctaHint && <p className="text-small text-ink-3">{ctaHint}</p>}
            </>
          ) : (
            <p className="text-small text-ink-3">L'AI-check lo avvia il titolare dell'azienda.</p>
          )}
          <p className="text-small text-ink-3">
            L'AI confronta ogni requisito con i dati della tua azienda e cita i passaggi del
            bando.
            {fraseQuota ? ` ${fraseQuota}` : ""}
          </p>
        </>
      )}

      {quota && quota.totale === 0 && !isPending && !isError && (
        <p className="text-small text-ink-3">
          Il tuo piano non include AI-check:{" "}
          <TextLink to="/app/abbonamento">passa a un piano superiore</TextLink> per usarli.
        </p>
      )}

      <ConfirmDialog
        open={confirmOpen}
        titolo="Avviare l'AI-check?"
        conferma="Avvia l'analisi"
        inCorso={requestCheck.isPending}
        onConferma={handleRequest}
        onAnnulla={() => setConfirmOpen(false)}
      >
        <p>
          L'analisi confronta i requisiti e i criteri di questo bando con i dati della tua
          azienda (compreso il dossier certificato, se importato) e produce un report con
          esito di ammissibilità e punteggio di compatibilità.
        </p>
        <p className="mt-2 text-small text-ink-3">
          Consuma 1 dei tuoi {quota?.totale ?? 0} AI-check annuali e richiede uno o due minuti.
          Più i dati aziendali sono completi, più l'analisi è affidabile.
        </p>
        {actionError && <InlineError className="mt-3">{actionError}</InlineError>}
      </ConfirmDialog>
    </div>
  );
}
