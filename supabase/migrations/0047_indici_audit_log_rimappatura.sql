-- ============================================================================
-- BandoFit — DB primario, migration 0047: INDICI DI audit_log PER LA
-- RIMAPPATURA DEI FUSI (prestazioni; complemento della 0043-0046).
--
-- Su audit_log ci sono solo gli indici della 0003 (famiglia, utente). Le
-- funzioni del ripristino e dei doppioni rimappati (0044, 0045, 0046) leggono
-- le voci catalogo.rimappatura_fuso per doppione, per riga (gli anelli della
-- catena) e per master (la riga che «sostiene» un'eliminazione), e per ogni
-- voce cercano con un not exists le voci catalogo.ripristino_rimappatura e
-- catalogo.conflitto_definitivo che la citano: senza indici ognuna di queste
-- letture è una scansione completa del registro, ripetuta per ogni voce.
--
-- Indici PARZIALI, uno per condizione, con le espressioni ESATTE delle
-- funzioni (un indice su un'espressione serve solo se coincide):
--   1) audit_log_rimappatura_doppione_idx — (payload->'doppione', id) delle
--      voci catalogo.rimappatura_fuso: il ciclo principale del ripristino
--      (payload->'doppione' = to_jsonb(p_doppione), order by id desc);
--   2) audit_log_rimappatura_riga_idx — (payload->'id', payload->'doppione')
--      delle stesse voci: gli anelli della catena della 0046 (stessa riga,
--      doppione uguale al master dell'anello precedente). Con il solo
--      doppione nella chiave il planner potrebbe scegliere il primo indice e
--      scorrere tutte le voci di un master molto usato per ogni anello;
--   3) audit_log_rimappatura_master_idx — (payload->'master',
--      payload->>'operazione') delle stesse voci: la regola della riga che
--      «sostiene» l'eliminazione della riga di un altro doppione dello stesso
--      master (master della catena e operazione 'eliminata'). L'operazione
--      sta nella chiave e non nel predicato: un indice con
--      payload->>'operazione' nel predicato sembrerebbe al planner molto più
--      piccolo di quello che è (quell'espressione non ha statistiche) e lo
--      scorrerebbe tutto invece di cercarvi il master;
--   4) audit_log_rimappatura_voce_idx — payload->'voce' delle voci
--      catalogo.ripristino_rimappatura e catalogo.conflitto_definitivo: i not
--      exists del ripristino e l'anti-join di fn_doppioni_rimappati.
-- Le funzioni PL/pgSQL passano le azioni come parametri: il planner usa un
-- indice parziale nei piani costruiti con i valori dei parametri, quelli che
-- la cache dei piani tiene quando il piano generico sarebbe una scansione
-- completa, cioè proprio quando il registro è grande.
--
-- SOLO PRESTAZIONI: nessuna funzione, tabella o riga toccata; senza questa
-- migration tutto funziona come prima. ADDITIVA e applicabile più volte
-- (if not exists). Niente concurrently: la tabella è piccola e la procedura
-- di docs/deploy.md esegue il file IN UN'UNICA TRANSAZIONE (begin; ...
-- commit;); la costruzione blocca le scritture su audit_log per la sua
-- durata. Rollback in coda.
-- ============================================================================

-- 1) Voci della rimappatura per doppione.
create index if not exists audit_log_rimappatura_doppione_idx
  on public.audit_log ((payload->'doppione'), id)
  where action = 'catalogo.rimappatura_fuso';

-- 2) Voci della rimappatura per riga (anelli della catena).
create index if not exists audit_log_rimappatura_riga_idx
  on public.audit_log ((payload->'id'), (payload->'doppione'))
  where action = 'catalogo.rimappatura_fuso';

-- 3) Voci della rimappatura per master e operazione.
create index if not exists audit_log_rimappatura_master_idx
  on public.audit_log ((payload->'master'), (payload->>'operazione'))
  where action = 'catalogo.rimappatura_fuso';

-- 4) Ripristini e conflitti definitivi per voce citata.
create index if not exists audit_log_rimappatura_voce_idx
  on public.audit_log ((payload->'voce'))
  where action in ('catalogo.ripristino_rimappatura', 'catalogo.conflitto_definitivo');

-- ============================================================================
-- ROLLBACK 0047 (in transazione, in qualunque momento: nessuna funzione
-- dipende dagli indici):
--   drop index if exists public.audit_log_rimappatura_doppione_idx;
--   drop index if exists public.audit_log_rimappatura_riga_idx;
--   drop index if exists public.audit_log_rimappatura_master_idx;
--   drop index if exists public.audit_log_rimappatura_voce_idx;
-- Nessun dato da annullare.
-- ============================================================================
