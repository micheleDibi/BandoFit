import { Ban, CreditCard, Gift, RotateCcw, UserCog, Users } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Alert } from "../components/ui/Alert";
import { Avatar } from "../components/ui/Avatar";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextareaField, TextField } from "../components/ui/Field";
import { KpiCard } from "../components/ui/KpiCard";
import { Menu, MenuItem, MenuSeparator } from "../components/ui/Menu";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { SearchInput } from "../components/ui/SearchInput";
import { Select } from "../components/ui/Select";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Status } from "../components/ui/Status";
import { Table, Td, Th } from "../components/ui/Table";
import { useToast } from "../components/ui/Toast";
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

/** Classi delle caselle di selezione della tabella: native, con `aria-label`
 *  (la `Checkbox` di ui ha sempre un'etichetta visibile). */
const CASELLA =
  "size-4 cursor-pointer accent-accent disabled:cursor-not-allowed disabled:opacity-50";

/** Testo del piano corrente (o «Nessun piano»), con la nota disattivato/ereditato. */
function planoCorrente(user: AdminUser, planiAttivi: Set<number>): string {
  const sub = user.subscription;
  if (!sub) return "—";
  const disattivato = !planiAttivi.has(sub.plan.id);
  return sub.plan.nome + (disattivato ? " (disattivato)" : "");
}

/** Il gruppo di account dell'utente in parole (sotto la ragione sociale). */
function gruppoAzienda(user: AdminUser): string | null {
  const f = user.family;
  if (f?.type === "child") {
    const stato =
      f.status === "active" ? "In azienda" : f.status === "pending" ? "Invitato" : "Retrocesso";
    return f.parent_email ? `${stato}, di ${f.parent_email}` : stato;
  }
  if (f?.type === "parent") return `Titolare, ${f.members_count ?? 0} collegati`;
  return null;
}

