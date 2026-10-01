import { Plus, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { EsperienzaPartner, LookupItem } from "../../types";
import { Button } from "../ui/Button";
import { SelectField, TextField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";

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

const RUOLI_ESPERIENZA: Record<NonNullable<EsperienzaPartner["ruolo"]>, string> = {
  capofila: "Capofila",
  partner: "Partner",
};

/** Riga aperta nell'editor: `originale` è la riga com'era all'apertura
 *  (`null` = riga appena aggiunta), per «Annulla». */
interface RigaAperta {
  chiave: string;
  originale: EsperienzaPartner | null;
}

/** Righe «esperienze nei programmi di finanziamento». Ogni riga si legge in
 *  una linea (programma, anno, ruolo, esito, titolo) e si apre in linea con
 *  «Modifica»; «Aggiungi un'esperienza» apre una riga nuova. Le modifiche
 *  vanno subito nel profilo (la barra di salvataggio compare come per gli
 *  altri campi): «Conferma l'esperienza» chiude l'editor se la riga è
 *  completa, «Annulla» rimette la riga com'era (o toglie quella nuova). Il
 *  focus segue l'azione: sul programma all'apertura, sulla riga (o su
 *  «Aggiungi») alla chiusura. Il programma si scrive o si sceglie dal
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
  const modificaRefs = useRef(new Map<string, HTMLButtonElement>());
  const [aperta, setAperta] = useState<RigaAperta | null>(null);
  // «Conferma» su una riga incompleta: mostra l'errore nell'editor.
  const [confermaTentata, setConfermaTentata] = useState(false);

  const aggiorna = (indice: number, modifica: Partial<EsperienzaPartner>) =>
    onChange(valore.map((e, i) => (i === indice ? { ...e, ...modifica } : e)));

  // Alla chiusura dell'editor il focus torna su «Modifica» della riga (montato
  // al render successivo), altrimenti su «Aggiungi».
  const focusDopo = (chiave: string) =>
    requestAnimationFrame(() => {
      const bottone = modificaRefs.current.get(chiave) ?? aggiungiRef.current;
      bottone?.focus();
    });

  const apri = (chiave: string, originale: EsperienzaPartner | null) => {
    daFocalizzare.current = chiave;
    setConfermaTentata(false);
    setAperta({ chiave, originale });
  };

  const aggiungi = () => {
    if (valore.length >= MAX_ESPERIENZE) return;
    const chiave = nuovoId();
    chiavi.current.push(chiave);
    onChange([...valore, esperienzaVuota()]);
    apri(chiave, null);
  };

  const rimuovi = (indice: number) => {
    chiavi.current.splice(indice, 1);
    onChange(valore.filter((_, i) => i !== indice));
    setAperta(null);
    requestAnimationFrame(() => aggiungiRef.current?.focus());
  };

  const conferma = (indice: number) => {
    if (erroreEsperienza(valore[indice], annoCorrente)) {
      setConfermaTentata(true);
      return;
    }
    const chiave = chiavi.current[indice];
    setAperta(null);
    focusDopo(chiave);
  };

  const annulla = (indice: number) => {
    if (!aperta) return;
    if (aperta.originale === null) {
      rimuovi(indice);
      return;
    }
    const originale = aperta.originale;
    onChange(valore.map((e, i) => (i === indice ? originale : e)));
    setAperta(null);
    focusDopo(chiavi.current[indice]);
  };

  const cambiaProgramma = (indice: number, testo: string) => {
    const nome = testo.trim().toLowerCase();
    const trovato = nome ? programmi.find((p) => p.nome.trim().toLowerCase() === nome) : undefined;
    aggiorna(indice, { programma: testo, programma_id: trovato?.id ?? null });
  };

  return (
    <div className="flex flex-col gap-3">
      <datalist id={idListaProgrammi}>
        {programmi.map((p) => (
          <option key={p.id} value={p.nome} />
        ))}
      </datalist>

      {valore.length === 0 && (
        <p className="text-body text-ink-2">
          Nessuna esperienza indicata. Aggiungi i programmi in cui l'azienda ha già partecipato
          a un progetto finanziato, anche con altri.
        </p>
      )}

      {valore.length > 0 && (
        <ol className="flex flex-col divide-y divide-line border-y border-line">
          {valore.map((esperienza, indice) => {
            const chiave = chiavi.current[indice];
            const errore = erroreEsperienza(esperienza, annoCorrente);
            const numero = indice + 1;
            const programma = esperienza.programma.trim();
            const nomeRiga = `l'esperienza ${numero}${programma ? ` (${programma})` : ""}`;

            if (aperta?.chiave === chiave) {
              const mostra = (mostraErrori || confermaTentata) && errore;
              const erroreProgramma = mostra && !programma ? errore : undefined;
              const erroreAnno = mostra && programma ? errore : undefined;
              return (
                <li key={chiave} className="py-3">
                  <fieldset className="grid gap-4 rounded-panel bg-desk p-5 sm:grid-cols-2">
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
                        error={erroreProgramma || undefined}
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
                      error={erroreAnno || undefined}
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
                    <div className="flex flex-wrap items-end justify-end gap-2 sm:col-span-2">
                      <Button variant="ghost" onClick={() => annulla(indice)}>
                        Annulla
                      </Button>
                      <Button variant="secondary" onClick={() => conferma(indice)}>
                        Conferma l'esperienza
                      </Button>
                    </div>
                  </fieldset>
                </li>
              );
            }

            const dettagli = [
              esperienza.anno !== null ? String(esperienza.anno) : null,
              esperienza.ruolo ? RUOLI_ESPERIENZA[esperienza.ruolo] : null,
              esperienza.esito ? ESITI_ESPERIENZA[esperienza.esito] : null,
            ].filter((d): d is string => d !== null);
            return (
              <li key={chiave} className="flex flex-wrap items-start justify-between gap-3 py-3">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <p className="text-row-title text-ink">{programma || "Programma non indicato"}</p>
                  {esperienza.titolo?.trim() && (
                    <p className="text-body text-ink-2">{esperienza.titolo.trim()}</p>
                  )}
                  {dettagli.length > 0 && (
                    <p className="flex flex-wrap gap-x-4 gap-y-1 text-small text-ink-3 tabular-nums">
                      {dettagli.map((d) => (
                        <span key={d}>{d}</span>
                      ))}
                    </p>
                  )}
                  {mostraErrori && errore && <InlineError>{errore}</InlineError>}
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    ref={(el) => {
                      if (el) modificaRefs.current.set(chiave, el);
                      else modificaRefs.current.delete(chiave);
                    }}
                    variant="ghost"
                    size="sm"
                    onClick={() => apri(chiave, esperienza)}
                    aria-label={`Modifica ${nomeRiga}`}
                  >
                    Modifica
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => rimuovi(indice)}
                    aria-label={`Rimuovi ${nomeRiga}`}
                  >
                    <Trash2 className="size-4" aria-hidden />
                    Rimuovi
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          ref={aggiungiRef}
          variant="ghost"
          onClick={aggiungi}
          disabled={valore.length >= MAX_ESPERIENZE}
        >
          <Plus className="size-4" aria-hidden />
          Aggiungi un'esperienza
        </Button>
        {valore.length >= MAX_ESPERIENZE && (
          <p className="text-small text-ink-3">
            Puoi indicare al massimo {MAX_ESPERIENZE} esperienze.
          </p>
        )}
      </div>
    </div>
  );
}
