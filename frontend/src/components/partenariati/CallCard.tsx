import { Link } from "react-router-dom";
import { cn } from "../../lib/cn";
import { CALL_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { CallBacheca } from "../../types";
import { Due } from "../ui/Due";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { linkCall } from "./callDati";
import { CallStatoBadge } from "./CallStatoBadge";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { SalvaCallButton } from "./SalvaCallButton";

function conteggio(n: number, uno: string, molti: string) {
  return `${n} ${n === 1 ? uno : molti}`;
}

/** Una call nella bacheca, in «Per te» e tra le salvate, come riga del
 *  registro: la scadenza del bando (`Due`), titolo, bando, che cosa cerca
 *  (ruolo, posti, budget), date; a destra chi la propone (classe, regione,
 *  sezione ATECO; il nome solo per una call con il nome di un'azienda
 *  verificata, altrimenti «Azienda anonima»), il confronto con la tua azienda
 *  se c'è (`Fit`) e «Salva» per il titolare. La riga è un `<li>`: va dentro
 *  un `<ul>`. */
export function CallCard({ call, editable }: { call: CallBacheca; editable: boolean }) {
  const creatore = call.creatore;
  const classe = creatore.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[creatore.classe_dimensionale] ?? creatore.classe_dimensionale)
    : null;
  const nome = creatore.denominazione || CALL_COPY.aziendaAnonima;
  const titolo = call.titolo || "Call senza titolo";
  const posti = call.posti ?? call.posizioni_n;
  const candidature = call.candidature_ricevute ?? 0;

  const chiPropone = (
    <>
      <span className="font-medium text-ink">{nome}</span>
      {(classe || creatore.regione) && (
        <span className="flex flex-wrap gap-x-3">
          {classe && <span>{classe}</span>}
          {creatore.regione && <span>{creatore.regione}</span>}
        </span>
      )}
      {creatore.ateco_sezione && <span>{creatore.ateco_sezione.descrizione}</span>}
    </>
  );

  return (
    <li className="flex items-start gap-6 border-b border-line px-2 py-4.5">
      <Due data={call.bando.scadenza} />
      <div className="flex min-w-0 grow flex-col gap-1">
        <h3 className="font-sans text-row-title text-ink">
          <Link to={linkCall(call)} className="rounded-mark hover:text-accent-hover">
            {titolo}
          </Link>
        </h3>
        <p className="text-body text-ink-2">Bando: {call.bando.titolo}</p>
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
          {call.stato !== "pubblicata" && <CallStatoBadge stato={call.stato} />}
          <span>{CALL_COPY.ruoliCreatoreBrevi[call.ruolo_creatore]}</span>
          <span>{conteggio(posti, "posto cercato", "posti cercati")}</span>
          {call.budget_fascia && <span>Budget: {CALL_COPY.fasceBudget[call.budget_fascia]}</span>}
        </p>
        <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
          <span>
            {call.bando.scadenza
              ? `Il bando scade il ${formatDate(call.bando.scadenza)}`
              : "Scadenza del bando da definire"}
          </span>
          {call.scadenza_call && <span>Candidature fino al {formatDate(call.scadenza_call)}</span>}
          {call.pubblicata_at && <span>Pubblicata il {formatDate(call.pubblicata_at)}</span>}
          <span>{conteggio(call.requisiti_cercati_n, "requisito cercato", "requisiti cercati")}</span>
          {candidature > 0 && <span>{conteggio(candidature, "candidatura", "candidature")}</span>}
        </p>
        {/* Nella colonna principale: chi propone sotto `md`, il confronto sotto
            `lg` (la sua colonna compare solo da `lg`). */}
        <div className={cn("mt-1 flex flex-col gap-2 lg:hidden", !call.match && "md:hidden")}>
          <p className="flex flex-col gap-0.5 text-small text-ink-2 md:hidden">{chiPropone}</p>
          {call.match && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <MatchBadge match={call.match} />
              <AttenzioneBadge match={call.match} />
            </div>
          )}
        </div>
      </div>
      <p className="hidden w-48 shrink-0 flex-col gap-0.5 text-small text-ink-2 md:flex">
        {chiPropone}
      </p>
      <div className="hidden w-32 shrink-0 flex-col items-start gap-1.5 lg:flex">
        {call.match && (
          <>
            <MatchBadge match={call.match} />
            <AttenzioneBadge match={call.match} />
          </>
        )}
      </div>
      {editable && (
        <div className="-mt-2 shrink-0">
          <SalvaCallButton id={call.id} titolo={titolo} salvata={call.salvata === true} />
        </div>
      )}
    </li>
  );
}
