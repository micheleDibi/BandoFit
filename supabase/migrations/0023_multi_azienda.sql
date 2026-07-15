-- ============================================================================
-- BandoFit — DB primario, migration 0023: gestione multi-azienda (piano
-- "Advisor").
--
-- Oggi il modello è "1 utente = 1 azienda" (company_profiles.parent_id UNIQUE).
-- L'Advisor deve gestire N aziende clienti con dati SEGREGATI. Questa migration
-- è puramente ADDITIVA e retro-compatibile: nessuna colonna esistente cambia,
-- il vincolo UNIQUE su parent_id NON viene toccato (lo rimuove la 0024 insieme
-- al codice di scrittura per-id). Finché il codice advisor non è attivo, tutte
-- le nuove colonne restano NULL e gli indici unici parziali "legacy"
-- riproducono esattamente i vincoli attuali.
--
-- Primitiva di segregazione: una colonna overlay `company_profile_id` sulle
-- tabelle per-utente (bandi salvati, calendario, preferenze, ledger alert,
-- notifiche). NULL = riga legacy/non-advisor (scope per user_id, come oggi);
-- valorizzata = riga di un'azienda gestita da un Advisor. Postgres è
-- NULLS DISTINCT, quindi un singolo indice (user_id, company_profile_id, ...)
-- lascerebbe passare duplicati legacy: si usano DUE indici unici parziali per
-- tabella (uno per le righe legacy, uno per le righe azienda).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Limite di aziende gestibili: default per piano + override per utente.
--    Distinto da num_account_aziendali (posti PERSONA della famiglia): un
--    Advisor gestisce N aziende, non N account umani.
-- ----------------------------------------------------------------------------
alter table public.subscription_plans
  add column max_aziende integer not null default 1 check (max_aziende >= 1);

comment on column public.subscription_plans.max_aziende is
  'Numero massimo di aziende gestibili con questo piano (asse distinto da num_account_aziendali, che è il limite di account persona della famiglia). Default 1 = comportamento a singola azienda.';

update public.subscription_plans set max_aziende = 10 where slug = 'advisor';

-- Override per singolo utente: quando valorizzato vince sul default di piano.
alter table public.profiles
  add column max_aziende_override integer
    check (max_aziende_override is null or max_aziende_override >= 1);

comment on column public.profiles.max_aziende_override is
  'Override amministrativo del limite di aziende gestibili: se valorizzato prevale su subscription_plans.max_aziende. NULL = usa il default del piano.';

-- ----------------------------------------------------------------------------
-- 2) Ciclo di vita dell'azienda: soft-delete + archiviazione da downgrade.
--    Nessuna cancellazione fisica (i dati collegati sopravvivono e sono
--    recuperabili). `deleted_at`: cancellata dall'utente. `archived_at`: oltre
--    il limite dopo un downgrade di piano (sola lettura, riattivabile).
-- ----------------------------------------------------------------------------
alter table public.company_profiles
  add column deleted_at  timestamptz,
  add column archived_at timestamptz;

comment on column public.company_profiles.deleted_at is
  'Soft-delete: valorizzata = azienda cancellata dall''utente. Le righe collegate restano (recuperabili); il resolver dell''azienda attiva la rifiuta.';
comment on column public.company_profiles.archived_at is
  'Archiviata da downgrade di piano (oltre max_aziende): sola lettura, esclusa da switch/alert/export. Riattivabile risalendo di piano.';

-- Aziende "vive" di un owner (l'unica corrente per i non-Advisor).
create index company_profiles_owner_live_idx
  on public.company_profiles (parent_id, created_at)
  where deleted_at is null and archived_at is null;

-- ----------------------------------------------------------------------------
-- 3) Overlay `company_profile_id` sulle tabelle per-utente (Gruppo A).
--    FK ON DELETE CASCADE: un'eventuale purge fisica di un'azienda porta via i
--    suoi dati collegati (semantica di cancellazione, non di bleed).
--
--    Unicità: si estende la chiave con company_profile_id usando
--    UNIQUE NULLS NOT DISTINCT (PG15+): le righe legacy (company NULL) restano
--    deduplicate come oggi (NULL == NULL), quelle per-azienda distinguono per
--    azienda. Un solo vincolo NOMINATO (non un indice parziale) così resta
--    inferibile da PostgREST per gli upsert `on_conflict` — necessario per il
--    ledger degli alert.
-- ----------------------------------------------------------------------------

