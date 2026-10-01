import type { ReactNode } from "react";
import { BACHECA_COPY, PARTNER_COPY } from "../../lib/copy";
import { nomePaese } from "../../lib/paesi";
import type { PartnerSuggerito } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { DefinitionList, type Definizione } from "../ui/Facts";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { MatchSpiegazione } from "./MatchSpiegazione";

/** Quante competenze in vista prima del «e altre N». */
const COMPETENZE_IN_VISTA = 8;

function Chips({ voci }: { voci: string[] }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((voce, i) => (
        <li key={`${i}-${voce}`}>
          <Badge>{voce}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** Un'azienda suggerita al proponente di una call, come riga: nome del
 *  registro oppure «Azienda anonima» con regione, sezione ATECO, classe
 *  dimensionale e fascia di fatturato (Q12); un riferimento valido solo per
 *  questa call (mai l'id dell'azienda); il confronto in vista «terzi» (solo
 *  fasce ed esiti, nessun importo) e il profilo pubblico. La card è un `<li>`. */
export function SuggeritoCard({
  suggerito,
  testi,
  azione,
}: {
  suggerito: PartnerSuggerito;
  /** Testo dei requisiti per etichetta, dalla call del proponente. */
  testi?: ReadonlyMap<string, string>;
  /** Azione sull'azienda (WP7: «Invita» o lo stato dell'invito), a destra. */
  azione?: ReactNode;
}) {
  const { profilo, match, pseudonimo } = suggerito;
  const classe = profilo.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[profilo.classe_dimensionale] ?? profilo.classe_dimensionale)
    : null;
  const dove = [classe, profilo.regione_sede, profilo.ateco_sezione?.descrizione ?? null].filter(
    Boolean,
  ) as string[];
  const competenze = profilo.competenze.map((c) => c.etichetta);
  const altre = competenze.length - COMPETENZE_IN_VISTA;
  const esperienze = profilo.esperienze.map((e) =>
    [e.programma, e.anno ? String(e.anno) : null, e.ruolo, e.titolo].filter(Boolean).join(", "),
  );
  const disponibilita = [
    ...profilo.ruoli_disponibili.map((r) => BACHECA_COPY.ruoli[r] ?? r),
    ...profilo.forme_accettate.map((f) => f.etichetta),
  ];
  const interessi = [...profilo.regioni_interesse, ...profilo.paesi_interesse.map(nomePaese)];
  const altroProfilo =
    !!profilo.descrizione_competenze ||
    esperienze.length > 0 ||
    profilo.certificazioni.length > 0 ||
    disponibilita.length > 0 ||
    interessi.length > 0 ||
    !!profilo.infrastrutture;

  const voci: Definizione[] = [];
  if (profilo.tipi_soggetto.length > 0) {
    voci.push({
      etichetta: "Tipo di soggetto",
      valore: (
        <Chips
          voci={profilo.tipi_soggetto.map((t) =>
            t.fonte === "dichiarato" ? `${t.etichetta} (dichiarato)` : t.etichetta,
          )}
        />
      ),
    });
  }
  if (competenze.length > 0) {
    voci.push({
      etichetta: "Competenze",
      valore: <Chips voci={competenze.slice(0, COMPETENZE_IN_VISTA)} />,
      nota: altre > 0 ? `e altre ${altre} nel profilo` : undefined,
    });
  }

  const profiloCompleto: Definizione[] = [];
  if (altre > 0) profiloCompleto.push({ etichetta: "Tutte le competenze", valore: <Chips voci={competenze} /> });
  if (profilo.competenze_libere.length > 0) {
    profiloCompleto.push({ etichetta: "Altre competenze", valore: <Chips voci={profilo.competenze_libere} /> });
  }
  if (profilo.descrizione_competenze) {
    profiloCompleto.push({
      etichetta: "In breve",
      valore: <span className="whitespace-pre-line">{profilo.descrizione_competenze}</span>,
    });
  }
  if (esperienze.length > 0) {
    profiloCompleto.push({
      etichetta: "Esperienze",
      valore: (
        <ul className="flex flex-col gap-0.5">
          {esperienze.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ),
    });
  }
  if (profilo.certificazioni.length > 0) {
    profiloCompleto.push({ etichetta: "Certificazioni", valore: <Chips voci={profilo.certificazioni} /> });
  }
  if (profilo.infrastrutture) {
    profiloCompleto.push({
      etichetta: "Infrastrutture",
      valore: <span className="whitespace-pre-line">{profilo.infrastrutture}</span>,
    });
  }
  if (disponibilita.length > 0) {
    profiloCompleto.push({ etichetta: "Disponibile come", valore: <Chips voci={disponibilita} /> });
  }
  if (interessi.length > 0) {
    profiloCompleto.push({ etichetta: "Territori d'interesse", valore: <Chips voci={interessi} /> });
  }

  return (
    <li>
      <Card area="partenariati" className="flex flex-col gap-4 md:flex-row md:gap-6">
        <div className="flex min-w-0 grow flex-col gap-3">
          <div className="flex flex-col gap-0.5">
            <h3 className="font-sans text-row-title text-ink">
              {profilo.denominazione ?? PARTNER_COPY.aziendaAnonima}
            </h3>
            {dove.length > 0 && (
              <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
                {dove.map((d) => (
                  <span key={d}>{d}</span>
                ))}
              </p>
            )}
            <p className="text-small text-ink-3">Riferimento per questa call: {pseudonimo}</p>
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <MatchBadge match={match} persona="lei" />
            <AttenzioneBadge match={match} />
          </div>
          <MatchSpiegazione match={match} persona="lei" testi={testi} compatta />

          {voci.length > 0 && <DefinitionList items={voci} />}

          {(altroProfilo || altre > 0) && (
            <details className="group">
              <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded-mark text-small font-medium text-accent-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
                <span className="group-open:hidden">Profilo completo</span>
                <span className="hidden group-open:inline">Nascondi il profilo</span>
              </summary>
              <div className="mt-3">
                <DefinitionList items={profiloCompleto} />
              </div>
            </details>
          )}
        </div>
        {azione && <div className="shrink-0 md:w-48">{azione}</div>}
      </Card>
    </li>
  );
}
