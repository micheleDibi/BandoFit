import { useState } from "react";
import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { cn } from "../../lib/cn";
import { formatEur } from "../../lib/format";
import type { Lookups } from "../../types";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { TextField } from "../ui/Field";
import { Filter } from "../ui/Filter";
import { usePopover } from "../ui/Popover";
import { RadioGroup } from "../ui/RadioGroup";
import { Skeleton } from "../ui/states";
import { FacetGroup } from "./FacetGroup";

export const STATI = [
  { id: "aperto", label: "Aperto" },
  { id: "in apertura prossimamente", label: "In apertura" },
  { id: "chiuso", label: "Chiuso" },
] as const;

const SCADENZE = [
  { id: "", label: "Qualsiasi scadenza" },
  { id: "7", label: "Entro 7 giorni" },
  { id: "15", label: "Entro 15 giorni" },
  { id: "30", label: "Entro 30 giorni" },
  { id: "60", label: "Entro 60 giorni" },
  { id: "90", label: "Entro 90 giorni" },
] as const;

/** Props comuni ai controlli dei filtri: stato dall'URL e callback di `useBandiFilters`. */
export interface FiltriControlli {
  filters: BandiFilterState;
  onToggleFacet: (key: FacetKey, id: number) => void;
  onToggleStato: (stato: string) => void;
  onUpdate: (changes: Partial<BandiFilterState>) => void;
}

/** Riassunto di una faccetta per il pulsante-filtro: il nome se è una sola
 *  scelta, altrimenti quante sono. */
function valoreFaccetta(ids: number[], items: { id: number; nome: string }[]): string | undefined {
  if (ids.length === 0) return undefined;
  if (ids.length === 1) return items.find((i) => i.id === ids[0])?.nome ?? "1";
  return String(ids.length);
}

function valoreStato(stato: string[]): string | undefined {
  if (stato.length === 0) return undefined;
  if (stato.length === 1) return STATI.find((s) => s.id === stato[0])?.label ?? "1";
  return String(stato.length);
}

export function valoreImporto(min: number | null, max: number | null): string | undefined {
  if (min !== null && max !== null) return `da ${formatEur(min)} a ${formatEur(max)}`;
  if (min !== null) return `da ${formatEur(min)}`;
  if (max !== null) return `fino a ${formatEur(max)}`;
  return undefined;
}

/** Caselle dello stato del bando. */
export function StatoOpzioni({
  filters,
  onToggleStato,
  titoloVisibile = true,
}: Pick<FiltriControlli, "filters" | "onToggleStato"> & { titoloVisibile?: boolean }) {
  return (
    <fieldset className="flex flex-col gap-1.5">
      <legend className={cn("mb-2 text-title-group text-ink", !titoloVisibile && "sr-only")}>
        Stato
      </legend>
      {STATI.map((s) => (
        <Checkbox
          key={s.id}
          label={s.label}
          checked={filters.stato.includes(s.id)}
          onChange={() => onToggleStato(s.id)}
        />
      ))}
    </fieldset>
  );
}

/** Scadenza entro N giorni: una scelta sola. `nome` distingue le copie
 *  (pannello della barra e cassetto) così i radio non fanno un solo gruppo. */
export function ScadenzaOpzioni({
  filters,
  onUpdate,
  nome,
}: Pick<FiltriControlli, "filters" | "onUpdate"> & { nome: string }) {
  return (
    <RadioGroup
      nome={nome}
      legend="Scadenza"
      opzioni={SCADENZE}
      valore={filters.scade_entro_giorni === null ? "" : String(filters.scade_entro_giorni)}
      onChange={(id) => onUpdate({ scade_entro_giorni: id === "" ? null : Number(id) })}
    />
  );
}

/** Cifre dell'importo scritte nei campi ma non ancora applicate. */
export type BozzaImporto = Pick<BandiFilterState, "importo_min" | "importo_max">;

/** Importo totale da/a: si scrive e si applica con un pulsante, per non
 *  interrogare il catalogo a ogni cifra. Chi lo monta gli dà una `key` con i
 *  valori dell'URL, così un azzeramento esterno svuota anche i campi.
 *  `onBozza` riceve le cifre scritte e non applicate (null dopo «Applica» o
 *  «Azzera»): il cassetto le applica quando si chiude. */
export function ImportoCampi({
  filters,
  onUpdate,
  onApplicato,
  onBozza,
  titoloVisibile = true,
}: Pick<FiltriControlli, "filters" | "onUpdate"> & {
  onApplicato?: () => void;
  onBozza?: (bozza: BozzaImporto | null) => void;
  titoloVisibile?: boolean;
}) {
  const [min, setMin] = useState(filters.importo_min === null ? "" : String(filters.importo_min));
  const [max, setMax] = useState(filters.importo_max === null ? "" : String(filters.importo_max));

  const numero = (v: string): number | null => {
    if (v.trim() === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(0, Math.floor(n)) : null;
  };

  const applica = () => {
    onUpdate({ importo_min: numero(min), importo_max: numero(max) });
    onBozza?.(null);
    onApplicato?.();
  };

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        applica();
      }}
    >
      <fieldset className="flex flex-col gap-2">
        <legend className={cn("mb-2 text-title-group text-ink", !titoloVisibile && "sr-only")}>
          Importo totale
        </legend>
        <div className="grid grid-cols-2 gap-2">
          <TextField
            label="Da (€)"
            type="number"
            min={0}
            inputMode="numeric"
            value={min}
            onChange={(e) => {
              setMin(e.target.value);
              onBozza?.({ importo_min: numero(e.target.value), importo_max: numero(max) });
            }}
            className="tabular-nums"
          />
          <TextField
            label="A (€)"
            type="number"
            min={0}
            inputMode="numeric"
            value={max}
            onChange={(e) => {
              setMax(e.target.value);
              onBozza?.({ importo_min: numero(min), importo_max: numero(e.target.value) });
            }}
            className="tabular-nums"
          />
        </div>
      </fieldset>
      <div className="flex gap-2">
        <Button type="submit" variant="secondary" size="sm">
          Applica
        </Button>
        {(filters.importo_min !== null || filters.importo_max !== null) && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setMin("");
              setMax("");
              onUpdate({ importo_min: null, importo_max: null });
              onBozza?.(null);
              onApplicato?.();
            }}
          >
            Azzera
          </Button>
        )}
      </div>
    </form>
  );
}

