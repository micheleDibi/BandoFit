import { useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { AntitrustBanner } from "../components/partenariati/AntitrustBanner";
import { BannerIdentitaNonRivelata } from "../components/partenariati/BannerIdentitaNonRivelata";
import { CallStatoBadge } from "../components/partenariati/CallStatoBadge";
import { ChatComposer } from "../components/partenariati/ChatComposer";
import { ChatThread } from "../components/partenariati/ChatThread";
import { Alert } from "../components/ui/Alert";
import { Button, LinkButton } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { DefinitionList, type Definizione } from "../components/ui/Facts";
import { Page } from "../components/ui/Page";
import { BackLink, PageHeader } from "../components/ui/PageHeader";
import { Panel } from "../components/ui/Panel";
import { Status } from "../components/ui/Status";
import { ErrorState, Skeleton } from "../components/ui/states";
import { TextLink } from "../components/ui/TextLink";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useCompany } from "../hooks/useCompany";
import {
  useCaricaPrecedenti,
  useChiudiConversazione,
  useConversazione,
  useMessaggi,
  useSegnaLetto,
} from "../hooks/useConversazioni";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CHAT_COPY, PARTNER_COPY } from "../lib/copy";
import { formatDate } from "../lib/format";
import type { Conversazione, IdentitaRivelata } from "../types";

/** La scheda del browser è visibile (il «letto» si segna solo allora). */
function useSchedaVisibile(): boolean {
  const [visibile, setVisibile] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );
  useEffect(() => {
    const aggiorna = () => setVisibile(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", aggiorna);
    return () => document.removeEventListener("visibilitychange", aggiorna);
  }, []);
  return visibile;
}

/** Come si chiama l'altra azienda: la ragione sociale solo se l'identità è
 *  rivelata (oggi mai), altrimenti «Azienda anonima». */
function nomeControparte(c: Conversazione): string {
  return (c.identita_rivelata ? c.identita?.ragione_sociale : null) ?? PARTNER_COPY.aziendaAnonima;
}

/** Briciole del terzo livello: «Partenariati › Call › Conversazione». */
function Briciole({ c }: { c: Conversazione }) {
  return (
    <nav aria-label="Percorso">
      <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-small text-ink-2">
        <li>
          <TextLink to="/app/partenariati?tab=conversazioni">Partenariati</TextLink>
        </li>
        <li aria-hidden>›</li>
        <li>
          <TextLink to={`/app/partenariati/call/${c.call.id}`}>{c.call.titolo || "Call senza titolo"}</TextLink>
        </li>
        <li aria-hidden>›</li>
        <li aria-current="page">Conversazione</li>
      </ol>
    </nav>
  );
}

/** Identità rivelata dopo l'accettazione (oggi spenta: non compare). Mai
 *  l'email personale del referente. */
function Identita({ identita }: { identita: IdentitaRivelata }) {
  const ruolo =
    identita.referente_ruolo === "titolare"
      ? "titolare"
      : identita.referente_ruolo === "referente"
        ? "referente"
        : null;
  const voci: Definizione[] = [];
  if (identita.sito_web) voci.push({ etichetta: "Sito", valore: identita.sito_web });
  if (identita.pec) voci.push({ etichetta: "PEC", valore: identita.pec });
  if (identita.referente_nome) {
    voci.push({ etichetta: "Referente", valore: `${identita.referente_nome}${ruolo ? ` (${ruolo})` : ""}` });
  }
  return (
    <Panel titolo={identita.ragione_sociale ?? "Dati dell'azienda"}>
      {voci.length > 0 ? <DefinitionList items={voci} /> : <p className="text-body text-ink-3">Nessun dato.</p>}
    </Panel>
  );
}

