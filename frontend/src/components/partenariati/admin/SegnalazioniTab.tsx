import { Eye, Gavel, MessageSquareText, Scale, ShieldOff, UserCheck } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";
import {
  useAdminSegnalazioni,
  useAnteprimaStatement,
  useContestoCompleto,
  useContestoSegnalazione,
  useDecidiRicorso,
  useDecidiSegnalazione,
  usePrendiSegnalazione,
  useRipristinaOggetto,
  useSospendiOggetto,
} from "../../../hooks/useAdminPartenariati";
import { useDebounce } from "../../../hooks/useDebounce";
import { apiErrorMessage } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { ADMIN_PARTENARIATI_COPY, CALL_COPY, MODERAZIONE_COPY } from "../../../lib/copy";
import { formatDate, formatDateTime } from "../../../lib/format";
import type {
  DecisioneSegnalazione,
  EsitoRicorso,
  FiltroCodaSegnalazioni,
  MessaggioContesto,
  OggettoModerazione,
  OggettoSegnalazione,
  SegnalazioneAdmin,
  StatoSegnalazione,
} from "../../../types";
import { Accordion } from "../../ui/Accordion";
import { Alert } from "../../ui/Alert";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Card } from "../../ui/Card";
import { Dialog } from "../../ui/Dialog";
import { TextField } from "../../ui/Field";
import { InlineError } from "../../ui/InlineError";
import { Pagination } from "../../ui/Pagination";
import { SceltaRadio, TestoLungo } from "../CampiCall";
import { codiceSegnalazione, StatoSegnalazioneBadge } from "../StatoSegnalazioneBadge";
import { useRientroPagina } from "../useRientroPagina";
import { Annuncio, Filtro, MotivazioneDialog, StatiLista } from "./comuni";

const STATI: StatoSegnalazione[] = [
  "ricevuta",
  "in_esame",
  "decisa",
  "ricorso_presentato",
  "ricorso_deciso",
];

/** La sola restrizione coerente con l'oggetto (oltre a «nessuna azione»). */
const RESTRIZIONE: Record<OggettoSegnalazione, Exclude<DecisioneSegnalazione, "nessuna_azione">> = {
  call: "call_sospesa",
  messaggio: "contenuto_rimosso",
  profilo: "profilo_sospeso",
};

const MIN = MODERAZIONE_COPY.motivazioneMin;
const MAX = MODERAZIONE_COPY.motivazioneMax;
/** Motivazione per leggere la conversazione intera (registrata in audit). */
const CONTESTO_MIN = MIN;
const CONTESTO_MAX = MAX;

const testo = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);

// ---- Contenuto segnalato (snapshot) ---------------------------------------------

function Riga({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-small text-ink-3">{titolo}</dt>
      <dd className="whitespace-pre-line text-body text-ink-2">{children}</dd>
    </div>
  );
}

/** Ciò che il segnalante vedeva quando ha segnalato (evidenza DSA): i campi
 *  principali in chiaro e, a parte, tutti i dati salvati. */
