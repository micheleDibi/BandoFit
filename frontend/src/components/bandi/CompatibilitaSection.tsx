import { Check } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { scoreColorClasses } from "../../lib/scoreColor";
import type { BandoDetail, CompatibilitaDimensione } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";

interface Voce {
  id: number;
  label: string;
  title?: string;
}

/** Una dimensione del pre-check: le voci richieste dal bando, con evidenziate
 *  quelle in comune con l'azienda. `dim` assente = dimensione non valutata
 *  (l'azienda non ha quel dato, quindi non entra nel punteggio). */
function Dimensione({
  nome,
  voci,
  dim,
  valutabile,
  className,
}: {
  nome: string;
  voci: Voce[];
  dim?: CompatibilitaDimensione;
  valutabile: boolean;
  className?: string;
}) {
  if (voci.length === 0) return null;
  const matched = new Set(dim?.matched_ids ?? []);

  return (
    <div className={className}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="text-xs font-medium uppercase tracking-wide text-slate-400">{nome}</h3>
        {dim ? (
          <span className="tabular text-xs font-semibold text-slate-700">
            {dim.matched}/{dim.totale}
          </span>
        ) : (
          valutabile && (
            <span
              className="text-xs text-slate-400"
              title="La tua azienda non ha questo dato: la dimensione non entra nel punteggio."
            >
              non valutato
            </span>
          )
        )}
        {dim?.nazionale && <Badge tone="brand">Nazionale</Badge>}
      </div>

      <ul className="mt-2 flex flex-wrap gap-1.5">
        {voci.map((voce) => {
          const inComune = matched.has(voce.id);
          return (
            <li key={voce.id}>
              <span
                title={voce.title}
                className={cn(
                  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs ring-1 ring-inset",
                  inComune
                    ? "bg-emerald-50 font-medium text-emerald-700 ring-emerald-200"
                    : "bg-slate-100 text-slate-600 ring-slate-200",
                )}
              >
                {inComune && <Check className="size-3 shrink-0" aria-hidden />}
                {voce.label}
              </span>
            </li>
          );
        })}
      </ul>

      {dim?.nazionale && (
        <p className="mt-1.5 text-xs text-slate-500">
          Il bando è aperto a tutte le regioni: il territorio conta come pienamente in comune.
          Evidenziate quelle dove hai una sede.
        </p>
      )}
    </div>
  );
}

/** Sezione «pre-check»: il lato del bando (regioni, ATECO, settori, beneficiari)
 *  messo a confronto con i dati dell'azienda, tutte le sedi comprese. È il
 *  dettaglio del punteggio mostrato in elenco; non sostituisce l'AI-check. */
export function CompatibilitaSection({ bando }: { bando: BandoDetail }) {
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
    <Card id="compatibilita" className="mt-8 p-5 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-semibold text-slate-900">
            Compatibilità con la tua azienda
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-slate-500">
            Confronto immediato tra i requisiti di catalogo del bando e i dati della tua azienda,
            tutte le sedi comprese. È un'indicazione di quanto avete in comune: non sostituisce
            l'AI-check, che legge il testo del bando.
          </p>
        </div>
        {compat && colori && (
          <div className="shrink-0 text-right">
            <p className="tabular font-display text-2xl font-bold leading-none">
              <span className={colori.text}>{compat.matched}</span>
              <span className="text-slate-300">/{compat.totale}</span>
            </p>
            <p className="mt-1 text-xs text-slate-500">{compat.punteggio}% in comune</p>
          </div>
        )}
      </div>

      {compat && colori && (
        <div
          className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100"
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
        <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Importa la tua azienda da P.IVA per vedere quanto sei compatibile con questo bando:{" "}
          <Link
            to="/app/azienda"
            className="font-medium underline underline-offset-2 hover:text-amber-900"
          >
            vai ad Azienda
          </Link>
          .
        </p>
      )}

      {/* Regioni e Beneficiari sono le liste più lunghe: a piena larghezza,
          altrimenti le due colonne restano frastagliate. */}
      <div className="mt-6 grid gap-6 sm:grid-cols-2">
        <Dimensione
          nome="Regioni"
          voci={voci.regioni}
          dim={dims?.regioni}
          valutabile={!!compat}
          className="sm:col-span-2"
        />
        <Dimensione nome="Codici ATECO" voci={voci.ateco} dim={dims?.ateco} valutabile={!!compat} />
        <Dimensione nome="Settori" voci={voci.settori} dim={dims?.settori} valutabile={!!compat} />
        <Dimensione
          nome="Beneficiari"
          voci={voci.beneficiari}
          dim={dims?.beneficiari}
          valutabile={!!compat}
          className="sm:col-span-2"
        />
      </div>

      {compat && (
        <p className="mt-6 flex items-center gap-1.5 border-t border-slate-100 pt-3 text-xs text-slate-500">
          <Check className="size-3.5 shrink-0 text-emerald-600" aria-hidden />
          Evidenziate le voci in comune con la tua azienda.
        </p>
      )}
    </Card>
  );
}
