import { CalendarClock, Check, Eye, Flag, Handshake, Lock, Pencil, Scale, Send, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { CallPubblicaCard } from "../components/partenariati/CallPubblicaCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import {
  CandidaturaCard,
  CandidaturaPropriaCard,
  SceltaDirezione,
} from "../components/partenariati/CandidaturaCard";
import { CandidaturaDialog } from "../components/partenariati/CandidaturaDialog";
import { descriviCriterio, linkCall, mostraDecimale, percentuale } from "../components/partenariati/callDati";
import { CoperturaBadge } from "../components/partenariati/PassoGap";
import { NotaAnonima } from "../components/partenariati/PassoBando";
import { paginaDa } from "../components/partenariati/FiltriBacheca";
import { AttenzioneBadge, MatchBadge } from "../components/partenariati/MatchBadge";
import { MatchSpiegazione } from "../components/partenariati/MatchSpiegazione";
import { InvitaDialog } from "../components/partenariati/InvitaDialog";
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
import { useCandidature } from "../hooks/useCandidature";
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
import { CALL_COPY, CANDIDATURE_COPY, PARTENARIATO_COPY } from "../lib/copy";
import { formatDate, formatDateTime, formatEur } from "../lib/format";
import type {
  CandidaturaPropria,
  CallDettaglioAltraAzienda,
  CallPubblica,
  CallPubblicaDettaglio,
  CallVistaCreatore,
  DirezioneCandidature,
  MatchOut,
  PartnerSuggerito,
  PartnerSuggeritoContatto,
} from "../types";

type Tab = "panoramica" | "suggeriti" | "candidature";

/** Schede per l'azienda che ha creato la call: i suggeriti solo quando la
 *  call è pubblicata (prima nessuna azienda la vede, dopo non si propone);
 *  candidature e inviti da quando è stata pubblicata (restano consultabili
 *  anche dopo la chiusura). */
function schedeCreatore(call: CallVistaCreatore): Array<{ id: Tab; etichetta: string }> {
  const schede: Array<{ id: Tab; etichetta: string }> = [{ id: "panoramica", etichetta: "Panoramica" }];
  if (call.stato === "pubblicata") schede.push({ id: "suggeriti", etichetta: "Aziende suggerite" });
  if (call.pubblicata_at) schede.push({ id: "candidature", etichetta: "Candidature e inviti" });
  return schede;
}

/** I campi della vista pubblica per l'azienda attiva (`CallPubblicaDettaglio`:
 *  confronto, «salvata», visibilità come partner; dal WP7 la sua candidatura
 *  e, per la controparte accettata, i dettagli riservati); l'hook del
 *  dettaglio la tipizza come `CallPubblica`, che serve anche all'anteprima. */
