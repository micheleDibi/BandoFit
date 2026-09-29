import { Play, RefreshCw } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import {
  runGiaEseguita,
  useAdminEstrazioni,
  useForzaEstrazione,
  useRunPartenariati,
} from "../../../hooks/useAdminPartenariati";
import { apiErrorMessage } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { PARTENARIATO_COPY } from "../../../lib/copy";
import { formatDateTime } from "../../../lib/format";
import type { EstrazioneAdmin } from "../../../types";
import { Badge, type BadgeProps } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { Pagination } from "../../ui/Pagination";
import { Annuncio, Filtro, importoCents, numero, StatiLista, TabellaCard, thClass } from "./comuni";

const STATI: Record<EstrazioneAdmin["stato"], { etichetta: string; tono: BadgeProps["tone"] }> = {
  in_corso: { etichetta: "In corso", tono: "amber" },
  pronta: { etichetta: "Pronta", tono: "emerald" },
  errore: { etichetta: "Errore", tono: "red" },
};
const ESITI: Record<"estratta" | "nessun_segnale", string> = {
  estratta: "Regole estratte",
  nessun_segnale: "Nessun riferimento al partenariato",
};

/** Nuova estrazione di un bando: paga sempre il modello, nel budget del
 *  giorno. «Ignora l'attesa» salta il cooldown tra due analisi. */
