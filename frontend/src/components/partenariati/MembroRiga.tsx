import { Building2, Check, CheckCircle2, Clock, Globe, LogOut, Pencil } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useAggiornaMembro, useConfermaMembro, useEsciMembro } from "../../hooks/useConsorzio";
import { apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { CONSORZIO_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import { nomePaese } from "../../lib/paesi";
import type { MembroConsorzio, PosizioneCall, RuoloMembro, StatoMembro } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextField } from "../ui/Field";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { leggiPercentuale, mostraDecimale, percentuale } from "./callDati";
import { SceltaRadio } from "./CampiCall";
import { useNomiCall } from "./useNomiCall";

/** Posizione della call da assegnare a un membro (solo il creatore le ha). */
export type PosizioneOpzione = Pick<PosizioneCall, "id" | "titolo" | "ruolo" | "quota_ipotizzata_pct">;

export const RUOLI_MEMBRO: readonly RuoloMembro[] = [
  "capofila",
  "partner",
  "affiliated_entity",
  "associated_partner",
];
/** La riga di chi ha creato la call può essere solo capofila o partner. */
const RUOLI_CREATORE: readonly RuoloMembro[] = ["capofila", "partner"];

export const opzioniRuolo = (ruoli: readonly RuoloMembro[]) =>
  ruoli.map((r) => ({
    valore: r,
    etichetta: CONSORZIO_COPY.ruoli[r],
    nota: CONSORZIO_COPY.ruoliSpiegazione[r],
  }));

/** Nome del membro fuori dalla sua riga (intestazioni della matrice,
 *  «Riguarda», esiti per membro, titoli dei dialog, annunci, nomi dei
 *  pulsanti): UNIVOCO nel consorzio. «La tua azienda» per la propria, «Chi ha
 *  creato la call» per il creatore visto dagli altri membri, le altre aziende
 *  anonime con l'inizio del loro riferimento per questa call, gli esterni col
 *  nome dichiarato. */
export const nomeMembro = (m: MembroConsorzio) => {
  if (m.sei_tu) return CONSORZIO_COPY.tuaAzienda;
  if (m.esterno) return m.nome;
  if (m.creatore) return CONSORZIO_COPY.creatoreCall;
  const riferimento = m.pseudonimo ?? m.id;
  return `${m.nome} ${riferimento.slice(0, 6).toUpperCase()}`;
};

const TONI_STATO: Record<StatoMembro, "amber" | "emerald" | "slate"> = {
  proposto: "amber",
  confermato: "emerald",
  uscito: "slate",
};
const ICONE_STATO: Record<StatoMembro, typeof Check> = {
  proposto: Clock,
  confermato: CheckCircle2,
  uscito: LogOut,
};

/** Stato nel consorzio: icona **e** testo. */
export function StatoMembroBadge({ stato }: { stato: StatoMembro }) {
  const Icona = ICONE_STATO[stato] ?? Clock;
  return (
    <Badge tone={TONI_STATO[stato] ?? "slate"}>
      <Icona className="size-3.5" aria-hidden />
      <span className="sr-only">Stato: </span>
      {CONSORZIO_COPY.stati[stato] ?? stato}
    </Badge>
  );
}

/** Ruolo, posizione e quota di un membro in piattaforma (solo il creatore).
 *  Per un'altra azienda la modifica chiede una nuova conferma. */
function MembroDialog({
  open,
  onClose,
  callId,
  membro,
  posizioni,
  onSalvato,
}: {
  open: boolean;
  onClose: () => void;
  callId: string;
  membro: MembroConsorzio;
  posizioni: PosizioneOpzione[];
  onSalvato: (annuncio: string) => void;
}) {
  const id = useId();
  const aggiorna = useAggiornaMembro(callId);
  const [ruolo, setRuolo] = useState<RuoloMembro>(membro.ruolo);
  const [posizione, setPosizione] = useState(membro.posizione?.id ?? "");
  const [quota, setQuota] = useState(mostraDecimale(membro.quota_percentuale));
  const [errore, setErrore] = useState<string | null>(null);

  // A ogni apertura si riparte dai dati salvati.
  useEffect(() => {
    if (!open) return;
    setRuolo(membro.ruolo);
    setPosizione(membro.posizione?.id ?? "");
    setQuota(mostraDecimale(membro.quota_percentuale));
    setErrore(null);
    aggiorna.reset();
    // Solo all'apertura (`aggiorna` cambia a ogni render).
  }, [open]);

  const lettura = leggiPercentuale(quota);
  const ruoli = membro.creatore ? RUOLI_CREATORE : RUOLI_MEMBRO;
  // La posizione salvata resta tra le scelte anche se la call non la elenca più.
  const salvata = membro.posizione;
  const opzioni: PosizioneOpzione[] =
    salvata && !posizioni.some((p) => p.id === salvata.id)
      ? [...posizioni, { id: salvata.id, titolo: salvata.titolo, ruolo: "partner", quota_ipotizzata_pct: null }]
      : posizioni;

  const salva = () => {
    if (!lettura.ok) {
      setErrore(lettura.errore);
      return;
    }
    setErrore(null);
    aggiorna.mutate(
      {
        membroId: membro.id,
        dati: { ruolo, posizione_id: posizione || null, quota_percentuale: lettura.valore },
      },
      {
        onSuccess: () => {
          onSalvato(
            membro.creatore
              ? "La tua riga del consorzio è aggiornata."
              : `Modifiche salvate: ${nomeMembro(membro)} deve confermare di nuovo.`,
          );
          onClose();
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      dismissible={!aggiorna.isPending}
      title={membro.creatore ? "La tua partecipazione" : `Modifica: ${nomeMembro(membro)}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={aggiorna.isPending}>
            Annulla
          </Button>
          <Button onClick={salva} loading={aggiorna.isPending}>
            Salva
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          salva();
        }}
      >
        <p className="text-sm text-slate-600">
          {!membro.creatore
            ? "Se cambi ruolo, posizione o quota, l'azienda dovrà confermare di nuovo la sua partecipazione."
            : membro.stato === "confermato"
              ? "La tua riga resta confermata, a meno che tu non tolga la quota."
              : "La tua partecipazione è ancora da confermare: dopo il salvataggio ricordati di confermarla."}
        </p>
        <SceltaRadio
          legenda="Ruolo nel consorzio"
          nome={`${id}-ruolo`}
          opzioni={opzioniRuolo(ruoli)}
          valore={ruolo}
          onChange={setRuolo}
        />
        {opzioni.length > 0 && (
          <SelectField
            label="Posizione della call"
            value={posizione}
            onChange={(e) => setPosizione(e.target.value)}
          >
            <option value="">Nessuna</option>
            {opzioni.map((p) => (
              <option key={p.id} value={p.id}>
                {p.titolo}
                {p.quota_ipotizzata_pct ? ` (quota prevista ${percentuale(p.quota_ipotizzata_pct)})` : ""}
              </option>
            ))}
          </SelectField>
        )}
        <TextField
          label="Quota del progetto (%)"
          inputMode="decimal"
          value={quota}
          onChange={(e) => setQuota(e.target.value)}
          placeholder="Es. 30"
          error={errore ?? undefined}
          helper="La parte del progetto (e del budget) che spetta a questo membro. Serve per confermare."
        />
        {aggiorna.isError && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(aggiorna.error)}
          </p>
        )}
      </form>
    </Dialog>
  );
}

/** Conferma dell'uscita (il membro esce da sé) o della rimozione (il
 *  creatore toglie un membro). */
function EsciDialog({
  open,
  onClose,
  callId,
  membro,
  onFatto,
}: {
  open: boolean;
  onClose: () => void;
  callId: string;
  membro: MembroConsorzio;
  onFatto: (annuncio: string) => void;
}) {
  const esci = useEsciMembro(callId);
  useEffect(() => {
    if (open) esci.reset();
    // Solo all'apertura (`esci` cambia a ogni render).
  }, [open]);
  const tu = membro.sei_tu;
  const conferma = () =>
    esci.mutate(
      { membroId: membro.id },
      {
        onSuccess: () => {
          onFatto(tu ? "La tua azienda è uscita dal consorzio." : `${nomeMembro(membro)} non fa più parte del consorzio.`);
          onClose();
        },
      },
    );
  return (
    <Dialog
      open={open}
      onClose={onClose}
      dismissible={!esci.isPending}
      title={tu ? "Uscire dal consorzio?" : `Togliere ${nomeMembro(membro)} dal consorzio?`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={esci.isPending}>
            Non ora
          </Button>
          <Button variant="danger" onClick={conferma} loading={esci.isPending}>
            {tu ? "Esci dal consorzio" : "Togli dal consorzio"}
          </Button>
        </>
      }
    >
      <p>
        {tu
          ? "La tua azienda non farà più parte del consorzio di questa call."
          : membro.esterno
            ? "Il membro esterno esce dal consorzio: potrai riproporlo modificandolo."
            : "L'azienda non farà più parte del consorzio di questa call."}
      </p>
      {esci.isError && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {apiErrorMessage(esci.error)}
        </p>
      )}
    </Dialog>
  );
}

/** Un membro del consorzio (un `<li>`): nome (lo pseudonimo della call per le
 *  altre aziende, con i soli dati anonimi), ruolo, posizione, quota e stato;
 *  per gli esterni paese e tipi dichiarati. Le azioni sono quelle che il
 *  server ammette (`puo_modificare`, `puo_confermare`, `puo_uscire`). */
export function MembroRiga({
  membro,
  callId,
  posizioni,
  onModificaEsterno,
  onAnnuncio,
}: {
  membro: MembroConsorzio;
  callId: string;
  posizioni: PosizioneOpzione[];
  /** Gli esterni si modificano con il loro dialog (nel consorzio). */
  onModificaEsterno: (membro: MembroConsorzio) => void;
  onAnnuncio: (testo: string) => void;
}) {
  const idNotaConferma = useId();
  const nomi = useNomiCall();
  const conferma = useConfermaMembro(callId);
  const [modifica, setModifica] = useState(false);
  const [esci, setEsci] = useState(false);
  const { profilo } = membro;
  const uscito = membro.stato === "uscito";
  const senzaQuota = !membro.quota_percentuale;
  // Il partner associato non riceve budget: conferma anche senza quota.
  const serveQuota = membro.ruolo !== "associated_partner";
  const nome = nomeMembro(membro);

  const classe = profilo?.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[profilo.classe_dimensionale] ?? profilo.classe_dimensionale)
    : null;
  const dati = membro.esterno
    ? [membro.paese ? nomePaese(membro.paese) : null].filter(Boolean)
    : [
        classe,
        profilo?.regione_sede ?? null,
        profilo?.ateco_sezione
          ? `${profilo.ateco_sezione.lettera} — ${profilo.ateco_sezione.descrizione}`
          : null,
      ].filter(Boolean);
  const riferimento = !membro.sei_tu && membro.pseudonimo && membro.pseudonimo !== membro.nome;
  const Icona = membro.esterno ? Globe : Building2;
  const azioneModifica = membro.esterno && uscito ? "Riproponi" : "Modifica";

  // Si conferma ciò che la pagina mostra: se nel frattempo è cambiato, il
  // server risponde 409 e il consorzio si rilegge.
  const confermaMembro = () =>
    conferma.mutate(
      {
        membroId: membro.id,
        visti: {
          ruolo: membro.ruolo,
          posizione_id: membro.posizione?.id ?? null,
          quota_percentuale: membro.quota_percentuale,
        },
      },
      {
        onSuccess: () =>
          onAnnuncio(
            membro.sei_tu ? "Hai confermato la partecipazione della tua azienda." : `Hai confermato ${nome}.`,
          ),
      },
    );

  return (
    <li className={cn("rounded-lg border border-slate-200 px-3.5 py-3", uscito && "bg-slate-50")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-start gap-2.5">
          <Icona className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
          <div className="min-w-0">
            <p className={cn("font-medium text-slate-900", uscito && "text-slate-500")}>
              {membro.nome}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {membro.sei_tu && <Badge tone="brand">{CONSORZIO_COPY.tuaAzienda}</Badge>}
              {membro.creatore && <Badge tone="slate">Ha creato la call</Badge>}
              {membro.esterno && <Badge tone="slate">{CONSORZIO_COPY.esterno}</Badge>}
              <Badge tone={membro.ruolo === "capofila" ? "brand" : "slate"}>
                <span className="sr-only">Ruolo: </span>
                {CONSORZIO_COPY.ruoli[membro.ruolo] ?? membro.ruolo}
              </Badge>
              <StatoMembroBadge stato={membro.stato} />
            </div>
            {dati.length > 0 && <p className="mt-1 text-xs text-slate-500">{dati.join(" · ")}</p>}
            {membro.esterno && membro.tipi_soggetto.length > 0 && (
              <p className="mt-1 text-xs text-slate-500">
                {membro.tipi_soggetto.map(nomi.tipo).join(", ")}{" "}
                <span className="text-amber-700">({CONSORZIO_COPY.dichiarato})</span>
              </p>
            )}
            {riferimento && (
              <p className="mt-1 text-xs text-slate-400">
                Riferimento per questa call:{" "}
                <span className="font-mono tracking-wide">{membro.pseudonimo}</span>
              </p>
            )}
          </div>
        </div>
        <dl className="grid shrink-0 grid-cols-2 gap-x-6 gap-y-1 text-sm sm:text-right">
          <div>
            <dt className="text-xs text-slate-400">Quota</dt>
            <dd className="font-medium text-slate-800 tabular">
              {senzaQuota ? (
                <span className="font-normal text-slate-500">{serveQuota ? "Da indicare" : "Non serve"}</span>
              ) : (
                percentuale(membro.quota_percentuale)
              )}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-slate-400">Posizione</dt>
            <dd className="max-w-44 truncate text-slate-700" title={membro.posizione?.titolo}>
              {membro.posizione?.titolo ?? <span className="text-slate-500">Nessuna</span>}
            </dd>
          </div>
        </dl>
      </div>

      {membro.stato === "confermato" && membro.confermato_at && (
        <p className="mt-2 text-xs text-slate-500">Confermato il {formatDate(membro.confermato_at)}.</p>
      )}

      {(membro.puo_confermare || membro.puo_modificare || membro.puo_uscire) && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {membro.puo_confermare && (
            <Button
              size="sm"
              onClick={confermaMembro}
              loading={conferma.isPending}
              disabled={senzaQuota && serveQuota}
              aria-describedby={senzaQuota && serveQuota ? idNotaConferma : undefined}
              aria-label={membro.sei_tu ? "Conferma la partecipazione della tua azienda" : `Conferma ${nome}`}
            >
              <Check className="size-4" aria-hidden />
              Conferma
            </Button>
          )}
          {membro.puo_modificare && (
            <Button
              size="sm"
              variant="secondary"
              onClick={() => (membro.esterno ? onModificaEsterno(membro) : setModifica(true))}
              aria-label={membro.sei_tu ? `${azioneModifica} la tua partecipazione` : `${azioneModifica} ${nome}`}
            >
              <Pencil className="size-4" aria-hidden />
              {azioneModifica}
            </Button>
          )}
          {membro.puo_uscire && (
            <Button
              size="sm"
              variant="ghost"
              className="text-red-700 hover:bg-red-50 hover:text-red-800"
              onClick={() => setEsci(true)}
              aria-label={membro.sei_tu ? undefined : `Togli dal consorzio: ${nome}`}
            >
              <LogOut className="size-4" aria-hidden />
              {membro.sei_tu ? "Esci dal consorzio" : "Togli dal consorzio"}
            </Button>
          )}
          {membro.puo_confermare && senzaQuota && serveQuota && (
            <p id={idNotaConferma} className="text-xs text-slate-500">
              {membro.puo_modificare
                ? "Per confermare serve una quota: indicala con «Modifica»."
                : "Per confermare serve una quota: la indica chi ha creato la call."}
            </p>
          )}
        </div>
      )}
      {conferma.isError && (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {apiErrorMessage(conferma.error)}
        </p>
      )}

      {membro.puo_modificare && !membro.esterno && (
        <MembroDialog
          open={modifica}
          onClose={() => setModifica(false)}
          callId={callId}
          membro={membro}
          posizioni={posizioni}
          onSalvato={onAnnuncio}
        />
      )}
      {membro.puo_uscire && (
        <EsciDialog
          open={esci}
          onClose={() => setEsci(false)}
          callId={callId}
          membro={membro}
          onFatto={onAnnuncio}
        />
      )}
    </li>
  );
}
