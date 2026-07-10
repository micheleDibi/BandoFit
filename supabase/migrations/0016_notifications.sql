-- ============================================================================
-- BandoFit — DB primario, migration 0016: notifiche in-app.
--
-- Il canale AFFIDABILE degli eventi applicativi: le email restano best-effort
-- (email_service non solleva mai), la notifica in-app persiste finché
-- l'utente non la legge. Prima tabella del genere nel progetto: non esisteva
-- alcun sistema di notifiche.
--
-- dedup_key è NOT NULL e l'unicità (user_id, dedup_key) è un CONSTRAINT
-- pieno, non un indice parziale: PostgREST può usarlo come arbiter di
-- `on_conflict` (upsert ignore-duplicates), così il retry di un fan-out
-- inserisce solo i destinatari mancanti senza duplicare gli altri.
--
-- MINIMIZZAZIONE: titolo/corpo non contengono dati personali di terzi (solo
-- titoli di bandi, codici progettista, orari). I dettagli si leggono seguendo
-- `url`, dove l'endpoint applica l'autorizzazione live: una notifica
-- recapitata a un progettista non trattiene dati di un cliente che poi
-- esercita il diritto di cancellazione.
-- ============================================================================

create table public.notifications (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  tipo       text not null,
  titolo     text not null check (char_length(titolo) between 1 and 200),
  corpo      text check (corpo is null or char_length(corpo) <= 1000),
  url        text,
  dedup_key  text not null,
  read_at    timestamptz,
  created_at timestamptz not null default now(),
  constraint notifications_dedup unique (user_id, dedup_key)
);

comment on table public.notifications is
  'Notifiche in-app per utente. Idempotenti per (user_id, dedup_key): il canale affidabile degli eventi, le email sono best-effort.';
comment on column public.notifications.tipo is
  'Codice macchina dell''evento, es. consulenza.nuova_richiesta: la UI ci aggancia icone/filtri.';
comment on column public.notifications.url is
  'Deep-link in-app (es. /app/consulenze/{id}): i dettagli si leggono lì, con autorizzazione live.';

create index notifications_user_idx
  on public.notifications (user_id, created_at desc);

-- Il badge conta solo le non lette: indice parziale dedicato.
create index notifications_unread_idx
  on public.notifications (user_id)
  where read_at is null;

-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
alter table public.notifications enable row level security;
revoke all on public.notifications from anon, authenticated;
