-- ============================================================================
-- BandoFit — DB primario, migration 0026: modulo pagamenti (fase 1).
--
-- Introduce lo strato dati dei pagamenti (provider: Revolut Merchant API) e
-- l'anagrafica di fatturazione. Le FATTURE arrivano con la 0027: qui vive solo
-- ciò che serve a checkout, rinnovi, cambi differiti e cambi admin.
--
-- Contenuto:
--   1) billing_profiles           — anagrafica di fatturazione del titolare
--   2) revolut_customers          — mapping utente ↔ customer Revolut + metodo
--   3) purchases                  — il perno: ogni movimento economico (o cambio
--                                   admin gratuito) è una riga qui, immutabile
--   4) user_addons                — attivazioni addon acquistate
--   5) scheduled_plan_changes     — downgrade/disdette differiti a scadenza
--   6) webhook_events             — registro/dedup degli eventi del provider
--   7) payment_runs               — claim giornaliero dello scheduler pagamenti
--   8) user_subscriptions         — colonne per rinnovo automatico e grazia
--   9) fn_apply_plan_change       — REFACTOR: il corpo applicativo di
--      fn_switch_plan estratto e parametrizzato (scadenza, attore, audit);
--      fn_switch_plan resta come wrapper identico all'odierno
--  10) fn_complete_purchase / fn_fail_purchase / fn_execute_scheduled_change /
--      fn_registra_cambio_admin  — le transizioni di stato, atomiche a DB
--  11) handle_new_user ridefinita — la registrazione non può più assegnare
--      piani a pagamento (l'endpoint è pubblico: la chiusura sta qui, non nel
--      frontend)
--
-- Invarianti (fatti rispettare da vincoli + RPC, non da convenzioni):
--   * nessun piano a pagamento applicato senza un purchase 'pagato';
--   * importi immutabili: le RPC non aggiornano MAI le colonne monetarie;
--   * un solo purchase 'in_attesa' per utente; un solo cambio 'programmato';
--   * idempotenza: completare due volte lo stesso purchase è un no-op; un
--     incasso su un purchase non applicabile è un esito 'pagamento_orfano'
--     ritornato al chiamante, mai un errore muto.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Anagrafica di fatturazione (1:1 col titolare). La validazione per tipo
--    (P.IVA/CF/SDI/PEC, VIES per l'UE) è del backend: qui solo i vincoli di
--    forma che non dipendono dal tipo di soggetto.
-- ----------------------------------------------------------------------------
create table public.billing_profiles (
  user_id             uuid primary key references public.profiles (id) on delete cascade,
  tipo_soggetto       text not null check (tipo_soggetto in ('azienda_it', 'privato_it', 'azienda_ue')),
  denominazione       text,
  nome                text,
  cognome             text,
  partita_iva         text,
  codice_fiscale      text,
  paese               char(2) not null default 'IT',
  indirizzo           text not null,
  comune              text not null,
  provincia           text,
  cap                 text not null,
  -- Recapito SDI: 7 char (default '0000000' = cassetto fiscale/B2C). Per i
  -- soggetti UE il builder XML forza 'XXXXXXX' e IGNORA questo campo.
  codice_destinatario text not null default '0000000' check (char_length(codice_destinatario) = 7),
  pec                 text,
  vies_valid          boolean,
  vies_checked_at     timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

comment on table public.billing_profiles is
  'Anagrafica di fatturazione del titolare. Le fatture citano lo SNAPSHOT in purchases.billing_snapshot, mai questa tabella: qui vive solo lo stato corrente editabile.';

create trigger billing_profiles_updated_at
  before update on public.billing_profiles
  for each row execute function public.set_updated_at();

alter table public.billing_profiles enable row level security;
revoke all on public.billing_profiles from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 2) Mapping Revolut. v1: UN metodo salvato per utente, a colonne (NULL =
--    nessun metodo). ATTENZIONE forma API: la lista metodi del provider
--    risponde {"payment_methods": [...]}, non una lista nuda (verificato in
--    sandbox, Fase 0).
-- ----------------------------------------------------------------------------
create table public.revolut_customers (
  user_id             uuid primary key references public.profiles (id) on delete cascade,
  revolut_customer_id text not null unique,
  saved_method_id     text,
  saved_method_type   text check (saved_method_type in ('card', 'revolut_pay')),
  saved_method_label  text,
  saved_method_at     timestamptz,
  created_at          timestamptz not null default now()
);

comment on table public.revolut_customers is
  'Customer Revolut per utente + metodo di pagamento salvato (saved_for=merchant, v1: al più uno). I dati carta NON esistono qui: solo l''id del metodo nel vault del provider.';

alter table public.revolut_customers enable row level security;
revoke all on public.revolut_customers from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 3) Acquisti. Record FINANZIARI: user_id senza FK (precedente:
--    api_usage_events) — sopravvivono alla cancellazione dell'utente, gli
--    obblighi di conservazione fiscale superano il ciclo di vita dell'account.
--    Gli importi sono in CENTESIMI interi (il provider ragiona così); il
--    listino resta numeric(10,2), la conversione avviene alla creazione e
--    viene congelata qui con la formula in dettaglio_calcolo.
-- ----------------------------------------------------------------------------
create table public.purchases (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null,
  kind              text not null check (kind in ('piano', 'rinnovo', 'addon', 'cambio_admin')),
  status            text not null check (status in ('in_attesa', 'pagato', 'fallito', 'scaduto', 'annullato', 'gratuito')),
  plan_id           bigint,
  addon_id          bigint,
  oggetto_slug      text not null,
  oggetto_nome      text not null,
  descrizione       text not null,
  imponibile_cents  integer not null check (imponibile_cents >= 0),
  iva_cents         integer not null check (iva_cents >= 0),
  totale_cents      integer not null,
  iva_aliquota      numeric(4,2) not null,
  natura_iva        text,
  valuta            char(3) not null default 'EUR',
  dettaglio_calcolo jsonb not null default '{}'::jsonb,
  billing_snapshot  jsonb,
  revolut_order_id  text unique,
  revolut_payment_id text,
  decline_reason    text,
  auto_renew_scelto boolean,
  -- Rinnovi: ciclo_rinnovo = data_scadenza della subscription al momento della
  -- creazione. È la chiave dell'idempotenza per ciclo (mai due addebiti per lo
  -- stesso ciclo) e del calendario dei retry (+3/+7 dal ciclo).
  ciclo_rinnovo     date,
  tentativo         integer,
  actor_admin_id    uuid,
  motivazione       text,
  refunded_cents    integer not null default 0,
  refund_note       text,
  created_at        timestamptz not null default now(),
  paid_at           timestamptz,
  failed_at         timestamptz,
  -- coerenza monetaria a livello DB, non solo applicativo
  constraint purchases_totale_coerente check (totale_cents = imponibile_cents + iva_cents),
  -- i campi ciclo esistono TUTTI e SOLO sui rinnovi
  constraint purchases_ciclo_solo_rinnovi check (
    (kind = 'rinnovo' and ciclo_rinnovo is not null and tentativo >= 1)
    or (kind <> 'rinnovo' and ciclo_rinnovo is null and tentativo is null)
  ),
  -- 'gratuito' è lo stato dei soli cambi admin, che nascono con attore e motivazione
  constraint purchases_cambio_admin_coerente check (
    (kind = 'cambio_admin' and status = 'gratuito' and actor_admin_id is not null
       and motivazione is not null and totale_cents = 0)
    or (kind <> 'cambio_admin' and status <> 'gratuito')
  )
);

