import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import type { OrigineLinkScheda } from "../../types";
import type { Area } from "../ui/area";
import { buttonClasses } from "../ui/Button";
import { Card } from "../ui/Card";
import type { Fatto } from "../ui/Facts";
import { IconChip, type IconaChip } from "../ui/IconChip";
import { PageHeader, type Ritorno } from "../ui/PageHeader";
import { StatoBadge } from "./badges";

/** Un fatto chiave della testata: come `Fatto`, più l'icona e il colore della
 *  sua piccola card (entrambi facoltativi: senza, l'icona non c'è). */
export interface FattoTestata extends Fatto {
  icon?: IconaChip;
  /** Colore dell'`IconChip` (default `bandi`). */
  area?: Area;
}

export interface BandoTestataProps {
  titolo: string;
  /** Stato del catalogo, in parole (`StatoBadge`). */
  stato: string | null;
  tipologia?: string | null;
  modalita?: string | null;
  /** Programma di finanziamento (PNRR, FESR…), accanto a tipologia e modalità. */
  programma?: string | null;
  ente?: string | null;
  /** «Vai al bando»: l'UNICO pulsante pieno della scheda (sulla fascia è
   *  `inverse`: bianco con il testo navy). Il sito sotto («Sito ufficiale:
   *  host») solo per l'origine `fonte_ufficiale`, l'unica verificata
   *  (docs/api.md): un portale o il link del bando non lo sono. */
  cta?: { url: string; host: string | null; origine?: OrigineLinkScheda } | null;
  /** Al posto di «Vai al bando» quando non c'è (bando non aperto): il perché. */
  notaSenzaCta?: string;
  /** Azione secondaria accanto al primario (il segnalibro, variante `fascia`). */
  azioni?: ReactNode;
  /** Fatti chiave (scadenza con il tempo relativo, dotazione, contributo
   *  massimo, apertura): una piccola card ciascuno; la riga non compare se vuota. */
  fatti: FattoTestata[];
  /** Sotto i fatti, a destra («Aggiungi la scadenza al calendario»). */
  azioneFatti?: ReactNode;
  indietro?: Ritorno;
}

/** Intestazione della scheda del bando (tavola `BandoDettaglio`): la fascia
 *  navy dell'area bandi con ritorno, stato e tipologia, titolo `title-bando`,
 *  ente e, a destra, le azioni; sotto, i fatti chiave in piccole card con
 *  l'icona colorata. Presentazionale, senza hook: la vetrina la mostra con dati
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
    <div className="flex flex-col gap-4">
      <PageHeader
        area="bandi"
        indietro={indietro}
        stileTitolo="bando"
        sopra={
          <>
            <StatoBadge stato={stato} />
            {tipologia && <span>{tipologia}</span>}
            {modalita && <span>{modalita}</span>}
            {programma && <span>{programma}</span>}
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
                  className={buttonClasses("inverse", "md", "w-full sm:order-2 sm:w-auto")}
                >
                  Vai al bando
                  <ExternalLink className="size-4" aria-hidden />
                </a>
              )}
              {cta?.origine === "fonte_ufficiale" && cta.host && (
                <p className="text-small text-white/80 sm:order-3 sm:basis-full sm:text-right">
                  Sito ufficiale: {cta.host}
                </p>
              )}
              {!cta && notaSenzaCta && (
                <p className="text-small text-white/80 sm:order-2 sm:basis-full sm:text-right">
                  {notaSenzaCta}
                </p>
              )}
              {azioni && <div className="sm:order-1">{azioni}</div>}
            </div>
          )
        }
      />
      {fatti.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {fatti.map((fatto, i) => (
              <Card key={i} className="flex items-start gap-3 p-4">
                {fatto.icon && <IconChip icon={fatto.icon} area={fatto.area ?? "bandi"} size="md" />}
                <dl className="flex min-w-0 flex-col gap-0.5">
                  <dt className="text-small text-ink-3">{fatto.etichetta}</dt>
                  <dd className="text-figure-sm text-ink">{fatto.valore}</dd>
                  {fatto.nota && <dd className="text-caption text-ink-3">{fatto.nota}</dd>}
                </dl>
              </Card>
            ))}
          </div>
          {azioneFatti && <div className="flex justify-end">{azioneFatti}</div>}
        </div>
      )}
    </div>
  );
}
