import { CalendarDays, FileText, ListTodo, Sparkles } from "lucide-react";
import type { FacetKey } from "../../hooks/useBandiFilters";
import { useEntitlements } from "../../hooks/useEntitlements";
import { Card } from "../ui/Card";
import { statoScadenza, tempoRelativo } from "../ui/Due";
import { KpiCard } from "../ui/KpiCard";
import { ProgressRing } from "../ui/ProgressRing";
import { Skeleton } from "../ui/states";
import {
  linkBandiAdatti,
  PAGINA_SALVATI,
  useBandiAdatti,
  usePresetPerTe,
  useScadenzeSalvate,
  useVociDaFare,
} from "./datiHome";

const NON_DISPONIBILE = "Dato non disponibile";

/** La forma di un indicatore mentre arrivano i dati. */
function KpiInCaricamento() {
  return (
    <Card className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-4 w-28" />
      <Skeleton className="h-8 w-16" />
      <Skeleton className="h-3 w-32" />
    </Card>
  );
}

/** Il conteggio: montato solo con un preset, come le righe del blocco «Nuovi
 *  bandi adatti» (stessa query, stessa chiave: nessuna chiamata in più). */
function ConteggioBandiAdatti({ preset }: { preset: Record<FacetKey, number[]> }) {
  const { data, isPending, isError } = useBandiAdatti(preset);
  if (isPending) return <KpiInCaricamento />;
  const disponibile = !isError && !!data;
  return (
    <KpiCard
      etichetta="Bandi adatti alla tua azienda"
      valore={disponibile ? data.total : "—"}
      nota={disponibile ? "Aperti o in apertura" : NON_DISPONIBILE}
      icon={FileText}
      area="bandi"
      to={linkBandiAdatti(preset)}
    />
  );
}

function KpiBandiAdatti() {
  const { preset, haPreset, inAttesa } = usePresetPerTe();
  if (inAttesa) return <KpiInCaricamento />;
  if (!haPreset) {
    return (
      <KpiCard
        etichetta="Bandi adatti alla tua azienda"
        valore="—"
        nota="Scegli gli interessi sui bandi"
        icon={FileText}
        area="bandi"
        to="/app/preferenze"
      />
    );
  }
  return <ConteggioBandiAdatti preset={preset} />;
}

/** Bandi salvati che scadono entro 7 giorni, oggi compreso (la tessera corallo
 *  di `Due`, 0-7 giorni: è anche la prima barra del grafico di «Prossime
 *  scadenze»). Si contano solo i salvati della prima pagina: se sono di più,
 *  la nota lo dice. */
function KpiScadenze() {
  const { data, isPending, isError, righe } = useScadenzeSalvate();
  if (isPending) return <KpiInCaricamento />;
  const urgenti = righe.filter((item) => statoScadenza(item.bando.data_scadenza) === "urgente");
  const nota = isError
    ? NON_DISPONIBILE
    : data && data.total > PAGINA_SALVATI
      ? `Tra i ${PAGINA_SALVATI} salvati più di recente`
      : urgenti.length > 0
        ? `Il più vicino: ${tempoRelativo(urgenti[0].bando.data_scadenza)}`
        : "Tra i bandi salvati";
  return (
    <KpiCard
      etichetta="In scadenza entro 7 giorni"
      valore={isError ? "—" : urgenti.length}
      nota={nota}
      icon={CalendarDays}
      area="scadenze"
      to="/app/salvati"
    />
  );
}

/** AI-check ancora disponibili quest'anno (dell'azienda, per un membro: il suo
 *  budget personale lo spiega il pannello «AI-check» qui accanto). */
function KpiAiCheck() {
  const { data, isPending, isError } = useEntitlements();
  if (isPending) return <KpiInCaricamento />;
  const quota = data?.ai_checks;
  const etichetta = data && !data.editable ? "AI-check dell'azienda" : "AI-check disponibili";

  if (isError || !quota) {
    return (
      <KpiCard
        etichetta={etichetta}
        valore="—"
        nota={NON_DISPONIBILE}
        icon={Sparkles}
        area="aicheck"
        to="/app/ai-check"
      />
    );
  }
  if (quota.effettivo <= 0) {
    return (
      <KpiCard
        etichetta={etichetta}
        valore={0}
        nota="Non inclusi nel piano"
        icon={Sparkles}
        area="aicheck"
        to="/app/ai-check"
      />
    );
  }
  const percento = Math.round((Math.max(0, quota.residuo) / quota.effettivo) * 100);
  return (
    <KpiCard
      etichetta={etichetta}
      valore={quota.residuo}
      icon={Sparkles}
      area="aicheck"
      to="/app/ai-check"
    >
      <span className="flex items-center gap-3">
        {/* L'anello ripete il numero per l'occhio: lo screen reader legge già
            valore e «su N quest'anno». */}
        <span aria-hidden className="inline-flex">
          <ProgressRing
            value={quota.residuo}
            max={quota.effettivo}
            size={40}
            tono="aicheck"
            label={`${quota.residuo} AI-check su ${quota.effettivo}`}
          >
            {percento}%
          </ProgressRing>
        </span>
        <span className="text-small text-ink-3">su {quota.effettivo} quest'anno</span>
      </span>
    </KpiCard>
  );
}

/** Quante cose aspettano una decisione (le voci del pannello «Da fare»). */
function KpiDaFare() {
  const { voci, inCaricamento, errore } = useVociDaFare();
  if (inCaricamento) return <KpiInCaricamento />;
  const totale = voci.reduce((somma, voce) => somma + voce.numero, 0);
  const dove = [...new Set(voci.map((voce) => voce.dove))].join(", ");
  // Con un errore il numero può essere parziale: la nota lo dice sempre (come
  // il pannello «Da fare»); senza voci il numero non è certo e non si mostra.
  return (
    <KpiCard
      etichetta="Da fare"
      valore={errore && voci.length === 0 ? "—" : totale}
      nota={
        errore
          ? "Alcune voci non sono aggiornate"
          : voci.length === 0
            ? "Niente da fare per ora"
            : dove
      }
      icon={ListTodo}
      area="home"
    />
  );
}

/** La riga degli indicatori della Home: bandi adatti, scadenze vicine,
 *  AI-check disponibili, cose da fare. Solo dati che i blocchi sotto caricano
 *  già (stessi hook, stesse chiavi): nessuna chiamata in più. Ogni indicatore
 *  ha il suo caricamento: uno lento non ferma gli altri. */
export function IndicatoriHome() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiBandiAdatti />
      <KpiScadenze />
      <KpiAiCheck />
      <KpiDaFare />
    </div>
  );
}
