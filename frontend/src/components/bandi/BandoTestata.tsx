import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import type { OrigineLinkScheda } from "../../types";
import { buttonClasses } from "../ui/Button";
import { Facts, type Fatto } from "../ui/Facts";
import { PageHeader, type Ritorno } from "../ui/PageHeader";
import { StatoBadge } from "./badges";

export interface BandoTestataProps {
  titolo: string;
  /** Stato del catalogo, in parole (`StatoBadge`). */
  stato: string | null;
  tipologia?: string | null;
  modalita?: string | null;
  /** Programma di finanziamento (PNRR, FESR…), accanto a tipologia e modalità. */
  programma?: string | null;
  ente?: string | null;
  /** «Vai al bando»: l'UNICO pulsante pieno della scheda. Il sito sotto
   *  («Sito ufficiale: host») solo per l'origine `fonte_ufficiale`, l'unica
   *  verificata (docs/api.md): un portale o il link del bando non lo sono. */
  cta?: { url: string; host: string | null; origine?: OrigineLinkScheda } | null;
  /** Al posto di «Vai al bando» quando non c'è (bando non aperto): il perché. */
  notaSenzaCta?: string;
  /** Azione secondaria accanto al primario (il segnalibro). */
  azioni?: ReactNode;
  /** Fatti chiave (scadenza con il tempo relativo, dotazione, contributo
   *  massimo, apertura): la riga non compare se vuota. */
  fatti: Fatto[];
  /** A destra dei fatti («Aggiungi la scadenza al calendario»). */
  azioneFatti?: ReactNode;
  indietro?: Ritorno;
}

/** Intestazione della scheda del bando (tavola `BandoDettaglio`): ritorno,
 *  stato e tipologia, titolo `title-bando`, ente; a destra le azioni; sotto i
 *  fatti chiave. Presentazionale, senza hook: la vetrina la mostra con dati
 *  finti. */
export function BandoTestata({
  titolo,
  stato,
  tipologia,
  modalita,
  programma,
  ente,
  cta,
  notaSenzaCta,
  azioni,
  fatti,
  azioneFatti,
  indietro,
}: BandoTestataProps) {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        indietro={indietro}
        stileTitolo="bando"
        sopra={
          <>
            <StatoBadge stato={stato} />
            {tipologia && <span className="text-small text-ink-2">{tipologia}</span>}
            {modalita && <span className="text-small text-ink-2">{modalita}</span>}
            {programma && <span className="text-small text-ink-2">{programma}</span>}
          </>
        }
        titolo={titolo}
        descrizione={ente ?? undefined}
        azioni={
          (azioni || cta) && (
            // DOM nell'ordine della tavola MobileBando (Vai al bando → sito → Salva);
            // da `sm` l'ordine del desktop con `sm:order-*`: Salva, Vai al bando, sito
            // sotto a tutta larghezza. Nessun pulsante duplicato.
            <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center sm:justify-end sm:gap-x-2 sm:gap-y-1.5">
              {cta && (
                <a
                  href={cta.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={buttonClasses("primary", "md", "w-full sm:order-2 sm:w-auto")}
                >
                  Vai al bando
                  <ExternalLink className="size-4" aria-hidden />
                </a>
              )}
              {cta?.origine === "fonte_ufficiale" && cta.host && (
                <p className="text-small text-ink-3 sm:order-3 sm:basis-full sm:text-right">
                  Sito ufficiale: {cta.host}
                </p>
              )}
              {!cta && notaSenzaCta && (
                <p className="text-small text-ink-2 sm:order-2 sm:basis-full sm:text-right">
                  {notaSenzaCta}
                </p>
              )}
              {azioni && <div className="sm:order-1">{azioni}</div>}
            </div>
          )
        }
      />
      {fatti.length > 0 && <Facts items={fatti} azione={azioneFatti} />}
    </div>
  );
}
