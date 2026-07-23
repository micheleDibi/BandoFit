-- ============================================================================
-- BandoFit — DB primario, migration 0031: APPARTENENZA e VISIBILITÀ aziende
-- per i membri della famiglia (WP7) + budget AI-check per membro (WP6).
--
-- Due concetti distinti:
--   appartenenza — l'azienda di riferimento del membro
--                  (family_members.company_profile_id, una sola);
--   visibilità   — l'insieme di aziende che il membro può vedere/usare
--                  (family_member_company_access), con INVARIANTE
--                  visibilità ⊇ {appartenenza} (RPC di scrittura + trigger).
-- L'enforcement runtime è nel resolver dell'azienda attiva (backend deps):
-- per un membro ATTIVO l'insieme utile è access ∩ aziende VIVE dell'owner;
-- header fuori insieme → 404; default = appartenenza se viva e visibile,
-- altrimenti la più vecchia dell'insieme, altrimenti nessuna azienda.
-- L'ARCHIVIAZIONE (fn_reconcile_companies) NON è bloccata dalle membership:
-- scatta il fallback del resolver. La soft-DELETE invece sì (company_has_members).
--
-- Budget AI-check (WP6): family_members.ai_check_budget — NULL = illimitato,
-- N ≥ 0 = tetto per CICLO di abbonamento; lo scalo avviene dal pool del
-- titolare al CONSUMO (conteggio righe ai_checks per user_id, colonna 0007
-- già valorizzata con l'attore), mai all'assegnazione. Backfill = 0 per i
-- membri esistenti: nessuno può lanciare finché il titolare non assegna
-- (i figli oggi non possono lanciare AI-check: comportamento preservato).
--
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Backfill come
-- FUNZIONE richiamabile (pattern 0028: il harness di test gira su DB vuoto).
-- Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Colonne su family_members.
-- ----------------------------------------------------------------------------
alter table public.family_members
  add column company_profile_id uuid references public.company_profiles (id),
  add column ai_check_budget integer
    check (ai_check_budget is null or ai_check_budget >= 0);

comment on column public.family_members.company_profile_id is
  'APPARTENENZA: l''azienda di riferimento del membro (una sola, scelta dal titolare all''invito — obbligatoria se l''owner ha più aziende vive). NULL solo se l''owner non ha aziende. La visibilità è un soprainsieme (family_member_company_access).';
comment on column public.family_members.ai_check_budget is
  'Budget AI-check del membro: NULL = illimitato, N >= 0 = tetto per ciclo di abbonamento. Il consumo scala dal pool del titolare al momento del consumo (min(residuo membro, residuo pool)); l''overbooking (somma budget > pool) è permesso per scelta.';

-- ----------------------------------------------------------------------------
-- 2) Visibilità: quali aziende dell'owner il membro può vedere/usare.
-- ----------------------------------------------------------------------------
create table public.family_member_company_access (
  family_member_id   uuid not null references public.family_members (id) on delete cascade,
  company_profile_id uuid not null references public.company_profiles (id) on delete cascade,
  granted_by         uuid,
  created_at         timestamptz not null default now(),
  primary key (family_member_id, company_profile_id)
);

comment on table public.family_member_company_access is
  'VISIBILITÀ per membro della famiglia: l''insieme di aziende dell''owner che il membro può vedere/usare (⊇ appartenenza, trigger). Le righe sopravvivono a demote/reactivate (la riga membership è riusata) e si intersecano a runtime con le aziende VIVE.';

alter table public.family_member_company_access enable row level security;
revoke all on public.family_member_company_access from anon, authenticated;

