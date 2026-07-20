-- ============================================================================
-- BandoFit — DB primario, migration 0028: inventario addon a QUANTITÀ + LEDGER.
--
-- Sostituisce il modello riga-per-credito di user_addons (0026) con:
--   1) addons.tipo_fruizione (consumabile a quantità | permanente binario);
--   2) user_addon_inventory  — saldo materializzato per (utente, addon);
--   3) addon_ledger          — registro APPEND-ONLY dei movimenti (il saldo è
--      sempre ricostruibile da qui: quantita = sum(delta));
--   4) purchases.kind += 'addon_admin' (grant gratuito da admin, riga a 0 €);
--   5) RPC per: applicare un movimento (unico punto di scrittura), creare una
--      richiesta di consulto consumando 1 unità in modo ATOMICO, grant/revoca
--      admin, completare un acquisto addon; + backfill dallo storico.
--
-- user_addons NON viene droppata: resta congelata (sola lettura storica) come
-- rete di rollback. Nessun percorso la scrive più dopo questa migration.
--
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Idempotente dove
-- indicato. Rollback documentato in coda al file.
--
-- Decisioni di prodotto (2026-07-20): gating consulto attivo da subito
-- (pilotato dal catalogo: la riga è già a pagamento in produzione); NESSUN
-- rimborso automatico (il tipo ledger 'refund' è predisposto ma non usato:
-- una restituzione la fa l'admin col grant); addon permanenti solo predisposti.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Catalogo: come si fruisce un addon.
-- ----------------------------------------------------------------------------
alter table public.addons
  add column if not exists tipo_fruizione text not null default 'consumabile'
    check (tipo_fruizione in ('consumabile', 'permanente'));

comment on column public.addons.tipo_fruizione is
  'consumabile = unità a quantità (si compra N volte, si consuma); permanente = possesso binario (0 o 1). Immutabile dopo la creazione (come lo slug): cambiarlo con inventario circolante ha semantica indefinita.';

-- L'addon consulto è consumabile. NON si tocca prezzo/tipo_prezzo/nome: quelli
-- li gestisce l'admin da AdminAddon; il gating si accende da sé quando la riga
-- è consumabile AND tipo_prezzo='importo' AND prezzo>0.
update public.addons set tipo_fruizione = 'consumabile' where slug = 'consulto-esperto';

-- ----------------------------------------------------------------------------
-- 2) purchases: nuovo kind 'addon_admin' + vincolo gratuito generalizzato.
--    Il grant addon crea una riga a 0 € nello storico, gemella di cambio_admin.
-- ----------------------------------------------------------------------------
alter table public.purchases drop constraint if exists purchases_kind_check;
alter table public.purchases add constraint purchases_kind_check
  check (kind in ('piano', 'rinnovo', 'addon', 'cambio_admin', 'addon_admin'));

alter table public.purchases drop constraint if exists purchases_cambio_admin_coerente;
alter table public.purchases add constraint purchases_gratuito_admin_coerente check (
  (kind in ('cambio_admin', 'addon_admin') and status = 'gratuito'
     and actor_admin_id is not null and motivazione is not null
     and totale_cents = 0
     and (kind <> 'addon_admin' or addon_id is not null))
  or (kind not in ('cambio_admin', 'addon_admin') and status <> 'gratuito')
);

-- ----------------------------------------------------------------------------
-- 3) Inventario a quantità: saldo materializzato per (utente, addon).
--    CACHE del ledger: si scrive SOLO via fn_addon_apply_movement, mai a mano.
--    Il check(quantita >= 0) è l'arbitro del consumo (23514 → esaurito).
-- ----------------------------------------------------------------------------
create table public.user_addon_inventory (
  user_id    uuid    not null references public.profiles (id) on delete cascade,
  addon_id   bigint  not null references public.addons (id),
  quantita   integer not null default 0 check (quantita >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, addon_id)
);

comment on table public.user_addon_inventory is
  'Saldo corrente per (utente, addon): quantita = sum(addon_ledger.delta). Cache materializzata, scritta solo via fn_addon_apply_movement. Per i permanenti quantita in {0,1} (fatto rispettare da RPC/checkout).';

create trigger user_addon_inventory_updated_at
  before update on public.user_addon_inventory
  for each row execute function public.set_updated_at();

