import { Download, Info } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useFunzioni } from "../../../hooks/useFunzioni";
import { BILANCI_COPY } from "../../../lib/copy";
import { formatSlotOra } from "../../../lib/format";
import type { BilanciOut, MotivoBilanci } from "../../../types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/states";

/** Vero quando l'istante è passato; si aggiorna da solo allo scadere, così il
 *  bottone si riabilita senza ricaricare la pagina. null = nessuna attesa. */
function useIstantePassato(iso: string | null): boolean {
  const istante = iso ? Date.parse(iso) : Number.NaN;
  const [adesso, setAdesso] = useState(() => Date.now());
  const passato = !Number.isFinite(istante) || adesso >= istante;
  useEffect(() => {
    if (passato) return;
    const attesa = Math.max(istante - Date.now(), 0) + 250;
    const id = window.setTimeout(() => setAdesso(Date.now()), attesa);
    return () => window.clearTimeout(id);
  }, [istante, passato]);
  return passato;
}

/** Il recupero non ha senso per chi non deposita il bilancio (il server
 *  risponderebbe 409 bilanci_non_previsti). */
const recuperabile = (motivo: MotivoBilanci | null) => motivo !== "forma_senza_bilancio";

function RecuperaBilanciButton({
  recuperabileDa,
  onRecupera,
  inCorso,
  variant,
  allinea,
}: {
  recuperabileDa: string | null;
  onRecupera: () => void;
  inCorso: boolean;
  variant: "primary" | "secondary";
  allinea: "center" | "start";
}) {
  const pronto = useIstantePassato(recuperabileDa);
  const notaId = useId();
  const inAttesa = !pronto && !!recuperabileDa;
  return (
    <div
      className={
        allinea === "center"
          ? "flex flex-col items-center gap-1.5"
          : "flex shrink-0 flex-col items-start gap-1.5 sm:items-end"
      }
    >
      {/* Nessun prezzo: il recupero lo paga la piattaforma, non l'utente. */}
      <Button
        variant={variant}
        onClick={onRecupera}
        loading={inCorso}
        disabled={inAttesa}
        aria-describedby={inAttesa ? notaId : undefined}
      >
        {!inCorso && <Download className="size-4" aria-hidden />}
        {inCorso ? "Recupero in corso…" : BILANCI_COPY.recupera}
      </Button>
      {inAttesa && recuperabileDa && (
        <p id={notaId} className="text-small text-ink-3">
          Potrai riprovare dalle {formatSlotOra(recuperabileDa)}.
        </p>
      )}
    </div>
  );
}

interface BilanciNonDisponibiliProps {
  data: BilanciOut;
  onRecupera: () => void;
  recuperoInCorso: boolean;
}

/** Cosa manca e come recuperarlo. Due forme:
 *  - nessun esercizio (`mai_richiesti` / `non_disponibili`): stato vuoto con il
 *    motivo e la CTA «Recupera i bilanci»;
 *  - esercizi presenti ma storico non recuperato: banner sopra la tabella.
 *  La CTA c'è solo per il titolare (`editable`) e resta disabilitata fino a
 *  `recuperabile_da`. L'esito del recupero lo annuncia la sezione, che resta
 *  montata anche quando questo componente sparisce. A storico spento nessun
 *  invito al recupero: niente banner, e lo stato vuoto dice solo un motivo
 *  certo (la forma giuridica) o una frase neutra. */
export function BilanciNonDisponibili({
  data,
  onRecupera,
  recuperoInCorso,
}: BilanciNonDisponibiliProps) {
  const { bilanciStoricoAttivo } = useFunzioni();
  const puoRiprovare = recuperabile(data.motivo);
  const conCta = bilanciStoricoAttivo && data.editable && puoRiprovare;

  if (data.stato !== "disponibili" || data.esercizi.length === 0) {
    if (!bilanciStoricoAttivo) {
      return (
        <EmptyState
          title="Bilanci non disponibili"
          description={
            data.motivo === "forma_senza_bilancio"
              ? BILANCI_COPY.motivi.forma_senza_bilancio
              : BILANCI_COPY.senzaBilanci
          }
        />
      );
    }
    const titolo =
      data.stato === "mai_richiesti" ? "Bilanci non ancora recuperati" : "Bilanci non disponibili";
    let descrizione: string;
    if (data.stato === "mai_richiesti") {
      descrizione = data.editable
        ? "Recupera i bilanci depositati al Registro Imprese negli ultimi anni: fatturato, utile, patrimonio e dipendenti, anno per anno."
        : "Il titolare non ha ancora recuperato i bilanci dell'azienda.";
    } else {
      descrizione = data.motivo ? BILANCI_COPY.motivi[data.motivo] : BILANCI_COPY.senzaBilanci;
      if (!data.editable && puoRiprovare) descrizione += " Solo il titolare può riprovare.";
      else if (data.editable && data.motivo === "piva_diversa") {
        descrizione += ` ${BILANCI_COPY.correggiPiva}`;
      }
    }
    return (
      <EmptyState
        title={titolo}
        description={descrizione}
        action={
          conCta ? (
            <RecuperaBilanciButton
              recuperabileDa={data.recuperabile_da}
              onRecupera={onRecupera}
              inCorso={recuperoInCorso}
              variant="primary"
              allinea="center"
            />
          ) : undefined
        }
      />
    );
  }

  if (data.storico_esito === "ok" || !bilanciStoricoAttivo) return null;

  const motivo: MotivoBilanci = data.motivo ?? "non_richiesto";
  const titolo =
    data.esercizi.length === 1
      ? "Solo l'ultimo esercizio"
      : "Lo storico degli anni passati è incompleto";
  // Qui una parte dei bilanci c'è (dalla visura): «nessun bilancio
  // depositato» contraddirebbe la tabella subito sotto.
  const spiegazione =
    motivo === "nessun_bilancio" ? BILANCI_COPY.nessunAltroBilancio : BILANCI_COPY.motivi[motivo];
  // Con `piva_diversa` il server rifiuta il recupero finché la P.IVA dei dati
  // aziendali non coincide con quella importata: prima va corretta.
  const invito = !puoRiprovare
    ? ""
    : !data.editable
      ? " Solo il titolare può recuperare lo storico."
      : motivo === "piva_diversa"
        ? ` ${BILANCI_COPY.correggiPiva}`
        : " Recupera lo storico per vedere anche gli anni precedenti.";

  return (
    <div className="flex flex-col gap-3 rounded-control border border-accent-line bg-accent-soft px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-2.5">
        <Info className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />
        <div>
          <p className="text-body font-medium text-ink">{titolo}</p>
          <p className="mt-0.5 text-body text-ink-2">
            {spiegazione}
            {invito}
          </p>
        </div>
      </div>
      {conCta && (
        <RecuperaBilanciButton
          recuperabileDa={data.recuperabile_da}
          onRecupera={onRecupera}
          inCorso={recuperoInCorso}
          variant="secondary"
          allinea="start"
        />
      )}
    </div>
  );
}