function RianalizzaDialog({
  estrazione,
  onClose,
  onFatto,
}: {
  estrazione: EstrazioneAdmin | null;
  onClose: () => void;
  onFatto: (annuncio: string) => void;
}) {
  const id = useId();
  const forza = useForzaEstrazione();
  const [ignora, setIgnora] = useState(false);
  useEffect(() => {
    if (!estrazione) return;
    setIgnora(false);
    forza.reset();
    // Solo all'apertura.
  }, [estrazione?.bando_id]);

  if (!estrazione) return null;
  return (
    <Dialog
      open
      onClose={onClose}
      title="Rianalizzare il bando?"
      dismissible={!forza.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={forza.isPending}>
            Annulla
          </Button>
          <Button
            loading={forza.isPending}
            onClick={() =>
              forza.mutate(
                { bandoId: estrazione.bando_id, ignoraCooldown: ignora },
                {
                  onSuccess: (stato) =>
                    onFatto(
                      stato.stato === "in_corso" || stato.aggiornamento_in_corso
                        ? "Analisi avviata: la lista si aggiorna da sola."
                        : "Richiesta registrata.",
                    ),
                },
              )
            }
          >
            Rianalizza
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p>
          «{estrazione.bando_titolo}»: la nuova analisi usa il modello e il budget giornaliero delle
          estrazioni.
        </p>
        <label htmlFor={id} className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
          <input
            id={id}
            type="checkbox"
            className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
            checked={ignora}
            onChange={(e) => setIgnora(e.target.checked)}
          />
          <span>Ignora l'attesa tra due analisi dello stesso bando</span>
        </label>
        {forza.isError && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(forza.error)}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** Run manuale dello scheduler di oggi (tutti i passi del modulo). */
function RunDialog({ open, onClose, onFatto }: { open: boolean; onClose: () => void; onFatto: (t: string) => void }) {
  const run = useRunPartenariati();
  const [ripeti, setRipeti] = useState(false);
  useEffect(() => {
    if (!open) return;
    setRipeti(false);
    run.reset();
    // Solo all'apertura.
  }, [open]);
  const giaEseguita = run.isError && runGiaEseguita(run.error);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Eseguire la run di oggi?"
      dismissible={!run.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={run.isPending}>
            Annulla
          </Button>
          <Button
            loading={run.isPending}
            onClick={() =>
              run.mutate(ripeti || giaEseguita, {
                onSuccess: (esito) => onFatto(`Run del ${esito.giorno} eseguita.`),
              })
            }
          >
            {giaEseguita ? "Esegui di nuovo" : "Esegui"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <p>
          Esegue subito i passi giornalieri del modulo (estrazioni, chiusure, notifiche, verifiche
          dei consorzi). Le estrazioni restano nel budget del giorno. Può richiedere qualche minuto.
        </p>
        {giaEseguita ? (
          <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900" role="alert">
            La run di oggi è già stata eseguita: puoi ripeterla (i passi sono idempotenti).
          </p>
        ) : run.isError ? (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(run.error)}
          </p>
        ) : null}
        {!giaEseguita && (
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={ripeti}
              onChange={(e) => setRipeti(e.target.checked)}
            />
            <span>Ripetila anche se è già stata eseguita oggi</span>
          </label>
        )}
      </div>
    </Dialog>
  );
}

/** Estrazioni delle regole di partenariato per bando (WP3, rotte esistenti):
 *  stato, esito, costo del modello, errori; «Rianalizza» e la run manuale. */
export function EstrazioniTab() {
  const [stato, setStato] = useState<EstrazioneAdmin["stato"] | "">("");
  const [esito, setEsito] = useState<"estratta" | "nessun_segnale" | "">("");
  const [page, setPage] = useState(1);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const [daRianalizzare, setDaRianalizzare] = useState<EstrazioneAdmin | null>(null);
  const [runAperta, setRunAperta] = useState(false);
  useEffect(() => setPage(1), [stato, esito]);
  const lista = useAdminEstrazioni({ stato, esito }, page);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <Filtro<EstrazioneAdmin["stato"]>
            etichetta="Stato"
            valore={stato}
            onChange={setStato}
            opzioni={[
              { valore: "", etichetta: "Tutti" },
              ...(Object.keys(STATI) as EstrazioneAdmin["stato"][]).map((s) => ({
                valore: s,
                etichetta: STATI[s].etichetta,
              })),
            ]}
          />
          <Filtro<"estratta" | "nessun_segnale">
            etichetta="Esito"
            valore={esito}
            onChange={setEsito}
            opzioni={[
              { valore: "", etichetta: "Tutti" },
              { valore: "estratta", etichetta: ESITI.estratta },
              { valore: "nessun_segnale", etichetta: ESITI.nessun_segnale },
            ]}
          />
        </div>
        <Button variant="secondary" onClick={() => setRunAperta(true)}>
          <Play className="size-4" aria-hidden />
          Esegui la run di oggi
        </Button>
      </div>
      <Annuncio testo={annuncio} />
      <StatiLista
        isPending={lista.isPending}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto="Nessuna estrazione"
        descrizioneVuoto="Le regole si estraggono quando qualcuno apre la sezione partenariato di un bando o con la run giornaliera."
      >
        <TabellaCard caption="Estrazioni delle regole di partenariato" attenuata={lista.isPlaceholderData}>
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
              <th scope="col" className={thClass}>Bando</th>
              <th scope="col" className={thClass}>Stato</th>
              <th scope="col" className={thClass}>Esito</th>
              <th scope="col" className={cn(thClass, "text-right")}>Costo</th>
              <th scope="col" className={thClass}>Ultima esecuzione</th>
              <th scope="col" className={cn(thClass, "text-right")}>Azioni</th>
            </tr>
          </thead>
          <tbody>
            {lista.data?.items.map((e) => (
              <tr key={e.bando_id} className="border-b border-slate-100 align-top last:border-b-0">
                <th scope="row" className="max-w-72 px-4 py-3 text-left font-normal">
                  <Link
                    to={`/app/bandi/${e.bando_slug}`}
                    className="font-medium text-brand-600 hover:text-brand-700"
                  >
                    {e.bando_titolo}
                  </Link>
                  {e.modalita_effettiva && (
                    <p className="text-xs text-slate-500">
                      {PARTENARIATO_COPY.modalita[e.modalita_effettiva] ?? e.modalita_effettiva}
                    </p>
                  )}
                </th>
                <td className="px-4 py-3">
                  <Badge tone={STATI[e.stato]?.tono ?? "slate"}>{STATI[e.stato]?.etichetta ?? e.stato}</Badge>
                  {e.errore_codice && (
                    <p className="mt-1 font-mono text-xs text-red-700">{e.errore_codice}</p>
                  )}
                  {e.tentativi_falliti > 0 && (
                    <p className="text-xs text-slate-500">
                      {numero(e.tentativi_falliti)} tentativi falliti
                      {e.prossimo_tentativo_at ? `, prossimo il ${formatDateTime(e.prossimo_tentativo_at)}` : ""}
                    </p>
                  )}
                </td>
                <td className="px-4 py-3 text-slate-700">{e.esito ? ESITI[e.esito] : "—"}</td>
                <td className="px-4 py-3 text-right tabular text-slate-700">
                  {importoCents(e.cost_cents, "USD")}
                  <p className="text-xs text-slate-400">
                    {numero(e.input_tokens)} + {numero(e.output_tokens)} token
                  </p>
                </td>
                <td className="px-4 py-3 text-slate-700">
                  {e.ultima_esecuzione_at ? formatDateTime(e.ultima_esecuzione_at) : "—"}
                  {e.model && <p className="text-xs text-slate-400">{e.model}</p>}
                </td>
                <td className="px-4 py-3 text-right">
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={e.stato === "in_corso"}
                    aria-label={`Rianalizza il bando ${e.bando_titolo}`}
                    onClick={() => setDaRianalizzare(e)}
                  >
                    <RefreshCw className="size-4" aria-hidden />
                    Rianalizza
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </TabellaCard>
        {lista.data && (
          <div className="mt-4">
            <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={setPage} />
          </div>
        )}
      </StatiLista>
      <RianalizzaDialog
        estrazione={daRianalizzare}
        onClose={() => setDaRianalizzare(null)}
        onFatto={(t) => {
          setDaRianalizzare(null);
          setAnnuncio(t);
        }}
      />
      <RunDialog
        open={runAperta}
        onClose={() => setRunAperta(false)}
        onFatto={(t) => {
          setRunAperta(false);
          setAnnuncio(t);
        }}
      />
    </div>
  );
}