comment on table public.purchases is
  'Ogni movimento del modulo pagamenti (acquisto piano, rinnovo, addon, cambio admin gratuito). Importi/IVA/snapshot IMMUTABILI dopo la creazione: lo storico non si ricalcola mai dai listini correnti.';

create index purchases_user_idx on public.purchases (user_id, created_at desc);
-- un solo checkout in corso per utente
create unique index purchases_one_pending on public.purchases (user_id) where status = 'in_attesa';
-- idempotenza per ciclo di rinnovo (il tentativo distingue i retry)
create unique index purchases_ciclo_tentativo
  on public.purchases (user_id, ciclo_rinnovo, tentativo) where kind = 'rinnovo';

alter table public.purchases enable row level security;
revoke all on public.purchases from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 4) Attivazioni addon acquistate (una riga per acquisto, consumabile).
-- ----------------------------------------------------------------------------
create table public.user_addons (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references public.profiles (id) on delete cascade,
  addon_id     bigint not null references public.addons (id),
  purchase_id  uuid not null unique references public.purchases (id),
  stato        text not null default 'disponibile' check (stato in ('disponibile', 'consumato')),
  consumed_at  timestamptz,
  consumed_ref text,
  created_at   timestamptz not null default now()
);

comment on table public.user_addons is
  'Crediti addon acquistati. Il flusso consulto-esperto GRATUITO non passa di qui: l''innesto scatta solo per gli addon con tipo_prezzo=''importo''.';

