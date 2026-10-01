import { useEffect, useRef } from "react";

/** Una pagina oltre l'ultima (link vecchio, URL a mano, elementi tolti nel
 *  frattempo) risponde vuota con `total > 0`: invece dello stato vuoto si
 *  rientra sull'ultima pagina. Mai sui dati segnaposto della query precedente.
 *  La destinazione è sempre prima della pagina corrente, anche con un
 *  `total_pages` incoerente: nessun ciclo. Restituisce true finché il rientro
 *  è in corso (la lista mostra il caricamento): anche mentre la pagina giusta
 *  arriva e al suo posto c'è, come segnaposto, la pagina vuota di prima.
 *  Una sola copia per bacheca, call e pannello admin dei partenariati:
 *  `rientra` sceglie come cambiare pagina (URL senza cronologia, o stato). */
export function useRientroPagina(
  dati: { items: readonly unknown[]; total: number; page: number; total_pages: number } | undefined,
  pagina: number,
  segnaposto: boolean,
  rientra: (n: number) => void,
): boolean {
  // Conta la pagina DEI DATI: sui segnaposto è ancora quella vuota di prima.
  const vuotaOltre = !!dati && dati.page > 1 && dati.items.length === 0 && dati.total > 0;
  const destinazione =
    vuotaOltre && !segnaposto && pagina > 1
      ? Math.max(1, Math.min(dati.total_pages, pagina - 1))
      : null;
  // La callback cambia a ogni render: l'effetto dipende solo dalla destinazione.
  const rientraRef = useRef(rientra);
  useEffect(() => {
    rientraRef.current = rientra;
  });
  useEffect(() => {
    if (destinazione !== null) rientraRef.current(destinazione);
  }, [destinazione]);
  // Mai un caricamento senza fine: senza segnaposto né rientro da fare, si
  // mostra quello che è arrivato.
  return vuotaOltre && (segnaposto || destinazione !== null);
}
