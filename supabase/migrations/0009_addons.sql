-- ============================================================================
-- BandoFit — DB primario, migration 0009: catalogo Add-on.
--
-- Estensioni acquistabili gestite dagli admin, gemelle di subscription_plans:
-- stesse convenzioni (slug unico come identificativo STABILE — aggancerà le
-- funzionalità future —, prezzo in euro numeric(10,2), ordering, is_active).
-- Come i piani, gli add-on NON si eliminano: si disattivano (is_active),
-- così i riferimenti futuri restano validi.
--
-- Nessun seed: il catalogo parte vuoto e l'admin crea gli add-on
-- dall'interfaccia (la sezione lato cliente si nasconde se non ce ne sono).
-- ============================================================================

create table public.addons (
  id          bigint generated always as identity primary key,
  nome        text not null,
  slug        text not null unique,
  descrizione text,
  prezzo      numeric(10,2) not null default 0 check (prezzo >= 0),
  ordering    integer not null default 0,
  is_active   boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

comment on table public.addons is
  'Catalogo add-on acquistabili (gestito dagli admin): lo slug è l''identificativo stabile a cui verranno agganciate le funzionalità; il flusso di acquisto arriverà in seguito.';

create trigger trg_addons_updated_at
  before update on public.addons
  for each row execute function public.set_updated_at();

-- Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche.
alter table public.addons enable row level security;
revoke all on public.addons from anon, authenticated;
