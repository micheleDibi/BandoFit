-- ============================================================================
-- BandoFit — DB primario, migration 0043: RIMAPPATURA DEI BANDI FUSI (fase c
-- del contratto del DB bandi, docs/contratto-db-bandi.md §6.2).
--
-- Il catalogo del DB secondario fonde i doppioni in un bando master: il
-- doppione esce dalla vista pubblica e `bando_fusione` indica il master
-- corrente (catene già appiattite). Le tabelle del primario che conservano un
-- bando_id senza FK cross-database vanno rimappate sul master, altrimenti
-- preferiti, scadenze in calendario e call di partenariato restano legati a
-- un bando che non si legge più.
--
-- Meccanismo: riconciliazione periodica e idempotente, NON per cursore sugli
-- eventi. Il backend (services/rimappatura_fusi.py) legge gli id «in uso» con
-- fn_bandi_in_uso, cerca quelli fusi nel secondario e per ogni coppia
-- (doppione, master) chiama fn_rimappa_bando_fuso.
--
--   1) fn_bandi_in_uso() → integer[] — id distinti, in ordine crescente,
--      presenti in saved_bandi, in calendar_events (tipo 'bando') e in
--      partner_calls negli stati attivi (quelli dell'indice
--      partner_calls_una_attiva); '{}' se non ce ne sono, mai NULL. Un array
--      e non un setof: anche l'output di una RPC passa dal max-rows di
--      PostgREST, che non tronca un valore unico;
--   2) fn_rimappa_bando_fuso(doppione, master, master_slug, prova) → jsonb —
--      rimappa in una sola transazione le righe del doppione. L'ambito è
--      utente × azienda con NULL = NULL (IS NOT DISTINCT FROM, come i vincoli
--      NULLS NOT DISTINCT della 0023):
--      - saved_bandi: se nello stesso ambito c'è già il master, la riga del
--        doppione si elimina; altrimenti passa al master (bando_id e
--        bando_slug; lo snapshot di titolo, scadenza e stato resta);
--      - calendar_events (tipo 'bando'): senza collisione passa al master; in
--        collisione con l'evento del master nello stesso ambito si elimina se
--        non ha note dell'utente, altrimenti diventa un evento personale
--        (titolo, data, orari e note invariati);
--      - partner_calls negli stati attivi: senza collisione su
--        partner_calls_una_attiva passa al master e perde bando_mancante_dal;
--        in collisione non si tocca (solo conteggio in_collisione). Le call
--        chiuse e partner_call_versioni restano come sono.
--      Nessun'altra tabella si tocca: storico degli alert, AI-check, cache
--      delle estrazioni, consulenze, estrazioni di partenariato, registri di
--      spesa e acquisti conservano il bando_id originale.
--      Ogni riga modificata o eliminata scrive una voce in audit_log
--      (catalogo.rimappatura_fuso) con i valori precedenti: dopo
--      un'eventuale separazione del doppione si ripristina a mano (query nel
--      rollback in coda). Con p_prova = true calcola gli stessi conteggi senza
--      scrivere nulla.
--
-- Concorrenza: le chiamate che scrivono si serializzano su un lock advisory;
-- ogni scrittura ricontrolla che la riga sia ancora del doppione e si conta
-- solo se ha toccato la riga. Se nel frattempo l'utente salva il master nello
-- stesso ambito, il vincolo di unicità fa fallire l'intera chiamata (nessuna
-- scrittura parziale) e il passo successivo la ripete.
--
-- ADDITIVA: due funzioni nuove; nessuna tabella o funzione esistente
-- modificata. Il backend attuale non le usa. Da eseguire IN UN'UNICA
-- TRANSAZIONE (begin; ... commit;). Rollback documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Bandi in uso nel primario (id del DB secondario, senza FK).
-- ----------------------------------------------------------------------------
create or replace function public.fn_bandi_in_uso()
returns integer[]
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(array_agg(u.bando_id order by u.bando_id), '{}'::integer[])
  from (
    select s.bando_id from public.saved_bandi s
    union
    select e.bando_id from public.calendar_events e where e.tipo = 'bando'
    union
    select c.bando_id from public.partner_calls c
    where c.stato in ('bozza', 'pubblicata', 'sospesa_moderazione')
  ) u;
