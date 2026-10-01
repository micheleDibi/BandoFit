import { Lock } from "lucide-react";
import { CALL_COPY, CONSORZIO_COPY } from "../../lib/copy";
import type { CellaMatrice, EsitoCoperturaCall, MatriceCoperturaConsorzio } from "../../types";
import { Badge } from "../ui/Badge";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Status, type TonoStatus } from "../ui/Status";
import { EsitoVoceBadge } from "./ValidatoreChecklist";

const TONI: Record<EsitoCoperturaCall, TonoStatus> = {
  coperto: "aperto",
  non_coperto: "chiuso",
  dato_mancante: "in-apertura",
  incerto: "in-apertura",
  non_valutabile: "neutro",
};

function Cella({ cella }: { cella: CellaMatrice | undefined }) {
  if (!cella) return <span className="text-ink-off">—</span>;
  if (!cella.si_applica) {
    return <span className="text-small text-ink-3">Non si applica</span>;
  }
  return (
    <div className="flex flex-col gap-1">
      {/* Il testo completo (tipo di requisito, esito, fonte) per i lettori di
          schermo: in cella resta l'esito breve. */}
      <Status tono={TONI[cella.esito] ?? "neutro"}>
        <span aria-hidden>{CONSORZIO_COPY.copertura[cella.esito] ?? cella.esito}</span>
        <span className="sr-only">{cella.testo}</span>
      </Status>
      {cella.fonte === "dichiarato" && (
        <span className="text-small text-warning-ink">{CONSORZIO_COPY.dichiarato}</span>
      )}
      {cella.testo_privato && (
        <p className="flex max-w-56 items-start gap-1 text-small text-ink-2">
          <Lock className="mt-0.5 size-3.5 shrink-0 text-ink-3" aria-hidden />
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
  livello = 2,
}: {
  matrice: MatriceCoperturaConsorzio;
  /** Nome da mostrare per id del membro. */
  nomi: ReadonlyMap<string, string>;
  /** Livello del titolo della sezione: 3 dentro una sezione con il suo h2,
   *  come la call vista dal progettista. */
  livello?: 2 | 3;
}) {
  const rapporto = matrice.copertura_gap_ratio;
  return (
    <Section>
      <SectionHeader titolo="Copertura dei requisiti" livello={livello} />
      <p className="text-body text-ink-2">
        Per ogni requisito della call, chi lo copre nel consorzio. Degli altri membri vedi solo
        l'esito sulle fasce pubbliche; i tuoi numeri li vedi solo tu.
      </p>
      {rapporto !== null && (
        <p className="text-body text-ink">
          Requisiti cercati coperti dal consorzio:{" "}
          <span className="font-semibold tabular-nums">{Math.round(rapporto * 100)}%</span>
        </p>
      )}
      {matrice.righe.length === 0 ? (
        <p className="text-body text-ink-3">
          La call non ha requisiti salvati: la copertura si calcola sui requisiti della call.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-left text-body">
            <caption className="sr-only">
              Copertura dei requisiti della call da parte di ogni membro del consorzio
            </caption>
            <thead>
              <tr className="border-b border-line-control text-small font-medium text-ink-3">
                <th scope="col" className="sticky left-0 z-10 min-w-48 bg-sheet px-3 py-2.5">
                  Requisito
                </th>
                {matrice.membri.map((mid) => (
                  <th key={mid} scope="col" className="min-w-32 px-3 py-2.5 whitespace-nowrap">
                    {nomi.get(mid) ?? "Membro"}
                  </th>
                ))}
                <th scope="col" className="min-w-32 px-3 py-2.5">
                  Consorzio
                </th>
              </tr>
            </thead>
            <tbody>
              {matrice.righe.map((riga) => (
                <tr key={riga.requisito_id} className="border-b border-line align-top">
                  <th scope="row" className="sticky left-0 z-10 bg-sheet px-3 py-3 font-normal">
                    <span className="flex items-start gap-2">
                      <Badge className="mt-0.5 shrink-0 tabular-nums">{riga.etichetta}</Badge>
                      <span className="min-w-0">
                        {riga.testo && <span className="block text-ink">{riga.testo}</span>}
                        <span className="block text-small text-ink-3">
                          {CALL_COPY.ambiti[riga.ambito]}
                          {riga.cercato ? ", cercato" : ""}
                        </span>
                      </span>
                    </span>
                  </th>
                  {matrice.membri.map((mid) => (
                    <td key={mid} className="px-3 py-3">
                      <Cella cella={riga.celle.find((c) => c.membro_id === mid)} />
                    </td>
                  ))}
                  <td className="px-3 py-3">
                    <EsitoVoceBadge esito={riga.esito} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
