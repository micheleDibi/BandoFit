import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { nomePaese, paesiOrdinati } from "../../lib/paesi";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { SelectField, TextareaField, TextField } from "../ui/Field";
import { RadioGroup } from "../ui/RadioGroup";
import { TagSelect } from "../ui/TagSelect";
import { Skeleton } from "../ui/states";

/* Campi di scelta riusati dagli editor della call (criteri e posizioni):
 * voci scelte come `Chip` rimovibili + aggiunta, sul modello del profilo partner. */

function Chips({ voci, onRimuovi }: { voci: Array<{ chiave: string; testo: string }>; onRimuovi: (chiave: string) => void }) {
  if (voci.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((v) => (
        <li key={v.chiave}>
          <Chip onRemove={() => onRimuovi(v.chiave)} label={`Rimuovi ${v.testo}`}>
            {v.testo}
          </Chip>
        </li>
      ))}
    </ul>
  );
}

export interface OpzioneCodice {
  codice: string;
  etichetta: string;
  gruppo?: string;
}

/** Scelta multipla su un elenco di codici (tipi di soggetto, competenze) con
 *  ricerca: i codici diventano indici per la `TagSelect` esistente. */
export function SceltaCodici({
  etichetta,
  aiuto,
  opzioni,
  scelti,
  onChange,
  massimo,
}: {
  etichetta: string;
  aiuto?: string;
  opzioni: OpzioneCodice[] | undefined;
  scelti: string[];
  onChange: (scelti: string[]) => void;
  massimo?: number;
}) {
  const idAiuto = useId();
  const nomi = useMemo(() => new Map((opzioni ?? []).map((o) => [o.codice, o.etichetta])), [opzioni]);
  const pieno = massimo !== undefined && scelti.length >= massimo;
  const alterna = (codice: string) => {
    if (scelti.includes(codice)) onChange(scelti.filter((c) => c !== codice));
    else if (!pieno) onChange([...scelti, codice]);
  };
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-small font-medium text-ink">{etichetta}</p>
      <div className="max-w-md">
        {opzioni ? (
          <TagSelect
            label={etichetta}
            options={opzioni.map((o, i) => ({ id: i + 1, label: o.etichetta, sublabel: o.gruppo }))}
            values={opzioni.flatMap((o, i) => (scelti.includes(o.codice) ? [i + 1] : []))}
            onToggle={(id) => {
              const o = opzioni[id - 1];
              if (o) alterna(o.codice);
            }}
            placeholder={pieno ? `Massimo ${massimo}` : "Cerca e aggiungi…"}
          />
        ) : (
          <Skeleton className="h-10 w-full" />
        )}
      </div>
      <Chips
        voci={scelti.map((c) => ({ chiave: c, testo: nomi.get(c) ?? c }))}
        onRimuovi={alterna}
      />
      {aiuto && (
        <p id={idAiuto} className="text-small text-ink-3">
          {aiuto}
        </p>
      )}
    </div>
  );
}

/** Divisioni ATECO a due cifre (es. 62), scritte a mano. */
export function SceltaDivisioni({
  scelte,
  onChange,
  massimo,
}: {
  scelte: string[];
  onChange: (scelte: string[]) => void;
  massimo: number;
}) {
  const id = useId();
  const [bozza, setBozza] = useState("");
  const [avviso, setAvviso] = useState<string | null>(null);
  const pieno = scelte.length >= massimo;
  const aggiungi = () => {
    const nuove = bozza
      .split(/[\s,;]+/)
      .map((v) => v.trim())
      .filter(Boolean);
    if (nuove.length === 0) return;
    const errate = nuove.filter((v) => !/^\d{2}$/.test(v));
    if (errate.length > 0) {
      setAvviso("Le divisioni ATECO hanno due cifre (es. 62)");
      return;
    }
    const unione = [...scelte];
    for (const v of nuove) if (!unione.includes(v)) unione.push(v);
    if (unione.length > massimo) {
      setAvviso(`Puoi indicare al massimo ${massimo} divisioni`);
      return;
    }
    onChange(unione);
    setBozza("");
    setAvviso(null);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      aggiungi();
    }
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex max-w-md items-end gap-2">
        <div className="min-w-0 flex-1">
          <TextField
            id={id}
            label="Divisioni ATECO"
            value={bozza}
            inputMode="numeric"
            maxLength={40}
            disabled={pieno}
            placeholder={pieno ? `Massimo ${massimo}` : "Es. 62, 72"}
            aria-describedby={`${id}-aiuto`}
            onChange={(e) => {
              setBozza(e.target.value);
              setAvviso(null);
            }}
            onKeyDown={onKeyDown}
          />
        </div>
        <Button variant="ghost" onClick={aggiungi} disabled={pieno || !bozza.trim()}>
          Aggiungi
        </Button>
      </div>
      <Chips
        voci={scelte.map((v) => ({ chiave: v, testo: v }))}
        onRimuovi={(v) => onChange(scelte.filter((x) => x !== v))}
      />
      <p id={`${id}-aiuto`} className="text-small text-ink-3">
        Le prime due cifre del codice ATECO (es. 62 per la produzione di software).
      </p>
      {avviso && (
        <p className="text-small text-warning-ink" role="status">
          {avviso}
        </p>
      )}
    </div>
  );
}

