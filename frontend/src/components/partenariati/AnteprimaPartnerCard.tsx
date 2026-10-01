import type { ReactNode } from "react";
import { usePartnerAnteprima } from "../../hooks/usePartnerProfile";
import { apiErrorMessage } from "../../lib/api";
import { etichettaFascia, FASCE_TITOLI, type TipoFascia } from "../../lib/bilanci";
import { cn } from "../../lib/cn";
import { PARTNER_COPY } from "../../lib/copy";
import { nomePaese } from "../../lib/paesi";
import type { PartnerPubblico } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { DefinitionList, type Definizione } from "../ui/Facts";
import { ErrorState, Skeleton } from "../ui/states";

/** Classe dimensionale del Registro Imprese, in parole. */
export const CLASSI_DIMENSIONALI: Record<string, string> = {
  micro: "Micro impresa",
  piccola: "Piccola impresa",
  media: "Media impresa",
  grande: "Grande impresa",
};

const RUOLI: Record<string, string> = { capofila: "Capofila", partner: "Partner" };

/** Fasce pubbliche → tipi di `lib/bilanci` (lì l'andamento si chiama
 *  `trend_fatturato`). Un codice sconosciuto resta visibile com'è. */
const FASCE: Array<[keyof PartnerPubblico["fasce"], TipoFascia]> = [
  ["fatturato", "fatturato"],
  ["trend", "trend_fatturato"],
  ["patrimonio_netto", "patrimonio_netto"],
  ["dipendenti", "dipendenti"],
];

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

function Contenuto({ dati }: { dati: PartnerPubblico }) {
  const fasce = FASCE.map(([chiave, tipo]) => ({
    tipo,
    valore: etichettaFascia(tipo, dati.fasce[chiave]),
  })).filter((f) => f.valore !== null);
  const classe = dati.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[dati.classe_dimensionale] ?? dati.classe_dimensionale)
    : null;
  const dove = [classe, dati.regione_sede].filter(Boolean) as string[];

  const voci: Definizione[] = [];
  if (dati.ateco_sezione) {
    voci.push({
      etichetta: "Settore di attività",
      valore: `${dati.ateco_sezione.lettera} — ${dati.ateco_sezione.descrizione}`,
    });
  }
  if (fasce.length > 0) {
    voci.push({
      etichetta: "Dai bilanci (per fasce)",
      valore: (
        <ul className="flex flex-col gap-0.5">
          {fasce.map((f) => (
            <li key={f.tipo}>
              <span className="text-ink-3">{FASCE_TITOLI[f.tipo]}:</span> {f.valore}
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (dati.tipi_soggetto.length > 0) {
    voci.push({
      etichetta: "Tipo di soggetto",
      valore: (
        <ul className="flex flex-wrap gap-1.5">
          {dati.tipi_soggetto.map((t) => (
            <li key={t.codice}>
              <Badge>
                {t.etichetta}
                <span className="font-normal">
                  {t.fonte === "registro" ? " (dal Registro Imprese)" : " (dichiarato)"}
                </span>
              </Badge>
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (dati.competenze.length > 0 || dati.competenze_libere.length > 0) {
    voci.push({
      etichetta: "Competenze",
      valore: <Chips voci={[...dati.competenze.map((c) => c.etichetta), ...dati.competenze_libere]} />,
    });
  }
  if (dati.descrizione_competenze) {
    voci.push({
      etichetta: "Descrizione",
      valore: <span className="whitespace-pre-line">{dati.descrizione_competenze}</span>,
    });
  }
  if (dati.esperienze.length > 0) {
    voci.push({
      etichetta: "Esperienze",
      valore: (
        <ul className="flex flex-col gap-1">
          {dati.esperienze.map((e, i) => (
            <li key={i}>
              <span className="font-medium text-ink">{e.programma}</span>
              {[e.anno, e.ruolo ? (RUOLI[e.ruolo] ?? e.ruolo) : null]
                .filter(Boolean)
                .map((v) => `, ${v}`)
                .join("")}
              {e.titolo && <span className="block text-small text-ink-3">{e.titolo}</span>}
            </li>
          ))}
        </ul>
      ),
    });
  }
  if (dati.certificazioni.length > 0) {
    voci.push({
      etichetta: dati.anonimo ? "Certificazioni (per categoria)" : "Certificazioni",
      valore: <Chips voci={dati.certificazioni} />,
    });
  }
  if (dati.infrastrutture) {
    voci.push({
      etichetta: "Infrastrutture",
      valore: <span className="whitespace-pre-line">{dati.infrastrutture}</span>,
    });
  }
  if (dati.regioni_interesse.length > 0 || dati.paesi_interesse.length > 0) {
    voci.push({
      etichetta: "Dove vuole lavorare",
      valore: [...dati.regioni_interesse, ...dati.paesi_interesse.map(nomePaese)].join(", "),
    });
  }
  if (dati.ruoli_disponibili.length > 0 || dati.forme_accettate.length > 0) {
    voci.push({
      etichetta: "Come vuole partecipare",
      valore: [
        ...dati.ruoli_disponibili.map((r) => RUOLI[r] ?? r),
        ...dati.forme_accettate.map((f) => f.etichetta),
      ].join(", "),
    });
  }
  voci.push({
    etichetta: "Inviti",
    valore: dati.accetta_inviti ? "Accetta inviti da altre aziende" : "Per ora non accetta inviti",
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-0.5">
        <p className="font-sans text-row-title text-ink">
          {dati.denominazione ?? PARTNER_COPY.aziendaAnonima}
        </p>
        {dove.length > 0 && (
          <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-2">
            {dove.map((d) => (
              <span key={d}>{d}</span>
            ))}
          </p>
        )}
      </div>
      <DefinitionList items={voci} />
    </div>
  );
}

/** «Come ti vedono le altre aziende»: la proiezione pubblica del profilo
 *  SALVATO, costruita dal server con la stessa whitelist usata verso terzi
 *  (niente contatti, importi esatti né referente). */
export function AnteprimaPartnerCard({
  visibile,
  className,
}: {
  visibile: boolean;
  className?: string;
}) {
  const { data, isPending, isError, error, refetch } = usePartnerAnteprima();

  let corpo: ReactNode;
  if (isPending) {
    corpo = (
      <div className="flex flex-col gap-3" aria-hidden>
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    );
  } else if (isError || !data) {
    corpo = <ErrorState message={apiErrorMessage(error)} onRetry={() => void refetch()} />;
  } else {
    corpo = <Contenuto dati={data} />;
  }

  return (
    <Card className={cn("flex flex-col gap-4", className)}>
      <div className="flex flex-col gap-0.5">
        <h3 id="partner-anteprima-titolo" className="font-sans text-title-group text-ink">
          {PARTNER_COPY.anteprimaTitolo}
        </h3>
        <p className="text-small text-ink-3">
          {visibile ? PARTNER_COPY.anteprimaNota : PARTNER_COPY.anteprimaNonVisibile}
        </p>
      </div>
      <div aria-labelledby="partner-anteprima-titolo" role="group">
        {corpo}
      </div>
    </Card>
  );
}
