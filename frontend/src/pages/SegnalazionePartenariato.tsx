import { Clock, Flag, Gavel, Handshake, Scale } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import { TestoLungo } from "../components/partenariati/CampiCall";
import {
  codiceSegnalazione,
  StatoSegnalazioneBadge,
} from "../components/partenariati/StatoSegnalazioneBadge";
import { Button, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useRicorsoSegnalazione, useSegnalazione } from "../hooks/useSegnalazioni";
import { apiErrorCode, apiErrorMessage } from "../lib/api";
import { CALL_COPY, MODERAZIONE_COPY } from "../lib/copy";
import { formatDate, formatDateTime } from "../lib/format";
import type { SegnalazioneEsito } from "../types";

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

/** La decisione motivata: per chi ha segnalato l'esito; per l'azienda autrice
 *  anche la motivazione formale (statement of reasons) con le vie di ricorso.
 *  Mai l'identità di chi ha segnalato. */
function Decisione({ s }: { s: SegnalazioneEsito }) {
  if (!s.decisione) {
    return (
      <Card className="p-5">
        <p className="inline-flex items-start gap-2 text-sm text-slate-700">
          <Clock className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
          {MODERAZIONE_COPY.inAttesa}
        </p>
      </Card>
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
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Gavel className="size-4 text-brand-500" aria-hidden />
        Decisione{data ? ` del ${formatDate(data)}` : ""}
      </h2>
      <p className="mt-2 text-sm text-slate-800">{testo}</p>
      {dalRicorso && (
        <p className="mt-2 text-sm text-slate-600">{MODERAZIONE_COPY.decisioneDalRicorso}</p>
      )}
      {perche && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Perché</p>
          <p className="mt-1 whitespace-pre-line text-sm text-slate-700">{perche}</p>
        </div>
      )}
      {s.sor_testo && (
        <details className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium text-slate-800">
            {MODERAZIONE_COPY.sorTitolo}
          </summary>
          <p className="mt-2 whitespace-pre-line text-[13px] leading-relaxed text-slate-700">
            {s.sor_testo}
          </p>
        </details>
      )}
    </Card>
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
      <div className="space-y-3">
        <p className="text-sm text-slate-700">
          {r.da === s.ruolo ? "Hai presentato un ricorso" : "È stato presentato un ricorso"}
          {r.at ? ` il ${formatDate(r.at)}` : ""}.
        </p>
        {r.testo && (
          <p className="whitespace-pre-line rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
            {r.testo}
          </p>
        )}
        {r.esito ? (
          <div>
            <p className="text-sm font-medium text-slate-900">
              {MODERAZIONE_COPY.esitiRicorso[r.esito]}
              {r.deciso_at ? ` il ${formatDate(r.deciso_at)}` : ""}
            </p>
            <p className="mt-1 text-sm text-slate-700">
              {MODERAZIONE_COPY.esitiRicorsoSpiegazione[r.esito]}
            </p>
            {r.motivazione && (
              <p className="mt-2 whitespace-pre-line text-sm text-slate-700">{r.motivazione}</p>
            )}
          </div>
        ) : (
          <p className="inline-flex items-start gap-2 text-sm text-slate-700">
            <Clock className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
            {MODERAZIONE_COPY.ricorsoInAttesa}
          </p>
        )}
      </div>
    );
  } else if (s.ricorso_possibile && !s.editable) {
    corpo = (
      <p className="text-sm text-slate-600">
        Il ricorso lo presenta il titolare dell'azienda
        {s.ricorso_entro ? `, entro il ${formatDate(s.ricorso_entro)}` : ""}.
      </p>
    );
  } else if (s.ricorso_possibile) {
    corpo = (
      <div className="space-y-3">
        <p className="text-sm text-slate-700">
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
        <Button onClick={() => void invia()} loading={ricorso.isPending}>
          Invia il ricorso
        </Button>
      </div>
    );
  } else {
    corpo = (
      <p className="text-sm text-slate-600">
        Il ricorso interno non è disponibile per questa decisione: si presenta una sola volta,
        entro 6 mesi, contro una decisione che ti riguarda.
      </p>
    );
  }

  return (
    <Card className="p-5">
      <h2 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <Scale className="size-4 text-brand-500" aria-hidden />
        {MODERAZIONE_COPY.ricorsoTitolo}
      </h2>
      {/* Sempre montata: la conferma dell'invio va annunciata anche se il
          modulo sparisce. */}
      <div role="status" aria-live="polite">
        {annuncio && (
          <p className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
            {annuncio}
          </p>
        )}
      </div>
      <div className="mt-3">{corpo}</div>
      <p className="mt-4 text-xs text-slate-500">{MODERAZIONE_COPY.vieEsterne}</p>
    </Card>
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

  let corpo: ReactNode;
  if (segnalazione.isPending) {
    corpo = (
      <div className="space-y-4" aria-hidden>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  } else if (segnalazione.isError) {
    corpo =
      apiErrorCode(segnalazione.error) === "not_found" ? (
        <EmptyState
          title="Segnalazione non trovata"
          description="Non esiste oppure non riguarda te o l'azienda che stai gestendo. Se gestisci più aziende, apri il link dalla notifica."
          action={<LinkButton to="/app/partenariati">Partenariati</LinkButton>}
        />
      ) : (
        <ErrorState
          message={apiErrorMessage(segnalazione.error, "Impossibile caricare la segnalazione.")}
          onRetry={() => void segnalazione.refetch()}
        />
      );
  } else {
    const s = segnalazione.data;
    corpo = (
      <div className="space-y-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <StatoSegnalazioneBadge stato={s.stato} />
            <span className="font-mono text-xs text-slate-400">
              <span className="sr-only">Codice </span>
              {s.codice || codiceSegnalazione(s.id)}
            </span>
          </div>
          <h1 className="mt-2 inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
            <Flag className="size-6 text-slate-400" aria-hidden />
            {s.ruolo === "autore" ? "Segnalazione su un contenuto della tua azienda" : "La tua segnalazione"}
          </h1>
        </div>

        <Card className="p-5">
          <dl className="grid gap-4 sm:grid-cols-3">
            <Voce titolo="Contenuto">{MODERAZIONE_COPY.oggetti[s.oggetto_tipo] ?? s.oggetto_tipo}</Voce>
            <Voce titolo="Motivo">{CALL_COPY.segnalaMotivi[s.motivo] ?? s.motivo}</Voce>
            <Voce titolo="Ricevuta il">{formatDateTime(s.created_at)}</Voce>
          </dl>
          {s.descrizione && (
            <div className="mt-4">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                Cosa hai scritto
              </p>
              <p className="mt-1 whitespace-pre-line text-sm text-slate-700">{s.descrizione}</p>
            </div>
          )}
          {s.ruolo === "autore" && (
            <p className="mt-4 text-xs text-slate-500">
              Per tutelare chi segnala, non ti diciamo chi è stato.
            </p>
          )}
        </Card>

        <Decisione s={s} />
        <Ricorso s={s} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <p className="text-sm text-slate-500">
        <Link
          to="/app/partenariati"
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
      {corpo}
    </div>
  );
}
