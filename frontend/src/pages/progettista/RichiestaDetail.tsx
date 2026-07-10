import { ArrowLeft, Building2, CalendarClock, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ConsulenzaStatoBadge } from "../Consulenze";
import { AiReportBody } from "../../components/bandi/AiReportBody";
import { DossierView } from "../../components/company/dossier/DossierView";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { TextareaField } from "../../components/ui/Field";
import { ErrorState, Skeleton } from "../../components/ui/states";
import {
  useDossierRichiesta,
  useInviaProposta,
  useRichiesta,
  useRitiraProposta,
} from "../../hooks/useProgettistaRichieste";
import { apiErrorMessage } from "../../lib/api";
import { PROPOSTA_STATO_LABELS } from "../../lib/copy";
import { formatDateTime, formatSlotGiorno, formatSlotOra } from "../../lib/format";

/** Vista FULL post-assegnazione: dati aziendali + dossier certificato.
 *  Il caricamento parte su azione esplicita: ogni lettura è registrata
 *  lato server in audit_log. */
function DossierCompleto({ requestId }: { requestId: string }) {
  const [visible, setVisible] = useState(false);
  const { data, isPending, isError, error, refetch } = useDossierRichiesta(
    requestId,
    visible,
  );

  if (!visible) {
    return (
      <Card className="p-5">
        <h2 className="inline-flex items-center gap-1.5 font-display text-sm font-semibold text-slate-900">
          <Building2 className="size-4 text-brand-500" aria-hidden />
          Dati completi dell'azienda
        </h2>
        <p className="mt-1.5 text-sm text-slate-600">
          Come progettista assegnato hai accesso a tutti i dati aziendali e al dossier
          certificato del Registro Imprese. Ogni accesso viene registrato.
        </p>
        <Button variant="secondary" className="mt-3" onClick={() => setVisible(true)}>
          Apri i dati completi
        </Button>
      </Card>
    );
  }
  if (isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />;
  }

  const company = data.company;
  return (
    <div>
      {company && (
        <Card className="p-5">
          <h2 className="font-display text-sm font-semibold text-slate-900">
            Dati dichiarati dal titolare
          </h2>
          <dl className="mt-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
            {(
              [
                ["Ragione sociale", company.ragione_sociale],
                ["Partita IVA", company.partita_iva],
                ["Forma giuridica", company.forma_giuridica],
                ["ATECO", company.ateco_codice],
                ["Settore", company.settore_nome],
                ["Regione", company.regione_nome],
                ["Comune", company.comune],
                ["Dipendenti", company.numero_dipendenti],
                ["Classe dimensionale", company.classe_dimensionale],
                ["Fascia di fatturato", company.fascia_fatturato],
                ["PEC", company.pec],
                ["Telefono", company.telefono],
              ] as Array<[string, string | number | null]>
            )
              .filter(([, value]) => value !== null && value !== undefined && value !== "")
              .map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between gap-3 sm:block">
                  <dt className="text-xs text-slate-400">{label}</dt>
                  <dd className="font-medium text-slate-800">{value}</dd>
                </div>
              ))}
          </dl>
        </Card>
      )}
      {data.dossier.imported && data.dossier.dossier ? (
        <DossierView dossier={data.dossier.dossier} people={data.dossier.people} />
      ) : (
        <p className="mt-4 text-sm text-slate-500">
          L'azienda non ha ancora importato il dossier certificato dal Registro Imprese.
        </p>
      )}
    </div>
  );
}

