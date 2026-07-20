import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  Addon,
  AdminInvoicesPage,
  AdminUser,
  MyAddon,
  Page,
  PaymentAnomaly,
  Plan,
  Purchase,
  TipoPrezzo,
  UserRole,
} from "../types";

export interface AdminUsersParams {
  q: string;
  role: "" | UserRole;
  page: number;
}

export function useAdminUsers(params: AdminUsersParams) {
  const query: Record<string, string | number> = { page: params.page, page_size: 20 };
  if (params.q) query.q = params.q;
  if (params.role) query.role = params.role;
  return useQuery({
    queryKey: ["admin-users", query],
    queryFn: async () => (await api.get<Page<AdminUser>>("/admin/users", { params: query })).data,
    placeholderData: keepPreviousData,
  });
}

export function useAdminUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      userId,
      data,
    }: {
      userId: string;
      data: { role?: UserRole; is_active?: boolean };
    }) => (await api.patch<AdminUser>(`/admin/users/${userId}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });
}

export function useAdminSwitchUserPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    // Cambio GRATUITO con motivazione obbligatoria: finisce nello storico
    // acquisti come kind=cambio_admin, con l'admin come attore.
    mutationFn: async ({
      userId,
      planId,
      motivazione,
    }: {
      userId: string;
      planId: number;
      motivazione: string;
    }) =>
      (
        await api.post<AdminUser>(`/admin/users/${userId}/subscription`, {
          plan_id: planId,
          motivazione,
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-purchases"] });
    },
  });
}

export function useAdminPlans() {
  return useQuery({
    queryKey: ["admin-plans"],
    queryFn: async () => (await api.get<Plan[]>("/admin/plans")).data,
  });
}

export interface PlanPayload {
  nome?: string;
  descrizione?: string | null;
  prezzo_annuale?: number;
  tipo_prezzo?: TipoPrezzo;
  etichetta_prezzo?: string | null;
  ai_check?: number;
  alert_attivo?: boolean;
  alert_giorni_preavviso?: number | null;
  alert_ritardo_giorni?: number | null;
  num_account_aziendali?: number;
  ordering?: number;
  is_active?: boolean;
}

export function useAdminUpdatePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ planId, data }: { planId: number; data: PlanPayload }) =>
      (await api.patch<Plan>(`/admin/plans/${planId}`, data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-plans"] });
      queryClient.invalidateQueries({ queryKey: ["plans"] });
    },
  });
}

export function useAdminCreatePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: PlanPayload & { slug: string; nome: string }) =>
      (await api.post<Plan>("/admin/plans", data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-plans"] });
      queryClient.invalidateQueries({ queryKey: ["plans"] });
    },
  });
}

// ---- Add-on (stesso pattern dei piani: mai delete, doppia invalidazione) ----

export function useAdminAddons() {
  return useQuery({
    queryKey: ["admin-addons"],
    queryFn: async () => (await api.get<Addon[]>("/admin/addons")).data,
  });
}

export interface AddonPayload {
  nome?: string;
  descrizione?: string | null;
  prezzo?: number;
  tipo_prezzo?: TipoPrezzo;
  etichetta_prezzo?: string | null;
  ordering?: number;
  is_active?: boolean;
}

export function useAdminUpdateAddon() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ addonId, data }: { addonId: number; data: AddonPayload }) =>
      (await api.patch<Addon>(`/admin/addons/${addonId}`, data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-addons"] });
      queryClient.invalidateQueries({ queryKey: ["addons"] });
    },
  });
}

export function useAdminCreateAddon() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: AddonPayload & { slug: string; nome: string }) =>
      (await api.post<Addon>("/admin/addons", data)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin-addons"] });
      queryClient.invalidateQueries({ queryKey: ["addons"] });
    },
  });
}

// ---- Inventario addon degli utenti (grant admin) ----------------------------

/** Inventario addon di un utente (solo voci con quantità > 0): mostra nel
 *  dialog di grant cosa l'utente possiede già. */
export function useAdminUserAddons(userId: string | undefined) {
  return useQuery({
    queryKey: ["admin-user-addons", userId],
    queryFn: async () =>
      (await api.get<MyAddon[]>(`/admin/users/${userId}/addons`)).data,
    enabled: !!userId,
  });
}

export function useAdminGrantAddon() {
  const queryClient = useQueryClient();
  return useMutation({
    // Accredito GRATUITO con motivazione obbligatoria: crea una riga
    // purchases kind=addon_admin con l'admin come attore (parità cambio piano).
    mutationFn: async ({
      userId,
      addonId,
      quantita,
      motivazione,
    }: {
      userId: string;
      addonId: number;
      quantita: number;
      motivazione: string;
    }) =>
      (
        await api.post<{ purchase_id: string | null; quantita_residua: number }>(
          `/admin/users/${userId}/addons`,
          { addon_id: addonId, quantita, motivazione },
        )
      ).data,
    onSuccess: (_data, { userId }) => {
      queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      queryClient.invalidateQueries({ queryKey: ["admin-purchases"] });
      queryClient.invalidateQueries({ queryKey: ["admin-user-addons", userId] });
    },
  });
}

// ---- Pagamenti (storico acquisti, fatture SDI, anomalie) --------------------

export interface AdminPurchasesParams {
  status: string;
  kind: string;
  page: number;
}

export function useAdminPurchases(params: AdminPurchasesParams) {
  const query: Record<string, string | number> = { page: params.page, page_size: 20 };
  if (params.status) query.status = params.status;
  if (params.kind) query.kind = params.kind;
  return useQuery({
    queryKey: ["admin-purchases", query],
    queryFn: async () =>
      (await api.get<Page<Purchase>>("/admin/purchases", { params: query })).data,
    placeholderData: keepPreviousData,
  });
}

export function useAdminInvoices(params: { stato: string; page: number }) {
  const query: Record<string, string | number> = { page: params.page, page_size: 20 };
  if (params.stato) query.stato = params.stato;
  return useQuery({
    queryKey: ["admin-invoices", query],
    queryFn: async () =>
      (await api.get<AdminInvoicesPage>("/admin/invoices", { params: query })).data,
    placeholderData: keepPreviousData,
  });
}

/** Ritrasmette una fattura in errore/scartata (stesso numero e stessa data).
 *  Per gli altri stati il backend risponde {stato, note} senza fare nulla. */
export function useRetryInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (invoiceId: string) =>
      (await api.post<{ stato: string; note?: string }>(`/admin/invoices/${invoiceId}/retry`))
        .data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-invoices"] }),
  });
}

export function useAdminAnomalies(stato: "aperta" | "risolta") {
  return useQuery({
    queryKey: ["admin-anomalies", stato],
    queryFn: async () =>
      (
        await api.get<{ items: PaymentAnomaly[] }>("/admin/payment-anomalies", {
          params: { stato },
        })
      ).data,
  });
}

export function useResolveAnomaly() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (auditId: number) => {
      await api.post(`/admin/payment-anomalies/${auditId}/resolve`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-anomalies"] }),
  });
}