-- L'invariante ⊇: la riga di visibilità dell'azienda di APPARTENENZA non si
-- cancella (prima si cambia l'appartenenza). Il DELETE in cascata dalla
-- membership passa comunque: a quel punto la riga family_members non c'è più.
create or replace function public.fn_access_protegge_appartenenza()
returns trigger
language plpgsql as $$
begin
  if exists (
    select 1 from public.family_members fm
    where fm.id = old.family_member_id
      and fm.company_profile_id = old.company_profile_id
  ) then
    raise exception 'La visibilità deve includere l''azienda di appartenenza'
      using detail = 'membership_access_required';
  end if;
  return old;
end;
$$;

create trigger access_protegge_appartenenza
  before delete on public.family_member_company_access
  for each row execute function public.fn_access_protegge_appartenenza();

-- ----------------------------------------------------------------------------
-- 3) fn_create_family_member — NUOVA FIRMA (azienda + budget). Un CREATE OR
--    REPLACE con firma diversa creerebbe un OVERLOAD (la versione a 5
--    argomenti resterebbe viva come bypass + ambiguità PGRST203): DROP
--    esplicito, come il precedente 0003:543. Corpo VERBATIM dalla 0003 salvo
--    i punti marcati «0031».
-- ----------------------------------------------------------------------------
drop function public.fn_create_family_member(uuid, uuid, text, text, text);

create function public.fn_create_family_member(
  p_parent_id uuid,
  p_member_id uuid,
  p_denominazione text,
  p_email text,
  p_kind text,
  p_company_id uuid default null,
  p_ai_check_budget integer default null
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
  v_company_id uuid;   -- 0031
  v_vive integer;      -- 0031
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

  -- 0031: risoluzione dell'APPARTENENZA. Obbligatoria (esplicita) se l'owner
  -- ha più aziende vive; con una sola è quella; senza aziende resta NULL.
  select count(*) into v_vive
  from public.company_profiles
  where parent_id = p_parent_id and deleted_at is null and archived_at is null;

  if p_company_id is not null then
    perform 1 from public.company_profiles
    where id = p_company_id and parent_id = p_parent_id
      and deleted_at is null and archived_at is null;
    if not found then
      raise exception 'Azienda non trovata o non disponibile'
        using detail = 'company_not_found';
    end if;
    v_company_id := p_company_id;
  elsif v_vive > 1 then
    raise exception 'Indica a quale azienda collegare questo account'
      using detail = 'company_required';
  elsif v_vive = 1 then
    select id into v_company_id
    from public.company_profiles
    where parent_id = p_parent_id and deleted_at is null and archived_at is null
    order by created_at asc, id asc
    limit 1;
  else
    v_company_id := null;
  end if;

  insert into public.family_members
    (parent_id, member_id, denominazione, invited_email, invite_kind,
     company_profile_id, ai_check_budget)
  values
    (p_parent_id, p_member_id, p_denominazione, lower(trim(p_email)), p_kind,
     v_company_id, p_ai_check_budget)
  returning id into v_membership_id;

  -- 0031: l'appartenenza è sempre anche visibile (invariante ⊇).
  if v_company_id is not null then
    insert into public.family_member_company_access
      (family_member_id, company_profile_id, granted_by)
    values (v_membership_id, v_company_id, p_parent_id)
    on conflict do nothing;
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.invite_created', p_member_id, p_parent_id,
          jsonb_build_object('membership_id', v_membership_id, 'invite_kind', p_kind,
                             'denominazione', p_denominazione,
                             'company_profile_id', v_company_id,
                             'ai_check_budget', p_ai_check_budget));

  return v_membership_id;
end;
$$;

revoke execute on function
  public.fn_create_family_member(uuid, uuid, text, text, text, uuid, integer)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 4) Modifiche del membro da parte del titolare: appartenenza, visibilità,
--    budget. Tre RPC piccole, tutte sotto lock del padre, con audit.
-- ----------------------------------------------------------------------------
create or replace function public.fn_set_member_company(
  p_parent_id uuid,
  p_membership_id uuid,
  p_company_id uuid
)
returns void
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

  perform 1 from public.company_profiles
  where id = p_company_id and parent_id = p_parent_id
    and deleted_at is null and archived_at is null;
  if not found then
    raise exception 'Azienda non trovata o non disponibile'
      using detail = 'company_not_found';
  end if;

  update public.family_members
  set company_profile_id = p_company_id
  where id = p_membership_id;

  -- L'appartenenza è sempre anche visibile (invariante ⊇). La visibilità
  -- della VECCHIA azienda resta: cambiare appartenenza non toglie accessi.
  insert into public.family_member_company_access
    (family_member_id, company_profile_id, granted_by)
  values (p_membership_id, p_company_id, p_parent_id)
  on conflict do nothing;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.member_company_changed', v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id,
                             'company_profile_id', p_company_id,
                             'precedente', v_row.company_profile_id));
