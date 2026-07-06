-- ============================================================================
-- BandoFit — DB primario, migration 0006: documenti ufficiali dell'azienda
-- (visure camerali da openapi.it).
--
-- La visura è un DOCUMENTO (PDF dentro uno ZIP, flusso asincrono): qui vive
-- il ciclo di vita della richiesta (pending → ready/error), il riferimento al
-- file nel bucket Storage `company-documents` e il TESTO ESTRATTO dal PDF —
-- oggetto sociale e poteri compresi — che alimenterà l'AI-check.
-- ============================================================================

create table public.company_documents (
  id                 uuid primary key default gen_random_uuid(),
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  kind               text not null check (kind in ('visura')),
  endpoint           text not null,
  provider           text not null default 'openapi.it',
  request_id         text,
  status             text not null default 'pending'
                       check (status in ('pending', 'ready', 'error')),
  error_detail       text,
  file_path          text,
  file_name          text,
  file_size          integer check (file_size is null or file_size >= 0),
  pages              integer check (pages is null or pages >= 0),
  extracted_text     text,
  cost_cents         integer not null default 0 check (cost_cents >= 0),
  sandbox            boolean not null default false,
  requested_by       uuid,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  ready_at           timestamptz
);

comment on table public.company_documents is
  'Documenti ufficiali richiesti a openapi.it (visure camerali): stato della richiesta, file nel bucket Storage e testo estratto dal PDF.';
comment on column public.company_documents.endpoint is
  'Variante che ha accettato la richiesta (ordinaria-societa-capitale / -societa-persone / -impresa-individuale).';
comment on column public.company_documents.extracted_text is
  'Testo del PDF (pypdf): include oggetto sociale e poteri — input per il futuro AI-check.';

create index company_documents_profile_idx
  on public.company_documents (company_profile_id, created_at desc);

-- Anti doppia-spesa a livello DB: al massimo UNA richiesta in corso per
-- azienda e tipo di documento.
create unique index company_documents_one_pending
  on public.company_documents (company_profile_id, kind)
  where status = 'pending';

create trigger trg_company_documents_updated_at
  before update on public.company_documents
  for each row execute function public.set_updated_at();

-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
alter table public.company_documents enable row level security;
revoke all on public.company_documents from anon, authenticated;
