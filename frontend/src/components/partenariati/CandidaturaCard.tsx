import {
  Building2,
  CalendarX,
  Check,
  Clock,
  EyeOff,
  MessagesSquare,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useState, type ComponentProps, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCandidatura, useDecidiCandidatura, type Decisione } from "../../hooks/useCandidature";
import { apiErrorMessage } from "../../lib/api";
import { etichettaFascia, FASCE_TITOLI, type TipoFascia } from "../../lib/bilanci";
import { CANDIDATURE_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import { cn } from "../../lib/cn";
import type {
  Candidatura,
  CandidaturaPropria,
  DirezioneCandidature,
  LatoPartenariato,
  ProfiloPartnerCall,
  StatoCandidatura,
  TipoCandidatura,
} from "../../types";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { BannerIdentitaNonRivelata } from "./BannerIdentitaNonRivelata";
import { TestoLungo } from "./CampiCall";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { MatchSpiegazione } from "./MatchSpiegazione";

/** Motivo del rifiuto: facoltativo, fino a 500 caratteri (come il server). */
const MOTIVO_RIFIUTO_MAX = 500;

const TONI: Record<StatoCandidatura, ComponentProps<typeof Badge>["tone"]> = {
  inviata: "amber",
  accettata: "emerald",
  rifiutata: "red",
  ritirata: "slate",
  scaduta: "slate",
};
const ICONE: Record<StatoCandidatura, typeof Check> = {
  inviata: Clock,
  accettata: Check,
  rifiutata: X,
  ritirata: Undo2,
  scaduta: CalendarX,
};

/** Stato di una candidatura o di un invito: icona **e** testo. */
export function StatoCandidaturaBadge({ stato }: { stato: StatoCandidatura }) {
  const Icona = ICONE[stato] ?? Clock;
  return (
    <Badge tone={TONI[stato] ?? "slate"}>
      <Icona className="size-3.5" aria-hidden />
      <span className="sr-only">Stato: </span>
      {CANDIDATURE_COPY.stati[stato] ?? stato}
    </Badge>
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

function descriviProfilo(p: ProfiloPartnerCall): string {
  const classe = p.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[p.classe_dimensionale] ?? p.classe_dimensionale)
    : null;
  return [
    classe,
    p.regione_sede,
    p.ateco_sezione ? `${p.ateco_sezione.lettera} — ${p.ateco_sezione.descrizione}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
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
  return (
    <dl className="space-y-3">
      {fasce.length > 0 && (
        <Voce titolo="Dai bilanci (per fasce)">
          <ul className="space-y-0.5">
            {fasce.map((f) => (
              <li key={f.tipo}>
                <span className="text-slate-500">{FASCE_TITOLI[f.tipo]}:</span> {f.valore}
              </li>
            ))}
          </ul>
        </Voce>
      )}
      {profilo.tipi_soggetto.length > 0 && (
        <Voce titolo="Tipo di soggetto">
          {profilo.tipi_soggetto
            .map((t) => (t.fonte === "dichiarato" ? `${t.etichetta} (dichiarato)` : t.etichetta))
            .join(", ")}
        </Voce>
      )}
      {competenze.length > 0 && <Voce titolo="Competenze">{competenze.join(", ")}</Voce>}
      {profilo.descrizione_competenze && (
        <Voce titolo="In breve">
          <span className="whitespace-pre-line">{profilo.descrizione_competenze}</span>
        </Voce>
      )}
      {profilo.esperienze.length > 0 && (
        <Voce titolo="Esperienze">
          <ul className="list-disc space-y-0.5 pl-5">
            {profilo.esperienze.map((e, i) => (
              <li key={i}>
                {[e.programma, e.anno ? String(e.anno) : null, e.ruolo, e.titolo]
                  .filter(Boolean)
                  .join(" · ")}
              </li>
            ))}
          </ul>
        </Voce>
      )}
      {profilo.certificazioni.length > 0 && (
        <Voce titolo="Certificazioni">{profilo.certificazioni.join(", ")}</Voce>
      )}
      {profilo.infrastrutture && (
        <Voce titolo="Infrastrutture">
          <span className="whitespace-pre-line">{profilo.infrastrutture}</span>
        </Voce>
      )}
    </dl>
  );
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
        <div className="space-y-2" aria-hidden>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      );
    } else if (dettaglio.isError) {
      corpo = (
        <p className="text-sm text-red-700" role="alert">
          {apiErrorMessage(dettaglio.error, "Impossibile caricare il profilo.")}
        </p>
      );
    } else if (candidato?.profilo) {
      const descrizione = descriviProfilo(candidato.profilo);
      corpo = (
        <div className="space-y-3">
          {descrizione && <p className="text-xs text-slate-500">{descrizione}</p>}
          <ProfiloVoci profilo={candidato.profilo} />
        </div>
      );
    } else {
      corpo = <p className="text-sm text-slate-500">{CANDIDATURE_COPY.nonPiuDisponibileNota}</p>;
    }
  }
  return (
    <details className="group mt-2" onToggle={(e) => setAperto(e.currentTarget.open)}>
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded text-sm font-medium text-brand-600 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
        <span className="group-open:hidden">Vedi il profilo</span>
        <span className="hidden group-open:inline">Nascondi il profilo</span>
      </summary>
      <div className="mt-3" aria-live="polite" aria-busy={aperto && dettaglio.isPending}>
        {corpo}
      </div>
    </details>
  );
}

/** Chi c'è dall'altra parte. Per il creatore: l'azienda candidata o invitata,
 *  anonima, con il riferimento valido solo per questa call e il profilo
 *  pubblico su richiesta («Vedi il profilo»: lo porta solo il dettaglio); se
 *  ha tolto la visibilità, «non più disponibile» e nessun dato. Per
 *  l'azienda partner: chi ha creato la call, anonima (il server non manda
 *  nulla di lei). */
function Controparte({ c }: { c: Candidatura }) {
  if (c.lato === "partner") {
    return (
      <div className="flex items-start gap-2">
        <Building2 className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
        <p className="text-sm text-slate-700">
          <span className="font-medium text-slate-900">{PARTNER_COPY.aziendaAnonima}</span>
          <span className="text-slate-500"> — ha creato la call</span>
        </p>
      </div>
    );
  }
  const candidato = c.candidato;
  if (candidato && !candidato.disponibile) {
    return (
      <div className="flex items-start gap-2">
        <EyeOff className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
        <div>
          <p className="text-sm font-medium text-slate-700">{CANDIDATURE_COPY.nonPiuDisponibile}</p>
          <p className="text-xs text-slate-500">{CANDIDATURE_COPY.nonPiuDisponibileNota}</p>
        </div>
      </div>
    );
  }
  const profilo = candidato?.profilo ?? null;
  const descrizione = profilo ? descriviProfilo(profilo) : "";
  return (
    <div className="flex items-start gap-2">
      <EyeOff className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-900">
          {profilo?.denominazione ?? PARTNER_COPY.aziendaAnonima}
        </p>
        {descrizione && <p className="text-xs text-slate-500">{descrizione}</p>}
        {candidato?.pseudonimo && (
          <p className="text-xs text-slate-400">
            Riferimento per questa call:{" "}
            <span className="font-mono tracking-wide">{candidato.pseudonimo}</span>
          </p>
        )}
        {!profilo && candidato?.disponibile && <ProfiloCandidato candidaturaId={c.id} />}
      </div>
    </div>
  );
}

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
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
      return <p className="text-xs text-slate-500">Se nessuno risponde, l'invito scade il {formatDate(c.scade_at)}.</p>;
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
    <div className="space-y-0.5 text-xs text-slate-600">
      {righe.filter(Boolean).map((r) => (
        <p key={r} className="whitespace-pre-line">
          {r}
        </p>
      ))}
    </div>
  );
}

/** Scelta tra le candidature ricevute e quelle mandate: due bottoni
 *  (`aria-pressed`), lo stato lo tiene il chiamante (searchParams). */
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
    <div role="group" aria-label="Quali mostrare" className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
      {direzioni.map((d) => (
        <button
          key={d}
          type="button"
          aria-pressed={valore === d}
          onClick={() => onChange(d)}
          className={cn(
            "cursor-pointer rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-brand-500",
            valore === d ? "bg-white text-brand-700 shadow-sm" : "text-slate-600 hover:text-slate-900",
          )}
        >
          {etichette[d]}
        </button>
      ))}
    </div>
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

/** Conferma di una decisione; dopo un'accettazione si va alla conversazione. */
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
            <Button variant="ghost" onClick={onClose} disabled={decidi.isPending}>
              Non ora
            </Button>
            <Button
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
        <div className="space-y-3">
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
          {decidi.isError && (
            <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
              {apiErrorMessage(decidi.error)}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** Bottoni delle azioni e dialog di conferma: «Apri la conversazione» dopo
 *  l'accettazione (anche per chi legge soltanto); «Accetta»/«Rifiuta» per chi
 *  riceve, «Ritira» per chi ha mandato, solo se ammesso (titolare, in attesa). */
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
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {accettata && conversazioneId && (
        <LinkButton to={`/app/partenariati/conversazioni/${conversazioneId}`} size="sm">
          <MessagesSquare className="size-4" aria-hidden />
          Apri la conversazione
        </LinkButton>
      )}
      {puoDecidere && (
        <>
          <Button size="sm" onClick={() => setDecisione("accetta")}>
            <Check className="size-4" aria-hidden />
            Accetta
          </Button>
          <Button size="sm" variant="secondary" onClick={() => setDecisione("rifiuta")}>
            <X className="size-4" aria-hidden />
            Rifiuta
          </Button>
        </>
      )}
      {puoRitirare && (
        <Button size="sm" variant="ghost" onClick={() => setDecisione("ritira")}>
          <Undo2 className="size-4" aria-hidden />
          {su.tipo === "invito" ? "Ritira l'invito" : "Ritira la candidatura"}
        </Button>
      )}
      <DecisioneDialog su={su} decisione={decisione} onClose={() => setDecisione(null)} />
    </div>
  );
}

/** La propria candidatura (o l'invito ricevuto) nella pagina di una call di
 *  altri (`CandidaturaPropriaOut`): tipo, stato, posizione, esito e le azioni
 *  che il server ammette per il titolare. */
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
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={c.tipo === "invito" ? "brand" : "slate"}>{etichettaTipo(su)}</Badge>
        <StatoCandidaturaBadge stato={c.stato} />
        {c.created_at && (
          <span className="text-xs text-slate-500">
            <span className="sr-only">Data: </span>
            {formatDate(c.created_at)}
          </span>
        )}
      </div>
      {posizione && (
        <p className="mt-2 text-sm text-slate-700">
          <span className="text-slate-500">Posizione: </span>
          {posizione.titolo}
        </p>
      )}
      <div className="mt-2">
        <Esito c={c} />
      </div>
      <Azioni
        su={su}
        conversazioneId={c.conversazione_id}
        accettata={c.stato === "accettata"}
        puoDecidere={c.puo_decidere}
        puoRitirare={c.puo_ritirare}
      />
    </Card>
  );
}

// ---- Card ---------------------------------------------------------------------------

/** Una candidatura o un invito, visto dalla tua parte: chi c'è dall'altra
 *  parte (sempre anonimo), la call (se `mostraCall`), posizione, messaggio,
 *  requisiti dichiarati, per il creatore il confronto in vista «terzi» (solo
 *  esiti e fasce), l'esito. Le azioni le ammette il server (solo il
 *  titolare, solo in attesa): chi riceve accetta o rifiuta, chi ha mandato
 *  ritira; dopo l'accettazione tutti aprono la conversazione. La card è un
 *  `<li>`. */
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

  return (
    <li>
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={c.tipo === "invito" ? "brand" : "slate"}>{etichettaTipo(c)}</Badge>
          <StatoCandidaturaBadge stato={c.stato} />
          {c.created_at && (
            <span className="text-xs text-slate-500">
              <span className="sr-only">Data: </span>
              {formatDate(c.created_at)}
            </span>
          )}
        </div>

        {mostraCall && (
          <h3 className="mt-2 font-display text-base font-semibold text-slate-900">
            <Link
              to={`/app/partenariati/call/${c.call.id}`}
              className="rounded hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
            >
              {c.call.titolo || "Call senza titolo"}
            </Link>
            <span className="block text-sm font-normal text-slate-600">Bando: {c.call.bando.titolo}</span>
          </h3>
        )}

        <div className="mt-3">
          <Controparte c={c} />
        </div>

        {c.lato === "creatore" && c.valutazione && (
          <div className="mt-3 space-y-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <MatchBadge match={c.valutazione} persona="lei" />
              <AttenzioneBadge match={c.valutazione} />
            </div>
            <MatchSpiegazione match={c.valutazione} persona="lei" testi={testi} compatta />
          </div>
        )}
        {c.lato === "creatore" && c.compatibile === false && c.candidato?.disponibile !== false && (
          <p className="mt-3 text-xs text-slate-500">
            Quando è arrivata, l'azienda non risultava compatibile con i requisiti o le
            posizioni della call: valuta tu dal messaggio.
          </p>
        )}

        <dl className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          {c.posizione && <Voce titolo="Posizione">{c.posizione.titolo}</Voce>}
          {c.messaggio && (
            <Voce titolo={tua ? "Il tuo messaggio" : "Messaggio"}>
              <span className="block whitespace-pre-line break-words">{c.messaggio}</span>
            </Voce>
          )}
          {c.requisiti_dichiarati.length > 0 && (
            <Voce titolo="Requisiti dichiarati">
              <ul className="space-y-1">
                {c.requisiti_dichiarati.map((r) => (
                  <li key={r.requisito_id} className="flex flex-wrap items-start gap-1.5">
                    <Badge tone="brand" className="shrink-0 tabular">
                      {r.etichetta}
                    </Badge>
                    {testi?.get(r.etichetta) && <span className="min-w-0">{testi.get(r.etichetta)}</span>}
                    <Badge tone="slate">{CANDIDATURE_COPY.dichiarato}</Badge>
                  </li>
                ))}
              </ul>
            </Voce>
          )}
        </dl>

        <div className="mt-3">
          <Esito c={c} />
        </div>

        <Azioni
          su={{ id: c.id, tipo: c.tipo, lato: c.lato, callId: c.call.id }}
          conversazioneId={c.conversazione_id}
          accettata={c.stato === "accettata"}
          puoDecidere={c.puo_decidere}
          puoRitirare={c.puo_ritirare}
        />
      </Card>
    </li>
  );
}
