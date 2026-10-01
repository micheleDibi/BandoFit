import { Check, CircleDot, HelpCircle, Minus } from "lucide-react";
import { fieldLabel } from "../../lib/aiCheckFields";
import type { AiCriterioReport, AiReport, AiRequisitoReport, AiVerdetto } from "../../types";
import { Accordion } from "../ui/Accordion";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { ProgressRing } from "../ui/ProgressRing";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { TextLink } from "../ui/TextLink";
import { AiEsitoBadge } from "./badges";

const VERDETTO_LABELS: Record<AiVerdetto, string> = {
  soddisfatto: "Soddisfatto",
  parzialmente_soddisfatto: "Parzialmente soddisfatto",
  non_soddisfatto: "Non soddisfatto",
  dato_mancante: "Dato mancante",
};

/** Il verdetto come segno: spunta per il soddisfatto, punto per il parziale,
 *  trattino per il non soddisfatto (una compatibilità bassa non è un errore:
 *  mai rosso), punto interrogativo per il dato mancante. La parola la dice
 *  il testo per lo screen reader. */
function VerdettoIcon({ verdetto }: { verdetto: AiVerdetto }) {
  const classi = "size-4 shrink-0";
  if (verdetto === "soddisfatto") return <Check className={`${classi} text-fit-ink`} aria-hidden />;
  if (verdetto === "parzialmente_soddisfatto") {
    return <CircleDot className={`${classi} text-warning-ink`} aria-hidden />;
  }
  if (verdetto === "non_soddisfatto") return <Minus className={`${classi} text-ink-3`} aria-hidden />;
  return <HelpCircle className={`${classi} text-ink-3`} aria-hidden />;
}

type Voce = AiRequisitoReport | AiCriterioReport;

function TitoloVoce({ voce, punti }: { voce: Voce; punti?: string }) {
  const titolo =
    ("nome" in voce ? voce.nome : undefined) ||
    ("testo" in voce ? voce.testo : undefined) ||
    voce.motivazione;
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2.5">
      <VerdettoIcon verdetto={voce.verdetto} />
      <span className="min-w-0 flex-1">{titolo}</span>
      <span className="sr-only">{VERDETTO_LABELS[voce.verdetto]}.</span>
      {punti && <span className="shrink-0 text-small text-ink-3 tabular-nums">{punti}</span>}
    </span>
  );
}

/** Il dettaglio verificabile del verdetto: citazione del bando (sezione
 *  inclusa) e — per i criteri — il dato aziendale usato. */
function DettaglioVoce({ voce, mostraDato = true }: { voce: Voce; mostraDato?: boolean }) {
  return (
    <div className="flex flex-col gap-2.5">
      <p>{voce.motivazione}</p>
      <div className="rounded-control bg-sunken px-3 py-2">
        <p className="text-small text-ink-3">
          Dal bando, sezione {voce.riferimento_bando.sezione}
          {!voce.riferimento_bando.verificata && " (citazione non verificata)"}
        </p>
        <p className="mt-1 italic text-ink">«{voce.riferimento_bando.testo}»</p>
      </div>
      {mostraDato &&
        (voce.dato_azienda ? (
          <p className="text-small text-ink-3">
            Dato aziendale usato: {fieldLabel(voce.dato_azienda.campo)},{" "}
            <span className="font-medium text-ink">{voce.dato_azienda.valore}</span>
          </p>
        ) : (
          <p className="text-small text-ink-3">
            Nessun dato aziendale disponibile per questa verifica.
          </p>
        ))}
    </div>
  );
}

/** Corpo del report AI-check (esito, punteggio, verdetti, forza/debolezza).
 *  Presentazionale e riusabile: lo vede il cliente sulla scheda del bando e il
 *  progettista sul dettaglio della richiesta. `mostraAzioni` nasconde i link
 *  «completa i dati» che hanno senso solo per il titolare. L'AI estrae e
 *  valuta, ma esito e punteggio li calcola il server: qui si mostrano. */
