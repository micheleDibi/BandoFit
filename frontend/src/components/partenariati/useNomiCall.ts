import { useMemo } from "react";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import type { NomiCall } from "./callDati";

const umanizza = (codice: string) => {
  const testo = codice.replaceAll("_", " ");
  return testo.charAt(0).toUpperCase() + testo.slice(1);
};

/** Nomi di tipi, competenze e lookup del catalogo per descrivere criteri e
 *  posizioni. Finché vocabolario e lookup non arrivano, codici leggibili. */
export function useNomiCall(): NomiCall {
  const { data: vocabolario } = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  return useMemo(() => {
    const tipi = new Map((vocabolario?.tipi_soggetto ?? []).map((t) => [t.codice, t.etichetta]));
    const competenze = new Map((vocabolario?.competenze ?? []).map((c) => [c.codice, c.etichetta]));
    const regioni = new Map((lookups?.regioni ?? []).map((r) => [r.id, r.nome]));
    const settori = new Map((lookups?.settori ?? []).map((r) => [r.id, r.nome]));
    const programmi = new Map((lookups?.programmi ?? []).map((r) => [r.id, r.nome]));
    return {
      tipo: (codice: string) => tipi.get(codice as never) ?? umanizza(codice),
      competenza: (codice: string) => competenze.get(codice) ?? umanizza(codice),
      regione: (id: number) => regioni.get(id) ?? `Regione ${id}`,
      settore: (id: number) => settori.get(id) ?? `Settore ${id}`,
      programma: (id: number) => programmi.get(id) ?? `Programma ${id}`,
    };
  }, [vocabolario, lookups]);
}
