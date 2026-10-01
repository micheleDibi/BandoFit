import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useConsorzio } from "../../hooks/useConsorzio";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import type { MembroConsorzio } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { DefinitionList } from "../ui/Facts";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { BudgetConsorzio } from "./BudgetConsorzio";
import { mostraDecimale } from "./callDati";
import { ChecklistDocumenti } from "./ChecklistDocumenti";
import { EsternoDialog } from "./EsternoDialog";
import { MatriceCopertura } from "./MatriceCopertura";
import { MembroRiga, nomeMembro, RUOLI_MEMBRO, type PosizioneOpzione } from "./MembroRiga";
import { ValidatoreChecklist } from "./ValidatoreChecklist";

const SOMMARIO =
  "inline-flex cursor-pointer items-center gap-1 rounded-mark text-small font-medium text-accent-hover hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden";

/** Somma delle quote indicate dai membri non usciti (centesimi interi per non
 *  sommare errori di virgola mobile) e quanti non l'hanno ancora. I partner
 *  associati non ricevono budget: non contano, come nella verifica. */
function quote(membri: MembroConsorzio[]) {
  let centesimi = 0;
  let mancanti = 0;
  for (const m of membri) {
    if (m.ruolo === "associated_partner") continue;
    const n = m.quota_percentuale ? Number(m.quota_percentuale) : NaN;
    if (Number.isFinite(n)) centesimi += Math.round(n * 100);
    else mancanti += 1;
  }
  return { somma: centesimi / 100, mancanti };
}

function LegendaRuoli() {
  return (
    <details className="group">
      <summary className={SOMMARIO}>
        Cosa vogliono dire i ruoli
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
      </summary>
      <div className="mt-3">
        <DefinitionList
          items={RUOLI_MEMBRO.map((r) => ({
            etichetta: CONSORZIO_COPY.ruoli[r],
            valore: CONSORZIO_COPY.ruoliSpiegazione[r],
          }))}
        />
      </div>
    </details>
  );
}

/** Scheda «Consorzio» della call (WP8), per l'azienda che l'ha creata e per le
 *  aziende accettate: chi c'è nel consorzio (in piattaforma ed esterni) con
 *  ruolo, posizione, quota e conferma; la verifica deterministica delle
 *  regole del bando; la copertura dei requisiti; budget e documenti. Cosa
 *  vede ciascuno (i propri numeri, degli altri solo fasce ed esiti, nomi o
 *  pseudonimi) e cosa può fare lo decide il server: qui non si ricalcola
 *  nulla. */