function ContenutoSegnalato({
  tipo,
  snapshot,
}: {
  tipo: OggettoSegnalazione;
  snapshot: Record<string, unknown>;
}) {
  const s = snapshot ?? {};
  const oggetto = (chiave: string) =>
    (s[chiave] && typeof s[chiave] === "object" ? (s[chiave] as Record<string, unknown>) : {});
  let righe: ReactNode;
  if (tipo === "messaggio") {
    righe = (
      <>
        <Riga titolo="Messaggio">
          {testo(s.testo) ?? (s.nascosto ? "Era già oscurato quando è stato segnalato." : "—")}
        </Riga>
        {testo(s.created_at) && <Riga titolo="Scritto il">{formatDateTime(s.created_at as string)}</Riga>}
      </>
    );
  } else if (tipo === "call") {
    righe = (
      <>
        <Riga titolo="Titolo">{testo(s.titolo) ?? "—"}</Riga>
        <Riga titolo="Bando">{testo(oggetto("bando").titolo) ?? "—"}</Riga>
        <Riga titolo="Chi la propone">{testo(oggetto("creatore").denominazione) ?? "—"}</Riga>
        {testo(s.descrizione_pubblica) && (
          <Riga titolo="Il progetto">{testo(s.descrizione_pubblica)}</Riga>
        )}
        {testo(s.profilo_partner_ideale) && (
          <Riga titolo="Il partner ideale">{testo(s.profilo_partner_ideale)}</Riga>
        )}
      </>
    );
  } else {
    const competenze = Array.isArray(s.competenze)
      ? (s.competenze as Array<Record<string, unknown>>)
          .map((c) => testo(c?.etichetta))
          .filter(Boolean)
          .join(", ")
      : "";
    righe = (
      <>
        <Riga titolo="Azienda">{testo(s.denominazione) ?? CALL_COPY.aziendaAnonima}</Riga>
        {testo(s.codice_pubblico) && <Riga titolo="Codice pubblico">{testo(s.codice_pubblico)}</Riga>}
        {testo(s.descrizione_competenze) && (
          <Riga titolo="Descrizione">{testo(s.descrizione_competenze)}</Riga>
        )}
        {competenze && <Riga titolo="Competenze">{competenze}</Riga>}
      </>
    );
  }
  return (
    <div className="flex flex-col gap-3 rounded-control bg-desk px-4 py-3">
      <p className="text-title-group text-ink">Contenuto al momento della segnalazione</p>
      <dl className="flex flex-col gap-2">{righe}</dl>
      {/* `<details>` nativo: dentro la scheda l'`Accordion` sarebbe fuori scala. */}
      <details>
        <summary className="cursor-pointer rounded-mark text-small font-medium text-accent-hover">
          Tutti i dati salvati
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-mark bg-sheet p-2 text-caption text-ink-2">
          {JSON.stringify(s, null, 2)}
        </pre>
      </details>
    </div>
  );
}

// ---- Contesto di un messaggio ------------------------------------------------------

function ListaMessaggi({ messaggi }: { messaggi: MessaggioContesto[] }) {
  if (messaggi.length === 0) return <p className="text-body text-ink-2">Nessun messaggio.</p>;
  return (
    <ol className="flex flex-col gap-2">
      {messaggi.map((m) => (
        <li
          key={m.id}
          className={cn(
            "rounded-control border px-3 py-2 text-body",
            m.segnalato ? "border-danger-line bg-danger-soft" : "border-line bg-sheet",
          )}
        >
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small text-ink-3">
            <span className="font-medium text-ink-2">
              {m.lato === "autore" ? "Autore del messaggio segnalato" : "Altra azienda"}
            </span>
            {m.created_at && <span>{formatDateTime(m.created_at)}</span>}
            {m.segnalato && <span className="font-medium text-danger">Messaggio segnalato</span>}
            {m.oscurato && <Badge>Oscurato</Badge>}
          </p>
          <p className="mt-1 whitespace-pre-line text-ink">
            {m.testo ?? <span className="italic text-ink-3">Testo non disponibile.</span>}
          </p>
        </li>
      ))}
    </ol>
  );
}

/** Contesto di un messaggio segnalato (minimizzazione): prima una finestra di
 *  ±10 messaggi, su richiesta; la conversazione intera solo con una
 *  motivazione, che il server registra. */