end;
$$;

revoke execute on function public.fn_set_member_company(uuid, uuid, uuid)
  from public, anon, authenticated;

create or replace function public.fn_set_member_access(
  p_parent_id uuid,
  p_membership_id uuid,
  p_company_ids uuid[]
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.family_members%rowtype;
  v_valide integer;
begin
  perform 1 from public.profiles where id = p_parent_id for update;

  select * into v_row from public.family_members
  where id = p_membership_id and parent_id = p_parent_id
    and status in ('pending', 'active', 'demoted');
  if not found then
    raise exception 'Account collegato non trovato' using detail = 'member_not_found';
  end if;

  -- Tutte le aziende richieste devono essere VIVE e dell'owner.
  select count(*) into v_valide
  from public.company_profiles
  where id = any(p_company_ids) and parent_id = p_parent_id
    and deleted_at is null and archived_at is null;
  if v_valide <> coalesce(array_length(p_company_ids, 1), 0) then
    raise exception 'Azienda non trovata o non disponibile'
      using detail = 'company_not_found';
  end if;

  -- Invariante ⊇: l'appartenenza non può uscire dalla visibilità.
  if v_row.company_profile_id is not null
     and not (v_row.company_profile_id = any(p_company_ids)) then
    raise exception 'La visibilità deve includere l''azienda di appartenenza'
      using detail = 'membership_access_required';
  end if;

  delete from public.family_member_company_access
  where family_member_id = p_membership_id
    and company_profile_id <> all(p_company_ids);

  insert into public.family_member_company_access
    (family_member_id, company_profile_id, granted_by)
  select p_membership_id, cid, p_parent_id from unnest(p_company_ids) as cid
  on conflict do nothing;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.member_access_changed', v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id,
                             'aziende', coalesce(array_length(p_company_ids, 1), 0)));
end;
$$;

revoke execute on function public.fn_set_member_access(uuid, uuid, uuid[])
  from public, anon, authenticated;

create or replace function public.fn_set_member_budget(
  p_parent_id uuid,
  p_membership_id uuid,
  p_budget integer
)
returns void
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
  if p_budget is not null and p_budget < 0 then
    raise exception 'Budget non valido' using detail = 'budget_non_valido';
  end if;

  update public.family_members
  set ai_check_budget = p_budget
  where id = p_membership_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.member_budget_changed', v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id,
                             'ai_check_budget', p_budget,
                             'precedente', v_row.ai_check_budget));
end;
$$;

revoke execute on function public.fn_set_member_budget(uuid, uuid, integer)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 5) fn_reactivate_family_member — ripara l'appartenenza al rientro: un
--    retrocesso può tornare quando la sua azienda è stata archiviata o
--    cancellata. Corpo VERBATIM dalla 0003 salvo i punti «0031».
-- ----------------------------------------------------------------------------
create or replace function public.fn_reactivate_family_member(p_parent_id uuid, p_membership_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.family_members%rowtype;
  v_limit integer;
  v_company_id uuid;  -- 0031
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

  -- 0031: appartenenza ancora valida? Se l'azienda non è più viva (o non c'è
  -- mai stata) si riassegna alla più vecchia viva dell'owner; senza aziende
  -- vive resta NULL (il resolver dà company_id = NULL, come per l'owner).
  v_company_id := v_row.company_profile_id;
  if v_company_id is null or not exists (
    select 1 from public.company_profiles
    where id = v_company_id and parent_id = p_parent_id
      and deleted_at is null and archived_at is null
  ) then
    select id into v_company_id
    from public.company_profiles
    where parent_id = p_parent_id and deleted_at is null and archived_at is null
    order by created_at asc, id asc
    limit 1;
  end if;

  update public.family_members
  set status = 'active', joined_at = now(), demoted_at = null,
      company_profile_id = v_company_id
  where id = p_membership_id;

  if v_company_id is not null then
    insert into public.family_member_company_access
      (family_member_id, company_profile_id, granted_by)
    values (p_membership_id, v_company_id, p_parent_id)
    on conflict do nothing;
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_parent_id, 'family.member_reactivated', v_row.member_id, p_parent_id,
          jsonb_build_object('membership_id', p_membership_id,
                             'company_profile_id', v_company_id));
