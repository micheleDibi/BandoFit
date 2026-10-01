import { isAxiosError } from "axios";
import { FileCheck2, ShoppingCart, Undo2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useAddons } from "../../../hooks/useAddons";
import {
  useBilanciUfficiali,
  useRichiediBilancioUfficiale,
} from "../../../hooks/useBilanciUfficiali";
import { useMyAddons } from "../../../hooks/useMyAddons";
import { apiErrorCode, apiErrorMessage } from "../../../lib/api";
import {
  BILANCIO_UFFICIALE_ADDON_SLUG,
  annoRichiesta,
  anniRichiedibili,
  nomeFilePdfBilancio,
  richiestaAperta,
} from "../../../lib/bilanci";
import { cn } from "../../../lib/cn";
import { BILANCIO_UFFICIALE_COPY as COPY } from "../../../lib/copy";
import { formatDateTime, todayItalyIso } from "../../../lib/format";
import { prezzoDisplay } from "../../../lib/prezzo";
import type { BilancioRichiesta, StatoRichiestaBilancio } from "../../../types";
import { ExportPdfButton } from "../../shared/ExportPdfButton";
import { Badge } from "../../ui/Badge";
import { Button, LinkButton } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { SelectField } from "../../ui/Field";
import { Skeleton } from "../../ui/states";
import { Status, type TonoStatus } from "../../ui/Status";

/** Lo stato in parole (`Status`): il punto accompagna sempre la parola, non
 *  la sostituisce. */
const TONI_STATO: Record<StatoRichiestaBilancio, TonoStatus> = {
  in_invio: "neutro",
  in_lavorazione: "neutro",
  esito_ignoto: "attenzione",
  completata: "aperto",
  non_disponibile: "chiuso",
  annullata: "chiuso",
  errore: "errore",
};

/** Riga di spiegazione sotto una richiesta: il messaggio del server se c'è,
 *  altrimenti una frase per stato, esito della lettura o codice d'errore. */
function testoRichiesta(r: BilancioRichiesta): string | null {
  if (r.messaggio) return r.messaggio;
  switch (r.stato) {
    case "in_invio":
    case "in_lavorazione":
      return COPY.attesa;
    case "esito_ignoto":
      return COPY.verifica;
    case "completata": {
      const lettura =
        !r.xbrl_esito || r.xbrl_esito === "ok"
          ? COPY.completataOk
          : (COPY.esitiLettura[r.xbrl_esito] ?? null);
      return r.pdf_disponibile ? lettura : [lettura, COPY.senzaPdf].filter(Boolean).join(" ");
    }
    default:
      return r.errore_codice ? (COPY.errori[r.errore_codice] ?? null) : null;
  }
}

/** Completata senza PDF né numeri (documento troppo grande o illeggibile):
 *  non c'è nulla di pronto da usare, quindi il badge non dice «Pronto». */
function nonUtilizzabile(r: BilancioRichiesta): boolean {
  return r.stato === "completata" && !r.pdf_disponibile && !!r.xbrl_esito && r.xbrl_esito !== "ok";
}

