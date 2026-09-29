import { CalendarClock, Eye, Flag, Handshake, Lock, Pencil, Scale } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { CallPubblicaCard } from "../components/partenariati/CallPubblicaCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { descriviCriterio, linkCall, mostraDecimale, percentuale } from "../components/partenariati/callDati";
import { CoperturaBadge } from "../components/partenariati/PassoGap";
import { NotaAnonima } from "../components/partenariati/PassoBando";
import { paginaDa } from "../components/partenariati/FiltriBacheca";
import { AttenzioneBadge, MatchBadge } from "../components/partenariati/MatchBadge";
import { MatchSpiegazione } from "../components/partenariati/MatchSpiegazione";
import { etichettaForma } from "../components/partenariati/PartenariatoRegole";
import { SalvaCallButton } from "../components/partenariati/SalvaCallButton";
import { Schede } from "../components/partenariati/Schede";
import { SegnalaDialog } from "../components/partenariati/SegnalaDialog";
import { SuggeritoCard } from "../components/partenariati/SuggeritoCard";
import { useNomiCall } from "../components/partenariati/useNomiCall";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { Dialog } from "../components/ui/Dialog";
import { Pagination } from "../components/ui/Pagination";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import {
  isVistaCreatore,
  useCall,
  useChiudiCall,
  useVersioniCall,
} from "../hooks/useCallPartenariato";
import { useCompany } from "../hooks/useCompany";
import { useSuggeriti } from "../hooks/usePartenariati";
import { usePartenariatiVocabolario } from "../hooks/usePartenariatiVocabolario";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CALL_COPY, PARTENARIATO_COPY } from "../lib/copy";
import { formatDate, formatDateTime, formatEur } from "../lib/format";
import type { CallPubblica, CallPubblicaDettaglio, CallVistaCreatore, MatchOut } from "../types";

type Tab = "panoramica" | "suggeriti";

/** Schede per l'azienda che ha creato la call: i suggeriti solo quando la
 *  call è pubblicata (prima nessuna azienda la vede, dopo non si propone). */
function schedeCreatore(call: CallVistaCreatore): Array<{ id: Tab; etichetta: string }> {
  const schede: Array<{ id: Tab; etichetta: string }> = [{ id: "panoramica", etichetta: "Panoramica" }];
  if (call.stato === "pubblicata") schede.push({ id: "suggeriti", etichetta: "Aziende suggerite" });
  return schede;
}

/** I campi della vista pubblica per l'azienda attiva (`CallPubblicaDettaglio`:
 *  confronto, «salvata», visibilità come partner); l'hook del dettaglio la
 *  tipizza come `CallPubblica`, che serve anche all'anteprima. */
function perLaTuaAzienda(call: CallPubblica): Pick<CallPubblicaDettaglio, "match" | "salvata"> & {
  opt_in: boolean | undefined;
} {
  const dettaglio = call as Partial<CallPubblicaDettaglio>;
  return {
    match: dettaglio.match ?? null,
    salvata: dettaglio.salvata === true,
    opt_in: dettaglio.opt_in,
  };
}

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

function Sezione({ titolo, azione, children }: { titolo: string; azione?: ReactNode; children: ReactNode }) {
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-base font-semibold text-slate-900">{titolo}</h2>
        {azione}
      </div>
      <div className="mt-3">{children}</div>
    </Card>
  );
}

