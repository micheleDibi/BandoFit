-- ============================================================================
-- BandoFit — DB primario, migration 0008: bandi salvati per utente e
-- calendario personale.
--
-- Entrambe le tabelle referenziano il catalogo del DB SECONDARIO senza FK
-- cross-database (bando_id intero + slug/titolo/scadenza denormalizzati,
-- stesso pattern di ai_checks): i dati sopravvivono alla scomparsa del bando
-- dal catalogo, e la pagina «Salvati» mostra lo snapshot con l'avviso
-- «non più disponibile».
--
-- Le date/ore degli eventi sono di CALENDARIO ITALIANO (wall-clock, colonne
-- date/time senza fuso), coerenti con data_scadenza del catalogo: nessuna
-- conversione di fuso in lettura o scrittura.
-- ============================================================================

create table public.saved_bandi (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  bando_id      integer not null,
  bando_slug    text not null,
  bando_titolo  text not null,
  data_scadenza date,
  stato_bando   text,
  created_at    timestamptz not null default now(),
  constraint saved_bandi_unique unique (user_id, bando_id)
);

comment on table public.saved_bandi is
  'Bandi preferiti per utente: riferimento al catalogo secondario (bando_id senza FK) con snapshot di slug/titolo/scadenza/stato al momento del salvataggio.';
comment on column public.saved_bandi.bando_titolo is
  'Snapshot per la visualizzazione quando il bando non è più nel catalogo (titolo_breve, fallback titolo, fallback slug).';

create index saved_bandi_user_idx
  on public.saved_bandi (user_id, created_at desc);
-- Righe immutabili (solo insert/delete): niente updated_at, come user_preferences.

create table public.calendar_events (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references public.profiles (id) on delete cascade,
  titolo          text not null check (char_length(titolo) between 1 and 200),
  data            date not null,
  tutto_il_giorno boolean not null default true,
  ora_inizio      time,
  ora_fine        time,
  note            text check (note is null or char_length(note) <= 2000),
  tipo            text not null default 'personale'
                    check (tipo in ('personale', 'bando')),
  bando_id        integer,
  bando_slug      text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  -- tipo='bando' ⇔ riferimento al catalogo presente (e viceversa)
  constraint calendar_events_bando_link check (
    (tipo = 'bando' and bando_id is not null and bando_slug is not null)
    or (tipo = 'personale' and bando_id is null and bando_slug is null)
  ),
  -- tutto il giorno ⇒ niente orari; con orari ⇒ ora di inizio obbligatoria
  constraint calendar_events_orari check (
    (tutto_il_giorno and ora_inizio is null and ora_fine is null)
    or (not tutto_il_giorno and ora_inizio is not null)
  ),
  constraint calendar_events_ora_fine check (ora_fine is null or ora_fine > ora_inizio)
);

comment on table public.calendar_events is
  'Eventi del calendario personale: tipo ''personale'' (creati dall''utente) o ''bando'' (scadenza derivata dal catalogo, data non modificabile lato API).';

create index calendar_events_user_month_idx
  on public.calendar_events (user_id, data);

-- Una sola scadenza in calendario per bando per utente (dedup + idempotenza).
create unique index calendar_events_one_per_bando
  on public.calendar_events (user_id, bando_id)
  where tipo = 'bando';

create trigger trg_calendar_events_updated_at
  before update on public.calendar_events
  for each row execute function public.set_updated_at();

-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
alter table public.saved_bandi enable row level security;
alter table public.calendar_events enable row level security;
revoke all on public.saved_bandi from anon, authenticated;
revoke all on public.calendar_events from anon, authenticated;
