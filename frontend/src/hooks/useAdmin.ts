import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Addon, AdminUser, Page, Plan, TipoPrezzo } from "../types";

export interface AdminUsersParams {
  q: string;
  role: "" | "admin" | "cliente";
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
      data: { role?: "admin" | "cliente"; is_active?: boolean };
    }) => (await api.patch<AdminUser>(`/admin/users/${userId}`, data)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });
}

export function useAdminSwitchUserPlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, planId }: { userId: string; planId: number }) =>
      (await api.post<AdminUser>(`/admin/users/${userId}/subscription`, { plan_id: planId })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
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
