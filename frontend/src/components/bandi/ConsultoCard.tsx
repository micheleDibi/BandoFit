import { MessagesSquare } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAddons } from "../../hooks/useAddons";
import { useAiChecksForBando } from "../../hooks/useAiCheck";
import { useConsulenze, useCreateConsulenza } from "../../hooks/useConsulenze";
import { apiErrorMessage } from "../../lib/api";
import { CONSULTO_ADDON_SLUG } from "../../lib/consulenza";
import { CONSULENZE_COPY } from "../../lib/copy";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";

/** CTA dell'addon «Consulto esperto» nella sidebar del bando: compare dopo
 *  OGNI AI-check completato (decisione #3), solo per il titolare. Il
 *  pagamento è fuori scope: l'attivazione crea direttamente la richiesta
 *  (l'innesto del checkout è nel backend, create_request). */
export function ConsultoCard({ slug }: { slug: string }) {
  const { data } = useAiChecksForBando(slug);
  const { data: addons } = useAddons();
  const { data: consulenze } = useConsulenze();
  const createConsulenza = useCreateConsulenza();
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const latest = data?.items[0];
  const editable = data?.editable ?? false;
  const addon = addons?.find((a) => a.slug === CONSULTO_ADDON_SLUG && a.is_active);

  // Il CTA vive solo accanto a un AI-check completato, con l'addon a catalogo.
  if (!addon || latest?.status !== "ready") return null;

  const esistente = consulenze?.find(
    (c) => c.bando_id === latest.bando_id && c.stato !== "annullata",
  );

  const handleActivate = async () => {
    if (createConsulenza.isPending) return;
    setActionError(null);
    try {
      const consulenza = await createConsulenza.mutateAsync(latest.id);
      setConfirmOpen(false);
      navigate(`/app/consulenze/${consulenza.id}`);
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  return (
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-slate-900">
        <MessagesSquare className="size-4 text-brand-500" aria-hidden />
        {addon.nome}
      </h2>
      {esistente ? (
        <div className="mt-3">
          <p className="text-sm text-slate-600">
            {esistente.stato === "nuova"
              ? "Hai già una richiesta di consulto aperta per questo bando."
              : "La consulenza per questo bando è già assegnata a un progettista."}
          </p>
          <Link
            to={`/app/consulenze/${esistente.id}`}
            className="mt-2 inline-block text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            Vedi la consulenza →
          </Link>
        </div>
      ) : (
        <div className="mt-3">
          <p className="text-sm text-slate-600">
            {addon.descrizione ??
              "Trenta minuti di confronto con un progettista esperto in finanza agevolata su questo bando."}
          </p>
          {editable ? (
            <Button
              variant="secondary"
              className="mt-3 w-full"
              onClick={() => {
                setActionError(null);
                setConfirmOpen(true);
              }}
            >
              Richiedi il consulto
            </Button>
          ) : (
            <p className="mt-3 text-xs text-slate-500">
              Il consulto lo richiede il titolare dell'azienda.
            </p>
          )}
        </div>
      )}

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        dismissible={!createConsulenza.isPending}
        title="Richiedi il consulto esperto"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={createConsulenza.isPending}
            >
              Annulla
            </Button>
            <Button loading={createConsulenza.isPending} onClick={handleActivate}>
              Invia la richiesta
            </Button>
          </>
        }
      >
        <p>
          La tua richiesta, con l'esito dell'AI-check, sarà visibile ai progettisti della
          piattaforma: chi può aiutarti ti invierà una proposta e sceglierai tu a chi
          affidare la consulenza.
        </p>
        <p className="mt-2 text-xs text-slate-500">{CONSULENZE_COPY.consenso}</p>
        {actionError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </Card>
  );
}
