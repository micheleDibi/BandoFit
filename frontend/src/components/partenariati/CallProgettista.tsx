import { Lock } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { CALL_COPY, CONSORZIO_COPY, PARTENARIATO_COPY } from "../../lib/copy";
import { formatDate, formatEur } from "../../lib/format";
import type { CallVistaProgettista, MembroConsorzio } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { descriviCriterio, mostraDecimale, percentuale } from "./callDati";
import { CallStatoBadge } from "./CallStatoBadge";
import { MatriceCopertura } from "./MatriceCopertura";
import { StatoMembroBadge } from "./MembroRiga";
import { CoperturaBadge } from "./PassoGap";
import { etichettaForma } from "./PartenariatoRegole";
import { useNomiCall } from "./useNomiCall";
import { ValidatoreChecklist } from "./ValidatoreChecklist";

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

function Sezione({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <Card className="p-5">
      <h3 className="font-display text-base font-semibold text-slate-900">{titolo}</h3>
      <div className="mt-3">{children}</div>
    </Card>
  );
}

/** Nomi univoci dei membri per la matrice, la verifica e l'elenco: il
 *  cliente col suo nome, gli esterni col nome dichiarato, le altre aziende in
 *  piattaforma «Azienda anonima» numerate (il server non manda né
 *  pseudonimo né profilo al progettista). */
function nomiMembri(membri: MembroConsorzio[]): Map<string, string> {
  let anonime = 0;
  return new Map(
    membri.map((m) => {
      if (m.creatore) return [m.id, `${m.nome} (il cliente)`] as const;
      if (m.esterno) return [m.id, `${m.nome} (fuori dalla piattaforma)`] as const;
      anonime += 1;
      return [m.id, `${m.nome} ${anonime}`] as const;
    }),
  );
}

/** La call di partenariato del cliente vista dal progettista assegnato
 *  (`CallVistaProgettista`, WP9): testi, regole confermate, requisiti con la
 *  copertura del cliente, posizioni, membri del consorzio (degli altri solo
 *  esiti e fasce), verifica delle regole e matrice di copertura. Sola
 *  lettura: le azioni restano del cliente. */