end;
$$;

-- ----------------------------------------------------------------------------
-- 6) fn_soft_delete_company — N6: un'azienda con membri collegati (pending,
--    attivi o retrocessi: anche un'appartenenza «dormiente» va riassegnata,
--    non lasciata orfana) non si cancella finché il titolare non riassegna.
--    Corpo VERBATIM dalla 0023 salvo il guard «0031».
-- ----------------------------------------------------------------------------
create or replace function public.fn_soft_delete_company(
  p_owner_id uuid,
  p_company_id uuid
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  -- 0031: prima i membri, poi la cancellazione.
  if exists (
    select 1 from public.family_members
    where company_profile_id = p_company_id
      and status in ('pending', 'active', 'demoted')
  ) then
    raise exception 'Ci sono account collegati a questa azienda: riassegnali prima di rimuoverla'
      using detail = 'company_has_members';
  end if;

  update public.company_profiles
  set deleted_at = now()
  where id = p_company_id
    and parent_id = p_owner_id
    and deleted_at is null;
  if not found then
    raise exception 'Azienda non trovata o già rimossa' using detail = 'company_not_found';
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_owner_id, 'company.soft_deleted', p_owner_id, p_owner_id,
          jsonb_build_object('company_profile_id', p_company_id));
end;
$$;

-- ----------------------------------------------------------------------------
-- 7) fn_create_company — DEROGA a N7 per la PRIMA azienda: i membri di un
--    owner che prima non ne aveva non devono restare ciechi per sempre (la
--    loro appartenenza è NULL). Dalla SECONDA in poi vale N7: la nuova
--    azienda è invisibile ai membri finché non viene concessa.
--    Corpo VERBATIM dalla 0023 salvo il blocco «0031».
-- ----------------------------------------------------------------------------
create or replace function public.fn_create_company(
  p_owner_id uuid,
  p_ragione_sociale text,
  p_partita_iva text
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit integer;
  v_used integer;
  v_company_id uuid;
  v_assegnati integer := 0;  -- 0031
begin
  -- Serializza le operazioni che cambiano il numero di aziende dell'owner.
  perform 1 from public.profiles where id = p_owner_id for update;
  if not found then
    raise exception 'Owner non trovato' using detail = 'owner_not_found';
  end if;

  if p_ragione_sociale is null or char_length(trim(p_ragione_sociale)) = 0 then
    raise exception 'Ragione sociale obbligatoria' using detail = 'ragione_sociale_required';
  end if;
  if p_partita_iva is null or p_partita_iva !~ '^[0-9]{11}$' then
    raise exception 'Partita IVA non valida' using detail = 'partita_iva_invalid';
  end if;

  v_limit := public.fn_effective_max_aziende(p_owner_id);

  select count(*) into v_used
  from public.company_profiles
  where parent_id = p_owner_id and deleted_at is null and archived_at is null;

  if v_used >= v_limit then
    raise exception 'Hai raggiunto il numero massimo di aziende del tuo piano'
      using detail = 'company_limit_reached';
  end if;

  insert into public.company_profiles (parent_id, ragione_sociale, partita_iva)
  values (p_owner_id, trim(p_ragione_sociale), p_partita_iva)
  returning id into v_company_id;

  -- 0031: prima azienda viva dell'owner → assegnala ai membri senza
  -- appartenenza (con visibilità), così ereditano il nuovo contesto.
  if v_used = 0 then
    with membri as (
      update public.family_members
      set company_profile_id = v_company_id
      where parent_id = p_owner_id
        and status in ('pending', 'active', 'demoted')
        and company_profile_id is null
      returning id
    )
    insert into public.family_member_company_access
      (family_member_id, company_profile_id, granted_by)
    select id, v_company_id, p_owner_id from membri
    on conflict do nothing;
    get diagnostics v_assegnati = row_count;
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_owner_id, 'company.created', p_owner_id, p_owner_id,
          jsonb_build_object('company_profile_id', v_company_id,
                             'ragione_sociale', trim(p_ragione_sociale),
                             'membri_assegnati', v_assegnati));

  return v_company_id;
end;
$$;

