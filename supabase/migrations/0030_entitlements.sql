-- ============================================================================
-- BandoFit — DB primario, migration 0030: motore ENTITLEMENT quantitativi.
--
-- Un'unica formula per i limiti: effettivo = base (piano) + extra (unità di
-- addon "allocativi" possedute in user_addon_inventory), per le risorse:
--   seats     — account della famiglia (num_account_aziendali, include il
--               titolare; l'extra si applica solo se la base > 1);
--   companies — aziende gestibili (coalesce(override, max_aziende, 1);
--               l'extra si applica solo se la base > 1);
--   ai_checks — AI-check per ciclo (ai_check; formula pronta, ma in v1 nessun
--               addon può avere risorsa='ai_checks': vedi CHECK sotto).
-- "Dormienza": se la base non abilita la capability (=1, o =0 per ai_checks)
-- l'extra NON si somma — la capability è del piano, l'addon estende la
-- capacità. Le unità restano possedute e tornano attive con un piano idoneo.
--
-- I risolutori storici fn_family_limit / fn_effective_max_aziende diventano
-- WRAPPER del nuovo fn_entitlement_limit: tutti gli arbitri esistenti
-- (fn_create_family_member, fn_accept_invitation, fn_reactivate_family_member,
-- fn_create_company, fn_reconcile_companies) si aggiornano senza toccarli.
--
-- Include inoltre (piano WP1/WP4/WP5, stessa migration additiva):
--   - purchases.quantita (acquisto addon a quantità; default 1) + backfill dei
--     grant admin esistenti da dettaglio_calcolo;
--   - addons.risorsa + canonizzazione dei due addon allocativi di produzione
--     (profilo-aziendale-aggiuntivo → seats, azienda-aggiuntiva → companies);
--   - fn_reconcile_family (estratta da fn_apply_plan_change, riusata dalla
--     revoca admin di addon seats: riduzione IMMEDIATA, decisione B3);
--   - fn_complete_purchase accredita purchases.quantita; fn_admin_grant_addon
--     persiste la quantità e vieta addon allocativi ai membri attivi;
--     fn_admin_revoke_addon locka il profilo PRIMA e riconcilia per risorsa.
--
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Idempotente dove
-- indicato. Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Catalogo: mappatura addon → risorsa entitlement.
--    Il motore chiavizza su QUESTA colonna, mai sullo slug (che resta stabile
--    per deep-link e snapshot storici). v1: solo seats/companies — un addon
--    ai_checks permanente avrebbe semantica ambigua (boost per ogni ciclo,
--    per sempre) e va progettato a parte.
-- ----------------------------------------------------------------------------
alter table public.addons
  add column risorsa text check (risorsa in ('seats', 'companies'));

comment on column public.addons.risorsa is
  'Risorsa entitlement estesa dall''addon (seats = account famiglia, companies = aziende gestibili); NULL = addon normale (es. consulto). Ogni unità in inventario alza di 1 il limite. Immutabile dopo la creazione, come slug e tipo_fruizione.';

-- Gli allocativi usano il modello a quantità: mai 'permanente' (che è un
-- possesso binario con lock a 1).
alter table public.addons add constraint addons_risorsa_consumabile
  check (risorsa is null or tipo_fruizione = 'consumabile');

-- ----------------------------------------------------------------------------
-- 2) purchases.quantita — unità acquistate/accreditate con questa riga.
--    Bound 1..100 allineato al grant admin. Backfill dei grant esistenti
--    (fin qui la quantità viveva solo in dettaglio_calcolo).
-- ----------------------------------------------------------------------------
alter table public.purchases
  add column quantita integer not null default 1 check (quantita between 1 and 100);

comment on column public.purchases.quantita is
  'Unità dell''oggetto acquistate/accreditate (solo kind addon/addon_admin può superare 1). Gli importi della riga sono già TOTALI (prezzo unitario × quantita).';

update public.purchases
set quantita = greatest(1, least(100, (dettaglio_calcolo ->> 'quantita')::integer))
where kind = 'addon_admin'
  and dettaglio_calcolo ? 'quantita'
  and (dettaglio_calcolo ->> 'quantita') ~ '^[0-9]+$';

-- ----------------------------------------------------------------------------
-- 3) Il risolutore unico. fn_entitlement_detail è la FORMULA (unica, per
--    tutte le risorse); fn_entitlement_limit ne espone l'effettivo;
--    fn_entitlement_extra somma le unità allocative possedute.
--    NB: niente filtro is_active sugli addons — disattivare un addon a
--    catalogo ferma le vendite, non spoglia chi ha già comprato.
-- ----------------------------------------------------------------------------
create or replace function public.fn_entitlement_extra(p_user_id uuid, p_risorsa text)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(sum(i.quantita), 0)::integer
  from public.user_addon_inventory i
  join public.addons a on a.id = i.addon_id
  where i.user_id = p_user_id and a.risorsa = p_risorsa;
