import { Lock } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { CALL_COPY, CONSORZIO_COPY, PARTENARIATO_COPY } from "../../lib/copy";
import { formatDate, formatEur } from "../../lib/format";
import type { CallVistaProgettista, MembroConsorzio } from "../../types";
import { Badge } from "../ui/Badge";
import { Due } from "../ui/Due";
import { Section, SectionHeader } from "../ui/SectionHeader";
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
    <div className="flex flex-col gap-1">
      <dt className="text-small text-ink-3">{titolo}</dt>
      <dd className="text-body text-ink">{children}</dd>
    </div>
  );
}

/** Sezione con titolo e filetto (niente riquadri): livello 3, dentro la
 *  sezione della call nella pagina della richiesta. */
function Sezione({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <Section>
      <SectionHeader titolo={titolo} livello={3} />
      {children}
    </Section>
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
  // Voci delle regole confermate, oltre alla modalità.
  const vociRegole = regole
    ? [
        regole.forme_ammesse.length ? `${regole.forme_ammesse.length} forme ammesse` : null,
        regole.composizione.length
          ? `${regole.composizione.length} voci sulla composizione`
          : null,
        regole.quote.length ? `${regole.quote.length} quote` : null,
        regole.vincoli.length ? `${regole.vincoli.length} vincoli` : null,
        regole.regole_finanziarie.length
          ? `${regole.regole_finanziarie.length} requisiti economici`
          : null,
      ].filter((v): v is string => v !== null)
    : [];

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <CallStatoBadge stato={call.stato} className="self-start" />
          <h3 className="text-title-section text-ink">{call.titolo || "Call senza titolo"}</h3>
          <p className="text-body text-ink-2">Per il bando {call.bando.titolo}</p>
          {call.motivo_chiusura && (
            <p className="text-body text-ink-2">
              {CALL_COPY.motiviChiusura[call.motivo_chiusura] ?? call.motivo_chiusura}
            </p>
          )}
        </div>
        <dl className="grid gap-x-6 gap-y-4 border-y border-line py-4 sm:grid-cols-2 lg:grid-cols-3">
          {call.scadenza_call && (
            <Voce titolo="Candidature fino al">
              <Due data={call.scadenza_call} />
            </Voce>
          )}
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
              <span className="inline-flex items-center gap-1.5 tabular-nums">
                <Lock className="size-4 text-ink-3" aria-hidden />
                {formatEur(call.budget_progetto_eur)}
              </span>
            ) : (
              "Non indicato"
            )}
          </Voce>
          <Voce titolo="Quota del cliente">
            {call.quota_creatore_pct
              ? `${mostraDecimale(call.quota_creatore_pct)}%`
              : "Non indicata"}
          </Voce>
          <Voce titolo="Chi la vede">{CALL_COPY.visibilita[call.visibilita]}</Voce>
          <Voce titolo="Nome dell'azienda">
            {call.anonima ? "Call anonima" : "Call con il nome dell'azienda"}
          </Voce>
          {call.pubblicata_at && (
            <Voce titolo="Pubblicata il">{formatDate(call.pubblicata_at)}</Voce>
          )}
        </dl>
      </div>

      <Sezione titolo="Testi della call">
        <dl className="flex max-w-lettura flex-col gap-4">
          <Voce titolo="Il progetto">
            {call.descrizione_pubblica ? (
              <span className="whitespace-pre-line">{call.descrizione_pubblica}</span>
            ) : (
              <span className="text-ink-2">Non ancora scritto.</span>
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
          <div className="flex flex-col gap-2 text-body text-ink">
            <p>
              Modalità:{" "}
              {PARTENARIATO_COPY.modalita[regole.modalita.valore] ?? regole.modalita.valore}
            </p>
            {vociRegole.length > 0 ? (
              <ul className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
                {vociRegole.map((voce) => (
                  <li key={voce}>{voce}</li>
                ))}
              </ul>
            ) : (
              <p className="text-small text-ink-2">Nessuna voce oltre alla modalità.</p>
            )}
            {call.esclusivita && <p>Il bando ammette un solo partenariato per soggetto.</p>}
          </div>
        ) : (
          <p className="text-body text-ink-2">Il cliente non le ha ancora confermate.</p>
        )}
      </Sezione>

      <Sezione titolo="Requisiti">
        {call.requisiti.length === 0 ? (
          <p className="text-body text-ink-2">Nessun requisito salvato.</p>
        ) : (
          <ul className="flex flex-col">
            {call.requisiti.map((r, i) => (
              <li
                key={r.id ?? i}
                className="flex flex-wrap items-start gap-x-4 gap-y-2 border-b border-line px-2 py-3"
              >
                {r.etichetta && (
                  <Badge className="shrink-0 tabular-nums">{r.etichetta}</Badge>
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <p className="text-body text-ink">{r.testo}</p>
                  <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-small text-ink-2">
                    <span>{descriviCriterio(r.criterio, nomiCriteri)}</span>
                    <span>{CALL_COPY.ambiti[r.ambito]}</span>
                    {r.cercato && <span className="font-medium text-ink">Lo cerca</span>}
                  </p>
                </div>
                {r.copertura_creatore && <CoperturaBadge esito={r.copertura_creatore} />}
              </li>
            ))}
          </ul>
        )}
        <p className="text-small text-ink-3">La copertura indicata è quella dell'azienda del cliente.</p>
      </Sezione>

      <Sezione titolo="Posizioni cercate">
        {call.posizioni.length === 0 ? (
          <p className="text-body text-ink-2">Nessuna posizione salvata.</p>
        ) : (
          <ul className="flex flex-col">
            {call.posizioni.map((p) => (
              <li key={p.id} className="flex flex-col gap-1 border-b border-line px-2 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-ink">{p.titolo}</span>
                  <Badge>{p.ruolo === "capofila" ? "Capofila" : "Partner"}</Badge>
                  {p.numero > 1 && <Badge>{p.numero} partner</Badge>}
                  {p.quota_ipotizzata_pct && (
                    <Badge>Quota {percentuale(p.quota_ipotizzata_pct)}</Badge>
                  )}
                </div>
                {p.requisiti_ids.length > 0 && (
                  <p className="text-small text-ink-2">
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
          <p className="text-body text-ink-2">
            Il consorzio nasce quando il cliente pubblica la call.
          </p>
        ) : attivi.length === 0 ? (
          <p className="text-body text-ink-2">Nessun membro nel consorzio per ora.</p>
        ) : (
          <ul className="flex flex-col">
            {attivi.map((m) => (
              <li key={m.id} className="flex flex-col gap-1 border-b border-line px-2 py-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-semibold text-ink">{nomi.get(m.id) ?? m.nome}</span>
                  <StatoMembroBadge stato={m.stato} />
                  <Badge>{CONSORZIO_COPY.ruoli[m.ruolo]}</Badge>
                  {m.esterno && <Badge>{CONSORZIO_COPY.esterno}</Badge>}
                  {m.quota_percentuale && <Badge>Quota {percentuale(m.quota_percentuale)}</Badge>}
                </div>
                {(m.posizione || (m.esterno && m.paese)) && (
                  <p className="flex flex-wrap gap-x-4 gap-y-0.5 text-small text-ink-2">
                    {m.posizione && <span>Posizione: {m.posizione.titolo}</span>}
                    {m.esterno && m.paese && <span>Paese: {m.paese}</span>}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        <p className="text-small text-ink-3">
          Delle aziende partner vedi solo esiti e fasce: niente contatti, messaggi o numeri esatti.
        </p>
      </Sezione>

      {/* Livello 3: stanno sotto l'h2 «La call di partenariato del cliente», come le altre sezioni. */}
      {consorzio && (
        <ValidatoreChecklist validazione={consorzio.validazione} nomi={nomi} livello={3} />
      )}
      {consorzio && <MatriceCopertura matrice={consorzio.matrice} nomi={nomi} livello={3} />}
    </div>
  );
}
