-- ============================================================================
-- BandoFit — DB primario, migration 0036: LIMITI DI PIANO del modulo
-- partenariati (WP5, prima parte; piano docs/partenariati.md §2.5 C5, §3 riga
-- 0036, §13 Q1/Q18).
--
--   1) subscription_plans.partner_calls_attive_max e partner_candidature_mese
--      — NULL = illimitato, 0 = esclusa (semantica OPPOSTA ad
--      alert_ritardo_giorni, dove NULL = esclusa). Default 0: un piano nuovo
--      creato dall'admin nasce senza partenariati;
--   2) fn_seed_limiti_partenariato_0036 — seed richiamabile per slug (Q1:
--      gratuito 0/0, smart 1/5, pro 3/20, advisor 10/50; gli altri piani,
--      tailored compreso, restano a 0 finché l'admin non li imposta) + DO di
--      verifica a WARNING (pattern 0030: il DB del harness ha solo i piani
--      della 0002);
--   3) fn_partenariati_limiti(p_owner) — i due limiti del piano ATTIVO del
--      titolare in un jsonb, letti come fa fn_entitlement_detail (che NON si
--      tocca). Senza abbonamento attivo: 0/0 (fail-closed).
--
-- Lo snapshot per /me/entitlements (fn_partenariati_snapshot) conta le call
-- di partner_calls, che nasce nella 0037: vive lì.
--
-- ADDITIVA: due colonne nullable con default e funzioni nuove; nessuna
-- funzione esistente ridefinita. Il backend attuale seleziona le colonne del
-- piano per nome, quindi ignora quelle nuove. Da eseguire IN UN'UNICA
-- TRANSAZIONE (begin; ... commit;). Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Colonne di piano. Le righe esistenti ricevono il default 0 (esclusa):
--    il seed qui sotto abilita i quattro piani standard.
-- ----------------------------------------------------------------------------
alter table public.subscription_plans
  add column partner_calls_attive_max integer default 0
    constraint subscription_plans_partner_calls_attive_max_check
    check (partner_calls_attive_max is null or partner_calls_attive_max >= 0),
  add column partner_candidature_mese integer default 0
    constraint subscription_plans_partner_candidature_mese_check
    check (partner_candidature_mese is null or partner_candidature_mese >= 0);

comment on column public.subscription_plans.partner_calls_attive_max is
  'Call di partenariato attive (pubblicate o sospese per moderazione) che il titolare può avere su tutte le sue aziende. NULL = illimitato, 0 = esclusa — semantica OPPOSTA ad alert_ritardo_giorni (dove NULL = esclusa). Le bozze hanno un tetto a parte per azienda.';
comment on column public.subscription_plans.partner_candidature_mese is
  'Candidature spontanee a call di partenariato per mese solare (Europe/Rome), su tutte le aziende del titolare. NULL = illimitato, 0 = esclusa — semantica OPPOSTA ad alert_ritardo_giorni (dove NULL = esclusa).';

