import { AlertTriangle, CheckCircle2, ChevronDown, ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import type { CitazioneRegola, FontePartenariato, VoceRegola } from "../../types";
import { Badge } from "../ui/Badge";

// «D1-p3», «[D1-P3]», «D1 p3»: documento 1, pagina 3.
const SEZIONE_DOCUMENTO = /^\[?\s*D\s*(\d+)\s*[-\s]?\s*p\s*(\d+)\s*\]?$/i;
// «META», «S2»: la scheda del bando nel catalogo.
const SEZIONE_SCHEDA = /^\[?\s*(META|S\d+)\s*\]?$/i;

/** «Avviso pubblico — pag. 3» → «Avviso pubblico»: la pagina la diciamo a
 *  parte, una volta sola. */
function senzaPagina(etichetta: string): string {
  return etichetta.replace(/\s*[—–-]\s*pag(?:ina|\.)?\s*\d+\s*$/i, "").trim();
}

/** Da dove viene la citazione, in parole: documento e pagina, la scheda del
 *  bando nel catalogo, oppure la fonte come la descrive il server («Fonte non
 *  riconosciuta» per un blocco citato che non esiste). */
function intestazioneCitazione(
  citazione: CitazioneRegola,
  fonti: FontePartenariato[],
): { testo: string; pagina: number | null } {
  const documento = SEZIONE_DOCUMENTO.exec(citazione.sezione.trim());
  const pagina = citazione.pagina ?? (documento ? Number(documento[2]) : null);
  if (documento || citazione.url_documento) {
    const fonte = documento ? fonti.find((f) => f.n === Number(documento[1])) : undefined;
    const nome = fonte?.etichetta || senzaPagina(citazione.fonte_etichetta) || "documento ufficiale";
    return {
      testo: pagina ? `Dal documento «${nome}» — pagina ${pagina}` : `Dal documento «${nome}»`,
      pagina,
    };
  }
  if (SEZIONE_SCHEDA.test(citazione.sezione.trim())) {
    return { testo: "Dalla scheda del bando", pagina: null };
  }
  return { testo: citazione.fonte_etichetta || "Fonte non riconosciuta", pagina: null };
}

/** Link al PDF alla pagina citata: solo https (il server lo garantisce già,
 *  qui non si rischia), frammento `#page=N` che i lettori PDF dei browser
 *  rispettano. */
function linkDocumento(url: string | null, pagina: number | null): string | null {
  if (!url || !/^https:\/\//i.test(url)) return null;
  const base = url.split("#")[0];
  return pagina ? `${base}#page=${pagina}` : base;
}

/** Riga espandibile di una regola di partenariato, gemella di `VoceRow`
 *  dell'AI-check: titolo e stato nel riepilogo, dettagli e passaggio del
 *  bando nel corpo. I testi arrivano dal modello: solo testo semplice, niente
 *  link automatici (l'unico link è quello al documento ufficiale). */
export function RegolaVoce({
  titolo,
  voce,
  fonti,
  children,
}: {
  titolo: ReactNode;
  voce: VoceRegola;
  /** Documenti analizzati: danno il nome del documento citato. */
  fonti: FontePartenariato[];
  /** Dettagli della voce, sopra la citazione. */
  children?: ReactNode;
}) {
  const daVerificare = voce.stato !== "verificata";
  const citazione = voce.citazione;
  const intestazione = citazione ? intestazioneCitazione(citazione, fonti) : null;
  const link = citazione ? linkDocumento(citazione.url_documento, intestazione?.pagina ?? null) : null;

  return (
    <details className="group rounded-lg border border-slate-200 bg-white">
      <summary className="flex cursor-pointer items-center gap-2.5 px-3.5 py-2.5 text-sm [&::-webkit-details-marker]:hidden">
        {daVerificare ? (
          <AlertTriangle className="size-4 shrink-0 text-amber-500" aria-hidden />
        ) : (
          <CheckCircle2 className="size-4 shrink-0 text-emerald-500" aria-hidden />
        )}
        <span className="min-w-0 flex-1 font-medium text-slate-800">{titolo}</span>
        {daVerificare ? (
          <Badge tone="amber" className="shrink-0">
            da verificare
          </Badge>
        ) : (
          <span className="sr-only">Verificata sul testo del bando.</span>
        )}
        <ChevronDown
          className="size-4 shrink-0 text-slate-400 transition-transform group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="space-y-2.5 border-t border-slate-100 px-3.5 py-3 text-sm">
        {children}
        {(voce.avvisi ?? []).length > 0 && (
          // Perché la voce è da verificare (valore incoerente, regione non
          // riconosciuta, passaggio troppo breve…): testo del server.
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-700">
            {(voce.avvisi ?? []).map((avviso, i) => (
              <li key={i}>{avviso}</li>
            ))}
          </ul>
        )}
        {citazione && intestazione ? (
          <div className="rounded-lg bg-slate-50 px-3 py-2">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              {intestazione.testo}
              {!citazione.verificata && (
                <span className="ml-1.5 normal-case text-amber-600">
                  (citazione non ritrovata alla lettera)
                </span>
              )}
            </p>
            <p className="mt-1 whitespace-pre-line italic text-slate-600">«{citazione.testo}»</p>
            {link && (
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-brand-600 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-brand-500"
              >
                {intestazione.pagina
                  ? `Apri il documento a pagina ${intestazione.pagina}`
                  : "Apri il documento"}
                <ExternalLink className="size-3.5" aria-hidden />
                <span className="sr-only">(si apre in una nuova scheda)</span>
              </a>
            )}
          </div>
        ) : (
          <p className="text-xs text-amber-600">
            Nessun passaggio del bando collegato a questa voce: controllala sul testo ufficiale.
          </p>
        )}
      </div>
    </details>
  );
}
