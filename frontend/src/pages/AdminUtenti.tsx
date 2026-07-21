import {
  Ban,
  Briefcase,
  CreditCard,
  Gift,
  RotateCcw,
  Search,
  ShieldCheck,
  UserCog,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextareaField, TextField } from "../components/ui/Field";
import { Menu, MenuItem, MenuSeparator } from "../components/ui/Menu";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAddons } from "../hooks/useAddons";
import {
  useAdminGrantAddon,
  useAdminSwitchUserPlan,
  useAdminUpdateUser,
  useAdminUserAddons,
  useAdminUsers,
} from "../hooks/useAdmin";
import { useDebounce } from "../hooks/useDebounce";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { apiErrorMessage } from "../lib/api";
import { cn } from "../lib/cn";
import { ADMIN_RUOLO_COPY, RUOLO_LABELS } from "../lib/copy";
import { formatDate } from "../lib/format";
import { hasAreaProgettista } from "../lib/roles";
import type { AdminUser, UserRole } from "../types";

// L'azione è sempre a conferma via dialog; ruolo e piano si SCELGONO nel dialog
// (non più da select inline), così la riga resta pulita.
type PendingAction =
  | { kind: "role"; user: AdminUser }
  | { kind: "active"; user: AdminUser; is_active: boolean }
  | { kind: "plan"; user: AdminUser }
  | { kind: "addon"; user: AdminUser };

/** Ordine di presentazione nei select (dal ruolo base al più privilegiato). */
const RUOLI: UserRole[] = ["cliente", "progettista", "admin"];

function RuoloBadge({ role }: { role: UserRole }) {
  if (role === "admin") {
    return (
      <Badge tone="brand">
        <ShieldCheck className="size-3" aria-hidden />
        {RUOLO_LABELS.admin}
      </Badge>
    );
  }
  if (role === "progettista") {
    return (
      <Badge tone="emerald">
        <Briefcase className="size-3" aria-hidden />
        {RUOLO_LABELS.progettista}
      </Badge>
    );
  }
  return (
    <Badge tone="slate">
      <UserRound className="size-3" aria-hidden />
      {RUOLO_LABELS.cliente}
    </Badge>
  );
}

/** Iniziali per l'avatar: nome+cognome, o le prime lettere dell'email. */
function iniziali(nome: string | null, cognome: string | null, email: string): string {
  const n = (nome ?? "").trim();
  const c = (cognome ?? "").trim();
  if (n || c) return `${n[0] ?? ""}${c[0] ?? ""}`.toUpperCase();
  return email.slice(0, 2).toUpperCase();
}

/** Avatar iniziali: ancora di scansione della riga; tinta coerente col ruolo. */
function UserAvatar({ user }: { user: AdminUser }) {
  const { role, nome, cognome, email } = user.profile;
  const tint =
    role === "admin"
      ? "bg-brand-100 text-brand-700"
      : role === "progettista"
        ? "bg-emerald-100 text-emerald-700"
        : "bg-slate-100 text-slate-600";
  return (
    <div
      aria-hidden
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
        tint,
      )}
    >
      {iniziali(nome, cognome, email)}
    </div>
  );
}

/** Testo del piano corrente (o «Nessun piano»), con la nota disattivato/ereditato. */
function planoCorrente(user: AdminUser, planiAttivi: Set<number>): string {
  const sub = user.subscription;
  if (!sub) return "—";
  const disattivato = !planiAttivi.has(sub.plan.id);
  return sub.plan.nome + (disattivato ? " (disattivato)" : "");
}

