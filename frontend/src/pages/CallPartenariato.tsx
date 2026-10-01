import { ClipboardList, Inbox, Pencil, Send, Target, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { useRientroPagina } from "../components/partenariati/useRientroPagina";
import { BannerOptIn } from "../components/partenariati/BannerOptIn";
import { BozzeTab } from "../components/partenariati/BozzeTab";
import { CallPubblicaCard } from "../components/partenariati/CallPubblicaCard";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import {
  CandidaturaCard,
  CandidaturaPropriaCard,
  SceltaDirezione,
} from "../components/partenariati/CandidaturaCard";
import { CandidaturaDialog } from "../components/partenariati/CandidaturaDialog";
import { descriviCriterio, linkCall, mostraDecimale, percentuale } from "../components/partenariati/callDati";
import { ConsorzioTab } from "../components/partenariati/ConsorzioTab";
import { ConsultoCallCard } from "../components/partenariati/ConsultoCallCard";
import { CallNonTrovata } from "../components/partenariati/UscitaCallSospesa";
import { paginaDa } from "../components/partenariati/FiltriBacheca";
import { AttenzioneBadge, MatchBadge } from "../components/partenariati/MatchBadge";
import { MatchSpiegazione } from "../components/partenariati/MatchSpiegazione";
import { InvitaDialog } from "../components/partenariati/InvitaDialog";
import { etichettaForma } from "../components/partenariati/PartenariatoRegole";
import { SalvaCallButton } from "../components/partenariati/SalvaCallButton";
import { SegnalaDialog } from "../components/partenariati/SegnalaDialog";
import { SuggeritoCard } from "../components/partenariati/SuggeritoCard";
import { useNomiCall } from "../components/partenariati/useNomiCall";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Due, tempoRelativo } from "../components/ui/Due";
import { DefinitionList, type Definizione } from "../components/ui/Facts";
import { Page } from "../components/ui/Page";
import { BackLink, PageHeader } from "../components/ui/PageHeader";
import { Pagination } from "../components/ui/Pagination";
import { Panel } from "../components/ui/Panel";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { Status, type TonoStatus } from "../components/ui/Status";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { TabPanel, Tabs, type Scheda } from "../components/ui/Tabs";
import { TextLink } from "../components/ui/TextLink";
import {
  FOCUS_SU_FASCIA,
  GHOST_SU_FASCIA,
  LINK_SU_FASCIA,
  TESTO_SU_FASCIA,
} from "../components/shared/fascia";
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
import { useTab } from "../hooks/useTab";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { BOZZE_COPY, CALL_COPY, CANDIDATURE_COPY, PARTENARIATO_COPY } from "../lib/copy";
import { formatDate, formatDateTime, formatEur } from "../lib/format";
import type {
  CandidaturaPropria,
  CallDettaglioAltraAzienda,
  CallPubblica,
  CallPubblicaDettaglio,
  CallVistaCreatore,
  DirezioneCandidature,
  EsitoCoperturaCall,
  MatchOut,
  PartnerSuggerito,
  PartnerSuggeritoContatto,
} from "../types";

type Tab = "panoramica" | "suggeriti" | "candidature" | "consorzio" | "bozze";

const PREFISSO_SCHEDE = "call";

/** Schede per l'azienda che ha creato la call: i suggeriti solo quando la
 *  call è pubblicata (prima nessuna azienda la vede, dopo non si propone);
 *  candidature, inviti, consorzio e bozze dei documenti (WP10: ruoli e quote
 *  vengono dal consorzio, che nasce alla pubblicazione) da quando è stata
 *  pubblicata (restano consultabili anche dopo la chiusura). */
function schedeCreatore(call: CallVistaCreatore): Scheda<Tab>[] {
  const schede: Scheda<Tab>[] = [{ id: "panoramica", label: "Panoramica" }];
  if (call.stato === "pubblicata") schede.push({ id: "suggeriti", label: "Aziende suggerite" });
  if (call.pubblicata_at) schede.push({ id: "candidature", label: "Candidature e inviti" });
  if (call.pubblicata_at) schede.push({ id: "consorzio", label: "Consorzio" });
  if (call.pubblicata_at) schede.push({ id: "bozze", label: BOZZE_COPY.titolo });
  return schede;
}

