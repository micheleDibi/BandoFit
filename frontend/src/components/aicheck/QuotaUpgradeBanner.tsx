import { TriangleAlert, X } from "lucide-react";
import { useDismissible } from "../../hooks/useDismissible";
import { cn } from "../../lib/cn";
import { QUOTA_BANNER_COPY as COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { AiQuota, Me, Plan } from "../../types";
import { LinkButton } from "../ui/Button";

/** Soglia: due terzi della quota del PIANO, non un numero fisso. In aritmetica
 *  intera per non dipendere da come arrotonda 66,6…% (con `totale = 3` e
 *  `usati = 2` il confronto in virgola mobile è esattamente sul bordo). */
export function inEsaurimento(quota: AiQuota): boolean {
  return quota.totale > 0 && quota.usati * 3 >= quota.totale * 2;
}

/** «Piano massimo» non è modellato a DB: si deriva da `ordering`, l'unico
 *  criterio che il resto dell'app usa per ordinare i piani. Un piano superiore
 *  `su_richiesta` resta un upgrade valido: la CTA porta ad Abbonamento, che sa
 *  già trasformarsi in «Richiedi una consulenza». */
function esistePianoSuperiore(corrente: Plan, piani: Plan[]): boolean {
  return piani.some((p) => p.is_active && p.ordering > corrente.ordering);
}

/** Avviso in-pagina quando la quota AI-check del piano è consumata per ≥ 2/3.
 *  È un callout inline, NON modale e NON bloccante: gli AI-check residui
 *  restano usabili (a bloccare, a zero, è già il backend). */
export function QuotaUpgradeBanner({
  quota,
  me,
  plans,
}: {
  quota: AiQuota | undefined;
  me: Me | undefined;
  plans: Plan[] | undefined;
}) {
  const piano = me?.subscription?.plan;
  // Il livello entra nella chiave del «chiudi»: nascondere l'avviso di
  // esaurimento imminente non deve nascondere anche quello di quota finita.
  const esaurito = !!quota && quota.totale > 0 && quota.rimanenti === 0;
  const livello = esaurito ? "esaurito" : "warning";
  const { dismissed, dismiss } = useDismissible(
    `aicheck-quota:${quota?.periodo_inizio ?? "n-d"}:${livello}`,
  );

  // Sotto soglia, piano senza AI-check (lo StatTile lo dice già, con link ai
  // piani), o dati non ancora arrivati: niente avviso.
  if (!quota || !me || !plans || !piano) return null;
  if (!inEsaurimento(quota)) return null;
  if (dismissed) return null;

  const figlioAttivo = me.family?.role === "child" && me.family.status === "active";
  const puoiFareUpgrade = !figlioAttivo && esistePianoSuperiore(piano, plans);

  let seguito: string;
  if (puoiFareUpgrade) seguito = esaurito ? COPY.invitoUpgradeEsaurito : COPY.invitoUpgrade;
  else if (figlioAttivo) seguito = COPY.gestitoDalTitolare;
  else {
    const rinnovo = quota.periodo_fine ? formatDate(quota.periodo_fine) : null;
    seguito = rinnovo ? COPY.pianoMassimo(rinnovo) : COPY.pianoMassimoSenzaData;
  }

  return (
    <div
      role="status"
      className={cn(
        "mt-4 flex flex-wrap items-start gap-x-4 gap-y-3 rounded-xl border px-4 py-3",
        esaurito ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50",
      )}
    >
      <TriangleAlert
        className={cn("mt-0.5 size-5 shrink-0", esaurito ? "text-red-500" : "text-amber-500")}
        aria-hidden
      />

      <div className="min-w-0 flex-1">
        <p className={cn("text-sm font-semibold", esaurito ? "text-red-900" : "text-amber-900")}>
          {esaurito ? COPY.titoloEsaurito : COPY.titoloWarning}
        </p>
        <p
          className={cn(
            "mt-0.5 text-sm leading-relaxed",
            esaurito ? "text-red-800" : "text-amber-800",
          )}
        >
          {esaurito ? COPY.esaurito : COPY.consumo(quota.usati, quota.totale)} {seguito}
        </p>
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {/* Anche a quota zero la CTA resta: è l'unica via d'uscita. */}
        {puoiFareUpgrade && (
          <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
            {COPY.cta}
          </LinkButton>
        )}
        <button
          type="button"
          onClick={dismiss}
          title={COPY.chiudi}
          aria-label={COPY.chiudi}
          className={cn(
            "rounded-lg p-1.5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2",
            esaurito
              ? "text-red-400 hover:bg-red-100 hover:text-red-700 focus-visible:outline-red-500"
              : "text-amber-400 hover:bg-amber-100 hover:text-amber-700 focus-visible:outline-amber-500",
          )}
        >
          <X className="size-4" aria-hidden />
        </button>
      </div>
    </div>
  );
}
