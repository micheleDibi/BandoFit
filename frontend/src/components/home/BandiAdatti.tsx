import { useMemo } from "react";
import { useBandi } from "../../hooks/useBandi";
import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { useCompanyFacets } from "../../hooks/useCompany";
import { usePreferences } from "../../hooks/usePreferences";
import { buildBandiPerTePreset, presetHasValues, presetSearchParams } from "../../lib/bandiPreset";
import { BandoRow, BandoRowSkeleton } from "../bandi/BandoRow";
import { LinkButton } from "../ui/Button";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

/** Quante righe nella Home. */
const RIGHE = 3;

/** Filtri di default dell'elenco (stessa forma di `useBandiFilters`): il
 *  preset «per te» si somma a questi. Solo bandi aperti o in apertura: senza
 *  filtro di stato il catalogo mette i chiusi in coda ma non li esclude, e con
 *  un preset stretto finirebbero fra i «nuovi bandi adatti». */
const FILTRI_BASE: BandiFilterState = {
  q: "",
  stato: ["aperto", "in apertura prossimamente"],
  tipologie: [],
  modalita: [],
  programmi: [],
  regioni: [],
  settori: [],
  beneficiari: [],
  ateco: [],
  importo_min: null,
  importo_max: null,
  scade_entro_giorni: null,
  partenariato: null,
  sort: "pubblicazione_desc",
  page: 1,
};

/** Le righe: montato solo con un preset, così la query parte solo allora.
 *  La chiave di cache è la stessa dell'elenco con quei filtri. */
function RigheAdatte({ preset }: { preset: Record<FacetKey, number[]> }) {
  const filters = useMemo(() => ({ ...FILTRI_BASE, ...preset }), [preset]);
  const { data, isPending, isError, refetch } = useBandi(filters);

  if (isPending) {
    return (
      <ul className="flex flex-col border-t border-line">
        {Array.from({ length: RIGHE }).map((_, i) => (
          <BandoRowSkeleton key={i} />
        ))}
      </ul>
    );
  }
  if (isError) {
    return (
      <ErrorState
        title="Non siamo riusciti a caricare i bandi adatti."
        onRetry={() => refetch()}
      />
    );
  }
  const righe = (data?.items ?? []).slice(0, RIGHE);
  if (righe.length === 0) {
    return (
      <EmptyState
        title="Nessun bando nuovo per la tua azienda in questo momento."
        description="Controlla tra qualche giorno, oppure allarga gli interessi sui bandi."
        action={
          <LinkButton to="/app/preferenze" variant="secondary">
            Scegli gli interessi sui bandi
          </LinkButton>
        }
      />
    );
  }
  return (
    <ul className="flex flex-col border-t border-line">
      {righe.map((bando) => (
        <BandoRow key={bando.id} bando={bando} />
      ))}
    </ul>
  );
}

/** «Nuovi bandi adatti alla tua azienda»: le prime righe dell'elenco con il
 *  preset «per te» (dati reali dell'azienda + interessi), dai più recenti. */
export function BandiAdatti() {
  const facets = useCompanyFacets();
  const preferenze = usePreferences();

  const preset = useMemo(
    () => buildBandiPerTePreset(facets.data, preferenze.data),
    [facets.data, preferenze.data],
  );
  const haPreset = presetHasValues(preset);
  const inAttesa = facets.isPending || preferenze.isPending;

  return (
    <Section aria-label="Nuovi bandi adatti alla tua azienda">
      <SectionHeader
        titolo="Nuovi bandi adatti alla tua azienda"
        azione={
          haPreset && (
            <TextLink to={`/app/bandi?${presetSearchParams(preset)}`} className="text-small font-medium">
              Vedi tutti i bandi adatti
            </TextLink>
          )
        }
      />
      {inAttesa ? (
        <Skeleton className="h-24 w-full" />
      ) : haPreset ? (
        <RigheAdatte preset={preset} />
      ) : (
        <EmptyState
          title="Dicci che cosa cerchi."
          description="Completa i dati aziendali o scegli gli interessi sui bandi: qui compariranno i bandi adatti alla tua azienda."
          action={
            <LinkButton to="/app/preferenze" variant="secondary">
              Scegli gli interessi sui bandi
            </LinkButton>
          }
        />
      )}
    </Section>
  );
}
