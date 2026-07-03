import { UserPlus } from "lucide-react";
import { useState } from "react";
import {
  useAcceptInvitation,
  useDeclineInvitation,
  useInvitations,
} from "../../hooks/useFamily";
import { useMe } from "../../hooks/useMe";
import { apiErrorMessage } from "../../lib/api";
import type { Invitation } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";

/** Banner mostrato agli utenti ESISTENTI con un invito famiglia in attesa. */
export function InviteBanner() {
  const { data: me } = useMe();
  const { data: invitations } = useInvitations();
  const acceptInvitation = useAcceptInvitation();
  const declineInvitation = useDeclineInvitation();
  const [confirming, setConfirming] = useState<Invitation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const invitation = invitations?.[0];
  if (!invitation) return null;

  const currentPlanName = me?.subscription?.plan.nome;

  const handleAccept = async () => {
    setError(null);
    try {
      await acceptInvitation.mutateAsync(invitation.id);
      setConfirming(null);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  const handleDecline = async () => {
    setError(null);
    try {
      await declineInvitation.mutateAsync(invitation.id);
    } catch (err) {
      setError(apiErrorMessage(err));
    }
  };

  return (
    <div className="border-b border-brand-100 bg-brand-50">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-brand-500 text-white">
          <UserPlus className="size-4" aria-hidden />
        </span>
        <p className="min-w-0 flex-1 text-sm text-brand-900">
          <strong>{invitation.parent_display_name}</strong> ti ha invitato nella sua famiglia di
          account come <strong>{invitation.denominazione}</strong>.
        </p>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setConfirming(invitation)}>
            Accetta
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleDecline}
            loading={declineInvitation.isPending}
          >
            Rifiuta
          </Button>
        </div>
        {error && !confirming && (
          <p className="w-full text-sm text-red-700" role="alert">
            {error}
          </p>
        )}
      </div>

      <Dialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        title="Entrare nella famiglia?"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirming(null)}>
              Annulla
            </Button>
            <Button onClick={handleAccept} loading={acceptInvitation.isPending}>
              Accetta l'invito
            </Button>
          </>
        }
      >
        <p>
          Entrando nella famiglia di{" "}
          <strong className="text-slate-900">{invitation.parent_display_name}</strong> erediterai
          il suo abbonamento e i suoi dati aziendali.
        </p>
        {currentPlanName && (
          <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-amber-800">
            Il tuo abbonamento attuale (<strong>{currentPlanName}</strong>) verrà annullato.
          </p>
        )}
        {error && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-red-700" role="alert">
            {error}
          </p>
        )}
      </Dialog>
    </div>
  );
}