-- 3a) Bandi salvati.
alter table public.saved_bandi
  add column company_profile_id uuid references public.company_profiles (id) on delete cascade;

alter table public.saved_bandi drop constraint saved_bandi_unique;
alter table public.saved_bandi
  add constraint saved_bandi_unique
  unique nulls not distinct (user_id, company_profile_id, bando_id);
create index saved_bandi_company_idx
  on public.saved_bandi (company_profile_id, created_at desc)
  where company_profile_id is not null;

comment on column public.saved_bandi.company_profile_id is
  'Azienda a cui il salvataggio appartiene (Advisor multi-azienda). NULL = riga legacy/non-advisor, scope per user_id.';

-- 3b) Calendario: l'unicità "una scadenza per bando" è parziale (solo
--     tipo='bando'), quindi resta un indice unico parziale, con NULLS NOT
--     DISTINCT per deduplicare le righe legacy.
alter table public.calendar_events
  add column company_profile_id uuid references public.company_profiles (id) on delete cascade;

drop index public.calendar_events_one_per_bando;
create unique index calendar_events_one_per_bando
  on public.calendar_events (user_id, company_profile_id, bando_id)
  nulls not distinct
  where tipo = 'bando';
create index calendar_events_company_month_idx
  on public.calendar_events (company_profile_id, data)
  where company_profile_id is not null;

comment on column public.calendar_events.company_profile_id is
  'Azienda a cui l''evento appartiene (Advisor multi-azienda). NULL = riga legacy/non-advisor.';

-- 3c) Preferenze di filtro/notifica.
alter table public.user_preferences
  add column company_profile_id uuid references public.company_profiles (id) on delete cascade;

alter table public.user_preferences drop constraint user_preferences_unique;
alter table public.user_preferences
  add constraint user_preferences_unique
  unique nulls not distinct (user_id, company_profile_id, facet, ref_id);

comment on column public.user_preferences.company_profile_id is
  'Azienda a cui la preferenza appartiene (Advisor multi-azienda). NULL = riga legacy/non-advisor.';

-- 3d) Ledger degli alert inviati: l'idempotenza diventa per (utente, azienda,
--     bando) — un Advisor può ricevere lo stesso bando per due aziende diverse.
--     Il claim-by-insert del backend fa upsert con on_conflict su questo
--     vincolo, quindi resta un vincolo nominato (NULLS NOT DISTINCT).
alter table public.bando_alert_sends
  add column company_profile_id uuid references public.company_profiles (id) on delete cascade;

alter table public.bando_alert_sends drop constraint bando_alert_sends_unique;
alter table public.bando_alert_sends
  add constraint bando_alert_sends_unique
  unique nulls not distinct (user_id, company_profile_id, bando_id);

comment on column public.bando_alert_sends.company_profile_id is
  'Azienda per cui l''alert è stato inviato (Advisor multi-azienda). NULL = riga legacy/non-advisor.';

-- 3e) Notifiche in-app: dimensione azienda per il centro alert aggregato con
--     filtro. ON DELETE SET NULL: la notifica resta nello storico dell''utente
--     anche se l''azienda viene rimossa. L''unicità (user_id, dedup_key) resta:
--     la company va codificata nel dedup_key a monte.
alter table public.notifications
  add column company_profile_id uuid references public.company_profiles (id) on delete set null;

create index notifications_company_idx
  on public.notifications (user_id, company_profile_id, created_at desc)
  where company_profile_id is not null;

