-- ============================================================================
-- BandoFit — DB primario, migration 0004: token per i link email di dominio
--
-- I link nelle email (conferma indirizzo, recupero password, inviti azienda)
-- NON passano più da Supabase/GoTrue: il backend emette token propri
-- (256 bit, salvati SOLO come hash SHA-256, monouso, con scadenza) e i link
-- puntano al dominio BandoFit. La verifica e l'effetto (conferma email,
-- cambio password) avvengono nel backend via Admin API.
-- ============================================================================

create table public.auth_tokens (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references public.profiles (id) on delete cascade,
  purpose    text not null check (purpose in ('confirm_email', 'recovery', 'invite')),
  token_hash text not null unique,
  expires_at timestamptz not null,
  used_at    timestamptz,
  created_at timestamptz not null default now()
);

comment on table public.auth_tokens is
  'Token monouso per i link email di dominio. token_hash = sha256 esadecimale; il token in chiaro non viene mai salvato.';

create index auth_tokens_user_purpose_idx on public.auth_tokens (user_id, purpose);

-- Sicurezza: pattern del repo — RLS deny-all, accesso solo dal backend.
alter table public.auth_tokens enable row level security;
revoke all on public.auth_tokens from anon, authenticated;