export default function RichiestaDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: richiesta, isPending, isError, error, refetch } = useRichiesta(id);
  const invia = useInviaProposta(id ?? "");
  const ritira = useRitiraProposta();

  const [messaggio, setMessaggio] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  if (isPending) {
    return (
      <div className="mx-auto max-w-4xl space-y-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError || !richiesta) {
    return (
      <div className="mx-auto max-w-4xl">
        <ErrorState message={apiErrorMessage(error)} onRetry={() => refetch()} />
      </div>
    );
  }

  const propostaAperta = richiesta.mie_proposte.find((p) => p.stato === "inviata");
  const report = richiesta.ai_check?.report ?? null;

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (invia.isPending || !messaggio.trim()) return;
    setActionError(null);
    try {
      await invia.mutateAsync(messaggio.trim());
      setMessaggio("");
    } catch (err) {
      setActionError(apiErrorMessage(err));
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <Link
        to="/app/progettista/richieste"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-slate-800"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Tutte le richieste
      </Link>

      <div className="mt-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-bold tracking-tight text-slate-900">
            {richiesta.ragione_sociale ?? richiesta.denominazione_utente}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {richiesta.partita_iva && (
              <span className="tabular">P.IVA {richiesta.partita_iva} · </span>
            )}
            {richiesta.denominazione_utente}
            {richiesta.email && ` · ${richiesta.email}`}
          </p>
        </div>
        <ConsulenzaStatoBadge stato={richiesta.stato} />
      </div>

      {/* Bando + appuntamento */}
      <Card className="mt-5 p-5">
        <h2 className="font-display text-sm font-semibold text-slate-900">Bando</h2>
        <p className="mt-1.5 text-sm text-slate-700">{richiesta.bando_titolo}</p>
        <Link
          to={`/app/bandi/${richiesta.bando_slug}`}
          className="mt-1 inline-block text-sm font-medium text-brand-600 underline-offset-2 hover:underline"
        >
          Vai al bando →
        </Link>
        {richiesta.appuntamento && (
          <p className="mt-3 inline-flex items-center gap-2 rounded-xl bg-slate-50 px-4 py-3 text-sm font-medium text-slate-800">
            <CalendarClock className="size-4 shrink-0 text-brand-500" aria-hidden />
            <span>
              <span className="capitalize">
                {formatSlotGiorno(richiesta.appuntamento.inizio)}
              </span>
              , {formatSlotOra(richiesta.appuntamento.inizio)} –{" "}
              {formatSlotOra(richiesta.appuntamento.fine)}
            </span>
          </p>
        )}
      </Card>

      {/* AI-check ricevuto dal cliente (requisito punto 3) */}
      {report ? (
        <Card className="mt-4 p-6">
          <h2 className="inline-flex items-center gap-2 font-display text-lg font-bold text-slate-900">
            <Sparkles className="size-5 text-brand-500" aria-hidden />
            AI-check del cliente
          </h2>
          {richiesta.ai_check?.ready_at && (
            <p className="mt-1 text-sm text-slate-500">
              Generato il {formatDateTime(richiesta.ai_check.ready_at)}
            </p>
          )}
          <AiReportBody report={report} mostraAzioni={false} />
        </Card>
      ) : (
        <Card className="mt-4 p-5">
          <p className="text-sm text-slate-500">
            Il report AI-check non è più disponibile; esito e punteggio della richiesta
            restano quelli registrati alla creazione.
          </p>
        </Card>
      )}

      {/* Proposta */}
      <section className="mt-6" aria-label="La tua proposta">
        <h2 className="font-display text-lg font-bold tracking-tight text-slate-900">
          La tua proposta
        </h2>
        {richiesta.mie_proposte.length > 0 && (
          <div className="mt-3 space-y-3">
            {richiesta.mie_proposte.map((proposta) => (
              <Card key={proposta.id} className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <p className="text-xs text-slate-400">
                    {formatDateTime(proposta.created_at)}
                  </p>
                  <Badge tone={proposta.stato === "accettata" ? "emerald" : proposta.stato === "inviata" ? "brand" : "slate"}>
                    {PROPOSTA_STATO_LABELS[proposta.stato]}
                  </Badge>
                </div>
                <p className="mt-2 whitespace-pre-line text-sm text-slate-700">
                  {proposta.messaggio}
                </p>
                {proposta.stato === "inviata" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3 text-red-600 hover:bg-red-50 hover:text-red-700"
                    loading={ritira.isPending}
                    onClick={async () => {
                      setActionError(null);
                      try {
                        await ritira.mutateAsync(proposta.id);
                      } catch (err) {
                        setActionError(apiErrorMessage(err));
                      }
                    }}
                  >
                    Ritira la proposta
                  </Button>
                )}
              </Card>
            ))}
          </div>
        )}

        {richiesta.stato === "nuova" && !propostaAperta && (
          <Card className="mt-3 p-5">
            <form onSubmit={handleSend}>
              <TextareaField
                label="Messaggio per il titolare"
                required
                rows={5}
                maxLength={4000}
                value={messaggio}
                onChange={(e) => setMessaggio(e.target.value)}
                helper="Presentati e spiega come puoi aiutare su questo bando: il titolare sceglie tra le proposte ricevute."
              />
              <Button type="submit" className="mt-3" loading={invia.isPending}>
                Invia la proposta
              </Button>
            </form>
          </Card>
        )}
      </section>

      {actionError && (
        <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {actionError}
        </p>
      )}

      {/* Vista full: solo per l'assegnato */}
      {richiesta.assegnata_a_me && (
        <section className="mt-6" aria-label="Dati completi dell'azienda">
          <DossierCompleto requestId={richiesta.id} />
        </section>
      )}
    </div>
  );
}
