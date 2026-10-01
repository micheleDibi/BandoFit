import { useState } from "react";
import {
  useAcceptInvitation,
  useDeclineInvitation,
  useInvitations,
} from "../../hooks/useFamily";
import { useMe } from "../../hooks/useMe";
import { apiErrorMessage } from "../../lib/api";
import type { Invitation } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { InlineError } from "../ui/InlineError";

/** Banner mostrato agli utenti ESISTENTI con un invito in un'azienda in attesa. */
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
    <>
      <Alert
        tono="info"
        azione={
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={() => setConfirming(invitation)}>
              Accetta
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleDecline}
              loading={declineInvitation.isPending}
            >
              Rifiuta
            </Button>
          </div>
        }
      >
        <p>
          <strong className="font-semibold">{invitation.parent_display_name}</strong> ti ha
          invitato nella sua azienda come{" "}
          <strong className="font-semibold">{invitation.denominazione}</strong>.
        </p>
        {error && !confirming && <InlineError>{error}</InlineError>}
      </Alert>

      <ConfirmDialog
        open={!!confirming}
        titolo="Entrare nell'azienda?"
        conferma="Accetta l'invito"
        inCorso={acceptInvitation.isPending}
        onConferma={handleAccept}
        onAnnulla={() => setConfirming(null)}
      >
        <div className="flex flex-col gap-3">
          <p>
            Entrando nell'azienda di{" "}
            <strong className="font-semibold text-ink">{invitation.parent_display_name}</strong>{" "}
            erediterai il suo abbonamento e i suoi dati aziendali.
          </p>
          {currentPlanName && (
            <Alert tono="attenzione">
              Il tuo abbonamento attuale (<strong className="font-semibold">{currentPlanName}</strong>)
              verrà annullato.
            </Alert>
          )}
          {error && <Alert tono="errore">{error}</Alert>}
        </div>
      </ConfirmDialog>
    </>
  );
}