/** Schede per le altre aziende: solo la controparte accettata (membro del
 *  consorzio) ha, oltre alla call, le schede del consorzio e delle bozze dei
 *  documenti. */
function schedeAltraAzienda(call: CallPubblica): Scheda<Tab>[] {
  if (!perLaTuaAzienda(call).controparte) return [];
  return [
    { id: "panoramica", label: "La call" },
    { id: "consorzio", label: "Consorzio" },
    { id: "bozze", label: BOZZE_COPY.titolo },
  ];
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

/** La tua copertura di un requisito, in parole (mai mostrata agli altri). */
const TONI_COPERTURA: Record<EsitoCoperturaCall, TonoStatus> = {
  coperto: "aperto",
  non_coperto: "chiuso",
  dato_mancante: "attenzione",
  incerto: "attenzione",
  non_valutabile: "neutro",
};

function Copertura({ esito }: { esito: EsitoCoperturaCall }) {
  return (
    <Status tono={TONI_COPERTURA[esito] ?? "neutro"}>{CALL_COPY.esitiCopertura[esito] ?? esito}</Status>
  );
}

/** Chiusura manuale dalla testata: completata (partenariato fatto) o
 *  annullata; una sola finestra di conferma con i testi del caso. */
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
        <Button variant="secondary" className={FOCUS_SU_FASCIA} onClick={() => setEsito("completata")}>
          Chiudi la call
        </Button>
      )}
      {/* Sta sulla fascia navy dell'intestazione: distruttiva, fondo bianco. */}
      <Button variant="danger" className={FOCUS_SU_FASCIA} onClick={() => setEsito("annullata")}>
        {bozza ? "Annulla la bozza" : "Annulla la call"}
      </Button>
      <ConfirmDialog
        open={esito !== null}
        titolo={
          esito === "completata"
            ? "Chiudere la call come completata?"
            : bozza
              ? "Annullare la bozza?"
              : "Annullare la call?"
        }
        conferma={esito === "completata" ? "Chiudi la call" : bozza ? "Annulla la bozza" : "Annulla la call"}
        annulla="Non ora"
        distruttiva={esito !== "completata"}
        inCorso={chiudi.isPending}
        onConferma={conferma}
        onAnnulla={() => setEsito(null)}
      >
        <div className="flex flex-col gap-3">
          <p>
            {esito === "completata"
              ? "Hai trovato i partner: la call non riceve più candidature e non conta più tra le call attive del tuo piano."
              : bozza
                ? "La bozza si chiude e non si può riprendere: per lo stesso bando potrai crearne una nuova."
                : "La call non riceve più candidature e non conta più tra le call attive del tuo piano. Non si può riaprire."}
          </p>
          {chiudi.isError && <Alert tono="errore">{apiErrorMessage(chiudi.error)}</Alert>}
        </div>
      </ConfirmDialog>
    </>
  );
}

