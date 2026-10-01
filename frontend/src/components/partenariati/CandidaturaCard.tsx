import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCandidatura, useDecidiCandidatura, type Decisione } from "../../hooks/useCandidature";
import { apiErrorMessage } from "../../lib/api";
import { etichettaFascia, FASCE_TITOLI, type TipoFascia } from "../../lib/bilanci";
import { CANDIDATURE_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type {
  Candidatura,
  CandidaturaPropria,
  DirezioneCandidature,
  LatoPartenariato,
  ProfiloPartnerCall,
  StatoCandidatura,
  TipoCandidatura,
} from "../../types";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { DefinitionList, type Definizione } from "../ui/Facts";
import { Segment } from "../ui/Segment";
import { Status, type TonoStatus } from "../ui/Status";
import { Skeleton } from "../ui/states";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { BannerIdentitaNonRivelata } from "./BannerIdentitaNonRivelata";
import { TestoLungo } from "./CampiCall";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { MatchSpiegazione } from "./MatchSpiegazione";

/** Motivo del rifiuto: facoltativo, fino a 500 caratteri (come il server). */
const MOTIVO_RIFIUTO_MAX = 500;

const TONI: Record<StatoCandidatura, TonoStatus> = {
  inviata: "in-apertura",
  accettata: "aperto",
  rifiutata: "chiuso",
  ritirata: "chiuso",
  scaduta: "chiuso",
};

/** Bordo sinistro della card nel colore dello stato (lo stesso di `Status`):
 *  in attesa accent, accettata fit, chiusa neutra. La parola sta nello `Status`. */
const BORDO_STATO: Record<StatoCandidatura, string> = {
  inviata: "border-l-4 border-l-accent",
  accettata: "border-l-4 border-l-fit",
  rifiutata: "border-l-4 border-l-line-control",
  ritirata: "border-l-4 border-l-line-control",
  scaduta: "border-l-4 border-l-line-control",
};

/** Stato di una candidatura o di un invito, in parole con il punto di `Status`. */
export function StatoCandidaturaBadge({ stato }: { stato: StatoCandidatura }) {
  return (
    <Status tono={TONI[stato] ?? "neutro"}>
      <span className="sr-only">Stato: </span>
      {CANDIDATURE_COPY.stati[stato] ?? stato}
    </Status>
  );
}

/** La candidatura (o l'invito) è partita dalla tua azienda? Le candidature
 *  le manda l'azienda partner, gli inviti il creatore della call. */
export function mandataDaTe(c: Pick<Candidatura, "tipo" | "lato">): boolean {
  return (c.tipo === "candidatura") === (c.lato === "partner");
}

/** «Candidatura ricevuta», «Invito inviato»… */
export function etichettaTipo(c: Pick<Candidatura, "tipo" | "lato">): string {
  const inviata = mandataDaTe(c);
  if (c.tipo === "invito") return inviata ? "Invito inviato" : "Invito ricevuto";
  return inviata ? "Candidatura inviata" : "Candidatura ricevuta";
}

/** Classe, regione e sezione ATECO del profilo pubblico, come voci separate. */
function descriviProfilo(p: ProfiloPartnerCall): string[] {
  const classe = p.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[p.classe_dimensionale] ?? p.classe_dimensionale)
    : null;
  return [classe, p.regione_sede, p.ateco_sezione?.descrizione ?? null].filter(Boolean) as string[];
}

/** Fasce pubbliche del profilo → tipi di `lib/bilanci` (come l'anteprima
 *  del profilo partner). */
const FASCE_PROFILO: Array<[keyof ProfiloPartnerCall["fasce"], TipoFascia]> = [
  ["fatturato", "fatturato"],
  ["trend", "trend_fatturato"],
  ["patrimonio_netto", "patrimonio_netto"],
  ["dipendenti", "dipendenti"],
];

/** Il profilo pubblico dell'azienda candidata o invitata (la stessa
 *  whitelist dei suggeriti: per un'anonima niente nome, fasce e non importi,
 *  esperienze col solo programma). */
