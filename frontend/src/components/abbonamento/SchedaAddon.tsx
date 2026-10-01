import { Infinity as InfinityIcon } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAddons } from "../../hooks/useAddons";
import { useEntitlements } from "../../hooks/useEntitlements";
import { useMyAddons } from "../../hooks/useMyAddons";
import { purchaseAddon } from "../../lib/addons";
import { requestConsultation } from "../../lib/consulenza";
import { prezzoDisplay } from "../../lib/prezzo";
import type { Addon, Entitlements, MyAddon } from "../../types";
import { AddonCard } from "../shared/AddonCard";
import { InventarioAddon } from "../shared/InventarioAddon";
import { Alert } from "../ui/Alert";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { ProgressBar } from "../ui/ProgressBar";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Status } from "../ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { aPagamento } from "./SchedaPiano";

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

function StatoPosseduto({ posseduto, dorme }: { posseduto: MyAddon; dorme: boolean }) {
  if (posseduto.quantita === 0) return <Status tono="chiuso">Esaurito</Status>;
  if (dorme) return <Status tono="in-apertura">Dormiente</Status>;
  return <Status tono="aperto">Attivo</Status>;
}

/** Un add-on posseduto: unità disponibili, consumo, movimenti. */
function AddonPosseduto({
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

  // Un consumo rimborsato (unità restituita) non conta come usato: i rimborsi
  // arrivano a parte, non più tra gli accrediti.
  const rimborsate = posseduto.rimborsate ?? 0;
  const usate = Math.max(posseduto.consumate - rimborsate, 0);
  const acquistate = Math.max(posseduto.acquistate, usate);
  const inUso = unitaInUso(posseduto, entitlements);

  return (
    <Card className="flex h-full flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-sans text-title-group text-ink">{posseduto.nome}</h3>
        <StatoPosseduto posseduto={posseduto} dorme={dorme} />
      </div>
      {posseduto.descrizione && <p className="text-body text-ink-2">{posseduto.descrizione}</p>}

      <div className="flex flex-1 flex-col gap-2">
        {consumabileNormale ? (
          <>
            <p className="text-body text-ink-2">
              Ne hai <strong className="text-ink tabular-nums">{posseduto.quantita}</strong>{" "}
              {posseduto.quantita === 1 ? "disponibile" : "disponibili"}
              {acquistate > 0 && (
                <span className="text-ink-3">
                  {" "}
                  (usate {usate} su {acquistate}
                  {rimborsate > 0 &&
                    `, ${rimborsate} ${rimborsate === 1 ? "restituita" : "restituite"}`}
                  )
                </span>
              )}
            </p>
            {acquistate > 0 && (
              <ProgressBar
                valore={usate}
                massimo={acquistate}
                label={`Usate ${usate} unità su ${acquistate}`}
              />
            )}
          </>
        ) : (
          <>
            <p className="text-body text-ink-2">
              Possiedi <strong className="text-ink tabular-nums">{posseduto.quantita}</strong> unità
              {!dorme && posseduto.quantita > 0 && (
                <span className="text-ink-3">
                  {" "}
                  (in uso {inUso} di {posseduto.quantita})
                </span>
              )}
            </p>
            {dorme && (
              <Alert tono="attenzione">
                Le unità restano tue ma ora non contano: il tuo piano attuale non include questa
                funzione. Torneranno attive con un piano che la prevede.
              </Alert>
            )}
          </>
        )}
        <p className="inline-flex items-center gap-1.5 text-small text-ink-3">
          <InfinityIcon className="size-4" aria-hidden />
          Una tantum, senza scadenza
        </p>
      </div>

      {acquistabile && (
        <div>
          <LinkButton to={`/app/checkout?addon=${posseduto.slug}`} variant="secondary" size="sm">
            Aumenta la quantità
          </LinkButton>
        </div>
      )}
      <InventarioAddon posseduto={posseduto} mostraBadge={false} />
    </Card>
  );
}

/** Scheda «Add-on» dell'Abbonamento: il catalogo gestito dagli admin e, sotto,
 *  «I tuoi add-on» con le unità disponibili e i movimenti. */
export function SchedaAddon() {
  const navigate = useNavigate();
  const addons = useAddons();
  const miei = useMyAddons();
  const entitlements = useEntitlements();
  // Add-on per cui è stato chiesto l'acquisto (flusso non ancora disponibile).
  const [addonInArrivo, setAddonInArrivo] = useState<Addon | null>(null);
  // Add-on «su richiesta» per cui è stata chiesta una consulenza.
  const [consulenzaInArrivo, setConsulenzaInArrivo] = useState<{ nome: string } | null>(null);
  // Slug con acquisto "in volo": un Set, così quando purchaseAddon diventerà
  // una vera chiamata di rete i click concorrenti non si pesteranno i piedi.
  const [addonLoading, setAddonLoading] = useState<Set<string>>(new Set());

  // Add-on a pagamento: si comprano dal checkout (modulo pagamenti). Il caso
  // gratis resta sul punto di estensione purchaseAddon (lib/addons.ts): finché
  // lo stub risponde available=false si apre la finestra «In arrivo».
  const handleAcquista = async (addon: Addon, quantita = 1) => {
    if (aPagamento(addon)) {
      navigate(`/app/checkout?addon=${addon.slug}${quantita > 1 ? `&qty=${quantita}` : ""}`);
      return;
    }
    if (addonLoading.has(addon.slug)) return;
    setAddonLoading((prev) => new Set(prev).add(addon.slug));
    try {
      const esito = await purchaseAddon(addon.slug);
      if (!esito.available) setAddonInArrivo(addon);
    } finally {
      setAddonLoading((prev) => {
        const next = new Set(prev);
        next.delete(addon.slug);
        return next;
      });
    }
  };

  const handleRichiedi = async (addon: Addon) => {
    const esito = await requestConsultation({ kind: "addon", slug: addon.slug });
    if (!esito.available) setConsulenzaInArrivo({ nome: addon.nome });
  };

  // Difensivo: con la riduzione immediata (B3) l'over-quota è transitorio,
  // ma se compare va spiegato, non nascosto.
  const overQuota =
    entitlements.data &&
    (entitlements.data.seats.usato > entitlements.data.seats.effettivo ||
      entitlements.data.companies.usato > entitlements.data.companies.effettivo);

  const catalogo = addons.data ?? [];

  return (
    <>
      {overQuota && (
        <Alert tono="attenzione">
          Stai usando più di quanto il tuo assetto attuale preveda: le eccedenze vengono adeguate
          automaticamente (nessun dato viene eliminato).
        </Alert>
      )}

      <Section aria-label="Catalogo degli add-on">
        <SectionHeader titolo="Catalogo" />
        <p className="text-body text-ink-2">Estensioni una tantum per potenziare il tuo piano.</p>
        {addons.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2" aria-hidden>
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : addons.isError ? (
          <ErrorState
            title="Non siamo riusciti a caricare gli add-on disponibili."
            onRetry={() => addons.refetch()}
          />
        ) : catalogo.length === 0 ? (
          <EmptyState title="Nessun add-on disponibile al momento." />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {catalogo.map((addon) => {
              const posseduto = miei.data?.find((m) => m.addon_id === addon.id);
              return (
                <AddonCard
                  key={addon.id}
                  addon={addon}
                  onAcquista={handleAcquista}
                  onRichiedi={handleRichiedi}
                  loading={addonLoading.has(addon.slug)}
                  inventario={
                    posseduto && posseduto.quantita > 0 ? (
                      <InventarioAddon posseduto={posseduto} />
                    ) : undefined
                  }
                />
              );
            })}
          </div>
        )}
      </Section>

      <Section aria-label="I tuoi add-on">
        <SectionHeader titolo="I tuoi add-on" />
        {miei.isPending ? (
          <div className="grid gap-4 sm:grid-cols-2" aria-hidden>
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : miei.isError ? (
          <ErrorState
            title="Non siamo riusciti a caricare i tuoi add-on."
            onRetry={() => miei.refetch()}
          />
        ) : (miei.data?.length ?? 0) === 0 ? (
          <EmptyState
            title="Non possiedi ancora nessun add-on."
            description="Gli add-on estendono il tuo piano: più account collegati, più aziende, consulenze con un progettista."
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {miei.data?.map((posseduto) => (
              <AddonPosseduto
                key={posseduto.addon_id}
                posseduto={posseduto}
                catalogo={catalogo.find((a) => a.id === posseduto.addon_id)}
                entitlements={entitlements.data}
              />
            ))}
          </div>
        )}
      </Section>

      {/* Acquisto add-on: flusso non ancora disponibile */}
      <Dialog
        open={!!addonInArrivo}
        onClose={() => setAddonInArrivo(null)}
        title="Acquisto in arrivo"
        footer={
          <Button type="button" variant="secondary" onClick={() => setAddonInArrivo(null)}>
            Ho capito
          </Button>
        }
      >
        {addonInArrivo && (
          <>
            <p>
              L'acquisto degli add-on sarà disponibile a breve. Hai scelto{" "}
              <strong className="text-ink">{addonInArrivo.nome}</strong> (
              {
                prezzoDisplay(
                  addonInArrivo.tipo_prezzo,
                  addonInArrivo.etichetta_prezzo,
                  addonInArrivo.prezzo,
                ).testo
              }
              ).
            </p>
            <p className="mt-2 text-small text-ink-3">Nessun addebito è stato effettuato.</p>
          </>
        )}
      </Dialog>

      {/* Richiesta di consulenza: flusso di contatto non ancora disponibile */}
      <Dialog
        open={!!consulenzaInArrivo}
        onClose={() => setConsulenzaInArrivo(null)}
        title="Richiesta in arrivo"
        footer={
          <Button type="button" variant="secondary" onClick={() => setConsulenzaInArrivo(null)}>
            Ho capito
          </Button>
        }
      >
        {consulenzaInArrivo && (
          <p>
            <strong className="text-ink">{consulenzaInArrivo.nome}</strong> si attiva su
            richiesta: la richiesta di consulenza dall'app sarà disponibile a breve. Nel frattempo
            contattaci per maggiori informazioni.
          </p>
        )}
      </Dialog>
    </>
  );
}
