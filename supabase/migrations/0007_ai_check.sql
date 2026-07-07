-- ============================================================================
-- BandoFit — DB primario, migration 0007: AI-check di compatibilità
-- azienda ↔ bando.
--
-- Due tabelle:
--  * bando_requirements — CACHE delle estrazioni LLM per bando (requisiti
--    obbligatori + criteri di valutazione + griglia), indipendente
--    dall'azienda: una riga per bando, aggiornata in place quando cambia il
--    contenuto del bando (content_hash) o la versione dei prompt.
--  * ai_checks — i report generati (storico versionato: ogni generazione è
--    una nuova riga `ready`; la più recente è quella in evidenza). Esito,
--    punteggio e tipo di punteggio sono colonne per servire le liste senza
--    parsare il report jsonb.
--
-- Il bando vive nel DB SECONDARIO (catalogo, sola lettura): bando_id è
-- l'id intero di quel DB, senza FK cross-database; slug e titolo sono
-- denormalizzati per mostrare lo storico anche se il bando sparisce.
-- ============================================================================

create table public.bando_requirements (
  id             uuid primary key default gen_random_uuid(),
  bando_id       integer not null,
  bando_slug     text not null,
  content_hash   text not null,
  prompt_version integer not null,
  model          text not null,
  extraction     jsonb not null,
  input_tokens   integer not null default 0 check (input_tokens >= 0),
  output_tokens  integer not null default 0 check (output_tokens >= 0),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

comment on table public.bando_requirements is
  'Cache delle estrazioni AI per bando (requisiti obbligatori, criteri di valutazione, griglia): una riga per bando, riusata da tutte le aziende finché content_hash e prompt_version coincidono.';
comment on column public.bando_requirements.content_hash is
  'hash_bando del catalogo se presente, altrimenti sha256 dell''input serializzato: se cambia, l''estrazione viene rigenerata.';

create unique index bando_requirements_bando_key
  on public.bando_requirements (bando_id);

create table public.ai_checks (
  id                 uuid primary key default gen_random_uuid(),
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  user_id            uuid,
  family_parent_id   uuid not null,
  bando_id           integer not null,
  bando_slug         text not null,
  bando_titolo       text not null,
  status             text not null default 'pending'
                       check (status in ('pending', 'ready', 'error')),
  error_detail       text,
  esito              text
                       check (esito is null or esito in ('ammissibile', 'non_ammissibile', 'da_verificare')),
  punteggio          integer
                       check (punteggio is null or (punteggio between 0 and 100)),
  tipo_punteggio     text
                       check (tipo_punteggio is null or tipo_punteggio in ('stima', 'euristico')),
  report             jsonb,
  model              text,
  prompt_version     integer,
  extraction_cached  boolean not null default false,
  input_tokens       integer not null default 0 check (input_tokens >= 0),
  output_tokens      integer not null default 0 check (output_tokens >= 0),
  cost_cents         integer not null default 0 check (cost_cents >= 0),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  ready_at           timestamptz
);

comment on table public.ai_checks is
  'Report AI-check di compatibilità azienda↔bando: storico versionato (una riga per generazione), report completo in jsonb, esito/punteggio come colonne per le liste.';
comment on column public.ai_checks.user_id is
  'Richiedente (senza FK: lo storico sopravvive alla cancellazione dell''utente).';
comment on column public.ai_checks.family_parent_id is
  'Titolare dell''azienda: chiave di visibilità e di conteggio quota (le quote del piano sono condivise dalla famiglia).';

-- Anti doppia-spesa a livello DB: al massimo UNA analisi in corso per
-- coppia azienda × bando.
create unique index ai_checks_one_pending
  on public.ai_checks (company_profile_id, bando_id)
  where status = 'pending';

create index ai_checks_family_idx
  on public.ai_checks (family_parent_id, created_at desc);
create index ai_checks_pair_idx
  on public.ai_checks (company_profile_id, bando_id, created_at desc);

create trigger trg_bando_requirements_updated_at
  before update on public.bando_requirements
  for each row execute function public.set_updated_at();
create trigger trg_ai_checks_updated_at
  before update on public.ai_checks
  for each row execute function public.set_updated_at();

-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
alter table public.bando_requirements enable row level security;
alter table public.ai_checks enable row level security;
revoke all on public.bando_requirements from anon, authenticated;
revoke all on public.ai_checks from anon, authenticated;
