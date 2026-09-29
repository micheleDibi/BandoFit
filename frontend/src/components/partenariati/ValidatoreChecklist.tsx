import { AlertTriangle, CheckCircle2, ChevronDown, ExternalLink, HelpCircle, Lock, XCircle } from "lucide-react";
import { useId } from "react";
import { cn } from "../../lib/cn";
import { CONSORZIO_COPY } from "../../lib/copy";
import type {
  CitazioneCall,
  EsitoVoce,
  RegolaOrigineVoce,
  ValidazioneConsorzio,
  VoceValidazione,
} from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";

const ICONE: Record<EsitoVoce, { icona: typeof CheckCircle2; colore: string }> = {
  verde: { icona: CheckCircle2, colore: "text-emerald-600" },
  rosso: { icona: XCircle, colore: "text-red-600" },
  grigio: { icona: HelpCircle, colore: "text-slate-500" },
};
const TONI: Record<EsitoVoce, "emerald" | "red" | "slate"> = {
  verde: "emerald",
  rosso: "red",
  grigio: "slate",
};
const RIQUADRO: Record<EsitoVoce, string> = {
  verde: "border-emerald-200 bg-emerald-50 text-emerald-900",
  rosso: "border-red-200 bg-red-50 text-red-900",
  grigio: "border-slate-200 bg-slate-50 text-slate-800",
};

/** Esito di una voce (o del consorzio): icona **e** testo, mai il solo colore. */
export function EsitoVoceBadge({ esito, className }: { esito: EsitoVoce; className?: string }) {
  const { icona: Icona } = ICONE[esito] ?? ICONE.grigio;
  return (
    <Badge tone={TONI[esito] ?? "slate"} className={cn("shrink-0", className)}>
      <Icona className="size-3.5" aria-hidden />
      <span className="sr-only">Esito: </span>
      {CONSORZIO_COPY.esiti[esito] ?? esito}
    </Badge>
  );
}

