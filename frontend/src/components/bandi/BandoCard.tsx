import { Banknote, Building2, MapPin } from "lucide-react";
import { Link } from "react-router-dom";
import { formatEur } from "../../lib/format";
import type { BandoListItem } from "../../types";
import { Badge } from "../ui/Badge";
import { ScadenzaBadge, StatoBadge } from "./badges";

export function BandoCard({ bando }: { bando: BandoListItem }) {
  const titolo = bando.titolo_breve ?? bando.titolo ?? "Bando senza titolo";
  const regioniVisibili = bando.regioni.slice(0, 2);
  const regioniExtra = bando.regioni.length - regioniVisibili.length;

  return (
    <Link
      to={`/app/bandi/${bando.slug}`}
      className="group block rounded-xl border border-slate-200 bg-white p-5 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-card-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatoBadge stato={bando.stato_bando} />
        {bando.tipologia && <Badge tone="brand">{bando.tipologia.nome}</Badge>}
        {bando.modalita_erogazione && <Badge tone="slate">{bando.modalita_erogazione.nome}</Badge>}
      </div>

      <h3 className="mt-3 font-display text-base font-semibold text-slate-900 transition-colors group-hover:text-brand-700">
        {titolo}
      </h3>

      {bando.descrizione_breve && (
        <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-slate-500">
          {bando.descrizione_breve}
        </p>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-slate-600">
        {bando.importo_totale_eur !== null && (
          <span className="tabular inline-flex items-center gap-1.5 font-medium">
            <Banknote className="size-4 text-brand-500" aria-hidden />
            {formatEur(bando.importo_totale_eur)}
          </span>
        )}
        {bando.ente_erogatore && (
          <span className="inline-flex max-w-60 items-center gap-1.5 truncate">
            <Building2 className="size-4 shrink-0 text-slate-400" aria-hidden />
            {bando.ente_erogatore}
          </span>
        )}
        {regioniVisibili.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <MapPin className="size-4 shrink-0 text-slate-400" aria-hidden />
            {regioniVisibili.map((r) => r.nome).join(", ")}
            {regioniExtra > 0 && <span className="text-slate-400">+{regioniExtra}</span>}
          </span>
        )}
      </div>

      <div className="mt-3 border-t border-slate-100 pt-3">
        <ScadenzaBadge dataScadenza={bando.data_scadenza} />
      </div>
    </Link>
  );
}
