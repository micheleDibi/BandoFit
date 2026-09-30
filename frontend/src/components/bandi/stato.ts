import { formatDate, formatTime } from "../../lib/format";

/** Stato da mostrare per un bando: quello calcolato dal catalogo alla lettura
 *  (`stato_effettivo`), con `stato_bando` come ripiego. */
export function statoDelBando(b: {
  stato_effettivo?: string | null;
  stato_bando: string | null;
}): string | null {
  return b.stato_effettivo ?? b.stato_bando;
}

/** Bando aperto o in apertura: solo allora si mostrano il conto alla rovescia,
 *  il pulsante principale e «Aggiungi scadenza al calendario». Uno stato
 *  mancante non toglie nulla (con il catalogo attuale c'è sempre); qualunque
 *  altro valore (chiuso, sospeso, revocato, valori nuovi) sì. */
export function bandoInCorso(stato: string | null): boolean {
  return stato === null || stato === "aperto" || stato === "in apertura prossimamente";
}

/** «15 ott 2026, ore 12:00»; senza orario solo la data. */
export function dataConOra(data: string | null, ora?: string | null): string {
  return ora ? `${formatDate(data)}, ore ${formatTime(ora)}` : formatDate(data);
}
