import { CircleCheck, CircleDashed, CircleX } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { scoreColorClasses } from "../../lib/scoreColor";
import type { BandoDetail, CompatibilitaDimensione } from "../../types";
import { Card } from "../ui/Card";

/** In colonna laterale lo spazio è poco: oltre questa soglia le voci non in
 *  comune si riassumono in un «+N» (il title le elenca comunque). */
const MAX_CHIP = 6;

interface Voce {
  id: number;
  label: string;
  title?: string;
}

/** Un requisito del bando. Le sue voci sono ALTERNATIVE: ne basta una in comune
 *  perché il requisito sia soddisfatto (un bando su quattro settori non chiede
 *  di operare in tutti e quattro). `dim` assente = requisito non valutabile,
 *  l'azienda non ha quel dato e non entra nel punteggio. */
function Requisito({
  nome,
  voci,
  dim,
  valutabile,
}: {
  nome: string;
  voci: Voce[];
  dim?: CompatibilitaDimensione;
  valutabile: boolean;
}) {
  if (voci.length === 0) return null;

  const inComune = new Set(dim?.matched_ids ?? []);
  // Le voci in comune per prime: in una lista lunga sono l'unica cosa che si cerca.
  const ordinate = [...voci].sort(
    (a, b) => Number(inComune.has(b.id)) - Number(inComune.has(a.id)),
  );
  // Bando aperto a tutte le regioni: elencarle tutte e venti è rumore, basta
  // dire dove l'azienda ha una sede e spiegarlo sotto.
  const visibili = dim?.nazionale
    ? ordinate.filter((v) => inComune.has(v.id))
    : ordinate.slice(0, MAX_CHIP);
  const restanti = dim?.nazionale ? [] : ordinate.slice(visibili.length);

  return (
    <li>
      <div className="flex items-center gap-1.5">
        {!valutabile ? null : !dim ? (
          <CircleDashed className="size-3.5 shrink-0 text-slate-300" aria-hidden />
        ) : dim.soddisfatta ? (
          <CircleCheck className="size-3.5 shrink-0 text-emerald-600" aria-hidden />
        ) : (
          <CircleX className="size-3.5 shrink-0 text-rose-400" aria-hidden />
        )}
        <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">{nome}</h3>
        {valutabile && !dim && (
          <span
            className="text-xs text-slate-400"
            title="La tua azienda non ha questo dato: il requisito non entra nel punteggio."
          >
            non valutato
          </span>
        )}
      </div>

      <ul className="mt-1.5 flex flex-wrap gap-1">
        {visibili.map((voce) => (
          <li key={voce.id}>
            <span
              title={voce.title}
              className={cn(
                "inline-flex rounded-full px-2 py-0.5 text-xs ring-1 ring-inset",
                inComune.has(voce.id)
                  ? "bg-emerald-50 font-medium text-emerald-700 ring-emerald-200"
                  : "bg-slate-100 text-slate-600 ring-slate-200",
              )}
            >
              {voce.label}
            </span>
          </li>
        ))}
        {restanti.length > 0 && (
          <li>
            <span
              title={restanti.map((v) => v.label).join(", ")}
              className="inline-flex rounded-full px-2 py-0.5 text-xs text-slate-400"
            >
              +{restanti.length}
            </span>
          </li>
        )}
      </ul>

      {dim?.nazionale && (
        <p className="mt-1 text-xs text-slate-500">Il bando è aperto a tutte le regioni.</p>
      )}
    </li>
  );
}

/** Pre-check in colonna laterale: i requisiti di catalogo del bando (regioni,
 *  ATECO, settori, beneficiari) e quali la tua azienda soddisfa, tutte le sedi
 *  comprese. Non sostituisce l'AI-check, che legge il testo del bando. */
export function CompatibilitaCard({ bando }: { bando: BandoDetail }) {
  const compat = bando.compatibilita ?? null;
  const dims = compat?.dimensioni ?? undefined;

  const voci = {
    regioni: bando.regioni.map((r) => ({ id: r.id, label: r.nome })),
    ateco: bando.codici_ateco.map((c) => ({
      id: c.id,
      label: c.codice,
      title: c.descrizione ?? undefined,
    })),
    settori: bando.settori.map((s) => ({ id: s.id, label: s.nome })),
    beneficiari: bando.beneficiari.map((b) => ({ id: b.id, label: b.nome })),
  };

  // Il bando non dichiara alcun requisito di catalogo: niente da confrontare.
  if (Object.values(voci).every((v) => v.length === 0)) return null;

  const colori = compat ? scoreColorClasses(compat.punteggio) : null;

  return (
    <Card id="compatibilita" className="p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-sm font-semibold text-slate-900">Compatibilità</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
            {compat
              ? "Requisiti del bando soddisfatti dalla tua azienda, tutte le sedi comprese."
              : "A chi si rivolge questo bando."}
          </p>
        </div>
        {compat && colori && (
          <p className="tabular shrink-0 font-display text-xl font-bold leading-none">
            <span className={colori.text}>{compat.matched}</span>
            <span className="text-slate-300">/{compat.totale}</span>
          </p>
        )}
      </div>

      {compat && colori && (
        <div
          className="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100"
          role="img"
          aria-label={`Compatibilità ${compat.punteggio}%`}
        >
          <div
            className={cn("h-full rounded-full transition-[width]", colori.bar)}
            style={{ width: `${compat.punteggio}%` }}
          />
        </div>
      )}

      {!compat && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
          <Link
            to="/app/azienda"
            className="font-medium underline underline-offset-2 hover:text-amber-900"
          >
            Importa la tua azienda
          </Link>{" "}
          da P.IVA per vedere quanto sei compatibile con questo bando.
        </p>
      )}

      <ul className="mt-4 space-y-3">
        <Requisito nome="Regioni" voci={voci.regioni} dim={dims?.regioni} valutabile={!!compat} />
        <Requisito nome="Codici ATECO" voci={voci.ateco} dim={dims?.ateco} valutabile={!!compat} />
        <Requisito nome="Settori" voci={voci.settori} dim={dims?.settori} valutabile={!!compat} />
        <Requisito
          nome="Beneficiari"
          voci={voci.beneficiari}
          dim={dims?.beneficiari}
          valutabile={!!compat}
        />
      </ul>
    </Card>
  );
}