create index user_addons_user_idx on public.user_addons (user_id, addon_id) where stato = 'disponibile';

alter table public.user_addons enable row level security;
revoke all on public.user_addons from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 5) Cambi piano differiti (downgrade, disdetta, esiti della grazia).
-- ----------------------------------------------------------------------------
create table public.scheduled_plan_changes (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references public.profiles (id) on delete cascade,
  from_plan_id   bigint references public.subscription_plans (id),
  to_plan_id     bigint not null references public.subscription_plans (id),
  effective_date date not null,
  status         text not null default 'programmato'
                 check (status in ('programmato', 'annullato', 'eseguito')),
  motivo         text not null
                 check (motivo in ('downgrade', 'disdetta', 'grace_scaduta', 'mancato_rinnovo')),
  created_by     uuid,
  created_at     timestamptz not null default now(),
  cancelled_at   timestamptz,
  executed_at    timestamptz
);

comment on table public.scheduled_plan_changes is
  'Cambi piano programmati a scadenza. REGOLA: un cambio verso un piano a pagamento si applica SOLO se il ciclo ha un rinnovo pagato; altrimenti l''esecuzione degrada a gratuito (mai regalare il piano di destinazione).';

create unique index scheduled_changes_one_active
  on public.scheduled_plan_changes (user_id) where status = 'programmato';

alter table public.scheduled_plan_changes enable row level security;
revoke all on public.scheduled_plan_changes from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 6) Eventi webhook del provider. Dedup DIFFERENZIATO per cardinalità
--    (verificato in Fase 0: payload thin {event, order_id}, consegna
--    at-least-once, nessun ordine garantito):
--    * eventi order-level (COMPLETED/FAILED/CANCELLED): al più uno per ordine
--      → UNIQUE parziale, il duplicato è un retry e si scarta;
--    * eventi payment-level (PAYMENT_DECLINED/FAILED): N legittimi per ordine
--      (un payment per tentativo) → si registrano e si processano SEMPRE
--      (riprocessare è sicuro: si rilegge l'ordine e le RPC sono idempotenti).
-- ----------------------------------------------------------------------------
create table public.webhook_events (
  id           uuid primary key default gen_random_uuid(),
  provider     text not null default 'revolut',
  event        text not null,
  resource_id  text not null,
  payload      jsonb not null,
  received_at  timestamptz not null default now(),
  processed_at timestamptz,
  esito        text
);

create unique index webhook_events_dedup_order
  on public.webhook_events (provider, event, resource_id)
  where event in ('ORDER_COMPLETED', 'ORDER_FAILED', 'ORDER_CANCELLED');
create index webhook_events_resource_idx on public.webhook_events (resource_id);

alter table public.webhook_events enable row level security;
revoke all on public.webhook_events from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 7) Claim giornaliero dello scheduler pagamenti (stesso stampo di
--    bando_alert_runs: INSERT sulla PK = claim; 23505 = già eseguito altrove).
-- ----------------------------------------------------------------------------
create table public.payment_runs (
  giorno     date primary key,
  created_at timestamptz not null default now()
);

alter table public.payment_runs enable row level security;
revoke all on public.payment_runs from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 8) Rinnovo automatico e grazia sull'abbonamento.
--    grace_until: valorizzata dal PRIMO tentativo di rinnovo fallito
--    (= ciclo + 14 giorni). La grazia è ancorata QUI, non ad auto_renew:
--    spegnere il toggle a metà dunning non deve far degradare subito.
-- ----------------------------------------------------------------------------
alter table public.user_subscriptions
  add column auto_renew             boolean not null default false,
  add column grace_until            date,
  add column renewal_notice_sent_at timestamptz;