export function ConsorzioTab({
  callId,
  posizioni = [],
}: {
  callId: string;
  /** Posizioni della call (solo per chi l'ha creata: le assegna ai membri). */
  posizioni?: PosizioneOpzione[];
}) {
  const consorzio = useConsorzio(callId);
  const [esterno, setEsterno] = useState<{ membro: MembroConsorzio | null; aperto: boolean }>({
    membro: null,
    aperto: false,
  });
  // Esiti delle azioni per i lettori di schermo: la regione è sempre montata
  // e si riempie dopo, così viene letta.
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  useEffect(() => {
    if (!annuncio) return;
    const timer = window.setTimeout(() => setAnnuncio(null), 8000);
    return () => window.clearTimeout(timer);
  }, [annuncio]);

  const dati = consorzio.data;
  const nomi = useMemo(
    () => new Map((dati?.membri ?? []).map((m) => [m.id, nomeMembro(m)] as const)),
    [dati?.membri],
  );

  const regioneAnnunci = <div aria-live="polite">{annuncio && <Alert tono="ok">{annuncio}</Alert>}</div>;

  if (consorzio.isPending) {
    return (
      <div className="flex flex-col gap-4" aria-hidden>
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (consorzio.isError) {
    return apiErrorCode(consorzio.error) === "not_found" ? (
      <EmptyState
        title="Consorzio non disponibile"
        description="Il consorzio lo vedono l'azienda che ha creato la call e le aziende accettate, da quando la call è stata pubblicata."
      />
    ) : (
      <ErrorState
        message={apiErrorMessage(consorzio.error, "Impossibile caricare il consorzio.")}
        onRetry={() => void consorzio.refetch()}
      />
    );
  }

  const attivi = consorzio.data.membri.filter((m) => m.stato !== "uscito");
  const usciti = consorzio.data.membri.filter((m) => m.stato === "uscito");
  const { somma, mancanti } = quote(attivi);
  // Esterni e documenti: solo il titolare del creatore e solo se la call lo
  // ammette ancora (lo dice il server, con gli stati delle RPC).
  const puoAggiungere = consorzio.data.modificabile;
  const pieno = attivi.length >= consorzio.data.membri_max;
  const riga = (m: MembroConsorzio) => (
    <MembroRiga
      key={m.id}
      membro={m}
      callId={callId}
      posizioni={posizioni}
      onModificaEsterno={(membro) => setEsterno({ membro, aperto: true })}
      onAnnuncio={setAnnuncio}
    />
  );

  return (
    <div className="flex flex-col gap-8">
      {regioneAnnunci}
      {!consorzio.data.editable && <p className="text-small text-ink-3">{CONSORZIO_COPY.soloTitolare}</p>}

      <Section>
        <SectionHeader
          titolo={`Chi c'è nel consorzio (${attivi.length === 1 ? "1 membro" : `${attivi.length} membri`})`}
          azione={
            puoAggiungere ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setEsterno({ membro: null, aperto: true })}
                disabled={pieno}
              >
                Aggiungi un membro esterno
              </Button>
            ) : undefined
          }
        />
        <p className="text-body text-ink-2">
          {consorzio.data.sei_creatore
            ? "La tua azienda, le aziende che hai accettato e i membri esterni che aggiungi tu. Decidi ruolo, posizione e quota di ciascuno: ogni azienda conferma la propria partecipazione, gli esterni li confermi tu."
            : "Chi fa parte del consorzio di questa call. Delle altre aziende vedi solo i dati anonimi. Conferma la partecipazione della tua azienda quando ruolo e quota ti vanno bene."}
        </p>
        {puoAggiungere && pieno && (
          <p className="text-small text-ink-3">
            Il consorzio ha già {consorzio.data.membri_max} membri: è il massimo.
          </p>
        )}
        <p className="text-body text-ink">
          Quote indicate: <span className="font-semibold tabular-nums">{mostraDecimale(somma) || "0"}%</span> su
          100%
          {mancanti > 0 && (
            <span className="text-ink-3">
              {" "}
              ({mancanti === 1 ? "manca la quota di 1 membro" : `mancano le quote di ${mancanti} membri`})
            </span>
          )}
        </p>
        <ul className="flex flex-col border-t border-line">{attivi.map(riga)}</ul>
        {usciti.length > 0 && (
          <details className="group">
            <summary className={SOMMARIO}>
              Usciti dal consorzio ({usciti.length})
              <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
            </summary>
            <ul className="mt-2 flex flex-col border-t border-line">{usciti.map(riga)}</ul>
          </details>
        )}
        <LegendaRuoli />
      </Section>

      <ValidatoreChecklist validazione={consorzio.data.validazione} nomi={nomi} />
      <MatriceCopertura matrice={consorzio.data.matrice} nomi={nomi} />
      <BudgetConsorzio
        callId={callId}
        budget={consorzio.data.budget}
        seiCreatore={consorzio.data.sei_creatore}
        onAnnuncio={setAnnuncio}
      />
      <ChecklistDocumenti
        callId={callId}
        documenti={consorzio.data.documenti}
        forma={consorzio.data.forma}
        modificabile={consorzio.data.modificabile}
        onAnnuncio={setAnnuncio}
      />

      {consorzio.data.editable && consorzio.data.sei_creatore && (
        <EsternoDialog
          open={esterno.aperto}
          onClose={() => setEsterno((prima) => ({ ...prima, aperto: false }))}
          callId={callId}
          membro={esterno.membro}
          posizioni={posizioni}
          onSalvato={setAnnuncio}
        />
      )}
    </div>
  );
}
