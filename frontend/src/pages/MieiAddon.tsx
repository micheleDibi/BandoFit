import { AlertTriangle, Infinity as InfinityIcon, Plus, Puzzle } from "lucide-react";
import { Link } from "react-router-dom";
import { InventarioAddon } from "../components/shared/InventarioAddon";
import { Badge } from "../components/ui/Badge";
import { LinkButton } from "../components/ui/Button";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAddons } from "../hooks/useAddons";
import { useEntitlements } from "../hooks/useEntitlements";
import { useMyAddons } from "../hooks/useMyAddons";
import { apiErrorMessage } from "../lib/api";
import type { Addon, Entitlements, MyAddon } from "../types";

/** Quante unità EXTRA della risorsa sono davvero in uso: la parte di consumo
 *  che eccede la base del piano, mai oltre le unità possedute. Specchio
 *  dichiarato della formula server (display-only: l'arbitro è il backend). */
function unitaInUso(posseduto: MyAddon, entitlements: Entitlements | undefined): number {
  if (!posseduto.risorsa || !entitlements) return 0;
  const r = entitlements[posseduto.risorsa];
  return Math.min(Math.max(r.usato - r.base, 0), posseduto.quantita);
}

/** Un allocativo è «dormiente» quando le sue unità non contano: base del
 *  piano non abilitante, oppure inventario di un collegato attivo (l'extra
 *  si somma solo sull'inventario del titolare). */
function dormiente(posseduto: MyAddon, entitlements: Entitlements | undefined): boolean {
  if (!posseduto.risorsa) return false;
  if (!entitlements) return false;
  return !entitlements.editable || entitlements[posseduto.risorsa].base <= 1;
}

function StatoBadge({ posseduto, dorme }: { posseduto: MyAddon; dorme: boolean }) {
  if (posseduto.quantita === 0) return <Badge tone="slate">Esaurito</Badge>;
  if (dorme) return <Badge tone="amber">Dormiente</Badge>;
  return <Badge tone="emerald">Attivo</Badge>;
}

function CardAddon({
  posseduto,
  catalogo,
  entitlements,
}: {
  posseduto: MyAddon;
  catalogo: Addon | undefined;
  entitlements: Entitlements | undefined;
}) {
  const dorme = dormiente(posseduto, entitlements);
  const consumabileNormale = !posseduto.risorsa;
  // «Aumenta quantità» solo se il catalogo dice che l'utente può comprare
  // (attivo, a pagamento, piano idoneo): il gate vero resta nel checkout.
  const acquistabile =
    !!catalogo && catalogo.tipo_prezzo === "importo" && catalogo.acquistabile;

  const acquistate = Math.max(posseduto.acquistate, posseduto.consumate);
  const pctConsumo =
    acquistate > 0 ? Math.min(100, Math.round((posseduto.consumate / acquistate) * 100)) : 0;
  const inUso = unitaInUso(posseduto, entitlements);

  return (
    <div className="flex h-full flex-col rounded-xl border border-slate-200 bg-white p-5 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
          <Puzzle className="size-4 text-brand-500" aria-hidden />
          {posseduto.nome}
        </h2>
        <StatoBadge posseduto={posseduto} dorme={dorme} />
      </div>
      {posseduto.descrizione && (
        <p className="mt-2 text-sm leading-relaxed text-slate-500">{posseduto.descrizione}</p>
      )}

      <div className="mt-4 flex-1">
        {consumabileNormale ? (
          <>
            <p className="text-sm text-slate-600">
              Ne hai{" "}
              <strong className="tabular-nums text-slate-900">{posseduto.quantita}</strong>{" "}
              {posseduto.quantita === 1 ? "disponibile" : "disponibili"}
              {acquistate > 0 && (
                <span className="text-slate-400">
                  {" "}
                  — usate {posseduto.consumate} su {acquistate}
                </span>
              )}
            </p>
            {acquistate > 0 && (
              <div
                role="img"
                aria-label={`Usate ${posseduto.consumate} unità su ${acquistate}`}
                className="mt-2 h-1.5 rounded-full bg-slate-100"
              >
                <div
                  className="h-1.5 rounded-full bg-brand-500"
                  style={{ width: `${pctConsumo}%` }}
                />
              </div>
            )}
          </>
        ) : (
          <>
            <p className="text-sm text-slate-600">
              Possiedi{" "}
              <strong className="tabular-nums text-slate-900">{posseduto.quantita}</strong>{" "}
              {posseduto.quantita === 1 ? "unità" : "unità"}
              {!dorme && posseduto.quantita > 0 && (
                <span className="text-slate-400">
                  {" "}
                  — in uso {inUso} di {posseduto.quantita}
                </span>
              )}
            </p>
            {dorme && (
              <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Le unità restano tue ma ora non contano: il tuo piano attuale non include questa
                funzione. Torneranno attive con un piano che la prevede.
              </p>
            )}
          </>
        )}
        <p className="mt-2 inline-flex items-center gap-1 text-xs text-slate-400">
          <InfinityIcon className="size-3.5" aria-hidden />
          Una tantum — senza scadenza
        </p>
      </div>

      {acquistabile && (
        <LinkButton
          to={`/app/checkout?addon=${posseduto.slug}`}
          variant="secondary"
          className="mt-4 w-full"
        >
          <Plus className="size-4" aria-hidden />
          Aumenta quantità
        </LinkButton>
      )}
      <InventarioAddon posseduto={posseduto} mostraBadge={false} />
    </div>
  );
}

