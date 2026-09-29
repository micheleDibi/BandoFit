import { AlertTriangle, Briefcase, Check, Lock, X } from "lucide-react";
import { useId, type ReactNode } from "react";
import { etichettaFascia, FASCE_TITOLI, type TipoFascia } from "../../lib/bilanci";
import { BACHECA_COPY } from "../../lib/copy";
import type { MatchFasce, MatchOut, MatchRequisito } from "../../types";
import { Badge } from "../ui/Badge";
import type { PersonaMatch } from "./MatchBadge";

/** Fasce del confronto → tipi di `lib/bilanci` (lì l'andamento si chiama
 *  `trend_fatturato`). */
const FASCE: Array<[keyof MatchFasce, TipoFascia]> = [
  ["fatturato", "fatturato"],
  ["trend", "trend_fatturato"],
  ["patrimonio_netto", "patrimonio_netto"],
  ["dipendenti", "dipendenti"],
];

function Gruppo({ titolo, children }: { titolo: string; children: ReactNode }) {
  const id = useId();
  return (
    <div>
      <p id={id} className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {titolo}
      </p>
      <ul aria-labelledby={id} className="mt-1.5 space-y-1">
        {children}
      </ul>
    </div>
  );
}

function Requisito({
  voce,
  testi,
  icona,
}: {
  voce: MatchRequisito;
  testi?: ReadonlyMap<string, string>;
  icona: ReactNode;
}) {
  const testo = testi?.get(voce.etichetta);
  return (
    <li className="flex items-start gap-2 text-sm text-slate-700">
      {icona}
      <Badge tone="brand" className="shrink-0 tabular">
        {voce.etichetta}
      </Badge>
      {testo && <span className="min-w-0">{testo}</span>}
    </li>
  );
}

function Dettagli({
  match,
  persona,
  testi,
}: {
  match: MatchOut;
  persona: PersonaMatch;
  testi?: ReadonlyMap<string, string>;
}) {
  const tu = persona === "tu";
  const fasce = match.fasce
    ? FASCE.map(([chiave, tipo]) => ({ tipo, valore: etichettaFascia(tipo, match.fasce?.[chiave] ?? null) })).filter(
        (f) => f.valore !== null,
      )
    : [];
  const dettaglio = tu ? (match.dettaglio ?? []) : [];

  return (
    <div className="space-y-3">
      {match.copre.length > 0 && (
        <Gruppo titolo={tu ? "Requisiti cercati che copri" : "Requisiti cercati che copre"}>
          {match.copre.map((r) => (
            <Requisito
              key={r.requisito_id}
              voce={r}
              testi={testi}
              icona={<Check className="mt-0.5 size-4 shrink-0 text-emerald-600" aria-hidden />}
            />
          ))}
        </Gruppo>
      )}
      {match.non_copre.length > 0 && (
        <Gruppo titolo={tu ? "Requisiti cercati che non copri" : "Requisiti cercati che non copre"}>
          {match.non_copre.map((r) => (
            <Requisito
              key={r.requisito_id}
              voce={r}
              testi={testi}
              icona={<X className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />}
            />
          ))}
        </Gruppo>
      )}
      {match.attenzione.length > 0 && (
        <div>
          <Gruppo titolo="Da verificare">
            {match.attenzione.map((a, i) => (
              <li key={`${a.codice}-${i}`} className="flex items-start gap-2 text-sm text-slate-700">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
                <span className="min-w-0">{a.testo}</span>
              </li>
            ))}
          </Gruppo>
          <p className="mt-1 text-xs text-slate-500">{BACHECA_COPY.daVerificareNota}</p>
        </div>
      )}
      {match.posizioni_compatibili.length > 0 && (
        <Gruppo titolo={tu ? "Posizioni adatte alla tua azienda" : "Posizioni adatte"}>
          {match.posizioni_compatibili.map((p) => (
            <li key={p.id} className="flex items-start gap-2 text-sm text-slate-700">
              <Briefcase className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
              <span className="min-w-0">{p.titolo}</span>
            </li>
          ))}
        </Gruppo>
      )}
      {!tu && fasce.length > 0 && (
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
            Fasce di bilancio
          </p>
          <dl className="mt-1.5 grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
            {fasce.map((f) => (
              <div key={f.tipo}>
                <dt className="inline text-slate-500">{FASCE_TITOLI[f.tipo]}: </dt>
                <dd className="inline text-slate-700">{f.valore}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {dettaglio.length > 0 && (
        <Gruppo titolo="I tuoi numeri (li vedi solo tu)">
          {dettaglio.map((riga, i) => (
            <li key={i} className="flex items-start gap-2 text-sm text-slate-700">
              <Lock className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
              <span className="min-w-0">{riga}</span>
            </li>
          ))}
        </Gruppo>
      )}
    </div>
  );
}

function elenco(etichette: string[]): string {
  const voci = etichette.map((e) => `«${e}»`);
  return voci.length === 1 ? voci[0] : `${voci.slice(0, -1).join(", ")} e ${voci[voci.length - 1]}`;
}

/** La frase del server parla all'azienda che si confronta con la call
 *  («Copri «A» e «C», che mancano al capofila.»). Verso il proponente
 *  (persona «lei») si ricompone in terza persona dagli stessi dati: chi legge
 *  è il capofila (o il proponente), non l'azienda suggerita. */
export function fraseConfronto(match: MatchOut, persona: PersonaMatch): string {
  if (persona === "tu") return match.spiegazione;
  if (match.copre.length > 0) {
    const verbo = match.copre.length === 1 ? "manca" : "mancano";
    return `Copre ${elenco(match.copre.map((r) => r.etichetta))}, che ${verbo} alla tua call.`;
  }
  if (match.spiegazione.startsWith("Corrispondi ")) {
    return `Corrisponde ${match.spiegazione.slice("Corrispondi ".length)}`;
  }
  return "È compatibile con la call, ma non copre requisiti cercati.";
}

/** Spiegazione del confronto tra una call e un'azienda: la frase del server
 *  (template fisso, niente AI; in terza persona verso il proponente,
 *  `fraseConfronto`) e le voci con icona **e** testo: requisiti
 *  cercati coperti e non coperti, voci da verificare, posizioni adatte, fasce
 *  (solo verso il proponente) e i propri numeri (solo per sé). `testi`
 *  aggiunge il testo dei requisiti per etichetta, quando la pagina lo ha.
 *  `compatta`: le voci stanno in un `<details>` chiuso (card delle liste). */
export function MatchSpiegazione({
  match,
  persona = "tu",
  testi,
  compatta = false,
}: {
  match: MatchOut;
  persona?: PersonaMatch;
  testi?: ReadonlyMap<string, string>;
  compatta?: boolean;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm text-slate-700">{fraseConfronto(match, persona)}</p>
      {compatta ? (
        <details className="group">
          <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded text-sm font-medium text-brand-600 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
            <span className="group-open:hidden">Dettagli del confronto</span>
            <span className="hidden group-open:inline">Nascondi i dettagli</span>
          </summary>
          <div className="mt-3">
            <Dettagli match={match} persona={persona} testi={testi} />
          </div>
        </details>
      ) : (
        <Dettagli match={match} persona={persona} testi={testi} />
      )}
    </div>
  );
}