function perLaTuaAzienda(call: CallPubblica): Pick<CallPubblicaDettaglio, "match" | "salvata"> & {
  opt_in: boolean | undefined;
  candidatura: CandidaturaPropria | null;
  controparte: boolean;
} {
  const dettaglio = call as Partial<CallDettaglioAltraAzienda>;
  return {
    match: dettaglio.match ?? null,
    salvata: dettaglio.salvata === true,
    opt_in: dettaglio.opt_in,
    candidatura: dettaglio.candidatura ?? null,
    controparte: dettaglio.vista === "controparte",
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

/** Cosa mostrare accanto a un'azienda suggerita: lo stato del contatto se
 *  c'è già (dal server o appena invitata da qui), altrimenti «Invita» per il
 *  titolare; chi preferisce non ricevere inviti lo dice il suo profilo. */
function AzioneSuggerito({
  suggerito,
  invitata,
  puoInvitare,
  onInvita,
}: {
  suggerito: PartnerSuggerito;
  invitata: boolean;
  puoInvitare: boolean;
  onInvita: () => void;
}) {
  const contatto = (suggerito as PartnerSuggeritoContatto).stato_contatto ?? null;
  if (contatto === "accettata") {
    return (
      <Badge tone="emerald">
        <Check className="size-3.5" aria-hidden />
        Accettata
      </Badge>
    );
  }
  if (contatto === "rifiutata" && !invitata) {
    // Ha già rifiutato un tuo invito su questa call: non si reinvita.
    return (
      <Badge tone="slate">
        <X className="size-3.5" aria-hidden />
        Ha rifiutato l'invito
      </Badge>
    );
  }
  if (contatto === "inviata" || invitata) {
    return (
      <Badge tone="amber">
        <Send className="size-3.5" aria-hidden />
        {invitata && contatto !== "inviata" ? "Invito inviato" : "In attesa di risposta"}
      </Badge>
    );
  }
  if (!suggerito.profilo.accetta_inviti) {
    return <p className="max-w-32 text-right text-xs text-slate-500">Preferisce non ricevere inviti</p>;
  }
  if (!puoInvitare) return null;
  return (
    <Button
      size="sm"
      variant="secondary"
      onClick={onInvita}
      aria-label={`Invita l'azienda con riferimento ${suggerito.pseudonimo}`}
    >
      <Send className="size-4" aria-hidden />
      Invita
    </Button>
  );
}

/** Scheda «Aziende suggerite» (solo per l'azienda che ha creato la call; i
 *  membri la leggono): aziende visibili come partner che coprono qualcosa di
 *  ciò che cerchi, con un riferimento valido solo per questa call, e
 *  «Invita» per il titolare. */
function Suggeriti({ call }: { call: CallVistaCreatore }) {
  const [params, setParams] = useSearchParams();
  const pagina = paginaDa(params);
  const suggeriti = useSuggeriti(call.id, pagina);
  // Il dialog tiene l'ultima azienda scelta anche mentre si chiude.
  const [invito, setInvito] = useState<{ suggerito: PartnerSuggerito | null; aperto: boolean }>({
    suggerito: null,
    aperto: false,
  });
  // Invitate da questa pagina, finché i suggeriti non si rileggono.
  const [invitate, setInvitate] = useState<ReadonlySet<string>>(new Set());
  const puoInvitare = call.editable && call.stato === "pubblicata";
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
          <SuggeritoCard
            key={s.pseudonimo}
            suggerito={s}
            testi={testi}
            azione={
              <AzioneSuggerito
                suggerito={s}
                invitata={invitate.has(s.pseudonimo)}
                puoInvitare={puoInvitare}
                onInvita={() => setInvito({ suggerito: s, aperto: true })}
              />
            }
          />
        ))}
      </ul>
      <Pagination page={dati.page} totalPages={dati.total_pages} onChange={vaiA} />
      <InvitaDialog
        open={invito.aperto}
        onClose={() => setInvito((prima) => ({ ...prima, aperto: false }))}
        callId={call.id}
        suggerito={invito.suggerito}
        posizioni={call.posizioni}
        onInvitata={(pseudonimo) => setInvitate((prima) => new Set(prima).add(pseudonimo))}
      />
    </div>
  );
}

/** Scheda «Candidature e inviti» (azienda che ha creato la call; i membri
 *  leggono): le candidature ricevute da decidere e gli inviti mandati, con
 *  la valutazione in vista «terzi» (solo esiti e fasce). */
