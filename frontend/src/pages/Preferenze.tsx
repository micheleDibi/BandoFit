import { useEffect, useMemo, useState } from "react";
import { Alert } from "../components/ui/Alert";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Chip } from "../components/ui/Chip";
import { InlineError } from "../components/ui/InlineError";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { ErrorState, Skeleton } from "../components/ui/states";
import { Switch } from "../components/ui/Switch";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { TagSelect, type TagSelectOption } from "../components/ui/TagSelect";
import { TextLink } from "../components/ui/TextLink";
import { useToast } from "../components/ui/Toast";
import { useAlertSettings, useSaveAlertSettings } from "../hooks/useAlertSettings";
import { useCompanyFacets } from "../hooks/useCompany";
import { useFunzioni } from "../hooks/useFunzioni";
import { useLookups } from "../hooks/useLookups";
import { usePartnerEmailSettings, useSalvaPartnerEmailSettings } from "../hooks/usePartenariati";
import { EMPTY_PREFERENCES, usePreferences, useSavePreferences } from "../hooks/usePreferences";
import { useTab } from "../hooks/useTab";
import { apiErrorMessage } from "../lib/api";
import { buildBandiPerTePreset, presetHasValues, presetSearchParams } from "../lib/bandiPreset";
import type { Lookups, PartnerEmailSettings, Preferences } from "../types";

type PrefKey = keyof Preferences;

interface FacetDef {
  key: PrefKey;
  title: string;
  description: string;
  options: (lookups: Lookups) => TagSelectOption[];
}

const toOptions = (items: Array<{ id: number; nome: string }>): TagSelectOption[] =>
  items.map((i) => ({ id: i.id, label: i.nome }));

const FACETS: FacetDef[] = [
  {
    key: "codici_ateco",
    title: "Codici ATECO",
    description: "Segui altri settori di attività oltre a quello della tua azienda.",
    options: (l) =>
      l.codici_ateco.map((a) => ({ id: a.id, label: a.codice, sublabel: a.descrizione ?? undefined })),
  },
  {
    key: "regioni",
    title: "Regioni",
    description: "Territori in cui operi o vuoi espanderti.",
    options: (l) => toOptions(l.regioni),
  },
  {
    key: "settori",
    title: "Settori",
    description: "Ambiti tematici dei bandi che ti interessano.",
    options: (l) => toOptions(l.settori),
  },
  {
    key: "beneficiari",
    title: "Beneficiari",
    description: "Categorie di destinatari in cui rientri o vuoi monitorare.",
    options: (l) => toOptions(l.beneficiari),
  },
  {
    key: "tipologie",
    title: "Tipologie di bando",
    description: "Es. contributi a fondo perduto, finanziamenti agevolati…",
    options: (l) => toOptions(l.tipologie_bando),
  },
  {
    key: "modalita",
    title: "Modalità di erogazione",
    description: "Come vengono assegnate le risorse (sportello, graduatoria…).",
    options: (l) => toOptions(l.modalita_erogazione),
  },
  {
    key: "programmi",
    title: "Programmi",
    description: "Programmi e fonti di finanziamento da seguire (PNRR, FESR…).",
    options: (l) => toOptions(l.programmi),
  },
];

const SCHEDE = [
  { id: "interessi", label: "Interessi sui bandi" },
  { id: "avvisi", label: "Avvisi email" },
] as const;
const IDS = SCHEDE.map((s) => s.id) as readonly (typeof SCHEDE)[number]["id"][];
const PREFISSO = "preferenze";

const sameSet = (a: number[], b: number[]) =>
  a.length === b.length && [...a].sort().join(",") === [...b].sort().join(",");

interface InheritedValue {
  id: number;
  label: string;
}

const valori = (n: number) => (n === 1 ? "1 valore" : `${n} valori`);

function descrizioneRitardo(giorni: number | null): string {
  if (giorni === 0) return "il giorno stesso della pubblicazione";
  if (giorni === 1) return "il giorno dopo la pubblicazione";
  return `dopo ${giorni} giorni dalla pubblicazione`;
}

/** Interruttore degli avvisi email sui nuovi bandi: stessa fonte di verità del
 *  link di disiscrizione presente in fondo a ogni email. */
