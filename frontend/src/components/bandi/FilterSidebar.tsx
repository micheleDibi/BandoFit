import { SlidersHorizontal } from "lucide-react";
import { useId } from "react";
import type { FiltroPartenariato, Lookups } from "../../types";
import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { useFunzioni } from "../../hooks/useFunzioni";
import { PARTENARIATO_COPY } from "../../lib/copy";
import { Button } from "../ui/Button";
import { FacetGroup } from "./FacetGroup";
import { Skeleton } from "../ui/states";

const STATI = [
  { id: "aperto", label: "Aperto" },
  { id: "in apertura prossimamente", label: "In apertura prossimamente" },
  { id: "chiuso", label: "Chiuso" },
];

const OPZIONI_PARTENARIATO: { valore: FiltroPartenariato | null; label: string }[] = [
  { valore: null, label: PARTENARIATO_COPY.filtroTutti },
  { valore: "ammesso", label: PARTENARIATO_COPY.filtro.ammesso },
  { valore: "obbligatorio", label: PARTENARIATO_COPY.filtro.obbligatorio },
];

export function FilterSidebar({
  lookups,
  filters,
  onToggleFacet,
  onToggleStato,
  onUpdate,
  onReset,
  activeCount,
}: {
  lookups: Lookups | undefined;
  filters: BandiFilterState;
  onToggleFacet: (key: FacetKey, id: number) => void;
  onToggleStato: (stato: string) => void;
  onUpdate: (changes: Partial<BandiFilterState>) => void;
  onReset: () => void;
  activeCount: number;
}) {
  const { partenariatiAttivo } = useFunzioni();
  // La sidebar è montata due volte (desktop e drawer mobile): nomi e id unici,
  // altrimenti i radio delle due copie farebbero un solo gruppo.
  const partenariatoId = useId();

  if (!lookups) {
    return (
      <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-card">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
        <span className="inline-flex items-center gap-2 font-display text-sm font-semibold text-slate-900">
          <SlidersHorizontal className="size-4 text-brand-500" aria-hidden />
          Filtri
        </span>
        {activeCount > 0 && (
          <Button variant="ghost" size="sm" onClick={onReset} className="text-brand-600">
            Azzera ({activeCount})
          </Button>
        )}
      </div>

      <div className="px-2 py-1">
        {/* Stato */}
        <div className="border-b border-slate-100 px-2 py-3">
          <p className="text-sm font-semibold text-slate-800">Stato</p>
          <ul className="mt-2 space-y-0.5">
            {STATI.map((s) => (
              <li key={s.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-50">
                  <input
                    type="checkbox"
                    checked={filters.stato.includes(s.id)}
                    onChange={() => onToggleStato(s.id)}
                    className="size-4 cursor-pointer accent-brand-500"
                  />
                  {s.label}
                </label>
              </li>
            ))}
          </ul>
        </div>

        {/* Partenariato (modulo partenariati): vale solo sui bandi già
            analizzati, quindi lo si dice accanto alle opzioni. */}
        {partenariatiAttivo && (
          <div className="border-b border-slate-100 px-2 py-3">
            <fieldset aria-describedby={`${partenariatoId}-nota`}>
              <legend className="text-sm font-semibold text-slate-800">
                {PARTENARIATO_COPY.filtroTitolo}
              </legend>
              <ul className="mt-2 space-y-0.5">
                {OPZIONI_PARTENARIATO.map((opzione) => (
                  <li key={opzione.valore ?? "tutti"}>
                    <label className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1.5 text-sm text-slate-600 transition-colors hover:bg-slate-50">
                      <input
                        type="radio"
                        name={`${partenariatoId}-partenariato`}
                        value={opzione.valore ?? ""}
                        checked={filters.partenariato === opzione.valore}
                        onChange={() => onUpdate({ partenariato: opzione.valore })}
                        className="size-4 cursor-pointer accent-brand-500"
                      />
                      {opzione.label}
                    </label>
                  </li>
                ))}
              </ul>
              <p id={`${partenariatoId}-nota`} className="mt-1.5 px-1.5 text-xs text-slate-500">
                {PARTENARIATO_COPY.filtroNota}
              </p>
            </fieldset>
          </div>
        )}

        <FacetGroup
          title="Tipologia"
          options={lookups.tipologie_bando.map((t) => ({ id: t.id, label: t.nome }))}
          selected={filters.tipologie}
          onToggle={(id) => onToggleFacet("tipologie", id)}
          defaultOpen
        />
        <FacetGroup
          title="Regioni"
          options={lookups.regioni.map((r) => ({ id: r.id, label: r.nome }))}
          selected={filters.regioni}
          onToggle={(id) => onToggleFacet("regioni", id)}
          searchable
        />
        <FacetGroup
          title="Settori"
          options={lookups.settori.map((s) => ({ id: s.id, label: s.nome }))}
          selected={filters.settori}
          onToggle={(id) => onToggleFacet("settori", id)}
          searchable
        />
        <FacetGroup
          title="Beneficiari"
          options={lookups.beneficiari.map((b) => ({ id: b.id, label: b.nome }))}
          selected={filters.beneficiari}
          onToggle={(id) => onToggleFacet("beneficiari", id)}
          searchable
        />
        <FacetGroup
          title="Codici ATECO"
          options={lookups.codici_ateco.map((c) => ({
            id: c.id,
            label: c.codice,
            sublabel: c.descrizione ?? undefined,
          }))}
          selected={filters.ateco}
          onToggle={(id) => onToggleFacet("ateco", id)}
          searchable
        />
        <FacetGroup
          title="Modalità di erogazione"
          options={lookups.modalita_erogazione.map((m) => ({ id: m.id, label: m.nome }))}
          selected={filters.modalita}
          onToggle={(id) => onToggleFacet("modalita", id)}
        />
        <FacetGroup
          title="Programmi"
          options={lookups.programmi.map((p) => ({ id: p.id, label: p.nome }))}
          selected={filters.programmi}
          onToggle={(id) => onToggleFacet("programmi", id)}
          searchable
        />

        {/* Importo */}
        <div className="border-b border-slate-100 px-2 py-3">
          <p className="text-sm font-semibold text-slate-800">Importo totale (€)</p>
          <div className="mt-2 flex items-center gap-2">
            <input
              type="number"
              min={0}
              placeholder="Min"
              aria-label="Importo minimo in euro"
              value={filters.importo_min ?? ""}
              onChange={(e) =>
                onUpdate({ importo_min: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })
              }
              className="tabular h-9 w-full rounded-md border border-slate-200 px-2 text-sm focus:border-brand-400 focus:outline-none"
            />
            <span className="text-slate-400">–</span>
            <input
              type="number"
              min={0}
              placeholder="Max"
              aria-label="Importo massimo in euro"
              value={filters.importo_max ?? ""}
              onChange={(e) =>
                onUpdate({ importo_max: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })
              }
              className="tabular h-9 w-full rounded-md border border-slate-200 px-2 text-sm focus:border-brand-400 focus:outline-none"
            />
          </div>
        </div>

        {/* Scadenza */}
        <div className="px-2 py-3">
          <p className="text-sm font-semibold text-slate-800">Scadenza</p>
          <select
            value={filters.scade_entro_giorni ?? ""}
            onChange={(e) =>
              onUpdate({ scade_entro_giorni: e.target.value === "" ? null : Number(e.target.value) })
            }
            aria-label="Scade entro"
            className="mt-2 h-9 w-full cursor-pointer rounded-md border border-slate-200 bg-white px-2 text-sm focus:border-brand-400 focus:outline-none"
          >
            <option value="">Qualsiasi scadenza</option>
            <option value="7">Entro 7 giorni</option>
            <option value="15">Entro 15 giorni</option>
            <option value="30">Entro 30 giorni</option>
            <option value="60">Entro 60 giorni</option>
            <option value="90">Entro 90 giorni</option>
          </select>
        </div>
      </div>
    </div>
  );
}
