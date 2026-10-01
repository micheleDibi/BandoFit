import { Sparkles } from "lucide-react";
import { useEntitlements } from "../../hooks/useEntitlements";
import { Button } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

/** «AI-check» della Home: quanti ne restano quest'anno e dove usarli. Per un
 *  membro attivo (`editable` falso) `residuo`/`effettivo` sono dell'azienda: il
 *  suo limite è min(budget − usati, residuo), con budget `null` = senza limite
 *  (stessa regola della scheda del bando). Il membro non cambia piano: niente
 *  «Vedi i piani». */
export function AiCheckDisponibili() {
  const { data, isPending, isError, refetch } = useEntitlements();
  const quota = data?.ai_checks;
  const membro = data ? !data.editable : false;

  let frase: string | null = null;
  if (quota && quota.effettivo > 0) {
    if (!membro) {
      frase = `Ti restano ${quota.residuo} AI-check su ${quota.effettivo} quest'anno.`;
    } else if (quota.budget_membro === null) {
      frase = `Alla tua azienda restano ${quota.residuo} AI-check su ${quota.effettivo} quest'anno; il tuo budget è senza limite.`;
    } else {
      const budgetResiduo = Math.max(0, quota.budget_membro - (quota.usati_membro ?? 0));
      frase = `Puoi avviarne ancora ${Math.min(budgetResiduo, quota.residuo)} quest'anno (il tuo budget: ${budgetResiduo}; all'azienda ne restano ${quota.residuo} su ${quota.effettivo}).`;
    }
  }

  return (
    <Panel titolo="AI-check" icon={Sparkles} area="aicheck">
      {isPending ? (
        <Skeleton className="h-4 w-4/5" />
      ) : isError ? (
        <>
          <InlineError>Non siamo riusciti a leggere gli AI-check disponibili.</InlineError>
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={() => refetch()}>
              Riprova
            </Button>
          </div>
        </>
      ) : frase ? (
        <>
          <p className="text-body text-ink-2">{frase}</p>
          <p className="text-small">
            <TextLink to="/app/ai-check" className="font-medium">
              Vai agli AI-check
            </TextLink>
          </p>
        </>
      ) : membro ? (
        <p className="text-body text-ink-2">Il piano della tua azienda non include AI-check.</p>
      ) : (
        <>
          <p className="text-body text-ink-2">Il tuo piano non include AI-check.</p>
          <p className="text-small">
            <TextLink to="/app/abbonamento" className="font-medium">
              Vedi i piani
            </TextLink>
          </p>
        </>
      )}
    </Panel>
  );
}
