import { Plus, Trash2 } from "lucide-react";
import { useId, useRef } from "react";
import type { EsperienzaPartner, LookupItem } from "../../types";
import { Button } from "../ui/Button";
import { SelectField, TextField } from "../ui/Field";

/** Stessi limiti dello schema del server (`EsperienzaPartner`). */
export const MAX_ESPERIENZE = 20;
export const MAX_PROGRAMMA = 120;
export const MAX_TITOLO_ESPERIENZA = 200;
export const ANNO_MINIMO_ESPERIENZA = 1990;

export const ESITI_ESPERIENZA: Record<NonNullable<EsperienzaPartner["esito"]>, string> = {
  finanziato: "Finanziato",
  in_valutazione: "In valutazione",
  non_finanziato: "Non finanziato",
};

export const esperienzaVuota = (): EsperienzaPartner => ({
  programma: "",
  programma_id: null,
  anno: null,
  ruolo: null,
  titolo: null,
  esito: null,
});

/** Errore di una riga, controllato prima del salvataggio (il server fa lo
 *  stesso controllo: qui serve a dire DOVE è il problema). */
export function erroreEsperienza(e: EsperienzaPartner, annoCorrente: number): string | null {
  if (!e.programma.trim()) return "Indica il programma";
  if (e.anno !== null && (e.anno < ANNO_MINIMO_ESPERIENZA || e.anno > annoCorrente + 1)) {
    return `L'anno deve essere tra ${ANNO_MINIMO_ESPERIENZA} e ${annoCorrente + 1}`;
  }
  return null;
}

let prossimoId = 0;
const nuovoId = () => `esp-${++prossimoId}`;

/** Righe «esperienze nei programmi di finanziamento»: aggiungi e rimuovi,
 *  con il focus che segue l'azione (sulla riga nuova, o sul bottone
 *  «Aggiungi» dopo una rimozione). Il programma si scrive o si sceglie dal
 *  catalogo: se il nome coincide con un programma noto, ne salva l'id. */
