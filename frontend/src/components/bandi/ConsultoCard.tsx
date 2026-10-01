import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAddons } from "../../hooks/useAddons";
import { useAiChecksForBando } from "../../hooks/useAiCheck";
import { useConsulenze, useCreateConsulenza } from "../../hooks/useConsulenze";
import { useMyAddons } from "../../hooks/useMyAddons";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CONSULTO_ADDON_SLUG } from "../../lib/consulenza";
import { CONSULENZE_COPY } from "../../lib/copy";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { InlineError } from "../ui/InlineError";
import { TextLink } from "../ui/TextLink";

/** Consulenza nel pannello «Fa per te?»: compare dopo OGNI AI-check completato
 *  (decisione #3), solo per il titolare. Se l'add-on è consumabile a pagamento
 *  serve un'unità in inventario: senza credito si va al checkout — il consumo
 *  vero lo fa il backend alla creazione. Rende `null` senza report o add-on. */
export function ConsultoCard({ slug }: { slug: string }) {
  const { data } = useAiChecksForBando(slug);
  const { data: addons } = useAddons();
  const inventario = useMyAddons();
  const { data: consulenze } = useConsulenze();
  const createConsulenza = useCreateConsulenza();
  const navigate = useNavigate();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // Fallback al gating FE: il backend ha risposto payment_required (il saldo
  // era cambiato sotto i piedi) — si passa allo stato «acquista».
  const [creditoEsaurito, setCreditoEsaurito] = useState(false);

  const latest = data?.items[0];
  const editable = data?.editable ?? false;
  const addon = addons?.find((a) => a.slug === CONSULTO_ADDON_SLUG && a.is_active);

  // Vive solo accanto a un AI-check completato, con l'add-on a catalogo.
  if (!addon || latest?.status !== "ready") return null;

  // La consulenza chiesta dalla call di partenariato (WP9) coesiste con quella
  // dall'AI-check sullo stesso bando: qui conta solo la seconda.
  const esistente = consulenze?.find(
    (c) => c.bando_id === latest.bando_id && c.stato !== "annullata" && !c.partner_call_id,
  );

  // Gating: consumabile a pagamento senza unità in inventario = bloccato.
  // Gratis (o quantità > 0) = flusso attuale. Finché l'inventario carica il
  // bottone resta disabilitato: niente stato «bloccato» a sfarfallio.
  const consumabileAPagamento =
    addon.tipo_fruizione === "consumabile" &&
    addon.tipo_prezzo === "importo" &&
    Number(addon.prezzo) > 0;
  const quantitaPosseduta =
    inventario.data?.find((m) => m.slug === CONSULTO_ADDON_SLUG)?.quantita ?? 0;
  const attesaInventario = consumabileAPagamento && inventario.isPending;
  const bloccato =
    creditoEsaurito || (consumabileAPagamento && !inventario.isPending && quantitaPosseduta === 0);

  const handleActivate = async () => {
    if (createConsulenza.isPending) return;
    setActionError(null);
    try {
      const consulenza = await createConsulenza.mutateAsync(latest.id);
      setConfirmOpen(false);
      navigate(`/app/consulenze/${consulenza.id}`);
    } catch (err) {
      // payment_required: serve un'unità dell'add-on — si chiude la finestra e
      // compare l'acquisto (stessa strada del gating a priori).
      if (apiErrorCode(err) === "payment_required") {
        setCreditoEsaurito(true);
        setConfirmOpen(false);
        return;
      }
      setActionError(apiErrorMessage(err));
    }
  };

  return (
    // Il filetto è qui e non nel pannello: il blocco spesso non c'è.
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      <h4 className="font-sans text-title-group text-ink">Consulenza</h4>
      {esistente ? (
        <>
          <p className="text-body text-ink-2">
            {esistente.stato === "nuova"
              ? "Hai già una richiesta di consulenza aperta per questo bando."
              : "La consulenza per questo bando è già assegnata a un progettista."}
          </p>
          <p className="text-small">
            <TextLink to={`/app/consulenze/${esistente.id}`}>Vedi la consulenza</TextLink>
          </p>
        </>
      ) : (
        <>
          <p className="text-body text-ink-2">
            {addon.descrizione ??
              "Trenta minuti di confronto con un progettista esperto in finanza agevolata su questo bando."}
          </p>
          {editable ? (
            <div className="flex flex-col gap-2">
              <div>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={bloccato || attesaInventario}
                  onClick={() => {
                    setActionError(null);
                    setConfirmOpen(true);
                  }}
                >
                  Richiedi una consulenza
                </Button>
              </div>
              {bloccato && (
                <>
                  <p className="text-small text-ink-3">Ti serve una consulenza per procedere.</p>
                  <div>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => navigate(`/app/checkout?addon=${CONSULTO_ADDON_SLUG}`)}
                    >
                      Acquista una consulenza
                    </Button>
                  </div>
                </>
              )}
            </div>
          ) : (
            <p className="text-small text-ink-3">La consulenza la richiede il titolare dell'azienda.</p>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        titolo="Richiedere una consulenza?"
        conferma="Invia la richiesta"
        inCorso={createConsulenza.isPending}
        onConferma={handleActivate}
        onAnnulla={() => setConfirmOpen(false)}
      >
        <p>
          La tua richiesta, con l'esito dell'AI-check, sarà visibile ai progettisti della
          piattaforma: chi può aiutarti ti invierà una proposta e sceglierai tu a chi
          affidare la consulenza.
        </p>
        <p className="mt-2 text-small text-ink-3">{CONSULENZE_COPY.consenso}</p>
        {actionError && <InlineError className="mt-3">{actionError}</InlineError>}
      </ConfirmDialog>
    </div>
  );
}
