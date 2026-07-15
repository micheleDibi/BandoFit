-- ============================================================================
-- BandoFit — DB primario, migration 0024: multi-azienda (piano "Advisor"),
-- abilitazione delle SCRITTURE per N aziende.
--
-- Spedire IN LOCKSTEP col backend della fase 2: da qui in poi un owner può
-- avere PIÙ righe in company_profiles, quindi il codice non scrive/legge più
-- l'azienda per `parent_id` (che non è più univoco) ma per `id`
-- (l'azienda ATTIVA risolta lato server). Prima di questa migration il backend
-- della fase 1 faceva upsert `on_conflict=parent_id`: quel percorso è stato
-- riscritto per `id`, quindi il vincolo UNIQUE può cadere senza rompere nulla.
--
-- Contenuto:
--   1) DROP del vincolo company_profiles_parent_id_key (1 utente = 1 azienda).
--   2) fn_reconcile_companies: allinea le aziende VIVE al limite del piano
--      (archivia le eccedenti / riattiva quelle archiviate da downgrade).
--   3) fn_switch_plan ridefinita: dopo il cambio piano chiama il reconcile
--      (downgrade Advisor → archivia le aziende oltre il nuovo limite).
--
-- I lock/draft di import restano chiavati sull'owner (company_import_locks/
-- drafts per parent_id): in v1 le importazioni di un owner sono serializzate
-- (azione rara, deliberata, a pagamento) — scelta documentata, non un bug.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Un owner può gestire N aziende: cade "1 utente = 1 azienda".
--    IF EXISTS: idempotente se rieseguita.
-- ----------------------------------------------------------------------------
alter table public.company_profiles
  drop constraint if exists company_profiles_parent_id_key;

-- ----------------------------------------------------------------------------
-- 2) Riconciliazione aziende↔limite di piano.
--    Invariante: le aziende VIVE (deleted_at is null and archived_at is null)
--    di un owner sono sempre le `fn_effective_max_aziende` PIÙ VECCHIE tra le
--    non-cancellate. Le eccedenti (più recenti) vengono ARCHIVIATE; se il
--    limite risale, si RIATTIVANO le più vecchie tra le archiviate — così un
--    downgrade seguito da upgrade riporta lo stato di prima, senza perdere dati.
--    (archived_at è impostato solo qui, quindi qualunque riga archiviata e non
--    cancellata è "archiviata da downgrade": riattivarla è sicuro.)
-- ----------------------------------------------------------------------------
create or replace function public.fn_reconcile_companies(p_owner_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit   integer;
  v_changed integer;
begin
  -- Serializza con fn_create_company / altri reconcile dello stesso owner.
  perform 1 from public.profiles where id = p_owner_id for update;
  if not found then
    return;
  end if;

  v_limit := public.fn_effective_max_aziende(p_owner_id);

  with ranked as (
    select id,
           row_number() over (order by created_at asc, id asc) as rn
    from public.company_profiles
    where parent_id = p_owner_id and deleted_at is null
  )
  update public.company_profiles c
  set archived_at = case
                      when r.rn > v_limit then coalesce(c.archived_at, now())
                      else null
                    end
  from ranked r
  where c.id = r.id
    and (
      (r.rn > v_limit  and c.archived_at is null) or      -- da archiviare
      (r.rn <= v_limit and c.archived_at is not null)     -- da riattivare
    );

  get diagnostics v_changed = row_count;
  if v_changed > 0 then
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_owner_id, 'company.reconciled', p_owner_id, p_owner_id,
            jsonb_build_object('limite', v_limit, 'aziende_modificate', v_changed));
  end if;
end;
$$;

comment on function public.fn_reconcile_companies(uuid) is
  'Allinea le aziende vive dell''owner al limite max_aziende: archivia le più recenti oltre il limite, riattiva le archiviate (da downgrade) se il limite risale. Le vive restano le N più vecchie non-cancellate.';

revoke execute on function public.fn_reconcile_companies(uuid) from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 3) fn_switch_plan: identica alla 0003 (adeguamento famiglia al nuovo
--    num_account_aziendali) + chiamata al reconcile delle aziende sul nuovo
--    limite max_aziende. I due assi sono indipendenti (famiglia = posti
--    persona; aziende = numero di aziende gestite): in v1 sono mutuamente
--    esclusivi, ma la RPC li gestisce entrambi senza assumerlo.
-- ----------------------------------------------------------------------------
create or replace function public.fn_switch_plan(p_user_id uuid, p_plan_id bigint)
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

  -- Adeguamento delle AZIENDE al nuovo max_aziende (downgrade Advisor →
  -- archivia le eccedenti; upgrade → riattiva le archiviate).
  perform public.fn_reconcile_companies(p_user_id);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_user_id, 'plan.switched', p_user_id, p_user_id,
          jsonb_build_object('plan_id', p_plan_id,
                             'demoted', v_demoted, 'revoked_pending', v_revoked));

  return jsonb_build_object('demoted', v_demoted, 'revoked_pending', v_revoked);
end;
$$;

revoke execute on function public.fn_switch_plan(uuid, bigint) from public, anon, authenticated;
