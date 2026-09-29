import { MessagesSquare, ShoppingCart } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAddons } from "../../hooks/useAddons";
import { useConsulenze, useConsultoCall } from "../../hooks/useConsulenze";
import { useMyAddons } from "../../hooks/useMyAddons";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CONSULTO_ADDON_SLUG } from "../../lib/consulenza";
import { CONSULENZE_COPY } from "../../lib/copy";
import type { CallVistaCreatore } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";

/** Stati in cui ha senso chiedere un consulto sulla call: non annullata e non
 *  sospesa (il server ricontrolla). */
const STATI_CONSULTO = new Set(["bozza", "pubblicata", "scaduta", "chiusa_completata"]);

/** «Chiedi un consulto» dalla call (WP9, Q19): per l'azienda che l'ha creata,
 *  con le STESSE regole di consumo e gli stessi messaggi di `ConsultoCard`
 *  (addon `consulto-esperto` a catalogo; se è consumabile a pagamento serve
 *  un'unità, altrimenti la CTA porta al checkout; il consumo vero lo fa il
 *  server). L'AI-check è facoltativo. Coesiste con un consulto chiesto
 *  dall'AI-check sullo stesso bando: qui conta solo quello della call. */
export function ConsultoCallCard({ call }: { call: CallVistaCreatore }) {
  const { data: addons } = useAddons();
  const inventario = useMyAddons();
  const { data: consulenze } = useConsulenze();
  const consulto = useConsultoCall(call.id);
  const navigate = useNavigate();
  const [confermaAperta, setConfermaAperta] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  // Il server ha risposto payment_required (il saldo era cambiato sotto i
  // piedi): la card passa allo stato «acquista».
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
      // Bando ritirato (410), consulto già aperto, call non più tua: il
      // messaggio del server dice cosa fare.
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <MessagesSquare className="size-4 text-brand-500" aria-hidden />
        Consulto con un progettista
      </h2>
      {esistente ? (
        <div className="mt-3">
          <p className="text-sm text-slate-600">
            {esistente.stato === "nuova"
              ? "Hai già chiesto un consulto su questa call: i progettisti ti manderanno le loro proposte."
              : "Il consulto su questa call è già assegnato a un progettista."}
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
            Un progettista esperto in finanza agevolata ti aiuta a impostare la call e il
            consorzio: regole del bando, requisiti, posizioni e quote. Non serve un AI-check.
          </p>
          {call.editable ? (
            <>
              <Button
                variant="secondary"
                className="mt-3"
                disabled={bloccato || attesaInventario}
                onClick={() => {
                  setErrore(null);
                  setConfermaAperta(true);
                }}
              >
                Chiedi un consulto
              </Button>
              {bloccato && (
                <>
                  <p className="mt-2 text-xs text-slate-500">Ti serve una consulenza per procedere.</p>
                  <Button
                    className="mt-2"
                    onClick={() => navigate(`/app/checkout?addon=${CONSULTO_ADDON_SLUG}`)}
                  >
                    <ShoppingCart className="size-4" aria-hidden />
                    Acquista una consulenza
                  </Button>
                </>
              )}
              {errore && !confermaAperta && (
                <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                  {errore}
                </p>
              )}
            </>
          ) : (
            <p className="mt-3 text-xs text-slate-500">
              Il consulto lo richiede il titolare dell'azienda.
            </p>
          )}
        </div>
      )}

      <Dialog
        open={confermaAperta}
        onClose={() => setConfermaAperta(false)}
        dismissible={!consulto.isPending}
        title="Chiedi un consulto sulla call"
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfermaAperta(false)}
              disabled={consulto.isPending}
            >
              Annulla
            </Button>
            <Button loading={consulto.isPending} onClick={() => void chiedi()}>
              Invia la richiesta
            </Button>
          </>
        }
      >
        <p>
          La richiesta arriva ai progettisti della piattaforma: chi può aiutarti ti invierà una
          proposta e sceglierai tu a chi affidare la consulenza.
          {consumabileAPagamento ? " Userai una delle tue consulenze." : ""}
        </p>
        <p className="mt-2 text-xs text-slate-500">{CONSULENZE_COPY.consensoCall}</p>
        {errore && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {errore}
          </p>
        )}
      </Dialog>
    </Card>
  );
}
