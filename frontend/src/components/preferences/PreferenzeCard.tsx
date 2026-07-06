import { BadgeCheck, Sparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../../hooks/usePreferences";
import { useLookups } from "../../hooks/useLookups";
import { apiErrorMessage } from "../../lib/api";
import type { Lookups, Preferences } from "../../types";
import { FacetGroup, type FacetOption } from "../bandi/FacetGroup";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/states";

type PrefKey = keyof Preferences;

interface FacetDef {
  key: PrefKey;
  title: string;
  options: (lookups: Lookups) => FacetOption[];
}

const toOptions = (items: Array<{ id: number; nome: string }>): FacetOption[] =>
  items.map((i) => ({ id: i.id, label: i.nome }));

const FACETS: FacetDef[] = [
  { key: "codici_ateco", title: "Codici ATECO", options: (l) => l.codici_ateco.map((a) => ({ id: a.id, label: a.codice, sublabel: a.descrizione ?? undefined })) },
  { key: "regioni", title: "Regioni", options: (l) => toOptions(l.regioni) },
  { key: "settori", title: "Settori", options: (l) => toOptions(l.settori) },
  { key: "beneficiari", title: "Beneficiari", options: (l) => toOptions(l.beneficiari) },
  { key: "tipologie", title: "Tipologie di bando", options: (l) => toOptions(l.tipologie_bando) },
  { key: "modalita", title: "Modalità di erogazione", options: (l) => toOptions(l.modalita_erogazione) },
  { key: "programmi", title: "Programmi", options: (l) => toOptions(l.programmi) },
];

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && [...a].sort().join(",") === [...b].sort().join(",");

/** Preferenze bandi PERSONALI: valori seguiti in aggiunta a quelli reali
 * dell'azienda, usati dal preset «Bandi per te» (e dalle future notifiche). */
export function PreferenzeCard() {
  const { data: saved, isPending } = usePreferences();
  const { data: lookups } = useLookups();
  const savePreferences = useSavePreferences();

  const [form, setForm] = useState<Preferences>(EMPTY_PREFERENCES);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (saved) setForm(saved);
  }, [saved]);

  const dirty = useMemo(
    () => !!saved && FACETS.some(({ key }) => !sameSet(form[key], saved[key])),
    [form, saved],
  );

  const selectedChips = useMemo(() => {
    if (!lookups) return [];
    return FACETS.flatMap(({ key, title, options }) => {
      const byId = new Map(options(lookups).map((o) => [o.id, o]));
      return form[key].map((id) => ({
        key,
        id,
        label: byId.get(id)?.label ?? String(id),
        group: title,
      }));
    });
  }, [form, lookups]);

  if (isPending) {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-4 h-24 w-full" />
      </Card>
    );
  }

  const toggle = (key: PrefKey, id: number) =>
    setForm((f) => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter((x) => x !== id) : [...f[key], id],
    }));

  const handleSave = async () => {
    setSavedFlash(false);
    try {
      await savePreferences.mutateAsync(form);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 3000);
    } catch {
      // errore mostrato sotto
    }
  };

  return (
    <Card className="p-6">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Sparkles className="size-4 text-brand-500" aria-hidden />
        Preferenze bandi
      </h2>
      <p className="mt-1 text-sm text-slate-500">
        Oltre ai dati reali della tua azienda, puoi seguire altri valori (es. un codice
        ATECO in più): li useremo per suggerirti i bandi giusti con «Bandi per te».
      </p>

      {selectedChips.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {selectedChips.map((chip) => (
            <button
              key={`${chip.key}-${chip.id}`}
              type="button"
              onClick={() => toggle(chip.key, chip.id)}
              title={`${chip.group}: rimuovi`}
              className="inline-flex cursor-pointer items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200 transition-colors hover:bg-brand-100"
            >
              {chip.label}
              <X className="size-3" aria-hidden />
            </button>
          ))}
        </div>
      )}

      <div className="mt-4 rounded-xl border border-slate-200 px-3 py-1">
        {lookups ? (
          FACETS.map(({ key, title, options }) => (
            <FacetGroup
              key={key}
              title={title}
              options={options(lookups)}
              selected={form[key]}
              onToggle={(id) => toggle(key, id)}
              searchable
            />
          ))
        ) : (
          <div className="space-y-2 py-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button onClick={handleSave} loading={savePreferences.isPending} disabled={!dirty}>
          Salva preferenze
        </Button>
        {savedFlash && (
          <span
            className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-600"
            role="status"
          >
            <BadgeCheck className="size-4" aria-hidden />
            Salvate
          </span>
        )}
        {savePreferences.isError && (
          <span className="text-sm text-red-600" role="alert">
            {apiErrorMessage(savePreferences.error)}
          </span>
        )}
      </div>
    </Card>
  );
}
