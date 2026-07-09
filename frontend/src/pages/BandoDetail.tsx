import {
  ArrowLeft,
  ArrowUpRight,
  Banknote,
  Building2,
  CalendarCheck,
  CalendarDays,
  CalendarPlus,
  ExternalLink,
  FileText,
  Landmark,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { AiCheckCard } from "../components/bandi/AiCheckCard";
import { AiCheckReport } from "../components/bandi/AiCheckReport";
import { ScadenzaBadge, StatoBadge } from "../components/bandi/badges";
import { CompatibilitaBadge } from "../components/bandi/CompatibilitaBadge";
import { ContenutoRenderer } from "../components/bandi/ContenutoRenderer";
import { SaveBandoButton } from "../components/bandi/SaveBandoButton";
import { Badge } from "../components/ui/Badge";
import { Button, buttonClasses, LinkButton } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { ErrorState, Skeleton } from "../components/ui/states";
import { useBando } from "../hooks/useBandi";
import { useAddBandoDeadline } from "../hooks/useCalendar";
import { apiErrorMessage } from "../lib/api";
import { formatDate, formatEur } from "../lib/format";

function MetaTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Banknote;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        <Icon className="size-3.5" aria-hidden />
        {label}
      </div>
      <p className="tabular mt-1.5 font-display text-lg font-semibold text-slate-900">{value}</p>
    </div>
  );
}

