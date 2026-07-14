-- ============================================================================
-- BandoFit — DB primario, migration 0022: posizioni aziendali + telefono E.164.
--
-- La registrazione raccoglie due nuovi dati: il telefono personale (salvato
-- in E.164 dal form) e la posizione in azienda, scelta da una lookup gestita
-- sul pattern di addons (0009): slug come identificativo STABILE — è lo slug
-- che viaggia nello user_metadata, mai l'id (gli id identity variano tra
-- ambienti) — e soft-disable via is_active, mai delete.
--
-- NESSUN backfill: gli utenti esistenti restano a NULL e completano da soli
-- dal Profilo. L'obbligatorietà vive SOLO nella validazione del form di
-- registrazione (client + server), mai come vincolo di schema.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) Lookup delle posizioni aziendali.
-- ---------------------------------------------------------------------------
create table public.job_positions (
  id         bigint generated always as identity primary key,
  nome       text not null,
  slug       text not null unique,
  ordering   integer not null default 0,
  is_active  boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

comment on table public.job_positions is
  'Posizioni aziendali selezionabili alla registrazione e nel profilo. Lo slug è l''identificativo stabile (viaggia nello user_metadata); le voci non si eliminano: si disattivano (is_active).';

create trigger trg_job_positions_updated_at
  before update on public.job_positions
  for each row execute function public.set_updated_at();

-- Seed: ordering a passi di 10 per inserimenti futuri; «Altro» sempre in coda.
insert into public.job_positions (nome, slug, ordering) values
  ('CEO / Amministratore Delegato',  'ceo-ad',                        10),
  ('CFO / Direttore Finanziario',    'cfo',                           20),
  ('CTO / Direttore Tecnico',        'cto',                           30),
  ('COO / Direttore Operativo',      'coo',                           40),
  ('CMO / Direttore Marketing',      'cmo',                           50),
  ('Founder',                        'founder',                       60),
  ('Co-Founder',                     'co-founder',                    70),
  ('Titolare',                       'titolare',                      80),
  ('Direttore Generale',             'direttore-generale',            90),
  ('Direttore di Divisione',         'direttore-divisione',          100),
  ('Responsabile di Reparto',        'responsabile-reparto',         110),
  ('Team Lead',                      'team-lead',                    120),
  ('Project Manager',                'project-manager',              130),
  ('Product Manager',                'product-manager',              140),
  ('Sviluppatore',                   'sviluppatore',                 150),
  ('Ingegnere',                      'ingegnere',                    160),
  ('Designer',                       'designer',                     170),
  ('Analista',                       'analista',                     180),
  ('Consulente',                     'consulente',                   190),
  ('Commerciale / Sales',            'commerciale-sales',            200),
  ('Account Manager',                'account-manager',              210),
  ('Responsabile Marketing',         'responsabile-marketing',       220),
  ('Responsabile HR',                'responsabile-hr',              230),
  ('Responsabile Amministrazione',   'responsabile-amministrazione', 240),
  ('Impiegato',                      'impiegato',                    250),
  ('Assistente',                     'assistente',                   260),
  ('Stagista / Tirocinante',         'stagista-tirocinante',         270),
  ('Libero Professionista',          'libero-professionista',        280),
  ('Altro',                          'altro',                        990);

-- ---------------------------------------------------------------------------
-- 2) Profili: FK alla posizione + testo libero per «Altro».
-- ---------------------------------------------------------------------------
alter table public.profiles
  add column job_position_id bigint references public.job_positions (id),
  add column job_position_altro text;

create index profiles_job_position_idx on public.profiles (job_position_id);

comment on column public.profiles.job_position_id is
  'Posizione aziendale dell''utente. Obbligatoria solo nel FORM di registrazione (nessun vincolo di schema): gli utenti pre-0022 e gli invitati in azienda restano NULL.';
comment on column public.profiles.job_position_altro is
  'Specifica libera della posizione, valorizzata solo quando la posizione scelta è «Altro» (il backend la azzera negli altri casi).';
comment on column public.profiles.telefono is
  'Telefono personale in E.164 (+393471234567) per i valori scritti dalla 0022 in poi; i valori precedenti possono essere testo libero e restano validi finché non modificati.';

-- ---------------------------------------------------------------------------
-- 3) handle_new_user: ripartenza VERBATIM dalla versione 0003 + telefono e
--    posizione dal metadata. Slug ignoto/disattivato → NULL: il signup non
--    si blocca MAI (invariante della 0003 preservata).
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_plan_id bigint;
  v_is_family_invite boolean;
  v_position_id bigint;
  v_position_slug text;
begin
  v_is_family_invite :=
    coalesce(new.raw_user_meta_data ->> 'family_invite', '') = 'true';

  -- Slug assente, ignoto o posizione disattivata → NULL, senza rami d'errore.
  select id, slug into v_position_id, v_position_slug
  from public.job_positions
  where slug = nullif(trim(new.raw_user_meta_data ->> 'job_position_slug'), '')
    and is_active
  limit 1;

  insert into public.profiles (id, email, nome, cognome, azienda,
                               telefono, job_position_id, job_position_altro)
  values (
    new.id,
    new.email,
    coalesce(
      nullif(trim(new.raw_user_meta_data ->> 'nome'), ''),
      nullif(trim(new.raw_user_meta_data ->> 'denominazione'), '')
    ),
    nullif(trim(new.raw_user_meta_data ->> 'cognome'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'azienda'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'telefono'), ''),
    v_position_id,
    case when v_position_slug = 'altro'
         then nullif(trim(new.raw_user_meta_data ->> 'job_position_altro'), '')
    end
  )
  on conflict (id) do nothing;

  if not v_is_family_invite then
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
  end if;

  return new;
exception when others then
  -- Mai bloccare la registrazione: il profilo mancante verrà sanato dal backend.
  raise warning 'handle_new_user fallita per utente %: %', new.id, sqlerrm;
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- 4) Coerenza posizione/testo libero a livello di RIGA: il testo «Altro»
--    sopravvive solo se la posizione della riga è la voce con slug 'altro'.
--    Race-free per costruzione (il trigger vede i valori finali della riga,
--    a differenza di un check read-then-write nel backend) e copre ogni
--    percorso di scrittura: signup, self-heal, PATCH /me.
-- ---------------------------------------------------------------------------
create or replace function public.fn_sync_job_position_altro()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  if new.job_position_altro is not null
     and (new.job_position_id is null
          or not exists (
            select 1 from public.job_positions
            where id = new.job_position_id and slug = 'altro'
          )) then
    new.job_position_altro := null;
  end if;
  return new;
end;
$$;

create trigger trg_profiles_job_position_altro
  before insert or update on public.profiles
  for each row execute function public.fn_sync_job_position_altro();

-- ---------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
-- (handle_new_user e fn_sync_job_position_altro restituiscono trigger: non
-- sono esponibili come RPC, niente revoke, coerente con 0001/0003.)
-- ---------------------------------------------------------------------------
alter table public.job_positions enable row level security;
revoke all on public.job_positions from anon, authenticated;