$$;

create or replace function public.fn_entitlement_detail(p_user_id uuid, p_risorsa text)
returns table (base integer, extra integer, effettivo integer)
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_base  integer;
  v_extra integer;
begin
  if p_risorsa = 'seats' then
    -- Semantica storica di fn_family_limit: 0 senza abbonamento attivo.
    select coalesce((
      select sp.num_account_aziendali
      from public.user_subscriptions us
      join public.subscription_plans sp on sp.id = us.plan_id
      where us.user_id = p_user_id and us.status = 'active'
      limit 1
    ), 0) into v_base;
    v_extra := case when v_base > 1
                    then public.fn_entitlement_extra(p_user_id, 'seats') else 0 end;

  elsif p_risorsa = 'companies' then
    -- Semantica storica di fn_effective_max_aziende: override > piano > 1.
    select coalesce(
      (select max_aziende_override from public.profiles where id = p_user_id),
      (select sp.max_aziende
         from public.user_subscriptions us
         join public.subscription_plans sp on sp.id = us.plan_id
        where us.user_id = p_user_id and us.status = 'active'
        limit 1),
      1
    ) into v_base;
    v_extra := case when v_base > 1
                    then public.fn_entitlement_extra(p_user_id, 'companies') else 0 end;

  elsif p_risorsa = 'ai_checks' then
    select coalesce((
      select sp.ai_check
      from public.user_subscriptions us
      join public.subscription_plans sp on sp.id = us.plan_id
      where us.user_id = p_user_id and us.status = 'active'
      limit 1
    ), 0) into v_base;
    v_extra := case when v_base > 0
                    then public.fn_entitlement_extra(p_user_id, 'ai_checks') else 0 end;

  else
    raise exception 'Risorsa entitlement sconosciuta: %', p_risorsa
      using detail = 'risorsa_sconosciuta';
  end if;

  return query select v_base, v_extra, v_base + v_extra;
end;
$$;

comment on function public.fn_entitlement_detail(uuid, text) is
  'La formula unica degli entitlement: base (piano attivo dell''utente) + extra (unità addon allocativi in inventario, sommate SOLO se la base abilita la capability — dormienza). Risorse: seats | companies | ai_checks.';

create or replace function public.fn_entitlement_limit(p_user_id uuid, p_risorsa text)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select effettivo from public.fn_entitlement_detail(p_user_id, p_risorsa);
$$;