-- ----------------------------------------------------------------------------
-- 2) Seed per slug (Q1). Richiamabile e idempotente; revocato ai client (un
--    richiamo riporterebbe ai valori di Q1 i limiti modificati dall'admin).
-- ----------------------------------------------------------------------------
create or replace function public.fn_seed_limiti_partenariato_0036()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  r record;
  v_aggiornati integer := 0;
begin
  for r in
    select * from (values
      ('gratuito', 0,  0),
      ('smart',    1,  5),
      ('pro',      3,  20),
      ('advisor',  10, 50)
    ) as t(slug, calls_attive_max, candidature_mese)
  loop
    update public.subscription_plans
    set partner_calls_attive_max = r.calls_attive_max,
        partner_candidature_mese = r.candidature_mese
    where slug = r.slug;
    if found then
      v_aggiornati := v_aggiornati + 1;
    end if;
  end loop;

  return jsonb_build_object('aggiornati', v_aggiornati, 'attesi', 4);
end;
$$;

comment on function public.fn_seed_limiti_partenariato_0036() is
  'Seed dei limiti di partenariato per slug (Q1: gratuito 0/0, smart 1/5, pro 3/20, advisor 10/50). Gli altri piani non si toccano (restano al default 0 = esclusa).';

select public.fn_seed_limiti_partenariato_0036();

-- Verifica: in produzione i quattro slug standard DEVONO esistere (0002). Se
-- uno manca, o ha valori diversi da Q1, WARNING e non abort: la migration
-- resta valida e i limiti si impostano da AdminPiani.
do $$
declare
  v_attesi   integer;
  v_esclusi  text;
begin
  select count(*) into v_attesi
  from public.subscription_plans sp
  join (values
    ('gratuito', 0,  0),
    ('smart',    1,  5),
    ('pro',      3,  20),
    ('advisor',  10, 50)
  ) as t(slug, calls_attive_max, candidature_mese) on t.slug = sp.slug
  where sp.partner_calls_attive_max is not distinct from t.calls_attive_max
    and sp.partner_candidature_mese is not distinct from t.candidature_mese;
  if v_attesi <> 4 then
    raise warning 'seed 0036: limiti di partenariato impostati su % piani dei 4 attesi (gratuito, smart, pro, advisor): verificare gli slug in console e impostare i limiti da AdminPiani', v_attesi;
  end if;

  select string_agg(slug, ', ' order by ordering) into v_esclusi
  from public.subscription_plans
  where slug not in ('gratuito', 'smart', 'pro', 'advisor')
    and partner_calls_attive_max = 0 and partner_candidature_mese = 0;
  if v_esclusi is not null then
    raise notice 'seed 0036: i piani % restano senza partenariati (0) finché non li imposti da AdminPiani', v_esclusi;
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 3) Limiti del piano attivo del titolare. Stessa lettura di
--    fn_entitlement_detail (abbonamento status = 'active', al più uno per
--    l'indice user_subscriptions_one_active); il pool è per titolare (Q1):
--    le RPC della 0037 e della 0039 passano sempre l'owner della famiglia.
--    Nessun lock qui: chi applica il limite blocca prima la riga profiles
--    dell'owner (modello fn_create_company) e poi chiama questa funzione.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariati_limiti(p_owner uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_calls integer;
  v_cand  integer;
begin
  select sp.partner_calls_attive_max, sp.partner_candidature_mese
    into v_calls, v_cand
  from public.user_subscriptions us
  join public.subscription_plans sp on sp.id = us.plan_id
  where us.user_id = p_owner and us.status = 'active'
  limit 1;

  if not found then
    -- Senza abbonamento attivo (owner inesistente, collegato, abbonamento
    -- scaduto o disdetto): niente partenariati.
    return jsonb_build_object(
      'calls_attive_max', 0, 'candidature_mese', 0, 'piano_attivo', false);
  end if;

  -- NULL resta null nel jsonb: illimitato.
  return jsonb_build_object(
    'calls_attive_max', v_calls, 'candidature_mese', v_cand, 'piano_attivo', true);
end;
$$;

comment on function public.fn_partenariati_limiti(uuid) is
  'Limiti di partenariato del piano attivo del titolare: {calls_attive_max, candidature_mese, piano_attivo}. null = illimitato, 0 = esclusa; senza abbonamento attivo {0, 0, false}.';

-- ----------------------------------------------------------------------------
-- 4) Permessi: nessuna esecuzione dai ruoli esposti (seed compreso).
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_seed_limiti_partenariato_0036() from public, anon, authenticated;
revoke execute on function public.fn_partenariati_limiti(uuid) from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0036 (eseguire in transazione, DOPO aver annullato la 0037, che
-- chiama fn_partenariati_limiti, e riportato il backend alla versione
-- precedente: quella nuova seleziona le due colonne).
-- 1) drop function public.fn_partenariati_limiti(uuid);
--    drop function public.fn_seed_limiti_partenariato_0036();
-- 2) alter table public.subscription_plans
--      drop column partner_calls_attive_max,
--      drop column partner_candidature_mese;   -- si perdono i limiti impostati
-- ============================================================================
