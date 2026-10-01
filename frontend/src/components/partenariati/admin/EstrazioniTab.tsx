import { Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import {
  runGiaEseguita,
  useAdminEstrazioni,
  useForzaEstrazione,
  useRunPartenariati,
} from "../../../hooks/useAdminPartenariati";
import { apiErrorMessage } from "../../../lib/api";
import { PARTENARIATO_COPY } from "../../../lib/copy";
import { formatDateTime } from "../../../lib/format";
import type { EstrazioneAdmin } from "../../../types";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Checkbox } from "../../ui/Checkbox";
import { Dialog } from "../../ui/Dialog";
import { Pagination } from "../../ui/Pagination";
import { Status, type TonoStatus } from "../../ui/Status";
import { Td, Th } from "../../ui/Table";
import { TextLink } from "../../ui/TextLink";
import {
  Annuncio,
  Filtro,
  importoCents,
  numero,
  StatiLista,
  TabellaCard,
  thRigaClass,
} from "./comuni";
import { useRientroPagina } from "../useRientroPagina";

const STATI: Record<EstrazioneAdmin["stato"], { etichetta: string; tono: TonoStatus }> = {
  in_corso: { etichetta: "In corso", tono: "in-apertura" },
  pronta: { etichetta: "Pronta", tono: "aperto" },
  errore: { etichetta: "Errore", tono: "errore" },
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
          <Button variant="secondary" onClick={onClose} disabled={forza.isPending}>
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
      <div className="flex flex-col gap-3">
        <p>
          «{estrazione.bando_titolo}»: la nuova analisi usa il modello e il budget giornaliero delle
          estrazioni.
        </p>
        <Checkbox
          label="Ignora l'attesa tra due analisi dello stesso bando"
          checked={ignora}
          onChange={(e) => setIgnora(e.target.checked)}
        />
        {forza.isError && <Alert tono="errore">{apiErrorMessage(forza.error)}</Alert>}
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
          <Button variant="secondary" onClick={onClose} disabled={run.isPending}>
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
      <div className="flex flex-col gap-3">
        <p>
          Esegue subito i passi giornalieri del modulo (estrazioni, chiusure, notifiche, verifiche
          dei consorzi). Le estrazioni restano nel budget del giorno. Può richiedere qualche minuto.
        </p>
        {giaEseguita ? (
          <Alert tono="attenzione">
            La run di oggi è già stata eseguita: puoi ripeterla (i passi sono idempotenti).
          </Alert>
        ) : run.isError ? (
          <Alert tono="errore">{apiErrorMessage(run.error)}</Alert>
        ) : null}
        {!giaEseguita && (
          <Checkbox
            label="Ripetila anche se è già stata eseguita oggi"
            checked={ripeti}
            onChange={(e) => setRipeti(e.target.checked)}
          />
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
  const fuoriPagina = useRientroPagina(lista.data, page, lista.isPlaceholderData, setPage);

  return (
    <div className="flex flex-col gap-4">
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
        isPending={lista.isPending || fuoriPagina}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto="Nessuna estrazione"
        descrizioneVuoto="Le regole si estraggono quando qualcuno apre la sezione partenariato di un bando o con la run giornaliera."
      >
        <TabellaCard caption="Estrazioni delle regole di partenariato" attenuata={lista.isPlaceholderData}>
          <thead>
            <tr>
              <Th>Bando</Th>
              <Th>Stato</Th>
              <Th>Esito</Th>
              <Th numerica>Costo</Th>
              <Th>Ultima esecuzione</Th>
              <Th numerica>Azioni</Th>
            </tr>
          </thead>
          <tbody>
            {lista.data?.items.map((e) => (
              <tr key={e.bando_id}>
                <th scope="row" className={`${thRigaClass} max-w-72`}>
                  <TextLink to={`/app/bandi/${e.bando_slug}`} className="font-medium">
                    {e.bando_titolo}
                  </TextLink>
                  {e.modalita_effettiva && (
                    <p className="text-small text-ink-3">
                      {PARTENARIATO_COPY.modalita[e.modalita_effettiva] ?? e.modalita_effettiva}
                    </p>
                  )}
                </th>
                <Td>
                  <Status tono={STATI[e.stato]?.tono ?? "neutro"}>
                    {STATI[e.stato]?.etichetta ?? e.stato}
                  </Status>
                  {e.errore_codice && (
                    <p className="mt-1 font-mono text-small text-danger">{e.errore_codice}</p>
                  )}
                  {e.tentativi_falliti > 0 && (
                    <p className="text-small text-ink-3">
                      {numero(e.tentativi_falliti)} tentativi falliti
                      {e.prossimo_tentativo_at ? `, prossimo il ${formatDateTime(e.prossimo_tentativo_at)}` : ""}
                    </p>
                  )}
                </Td>
                <Td className="text-ink-2">{e.esito ? ESITI[e.esito] : "—"}</Td>
                <Td numerica className="text-ink-2">
                  {importoCents(e.cost_cents, "USD")}
                  <p className="text-small text-ink-3">
                    {numero(e.input_tokens)} + {numero(e.output_tokens)} token
                  </p>
                </Td>
                <Td className="text-ink-2">
                  {e.ultima_esecuzione_at ? formatDateTime(e.ultima_esecuzione_at) : "—"}
                  {e.model && <p className="text-small text-ink-3">{e.model}</p>}
                </Td>
                <Td className="text-right">
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
                </Td>
              </tr>
            ))}
          </tbody>
        </TabellaCard>
        {lista.data && (
          <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={setPage} />
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
