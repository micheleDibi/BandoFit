import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Family, Invitation, InviteMemberResult, Me } from "../types";
import { useMe } from "./useMe";

function useInvalidateFamily() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["family"] });
    queryClient.invalidateQueries({ queryKey: ["me"] });
  };
}

export function useFamily() {
  const { data: me } = useMe();
  return useQuery({
    queryKey: ["family"],
    queryFn: async () => (await api.get<Family>("/me/family")).data,
    enabled: me?.family?.role === "parent",
  });
}

export function useInviteMember() {
  const invalidate = useInvalidateFamily();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: { email: string; denominazione: string }) =>
      (await api.post<InviteMemberResult>("/me/family/members", data)).data,
    onSuccess: (result) => {
      queryClient.setQueryData(["family"], result.family);
      invalidate();
    },
  });
}

export function useResendInvite() {
  const invalidate = useInvalidateFamily();
  return useMutation({
    mutationFn: async (membershipId: string) =>
      (await api.post<InviteMemberResult>(`/me/family/members/${membershipId}/resend`)).data,
    onSuccess: invalidate,
  });
}

export function useReactivateMember() {
  const invalidate = useInvalidateFamily();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (membershipId: string) =>
      (await api.post<Family>(`/me/family/members/${membershipId}/reactivate`)).data,
    onSuccess: (family) => {
      queryClient.setQueryData(["family"], family);
      invalidate();
    },
  });
}

export function useRemoveMember() {
  const invalidate = useInvalidateFamily();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (membershipId: string) =>
      (await api.delete<Family>(`/me/family/members/${membershipId}`)).data,
    onSuccess: (family) => {
      queryClient.setQueryData(["family"], family);
      invalidate();
    },
  });
}

export function useInvitations() {
  const { data: me } = useMe();
  return useQuery({
    queryKey: ["invitations"],
    queryFn: async () => (await api.get<Invitation[]>("/me/invitations")).data,
    enabled: !!me,
    staleTime: 60_000,
  });
}

export function useAcceptInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (membershipId: string) =>
      (await api.post<Me>(`/me/invitations/${membershipId}/accept`)).data,
    onSuccess: (me) => {
      queryClient.setQueryData(["me"], me);
      queryClient.invalidateQueries({ queryKey: ["invitations"] });
      queryClient.invalidateQueries({ queryKey: ["company"] });
    },
  });
}

export function useDeclineInvitation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (membershipId: string) =>
      (await api.post(`/me/invitations/${membershipId}/decline`)).data,
    onSuccess: (invitations) => {
      queryClient.setQueryData(["invitations"], invitations);
    },
  });
}
