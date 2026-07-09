-- ============================================================================
-- BandoFit — DB primario, migration 0013: staging dell'import P.IVA.
--
-- L'import diventa a due fasi: ANTEPRIMA (mostra cosa si sta per scrivere) e
-- CONFERMA (scrive). Ma il payload IT-full costa 0,30 € + IVA a chiamata:
-- rifarlo alla conferma raddoppierebbe la spesa. Il payload pagato
-- dall'anteprima resta qui, e la conferma lo consuma senza ripagare.
--
-- Perché una tabella e non una cache in memoria: il backend gira multi-worker
-- e le istanze possono essere più d'una — un draft che non si ritrova alla
-- conferma significa pagare due volte, o rifiutarla a caso.
--
-- Questo NON è un dato aziendale: nessuna pagina lo legge, non entra
-- nell'AI-check né nel punteggio di compatibilità, e scade da solo. Il vincolo
-- «nulla viene salvato finché l'utente non conferma» riguarda
-- company_profiles / company_data / company_people, che l'anteprima non tocca.
--
-- Se l'utente annulla, il draft NON viene cancellato: ha già pagato quel
-- fetch, e riaprendo entro il TTL l'anteprima si ricostruisce gratis.
--
-- Gemella di `company_import_locks` (0005): stessa forma, stessa sicurezza.
-- ============================================================================

create table public.company_import_drafts (
  parent_id    uuid primary key references public.profiles (id) on delete cascade,
  partita_iva  text not null check (partita_iva ~ '^[0-9]{11}$'),
  raw          jsonb not null,
  sandbox      boolean not null default false,
  fetched_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  created_at   timestamptz not null default now()
);

comment on table public.company_import_drafts is
  'Payload IT-full già pagato, in attesa di conferma dell''utente. Una riga per titolare: una nuova anteprima sostituisce la precedente. Scade da sola.';
comment on column public.company_import_drafts.raw is
  'Payload grezzo del provider: non viene MAI esposto al client, esattamente come company_data.raw.';

-- Le letture filtrano sempre su expires_at > now().
create index company_import_drafts_expires_idx
  on public.company_import_drafts (expires_at);

-- ----------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy: anon e
-- authenticated non leggono né scrivono nulla; il backend usa la service_role,
-- che bypassa la RLS) + revoche esplicite.
-- ----------------------------------------------------------------------------
alter table public.company_import_drafts enable row level security;
revoke all on public.company_import_drafts from anon, authenticated;
