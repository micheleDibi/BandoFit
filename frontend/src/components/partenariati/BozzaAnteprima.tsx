import { useId } from "react";
import { BOZZE_COPY } from "../../lib/copy";
import type { BozzaDocumento } from "../../types";
import { Alert } from "../ui/Alert";
import { Card } from "../ui/Card";

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
          <mark key={i} className="rounded-mark bg-warning-soft px-0.5 text-warning-ink">
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
  // Testo fisso: non va annunciato come allarme.
  return (
    <Alert tono="attenzione" ruolo="none">
      {BOZZE_COPY.disclaimer}
    </Alert>
  );
}

/** Anteprima di una bozza pronta: disclaimer, il documento (un riquadro: è un
 *  oggetto), le note per chi la usa (cosa completare) e cosa ha tolto il
 *  controllo automatico. I segnaposto sono evidenziati. */
export function BozzaAnteprima({ bozza }: { bozza: BozzaDocumento }) {
  const idTitolo = useId();
  const titolo = bozza.titolo?.trim() || BOZZE_COPY.tipi[bozza.tipo];
  const note = bozza.note_per_l_utente.filter((n) => n.trim());
  const avvisi = bozza.avvisi.filter((a) => a.trim());
  return (
    <article aria-labelledby={idTitolo} className="flex flex-col gap-4">
      <DisclaimerBozza />
      {/* Il documento sta dentro la card della scheda: foglio incassato, senza ombra. */}
      <Card className="flex flex-col gap-4 bg-desk shadow-none">
        <h3 id={idTitolo} className="font-sans text-row-title text-ink">
          {titolo}
        </h3>
        {bozza.sezioni.map((s, i) => (
          <section key={i} className="flex flex-col gap-1">
            {s.titolo.trim() && (
              <h4 className="font-sans text-title-group text-ink">
                <ConSegnaposto testo={s.titolo} />
              </h4>
            )}
            <p className="whitespace-pre-line text-body text-ink-2">
              <ConSegnaposto testo={s.testo} />
            </p>
          </section>
        ))}
      </Card>
      {note.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="font-sans text-title-group text-ink">{BOZZE_COPY.daCompletare}</h4>
          <ul className="list-disc pl-5 text-body text-ink-2">
            {note.map((n, i) => (
              <li key={i}>
                <ConSegnaposto testo={n} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {avvisi.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="font-sans text-title-group text-ink">{BOZZE_COPY.avvisiTitolo}</h4>
          <ul className="list-disc pl-5 text-body text-ink-2">
            {avvisi.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="text-small text-ink-3">{BOZZE_COPY.notaSegnaposto}</p>
    </article>
  );
}