$$;

comment on function public.fn_bandi_in_uso() is
  'Array degli id distinti e crescenti dei bandi del DB secondario referenziati da saved_bandi, calendar_events (tipo bando) e partner_calls negli stati attivi di partner_calls_una_attiva (bozza, pubblicata, sospesa_moderazione): i candidati della rimappatura dei fusi (0043). Un solo valore, così il max-rows di PostgREST non lo tronca; senza id restituisce ''{}'', mai NULL.';

-- ----------------------------------------------------------------------------
-- 2) Rimappatura di un doppione fuso sul master corrente. Classificazione di
--    ogni riga comune a prova e scrittura: i conteggi della prova sono quelli
--    che la scrittura produrrebbe sugli stessi dati.
-- ----------------------------------------------------------------------------
create or replace function public.fn_rimappa_bando_fuso(
  p_doppione    integer,
  p_master      integer,
  p_master_slug text,
  p_prova       boolean default false
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_azione     constant text := 'catalogo.rimappatura_fuso';
  r_sb         public.saved_bandi%rowtype;
  r_ce         public.calendar_events%rowtype;
  r_pc         record;
  v_operazione text;
  v_prima      jsonb;
  v_n          integer;
  v_sb_agg     integer := 0;
  v_sb_eli     integer := 0;
  v_ce_agg     integer := 0;
  v_ce_eli     integer := 0;
  v_ce_con     integer := 0;
  v_pc_agg     integer := 0;
  v_pc_col     integer := 0;
begin
  if p_doppione is null or p_master is null or p_prova is null
     or p_doppione <= 0 or p_master <= 0 or p_doppione = p_master
     or p_master_slug is null or btrim(p_master_slug) = ''
     or char_length(p_master_slug) > 300 then
    raise exception 'Parametri della rimappatura non validi'
      using detail = 'parametri_non_validi';
  end if;

  -- Due passi concorrenti (più worker) si serializzano; la prova non scrive
  -- e non attende.
  if not p_prova then
    perform pg_advisory_xact_lock(hashtext('catalogo_rimappatura_fusi'));
  end if;

  -- a) Bandi salvati: una sola riga del doppione per ambito (saved_bandi_unique).
  for r_sb in
    select * from public.saved_bandi where bando_id = p_doppione order by id
  loop
    v_operazione := case
      when exists (
        select 1 from public.saved_bandi m
        where m.bando_id = p_master
          and m.user_id = r_sb.user_id
          and m.company_profile_id is not distinct from r_sb.company_profile_id
      ) then 'eliminata'
      else 'aggiornata'
    end;

    if p_prova then
      v_n := 1;
    elsif v_operazione = 'eliminata' then
      delete from public.saved_bandi s
      where s.id = r_sb.id and s.bando_id = p_doppione
      returning to_jsonb(s) into v_prima;
      get diagnostics v_n = row_count;
    else
      update public.saved_bandi s
      set bando_id = p_master, bando_slug = p_master_slug
      where s.id = r_sb.id and s.bando_id = p_doppione;
      get diagnostics v_n = row_count;
      v_prima := jsonb_build_object('bando_id', r_sb.bando_id, 'bando_slug', r_sb.bando_slug);
    end if;

    if v_operazione = 'eliminata' then
      v_sb_eli := v_sb_eli + v_n;
    else
      v_sb_agg := v_sb_agg + v_n;
    end if;

    if not p_prova and v_n > 0 then
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (null, v_azione, r_sb.user_id, null,
              jsonb_build_object('tabella', 'saved_bandi', 'id', r_sb.id,
                                 'operazione', v_operazione,
                                 'company_profile_id', r_sb.company_profile_id,
                                 'doppione', p_doppione, 'master', p_master,
                                 'master_slug', p_master_slug, 'prima', v_prima));
    end if;
  end loop;

  -- b) Scadenze in calendario: una sola riga del doppione per ambito
  --    (calendar_events_one_per_bando). Gli eventi personali non hanno bando.
  for r_ce in
    select * from public.calendar_events
    where tipo = 'bando' and bando_id = p_doppione
    order by id
  loop
    v_operazione := case
      when not exists (
        select 1 from public.calendar_events m
        where m.tipo = 'bando'
          and m.bando_id = p_master
          and m.user_id = r_ce.user_id
          and m.company_profile_id is not distinct from r_ce.company_profile_id
      ) then 'aggiornata'
      -- Note dell'utente: almeno un carattere che non sia spazio bianco.
      when r_ce.note ~ '\S' then 'convertita'
      else 'eliminata'
    end;

    if p_prova then
      v_n := 1;
    elsif v_operazione = 'aggiornata' then
      update public.calendar_events e
      set bando_id = p_master, bando_slug = p_master_slug
      where e.id = r_ce.id and e.tipo = 'bando' and e.bando_id = p_doppione;
      get diagnostics v_n = row_count;
      v_prima := jsonb_build_object('bando_id', r_ce.bando_id, 'bando_slug', r_ce.bando_slug);
    elsif v_operazione = 'convertita' then
      update public.calendar_events e
      set tipo = 'personale', bando_id = null, bando_slug = null
      where e.id = r_ce.id and e.tipo = 'bando' and e.bando_id = p_doppione;
      get diagnostics v_n = row_count;
      v_prima := jsonb_build_object('tipo', r_ce.tipo, 'bando_id', r_ce.bando_id,
                                    'bando_slug', r_ce.bando_slug);
    else
      delete from public.calendar_events e
      where e.id = r_ce.id and e.tipo = 'bando' and e.bando_id = p_doppione
      returning to_jsonb(e) into v_prima;
      get diagnostics v_n = row_count;
    end if;

    if v_operazione = 'aggiornata' then
      v_ce_agg := v_ce_agg + v_n;
    elsif v_operazione = 'convertita' then
      v_ce_con := v_ce_con + v_n;
    else
      v_ce_eli := v_ce_eli + v_n;
    end if;

    if not p_prova and v_n > 0 then
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (null, v_azione, r_ce.user_id, null,
              jsonb_build_object('tabella', 'calendar_events', 'id', r_ce.id,
                                 'operazione', v_operazione,
                                 'company_profile_id', r_ce.company_profile_id,
                                 'doppione', p_doppione, 'master', p_master,
                                 'master_slug', p_master_slug, 'prima', v_prima));
    end if;
  end loop;

  -- c) Call di partenariato attive: una sola del doppione per azienda
  --    (partner_calls_una_attiva). In collisione restano sul doppione.
  for r_pc in
    select c.id, c.company_profile_id, c.family_parent_id, c.bando_id, c.bando_slug,
           c.bando_mancante_dal
    from public.partner_calls c
    where c.bando_id = p_doppione
      and c.stato in ('bozza', 'pubblicata', 'sospesa_moderazione')
    order by c.id
  loop
    if exists (
      select 1 from public.partner_calls m
      where m.bando_id = p_master
        and m.company_profile_id is not distinct from r_pc.company_profile_id
        and m.stato in ('bozza', 'pubblicata', 'sospesa_moderazione')
    ) then
      v_pc_col := v_pc_col + 1;
      continue;
    end if;

    if p_prova then
      v_n := 1;
    else
      update public.partner_calls c
      set bando_id = p_master, bando_slug = p_master_slug, bando_mancante_dal = null
      where c.id = r_pc.id
        and c.bando_id = p_doppione
        and c.stato in ('bozza', 'pubblicata', 'sospesa_moderazione');
      get diagnostics v_n = row_count;
    end if;
    v_pc_agg := v_pc_agg + v_n;

    if not p_prova and v_n > 0 then
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (null, v_azione, r_pc.family_parent_id, r_pc.family_parent_id,
              jsonb_build_object('tabella', 'partner_calls', 'id', r_pc.id,
                                 'operazione', 'aggiornata',
                                 'company_profile_id', r_pc.company_profile_id,
                                 'doppione', p_doppione, 'master', p_master,
                                 'master_slug', p_master_slug,
                                 'prima', jsonb_build_object(
                                   'bando_id', r_pc.bando_id,
                                   'bando_slug', r_pc.bando_slug,
                                   'bando_mancante_dal', r_pc.bando_mancante_dal)));
    end if;
  end loop;

  return jsonb_build_object(
    'saved_bandi', jsonb_build_object('aggiornate', v_sb_agg, 'eliminate', v_sb_eli),
    'calendar_events', jsonb_build_object('aggiornati', v_ce_agg, 'eliminati', v_ce_eli,
                                          'convertiti', v_ce_con),
    'partner_calls', jsonb_build_object('aggiornate', v_pc_agg, 'in_collisione', v_pc_col));
