import { useMemo } from "react";
import { useBandi } from "../../hooks/useBandi";
import type { BandiFilterState, FacetKey } from "../../hooks/useBandiFilters";
import { useCompanyFacets } from "../../hooks/useCompany";
import { useConsulenze } from "../../hooks/useConsulenze";
import { useFunzioni } from "../../hooks/useFunzioni";
import { useNotifications } from "../../hooks/useNotifications";
import { useRiepilogoPartenariati } from "../../hooks/usePartenariati";
import { usePreferences } from "../../hooks/usePreferences";
import { useSavedBandi } from "../../hooks/useSavedBandi";
import { buildBandiPerTePreset, presetHasValues, presetSearchParams } from "../../lib/bandiPreset";
import { bandoInCorso, statoDelBando } from "../bandi/stato";
import type { Area } from "../ui/area";

/** I dati dei blocchi della Home, in un posto solo: i blocchi e la riga degli
 *  indicatori chiamano gli STESSI hook con le STESSE chiavi, quindi leggono la
 *  stessa cache di TanStack Query (nessuna chiamata in più). */

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

/** Il preset «per te» (dati reali dell'azienda + interessi). */
export function usePresetPerTe() {
  const facets = useCompanyFacets();
  const preferenze = usePreferences();

  const preset = useMemo(
    () => buildBandiPerTePreset(facets.data, preferenze.data),
    [facets.data, preferenze.data],
  );
  return {
    preset,
    haPreset: presetHasValues(preset),
    inAttesa: facets.isPending || preferenze.isPending,
  };
}

/** L'elenco dei bandi con il preset: da chiamare solo con un preset, così la
 *  query parte solo allora. La chiave di cache è la stessa dell'elenco con quei
 *  filtri. */
export function useBandiAdatti(preset: Record<FacetKey, number[]>) {
  const filters = useMemo(() => ({ ...FILTRI_BASE, ...preset }), [preset]);
  return useBandi(filters);
}

/** Il link all'elenco con gli STESSI filtri di `useBandiAdatti` (preset più lo
 *  `stato` di base), nel formato dell'URL letto da `useBandiFilters`: l'elenco
 *  mostra gli stessi bandi contati nella Home. Gli altri filtri di base sono i
 *  default dell'elenco e non vanno nell'URL. */
export function linkBandiAdatti(preset: Record<FacetKey, number[]>): string {
  const params = new URLSearchParams(presetSearchParams(preset));
  params.set("stato", FILTRI_BASE.stato.join(","));
  return `/app/bandi?${params.toString()}`;
}

/** «Prossime scadenze»: la prima pagina dei bandi salvati (20) e, fra questi,
 *  quelli disponibili, in corso e con una data, dalla scadenza più vicina. */
export function useScadenzeSalvate() {
  const query = useSavedBandi(1);
  const { data } = query;

  const righe = useMemo(
    () =>
      (data?.items ?? [])
        .filter(
          (item) =>
            item.disponibile &&
            item.bando.data_scadenza !== null &&
            bandoInCorso(statoDelBando(item.bando)),
        )
        .sort((a, b) => a.bando.data_scadenza!.localeCompare(b.bando.data_scadenza!)),
    [data],
  );

  return { ...query, righe };
}

/** I bandi salvati più recenti (la prima pagina: 20). */
export const PAGINA_SALVATI = 20;

export interface VoceDaFare {
  numero: number;
  frase: string;
  dove: string;
  /** Colore della pillola «dove» (l'area della pagina di destinazione). */
  area: Area;
  to: string;
}

function plurale(n: number, uno: string, tanti: string): string {
  return n === 1 ? uno : tanti;
}

/** «Da fare»: le cose che aspettano una decisione, con il numero e dove si
 *  trovano. Partenariati solo a modulo acceso (l'hook non chiama l'endpoint a
 *  modulo spento). Ogni fonte ha il suo errore: le altre voci restano. */
export function useVociDaFare() {
  const { partenariatiAttivo } = useFunzioni();
  const riepilogo = useRiepilogoPartenariati();
  const consulenze = useConsulenze();
  const notifiche = useNotifications();

  const voci: VoceDaFare[] = [];
  if (partenariatiAttivo && riepilogo.data) {
    const r = riepilogo.data;
    const candidature = r.candidature_da_decidere ?? 0;
    const inviti = r.inviti_ricevuti ?? 0;
    const messaggi = r.messaggi_non_letti ?? 0;
    if (candidature > 0) {
      voci.push({
        numero: candidature,
        frase: plurale(candidature, "candidatura da valutare", "candidature da valutare"),
        dove: "Partenariati",
        area: "partenariati",
        to: "/app/partenariati?tab=candidature",
      });
    }
    if (inviti > 0) {
      voci.push({
        numero: inviti,
        frase: plurale(inviti, "invito a cui rispondere", "inviti a cui rispondere"),
        dove: "Partenariati",
        area: "partenariati",
        to: "/app/partenariati?tab=candidature",
      });
    }
    if (messaggi > 0) {
      voci.push({
        numero: messaggi,
        frase: plurale(messaggi, "messaggio non letto", "messaggi non letti"),
        dove: "Partenariati",
        area: "partenariati",
        to: "/app/partenariati?tab=conversazioni",
      });
    }
  }
  const proposte = (consulenze.data ?? [])
    .filter((c) => c.stato === "nuova")
    .reduce((somma, c) => somma + c.proposte_aperte, 0);
  if (proposte > 0) {
    voci.push({
      numero: proposte,
      frase: plurale(proposte, "proposta da valutare", "proposte da valutare"),
      dove: "Consulenze",
      area: "consulenze",
      to: "/app/consulenze",
    });
  }
  const nonLette = notifiche.data?.non_lette ?? 0;
  if (nonLette > 0) {
    voci.push({
      numero: nonLette,
      frase: plurale(nonLette, "notifica non letta", "notifiche non lette"),
      dove: "Notifiche",
      area: "account",
      to: "/app/notifiche",
    });
  }

  const inCaricamento =
    consulenze.isPending || notifiche.isPending || (partenariatiAttivo && riepilogo.isPending);
  const errore =
    consulenze.isError || notifiche.isError || (partenariatiAttivo && riepilogo.isError);

  const riprova = () => {
    if (consulenze.isError) void consulenze.refetch();
    if (notifiche.isError) void notifiche.refetch();
    if (riepilogo.isError) void riepilogo.refetch();
  };

  return { voci, inCaricamento, errore, riprova };
}
