import { ShieldAlert } from "lucide-react";
import { useId } from "react";
import { BOZZE_COPY } from "../../lib/copy";
import type { BozzaDocumento } from "../../types";

/** Segnaposto da sostituire a mano: «[Capofila]», «[Partner 1]», «[Data]», …
 *  (la stessa forma di `_SEGNAPOSTO` in `partenariato_bozze_prompts.py`). */
const SEGNAPOSTO = /(\[[^[\]\n]{1,80}\])/g;

/** Testo semplice (mai HTML) con i segnaposto evidenziati: `split` con il
 *  gruppo di cattura mette i segnaposto negli indici dispari. */
function ConSegnaposto({ testo }: { testo: string }) {
  const parti = testo.split(SEGNAPOSTO);
  return (
    <>
      {parti.map((parte, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded bg-amber-100 px-0.5 text-amber-900">
            {parte}
          </mark>
        ) : (
          parte
        ),
      )}
    </>
  );
}

/** Il disclaimer fisso delle bozze (non lo scrive il modello): lo stesso
 *  testo in testa e a piè del PDF. */
export function DisclaimerBozza() {
  return (
    <p
      className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-sm text-amber-900"
      role="note"
    >
      <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
      {BOZZE_COPY.disclaimer}
    </p>
  );
}

/** Anteprima di una bozza pronta: disclaimer, titolo, sezioni, le note per
 *  chi la usa (cosa completare) e cosa ha tolto il controllo automatico. I
 *  segnaposto sono evidenziati. */
export function BozzaAnteprima({ bozza }: { bozza: BozzaDocumento }) {
  const idTitolo = useId();
  const titolo = bozza.titolo?.trim() || BOZZE_COPY.tipi[bozza.tipo];
  const note = bozza.note_per_l_utente.filter((n) => n.trim());
  const avvisi = bozza.avvisi.filter((a) => a.trim());
  return (
    <article aria-labelledby={idTitolo} className="space-y-4">
      <DisclaimerBozza />
      <div className="rounded-lg border border-slate-200 bg-white px-5 py-4">
        <h3 id={idTitolo} className="font-display text-base font-semibold text-slate-900">
          {titolo}
        </h3>
        <div className="mt-3 space-y-4">
          {bozza.sezioni.map((s, i) => (
            <section key={i}>
              {s.titolo.trim() && (
                <h4 className="text-sm font-semibold text-slate-800">
                  <ConSegnaposto testo={s.titolo} />
                </h4>
              )}
              <p className="mt-1 whitespace-pre-line text-sm leading-relaxed text-slate-700">
                <ConSegnaposto testo={s.testo} />
              </p>
            </section>
          ))}
        </div>
      </div>
      {note.length > 0 && (
        <div className="rounded-lg bg-slate-50 px-4 py-3">
          <h4 className="text-sm font-semibold text-slate-800">{BOZZE_COPY.daCompletare}</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-slate-700">
            {note.map((n, i) => (
              <li key={i}>
                <ConSegnaposto testo={n} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {avvisi.length > 0 && (
        <div className="rounded-lg border border-slate-200 px-4 py-3">
          <h4 className="text-sm font-semibold text-slate-800">{BOZZE_COPY.avvisiTitolo}</h4>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm text-slate-600">
            {avvisi.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-xs text-slate-500">{BOZZE_COPY.notaSegnaposto}</p>
    </article>
  );
}