/** Link al documento citato, solo https, alla pagina indicata. */
function linkDocumento(url: string | null | undefined, pagina: number | null | undefined) {
  if (!url || !/^https:\/\//i.test(url)) return null;
  const base = url.split("#")[0];
  return pagina ? `${base}#page=${pagina}` : base;
}

function Citazione({ citazione }: { citazione: CitazioneCall }) {
  const fonte = citazione.fonte_etichetta?.trim() || null;
  const intestazione = citazione.pagina
    ? `Dal documento «${fonte ?? "ufficiale"}» — pagina ${citazione.pagina}`
    : (fonte ?? "Dal testo del bando");
  const link = linkDocumento(citazione.url_documento, citazione.pagina);
  return (
    <div className="mt-2 rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {intestazione}
        {!citazione.verificata && (
          <span className="ml-1.5 normal-case text-amber-600">(citazione non ritrovata alla lettera)</span>
        )}
      </p>
      {/* Testo del bando (estratto dal modello): solo testo semplice. */}
      <p className="mt-1 whitespace-pre-line text-sm italic text-slate-600">«{citazione.testo}»</p>
      {link && (
        <a
          href={link}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand-600 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-brand-500"
        >
          {citazione.pagina ? `Apri il documento a pagina ${citazione.pagina}` : "Apri il documento"}
          <ExternalLink className="size-3.5" aria-hidden />
          <span className="sr-only">(si apre in una nuova scheda)</span>
        </a>
      )}
    </div>
  );
}

/** Da dove viene la regola: il bando (con il passaggio, espandibile), chi ha
 *  creato la call, oppure un controllo della piattaforma sempre valido. */
function OrigineRegola({ regola }: { regola: RegolaOrigineVoce | null }) {
  if (!regola) {
    return <p className="mt-2 text-xs text-slate-500">Controllo automatico della piattaforma.</p>;
  }
  if (regola.fonte === "creatore") {
    return <p className="mt-2 text-xs text-slate-500">Regola impostata da chi ha creato la call.</p>;
  }
  if (!regola.citazione) {
    return <p className="mt-2 text-xs text-slate-500">Regola del bando.</p>;
  }
  return (
    <details className="group mt-2">
      <summary className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 [&::-webkit-details-marker]:hidden">
        Regola del bando: vedi il passaggio
        <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <Citazione citazione={regola.citazione} />
    </details>
  );
}

function elenco(nomi: string[]) {
  if (nomi.length <= 1) return nomi.join("");
  return `${nomi.slice(0, -1).join(", ")} e ${nomi[nomi.length - 1]}`;
}

function Voce({ voce, nomi }: { voce: VoceValidazione; nomi: ReadonlyMap<string, string> }) {
  const { icona: Icona, colore } = ICONE[voce.esito] ?? ICONE.grigio;
  const nome = (id: string) => nomi.get(id) ?? "Un membro";
  const coinvolti = voce.membri_coinvolti.map(nome);
  return (
    <li className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
      <div className="flex items-start gap-2.5">
        <Icona className={cn("mt-0.5 size-4 shrink-0", colore)} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 className="text-sm font-medium text-slate-900">{voce.titolo}</h3>
            <EsitoVoceBadge esito={voce.esito} />
            {voce.dichiarato && <Badge tone="amber">{CONSORZIO_COPY.dichiarato}</Badge>}
          </div>
          <p className="mt-1 text-sm text-slate-600">{voce.dettaglio_pubblico}</p>
          {voce.dettaglio_privato && (
            <p className="mt-1.5 flex items-start gap-1.5 rounded-md bg-brand-50 px-2.5 py-1.5 text-xs text-brand-900">
              <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="font-medium">Solo per te: </span>
                {voce.dettaglio_privato}
              </span>
            </p>
          )}
          {coinvolti.length > 0 && (
            <p className="mt-1.5 text-xs text-slate-500">Riguarda: {elenco(coinvolti)}</p>
          )}
          {voce.esiti_membri.length > 1 && (
            <details className="group mt-1.5">
              <summary className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-slate-600 hover:text-slate-800 [&::-webkit-details-marker]:hidden">
                Esito membro per membro
                <ChevronDown className="size-3.5 transition-transform group-open:rotate-180" aria-hidden />
              </summary>
              <ul className="mt-1.5 space-y-1">
                {voce.esiti_membri.map((e) => (
                  <li key={e.membro_id} className="flex flex-wrap items-center gap-1.5 text-xs text-slate-700">
                    <span>{nome(e.membro_id)}</span>
                    <EsitoVoceBadge esito={e.esito} />
                    {e.dichiarato && <span className="text-amber-700">({CONSORZIO_COPY.dichiarato})</span>}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <OrigineRegola regola={voce.regola} />
        </div>
      </div>
    </li>
  );
}

/** Verifica deterministica del consorzio (V2): esito complessivo in una
 *  regione `aria-live` (si annuncia quando cambia dopo una modifica), poi le
 *  voci con l'esito in icona e testo, il dettaglio (i propri numeri solo per
 *  sé), i membri coinvolti e la regola di origine con la citazione del bando.
 *  Degli altri membri solo esiti sulle fasce: lo decide il server. */
export function ValidatoreChecklist({
  validazione,
  nomi,
}: {
  validazione: ValidazioneConsorzio;
  /** Nome da mostrare per id del membro. */
  nomi: ReadonlyMap<string, string>;
}) {
  const idTitolo = useId();
  const { esito, voci, riepilogo } = validazione;
  const { icona: Icona } = ICONE[esito] ?? ICONE.grigio;
  const conteggi = [
    riepilogo.rosso ? `${riepilogo.rosso} da sistemare` : null,
    riepilogo.grigio ? `${riepilogo.grigio} da verificare` : null,
    riepilogo.verde ? `${riepilogo.verde} in regola` : null,
  ].filter(Boolean);

  return (
    <Card className="p-5">
      <section aria-labelledby={idTitolo}>
        <h2 id={idTitolo} className="font-display text-base font-semibold text-slate-900">
          Verifica delle regole
        </h2>
        <div
          role="status"
          aria-live="polite"
          className={cn("mt-3 flex items-start gap-2 rounded-lg border px-4 py-3 text-sm", RIQUADRO[esito])}
        >
          <Icona className="mt-0.5 size-4 shrink-0" aria-hidden />
          <p>
            <span className="font-semibold">{CONSORZIO_COPY.esiti[esito]}.</span>{" "}
            {CONSORZIO_COPY.esitoComplessivo[esito]}
            {conteggi.length > 0 && <span className="block text-xs opacity-80">{conteggi.join(" · ")}</span>}
          </p>
        </div>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-slate-500" role="note">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          {CONSORZIO_COPY.disclaimerValidatore}
        </p>
        {voci.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">Nessuna regola da controllare per ora.</p>
        ) : (
          <ul className="mt-3 space-y-2">
            {voci.map((voce) => (
              <Voce key={voce.id} voce={voce} nomi={nomi} />
            ))}
          </ul>
        )}
        {voci.some((v) => v.dichiarato || v.esiti_membri.some((e) => e.dichiarato)) && (
          <p className="mt-3 text-xs text-slate-500">{CONSORZIO_COPY.dichiaratoNota}</p>
        )}
      </section>
    </Card>
  );
}