/** Paesi (ISO a due lettere, nomi in italiano). */
export function SceltaPaesi({
  etichetta,
  scelti,
  onChange,
  massimo,
}: {
  etichetta: string;
  scelti: string[];
  onChange: (paesi: string[]) => void;
  massimo: number;
}) {
  const [paese, setPaese] = useState("");
  const disponibili = useMemo(() => paesiOrdinati().filter((c) => !scelti.includes(c)), [scelti]);
  const pieno = scelti.length >= massimo;
  const aggiungi = () => {
    if (!paese || pieno || scelti.includes(paese)) return;
    onChange([...scelti, paese]);
    setPaese("");
  };
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex max-w-md items-end gap-2">
        <div className="min-w-0 flex-1">
          <SelectField
            label={etichetta}
            value={paese}
            disabled={pieno}
            onChange={(e) => setPaese(e.target.value)}
          >
            <option value="">{pieno ? `Massimo ${massimo} paesi` : "Scegli un paese…"}</option>
            {disponibili.map((c) => (
              <option key={c} value={c}>
                {nomePaese(c)}
              </option>
            ))}
          </SelectField>
        </div>
        <Button variant="ghost" onClick={aggiungi} disabled={!paese || pieno}>
          Aggiungi
        </Button>
      </div>
      <Chips
        voci={scelti.map((c) => ({ chiave: c, testo: nomePaese(c) }))}
        onRimuovi={(c) => onChange(scelti.filter((x) => x !== c))}
      />
    </div>
  );
}

/** Testo lungo con contatore dei caratteri. */
export function TestoLungo({
  etichetta,
  aiuto,
  valore,
  onChange,
  massimo,
  righe = 5,
  disabled,
  errore,
  required,
}: {
  etichetta: string;
  aiuto?: string;
  valore: string;
  onChange: (v: string) => void;
  massimo: number;
  righe?: number;
  disabled?: boolean;
  errore?: string;
  required?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <TextareaField
        label={etichetta}
        helper={aiuto}
        error={errore}
        rows={righe}
        maxLength={massimo}
        value={valore}
        disabled={disabled}
        required={required}
        onChange={(e) => onChange(e.target.value)}
      />
      <p className="text-right text-small text-ink-3 tabular-nums" aria-hidden>
        {valore.length.toLocaleString("it-IT")} / {massimo.toLocaleString("it-IT")}
      </p>
    </div>
  );
}

/** Scelta tra poche opzioni con radio (legenda obbligatoria), sul `RadioGroup`
 *  del design system. */
export function SceltaRadio<T extends string>({
  legenda,
  nome,
  opzioni,
  valore,
  onChange,
  disabled,
  nota,
}: {
  legenda: string;
  nome: string;
  opzioni: Array<{ valore: T; etichetta: string; nota?: string }>;
  valore: T | null;
  onChange: (v: T) => void;
  disabled?: boolean;
  nota?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <RadioGroup
        nome={nome}
        legend={legenda}
        opzioni={opzioni.map((o) => ({ id: o.valore, label: o.etichetta, descrizione: o.nota }))}
        valore={valore}
        onChange={(id) => onChange(id as T)}
        disabled={disabled}
      />
      {nota && <p className="text-small text-ink-3">{nota}</p>}
    </div>
  );
}
