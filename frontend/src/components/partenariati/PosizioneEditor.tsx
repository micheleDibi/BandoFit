import { useId, useMemo, useState } from "react";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { CALL_COPY } from "../../lib/copy";
import type {
  DimensioneImpresa,
  PosizioneInput,
  RequisitoCall,
  TerritorioModalitaCall,
  TipoSoggettoPartenariato,
} from "../../types";
import { Button } from "../ui/Button";
import { TextField } from "../ui/Field";
import { SceltaCodici, SceltaDivisioni, SceltaPaesi, SceltaRadio, TestoLungo } from "./CampiCall";
import { leggiPercentuale, LIMITI_CALL, mostraDecimale } from "./callDati";
import { GruppoCheckbox, SceltaLookup } from "./PartnerProfileForm";

const DIMENSIONI: DimensioneImpresa[] = ["micro", "piccola", "media", "grande"];
const TERRITORI: TerritorioModalitaCall[] = ["qualsiasi", "sede_attuale", "sede_entro_erogazione"];

export const posizioneVuota = (): PosizioneInput => ({
  id: null,
  titolo: "",
  ruolo: "partner",
  tipi_soggetto: [],
  competenze: [],
  ateco_divisioni: [],
  regioni: [],
  territorio_modalita: "qualsiasi",
  paesi: [],
  dimensioni: [],
  quota_ipotizzata_pct: null,
  numero: 1,
  requisiti_ids: [],
  note: null,
});

/** Editor tipizzato di una posizione: lavora su una copia e la consegna solo
 *  con «Fatto», già valida per lo schema del server. */
