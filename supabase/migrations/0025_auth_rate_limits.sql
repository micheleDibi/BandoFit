-- ----------------------------------------------------------------------------
-- Rate limiting DURABLE per gli endpoint auth pubblici (register/recover/
-- resend-confirmation), che sono anonimi e fanno partire email reali.
--
-- Perché a DB e non in-process: il cooldown esistente (auth_service._last_sent)
-- è un dict di processo — si azzera a ogni deploy (`docker compose up -d
-- --build`) e non è condiviso se un giorno uvicorn girasse con più worker. Un
-- attaccante che aspetta un riavvio lo aggira. Stesso ragionamento del claim a
-- DB dello scheduler alert (alert_scheduler.py) e del lock di import: si assume
-- un processo solo, ma la guardia sta comunque nel database.
--
-- Il bucket è OPACO: il backend ci passa un HMAC (pepper nei settings), mai
-- l'IP o l'email in chiaro. Così questa tabella non è né un registro di dati
-- personali né un dizionario di indirizzi attaccabile offline.
--
-- Finestra fissa (non sliding): a un attaccante regala al più un raddoppio a
-- cavallo di due finestre, e in cambio costa una riga e un round-trip invece
-- di una lista di timestamp. Per l'anti-enumerazione è un compromesso onesto.
-- ----------------------------------------------------------------------------
create table public.auth_rate_limits (
  bucket       text primary key,
  window_start timestamptz not null default now(),
  count        integer not null default 0
);

comment on table public.auth_rate_limits is
  'Contatori a finestra per gli endpoint auth pubblici. Il bucket è un HMAC opaco (ip:… / email:… / global): mai IP o email in chiaro. Nessun created_at: la riga viene riscritta a ogni finestra.';

-- Il GC cancella per finestra scaduta: senza indice sarebbe un seq scan su una
-- tabella che cresce con gli IP visti.
create index auth_rate_limits_window_idx on public.auth_rate_limits (window_start);

-- ----------------------------------------------------------------------------
-- Claim atomico del contatore, un solo round-trip. Stessa forma di
-- fn_acquire_import_lock (0005): insert, oppure update che "ruba" la riga se la
-- finestra è scaduta. L'atomicità è dell'UPSERT, quindi due richieste
-- concorrenti sullo stesso bucket non possono contare una volta sola.
--
-- Ritorna TRUE se la richiesta è CONSENTITA (contatore entro il limite).
-- Il conteggio avviene comunque: chi supera il limite continua ad allontanare
-- la propria finestra: è voluto, un attaccante che martella non si sblocca.
-- ----------------------------------------------------------------------------
create or replace function public.fn_consume_auth_rate_limit(
  p_bucket         text,
  p_limit          integer,
  p_window_seconds integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  -- Clamp difensivo sull'input, come fn_acquire_import_lock: 1 giorno è anche
  -- il massimo che il GC qui sotto sa riconoscere come "sicuramente scaduta".
  v_window integer := greatest(1, least(coalesce(p_window_seconds, 3600), 86400));
  v_limit  integer := greatest(1, coalesce(p_limit, 1));
  v_count  integer;
begin
  insert into public.auth_rate_limits (bucket, window_start, count)
  values (p_bucket, now(), 1)
  on conflict (bucket) do update
    set count = case
                  when auth_rate_limits.window_start < now() - make_interval(secs => v_window)
                  then 1
                  else auth_rate_limits.count + 1
                end,
        window_start = case
                  when auth_rate_limits.window_start < now() - make_interval(secs => v_window)
                  then now()
                  else auth_rate_limits.window_start
                end
  returning count into v_count;

  -- GC opportunistico (~1 chiamata su 100): il repo non ha un job di pulizia né
  -- pg_cron, e questa tabella — a differenza di auth_tokens — cresce con ogni
  -- IP visto. Pulire qui costa poco e non richiede infrastruttura.
  if random() < 0.01 then
    perform public.fn_purge_auth_rate_limits(86400);
  end if;

  return v_count <= v_limit;
end;
$$;

comment on function public.fn_consume_auth_rate_limit(text, integer, integer) is
  'Incrementa il contatore del bucket nella finestra e dice se la richiesta è consentita (true) o oltre il limite (false).';

-- Separata dal claim per essere testabile in modo deterministico (il richiamo
-- da fn_consume_auth_rate_limit è probabilistico).
create or replace function public.fn_purge_auth_rate_limits(p_older_than_seconds integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_deleted integer;
begin
  delete from public.auth_rate_limits
   where window_start < now() - make_interval(secs => greatest(1, coalesce(p_older_than_seconds, 86400)));
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

comment on function public.fn_purge_auth_rate_limits(integer) is
  'Cancella i contatori con finestra più vecchia di N secondi. Richiamata opportunisticamente da fn_consume_auth_rate_limit.';

-- ----------------------------------------------------------------------------
-- Ricerca del profilo per indirizzo esatto (auth_service._find_profile_by_email).
--
-- L'indice esistente profiles_email_idx è su lower(email) e serve alle ricerche
-- case-insensitive: NON copre il confronto esatto usato in registrazione, che
-- senza questo indice fa un seq scan. Non è (solo) una questione di velocità: in
-- un seq scan con LIMIT 1 un indirizzo ESISTENTE può uscire alla prima riga
-- utile, mentre uno INESISTENTE costringe a scorrere l'intera tabella. La
-- differenza di tempo è esattamente il bit che la risposta neutra nasconde.
-- ----------------------------------------------------------------------------
create index if not exists profiles_email_exact_idx on public.profiles (email);

-- ----------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
-- esplicite. Qui la revoke execute è particolarmente importante: una funzione
-- di rate limiting eseguibile da anon sarebbe essa stessa un vettore di abuso
-- (chiunque potrebbe bruciare il contatore di un altro).
-- ----------------------------------------------------------------------------
alter table public.auth_rate_limits enable row level security;

revoke all on public.auth_rate_limits from anon, authenticated;

revoke execute on function public.fn_consume_auth_rate_limit(text, integer, integer) from public, anon, authenticated;
revoke execute on function public.fn_purge_auth_rate_limits(integer) from public, anon, authenticated;
