-- ============================================================================
-- BandoFit — DB primario, migration 0021: alert email sui nuovi bandi.
--
-- Quando un bando diventa disponibile, gli utenti con azienda COMPATIBILE
-- (pre-check, punteggio >= 60) lo ricevono via email con il ritardo previsto
-- dal piano. NESSUNA pre-schedulazione: l'idoneità si ricalcola a ogni run
-- giornaliera dallo stato corrente (piano, opt-in, email verificata, bando
-- ancora aperto) — lo stato persistito è solo: ledger invii, impostazioni
-- utente, registro run, suppression list.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) Piani: ritardo dell'alert nuovi-bandi in giorni dalla pubblicazione.
--    NULL = feature esclusa dal piano anche se alert_attivo (il campo
--    alert_giorni_preavviso resta per la futura feature promemoria scadenze:
--    semantica diversa, non si tocca).
-- ---------------------------------------------------------------------------
alter table public.subscription_plans
  add column alert_ritardo_giorni integer
    check (alert_ritardo_giorni is null or alert_ritardo_giorni >= 0);

comment on column public.subscription_plans.alert_ritardo_giorni is
  'Giorni di ritardo dell''alert nuovi-bandi rispetto alla pubblicazione (0 = stesso giorno). NULL = avvisi nuovi-bandi esclusi dal piano.';

update public.subscription_plans set alert_ritardo_giorni = 1  where slug = 'advisor';
update public.subscription_plans set alert_ritardo_giorni = 7  where slug = 'pro';
update public.subscription_plans set alert_ritardo_giorni = 14 where slug = 'smart';
-- 'gratuito' resta null (alert_attivo = false).

-- ---------------------------------------------------------------------------
-- 2) Impostazioni per utente. Riga PIGRA: l'assenza vale «abilitati».
--    unsubscribe_token è la stessa fonte di verità del toggle in-app: il link
--    di disiscrizione nell'email e le Preferenze scrivono la stessa riga.
-- ---------------------------------------------------------------------------
create table public.bando_alert_settings (
  user_id           uuid primary key references public.profiles (id) on delete cascade,
  abilitati         boolean not null default true,
  unsubscribe_token uuid not null default gen_random_uuid() unique,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table public.bando_alert_settings is
  'Opt-in/out degli alert email sui nuovi bandi. Assenza della riga = abilitati; il token di disiscrizione (RFC 8058) punta a questa riga.';

create trigger trg_bando_alert_settings_updated_at
  before update on public.bando_alert_settings
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 3) Ledger degli invii: idempotenza a livello DB. Il claim avviene per
--    INSERT (l'unicità su (user_id, bando_id) è l'arbiter dell'upsert
--    PostgREST): retry e run sovrapposte non possono produrre doppi invii.
--    Stati: in_invio → inviata | fallita (ritentabile fino al tetto) ;
--    incerta = run interrotta tra invio e conferma, MAI ritentata
--    (at-most-once per requisito).
-- ---------------------------------------------------------------------------
create table public.bando_alert_sends (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  bando_id   integer not null,
  bando_slug text,
  stato      text not null default 'in_invio'
               check (stato in ('in_invio', 'inviata', 'fallita', 'incerta')),
  tentativi  integer not null default 1,
  errore     text,
  run_giorno date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint bando_alert_sends_unique unique (user_id, bando_id)
);

comment on table public.bando_alert_sends is
  'Ledger alert inviati: una riga per (utente, bando). Nessun contenuto personale oltre i riferimenti (minimizzazione).';

create index bando_alert_sends_pendenti_idx
  on public.bando_alert_sends (stato)
  where stato in ('in_invio', 'fallita');

create trigger trg_bando_alert_sends_updated_at
  before update on public.bando_alert_sends
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- 4) Registro delle run: la PK sul giorno è la guardia anti-esecuzioni
--    concorrenti (il claim è l'insert: un 23505 = già eseguita) e i contatori
--    sono l'osservabilità richiesta (schedulate/inviate/fallite).
-- ---------------------------------------------------------------------------
create table public.bando_alert_runs (
  giorno          date primary key,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  esito           text,
  bandi_candidati integer,
  destinatari     integer,
  email_inviate   integer,
  email_fallite   integer,
  dettagli        jsonb not null default '{}'::jsonb
);

comment on table public.bando_alert_runs is
  'Una riga per esecuzione giornaliera del job alert: claim per insert (PK giorno) + contatori.';

-- ---------------------------------------------------------------------------
-- 5) Suppression list: hard bounce, reclami spam o esclusioni manuali.
--    Il job non invia MAI a un indirizzo presente qui.
-- ---------------------------------------------------------------------------
create table public.email_suppressions (
  id         bigint generated always as identity primary key,
  email      text not null,
  motivo     text not null check (motivo in ('hard_bounce', 'reclamo', 'manuale')),
  note       text,
  created_at timestamptz not null default now()
);

comment on table public.email_suppressions is
  'Indirizzi a cui non inviare mai (bounce/reclami/manuale). Confronto case-insensitive.';

create unique index email_suppressions_email_key
  on public.email_suppressions (lower(email));

-- ---------------------------------------------------------------------------
-- 6) Verifica email in batch: auth.users non è esposto da PostgREST, quindi
--    una funzione SECURITY DEFINER nel schema public fa da ponte. Ritorna il
--    sottoinsieme di id con email confermata.
-- ---------------------------------------------------------------------------
create or replace function public.fn_email_verificate(p_user_ids uuid[])
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select u.id
    from auth.users u
   where u.id = any(p_user_ids)
     and u.email_confirmed_at is not null;
$$;

revoke execute on function public.fn_email_verificate(uuid[]) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all + revoche ai ruoli client.
-- ---------------------------------------------------------------------------
alter table public.bando_alert_settings enable row level security;
alter table public.bando_alert_sends    enable row level security;
alter table public.bando_alert_runs     enable row level security;
alter table public.email_suppressions   enable row level security;

revoke all on public.bando_alert_settings from anon, authenticated;
revoke all on public.bando_alert_sends    from anon, authenticated;
revoke all on public.bando_alert_runs     from anon, authenticated;
revoke all on public.email_suppressions   from anon, authenticated;