end;
$$;

comment on function public.fn_rimappa_bando_fuso(integer, integer, text, boolean) is
  'Rimappa sul master corrente le righe di un bando fuso (0043), in una transazione e in modo idempotente: saved_bandi (master già salvato nello stesso ambito utente × azienda → DELETE, altrimenti UPDATE di bando_id e bando_slug), calendar_events tipo bando (senza collisione UPDATE; in collisione DELETE se senza note, altrimenti evento personale), partner_calls attive (senza collisione su partner_calls_una_attiva UPDATE e bando_mancante_dal NULL; in collisione nessuna modifica). Nessun''altra tabella. Ogni riga toccata → audit_log catalogo.rimappatura_fuso con i valori precedenti (riga intera per le DELETE). p_prova = true: stessi conteggi, nessuna scrittura. Lock advisory per le chiamate che scrivono. Ritorna {saved_bandi:{aggiornate,eliminate}, calendar_events:{aggiornati,eliminati,convertiti}, partner_calls:{aggiornate,in_collisione}}. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 3) Sicurezza: nessuna delle due funzioni è eseguibile dai ruoli esposti
--    (Supabase concede EXECUTE di default a PUBLIC); le chiama solo il backend
--    con service_role.
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_bandi_in_uso()
  from public, anon, authenticated;