alter table public.user_addon_inventory enable row level security;
revoke all on public.user_addon_inventory from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 4) Ledger append-only. user_id SENZA FK (come purchases/api_usage_events: il
--    registro sopravvive alla cancellazione dell'utente; l'inventario invece
--    cade in cascade). Il saldo è sempre ricostruibile da qui.
-- ----------------------------------------------------------------------------
create table public.addon_ledger (
  id          bigint generated always as identity primary key,
  user_id     uuid    not null,
  addon_id    bigint  not null references public.addons (id),
  tipo        text    not null check (tipo in
                ('purchase', 'admin_grant', 'consume', 'refund', 'admin_revoke')),
  delta       integer not null,
  purchase_id uuid    references public.purchases (id),
  request_id  uuid,   -- consultation_requests.id, SENZA FK (muore col cascade azienda)
  actor_id    uuid,   -- chi ha causato il movimento (utente o admin)
  note        text,
  created_at  timestamptz not null default now(),
  constraint addon_ledger_delta_coerente check (
    (tipo in ('purchase', 'admin_grant') and delta > 0)
    or (tipo = 'consume'      and delta = -1)
    or (tipo = 'refund'       and delta =  1)
    or (tipo = 'admin_revoke' and delta <  0)
  ),
  constraint addon_ledger_riferimenti check (
    (tipo = 'purchase'     and purchase_id is not null)
    or (tipo = 'admin_grant'  and purchase_id is not null and actor_id is not null and note is not null)
    or (tipo in ('consume', 'refund') and request_id is not null)
    or (tipo = 'admin_revoke' and actor_id is not null and note is not null)
  )
);

comment on table public.addon_ledger is
  'Registro APPEND-ONLY dei movimenti addon (il trigger vieta UPDATE/DELETE anche al service_role). Il saldo di user_addon_inventory è sum(delta). Idempotenza via indici parziali UNIQUE. Il tipo refund è predisposto ma oggi non emesso (nessun rimborso automatico).';

-- Idempotenza a DB: un consume e un refund al più per richiesta; una entry di
-- accredito (acquisto o grant) al più per purchase — il delta può essere +N.
create unique index addon_ledger_consume_once  on public.addon_ledger (request_id)  where tipo = 'consume';
create unique index addon_ledger_refund_once   on public.addon_ledger (request_id)  where tipo = 'refund';
create unique index addon_ledger_purchase_once on public.addon_ledger (purchase_id) where tipo in ('purchase', 'admin_grant');
create index addon_ledger_user_idx on public.addon_ledger (user_id, addon_id, created_at desc);

alter table public.addon_ledger enable row level security;
revoke all on public.addon_ledger from anon, authenticated;

-- Append-only anche per il service_role (che bypassa la RLS): trigger che
-- rifiuta ogni UPDATE/DELETE. Il rollback lo droppa con la tabella.
create or replace function public.fn_addon_ledger_readonly()
returns trigger
language plpgsql as $$
begin
  raise exception 'addon_ledger è append-only' using detail = 'ledger_append_only';
end;
$$;

create trigger addon_ledger_readonly
  before update or delete on public.addon_ledger
  for each row execute function public.fn_addon_ledger_readonly();

-- ----------------------------------------------------------------------------
-- 5) fn_addon_apply_movement — UNICO punto di scrittura (ledger + inventario).
--    Chiamata SEMPRE dentro la transazione di un'altra RPC. Saldo mai negativo.
-- ----------------------------------------------------------------------------
create or replace function public.fn_addon_apply_movement(
  p_user_id     uuid,
  p_addon_id    bigint,
  p_tipo        text,
  p_delta       integer,
  p_purchase_id uuid,
  p_request_id  uuid,
  p_actor_id    uuid,
  p_note        text
)
returns integer  -- quantità residua
language plpgsql
security definer
set search_path = public
as $$
declare
  v_qty integer;
