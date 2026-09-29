import { AlertTriangle, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import {
  useAggiornaCall,
  useProponiPosizioni,
  useSalvaPosizioni,
} from "../../hooks/useCallPartenariato";
import { apiErrorMessage } from "../../lib/api";
import { CALL_COPY } from "../../lib/copy";
import { nomePaese } from "../../lib/paesi";
import type { PosizioneInput, PosizioneProposta } from "../../types";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { BarraPasso } from "./CallStepper";
import { firma, LIMITI_CALL, percentuale, posizioneInput, sommaQuote } from "./callDati";
import { JobAiStato } from "./JobAiStato";
import { avanzamento, vuoto, type PassoProps } from "./passoComune";
import { posizioneVuota, PosizioneEditor } from "./PosizioneEditor";
import { useNomiCall } from "./useNomiCall";

interface PosizioneLocale extends PosizioneInput {
  chiave: string;
}

let contatore = 0;
const nuovaChiave = () => `p${++contatore}`;
const locale = (p: PosizioneInput): PosizioneLocale => ({ ...p, chiave: p.id ?? nuovaChiave() });
const senzaChiave = ({ chiave: _chiave, ...p }: PosizioneLocale): PosizioneInput => p;

/** Riassunto di una posizione in parole (tipi, competenze, territorio…). */
function DettagliPosizione({
  p,
  etichette,
}: {
  p: PosizioneInput | PosizioneProposta;
  etichette: Map<string, string>;
}) {
  const nomi = useNomiCall();
  const righe: string[] = [];
  if (p.tipi_soggetto.length) righe.push(`Tipo: ${p.tipi_soggetto.map(nomi.tipo).join(", ")}`);
  if (p.competenze.length) righe.push(`Competenze: ${p.competenze.map(nomi.competenza).join(", ")}`);
  if (p.ateco_divisioni.length) righe.push(`ATECO: ${p.ateco_divisioni.join(", ")}`);
  if (p.territorio_modalita !== "qualsiasi") {
    righe.push(`${CALL_COPY.territorio[p.territorio_modalita]}: ${p.regioni.map(nomi.regione).join(", ")}`);
  }
  if (p.paesi.length) righe.push(`Paesi: ${p.paesi.map(nomePaese).join(", ")}`);
  if (p.dimensioni.length) {
    righe.push(`Dimensione: ${p.dimensioni.map((d) => CALL_COPY.dimensioni[d] ?? d).join(", ")}`);
  }
  const requisiti = p.requisiti_ids.map((id) => etichette.get(id)).filter(Boolean);
  if (requisiti.length) righe.push(`Copre i requisiti: ${requisiti.join(", ")}`);
  if (righe.length === 0 && !p.note) return null;
  return (
    <div className="space-y-0.5 text-xs text-slate-500">
      {righe.map((r) => (
        <p key={r}>{r}</p>
      ))}
      {p.note && <p className="whitespace-pre-line text-slate-600">{p.note}</p>}
    </div>
  );
}

function IntestazionePosizione({ p }: { p: PosizioneInput | PosizioneProposta }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-sm font-medium text-slate-800">{p.titolo || "Nuova posizione"}</p>
      <Badge tone={p.ruolo === "capofila" ? "brand" : "slate"}>
        {p.ruolo === "capofila" ? "Capofila" : "Partner"}
      </Badge>
      {p.numero > 1 && <Badge tone="slate">{p.numero} partner</Badge>}
      {p.quota_ipotizzata_pct && <Badge tone="slate">Quota {percentuale(p.quota_ipotizzata_pct)}</Badge>}
    </div>
  );
}

/** Passo 4: le posizioni cercate. L'AI può proporle (job asincrono), ma nulla
 *  si salva finché non lo decidi tu. */
