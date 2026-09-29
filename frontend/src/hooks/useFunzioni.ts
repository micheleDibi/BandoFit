import { useMe } from "./useMe";

/** Moduli accesi su questo ambiente, letti da `GET /me` (`funzioni`).
 *
 *  Finché `/me` non è arrivato (o se non riporta il campo) tutto vale spento:
 *  meglio una sezione che compare un attimo dopo che una che chiama rotte in
 *  404. `isPending` serve solo ai guard di rotta, che devono aspettare prima
 *  di decidere per il «non trovato». */
export function useFunzioni() {
  const { data: me, isPending } = useMe();
  return {
    partenariatiAttivo: me?.funzioni?.partenariati === true,
    bilanciStoricoAttivo: me?.funzioni?.bilanci_storico === true,
    isPending,
  };
}