begin
  -- Accredito e decremento sono percorsi DIVERSI: in INSERT ... ON CONFLICT il
  -- CHECK(quantita>=0) è valutato sui VALUES proposti PRIMA di risolvere il
  -- conflitto, quindi un decremento (-1) su riga esistente fallirebbe comunque.
  -- Il decremento passa da UPDATE (row lock + check sul risultato).
  begin
    if p_delta >= 0 then
      insert into public.user_addon_inventory (user_id, addon_id, quantita)
      values (p_user_id, p_addon_id, p_delta)
      on conflict (user_id, addon_id)
        do update set quantita = public.user_addon_inventory.quantita + p_delta
      returning quantita into v_qty;
    else
      -- Decremento: la riga deve esistere e avere capienza. Il CHECK sul
      -- risultato dell'UPDATE (o l'assenza di riga) diventa 'esaurito'.
      update public.user_addon_inventory
      set quantita = quantita + p_delta
      where user_id = p_user_id and addon_id = p_addon_id
      returning quantita into v_qty;
      if not found then
        raise exception 'Credito addon esaurito' using detail = 'addon_credit_esaurito';
      end if;
    end if;
  exception when check_violation then
    raise exception 'Credito addon esaurito' using detail = 'addon_credit_esaurito';
  end;

  insert into public.addon_ledger
    (user_id, addon_id, tipo, delta, purchase_id, request_id, actor_id, note)
  values (p_user_id, p_addon_id, p_tipo, p_delta,
          p_purchase_id, p_request_id, p_actor_id, p_note);

  return v_qty;
end;
$$;

revoke execute on function
  public.fn_addon_apply_movement(uuid, bigint, text, integer, uuid, uuid, uuid, text)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 6) fn_create_consultation_request — insert richiesta + consumo ATOMICI.
--    Il gating lo RICALCOLA la RPC leggendo il catalogo nella stessa
--    transazione (niente TOCTOU col pre-check Python). Payload jsonb: il Python
--    possiede già tutti i campi validati.
-- ----------------------------------------------------------------------------
create or replace function public.fn_create_consultation_request(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_addon public.addons%rowtype;
  v_req   public.consultation_requests%rowtype;
  v_gate  boolean;
  v_qty   integer := null;
begin
  select * into v_addon from public.addons
  where id = (p_payload ->> 'addon_id')::bigint and is_active;
  if not found then
    raise exception 'Addon non disponibile' using detail = 'addon_not_available';
  end if;

  -- Gating pilotato dal catalogo: consumabile a pagamento → serve 1 unità.
  v_gate := (v_addon.tipo_fruizione = 'consumabile'
             and v_addon.tipo_prezzo = 'importo' and v_addon.prezzo > 0);

  begin
    insert into public.consultation_requests
      (cliente_id, family_parent_id, company_profile_id, ai_check_id, esito,
       punteggio, bando_id, bando_slug, bando_titolo, addon_id, addon_slug, addon_prezzo)
    values
      ((p_payload ->> 'cliente_id')::uuid,
       (p_payload ->> 'family_parent_id')::uuid,
       (p_payload ->> 'company_profile_id')::uuid,
       nullif(p_payload ->> 'ai_check_id', '')::uuid,
       p_payload ->> 'esito',
       nullif(p_payload ->> 'punteggio', '')::integer,
       (p_payload ->> 'bando_id')::integer,
       p_payload ->> 'bando_slug',
       p_payload ->> 'bando_titolo',
       v_addon.id, v_addon.slug, v_addon.prezzo)
    returning * into v_req;
  exception when unique_violation then
    -- 23505 dell'indice consultation_requests_one_open: dentro una RPC
    -- arriverebbe come 502, lo si traduce in detail-code → 409.
    raise exception 'C''è già una richiesta di consulto aperta per questo bando'
      using detail = 'request_gia_aperta';
  end;

  if v_gate then
    -- Consumo di 1 unità ALLA CREAZIONE: se il saldo è 0 l'eccezione annulla
    -- anche l'insert della richiesta. Idempotente per costruzione (request
    -- appena creata) + UNIQUE addon_ledger_consume_once.
    v_qty := public.fn_addon_apply_movement(
      v_req.cliente_id, v_addon.id, 'consume', -1,
      null, v_req.id, v_req.cliente_id, null);
  end if;

  return jsonb_build_object('request', to_jsonb(v_req),
                            'consumato', v_gate, 'quantita_residua', v_qty);
end;
$$;

