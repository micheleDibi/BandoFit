import { Building2, EyeOff } from "lucide-react";
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

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

function Chips({ voci, tono = "slate" }: { voci: string[]; tono?: "slate" | "brand" }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((voce, i) => (
        <li key={`${i}-${voce}`}>
          <Badge tone={tono}>{voce}</Badge>
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

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        <div
          className={cn(
            "rounded-lg p-2",
            dati.anonimo ? "bg-slate-100 text-slate-500" : "bg-brand-50 text-brand-600",
          )}
        >
          {dati.anonimo ? (
            <EyeOff className="size-5" aria-hidden />
          ) : (
            <Building2 className="size-5" aria-hidden />
          )}
        </div>
        <div className="min-w-0">
          <p className="font-display text-base font-semibold text-slate-900">
            {dati.denominazione ?? PARTNER_COPY.aziendaAnonima}
          </p>
          <p className="text-xs text-slate-500">
            {[classe, dati.regione_sede].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
      </div>

      <dl className="space-y-3">
        {dati.ateco_sezione && (
          <Voce titolo="Settore di attività">
            {dati.ateco_sezione.lettera} — {dati.ateco_sezione.descrizione}
          </Voce>
        )}
        {fasce.length > 0 && (
          <Voce titolo="Dai bilanci (per fasce)">
            <ul className="space-y-0.5">
              {fasce.map((f) => (
                <li key={f.tipo}>
                  <span className="text-slate-500">{FASCE_TITOLI[f.tipo]}:</span> {f.valore}
                </li>
              ))}
            </ul>
          </Voce>
        )}
        {dati.tipi_soggetto.length > 0 && (
          <Voce titolo="Tipo di soggetto">
            <ul className="flex flex-wrap gap-1.5">
              {dati.tipi_soggetto.map((t) => (
                <li key={t.codice}>
                  <Badge tone={t.fonte === "registro" ? "brand" : "slate"}>
                    {t.etichetta}
                    <span className="text-[11px] font-normal opacity-80">
                      {t.fonte === "registro" ? " · dal Registro Imprese" : " · dichiarato"}
                    </span>
                  </Badge>
                </li>
              ))}
            </ul>
          </Voce>
        )}
        {(dati.competenze.length > 0 || dati.competenze_libere.length > 0) && (
          <Voce titolo="Competenze">
            <Chips
              voci={[
                ...dati.competenze.map((c) => c.etichetta),
                ...dati.competenze_libere,
              ]}
              tono="brand"
            />
          </Voce>
        )}
        {dati.descrizione_competenze && (
          <Voce titolo="Descrizione">
            <span className="whitespace-pre-line">{dati.descrizione_competenze}</span>
          </Voce>
        )}
        {dati.esperienze.length > 0 && (
          <Voce titolo="Esperienze">
            <ul className="space-y-1">
              {dati.esperienze.map((e, i) => (
                <li key={i}>
                  <span className="font-medium text-slate-800">{e.programma}</span>
                  {[e.anno, e.ruolo ? (RUOLI[e.ruolo] ?? e.ruolo) : null]
                    .filter(Boolean)
                    .map((v) => ` · ${v}`)
                    .join("")}
                  {e.titolo && <span className="block text-xs text-slate-500">{e.titolo}</span>}
                </li>
              ))}
            </ul>
          </Voce>
        )}
        {dati.certificazioni.length > 0 && (
          <Voce titolo={dati.anonimo ? "Certificazioni (per categoria)" : "Certificazioni"}>
            <Chips voci={dati.certificazioni} />
          </Voce>
        )}
        {dati.infrastrutture && (
          <Voce titolo="Infrastrutture">
            <span className="whitespace-pre-line">{dati.infrastrutture}</span>
          </Voce>
        )}
        {(dati.regioni_interesse.length > 0 || dati.paesi_interesse.length > 0) && (
          <Voce titolo="Dove vuole lavorare">
            {[...dati.regioni_interesse, ...dati.paesi_interesse.map(nomePaese)].join(", ")}
          </Voce>
        )}
        {(dati.ruoli_disponibili.length > 0 || dati.forme_accettate.length > 0) && (
          <Voce titolo="Come vuole partecipare">
            {[
              ...dati.ruoli_disponibili.map((r) => RUOLI[r] ?? r),
              ...dati.forme_accettate.map((f) => f.etichetta),
            ].join(", ")}
          </Voce>
        )}
        <Voce titolo="Inviti">
          {dati.accetta_inviti ? "Accetta inviti da altre aziende" : "Per ora non accetta inviti"}
        </Voce>
      </dl>
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
      <div className="space-y-3" aria-hidden>
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
    <Card className={cn("p-5", className)}>
      <h3 id="partner-anteprima-titolo" className="font-display text-sm font-semibold text-slate-900">
        {PARTNER_COPY.anteprimaTitolo}
      </h3>
      <p className="mt-0.5 text-xs text-slate-500">
        {visibile ? PARTNER_COPY.anteprimaNota : PARTNER_COPY.anteprimaNonVisibile}
      </p>
      <div className="mt-4" aria-labelledby="partner-anteprima-titolo" role="group">
        {corpo}
      </div>
    </Card>
  );
}