-- ----------------------------------------------------------------------------
-- 9) REFACTOR: fn_apply_plan_change = il corpo applicativo di fn_switch_plan
--    (0024) con scadenza, attore e azione di audit parametrizzati.
--    fn_switch_plan resta come wrapper con il comportamento IDENTICO a oggi
--    (scadenza +1 anno, attore = utente, azione 'plan.switched').
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

  insert into public.user_subscriptions (user_id, plan_id, data_scadenza)
  values (p_user_id, p_plan_id,
          coalesce(p_data_scadenza, current_date + interval '1 year'));

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
    values (p_actor_id, 'family.invite_revoked', v_row.member_id, p_user_id,
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
    values (p_actor_id, 'family.member_demoted', v_row.member_id, p_user_id,
            jsonb_build_object('membership_id', v_row.id, 'reason', 'plan_downgrade'));
  end loop;

  -- Adeguamento delle AZIENDE al nuovo max_aziende.
  perform public.fn_reconcile_companies(p_user_id);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_actor_id, p_audit_action, p_user_id, p_user_id,
          jsonb_build_object('plan_id', p_plan_id,
                             'data_scadenza', coalesce(p_data_scadenza, current_date + interval '1 year'),
                             'demoted', v_demoted, 'revoked_pending', v_revoked));

  return jsonb_build_object('demoted', v_demoted, 'revoked_pending', v_revoked);
end;
$$;

revoke execute on function public.fn_apply_plan_change(uuid, bigint, date, uuid, text)
  from public, anon, authenticated;