/** Dentro il pannello del filtro «Importo»: «Applica» chiude anche il pannello. */
function ImportoPannello(props: Pick<FiltriControlli, "filters" | "onUpdate">) {
  const { chiudi } = usePopover();
  return <ImportoCampi {...props} onApplicato={chiudi} titoloVisibile={false} />;
}

export interface FiltriBandiProps extends FiltriControlli {
  lookups: Lookups | undefined;
  onReset: () => void;
  activeCount: number;
  /** Quante scelte attive stanno nel cassetto («Altri filtri»). */
  altriAttivi: number;
  altriAperti: boolean;
  onAltri: () => void;
  className?: string;
}

/** La riga dei filtri a menu dell'elenco (tavola `Main`): Stato, Regione,
 *  Tipologia, Beneficiari, Importo, Scadenza, «Altri filtri» (il cassetto) e
 *  «Azzera i filtri». Ogni scelta si applica subito nell'URL. */
export function FiltriBandi({
  lookups,
  filters,
  onToggleFacet,
  onToggleStato,
  onUpdate,
  onReset,
  activeCount,
  altriAttivi,
  altriAperti,
  onAltri,
  className,
}: FiltriBandiProps) {
  if (!lookups) {
    return (
      <div className={cn("flex flex-wrap items-center gap-2", className)} aria-hidden>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-24" />
        ))}
      </div>
    );
  }

  const chiaveImporto = `${filters.importo_min ?? ""}-${filters.importo_max ?? ""}`;

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <Filter label="Stato" attivo={filters.stato.length > 0} valore={valoreStato(filters.stato)}>
        <StatoOpzioni filters={filters} onToggleStato={onToggleStato} titoloVisibile={false} />
      </Filter>
      <Filter
        label="Regione"
        attivo={filters.regioni.length > 0}
        valore={valoreFaccetta(filters.regioni, lookups.regioni)}
      >
        <FacetGroup
          title="Regione"
          titoloVisibile={false}
          options={lookups.regioni.map((r) => ({ id: r.id, label: r.nome }))}
          selected={filters.regioni}
          onToggle={(id) => onToggleFacet("regioni", id)}
          searchable
          className="w-64"
        />
      </Filter>
      <Filter
        label="Tipologia"
        attivo={filters.tipologie.length > 0}
        valore={valoreFaccetta(filters.tipologie, lookups.tipologie_bando)}
      >
        <FacetGroup
          title="Tipologia"
          titoloVisibile={false}
          options={lookups.tipologie_bando.map((t) => ({ id: t.id, label: t.nome }))}
          selected={filters.tipologie}
          onToggle={(id) => onToggleFacet("tipologie", id)}
          className="w-64"
        />
      </Filter>
      <Filter
        label="Beneficiari"
        attivo={filters.beneficiari.length > 0}
        valore={valoreFaccetta(filters.beneficiari, lookups.beneficiari)}
      >
        <FacetGroup
          title="Beneficiari"
          titoloVisibile={false}
          options={lookups.beneficiari.map((b) => ({ id: b.id, label: b.nome }))}
          selected={filters.beneficiari}
          onToggle={(id) => onToggleFacet("beneficiari", id)}
          searchable
          className="w-64"
        />
      </Filter>
      <Filter
        label="Importo"
        attivo={filters.importo_min !== null || filters.importo_max !== null}
        valore={valoreImporto(filters.importo_min, filters.importo_max)}
      >
        <ImportoPannello key={chiaveImporto} filters={filters} onUpdate={onUpdate} />
      </Filter>
      <Filter
        label="Scadenza"
        attivo={filters.scade_entro_giorni !== null}
        valore={
          filters.scade_entro_giorni === null
            ? undefined
            : `entro ${filters.scade_entro_giorni} giorni`
        }
      >
        <ScadenzaOpzioni filters={filters} onUpdate={onUpdate} nome="scadenza-barra" />
      </Filter>
      <Button
        type="button"
        variant="ghost"
        className="h-9"
        onClick={onAltri}
        aria-haspopup="dialog"
        aria-expanded={altriAperti}
      >
        Altri filtri{altriAttivi > 0 ? ` (${altriAttivi})` : ""}
      </Button>
      {activeCount > 0 && (
        <Button type="button" variant="ghost" className="ml-auto h-9" onClick={onReset}>
          Azzera i filtri
        </Button>
      )}
    </div>
  );
}
