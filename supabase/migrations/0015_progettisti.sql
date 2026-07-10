-- ============================================================================
-- BandoFit — DB primario, migration 0015: identità del progettista.
--
-- Il progettista è un consulente in finanza agevolata: un utente elevato
-- dall'admin (nessun abbonamento dedicato in questa fase — quando esisterà,
-- il gate passerà dal ruolo al piano). Conserva tutte le funzionalità
-- cliente; il ruolo abilita in più l'area progettista.
--
-- La tabella `progettisti` estende il profilo (1:1) con il codice
-- identificativo leggibile «PRG-00001»: assegnato dal sistema alla prima
-- promozione, unico e IMMUTABILE (trigger, non solo convenzione applicativa).
-- Una demozione non cancella la riga: una ri-promozione riusa lo stesso
-- codice, così i riferimenti storici (proposte, email) restano coerenti.
-- ============================================================================

create sequence public.progettista_codice_seq;

create table public.progettisti (
  user_id          uuid primary key references public.profiles (id) on delete cascade,
  codice           text not null unique,
  bio              text check (bio is null or char_length(bio) <= 2000),
  specializzazioni text check (specializzazioni is null or char_length(specializzazioni) <= 500),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

comment on table public.progettisti is
  'Attributi del ruolo progettista (1:1 con profiles). La riga sopravvive alla demozione: il codice resta riservato all''utente.';
comment on column public.progettisti.codice is
  'Codice identificativo leggibile (PRG-00001): assegnato dal sistema, unico, immutabile.';
comment on column public.progettisti.specializzazioni is
  'Descrittiva, non usata nei filtri: predisposta per un futuro routing delle richieste per specializzazione.';

create trigger trg_progettisti_updated_at
  before update on public.progettisti
  for each row execute function public.set_updated_at();

create or replace function public.fn_progettisti_codice_immutabile()
returns trigger
language plpgsql
as $$
begin
  if new.codice is distinct from old.codice then
    raise exception 'Il codice progettista non è modificabile'
      using detail = 'codice_immutabile';
  end if;
  return new;
end;
$$;

create trigger trg_progettisti_codice_immutabile
  before update on public.progettisti
  for each row execute function public.fn_progettisti_codice_immutabile();

-- ---------------------------------------------------------------------------
-- Promozione a progettista (chiamata dal backend per conto dell'admin).
-- Idempotente: ri-promuovere chi ha già una riga in `progettisti` riusa il
-- codice esistente. Errori con detail = codice macchina (pattern 0003).
-- ---------------------------------------------------------------------------
create or replace function public.fn_promote_progettista(p_user_id uuid, p_actor_id uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ruolo_precedente public.user_role;
  v_codice text;
begin
  -- Serializza promozioni concorrenti sullo stesso profilo.
  select role into v_ruolo_precedente
  from public.profiles where id = p_user_id for update;
  if not found then
    raise exception 'Utente non trovato' using detail = 'user_not_found';
  end if;

  update public.profiles set role = 'progettista' where id = p_user_id;

  select codice into v_codice from public.progettisti where user_id = p_user_id;
  if v_codice is null then
    v_codice := 'PRG-' || lpad(nextval('public.progettista_codice_seq')::text, 5, '0');
    insert into public.progettisti (user_id, codice) values (p_user_id, v_codice);
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, payload)
  values (p_actor_id, 'admin.progettista_promoted', p_user_id,
          jsonb_build_object('codice', v_codice,
                             'ruolo_precedente', v_ruolo_precedente));

  return v_codice;
end;
$$;

-- ---------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all + revoche; revoke EXECUTE sulle
-- funzioni (PostgREST esporrebbe ogni funzione di public come RPC).
-- ---------------------------------------------------------------------------
alter table public.progettisti enable row level security;
revoke all on public.progettisti from anon, authenticated;

revoke execute on function public.fn_progettisti_codice_immutabile() from public, anon, authenticated;
revoke execute on function public.fn_promote_progettista(uuid, uuid) from public, anon, authenticated;
