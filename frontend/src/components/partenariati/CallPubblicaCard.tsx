import { CalendarClock, EyeOff, Users } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { CALL_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import { nomePaese } from "../../lib/paesi";
import type { CallPubblica, PosizionePubblicaCall } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { descriviCriterio, percentuale } from "./callDati";
import { etichettaForma } from "./PartenariatoRegole";
import { useNomiCall } from "./useNomiCall";

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

function Posizione({ p }: { p: PosizionePubblicaCall }) {
  const nomi = useNomiCall();
  const righe: Array<[string, string]> = [];
  if (p.tipi_soggetto.length) righe.push(["Tipo di soggetto", p.tipi_soggetto.map(nomi.tipo).join(", ")]);
  if (p.competenze.length) righe.push(["Competenze", p.competenze.map(nomi.competenza).join(", ")]);
  if (p.ateco_divisioni.length) righe.push(["Attività ATECO", p.ateco_divisioni.join(", ")]);
  if (p.territorio_modalita !== "qualsiasi") {
    const regioni = p.regioni_nomi.length ? p.regioni_nomi : p.regioni.map(nomi.regione);
    righe.push([CALL_COPY.territorio[p.territorio_modalita], regioni.join(", ")]);
  }
  if (p.paesi.length) righe.push(["Paesi", p.paesi.map(nomePaese).join(", ")]);
  if (p.dimensioni.length) {
    righe.push(["Dimensione", p.dimensioni.map((d) => CALL_COPY.dimensioni[d] ?? d).join(", ")]);
  }
  if (p.requisiti.length) righe.push(["Copre i requisiti", p.requisiti.join(", ")]);
  return (
    <li className="rounded-lg border border-slate-200 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium text-slate-900">{p.titolo}</p>
        <Badge tone={p.ruolo === "capofila" ? "brand" : "slate"}>
          {p.ruolo === "capofila" ? "Capofila" : "Partner"}
        </Badge>
        {p.numero > 1 && <Badge tone="slate">{p.numero} partner</Badge>}
        {p.quota_ipotizzata_pct && (
          <Badge tone="slate">Quota {percentuale(p.quota_ipotizzata_pct)}</Badge>
        )}
      </div>
      {righe.length > 0 && (
        <dl className="mt-2 space-y-1 text-sm">
          {righe.map(([t, v]) => (
            <div key={t}>
              <dt className="inline text-slate-500">{t}: </dt>
              <dd className="inline text-slate-700">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {p.note && <p className="mt-2 whitespace-pre-line text-sm text-slate-600">{p.note}</p>}
    </li>
  );
}

/** La call come la vedono le altre aziende (proiezione a whitelist del server:
 *  niente nome, niente budget esatto, niente dettagli riservati né coperture
 *  del creatore). I testi sono testo semplice, mai HTML né link. */
export function CallPubblicaCard({ call, className }: { call: CallPubblica; className?: string }) {
  const { data: vocabolario } = usePartenariatiVocabolario();
  const nomi = useNomiCall();
  const creatore = call.creatore;
  const classe = creatore.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[creatore.classe_dimensionale] ?? creatore.classe_dimensionale)
    : null;

  return (
    <Card className={className}>
      <div className="border-b border-slate-100 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className="rounded-lg bg-slate-100 p-2 text-slate-500">
            {creatore.anonima ? (
              <EyeOff className="size-5" aria-hidden />
            ) : (
              <Users className="size-5" aria-hidden />
            )}
          </div>
          <div className="min-w-0">
            <p className="font-display text-base font-semibold text-slate-900">
              {creatore.denominazione || CALL_COPY.aziendaAnonima}
            </p>
            <p className="text-xs text-slate-500">
              {[classe, creatore.regione, creatore.ateco_sezione
                ? `${creatore.ateco_sezione.lettera} — ${creatore.ateco_sezione.descrizione}`
                : null]
                .filter(Boolean)
                .join(" · ") || "—"}
            </p>
          </div>
        </div>
        <h3 className="mt-4 font-display text-lg font-bold tracking-tight text-slate-900">
          {call.titolo || "Call senza titolo"}
        </h3>
        <p className="mt-1 text-sm text-slate-600">
          Per il bando{" "}
          <Link
            to={`/app/bandi/${call.bando.slug}`}
            className="font-medium text-brand-600 underline-offset-2 hover:underline"
          >
            {call.bando.titolo}
          </Link>
        </p>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Badge tone="brand">{CALL_COPY.ruoliCreatoreBrevi[call.ruolo_creatore]}</Badge>
          {call.forma_aggregazione_prevista && (
            <Badge tone="slate">{etichettaForma(call.forma_aggregazione_prevista, vocabolario)}</Badge>
          )}
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
      </div>

      <dl className="space-y-4 px-5 py-4">
        {call.descrizione_pubblica && (
          <Voce titolo="Il progetto">
            <span className="whitespace-pre-line">{call.descrizione_pubblica}</span>
          </Voce>
        )}
        {call.profilo_partner_ideale && (
          <Voce titolo="Il partner ideale">
            <span className="whitespace-pre-line">{call.profilo_partner_ideale}</span>
          </Voce>
        )}
        <Voce titolo="Posizioni cercate">
          {call.posizioni.length === 0 ? (
            <span className="text-slate-500">Nessuna posizione indicata.</span>
          ) : (
            <ul className="space-y-2">
              {call.posizioni.map((p) => (
                <Posizione key={p.id} p={p} />
              ))}
            </ul>
          )}
        </Voce>
        <Voce titolo="Requisiti cercati">
          {call.requisiti.length === 0 ? (
            <span className="text-slate-500">Nessun requisito indicato.</span>
          ) : (
            <ul className="space-y-1.5">
              {call.requisiti.map((r) => (
                <li key={r.etichetta} className="flex items-start gap-2">
                  <Badge tone="brand" className="shrink-0 tabular">
                    {r.etichetta}
                  </Badge>
                  <span>
                    {r.testo}
                    <span className="block text-xs text-slate-500">
                      {descriviCriterio(r.criterio, nomi)} · {CALL_COPY.ambiti[r.ambito]}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Voce>
        {call.esclusivita && (
          <Voce titolo="Esclusività">
            Il bando ammette la partecipazione a un solo partenariato: chi entra in questo non può
            partecipare ad altri sullo stesso bando.
          </Voce>
        )}
      </dl>
    </Card>
  );
}