/** Chiusura manuale: completata (partenariato fatto) o annullata. */
function ChiudiCall({ call }: { call: CallVistaCreatore }) {
  const chiudi = useChiudiCall(call.id);
  const [esito, setEsito] = useState<"completata" | "annullata" | null>(null);
  const bozza = call.stato === "bozza";
  const conferma = () => {
    if (!esito) return;
    chiudi.mutate({ esito }, { onSuccess: () => setEsito(null) });
  };
  return (
    <>
      {call.stato === "pubblicata" && (
        <Button variant="secondary" size="sm" onClick={() => setEsito("completata")}>
          Chiudi: partenariato completato
        </Button>
      )}
      <Button
        variant="ghost"
        size="sm"
        className="text-red-700 hover:bg-red-50 hover:text-red-800"
        onClick={() => setEsito("annullata")}
      >
        {bozza ? "Annulla la bozza" : "Annulla la call"}
      </Button>
      <Dialog
        open={esito !== null}
        onClose={() => setEsito(null)}
        dismissible={!chiudi.isPending}
        title={
          esito === "completata"
            ? "Chiudere la call come completata?"
            : bozza
              ? "Annullare la bozza?"
              : "Annullare la call?"
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setEsito(null)} disabled={chiudi.isPending}>
              Non ora
            </Button>
            <Button
              variant={esito === "completata" ? "primary" : "danger"}
              loading={chiudi.isPending}
              onClick={conferma}
            >
              {esito === "completata" ? "Chiudi la call" : bozza ? "Annulla la bozza" : "Annulla la call"}
            </Button>
          </>
        }
      >
        <p>
          {esito === "completata"
            ? "Hai trovato i partner: la call non riceve più candidature e non conta più tra le call attive del tuo piano."
            : bozza
              ? "La bozza si chiude e non si può riprendere: per lo stesso bando potrai crearne una nuova."
              : "La call non riceve più candidature e non conta più tra le call attive del tuo piano. Non si può riaprire."}
        </p>
        {chiudi.isError && (
          <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(chiudi.error)}
          </p>
        )}
      </Dialog>
    </>
  );
}

function Versioni({ call }: { call: CallVistaCreatore }) {
  const versioni = useVersioniCall(call.id, !!call.pubblicata_at);
  if (!call.pubblicata_at) return null;
  return (
    <Sezione titolo="Versioni pubblicate">
      {versioni.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : versioni.isError ? (
        <p className="text-sm text-red-700" role="alert">
          {apiErrorMessage(versioni.error, "Impossibile caricare le versioni.")}
        </p>
      ) : (versioni.data ?? []).length === 0 ? (
        <p className="text-sm text-slate-500">Nessuna versione.</p>
      ) : (
        <ol className="space-y-1 text-sm text-slate-700">
          {[...(versioni.data ?? [])]
            .sort((a, b) => b.versione - a.versione)
            .map((v) => (
              <li key={v.versione}>
                Versione {v.versione} — {formatDateTime(v.created_at)}
              </li>
            ))}
        </ol>
      )}
    </Sezione>
  );
}