/** Chiusura (solo il titolare dell'azienda che ha creato la call). */
function ChiudiConversazione({ id }: { id: string }) {
  const chiudi = useChiudiConversazione(id);
  const [aperto, setAperto] = useState(false);
  return (
    <>
      <Button variant="ghost" onClick={() => setAperto(true)}>
        Chiudi la conversazione
      </Button>
      <ConfirmDialog
        open={aperto}
        titolo="Chiudere la conversazione?"
        conferma="Chiudi la conversazione"
        annulla="Non ora"
        distruttiva
        inCorso={chiudi.isPending}
        onConferma={() => chiudi.mutate(undefined, { onSuccess: () => setAperto(false) })}
        onAnnulla={() => setAperto(false)}
      >
        <div className="flex flex-col gap-3">
          <p>
            Nessuna delle due aziende potrà più scrivere. Lo storico dei messaggi resta consultabile.
            Non si può riaprire.
          </p>
          {chiudi.isError && <Alert tono="errore">{apiErrorMessage(chiudi.error)}</Alert>}
        </div>
      </ConfirmDialog>
    </>
  );
}

/** Messaggi, «letto» e scrittura: montato quando la conversazione è nota. */
function Chat({ conversazione, puoScrivere }: { conversazione: Conversazione; puoScrivere: boolean }) {
  const messaggiQ = useMessaggi(conversazione.id);
  const precedenti = useCaricaPrecedenti(conversazione.id);
  const segnaLetto = useSegnaLetto(conversazione.id);
  const visibile = useSchedaVisibile();
  const lettoInviato = useRef(conversazione.letto_fino_a_id);
  const items = messaggiQ.data?.items ?? [];
  const ultimoAltri = items.reduce((massimo, m) => (!m.propria && m.id > massimo ? m.id : massimo), 0);
  const ultimo = items.length ? items[items.length - 1].id : 0;

  // Si segna letto fino all'ultimo messaggio in pagina, solo a scheda
  // visibile e solo se c'è qualcosa di nuovo dell'altra azienda.
  const { mutate: segna } = segnaLetto;
  useEffect(() => {
    if (!visibile || ultimo === 0 || ultimoAltri <= lettoInviato.current) return;
    lettoInviato.current = ultimo;
    segna(ultimo);
  }, [visibile, ultimo, ultimoAltri, segna]);

  // L'errore a tutta pagina solo se non ci sono ancora messaggi: un polling
  // fallito (rete che cade per qualche secondo) non smonta thread e
  // composer, così la bozza e la posizione restano.
  if (!messaggiQ.data) {
    if (messaggiQ.isError) {
      return (
        <ErrorState
          message={apiErrorMessage(messaggiQ.error, "Impossibile caricare i messaggi.")}
          onRetry={() => void messaggiQ.refetch()}
        />
      );
    }
    return (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-16 w-2/3" />
        <Skeleton className="ml-auto h-16 w-2/3" />
        <Skeleton className="h-16 w-1/2" />
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      {messaggiQ.isRefetchError && (
        <Alert tono="attenzione">Non riesco ad aggiornare i messaggi: riprovo tra poco.</Alert>
      )}
      <ChatThread
        messaggi={items}
        lettoFinoAId={conversazione.letto_fino_a_id}
        controparte={nomeControparte(conversazione)}
        haPrecedenti={messaggiQ.data.ha_precedenti}
        onCaricaPrecedenti={() => precedenti.mutate()}
        caricandoPrecedenti={precedenti.isPending}
        errorePrecedenti={precedenti.isError ? apiErrorMessage(precedenti.error) : null}
      />
      {puoScrivere && <ChatComposer conversazioneId={conversazione.id} />}
    </div>
  );
}

/** Pagina della conversazione (`/app/partenariati/conversazioni/:id`): le due
 *  aziende di una candidatura accettata si scrivono. Avviso antitrust fisso
 *  e nota sull'identità non rivelata; scrive solo il titolare, e solo se la
 *  conversazione è aperta ed entrambe le aziende sono ancora attive (sennò
 *  sola lettura con il motivo). La chiude chi ha creato la call. */
export default function ConversazionePartenariato() {
  const { id } = useParams();
  const location = useLocation();
  const { avviso } = useAziendaDaLink();
  const { data: azienda } = useCompany();
  const conversazioneQ = useConversazione(id);
  const editable = azienda?.editable ?? false;
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

  // Il ritorno a «Partenariati» c'è anche in caricamento ed errore (a dati
  // arrivati lo danno le briciole).
  const ritorno = <BackLink label="Partenariati" to="/app/partenariati?tab=conversazioni" />;

  if (!conversazioneQ.data && !conversazioneQ.isError) {
    return (
      <Page variante="sezioni">
        {ritorno}
        {avvisi}
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </Page>
    );
  }
  if (!conversazioneQ.data) {
    // Errore a tutta pagina solo al primo caricamento: una rilettura fallita
    // lascia la conversazione (e la bozza nel composer) com'era.
    return (
      <Page variante="sezioni">
        {ritorno}
        {avvisi}
        {apiErrorCode(conversazioneQ.error) === "not_found" ? (
          <>
            <ErrorState
              title="Conversazione non trovata"
              message="Non esiste oppure non riguarda la tua azienda. Se gestisci più aziende, controlla quella attiva."
            />
            <div>
              <LinkButton to="/app/partenariati?tab=conversazioni" variant="secondary">
                Le tue conversazioni
              </LinkButton>
            </div>
          </>
        ) : (
          <ErrorState
            message={apiErrorMessage(conversazioneQ.error, "Impossibile caricare la conversazione.")}
            onRetry={() => void conversazioneQ.refetch()}
          />
        )}
      </Page>
    );
  }

  const c = conversazioneQ.data;
  const nome = nomeControparte(c);
  const aperta = c.stato === "aperta";
  const attiva = c.controparte.attiva !== false;
  // Cosa si può fare lo decide il server; qui solo il perché della sola
  // lettura, in parole.
  const puoScrivere = c.puo_scrivere;
  const puoChiudere = c.puo_chiudere;
  const solaLettura = puoScrivere
    ? null
    : !aperta
      ? CHAT_COPY.chiusa
      : !attiva
        ? CHAT_COPY.controparteNonAttiva
        : !editable
          ? CHAT_COPY.soloTitolare
          : CHAT_COPY.solaLettura;

  return (
    <Page variante="sezioni">
      <PageHeader
        sopra={
          <>
            <Briciole c={c} />
            <Status tono={aperta ? "aperto" : "chiuso"}>
              <span className="sr-only">Stato: </span>
              {aperta ? "Aperta" : "Chiusa"}
            </Status>
            <CallStatoBadge stato={c.call.stato} />
          </>
        }
        titolo={
          <>
            {nome}
            {c.controparte.pseudonimo && (
              <span className="ml-2 font-sans text-small font-normal text-ink-3">
                <span className="sr-only">riferimento </span>
                {c.controparte.pseudonimo}
              </span>
            )}
          </>
        }
        descrizione={
          <>
            {c.lato === "partner" ? "L'azienda che ha creato la call. " : ""}
            {c.lato === "creatore" ? "Sulla tua call " : "Sulla call "}
            <TextLink to={`/app/partenariati/call/${c.call.id}`}>{c.call.titolo || "Call senza titolo"}</TextLink>{" "}
            per il bando {c.call.bando.titolo}
            {c.chiusa_at ? `, chiusa il ${formatDate(c.chiusa_at)}` : ""}
          </>
        }
        azioni={puoChiudere ? <ChiudiConversazione id={c.id} /> : undefined}
      />
      {avvisi}

      <AntitrustBanner />
      {c.identita_rivelata && c.identita ? (
        <Identita identita={c.identita} />
      ) : (
        <BannerIdentitaNonRivelata />
      )}
      {solaLettura && <Alert tono="info">{solaLettura}</Alert>}

      <Chat key={c.id} conversazione={c} puoScrivere={puoScrivere} />
    </Page>
  );
}
