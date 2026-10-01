import { useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { TestoLungo } from "../components/partenariati/CampiCall";
import {
  codiceSegnalazione,
  StatoSegnalazioneBadge,
} from "../components/partenariati/StatoSegnalazioneBadge";
import { Alert } from "../components/ui/Alert";
import { Button, LinkButton } from "../components/ui/Button";
import { Facts } from "../components/ui/Facts";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useRicorsoSegnalazione, useSegnalazione } from "../hooks/useSegnalazioni";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CALL_COPY, MODERAZIONE_COPY } from "../lib/copy";
import { formatDate, formatDateTime } from "../lib/format";
import type { SegnalazioneEsito } from "../types";

/** La decisione motivata: per chi ha segnalato l'esito; per l'azienda autrice
 *  anche la motivazione formale (statement of reasons) con le vie di ricorso.
 *  Mai l'identità di chi ha segnalato. */
function Decisione({ s }: { s: SegnalazioneEsito }) {
  if (!s.decisione) {
    return (
      <Section>
        <SectionHeader titolo="Decisione" />
        <p className="text-body text-ink-2">{MODERAZIONE_COPY.inAttesa}</p>
      </Section>
    );
  }
  // Per l'azienda autrice una restrizione nata dal ricorso accolto di chi
  // aveva segnalato (la prima decisione era «nessuna azione»): in testa la
  // restrizione in vigore, con la data e la motivazione del ricorso.
  const dalRicorso =
    s.ruolo === "autore" &&
    s.decisione === "nessuna_azione" &&
    !!s.decisione_effettiva &&
    s.decisione_effettiva !== "nessuna_azione";
  const decisione = dalRicorso && s.decisione_effettiva ? s.decisione_effettiva : s.decisione;
  const data = dalRicorso ? (s.ricorso?.deciso_at ?? null) : s.deciso_at;
  const perche = dalRicorso ? (s.ricorso?.motivazione ?? null) : s.motivazione;
  const testo =
    s.ruolo === "autore"
      ? MODERAZIONE_COPY.decisioniAutore[decisione]
      : MODERAZIONE_COPY.decisioniSegnalante[decisione];
  return (
    <Section>
      <SectionHeader titolo={`Decisione${data ? ` del ${formatDate(data)}` : ""}`} />
      <p className="text-body text-ink">{testo}</p>
      {dalRicorso && <p className="text-body text-ink-2">{MODERAZIONE_COPY.decisioneDalRicorso}</p>}
      {perche && (
        <div className="flex flex-col gap-1">
          <p className="text-small font-medium text-ink-3">Perché</p>
          <p className="whitespace-pre-line text-body text-ink-2">{perche}</p>
        </div>
      )}
      {s.sor_testo && (
        <details className="group">
          <summary className="inline-flex cursor-pointer items-center gap-1 rounded-mark text-small font-medium text-accent-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
            {MODERAZIONE_COPY.sorTitolo}
          </summary>
          <p className="mt-2 whitespace-pre-line text-body text-ink-2">{s.sor_testo}</p>
        </details>
      )}
    </Section>
  );
}

/** Ricorso interno (uno solo, entro 6 mesi): stato, esito oppure il modulo
 *  per presentarlo quando il server dice che si può. */