comment on column public.notifications.company_profile_id is
  'Azienda di riferimento della notifica (per il centro alert filtrabile). NULL = notifica non legata a una singola azienda.';

-- ----------------------------------------------------------------------------
-- 4) RPC (SECURITY DEFINER, revoke da public/anon/authenticated: pattern repo).
-- ----------------------------------------------------------------------------

-- Limite effettivo di aziende: override utente > default piano > 1.
-- Risolto sull'abbonamento PROPRIO dell'utente (un collegato famiglia non ha
-- abbonamento proprio → 1: i collegati non gestiscono aziende).
create or replace function public.fn_effective_max_aziende(p_user_id uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    (select max_aziende_override from public.profiles where id = p_user_id),
    (select sp.max_aziende
       from public.user_subscriptions us
       join public.subscription_plans sp on sp.id = us.plan_id
      where us.user_id = p_user_id and us.status = 'active'
      limit 1),
    1
  );
$$;

comment on function public.fn_effective_max_aziende(uuid) is
  'Limite effettivo di aziende gestibili dall''utente: coalesce(override profilo, max_aziende del piano attivo, 1).';

-- Crea un'azienda per un owner rispettando il limite (race-free: locka la riga
-- profilo, come fn_create_family_member). Conta solo le aziende VIVE.
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

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_owner_id, 'company.created', p_owner_id, p_owner_id,
          jsonb_build_object('company_profile_id', v_company_id,
                             'ragione_sociale', trim(p_ragione_sociale)));

  return v_company_id;
end;
$$;

comment on function public.fn_create_company(uuid, text, text) is
  'Crea un''azienda per l''owner applicando il limite max_aziende (race-free). Ragione sociale e P.IVA (11 cifre) obbligatorie. Ritorna l''id dell''azienda.';

-- Soft-delete di un'azienda dell'owner.
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

comment on function public.fn_soft_delete_company(uuid, uuid) is
  'Soft-delete di un''azienda dell''owner (imposta deleted_at). I dati collegati restano, il resolver dell''azienda attiva la rifiuta.';

revoke execute on function public.fn_effective_max_aziende(uuid) from public, anon, authenticated;
revoke execute on function public.fn_create_company(uuid, text, text) from public, anon, authenticated;
revoke execute on function public.fn_soft_delete_company(uuid, uuid) from public, anon, authenticated;

-- ----------------------------------------------------------------------------
-- 5) Backfill idempotente (solo Advisor): assegna i dati per-utente esistenti
--    all'unica azienda dell'Advisor ("prima azienda"). "Advisor" = piano
--    attivo con max_aziende > 1. I non-Advisor restano a NULL (scope legacy):
--    non si sintetizzano aziende (partita_iva/ragione_sociale sono NOT NULL).
--    Inerte finché il codice advisor non è attivo (nessuno legge la colonna).
-- ----------------------------------------------------------------------------
do $$
declare
  v_table text;
begin
  foreach v_table in array array[
    'saved_bandi', 'calendar_events', 'user_preferences', 'bando_alert_sends'
  ] loop
    execute format($f$
      update public.%1$I x
      set company_profile_id = c.id
      from public.company_profiles c
      where c.parent_id = x.user_id
        and c.deleted_at is null
        and x.company_profile_id is null
        and exists (
          select 1
          from public.user_subscriptions us
          join public.subscription_plans sp on sp.id = us.plan_id
          where us.user_id = x.user_id
            and us.status = 'active'
            and sp.max_aziende > 1
        )
    $f$, v_table);
  end loop;
end;
$$;

-- ============================================================================
-- Nota operativa: num_account_aziendali del piano Advisor NON viene modificato
-- qui. La mutua esclusività Advisor/collegati (v1) è imposta a livello
-- applicativo nella fase 2; ridurre il valore ora rischierebbe di rompere un
-- eventuale Advisor con collegati già attivi. Verificare a parte prima di
-- eventualmente portarlo a 1.
-- ============================================================================