revoke execute on function public.fn_rimappa_bando_fuso(integer, integer, text, boolean)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0043 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento la rimappatura, RIMAPPATURA_FUSI_MODALITA =
-- spenta: quella nuova chiama le due RPC).
-- 1) drop function public.fn_rimappa_bando_fuso(integer, integer, text, boolean);
--    drop function public.fn_bandi_in_uso();
-- 2) I drop NON annullano le rimappature già scritte. Per riportare le righe di
--    un doppione al bando originale (per esempio se il catalogo separa una
--    fusione), prima spegnere la rimappatura, poi applicare le voci di
--    audit_log con action = 'catalogo.rimappatura_fuso' e
--    payload->>'doppione' = '<id del doppione>', dalla più recente alla più
--    vecchia:
--    - operazione 'eliminata' (saved_bandi, calendar_events): reinserire la
--      riga intera, saltandola se il vincolo di unicità dice che c'è già:
--        insert into public.saved_bandi
--        select * from jsonb_populate_record(null::public.saved_bandi, payload->'prima');
--        insert into public.calendar_events
--        select * from jsonb_populate_record(null::public.calendar_events, payload->'prima');
--    - operazione 'aggiornata': riportare i valori precedenti sulla riga, se è
--      ancora sul master:
--        update public.saved_bandi   -- idem calendar_events
--        set bando_id = (payload->'prima'->>'bando_id')::integer,
--            bando_slug = payload->'prima'->>'bando_slug'
--        where id = (payload->>'id')::uuid and bando_id = (payload->>'master')::integer;
--      per partner_calls anche bando_mancante_dal =
--      (payload->'prima'->>'bando_mancante_dal')::date, e solo se l'azienda non
--      ha nel frattempo un'altra call attiva sul doppione;
--    - operazione 'convertita' (calendar_events): tipo = 'bando', bando_id e
--      bando_slug da payload->'prima' sulla riga payload->>'id', se l'utente
--      non ha già un evento del doppione nello stesso ambito.
-- Le voci di audit_log restano (registro senza FK).
-- ============================================================================