/** Scheda «Panoramica» per l'azienda che ha creato la call. */
function Panoramica({ call }: { call: CallVistaCreatore }) {
  const nomi = useNomiCall();
  const { data: vocabolario } = usePartenariatiVocabolario();
  const aperta = call.stato === "bozza" || call.stato === "pubblicata";
  const requisiti = call.gap.requisiti;
  const cercati = requisiti.filter((r) => r.cercato).length;
  const regole = call.regole_partenariato;
  const etichetteRequisiti = new Map(requisiti.flatMap((r) => (r.id ? [[r.id, r.etichetta ?? r.testo] as const] : [])));

  return (
    <div className="space-y-4">
      {call.motivo_chiusura && (
        <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-700" role="note">
          {CALL_COPY.motiviChiusura[call.motivo_chiusura] ?? call.motivo_chiusura}
          {call.chiusa_at ? ` Chiusa il ${formatDate(call.chiusa_at)}.` : ""}
        </p>
      )}
      {call.stato === "sospesa_moderazione" && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="note">
          La call è sospesa dalla moderazione
          {call.sospesa_at ? ` dal ${formatDate(call.sospesa_at)}` : ""}
          {call.sospeso_motivo ? `: ${call.sospeso_motivo}` : "."} Per chiarimenti scrivi
          all'assistenza.
        </p>
      )}
      {!call.editable && (
        <p className="rounded-lg bg-slate-50 px-4 py-3 text-sm text-slate-600">{CALL_COPY.soloTitolare}</p>
      )}

      {call.editable && aperta && (
        <div className="flex flex-wrap gap-2">
          {call.stato === "bozza" ? (
            <LinkButton to={linkCall(call)} size="sm">
              <Pencil className="size-4" aria-hidden />
              Riprendi dal passo {call.wizard_passo}: {CALL_COPY.passi[call.wizard_passo - 1] ?? ""}
            </LinkButton>
          ) : (
            <LinkButton to={`/app/partenariati/call/${call.id}/modifica?passo=5`} size="sm">
              <Pencil className="size-4" aria-hidden />
              Modifica
            </LinkButton>
          )}
          <LinkButton to={`/app/partenariati/call/${call.id}/modifica?passo=6`} size="sm" variant="secondary">
            <Eye className="size-4" aria-hidden />
            Come ti vedono
          </LinkButton>
          <ChiudiCall call={call} />
        </div>
      )}

      <Sezione titolo="In sintesi">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Voce titolo="Il tuo ruolo">{CALL_COPY.ruoliCreatore[call.ruolo_creatore]}</Voce>
          <Voce titolo="Forma prevista">
            {call.forma_aggregazione_prevista
              ? etichettaForma(call.forma_aggregazione_prevista, vocabolario)
              : "Non ancora decisa"}
          </Voce>
          <Voce titolo="Candidature fino al">
            {call.scadenza_call ? formatDate(call.scadenza_call) : "Da decidere"}
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
          <Voce titolo="La tua quota">
            {call.quota_creatore_pct ? `${mostraDecimale(call.quota_creatore_pct)}%` : "Non indicata"}
          </Voce>
          <Voce titolo="Chi la vede">{CALL_COPY.visibilita[call.visibilita]}</Voce>
          {call.pubblicata_at && <Voce titolo="Pubblicata il">{formatDate(call.pubblicata_at)}</Voce>}
          {call.versione > 0 && <Voce titolo="Versione">{call.versione}</Voce>}
        </dl>
        <div className="mt-4">
          <NotaAnonima />
        </div>
      </Sezione>

      <Sezione titolo={call.titolo || "Call senza titolo"}>
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
            <Voce titolo="Dettagli riservati (solo per chi accetti)">
              <span className="whitespace-pre-line">{call.dettagli_riservati}</span>
            </Voce>
          )}
        </dl>
      </Sezione>

      <Sezione titolo={`Requisiti (ne cerchi ${cercati})`}>
        {requisiti.length === 0 ? (
          <p className="text-sm text-slate-500">Nessun requisito salvato.</p>
        ) : (
          <ul className="space-y-2">
            {requisiti.map((r, i) => (
              <li key={r.id ?? i} className="flex flex-wrap items-start gap-2 text-sm">
                {r.etichetta && (
                  <Badge tone="brand" className="shrink-0 tabular">
                    {r.etichetta}
                  </Badge>
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-slate-800">
                    {r.testo}
                    {r.cercato && <span className="ml-1.5 text-xs font-medium text-brand-700">· lo cerchi</span>}
                  </p>
                  <p className="text-xs text-slate-500">
                    {descriviCriterio(r.criterio, nomi)} · {CALL_COPY.ambiti[r.ambito]}
                  </p>
                </div>
                {r.copertura_creatore && <CoperturaBadge esito={r.copertura_creatore} />}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-slate-500">La tua copertura dei requisiti la vedi solo tu.</p>
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
                  {p.quota_ipotizzata_pct && <Badge tone="slate">Quota {percentuale(p.quota_ipotizzata_pct)}</Badge>}
                </div>
                {p.requisiti_ids.length > 0 && (
                  <p className="mt-1 text-xs text-slate-500">
                    Copre i requisiti: {p.requisiti_ids.map((id) => etichetteRequisiti.get(id) ?? "—").join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Sezione>

      <Sezione
        titolo="Regole del bando confermate"
        azione={
          call.editable && call.stato === "bozza" ? (
            <Link
              to={`/app/partenariati/call/${call.id}/modifica?passo=2`}
              className="text-sm font-medium text-brand-600 hover:text-brand-700"
            >
              Rivedi
            </Link>
          ) : undefined
        }
      >
        {regole ? (
          <div className="space-y-1 text-sm text-slate-700">
            <p>Modalità: {PARTENARIATO_COPY.modalita[regole.modalita.valore] ?? regole.modalita.valore}</p>
            <p className="text-xs text-slate-500">
              {[
                regole.forme_ammesse.length ? `${regole.forme_ammesse.length} forme ammesse` : null,
                regole.composizione.length ? `${regole.composizione.length} voci sulla composizione` : null,
                regole.quote.length ? `${regole.quote.length} quote` : null,
                regole.vincoli.length ? `${regole.vincoli.length} vincoli` : null,
                regole.regole_finanziarie.length ? `${regole.regole_finanziarie.length} requisiti economici` : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Nessuna voce oltre alla modalità."}
            </p>
            {call.esclusivita && <p>Il bando ammette un solo partenariato per soggetto.</p>}
            {call.regole_confermate_at && (
              <p className="text-xs text-slate-500">Confermate il {formatDate(call.regole_confermate_at)}.</p>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Non ancora confermate.</p>
        )}
      </Sezione>

      <Versioni call={call} />
    </div>
  );
}

/** Scheda «Aziende suggerite» (solo per l'azienda che ha creato la call; i
 *  membri la leggono): aziende visibili come partner che coprono qualcosa di
 *  ciò che cerchi, con un riferimento valido solo per questa call. */
function Suggeriti({ call }: { call: CallVistaCreatore }) {
  const [params, setParams] = useSearchParams();
  const pagina = paginaDa(params);
  const suggeriti = useSuggeriti(call.id, pagina);
  // Testo dei requisiti cercati per etichetta, per spiegare «A», «C»…
  const testi = new Map(
    call.gap.requisiti.flatMap((r) => (r.etichetta ? [[r.etichetta, r.testo] as const] : [])),
  );
  const vaiA = (n: number) => {
    setParams((prima) => {
      const dopo = new URLSearchParams(prima);
      if (n > 1) dopo.set("page", String(n));
      else dopo.delete("page");
      return dopo;
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  if (suggeriti.isPending) {
    return (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (suggeriti.isError) {
    return (
      <ErrorState
        message={apiErrorMessage(suggeriti.error, "Impossibile caricare le aziende suggerite.")}
        onRetry={() => void suggeriti.refetch()}
      />
    );
  }
  const dati = suggeriti.data;
  if (dati.items.length === 0) {
    return (
      <EmptyState
        title="Nessuna azienda suggerita per ora"
        description="Nessuna azienda visibile come partner copre i requisiti che cerchi. Prova ad allargare le posizioni o i requisiti: i suggerimenti si aggiornano da soli."
      />
    );
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-500">
        Aziende che hanno scelto di farsi trovare come partner e coprono qualcosa di ciò che
        cerchi, dalle più adatte. Non sanno che le stai guardando. Le aziende collegate alla tua
        non compaiono.
      </p>
      <p className="text-sm text-slate-500" role="status" aria-live="polite">
        {suggeriti.isPlaceholderData
          ? "Aggiornamento…"
          : dati.total === 1
            ? "1 azienda suggerita"
            : `${dati.total} aziende suggerite`}
      </p>
      <ul
        className={`space-y-3 transition-opacity ${suggeriti.isPlaceholderData ? "opacity-60" : ""}`}
        aria-busy={suggeriti.isPlaceholderData}
      >
        {dati.items.map((s) => (
          <SuggeritoCard key={s.pseudonimo} suggerito={s} testi={testi} />
        ))}
      </ul>
      <Pagination page={dati.page} totalPages={dati.total_pages} onChange={vaiA} />
    </div>
  );
}

/** Il confronto della tua azienda con la call di un'altra azienda: arriva
 *  con il dettaglio della call (vista «proprio», con i tuoi numeri). */
function Confronto({ call, match }: { call: CallPubblica; match: MatchOut | null }) {
  const testi = new Map(call.requisiti.map((r) => [r.etichetta, r.testo] as const));
  let corpo: ReactNode;
  if (!match) {
    corpo = (
      <p className="text-sm text-slate-600">
        La tua azienda non risulta tra quelle adatte a questa call. Controlla i requisiti e le
        posizioni cercate.
      </p>
    );
  } else {
    corpo = (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <MatchBadge match={match} />
          <AttenzioneBadge match={match} />
        </div>
        <MatchSpiegazione match={match} testi={testi} />
      </div>
    );
  }
  return (
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Scale className="size-4 text-brand-500" aria-hidden />
        La tua azienda e questa call
      </h2>
      <div className="mt-3">{corpo}</div>
    </Card>
  );
}

/** Pagina della call (`?tab=panoramica|suggeriti`; poi candidature,
 *  consorzio…). Per l'azienda che l'ha creata: vista completa e aziende
 *  suggerite; per le altre la proiezione pubblica con il proprio confronto,
 *  «Salva» (titolare) e «Segnala». */
export default function CallPartenariato() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const { avviso } = useAziendaDaLink();
  const { data: azienda } = useCompany();
  const callQ = useCall(id);
  const [segnala, setSegnala] = useState(false);
  const annuncioArrivo = (location.state as { annuncio?: string } | null)?.annuncio ?? null;
  // Una regione live già piena al montaggio non viene letta: il messaggio
  // arriva dopo, quando la regione (vuota) è già nella pagina.
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  useEffect(() => {
    setAnnuncio(null);
    if (!annuncioArrivo) return;
    const timer = window.setTimeout(() => setAnnuncio(annuncioArrivo), 150);
    return () => window.clearTimeout(timer);
  }, [annuncioArrivo, location.key]);
  const schede = isVistaCreatore(callQ.data) ? schedeCreatore(callQ.data) : [];
  const tab: Tab = schede.some((s) => s.id === params.get("tab")) ? (params.get("tab") as Tab) : "panoramica";

  let corpo: ReactNode;
  if (callQ.isPending) {
    corpo = (
      <div className="space-y-4" aria-hidden>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  } else if (callQ.isError) {
    corpo =
      apiErrorCode(callQ.error) === "not_found" ? (
        <EmptyState
          title="Call non trovata"
          description="Non esiste, non è più aperta oppure non è visibile alla tua azienda."
          action={<LinkButton to="/app/partenariati?vista=tutte">Tutte le call</LinkButton>}
        />
      ) : (
        <ErrorState
          message={apiErrorMessage(callQ.error, "Impossibile caricare la call.")}
          onRetry={() => void callQ.refetch()}
        />
      );
  } else if (!isVistaCreatore(callQ.data)) {
    const pubblica = callQ.data;
    const tua = perLaTuaAzienda(pubblica);
    corpo = (
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <h1 className="sr-only">{pubblica.titolo || "Call di partenariato"}</h1>
        <CallPubblicaCard call={pubblica} />
        <aside className="space-y-4" aria-label="Per la tua azienda">
          <Confronto call={pubblica} match={tua.match} />
          <BannerOptIn optIn={tua.opt_in} />
          <div className="flex flex-wrap items-start justify-end gap-2">
            {azienda?.editable && (
              <SalvaCallButton
                id={pubblica.id}
                titolo={pubblica.titolo || "Call senza titolo"}
                salvata={tua.salvata}
              />
            )}
            <Button variant="ghost" size="sm" onClick={() => setSegnala(true)}>
              <Flag className="size-4" aria-hidden />
              Segnala
            </Button>
          </div>
        </aside>
        <SegnalaDialog open={segnala} onClose={() => setSegnala(false)} oggettoTipo="call" oggettoId={pubblica.id} />
      </div>
    );
  } else {
    const call = callQ.data;
    corpo = (
      <div className="space-y-5">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <CallStatoBadge stato={call.stato} />
            {call.scadenza_call && (
              <span className="inline-flex items-center gap-1 text-sm text-slate-600">
                <CalendarClock className="size-4 text-slate-400" aria-hidden />
                Candidature fino al {formatDate(call.scadenza_call)}
              </span>
            )}
          </div>
          <h1 className="mt-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            {call.titolo || "Call senza titolo"}
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Per il bando{" "}
            <Link to={`/app/bandi/${call.bando.slug}`} className="font-medium text-brand-600 hover:text-brand-700">
              {call.bando.titolo}
            </Link>
            {call.bando.scadenza ? `, che scade il ${formatDate(call.bando.scadenza)}` : ""}
          </p>
        </div>
        <Schede
          etichetta="Sezioni della call"
          schede={schede}
          attiva={tab}
          onCambia={(t) =>
            setParams(
              (p) => {
                const nuovi = new URLSearchParams(p);
                nuovi.set("tab", t);
                // La pagina è della scheda: si riparte dalla prima.
                nuovi.delete("page");
                return nuovi;
              },
              { replace: true },
            )
          }
        >
          {tab === "panoramica" && <Panoramica call={call} />}
          {tab === "suggeriti" && <Suggeriti call={call} />}
        </Schede>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <p className="text-sm text-slate-500">
        <Link
          to={isVistaCreatore(callQ.data) ? "/app/partenariati?vista=mie" : "/app/partenariati"}
          className="inline-flex items-center gap-1.5 font-medium text-brand-600 hover:text-brand-700"
        >
          <Handshake className="size-4" aria-hidden />
          Partenariati
        </Link>
      </p>
      {avviso && (
        <p role="status" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {avviso}
        </p>
      )}
      <div role="status" aria-live="polite">
        {annuncio && (
          <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{annuncio}</p>
        )}
      </div>
      {corpo}
    </div>
  );
}