revoke execute on function public.fn_create_consultation_request(jsonb)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 7) fn_complete_purchase — riscritta: ramo addon → ledger (non più
--    user_addons); guard esteso ai kind amministrativi. Corpo VERBATIM dalla
--    0026 salvo i due punti marcati «0028».
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
  v_fruizione text;  -- 0028
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
  -- 0028: i kind amministrativi (gratuiti) non si completano con un pagamento.
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
    -- 0028: l'addon si accredita nel ledger (+1), non più in user_addons.
    select tipo_fruizione into v_fruizione from public.addons where id = v_p.addon_id;
    if v_fruizione = 'permanente' and exists (
      select 1 from public.user_addon_inventory
      where user_id = v_p.user_id and addon_id = v_p.addon_id and quantita >= 1
    ) then
      -- Già posseduto (grant o acquisto arrivato tra checkout e incasso): il
      -- denaro è registrato (status già 'pagato'), il possesso non si duplica.
      -- Orfano esplicito: il chiamante segnala agli admin (rimborso manuale v1).
      return jsonb_build_object('esito', 'pagamento_orfano', 'motivo', 'addon_gia_posseduto');
    end if;
    -- v1: un purchase addon = 1 unità (delta +1). N unità = estensione futura
    -- del checkout, il ledger la supporta già (entry unica con delta +N).
    perform public.fn_addon_apply_movement(
      v_p.user_id, v_p.addon_id, 'purchase', 1, v_p.id, null, v_p.user_id, null);
  end if;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (v_p.user_id, 'purchase.completed', v_p.user_id, v_p.user_id,
          jsonb_build_object('purchase_id', v_p.id, 'kind', v_p.kind,
                             'oggetto', v_p.oggetto_slug, 'totale_cents', v_p.totale_cents));

  return jsonb_build_object('esito', 'applicato', 'kind', v_p.kind, 'apply', v_apply);
end;
$$;

revoke execute on function public.fn_complete_purchase(uuid, text)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 8) Grant / revoca admin di unità addon (R5).
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

  -- Riga nello storico acquisti a 0 € (gemella di cambio_admin): esclusa dai
  -- ricavi per costruzione (totale 0). NON annulla il checkout in corso
  -- dell'utente: le unità regalate non invalidano un acquisto in volo.
  insert into public.purchases
    (user_id, kind, status, addon_id, oggetto_slug, oggetto_nome, descrizione,
     imponibile_cents, iva_cents, totale_cents, iva_aliquota,
     dettaglio_calcolo, actor_admin_id, motivazione)
  values
    (p_user_id, 'addon_admin', 'gratuito', v_addon.id, v_addon.slug, v_addon.nome,
     'Accredito addon da amministratore: ' || v_addon.nome
       || case when p_quantita > 1 then ' × ' || p_quantita else '' end,
     0, 0, 0, 0,
     jsonb_build_object('quantita', p_quantita), p_admin_id, trim(p_motivazione))
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

revoke execute on function
  public.fn_admin_grant_addon(uuid, uuid, bigint, integer, text)
  from public, anon, authenticated;

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
begin
  if nullif(trim(p_motivazione), '') is null then
    raise exception 'La motivazione è obbligatoria' using detail = 'motivation_required';
  end if;
  if p_quantita is null or p_quantita < 1 then
    raise exception 'Quantità non valida' using detail = 'quantita_non_valida';
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

  return jsonb_build_object('quantita_revocata', v_delta, 'quantita_residua', v_qty);
end;
$$;

revoke execute on function
  public.fn_admin_revoke_addon(uuid, uuid, bigint, integer, text)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 9) Backfill idempotente dallo storico (user_addons + purchases) + verifica.
-- ----------------------------------------------------------------------------
create or replace function public.fn_backfill_addon_ledger_0028()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uuid_re constant text := '^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$';
  v_purchase integer := 0;
  v_consume  integer := 0;
  v_orfani   integer := 0;
  v_skip     integer := 0;