export default function AdminUtenti() {
  const { data: me } = useMe();
  const { data: plans } = usePlans();
  const [searchInput, setSearchInput] = useState("");
  const [role, setRole] = useState<"" | UserRole>("");
  const [page, setPage] = useState(1);
  const q = useDebounce(searchInput, 400);
  const hasFilter = q.trim() !== "" || role !== "";

  const { data, isPending, isError, error, refetch, isPlaceholderData } = useAdminUsers({
    q,
    role,
    page,
  });
  const updateUser = useAdminUpdateUser();
  const switchPlan = useAdminSwitchUserPlan();
  const grantAddon = useAdminGrantAddon();
  const { data: catalogoAddons } = useAddons();
  const toast = useToast();

  // Pagina oltre l'ultima (il totale è calato, per esempio dopo una
  // sospensione): si rientra sull'ultima piena invece di mostrare il vuoto.
  // Mai sui dati segnaposto della query precedente.
  const fuoriPagina =
    !!data && page > 1 && page > data.total_pages && data.items.length === 0 && data.total > 0;
  useEffect(() => {
    if (fuoriPagina && !isPlaceholderData && data) setPage(Math.max(1, data.total_pages));
  }, [fuoriPagina, isPlaceholderData, data]);

  // Cambio di filtro o ricerca: si riparte da pagina 1. Dichiarato DOPO il
  // rientro: se nello stesso commit partono tutti e due (pagina vuota già in
  // cache per il nuovo filtro), vince il ritorno a pagina 1.
  useEffect(() => setPage(1), [q, role]);

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
  const selectAllRef = useRef<HTMLInputElement>(null);

  // La selezione è per-pagina: si azzera cambiando pagina, filtro o ricerca.
  useEffect(() => setSelected(new Set()), [q, role, page]);

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
    toast.mostra(
      ko === 0
        ? { testo: `${utenti(ok)} ${verbo(ok)}.` }
        : {
            testo: `${ok} ${verbo(ok)}, ${ko} non riuscit${ko === 1 ? "o" : "i"}.`,
            tono: "errore",
            durata: 6000,
          },
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

  // Pagina oltre l'ultima (`fuoriPagina`, sopra): finché non si rientra si
  // mostra lo scheletro, non lo stato vuoto.

  let elenco: ReactNode;
  if (isPending) {
    elenco = <SkeletonRighe />;
  } else if (isError) {
    elenco = <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  } else if (fuoriPagina) {
    elenco = <SkeletonRighe />;
  } else if (data && data.items.length === 0) {
    elenco = (
      <Card>
        <EmptyState
          title="Nessun utente trovato"
          description="Prova con un'altra ricerca."
          icon={Users}
          area="admin"
        />
      </Card>
    );
  } else {
    elenco = (
      <Card className="overflow-hidden p-0">
        <Table
          className={cn(
            "min-w-[760px] [&_tbody_tr:last-child_td]:border-b-0",
            isPlaceholderData && "opacity-60 transition-opacity",
          )}
          classNameContenitore="px-2"
        >
          <caption className="sr-only">Elenco degli utenti registrati</caption>
          <thead>
            <tr>
              <Th className="w-10">
                <input
                  ref={selectAllRef}
                  type="checkbox"
                  checked={tuttiSelezionati}
                  onChange={toggleAll}
                  disabled={selezionabili.length === 0}
                  aria-label="Seleziona tutti gli utenti della pagina"
                  className={CASELLA}
                />
              </Th>
              <Th>Utente</Th>
              <Th>Ruolo</Th>
              <Th>Azienda</Th>
              <Th>Piano</Th>
              <Th>Stato</Th>
              <Th>Registrato</Th>
              <Th className="text-right">
                <span className="sr-only">Azioni</span>
              </Th>
            </tr>
          </thead>
          <tbody>
            {data?.items.map((user) => {
              const isSelf = user.profile.id === me?.profile.id;
              const fullName = [user.profile.nome, user.profile.cognome].filter(Boolean).join(" ");
              // Solo i figli ATTIVI ereditano il piano (pending/retrocessi
              // hanno un piano proprio, gestibile normalmente).
              const isManagedChild = user.family?.type === "child" && user.family.status === "active";
              const sospeso = !user.profile.is_active;
              const checked = selected.has(user.profile.id);
              const gruppo = gruppoAzienda(user);
              return (
                <tr
                  key={user.profile.id}
                  className={cn(
                    "transition-colors duration-150 ease-uscita",
                    checked ? "bg-accent-soft" : "hover:bg-desk",
                  )}
                >
                  <Td>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleOne(user.profile.id)}
                      disabled={isSelf}
                      title={isSelf ? "Non puoi selezionare il tuo account" : undefined}
                      aria-label={`Seleziona ${user.profile.email}`}
                      className={CASELLA}
                    />
                  </Td>
                  <Td>
                    <div className="flex items-center gap-3">
                      {/* Il nome è accanto: l'avatar non lo ripete allo screen reader. */}
                      <span aria-hidden>
                        <Avatar nome={fullName || user.profile.email} />
                      </span>
                      <div className="min-w-0">
                        <p className={cn("font-medium", sospeso ? "text-ink-2" : "text-ink")}>
                          {fullName || "—"}
                          {isSelf && <span className="ml-1.5 text-small font-normal text-ink-3">(tu)</span>}
                        </p>
                        <p className="text-small text-ink-3">{user.profile.email}</p>
                      </div>
                    </div>
                  </Td>
                  <Td>
                    <p className="text-ink-2">{RUOLO_LABELS[user.profile.role]}</p>
                    {user.progettista?.codice && (
                      <p className="text-small text-ink-3 tabular-nums">{user.progettista.codice}</p>
                    )}
                  </Td>
                  <Td>
                    {/* Ragione sociale (dal dossier, se no dalla registrazione) in cima; il
                        gruppo di account resta come informazione sotto. */}
                    {user.azienda_nome ? (
                      <p className="text-ink">{user.azienda_nome}</p>
                    ) : (
                      !gruppo && <span className="text-ink-3">—</span>
                    )}
                    {gruppo && <p className="text-small text-ink-3">{gruppo}</p>}
                  </Td>
                  <Td>
                    {user.subscription ? (
                      <>
                        <p className="text-ink-2">{planoCorrente(user, planiAttivi)}</p>
                        {user.subscription.inherited && (
                          <p className="text-small text-ink-3">(ereditato)</p>
                        )}
                      </>
                    ) : (
                      <span className="text-ink-3">—</span>
                    )}
                  </Td>
                  <Td>
                    {user.profile.is_active ? (
                      <Status tono="aperto">Attivo</Status>
                    ) : (
                      <Status tono="attenzione">Sospeso</Status>
                    )}
                  </Td>
                  <Td className="whitespace-nowrap text-ink-2 tabular-nums">
                    {formatDate(user.profile.created_at)}
                  </Td>
                  <Td>
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
                        <MenuItem icon={<Gift className="size-4" />} onSelect={() => openAddon(user)}>
                          Assegna add-on…
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
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>
    );
  }

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Utenti"
        area="admin"
        descrizione={
          data ? (
            <>
              <span className="font-medium text-white tabular-nums">{data.total}</span>{" "}
              {hasFilter ? "risultati" : "utenti registrati"}
            </>
          ) : (
            "Cerca, modifica ruoli e abbonamenti"
          )
        }
      />

      {/* Indicatore: il totale della query (lo stesso numero della descrizione);
          attenuato come la tabella finché mostra i dati della ricerca precedente. */}
      {data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard
            etichetta={hasFilter ? "Risultati" : "Utenti registrati"}
            valore={data.total}
            icon={Users}
            area="admin"
            className={cn(isPlaceholderData && "opacity-60 transition-opacity")}
          />
        </div>
      )}

      <div className="flex flex-col gap-3">
        {/* Barra: ricerca, ruolo, azzera. */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <SearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder="Cerca per email, nome o azienda…"
            label="Cerca utenti"
            className="sm:flex-1"
          />
          <Select
            label="Filtra per ruolo"
            value={role}
            onChange={(e) => setRole(e.target.value as typeof role)}
          >
            <option value="">Tutti i ruoli</option>
            <option value="admin">Solo admin</option>
            <option value="progettista">Solo progettisti</option>
            <option value="cliente">Solo clienti</option>
          </Select>
          {hasFilter && (
            <Button
              variant="ghost"
              className="shrink-0 self-start sm:self-auto"
              onClick={() => {
                setSearchInput("");
                setRole("");
              }}
            >
              Azzera i filtri
            </Button>
          )}
        </div>

        {/* Azioni di massa sugli utenti selezionati. */}
        {selected.size > 0 && (
          <div className="flex flex-wrap items-center gap-3 rounded-control border border-accent-line bg-accent-soft px-4 py-3 motion-safe:animate-entrata">
            <p className="text-body font-medium text-ink tabular-nums" aria-live="polite">
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
              <Button variant="danger" size="sm" onClick={() => setBulkPending({ is_active: false })}>
                <Ban className="size-4" aria-hidden />
                Sospendi selezionati
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
                Deseleziona
              </Button>
            </div>
          </div>
        )}
      </div>

      <div aria-busy={isPending || isPlaceholderData || fuoriPagina}>{elenco}</div>

      {data && data.total_pages > 1 && (
        <Pagination page={page} totalPages={data.total_pages} onChange={setPage} />
      )}

      {/* Dialog di conferma (azione singola): contiene le scelte, non è una
          semplice conferma. */}
      <Dialog
        open={!!pending}
        onClose={() => setPending(null)}
        dismissible={!actionBusy}
        title="Conferma operazione"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPending(null)} disabled={actionBusy}>
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
        <div className="flex flex-col gap-3">
          {pending?.kind === "role" && (
            <>
              <p>
                Cambia il ruolo di <strong className="text-ink">{pending.user.profile.email}</strong>.
              </p>
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
              {roleChoice === "progettista" && <p>{ADMIN_RUOLO_COPY.promozioneProgettista}</p>}
              {roleChoice === "admin" && <p>{ADMIN_RUOLO_COPY.nominaAdmin}</p>}
              {/* L'area progettista si perde solo tornando cliente (parità admin). */}
              {hasAreaProgettista(pending.user.profile.role) && !hasAreaProgettista(roleChoice) && (
                <p>{ADMIN_RUOLO_COPY.perditaAreaProgettista}</p>
              )}
            </>
          )}
          {pending?.kind === "active" && (
            <p>
              {pending.is_active ? "Riattivare" : "Sospendere"} l'account di{" "}
              <strong className="text-ink">{pending.user.profile.email}</strong>?
              {!pending.is_active && " L'utente non potrà più accedere alla piattaforma."}
            </p>
          )}
          {pending?.kind === "addon" && (
            <>
              <p>
                Assegna un add-on a{" "}
                <strong className="text-ink">{pending.user.profile.email}</strong>.
              </p>
              {(inventarioUtente?.length ?? 0) > 0 && (
                <p>
                  Possiede già:{" "}
                  {inventarioUtente!.map((m) => `${m.quantita} × ${m.nome}`).join(", ")}.
                </p>
              )}
              <SelectField
                label="Add-on"
                required
                value={grantAddonId}
                onChange={(e) => setGrantAddonId(e.target.value === "" ? "" : Number(e.target.value))}
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
                helper={addonPermanente ? "Add-on permanente: si possiede una volta sola." : undefined}
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
              {/* Nota fissa del dialog: non va annunciata come allarme all'apertura. */}
              <Alert tono="attenzione" ruolo="none">
                L'accredito è gratuito e verrà registrato nello storico dell'utente con il tuo
                nome.
              </Alert>
            </>
          )}
          {pending?.kind === "plan" && (
            <>
              <p>
                Cambia il piano di <strong className="text-ink">{pending.user.profile.email}</strong>.
                {planNome && (
                  <>
                    {" "}
                    L'abbonamento annuale di <strong className="text-ink">{planNome}</strong>{" "}
                    riparte da oggi.
                  </>
                )}
              </p>
              <SelectField
                label="Nuovo piano"
                required
                value={planChoice}
                onChange={(e) => setPlanChoice(e.target.value === "" ? "" : Number(e.target.value))}
              >
                <option value="">Seleziona un piano…</option>
                {pending.user.subscription && !planiAttivi.has(pending.user.subscription.plan.id) && (
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
              {/* Nota fissa del dialog: non va annunciata come allarme all'apertura. */}
              <Alert tono="attenzione" ruolo="none">
                Il cambio è gratuito e verrà registrato nello storico con il tuo nome. Un eventuale
                pagamento in corso dell'utente verrà annullato.
              </Alert>
            </>
          )}
          {actionError && <Alert tono="errore">{actionError}</Alert>}
        </div>
      </Dialog>

      {/* Conferma dell'azione di massa. */}
      <ConfirmDialog
        open={!!bulkPending}
        titolo="Conferma operazione di massa"
        conferma="Conferma"
        distruttiva={!!bulkPending && !bulkPending.is_active}
        inCorso={bulkBusy}
        onConferma={() => void confirmBulk()}
        onAnnulla={() => setBulkPending(null)}
      >
        {bulkPending && (
          <p>
            {bulkPending.is_active ? "Riattivare" : "Sospendere"}{" "}
            <strong className="text-ink">
              {selected.size} utent{selected.size === 1 ? "e" : "i"}
            </strong>{" "}
            selezionat{selected.size === 1 ? "o" : "i"}?
            {!bulkPending.is_active && " Non potranno più accedere alla piattaforma."}
          </p>
        )}
      </ConfirmDialog>
    </Page>
  );
}

function SkeletonRighe() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-12 w-full rounded-control" />
      ))}
    </div>
  );
}
