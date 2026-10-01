-- ============================================================================
-- BandoFit — DB primario, migration 0045: DOPPIONI RIMAPPATI NON ANCORA
-- RIPRISTINATI (complemento della 0043 e della 0044).
--
-- Il passo periodico della rimappatura (services/rimappatura_fusi.py) legge
-- gli id «in uso» con fn_bandi_in_uso: dopo la rimappatura le righe stanno
-- sul master e il doppione non è più in uso, quindi una sua successiva
-- SEPARAZIONE nel catalogo (contratto DB bandi §6.2) resterebbe invisibile.
-- Questa funzione dà al passo l'elenco dei doppioni da tenere d'occhio: quelli
-- con almeno una voce catalogo.rimappatura_fuso in audit_log che nessuna voce
-- catalogo.ripristino_rimappatura cita (payload->'voce' è l'id della voce
-- d'origine, come scrive la 0044).
--
--   fn_doppioni_rimappati() → integer[] — id distinti, in ordine crescente,
--   di payload->'doppione' delle voci non ancora ripristinate; '{}' se non ce
--   ne sono, mai NULL. Un array e non un setof: anche l'output di una RPC
--   passa dal max-rows di PostgREST, che non tronca un valore unico. Una voce
--   con un doppione che non è un numero si ignora (la 0043 scrive sempre un
--   numero).
--
-- Con la 0045 il passo, dopo la rimappatura, cerca questi doppioni in
-- bando_fusione: chi manca è stato separato. Se il doppione è di nuovo nella
-- vista pubblica del catalogo, il passo chiama fn_ripristina_rimappatura
-- (0044) con p_prova pari alla modalità (prova → solo conteggi); altrimenti le
-- righe restano sul master e si riprova al passo successivo. Il ripristino
-- non cancella mai righe; le voci in conflitto restano da vedere a mano
-- (docs/deploy.md). La 0044 nasceva «da lanciare a mano»: da questa
-- migration la chiama anche lo scheduler, e la procedura manuale resta come
-- ripiego.
--
-- ADDITIVA: una funzione nuova; nessuna tabella o funzione esistente
-- modificata; nessun indice (le voci di audit_log sono poche; un indice
-- parziale su action si valuta se i volumi crescono). Da eseguire IN UN'UNICA
-- TRANSAZIONE (begin; ... commit;). Rollback in coda.
-- ============================================================================

create or replace function public.fn_doppioni_rimappati()
returns integer[]
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(array_agg(d.doppione order by d.doppione), '{}'::integer[])
  from (
    select distinct (v.payload->>'doppione')::integer as doppione
    from public.audit_log v
    where v.action = 'catalogo.rimappatura_fuso'
      and jsonb_typeof(v.payload->'doppione') = 'number'
      and not exists (
        select 1 from public.audit_log r
        where r.action = 'catalogo.ripristino_rimappatura'
          and r.payload->'voce' = to_jsonb(v.id)
      )
  ) d;
$$;

comment on function public.fn_doppioni_rimappati() is
  'Array degli id distinti e crescenti dei doppioni con almeno una voce audit_log catalogo.rimappatura_fuso (0043) che nessuna voce catalogo.ripristino_rimappatura (0044) cita in payload->''voce'': i doppioni la cui separazione nel catalogo va rilevata dal passo della rimappatura (0045). Un solo valore, così il max-rows di PostgREST non lo tronca; senza voci restituisce ''{}'', mai NULL.';

-- ----------------------------------------------------------------------------
-- Sicurezza: non eseguibile dai ruoli esposti (Supabase concede EXECUTE di
-- default a PUBLIC); la chiama solo il backend con service_role.
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_doppioni_rimappati()
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0045 (in transazione, DOPO aver riportato il backend alla versione
-- precedente o spento la rimappatura):
--   drop function public.fn_doppioni_rimappati();
-- Nessun dato da annullare: la funzione legge soltanto.
-- ============================================================================
