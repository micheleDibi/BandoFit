import {
  AlertTriangle,
  Building2,
  Clock3,
  Lock,
  Pencil,
  RotateCcw,
  Send,
  Trash2,
  UserPlus,
  Users,
} from "lucide-react";
import { useState } from "react";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextField } from "../components/ui/Field";
import { Menu, MenuItem, MenuSeparator } from "../components/ui/Menu";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useCompanies } from "../hooks/useCompanies";
import { useEntitlements } from "../hooks/useEntitlements";
import {
  useFamily,
  useInviteMember,
  useReactivateMember,
  useRemoveMember,
  useResendInvite,
  useUpdateMember,
} from "../hooks/useFamily";
import { useMe } from "../hooks/useMe";
import { apiErrorMessage } from "../lib/api";
import { formatDate } from "../lib/format";
import type { FamilyMember } from "../types";

function MemberStatusBadge({ status }: { status: FamilyMember["status"] }) {
  if (status === "active") return <Badge tone="emerald">Attivo</Badge>;
  if (status === "pending")
    return (
      <Badge tone="amber">
        <Clock3 className="size-3" aria-hidden />
        In attesa
      </Badge>
    );
  return <Badge tone="slate">Retrocesso</Badge>;
}

function MemberAvatar({ member }: { member: FamilyMember }) {
  const iniziali =
    member.denominazione
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join("") || member.email[0]?.toUpperCase();
  return (
    <span
      aria-hidden
      className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-700"
    >
      {iniziali}
    </span>
  );
}

/** Budget nel form: "" = illimitato, altrimenti il tetto per ciclo. */
type BudgetChoice = { illimitato: boolean; tetto: string };

function BudgetFields({
  value,
  onChange,
  idPrefix,
}: {
  value: BudgetChoice;
  onChange: (v: BudgetChoice) => void;
  idPrefix: string;
}) {
  return (
    <fieldset className="mt-3">
      <legend className="text-sm font-medium text-slate-700">AI-check</legend>
      <p className="mt-0.5 text-xs text-slate-500">
        Decidi quanti AI-check all'anno può usare questo account (con 0 non può avviarne).
        Ogni analisi si scala comunque dagli AI-check inclusi nel tuo piano.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-4">
        <label className="inline-flex items-center gap-1.5 text-sm text-slate-700">
          <input
            type="radio"
            name={`${idPrefix}-budget`}
            className="accent-brand-500"
            checked={value.illimitato}
            onChange={() => onChange({ ...value, illimitato: true })}
          />
          Senza limite
        </label>
        <label className="inline-flex items-center gap-1.5 text-sm text-slate-700">
          <input
            type="radio"
            name={`${idPrefix}-budget`}
            className="accent-brand-500"
            checked={!value.illimitato}
            onChange={() => onChange({ illimitato: false, tetto: value.tetto || "0" })}
          />
          Al massimo
        </label>
        {!value.illimitato && (
          <>
            <input
              type="number"
              min={0}
              max={9999}
              aria-label="Numero massimo di AI-check all'anno"
              value={value.tetto}
              onChange={(e) => onChange({ illimitato: false, tetto: e.target.value })}
              className="w-24 rounded-lg border border-slate-300 px-3 py-1.5 text-sm tabular-nums focus:border-brand-400 focus:outline-none"
            />
            <span className="text-sm text-slate-500">all'anno</span>
          </>
        )}
      </div>
    </fieldset>
  );
}

function budgetToApi(v: BudgetChoice): number | null {
  if (v.illimitato) return null;
  const n = Number(v.tetto);
  return Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
}