-- ----------------------------------------------------------------------------
-- 4) Wrapper: i risolutori storici delegano alla formula unica. Firme e
--    semantica invariate per i chiamanti (SQL e Python via RPC).
-- ----------------------------------------------------------------------------
create or replace function public.fn_family_limit(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select public.fn_entitlement_limit(p_user_id, 'seats');
$$;

comment on function public.fn_family_limit(uuid) is
  'Limite account della famiglia (include il titolare): dalla 0030 è un wrapper di fn_entitlement_limit(''seats'') = num_account_aziendali del piano attivo + unità di addon seats (0 senza abbonamento attivo).';

create or replace function public.fn_effective_max_aziende(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select public.fn_entitlement_limit(p_user_id, 'companies');
$$;

comment on function public.fn_effective_max_aziende(uuid) is
  'Limite effettivo di aziende gestibili: dalla 0030 è un wrapper di fn_entitlement_limit(''companies'') = coalesce(override profilo, max_aziende del piano attivo, 1) + unità di addon companies (extra solo se la base > 1).';

-- ----------------------------------------------------------------------------
-- 5) fn_entitlement_snapshot — la fotografia read-only che il backend serve
--    da /me/entitlements (e da cui derivano i numeri di /me/family,
--    /me/aziende e della quota AI-check: zero doppio calcolo).
--    Chiavata sull'OWNER: per un collegato attivo il backend risolve prima
--    il titolare (owner_and_editable) e aggiunge i campi propri del figlio.
-- ----------------------------------------------------------------------------
create or replace function public.fn_entitlement_snapshot(p_user_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_seats     record;
  v_companies record;
  v_ai        record;
  v_sub       record;
  v_seats_usato integer;
  v_comp_usato  integer;
  v_ai_usato    integer := 0;
begin
  select * into v_seats     from public.fn_entitlement_detail(p_user_id, 'seats');
  select * into v_companies from public.fn_entitlement_detail(p_user_id, 'companies');
  select * into v_ai        from public.fn_entitlement_detail(p_user_id, 'ai_checks');

  -- seats: il titolare occupa sempre un posto; pending e active occupano.
  v_seats_usato := 1 + public.fn_family_used_slots(p_user_id);

  select count(*)::integer into v_comp_usato
  from public.company_profiles
  where parent_id = p_user_id and deleted_at is null and archived_at is null;

  -- ai_checks: finestra dell'abbonamento attivo, conteggio pending|ready
  -- (stessa semantica di ai_check_service.get_quota: l'insert è il consumo,
  -- l'errore non conta, fine finestra esclusiva a scadenza+1 giorno).
  select us.data_inizio, us.data_scadenza into v_sub
  from public.user_subscriptions us
  where us.user_id = p_user_id and us.status = 'active'
  limit 1;
  if found then
    select count(*)::integer into v_ai_usato
    from public.ai_checks ac
    where ac.family_parent_id = p_user_id
      and ac.status in ('pending', 'ready')
      and ac.created_at >= v_sub.data_inizio
      and ac.created_at < v_sub.data_scadenza + 1;
  end if;

  return jsonb_build_object(
    'seats', jsonb_build_object(
      'base', v_seats.base, 'extra', v_seats.extra, 'effettivo', v_seats.effettivo,
      'usato', v_seats_usato,
      'residuo', greatest(v_seats.effettivo - v_seats_usato, 0)),
    'companies', jsonb_build_object(
      'base', v_companies.base, 'extra', v_companies.extra, 'effettivo', v_companies.effettivo,
      'usato', v_comp_usato,
      'residuo', greatest(v_companies.effettivo - v_comp_usato, 0)),
    'ai_checks', jsonb_build_object(
      'base', v_ai.base, 'extra', v_ai.extra, 'effettivo', v_ai.effettivo,
      'usato', v_ai_usato,
      'residuo', greatest(v_ai.effettivo - v_ai_usato, 0),
      'periodo_inizio', case when v_sub.data_inizio is null then null
                             else to_jsonb(v_sub.data_inizio) end,
      'periodo_fine',   case when v_sub.data_scadenza is null then null
                             else to_jsonb(v_sub.data_scadenza) end)
  );
end;
$$;

comment on function public.fn_entitlement_snapshot(uuid) is
  'Snapshot read-only delle tre risorse entitlement per l''owner: base/extra/effettivo/usato/residuo (+periodo per ai_checks). Fonte unica dei numeri mostrati dal frontend.';

-- ----------------------------------------------------------------------------
-- 6) fn_reconcile_family — il loop di adeguamento famiglia estratto VERBATIM
--    da fn_apply_plan_change (0026 §9), con limite risolto dalla formula
--    unica, attore e reason parametrizzati. Riusata dalla revoca admin di
--    addon seats (riduzione immediata, decisione B3).
-- ----------------------------------------------------------------------------
create or replace function public.fn_reconcile_family(
  p_owner_id uuid,
  p_actor_id uuid,
  p_reason   text default 'plan_downgrade'
)
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
  -- Serializza con inviti/accettazioni/riattivazioni (stesso lock del padre).
  perform 1 from public.profiles where id = p_owner_id for update;
  if not found then
    return jsonb_build_object('demoted', v_demoted, 'revoked_pending', v_revoked);
  end if;

  v_limit := public.fn_entitlement_limit(p_owner_id, 'seats');

  -- 1) revoca degli inviti pending, dai più recenti
  for v_row in
    select * from public.family_members
    where parent_id = p_owner_id and status = 'pending'
    order by invited_at desc, id desc
  loop
    exit when 1 + public.fn_family_used_slots(p_owner_id) <= v_limit;
    update public.family_members
    set status = 'removed', removed_at = now()
    where id = v_row.id;
    v_revoked := v_revoked || jsonb_build_object(
      'membership_id', v_row.id, 'member_id', v_row.member_id,
      'invite_kind', v_row.invite_kind, 'denominazione', v_row.denominazione);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_actor_id, 'family.invite_revoked', v_row.member_id, p_owner_id,
            jsonb_build_object('membership_id', v_row.id, 'reason', p_reason));
  end loop;

  -- 2) retrocessione dei figli attivi, dai più recenti
  for v_row in
    select * from public.family_members
    where parent_id = p_owner_id and status = 'active'
    order by joined_at desc, id desc
  loop
    exit when 1 + public.fn_family_used_slots(p_owner_id) <= v_limit;
    perform public.fn_grant_free_plan(v_row.member_id);
    update public.family_members
    set status = 'demoted', demoted_at = now()
    where id = v_row.id;
    v_demoted := v_demoted || jsonb_build_object(
      'membership_id', v_row.id, 'member_id', v_row.member_id,
      'denominazione', v_row.denominazione);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_actor_id, 'family.member_demoted', v_row.member_id, p_owner_id,
            jsonb_build_object('membership_id', v_row.id, 'reason', p_reason));
  end loop;

  return jsonb_build_object('demoted', v_demoted, 'revoked_pending', v_revoked);
