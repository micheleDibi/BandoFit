import { CircleUser, Pencil, RotateCcw, Send, Sparkles, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { Alert } from "../components/ui/Alert";
import { Avatar } from "../components/ui/Avatar";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Checkbox } from "../components/ui/Checkbox";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Dialog } from "../components/ui/Dialog";
import { SelectField, TextField } from "../components/ui/Field";
import { InlineError } from "../components/ui/InlineError";
import { KpiCard } from "../components/ui/KpiCard";
import { Menu, MenuItem, MenuSeparator } from "../components/ui/Menu";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { ProgressRing } from "../components/ui/ProgressRing";
import { RadioGroup } from "../components/ui/RadioGroup";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Status } from "../components/ui/Status";
import { Table, Td, Th } from "../components/ui/Table";
import { TextLink } from "../components/ui/TextLink";
import { useToast } from "../components/ui/Toast";
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

/** Stato del membro in parole: una parola con il suo punto. «In attesa» ha
 *  sotto la sua spiegazione (dopo l'invito il toast sparisce, la riga resta). */
function MemberStatus({ status }: { status: FamilyMember["status"] }) {
  if (status === "active") return <Status tono="aperto">Attivo</Status>;
  if (status === "pending")
    return (
      <span className="flex flex-col gap-0.5">
        <Status tono="in-apertura">In attesa</Status>
        <span className="text-small text-ink-3">finché l'invito non viene accettato</span>
      </span>
    );
  return <Status tono="chiuso">Retrocesso</Status>;
}

/** Budget nel form: illimitato, oppure il numero massimo di AI-check all'anno. */
type BudgetChoice = { illimitato: boolean; tetto: string };

function BudgetFields({
  value,
  onChange,
  nome,
}: {
  value: BudgetChoice;
  onChange: (v: BudgetChoice) => void;
  nome: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <RadioGroup
        nome={nome}
        legend="AI-check"
        descrizione="Decidi quanti AI-check all'anno può usare questo account (con 0 non può avviarne). Ogni analisi si scala comunque dagli AI-check inclusi nel tuo piano."
        opzioni={[
          { id: "illimitato", label: "Senza limite" },
          { id: "limite", label: "Al massimo" },
        ]}
        valore={value.illimitato ? "illimitato" : "limite"}
        onChange={(id) =>
          onChange(
            id === "illimitato"
              ? { ...value, illimitato: true }
              : { illimitato: false, tetto: value.tetto || "0" },
          )
        }
      />
      {!value.illimitato && (
        // Allineato all'etichetta dell'opzione (radio 16px + 10px di spazio).
        <div className="w-40 pl-6.5">
          <TextField
            label="AI-check all'anno"
            type="number"
            min={0}
            max={9999}
            inputMode="numeric"
            value={value.tetto}
            onChange={(e) => onChange({ illimitato: false, tetto: e.target.value })}
            className="tabular-nums"
          />
        </div>
      )}
    </div>
  );
}

function budgetToApi(v: BudgetChoice): number | null {
  if (v.illimitato) return null;
  const n = Number(v.tetto);
  return Number.isFinite(n) && n >= 0 ? Math.trunc(n) : 0;
}

type Avviso = { tono: "attenzione" | "errore"; testo: string };

/** «Account collegati» (solo titolare): gli account che usano l'abbonamento,
 *  in tabella con lo stato in parole e un menu di azioni per riga. «Invita un
 *  account» è l'unico pulsante pieno. */
