import { FileText } from "lucide-react";
import type { FacetKey } from "../../hooks/useBandiFilters";
import { BandoRow, BandoRowSkeleton } from "../bandi/BandoRow";
import { LinkButton } from "../ui/Button";
import { IconChip } from "../ui/IconChip";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { linkBandiAdatti, useBandiAdatti, usePresetPerTe } from "./datiHome";

/** Quante righe nella Home. */
const RIGHE = 3;

/** Le righe: montato solo con un preset, così la query parte solo allora.
 *  La chiave di cache è la stessa dell'elenco con quei filtri. */
function RigheAdatte({ preset }: { preset: Record<FacetKey, number[]> }) {
  const { data, isPending, isError, refetch } = useBandiAdatti(preset);

  if (isPending) {
    return (
      <ul className="flex flex-col gap-3">
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
        area="bandi"
        action={
          <LinkButton to="/app/preferenze" variant="secondary">
            Scegli gli interessi sui bandi
          </LinkButton>
        }
      />
    );
  }
  return (
    <ul className="flex flex-col gap-3">
      {righe.map((bando) => (
        <BandoRow key={bando.id} bando={bando} />
      ))}
    </ul>
  );
}

/** «Nuovi bandi adatti alla tua azienda»: le prime righe dell'elenco con il
 *  preset «per te» (dati reali dell'azienda + interessi), dai più recenti. Le
 *  righe sono le card dell'elenco dei bandi: il blocco non ha un riquadro suo. */
export function BandiAdatti() {
  const { preset, haPreset, inAttesa } = usePresetPerTe();

  return (
    <Section aria-label="Nuovi bandi adatti alla tua azienda">
      <SectionHeader
        className="items-center"
        titolo={
          <span className="flex items-center gap-3">
            <IconChip icon={FileText} area="bandi" size="sm" />
            Nuovi bandi adatti alla tua azienda
          </span>
        }
        azione={
          haPreset && (
            <TextLink to={linkBandiAdatti(preset)} className="text-small font-medium">
              Vedi tutti i bandi adatti
            </TextLink>
          )
        }
      />
      {inAttesa ? (
        <Skeleton className="h-24 w-full rounded-panel" />
      ) : haPreset ? (
        <RigheAdatte preset={preset} />
      ) : (
        <EmptyState
          title="Dicci che cosa cerchi."
          description="Completa i dati aziendali o scegli gli interessi sui bandi: qui compariranno i bandi adatti alla tua azienda."
          area="bandi"
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