function RichiestaVoce({ richiesta: r }: { richiesta: BilancioRichiesta }) {
  const anno = annoRichiesta(r);
  const testo = testoRichiesta(r);
  const inutilizzabile = nonUtilizzabile(r);
  return (
    // aria-atomic: quando il polling cambia lo stato si annuncia la voce
    // intera («Esercizio 2024, Pronto, …»), non la sola parola nuova.
    <li aria-atomic="true" className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-medium text-ink">{COPY.esercizioTitolo(anno)}</span>
          <Status tono={inutilizzabile ? "attenzione" : (TONI_STATO[r.stato] ?? "neutro")}>
            {inutilizzabile ? COPY.statoNonUtilizzabile : (COPY.stati[r.stato] ?? r.stato)}
          </Status>
          {r.rimborsata && (
            <Badge>
              <Undo2 className="size-3" aria-hidden />
              {COPY.unitaRestituita}
            </Badge>
          )}
        </div>
        <p className="mt-0.5 text-small text-ink-3">
          {COPY.richiestoIl(formatDateTime(r.created_at))}
          {r.stato === "completata" &&
            r.completata_at &&
            `, ${COPY.prontoIl(formatDateTime(r.completata_at))}`}
        </p>
        {testo && <p className="mt-1 text-body text-ink-2">{testo}</p>}
        {r.stato === "completata" && r.avvisi_count > 0 && (
          <p className="mt-1 text-small text-warning-ink">
            {r.pdf_disponibile ? COPY.avvisi(r.avvisi_count) : COPY.avvisiSenzaPdf(r.avvisi_count)}
          </p>
        )}
      </div>
      {r.pdf_disponibile && (
        <ExportPdfButton
          url={`/me/company/bilanci/ufficiale/${r.id}/pdf`}
          filename={nomeFilePdfBilancio(r)}
          label={COPY.scaricaPdf}
          busyLabel={COPY.scaricamento}
          srLabel={COPY.scaricaPdfContesto(anno)}
          size="sm"
        />
      )}
    </li>
  );
}

/** Errore senza risposta leggibile del server (timeout, rete, proxy): la
 *  richiesta può essere partita comunque, quindi non va rifatta alla cieca. */
function esitoIncerto(err: unknown): boolean {
  if (!isAxiosError(err) || apiErrorCode(err)) return false;
  return !err.response || err.response.status >= 500;
}

/** Card «Bilancio ufficiale» nella sezione Bilanci: il bilancio depositato al
 *  Registro Imprese, pagato con un'unità dell'addon `bilancio-ufficiale`
 *  (consumo sempre, anche per gli esercizi senza dati). Il titolare sceglie
 *  l'esercizio e conferma; tutti vedono lo storico e scaricano i PDF. Senza
 *  addon a catalogo la card sparisce, a meno che ci siano già richieste: i
 *  PDF acquistati restano scaricabili. */
