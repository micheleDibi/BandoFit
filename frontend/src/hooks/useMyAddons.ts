import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { AddonLedgerEntry, MyAddon } from "../types";
import { useAuth } from "./useAuth";

/** Inventario addon dell'utente (solo le voci con quantità > 0). Alimenta il
 *  badge «Hai N …» in Abbonamento e il gating del consulto (ConsultoCard). */
export function useMyAddons() {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["my-addons"],
    queryFn: async () => (await api.get<MyAddon[]>("/me/addons")).data,
    enabled: !!session,
  });
}

/** Storico movimenti di un addon (ultimi 20, più recenti prima). On-demand:
 *  senza addonId la query resta spenta — si carica solo quando l'utente
 *  espande «Vedi movimenti». */
export function useMyAddonLedger(addonId?: number) {
  const { session } = useAuth();
  return useQuery({
    queryKey: ["my-addons", "ledger", addonId],
    queryFn: async () =>
      (
        await api.get<AddonLedgerEntry[]>("/me/addons/ledger", {
          params: { addon_id: addonId, limit: 20 },
        })
      ).data,
    enabled: !!session && addonId !== undefined,
  });
}
