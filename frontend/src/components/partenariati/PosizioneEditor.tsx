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
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { SelectField, TextField } from "../ui/Field";
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

/** Editor tipizzato di una posizione, in linea nel passo: lavora su una copia
 *  e la consegna solo con «Conferma la posizione», già valida per lo schema
 *  del server. Il pulsante pieno del passo resta quello della barra. */
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
  const nuova = iniziale.id === null && !iniziale.titolo;

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
    <div className="flex flex-col gap-6">
      <h4 className="font-sans text-title-group text-ink">{nuova ? "Nuova posizione" : "Modifica la posizione"}</h4>
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

      <div className="grid gap-6 sm:grid-cols-2">
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
        <GruppoCheckbox
          legenda="Dimensioni ammesse"
          nota="Se non scegli nulla, vale qualsiasi dimensione."
          opzioni={DIMENSIONI.map((d) => ({ codice: d, etichetta: CALL_COPY.dimensioni[d] }))}
          scelti={p.dimensioni}
          onToggle={(d) =>
            set(
              "dimensioni",
              DIMENSIONI.filter((x) => (x === d ? !p.dimensioni.includes(d) : p.dimensioni.includes(x))),
            )
          }
        />
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
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
      </div>

      <div className="grid gap-6 sm:grid-cols-2">
        <SelectField
          id={`${id}-territorio`}
          label="Dove deve avere sede"
          value={p.territorio_modalita}
          onChange={(e) => set("territorio_modalita", e.target.value as TerritorioModalitaCall)}
        >
          {TERRITORI.map((t) => (
            <option key={t} value={t}>
              {CALL_COPY.territorio[t]}
            </option>
          ))}
        </SelectField>
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

      <div className="grid gap-6 sm:grid-cols-2">
        <SceltaDivisioni
          scelte={p.ateco_divisioni}
          onChange={(v) => set("ateco_divisioni", v)}
          massimo={LIMITI_CALL.atecoPosizioneMax}
        />
        <SceltaPaesi
          etichetta="Paesi (per i bandi europei)"
          scelti={p.paesi}
          onChange={(v) => set("paesi", v)}
          massimo={LIMITI_CALL.paesiMax}
        />
      </div>

      {requisitiSalvati.length > 0 ? (
        <GruppoCheckbox
          legenda="Requisiti che questa posizione copre"
          nota="I requisiti salvati nel passo «Requisiti»."
          colonne={1}
          massimo={LIMITI_CALL.requisitiPosizioneMax}
          opzioni={requisitiSalvati.map((r) => ({
            codice: r.id,
            etichetta: `${r.etichetta ? `${r.etichetta}: ` : ""}${r.testo}`,
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
        <p className="text-small text-ink-3">
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
        <Alert tono="errore">
          <ul className="list-disc pl-5">
            {errori.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </Alert>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="ghost" onClick={onAnnulla}>
          Annulla
        </Button>
        <Button variant="secondary" onClick={conferma}>
          Conferma la posizione
        </Button>
      </div>
    </div>
  );
}