export function PassoPosizioni({ call, onAvanti, onIndietro, onDirty }: PassoProps) {
  const salvaPosizioni = useSalvaPosizioni(call.id);
  const aggiorna = useAggiornaCall(call.id);
  const proponi = useProponiPosizioni(call.id);
  const bozza = call.stato === "bozza";

  const salvate = call.posizioni.map(posizioneInput);
  const [posizioni, setPosizioni] = useState<PosizioneLocale[]>(() => salvate.map(locale));
  const [modifica, setModifica] = useState<{ chiave: string; nuova: boolean } | null>(null);
  const [errori, setErrori] = useState<string[]>([]);
  const [annuncio, setAnnuncio] = useState<string | null>(null);

  const correnti = posizioni.map(senzaChiave);
  const dirty = firma(correnti) !== firma(salvate);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const etichette = new Map(
    call.gap.requisiti.flatMap((r) => (r.id ? [[r.id, r.etichetta ?? r.testo] as [string, string]] : [])),
  );
  const job = call.ai_posizioni;
  const proposta = job.stato === "pronta" ? job.proposta : null;

  // Controlli deterministici, gli stessi avvisi che dà il server alla proposta.
  const totale = sommaQuote(call.quota_creatore_pct, correnti);
  const soggetti = 1 + correnti.reduce((n, p) => n + p.numero, 0);
  const regole = call.regole_partenariato;
  const avvisi: string[] = [];
  if (totale !== null && totale > 100) {
    avvisi.push(`Le quote sommate (la tua e quelle delle posizioni) fanno ${totale}%: superano il 100%.`);
  }
  if (regole?.partner_min && soggetti < regole.partner_min.valore && correnti.length > 0) {
    avvisi.push(`Il bando chiede almeno ${regole.partner_min.valore} soggetti: con queste posizioni sareste ${soggetti}.`);
  }
  if (regole?.partner_max && soggetti > regole.partner_max.valore) {
    avvisi.push(`Il bando ammette al massimo ${regole.partner_max.valore} soggetti: con queste posizioni sareste ${soggetti}.`);
  }
  if (call.ruolo_creatore === "cerco_capofila" && !correnti.some((p) => p.ruolo === "capofila")) {
    avvisi.push("Cerchi un capofila: aggiungi una posizione con il ruolo «Capofila».");
  }

  const usaProposta = (proposte: PosizioneProposta[], sostituisci: boolean) => {
    const nuove = proposte.map((p) => locale(posizioneInput(p)));
    setPosizioni((lista) => (sostituisci ? nuove : [...lista, ...nuove]).slice(0, LIMITI_CALL.posizioniMax));
    setModifica(null);
    setAnnuncio(
      sostituisci
        ? "Abbiamo messo le posizioni proposte al posto delle tue: controllale e salva."
        : "Abbiamo aggiunto la posizione proposta: controllala e salva.",
    );
  };

  const salva = async () => {
    setErrori([]);
    if (modifica) {
      setErrori(["Completa o annulla la posizione che stai modificando."]);
      return;
    }
    try {
      if (dirty) await salvaPosizioni.mutateAsync(correnti);
      const passo = avanzamento(call, 5);
      if (!vuoto(passo)) await aggiorna.mutateAsync(passo);
      onAvanti();
    } catch {
      // mostrato nella barra
    }
  };

  const erroreServer = salvaPosizioni.isError
    ? apiErrorMessage(salvaPosizioni.error)
    : aggiorna.isError
      ? apiErrorMessage(aggiorna.error)
      : null;

  return (
    <div className="space-y-4">
      {bozza && (
        <JobAiStato
          job={job}
          descrizione="L'AI può proporti le posizioni da cercare, a partire dalle regole e dai requisiti che hai confermato."
          etichettaAvvia="Proponi con l'AI"
          etichettaRigenera="Nuova proposta"
          onAvvia={() => proponi.mutate()}
          avvioInCorso={proponi.isPending}
          erroreAvvio={proponi.isError ? apiErrorMessage(proponi.error) : null}
        >
          {proposta && (
            <div className="mt-3 space-y-3">
              {proposta.avvisi.length > 0 && (
                <ul className="list-disc space-y-0.5 rounded-lg bg-amber-50 py-2 pl-8 pr-3 text-sm text-amber-900">
                  {proposta.avvisi.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              )}
              {proposta.posizioni.length === 0 ? (
                <p className="text-sm text-slate-500">L'AI non ha proposto posizioni.</p>
              ) : (
                <ul className="space-y-2">
                  {proposta.posizioni.map((p, i) => (
                    <li key={`${p.titolo}-${i}`} className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="min-w-0 flex-1 space-y-1">
                          <IntestazionePosizione p={p} />
                          <DettagliPosizione p={p} etichette={etichette} />
                          {p.motivazione && (
                            <p className="text-xs italic text-slate-500">Perché: {p.motivazione}</p>
                          )}
                        </div>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => usaProposta([p], false)}
                          disabled={posizioni.length >= LIMITI_CALL.posizioniMax}
                        >
                          Aggiungi
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {proposta.posizioni.length > 0 && (
                <Button size="sm" onClick={() => usaProposta(proposta.posizioni, true)}>
                  Usa tutte al posto delle mie
                </Button>
              )}
            </div>
          )}
        </JobAiStato>
      )}

      <Card className="space-y-4 p-5">
        <div>
          <h3 className="text-sm font-semibold text-slate-800">Le posizioni che cerchi</h3>
          <p className="text-xs text-slate-500">
            Per ogni posizione indica chi cerchi: le aziende la vedono nella call e i suggerimenti
            partono da qui.
          </p>
        </div>

        <div role="status" aria-live="polite">
          {annuncio && <p className="text-sm text-emerald-700">{annuncio}</p>}
        </div>

        {posizioni.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nessuna posizione ancora: aggiungine almeno una per pubblicare la call.
          </p>
        ) : (
          <ul className="space-y-2">
            {posizioni.map((p) => (
              <li key={p.chiave} className="rounded-lg border border-slate-200 bg-white px-3.5 py-3">
                {modifica?.chiave === p.chiave ? (
                  <PosizioneEditor
                    iniziale={senzaChiave(p)}
                    requisiti={call.gap.requisiti}
                    onFatto={(nuova) => {
                      setPosizioni((lista) =>
                        lista.map((x) => (x.chiave === p.chiave ? { ...nuova, chiave: p.chiave } : x)),
                      );
                      setModifica(null);
                    }}
                    onAnnulla={() => {
                      if (modifica.nuova) setPosizioni((lista) => lista.filter((x) => x.chiave !== p.chiave));
                      setModifica(null);
                    }}
                  />
                ) : (
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 flex-1 space-y-1">
                      <IntestazionePosizione p={p} />
                      <DettagliPosizione p={p} etichette={etichette} />
                    </div>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setModifica({ chiave: p.chiave, nuova: false })}
                        disabled={!!modifica}
                        aria-label={`Modifica la posizione ${p.titolo}`}
                      >
                        <Pencil className="size-4" aria-hidden />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setPosizioni((lista) => lista.filter((x) => x.chiave !== p.chiave))}
                        disabled={!!modifica}
                        aria-label={`Rimuovi la posizione ${p.titolo}`}
                      >
                        <Trash2 className="size-4" aria-hidden />
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <Button
          variant="secondary"
          size="sm"
          disabled={!!modifica || posizioni.length >= LIMITI_CALL.posizioniMax}
          onClick={() => {
            const nuova = locale(posizioneVuota());
            setPosizioni((lista) => [...lista, nuova]);
            setModifica({ chiave: nuova.chiave, nuova: true });
          }}
        >
          <Plus className="size-4" aria-hidden />
          Aggiungi una posizione
        </Button>

        {avvisi.length > 0 && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <ul className="space-y-0.5">
              {avvisi.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        )}
        {totale !== null && (
          <p className="text-xs text-slate-500 tabular">Quote sommate (la tua e quelle cercate): {percentuale(totale)}</p>
        )}

        {errori.length > 0 && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {errori.join(" ")}
          </p>
        )}
        <BarraPasso
          onIndietro={onIndietro}
          onAvanti={() => void salva()}
          inCorso={salvaPosizioni.isPending || aggiorna.isPending}
          errore={erroreServer}
        />
      </Card>
    </div>
  );
}
