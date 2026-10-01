import type { ComponentType } from "react";

/** Forma di una sezione: `export const titolo` e un componente di default. */
interface Sezione {
  titolo?: string;
  default: ComponentType;
}

/** Le sezioni si caricano con `import.meta.glob`, senza import statici: una
 *  sezione mancante (un task non ancora finito) non rompe la pagina. Ogni
 *  operatore scrive solo la propria: Campi, Feedback, Primitivi, Struttura. */
const moduli = import.meta.glob<Sezione>("./sezioni/*.tsx", { eager: true });

const sezioni = Object.entries(moduli)
  .map(([percorso, modulo]) => {
    const id = percorso.replace(/^.*\//, "").replace(/\.tsx$/, "");
    return { id, titolo: modulo.titolo ?? id, Contenuto: modulo.default };
  })
  .sort((a, b) => a.id.localeCompare(b.id, "it"));

/** Vetrina dei componenti: la prova visiva dell'ondata. Montata in `App.tsx`
 *  su `/_vetrina`, fuori dall'autenticazione e dalla cornice dell'app, solo con
 *  `import.meta.env.DEV` e `lazy()`: non entra nel bundle di produzione. Per
 *  questo ha una cornice minima propria e non usa hook che leggono `/me` o
 *  l'azienda attiva; una sezione che ha bisogno di un provider (`ToastProvider`)
 *  se lo monta da sola. */
export default function Vetrina() {
  return (
    <div className="min-h-dvh bg-desk text-ink">
      <div className="mx-auto flex w-full max-w-278 flex-col gap-12 px-6 py-8 lg:px-10">
      <header className="flex flex-col gap-1">
        <h1 className="text-title-page text-ink">Vetrina dei componenti</h1>
        <p className="text-body text-ink-2">
          Solo in sviluppo. Ogni componente di <code>components/ui</code> con i suoi stati, sezione
          per sezione.
        </p>
        {sezioni.length > 0 && (
          <nav aria-label="Sezioni" className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
            {sezioni.map((s) => (
              <a key={s.id} href={`#${s.id}`} className="text-small font-medium text-accent-hover hover:underline">
                {s.titolo}
              </a>
            ))}
          </nav>
        )}
      </header>

      {sezioni.length === 0 && (
        <p className="text-body text-ink-2">
          Nessuna sezione ancora: aggiungi un file in <code>pages/dev/sezioni/</code>.
        </p>
      )}

      {sezioni.map(({ id, titolo, Contenuto }) => (
        <section key={id} id={id} aria-labelledby={`${id}-titolo`} className="flex flex-col gap-6">
          <h2 id={`${id}-titolo`} className="border-b border-line pb-3 text-title-section text-ink">
            {titolo}
          </h2>
          <Contenuto />
        </section>
      ))}
      </div>
    </div>
  );
}