end;
$$;

comment on function public.fn_reconcile_family(uuid, uuid, text) is
  'Adegua la famiglia al limite seats EFFETTIVO (base+extra): revoca gli inviti pending più recenti, poi retrocede i figli attivi più recenti. Ritorna jsonb {demoted, revoked_pending}. Chiamata da fn_apply_plan_change e da fn_admin_revoke_addon (addon seats).';

-- ----------------------------------------------------------------------------
-- 7) fn_apply_plan_change — riscritta: il loop famiglia vive in
--    fn_reconcile_family e il limite è quello EFFETTIVO, risolto DOPO
--    l'insert del nuovo abbonamento. Validazioni ed esiti invariati.
-- ----------------------------------------------------------------------------
create or replace function public.fn_apply_plan_change(
  p_user_id       uuid,
  p_plan_id       bigint,
  p_data_scadenza date,
  p_actor_id      uuid,
  p_audit_action  text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_fam jsonb;
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

  -- La validazione del piano resta PRIMA di ogni effetto (0026: la SELECT del
  -- limite faceva anche da guardia; qui il limite si risolve dopo, la guardia
  -- resta esplicita).
  perform 1 from public.subscription_plans where id = p_plan_id and is_active;
  if not found then
    raise exception 'Piano inesistente o non attivo' using detail = 'plan_not_available';
  end if;

  update public.user_subscriptions
  set status = 'cancelled'
  where user_id = p_user_id and status = 'active';

  insert into public.user_subscriptions (user_id, plan_id, data_scadenza)
  values (p_user_id, p_plan_id,
          coalesce(p_data_scadenza, current_date + interval '1 year'));

  -- Adeguamento famiglia al limite EFFETTIVO del nuovo assetto (base del
  -- nuovo piano + eventuali addon seats posseduti).
  v_fam := public.fn_reconcile_family(p_user_id, p_actor_id, 'plan_downgrade');

  -- Adeguamento delle AZIENDE al nuovo effettivo (wrapper → formula unica).
  perform public.fn_reconcile_companies(p_user_id);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_actor_id, p_audit_action, p_user_id, p_user_id,
          jsonb_build_object('plan_id', p_plan_id,
                             'data_scadenza', coalesce(p_data_scadenza, current_date + interval '1 year'),
                             'demoted', v_fam -> 'demoted',
                             'revoked_pending', v_fam -> 'revoked_pending'));

  return v_fam;
