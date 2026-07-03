-- ============================================================================
-- BandoFit — DB primario, migration 0001: schema iniziale
-- Tabelle: subscription_plans, profiles, user_subscriptions
-- Trigger di provisioning alla registrazione + RPC per il cambio piano.
-- RLS: abilitata su tutto SENZA policy (deny-all).
-- L'accesso ai dati avviene esclusivamente dal backend con service_role.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Enum
-- ---------------------------------------------------------------------------
create type public.user_role as enum ('admin', 'cliente');
create type public.subscription_status as enum ('active', 'cancelled', 'expired');

-- ---------------------------------------------------------------------------
-- Funzione di utilità: updated_at automatico
-- ---------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Piani di abbonamento (annuali). Parametri e prezzi modificabili dall'admin.
-- ---------------------------------------------------------------------------
create table public.subscription_plans (
  id                     bigint generated always as identity primary key,
  nome                   text not null,
  slug                   text not null unique,
  descrizione            text,
  prezzo_annuale         numeric(10, 2) not null default 0 check (prezzo_annuale >= 0),
  ai_check               integer not null default 0 check (ai_check >= 0),
  alert_attivo           boolean not null default false,
  alert_giorni_preavviso integer check (alert_giorni_preavviso is null or alert_giorni_preavviso > 0),
  num_account_aziendali  integer not null default 1 check (num_account_aziendali >= 1),
  ordering               integer not null default 0,
  is_active              boolean not null default true,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  -- se gli alert sono attivi, i giorni di preavviso devono essere valorizzati
  constraint plans_alert_coherence check (not alert_attivo or alert_giorni_preavviso is not null)
);

comment on table public.subscription_plans is
  'Piani di abbonamento annuali di BandoFit. Parametri e prezzi gestibili dall''admin.';

create trigger trg_subscription_plans_updated_at
  before update on public.subscription_plans
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Profili utente (1:1 con auth.users)
-- ---------------------------------------------------------------------------
create table public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  email      text not null,
  nome       text,
  cognome    text,
  azienda    text,
  telefono   text,
  role       public.user_role not null default 'cliente',
  is_active  boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.profiles is
  'Anagrafica utenti della piattaforma. email denormalizzata da auth.users per la ricerca admin.';

create index profiles_email_idx on public.profiles (lower(email));
create index profiles_role_idx on public.profiles (role);

create trigger trg_profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Abbonamenti utente (storico completo; uno solo 'active' per utente)
-- ---------------------------------------------------------------------------
create table public.user_subscriptions (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  plan_id       bigint not null references public.subscription_plans (id),
  status        public.subscription_status not null default 'active',
  data_inizio   date not null default current_date,
  data_scadenza date not null default (current_date + interval '1 year'),
  created_at    timestamptz not null default now()
);

comment on table public.user_subscriptions is
  'Storico abbonamenti. L''indice unico parziale garantisce un solo abbonamento attivo per utente.';

create unique index user_subscriptions_one_active
  on public.user_subscriptions (user_id)
  where status = 'active';

create index user_subscriptions_user_idx on public.user_subscriptions (user_id);
create index user_subscriptions_plan_idx on public.user_subscriptions (plan_id);

-- ---------------------------------------------------------------------------
-- Provisioning alla registrazione: profilo + abbonamento iniziale.
-- DIFENSIVA: non deve mai sollevare eccezioni, altrimenti il signup
-- fallirebbe per tutti gli utenti.
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_plan_id bigint;
begin
  insert into public.profiles (id, email, nome, cognome, azienda)
  values (
    new.id,
    new.email,
    nullif(trim(new.raw_user_meta_data ->> 'nome'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'cognome'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'azienda'), '')
  )
  on conflict (id) do nothing;

  select id into v_plan_id
  from public.subscription_plans
  where slug = coalesce(new.raw_user_meta_data ->> 'plan_slug', 'gratuito')
    and is_active
  limit 1;

  if v_plan_id is null then
    select id into v_plan_id
    from public.subscription_plans
    where slug = 'gratuito'
    limit 1;
  end if;

  if v_plan_id is not null then
    insert into public.user_subscriptions (user_id, plan_id)
    values (new.id, v_plan_id)
    on conflict do nothing;
  end if;

  return new;
exception when others then
  -- Mai bloccare la registrazione: il profilo mancante verrà sanato a mano.
  raise warning 'handle_new_user fallita per utente %: %', new.id, sqlerrm;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------
-- Cambio piano atomico (chiamata dal backend via RPC con service_role).
-- Chiude l'abbonamento attivo e ne apre uno nuovo con durata annuale.
-- ---------------------------------------------------------------------------
create or replace function public.fn_switch_plan(p_user_id uuid, p_plan_id bigint)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not exists (
    select 1 from public.subscription_plans where id = p_plan_id and is_active
  ) then
    raise exception 'Piano % inesistente o non attivo', p_plan_id;
  end if;

  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = p_user_id and status = 'active';

  insert into public.user_subscriptions (user_id, plan_id)
  values (p_user_id, p_plan_id);
end;
$$;

-- ---------------------------------------------------------------------------
-- RLS: deny-all. Nessuna policy: anon e authenticated non accedono a nulla;
-- il backend usa service_role che bypassa la RLS.
-- ---------------------------------------------------------------------------
alter table public.subscription_plans enable row level security;
alter table public.profiles enable row level security;
alter table public.user_subscriptions enable row level security;

-- Difesa in profondità: revoca dei privilegi di default ai ruoli client-side.
revoke all on public.subscription_plans from anon, authenticated;
revoke all on public.profiles from anon, authenticated;
revoke all on public.user_subscriptions from anon, authenticated;
