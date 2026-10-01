import { X } from "lucide-react";
import { useDismissible } from "../../hooks/useDismissible";
import { QUOTA_BANNER_COPY as COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { AiQuota, Me, Plan } from "../../types";
import { Alert } from "../ui/Alert";
import { LinkButton } from "../ui/Button";
import { IconButton } from "../ui/IconButton";

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
 *  È un `Alert` di attenzione (anche a quota finita: il rosso è per gli errori
 *  e le azioni distruttive; cambia il testo), NON modale e NON bloccante: gli
 *  AI-check residui restano usabili (a bloccare, a zero, è già il backend). */
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

  // Sotto soglia, piano senza AI-check (la quota lo dice già, con il link ai
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
    <Alert
      tono="attenzione"
      titolo={esaurito ? COPY.titoloEsaurito : COPY.titoloWarning}
      azione={
        <div className="flex items-center gap-1">
          {/* Anche a quota zero la CTA resta: è l'unica via d'uscita. */}
          {puoiFareUpgrade && (
            <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
              {COPY.cta}
            </LinkButton>
          )}
          <IconButton label={COPY.chiudi} icon={<X />} size="sm" onClick={dismiss} />
        </div>
      }
    >
      {esaurito ? COPY.esaurito : COPY.consumo(quota.usati, quota.totale)} {seguito}
    </Alert>
  );
}
