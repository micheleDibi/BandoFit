import { Search, ShieldCheck, UserRound, UsersRound } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import {
  useAdminSwitchUserPlan,
  useAdminUpdateUser,
  useAdminUsers,
} from "../hooks/useAdmin";
import { useDebounce } from "../hooks/useDebounce";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import type { AdminUser } from "../types";

type PendingAction =
  | { kind: "role"; user: AdminUser; role: "admin" | "cliente" }
  | { kind: "active"; user: AdminUser; is_active: boolean }
  | { kind: "plan"; user: AdminUser; planId: number; planName: string };

export default function AdminUtenti() {
  const { data: me } = useMe();
  const { data: plans } = usePlans();
  const [searchInput, setSearchInput] = useState("");
  const [role, setRole] = useState<"" | "admin" | "cliente">("");
  const [page, setPage] = useState(1);
  const q = useDebounce(searchInput, 400);

  useEffect(() => setPage(1), [q, role]);

  const { data, isPending, isError, error, refetch } = useAdminUsers({ q, role, page });
  const updateUser = useAdminUpdateUser();
  const switchPlan = useAdminSwitchUserPlan();

  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const confirmAction = async () => {
    if (!pending) return;
    setActionError(null);
    try {
      if (pending.kind === "role") {
        await updateUser.mutateAsync({
          userId: pending.user.profile.id,
          data: { role: pending.role },
        });
      } else if (pending.kind === "active") {
        await updateUser.mutateAsync({
          userId: pending.user.profile.id,
          data: { is_active: pending.is_active },
        });
      } else {
        await switchPlan.mutateAsync({ userId: pending.user.profile.id, planId: pending.planId });
      }
      setPending(null);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const actionBusy = updateUser.isPending || switchPlan.isPending;

  return (
    <div>
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Gestione utenti
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        {data ? (
          <>
            <span className="tabular font-medium text-slate-700">{data.total}</span> utenti
            registrati
          </>
        ) : (
          "Cerca, modifica ruoli e abbonamenti"
        )}
      </p>

      {/* Toolbar */}
      <div className="mt-5 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" aria-hidden />
          <input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Cerca per email, nome o azienda…"
            aria-label="Cerca utenti"
            className="h-11 w-full rounded-xl border border-slate-300 bg-white pl-10 pr-4 text-sm shadow-card placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
          />
        </div>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as typeof role)}
          aria-label="Filtra per ruolo"
          className="h-11 cursor-pointer rounded-xl border border-slate-300 bg-white px-3 text-sm shadow-card focus:border-brand-500 focus:outline-none"
        >
          <option value="">Tutti i ruoli</option>
          <option value="admin">Solo admin</option>
          <option value="cliente">Solo clienti</option>
        </select>
      </div>

      <Card className="mt-5 overflow-hidden">
        {isPending ? (
          <div className="space-y-3 p-5">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="p-5">
            <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
          </div>
        ) : data && data.items.length === 0 ? (
          <div className="p-5">
            <EmptyState title="Nessun utente trovato" description="Prova con un'altra ricerca." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[840px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-4 py-3 font-medium">Utente</th>
                  <th className="px-4 py-3 font-medium">Ruolo</th>
                  <th className="px-4 py-3 font-medium">Famiglia</th>
                  <th className="px-4 py-3 font-medium">Piano</th>
                  <th className="px-4 py-3 font-medium">Stato</th>
                  <th className="px-4 py-3 font-medium">Registrato</th>
                  <th className="px-4 py-3 text-right font-medium">Azioni</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((user) => {
                  const isSelf = user.profile.id === me?.profile.id;
                  const fullName = [user.profile.nome, user.profile.cognome]
                    .filter(Boolean)
                    .join(" ");
                  // Solo i figli ATTIVI ereditano il piano (pending/retrocessi
                  // hanno un piano proprio, gestibile normalmente).
                  const isManagedChild =
                    user.family?.type === "child" && user.family.status === "active";
                  return (
                    <tr
                      key={user.profile.id}
                      className="border-b border-slate-100 transition-colors last:border-b-0 hover:bg-slate-50/60"
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium text-slate-900">
                          {fullName || "—"}
                          {isSelf && <span className="ml-1.5 text-xs text-brand-500">(tu)</span>}
                        </div>
                        <div className="text-xs text-slate-500">{user.profile.email}</div>
                        {user.profile.azienda && (
                          <div className="text-xs text-slate-400">{user.profile.azienda}</div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {user.profile.role === "admin" ? (
                          <Badge tone="brand">
                            <ShieldCheck className="size-3" aria-hidden />
                            Admin
                          </Badge>
                        ) : (
                          <Badge tone="slate">
                            <UserRound className="size-3" aria-hidden />
                            Cliente
                          </Badge>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {user.family?.type === "child" ? (
                          <div>
                            <Badge
                              tone={
                                user.family.status === "active"
                                  ? "brand"
                                  : user.family.status === "pending"
                                    ? "amber"
                                    : "slate"
                              }
                            >
                              <UsersRound className="size-3" aria-hidden />
                              {user.family.status === "active"
                                ? "In famiglia"
                                : user.family.status === "pending"
                                  ? "Invitato"
                                  : "Retrocesso"}
                            </Badge>
                            {user.family.parent_email && (
                              <p className="mt-1 text-xs text-slate-400">
                                di {user.family.parent_email}
                              </p>
                            )}
                          </div>
                        ) : user.family?.type === "parent" ? (
                          <Badge tone="brand">
                            <UsersRound className="size-3" aria-hidden />
                            Titolare · {user.family.members_count ?? 0} collegati
                          </Badge>
                        ) : (
                          <span className="text-xs text-slate-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <select
                          value={user.subscription?.plan.id ?? ""}
                          disabled={isManagedChild}
                          title={
                            isManagedChild
                              ? "Il piano si gestisce sull'account titolare della famiglia"
                              : undefined
                          }
                          onChange={(e) => {
                            const planId = Number(e.target.value);
                            const plan = plans?.find((p) => p.id === planId);
                            if (plan) {
                              setActionError(null);
                              setPending({ kind: "plan", user, planId, planName: plan.nome });
                            }
                          }}
                          aria-label={`Piano di ${user.profile.email}`}
                          className="h-9 cursor-pointer rounded-lg border border-slate-200 bg-white px-2 text-sm focus:border-brand-400 focus:outline-none disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-400"
                        >
                          {!user.subscription && <option value="">Nessun piano</option>}
                          {user.subscription &&
                            !(plans ?? []).some((p) => p.id === user.subscription!.plan.id) && (
                              <option value={user.subscription.plan.id} disabled>
                                {user.subscription.plan.nome} (disattivato)
                              </option>
                            )}
                          {(plans ?? []).map((plan) => (
                            <option key={plan.id} value={plan.id}>
                              {plan.nome}
                            </option>
                          ))}
                        </select>
                        {user.subscription?.inherited && (
                          <p className="mt-1 text-xs text-slate-400">(ereditato)</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {user.profile.is_active ? (
                          <Badge tone="emerald">Attivo</Badge>
                        ) : (
                          <Badge tone="red">Sospeso</Badge>
                        )}
                      </td>
                      <td className="tabular px-4 py-3 text-slate-500">
                        {formatDate(user.profile.created_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1.5">
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isSelf}
                            title={isSelf ? "Non puoi modificare il tuo ruolo" : undefined}
                            onClick={() => {
                              setActionError(null);
                              setPending({
                                kind: "role",
                                user,
                                role: user.profile.role === "admin" ? "cliente" : "admin",
                              });
                            }}
                          >
                            {user.profile.role === "admin" ? "Rendi cliente" : "Rendi admin"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={isSelf}
                            title={isSelf ? "Non puoi disattivare il tuo account" : undefined}
                            className={user.profile.is_active ? "text-red-600 hover:bg-red-50 hover:text-red-700" : "text-emerald-600 hover:bg-emerald-50 hover:text-emerald-700"}
                            onClick={() => {
                              setActionError(null);
                              setPending({ kind: "active", user, is_active: !user.profile.is_active });
                            }}
                          >
                            {user.profile.is_active ? "Sospendi" : "Riattiva"}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data && data.total_pages > 1 && (
        <div className="mt-6">
          <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
        </div>
      )}

      {/* Dialog di conferma */}
      <Dialog
        open={!!pending}
        onClose={() => setPending(null)}
        title="Conferma operazione"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPending(null)}>
              Annulla
            </Button>
            <Button
              variant={pending?.kind === "active" && !pending.is_active ? "danger" : "primary"}
              onClick={confirmAction}
              loading={actionBusy}
            >
              Conferma
            </Button>
          </>
        }
      >
        {pending?.kind === "role" && (
          <p>
            Cambiare il ruolo di <strong className="text-slate-900">{pending.user.profile.email}</strong>{" "}
            in <strong className="text-slate-900">{pending.role}</strong>?
          </p>
        )}
        {pending?.kind === "active" && (
          <p>
            {pending.is_active ? "Riattivare" : "Sospendere"} l'account di{" "}
            <strong className="text-slate-900">{pending.user.profile.email}</strong>?
            {!pending.is_active && " L'utente non potrà più accedere alla piattaforma."}
          </p>
        )}
        {pending?.kind === "plan" && (
          <p>
            Assegnare il piano <strong className="text-slate-900">{pending.planName}</strong> a{" "}
            <strong className="text-slate-900">{pending.user.profile.email}</strong>? L'abbonamento
            annuale riparte da oggi.
          </p>
        )}
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </div>
  );
}