end;
$$;

-- ----------------------------------------------------------------------------
-- 8) fn_complete_purchase — ramo addon a QUANTITÀ (delta = purchases.quantita,
--    entry unica di ledger: l'indice addon_ledger_purchase_once già lo copre).
--    Corpo VERBATIM dalla 0028 salvo i punti marcati «0030».
-- ----------------------------------------------------------------------------
create or replace function public.fn_complete_purchase(
  p_purchase_id        uuid,
  p_revolut_payment_id text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_p public.purchases%rowtype;
  v_auto_renew boolean;
  v_apply jsonb := null;
  v_nuova_scadenza date;
  v_fruizione text;
begin
  select * into v_p from public.purchases where id = p_purchase_id for update;
  if not found then
    raise exception 'Acquisto inesistente' using detail = 'purchase_not_found';
  end if;

  if v_p.status = 'pagato' then
    return jsonb_build_object('esito', 'gia_pagato');
  end if;
  if v_p.status <> 'in_attesa' then
    return jsonb_build_object('esito', 'pagamento_orfano', 'stato_purchase', v_p.status);
  end if;
  if v_p.kind in ('cambio_admin', 'addon_admin') then
    raise exception 'Un movimento amministrativo non si completa con un pagamento'
      using detail = 'invalid_kind';
  end if;

  if v_p.kind = 'rinnovo' and exists (
    select 1 from public.purchases
    where user_id = v_p.user_id and kind = 'rinnovo' and status = 'pagato'
      and ciclo_rinnovo = v_p.ciclo_rinnovo and id <> v_p.id
  ) then
    update public.purchases
    set status = 'pagato', paid_at = now(), revolut_payment_id = p_revolut_payment_id
    where id = v_p.id;
    return jsonb_build_object('esito', 'pagamento_orfano', 'motivo', 'ciclo_gia_coperto');
  end if;

  update public.purchases
  set status = 'pagato', paid_at = now(), revolut_payment_id = p_revolut_payment_id
  where id = v_p.id;

  if v_p.kind = 'piano' then
    v_apply := public.fn_apply_plan_change(
      v_p.user_id, v_p.plan_id,
      (current_date + interval '1 year')::date,
      v_p.user_id, 'plan.purchased');
    update public.scheduled_plan_changes
    set status = 'annullato', cancelled_at = now()
    where user_id = v_p.user_id and status = 'programmato';
    update public.user_subscriptions
    set auto_renew = coalesce(v_p.auto_renew_scelto, false)
    where user_id = v_p.user_id and status = 'active';

  elsif v_p.kind = 'rinnovo' then
    select auto_renew into v_auto_renew
    from public.user_subscriptions
    where user_id = v_p.user_id and status = 'active';

    v_nuova_scadenza := v_p.ciclo_rinnovo + interval '1 year';
    v_apply := public.fn_apply_plan_change(
      v_p.user_id, v_p.plan_id, v_nuova_scadenza,
      v_p.user_id, 'plan.renewed');
    update public.scheduled_plan_changes
    set status = 'eseguito', executed_at = now()
    where user_id = v_p.user_id and status = 'programmato'
      and to_plan_id = v_p.plan_id;
    update public.user_subscriptions
    set auto_renew = coalesce(v_auto_renew, false),
        grace_until = null,
        renewal_notice_sent_at = null
    where user_id = v_p.user_id and status = 'active';

  elsif v_p.kind = 'addon' then
    select tipo_fruizione into v_fruizione from public.addons where id = v_p.addon_id;
    -- 0030: un permanente resta possesso binario — quantità diversa da 1 è
    -- un'incoerenza a monte: soldi registrati, possesso non applicato, orfano.
    if v_fruizione = 'permanente' and v_p.quantita <> 1 then
      return jsonb_build_object('esito', 'pagamento_orfano', 'motivo', 'quantita_non_valida');
    end if;
    if v_fruizione = 'permanente' and exists (
      select 1 from public.user_addon_inventory
      where user_id = v_p.user_id and addon_id = v_p.addon_id and quantita >= 1
    ) then
      return jsonb_build_object('esito', 'pagamento_orfano', 'motivo', 'addon_gia_posseduto');
    end if;
    -- 0030: si accredita la QUANTITÀ del purchase (entry unica con delta +N).
    perform public.fn_addon_apply_movement(
      v_p.user_id, v_p.addon_id, 'purchase', v_p.quantita, v_p.id, null, v_p.user_id, null);
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (v_p.user_id, 'purchase.completed', v_p.user_id, v_p.user_id,
          jsonb_build_object('purchase_id', v_p.id, 'kind', v_p.kind,
                             'oggetto', v_p.oggetto_slug, 'totale_cents', v_p.totale_cents));

  return jsonb_build_object('esito', 'applicato', 'kind', v_p.kind, 'apply', v_apply);
end;
$$;

-- ----------------------------------------------------------------------------
-- 9) fn_admin_grant_addon — persiste la quantità in colonna e vieta gli addon
--    allocativi ai membri di famiglia ATTIVI (l'extra si somma sull'inventario
--    dell'OWNER: unità accreditate a un figlio attivo resterebbero dormienti
--    e invisibili per sempre). Corpo VERBATIM dalla 0028 salvo i punti «0030».
-- ----------------------------------------------------------------------------
create or replace function public.fn_admin_grant_addon(
  p_admin_id    uuid,
  p_user_id     uuid,
  p_addon_id    bigint,
  p_quantita    integer,
  p_motivazione text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_addon public.addons%rowtype;
  v_purchase_id uuid;
  v_qty integer;
begin
  if nullif(trim(p_motivazione), '') is null then
    raise exception 'La motivazione è obbligatoria' using detail = 'motivation_required';
  end if;
  if p_quantita is null or p_quantita < 1 then
    raise exception 'Quantità non valida' using detail = 'quantita_non_valida';
  end if;
  perform 1 from public.profiles where id = p_user_id;
  if not found then
    raise exception 'Utente non trovato' using detail = 'user_not_found';
  end if;
  select * into v_addon from public.addons where id = p_addon_id and is_active;
  if not found then
    raise exception 'Addon non disponibile' using detail = 'addon_not_available';
  end if;
  if v_addon.tipo_fruizione = 'permanente' then
    if p_quantita <> 1 then
      raise exception 'Un addon permanente si concede in unità singola'
        using detail = 'quantita_non_valida';
    end if;
    if exists (select 1 from public.user_addon_inventory
               where user_id = p_user_id and addon_id = p_addon_id and quantita >= 1) then
      raise exception 'L''utente possiede già questo addon' using detail = 'addon_gia_posseduto';
    end if;
  end if;
  -- 0030: gli addon allocativi si accreditano al titolare, non ai collegati.
  if v_addon.risorsa is not null and exists (
    select 1 from public.family_members
    where member_id = p_user_id and status = 'active'
  ) then
    raise exception 'Gli addon che estendono i limiti si accreditano all''account titolare'
      using detail = 'addon_risorsa_solo_titolare';
  end if;

  insert into public.purchases
    (user_id, kind, status, addon_id, oggetto_slug, oggetto_nome, descrizione,
     imponibile_cents, iva_cents, totale_cents, iva_aliquota,
     quantita, dettaglio_calcolo, actor_admin_id, motivazione)
  values
    (p_user_id, 'addon_admin', 'gratuito', v_addon.id, v_addon.slug, v_addon.nome,
     'Accredito addon da amministratore: ' || v_addon.nome
       || case when p_quantita > 1 then ' × ' || p_quantita else '' end,
     0, 0, 0, 0,
     p_quantita, jsonb_build_object('quantita', p_quantita), p_admin_id, trim(p_motivazione))
  returning id into v_purchase_id;

  v_qty := public.fn_addon_apply_movement(
    p_user_id, p_addon_id, 'admin_grant', p_quantita,
    v_purchase_id, null, p_admin_id, trim(p_motivazione));

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin_id, 'addon.granted', p_user_id, p_user_id,
          jsonb_build_object('addon_id', p_addon_id, 'addon_slug', v_addon.slug,
                             'quantita', p_quantita, 'purchase_id', v_purchase_id,
                             'motivazione', trim(p_motivazione)));

  return jsonb_build_object('purchase_id', v_purchase_id, 'quantita_residua', v_qty);
end;
$$;

-- ----------------------------------------------------------------------------
-- 10) fn_admin_revoke_addon — ordine lock GLOBALE (profilo PRIMA, come tutti
--     gli arbitri; poi la riga inventario) + riduzione IMMEDIATA per risorsa
--     dopo la revoca (B3). Corpo VERBATIM dalla 0028 salvo i punti «0030».
-- ----------------------------------------------------------------------------
create or replace function public.fn_admin_revoke_addon(
  p_admin_id    uuid,
  p_user_id     uuid,
  p_addon_id    bigint,
  p_quantita    integer,
  p_motivazione text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_cur integer;
  v_delta integer;
  v_qty integer;
  v_risorsa text;
  v_fam jsonb := null;
begin
  if nullif(trim(p_motivazione), '') is null then
    raise exception 'La motivazione è obbligatoria' using detail = 'motivation_required';
  end if;
  if p_quantita is null or p_quantita < 1 then
    raise exception 'Quantità non valida' using detail = 'quantita_non_valida';
  end if;

  -- 0030: lock del profilo PRIMA della riga inventario (ordine globale unico
  -- profilo → inventario, lo stesso dei percorsi di consumo/reconcile).
  perform 1 from public.profiles where id = p_user_id for update;
  if not found then
    raise exception 'Utente non trovato' using detail = 'user_not_found';
  end if;

  -- FOR UPDATE: serializza con consumi/grant concorrenti sulla riga.
  select quantita into v_cur from public.user_addon_inventory
  where user_id = p_user_id and addon_id = p_addon_id
  for update;
  if not found or v_cur = 0 then
    raise exception 'Nessuna unità da revocare' using detail = 'niente_da_revocare';
  end if;

  -- Clamp al residuo: non si revocano unità già consumate.
  v_delta := least(p_quantita, v_cur);

  v_qty := public.fn_addon_apply_movement(
    p_user_id, p_addon_id, 'admin_revoke', -v_delta, null, null, p_admin_id,
    trim(p_motivazione) || case when v_delta < p_quantita
      then format(' [richieste %s, revocate %s: residuo insufficiente]', p_quantita, v_delta)
      else '' end);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin_id, 'addon.revoked', p_user_id, p_user_id,
          jsonb_build_object('addon_id', p_addon_id, 'quantita_revocata', v_delta,
                             'motivazione', trim(p_motivazione)));

  -- 0030: riduzione IMMEDIATA (B3) se l'addon era allocativo — l'effettivo è
  -- appena sceso, la riconciliazione è la stessa del downgrade di piano.
  select risorsa into v_risorsa from public.addons where id = p_addon_id;
  if v_risorsa = 'seats' then
    v_fam := public.fn_reconcile_family(p_user_id, p_admin_id, 'addon_revoked');
  elsif v_risorsa = 'companies' then
    perform public.fn_reconcile_companies(p_user_id);
  end if;

  return jsonb_build_object('quantita_revocata', v_delta, 'quantita_residua', v_qty,
                            'reconcile_family', v_fam);
