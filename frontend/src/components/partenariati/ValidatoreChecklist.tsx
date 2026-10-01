import { ChevronDown, Lock } from "lucide-react";
import { cn } from "../../lib/cn";
import { CONSORZIO_COPY } from "../../lib/copy";
import type {
  CitazioneCall,
  EsitoVoce,
  RegolaOrigineVoce,
  ValidazioneConsorzio,
  VoceValidazione,
} from "../../types";
import { Alert, type AlertTono } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Status, type TonoStatus } from "../ui/Status";
import { TextLink } from "../ui/TextLink";

const TONI: Record<EsitoVoce, TonoStatus> = {
  verde: "aperto",
  rosso: "attenzione",
  grigio: "neutro",
};
const TONI_AVVISO: Record<EsitoVoce, AlertTono> = {
  verde: "ok",
  rosso: "errore",
  grigio: "info",
};

const SOMMARIO =
  "inline-flex cursor-pointer items-center gap-1 rounded-mark text-small font-medium text-accent-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden";

/** Esito di una voce (o del consorzio): in parole con il punto di `Status`,
 *  mai il solo colore. Lo usa anche la matrice di copertura. */
export function EsitoVoceBadge({ esito, className }: { esito: EsitoVoce; className?: string }) {
  return (
    <Status tono={TONI[esito] ?? "neutro"} className={cn("shrink-0", className)}>
      <span className="sr-only">Esito: </span>
      {CONSORZIO_COPY.esiti[esito] ?? esito}
    </Status>
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
    ? `Dal documento «${fonte ?? "ufficiale"}», pagina ${citazione.pagina}`
    : (fonte ?? "Dal testo del bando");
  const link = linkDocumento(citazione.url_documento, citazione.pagina);
  return (
    <div className="mt-2 flex flex-col gap-1 rounded-control bg-desk px-3 py-2">
      <p className="text-small font-medium text-ink-3">
        {intestazione}
        {!citazione.verificata && (
          <span className="ml-1.5 text-warning-ink">(citazione non ritrovata alla lettera)</span>
        )}
      </p>
      {/* Testo del bando (estratto dal modello): solo testo semplice. */}
      <p className="whitespace-pre-line text-body italic text-ink-2">«{citazione.testo}»</p>
      {link && (
        <p className="text-small">
          <TextLink href={link} esterno>
            {citazione.pagina ? `Apri il documento a pagina ${citazione.pagina}` : "Apri il documento"}
          </TextLink>
        </p>
      )}
    </div>
  );
}

/** Da dove viene la regola: il bando (con il passaggio, espandibile), chi ha
 *  creato la call, oppure un controllo della piattaforma sempre valido. */
function OrigineRegola({ regola }: { regola: RegolaOrigineVoce | null }) {
  if (!regola) {
    return <p className="text-small text-ink-3">Controllo automatico della piattaforma.</p>;
  }
  if (regola.fonte === "creatore") {
    return <p className="text-small text-ink-3">Regola impostata da chi ha creato la call.</p>;
  }
  if (!regola.citazione) {
    return <p className="text-small text-ink-3">Regola del bando.</p>;
  }
  return (
    <details className="group">
      <summary className={SOMMARIO}>
        Regola del bando: vedi il passaggio
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <Citazione citazione={regola.citazione} />
    </details>
  );
}

function elenco(nomi: string[]) {
  if (nomi.length <= 1) return nomi.join("");
  return `${nomi.slice(0, -1).join(", ")} e ${nomi[nomi.length - 1]}`;
}

function Voce({
  voce,
  nomi,
  livello,
}: {
  voce: VoceValidazione;
  nomi: ReadonlyMap<string, string>;
  /** Livello del titolo della voce: uno sotto quello della sezione. */
  livello: 3 | 4;
}) {
  const nome = (id: string) => nomi.get(id) ?? "Un membro";
  const coinvolti = voce.membri_coinvolti.map(nome);
  const Titolo = livello === 4 ? "h4" : "h3";
  return (
    <li className="flex flex-col gap-1.5 border-b border-line py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Titolo className="font-sans text-title-group text-ink">{voce.titolo}</Titolo>
        <EsitoVoceBadge esito={voce.esito} />
        {voce.dichiarato && <Badge>{CONSORZIO_COPY.dichiarato}</Badge>}
      </div>
      <p className="text-body text-ink-2">{voce.dettaglio_pubblico}</p>
      {voce.dettaglio_privato && (
        <p className="flex items-start gap-1.5 text-small text-ink-2">
          <Lock className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
          <span>
            <span className="font-medium text-ink">Solo per te: </span>
            {voce.dettaglio_privato}
          </span>
        </p>
      )}
      {coinvolti.length > 0 && <p className="text-small text-ink-3">Riguarda: {elenco(coinvolti)}</p>}
      {voce.esiti_membri.length > 1 && (
        <details className="group">
          <summary className={SOMMARIO}>
            Esito membro per membro
            <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1">
            {voce.esiti_membri.map((e) => (
              <li key={e.membro_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-small text-ink-2">
                <span>{nome(e.membro_id)}</span>
                <EsitoVoceBadge esito={e.esito} />
                {e.dichiarato && <span className="text-ink-3">({CONSORZIO_COPY.dichiarato})</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      <OrigineRegola regola={voce.regola} />
    </li>
  );
}

/** Verifica deterministica del consorzio (V2): esito complessivo in una
 *  regione live (si annuncia quando cambia dopo una modifica), poi le voci
 *  con l'esito in parole, il dettaglio (i propri numeri solo per sé), i
 *  membri coinvolti e la regola di origine con la citazione del bando.
 *  Degli altri membri solo esiti sulle fasce: lo decide il server. */
export function ValidatoreChecklist({
  validazione,
  nomi,
  livello = 2,
}: {
  validazione: ValidazioneConsorzio;
  /** Nome da mostrare per id del membro. */
  nomi: ReadonlyMap<string, string>;
  /** Livello del titolo della sezione (le voci stanno un livello sotto): 3
   *  dentro una sezione con il suo h2, come la call vista dal progettista. */
  livello?: 2 | 3;
}) {
  const { esito, voci, riepilogo } = validazione;
  const conteggi = [
    riepilogo.rosso ? `${riepilogo.rosso} da sistemare` : null,
    riepilogo.grigio ? `${riepilogo.grigio} da verificare` : null,
    riepilogo.verde ? `${riepilogo.verde} in regola` : null,
  ].filter(Boolean);

  return (
    <Section>
      <SectionHeader titolo="Verifica delle regole" livello={livello} />
      <div aria-live="polite">
        <Alert
          tono={TONI_AVVISO[esito] ?? "info"}
          ruolo="none"
          titolo={`${CONSORZIO_COPY.esiti[esito]}.`}
        >
          {CONSORZIO_COPY.esitoComplessivo[esito]}
          {conteggi.length > 0 && <span className="block text-small text-ink-3">{conteggi.join(", ")}</span>}
        </Alert>
      </div>
      <p className="text-small text-ink-3" role="note">
        {CONSORZIO_COPY.disclaimerValidatore}
      </p>
      {voci.length === 0 ? (
        <p className="text-body text-ink-3">Nessuna regola da controllare per ora.</p>
      ) : (
        <ul className="flex flex-col">
          {voci.map((voce) => (
            <Voce key={voce.id} voce={voce} nomi={nomi} livello={livello === 3 ? 4 : 3} />
          ))}
        </ul>
      )}
      {voci.some((v) => v.dichiarato || v.esiti_membri.some((e) => e.dichiarato)) && (
        <p className="text-small text-ink-3">{CONSORZIO_COPY.dichiaratoNota}</p>
      )}
    </Section>
  );
}