export function EsperienzeEditor({
  valore,
  onChange,
  programmi,
  mostraErrori,
}: {
  valore: EsperienzaPartner[];
  onChange: (esperienze: EsperienzaPartner[]) => void;
  programmi: LookupItem[];
  /** Dopo un tentativo di salvataggio: evidenzia le righe incomplete. */
  mostraErrori: boolean;
}) {
  const idBase = useId();
  const idListaProgrammi = `${idBase}-programmi`;
  const annoCorrente = new Date().getFullYear();
  // Chiavi stabili per riga (non si inviano al server): con l'indice, dopo
  // una rimozione React riuserebbe gli input della riga sbagliata.
  const chiavi = useRef<string[]>([]);
  while (chiavi.current.length < valore.length) chiavi.current.push(nuovoId());
  if (chiavi.current.length > valore.length) chiavi.current.length = valore.length;
  const aggiungiRef = useRef<HTMLButtonElement>(null);
  const daFocalizzare = useRef<string | null>(null);

  const aggiorna = (indice: number, modifica: Partial<EsperienzaPartner>) =>
    onChange(valore.map((e, i) => (i === indice ? { ...e, ...modifica } : e)));

  const aggiungi = () => {
    if (valore.length >= MAX_ESPERIENZE) return;
    const chiave = nuovoId();
    chiavi.current.push(chiave);
    daFocalizzare.current = chiave;
    onChange([...valore, esperienzaVuota()]);
  };

  const rimuovi = (indice: number) => {
    chiavi.current.splice(indice, 1);
    onChange(valore.filter((_, i) => i !== indice));
    requestAnimationFrame(() => aggiungiRef.current?.focus());
  };

  const cambiaProgramma = (indice: number, testo: string) => {
    const nome = testo.trim().toLowerCase();
    const trovato = nome ? programmi.find((p) => p.nome.trim().toLowerCase() === nome) : undefined;
    aggiorna(indice, { programma: testo, programma_id: trovato?.id ?? null });
  };

  return (
    <div className="space-y-3">
      <datalist id={idListaProgrammi}>
        {programmi.map((p) => (
          <option key={p.id} value={p.nome} />
        ))}
      </datalist>

      {valore.length === 0 && (
        <p className="text-sm text-slate-500">
          Nessuna esperienza indicata. Aggiungi i programmi in cui l'azienda ha già partecipato
          a un progetto finanziato, anche con altri.
        </p>
      )}

      <ol className="space-y-3">
        {valore.map((esperienza, indice) => {
          const chiave = chiavi.current[indice];
          const errore = mostraErrori ? erroreEsperienza(esperienza, annoCorrente) : null;
          const numero = indice + 1;
          return (
            <li key={chiave} className="rounded-lg border border-slate-200 bg-slate-50/50 p-4">
              <fieldset className="grid gap-3 sm:grid-cols-2">
                <legend className="sr-only">Esperienza {numero}</legend>
                <div className="sm:col-span-2">
                  <TextField
                    ref={(el) => {
                      if (el && daFocalizzare.current === chiave) {
                        daFocalizzare.current = null;
                        el.focus();
                      }
                    }}
                    label="Programma"
                    required
                    list={idListaProgrammi}
                    maxLength={MAX_PROGRAMMA}
                    placeholder="Es. Horizon Europe, PNRR, POR FESR…"
                    value={esperienza.programma}
                    onChange={(e) => cambiaProgramma(indice, e.target.value)}
                    error={errore ?? undefined}
                  />
                </div>
                <TextField
                  label="Anno"
                  type="number"
                  inputMode="numeric"
                  min={ANNO_MINIMO_ESPERIENZA}
                  max={annoCorrente + 1}
                  value={esperienza.anno ?? ""}
                  onChange={(e) => {
                    const n = Number.parseInt(e.target.value, 10);
                    aggiorna(indice, { anno: Number.isFinite(n) ? n : null });
                  }}
                />
                <SelectField
                  label="Ruolo"
                  value={esperienza.ruolo ?? ""}
                  onChange={(e) =>
                    aggiorna(indice, {
                      ruolo: (e.target.value || null) as EsperienzaPartner["ruolo"],
                    })
                  }
                >
                  <option value="">Non indicato</option>
                  <option value="capofila">Capofila</option>
                  <option value="partner">Partner</option>
                </SelectField>
                <div className="sm:col-span-2">
                  <TextField
                    label="Titolo del progetto (facoltativo)"
                    maxLength={MAX_TITOLO_ESPERIENZA}
                    value={esperienza.titolo ?? ""}
                    onChange={(e) => aggiorna(indice, { titolo: e.target.value || null })}
                  />
                </div>
                <SelectField
                  label="Esito"
                  value={esperienza.esito ?? ""}
                  onChange={(e) =>
                    aggiorna(indice, {
                      esito: (e.target.value || null) as EsperienzaPartner["esito"],
                    })
                  }
                >
                  <option value="">Non indicato</option>
                  {Object.entries(ESITI_ESPERIENZA).map(([codice, etichetta]) => (
                    <option key={codice} value={codice}>
                      {etichetta}
                    </option>
                  ))}
                </SelectField>
                <div className="flex items-end justify-end">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => rimuovi(indice)}
                    aria-label={`Rimuovi l'esperienza ${numero}${esperienza.programma.trim() ? ` (${esperienza.programma.trim()})` : ""}`}
                  >
                    <Trash2 className="size-4" aria-hidden />
                    Rimuovi
                  </Button>
                </div>
              </fieldset>
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          ref={aggiungiRef}
          variant="secondary"
          size="sm"
          onClick={aggiungi}
          disabled={valore.length >= MAX_ESPERIENZE}
        >
          <Plus className="size-4" aria-hidden />
          Aggiungi un'esperienza
        </Button>
        {valore.length >= MAX_ESPERIENZE && (
          <p className="text-xs text-slate-500">Puoi indicare al massimo {MAX_ESPERIENZE} esperienze.</p>
        )}
      </div>
    </div>
  );
}
