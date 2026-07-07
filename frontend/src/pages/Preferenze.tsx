import {
  ArrowRight,
  Building2,
  Coins,
  FileText,
  Flag,
  Hash,
  Landmark,
  MapPin,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { TagSelect, type TagSelectOption } from "../components/ui/TagSelect";
import { Skeleton } from "../components/ui/states";
import { useCompany } from "../hooks/useCompany";
import { useCompanyDossier } from "../hooks/useCompanyDossier";
import { useLookups } from "../hooks/useLookups";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../hooks/usePreferences";
import { apiErrorMessage } from "../lib/api";
import { buildBandiPerTePreset, presetHasValues, presetSearchParams } from "../lib/bandiPreset";
import type { Lookups, Preferences } from "../types";

type PrefKey = keyof Preferences;

interface FacetDef {
  key: PrefKey;
  title: string;
  description: string;
  icon: ReactNode;
  options: (lookups: Lookups) => TagSelectOption[];
}

const toOptions = (items: Array<{ id: number; nome: string }>): TagSelectOption[] =>
  items.map((i) => ({ id: i.id, label: i.nome }));

const FACETS: FacetDef[] = [
  {
    key: "codici_ateco",
    title: "Codici ATECO",
    description: "Segui altri settori di attività oltre a quello della tua azienda.",
    icon: <Hash className="size-4" aria-hidden />,
    options: (l) =>
      l.codici_ateco.map((a) => ({ id: a.id, label: a.codice, sublabel: a.descrizione ?? undefined })),
  },
  {
    key: "regioni",
    title: "Regioni",
    description: "Territori in cui operi o vuoi espanderti.",
    icon: <MapPin className="size-4" aria-hidden />,
    options: (l) => toOptions(l.regioni),
  },
  {
    key: "settori",
    title: "Settori",
    description: "Ambiti tematici dei bandi che ti interessano.",
    icon: <Landmark className="size-4" aria-hidden />,
    options: (l) => toOptions(l.settori),
  },
  {
    key: "beneficiari",
    title: "Beneficiari",
    description: "Categorie di destinatari in cui rientri o vuoi monitorare.",
    icon: <Users className="size-4" aria-hidden />,
    options: (l) => toOptions(l.beneficiari),
  },
  {
    key: "tipologie",
    title: "Tipologie di bando",
    description: "Es. contributi a fondo perduto, finanziamenti agevolati…",
    icon: <FileText className="size-4" aria-hidden />,
    options: (l) => toOptions(l.tipologie_bando),
  },
  {
    key: "modalita",
    title: "Modalità di erogazione",
    description: "Come vengono assegnate le risorse (sportello, graduatoria…).",
    icon: <Coins className="size-4" aria-hidden />,
    options: (l) => toOptions(l.modalita_erogazione),
  },
  {
    key: "programmi",
    title: "Programmi",
    description: "Programmi e fonti di finanziamento da seguire (PNRR, FESR…).",
    icon: <Flag className="size-4" aria-hidden />,
    options: (l) => toOptions(l.programmi),
  },
];

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && [...a].sort().join(",") === [...b].sort().join(",");

interface InheritedValue {
  id: number;
  label: string;
}

export default function Preferenze() {
  const { data: saved, isPending } = usePreferences();
  const { data: lookups } = useLookups();
  const { data: companyData } = useCompany();
  const { data: dossier } = useCompanyDossier();
  const savePreferences = useSavePreferences();

  const [form, setForm] = useState<Preferences>(EMPTY_PREFERENCES);
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (saved) setForm(saved);
  }, [saved]);

  const company = companyData?.company ?? null;

  // Valori EREDITATI dai dati aziendali: sempre inclusi in «Bandi per te»,
  // si modificano dai dati aziendali (non da qui).
  const inherited = useMemo<Record<PrefKey, InheritedValue[]>>(() => {
    const derivedBeneficiari = Array.isArray(dossier?.derived?.beneficiari)
      ? (dossier!.derived.beneficiari as Array<{ id: number; nome: string }>)
      : [];
    return {
      codici_ateco:
        company?.ateco_id != null
          ? [{
              id: company.ateco_id,
              label: `${company.ateco_codice ?? company.ateco_id}${company.ateco_descrizione ? ` — ${company.ateco_descrizione}` : ""}`,
            }]
          : [],
      regioni:
        company?.regione_id != null
          ? [{ id: company.regione_id, label: company.regione_nome ?? String(company.regione_id) }]
          : [],
      settori:
        company?.settore_id != null
          ? [{ id: company.settore_id, label: company.settore_nome ?? String(company.settore_id) }]
          : [],
      beneficiari: derivedBeneficiari.map((b) => ({ id: b.id, label: b.nome })),
      tipologie: [],
      modalita: [],
      programmi: [],
    };
  }, [company, dossier]);

  const hasInherited = Object.values(inherited).some((v) => v.length > 0);

  const dirty = useMemo(
    () => !!saved && FACETS.some(({ key }) => !sameSet(form[key], saved[key])),
    [form, saved],
  );

  const followedCount = FACETS.reduce((acc, { key }) => acc + form[key].length, 0);

  const preset = useMemo(
    () => buildBandiPerTePreset(company, saved ?? null),
    [company, saved],
  );

  if (isPending) {
    return (
      <div className="mx-auto max-w-6xl space-y-4">
        <Skeleton className="h-10 w-72" />
        <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-96 w-full" />
        </div>
      </div>
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
      // errore mostrato nella barra
    }
  };

  const labelOf = (facet: FacetDef, id: number): string => {
    if (!lookups) return String(id);
    return facet.options(lookups).find((o) => o.id === id)?.label ?? String(id);
  };

  return (
    <div className="mx-auto max-w-6xl pb-24">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            Preferenze bandi
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Il profilo della tua azienda è la base; qui aggiungi ciò che vuoi seguire in
            più. Insieme alimentano il filtro «Bandi per te» e, in futuro, notifiche e
            AI-check.
          </p>
        </div>
        {presetHasValues(preset) && (
          <LinkButton
            to={`/app/bandi?${presetSearchParams(preset)}`}
            variant="secondary"
            size="sm"
          >
            <Sparkles className="size-4" aria-hidden />
            Vedi i bandi per te
            <ArrowRight className="size-3.5" aria-hidden />
          </LinkButton>
        )}
      </div>

      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[320px_1fr]">
        {/* Colonna sinistra: il profilo ereditato dall'azienda */}
        <aside className="space-y-4 lg:sticky lg:top-20">
          <Card className="overflow-hidden">
            <div className="border-b border-slate-100 bg-gradient-to-br from-brand-50 to-white px-5 py-4">
              <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
                <Building2 className="size-4 text-brand-500" aria-hidden />
                La tua azienda
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                Valori reali dai dati aziendali: sono <strong>sempre inclusi</strong> in
                «Bandi per te» e si aggiornano da lì.
              </p>
            </div>
            <div className="space-y-3 px-5 py-4">
              {hasInherited ? (
                (
                  [
                    ["codici_ateco", "Codice ATECO"],
                    ["settori", "Settore"],
                    ["regioni", "Regione"],
                    ["beneficiari", "Beneficiari"],
                  ] as Array<[PrefKey, string]>
                ).map(([key, title]) =>
                  inherited[key].length ? (
                    <div key={key}>
                      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        {title}
                      </p>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {inherited[key].map((value) => (
                          <span
                            key={value.id}
                            className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-200"
                          >
                            <Building2 className="size-3 shrink-0 text-slate-400" aria-hidden />
                            <span className="truncate">{value.label}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null,
                )
              ) : (
                <p className="text-sm text-slate-400">
                  Nessun dato aziendale ancora: compila i dati o usa «Importa da P.IVA»
                  per partire dal profilo reale della tua azienda.
                </p>
              )}
              <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 pt-3 text-sm">
                {/* Dati, dossier e documenti vivono tutti nella pagina Azienda */}
                <Link to="/app/azienda" className="font-medium text-brand-600 hover:text-brand-700">
                  Dati aziendali →
                </Link>
              </div>
            </div>
          </Card>

          <Card className="px-5 py-4">
            <p className="text-sm text-slate-600">
              <span className="font-display text-2xl font-bold text-brand-600 tabular-nums">
                {followedCount}
              </span>{" "}
              {followedCount === 1 ? "valore seguito" : "valori seguiti"} in aggiunta al
              profilo aziendale
            </p>
          </Card>
        </aside>

        {/* Colonna destra: le preferenze per faccetta */}
        <div className="space-y-4">
          {FACETS.map((facet) => {
            const inheritedHere = inherited[facet.key];
            const inheritedIds = inheritedHere.map((v) => v.id);
            const extra = form[facet.key].filter((id) => !inheritedIds.includes(id));
            return (
              <Card key={facet.key} className="p-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
                    <span className="text-brand-500">{facet.icon}</span>
                    {facet.title}
                    {extra.length > 0 && <Badge tone="brand">{extra.length}</Badge>}
                  </h2>
                </div>
                <p className="mt-0.5 text-sm text-slate-500">{facet.description}</p>

                {(inheritedHere.length > 0 || extra.length > 0) && (
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {inheritedHere.map((value) => (
                      <span
                        key={`inh-${value.id}`}
                        title="Dai dati aziendali: sempre incluso"
                        className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-600 ring-1 ring-inset ring-slate-200"
                      >
                        <Building2 className="size-3 shrink-0 text-slate-400" aria-hidden />
                        <span className="truncate">{value.label}</span>
                      </span>
                    ))}
                    {extra.map((id) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => toggle(facet.key, id)}
                        title="Rimuovi"
                        className="inline-flex max-w-full cursor-pointer items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200 transition-colors hover:bg-brand-100"
                      >
                        <span className="truncate">{labelOf(facet, id)}</span>
                        <X className="size-3 shrink-0" aria-hidden />
                      </button>
                    ))}
                  </div>
                )}

                <div className="mt-3 max-w-md">
                  {lookups ? (
                    <TagSelect
                      label={`Aggiungi ${facet.title.toLowerCase()}`}
                      options={facet.options(lookups)}
                      values={form[facet.key]}
                      inherited={inheritedIds}
                      onToggle={(id) => toggle(facet.key, id)}
                    />
                  ) : (
                    <Skeleton className="h-10 w-full" />
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      </div>

      {/* Barra di salvataggio: compare solo con modifiche non salvate */}
      {(dirty || savedFlash || savePreferences.isError) && (
        <div className="fixed inset-x-0 bottom-0 z-40 border-t border-slate-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
            <p className="text-sm" role="status">
              {dirty ? (
                <span className="font-medium text-slate-700">Hai modifiche non salvate</span>
              ) : savedFlash ? (
                <span className="font-medium text-emerald-600">Preferenze salvate ✓</span>
              ) : (
                <span className="text-red-600" role="alert">
                  {apiErrorMessage(savePreferences.error)}
                </span>
              )}
            </p>
            {dirty && (
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  onClick={() => saved && setForm(saved)}
                  disabled={savePreferences.isPending}
                >
                  Annulla
                </Button>
                <Button onClick={handleSave} loading={savePreferences.isPending}>
                  Salva preferenze
                </Button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