export function PosizioneEditor({
  iniziale,
  requisiti,
  onFatto,
  onAnnulla,
}: {
  iniziale: PosizioneInput;
  /** Requisiti SALVATI della call (solo quelli hanno un id a cui rimandare). */
  requisiti: RequisitoCall[];
  onFatto: (p: PosizioneInput) => void;
  onAnnulla: () => void;
}) {
  const id = useId();
  const vocabolario = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const [p, setP] = useState<PosizioneInput>(iniziale);
  const [quota, setQuota] = useState(mostraDecimale(iniziale.quota_ipotizzata_pct));
  const [numero, setNumero] = useState(String(iniziale.numero));
  const [errori, setErrori] = useState<string[]>([]);

  const tipi = useMemo(
    () => vocabolario.data?.tipi_soggetto.map((t) => ({ codice: t.codice, etichetta: t.etichetta })),
    [vocabolario.data],
  );
  const competenze = useMemo(
    () =>
      vocabolario.data?.competenze.map((c) => ({
        codice: c.codice,
        etichetta: c.etichetta,
        gruppo: c.area,
      })),
    [vocabolario.data],
  );
  const requisitiSalvati = requisiti.filter((r): r is RequisitoCall & { id: string } => !!r.id);

  const set = <K extends keyof PosizioneInput>(k: K, v: PosizioneInput[K]) =>
    setP((prima) => ({ ...prima, [k]: v }));

  const conferma = () => {
    const problemi: string[] = [];
    const titolo = p.titolo.trim();
    if (titolo.length < LIMITI_CALL.titoloPosizioneMin || titolo.length > LIMITI_CALL.titoloPosizioneMax) {
      problemi.push(
        `Il titolo della posizione deve avere tra ${LIMITI_CALL.titoloPosizioneMin} e ${LIMITI_CALL.titoloPosizioneMax} caratteri.`,
      );
    }
    const n = Number(numero);
    if (!Number.isInteger(n) || n < 1 || n > LIMITI_CALL.numeroPosizioneMax) {
      problemi.push(`Il numero di partner va da 1 a ${LIMITI_CALL.numeroPosizioneMax}.`);
    }
    const q = leggiPercentuale(quota);
    if (!q.ok) problemi.push(q.errore);
    if (p.territorio_modalita !== "qualsiasi" && p.regioni.length === 0) {
      problemi.push("Indica le regioni in cui serve la sede, oppure scegli «Ovunque».");
    }
    if (problemi.length > 0 || !q.ok) {
      setErrori(problemi);
      return;
    }
    onFatto({
      ...p,
      titolo,
      numero: n,
      quota_ipotizzata_pct: q.valore,
      regioni: p.territorio_modalita === "qualsiasi" ? [] : p.regioni,
      note: p.note?.trim() ? p.note.trim() : null,
    });
  };

  return (
    <div className="space-y-4 rounded-lg border border-brand-200 bg-brand-50/30 p-4">
      <div className="grid gap-4 sm:grid-cols-[2fr_1fr_1fr]">
        <TextField
          label="Titolo della posizione"
          required
          maxLength={LIMITI_CALL.titoloPosizioneMax}
          value={p.titolo}
          onChange={(e) => set("titolo", e.target.value)}
          placeholder="Es. Organismo di ricerca per la prototipazione"
        />
        <TextField
          label="Quanti partner"
          type="number"
          min={1}
          max={LIMITI_CALL.numeroPosizioneMax}
          value={numero}
          onChange={(e) => setNumero(e.target.value)}
        />
        <TextField
          label="Quota ipotizzata (%)"
          inputMode="decimal"
          value={quota}
          onChange={(e) => setQuota(e.target.value)}
          helper="Per ciascun partner"
          placeholder="Es. 30"
        />
      </div>

      <SceltaRadio
        legenda="Ruolo"
        nome={`${id}-ruolo`}
        valore={p.ruolo}
        onChange={(r) => set("ruolo", r)}
        opzioni={[
          { valore: "partner", etichetta: "Partner" },
          { valore: "capofila", etichetta: "Capofila", nota: "Guida il progetto e tiene i rapporti con l'ente." },
        ]}
      />

      <SceltaCodici
        etichetta="Tipi di soggetto"
        aiuto={`Basta uno dei tipi indicati. Al massimo ${LIMITI_CALL.tipiPosizioneMax}.`}
        opzioni={tipi}
        scelti={p.tipi_soggetto}
        onChange={(v) => set("tipi_soggetto", v as TipoSoggettoPartenariato[])}
        massimo={LIMITI_CALL.tipiPosizioneMax}
      />
      <SceltaCodici
        etichetta="Competenze cercate"
        aiuto={`Al massimo ${LIMITI_CALL.competenzePosizioneMax}.`}
        opzioni={competenze}
        scelti={p.competenze}
        onChange={(v) => set("competenze", v)}
        massimo={LIMITI_CALL.competenzePosizioneMax}
      />
      <SceltaDivisioni
        scelte={p.ateco_divisioni}
        onChange={(v) => set("ateco_divisioni", v)}
        massimo={LIMITI_CALL.atecoPosizioneMax}
      />

      <div className="space-y-3">
        <div className="space-y-1.5">
          <label htmlFor={`${id}-territorio`} className="block text-sm font-medium text-slate-700">
            Dove deve avere sede
          </label>
          <select
            id={`${id}-territorio`}
            value={p.territorio_modalita}
            onChange={(e) => set("territorio_modalita", e.target.value as TerritorioModalitaCall)}
            className="h-10 w-full max-w-md cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
          >
            {TERRITORI.map((t) => (
              <option key={t} value={t}>
                {CALL_COPY.territorio[t]}
              </option>
            ))}
          </select>
        </div>
        {p.territorio_modalita !== "qualsiasi" && (
          <SceltaLookup
            etichetta="Regioni"
            opzioni={lookups?.regioni}
            scelti={p.regioni}
            onChange={(v) => set("regioni", v)}
            massimo={LIMITI_CALL.regioniMax}
          />
        )}
      </div>

      <SceltaPaesi
        etichetta="Paesi (per i bandi europei)"
        scelti={p.paesi}
        onChange={(v) => set("paesi", v)}
        massimo={LIMITI_CALL.paesiMax}
      />

      <GruppoCheckbox
        legenda="Dimensioni ammesse"
        nota="Nessuna scelta = qualsiasi dimensione."
        opzioni={DIMENSIONI.map((d) => ({ codice: d, etichetta: CALL_COPY.dimensioni[d] }))}
        scelti={p.dimensioni}
        onToggle={(d) =>
          set(
            "dimensioni",
            DIMENSIONI.filter((x) => (x === d ? !p.dimensioni.includes(d) : p.dimensioni.includes(x))),
          )
        }
      />

      {requisitiSalvati.length > 0 ? (
        <GruppoCheckbox
          legenda="Requisiti che questa posizione copre"
          nota="I requisiti salvati nel passo «Requisiti»."
          colonne={1}
          massimo={LIMITI_CALL.requisitiPosizioneMax}
          opzioni={requisitiSalvati.map((r) => ({
            codice: r.id,
            etichetta: `${r.etichetta ? `${r.etichetta} — ` : ""}${r.testo}`,
          }))}
          scelti={p.requisiti_ids}
          onToggle={(rid) =>
            set(
              "requisiti_ids",
              p.requisiti_ids.includes(rid)
                ? p.requisiti_ids.filter((x) => x !== rid)
                : [...p.requisiti_ids, rid],
            )
          }
        />
      ) : (
        <p className="text-xs text-slate-500">
          Quando salvi i requisiti puoi collegarli alle posizioni che li coprono.
        </p>
      )}

      <TestoLungo
        etichetta="Note (facoltative)"
        aiuto="Visibili alle altre aziende: niente contatti né dati che fanno riconoscere l'azienda."
        valore={p.note ?? ""}
        onChange={(v) => set("note", v || null)}
        massimo={LIMITI_CALL.notePosizioneMax}
        righe={3}
      />

      {errori.length > 0 && (
        <ul className="list-disc space-y-0.5 rounded-lg bg-red-50 py-2 pl-8 pr-3 text-sm text-red-700" role="alert">
          {errori.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onAnnulla}>
          Annulla
        </Button>
        <Button onClick={conferma}>Fatto</Button>
      </div>
    </div>
  );
}