-- Il wrapper: firma e comportamento invariati rispetto alla 0024.
create or replace function public.fn_switch_plan(p_user_id uuid, p_plan_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
begin
  return public.fn_apply_plan_change(
    p_user_id, p_plan_id,
    (current_date + interval '1 year')::date,
    p_user_id, 'plan.switched');
end;
$$;

revoke execute on function public.fn_switch_plan(uuid, bigint) from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 10a) Completamento di un purchase pagato. ATOMICA e IDEMPOTENTE.
--      Esiti (jsonb, campo 'esito'):
--        'applicato'         — pagamento registrato e piano/addon applicato
--        'gia_pagato'        — no-op (webhook duplicato / sync concorrente)
--        'pagamento_orfano'  — soldi incassati ma purchase non applicabile
--                              (annullato/scaduto/fallito, o ciclo di rinnovo
--                              già coperto): il CHIAMANTE registra l'anomalia
--                              e avvisa l'admin. Mai un errore muto.
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
  v_sched public.scheduled_plan_changes%rowtype;
  v_auto_renew boolean;
  v_apply jsonb := null;
  v_nuova_scadenza date;
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
  if v_p.kind = 'cambio_admin' then
    raise exception 'Un cambio admin non si completa con un pagamento'
      using detail = 'invalid_kind';
  end if;

  -- Rinnovo su ciclo già coperto: il denaro è arrivato (lo si registra) ma il
  -- piano NON si estende una seconda volta.
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
    -- L'auto_renew sopravvive al rinnovo (la riga nuova nasce coi default).
    select auto_renew into v_auto_renew
    from public.user_subscriptions
    where user_id = v_p.user_id and status = 'active';

    v_nuova_scadenza := v_p.ciclo_rinnovo + interval '1 year';
    -- Il purchase di rinnovo snapshotta il piano di DESTINAZIONE alla
    -- creazione: importo addebitato e piano applicato coincidono per
    -- costruzione. Se c'è un cambio programmato coerente, si marca eseguito
    -- nella STESSA transazione.
    v_apply := public.fn_apply_plan_change(
      v_p.user_id, v_p.plan_id, v_nuova_scadenza,
      v_p.user_id, 'plan.renewed');
    -- Si marca eseguito SOLO il cambio coerente col piano appena applicato: un
    -- downgrade programmato DOPO la creazione del rinnovo (piano corrente)
    -- resta 'programmato' e maturerà al ciclo successivo.
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
    insert into public.user_addons (user_id, addon_id, purchase_id)
    values (v_p.user_id, v_p.addon_id, v_p.id);
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
-- 10b) Fallimento/scadenza/annullo di un purchase. Idempotente; non tocca mai
--      il piano. Sul PRIMO tentativo di rinnovo fallito arma la grazia
--      (ciclo + 14 giorni) sull'abbonamento attivo.
-- ----------------------------------------------------------------------------
create or replace function public.fn_fail_purchase(
  p_purchase_id    uuid,
  p_status         text,
  p_decline_reason text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_p public.purchases%rowtype;
begin
  if p_status not in ('fallito', 'scaduto', 'annullato') then
    raise exception 'Stato di fallimento non valido' using detail = 'invalid_status';
  end if;

  select * into v_p from public.purchases where id = p_purchase_id for update;
  if not found then
    raise exception 'Acquisto inesistente' using detail = 'purchase_not_found';
  end if;
  if v_p.status = 'pagato' then
    return jsonb_build_object('esito', 'gia_pagato');
  end if;
  if v_p.status <> 'in_attesa' then
    return jsonb_build_object('esito', 'gia_chiuso', 'stato_purchase', v_p.status);
  end if;

  update public.purchases
  set status = p_status,
      failed_at = now(),
      decline_reason = coalesce(p_decline_reason, decline_reason)
  where id = v_p.id;

  if v_p.kind = 'rinnovo' and p_status = 'fallito' and v_p.tentativo = 1 then
    update public.user_subscriptions
    set grace_until = v_p.ciclo_rinnovo + 14
    where user_id = v_p.user_id and status = 'active';
  end if;

  return jsonb_build_object('esito', p_status);
end;
$$;

revoke execute on function public.fn_fail_purchase(uuid, text, text)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 10c) Esecuzione di un cambio programmato giunto a maturazione.
--      REGOLA CARDINE: mai applicare un piano a pagamento non pagato — se la
--      destinazione è a pagamento e il ciclo non ha un rinnovo pagato, si
--      degrada a GRATUITO (il purchase pagato, quando c'è, esegue il cambio
--      da dentro fn_complete_purchase, non da qui).
-- ----------------------------------------------------------------------------
create or replace function public.fn_execute_scheduled_change(p_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_s public.scheduled_plan_changes%rowtype;
  v_to_pagato boolean;
  v_coperto boolean;
  v_free_id bigint;
  v_target bigint;
  v_fallback boolean := false;
begin
  select * into v_s from public.scheduled_plan_changes
  where id = p_id and status = 'programmato'
  for update;
  if not found then
    return jsonb_build_object('esito', 'non_programmato');
  end if;
  if v_s.effective_date > current_date then
    raise exception 'Cambio non ancora maturato' using detail = 'not_due';
  end if;

  select (tipo_prezzo = 'importo' and prezzo_annuale > 0) into v_to_pagato
  from public.subscription_plans where id = v_s.to_plan_id;

  v_target := v_s.to_plan_id;
  if coalesce(v_to_pagato, false) then
    select exists (
      select 1 from public.purchases
      where user_id = v_s.user_id and kind = 'rinnovo' and status = 'pagato'
        and ciclo_rinnovo = v_s.effective_date
    ) into v_coperto;
    if not v_coperto then
      select id into v_free_id from public.subscription_plans where slug = 'gratuito' limit 1;
      if v_free_id is null then
        raise exception 'Piano gratuito non configurato' using detail = 'free_plan_missing';
      end if;
      v_target := v_free_id;
      v_fallback := true;
    end if;
  end if;

  perform public.fn_apply_plan_change(
    v_s.user_id, v_target,
    (current_date + interval '1 year')::date,
    coalesce(v_s.created_by, v_s.user_id),
    'plan.scheduled_change_executed');

  update public.scheduled_plan_changes
  set status = 'eseguito', executed_at = now()
  where id = v_s.id;

  return jsonb_build_object('esito', 'eseguito', 'plan_id', v_target,
                            'fallback_gratuito', v_fallback);
end;
$$;

revoke execute on function public.fn_execute_scheduled_change(uuid)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 10d) Cambio piano gratuito da admin, con audit dell'ATTORE VERO (chiude il
--      gap della 0024: l'audit registrava l'utente target come actor).
--      Annulla i purchase in_attesa (il service ha GIÀ cancellato i relativi
--      ordini sul provider PRIMA di chiamare: qui si ritorna la lista per il
--      controllo di coerenza) e i cambi programmati.
-- ----------------------------------------------------------------------------
create or replace function public.fn_registra_cambio_admin(
  p_admin_id    uuid,
  p_user_id     uuid,
  p_plan_id     bigint,
  p_motivazione text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_plan public.subscription_plans%rowtype;
  v_purchase_id uuid;
  v_annullati jsonb;
  v_apply jsonb;
begin
  if nullif(trim(p_motivazione), '') is null then
    raise exception 'La motivazione è obbligatoria' using detail = 'motivation_required';
  end if;

  select * into v_plan from public.subscription_plans where id = p_plan_id and is_active;
  if not found then
    raise exception 'Piano inesistente o non attivo' using detail = 'plan_not_available';
  end if;

  -- Annulla i checkout in corso (gli ordini provider sono già stati cancellati
  -- dal service) e i cambi programmati.
  with chiusi as (
    update public.purchases
    set status = 'annullato', failed_at = now()
    where user_id = p_user_id and status = 'in_attesa'
    returning id, revolut_order_id
  )
  select coalesce(jsonb_agg(jsonb_build_object('purchase_id', id, 'revolut_order_id', revolut_order_id)), '[]'::jsonb)
  into v_annullati from chiusi;

  update public.scheduled_plan_changes
  set status = 'annullato', cancelled_at = now()
  where user_id = p_user_id and status = 'programmato';

  insert into public.purchases (
    user_id, kind, status, plan_id, oggetto_slug, oggetto_nome,
    descrizione, imponibile_cents, iva_cents, totale_cents, iva_aliquota,
    actor_admin_id, motivazione
  ) values (
    p_user_id, 'cambio_admin', 'gratuito', p_plan_id, v_plan.slug, v_plan.nome,
    'Cambio piano da amministratore: ' || v_plan.nome,
    0, 0, 0, 0,
    p_admin_id, trim(p_motivazione)
  ) returning id into v_purchase_id;

  v_apply := public.fn_apply_plan_change(
    p_user_id, p_plan_id,
    (current_date + interval '1 year')::date,
    p_admin_id, 'plan.admin_changed');

  return jsonb_build_object('purchase_id', v_purchase_id,
                            'annullati', v_annullati, 'apply', v_apply);
end;
$$;

revoke execute on function public.fn_registra_cambio_admin(uuid, uuid, bigint, text)
  from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 11) handle_new_user: la registrazione non assegna MAI un piano a pagamento.
