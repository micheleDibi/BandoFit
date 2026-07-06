-- ============================================================================
-- BandoFit — DB primario, migration 0005: dati aziendali certificati
-- (openapi.it IT-full), verifica del codice fiscale personale, preferenze
-- per utente e registro dei consumi API a pagamento.
--
-- Il backend recupera su richiesta esplicita dell'utente la visura completa
-- dell'azienda (endpoint IT-full di company.openapi.com) e la persiste QUI:
-- il payload grezzo è la fonte di verità, le persone (cariche/soci) vengono
-- estratte per la consultazione, e ogni chiamata a pagamento viene annotata
-- nel registro consumi (che servirà anche alle quote AI-check).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- profiles: codice fiscale personale + marca temporale della verifica
-- (Verifica CF Agenzia delle Entrate). La verifica decade se il CF cambia.
-- ----------------------------------------------------------------------------
alter table public.profiles
  add column codice_fiscale text
    constraint profiles_codice_fiscale_format
    check (codice_fiscale is null or codice_fiscale ~ '^[A-Z0-9]{16}$'),
  add column cf_verified_at timestamptz;

comment on column public.profiles.codice_fiscale is
  'Codice fiscale della persona (16 caratteri, maiuscolo). Facoltativo.';
comment on column public.profiles.cf_verified_at is
  'Quando il CF è stato verificato all''Anagrafe Tributaria. NULL = non verificato.';

create or replace function public.fn_reset_cf_verification()
returns trigger
language plpgsql
as $$
begin
  -- Se il CF cambia la verifica decade, TRANNE quando lo statement imposta
  -- esplicitamente anche cf_verified_at (flusso di verifica: CF + marca
  -- temporale scritti insieme).
  if new.codice_fiscale is distinct from old.codice_fiscale
     and new.cf_verified_at is not distinct from old.cf_verified_at then
    new.cf_verified_at := null;
  end if;
  return new;
end;
$$;

create trigger trg_profiles_reset_cf_verification
  before update of codice_fiscale on public.profiles
  for each row execute function public.fn_reset_cf_verification();