end;
$$;

-- ----------------------------------------------------------------------------
-- 11) Canonizzazione dei due addon allocativi di PRODUZIONE (creati da console
--     admin: B1). Funzione richiamabile (pattern 0028: il harness di test gira
--     su DB vuoto e la invoca dopo aver inscenato le righe). Se una riga manca
--     la crea INATTIVA (compare in catalogo solo quando l'admin fissa prezzo e
--     la attiva). Slug INVARIATI: il motore usa la colonna risorsa.
-- ----------------------------------------------------------------------------
create or replace function public.fn_canonizza_addon_0030()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  r record;
  v_aggiornati integer := 0;
  v_creati     integer := 0;
begin
  for r in
    select * from (values
      ('profilo-aziendale-aggiuntivo', 'seats',
       'Account collegato aggiuntivo',
       'Un posto in più nella tua Azienda: invita un altro account collegato che lavora con il tuo stesso abbonamento. Ogni unità acquistata alza di uno il limite di account del tuo piano.',
       200),
      ('azienda-aggiuntiva', 'companies',
       'Azienda aggiuntiva',
       'Gestisci un''azienda in più oltre a quelle incluse nel tuo piano: dossier, preferenze, AI-check e alert dedicati. Ogni unità acquistata alza di uno il limite di aziende gestibili.',
       210)
    ) as t(slug, risorsa, nome, descrizione, ordering)
  loop
    update public.addons
    set risorsa = r.risorsa,
        nome = r.nome,
        descrizione = r.descrizione,
        tipo_fruizione = 'consumabile'   -- difesa del CHECK: la console non lo imposta
    where slug = r.slug;
    if found then
      v_aggiornati := v_aggiornati + 1;
    else
      insert into public.addons (nome, slug, descrizione, ordering, is_active, tipo_fruizione, risorsa)
      values (r.nome, r.slug, r.descrizione, r.ordering, false, 'consumabile', r.risorsa);
      v_creati := v_creati + 1;
    end if;
  end loop;

  return jsonb_build_object('aggiornati', v_aggiornati, 'creati', v_creati);