function ContestoMessaggio({ id }: { id: string }) {
  const [aperto, setAperto] = useState(false);
  const [chiediCompleto, setChiediCompleto] = useState(false);
  const finestra = useContestoSegnalazione(id, aperto);
  const completo = useContestoCompleto(id);

  if (!aperto) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" onClick={() => setAperto(true)}>
          <MessageSquareText className="size-4" aria-hidden />
          Mostra il contesto (10 messaggi prima e dopo)
        </Button>
        <p className="text-small text-ink-3">La lettura viene registrata.</p>
      </div>
    );
  }
  const dati = completo.data ?? finestra.data;
  return (
    <section
      aria-label="Contesto del messaggio"
      className="flex flex-col items-start gap-3 rounded-control bg-desk px-4 py-3"
    >
      <p className="text-title-group text-ink">
        {completo.data ? "Conversazione intera" : "Contesto: 10 messaggi prima e dopo"}
      </p>
      {finestra.isPending && !completo.data ? (
        <p className="text-body text-ink-3">Caricamento…</p>
      ) : finestra.isError && !completo.data ? (
        <InlineError>{apiErrorMessage(finestra.error, "Impossibile caricare il contesto.")}</InlineError>
      ) : dati ? (
        <div className="flex w-full flex-col gap-2">
          {dati.altri_prima && (
            <p className="text-small text-ink-3">Ci sono messaggi precedenti non mostrati.</p>
          )}
          <ListaMessaggi messaggi={dati.messaggi} />
          {dati.altri_dopo && (
            <p className="text-small text-ink-3">Ci sono messaggi successivi non mostrati.</p>
          )}
          {dati.troncato && (
            <p className="text-small text-warning-ink">
              La conversazione è molto lunga: ne vedi solo una parte.
            </p>
          )}
        </div>
      ) : null}
      {!completo.data && (
        <Button variant="ghost" size="sm" onClick={() => setChiediCompleto(true)}>
          <Eye className="size-4" aria-hidden />
          Vedi tutta la conversazione
        </Button>
      )}
      <MotivazioneDialog
        open={chiediCompleto}
        onClose={() => setChiediCompleto(false)}
        titolo="Leggere tutta la conversazione?"
        descrizione="È una conversazione privata tra due aziende: leggila solo se il contesto non basta a decidere. La motivazione viene registrata insieme all'accesso."
        etichetta="Perché ti serve la conversazione intera"
        min={CONTESTO_MIN}
        max={CONTESTO_MAX}
        etichettaConferma="Mostra la conversazione"
        inCorso={completo.isPending}
        errore={completo.isError ? apiErrorMessage(completo.error) : null}
        onConferma={(motivazione) =>
          completo.mutate(motivazione, { onSuccess: () => setChiediCompleto(false) })
        }
      />
    </section>
  );
}

// ---- Decisioni ---------------------------------------------------------------------

/** Decisione motivata con l'anteprima della motivazione (statement of
 *  reasons) che riceverà l'azienda autrice. */
