import { Building2, EyeOff } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { BACHECA_COPY, PARTNER_COPY } from "../../lib/copy";
import { nomePaese } from "../../lib/paesi";
import type { PartnerSuggerito } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { AttenzioneBadge, MatchBadge } from "./MatchBadge";
import { MatchSpiegazione } from "./MatchSpiegazione";

/** Quante competenze in vista prima del «e altre N». */
const COMPETENZE_IN_VISTA = 8;

function Voce({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-400">{titolo}</dt>
      <dd className="mt-1 text-sm text-slate-700">{children}</dd>
    </div>
  );
}

function Chips({ voci }: { voci: string[] }) {
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((voce, i) => (
        <li key={`${i}-${voce}`}>
          <Badge tone="slate">{voce}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** Un'azienda suggerita al proponente di una call: nome del registro oppure
 *  «Azienda anonima» con regione, sezione ATECO, classe dimensionale e fascia
 *  di fatturato (Q12); un riferimento valido solo per questa call (mai l'id
 *  dell'azienda); il confronto in vista «terzi» (solo fasce ed esiti, nessun
 *  importo) e il profilo pubblico. La card è un `<li>`. */
export function SuggeritoCard({
  suggerito,
  testi,
  azione,
}: {
  suggerito: PartnerSuggerito;
  /** Testo dei requisiti per etichetta, dalla call del proponente. */
  testi?: ReadonlyMap<string, string>;
  /** Azione sull'azienda (WP7: «Invita» o lo stato dell'invito), in alto a destra. */
  azione?: ReactNode;
}) {
  const { profilo, match, pseudonimo } = suggerito;
  const classe = profilo.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[profilo.classe_dimensionale] ?? profilo.classe_dimensionale)
    : null;
  const dove = [
    classe,
    profilo.regione_sede,
    profilo.ateco_sezione ? `${profilo.ateco_sezione.lettera} — ${profilo.ateco_sezione.descrizione}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const competenze = profilo.competenze.map((c) => c.etichetta);
  const altre = competenze.length - COMPETENZE_IN_VISTA;
  const esperienze = profilo.esperienze.map((e) =>
    [e.programma, e.anno ? String(e.anno) : null, e.ruolo, e.titolo].filter(Boolean).join(" · "),
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

  return (
    <li>
      <Card className="p-4">
        <div className="flex items-start gap-3">
          <div
            className={cn(
              "rounded-lg p-2",
              profilo.anonimo ? "bg-slate-100 text-slate-500" : "bg-brand-50 text-brand-600",
            )}
          >
            {profilo.anonimo ? (
              <EyeOff className="size-5" aria-hidden />
            ) : (
              <Building2 className="size-5" aria-hidden />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-base font-semibold text-slate-900">
              {profilo.denominazione ?? PARTNER_COPY.aziendaAnonima}
            </h3>
            <p className="text-xs text-slate-500">{dove || "—"}</p>
            <p className="mt-0.5 text-xs text-slate-400">
              Riferimento per questa call: <span className="font-mono tracking-wide">{pseudonimo}</span>
            </p>
          </div>
          {azione && <div className="shrink-0">{azione}</div>}
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <MatchBadge match={match} persona="lei" />
          <AttenzioneBadge match={match} />
        </div>
        <div className="mt-2">
          <MatchSpiegazione match={match} persona="lei" testi={testi} compatta />
        </div>

        <dl className="mt-3 space-y-3 border-t border-slate-100 pt-3">
          {profilo.tipi_soggetto.length > 0 && (
            <Voce titolo="Tipo di soggetto">
              <Chips
                voci={profilo.tipi_soggetto.map((t) =>
                  t.fonte === "dichiarato" ? `${t.etichetta} (dichiarato)` : t.etichetta,
                )}
              />
            </Voce>
          )}
          {competenze.length > 0 && (
            <Voce titolo="Competenze">
              <Chips voci={competenze.slice(0, COMPETENZE_IN_VISTA)} />
              {altre > 0 && <p className="mt-1 text-xs text-slate-500">e altre {altre} nel profilo</p>}
            </Voce>
          )}
        </dl>

        {(altroProfilo || altre > 0) && (
          <details className="group mt-3">
            <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded text-sm font-medium text-brand-600 hover:text-brand-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500">
              <span className="group-open:hidden">Profilo completo</span>
              <span className="hidden group-open:inline">Nascondi il profilo</span>
            </summary>
            <dl className="mt-3 space-y-3">
              {altre > 0 && (
                <Voce titolo="Tutte le competenze">
                  <Chips voci={competenze} />
                </Voce>
              )}
              {profilo.competenze_libere.length > 0 && (
                <Voce titolo="Altre competenze">
                  <Chips voci={profilo.competenze_libere} />
                </Voce>
              )}
              {profilo.descrizione_competenze && (
                <Voce titolo="In breve">
                  <span className="whitespace-pre-line">{profilo.descrizione_competenze}</span>
                </Voce>
              )}
              {esperienze.length > 0 && (
                <Voce titolo="Esperienze">
                  <ul className="list-disc space-y-0.5 pl-5">
                    {esperienze.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </Voce>
              )}
              {profilo.certificazioni.length > 0 && (
                <Voce titolo="Certificazioni">
                  <Chips voci={profilo.certificazioni} />
                </Voce>
              )}
              {profilo.infrastrutture && (
                <Voce titolo="Infrastrutture">
                  <span className="whitespace-pre-line">{profilo.infrastrutture}</span>
                </Voce>
              )}
              {disponibilita.length > 0 && (
                <Voce titolo="Disponibile come">
                  <Chips voci={disponibilita} />
                </Voce>
              )}
              {interessi.length > 0 && (
                <Voce titolo="Territori d'interesse">
                  <Chips voci={interessi} />
                </Voce>
              )}
            </dl>
          </details>
        )}
      </Card>
    </li>
  );
}