--     /auth/register è pubblico: un client ostile può mettere plan_slug='pro'
--     nei metadata e (fino a oggi) riceverlo gratis. Da qui in poi i piani con
--     tipo_prezzo='importo' e prezzo > 0 ripiegano su 'gratuito'; il piano
--     desiderato resta nei metadata per il checkout post-login.
--     Per il resto: ripartenza VERBATIM dalla versione 0022 (telefono +
--     posizione lavorativa inclusi), difensiva, mai raise.
-- ----------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_plan_id bigint;
  v_is_family_invite boolean;
  v_position_id bigint;
  v_position_slug text;
begin
  v_is_family_invite :=
    coalesce(new.raw_user_meta_data ->> 'family_invite', '') = 'true';

  -- Slug assente, ignoto o posizione disattivata → NULL, senza rami d'errore.
  select id, slug into v_position_id, v_position_slug
  from public.job_positions
  where slug = nullif(trim(new.raw_user_meta_data ->> 'job_position_slug'), '')
    and is_active
  limit 1;

  insert into public.profiles (id, email, nome, cognome, azienda,
                               telefono, job_position_id, job_position_altro)
  values (
    new.id,
    new.email,
    coalesce(
      nullif(trim(new.raw_user_meta_data ->> 'nome'), ''),
      nullif(trim(new.raw_user_meta_data ->> 'denominazione'), '')
    ),
    nullif(trim(new.raw_user_meta_data ->> 'cognome'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'azienda'), ''),
    nullif(trim(new.raw_user_meta_data ->> 'telefono'), ''),
    v_position_id,
    case when v_position_slug = 'altro'
         then nullif(trim(new.raw_user_meta_data ->> 'job_position_altro'), '')
    end
  )
  on conflict (id) do nothing;

  if not v_is_family_invite then
    select id into v_plan_id
    from public.subscription_plans
    where slug = coalesce(new.raw_user_meta_data ->> 'plan_slug', 'gratuito')
      and is_active
      and not (tipo_prezzo = 'importo' and prezzo_annuale > 0)
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
