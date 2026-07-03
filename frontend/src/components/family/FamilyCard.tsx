import { Clock3, Mail, RotateCcw, UserPlus, Users } from "lucide-react";
import { useState, type FormEvent } from "react";
import {
  useFamily,
  useInviteMember,
  useReactivateMember,
  useRemoveMember,
  useResendInvite,
} from "../../hooks/useFamily";
import { apiErrorMessage } from "../../lib/api";
import { formatDate } from "../../lib/format";
import type { FamilyMember } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { TextField } from "../ui/Field";
import { Skeleton } from "../ui/states";

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

export function FamilyCard() {
  const { data: family, isPending } = useFamily();
  const inviteMember = useInviteMember();
  const resendInvite = useResendInvite();
  const reactivateMember = useReactivateMember();
  const removeMember = useRemoveMember();

  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteDenominazione, setInviteDenominazione] = useState("");
  const [inviteError, setInviteError] = useState<string | null>(null);
  const [inviteNotice, setInviteNotice] = useState<string | null>(null);
  const [memberToRemove, setMemberToRemove] = useState<FamilyMember | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resentId, setResentId] = useState<string | null>(null);

  if (isPending) {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="mt-4 h-24 w-full" />
      </Card>
    );
  }
  if (!family) return null;

  const slotsFree = family.used < family.limit;
  const visibleMembers = family.members;

  const handleInvite = async (e: FormEvent) => {
    e.preventDefault();
    setInviteError(null);
    setInviteNotice(null);
    const email = inviteEmail.trim().toLowerCase();
    if (!/^\S+@\S+\.\S+$/.test(email)) {
      setInviteError("Inserisci un indirizzo email valido.");
      return;
    }
    if (!inviteDenominazione.trim()) {
      setInviteError("La denominazione è obbligatoria (es. «Sede di Bari», «Ufficio gare»).");
      return;
    }
    try {
      const result = await inviteMember.mutateAsync({
        email,
        denominazione: inviteDenominazione.trim(),
      });
      setInviteOpen(false);
      setInviteEmail("");
      setInviteDenominazione("");
      if (!result.email_sent) {
        setInviteNotice(
          "Invito creato, ma l'email di notifica non è partita: l'utente lo troverà comunque al prossimo accesso.",
        );
      }
    } catch (err) {
      setInviteError(apiErrorMessage(err));
    }
  };

  const handleResend = async (member: FamilyMember) => {
    setActionError(null);
    setResentId(null);
    try {
      await resendInvite.mutateAsync(member.id);
      setResentId(member.id);
      setTimeout(() => setResentId(null), 3000);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleReactivate = async (member: FamilyMember) => {
    setActionError(null);
    try {
      await reactivateMember.mutateAsync(member.id);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  const handleRemove = async () => {
    if (!memberToRemove) return;
    setActionError(null);
    try {
      await removeMember.mutateAsync(memberToRemove.id);
      setMemberToRemove(null);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  return (
    <Card className="p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
            <Users className="size-4 text-brand-500" aria-hidden />
            Gestione account
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            <span className="tabular font-medium text-slate-700">
              {family.used} di {family.limit}
            </span>{" "}
            account usati (incluso il tuo)
          </p>
        </div>
        <Button
          onClick={() => {
            setInviteError(null);
            setInviteOpen(true);
          }}
          disabled={!slotsFree}
          title={!slotsFree ? "Hai raggiunto il limite di account del tuo piano" : undefined}
        >
          <UserPlus className="size-4" aria-hidden />
          Invita account
        </Button>
      </div>

      {inviteNotice && (
        <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800" role="status">
          {inviteNotice}
        </p>
      )}
      {actionError && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {actionError}
        </p>
      )}

      {visibleMembers.length === 0 ? (
        <div className="mt-5 rounded-xl border border-dashed border-slate-300 px-5 py-8 text-center">
          <p className="text-sm text-slate-500">
            Nessun account collegato. Invita colleghi o sedi con «Invita account»: condivideranno
            il tuo abbonamento e i dati aziendali.
          </p>
        </div>
      ) : (
        <ul className="mt-5 divide-y divide-slate-100">
          {visibleMembers.map((member) => (
            <li key={member.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">
                  {member.denominazione}
                </p>
                <p className="truncate text-xs text-slate-500">{member.email}</p>
                <p className="mt-0.5 text-xs text-slate-400">
                  {member.status === "active" && member.joined_at
                    ? `Attivo dal ${formatDate(member.joined_at)}`
                    : member.status === "demoted" && member.demoted_at
                      ? `Retrocesso il ${formatDate(member.demoted_at)}`
                      : `Invitato il ${formatDate(member.invited_at)}`}
                </p>
              </div>
              <MemberStatusBadge status={member.status} />
              <div className="flex gap-1.5">
                {member.status === "pending" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleResend(member)}
                    loading={resendInvite.isPending}
                  >
                    <Mail className="size-3.5" aria-hidden />
                    {resentId === member.id ? "Inviato!" : "Reinvia"}
                  </Button>
                )}
                {member.status === "demoted" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleReactivate(member)}
                    disabled={!slotsFree}
                    loading={reactivateMember.isPending}
                    title={!slotsFree ? "Non ci sono posti liberi nel tuo piano" : undefined}
                  >
                    <RotateCcw className="size-3.5" aria-hidden />
                    Riattiva
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-red-600 hover:bg-red-50 hover:text-red-700"
                  onClick={() => {
                    setActionError(null);
                    setMemberToRemove(member);
                  }}
                >
                  Rimuovi
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Dialog invito */}
      <Dialog
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        title="Invita un nuovo account"
        footer={
          <>
            <Button variant="ghost" onClick={() => setInviteOpen(false)}>
              Annulla
            </Button>
            <Button onClick={handleInvite} loading={inviteMember.isPending}>
              Invia invito
            </Button>
          </>
        }
      >
        <form onSubmit={handleInvite} className="space-y-4">
          <TextField
            label="Email"
            type="email"
            required
            placeholder="collega@azienda.it"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
          />
          <TextField
            label="Denominazione"
            required
            placeholder="es. Sede di Bari, Ufficio gare…"
            helper="Come apparirà nell'elenco degli account"
            value={inviteDenominazione}
            onChange={(e) => setInviteDenominazione(e.target.value)}
          />
          {inviteError && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
              {inviteError}
            </p>
          )}
          <p className="text-xs text-slate-400">
            L'account invitato condividerà il tuo abbonamento e i dati aziendali. Se l'email è già
            registrata su BandoFit, l'utente riceverà l'invito al prossimo accesso.
          </p>
        </form>
      </Dialog>

      {/* Dialog rimozione */}
      <Dialog
        open={!!memberToRemove}
        onClose={() => setMemberToRemove(null)}
        title="Rimuovere questo account?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setMemberToRemove(null)}>
              Annulla
            </Button>
            <Button variant="danger" onClick={handleRemove} loading={removeMember.isPending}>
              Rimuovi
            </Button>
          </>
        }
      >
        {memberToRemove && (
          <>
            <p>
              Stai per rimuovere{" "}
              <strong className="text-slate-900">{memberToRemove.denominazione}</strong> (
              {memberToRemove.email}) dalla famiglia.
            </p>
            <p className="mt-2">
              {memberToRemove.status === "pending"
                ? "L'invito verrà annullato."
                : "L'account diventerà indipendente con piano Gratuito e perderà l'accesso ai dati aziendali condivisi. Potrai invitarlo di nuovo in futuro."}
            </p>
            {actionError && (
              <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
                {actionError}
              </p>
            )}
          </>
        )}
      </Dialog>
    </Card>
  );
}