function AvvisiBandiSection() {
  const { data: settings, isPending, isError, error, refetch } = useAlertSettings();
  const save = useSaveAlertSettings();
  const { mostra } = useToast();
  const [errore, setErrore] = useState<string | null>(null);

  const cambia = async (abilitati: boolean) => {
    setErrore(null);
    try {
      await save.mutateAsync({ abilitati });
      mostra({ testo: abilitati ? "Avvisi email attivati" : "Avvisi email disattivati" });
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Card className="sm:p-6">
      <Section aria-labelledby="preferenze-avvisi-bandi">
        <SectionHeader id="preferenze-avvisi-bandi" titolo="Avvisi email sui nuovi bandi" />
        {isPending ? (
          <Skeleton className="h-10 w-full" />
        ) : isError || !settings ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <InlineError>
              {apiErrorMessage(error, "Impossibile caricare le impostazioni degli avvisi email.")}
            </InlineError>
            <Button type="button" variant="secondary" size="sm" onClick={() => void refetch()}>
              Riprova
            </Button>
          </div>
        ) : settings.piano_include_alert ? (
          <>
            <Switch
              label="Ricevi gli avvisi via email"
              descrizione={`Quando esce un bando compatibile con la tua azienda te lo segnaliamo via email ${descrizioneRitardo(settings.ritardo_giorni)}.`}
              checked={settings.abilitati}
              disabled={save.isPending}
              onChange={(checked) => void cambia(checked)}
            />
            <p className="text-small text-ink-3">
              Puoi disattivarli quando vuoi, anche dal link in fondo a ogni email.
            </p>
            {errore && <InlineError>{errore}</InlineError>}
          </>
        ) : (
          <>
            <p className="text-body text-ink-2">
              Gli avvisi email sui nuovi bandi compatibili con la tua azienda sono inclusi nei
              piani a pagamento.
            </p>
            <div>
              <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
                Scopri i piani
              </LinkButton>
            </div>
          </>
        )}
      </Section>
    </Card>
  );
}

/** Email del modulo partenariati (solo a modulo acceso): il riepilogo
 *  settimanale delle call per te e le email sulle attività. Sono dell'utente,
 *  separate dagli avvisi sui bandi; ogni email ha il proprio link per
 *  disiscriversi. */
