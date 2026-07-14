/** Robustezza password via zxcvbn-ts, caricato LAZY: i dizionari pesano
 *  centinaia di kB e le pagine che li usano sono pubbliche — i tre pacchetti
 *  diventano chunk async separati e si scaricano solo alla prima digitazione
 *  nel campo password. L'indicatore è puramente INFORMATIVO: non blocca mai
 *  il submit (l'unica regola imposta resta «almeno 8 caratteri», client e
 *  server). */

export interface Strength {
  label: "Debole" | "Media" | "Forte";
  segments: 1 | 2 | 3;
  barClass: string;
  textClass: string;
}

type CheckFn = (password: string, userInputs?: string[]) => { score: number };

let enginePromise: Promise<CheckFn | null> | null = null;

/** Promise-singleton: import dinamici + factory una volta sola.
 *  Ritorna null se il download fallisce (il meter non compare). */
export function loadZxcvbn(): Promise<CheckFn | null> {
  enginePromise ??= Promise.all([
    import("@zxcvbn-ts/core"),
    import("@zxcvbn-ts/language-common"),
    import("@zxcvbn-ts/language-it"),
  ])
    .then(([core, common, it]) => {
      const factory = new core.ZxcvbnFactory({
        dictionary: { ...common.dictionary, ...it.dictionary },
        graphs: common.adjacencyGraphs,
      });
      // zxcvbn degrada sugli input lunghissimi: 64 caratteri bastano.
      return (password: string, userInputs?: string[]) =>
        factory.check(password.slice(0, 64), userInputs);
    })
    .catch(() => null);
  return enginePromise;
}

/** Mappa lo score zxcvbn (0-4) sui tre livelli mostrati. */
export function strengthFromScore(score: number): Strength {
  if (score <= 1) {
    return { label: "Debole", segments: 1, barClass: "bg-red-500", textClass: "text-red-600" };
  }
  if (score === 2) {
    return { label: "Media", segments: 2, barClass: "bg-amber-500", textClass: "text-amber-600" };
  }
  return { label: "Forte", segments: 3, barClass: "bg-emerald-500", textClass: "text-emerald-600" };
}