export default function Collegati() {
  const { data: me, isPending: mePending } = useMe();
  const family = useFamily();
  const entitlements = useEntitlements();
  const companies = useCompanies();
  const { mostra } = useToast();

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
  const [avviso, setAvviso] = useState<Avviso | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const aziende = companies.data?.aziende ?? [];
  const multiAziende = aziende.length > 1;

  if (mePending) {
    return (
      <Page variante="elenco">
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-64 w-full" />
        </div>
      </Page>
    );
  }

  // Permesso negato: la pagina è del TITOLARE con piano multi-account.
  if (me?.family?.role !== "parent") {
    const membro = me?.family?.role === "child";
    return (
      <Page variante="elenco">
        <PageHeader titolo="Account collegati" area="account" />
        <Card>
          <EmptyState
            title="Gestione riservata al titolare"
            area="account"
            description={
              membro
                ? "Gli account collegati si gestiscono dall'account titolare della tua Azienda."
                : "Il tuo piano non prevede account aggiuntivi: passa a un piano superiore per invitarne."
            }
            action={
              membro ? undefined : (
                <LinkButton to="/app/abbonamento" variant="secondary">
                  Vedi i piani
                </LinkButton>
              )
            }
          />
        </Card>
      </Page>
    );
  }

  const data = family.data;
  const slotsFree = !!data && data.used < data.limit;
  const poolResiduo = entitlements.data?.ai_checks.residuo ?? null;
  // Come l'indicatore della Home: senza AI-check nel piano (né add-on) si dice.
  const aiCheckNelPiano = (entitlements.data?.ai_checks.effettivo ?? 0) > 0;
  // Overbooking (permesso per scelta: lo scalo è al consumo): la somma dei
  // tetti assegnati supera il residuo del pool: avviso, non blocco.
  const attivi = (data?.members ?? []).filter((m) => m.status === "active");
  const sommaBudget = attivi.reduce(
    (acc, m) => (m.ai_check_budget === null ? acc : acc + m.ai_check_budget),
    0,
  );
  const overbooking =
    poolResiduo !== null &&
    attivi.some((m) => m.ai_check_budget !== null) &&
    sommaBudget > poolResiduo;
  const inAttesa = (data?.members ?? []).filter((m) => m.status === "pending").length;

  const apriInvito = () => {
    setActionError(null);
    setAvviso(null);
    setInviteEmail("");
    setInviteNome("");
    setInviteCompany(aziende.find((a) => a.attiva)?.id ?? aziende[0]?.id ?? "");
    setInviteBudget({ illimitato: false, tetto: "0" });
    setInviteOpen(true);
  };

  const confirmInvite = async () => {
    setActionError(null);
    setAvviso(null);
    try {
      const result = await invite.mutateAsync({
        email: inviteEmail.trim(),
        denominazione: inviteNome.trim(),
        company_profile_id: multiAziende ? inviteCompany || undefined : undefined,
        ai_check_budget: budgetToApi(inviteBudget),
      });
      setInviteOpen(false);
      if (result.email_sent) {
        mostra({ testo: "Invito inviato" });
      } else {
        setAvviso({
          tono: "attenzione",
          testo:
            "Invito creato, ma l'email non è partita: usa «Reinvia invito» tra qualche minuto.",
        });
      }
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const apriModifica = (member: FamilyMember) => {
    setActionError(null);
    setAvviso(null);
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
      mostra({ testo: "Modifiche salvate" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const confirmRemove = async () => {
    if (!removing) return;
    const eraInvito = removing.status === "pending";
    setActionError(null);
    try {
      await remove.mutateAsync(removing.id);
      setRemoving(null);
      mostra({ testo: eraInvito ? "Invito revocato" : "Account rimosso" });
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  // Azioni dirette dal menu (senza finestra): esito in un toast, errore in pagina.
  const azione = async (fn: () => Promise<unknown>, esito: string) => {
    setActionError(null);
    setAvviso(null);
    try {
      await fn();
      mostra({ testo: esito });
    } catch (err) {
      setAvviso({ tono: "errore", testo: apiErrorMessage(err) });
    }
  };

  const actionBusy =
    invite.isPending || update.isPending || remove.isPending || reactivate.isPending;

  return (
    <Page variante="elenco">
      {/* Intestazione e avvisi in un blocco senza gap: la regione live vuota
          resta montata senza aggiungere spazio. */}
      <div className="flex flex-col">
        <PageHeader
          titolo="Account collegati"
          area="account"
          descrizione={
            data ? (
              <>
                <span className="font-semibold tabular-nums text-white">
                  {data.used} di {data.limit}
                </span>{" "}
                account usati (incluso il tuo).
              </>
            ) : (
              "Le persone che lavorano con il tuo abbonamento."
            )
          }
          azioni={
            <Button type="button" variant="inverse" onClick={apriInvito} disabled={!slotsFree}>
              <UserPlus className="size-4" aria-hidden />
              Invita un account
            </Button>
          }
        />
        {!slotsFree && data && (
          <Alert
            tono="info"
            className="mt-4"
            azione={
              <TextLink to="/app/abbonamento?tab=addon" className="text-small font-medium">
                Vedi gli add-on
              </TextLink>
            }
          >
            Hai raggiunto il limite di account del tuo piano: libera un posto o acquista
            l'add-on «Account collegato aggiuntivo».
          </Alert>
        )}
        <div aria-live="polite">
          {overbooking && (
            <Alert tono="attenzione" ruolo="none" className="mt-4">
              Hai assegnato in tutto {sommaBudget} AI-check, ma al tuo piano ne restano{" "}
              {poolResiduo}: va bene, conta solo chi li usa davvero — però se tutti usano il
              loro, i primi li esauriranno per tutti.
            </Alert>
          )}
        </div>
        {avviso && (
          <Alert tono={avviso.tono} className="mt-4">
            {avviso.testo}
          </Alert>
        )}
      </div>

      {/* Indicatori: solo i numeri già in pagina (posti, inviti, AI-check del piano). */}
      {data && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <KpiCard
            etichetta="Account usati"
            valore={data.used}
            nota={`su ${data.limit}, incluso il tuo`}
            className="relative pr-28"
          >
            <ProgressRing
              value={data.used}
              max={data.limit}
              size={72}
              tono="account"
              label={`Account usati: ${data.used} su ${data.limit}`}
              className="absolute top-1/2 right-5 -translate-y-1/2"
            >
              <CircleUser className="size-6 text-area-account-ink" aria-hidden />
            </ProgressRing>
          </KpiCard>
          {inAttesa > 0 && (
            <KpiCard
              etichetta="Inviti in attesa"
              valore={inAttesa}
              nota="Occupano già un posto"
              icon={Send}
              area="account"
            />
          )}
          {poolResiduo !== null && (
            <KpiCard
              etichetta="AI-check disponibili"
              valore={aiCheckNelPiano ? poolResiduo : 0}
              nota={
                aiCheckNelPiano
                  ? "Ancora da usare quest'anno, per tutti gli account"
                  : "Non inclusi nel piano"
              }
              icon={Sparkles}
              area="aicheck"
            />
          )}
        </div>
      )}

      {family.isPending ? (
        <div className="flex flex-col gap-3" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : family.isError ? (
        <ErrorState message={apiErrorMessage(family.error)} onRetry={() => family.refetch()} />
      ) : (data?.members.length ?? 0) === 0 ? (
        <Card>
          <EmptyState
            title="Nessun account collegato"
            icon={UserPlus}
            area="account"
            description="Invita le persone che lavorano con te: useranno il tuo stesso abbonamento, sulle aziende che decidi tu."
            action={
              <Button type="button" variant="secondary" onClick={apriInvito} disabled={!slotsFree}>
                <UserPlus className="size-4" aria-hidden />
                Invita un account
              </Button>
            }
          />
        </Card>
      ) : (
        <Card className="overflow-hidden p-0">
          <Table
            className="min-w-[760px] [&_tbody_tr:last-child_td]:border-b-0"
            classNameContenitore="px-2"
          >
            <caption className="sr-only">
              Account collegati: stato, azienda, visibilità, budget AI-check e azioni
            </caption>
            <thead>
              <tr>
                <Th>Account</Th>
                <Th>Stato</Th>
                <Th>Azienda</Th>
                {multiAziende && <Th>Visibilità</Th>}
                <Th>AI-check</Th>
                <Th>Invitato il</Th>
                <Th>
                  <span className="sr-only">Azioni</span>
                </Th>
              </tr>
            </thead>
            <tbody>
              {data?.members.map((member) => {
                const nomiVisibili = aziende
                  .filter((a) => member.aziende_visibili.includes(a.id))
                  .map((a) => a.ragione_sociale)
                  .join(", ");
                return (
                  <tr key={member.id}>
                    <Td>
                      <div className="flex items-center gap-3">
                        {/* Il nome è già scritto accanto: l'avatar non si annuncia. */}
                        <span aria-hidden className="flex">
                          <Avatar nome={member.denominazione || member.email} />
                        </span>
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-ink">{member.denominazione}</p>
                          <p className="truncate text-small text-ink-2">{member.email}</p>
                        </div>
                      </div>
                    </Td>
                    <Td>
                      <MemberStatus status={member.status} />
                    </Td>
                    <Td>
                      {member.company_nome ? (
                        <span className="block text-ink">
                          {member.company_nome}
                        </span>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </Td>
                    {multiAziende && (
                      <Td>
                        <span className="block tabular-nums text-ink">
                          {member.aziende_visibili.length}{" "}
                          {member.aziende_visibili.length === 1 ? "azienda" : "aziende"}
                        </span>
                        {nomiVisibili && (
                          <span className="block text-small text-ink-3">
                            {nomiVisibili}
                          </span>
                        )}
                      </Td>
                    )}
                    <Td>
                      {member.ai_check_budget === null ? (
                        <span className="text-ink">Senza limite</span>
                      ) : member.ai_check_budget === 0 ? (
                        <span className="text-ink-3">Nessuno</span>
                      ) : (
                        <span className="tabular-nums text-ink">
                          {member.ai_check_usati} di {member.ai_check_budget} usati
                        </span>
                      )}
                    </Td>
                    <Td className="tabular-nums text-ink-2">{formatDate(member.invited_at)}</Td>
                    <Td className="py-2.5 text-right">
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
                              azione(() => resend.mutateAsync(member.id), "Invito reinviato")
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
                            onSelect={() =>
                              azione(() => reactivate.mutateAsync(member.id), "Account riattivato")
                            }
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
                            setAvviso(null);
                            setRemoving(member);
                          }}
                        >
                          {member.status === "pending" ? "Revoca invito" : "Rimuovi"}
                        </MenuItem>
                      </Menu>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}

      {/* ------------------------------------------------------ finestra invito */}
      <Dialog
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        dismissible={!actionBusy}
        title="Invita un account"
        footer={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setInviteOpen(false)}
              disabled={actionBusy}
            >
              Annulla
            </Button>
            <Button
              type="button"
              loading={invite.isPending}
              disabled={!inviteEmail.trim() || !inviteNome.trim() || (multiAziende && !inviteCompany)}
              onClick={confirmInvite}
            >
              Invia l'invito
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          <p>
            La persona invitata userà il tuo stesso abbonamento. Occupa un posto già
            dall'invito ({data?.used ?? 1} di {data?.limit ?? 1} usati).
          </p>
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
          <BudgetFields value={inviteBudget} onChange={setInviteBudget} nome="invito-budget" />
          {actionError && <InlineError>{actionError}</InlineError>}
        </div>
      </Dialog>

      {/* ---------------------------------------------------- finestra modifica */}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        dismissible={!actionBusy}
        title={`Modifica ${editing?.denominazione ?? ""}`}
        footer={
          <>
            <Button
              type="button"
              variant="secondary"
              onClick={() => setEditing(null)}
              disabled={actionBusy}
            >
              Annulla
            </Button>
            <Button type="button" loading={update.isPending} onClick={confirmModifica}>
              Salva
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
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
              <fieldset className="flex flex-col gap-2" aria-describedby="collegati-visibili-aiuto">
                <legend className="mb-1 text-small font-medium text-ink">Aziende visibili</legend>
                <p id="collegati-visibili-aiuto" className="mb-1 text-small text-ink-3">
                  L'azienda di appartenenza è sempre visibile. Le nuove aziende che creerai non
                  saranno visibili finché non le concedi da qui.
                </p>
                {aziende.map((a) => {
                  const isMembership = a.id === editCompany;
                  return (
                    <Checkbox
                      key={a.id}
                      label={a.ragione_sociale}
                      descrizione={isMembership ? "Azienda di appartenenza" : undefined}
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
                  );
                })}
              </fieldset>
            </>
          )}
          <BudgetFields value={editBudget} onChange={setEditBudget} nome="modifica-budget" />
          {editing && editing.ai_check_usati > 0 && (
            <p className="text-small text-ink-3">
              Quest'anno ha già usato {editing.ai_check_usati} AI-check.
            </p>
          )}
          {actionError && <InlineError>{actionError}</InlineError>}
        </div>
      </Dialog>

      {/* --------------------------------------------------- conferma rimozione */}
      <ConfirmDialog
        open={removing !== null}
        titolo={removing?.status === "pending" ? "Revocare l'invito?" : "Rimuovere l'account?"}
        conferma={removing?.status === "pending" ? "Revoca invito" : "Rimuovi"}
        distruttiva
        inCorso={remove.isPending}
        onConferma={confirmRemove}
        onAnnulla={() => setRemoving(null)}
      >
        <p>
          {removing?.status === "pending" ? (
            <>
              L'invito a <strong className="text-ink">{removing?.email}</strong> non sarà più
              valido. Il posto si libera subito.
            </>
          ) : (
            <>
              <strong className="text-ink">{removing?.denominazione}</strong> non userà più il tuo
              abbonamento: tornerà su un piano Gratuito indipendente. I dati delle aziende restano
              alle aziende. Il posto si libera subito.
            </>
          )}
        </p>
        {actionError && <InlineError className="mt-3">{actionError}</InlineError>}
      </ConfirmDialog>
    </Page>
  );
}