function DecidiDialog({
  s,
  open,
  onClose,
  onFatto,
}: {
  s: SegnalazioneAdmin;
  open: boolean;
  onClose: () => void;
  onFatto: (annuncio: string) => void;
}) {
  const nome = useId();
  const decidi = useDecidiSegnalazione();
  const [decisione, setDecisione] = useState<DecisioneSegnalazione | null>(null);
  const [motivazione, setMotivazione] = useState("");
  const [errore, setErrore] = useState<string | null>(null);
  const restrizione = RESTRIZIONE[s.oggetto_tipo];
  // Anteprima della motivazione formale dal server (stesso modello del testo
  // inviato), solo per una restrizione e con una motivazione valida.
  const motivazioneStabile = useDebounce(motivazione.trim(), 600);
  const anteprimaAttiva =
    open &&
    decisione !== null &&
    decisione !== "nessuna_azione" &&
    motivazioneStabile.length >= MIN &&
    motivazioneStabile.length <= MAX;
  const anteprima = useAnteprimaStatement(
    s.id,
    anteprimaAttiva && decisione ? { decisione, motivazione: motivazioneStabile } : null,
  );

  useEffect(() => {
    if (!open) return;
    setDecisione(null);
    setMotivazione("");
    setErrore(null);
    decidi.reset();
    // Solo all'apertura (`decidi` cambia a ogni render).
  }, [open]);

  const conferma = async () => {
    setErrore(null);
    if (!decisione) {
      setErrore("Scegli la decisione.");
      return;
    }
    if (motivazione.trim().length < MIN) {
      setErrore(`La motivazione deve avere almeno ${MIN} caratteri.`);
      return;
    }
    try {
      await decidi.mutateAsync({ id: s.id, decisione, motivazione: motivazione.trim() });
      onFatto(`Decisione registrata: ${MODERAZIONE_COPY.decisioni[decisione].toLowerCase()}.`);
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Decidi la segnalazione"
      size="lg"
      dismissible={!decidi.isPending}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={decidi.isPending}>
            Annulla
          </Button>
          <Button
            variant={decisione && decisione !== "nessuna_azione" ? "danger" : "primary"}
            loading={decidi.isPending}
            onClick={() => void conferma()}
          >
            Registra la decisione
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <SceltaRadio<DecisioneSegnalazione>
          legenda="Decisione"
          nome={nome}
          valore={decisione}
          onChange={setDecisione}
          opzioni={[
            {
              valore: restrizione,
              etichetta: MODERAZIONE_COPY.decisioni[restrizione],
              nota: "Effetto immediato. L'azienda autrice riceve la motivazione e può fare ricorso.",
            },
            {
              valore: "nessuna_azione",
              etichetta: MODERAZIONE_COPY.decisioni.nessuna_azione,
              nota: "Il contenuto resta. Chi ha segnalato riceve l'esito e può fare ricorso.",
            },
          ]}
        />
        <TestoLungo
          etichetta="Motivazione (fatti e circostanze)"
          aiuto={`Da ${MIN} a ${MAX} caratteri. Non scrivere chi ha segnalato: l'azienda autrice legge questo testo.`}
          valore={motivazione}
          onChange={setMotivazione}
          massimo={MAX}
          righe={5}
          required
        />
        {decisione && decisione !== "nessuna_azione" && (
          <div className="flex flex-col gap-1">
            <p className="text-small font-medium text-ink">
              Anteprima della motivazione per l'azienda autrice
            </p>
            <p className="text-small text-ink-3">
              È il testo che riceverà, con le vie di ricorso. Non indica mai chi ha segnalato.
            </p>
            <div
              className="mt-1 max-h-72 overflow-auto rounded-control bg-desk p-3 text-small text-ink-2"
              aria-live="polite"
              aria-busy={anteprima.isFetching}
            >
              {!anteprimaAttiva && !anteprima.data ? (
                <p className="text-ink-3">
                  Scrivi almeno {MIN} caratteri di motivazione per vedere l'anteprima.
                </p>
              ) : anteprima.isError ? (
                <p className="text-danger">{apiErrorMessage(anteprima.error)}</p>
              ) : anteprima.data?.testo ? (
                <p
                  className={cn(
                    "whitespace-pre-line",
                    anteprima.isPlaceholderData && "opacity-60 transition-opacity",
                  )}
                >
                  {anteprima.data.testo}
                </p>
              ) : (
                <p className="text-ink-3">Preparazione dell'anteprima…</p>
              )}
            </div>
          </div>
        )}
        {errore && <Alert tono="errore">{errore}</Alert>}
      </div>
    </Dialog>
  );
}

function DecidiRicorsoDialog({
  s,
  open,
  onClose,
  onFatto,
}: {
  s: SegnalazioneAdmin;
  open: boolean;
  onClose: () => void;
  onFatto: (annuncio: string) => void;
}) {
  const nome = useId();
  const decidi = useDecidiRicorso();
  const [esito, setEsito] = useState<EsitoRicorso | null>(null);
  const [motivazione, setMotivazione] = useState("");
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setEsito(null);
    setMotivazione("");
    setErrore(null);
    decidi.reset();
    // Solo all'apertura.
  }, [open]);

  const conferma = async () => {
    setErrore(null);
    if (!esito) {
      setErrore("Scegli l'esito del ricorso.");
      return;
    }
    if (motivazione.trim().length < MIN) {
      setErrore(`La motivazione deve avere almeno ${MIN} caratteri.`);
      return;
    }
    try {
      await decidi.mutateAsync({ id: s.id, esito, motivazione: motivazione.trim() });
      onFatto(`Ricorso deciso: ${MODERAZIONE_COPY.esitiRicorso[esito].toLowerCase()}.`);
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const restrittiva = s.decisione !== null && s.decisione !== "nessuna_azione";
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Decidi il ricorso"
      dismissible={!decidi.isPending}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={decidi.isPending}>
            Annulla
          </Button>
          <Button loading={decidi.isPending} onClick={() => void conferma()}>
            Registra l'esito
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {s.ricorso && (
          <div className="rounded-control bg-desk px-3 py-2 text-ink-2">
            <p className="text-small font-medium text-ink-3">
              Ricorso {s.ricorso.da === "autore" ? "dell'azienda autrice" : "di chi ha segnalato"}
              {s.ricorso.at ? ` del ${formatDate(s.ricorso.at)}` : ""}
            </p>
            {s.ricorso.testo && <p className="mt-1 whitespace-pre-line">{s.ricorso.testo}</p>}
          </div>
        )}
        <SceltaRadio<EsitoRicorso>
          legenda="Esito"
          nome={nome}
          valore={esito}
          onChange={setEsito}
          opzioni={[
            {
              valore: "confermata",
              etichetta: MODERAZIONE_COPY.esitiRicorso.confermata,
              nota: "Nulla cambia.",
            },
            {
              valore: "riformata",
              etichetta: MODERAZIONE_COPY.esitiRicorso.riformata,
              nota: restrittiva
                ? "La restrizione si annulla (salvo un'altra decisione valida sullo stesso contenuto)."
                : "La restrizione sul contenuto si applica adesso.",
            },
          ]}
        />
        <TestoLungo
          etichetta="Motivazione"
          aiuto={`Da ${MIN} a ${MAX} caratteri.`}
          valore={motivazione}
          onChange={setMotivazione}
          massimo={MAX}
          righe={4}
          required
        />
        {errore && <Alert tono="errore">{errore}</Alert>}
      </div>
    </Dialog>
  );
}