end;
$$;

select public.fn_canonizza_addon_0030();

-- Verifica: in produzione le due righe DEVONO esistere già (B1) — se il ramo
-- INSERT è scattato gli slug non combaciano: WARNING, non abort (la migration
-- resta valida, gli addon veri vanno riconciliati a mano dalla console).
do $$
declare
  v jsonb;
begin
  -- La fn è idempotente: richiamarla qui per leggere i contatori rifarebbe
  -- gli UPDATE; si verifica invece lo stato risultante.
  select jsonb_build_object(
    'inattivi', count(*) filter (where not is_active),
    'totali', count(*)
  ) into v
  from public.addons
  where slug in ('profilo-aziendale-aggiuntivo', 'azienda-aggiuntiva');
  if (v ->> 'totali')::integer <> 2 then
    raise exception 'canonizzazione 0030: attesi 2 addon allocativi, trovati %', v ->> 'totali';
  end if;
  if (v ->> 'inattivi')::integer > 0 then
    raise warning 'canonizzazione 0030: % addon creati ora come INATTIVI — gli slug attesi non esistevano nel catalogo: verificare in console (B1) e riconciliare', (v ->> 'inattivi')::integer;
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 12) Permessi: pattern repo (nessuna esecuzione diretta dai ruoli esposti).
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_entitlement_extra(uuid, text)    from public, anon, authenticated;
revoke execute on function public.fn_entitlement_detail(uuid, text)   from public, anon, authenticated;
revoke execute on function public.fn_entitlement_limit(uuid, text)    from public, anon, authenticated;
revoke execute on function public.fn_entitlement_snapshot(uuid)       from public, anon, authenticated;
revoke execute on function public.fn_reconcile_family(uuid, uuid, text) from public, anon, authenticated;
revoke execute on function public.fn_canonizza_addon_0030()           from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0030 (eseguire in transazione).
-- 1) Ripristinare i corpi storici (verbatim dalle migration indicate):
--      fn_family_limit            → 0003 «Helper interni»
--      fn_effective_max_aziende   → 0023 §4
--      fn_apply_plan_change       → 0026 §9
--      fn_complete_purchase       → 0028 §7
--      fn_admin_grant_addon       → 0028 §8
--      fn_admin_revoke_addon      → 0028 §8
-- 2) drop function fn_canonizza_addon_0030();
--    drop function fn_reconcile_family(uuid, uuid, text);
--    drop function fn_entitlement_snapshot(uuid);
--    drop function fn_entitlement_limit(uuid, text);
--    drop function fn_entitlement_detail(uuid, text);
--    drop function fn_entitlement_extra(uuid, text);
-- 3) alter table public.purchases drop column quantita;
--    (le quantità dei grant admin restano leggibili da dettaglio_calcolo)
-- 4) alter table public.addons drop constraint addons_risorsa_consumabile;
--    alter table public.addons drop column risorsa;
-- 5) Nome/descrizione dei due addon: ripristinare A MANO dalla console con i
--    testi annotati prima della migration (non sono nel repo — B1). Le righe
--    eventualmente CREATE dalla canonizzazione (inattive) si possono
--    disattivare/lasciare: gli addon non si eliminano (0009).
-- ============================================================================
