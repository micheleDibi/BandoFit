import { MessageSquare } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAddons } from "../../hooks/useAddons";
import { useConsulenze, useConsultoCall } from "../../hooks/useConsulenze";
import { useMyAddons } from "../../hooks/useMyAddons";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CONSULTO_ADDON_SLUG } from "../../lib/consulenza";
import { CONSULENZE_COPY } from "../../lib/copy";
import type { CallVistaCreatore } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Panel } from "../ui/Panel";
import { TextLink } from "../ui/TextLink";

/** Stati in cui ha senso chiedere una consulenza sulla call: non annullata e
 *  non sospesa (il server ricontrolla). */
const STATI_CONSULTO = new Set(["bozza", "pubblicata", "scaduta", "chiusa_completata"]);

/** «Chiedi una consulenza» dalla call (WP9, Q19), nel pannello della colonna
 *  laterale: per l'azienda che l'ha creata, con le STESSE regole di consumo e
 *  gli stessi messaggi di `ConsultoCard` (add-on `consulto-esperto` a catalogo;
 *  se è consumabile a pagamento serve un'unità, altrimenti il pulsante porta al
 *  checkout; il consumo vero lo fa il server). L'AI-check è facoltativo.
 *  Coesiste con una consulenza chiesta dall'AI-check sullo stesso bando: qui
 *  conta solo quella della call. */
export function ConsultoCallCard({ call }: { call: CallVistaCreatore }) {
  const { data: addons } = useAddons();
  const inventario = useMyAddons();
  const { data: consulenze } = useConsulenze();
  const consulto = useConsultoCall(call.id);
  const navigate = useNavigate();
  const [confermaAperta, setConfermaAperta] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  // Il server ha risposto payment_required (il saldo era cambiato sotto i
  // piedi): il pannello passa allo stato «acquista».
  const [creditoEsaurito, setCreditoEsaurito] = useState(false);

  const addon = addons?.find((a) => a.slug === CONSULTO_ADDON_SLUG && a.is_active);
  if (!addon || !STATI_CONSULTO.has(call.stato)) return null;

  const esistente = consulenze?.find(
    (c) => c.partner_call_id === call.id && c.stato !== "annullata",
  );

  const consumabileAPagamento =
    addon.tipo_fruizione === "consumabile" &&
    addon.tipo_prezzo === "importo" &&
    Number(addon.prezzo) > 0;
  const quantita = inventario.data?.find((m) => m.slug === CONSULTO_ADDON_SLUG)?.quantita ?? 0;
  const attesaInventario = consumabileAPagamento && inventario.isPending;
  const bloccato =
    creditoEsaurito || (consumabileAPagamento && !inventario.isPending && quantita === 0);

  const chiedi = async () => {
    if (consulto.isPending) return;
    setErrore(null);
    try {
      const creata = await consulto.mutateAsync();
      setConfermaAperta(false);
      navigate(`/app/consulenze/${creata.id}`);
    } catch (err) {
      if (apiErrorCode(err) === "payment_required") {
        setCreditoEsaurito(true);
        setConfermaAperta(false);
        return;
      }
      // Bando ritirato (410), consulenza già aperta, call non più tua: il
      // messaggio del server dice cosa fare.
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Panel titolo="Consulenza con un progettista" icon={MessageSquare} area="consulenze">
      {esistente ? (
        <>
          <p className="text-body text-ink-2">
            {esistente.stato === "nuova"
              ? "Hai già chiesto una consulenza su questa call: i progettisti ti manderanno le loro proposte."
              : "La consulenza su questa call è già assegnata a un progettista."}
          </p>
          <div>
            <TextLink to={`/app/consulenze/${esistente.id}`}>Vedi la consulenza</TextLink>
          </div>
        </>
      ) : (
        <>
          <p className="text-body text-ink-2">
            Un progettista esperto in finanza agevolata ti aiuta a impostare la call e il
            consorzio: regole del bando, requisiti, posizioni e quote. Non serve un AI-check.
          </p>
          {call.editable ? (
            <>
              <div>
                <Button
                  variant="secondary"
                  disabled={bloccato || attesaInventario}
                  onClick={() => {
                    setErrore(null);
                    setConfermaAperta(true);
                  }}
                >
                  Chiedi una consulenza
                </Button>
              </div>
              {bloccato && (
                <>
                  <p className="text-small text-ink-3">Ti serve una consulenza per procedere.</p>
                  <div>
                    <Button
                      variant="secondary"
                      onClick={() => navigate(`/app/checkout?addon=${CONSULTO_ADDON_SLUG}`)}
                    >
                      Acquista una consulenza
                    </Button>
                  </div>
                </>
              )}
              {errore && !confermaAperta && <Alert tono="errore">{errore}</Alert>}
            </>
          ) : (
            <p className="text-small text-ink-3">La consulenza la richiede il titolare dell'azienda.</p>
          )}
        </>
      )}

      <ConfirmDialog
        open={confermaAperta}
        titolo="Chiedi una consulenza sulla call"
        conferma="Invia la richiesta"
        inCorso={consulto.isPending}
        onConferma={() => void chiedi()}
        onAnnulla={() => setConfermaAperta(false)}
      >
        <div className="flex flex-col gap-3">
          <p>
            La richiesta arriva ai progettisti della piattaforma: chi può aiutarti ti invierà una
            proposta e sceglierai tu a chi affidare la consulenza.
            {consumabileAPagamento ? " Userai una delle tue consulenze." : ""}
          </p>
          <p className="text-small text-ink-3">{CONSULENZE_COPY.consensoCall}</p>
          {errore && <Alert tono="errore">{errore}</Alert>}
        </div>
      </ConfirmDialog>
    </Panel>
  );
}