function CandidatureCall({ call }: { call: CallVistaCreatore }) {
  const [params, setParams] = useSearchParams();
  const pagina = paginaDa(params);
  const direzione: DirezioneCandidature = params.get("direzione") === "inviate" ? "inviate" : "ricevute";
  const lista = useCandidature({ direzione, stato: null, call_id: call.id }, pagina);
  const testi = new Map(
    call.gap.requisiti.flatMap((r) => (r.etichetta ? [[r.etichetta, r.testo] as const] : [])),
  );
  const aggiorna = (modifica: (p: URLSearchParams) => void) =>
    setParams(
      (prima) => {
        const dopo = new URLSearchParams(prima);
        modifica(dopo);
        return dopo;
      },
      { replace: true },
    );
  const cambiaDirezione = (d: DirezioneCandidature) =>
    aggiorna((p) => {
      if (d === "inviate") p.set("direzione", d);
      else p.delete("direzione");
      p.delete("page");
    });
  const vaiA = (n: number) => {
    aggiorna((p) => {
      if (n > 1) p.set("page", String(n));
      else p.delete("page");
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  let corpo: ReactNode;
  if (lista.isPending) {
    corpo = (
      <div className="space-y-3" aria-hidden>
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  } else if (lista.isError) {
    corpo = (
      <ErrorState
        message={apiErrorMessage(lista.error, "Impossibile caricare candidature e inviti.")}
        onRetry={() => void lista.refetch()}
      />
    );
  } else if (lista.data.items.length === 0) {
    corpo =
      direzione === "ricevute" ? (
        <EmptyState
          title="Nessuna candidatura per ora"
          description={
            call.visibilita === "solo_invitati"
              ? "La call è visibile solo alle aziende che inviti: invitale dalle aziende suggerite."
              : "Quando un'azienda si candida alla call, la trovi qui. Intanto puoi invitare le aziende suggerite."
          }
        />
      ) : (
        <EmptyState
          title="Nessun invito mandato"
          description="Puoi invitare le aziende dalla scheda «Aziende suggerite», finché la call è pubblicata."
        />
      );
  } else {
    corpo = (
      <div className="space-y-3">
        <p className="text-sm text-slate-500" role="status" aria-live="polite">
          {lista.isPlaceholderData
            ? "Aggiornamento…"
            : lista.data.total === 1
              ? direzione === "ricevute"
                ? "1 candidatura"
                : "1 invito"
              : direzione === "ricevute"
                ? `${lista.data.total} candidature`
                : `${lista.data.total} inviti`}
        </p>
        <ul
          className={`space-y-3 transition-opacity ${lista.isPlaceholderData ? "opacity-60" : ""}`}
          aria-busy={lista.isPlaceholderData}
        >
          {lista.data.items.map((c) => (
            <CandidaturaCard key={c.id} candidatura={c} mostraCall={false} testi={testi} />
          ))}
        </ul>
        <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <SceltaDirezione
        valore={direzione}
        onChange={cambiaDirezione}
        etichette={{ ricevute: "Candidature ricevute", inviate: "Inviti mandati" }}
      />
      {!call.editable && <p className="text-sm text-slate-500">{CANDIDATURE_COPY.soloTitolare}</p>}
      {corpo}
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

/** La propria candidatura (o l'invito ricevuto) sulla call di un'altra
 *  azienda, con le azioni del titolare; se non c'è e la call è aperta a
 *  tutti, «Candidati». Dopo che chi ha creato la call ha rifiutato la tua
 *  candidatura non si ripropone (lo ha deciso lui); dopo un ritiro, una
 *  scadenza o un invito che hai rifiutato tu sì. */
function LaTuaCandidatura({
  call,
  propria,
  editable,
}: {
  call: CallDettaglioAltraAzienda;
  propria: CandidaturaPropria | null;
  editable: boolean;
}) {
  const [aperto, setAperto] = useState(false);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const attiva = propria?.stato === "inviata" || propria?.stato === "accettata";
  const rifiutataDalCreatore = propria?.tipo === "candidatura" && propria.stato === "rifiutata";
  const puoCandidarsi =
    call.stato === "pubblicata" && call.visibilita === "pubblica" && !attiva && !rifiutataDalCreatore;

  if (!propria && !puoCandidarsi && !annuncio) return null;
  const titolo = propria?.tipo === "invito" ? "L'invito ricevuto" : "La tua candidatura";

  return (
    <section aria-label={titolo} className="space-y-3">
      <div role="status" aria-live="polite">
        {annuncio && (
          <p className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-800">{annuncio}</p>
        )}
      </div>
      {propria && (
        <div>
          <h2 className="mb-2 font-display text-base font-semibold text-slate-900">{titolo}</h2>
          <CandidaturaPropriaCard candidatura={propria} callId={call.id} posizioni={call.posizioni} />
        </div>
      )}
      {puoCandidarsi && (
        <Card className="p-5">
          <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
            <Send className="size-4 text-brand-500" aria-hidden />
            {propria?.tipo === "candidatura" ? "Candidati di nuovo" : "Ti interessa?"}
          </h2>
          <p className="mt-2 text-sm text-slate-600">
            Candidati con un messaggio: chi ha creato la call vede il profilo partner della tua
            azienda, in forma anonima, e decide se aprire una conversazione.
          </p>
          {editable ? (
            <Button className="mt-3 w-full" onClick={() => setAperto(true)}>
              Candidati
            </Button>
          ) : (
            <p className="mt-2 text-xs text-slate-500">{CANDIDATURE_COPY.soloTitolare}</p>
          )}
        </Card>
      )}
      {editable && (
        <CandidaturaDialog
          open={aperto}
          onClose={() => setAperto(false)}
          call={call}
          onInviata={(inviata) =>
            setAnnuncio(
              "Candidatura inviata: ti avvisiamo quando arriva una risposta." +
                (inviata.quota ? ` ${CANDIDATURE_COPY.quota(inviata.quota.usate, inviata.quota.limite)}.` : ""),
            )
          }
        />
      )}
    </section>
  );
}

/** Per la controparte accettata: budget esatto e dettagli riservati della
 *  call e, solo con la rivelazione accesa (oggi spenta), l'identità
 *  dell'azienda. Mai i bilanci di nessuno. */
function Riservati({ call }: { call: CallDettaglioAltraAzienda }) {
  const identita = call.identita_rivelata ? call.identita : null;
  const dettagli = call.dettagli_riservati ?? null;
  const budget = call.budget_progetto_eur ?? null;
  return (
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Lock className="size-4 text-slate-400" aria-hidden />
        Riservato alle aziende accettate
      </h2>
      <dl className="mt-3 space-y-4">
        {identita?.ragione_sociale && (
          <Voce titolo="Azienda">
            {identita.ragione_sociale}
            {identita.sito_web ? ` · ${identita.sito_web}` : ""}
            {identita.pec ? ` · PEC ${identita.pec}` : ""}
          </Voce>
        )}
        {identita?.referente_nome && <Voce titolo="Referente">{identita.referente_nome}</Voce>}
        <Voce titolo="Budget esatto del progetto">{budget ? formatEur(budget) : "Non indicato"}</Voce>
        <Voce titolo="Dettagli riservati">
          {dettagli ? (
            <span className="whitespace-pre-line">{dettagli}</span>
          ) : (
            <span className="text-slate-500">Nessun dettaglio riservato.</span>
          )}
        </Voce>
      </dl>
    </Card>
  );
}

/** Pagina della call (`?tab=panoramica|suggeriti|candidature`; poi
 *  consorzio…). Per l'azienda che l'ha creata: vista completa, aziende
 *  suggerite con «Invita», candidature e inviti; per le altre la proiezione
 *  pubblica con la propria candidatura («Candidati»), il proprio confronto,
 *  «Salva» (titolare) e «Segnala»; dopo l'accettazione anche i dettagli
 *  riservati. */
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
        <div className="min-w-0 space-y-4">
          <CallPubblicaCard call={pubblica} />
          {tua.controparte && <Riservati call={pubblica as CallDettaglioAltraAzienda} />}
        </div>
        <aside className="space-y-4" aria-label="Per la tua azienda">
          <LaTuaCandidatura
            call={pubblica as CallDettaglioAltraAzienda}
            propria={tua.candidatura}
            editable={azienda?.editable ?? false}
          />
          {/* La controparte accettata non ha confronto né «salvata»: il
              partenariato c'è già. */}
          {!tua.controparte && <Confronto call={pubblica} match={tua.match} />}
          {!tua.controparte && <BannerOptIn optIn={tua.opt_in} />}
          <div className="flex flex-wrap items-start justify-end gap-2">
            {azienda?.editable && !tua.controparte && (
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
          {tab === "candidature" && <CandidatureCall call={call} />}
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
