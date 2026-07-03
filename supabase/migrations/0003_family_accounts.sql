-- ============================================================================
-- BandoFit — DB primario, migration 0003: account famiglia
--
-- Famiglia = account padre (titolare dell'abbonamento) + account figli.
-- Il limite di account è num_account_aziendali del piano e INCLUDE il padre.
-- I figli attivi non hanno un abbonamento proprio: lo ereditano dal padre.
-- Le quote (ai-check, alert) sono condivise a livello famiglia (pooled).
--
-- Nessun backfill necessario: gli utenti esistenti restano account singoli
-- (famiglia implicita di 1).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Enum
-- ---------------------------------------------------------------------------
create type public.family_member_status as enum
  ('pending', 'active', 'demoted', 'removed', 'declined');

create type public.classe_dimensionale as enum ('micro', 'piccola', 'media', 'grande');

create type public.fascia_fatturato as enum
  ('fino_100k', '100k_500k', '500k_2m', '2m_10m', '10m_50m', 'oltre_50m');

-- ---------------------------------------------------------------------------
-- Membership + invito (una riga per "permanenza"; un re-invito dopo una
-- rimozione crea una riga nuova). In entrambi i flussi di invito l'utente
-- auth e il profilo esistono già al momento dell'insert.
-- ---------------------------------------------------------------------------
create table public.family_members (
  id            uuid primary key default gen_random_uuid(),
  parent_id     uuid not null,
  member_id     uuid not null,
  denominazione text not null,
  invited_email text not null,
  invite_kind   text not null check (invite_kind in ('new_user', 'existing_user')),
  status        public.family_member_status not null default 'pending',
  invited_at    timestamptz not null default now(),
  joined_at     timestamptz,
  demoted_at    timestamptz,
  removed_at    timestamptz,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  -- FK con nomi espliciti: servono al backend per disambiguare gli embed
  -- PostgREST (due FK verso profiles).
  constraint family_members_parent_id_fkey
    foreign key (parent_id) references public.profiles (id) on delete cascade,
  constraint family_members_member_id_fkey
    foreign key (member_id) references public.profiles (id) on delete cascade,
  constraint family_members_not_self check (parent_id <> member_id)
);

comment on table public.family_members is
  'Membri (e inviti) delle famiglie. joined_at è la chiave d''ordine per le retrocessioni.';

-- Una sola famiglia "corrente" per utente (pending, active o demoted).
create unique index family_members_one_current
  on public.family_members (member_id)
  where status in ('pending', 'active', 'demoted');

create index family_members_parent_idx
  on public.family_members (parent_id)
  where status in ('pending', 'active', 'demoted');

create trigger trg_family_members_updated_at
  before update on public.family_members
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Dati aziendali: uno per padre (= per famiglia). I riferimenti ad ATECO,
-- settore e regione puntano alle lookup del DB SECONDARIO: si salvano id +
-- copie denormalizzate del testo, nessuna FK cross-database.
-- ---------------------------------------------------------------------------
create table public.company_profiles (
  id                  uuid primary key default gen_random_uuid(),
  parent_id           uuid not null unique references public.profiles (id) on delete cascade,
  ragione_sociale     text not null,
  forma_giuridica     text,
  partita_iva         text not null check (partita_iva ~ '^[0-9]{11}$'),
  codice_fiscale      text,
  ateco_id            integer,
  ateco_codice        text,
  ateco_descrizione   text,
  settore_id          integer,
  settore_nome        text,
  regione_id          integer,
  regione_nome        text,
  anno_fondazione     integer check (anno_fondazione is null or anno_fondazione between 1800 and 2100),
  indirizzo           text,
  comune              text,
  provincia           text,
  cap                 text check (cap is null or cap ~ '^[0-9]{5}$'),
  classe_dimensionale public.classe_dimensionale,
  numero_dipendenti   integer check (numero_dipendenti is null or numero_dipendenti >= 0),
  fascia_fatturato    public.fascia_fatturato,
  pec                 text,
  telefono            text,
  sito_web            text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

comment on table public.company_profiles is
  'Dati aziendali della famiglia: modificabili dal padre, in sola lettura per i figli.';

create trigger trg_company_profiles_updated_at
  before update on public.company_profiles
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Audit log delle operazioni sensibili. Senza FK: le righe devono
-- sopravvivere alla cancellazione degli utenti.
-- ---------------------------------------------------------------------------
create table public.audit_log (
  id               bigint generated always as identity primary key,
  actor_id         uuid,
  action           text not null,
  target_user_id   uuid,
  family_parent_id uuid,
  payload          jsonb not null default '{}'::jsonb,
  created_at       timestamptz not null default now()
);

create index audit_log_family_idx on public.audit_log (family_parent_id, created_at desc);
create index audit_log_target_idx on public.audit_log (target_user_id);

-- ---------------------------------------------------------------------------
-- Provisioning alla registrazione: gli utenti invitati in famiglia
-- (metadata family_invite='true') ricevono SOLO il profilo, senza abbonamento
-- gratuito. La riga family_members la crea il backend via RPC validata.
-- Resta difensiva: non deve mai sollevare eccezioni.
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
begin
  v_is_family_invite :=
    coalesce(new.raw_user_meta_data ->> 'family_invite', '') = 'true';

  insert into public.profiles (id, email, nome, cognome, azienda)
  values (
    new.id,
    new.email,
    coalesce(
      nullif(trim(new.raw_user_meta_data ->> 'nome'), ''),
      nullif(trim(new.raw_user_meta_data ->> 'denominazione'), '')
    ),
    nullif(trim(new.raw_user_meta_data ->> 'cognome'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'azienda'), '')
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
-- Helper interni
-- ---------------------------------------------------------------------------

-- Limite account del piano attivo di un utente (0 se nessun abbonamento attivo).
create or replace function public.fn_family_limit(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select coalesce((
    select sp.num_account_aziendali
    from public.user_subscriptions us
    join public.subscription_plans sp on sp.id = us.plan_id
    where us.user_id = p_user_id and us.status = 'active'
    limit 1
  ), 0);
$$;

-- Membri che occupano un posto (pending + active; il padre conta a parte).
create or replace function public.fn_family_used_slots(p_parent_id uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select count(*)::integer
  from public.family_members
  where parent_id = p_parent_id and status in ('pending', 'active');
$$;

-- Abbonamento gratuito "fresco" per un utente (usato in retrocessione/rimozione).
create or replace function public.fn_grant_free_plan(p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_free_id bigint;
begin
  select id into v_free_id from public.subscription_plans where slug = 'gratuito' limit 1;
  if v_free_id is null then
    raise exception 'Piano gratuito non configurato' using detail = 'free_plan_missing';
  end if;
  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = p_user_id and status = 'active';
  insert into public.user_subscriptions (user_id, plan_id) values (p_user_id, v_free_id);
end;
$$;

-- ---------------------------------------------------------------------------
-- Creazione membro/invito. Tutte le validazioni sotto lock del padre.
-- Errori con detail = codice macchina per la mappatura del backend.
-- ---------------------------------------------------------------------------
create or replace function public.fn_create_family_member(
  p_parent_id uuid,
  p_member_id uuid,
  p_denominazione text,
  p_email text,
  p_kind text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit integer;
  v_membership_id uuid;
  v_target record;
begin
  -- Serializza tutte le operazioni che cambiano la dimensione della famiglia.
  perform 1 from public.profiles where id = p_parent_id for update;
  if not found then
    raise exception 'Account titolare non trovato' using detail = 'parent_not_found';
  end if;

  if p_parent_id = p_member_id then
    raise exception 'Non puoi invitare te stesso' using detail = 'cannot_invite_self';
  end if;

  -- Il padre non può essere a sua volta membro di un'altra famiglia.
  if exists (
    select 1 from public.family_members
    where member_id = p_parent_id and status in ('pending', 'active', 'demoted')
  ) then
    raise exception 'Un account collegato a una famiglia non può crearne una propria'
      using detail = 'parent_in_family';
  end if;

  v_limit := public.fn_family_limit(p_parent_id);
  if v_limit <= 1 then
    raise exception 'Il tuo piano non prevede account aggiuntivi'
      using detail = 'not_family_parent';
  end if;

  select id, role, is_active into v_target
  from public.profiles where id = p_member_id;
  if not found then
    raise exception 'Utente da invitare non trovato' using detail = 'target_not_found';
  end if;
  if v_target.role = 'admin' then
    raise exception 'Non è possibile invitare un amministratore'
      using detail = 'target_is_admin';
  end if;

  -- Il target non deve essere titolare di una famiglia con membri.
  if exists (
    select 1 from public.family_members
    where parent_id = p_member_id and status in ('pending', 'active', 'demoted')
  ) then
    raise exception 'L''utente è già titolare di una famiglia'
      using detail = 'target_is_parent';
  end if;

  -- Né già collegato a una famiglia (questa o un'altra).
  if exists (
    select 1 from public.family_members
    where member_id = p_member_id
      and parent_id = p_parent_id
      and status = 'pending'
  ) then
    raise exception 'C''è già un invito in attesa per questa email'
      using detail = 'invite_already_pending';
  end if;
  if exists (
    select 1 from public.family_members
    where member_id = p_member_id and status in ('pending', 'active', 'demoted')
  ) then
    raise exception 'L''utente fa già parte di una famiglia'
      using detail = 'already_in_family';
  end if;

  -- Posto libero: il padre conta sempre; pending e active occupano un posto.
  if 1 + public.fn_family_used_slots(p_parent_id) + 1 > v_limit then
    raise exception 'Hai raggiunto il numero massimo di account del tuo piano'
      using detail = 'family_limit_reached';
  end if;

  insert into public.family_members
    (parent_id, member_id, denominazione, invited_email, invite_kind)
  values
    (p_parent_id, p_member_id, p_denominazione, lower(trim(p_email)), p_kind)
  returning id into v_membership_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.invite_created', p_member_id, p_parent_id,
          jsonb_build_object('membership_id', v_membership_id, 'invite_kind', p_kind,
                             'denominazione', p_denominazione));

  return v_membership_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- Accettazione invito (chiamata per conto dell'invitato).
-- Cancella l'eventuale abbonamento proprio: da qui in poi eredita dal padre.
-- ---------------------------------------------------------------------------
create or replace function public.fn_accept_invitation(p_membership_id uuid, p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.family_members%rowtype;
  v_limit integer;
  v_active_count integer;
begin
  select * into v_row from public.family_members
  where id = p_membership_id and member_id = p_user_id and status = 'pending';
  if not found then
    raise exception 'Invito non trovato o non più valido'
      using detail = 'invitation_not_found';
  end if;

  perform 1 from public.profiles where id = v_row.parent_id for update;

  -- Ri-lettura SOTTO lock: l'invito può essere stato revocato/consumato tra la
  -- prima lettura e l'acquisizione del lock (revoca del padre o downgrade
  -- concorrenti); senza questa ri-verifica l'accept lo "resusciterebbe".
  select * into v_row from public.family_members
  where id = p_membership_id and member_id = p_user_id and status = 'pending';
  if not found then
    raise exception 'Invito non trovato o non più valido'
      using detail = 'invitation_not_found';
  end if;

  -- Ri-verifica del posto contando i soli attivi: intercetta il caso di un
  -- limite di piano abbassato dall'admin dopo l'invio dell'invito.
  v_limit := public.fn_family_limit(v_row.parent_id);
  select count(*) into v_active_count
  from public.family_members
  where parent_id = v_row.parent_id and status = 'active';
  if 1 + v_active_count + 1 > v_limit then
    raise exception 'La famiglia ha raggiunto il numero massimo di account'
      using detail = 'family_full';
  end if;

  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = p_user_id and status = 'active';

  update public.family_members
  set status = 'active', joined_at = now()
  where id = p_membership_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_user_id, 'family.invite_accepted', p_user_id, v_row.parent_id,
          jsonb_build_object('membership_id', p_membership_id));
end;
$$;

-- ---------------------------------------------------------------------------
-- Rifiuto invito.
-- ---------------------------------------------------------------------------
create or replace function public.fn_decline_invitation(p_membership_id uuid, p_user_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_parent uuid;
begin
  update public.family_members
  set status = 'declined'
  where id = p_membership_id and member_id = p_user_id and status = 'pending'
  returning parent_id into v_parent;
  if not found then
    raise exception 'Invito non trovato o non più valido'
      using detail = 'invitation_not_found';
  end if;

  -- Un invitato "new_user" non ha mai ricevuto un abbonamento (il trigger lo
  -- salta): senza questo grant resterebbe un account senza alcun piano.
  if not exists (
    select 1 from public.user_subscriptions
    where user_id = p_user_id and status = 'active'
  ) then
    perform public.fn_grant_free_plan(p_user_id);
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_user_id, 'family.invite_declined', p_user_id, v_parent,
          jsonb_build_object('membership_id', p_membership_id));
end;
$$;

-- ---------------------------------------------------------------------------
-- Rimozione di un membro (pending/active/demoted) da parte del padre.
-- Ritorna le info per l'eventuale cleanup dell'utente auth lato backend.
-- ---------------------------------------------------------------------------
create or replace function public.fn_remove_family_member(p_parent_id uuid, p_membership_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.family_members%rowtype;
begin
  perform 1 from public.profiles where id = p_parent_id for update;

  select * into v_row from public.family_members
  where id = p_membership_id and parent_id = p_parent_id
    and status in ('pending', 'active', 'demoted');
  if not found then
    raise exception 'Account collegato non trovato' using detail = 'member_not_found';
  end if;

  -- Un membro attivo torna indipendente con un piano gratuito fresco;
  -- pending (mai entrato) e demoted (ha già una sub propria) restano com'erano.
  if v_row.status = 'active' then
    perform public.fn_grant_free_plan(v_row.member_id);
  end if;

  update public.family_members
  set status = 'removed', removed_at = now()
  where id = p_membership_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id,
          case when v_row.status = 'pending'
               then 'family.invite_revoked' else 'family.member_removed' end,
          v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id,
                             'prior_status', v_row.status,
                             'invite_kind', v_row.invite_kind));

  return jsonb_build_object(
    'member_id', v_row.member_id,
    'invite_kind', v_row.invite_kind,
    'prior_status', v_row.status
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Riattivazione di un membro retrocesso (se c'è un posto libero).
-- Rientra come membro più recente (joined_at = now()): scelta deliberata.
-- ---------------------------------------------------------------------------
create or replace function public.fn_reactivate_family_member(p_parent_id uuid, p_membership_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.family_members%rowtype;
  v_limit integer;
begin
  perform 1 from public.profiles where id = p_parent_id for update;

  select * into v_row from public.family_members
  where id = p_membership_id and parent_id = p_parent_id and status = 'demoted';
  if not found then
    raise exception 'Account retrocesso non trovato' using detail = 'member_not_found';
  end if;

  v_limit := public.fn_family_limit(p_parent_id);
  if 1 + public.fn_family_used_slots(p_parent_id) + 1 > v_limit then
    raise exception 'Non ci sono posti liberi nel tuo piano'
      using detail = 'family_limit_reached';
  end if;

  -- Torna a ereditare: l'abbonamento proprio (gratuito) viene cancellato.
  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = v_row.member_id and status = 'active';

  update public.family_members
  set status = 'active', joined_at = now(), demoted_at = null
  where id = p_membership_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.member_reactivated', v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id));
end;
$$;

-- ---------------------------------------------------------------------------
-- Cambio piano, ora family-aware. Nuova firma con ritorno jsonb:
-- CREATE OR REPLACE non può cambiare il return type → DROP + CREATE,
-- e la revoke EXECUTE va ri-applicata alla nuova firma (in coda al file).
--
-- Al downgrade: prima si revocano gli inviti pending (più recenti prima),
-- poi si retrocedono i figli attivi più recenti (joined_at desc, id desc),
-- finché la famiglia rientra nel limite. Il padre non è mai toccato.
-- All'upgrade nessuna riattivazione automatica.
-- ---------------------------------------------------------------------------
drop function public.fn_switch_plan(uuid, bigint);

create function public.fn_switch_plan(p_user_id uuid, p_plan_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit integer;
  v_row public.family_members%rowtype;
  v_demoted jsonb := '[]'::jsonb;
  v_revoked jsonb := '[]'::jsonb;
begin
  perform 1 from public.profiles where id = p_user_id for update;
  if not found then
    raise exception 'Utente non trovato' using detail = 'user_not_found';
  end if;

  -- Un figlio attivo eredita il piano della famiglia: non può cambiarlo.
  if exists (
    select 1 from public.family_members
    where member_id = p_user_id and status = 'active'
  ) then
    raise exception 'Il piano si gestisce sull''account titolare della famiglia'
      using detail = 'child_plan_locked';
  end if;

  select num_account_aziendali into v_limit
  from public.subscription_plans
  where id = p_plan_id and is_active;
  if v_limit is null then
    raise exception 'Piano inesistente o non attivo' using detail = 'plan_not_available';
  end if;

  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = p_user_id and status = 'active';

  insert into public.user_subscriptions (user_id, plan_id)
  values (p_user_id, p_plan_id);

  -- Adeguamento della famiglia al nuovo limite (solo se l'utente è padre).
  -- 1) revoca degli inviti pending, dai più recenti
  for v_row in
    select * from public.family_members
    where parent_id = p_user_id and status = 'pending'
    order by invited_at desc, id desc
  loop
    exit when 1 + public.fn_family_used_slots(p_user_id) <= v_limit;
    update public.family_members
    set status = 'removed', removed_at = now()
    where id = v_row.id;
    v_revoked := v_revoked || jsonb_build_object(
      'membership_id', v_row.id, 'member_id', v_row.member_id,
      'invite_kind', v_row.invite_kind, 'denominazione', v_row.denominazione);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_user_id, 'family.invite_revoked', v_row.member_id, p_user_id,
            jsonb_build_object('membership_id', v_row.id, 'reason', 'plan_downgrade'));
  end loop;

  -- 2) retrocessione dei figli attivi, dai più recenti
  for v_row in
    select * from public.family_members
    where parent_id = p_user_id and status = 'active'
    order by joined_at desc, id desc
  loop
    exit when 1 + public.fn_family_used_slots(p_user_id) <= v_limit;
    perform public.fn_grant_free_plan(v_row.member_id);
    update public.family_members
    set status = 'demoted', demoted_at = now()
    where id = v_row.id;
    v_demoted := v_demoted || jsonb_build_object(
      'membership_id', v_row.id, 'member_id', v_row.member_id,
      'denominazione', v_row.denominazione);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_user_id, 'family.member_demoted', v_row.member_id, p_user_id,
            jsonb_build_object('membership_id', v_row.id, 'reason', 'plan_downgrade'));
  end loop;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_user_id, 'plan.switched', p_user_id, p_user_id,
          jsonb_build_object('plan_id', p_plan_id,
                             'demoted', v_demoted, 'revoked_pending', v_revoked));

  return jsonb_build_object('demoted', v_demoted, 'revoked_pending', v_revoked);
end;
$$;

-- ---------------------------------------------------------------------------
-- Il padre non può essere cancellato finché ha membri collegati
-- (pending/active/demoted). Questo trigger DEVE sollevare eccezioni:
-- la regola "mai raise" vale solo per handle_new_user.
-- ---------------------------------------------------------------------------
create or replace function public.fn_block_parent_delete()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if exists (
    select 1 from public.family_members
    where parent_id = old.id and status in ('pending', 'active', 'demoted')
  ) then
    raise exception
      'Impossibile eliminare l''account: rimuovi prima gli account collegati alla famiglia';
  end if;
  return old;
end;
$$;

create trigger trg_block_parent_delete
  before delete on public.profiles
  for each row execute function public.fn_block_parent_delete();

-- ---------------------------------------------------------------------------
-- Sicurezza: RLS deny-all + revoke sui ruoli client; revoke EXECUTE su tutte
-- le funzioni (PostgREST esporrebbe ogni funzione di public come RPC).
-- ---------------------------------------------------------------------------
alter table public.family_members enable row level security;
alter table public.company_profiles enable row level security;
alter table public.audit_log enable row level security;

revoke all on public.family_members from anon, authenticated;
revoke all on public.company_profiles from anon, authenticated;
revoke all on public.audit_log from anon, authenticated;

revoke execute on function public.fn_family_limit(uuid) from public, anon, authenticated;
revoke execute on function public.fn_family_used_slots(uuid) from public, anon, authenticated;
revoke execute on function public.fn_grant_free_plan(uuid) from public, anon, authenticated;
revoke execute on function public.fn_create_family_member(uuid, uuid, text, text, text) from public, anon, authenticated;
revoke execute on function public.fn_accept_invitation(uuid, uuid) from public, anon, authenticated;
revoke execute on function public.fn_decline_invitation(uuid, uuid) from public, anon, authenticated;
revoke execute on function public.fn_remove_family_member(uuid, uuid) from public, anon, authenticated;
revoke execute on function public.fn_reactivate_family_member(uuid, uuid) from public, anon, authenticated;
-- Nuova firma di fn_switch_plan: la revoke della 0001 non si applica più.
revoke execute on function public.fn_switch_plan(uuid, bigint) from public, anon, authenticated;