export default function AdminUtenti() {
  const { data: me } = useMe();
  const { data: plans } = usePlans();
  const [searchInput, setSearchInput] = useState("");
  const [role, setRole] = useState<"" | UserRole>("");
  const [page, setPage] = useState(1);
  const q = useDebounce(searchInput, 400);
  const hasFilter = q.trim() !== "" || role !== "";

  useEffect(() => setPage(1), [q, role]);

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminUsers({
    q,
    role,
    page,
  });
  const updateUser = useAdminUpdateUser();
  const switchPlan = useAdminSwitchUserPlan();
  const grantAddon = useAdminGrantAddon();
  const { data: catalogoAddons } = useAddons();

  const planiAttivi = new Set((plans ?? []).map((p) => p.id));

  // Azione singola (dialog di conferma).
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [motivazione, setMotivazione] = useState("");
  // Scelte fatte DENTRO il dialog (ruolo/piano non sono più select inline).
  const [roleChoice, setRoleChoice] = useState<UserRole>("cliente");
  const [planChoice, setPlanChoice] = useState<number | "">("");
  // Form del grant addon.
  const [grantAddonId, setGrantAddonId] = useState<number | "">("");
  const [grantQuantita, setGrantQuantita] = useState("1");

  const { data: inventarioUtente } = useAdminUserAddons(
    pending?.kind === "addon" ? pending.user.profile.id : undefined,
  );

  const addonSelezionato =
    grantAddonId === "" ? undefined : catalogoAddons?.find((a) => a.id === grantAddonId);
  const addonPermanente = addonSelezionato?.tipo_fruizione === "permanente";
  const grantQuantitaNum = addonPermanente ? 1 : Number(grantQuantita);
  const grantQuantitaValida =
    Number.isInteger(grantQuantitaNum) && grantQuantitaNum >= 1 && grantQuantitaNum <= 100;
  const grantIncompleto = grantAddonId === "" || !grantQuantitaValida || !motivazione.trim();

  // ── Selezione multipla / azioni bulk ──────────────────────────────────────
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkPending, setBulkPending] = useState<{ is_active: boolean } | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkNotice, setBulkNotice] = useState<string | null>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);

  // La selezione è per-pagina: si azzera cambiando pagina, filtro o ricerca.
  useEffect(() => setSelected(new Set()), [q, role, page]);
  useEffect(() => {
    if (!bulkNotice) return;
    const t = setTimeout(() => setBulkNotice(null), 6000);
    return () => clearTimeout(t);
  }, [bulkNotice]);

  // Sé stessi non è selezionabile (non ci si può sospendere).
  const selezionabili = (data?.items ?? []).filter((u) => u.profile.id !== me?.profile.id);
  const tuttiSelezionati =
    selezionabili.length > 0 && selezionabili.every((u) => selected.has(u.profile.id));
  const alcuniSelezionati = selected.size > 0 && !tuttiSelezionati;

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = alcuniSelezionati;
  }, [alcuniSelezionati]);

  const toggleAll = () => {
    setSelected((prev) => {
      if (tuttiSelezionati) {
        const next = new Set(prev);
        selezionabili.forEach((u) => next.delete(u.profile.id));
        return next;
      }
      const next = new Set(prev);
      selezionabili.forEach((u) => next.add(u.profile.id));
      return next;
    });
  };

  const toggleOne = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const confirmBulk = async () => {
    if (!bulkPending) return;
    const ids = [...selected];
    setBulkBusy(true);
    const esiti = await Promise.allSettled(
      ids.map((id) =>
        updateUser.mutateAsync({ userId: id, data: { is_active: bulkPending.is_active } }),
      ),
    );
    const ok = esiti.filter((e) => e.status === "fulfilled").length;
    const ko = esiti.length - ok;
    const azione = bulkPending.is_active ? "riattivat" : "sospes";
    const verbo = (n: number) => `${azione}${n === 1 ? "o" : "i"}`;
    const utenti = (n: number) => `${n} utent${n === 1 ? "e" : "i"}`;
    setBulkBusy(false);
    setBulkPending(null);
    setSelected(new Set());
    setBulkNotice(
      ko === 0
        ? `${utenti(ok)} ${verbo(ok)}.`
        : `${ok} ${verbo(ok)}, ${ko} non riuscit${ko === 1 ? "o" : "i"}.`,
    );
  };

  // ── Azione singola ────────────────────────────────────────────────────────
  const openRole = (user: AdminUser) => {
    setActionError(null);
    setRoleChoice(user.profile.role);
    setPending({ kind: "role", user });
  };
  const openPlan = (user: AdminUser) => {
    setActionError(null);
    setMotivazione("");
    // Preseleziona il piano corrente solo se è ancora a catalogo: un piano
    // disattivato non è sottomettibile (come col vecchio select inline).
    const cur = user.subscription?.plan.id;
    setPlanChoice(cur !== undefined && planiAttivi.has(cur) ? cur : "");
    setPending({ kind: "plan", user });
  };
  const openAddon = (user: AdminUser) => {
    setActionError(null);
    setMotivazione("");
    setGrantAddonId("");
    setGrantQuantita("1");
    setPending({ kind: "addon", user });
  };
  const openActive = (user: AdminUser) => {
    setActionError(null);
    setPending({ kind: "active", user, is_active: !user.profile.is_active });
  };

  const confirmAction = async () => {
    if (!pending) return;
    setActionError(null);
    try {
      if (pending.kind === "role") {
        await updateUser.mutateAsync({
          userId: pending.user.profile.id,
          data: { role: roleChoice },
        });
      } else if (pending.kind === "active") {
        await updateUser.mutateAsync({
          userId: pending.user.profile.id,
          data: { is_active: pending.is_active },
        });
      } else if (pending.kind === "addon") {
        if (grantAddonId === "" || !grantQuantitaValida) return;
        await grantAddon.mutateAsync({
          userId: pending.user.profile.id,
          addonId: grantAddonId,
          quantita: grantQuantitaNum,
          motivazione: motivazione.trim(),
        });
      } else {
        if (planChoice === "") return;
        await switchPlan.mutateAsync({
          userId: pending.user.profile.id,
          planId: planChoice,
          motivazione: motivazione.trim(),
        });
      }
      setPending(null);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const actionBusy = updateUser.isPending || switchPlan.isPending || grantAddon.isPending;
  const planNome = planChoice === "" ? "" : (plans?.find((p) => p.id === planChoice)?.nome ?? "");
  const confirmDisabled =
    (pending?.kind === "role" && roleChoice === pending.user.profile.role) ||
    // Come col vecchio select: cambiare al piano già attivo è un no-op.
    (pending?.kind === "plan" &&
      (planChoice === "" ||
        planChoice === pending.user.subscription?.plan.id ||
        !motivazione.trim())) ||
    (pending?.kind === "addon" && grantIncompleto);

  return (
    <div>
      <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
        Gestione utenti
      </h1>
      <p className="mt-1 text-sm text-slate-500">
        {data ? (
          <>
            <span className="tabular font-medium text-slate-700">{data.total}</span>{" "}
            {hasFilter ? "risultati" : "utenti registrati"}
          </>
        ) : (
          "Cerca, modifica ruoli e abbonamenti"
        )}
      </p>

      {/* Toolbar */}
      <div className="mt-5 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
            aria-hidden
          />
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
          <option value="progettista">Solo progettisti</option>
          <option value="cliente">Solo clienti</option>
        </select>
        {hasFilter && (
          <Button
            variant="ghost"
            onClick={() => {
              setSearchInput("");
              setRole("");
            }}
            className="h-11 shrink-0"
          >
            <X className="size-4" aria-hidden />
            Azzera filtri
          </Button>
        )}
      </div>

      {/* Barra azioni di massa */}
      {selected.size > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-3">
          <p className="text-sm font-medium text-brand-800" aria-live="polite">
            {selected.size} selezionat{selected.size === 1 ? "o" : "i"}
          </p>
          <div className="ml-auto flex flex-wrap gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setBulkPending({ is_active: true })}
            >
              <RotateCcw className="size-4" aria-hidden />
              Riattiva selezionati
            </Button>
            <Button
              variant="secondary"
              size="sm"
              className="text-red-600 hover:border-red-300 hover:text-red-700 active:bg-red-50"
              onClick={() => setBulkPending({ is_active: false })}
            >
              <Ban className="size-4" aria-hidden />
              Sospendi selezionati
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              Deseleziona
            </Button>
          </div>
        </div>
      )}

      {bulkNotice && (
        <p
          className="mt-4 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800"
          role="status"
        >
          {bulkNotice}
        </p>
      )}

      <Card
        className="mt-5 overflow-hidden"
        aria-busy={isPending || isPlaceholderData}
      >
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
            <table
              className={cn(
                "w-full min-w-[760px] text-left text-sm",
                isPlaceholderData && "opacity-60 transition-opacity",
              )}
            >
              <caption className="sr-only">Elenco degli utenti registrati</caption>
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
                  <th scope="col" className="w-10 px-4 py-3">
                    <input
                      ref={selectAllRef}
                      type="checkbox"
                      checked={tuttiSelezionati}
                      onChange={toggleAll}
                      disabled={selezionabili.length === 0}
                      aria-label="Seleziona tutti gli utenti della pagina"
                      className="size-4 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
                    />
                  </th>
                  <th scope="col" className="px-4 py-3 font-medium">Utente</th>
                  <th scope="col" className="px-4 py-3 font-medium">Ruolo</th>
                  <th scope="col" className="px-4 py-3 font-medium">Azienda</th>
                  <th scope="col" className="px-4 py-3 font-medium">Piano</th>
                  <th scope="col" className="px-4 py-3 font-medium">Stato</th>
                  <th scope="col" className="px-4 py-3 font-medium">Registrato</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">
                    <span className="sr-only">Azioni</span>
                  </th>
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
                  const sospeso = !user.profile.is_active;
                  const checked = selected.has(user.profile.id);
                  return (
                    <tr
                      key={user.profile.id}
                      className={cn(
                        "border-b border-slate-100 transition-colors last:border-b-0",
                        checked ? "bg-brand-50/50" : sospeso ? "bg-slate-50/40" : "hover:bg-slate-50/60",
                      )}
                    >
                      <td className="px-4 py-3">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleOne(user.profile.id)}
                          disabled={isSelf}
                          title={isSelf ? "Non puoi selezionare il tuo account" : undefined}
                          aria-label={`Seleziona ${user.profile.email}`}
                          className="size-4 cursor-pointer accent-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <UserAvatar user={user} />
                          <div className="min-w-0">
                            <div
                              className={cn(
                                "font-medium",
                                sospeso ? "text-slate-500" : "text-slate-900",
                              )}
                            >
                              {fullName || "—"}
                              {isSelf && (
                                <span className="ml-1.5 text-xs text-brand-500">(tu)</span>
                              )}
                            </div>
                            <div className="text-xs text-slate-500">{user.profile.email}</div>
                            {user.profile.azienda && (
                              <div className="text-xs text-slate-400">{user.profile.azienda}</div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <RuoloBadge role={user.profile.role} />
                        {user.progettista?.codice && (
                          <p className="tabular mt-1 text-xs text-slate-400">
                            {user.progettista.codice}
                          </p>
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
                                ? "In azienda"
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
                      <td className="px-4 py-3 text-slate-700">
                        {user.subscription ? (
                          <>
                            {planoCorrente(user, planiAttivi)}
                            {user.subscription.inherited && (
                              <p className="mt-0.5 text-xs text-slate-400">(ereditato)</p>
                            )}
                          </>
                        ) : (
                          <span className="text-xs text-slate-300">—</span>
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
                        <div className="flex justify-end">
                          <Menu label={`Azioni per ${user.profile.email}`}>
                            <MenuItem
                              icon={<UserCog className="size-4" />}
                              disabled={isSelf}
                              title={isSelf ? "Non puoi modificare il tuo ruolo" : undefined}
                              onSelect={() => openRole(user)}
                            >
                              Cambia ruolo…
                            </MenuItem>
                            <MenuItem
                              icon={<CreditCard className="size-4" />}
                              disabled={isManagedChild}
                              title={
                                isManagedChild
                                  ? "Il piano si gestisce sull'account titolare dell'azienda"
                                  : undefined
                              }
                              onSelect={() => openPlan(user)}
                            >
                              Cambia piano…
                            </MenuItem>
                            <MenuItem
                              icon={<Gift className="size-4" />}
                              onSelect={() => openAddon(user)}
                            >
                              Assegna addon…
                            </MenuItem>
                            <MenuSeparator />
                            <MenuItem
                              icon={
                                user.profile.is_active ? (
                                  <Ban className="size-4" />
                                ) : (
                                  <RotateCcw className="size-4" />
                                )
                              }
                              danger={user.profile.is_active}
                              disabled={isSelf}
                              title={isSelf ? "Non puoi disattivare il tuo account" : undefined}
                              onSelect={() => openActive(user)}
                            >
                              {user.profile.is_active ? "Sospendi" : "Riattiva"}
                            </MenuItem>
                          </Menu>
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

      {/* Dialog di conferma (azione singola) */}
      <Dialog
        open={!!pending}
        onClose={() => setPending(null)}
        dismissible={!actionBusy}
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
              disabled={confirmDisabled}
            >
              Conferma
            </Button>
          </>
        }
      >
        {pending?.kind === "role" && (
          <>
            <p>
              Cambia il ruolo di{" "}
              <strong className="text-slate-900">{pending.user.profile.email}</strong>.
            </p>
            <div className="mt-3">
              <SelectField
                label="Nuovo ruolo"
                value={roleChoice}
                onChange={(e) => setRoleChoice(e.target.value as UserRole)}
              >
                {RUOLI.map((r) => (
                  <option key={r} value={r}>
                    {RUOLO_LABELS[r]}
                  </option>
                ))}
              </SelectField>
            </div>
            {roleChoice === "progettista" && (
              <p className="mt-2 text-sm text-slate-500">
                {ADMIN_RUOLO_COPY.promozioneProgettista}
              </p>
            )}
            {roleChoice === "admin" && (
              <p className="mt-2 text-sm text-slate-500">{ADMIN_RUOLO_COPY.nominaAdmin}</p>
            )}
            {/* L'area progettista si perde solo tornando cliente (parità admin). */}
            {hasAreaProgettista(pending.user.profile.role) && !hasAreaProgettista(roleChoice) && (
              <p className="mt-2 text-sm text-slate-500">
                {ADMIN_RUOLO_COPY.perditaAreaProgettista}
              </p>
            )}
          </>
        )}
        {pending?.kind === "active" && (
          <p>
            {pending.is_active ? "Riattivare" : "Sospendere"} l'account di{" "}
            <strong className="text-slate-900">{pending.user.profile.email}</strong>?
            {!pending.is_active && " L'utente non potrà più accedere alla piattaforma."}
          </p>
        )}
        {pending?.kind === "addon" && (
          <>
            <p>
              Assegna un add-on a{" "}
              <strong className="text-slate-900">{pending.user.profile.email}</strong>.
            </p>
            {(inventarioUtente?.length ?? 0) > 0 && (
              <p className="mt-2 text-sm text-slate-500">
                Possiede già:{" "}
                {inventarioUtente!.map((m) => `${m.quantita} × ${m.nome}`).join(", ")}.
              </p>
            )}
            <div className="mt-3 space-y-3">
              <SelectField
                label="Add-on"
                required
                value={grantAddonId}
                onChange={(e) =>
                  setGrantAddonId(e.target.value === "" ? "" : Number(e.target.value))
                }
              >
                <option value="">Seleziona un add-on…</option>
                {(catalogoAddons ?? []).map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.nome}
                  </option>
                ))}
              </SelectField>
              <TextField
                label="Quantità"
                type="number"
                min={1}
                max={100}
                required
                value={addonPermanente ? "1" : grantQuantita}
                disabled={addonPermanente}
                helper={
                  addonPermanente ? "Add-on permanente: si possiede una volta sola." : undefined
                }
                error={
                  !addonPermanente && grantQuantita !== "" && !grantQuantitaValida
                    ? "Indica un numero intero da 1 a 100."
                    : undefined
                }
                onChange={(e) => setGrantQuantita(e.target.value)}
              />
              <TextareaField
                label="Motivazione"
                required
                value={motivazione}
                onChange={(e) => setMotivazione(e.target.value)}
                placeholder="Es. Cortesia, rimborso, accordo commerciale…"
                maxLength={500}
                rows={2}
              />
            </div>
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              L'accredito è gratuito e verrà registrato nello storico dell'utente con il tuo
              nome.
            </p>
          </>
        )}
        {pending?.kind === "plan" && (
          <>
            <p>
              Cambia il piano di{" "}
              <strong className="text-slate-900">{pending.user.profile.email}</strong>.
              {planNome && (
                <>
                  {" "}
                  L'abbonamento annuale di{" "}
                  <strong className="text-slate-900">{planNome}</strong> riparte da oggi.
                </>
              )}
            </p>
            <div className="mt-3 space-y-3">
              <SelectField
                label="Nuovo piano"
                required
                value={planChoice}
                onChange={(e) =>
                  setPlanChoice(e.target.value === "" ? "" : Number(e.target.value))
                }
              >
                <option value="">Seleziona un piano…</option>
                {pending.user.subscription &&
                  !planiAttivi.has(pending.user.subscription.plan.id) && (
                    <option value={pending.user.subscription.plan.id} disabled>
                      {pending.user.subscription.plan.nome} (disattivato)
                    </option>
                  )}
                {(plans ?? []).map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.nome}
                  </option>
                ))}
              </SelectField>
              <TextareaField
                label="Motivazione"
                required
                value={motivazione}
                onChange={(e) => setMotivazione(e.target.value)}
                placeholder="Es. Cliente convenzionato, correzione, cortesia…"
                maxLength={500}
                rows={2}
              />
            </div>
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
              Il cambio è gratuito e verrà registrato nello storico con il tuo nome. Un eventuale
              pagamento in corso dell'utente verrà annullato.
            </p>
          </>
        )}
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>

      {/* Dialog di conferma (azione di massa) */}
      <Dialog
        open={!!bulkPending}
        onClose={() => setBulkPending(null)}
        dismissible={!bulkBusy}
        title="Conferma operazione di massa"
        footer={
          <>
            <Button variant="ghost" onClick={() => setBulkPending(null)}>
              Annulla
            </Button>
            <Button
              variant={bulkPending?.is_active ? "primary" : "danger"}
              onClick={confirmBulk}
              loading={bulkBusy}
            >
              Conferma
            </Button>
          </>
        }
      >
        {bulkPending && (
          <p>
            {bulkPending.is_active ? "Riattivare" : "Sospendere"}{" "}
            <strong className="text-slate-900">
              {selected.size} utent{selected.size === 1 ? "e" : "i"}
            </strong>{" "}
            selezionat{selected.size === 1 ? "o" : "i"}?
            {!bulkPending.is_active && " Non potranno più accedere alla piattaforma."}
          </p>
        )}
      </Dialog>
    </div>
  );
}