export default function Collegati() {
  const { data: me, isPending: mePending } = useMe();
  const family = useFamily();
  const entitlements = useEntitlements();
  const companies = useCompanies();

  const invite = useInviteMember();
  const resend = useResendInvite();
  const reactivate = useReactivateMember();
  const remove = useRemoveMember();
  const update = useUpdateMember();

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteNome, setInviteNome] = useState("");
  const [inviteCompany, setInviteCompany] = useState("");
  const [inviteBudget, setInviteBudget] = useState<BudgetChoice>({ illimitato: false, tetto: "0" });
  const [editing, setEditing] = useState<FamilyMember | null>(null);
  const [editCompany, setEditCompany] = useState("");
  const [editVisibili, setEditVisibili] = useState<Set<string>>(new Set());
  const [editBudget, setEditBudget] = useState<BudgetChoice>({ illimitato: true, tetto: "" });
  const [removing, setRemoving] = useState<FamilyMember | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const aziende = companies.data?.aziende ?? [];
  const multiAziende = aziende.length > 1;

  if (mePending) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  // Permesso negato: la pagina è del TITOLARE con piano multi-account.
  if (me?.family?.role !== "parent") {
    return (
      <div className="mx-auto max-w-5xl">
        <Card className="flex flex-col items-center px-6 py-12 text-center">
          <div className="rounded-full bg-slate-100 p-3 text-slate-500">
            <Lock className="size-7" aria-hidden />
          </div>
          <h1 className="mt-4 font-display text-base font-semibold text-slate-900">
            Gestione riservata al titolare
          </h1>
          <p className="mt-1 max-w-sm text-sm text-slate-500">
            {me?.family?.role === "child"
              ? "Gli account collegati si gestiscono dall'account titolare della tua Azienda."
              : "Il tuo piano non prevede account aggiuntivi: passa a un piano superiore per invitarne."}
          </p>
        </Card>
      </div>
    );
  }

  const data = family.data;
  const slotsFree = !!data && data.used < data.limit;
  const poolResiduo = entitlements.data?.ai_checks.residuo ?? null;
  // Overbooking (permesso per scelta: lo scalo è al consumo): la somma dei
  // tetti assegnati supera il residuo del pool → avviso, non blocco.
  const attivi = (data?.members ?? []).filter((m) => m.status === "active");
  const sommaBudget = attivi.reduce(
    (acc, m) => (m.ai_check_budget === null ? acc : acc + m.ai_check_budget),
    0,
  );
  const overbooking =
    poolResiduo !== null &&
    attivi.some((m) => m.ai_check_budget !== null) &&
    sommaBudget > poolResiduo;

  const apriInvito = () => {
    setActionError(null);
    setInviteEmail("");
    setInviteNome("");
    setInviteCompany(aziende.find((a) => a.attiva)?.id ?? aziende[0]?.id ?? "");
    setInviteBudget({ illimitato: false, tetto: "0" });
    setInviteOpen(true);
  };

  const confirmInvite = async () => {
    setActionError(null);
    try {
      const result = await invite.mutateAsync({
        email: inviteEmail.trim(),
        denominazione: inviteNome.trim(),
        company_profile_id: multiAziende ? inviteCompany || undefined : undefined,
        ai_check_budget: budgetToApi(inviteBudget),
      });
      setInviteOpen(false);
      setNotice(
        result.email_sent
          ? "Invito inviato: l'account comparirà come «In attesa» finché non viene accettato."
          : "Invito creato, ma l'email non è partita: usa «Reinvia» tra qualche minuto.",
      );
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const apriModifica = (member: FamilyMember) => {
    setActionError(null);
    setEditing(member);
    setEditCompany(member.company_profile_id ?? "");
    setEditVisibili(new Set(member.aziende_visibili));
    setEditBudget(
      member.ai_check_budget === null
        ? { illimitato: true, tetto: "" }
        : { illimitato: false, tetto: String(member.ai_check_budget) },
    );
  };

  const confirmModifica = async () => {
    if (!editing) return;
    setActionError(null);
    // Solo i campi CAMBIATI: il PATCH applica ciò che riceve.
    const changes: {
      company_profile_id?: string;
      aziende_visibili?: string[];
      ai_check_budget?: number | null;
    } = {};
    if (editCompany && editCompany !== (editing.company_profile_id ?? "")) {
      changes.company_profile_id = editCompany;
    }
    if (multiAziende) {
      const visibili = new Set(editVisibili);
      if (editCompany) visibili.add(editCompany); // invariante ⊇ appartenenza
      const prima = [...editing.aziende_visibili].sort().join(",");
      const dopo = [...visibili].sort().join(",");
      if (prima !== dopo) changes.aziende_visibili = [...visibili];
    }
    const budget = budgetToApi(editBudget);
    if (budget !== editing.ai_check_budget) changes.ai_check_budget = budget;
    if (Object.keys(changes).length === 0) {
      setEditing(null);
      return;
    }
    try {
      await update.mutateAsync({ membershipId: editing.id, changes });
      setEditing(null);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const confirmRemove = async () => {
    if (!removing) return;
    setActionError(null);
    try {
      await remove.mutateAsync(removing.id);
      setRemoving(null);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const azione = async (fn: () => Promise<unknown>) => {
    setActionError(null);
    setNotice(null);
    try {
      await fn();
    } catch (err) {
      setNotice(apiErrorMessage(err));
    }
  };

  const actionBusy =
    invite.isPending || update.isPending || remove.isPending || reactivate.isPending;

  return (
    <div className="mx-auto max-w-5xl">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold text-slate-900">
            <Users className="size-6 text-brand-500" aria-hidden />
            Account collegati
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {data ? (
              <>
                <strong className="tabular-nums text-slate-700">
                  {data.used} di {data.limit}
                </strong>{" "}
                account usati (incluso il tuo).
              </>
            ) : (
              "Le persone che lavorano con il tuo abbonamento."
            )}
          </p>
        </div>
        <Button
          onClick={apriInvito}
          disabled={!slotsFree}
          title={!slotsFree ? "Hai raggiunto il limite di account del tuo piano" : undefined}
        >
          <UserPlus className="size-4" aria-hidden />
          Invita account
        </Button>
      </div>
      {!slotsFree && data && (
        <p className="mt-2 text-xs text-slate-500">
          Hai raggiunto il limite di account del tuo piano: libera un posto o acquista
          l'add-on «Account collegato aggiuntivo» dalla pagina Abbonamento.
        </p>
      )}

      {overbooking && (
        <p
          role="status"
          aria-live="polite"
          className="mt-4 inline-flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          Hai assegnato in tutto {sommaBudget} AI-check, ma al tuo piano ne restano{" "}
          {poolResiduo}: va bene, conta solo chi li usa davvero — però se tutti usano il
          loro, i primi li esauriranno per tutti.
        </p>
      )}

      {notice && (
        <p role="status" className="mt-4 rounded-lg bg-brand-50 px-4 py-3 text-sm text-brand-800">
          {notice}
        </p>
      )}

      <div className="mt-6">
        {family.isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : family.isError ? (
          <ErrorState message={apiErrorMessage(family.error)} onRetry={() => family.refetch()} />
        ) : (data?.members.length ?? 0) === 0 ? (
          <EmptyState
            title="Nessun account collegato"
            description="Invita le persone che lavorano con te: useranno il tuo stesso abbonamento, sulle aziende che decidi tu."
            action={
              <Button onClick={apriInvito} disabled={!slotsFree}>
                <UserPlus className="size-4" aria-hidden />
                Invita account
              </Button>
            }
          />
        ) : (
          <Card className="overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-[760px] w-full text-sm">
                <caption className="sr-only">
                  Account collegati: stato, azienda, visibilità, budget AI-check e azioni
                </caption>
                <thead>
                  <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-400">
                    <th scope="col" className="px-4 py-3 font-medium">Account</th>
                    <th scope="col" className="px-4 py-3 font-medium">Stato</th>
                    <th scope="col" className="px-4 py-3 font-medium">Azienda</th>
                    {multiAziende && (
                      <th scope="col" className="px-4 py-3 font-medium">Visibilità</th>
                    )}
                    <th scope="col" className="px-4 py-3 font-medium">AI-check</th>
                    <th scope="col" className="px-4 py-3 font-medium">Invitato</th>
                    <th scope="col" className="px-4 py-3 text-right font-medium">
                      <span className="sr-only">Azioni</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data?.members.map((member) => (
                    <tr
                      key={member.id}
                      className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60"
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-3">
                          <MemberAvatar member={member} />
                          <div className="min-w-0">
                            <p className="truncate font-medium text-slate-900">
                              {member.denominazione}
                            </p>
                            <p className="truncate text-xs text-slate-500">{member.email}</p>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <MemberStatusBadge status={member.status} />
                      </td>
                      <td className="px-4 py-3">
                        {member.company_nome ? (
                          <span className="inline-flex items-center gap-1.5 text-slate-700">
                            <Building2 className="size-3.5 shrink-0 text-slate-400" aria-hidden />
                            <span className="max-w-40 truncate">{member.company_nome}</span>
                          </span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      {multiAziende && (
                        <td className="px-4 py-3">
                          <span
                            className="text-slate-700 tabular-nums"
                            title={aziende
                              .filter((a) => member.aziende_visibili.includes(a.id))
                              .map((a) => a.ragione_sociale)
                              .join(", ")}
                          >
                            {member.aziende_visibili.length}{" "}
                            {member.aziende_visibili.length === 1 ? "azienda" : "aziende"}
                          </span>
                        </td>
                      )}
                      <td className="px-4 py-3">
                        {member.ai_check_budget === null ? (
                          <span className="text-slate-700">Senza limite</span>
                        ) : member.ai_check_budget === 0 ? (
                          <span className="text-slate-400">Nessuno</span>
                        ) : (
                          <span className="tabular-nums text-slate-700">
                            {member.ai_check_usati} di {member.ai_check_budget} usati
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 tabular-nums text-slate-500">
                        {formatDate(member.invited_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Menu label={`Azioni per ${member.denominazione}`}>
                          <MenuItem
                            icon={<Pencil className="size-4" aria-hidden />}
                            onSelect={() => apriModifica(member)}
                          >
                            Modifica…
                          </MenuItem>
                          {member.status === "pending" && (
                            <MenuItem
                              icon={<Send className="size-4" aria-hidden />}
                              onSelect={() =>
                                azione(async () => {
                                  await resend.mutateAsync(member.id);
                                  setNotice("Invito reinviato.");
                                })
                              }
                            >
                              Reinvia invito
                            </MenuItem>
                          )}
                          {member.status === "demoted" && (
                            <MenuItem
                              icon={<RotateCcw className="size-4" aria-hidden />}
                              disabled={!slotsFree}
                              title={!slotsFree ? "Non ci sono posti liberi nel tuo piano" : undefined}
                              onSelect={() => azione(() => reactivate.mutateAsync(member.id))}
                            >
                              Riattiva
                            </MenuItem>
                          )}
                          <MenuSeparator />
                          <MenuItem
                            danger
                            icon={<Trash2 className="size-4" aria-hidden />}
                            onSelect={() => {
                              setActionError(null);
                              setRemoving(member);
                            }}
                          >
                            {member.status === "pending" ? "Revoca invito" : "Rimuovi"}
                          </MenuItem>
                        </Menu>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      {/* ------------------------------------------------------ dialog invito */}
      <Dialog
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        dismissible={!actionBusy}
        title="Invita un account"
        footer={
          <>
            <Button variant="ghost" onClick={() => setInviteOpen(false)} disabled={actionBusy}>
              Annulla
            </Button>
            <Button
              loading={invite.isPending}
              disabled={
                !inviteEmail.trim() ||
                !inviteNome.trim() ||
                (multiAziende && !inviteCompany)
              }
              onClick={confirmInvite}
            >
              Invia invito
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          La persona invitata userà il tuo stesso abbonamento. Occupa un posto già
          dall'invito ({data?.used ?? 1} di {data?.limit ?? 1} usati).
        </p>
        <div className="mt-3 space-y-3">
          <TextField
            label="Email"
            type="email"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            placeholder="nome@azienda.it"
          />
          <TextField
            label="Denominazione"
            value={inviteNome}
            onChange={(e) => setInviteNome(e.target.value)}
            placeholder="Es. Sede di Bari, Ufficio gare…"
          />
          {multiAziende && (
            <SelectField
              label="Azienda di appartenenza"
              value={inviteCompany}
              onChange={(e) => setInviteCompany(e.target.value)}
            >
              <option value="" disabled>
                Scegli un'azienda…
              </option>
              {aziende.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.ragione_sociale}
                </option>
              ))}
            </SelectField>
          )}
        </div>
        <BudgetFields value={inviteBudget} onChange={setInviteBudget} idPrefix="invite" />
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>

      {/* ---------------------------------------------------- dialog modifica */}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        dismissible={!actionBusy}
        title={`Modifica ${editing?.denominazione ?? ""}`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)} disabled={actionBusy}>
              Annulla
            </Button>
            <Button loading={update.isPending} onClick={confirmModifica}>
              Salva
            </Button>
          </>
        }
      >
        {multiAziende && (
          <>
            <SelectField
              label="Azienda di appartenenza"
              value={editCompany}
              onChange={(e) => setEditCompany(e.target.value)}
            >
              {aziende.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.ragione_sociale}
                </option>
              ))}
            </SelectField>
            <fieldset className="mt-3">
              <legend className="text-sm font-medium text-slate-700">Aziende visibili</legend>
              <p className="mt-0.5 text-xs text-slate-500">
                L'azienda di appartenenza è sempre visibile. Le nuove aziende che creerai
                NON saranno visibili finché non le concedi da qui.
              </p>
              <div className="mt-2 space-y-1.5">
                {aziende.map((a) => {
                  const isMembership = a.id === editCompany;
                  return (
                    <label
                      key={a.id}
                      className="flex items-center gap-2 text-sm text-slate-700"
                    >
                      <input
                        type="checkbox"
                        className="accent-brand-500"
                        checked={isMembership || editVisibili.has(a.id)}
                        disabled={isMembership}
                        onChange={(e) =>
                          setEditVisibili((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(a.id);
                            else next.delete(a.id);
                            return next;
                          })
                        }
                      />
                      <span className="truncate">{a.ragione_sociale}</span>
                      {isMembership && (
                        <span className="text-xs text-slate-400">(appartenenza)</span>
                      )}
                    </label>
                  );
                })}
              </div>
            </fieldset>
          </>
        )}
        <BudgetFields value={editBudget} onChange={setEditBudget} idPrefix="edit" />
        {editing && editing.ai_check_usati > 0 && (
          <p className="mt-2 text-xs text-slate-500">
            Quest'anno ha già usato {editing.ai_check_usati} AI-check.
          </p>
        )}
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>

      {/* ---------------------------------------------------- dialog rimozione */}
      <Dialog
        open={removing !== null}
        onClose={() => setRemoving(null)}
        dismissible={!actionBusy}
        title={removing?.status === "pending" ? "Revocare l'invito?" : "Rimuovere l'account?"}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRemoving(null)} disabled={actionBusy}>
              Annulla
            </Button>
            <Button variant="danger" loading={remove.isPending} onClick={confirmRemove}>
              {removing?.status === "pending" ? "Revoca invito" : "Rimuovi"}
            </Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          {removing?.status === "pending" ? (
            <>L'invito a <strong>{removing?.email}</strong> non sarà più valido. Il posto si libera subito.</>
          ) : (
            <>
              <strong>{removing?.denominazione}</strong> non userà più il tuo abbonamento:
              tornerà su un piano Gratuito indipendente. I dati delle aziende restano alle
              aziende. Il posto si libera subito.
            </>
          )}
        </p>
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </div>
  );
}