export default function BandoDetail() {
  const { slug } = useParams<{ slug: string }>();
  const { data: bando, isPending, isError, error, refetch } = useBando(slug);
  const addDeadline = useAddBandoDeadline();

  if (isPending) {
    return (
      <div className="mx-auto max-w-5xl">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="mt-6 h-8 w-3/4" />
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="mt-8 h-64 w-full" />
      </div>
    );
  }

  if (isError || !bando) {
    return (
      <div className="mx-auto max-w-3xl">
        <ErrorState
          message={apiErrorMessage(error, "Bando non trovato.")}
          onRetry={() => refetch()}
        />
        <div className="mt-4 text-center">
          <Link to="/app/bandi" className="text-sm font-medium text-brand-600 hover:underline">
            ← Torna all'elenco bandi
          </Link>
        </div>
      </div>
    );
  }

  const titolo = bando.titolo ?? bando.titolo_breve ?? "Bando";
  const linkPrincipale = bando.link_candidatura ?? bando.link_bando;
  const allegati = (bando.allegati ?? []).filter((a) => a && (a.url || a.link));

  // Solo i riquadri con un dato reale: niente box con "—".
  const metaTiles: { icon: typeof Banknote; label: string; value: string }[] = [];
  if (bando.importo_totale_eur !== null) {
    metaTiles.push({
      icon: Banknote,
      label: "Dotazione totale",
      value: formatEur(bando.importo_totale_eur),
    });
  }
  if (bando.importo_max_per_progetto_eur !== null) {
    metaTiles.push({
      icon: Landmark,
      label: "Max per progetto",
      value: formatEur(bando.importo_max_per_progetto_eur),
    });
  }
  if (bando.data_apertura) {
    metaTiles.push({ icon: CalendarDays, label: "Apertura", value: formatDate(bando.data_apertura) });
  }
  if (bando.data_scadenza) {
    metaTiles.push({ icon: CalendarDays, label: "Scadenza", value: formatDate(bando.data_scadenza) });
  }

  return (
    <div className="mx-auto max-w-6xl">
      <Link
        to="/app/bandi"
        className="inline-flex items-center gap-1.5 rounded-lg text-sm font-medium text-slate-500 transition-colors hover:text-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Tutti i bandi
      </Link>

      {/* Header */}
      <header className="mt-4">
        <div className="flex flex-wrap items-center gap-2">
          <StatoBadge stato={bando.stato_bando} />
          {bando.tipologia && <Badge tone="brand">{bando.tipologia.nome}</Badge>}
          {bando.modalita_erogazione && <Badge tone="slate">{bando.modalita_erogazione.nome}</Badge>}
          {bando.programma && <Badge tone="slate">{bando.programma.nome}</Badge>}
          {bando.compatibilita && <CompatibilitaBadge compatibilita={bando.compatibilita} />}
        </div>
        <h1 className="mt-3 font-display text-2xl font-bold leading-tight tracking-tight text-slate-900 sm:text-3xl">
          {titolo}
        </h1>
        <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-slate-500">
          {bando.ente_erogatore && (
            <span className="inline-flex items-center gap-1.5">
              <Building2 className="size-4" aria-hidden />
              {bando.ente_erogatore}
            </span>
          )}
          <ScadenzaBadge dataScadenza={bando.data_scadenza} />
        </div>

        {/* Azioni: salva + scadenza in calendario */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <SaveBandoButton bando={{ id: bando.id, slug: bando.slug }} variant="inline" />
          {bando.data_scadenza &&
            (addDeadline.isSuccess ? (
              <LinkButton
                to={`/app/calendario?m=${bando.data_scadenza.slice(0, 7)}`}
                variant="secondary"
                size="sm"
              >
                <CalendarCheck className="size-4 text-emerald-600" aria-hidden />
                Nel calendario
              </LinkButton>
            ) : (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={addDeadline.isPending}
                onClick={() => addDeadline.mutate(bando.slug)}
              >
                <CalendarPlus className="size-4" aria-hidden />
                Aggiungi scadenza al calendario
              </Button>
            ))}
          {addDeadline.isError && (
            <span className="text-sm text-red-600" role="alert">
              {apiErrorMessage(addDeadline.error)}
            </span>
          )}
        </div>
      </header>

      {/* Meta tiles (solo quelli valorizzati) */}
      {metaTiles.length > 0 && (
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {metaTiles.map((tile) => (
            <MetaTile key={tile.label} icon={tile.icon} label={tile.label} value={tile.value} />
          ))}
        </div>
      )}

      <div className="mt-8 grid gap-8 lg:grid-cols-[1fr_320px]">
        {/* Contenuto */}
        <article className="min-w-0">
          {bando.descrizione_breve && (
            <p className="rounded-xl border border-brand-100 bg-brand-50/60 px-5 py-4 text-[15px] leading-relaxed text-slate-700">
              {bando.descrizione_breve}
            </p>
          )}
          <div className="mt-6">
            {bando.contenuto?.sections?.length ? (
              <ContenutoRenderer sections={bando.contenuto.sections} />
            ) : (
              <p className="text-slate-500">
                La scheda dettagliata non è ancora disponibile: consulta il bando ufficiale dal
                link a fianco.
              </p>
            )}
          </div>
        </article>

        {/* Sidebar */}
        <aside>
          <div className="sticky top-20 space-y-4">
            <AiCheckCard slug={bando.slug} />

            {linkPrincipale && (
              <Card className="p-5">
                <h2 className="font-display text-sm font-semibold text-slate-900">Candidatura</h2>
                <div className="mt-3 space-y-2">
                  <a
                    href={linkPrincipale}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={buttonClasses("primary", "md", "w-full")}
                  >
                    Vai al bando
                    <ArrowUpRight className="size-4" aria-hidden />
                  </a>
                  {bando.link_bando && bando.link_bando !== linkPrincipale && (
                    <a
                      href={bando.link_bando}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={buttonClasses("secondary", "md", "w-full")}
                    >
                      Fonte ufficiale
                      <ExternalLink className="size-4" aria-hidden />
                    </a>
                  )}
                </div>
              </Card>
            )}

            {allegati.length > 0 && (
              <Card className="p-5">
                <h2 className="font-display text-sm font-semibold text-slate-900">Allegati</h2>
                <ul className="mt-3 space-y-2">
                  {allegati.map((allegato, i) => (
                    <li key={i}>
                      <a
                        href={allegato.url ?? allegato.link}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-2 text-sm text-brand-600 underline-offset-2 hover:underline"
                      >
                        <FileText className="size-4 shrink-0" aria-hidden />
                        {allegato.nome ?? allegato.titolo ?? `Allegato ${i + 1}`}
                      </a>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {(bando.regioni.length > 0 ||
              bando.settori.length > 0 ||
              bando.beneficiari.length > 0 ||
              bando.codici_ateco.length > 0 ||
              bando.tematica.length > 0) && (
              <Card className="p-5">
                <h2 className="font-display text-sm font-semibold text-slate-900">
                  A chi si rivolge
                </h2>
                <dl className="mt-3 space-y-3 text-sm">
                  {bando.regioni.length > 0 && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        Regioni
                      </dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {bando.regioni.map((r) => (
                          <Badge key={r.id} tone="slate">
                            {r.nome}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                  {bando.beneficiari.length > 0 && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        Beneficiari
                      </dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {bando.beneficiari.map((b) => (
                          <Badge key={b.id} tone="slate">
                            {b.nome}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                  {bando.settori.length > 0 && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        Settori
                      </dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {bando.settori.map((s) => (
                          <Badge key={s.id} tone="slate">
                            {s.nome}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                  {bando.codici_ateco.length > 0 && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        Codici ATECO
                      </dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {bando.codici_ateco.map((c) => (
                          <Badge key={c.id} tone="slate" title={c.descrizione ?? undefined}>
                            {c.codice}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                  {bando.tematica.length > 0 && (
                    <div>
                      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">
                        Tematiche
                      </dt>
                      <dd className="mt-1 flex flex-wrap gap-1.5">
                        {bando.tematica.map((t) => (
                          <Badge key={t} tone="brand">
                            {t}
                          </Badge>
                        ))}
                      </dd>
                    </div>
                  )}
                </dl>
              </Card>
            )}
          </div>
        </aside>
      </div>

      <AiCheckReport slug={bando.slug} />
    </div>
  );
}
