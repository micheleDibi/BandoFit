import { AlertTriangle, CheckCircle2, HelpCircle, Lock, MinusCircle, XCircle } from "lucide-react";
import { useId } from "react";
import { cn } from "../../lib/cn";
import { CALL_COPY, CONSORZIO_COPY } from "../../lib/copy";
import type { CellaMatrice, EsitoCoperturaCall, MatriceCoperturaConsorzio } from "../../types";
import { Badge } from "../ui/Badge";
import { Card } from "../ui/Card";
import { EsitoVoceBadge } from "./ValidatoreChecklist";

const ICONE: Record<EsitoCoperturaCall, { icona: typeof CheckCircle2; colore: string }> = {
  coperto: { icona: CheckCircle2, colore: "text-emerald-600" },
  non_coperto: { icona: XCircle, colore: "text-red-600" },
  dato_mancante: { icona: HelpCircle, colore: "text-amber-600" },
  incerto: { icona: AlertTriangle, colore: "text-amber-600" },
  non_valutabile: { icona: MinusCircle, colore: "text-slate-500" },
};

function Cella({ cella }: { cella: CellaMatrice | undefined }) {
  if (!cella) return <span className="text-slate-400">—</span>;
  if (!cella.si_applica) {
    return <span className="text-xs text-slate-500">Non si applica</span>;
  }
  const { icona: Icona, colore } = ICONE[cella.esito] ?? ICONE.non_valutabile;
  return (
    <div className="space-y-1">
      {/* Il testo completo (tipo di requisito, esito, fonte) per i lettori di
          schermo e al passaggio del mouse: in cella resta l'esito breve. */}
      <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-slate-700" title={cella.testo}>
        <Icona className={cn("size-3.5 shrink-0", colore)} aria-hidden />
        <span aria-hidden>{CONSORZIO_COPY.copertura[cella.esito] ?? cella.esito}</span>
        <span className="sr-only">{cella.testo}</span>
        {cella.fonte === "dichiarato" && (
          <span className="font-normal text-amber-700">· {CONSORZIO_COPY.dichiarato}</span>
        )}
      </span>
      {cella.testo_privato && (
        <p className="flex max-w-56 items-start gap-1 text-xs text-brand-800">
          <Lock className="mt-0.5 size-3 shrink-0" aria-hidden />
          <span>
            <span className="sr-only">Solo per te: </span>
            {cella.testo_privato}
          </span>
        </p>
      )}
    </div>
  );
}

/** Matrice di copertura (V3): requisiti della call × membri del consorzio,
 *  con la stessa valutazione dei suggerimenti. Nella tua colonna i tuoi
 *  numeri (solo per te), nelle altre gli esiti sulle fasce pubbliche. Tabella
 *  accessibile: `caption`, `th scope`, prima colonna ferma durante lo scorrimento. */
export function MatriceCopertura({
  matrice,
  nomi,
}: {
  matrice: MatriceCoperturaConsorzio;
  /** Nome da mostrare per id del membro. */
  nomi: ReadonlyMap<string, string>;
}) {
  const idTitolo = useId();
  const rapporto = matrice.copertura_gap_ratio;
  return (
    <Card className="p-5">
      <section aria-labelledby={idTitolo}>
        <h2 id={idTitolo} className="font-display text-base font-semibold text-slate-900">
          Copertura dei requisiti
        </h2>
        <p className="mt-1 text-sm text-slate-600">
          Per ogni requisito della call, chi lo copre nel consorzio. Degli altri membri vedi solo
          l'esito sulle fasce pubbliche; i tuoi numeri li vedi solo tu.
        </p>
        {rapporto !== null && (
          <p className="mt-2 text-sm text-slate-700">
            Requisiti cercati coperti dal consorzio:{" "}
            <span className="font-semibold tabular">{Math.round(rapporto * 100)}%</span>
          </p>
        )}
        {matrice.righe.length === 0 ? (
          <p className="mt-3 text-sm text-slate-500">
            La call non ha requisiti salvati: la copertura si calcola sui requisiti della call.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200">
            <table className="min-w-full border-collapse text-left text-sm">
              <caption className="sr-only">
                Copertura dei requisiti della call da parte di ogni membro del consorzio
              </caption>
              <thead className="bg-slate-50 text-xs font-medium text-slate-500">
                <tr>
                  <th scope="col" className="sticky left-0 z-10 min-w-48 bg-slate-50 px-3 py-2">
                    Requisito
                  </th>
                  {matrice.membri.map((mid) => (
                    <th key={mid} scope="col" className="min-w-32 px-3 py-2">
                      {nomi.get(mid) ?? "Membro"}
                    </th>
                  ))}
                  <th scope="col" className="min-w-32 px-3 py-2">
                    Consorzio
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {matrice.righe.map((riga) => (
                  <tr key={riga.requisito_id} className="align-top">
                    <th scope="row" className="sticky left-0 z-10 bg-white px-3 py-2.5 font-normal">
                      <span className="flex items-start gap-2">
                        <Badge tone="brand" className="shrink-0 tabular">
                          {riga.etichetta}
                        </Badge>
                        <span className="min-w-0">
                          {riga.testo && <span className="block text-slate-800">{riga.testo}</span>}
                          <span className="block text-xs text-slate-500">
                            {CALL_COPY.ambiti[riga.ambito]}
                            {riga.cercato ? " · cercato" : ""}
                          </span>
                        </span>
                      </span>
                    </th>
                    {matrice.membri.map((mid) => (
                      <td key={mid} className="px-3 py-2.5">
                        <Cella cella={riga.celle.find((c) => c.membro_id === mid)} />
                      </td>
                    ))}
                    <td className="px-3 py-2.5">
                      <EsitoVoceBadge esito={riga.esito} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </Card>
  );
}
