-- ============================================================================
-- BandoFit — DB primario, migration 0018: ricorrenza degli slot (serie).
--
-- Additiva. serie_id raggruppa gli slot creati da una ricorrenza («ogni
-- giorno / giorno feriale / settimana / mese»): nessuna tabella madre, è un
-- puro raggruppamento che serve all'eliminazione in blocco.
--
-- Le OCCORRENZE della ricorrenza le materializza il BROWSER, non il DB: solo
-- il client conosce il fuso dell'utente, e «ogni settimana alle 10:00» deve
-- restare alle 10:00 a muro anche quando cambia l'ora legale (l'offset UTC
-- cambia, l'orario no). Il backend valida ogni occorrenza come uno slot
-- singolo e passa da fn_create_slot_serie.
-- ============================================================================

alter table public.availability_slots
  add column serie_id uuid;

comment on column public.availability_slots.serie_id is
  'Serie di ricorrenza (null = slot singolo). Raggruppamento puro: modificare una singola occorrenza non la stacca dalla serie.';

create index availability_slots_serie_idx
  on public.availability_slots (serie_id)
  where serie_id is not null;

-- ---------------------------------------------------------------------------
-- Creazione di una serie: UNA transazione, blocco begin/exception per riga
-- (savepoint implicito) così le occorrenze che violano l'exclusion constraint
-- anti-sovrapposizione vengono SALTATE invece di far fallire tutto — un bulk
-- insert PostgREST sarebbe all-or-nothing, pessimo per una serie lunga.
-- Vale anche per le sovrapposizioni INTERNE al payload: la prima occorrenza
-- entra, la seconda si scarta con lo stesso meccanismo.
--
-- La validazione degli orari (fine > inizio, durata 15 min–12 h, futuro) è
-- autorevole nel service, PRIMA di questa chiamata: un input malformato qui
-- (check violation, timestamp invalido) abortisce tutto ed è un errore di
-- programmazione, non un caso utente.
-- ---------------------------------------------------------------------------
create or replace function public.fn_create_slot_serie(
  p_progettista_id uuid,
  p_occorrenze jsonb  -- [{"inizio": iso-con-offset, "fine": iso-con-offset}, ...]
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_serie_id uuid := gen_random_uuid();
  v_occ      jsonb;
  v_row      public.availability_slots;
  v_creati   jsonb := '[]'::jsonb;
  v_saltati  integer := 0;
begin
  if p_occorrenze is null
     or jsonb_typeof(p_occorrenze) <> 'array'
     or jsonb_array_length(p_occorrenze) = 0 then
    raise exception 'Nessuna occorrenza da creare' using detail = 'serie_vuota';
  end if;
  -- Tetto difensivo, allineato a MAX_OCCORRENZE_SERIE del backend
  -- (giornaliera per 12 mesi = 367 occorrenze al massimo).
  if jsonb_array_length(p_occorrenze) > 370 then
    raise exception 'Troppe occorrenze nella serie' using detail = 'serie_troppo_lunga';
  end if;

  for v_occ in select value from jsonb_array_elements(p_occorrenze)
  loop
    begin
      insert into public.availability_slots (progettista_id, inizio, fine, serie_id)
      values (p_progettista_id,
              (v_occ ->> 'inizio')::timestamptz,
              (v_occ ->> 'fine')::timestamptz,
              v_serie_id)
      returning * into v_row;
      v_creati := v_creati || jsonb_build_object(
        'id', v_row.id,
        'inizio', v_row.inizio,
        'fine', v_row.fine,
        'serie_id', v_row.serie_id
      );
    exception when exclusion_violation then
      v_saltati := v_saltati + 1;  -- sovrapposta: si salta, non si abortisce
    end;
  end loop;

  -- Nessuna occorrenza entrata: errore esplicito invece di un successo vuoto
  -- (l'eccezione annulla anche l'intera funzione, ma non c'è nulla da salvare).
  if jsonb_array_length(v_creati) = 0 then
    raise exception 'Tutti gli slot della serie si sovrappongono'
      using detail = 'serie_tutta_sovrapposta';
  end if;

  return jsonb_build_object(
    'serie_id', v_serie_id,
    'creati', v_creati,
    'saltati', v_saltati
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Eliminazione di una serie: cancella SOLO gli slot senza prenotazione
-- confermata. FOR UPDATE prima del delete (stessa ragione di fn_update_slot/
-- fn_delete_slot, 0017): in READ COMMITTED il not exists non vedrebbe un
-- booking committato durante l'attesa del lock; dopo il lock il DELETE
-- rivaluta la condizione su uno snapshot fresco. L'ownership è la coppia
-- (serie_id, progettista_id): la serie di un altro «non esiste».
-- ---------------------------------------------------------------------------
create or replace function public.fn_delete_slot_serie(
  p_serie_id uuid,
  p_progettista_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_totale    integer;
  v_eliminati integer;
begin
  select count(*) into v_totale
    from (
      select 1
        from public.availability_slots
        where serie_id = p_serie_id and progettista_id = p_progettista_id
        for update
    ) locked;
  if v_totale = 0 then
    raise exception 'Serie non trovata' using detail = 'serie_not_found';
  end if;

  delete from public.availability_slots s
   where s.serie_id = p_serie_id
     and s.progettista_id = p_progettista_id
     and not exists (
       select 1 from public.consultation_bookings b
        where b.slot_id = s.id and b.stato = 'confermata'
     );
  get diagnostics v_eliminati = row_count;

  return jsonb_build_object(
    'eliminati', v_eliminati,
    'mantenuti', v_totale - v_eliminati
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Sicurezza: pattern del repo — PostgREST esporrebbe ogni funzione di public
-- come RPC, quindi revoke EXECUTE ai ruoli client.
-- ---------------------------------------------------------------------------
revoke execute on function public.fn_create_slot_serie(uuid, jsonb) from public, anon, authenticated;
revoke execute on function public.fn_delete_slot_serie(uuid, uuid) from public, anon, authenticated;