function Ricorso({ s }: { s: SegnalazioneEsito }) {
  const ricorso = useRicorsoSegnalazione(s.id);
  const [testo, setTesto] = useState("");
  const [errore, setErrore] = useState<string | null>(null);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const min = MODERAZIONE_COPY.ricorsoMin;
  const max = MODERAZIONE_COPY.ricorsoMax;

  if (!s.decisione) return null;

  const invia = async () => {
    setErrore(null);
    const pulito = testo.trim();
    if (pulito.length < min) {
      setErrore(`Scrivi almeno ${min} caratteri.`);
      return;
    }
    try {
      await ricorso.mutateAsync({ testo: pulito });
      setTesto("");
      setAnnuncio(MODERAZIONE_COPY.ricorsoInviato);
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  let corpo: ReactNode;
  if (s.ricorso) {
    const r = s.ricorso;
    corpo = (
      <div className="flex flex-col gap-3">
        <p className="text-body text-ink">
          {r.da === s.ruolo ? "Hai presentato un ricorso" : "È stato presentato un ricorso"}
          {r.at ? ` il ${formatDate(r.at)}` : ""}.
        </p>
        {r.testo && <p className="whitespace-pre-line text-body text-ink-2">{r.testo}</p>}
        {r.esito ? (
          <div className="flex flex-col gap-1">
            <p className="font-medium text-ink">
              {MODERAZIONE_COPY.esitiRicorso[r.esito]}
              {r.deciso_at ? ` il ${formatDate(r.deciso_at)}` : ""}
            </p>
            <p className="text-body text-ink-2">{MODERAZIONE_COPY.esitiRicorsoSpiegazione[r.esito]}</p>
            {r.motivazione && <p className="whitespace-pre-line text-body text-ink-2">{r.motivazione}</p>}
          </div>
        ) : (
          <p className="text-body text-ink-2">{MODERAZIONE_COPY.ricorsoInAttesa}</p>
        )}
      </div>
    );
  } else if (s.ricorso_possibile && !s.editable) {
    corpo = (
      <p className="text-body text-ink-2">
        Il ricorso lo presenta il titolare dell'azienda
        {s.ricorso_entro ? `, entro il ${formatDate(s.ricorso_entro)}` : ""}.
      </p>
    );
  } else if (s.ricorso_possibile) {
    corpo = (
      <div className="flex flex-col gap-4">
        <p className="text-body text-ink-2">
          {MODERAZIONE_COPY.ricorsoSpiegazione}
          {s.ricorso_entro ? ` Puoi presentarlo fino al ${formatDate(s.ricorso_entro)}.` : ""}
        </p>
        <TestoLungo
          etichetta={MODERAZIONE_COPY.ricorsoEtichetta}
          aiuto={`Almeno ${min} caratteri.`}
          valore={testo}
          onChange={setTesto}
          massimo={max}
          righe={5}
          required
          errore={errore ?? undefined}
        />
        <div>
          <Button onClick={() => void invia()} loading={ricorso.isPending}>
            Invia il ricorso
          </Button>
        </div>
      </div>
    );
  } else {
    corpo = (
      <p className="text-body text-ink-2">
        Il ricorso interno non è disponibile per questa decisione: si presenta una sola volta,
        entro 6 mesi, contro una decisione che ti riguarda.
      </p>
    );
  }

  return (
    <Section>
      <SectionHeader titolo={MODERAZIONE_COPY.ricorsoTitolo} />
      {/* Sempre montata: la conferma dell'invio va annunciata anche se il
          modulo sparisce. */}
      <div aria-live="polite">{annuncio && <Alert tono="ok">{annuncio}</Alert>}</div>
      {corpo}
      <p className="text-small text-ink-3">{MODERAZIONE_COPY.vieEsterne}</p>
    </Section>
  );
}

/** Pagina di una segnalazione (`/app/partenariati/segnalazioni/:id`, WP9):
 *  per chi ha segnalato e per l'azienda autrice del contenuto (l'Advisor la
 *  apre con l'azienda giusta dal link della notifica, `?azienda=`): stato,
 *  decisione motivata e ricorso. */
export default function SegnalazionePartenariato() {
  const { id } = useParams();
  const { avviso } = useAziendaDaLink();
  const segnalazione = useSegnalazione(id);

  const avvisoAzienda = avviso ? <Alert tono="attenzione">{avviso}</Alert> : null;

  if (segnalazione.isPending) {
    return (
      <Page variante="sezioni">
        {avvisoAzienda}
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Page>
    );
  }
  if (segnalazione.isError) {
    return (
      <Page variante="sezioni">
        {avvisoAzienda}
        {apiErrorCode(segnalazione.error) === "not_found" ? (
          <>
            <ErrorState
              title="Segnalazione non trovata"
              message="Non esiste oppure non riguarda te o l'azienda che stai gestendo. Se gestisci più aziende, apri il link dalla notifica."
            />
            <div>
              <LinkButton to="/app/partenariati" variant="secondary">
                Partenariati
              </LinkButton>
            </div>
          </>
        ) : (
          <ErrorState
            message={apiErrorMessage(segnalazione.error, "Impossibile caricare la segnalazione.")}
            onRetry={() => void segnalazione.refetch()}
          />
        )}
      </Page>
    );
  }

  const s = segnalazione.data;
  return (
    <Page variante="sezioni">
      <PageHeader
        indietro={{ label: "Partenariati", to: "/app/partenariati" }}
        sopra={
          <>
            <StatoSegnalazioneBadge stato={s.stato} />
            <span className="text-small text-ink-3 tabular-nums">
              <span className="sr-only">Codice </span>
              {s.codice || codiceSegnalazione(s.id)}
            </span>
          </>
        }
        titolo={s.ruolo === "autore" ? "Segnalazione su un contenuto della tua azienda" : "La tua segnalazione"}
      />
      {avvisoAzienda}

      <Facts
        items={[
          { etichetta: "Contenuto", valore: MODERAZIONE_COPY.oggetti[s.oggetto_tipo] ?? s.oggetto_tipo },
          { etichetta: "Motivo", valore: CALL_COPY.segnalaMotivi[s.motivo] ?? s.motivo },
          { etichetta: "Ricevuta il", valore: formatDateTime(s.created_at) },
        ]}
      />
      {s.descrizione && (
        <Section>
          <SectionHeader titolo="Cosa hai scritto" />
          <p className="whitespace-pre-line text-body text-ink-2">{s.descrizione}</p>
        </Section>
      )}
      {s.ruolo === "autore" && (
        <p className="text-small text-ink-3">Per tutelare chi segnala, non ti diciamo chi è stato.</p>
      )}

      <Decisione s={s} />
      <Ricorso s={s} />
    </Page>
  );
}