export function BilancioUfficialeCard({ className }: { className?: string }) {
  const { data, isPending, isError, error, refetch } = useBilanciUfficiali();
  const { data: addons } = useAddons();
  const inventario = useMyAddons();
  const richiedi = useRichiediBilancioUfficiale();
  const [anno, setAnno] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [esito, setEsito] = useState<string | null>(null);
  // Fallback al gating a priori: il server ha risposto payment_required
  // (saldo cambiato sotto i piedi). La CTA d'acquisto resta finché non arriva
  // un inventario letto DOPO quell'errore (la mutation lo fa rileggere): da lì
  // decide di nuovo la quantità, così un acquisto sblocca la card.
  const [esauritoAlle, setEsauritoAlle] = useState<number | null>(null);
  // Lo storico diventa regione live solo DOPO la prima lettura (anche dopo un
  // cambio azienda): all'arrivo dei dati la lista entra in silenzio, poi si
  // annunciano solo i cambiamenti (nuova richiesta, stati dal polling).
  const haDati = data !== undefined;
  const [storicoLive, setStoricoLive] = useState(false);
  useEffect(() => setStoricoLive(haDati), [haDati]);

  const addon = addons?.find((a) => a.slug === BILANCIO_UFFICIALE_ADDON_SLUG && a.is_active);
  const richieste = data?.richieste ?? [];
  if (!addon && richieste.length === 0) return null;

  const prezzo = addon
    ? prezzoDisplay(addon.tipo_prezzo, addon.etichetta_prezzo, addon.prezzo).testo
    : null;
  const quantita =
    inventario.data?.find((m) => m.slug === BILANCIO_UFFICIALE_ADDON_SLUG)?.quantita ?? 0;
  const attesaInventario = inventario.isPending;
  const bloccato =
    !attesaInventario &&
    (quantita === 0 || (esauritoAlle !== null && inventario.dataUpdatedAt <= esauritoAlle));
  const aperta = richieste.some((r) => richiestaAperta(r.stato));
  // Esercizi già posseduti: registrati da un XBRL oppure consegnati da una
  // richiesta conclusa, anche senza numeri leggibili (lo stesso documento
  // tornerebbe identico, a pagamento). Il server li dà tutti, anche quelli
  // oltre le ultime richieste in lista; qui si aggiungono quelli della lista.
  const anniAcquisiti = [
    ...new Set([
      ...(data?.anni_acquisiti ?? []),
      ...richieste
        .filter((r) => r.stato === "completata")
        .map(annoRichiesta)
        .filter((a): a is number => a !== null),
    ]),
  ];
  const annoCorrente = Number(todayItalyIso().slice(0, 4));
  const opzioniAnno = anniRichiedibili(anniAcquisiti, annoCorrente);
  // «Ultimo disponibile» può riportare l'ultimo esercizio chiuso o, a inizio
  // anno, quello di due anni fa: se l'azienda ne ha già uno (il server
  // risponde bilancio_gia_presente) l'opzione non si propone e la scelta
  // parte dall'esercizio più recente che manca. Una richiesta conclusa senza
  // anno (né chiesto né letto) vale, come sul server, l'esercizio chiuso
  // prima della richiesta.
  const ignotoRecente = richieste.some(
    (r) =>
      r.stato === "completata" &&
      annoRichiesta(r) === null &&
      Number(r.created_at.slice(0, 4)) - 1 >= annoCorrente - 2,
  );
  const ultimoPosseduto = ignotoRecente || anniAcquisiti.some((a) => a >= annoCorrente - 2);
  // Un anno appena acquisito sparisce dal menu: la scelta torna al default.
  const annoScelto =
    anno !== "" && opzioniAnno.includes(Number(anno))
      ? Number(anno)
      : ultimoPosseduto
        ? (opzioniAnno[0] ?? null)
        : null;

  const handleConferma = async () => {
    if (richiedi.isPending) return; // doppio click: la richiesta consuma un'unità
    setActionError(null);
    try {
      await richiedi.mutateAsync(annoScelto);
      // L'esito si legge nella nuova voce dello storico (annunciata).
      setConfirmOpen(false);
      setAnno("");
    } catch (err) {
      const code = apiErrorCode(err);
      if (code === "payment_required") {
        setEsauritoAlle(Date.now());
        setConfirmOpen(false);
        return;
      }
      if (code === "bilancio_in_corso" || code === "bilancio_gia_presente") {
        // Il dialog non resta aperto: rilette le richieste, l'esercizio
        // mostrato potrebbe cambiare sotto una seconda conferma.
        setConfirmOpen(false);
        setAnno("");
        setEsito(apiErrorMessage(err));
        return;
      }
      if (esitoIncerto(err)) {
        setConfirmOpen(false);
        setEsito(COPY.esitoSenzaConferma);
        return;
      }
      setActionError(apiErrorMessage(err));
    }
  };

  let azioni: ReactNode = null;
  if (!addon) {
    // Addon tolto dal catalogo: resta lo storico, con i PDF già pagati, e il
    // motivo per cui non se ne richiedono altri.
    azioni = data?.motivo_non_richiedibile ? (
      <p className="mt-4 rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-small text-ink">
        {data.motivo_non_richiedibile}
      </p>
    ) : null;
  } else if (isPending) {
    azioni = <Skeleton className="mt-4 h-20 w-full" />;
  } else if (isError || !data) {
    azioni = (
      <div
        role="alert"
        className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-control border border-danger-line bg-danger-soft px-3 py-2 text-small text-danger"
      >
        {apiErrorMessage(error, COPY.erroreCaricamento)}
        <Button variant="secondary" size="sm" onClick={() => refetch()}>
          Riprova
        </Button>
      </div>
    );
  } else if (!data.editable) {
    azioni = <p className="mt-3 text-small text-ink-3">{COPY.soloTitolare}</p>;
  } else if (!data.richiedibile) {
    azioni = data.motivo_non_richiedibile ? (
      <p className="mt-4 rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-small text-ink">
        {data.motivo_non_richiedibile}
      </p>
    ) : null;
  } else {
    azioni = (
      <div className="mt-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-full sm:w-60">
            <SelectField
              label={COPY.esercizio}
              value={annoScelto === null ? "" : String(annoScelto)}
              onChange={(e) => setAnno(e.target.value)}
              disabled={aperta || richiedi.isPending}
            >
              {!ultimoPosseduto && <option value="">{COPY.ultimoDisponibile}</option>}
              {opzioniAnno.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </SelectField>
          </div>
          <Button
            disabled={aperta || bloccato || attesaInventario}
            onClick={() => {
              setActionError(null);
              setEsito(null);
              setConfirmOpen(true);
            }}
          >
            {COPY.richiedi}
          </Button>
        </div>
        {anniAcquisiti.length > 0 && (
          <p className="mt-2 text-small text-ink-3">
            {COPY.giaAcquisiti([...anniAcquisiti].sort((a, b) => b - a).join(", "))}
          </p>
        )}
        {aperta ? (
          <p className="mt-2 text-small text-ink-3">{COPY.inCorso}</p>
        ) : bloccato ? (
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <p className="text-body text-ink-2">{COPY.senzaUnita(prezzo ?? "")}</p>
            <LinkButton
              to={`/app/checkout?addon=${BILANCIO_UFFICIALE_ADDON_SLUG}`}
              variant="secondary"
              size="sm"
            >
              <ShoppingCart className="size-4" aria-hidden />
              {COPY.acquista}
            </LinkButton>
          </div>
        ) : (
          !attesaInventario && (
            <p className="mt-2 text-small text-ink-3">{COPY.disponibili(quantita)}</p>
          )
        )}
      </div>
    );
  }

  return (
    <Card className={cn("p-5", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="inline-flex items-center gap-1.5 font-sans text-title-group text-ink">
          <FileCheck2 className="size-4 text-ink-2" aria-hidden />
          {addon?.nome ?? COPY.titolo}
        </h3>
        {prezzo && <span className="text-small text-ink-3">{COPY.prezzo(prezzo)}</span>}
      </div>
      <p className="mt-1 text-body text-ink-2">{COPY.descrizione}</p>

      {azioni}

      {/* Esiti che lo storico non mostra (conferma mancata, richiesta già in
          corso): regione sempre montata, così l'annuncio non si perde. */}
      <div role="status" aria-live="polite">
        {esito && (
          <p className="mt-3 rounded-control border border-warning-line bg-warning-soft px-3 py-2 text-small text-ink">{esito}</p>
        )}
      </div>

      {/* Storico: regione sempre montata, live dopo la prima lettura, così una
          richiesta nuova e i cambi di stato portati dal polling vengono
          annunciati (ma non l'intera lista a ogni visita). */}
      <div
        aria-live={storicoLive ? "polite" : "off"}
        className={richieste.length > 0 ? "mt-5" : undefined}
      >
        {richieste.length > 0 && (
          <>
            <h4 className="text-small font-medium text-ink-3">
              {COPY.storicoTitolo}
            </h4>
            <ul className="mt-1 divide-y divide-line">
              {richieste.map((r) => (
                <RichiestaVoce key={r.id} richiesta={r} />
              ))}
            </ul>
          </>
        )}
      </div>

      <Dialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        dismissible={!richiedi.isPending}
        title={COPY.confermaTitolo}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={richiedi.isPending}
            >
              {COPY.annulla}
            </Button>
            <Button loading={richiedi.isPending} onClick={handleConferma}>
              {COPY.confermaInvia}
            </Button>
          </>
        }
      >
        <p>{COPY.conferma(quantita)}</p>
        <p className="mt-2 font-medium text-ink">{COPY.confermaEsercizio(annoScelto)}</p>
        <p className="mt-2 text-small text-ink-3">{COPY.confermaRimborso}</p>
        {actionError && (
          <p className="mt-3 rounded-control border border-danger-line bg-danger-soft px-3 py-2 text-small text-danger" role="alert">
            {actionError}
          </p>
        )}
      </Dialog>
    </Card>
  );
}