export function CallProgettista({ call }: { call: CallVistaProgettista }) {
  const nomiCriteri = useNomiCall();
  const { data: vocabolario } = usePartenariatiVocabolario();
  const consorzio = call.consorzio;
  const nomi = useMemo(() => nomiMembri(consorzio?.membri ?? []), [consorzio]);
  const etichetteRequisiti = new Map(
    call.requisiti.flatMap((r) => (r.id ? [[r.id, r.etichetta ?? r.testo] as const] : [])),
  );
  const regole = call.regole_partenariato;
  const attivi = (consorzio?.membri ?? []).filter((m) => m.stato !== "uscito");

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex flex-wrap items-center gap-2">
          <CallStatoBadge stato={call.stato} />
          {call.scadenza_call && (
            <span className="text-sm text-slate-600">
              Candidature fino al {formatDate(call.scadenza_call)}
            </span>
          )}
        </div>
        <h3 className="mt-2 font-display text-lg font-semibold text-slate-900">
          {call.titolo || "Call senza titolo"}
        </h3>
        <p className="mt-0.5 text-sm text-slate-600">Per il bando {call.bando.titolo}</p>
        {call.motivo_chiusura && (
          <p className="mt-1 text-sm text-slate-600">
            {CALL_COPY.motiviChiusura[call.motivo_chiusura] ?? call.motivo_chiusura}
          </p>
        )}
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Voce titolo="Ruolo del cliente">{CALL_COPY.ruoliCreatore[call.ruolo_creatore]}</Voce>
          <Voce titolo="Forma prevista">
            {call.forma_aggregazione_prevista
              ? etichettaForma(call.forma_aggregazione_prevista, vocabolario)
              : "Non ancora decisa"}
          </Voce>
          <Voce titolo="Budget (fascia pubblica)">
            {call.budget_fascia ? CALL_COPY.fasceBudget[call.budget_fascia] : "Non indicato"}
          </Voce>
          <Voce titolo="Budget esatto (riservato)">
            {call.budget_progetto_eur ? (
              <span className="inline-flex items-center gap-1">
                <Lock className="size-3.5 text-slate-400" aria-hidden />
                {formatEur(call.budget_progetto_eur)}
              </span>
            ) : (
              "Non indicato"
            )}
          </Voce>
          <Voce titolo="Quota del cliente">
            {call.quota_creatore_pct ? `${mostraDecimale(call.quota_creatore_pct)}%` : "Non indicata"}
          </Voce>
          <Voce titolo="Chi la vede">{CALL_COPY.visibilita[call.visibilita]}</Voce>
          <Voce titolo="Nome dell'azienda">
            {call.anonima ? "Call anonima" : "Call con il nome dell'azienda"}
          </Voce>
          {call.pubblicata_at && <Voce titolo="Pubblicata il">{formatDate(call.pubblicata_at)}</Voce>}
        </dl>
      </Card>

      <Sezione titolo="Testi della call">
        <dl className="space-y-4">
          <Voce titolo="Il progetto">
            {call.descrizione_pubblica ? (
              <span className="whitespace-pre-line">{call.descrizione_pubblica}</span>
            ) : (
              <span className="text-slate-500">Non ancora scritto.</span>
            )}
          </Voce>
          {call.profilo_partner_ideale && (
            <Voce titolo="Il partner ideale">
              <span className="whitespace-pre-line">{call.profilo_partner_ideale}</span>
            </Voce>
          )}
          {call.dettagli_riservati && (
            <Voce titolo="Dettagli riservati (per le aziende accettate)">
              <span className="whitespace-pre-line">{call.dettagli_riservati}</span>
            </Voce>
          )}
        </dl>
      </Sezione>

      <Sezione titolo="Regole del bando confermate">
        {regole ? (
          <div className="space-y-1 text-sm text-slate-700">
            <p>Modalità: {PARTENARIATO_COPY.modalita[regole.modalita.valore] ?? regole.modalita.valore}</p>
            <p className="text-xs text-slate-500">
              {[
                regole.forme_ammesse.length ? `${regole.forme_ammesse.length} forme ammesse` : null,
                regole.composizione.length ? `${regole.composizione.length} voci sulla composizione` : null,
                regole.quote.length ? `${regole.quote.length} quote` : null,
                regole.vincoli.length ? `${regole.vincoli.length} vincoli` : null,
                regole.regole_finanziarie.length
                  ? `${regole.regole_finanziarie.length} requisiti economici`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Nessuna voce oltre alla modalità."}
            </p>
            {call.esclusivita && <p>Il bando ammette un solo partenariato per soggetto.</p>}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Il cliente non le ha ancora confermate.</p>
        )}
      </Sezione>

      <Sezione titolo="Requisiti">
        {call.requisiti.length === 0 ? (
          <p className="text-sm text-slate-500">Nessun requisito salvato.</p>
        ) : (
          <ul className="space-y-2">
            {call.requisiti.map((r, i) => (
              <li key={r.id ?? i} className="flex flex-wrap items-start gap-2 text-sm">
                {r.etichetta && (
                  <Badge tone="brand" className="shrink-0 tabular">
                    {r.etichetta}
                  </Badge>
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-slate-800">
                    {r.testo}
                    {r.cercato && (
                      <span className="ml-1.5 text-xs font-medium text-brand-700">· lo cerca</span>
                    )}
                  </p>
                  <p className="text-xs text-slate-500">
                    {descriviCriterio(r.criterio, nomiCriteri)} · {CALL_COPY.ambiti[r.ambito]}
                  </p>
                </div>
                {r.copertura_creatore && <CoperturaBadge esito={r.copertura_creatore} />}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-slate-500">
          La copertura indicata è quella dell'azienda del cliente.
        </p>
      </Sezione>

      <Sezione titolo="Posizioni cercate">
        {call.posizioni.length === 0 ? (
          <p className="text-sm text-slate-500">Nessuna posizione salvata.</p>
        ) : (
          <ul className="space-y-2">
            {call.posizioni.map((p) => (
              <li key={p.id} className="rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-800">{p.titolo}</span>
                  <Badge tone={p.ruolo === "capofila" ? "brand" : "slate"}>
                    {p.ruolo === "capofila" ? "Capofila" : "Partner"}
                  </Badge>
                  {p.numero > 1 && <Badge tone="slate">{p.numero} partner</Badge>}
                  {p.quota_ipotizzata_pct && (
                    <Badge tone="slate">Quota {percentuale(p.quota_ipotizzata_pct)}</Badge>
                  )}
                </div>
                {p.requisiti_ids.length > 0 && (
                  <p className="mt-1 text-xs text-slate-500">
                    Copre i requisiti:{" "}
                    {p.requisiti_ids.map((id) => etichetteRequisiti.get(id) ?? "—").join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Sezione>

      <Sezione titolo="Consorzio">
        {!consorzio ? (
          <p className="text-sm text-slate-500">
            Il consorzio nasce quando il cliente pubblica la call.
          </p>
        ) : attivi.length === 0 ? (
          <p className="text-sm text-slate-500">Nessun membro nel consorzio per ora.</p>
        ) : (
          <ul className="space-y-2">
            {attivi.map((m) => (
              <li key={m.id} className="rounded-lg border border-slate-200 px-3.5 py-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-800">{nomi.get(m.id) ?? m.nome}</span>
                  <Badge tone={m.ruolo === "capofila" ? "brand" : "slate"}>
                    {CONSORZIO_COPY.ruoli[m.ruolo]}
                  </Badge>
                  <StatoMembroBadge stato={m.stato} />
                  {m.esterno && <Badge tone="slate">{CONSORZIO_COPY.esterno}</Badge>}
                  {m.quota_percentuale && (
                    <Badge tone="slate">Quota {percentuale(m.quota_percentuale)}</Badge>
                  )}
                </div>
                {m.posizione && (
                  <p className="mt-1 text-xs text-slate-500">Posizione: {m.posizione.titolo}</p>
                )}
                {m.esterno && m.paese && <p className="text-xs text-slate-500">Paese: {m.paese}</p>}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-slate-500">
          Delle aziende partner vedi solo esiti e fasce: niente contatti, messaggi o numeri esatti.
        </p>
      </Sezione>

      {consorzio && <ValidatoreChecklist validazione={consorzio.validazione} nomi={nomi} />}
      {consorzio && <MatriceCopertura matrice={consorzio.matrice} nomi={nomi} />}
    </div>
  );
}