function ProfiloVoci({ profilo }: { profilo: ProfiloPartnerCall }) {
  const fasce = FASCE_PROFILO.map(([chiave, tipo]) => ({
    tipo,
    valore: etichettaFascia(tipo, profilo.fasce?.[chiave] ?? null),
  })).filter((f) => f.valore !== null);
  const competenze = [...profilo.competenze.map((c) => c.etichetta), ...profilo.competenze_libere];
  const voci: Definizione[] = [];
  if (fasce.length > 0) {
    voci.push({
      etichetta: "Dai bilanci (per fasce)",
      valore: (
        <ul className="flex flex-col gap-0.5">
          {fasce.map((f) => (
            <li key={f.tipo}>
              <span className="text-ink-3">{FASCE_TITOLI[f.tipo]}:</span> {f.valore}
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (profilo.tipi_soggetto.length > 0) {
    voci.push({
      etichetta: "Tipo di soggetto",
      valore: profilo.tipi_soggetto
        .map((t) => (t.fonte === "dichiarato" ? `${t.etichetta} (dichiarato)` : t.etichetta))
        .join(", "),
    });
  }
  if (competenze.length > 0) voci.push({ etichetta: "Competenze", valore: competenze.join(", ") });
  if (profilo.descrizione_competenze) {
    voci.push({
      etichetta: "In breve",
      valore: <span className="whitespace-pre-line">{profilo.descrizione_competenze}</span>,
    });
  }
  if (profilo.esperienze.length > 0) {
    voci.push({
      etichetta: "Esperienze",
      valore: (
        <ul className="flex flex-col gap-0.5">
          {profilo.esperienze.map((e, i) => (
            <li key={i}>
              {[e.programma, e.anno ? String(e.anno) : null, e.ruolo, e.titolo].filter(Boolean).join(", ")}
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (profilo.certificazioni.length > 0) {
    voci.push({ etichetta: "Certificazioni", valore: profilo.certificazioni.join(", ") });
  }
  if (profilo.infrastrutture) {
    voci.push({
      etichetta: "Infrastrutture",
      valore: <span className="whitespace-pre-line">{profilo.infrastrutture}</span>,
    });
  }
  return <DefinitionList items={voci} />;
}

/** «Vedi il profilo» per chi ha creato la call: il dettaglio della
 *  candidatura (con il profilo pubblico) si legge solo quando si apre. */
function ProfiloCandidato({ candidaturaId }: { candidaturaId: string }) {
  const [aperto, setAperto] = useState(false);
  const dettaglio = useCandidatura(candidaturaId, aperto);
  const candidato = dettaglio.data?.candidato ?? null;
  let corpo: ReactNode = null;
  if (aperto) {
    if (dettaglio.isPending) {
      corpo = (
        <div className="flex flex-col gap-2" aria-hidden>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      );
    } else if (dettaglio.isError) {
      corpo = <Alert tono="errore">{apiErrorMessage(dettaglio.error, "Impossibile caricare il profilo.")}</Alert>;
    } else if (candidato?.profilo) {
      const descrizione = descriviProfilo(candidato.profilo);
      corpo = (
        <div className="flex flex-col gap-3">
          {descrizione.length > 0 && (
            <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
              {descrizione.map((d) => (
                <span key={d}>{d}</span>
              ))}
            </p>
          )}
          <ProfiloVoci profilo={candidato.profilo} />
        </div>
      );
    } else {
      corpo = <p className="text-body text-ink-3">{CANDIDATURE_COPY.nonPiuDisponibileNota}</p>;
    }
  }
  return (
    <details className="group" onToggle={(e) => setAperto(e.currentTarget.open)}>
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded-mark text-small font-medium text-accent-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        <span className="group-open:hidden">Vedi il profilo</span>
        <span className="hidden group-open:inline">Nascondi il profilo</span>
      </summary>
      <div className="mt-3" aria-live="polite" aria-busy={aperto && dettaglio.isPending}>
        {corpo}
      </div>
    </details>
  );
}

/** Chi c'è dall'altra parte: nome (o «Azienda anonima»), classe, regione,
 *  settore, riferimento. Per il creatore: l'azienda candidata o invitata,
 *  anonima, con il riferimento valido solo per questa call e il profilo
 *  pubblico su richiesta («Vedi il profilo»: lo porta solo il dettaglio); se
 *  ha tolto la visibilità, «non più disponibile» e nessun dato. Per
 *  l'azienda partner: chi ha creato la call, anonima (il server non manda
 *  nulla di lei). */
function Controparte({ c, comeTitolo }: { c: Candidatura; comeTitolo: boolean }) {
  // Una funzione che restituisce l'elemento, non un componente definito qui
  // dentro (che React smonterebbe e rimonterebbe a ogni render).
  const nome = (testo: ReactNode) =>
    comeTitolo ? (
      <h3 className="font-sans text-row-title text-ink">{testo}</h3>
    ) : (
      <p className="font-medium text-ink">{testo}</p>
    );

  if (c.lato === "partner") {
    return (
      <div className="flex flex-col gap-0.5">
        {nome(PARTNER_COPY.aziendaAnonima)}
        <p className="text-small text-ink-2">Ha creato la call</p>
      </div>
    );
  }
  const candidato = c.candidato;
  if (candidato && !candidato.disponibile) {
    return (
      <div className="flex flex-col gap-0.5">
        {nome(CANDIDATURE_COPY.nonPiuDisponibile)}
        <p className="text-small text-ink-3">{CANDIDATURE_COPY.nonPiuDisponibileNota}</p>
      </div>
    );
  }
  const profilo = candidato?.profilo ?? null;
  const descrizione = profilo ? descriviProfilo(profilo) : [];
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      {nome(profilo?.denominazione ?? PARTNER_COPY.aziendaAnonima)}
      {descrizione.length > 0 && (
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
          {descrizione.map((d) => (
            <span key={d}>{d}</span>
          ))}
        </p>
      )}
      {candidato?.pseudonimo && (
        <p className="text-small text-ink-3">Riferimento per questa call: {candidato.pseudonimo}</p>
      )}
    </div>
  );
}

type EsitoDati = Pick<
  Candidatura,
  "tipo" | "stato" | "scade_at" | "motivo_rifiuto" | "motivo_chiusura" | "decisa_at"
> & { chiusa_at?: string | null };

/** Esito leggibile di una candidatura chiusa o ancora in attesa. */
function Esito({ c }: { c: EsitoDati }) {
  if (c.stato === "inviata") {
    if (c.tipo === "invito" && c.scade_at) {
      return (
        <p className="text-small text-ink-3">
          Se nessuno risponde, l'invito scade il {formatDate(c.scade_at)}.
        </p>
      );
    }
    return null;
  }
  const quando = c.decisa_at ?? c.chiusa_at;
  const righe: string[] = [];
  if (c.stato === "rifiutata" && c.motivo_rifiuto) righe.push(`Motivo: ${c.motivo_rifiuto}`);
  if (c.motivo_chiusura) righe.push(CANDIDATURE_COPY.motiviChiusura[c.motivo_chiusura] ?? "");
  // `ritirata` senza motivo: l'ha ritirata chi l'aveva mandata.
  if (quando) righe.push(`${CANDIDATURE_COPY.stati[c.stato]} il ${formatDate(quando)}.`);
  if (righe.filter(Boolean).length === 0) return null;
  return (
    <div className="flex flex-col gap-0.5 text-small text-ink-2">
      {righe.filter(Boolean).map((r) => (
        <p key={r} className="whitespace-pre-line">
          {r}
        </p>
      ))}
    </div>
  );
}

/** Scelta tra le candidature ricevute e quelle mandate: un `Segment`, lo
 *  stato lo tiene il chiamante (searchParams). */
export function SceltaDirezione({
  valore,
  onChange,
  etichette,
}: {
  valore: DirezioneCandidature;
  onChange: (d: DirezioneCandidature) => void;
  etichette: Record<DirezioneCandidature, string>;
}) {
  const direzioni: DirezioneCandidature[] = ["ricevute", "inviate"];
  return (
    <Segment
      ariaLabel="Quali mostrare"
      opzioni={direzioni.map((d) => ({ id: d, label: etichette[d] }))}
      valore={valore}
      onChange={onChange}
    />
  );
}

// ---- Decisione (accetta, rifiuta, ritira) ------------------------------------------

/** Quanto serve per decidere: la riga, chi guarda e la call. */
export interface DecisioneSu {
  id: string;
  tipo: TipoCandidatura;
  lato: LatoPartenariato;
  callId: string;
}

function titoloDecisione(c: DecisioneSu, decisione: Decisione): string {
  const oggetto = c.tipo === "invito" ? "l'invito" : "la candidatura";
  if (decisione === "accetta") return `Accettare ${oggetto}?`;
  if (decisione === "rifiuta") return `Rifiutare ${oggetto}?`;
  return `Ritirare ${oggetto}?`;
}

function testoDecisione(c: DecisioneSu, decisione: Decisione): string {
  if (decisione === "accetta") {
    return c.lato === "creatore"
      ? "Si apre una conversazione con l'azienda. Da quel momento vede anche i dettagli riservati e il budget esatto della call."
      : "Si apre una conversazione con l'azienda che ha creato la call e vedi anche i dettagli riservati e il budget esatto della call.";
  }
  if (decisione === "rifiuta") {
    return "L'altra azienda vede che hai rifiutato e, se lo scrivi, il motivo. Non si può annullare.";
  }
  return c.tipo === "invito"
    ? "L'azienda non potrà più accettare l'invito."
    : "Chi ha creato la call non la vedrà più tra quelle da decidere. Resta comunque tra le candidature usate questo mese.";
}

/** Conferma di una decisione; dopo un'accettazione si va alla conversazione.
 *  Resta su `Dialog` (non `ConfirmDialog`) perché il rifiuto ha un campo:
 *  i pulsanti hanno `onClick` esplicito. */
export function DecisioneDialog({
  su,
  decisione,
  onClose,
}: {
  su: DecisioneSu;
  decisione: Decisione | null;
  onClose: () => void;
}) {
  const decidi = useDecidiCandidatura();
  const navigate = useNavigate();
  const [motivo, setMotivo] = useState("");
  const aperto = decisione !== null;

  // A ogni apertura si riparte da zero.
  useEffect(() => {
    if (!aperto) return;
    setMotivo("");
    decidi.reset();
    // Solo all'apertura (`decidi` cambia a ogni render).
  }, [aperto]);

  const conferma = () => {
    if (!decisione) return;
    decidi.mutate(
      { id: su.id, callId: su.callId, decisione, motivo: decisione === "rifiuta" ? motivo : null },
      {
        onSuccess: (dati) => {
          onClose();
          if (decisione === "accetta" && dati?.conversazione_id) {
            navigate(`/app/partenariati/conversazioni/${dati.conversazione_id}`, {
              state: { annuncio: "Accettata: la conversazione è aperta." },
            });
          }
        },
      },
    );
  };

  return (
    <Dialog
      open={aperto}
      onClose={onClose}
      dismissible={!decidi.isPending}
      title={decisione ? titoloDecisione(su, decisione) : ""}
      footer={
        decisione ? (
          <>
            <Button type="button" variant="secondary" onClick={onClose} disabled={decidi.isPending}>
              Non ora
            </Button>
            <Button
              type="button"
              variant={decisione === "accetta" ? "primary" : "danger"}
              loading={decidi.isPending}
              onClick={conferma}
            >
              {decisione === "accetta" ? "Accetta" : decisione === "rifiuta" ? "Rifiuta" : "Ritira"}
            </Button>
          </>
        ) : undefined
      }
    >
      {decisione && (
        <div className="flex flex-col gap-3">
          <p>{testoDecisione(su, decisione)}</p>
          {decisione === "accetta" && <BannerIdentitaNonRivelata />}
          {decisione === "rifiuta" && (
            <TestoLungo
              etichetta="Motivo (facoltativo)"
              aiuto="Lo legge l'altra azienda: niente contatti né dati riservati."
              valore={motivo}
              onChange={setMotivo}
              massimo={MOTIVO_RIFIUTO_MAX}
              righe={3}
            />
          )}
          {decidi.isError && <Alert tono="errore">{apiErrorMessage(decidi.error)}</Alert>}
        </div>
      )}
    </Dialog>
  );
}

/** Azioni e finestra di conferma: «Apri la conversazione» dopo
 *  l'accettazione (anche per chi legge soltanto); «Accetta» (secondaria) e
 *  «Rifiuta» (testuale) per chi riceve, «Ritira» per chi ha mandato, solo se
 *  ammesso (titolare, in attesa). */
function Azioni({
  su,
  conversazioneId,
  accettata,
  puoDecidere,
  puoRitirare,
}: {
  su: DecisioneSu;
  conversazioneId: string | null;
  accettata: boolean;
  puoDecidere: boolean;
  puoRitirare: boolean;
}) {
  const [decisione, setDecisione] = useState<Decisione | null>(null);
  if (!(accettata && conversazioneId) && !puoDecidere && !puoRitirare) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {accettata && conversazioneId && (
        <LinkButton to={`/app/partenariati/conversazioni/${conversazioneId}`} variant="ghost" size="sm">
          Apri la conversazione
        </LinkButton>
      )}
      {puoDecidere && (
        <>
          <Button size="sm" variant="secondary" onClick={() => setDecisione("accetta")}>
            Accetta
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setDecisione("rifiuta")}>
            Rifiuta
          </Button>
        </>
      )}
      {puoRitirare && (
        <Button size="sm" variant="ghost" onClick={() => setDecisione("ritira")}>
          {su.tipo === "invito" ? "Ritira l'invito" : "Ritira la candidatura"}
        </Button>
      )}
      <DecisioneDialog su={su} decisione={decisione} onClose={() => setDecisione(null)} />
    </div>
  );
}

/** La propria candidatura (o l'invito ricevuto) nella pagina di una call di
 *  altri (`CandidaturaPropriaOut`): tipo, stato, posizione, esito e le azioni
 *  che il server ammette per il titolare. Senza riquadro: sta nel pannello
 *  della colonna laterale. */
export function CandidaturaPropriaCard({
  candidatura: c,
  callId,
  posizioni,
}: {
  candidatura: CandidaturaPropria;
  callId: string;
  posizioni: ReadonlyArray<{ id: string; titolo: string }>;
}) {
  const posizione = c.posizione_id ? posizioni.find((p) => p.id === c.posizione_id) : undefined;
  const su: DecisioneSu = { id: c.id, tipo: c.tipo, lato: "partner", callId };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1">
        <p className="text-small text-ink-2">{etichettaTipo(su)}</p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <StatoCandidaturaBadge stato={c.stato} />
          {c.created_at && (
            <span className="text-small text-ink-3">
              <span className="sr-only">Data: </span>
              {formatDate(c.created_at)}
            </span>
          )}
        </div>
      </div>
      {posizione && (
        <p className="text-body text-ink">
          <span className="text-ink-3">Posizione: </span>
          {posizione.titolo}
        </p>
      )}
      <Esito c={c} />
      <Azioni
        su={su}
        conversazioneId={c.conversazione_id}
        accettata={c.stato === "accettata"}
        puoDecidere={c.puo_decidere}
        puoRitirare={c.puo_ritirare}
      />
    </div>
  );
}

// ---- Riga ---------------------------------------------------------------------------

/** Una candidatura o un invito, visto dalla tua parte, come card (bordo
 *  sinistro nel colore dello stato): chi c'è
 *  dall'altra parte (sempre anonimo), la call (se `mostraCall`), posizione,
 *  requisiti dichiarati, per il creatore il confronto in vista «terzi» (solo
 *  esiti e fasce), messaggio; a destra lo stato, la data, l'esito e le
 *  azioni. Le azioni le ammette il server (solo il titolare, solo in
 *  attesa): chi riceve accetta o rifiuta, chi ha mandato ritira; dopo
 *  l'accettazione tutti aprono la conversazione. La riga è un `<li>`. */
export function CandidaturaCard({
  candidatura: c,
  mostraCall = true,
  testi,
}: {
  candidatura: Candidatura;
  mostraCall?: boolean;
  /** Testo dei requisiti per etichetta, quando la call è nota (creatore). */
  testi?: ReadonlyMap<string, string>;
}) {
  const tua = mandataDaTe(c);
  const puoVedereProfilo =
    c.lato === "creatore" && !c.candidato?.profilo && c.candidato?.disponibile !== false;

  const voci: Definizione[] = [];
  if (c.posizione) voci.push({ etichetta: "Posizione", valore: c.posizione.titolo });
  if (c.requisiti_dichiarati.length > 0) {
    voci.push({
      etichetta: "Requisiti dichiarati",
      valore: (
        <ul className="flex flex-col gap-1">
          {c.requisiti_dichiarati.map((r) => (
            <li key={r.requisito_id} className="flex flex-wrap items-start gap-1.5">
              <Badge className="shrink-0 tabular-nums">{r.etichetta}</Badge>
              {testi?.get(r.etichetta) && <span className="min-w-0">{testi.get(r.etichetta)}</span>}
              <span className="text-small text-ink-3">({CANDIDATURE_COPY.dichiarato})</span>
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (c.lato === "creatore" && c.valutazione) {
    voci.push({
      etichetta: "Confronto",
      valore: (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <MatchBadge match={c.valutazione} persona="lei" />
            <AttenzioneBadge match={c.valutazione} />
          </div>
          <MatchSpiegazione match={c.valutazione} persona="lei" testi={testi} compatta />
        </div>
      ),
    });
  }
  if (c.messaggio) {
    voci.push({
      etichetta: tua ? "Il tuo messaggio" : "Messaggio",
      valore: <span className="block whitespace-pre-line break-words">{c.messaggio}</span>,
    });
  }

  return (
    <li>
      <Card className={`flex flex-col gap-4 md:flex-row md:gap-6 ${BORDO_STATO[c.stato] ?? ""}`}>
        <div className="flex min-w-0 grow flex-col gap-3">
          {mostraCall ? (
            <div className="flex flex-col gap-1">
              <h3 className="font-sans text-row-title text-ink">
                <Link to={`/app/partenariati/call/${c.call.id}`} className="rounded-mark hover:text-accent-hover">
                  {c.call.titolo || "Call senza titolo"}
                </Link>
              </h3>
              <p className="text-body text-ink-2">Bando: {c.call.bando.titolo}</p>
              <Controparte c={c} comeTitolo={false} />
            </div>
          ) : (
            <Controparte c={c} comeTitolo />
          )}

          {c.lato === "creatore" && c.compatibile === false && c.candidato?.disponibile !== false && (
            <p className="text-small text-ink-3">
              Quando è arrivata, l'azienda non risultava compatibile con i requisiti o le posizioni
              della call: valuta tu dal messaggio.
            </p>
          )}

          {voci.length > 0 && <DefinitionList items={voci} />}

          {puoVedereProfilo && <ProfiloCandidato candidaturaId={c.id} />}
        </div>

        <div className="flex shrink-0 flex-col items-start gap-3 md:w-48">
          <div className="flex flex-col gap-1">
            <p className="text-small text-ink-2">{etichettaTipo(c)}</p>
            <StatoCandidaturaBadge stato={c.stato} />
            {c.created_at && (
              <p className="text-small text-ink-3">
                <span className="sr-only">Data: </span>
                {tua ? "mandata il" : "dal"} {formatDate(c.created_at)}
              </p>
            )}
          </div>
          <Esito c={c} />
          <Azioni
            su={{ id: c.id, tipo: c.tipo, lato: c.lato, callId: c.call.id }}
            conversazioneId={c.conversazione_id}
            accettata={c.stato === "accettata"}
            puoDecidere={c.puo_decidere}
            puoRitirare={c.puo_ritirare}
          />
        </div>
      </Card>
    </li>
  );
}
