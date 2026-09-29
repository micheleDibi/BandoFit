import { Plus, X } from "lucide-react";
import { useId, useMemo, useState, type KeyboardEvent } from "react";
import { nomePaese, paesiOrdinati } from "../../lib/paesi";
import { Button } from "../ui/Button";
import { TextareaField } from "../ui/Field";
import { TagSelect } from "../ui/TagSelect";
import { Skeleton } from "../ui/states";

/* Campi di scelta riusati dagli editor della call (criteri e posizioni):
 * chip rimovibili + aggiunta, sul modello del profilo partner. */

function Chip({ testo, onRimuovi }: { testo: string; onRimuovi?: () => void }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-50 py-1 pl-2.5 pr-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
      <span className="truncate">{testo}</span>
      {onRimuovi && (
        <button
          type="button"
          onClick={onRimuovi}
          aria-label={`Rimuovi ${testo}`}
          className="cursor-pointer rounded-full p-0.5 transition-colors hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500"
        >
          <X className="size-3" aria-hidden />
        </button>
      )}
    </span>
  );
}

function Chips({ voci, onRimuovi }: { voci: Array<{ chiave: string; testo: string }>; onRimuovi: (chiave: string) => void }) {
  if (voci.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {voci.map((v) => (
        <li key={v.chiave}>
          <Chip testo={v.testo} onRimuovi={() => onRimuovi(v.chiave)} />
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
    <div className="space-y-1.5">
      <p className="text-sm font-medium text-slate-700">{etichetta}</p>
      {aiuto && (
        <p id={idAiuto} className="text-xs text-slate-500">
          {aiuto}
        </p>
      )}
      <Chips
        voci={scelti.map((c) => ({ chiave: c, testo: nomi.get(c) ?? c }))}
        onRimuovi={alterna}
      />
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
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        Divisioni ATECO
      </label>
      <p id={`${id}-aiuto`} className="text-xs text-slate-500">
        Le prime due cifre del codice ATECO (es. 62 per la produzione di software).
      </p>
      <Chips
        voci={scelte.map((v) => ({ chiave: v, testo: v }))}
        onRimuovi={(v) => onChange(scelte.filter((x) => x !== v))}
      />
      <div className="flex max-w-md gap-2">
        <input
          id={id}
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
          className="h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
        />
        <Button variant="secondary" onClick={aggiungi} disabled={pieno || !bozza.trim()}>
          <Plus className="size-4" aria-hidden />
          Aggiungi
        </Button>
      </div>
      {avviso && (
        <p className="text-xs text-amber-700" role="status">
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
  const id = useId();
  const [paese, setPaese] = useState("");
  const disponibili = useMemo(() => paesiOrdinati().filter((c) => !scelti.includes(c)), [scelti]);
  const pieno = scelti.length >= massimo;
  const aggiungi = () => {
    if (!paese || pieno || scelti.includes(paese)) return;
    onChange([...scelti, paese]);
    setPaese("");
  };
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        {etichetta}
      </label>
      <Chips
        voci={scelti.map((c) => ({ chiave: c, testo: nomePaese(c) }))}
        onRimuovi={(c) => onChange(scelti.filter((x) => x !== c))}
      />
      <div className="flex max-w-md gap-2">
        <select
          id={id}
          value={paese}
          disabled={pieno}
          onChange={(e) => setPaese(e.target.value)}
          className="h-10 w-full cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
        >
          <option value="">{pieno ? `Massimo ${massimo} paesi` : "Scegli un paese…"}</option>
          {disponibili.map((c) => (
            <option key={c} value={c}>
              {nomePaese(c)}
            </option>
          ))}
        </select>
        <Button variant="secondary" onClick={aggiungi} disabled={!paese || pieno}>
          <Plus className="size-4" aria-hidden />
          Aggiungi
        </Button>
      </div>
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
    <div>
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
      <p className="mt-1 text-right text-xs text-slate-400 tabular" aria-hidden>
        {valore.length.toLocaleString("it-IT")} / {massimo.toLocaleString("it-IT")}
      </p>
    </div>
  );
}

/** Scelta tra poche opzioni con radio (legenda obbligatoria). */
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
  const idNota = useId();
  return (
    <fieldset disabled={disabled} aria-describedby={nota ? idNota : undefined}>
      <legend className="text-sm font-medium text-slate-700">{legenda}</legend>
      {nota && (
        <p id={idNota} className="mt-0.5 text-xs text-slate-500">
          {nota}
        </p>
      )}
      <div className="mt-2 space-y-1.5">
        {opzioni.map((o) => (
          <label key={o.valore} className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input
              type="radio"
              name={nome}
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={valore === o.valore}
              onChange={() => onChange(o.valore)}
            />
            <span>
              {o.etichetta}
              {o.nota && <span className="block text-xs text-slate-500">{o.nota}</span>}
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
