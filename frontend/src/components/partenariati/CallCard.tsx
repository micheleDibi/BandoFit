import { CalendarClock, EyeOff, Users } from "lucide-react";
import { Link } from "react-router-dom";
import { CALL_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { CallBacheca } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { linkCall } from "./callDati";
import { CallStatoBadge } from "./CallStatoBadge";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { MatchSpiegazione } from "./MatchSpiegazione";
import { SalvaCallButton } from "./SalvaCallButton";

function conteggio(n: number, uno: string, molti: string) {
  return `${n} ${n === 1 ? uno : molti}`;
}

/** Una call nella bacheca, in «Per te» e tra le salvate: chi la propone
 *  (classe, regione, sezione ATECO; il nome solo per una call con il nome di
 *  un'azienda verificata, altrimenti «Azienda anonima»), bando, ruolo, budget,
 *  scadenza, posti; il confronto con la tua azienda se c'è; «Salva» per il
 *  titolare. La card è un `<li>`: va dentro un `<ul>`. */
export function CallCard({ call, editable }: { call: CallBacheca; editable: boolean }) {
  const creatore = call.creatore;
  const classe = creatore.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[creatore.classe_dimensionale] ?? creatore.classe_dimensionale)
    : null;
  const chi = [
    creatore.denominazione || CALL_COPY.aziendaAnonima,
    classe,
    creatore.regione,
    creatore.ateco_sezione?.descrizione ?? null,
  ]
    .filter(Boolean)
    .join(" · ");
  const titolo = call.titolo || "Call senza titolo";
  const posti = call.posti ?? call.posizioni_n;
  const candidature = call.candidature_ricevute ?? 0;

  return (
    <li>
      <Card className="p-4 transition-colors hover:border-brand-300">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="inline-flex max-w-full items-center gap-1.5 text-xs text-slate-500">
              {creatore.anonima ? (
                <EyeOff className="size-3.5 shrink-0" aria-hidden />
              ) : (
                <Users className="size-3.5 shrink-0" aria-hidden />
              )}
              <span className="truncate">{chi}</span>
            </p>
            <h3 className="mt-1 font-display text-base font-semibold text-slate-900">
              <Link
                to={linkCall(call)}
                className="rounded hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
              >
                {titolo}
              </Link>
            </h3>
            <p className="mt-0.5 text-sm text-slate-600">Bando: {call.bando.titolo}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {call.stato !== "pubblicata" && <CallStatoBadge stato={call.stato} />}
              <Badge tone="brand">{CALL_COPY.ruoliCreatoreBrevi[call.ruolo_creatore]}</Badge>
              {call.budget_fascia && (
                <Badge tone="slate">Budget: {CALL_COPY.fasceBudget[call.budget_fascia]}</Badge>
              )}
              {call.scadenza_call && (
                <Badge tone="amber">
                  <CalendarClock className="size-3" aria-hidden />
                  Candidature entro il {formatDate(call.scadenza_call)}
                </Badge>
              )}
            </div>
            <p className="mt-2 text-xs text-slate-500">
              {conteggio(posti, "posto cercato", "posti cercati")} ·{" "}
              {conteggio(call.requisiti_cercati_n, "requisito cercato", "requisiti cercati")}
              {candidature > 0 ? ` · ${conteggio(candidature, "candidatura", "candidature")}` : ""}
              {call.pubblicata_at ? ` · pubblicata il ${formatDate(call.pubblicata_at)}` : ""}
            </p>
          </div>
          {editable && <SalvaCallButton id={call.id} titolo={titolo} salvata={call.salvata === true} />}
        </div>
        {call.match && (
          <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <MatchBadge match={call.match} />
              <AttenzioneBadge match={call.match} />
            </div>
            <MatchSpiegazione match={call.match} compatta />
          </div>
        )}
      </Card>
    </li>
  );
}