function EmailPartenariatiSection() {
  const { data: settings, isPending, isError, error, refetch } = usePartnerEmailSettings();
  const save = useSalvaPartnerEmailSettings();
  const { mostra } = useToast();
  const [errore, setErrore] = useState<string | null>(null);

  const cambia = async (campo: keyof PartnerEmailSettings, valore: boolean) => {
    if (!settings) return;
    setErrore(null);
    try {
      await save.mutateAsync({ ...settings, [campo]: valore });
      mostra({ testo: "Preferenze email salvate" });
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const voci: Array<{ campo: keyof PartnerEmailSettings; etichetta: string; nota: string }> = [
    {
      campo: "digest_abilitato",
      etichetta: "Riepilogo settimanale delle call per te",
      nota: "Il lunedì, se ci sono call nuove adatte alla tua azienda. Arriva solo per le aziende visibili come partner.",
    },
    {
      campo: "eventi_abilitati",
      etichetta: "Email sulle attività dei partenariati",
      nota: "Quando ricevi un invito, una candidatura, una risposta o nuovi messaggi.",
    },
  ];

  return (
    <Card className="sm:p-6">
      <Section aria-labelledby="preferenze-email-partenariati">
        <SectionHeader id="preferenze-email-partenariati" titolo="Email sui partenariati" />
        {isPending ? (
          <Skeleton className="h-16 w-full" />
        ) : isError || !settings ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <InlineError>
              {apiErrorMessage(error, "Impossibile caricare le preferenze email dei partenariati.")}
            </InlineError>
            <Button type="button" variant="secondary" size="sm" onClick={() => void refetch()}>
              Riprova
            </Button>
          </div>
        ) : (
          <>
            <p className="text-body text-ink-2">
              Gli avvisi nell'app arrivano comunque: qui scegli quali ricevere anche via email.
            </p>
            <div className="flex flex-col gap-4">
              {voci.map((v) => (
                <Switch
                  key={v.campo}
                  label={v.etichetta}
                  descrizione={v.nota}
                  checked={settings[v.campo]}
                  disabled={save.isPending}
                  onChange={(checked) => void cambia(v.campo, checked)}
                />
              ))}
            </div>
            <p className="text-small text-ink-3">
              Puoi disattivarle quando vuoi, anche dal link in fondo a ogni email.
            </p>
            {errore && <InlineError>{errore}</InlineError>}
          </>
        )}
      </Section>
    </Card>
  );
}

/** «Preferenze»: due schede (`?tab=`), «Interessi sui bandi» (predefinita) e
 *  «Avvisi email». Gli interessi si salvano con un pulsante; gli interruttori
 *  degli avvisi hanno effetto subito. */
export default function Preferenze() {
  const { tab, setTab } = useTab(IDS);
  const { partenariatiAttivo } = useFunzioni();
  const {
    data: saved,
    isPending,
    isError: prefsError,
    error: prefsErrorDetail,
    refetch: refetchPrefs,
  } = usePreferences();
  const { data: lookups } = useLookups();
  const { data: facets } = useCompanyFacets();
  const savePreferences = useSavePreferences();
  const { mostra } = useToast();

  const [form, setForm] = useState<Preferences>(EMPTY_PREFERENCES);

  useEffect(() => {
    if (saved) setForm(saved);
  }, [saved]);

  const optionOf = (facet: FacetDef, id: number): TagSelectOption | undefined =>
    lookups ? facet.options(lookups).find((o) => o.id === id) : undefined;

  const labelOf = (facet: FacetDef, id: number): string => optionOf(facet, id)?.label ?? String(id);

  /** Tra i valori scelti l'ATECO è il solo codice; per quelli dei dati aziendali
   *  c'è anche la descrizione, e un «62» nudo non direbbe niente. */
  const labelEsteso = (facet: FacetDef, id: number): string => {
    const option = optionOf(facet, id);
    if (!option) return String(id);
    return option.sublabel ? `${option.label} — ${option.sublabel}` : option.label;
  };

  // Valori EREDITATI dai dati aziendali: sempre inclusi in «Adatti alla tua
  // azienda», si modificano dai dati aziendali (non da qui). Vengono dai FACET,
  // non dai campi del form: tutte le sedi, non la sola sede legale, e le
  // divisioni ATECO secondarie oltre alla principale.
  const inherited = useMemo<Record<PrefKey, InheritedValue[]>>(() => {
    const byKey = Object.fromEntries(FACETS.map((f) => [f.key, f])) as Record<PrefKey, FacetDef>;
    const values = (key: PrefKey, ids: number[] | undefined): InheritedValue[] =>
      (ids ?? []).map((id) => ({ id, label: labelEsteso(byKey[key], id) }));
    return {
      codici_ateco: values("codici_ateco", facets?.ateco),
      regioni: values("regioni", facets?.regioni),
      settori: values("settori", facets?.settori),
      // Dichiarate sui dati aziendali (prima erano dedotte dalla visura).
      beneficiari: values("beneficiari", facets?.beneficiari),
      tipologie: [],
      modalita: [],
      programmi: [],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facets, lookups]);

  const hasInherited = Object.values(inherited).some((v) => v.length > 0);

  const dirty = useMemo(
    () => !!saved && FACETS.some(({ key }) => !sameSet(form[key], saved[key])),
    [form, saved],
  );

  const followedCount = FACETS.reduce((acc, { key }) => acc + form[key].length, 0);

  const preset = useMemo(
    () => buildBandiPerTePreset(facets, saved ?? null),
    [facets, saved],
  );

  const toggle = (key: PrefKey, id: number) =>
    setForm((f) => ({
      ...f,
      [key]: f[key].includes(id) ? f[key].filter((x) => x !== id) : [...f[key], id],
    }));

  const handleSave = async () => {
    try {
      await savePreferences.mutateAsync(form);
      mostra({ testo: "Preferenze salvate" });
    } catch {
      // errore mostrato nella barra di salvataggio
    }
  };

  const annulla = () => {
    if (saved) setForm(saved);
    savePreferences.reset();
  };

  return (
    <Page variante="sezioni">
      <PageHeader
        titolo="Preferenze"
        descrizione="Il profilo della tua azienda è la base: qui aggiungi ciò che vuoi seguire in più e scegli quali avvisi ricevere via email."
        area="account"
        azioni={
          presetHasValues(preset) && (
            <LinkButton
              to={`/app/bandi?${presetSearchParams(preset)}`}
              variant="inverse"
              size="sm"
            >
              Vedi i bandi adatti alla tua azienda
            </LinkButton>
          )
        }
      />

      <Tabs
        tabs={SCHEDE}
        attivo={tab}
        onChange={setTab}
        ariaLabel="Sezioni delle preferenze"
        prefisso={PREFISSO}
      />

      <TabPanel id="interessi" attivo={tab} prefisso={PREFISSO}>
        {isPending ? (
          <div className="flex flex-col gap-4" aria-hidden>
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-40 w-full rounded-panel" />
            <Skeleton className="h-40 w-full rounded-panel" />
          </div>
        ) : prefsError ? (
          <ErrorState
            title="Non siamo riusciti a caricare i tuoi interessi."
            message={apiErrorMessage(prefsErrorDetail)}
            onRetry={() => void refetchPrefs()}
          />
        ) : (
          <>
            <div className="flex flex-col gap-2">
              <p className="text-body text-ink-2">
                I valori dei dati aziendali sono sempre inclusi nei bandi adatti alla tua azienda
                e si aggiornano da lì. Contano tutte le sedi e tutti i codici ATECO del Registro
                Imprese, non solo la sede legale.{" "}
                <TextLink to="/app/azienda">Vai ai dati azienda</TextLink>
              </p>
              <p className="text-body text-ink">
                Stai seguendo{" "}
                <span className="font-semibold tabular-nums">{valori(followedCount)}</span> in
                aggiunta al profilo aziendale.
              </p>
            </div>

            {!hasInherited && (
              <Alert
                tono="info"
                azione={
                  <TextLink to="/app/azienda" className="text-small font-medium">
                    Vai ai dati azienda
                  </TextLink>
                }
              >
                Nessun dato aziendale ancora: compila i dati o usa «Importa da P.IVA» per partire
                dal profilo reale della tua azienda.
              </Alert>
            )}

            {FACETS.map((facet) => {
              const inheritedHere = inherited[facet.key];
              const inheritedIds = inheritedHere.map((v) => v.id);
              const extra = form[facet.key].filter((id) => !inheritedIds.includes(id));
              const titoloId = `preferenze-${facet.key}`;
              return (
                <Card key={facet.key} className="sm:p-6">
                  <Section aria-labelledby={titoloId}>
                    <SectionHeader
                      id={titoloId}
                      titolo={facet.title}
                      azione={
                        extra.length > 0 && (
                          <span className="text-small text-ink-2 tabular-nums">
                            {extra.length === 1 ? "1 scelto da te" : `${extra.length} scelti da te`}
                          </span>
                        )
                      }
                    />
                    <p className="text-body text-ink-2">{facet.description}</p>

                    {inheritedHere.length > 0 && (
                      <div className="flex flex-col gap-2">
                        <p className="text-small text-ink-3">Dai dati aziendali, sempre inclusi</p>
                        <ul className="flex flex-wrap gap-2">
                          {inheritedHere.map((value) => (
                            <li key={value.id} className="max-w-full">
                              <Chip>{value.label}</Chip>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {extra.length > 0 && (
                      <div className="flex flex-col gap-2">
                        <p className="text-small text-ink-3">Scelti da te</p>
                        <ul className="flex flex-wrap gap-2">
                          {extra.map((id) => (
                            <li key={id} className="max-w-full">
                              <Chip
                                onRemove={() => toggle(facet.key, id)}
                                label={`Rimuovi ${labelOf(facet, id)}`}
                              >
                                {labelOf(facet, id)}
                              </Chip>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    <div className="w-full sm:w-md">
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
                  </Section>
                </Card>
              );
            })}
          </>
        )}
      </TabPanel>

      <TabPanel id="avvisi" attivo={tab} prefisso={PREFISSO}>
        <AvvisiBandiSection />
        {partenariatiAttivo && <EmailPartenariatiSection />}
      </TabPanel>

      {/* Barra di salvataggio degli interessi: fuori dalle schede, così il
          segnale resta anche da «Avvisi email» (uscendo dalla pagina le
          modifiche si perderebbero). Resta in vista mentre si scorre; compare
          solo con modifiche non salvate (un salvataggio fallito le lascia tali,
          con l'errore sotto). In flusso e non `fixed`: non copre la barra laterale. */}
      {dirty && (
        <div className="sticky bottom-4 z-10 flex flex-col gap-2 rounded-panel border border-line bg-sheet px-5 py-3 shadow-overlay motion-safe:animate-entrata">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-body font-medium text-ink">
              Hai modifiche non salvate agli interessi sui bandi
            </p>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={annulla}
                disabled={savePreferences.isPending}
              >
                Annulla
              </Button>
              <Button type="button" onClick={handleSave} loading={savePreferences.isPending}>
                Salva le preferenze
              </Button>
            </div>
          </div>
          {savePreferences.isError && (
            <InlineError>{apiErrorMessage(savePreferences.error)}</InlineError>
          )}
        </div>
      )}
    </Page>
  );
}