function Versioni({ call }: { call: CallVistaCreatore }) {
  const versioni = useVersioniCall(call.id, !!call.pubblicata_at);
  if (!call.pubblicata_at) return null;
  return (
    <Section>
      <SectionHeader titolo="Versioni pubblicate" />
      {versioni.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : versioni.isError ? (
        <Alert tono="errore">{apiErrorMessage(versioni.error, "Impossibile caricare le versioni.")}</Alert>
      ) : (versioni.data ?? []).length === 0 ? (
        <p className="text-body text-ink-3">Nessuna versione.</p>
      ) : (
        <ol className="flex flex-col gap-1 text-body text-ink-2">
          {[...(versioni.data ?? [])]
            .sort((a, b) => b.versione - a.versione)
            .map((v) => (
              <li key={v.versione}>
                Versione {v.versione}, {formatDateTime(v.created_at)}
              </li>
            ))}
        </ol>
      )}
    </Section>
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

  const sintesi: Definizione[] = [
    { etichetta: "Il tuo ruolo", valore: CALL_COPY.ruoliCreatore[call.ruolo_creatore] },
    {
      etichetta: "Forma prevista",
      valore: call.forma_aggregazione_prevista
        ? etichettaForma(call.forma_aggregazione_prevista, vocabolario)
        : "Non ancora decisa",
    },
    { etichetta: "Candidature fino al", valore: call.scadenza_call ? formatDate(call.scadenza_call) : "Da decidere" },
    {
      etichetta: "Budget (fascia pubblica)",
      valore: call.budget_fascia ? CALL_COPY.fasceBudget[call.budget_fascia] : "Non indicato",
    },
    {
      etichetta: "Budget esatto (riservato)",
      valore: call.budget_progetto_eur ? formatEur(call.budget_progetto_eur) : "Non indicato",
    },
    // Dopo la pubblicazione la quota vive nel consorzio: qui resta quella di partenza.
    {
      etichetta: call.pubblicata_at ? "La tua quota alla pubblicazione" : "La tua quota",
      valore: call.quota_creatore_pct ? `${mostraDecimale(call.quota_creatore_pct)}%` : "Non indicata",
    },
    { etichetta: "Chi la vede", valore: CALL_COPY.visibilita[call.visibilita] },
  ];
  if (call.pubblicata_at) sintesi.push({ etichetta: "Pubblicata il", valore: formatDate(call.pubblicata_at) });
  if (call.versione > 0) sintesi.push({ etichetta: "Versione", valore: String(call.versione) });

  return (
    <Card className="flex flex-col gap-8 sm:p-8">
      {call.motivo_chiusura && (
        <Alert tono="info">
          {CALL_COPY.motiviChiusura[call.motivo_chiusura] ?? call.motivo_chiusura}
          {call.chiusa_at ? ` Chiusa il ${formatDate(call.chiusa_at)}.` : ""}
        </Alert>
      )}
      {call.stato === "sospesa_moderazione" && (
        <Alert tono="errore" titolo="La call è sospesa dalla moderazione">
          {call.sospesa_at ? `Dal ${formatDate(call.sospesa_at)}` : ""}
          {call.sospeso_motivo ? `${call.sospesa_at ? ": " : ""}${call.sospeso_motivo}` : ""}
          {call.sospesa_at || call.sospeso_motivo ? ". " : ""}
          Per chiarimenti scrivi all'assistenza.
        </Alert>
      )}
      {!call.editable && <p className="text-small text-ink-3">{CALL_COPY.soloTitolare}</p>}
      {call.editable && aperta && (
        <p>
          <TextLink to={`/app/partenariati/call/${call.id}/modifica?passo=6`}>Come ti vedono</TextLink>
        </p>
      )}

      <Section>
        <SectionHeader titolo="In sintesi" />
        <DefinitionList items={sintesi} />
        <Alert tono="info">{call.anonima ? CALL_COPY.notaAnonima : CALL_COPY.notaNominativa}</Alert>
      </Section>

      <Section>
        <SectionHeader titolo="Il progetto" />
        {call.descrizione_pubblica ? (
          <p className="max-w-[680px] whitespace-pre-line text-prose text-ink">{call.descrizione_pubblica}</p>
        ) : (
          <p className="text-body text-ink-3">Non ancora scritto.</p>
        )}
      </Section>
      {call.profilo_partner_ideale && (
        <Section>
          <SectionHeader titolo="Il partner ideale" />
          <p className="max-w-[680px] whitespace-pre-line text-prose text-ink">{call.profilo_partner_ideale}</p>
        </Section>
      )}
      {call.dettagli_riservati && (
        <Section>
          <SectionHeader titolo="Dettagli riservati (solo per chi accetti)" />
          <p className="max-w-[680px] whitespace-pre-line text-prose text-ink">{call.dettagli_riservati}</p>
        </Section>
      )}

      <Section>
        <SectionHeader titolo={`Requisiti (ne cerchi ${cercati})`} />
        {requisiti.length === 0 ? (
          <p className="text-body text-ink-3">Nessun requisito salvato.</p>
        ) : (
          <ul className="flex flex-col">
            {requisiti.map((r, i) => (
              <li key={r.id ?? i} className="flex flex-wrap items-start gap-x-4 gap-y-1 border-b border-line py-3 text-body">
                {r.etichetta && <Badge className="mt-0.5 shrink-0 tabular-nums">{r.etichetta}</Badge>}
                <div className="min-w-0 flex-1">
                  <p className="text-ink">
                    {r.testo}
                    {r.cercato && <span className="ml-2 text-small font-medium text-accent-hover">Lo cerchi</span>}
                  </p>
                  <p className="text-small text-ink-3">
                    {descriviCriterio(r.criterio, nomi)}, {CALL_COPY.ambiti[r.ambito]}
                  </p>
                </div>
                {r.copertura_creatore && <Copertura esito={r.copertura_creatore} />}
              </li>
            ))}
          </ul>
        )}
        <p className="text-small text-ink-3">La tua copertura dei requisiti la vedi solo tu.</p>
      </Section>

      <Section>
        <SectionHeader titolo="Posizioni cercate" />
        {call.posizioni.length === 0 ? (
          <p className="text-body text-ink-3">Nessuna posizione salvata.</p>
        ) : (
          <ul className="flex flex-col">
            {call.posizioni.map((p) => (
              <li key={p.id} className="flex flex-col gap-1 border-b border-line py-3">
                <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="font-medium text-ink">{p.titolo}</span>
                  <span className="text-small text-ink-2">{p.ruolo === "capofila" ? "Capofila" : "Partner"}</span>
                  {p.numero > 1 && <span className="text-small text-ink-2">{p.numero} partner</span>}
                  {p.quota_ipotizzata_pct && (
                    <span className="text-small text-ink-2">Quota {percentuale(p.quota_ipotizzata_pct)}</span>
                  )}
                </p>
                {p.requisiti_ids.length > 0 && (
                  <p className="text-small text-ink-3">
                    Copre i requisiti: {p.requisiti_ids.map((id) => etichetteRequisiti.get(id) ?? "—").join(", ")}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section>
        <SectionHeader
          titolo="Regole del bando confermate"
          azione={
            call.editable && call.stato === "bozza" ? (
              <TextLink to={`/app/partenariati/call/${call.id}/modifica?passo=2`}>Rivedi</TextLink>
            ) : undefined
          }
        />
        {regole ? (
          <div className="flex flex-col gap-1 text-body text-ink-2">
            <p>Modalità: {PARTENARIATO_COPY.modalita[regole.modalita.valore] ?? regole.modalita.valore}</p>
            <p className="text-small text-ink-3">
              {[
                regole.forme_ammesse.length ? `${regole.forme_ammesse.length} forme ammesse` : null,
                regole.composizione.length ? `${regole.composizione.length} voci sulla composizione` : null,
                regole.quote.length ? `${regole.quote.length} quote` : null,
                regole.vincoli.length ? `${regole.vincoli.length} vincoli` : null,
                regole.regole_finanziarie.length ? `${regole.regole_finanziarie.length} requisiti economici` : null,
              ]
                .filter(Boolean)
                .join(", ") || "Nessuna voce oltre alla modalità."}
            </p>
            {call.esclusivita && <p>Il bando ammette un solo partenariato per soggetto.</p>}
            {call.regole_confermate_at && (
              <p className="text-small text-ink-3">Confermate il {formatDate(call.regole_confermate_at)}.</p>
            )}
          </div>
        ) : (
          <p className="text-body text-ink-3">Non ancora confermate.</p>
        )}
      </Section>

      <Versioni call={call} />
    </Card>
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
  if (contatto === "accettata") return <Status tono="aperto">Accettata</Status>;
  if (contatto === "rifiutata" && !invitata) {
    // Ha già rifiutato un tuo invito su questa call: non si reinvita.
    return <Status tono="chiuso">Ha rifiutato l'invito</Status>;
  }
  if (contatto === "inviata" || invitata) {
    return (
      <Status tono="in-apertura">
        {invitata && contatto !== "inviata" ? "Invito inviato" : "In attesa di risposta"}
      </Status>
    );
  }
  if (!suggerito.profilo.accetta_inviti) {
    return <p className="text-small text-ink-3">Preferisce non ricevere inviti</p>;
  }
  if (!puoInvitare) return null;
  return (
    <Button
      size="sm"
      variant="secondary"
      onClick={onInvita}
      aria-label={`Invita l'azienda con riferimento ${suggerito.pseudonimo}`}
    >
      Invita
    </Button>
  );
}

function ListaInCaricamento() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
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
  const scrivi = (n: number, replace: boolean) =>
    setParams(
      (prima) => {
        const dopo = new URLSearchParams(prima);
        if (n > 1) dopo.set("page", String(n));
        else dopo.delete("page");
        return dopo;
      },
      { replace },
    );
  const vaiA = (n: number) => {
    scrivi(n, false);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const inRientro = useRientroPagina(suggeriti.data, pagina, suggeriti.isPlaceholderData, (n) =>
    scrivi(n, true),
  );

  if (suggeriti.isPending || inRientro) return <ListaInCaricamento />;
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
        icon={Users}
        area="partenariati"
        title="Nessuna azienda suggerita per ora"
        description="Nessuna azienda visibile come partner copre i requisiti che cerchi. Prova ad allargare le posizioni o i requisiti: i suggerimenti si aggiornano da soli."
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-body text-ink-2">
        Aziende che hanno scelto di farsi trovare come partner e coprono qualcosa di ciò che
        cerchi, dalle più adatte. Non sanno che le stai guardando. Le aziende collegate alla tua
        non compaiono.
      </p>
      <p className="text-small text-ink-3" role="status" aria-live="polite">
        {suggeriti.isPlaceholderData
          ? "Aggiornamento…"
          : dati.total === 1
            ? "1 azienda suggerita"
            : `${dati.total} aziende suggerite`}
      </p>
      <ul
        className={`flex flex-col gap-3 transition-opacity ${suggeriti.isPlaceholderData ? "opacity-60" : ""}`}
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
  const scriviPagina = (n: number) =>
    aggiorna((p) => {
      if (n > 1) p.set("page", String(n));
      else p.delete("page");
    });
  const vaiA = (n: number) => {
    scriviPagina(n);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  // `aggiorna` scrive già in sostituzione: il rientro è la stessa scrittura, senza scroll.
  const inRientro = useRientroPagina(lista.data, pagina, lista.isPlaceholderData, scriviPagina);

  let corpo: ReactNode;
  if (lista.isPending || inRientro) {
    corpo = <ListaInCaricamento />;
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
          icon={Inbox}
          area="partenariati"
          title="Nessuna candidatura per ora"
          description={
            call.visibilita === "solo_invitati"
              ? "La call è visibile solo alle aziende che inviti: invitale dalle aziende suggerite."
              : "Quando un'azienda si candida alla call, la trovi qui. Intanto puoi invitare le aziende suggerite."
          }
        />
      ) : (
        <EmptyState
          icon={Send}
          area="partenariati"
          title="Nessun invito mandato"
          description="Puoi invitare le aziende dalla scheda «Aziende suggerite», finché la call è pubblicata."
        />
      );
  } else {
    corpo = (
      <div className="flex flex-col gap-3">
        <p className="text-small text-ink-3" role="status" aria-live="polite">
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
          className={`flex flex-col gap-3 transition-opacity ${lista.isPlaceholderData ? "opacity-60" : ""}`}
          aria-busy={lista.isPlaceholderData}
        >
          {lista.data.items.map((c) => (
            <CandidaturaCard key={c.id} candidatura={c} mostraCall={false} testi={testi} />
          ))}
        </ul>
        <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={vaiA} />
        {direzione === "ricevute" && call.editable && (
          <p className="text-small text-ink-3">
            Quando accetti una candidatura si apre una conversazione con l'azienda. Da quel momento
            vede anche i dettagli riservati e il budget esatto della call.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <SceltaDirezione
        valore={direzione}
        onChange={cambiaDirezione}
        etichette={{ ricevute: "Candidature ricevute", inviate: "Inviti mandati" }}
      />
      {!call.editable && <p className="text-small text-ink-3">{CANDIDATURE_COPY.soloTitolare}</p>}
      {corpo}
    </div>
  );
}

/** Il confronto della tua azienda con la call di un'altra azienda: arriva
 *  con il dettaglio della call (vista «proprio», con i tuoi numeri). */
function Confronto({ call, match }: { call: CallPubblica; match: MatchOut | null }) {
  const testi = new Map(call.requisiti.map((r) => [r.etichetta, r.testo] as const));
  return (
    <Panel titolo="La tua azienda e questa call" icon={Target} area="partenariati">
      {!match ? (
        <p className="text-body text-ink-2">
          La tua azienda non risulta tra quelle adatte a questa call. Controlla i requisiti e le
          posizioni cercate.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <MatchBadge match={match} />
            <AttenzioneBadge match={match} />
          </div>
          <MatchSpiegazione match={match} testi={testi} />
        </>
      )}
    </Panel>
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
    <section aria-label={titolo} className="flex flex-col gap-4">
      <div aria-live="polite">
        {annuncio && (
          <Alert tono="ok" ruolo="none">
            {annuncio}
          </Alert>
        )}
      </div>
      {propria && (
        <Panel titolo={titolo} icon={Send} area="partenariati">
          <CandidaturaPropriaCard candidatura={propria} callId={call.id} posizioni={call.posizioni} />
        </Panel>
      )}
      {puoCandidarsi && (
        <Panel
          titolo={propria?.tipo === "candidatura" ? "Candidati di nuovo" : "Ti interessa?"}
          icon={Send}
          area="partenariati"
        >
          <p className="text-body text-ink-2">
            Candidati con un messaggio: chi ha creato la call vede il profilo partner della tua
            azienda, in forma anonima, e decide se aprire una conversazione.
          </p>
          {editable ? (
            <div>
              <Button onClick={() => setAperto(true)}>Candidati</Button>
            </div>
          ) : (
            <p className="text-small text-ink-3">{CANDIDATURE_COPY.soloTitolare}</p>
          )}
        </Panel>
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
 *  call e, solo se all'accettazione tutte e due le aziende avevano (e hanno
 *  ancora) l'identità verificata dalla piattaforma, l'identità dell'azienda.
 *  Mai i bilanci di nessuno. */
function Riservati({ call }: { call: CallDettaglioAltraAzienda }) {
  const identita = call.identita_rivelata ? call.identita : null;
  const dettagli = call.dettagli_riservati ?? null;
  const budget = call.budget_progetto_eur ?? null;
  const voci: Definizione[] = [];
  if (identita?.ragione_sociale) {
    voci.push({
      etichetta: "Azienda",
      valore: identita.ragione_sociale,
      nota: [identita.sito_web, identita.pec ? `PEC ${identita.pec}` : null].filter(Boolean).join(", ") || undefined,
    });
  }
  if (identita?.referente_nome) voci.push({ etichetta: "Referente", valore: identita.referente_nome });
  voci.push({ etichetta: "Budget esatto del progetto", valore: budget ? formatEur(budget) : "Non indicato" });
  voci.push({
    etichetta: "Dettagli riservati",
    valore: dettagli ? (
      <span className="whitespace-pre-line">{dettagli}</span>
    ) : (
      <span className="text-ink-3">Nessun dettaglio riservato.</span>
    ),
  });
  return (
    <Section>
      <SectionHeader titolo="Riservato alle aziende accettate" />
      <DefinitionList items={voci} />
    </Section>
  );
}

/** «La call in breve»: posizioni cercate, scadenza del bando e visibilità,
 *  nel pannello della colonna laterale. */
function CallInBreve({ call }: { call: CallVistaCreatore }) {
  const n = call.posizioni.length;
  return (
    <Panel titolo="La call in breve" icon={ClipboardList} area="partenariati">
      <div className="flex flex-col gap-1">
        <p className="font-semibold text-ink">
          {n === 0 ? "Nessuna posizione cercata" : n === 1 ? "1 posizione cercata" : `${n} posizioni cercate`}
        </p>
        {n > 0 && (
          <ul className="flex flex-col gap-0.5 text-small text-ink-2">
            {call.posizioni.map((p) => (
              <li key={p.id}>{p.titolo}</li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-col gap-1 border-t border-line pt-3">
        <p className="font-semibold text-ink">Scadenza del bando</p>
        <Due data={call.bando.scadenza} />
      </div>
      <div className="flex flex-col gap-1 border-t border-line pt-3">
        <p className="font-semibold text-ink">Visibilità</p>
        <p className="text-small text-ink-2">
          {CALL_COPY.visibilita[call.visibilita]}.{" "}
          {call.anonima
            ? "La call è anonima: le altre aziende non vedono il nome della tua."
            : "La call mostra il nome della tua azienda."}
        </p>
      </div>
    </Panel>
  );
}

/** Pagina della call (`?tab=panoramica|suggeriti|candidature|consorzio|bozze`).
 *  Per l'azienda che l'ha creata: vista completa, aziende suggerite con
 *  «Invita», candidature e inviti, consorzio, bozze dei documenti; per le
 *  altre la proiezione pubblica con la propria candidatura («Candidati»), il
 *  proprio confronto, «Salva» (titolare) e «Segnala»; dopo l'accettazione
 *  anche i dettagli riservati e le schede del consorzio e delle bozze. */
export default function CallPartenariato() {
  const { id } = useParams();
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
  // Il type guard va usato direttamente nella condizione: in una variabile
  // booleana non restringerebbe `callQ.data`.
  const schede: Scheda<Tab>[] = isVistaCreatore(callQ.data)
    ? schedeCreatore(callQ.data)
    : callQ.data
      ? schedeAltraAzienda(callQ.data)
      : [];
  const { tab, setTab: cambiaScheda } = useTab<Tab>(
    schede.map((s) => s.id),
    { default: "panoramica" },
  );
  const editable = azienda?.editable ?? false;

  const avvisi = (
    <>
      {avviso && <Alert tono="attenzione">{avviso}</Alert>}
      <div aria-live="polite">
        {annuncio && (
          <Alert tono="ok" ruolo="none">
            {annuncio}
          </Alert>
        )}
      </div>
    </>
  );

  // Il ritorno a «Partenariati» c'è in ogni stato, anche in caricamento ed errore.
  const ritorno = <BackLink label="Partenariati" to="/app/partenariati" />;

  if (callQ.isPending) {
    return (
      <Page variante="dettaglio">
        {ritorno}
        {avvisi}
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </Page>
    );
  }
  if (callQ.isError) {
    return (
      <Page variante="dettaglio">
        {ritorno}
        {avvisi}
        {apiErrorCode(callQ.error) === "not_found" && id ? (
          // «Call non trovata», oppure (WP9) l'uscita dal consorzio di una
          // call sospesa per moderazione di cui la tua azienda fa parte.
          <CallNonTrovata callId={id} />
        ) : (
          <ErrorState
            message={apiErrorMessage(callQ.error, "Impossibile caricare la call.")}
            onRetry={() => void callQ.refetch()}
          />
        )}
      </Page>
    );
  }

  const call = callQ.data;
  const titolo = call.titolo || "Call senza titolo";
  const relativo = call.scadenza_call ? tempoRelativo(call.scadenza_call) : "";
  const sopra = (
    <>
      <CallStatoBadge stato={call.stato} />
      {call.scadenza_call && (
        <span className="text-small">Candidature fino al {formatDate(call.scadenza_call)}</span>
      )}
      {relativo && <span className={`text-caption ${TESTO_SU_FASCIA}`}>{relativo}</span>}
    </>
  );
  const descrizione = (
    <>
      Per il bando{" "}
      <TextLink to={`/app/bandi/${call.bando.slug}`} className={LINK_SU_FASCIA}>
        {call.bando.titolo}
      </TextLink>
      {call.bando.scadenza ? `, che scade il ${formatDate(call.bando.scadenza)}` : ""}
    </>
  );
  const schedeVisibili = schede.length > 1 && (
    <Tabs tabs={schede} attivo={tab} onChange={cambiaScheda} ariaLabel="Sezioni della call" prefisso={PREFISSO_SCHEDE} />
  );

  if (!isVistaCreatore(call)) {
    const pubblica = call as CallPubblica;
    const tua = perLaTuaAzienda(pubblica);
    const dettaglio = pubblica as CallDettaglioAltraAzienda;
    const vistaCall = (
      <Card className="flex flex-col gap-8 sm:p-8">
        <CallPubblicaCard call={pubblica} senzaTitolo />
        {tua.controparte && <Riservati call={dettaglio} />}
      </Card>
    );
    const laterale = (
      <>
        <LaTuaCandidatura call={dettaglio} propria={tua.candidatura} editable={editable} />
        {/* La controparte accettata non ha confronto né «salvata»: il partenariato c'è già. */}
        {!tua.controparte && <Confronto call={pubblica} match={tua.match} />}
        {!tua.controparte && <BannerOptIn optIn={tua.opt_in} />}
      </>
    );
    const inPanoramica = !tua.controparte || tab === "panoramica";
    return (
      <Page
        variante="dettaglio"
        intestazione={
          <>
            <PageHeader
              area="partenariati"
              indietro={{ label: "Partenariati", to: "/app/partenariati" }}
              sopra={sopra}
              titolo={titolo}
              descrizione={descrizione}
              azioni={
                <>
                  {editable && !tua.controparte && (
                    <SalvaCallButton
                      id={pubblica.id}
                      titolo={titolo}
                      salvata={tua.salvata}
                      variant="secondary"
                      className={FOCUS_SU_FASCIA}
                    />
                  )}
                  <Button variant="ghost" className={GHOST_SU_FASCIA} onClick={() => setSegnala(true)}>
                    Segnala
                  </Button>
                </>
              }
            />
            {avvisi}
            {schedeVisibili}
          </>
        }
        laterale={inPanoramica ? laterale : undefined}
      >
        {/* Il pannello solo se ci sono le schede: senza, `aria-labelledby`
            punterebbe a una scheda che non esiste. */}
        {tua.controparte && schede.length > 1 ? (
          <TabPanel key={tab} id={tab} attivo={tab} prefisso={PREFISSO_SCHEDE}>
            {tab === "consorzio" ? (
              <ConsorzioTab callId={pubblica.id} />
            ) : tab === "bozze" ? (
              <BozzeTab callId={pubblica.id} editable={editable} />
            ) : (
              vistaCall
            )}
          </TabPanel>
        ) : (
          vistaCall
        )}
        <SegnalaDialog open={segnala} onClose={() => setSegnala(false)} oggettoTipo="call" oggettoId={pubblica.id} />
      </Page>
    );
  }

  const aperta = call.stato === "bozza" || call.stato === "pubblicata";
  const contenutoScheda = (
    <>
      {tab === "panoramica" && <Panoramica call={call} />}
      {tab === "suggeriti" && <Suggeriti call={call} />}
      {tab === "candidature" && <CandidatureCall call={call} />}
      {tab === "consorzio" && <ConsorzioTab callId={call.id} posizioni={call.posizioni} />}
      {tab === "bozze" && <BozzeTab callId={call.id} editable={call.editable} />}
    </>
  );
  return (
    <Page
      variante="dettaglio"
      intestazione={
        <>
          <PageHeader
            area="partenariati"
            indietro={{ label: "Partenariati", to: "/app/partenariati?tab=mie" }}
            sopra={sopra}
            titolo={titolo}
            descrizione={descrizione}
            azioni={
              call.editable && aperta ? (
                <>
                  {call.stato === "bozza" ? (
                    <LinkButton to={linkCall(call)} variant="inverse">
                      <Pencil className="size-4" aria-hidden />
                      Riprendi dal passo {call.wizard_passo}
                    </LinkButton>
                  ) : (
                    <LinkButton
                      to={`/app/partenariati/call/${call.id}/modifica?passo=5`}
                      variant="secondary"
                      className={FOCUS_SU_FASCIA}
                    >
                      <Pencil className="size-4" aria-hidden />
                      Modifica la call
                    </LinkButton>
                  )}
                  <ChiudiCall call={call} />
                </>
              ) : undefined
            }
          />
          {avvisi}
          {schedeVisibili}
        </>
      }
      laterale={
        <>
          <CallInBreve call={call} />
          <ConsultoCallCard call={call} />
        </>
      }
    >
      {/* Senza schede (bozza) niente pannello: `aria-labelledby` punterebbe a
          una scheda che non esiste. */}
      {schede.length > 1 ? (
        <TabPanel key={tab} id={tab} attivo={tab} prefisso={PREFISSO_SCHEDE}>
          {contenutoScheda}
        </TabPanel>
      ) : (
        contenutoScheda
      )}
    </Page>
  );
}