export function AiReportBody({
  report,
  mostraAzioni = true,
}: {
  report: AiReport;
  mostraAzioni?: boolean;
}) {
  const punteggio = report.punteggio_totale;
  const daApprofondire = report.esito_ammissibilita === "non_ammissibile";

  return (
    <div className="flex flex-col gap-8">
      {/* Esito e punteggio */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <AiEsitoBadge esito={report.esito_ammissibilita} />
          {report.tipo_punteggio === "stima" ? (
            <Badge area="aicheck">Stima del punteggio ufficiale</Badge>
          ) : (
            <Badge area="aicheck">Punteggio euristico interno</Badge>
          )}
        </div>
        {punteggio !== null ? (
          <div className="flex max-w-[520px] items-center gap-5">
            <ProgressRing
              value={punteggio}
              max={100}
              size={96}
              tono="fit"
              label={`Punteggio di compatibilità: ${punteggio} su 100`}
            >
              {punteggio}
            </ProgressRing>
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-title-group text-ink">Punteggio di compatibilità</span>
              <span className="text-small text-ink-3 tabular-nums" aria-hidden>
                {punteggio} su 100
              </span>
              {report.griglia.soglia_minima !== null &&
                report.griglia.punti_ottenuti_stimati !== null && (
                  <p className="text-small text-ink-3">
                    {report.griglia.punti_ottenuti_stimati} punti stimati su soglia minima{" "}
                    {report.griglia.soglia_minima}
                  </p>
                )}
            </div>
          </div>
        ) : (
          <p className="text-body text-ink-2">
            Punteggio non calcolabile: il bando non specifica criteri confrontabili.
          </p>
        )}
        {daApprofondire && (
          <p className="text-small text-ink-2">
            L'analisi segnala alcuni requisiti da approfondire: guarda i dettagli qui sotto e
            verifica sempre il testo ufficiale del bando.
          </p>
        )}
        {report.esito_ammissibilita === "da_verificare" && (
          <p className="text-small font-medium text-warning-ink">
            Esito provvisorio: completa i dati indicati sotto per una verifica piena.
          </p>
        )}
      </div>

      {/* Requisiti obbligatori */}
      {report.requisiti.length > 0 && (
        <Section>
          <SectionHeader
            livello={3}
            titolo={`Requisiti di ammissibilità (${report.requisiti.length})`}
          />
          <p className="text-small text-ink-2">
            La verifica punto per punto dei requisiti obbligatori del bando: apri ogni voce per
            vedere il passaggio citato e il dato aziendale usato.
          </p>
          <Accordion
            items={report.requisiti.map((r) => ({
              id: `ai-requisito-${r.id}`,
              titolo: <TitoloVoce voce={r} />,
              children: <DettaglioVoce voce={r} mostraDato={false} />,
            }))}
          />
        </Section>
      )}

      {/* Criteri di valutazione */}
      {report.criteri.length > 0 && (
        <Section>
          <SectionHeader
            livello={3}
            titolo={`Criteri di valutazione (${report.criteri.length})`}
          />
          <Accordion
            items={report.criteri.map((c) => ({
              id: `ai-criterio-${c.id}`,
              titolo: (
                <TitoloVoce
                  voce={c}
                  punti={
                    c.punti_max !== null && c.punteggio_parziale !== null
                      ? `${c.punteggio_parziale}/${c.punti_max} punti`
                      : undefined
                  }
                />
              ),
              children: <DettaglioVoce voce={c} />,
            }))}
          />
        </Section>
      )}

      {/* Punti di forza / debolezza */}
      {(report.punti_di_forza.length > 0 || report.punti_di_debolezza.length > 0) && (
        <div className="grid gap-6 sm:grid-cols-2">
          {report.punti_di_forza.length > 0 && (
            <Section>
              <SectionHeader livello={3} titolo="Punti di forza" />
              <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body text-ink marker:text-fit">
                {report.punti_di_forza.map((p, i) => (
                  <li key={i}>{p.testo}</li>
                ))}
              </ul>
            </Section>
          )}
          {report.punti_di_debolezza.length > 0 && (
            <Section>
              <SectionHeader livello={3} titolo="Punti di debolezza" />
              <ul className="flex list-disc flex-col gap-1.5 pl-5 text-body text-ink marker:text-ink-3">
                {report.punti_di_debolezza.map((p, i) => (
                  <li key={i}>{p.testo}</li>
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}

      {/* Dati mancanti */}
      {report.dati_mancanti.length > 0 && (
        <Alert tono="attenzione" titolo="Dati mancanti per completare la verifica">
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {report.dati_mancanti.map((d, i) => (
              <li key={i}>
                {d.campo ? <span className="font-semibold">{fieldLabel(d.campo)}: </span> : null}
                {d.descrizione}
              </li>
            ))}
          </ul>
          {mostraAzioni && (
            <p className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-small">
              <TextLink to="/app/azienda">Completa i dati aziendali</TextLink>
              <TextLink to="/app/azienda">Importa dal Registro Imprese</TextLink>
            </p>
          )}
        </Alert>
      )}

      <p className="border-t border-line pt-4 text-small text-ink-3">{report.disclaimer}</p>
    </div>
  );
}