begin
  -- 1) ogni user_addons → entry 'purchase' (+1), created_at originale.
  with ins as (
    insert into public.addon_ledger
      (user_id, addon_id, tipo, delta, purchase_id, actor_id, note, created_at)
    select ua.user_id, ua.addon_id, 'purchase', 1, ua.purchase_id, ua.user_id,
           'backfill 0028 da user_addons', ua.created_at
    from public.user_addons ua
    where (ua.stato = 'disponibile'
           or (ua.stato = 'consumato' and ua.consumed_ref ~ v_uuid_re))
      and not exists (select 1 from public.addon_ledger l
                      where l.purchase_id = ua.purchase_id and l.tipo = 'purchase')
    returning 1
  )
  select count(*) into v_purchase from ins;

  -- 2) righe consumate con ref valido → entry 'consume' (-1), consumed_at.
  with ins as (
    insert into public.addon_ledger
      (user_id, addon_id, tipo, delta, request_id, actor_id, note, created_at)
    select ua.user_id, ua.addon_id, 'consume', -1, ua.consumed_ref::uuid, ua.user_id,
           'backfill 0028 da user_addons', coalesce(ua.consumed_at, ua.created_at)
    from public.user_addons ua
    where ua.stato = 'consumato' and ua.consumed_ref ~ v_uuid_re
      and not exists (select 1 from public.addon_ledger l
                      where l.request_id = ua.consumed_ref::uuid and l.tipo = 'consume')
    returning 1
  )
  select count(*) into v_consume from ins;

  -- 3) purchases addon PAGATI senza credito user_addons (non dovrebbe esistere,
  --    ma se c'è l'utente ha pagato e l'unità gli spetta) → entry 'purchase' +1.
  with ins as (
    insert into public.addon_ledger
      (user_id, addon_id, tipo, delta, purchase_id, actor_id, note, created_at)
    select p.user_id, p.addon_id, 'purchase', 1, p.id, p.user_id,
           'backfill 0028: purchase pagato senza credito user_addons',
           coalesce(p.paid_at, p.created_at)
    from public.purchases p
    where p.kind = 'addon' and p.status = 'pagato' and p.addon_id is not null
      and not exists (select 1 from public.user_addons ua where ua.purchase_id = p.id)
      and not exists (select 1 from public.addon_ledger l
                      where l.purchase_id = p.id and l.tipo = 'purchase')
    returning 1
  )
  select count(*) into v_orfani from ins;

  -- 3b) dati ambigui: consumate con ref NULL/malformato → saltate IN COPPIA
  --     (né purchase né consume: saldo netto 0), tracciate per revisione.
  for v_skip in
    select 1 from public.user_addons ua
    where ua.stato = 'consumato' and (ua.consumed_ref is null or ua.consumed_ref !~ v_uuid_re)
  loop
    null;  -- conteggio sotto
  end loop;
  select count(*) into v_skip from public.user_addons ua
  where ua.stato = 'consumato' and (ua.consumed_ref is null or ua.consumed_ref !~ v_uuid_re);
  if v_skip > 0 then
    insert into public.audit_log (actor_id, action, target_user_id, payload)
    values (null, 'addon.backfill_skipped', null,
            jsonb_build_object('righe_consumate_senza_ref_valido', v_skip));
  end if;

  -- 4) inventario = RICOSTRUZIONE dal ledger (l'invariante di R3); solo per
  --    profili esistenti (il ledger senza FK sopravvive agli utenti cancellati).
  insert into public.user_addon_inventory (user_id, addon_id, quantita)
  select l.user_id, l.addon_id, sum(l.delta)
  from public.addon_ledger l
  join public.profiles pr on pr.id = l.user_id
  group by l.user_id, l.addon_id
  on conflict (user_id, addon_id) do update set quantita = excluded.quantita;

  return jsonb_build_object('purchase', v_purchase, 'consume', v_consume,
                            'orfani_recuperati', v_orfani, 'saltate', v_skip);
end;
$$;

revoke execute on function public.fn_backfill_addon_ledger_0028()
  from public, anon, authenticated;

select public.fn_backfill_addon_ledger_0028();

-- Verifica invariante: la migration ABORTISCE se il saldo materializzato non
-- combacia con una RICOSTRUZIONE INDIPENDENTE dallo storico (user_addons +
-- purchases). Confrontarlo col ledger sarebbe tautologico (l'inventario è
-- appena stato popolato con sum(delta)): questo invece riconcilia contro la
-- SORGENTE, così un bug nei predicati del backfill fa davvero abortire.
-- Saldo atteso per (user, addon): +1 per ogni user_addons 'disponibile'
-- (i 'consumato' con ref valido netto 0: purchase+consume) + 1 per ogni
-- purchase addon pagato orfano recuperato.
do $$
declare
  v_bad integer;
begin
  with disp as (
    select user_id, addon_id, count(*)::integer as q
    from public.user_addons where stato = 'disponibile'
    group by user_id, addon_id
  ),
  orfani as (
    select p.user_id, p.addon_id, count(*)::integer as q
    from public.purchases p
    where p.kind = 'addon' and p.status = 'pagato' and p.addon_id is not null
      and not exists (select 1 from public.user_addons ua where ua.purchase_id = p.id)
    group by p.user_id, p.addon_id
  ),
  atteso as (
    select coalesce(d.user_id, o.user_id) as user_id,
           coalesce(d.addon_id, o.addon_id) as addon_id,
           coalesce(d.q, 0) + coalesce(o.q, 0) as q
    from disp d
    full join orfani o on d.user_id = o.user_id and d.addon_id = o.addon_id
  )
  select count(*) into v_bad
  from atteso a
  join public.profiles pr on pr.id = a.user_id
  left join public.user_addon_inventory i
    on i.user_id = a.user_id and i.addon_id = a.addon_id
  where coalesce(i.quantita, 0) <> a.q;

  if v_bad > 0 then
    raise exception 'backfill 0028 incoerente su % coppie (user, addon)', v_bad;
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 10) user_addons congelata (nessun DROP: rollback/storico).
-- ----------------------------------------------------------------------------
comment on table public.user_addons is
  'DEPRECATA dalla 0028 (sola lettura storica): sostituita da user_addon_inventory + addon_ledger. Non più scritta da alcun percorso; DROP in una migration futura.';