-- ----------------------------------------------------------------------------
-- 8) Backfill richiamabile (pattern 0028) + verifica. Idempotente: tocca solo
--    le membership senza appartenenza. Budget = 0 (i membri esistenti non
--    possono lanciare AI-check finché il titolare non assegna: comportamento
--    di oggi preservato); visibilità = TUTTE le aziende vive dell'owner
--    (zero regressioni di accesso al cutover).
-- ----------------------------------------------------------------------------
create or replace function public.fn_backfill_famiglia_0031()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_membership integer := 0;
  v_access integer := 0;
  v_budget integer := 0;
begin
  -- Appartenenza = azienda viva più vecchia dell'owner (il default storico
  -- del resolver): per i membri pre-0031 nulla cambia.
  with assegnate as (
    update public.family_members fm
    set company_profile_id = (
      select cp.id from public.company_profiles cp
      where cp.parent_id = fm.parent_id
        and cp.deleted_at is null and cp.archived_at is null
      order by cp.created_at asc, cp.id asc
      limit 1
    )
    where fm.status in ('pending', 'active', 'demoted')
      and fm.company_profile_id is null
      and exists (
        select 1 from public.company_profiles cp2
        where cp2.parent_id = fm.parent_id
          and cp2.deleted_at is null and cp2.archived_at is null
      )
    returning fm.id
  )
  select count(*) into v_membership from assegnate;

  -- Visibilità = tutte le vive correnti dell'owner, per ogni membro corrente.
  with ins as (
    insert into public.family_member_company_access
      (family_member_id, company_profile_id, granted_by)
    select fm.id, cp.id, fm.parent_id
    from public.family_members fm
    join public.company_profiles cp on cp.parent_id = fm.parent_id
    where fm.status in ('pending', 'active', 'demoted')
      and cp.deleted_at is null and cp.archived_at is null
    on conflict do nothing
    returning 1
  )
  select count(*) into v_access from ins;

  -- Budget: 0 per chi non l'ha mai avuto (solo righe NULL: idempotente sui
  -- valori assegnati dopo).
  update public.family_members
  set ai_check_budget = 0
  where status in ('pending', 'active', 'demoted')
    and ai_check_budget is null;
  get diagnostics v_budget = row_count;

  return jsonb_build_object('membership_assegnate', v_membership,
                            'access_create', v_access,
                            'budget_azzerati', v_budget);
end;
$$;

revoke execute on function public.fn_backfill_famiglia_0031()
  from public, anon, authenticated;

select public.fn_backfill_famiglia_0031();

-- Verifica: nessuna membership corrente senza appartenenza il cui owner ha
-- aziende vive (WARNING, non abort: il resolver ha comunque il fallback).
do $$
declare
  v_bad integer;
begin
  select count(*) into v_bad
  from public.family_members fm
  where fm.status in ('pending', 'active', 'demoted')
    and fm.company_profile_id is null
    and exists (
      select 1 from public.company_profiles cp
      where cp.parent_id = fm.parent_id
        and cp.deleted_at is null and cp.archived_at is null
    );
  if v_bad > 0 then
    raise warning 'backfill 0031: % membership senza appartenenza con owner dotato di aziende vive', v_bad;
  end if;
end;
$$;

-- ============================================================================
-- ROLLBACK 0031 (eseguire in transazione).
-- 1) Ripristinare i corpi storici (verbatim dalle migration indicate):
--      fn_reactivate_family_member → 0003 (riattivazione)
--      fn_soft_delete_company      → 0023 §4
--      fn_create_company           → 0023 §4
-- 2) drop function fn_create_family_member(uuid, uuid, text, text, text, uuid, integer);
--    ricreare la firma a 5 argomenti VERBATIM dalla 0003 («Creazione
--    membro/invito») + revoke.
-- 3) drop function fn_backfill_famiglia_0031();
--    drop function fn_set_member_budget(uuid, uuid, integer);
--    drop function fn_set_member_access(uuid, uuid, uuid[]);
--    drop function fn_set_member_company(uuid, uuid, uuid);
-- 4) drop trigger access_protegge_appartenenza on public.family_member_company_access;
--    drop function fn_access_protegge_appartenenza();
--    drop table public.family_member_company_access;
-- 5) alter table public.family_members drop column ai_check_budget;
--    alter table public.family_members drop column company_profile_id;
-- ============================================================================