export default function MieiAddon() {
  const { data: mieiAddon, isPending, isError, error, refetch } = useMyAddons();
  const { data: addons } = useAddons();
  const entitlements = useEntitlements();

  // Difensivo: con la riduzione immediata (B3) l'over-quota è transitorio,
  // ma se compare va spiegato, non nascosto.
  const overQuota =
    entitlements.data &&
    (entitlements.data.seats.usato > entitlements.data.seats.effettivo ||
      entitlements.data.companies.usato > entitlements.data.companies.effettivo);

  const renderContent = () => {
    if (isPending) {
      return (
        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <Skeleton className="h-56 w-full" />
          <Skeleton className="h-56 w-full" />
        </div>
      );
    }
    if (isError) {
      return (
        <div className="mt-6">
          <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
        </div>
      );
    }
    if ((mieiAddon?.length ?? 0) === 0) {
      return (
        <div className="mt-6">
          <EmptyState
            title="Non possiedi ancora nessun add-on"
            description="Gli add-on estendono il tuo piano: più account collegati, più aziende, consulenze con un progettista."
            action={
              <LinkButton to="/app/abbonamento" variant="secondary">
                Vai al catalogo
              </LinkButton>
            }
          />
        </div>
      );
    }
    return (
      <div className="mt-6 grid gap-5 sm:grid-cols-2">
        {mieiAddon?.map((posseduto) => (
          <CardAddon
            key={posseduto.addon_id}
            posseduto={posseduto}
            catalogo={addons?.find((a) => a.id === posseduto.addon_id)}
            entitlements={entitlements.data}
          />
        ))}
      </div>
    );
  };

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="font-display text-2xl font-bold text-slate-900">I miei addon</h1>
      <p className="mt-1 text-sm text-slate-500">
        Gli add-on che possiedi, con le unità disponibili e lo storico dei movimenti. Il
        catalogo per acquistarne altri è nella pagina{" "}
        <Link to="/app/abbonamento" className="font-medium text-brand-600 hover:underline">
          Abbonamento
        </Link>
        .
      </p>

      {overQuota && (
        <p
          role="status"
          className="mt-4 inline-flex items-start gap-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          Stai usando più di quanto il tuo assetto attuale preveda: le eccedenze vengono
          adeguate automaticamente (nessun dato viene eliminato).
        </p>
      )}

      {renderContent()}
    </div>
  );
}