// ---- Una segnalazione della coda -----------------------------------------------------

function SegnalazioneCard({ s, onAnnuncio }: { s: SegnalazioneAdmin; onAnnuncio: (t: string) => void }) {
  const prendi = usePrendiSegnalazione();
  const [decidiAperto, setDecidiAperto] = useState(false);
  const [ricorsoAperto, setRicorsoAperto] = useState(false);
  const daDecidere = s.stato === "ricevuta" || s.stato === "in_esame";

  return (
    <li>
      <Card className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <StatoSegnalazioneBadge stato={s.stato} />
              <Badge>{MODERAZIONE_COPY.oggetti[s.oggetto_tipo] ?? s.oggetto_tipo}</Badge>
              <span className="font-mono text-caption text-ink-3">
                <span className="sr-only">Codice </span>
                {s.codice || codiceSegnalazione(s.id)}
              </span>
            </div>
            <h2 className="text-row-title text-ink">
              {CALL_COPY.segnalaMotivi[s.motivo] ?? s.motivo}
            </h2>
            <p className="flex flex-wrap gap-x-3 text-small text-ink-3">
              <span>{s.created_at ? `Ricevuta il ${formatDateTime(s.created_at)}` : "Ricevuta"}</span>
              {s.autore && (
                <span>Azienda autrice: {s.autore.ragione_sociale ?? "non più disponibile"}</span>
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {s.stato === "ricevuta" && (
              <Button
                variant="secondary"
                size="sm"
                loading={prendi.isPending}
                onClick={() =>
                  prendi.mutate(s.id, { onSuccess: () => onAnnuncio("Segnalazione presa in carico.") })
                }
              >
                <UserCheck className="size-4" aria-hidden />
                Prendi in carico
              </Button>
            )}
            {daDecidere && (
              <Button variant="secondary" size="sm" onClick={() => setDecidiAperto(true)}>
                <Gavel className="size-4" aria-hidden />
                Decidi
              </Button>
            )}
            {s.stato === "ricorso_presentato" && (
              <Button variant="secondary" size="sm" onClick={() => setRicorsoAperto(true)}>
                <Scale className="size-4" aria-hidden />
                Decidi il ricorso
              </Button>
            )}
          </div>
        </div>

        {prendi.isError && <Alert tono="errore">{apiErrorMessage(prendi.error)}</Alert>}

        <div className="flex flex-col gap-1">
          <p className="text-small text-ink-3">Cosa scrive chi ha segnalato</p>
          <p className="whitespace-pre-line text-body text-ink-2">{s.descrizione}</p>
        </div>

        <ContenutoSegnalato tipo={s.oggetto_tipo} snapshot={s.contenuto_snapshot} />

        {s.oggetto_tipo === "messaggio" && <ContestoMessaggio id={s.id} />}

        {s.decisione && (
          // Blocchi separati da un filetto: niente riquadri dentro la scheda.
          <div className="flex flex-col gap-1 border-t border-line pt-4">
            <p className="text-title-group text-ink">
              Decisione: {MODERAZIONE_COPY.decisioni[s.decisione]}
              {s.deciso_at ? ` (${formatDate(s.deciso_at)})` : ""}
            </p>
            {s.decisione_effettiva && s.decisione_effettiva !== s.decisione && (
              <p className="text-body text-ink-2">
                Dopo il ricorso: {MODERAZIONE_COPY.decisioni[s.decisione_effettiva]}
              </p>
            )}
            {s.ricorso_entro && !s.ricorso && (
              <p className="text-small text-ink-3">
                Ricorso possibile fino al {formatDate(s.ricorso_entro)}.
              </p>
            )}
            {s.motivazione && (
              <p className="whitespace-pre-line text-body text-ink-2">{s.motivazione}</p>
            )}
            {s.sor_testo && (
              <details className="mt-1">
                <summary className="cursor-pointer rounded-mark text-small font-medium text-accent-hover">
                  Motivazione inviata all'azienda autrice
                </summary>
                <p className="mt-2 whitespace-pre-line text-small text-ink-2">{s.sor_testo}</p>
              </details>
            )}
          </div>
        )}

        {s.ricorso && (
          <div className="flex flex-col gap-1 border-t border-line pt-4">
            <p className="text-title-group text-ink">
              Ricorso {s.ricorso.da === "autore" ? "dell'azienda autrice" : "di chi ha segnalato"}
              {s.ricorso.at ? ` del ${formatDate(s.ricorso.at)}` : ""}
            </p>
            {s.ricorso.testo && (
              <p className="whitespace-pre-line text-body text-ink-2">{s.ricorso.testo}</p>
            )}
            {s.ricorso.esito && (
              <p className="mt-1 text-body text-ink">
                <span className="font-medium">{MODERAZIONE_COPY.esitiRicorso[s.ricorso.esito]}</span>
                {s.ricorso.motivazione ? `: ${s.ricorso.motivazione}` : ""}
              </p>
            )}
          </div>
        )}
      </Card>
      <DecidiDialog
        s={s}
        open={decidiAperto}
        onClose={() => setDecidiAperto(false)}
        onFatto={(t) => {
          setDecidiAperto(false);
          onAnnuncio(t);
        }}
      />
      <DecidiRicorsoDialog
        s={s}
        open={ricorsoAperto}
        onClose={() => setRicorsoAperto(false)}
        onFatto={(t) => {
          setRicorsoAperto(false);
          onAnnuncio(t);
        }}
      />
    </li>
  );
}

// ---- Azione diretta su un profilo o un messaggio ------------------------------------

/** Sospensione o ripristino diretti di un profilo (codice pubblico) o di un
 *  messaggio (id), senza segnalazione. Le call si gestiscono dalla scheda
 *  «Call». */
function AzioneDiretta({ onAnnuncio }: { onAnnuncio: (t: string) => void }) {
  const nome = useId();
  const sospendi = useSospendiOggetto();
  const ripristina = useRipristinaOggetto();
  const [oggetto, setOggetto] = useState<Exclude<OggettoModerazione, "call">>("profilo");
  const [riferimento, setRiferimento] = useState("");
  const [azione, setAzione] = useState<"sospendi" | "ripristina" | null>(null);
  const mutazione = azione === "ripristina" ? ripristina : sospendi;

  const esegui = (motivazione: string) => {
    if (!azione) return;
    mutazione.mutate(
      { oggetto, id: riferimento.trim(), motivazione },
      {
        onSuccess: (esito) => {
          setAzione(null);
          onAnnuncio(
            esito.modificato
              ? azione === "sospendi"
                ? "Fatto: il contenuto è sospeso."
                : "Fatto: il contenuto è di nuovo visibile."
              : azione === "sospendi"
                ? "Il contenuto era già sospeso: nulla è cambiato."
                : "Il contenuto non era sospeso: nulla è cambiato.",
          );
        },
      },
    );
  };

  const contenuto = (
    <div className="flex flex-col gap-4">
      <SceltaRadio<Exclude<OggettoModerazione, "call">>
        legenda="Contenuto"
        nome={nome}
        valore={oggetto}
        onChange={setOggetto}
        opzioni={[
          { valore: "profilo", etichetta: "Profilo partner", nota: "Indica il codice pubblico del profilo." },
          { valore: "messaggio", etichetta: "Messaggio in chat", nota: "Indica il numero del messaggio." },
        ]}
      />
      <div className="max-w-md">
        <TextField
          label="Riferimento"
          value={riferimento}
          onChange={(e) => setRiferimento(e.target.value)}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          variant="danger"
          size="sm"
          disabled={!riferimento.trim()}
          onClick={() => {
            sospendi.reset();
            setAzione("sospendi");
          }}
        >
          <ShieldOff className="size-4" aria-hidden />
          Sospendi
        </Button>
        <Button
          variant="secondary"
          size="sm"
          disabled={!riferimento.trim()}
          onClick={() => {
            ripristina.reset();
            setAzione("ripristina");
          }}
        >
          Ripristina
        </Button>
      </div>
      <MotivazioneDialog
        open={azione !== null}
        onClose={() => setAzione(null)}
        titolo={azione === "ripristina" ? "Ripristinare il contenuto?" : "Sospendere il contenuto?"}
        descrizione={`${oggetto === "profilo" ? "Profilo" : "Messaggio"} ${riferimento.trim()}`}
        etichetta="Motivazione"
        min={ADMIN_PARTENARIATI_COPY.motivazioneMin}
        max={
          azione === "sospendi"
            ? ADMIN_PARTENARIATI_COPY.motivazioneSospensioneMax
            : ADMIN_PARTENARIATI_COPY.motivazioneMax
        }
        etichettaConferma={azione === "ripristina" ? "Ripristina" : "Sospendi"}
        pericolosa={azione === "sospendi"}
        inCorso={mutazione.isPending}
        errore={mutazione.isError ? apiErrorMessage(mutazione.error) : null}
        onConferma={esegui}
      >
        {azione === "sospendi" && (
          <p className="text-small text-ink-3">
            {ADMIN_PARTENARIATI_COPY.motivazioneSospensioneAiuto}
          </p>
        )}
      </MotivazioneDialog>
    </div>
  );

  return (
    <Accordion
      items={[
        {
          id: "azione-diretta",
          titolo: "Sospendi o ripristina direttamente un profilo o un messaggio",
          children: contenuto,
        },
      ]}
    />
  );
}

// ---- Scheda --------------------------------------------------------------------------

/** Coda delle segnalazioni DSA: presa in carico, decisione motivata con
 *  l'anteprima della motivazione, contesto dei messaggi, ricorsi. */
export function SegnalazioniTab() {
  const [stato, setStato] = useState<FiltroCodaSegnalazioni>("aperte");
  const [page, setPage] = useState(1);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  useEffect(() => setPage(1), [stato]);
  const lista = useAdminSegnalazioni(stato, page);
  const fuoriPagina = useRientroPagina(lista.data, page, lista.isPlaceholderData, setPage);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Filtro<FiltroCodaSegnalazioni>
          etichetta="Stato"
          valore={stato}
          onChange={(v) => setStato(v || "aperte")}
          opzioni={[
            { valore: "aperte", etichetta: "Da gestire" },
            ...STATI.map((s) => ({ valore: s, etichetta: MODERAZIONE_COPY.stati[s] })),
            { valore: "tutte", etichetta: "Tutte" },
          ]}
        />
      </div>
      <Annuncio testo={annuncio} />
      <StatiLista
        isPending={lista.isPending || fuoriPagina}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto="Nessuna segnalazione"
        descrizioneVuoto={
          stato === "aperte"
            ? "Non ci sono segnalazioni o ricorsi da gestire."
            : "Con questo filtro non c'è nulla."
        }
      >
        <ul
          className={cn(
            "flex flex-col gap-3",
            lista.isPlaceholderData && "opacity-60 transition-opacity",
          )}
          aria-busy={lista.isPlaceholderData}
        >
          {lista.data?.items.map((s) => (
            <SegnalazioneCard key={s.id} s={s} onAnnuncio={setAnnuncio} />
          ))}
        </ul>
        {lista.data && (
          <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={setPage} />
        )}
      </StatiLista>
      <AzioneDiretta onAnnuncio={setAnnuncio} />
    </div>
  );
}