-- ----------------------------------------------------------------------------
-- company_data: la visura certificata, UNA riga per profilo aziendale
-- (solo l'ultima versione: lo storico dei recuperi vive in audit_log e
-- api_usage_events). raw = payload IT-full completo; derived = valori
-- calcolati al momento dell'import (divisione ATECO, match regione,
-- beneficiari derivati, fasce). CASCADE: cancellare l'azienda cancella
-- anche i dati openapi (uso esclusivo, CGC art. 7.3 / GDPR).
-- ----------------------------------------------------------------------------
create table public.company_data (
  id                 uuid primary key default gen_random_uuid(),
  company_profile_id uuid not null unique
                       references public.company_profiles (id) on delete cascade,
  provider           text not null default 'openapi.it',
  endpoint           text not null default 'IT-full',
  piva_fetched       text not null check (piva_fetched ~ '^[0-9]{11}$'),
  sandbox            boolean not null default false,
  raw                jsonb not null,
  derived            jsonb not null default '{}'::jsonb,
  denominazione      text,
  stato_impresa      text,
  fetch_count        integer not null default 1 check (fetch_count >= 1),
  fetched_at         timestamptz not null default now(),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on table public.company_data is
  'Dati aziendali certificati da openapi.it (IT-full): raw è la fonte di verità, una riga per azienda.';

create trigger trg_company_data_updated_at
  before update on public.company_data
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- company_people: persone estratte dalla visura (cariche, soci, sindaci).
-- Sostituite in blocco a ogni import: nessuna storia, nessuna modifica manuale.
-- ----------------------------------------------------------------------------
create table public.company_people (
  id                       uuid primary key default gen_random_uuid(),
  company_profile_id       uuid not null
                             references public.company_profiles (id) on delete cascade,
  kind                     text not null check (kind in ('manager', 'shareholder', 'auditor')),
  nome                     text,
  cognome                  text,
  denominazione            text,
  codice_fiscale           text,
  data_nascita             date,
  luogo_nascita            text,
  genere                   text,
  ruoli                    jsonb not null default '[]'::jsonb,
  is_legale_rappresentante boolean not null default false,
  quota_percentuale        numeric(6,3),
  data_inizio_carica       date,
  raw                      jsonb not null,
  created_at               timestamptz not null default now()
);

comment on table public.company_people is
  'Cariche, soci e organi di controllo estratti dalla visura openapi. denominazione = soci persona giuridica.';

create index company_people_profile_idx on public.company_people (company_profile_id);

-- ----------------------------------------------------------------------------
-- user_preferences: valori "seguiti" dall''utente OLTRE a quelli reali
-- dell''azienda (es. un ATECO in più), per filtri e future notifiche.
-- PER UTENTE (anche i figli hanno le proprie). ref_id punta alle lookup del
-- DB secondario: nessuna FK cross-database, etichetta denormalizzata.
-- ----------------------------------------------------------------------------
create table public.user_preferences (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles (id) on delete cascade,
  facet      text not null check (facet in
               ('regioni', 'settori', 'beneficiari', 'codici_ateco',
                'tipologie', 'modalita', 'programmi')),
  ref_id     integer not null,
  label      text not null,
  created_at timestamptz not null default now(),
  constraint user_preferences_unique unique (user_id, facet, ref_id)
);

comment on table public.user_preferences is
  'Preferenze di filtro/notifica per utente: una riga per (utente, faccetta, id lookup del catalogo bandi).';

create index user_preferences_user_idx on public.user_preferences (user_id);

-- ----------------------------------------------------------------------------
-- api_usage_events: registro dei consumi API a pagamento. SENZA foreign key
-- (come audit_log): è un registro di spesa e deve sopravvivere alla
-- cancellazione degli utenti. request_meta non contiene mai dati personali
-- in chiaro (CF mascherato). Servirà anche al conteggio delle quote AI-check
-- (service = 'ai_check', provider = 'internal').
-- ----------------------------------------------------------------------------
create table public.api_usage_events (
  id               bigint generated always as identity primary key,
  user_id          uuid,
  family_parent_id uuid,
  provider         text not null,
  service          text not null,
  outcome          text not null check (outcome in ('success', 'error', 'timeout_unknown')),
  cost_cents       integer not null default 0 check (cost_cents >= 0),
  response_status  integer,
  request_meta     jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now()
);

comment on table public.api_usage_events is
  'Registro consumi API a pagamento (openapi.it e futuri servizi interni a quota). Nessuna FK: sopravvive alle cancellazioni.';

create index api_usage_events_family_idx
  on public.api_usage_events (family_parent_id, created_at desc);
create index api_usage_events_service_idx
  on public.api_usage_events (provider, service, created_at desc);

-- ----------------------------------------------------------------------------
-- Lock anti doppia-spesa per l'import: la chiamata HTTP esterna avviene nel
-- backend TRA statement PostgREST, quindi nessun lock di riga può coprirla.
-- Claim atomico: insert, oppure "furto" del lock solo se quello esistente è
-- scaduto. Chiave = profilo del titolare (il company_profile potrebbe non
-- esistere ancora al primo import).
-- ----------------------------------------------------------------------------
create table public.company_import_locks (
  parent_id  uuid primary key references public.profiles (id) on delete cascade,
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

comment on table public.company_import_locks is
  'Lock temporanei per l''import dati azienda: impedisce chiamate a pagamento concorrenti per la stessa azienda.';

create or replace function public.fn_acquire_import_lock(p_parent_id uuid, p_ttl_seconds integer)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ttl integer := greatest(1, least(coalesce(p_ttl_seconds, 120), 600));
  v_ok  boolean;
begin
  insert into public.company_import_locks (parent_id, expires_at)
  values (p_parent_id, now() + make_interval(secs => v_ttl))
  on conflict (parent_id) do update
    set expires_at = excluded.expires_at,
        created_at = now()
    where company_import_locks.expires_at < now()
  returning true into v_ok;
  return coalesce(v_ok, false);
end;
$$;

comment on function public.fn_acquire_import_lock(uuid, integer) is
  'Acquisisce il lock di import per il titolare: true se acquisito, false se un import è già in corso.';

create or replace function public.fn_release_import_lock(p_parent_id uuid)
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.company_import_locks where parent_id = p_parent_id;
$$;

-- ----------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all su ogni tabella (nessuna policy:
-- anon/authenticated non leggono né scrivono nulla; il backend usa la
-- service_role che bypassa la RLS) + revoche esplicite. Supabase concede di
-- default EXECUTE su ogni funzione di public: revochiamo sempre.
-- ----------------------------------------------------------------------------
alter table public.company_data         enable row level security;
alter table public.company_people       enable row level security;
alter table public.user_preferences     enable row level security;
alter table public.api_usage_events     enable row level security;
alter table public.company_import_locks enable row level security;

revoke all on public.company_data         from anon, authenticated;
revoke all on public.company_people       from anon, authenticated;
revoke all on public.user_preferences     from anon, authenticated;
revoke all on public.api_usage_events     from anon, authenticated;
revoke all on public.company_import_locks from anon, authenticated;

revoke execute on function public.fn_reset_cf_verification() from public, anon, authenticated;
revoke execute on function public.fn_acquire_import_lock(uuid, integer) from public, anon, authenticated;
revoke execute on function public.fn_release_import_lock(uuid) from public, anon, authenticated;