-- ============================================================================
-- ROLLBACK 0028 (eseguire in transazione). PRECONDIZIONE: nessuna riga
-- purchases.kind='addon_admin' (il grant non ha equivalente in user_addons).
--   do $$ begin
--     if exists (select 1 from public.purchases where kind = 'addon_admin') then
--       raise exception 'rollback bloccato: esistono grant addon_admin (riclassificarli a mano prima)';
--     end if;
--   end $$;
-- 1) Riallineare user_addons con gli acquisti addon completati DOPO la 0028
--    (vivono solo nel ledger). SQL eseguibile per il caso normale (acquisti
--    consumabili): reinserisce la riga user_addons 'disponibile' o 'consumato'
--    ricostruendola dal ledger. Le entry admin_grant NON hanno equivalente in
--    user_addons e vanno annotate/gestite a mano PRIMA (vedi precondizione).
--      insert into public.user_addons
--        (user_id, addon_id, purchase_id, stato, consumed_ref, consumed_at, created_at)
--      select l.user_id, l.addon_id, l.purchase_id,
--             case when c.request_id is not null then 'consumato' else 'disponibile' end,
--             c.request_id::text, c.created_at, l.created_at
--      from public.addon_ledger l
--      left join lateral (
--        select request_id, created_at from public.addon_ledger c2
--        where c2.tipo = 'consume' and c2.user_id = l.user_id and c2.addon_id = l.addon_id
--          and not exists (select 1 from public.addon_ledger r
--                          where r.tipo = 'refund' and r.request_id = c2.request_id)
--        order by c2.created_at limit 1
--      ) c on true
--      where l.tipo = 'purchase'
--        and not exists (select 1 from public.user_addons ua where ua.purchase_id = l.purchase_id);
--    (accoppiamento consume↔purchase best-effort per (user,addon): sufficiente
--    per il conteggio del credito, che è ciò che il vecchio backend legge.)
-- 2) Ripristinare fn_complete_purchase alla versione 0026 (rieseguire la
--    sezione 10a della 0026, verbatim, incluso il ramo addon con user_addons).
-- 3) drop function fn_admin_revoke_addon(uuid,uuid,bigint,integer,text);
--    drop function fn_admin_grant_addon(uuid,uuid,bigint,integer,text);
--    drop function fn_create_consultation_request(jsonb);
--    drop function fn_backfill_addon_ledger_0028();
--    drop function fn_addon_apply_movement(uuid,bigint,text,integer,uuid,uuid,uuid,text);
--    drop trigger addon_ledger_readonly on public.addon_ledger;
--    drop function fn_addon_ledger_readonly();
--    drop table public.addon_ledger;
--    drop table public.user_addon_inventory;
-- 4) alter table public.purchases drop constraint purchases_gratuito_admin_coerente;
--    alter table public.purchases add constraint purchases_cambio_admin_coerente check (
--      (kind = 'cambio_admin' and status = 'gratuito' and actor_admin_id is not null
--         and motivazione is not null and totale_cents = 0)
--      or (kind <> 'cambio_admin' and status <> 'gratuito'));
--    alter table public.purchases drop constraint purchases_kind_check;
--    alter table public.purchases add constraint purchases_kind_check
--      check (kind in ('piano','rinnovo','addon','cambio_admin'));
-- 5) alter table public.addons drop column tipo_fruizione;
-- 6) NON toccare prezzo/tipo_prezzo di consulto-esperto (gestiti da AdminAddon).
--    Il vecchio backend rilegge user_addons: coerente col passo 1.
-- ============================================================================
