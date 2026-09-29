-- ============================================================================
-- BandoFit — DB primario, migration 0042: BOZZE AI DEI DOCUMENTI del
-- partenariato (WP10; piano docs/partenariati.md §2.1 T6-T8, §2.9 W4, §3 riga
-- 0042, §13 Q7).
--
--   1) subscription_plans.partner_bozze_mese — bozze al mese per titolare
--      (NULL = illimitato, 0 = esclusa, default 0: un piano nuovo nasce senza
--      bozze) + seed richiamabile per slug (Q7: gratuito 0, smart 3, pro 10,
--      advisor 30) e DO di verifica a WARNING (il DB del harness ha solo i
--      piani della 0002);
--   2) partner_bozze_documento — una riga per bozza richiesta (lettera
--      d'intenti, NDA, term sheet) da un'azienda che partecipa alla call:
--      input a whitelist costruito dal backend, contenuto generato, esito,
--      costi e token; una sola pending per azienda × call × tipo;
--   3) funzioni interne: fn_partner_bozze_limite / fn_partner_bozze_usate
--      (formula UNICA del conteggio mensile, per la RPC e per lo snapshot),
--      fn_partner_bozza_esecuzione_interrotta (costo ignoto + riga
--      timeout_unknown, come la 0037);
--   4) RPC: fn_partner_bozza_prenota (partecipazione, limite di piano sotto il
--      lock dell'owner, una pending, poi fn_partenariati_ai_prenota nel gruppo
--      altri con il tetto giornaliero per owner p_limite_owner),
--      fn_partner_bozza_concludi (chiusura ATOMICA di bozza ed
--      esecuzione), fn_partner_bozza_chiudi_stale (failsafe in lettura e
--      nello scheduler);
--   5) ridefinizioni con la STESSA firma (una sola funzione per nome):
--      - fn_partenariati_limiti (0036): aggiunge bozze_mese;
--      - fn_partenariati_snapshot (0039): aggiunge bozze_mese {limite, usate,
--        residuo, periodo_inizio, periodo_fine} per /me/entitlements.
--
-- Semantica del limite (Q7): mese solare Europe/Rome, pool del titolare su
-- tutte le sue aziende. Contano le bozze pending e ready e quelle in errore
-- con il modello chiamato o a costo ignoto (gli errori pagati contano); non
-- conta un errore chiuso senza LLM a costo 0 (la stessa esclusione di
-- fn_partenariati_ai_prenota, 0034) né un errore transitorio del provider
-- senza generazione (errore ai_non_disponibile). Nessuna scrittura in
-- ai_checks.
--
-- Chi agisce (T4, Q14): solo il titolare dell'azienda attiva (richiedente =
-- owner), per una call di cui l'azienda è la creatrice o un membro non uscito
-- del consorzio (partner_call_membri, 0040). Fuori partecipazione
-- call_non_trovata (il backend risponde 404).
--
-- ORDINE DEI LOCK (globale, quello della 0037 esteso dalla 0039 e dalla
-- 0040):
--   owner (profiles FOR UPDATE) → azienda che agisce (company_profiles FOR NO
--   KEY UPDATE) → call (FOR SHARE: la RPC non la modifica) → riga del membro
--   (FOR SHARE) → bozza pending della stessa azienda × call × tipo (FOR
--   UPDATE) → lock advisory del budget AI hashtext('partenariati_ai_budget')
--   per ULTIMO → righe di partenariati_ai_esecuzioni.
-- La chiusura e il failsafe bloccano la riga della bozza e poi quella
-- dell'esecuzione (mai il contrario) e non prendono il lock del budget.
--
-- ADDITIVA: una colonna nullable con default, una tabella e funzioni nuove;
-- due funzioni ridefinite con la stessa firma e lo stesso comportamento verso
-- il backend attuale (una chiave in più nel jsonb, che il backend ignora).
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Rollback
-- documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Colonna di piano. Le righe esistenti ricevono il default 0 (esclusa): il
--    seed qui sotto abilita i quattro piani standard.
-- ----------------------------------------------------------------------------
alter table public.subscription_plans
  add column partner_bozze_mese integer default 0
    constraint subscription_plans_partner_bozze_mese_check
    check (partner_bozze_mese is null or partner_bozze_mese >= 0);

comment on column public.subscription_plans.partner_bozze_mese is
  'Bozze AI dei documenti del partenariato (lettera d''intenti, NDA, term sheet) per mese solare (Europe/Rome), su tutte le aziende del titolare. NULL = illimitato, 0 = esclusa — semantica OPPOSTA ad alert_ritardo_giorni (dove NULL = esclusa). Contano anche le bozze in errore con il modello già chiamato.';

-- ----------------------------------------------------------------------------
-- 2) Seed per slug (Q7). Richiamabile e idempotente; revocato ai client (un
--    richiamo riporterebbe ai valori di Q7 i limiti modificati dall'admin).
-- ----------------------------------------------------------------------------
create or replace function public.fn_seed_limiti_bozze_0042()
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
      ('gratuito', 0),
      ('smart',    3),
      ('pro',      10),
      ('advisor',  30)
    ) as t(slug, bozze_mese)
  loop
    update public.subscription_plans
    set partner_bozze_mese = r.bozze_mese
    where slug = r.slug;
    if found then
      v_aggiornati := v_aggiornati + 1;
    end if;
  end loop;

  return jsonb_build_object('aggiornati', v_aggiornati, 'attesi', 4);
end;
$$;

comment on function public.fn_seed_limiti_bozze_0042() is
  'Seed del limite di bozze AI dei documenti per slug (Q7: gratuito 0, smart 3, pro 10, advisor 30). Gli altri piani non si toccano (restano al default 0 = esclusa).';

select public.fn_seed_limiti_bozze_0042();

-- Verifica: in produzione i quattro slug standard DEVONO esistere (0002). Se
-- uno manca, o ha un valore diverso da Q7, WARNING e non abort: la migration
-- resta valida e il limite si imposta da AdminPiani.
do $$
declare
  v_attesi  integer;
  v_esclusi text;
begin
  select count(*) into v_attesi
  from public.subscription_plans sp
  join (values
    ('gratuito', 0),
    ('smart',    3),
    ('pro',      10),
    ('advisor',  30)
  ) as t(slug, bozze_mese) on t.slug = sp.slug
  where sp.partner_bozze_mese is not distinct from t.bozze_mese;
  if v_attesi <> 4 then
    raise warning 'seed 0042: limite delle bozze impostato su % piani dei 4 attesi (gratuito, smart, pro, advisor): verificare gli slug in console e impostare il limite da AdminPiani', v_attesi;
  end if;

  select string_agg(slug, ', ' order by ordering) into v_esclusi
  from public.subscription_plans
  where slug not in ('gratuito', 'smart', 'pro', 'advisor')
    and partner_bozze_mese = 0;
  if v_esclusi is not null then
    raise notice 'seed 0042: i piani % restano senza bozze dei documenti (0) finché non li imposti da AdminPiani', v_esclusi;
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 3) Bozze dei documenti. Righe create SOLO da fn_partner_bozza_prenota,
--    chiuse SOLO da fn_partner_bozza_concludi, dal failsafe o da una nuova
--    prenotazione che trova la pending orfana. Cascade dalla call e
--    dall'azienda (Q21); il registro della spesa (senza FK) resta.
-- ----------------------------------------------------------------------------
create table public.partner_bozze_documento (
  id                   uuid primary key default gen_random_uuid(),
  partner_call_id      uuid not null
                         references public.partner_calls (id) on delete cascade,
  company_profile_id   uuid not null
                         references public.company_profiles (id) on delete cascade,
  family_parent_id     uuid not null,
  richiesta_da_user_id uuid not null,
  tipo                 text not null
    constraint pbd_tipo_check check (tipo in ('lettera_intenti', 'nda', 'term_sheet')),
  stato                text not null default 'pending'
    constraint pbd_stato_check check (stato in ('pending', 'ready', 'error')),
  input_snapshot       jsonb not null
    constraint pbd_input_snapshot_check check (jsonb_typeof(input_snapshot) = 'object'),
  contenuto            jsonb
    constraint pbd_contenuto_check
    check (contenuto is null or jsonb_typeof(contenuto) = 'object'),
  errore               text
    constraint pbd_errore_check
    check (errore is null or char_length(errore) between 1 and 200),
  esecuzione_id        uuid not null
    constraint pbd_esecuzione_key unique,
  avviata_at           timestamptz not null default now(),
  llm_eseguito         boolean not null default false,
  model                text,
  prompt_version       integer
    constraint pbd_prompt_version_check check (prompt_version is null or prompt_version >= 1),
  input_tokens         integer not null default 0
    constraint pbd_input_tokens_check check (input_tokens >= 0),
  output_tokens        integer not null default 0
    constraint pbd_output_tokens_check check (output_tokens >= 0),
  cost_cents           integer
    constraint pbd_cost_cents_check check (cost_cents is null or cost_cents >= 0),
  created_at           timestamptz not null default now(),
  ready_at             timestamptz,
  -- Contenuto solo (e sempre) per una bozza pronta, codice d'errore solo (e
  -- sempre) per una in errore; chiusa se e solo se non è più pending.
  constraint pbd_ready_ha_contenuto check ((stato = 'ready') = (contenuto is not null)),
  constraint pbd_error_ha_codice check ((stato = 'error') = (errore is not null)),
  constraint pbd_chiusa_coerente check ((stato = 'pending') = (ready_at is null)),
  -- Una pending non ha ancora esito né costo (ignoto: conta nel limite).
  constraint pbd_pending_senza_esito
    check (stato <> 'pending'
           or (cost_cents is null and not llm_eseguito
               and input_tokens = 0 and output_tokens = 0))
);

comment on table public.partner_bozze_documento is
  'Bozze AI dei documenti del partenariato (WP10, W4): lettera d''intenti, NDA, term sheet per una call a cui l''azienda partecipa (creatrice o membro non uscito). Scritture SOLO tramite le RPC della 0042. Verso il client solo la proiezione del backend (mai input_snapshot grezzo di altri, *_user_id, esecuzione_id). Non scrive mai in ai_checks.';
comment on column public.partner_bozze_documento.family_parent_id is
  'Titolare dell''azienda che ha chiesto la bozza: pool del limite mensile partner_bozze_mese. Senza FK.';
comment on column public.partner_bozze_documento.richiesta_da_user_id is
  'Titolare che ha avviato la bozza. Senza FK: MAI verso terzi.';
comment on column public.partner_bozze_documento.input_snapshot is
  'Input a WHITELIST inviato al modello, costruito dal backend (tipo, bando, forma, ruoli e quote con segnaposto stabili): mai bilanci, contatti, persone, P.IVA o nomi di altre aziende. Oggetto, al massimo 64 KB.';
comment on column public.partner_bozze_documento.contenuto is
  'Bozza generata e ripulita dal backend (titolo, sezioni, note per l''utente): oggetto, solo per stato ready. Il disclaimer legale NON è qui: lo aggiunge il backend a risposta e PDF.';
comment on column public.partner_bozze_documento.errore is
  'Codice d''errore (solo per stato error): interrotta = chiusa dal failsafe o da una nuova prenotazione (costo ignoto); ai_non_disponibile = errore transitorio del provider senza generazione (non conta nel limite mensile); ai_rete = nessuna risposta HTTP (costo ignoto, conta).';
comment on column public.partner_bozze_documento.esecuzione_id is
  'Esecuzione nel registro unico della spesa (partenariati_ai_esecuzioni, servizio partner_bozza). Senza FK: il registro sopravvive alle cancellazioni.';
comment on column public.partner_bozze_documento.llm_eseguito is
  'true se la chiamata al modello ha prodotto costo o token. Un errore senza LLM a costo 0 non conta nel limite mensile, né un errore ai_non_disponibile (errore transitorio del provider, costo ignoto).';
comment on column public.partner_bozze_documento.cost_cents is
  'Costo in CENTESIMI DI USD, copia di quello dell''esecuzione; NULL = ignoto (in corso, interrotta, errore senza usage): un errore a costo ignoto conta nel limite mensile, tranne ai_non_disponibile.';
comment on column public.partner_bozze_documento.ready_at is
  'Istante di chiusura della bozza (ready o error); NULL finché è pending.';

-- Una sola bozza in preparazione per azienda × call × tipo.
create unique index partner_bozze_one_pending on public.partner_bozze_documento
  (company_profile_id, partner_call_id, tipo)
  where stato = 'pending';
-- Conteggio mensile del pool del titolare (fn_partner_bozze_usate).
create index partner_bozze_owner_mese_idx on public.partner_bozze_documento
  (family_parent_id, created_at);
-- Lista delle bozze dell'azienda sulla call; cascade dall'azienda.
create index partner_bozze_azienda_call_idx on public.partner_bozze_documento
  (company_profile_id, partner_call_id, created_at desc);
-- Cascade dalla call.
create index partner_bozze_call_idx on public.partner_bozze_documento (partner_call_id);
-- Failsafe.
create index partner_bozze_pending_idx on public.partner_bozze_documento (avviata_at)
  where stato = 'pending';

-- ----------------------------------------------------------------------------
-- 4) Funzioni interne.
-- ----------------------------------------------------------------------------

-- Bozze usate nel mese solare Europe/Rome dal pool dell'owner: pending e
-- ready di tutte le sue aziende, più quelle in errore con il modello chiamato
-- (costo o token) o a costo ignoto, tranne ai_non_disponibile (il provider ha
-- risposto con un errore transitorio, 429/5xx/529, senza generare nulla: il
-- costo resta ignoto per il budget, ma la bozza non conta). Formula UNICA:
-- RPC e snapshot.
create or replace function public.fn_partner_bozze_usate(p_owner uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select count(*)::integer
  from public.partner_bozze_documento
  where family_parent_id = p_owner
    and created_at >= (date_trunc('month', now() at time zone 'Europe/Rome')
                       at time zone 'Europe/Rome')
    and created_at < ((date_trunc('month', now() at time zone 'Europe/Rome')
                       + interval '1 month') at time zone 'Europe/Rome')
    and (stato in ('pending', 'ready')
         or llm_eseguito
         or (cost_cents is distinct from 0
             and errore is distinct from 'ai_non_disponibile'));
$$;

comment on function public.fn_partner_bozze_usate(uuid) is
  'Bozze dei documenti del mese solare corrente Europe/Rome di tutte le aziende dell''owner (pool, Q7): pending e ready, più le error con LLM eseguito o costo ignoto/positivo (gli errori pagati contano; un errore senza LLM a costo 0 no, né ai_non_disponibile: errore transitorio del provider senza generazione). Unica formula di fn_partner_bozza_prenota e fn_partenariati_snapshot.';

-- Limite mensile del piano: NULL = illimitato; chiave assente o risposta non
-- valida = 0 (fail-closed), come fn_partner_candidature_limite (0039).
create or replace function public.fn_partner_bozze_limite(p_owner uuid)
returns integer
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_lim jsonb;
begin
  v_lim := public.fn_partenariati_limiti(p_owner);
  if v_lim is null or jsonb_typeof(v_lim) <> 'object' or not (v_lim ? 'bozze_mese') then
    return 0;
  end if;
  return (v_lim ->> 'bozze_mese')::integer;
end;
$$;

comment on function public.fn_partner_bozze_limite(uuid) is
  'Interna: bozze_mese di fn_partenariati_limiti (0042). NULL = illimitato, 0 = esclusa; risposta senza la chiave = 0 (fail-closed).';

-- Chiude un'esecuzione partner_bozza rimasta in_corso senza più un job che la
-- chiuda (riavvio, crash, cancellazione a metà): la chiamata al modello può
-- essere partita e addebitata, quindi costo ignoto (NULL: la riserva resta
-- nel budget) e una riga timeout_unknown in api_usage_events con la riserva,
-- come fn_partner_call_ai_esecuzione_interrotta (0037). Chi chiude
-- l'esecuzione ne registra il consumo: una sola riga per esecuzione.
create or replace function public.fn_partner_bozza_esecuzione_interrotta(
  p_esecuzione_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_exec public.partenariati_ai_esecuzioni%rowtype;
begin
  select * into v_exec from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id and servizio = 'partner_bozza' and stato = 'in_corso'
  for update;
  if not found then
    return false;
  end if;

  insert into public.api_usage_events
    (user_id, family_parent_id, provider, service, outcome, cost_cents, request_meta)
  values
    (v_exec.richiedente_user_id, v_exec.owner_id, 'anthropic', v_exec.servizio,
     'timeout_unknown', v_exec.costo_riservato_cents,
     jsonb_build_object('company_profile_id', v_exec.company_profile_id,
                        'bando_id', v_exec.bando_id, 'esecuzione_id', v_exec.id,
                        'esito', 'interrotta', 'failsafe', true));
  perform public.fn_partenariati_ai_concludi(
    p_esecuzione_id, 'interrotta', null, 0, 0, null, 'interrotta');
  return true;
end;
$$;

comment on function public.fn_partner_bozza_esecuzione_interrotta(uuid) is
  'Chiude un''esecuzione partner_bozza ancora in_corso come interrotta a costo ignoto (la riserva resta nel budget) e scrive la sua riga timeout_unknown in api_usage_events con la riserva. Già chiusa, inesistente o di un altro servizio → nessun effetto (false).';

-- ----------------------------------------------------------------------------
-- 5) fn_partner_bozza_prenota — prenotazione ATOMICA di una bozza. Nell'ordine:
--      - parametri (tipo, input oggetto ≤ 64 KB, riserva ≥ 0, versione del
--        prompt ≥ 1 se indicata) → parametri_non_validi; richiedente =
--        titolare → attore_non_titolare;
--      - lock owner → azienda viva (owner_not_found | company_not_found);
--      - call FOR SHARE; partecipazione: azienda creatrice (qualunque stato)
--        oppure membro NON uscito del consorzio (riga FOR SHARE) di una call
--        non sospesa per moderazione; altrimenti call_non_trovata;
--      - piano con 0 bozze al mese (o senza abbonamento attivo) →
--        funzione_non_inclusa;
--      - bozza pending della stessa azienda × call × tipo avviata da meno di
--        10 minuti → bozza_in_corso (se più vecchia è orfana: si chiude sotto
--        il lock del budget, prima della nuova prenotazione);
--      - bozze del mese ≥ limite → bozze_esaurite (NULL = illimitato);
--      - lock del budget; fn_partenariati_ai_prenota('partner_bozza',
--        'utente', 'altri', …) con il tetto giornaliero per owner
--        p_limite_owner (esecuzioni partner_bozza dell'owner nel giorno
--        Europe/Rome, su tutte le sue aziende, con l'esclusione della 0034;
--        NULL = nessun tetto) → ai_limite_owner, poi il budget →
--        ai_budget_esaurito. Il tetto giornaliero impedisce che un piano
--        illimitato consumi in un giorno il budget «altri», condiviso con la
--        bozza del profilo (WP4) e le proposte della call (WP5); nessun
--        limite per richiedente (richiedente = owner);
--      - insert della bozza pending con l'esecuzione.
--    Ritorna {bozza_id, esecuzione_id}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_prenota(
  p_owner                 uuid,
  p_company               uuid,
  p_call                  uuid,
  p_richiedente           uuid,
  p_tipo                  text,
  p_input                 jsonb,
  p_budget_cents          integer,
  p_costo_riservato_cents integer,
  p_prompt_version        integer default null,
  p_limite_owner          integer default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call    public.partner_calls%rowtype;
  v_limite  integer;
  v_pending public.partner_bozze_documento%rowtype;
  v_esec    uuid;
  v_id      uuid;
begin
  if p_owner is null or p_company is null or p_call is null
     or p_tipo is null or p_tipo not in ('lettera_intenti', 'nda', 'term_sheet')
     or jsonb_typeof(p_input) is distinct from 'object'
     or octet_length(p_input::text) > 65536
     or p_costo_riservato_cents is null or p_costo_riservato_cents < 0
     or p_prompt_version < 1 then
    raise exception 'Parametri della bozza non validi' using detail = 'parametri_non_validi';
  end if;
  if p_richiedente is null or p_richiedente is distinct from p_owner then
    raise exception 'Le bozze dei documenti le avvia il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform public.fn_partner_call_blocca_azienda(p_owner, p_company, true);

  select * into v_call from public.partner_calls where id = p_call for share;
  if not found then
    raise exception 'Call di partenariato non trovata' using detail = 'call_non_trovata';
  end if;
  if not (v_call.company_profile_id = p_company and v_call.family_parent_id = p_owner) then
    -- Membro del consorzio: una call sospesa non si legge (come nel backend).
    if v_call.stato = 'sospesa_moderazione' or v_call.sospesa_at is not null then
      raise exception 'Call di partenariato non trovata' using detail = 'call_non_trovata';
    end if;
    perform 1 from public.partner_call_membri
    where partner_call_id = p_call and company_profile_id = p_company and stato <> 'uscito'
    for share;
    if not found then
      raise exception 'Call di partenariato non trovata' using detail = 'call_non_trovata';
    end if;
  end if;

  v_limite := public.fn_partner_bozze_limite(p_owner);
  if v_limite is not null and v_limite <= 0 then
    raise exception 'Il tuo piano non include le bozze dei documenti'
      using detail = 'funzione_non_inclusa';
  end if;

  select * into v_pending from public.partner_bozze_documento
  where company_profile_id = p_company and partner_call_id = p_call and tipo = p_tipo
    and stato = 'pending'
  for update;
  if found and v_pending.avviata_at > now() - interval '10 minutes' then
    raise exception 'La bozza di questo documento è già in preparazione'
      using detail = 'bozza_in_corso';
  end if;

  -- Sotto il lock dell'owner: nessuna prenotazione concorrente dello stesso
  -- pool. Una pending orfana conta già (pending ora, errore a costo ignoto
  -- dopo la chiusura).
  if v_limite is not null and public.fn_partner_bozze_usate(p_owner) >= v_limite then
    raise exception 'Hai usato tutte le bozze di documenti di questo mese'
      using detail = 'bozze_esaurite';
  end if;

  -- Dopo i lock di riga (ordine globale).
  perform pg_advisory_xact_lock(hashtext('partenariati_ai_budget'));

  if v_pending.id is not null then
    update public.partner_bozze_documento
    set stato    = 'error',
        errore   = 'interrotta',
        ready_at = now()
    where id = v_pending.id;
    perform public.fn_partner_bozza_esecuzione_interrotta(v_pending.esecuzione_id);
  end if;

  v_esec := public.fn_partenariati_ai_prenota(
    p_servizio              => 'partner_bozza',
    p_origine               => 'utente',
    p_gruppo                => 'altri',
    p_budget_cents          => p_budget_cents,
    p_costo_riservato_cents => p_costo_riservato_cents,
    p_richiedente           => p_richiedente,
    p_limite_richiedente    => null,
    p_owner                 => p_owner,
    p_limite_owner          => p_limite_owner,
    p_company               => p_company,
    p_bando_id              => v_call.bando_id);

  begin
    insert into public.partner_bozze_documento
      (partner_call_id, company_profile_id, family_parent_id, richiesta_da_user_id, tipo,
       input_snapshot, esecuzione_id, prompt_version)
    values
      (p_call, p_company, p_owner, p_richiedente, p_tipo, p_input, v_esec, p_prompt_version)
    returning id into v_id;
  exception when unique_violation then
    -- Rete di sicurezza dell'indice partner_bozze_one_pending (le prenotazioni
    -- dello stesso owner sono già serializzate dal suo lock).
    raise exception 'La bozza di questo documento è già in preparazione'
      using detail = 'bozza_in_corso';
  end;

  return jsonb_build_object('bozza_id', v_id, 'esecuzione_id', v_esec);
end;
$$;

comment on function public.fn_partner_bozza_prenota(uuid, uuid, uuid, uuid, text, jsonb, integer, integer, integer, integer) is
  'Prenota una bozza AI di un documento del partenariato (lock owner → azienda viva → call FOR SHARE → membro FOR SHARE → bozza pending → budget): richiedente = titolare; azienda creatrice della call o membro non uscito di una call non sospesa, altrimenti call_non_trovata; piano con 0 bozze → funzione_non_inclusa; pending della stessa azienda × call × tipo < 10 minuti → bozza_in_corso (più vecchia: chiusa come interrotta a costo ignoto); bozze del mese (fn_partner_bozze_usate, pool dell''owner, Europe/Rome) ≥ limite → bozze_esaurite (NULL = illimitato); poi fn_partenariati_ai_prenota(partner_bozza, utente, altri) con il tetto giornaliero per owner p_limite_owner (esecuzioni partner_bozza dell''owner nel giorno, NULL = nessun tetto). Ritorna {bozza_id, esecuzione_id}. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | company_not_found | call_non_trovata | funzione_non_inclusa | bozza_in_corso | bozze_esaurite | ai_limite_owner | ai_budget_esaurito.';

-- ----------------------------------------------------------------------------
-- 6) fn_partner_bozza_concludi — chiusura ATOMICA del job della bozza (modello
--    fn_partner_call_ai_concludi, 0037). Nella stessa transazione:
--      - la bozza diventa ready (con il contenuto, un oggetto) o error (con il
--        codice), con costo, token e modello, SOLO se è ancora pending con
--        questa esecuzione (il failsafe o una nuova prenotazione vincono);
--      - l'esecuzione si chiude con costo e token (fn_partenariati_ai_concludi)
--        SOLO se è ancora in_corso (anche se la bozza non esiste più).
--    llm_eseguito = costo o token > 0 (come la 0034). Ritorna {bozza_scritta,
--    esecuzione_chiusa}: il job registra il consumo solo se ha chiuso lui
--    l'esecuzione (chi chiude registra, una volta).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_concludi(
  p_bozza         uuid,
  p_esecuzione_id uuid,
  p_bozza_stato   text,
  p_contenuto     jsonb,
  p_bozza_errore  text,
  p_stato         text,
  p_cost_cents    integer,
  p_input_tokens  integer,
  p_output_tokens integer,
  p_model         text,
  p_errore        text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_scritta boolean;
  v_chiusa  boolean;
begin
  if p_bozza is null or p_esecuzione_id is null
     or p_bozza_stato is null or p_bozza_stato not in ('ready', 'error')
     or (p_bozza_stato = 'ready' and jsonb_typeof(p_contenuto) is distinct from 'object')
     or (p_bozza_stato = 'error'
         and (p_bozza_errore is null or char_length(p_bozza_errore) not between 1 and 200))
     or p_stato is null or p_stato not in ('conclusa', 'errore', 'timeout', 'interrotta')
     or p_cost_cents < 0 or p_input_tokens < 0 or p_output_tokens < 0 then
    raise exception 'Parametri di chiusura della bozza non validi'
      using detail = 'parametri_non_validi';
  end if;

  update public.partner_bozze_documento
  set stato         = p_bozza_stato,
      contenuto     = case when p_bozza_stato = 'ready' then p_contenuto end,
      errore        = case when p_bozza_stato = 'error' then p_bozza_errore end,
      cost_cents    = p_cost_cents,
      input_tokens  = coalesce(p_input_tokens, 0),
      output_tokens = coalesce(p_output_tokens, 0),
      model         = coalesce(p_model, model),
      llm_eseguito  = coalesce(p_cost_cents, 0) > 0
                      or coalesce(p_input_tokens, 0) > 0
                      or coalesce(p_output_tokens, 0) > 0,
      ready_at      = now()
  where id = p_bozza
    and esecuzione_id = p_esecuzione_id
    and stato = 'pending';
  v_scritta := found;

  perform 1 from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id and servizio = 'partner_bozza' and stato = 'in_corso'
  for update;
  v_chiusa := found;
  if v_chiusa then
    perform public.fn_partenariati_ai_concludi(
      p_esecuzione_id, p_stato, p_cost_cents, p_input_tokens, p_output_tokens, p_model,
      p_errore);
  end if;

  return jsonb_build_object('bozza_scritta', v_scritta, 'esecuzione_chiusa', v_chiusa);
end;
$$;

comment on function public.fn_partner_bozza_concludi(uuid, uuid, text, jsonb, text, text, integer, integer, integer, text, text) is
  'Chiusura atomica del job di una bozza dei documenti: bozza ready (contenuto oggetto) | error (codice) con costo, token, modello e llm_eseguito solo se è ancora pending con questa esecuzione, e nella stessa transazione esecuzione partner_bozza chiusa (fn_partenariati_ai_concludi: conclusa | errore | timeout | interrotta) solo se ancora in_corso. Ritorna {bozza_scritta, esecuzione_chiusa}. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 7) fn_partner_bozza_chiudi_stale — failsafe (in lettura e nello scheduler),
--    soglia p_minuti (NULL = 10, minimo 1):
--      - le bozze pending avviate da almeno la soglia diventano error
--        (interrotta, costo ignoto: contano nel limite mensile) e la loro
--        esecuzione si chiude con fn_partner_bozza_esecuzione_interrotta
--        (riga timeout_unknown con la riserva);
--      - le esecuzioni partner_bozza ancora in_corso, avviate da almeno la
--        soglia e che nessuna bozza pending referenzia più (call o azienda
--        cancellata durante il job, chiusura rimasta a metà) si chiudono allo
--        stesso modo.
--    Salta le righe bloccate. Ritorna quante bozze ed esecuzioni ha chiuso.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_chiudi_stale(p_minuti integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_soglia constant timestamptz :=
    now() - make_interval(mins => greatest(coalesce(p_minuti, 10), 1));
  v_row record;
  v_id  uuid;
  v_n   integer := 0;
begin
  for v_row in
    select id, esecuzione_id from public.partner_bozze_documento
    where stato = 'pending' and avviata_at <= v_soglia
    order by avviata_at
    for update skip locked
  loop
    update public.partner_bozze_documento
    set stato    = 'error',
        errore   = 'interrotta',
        ready_at = now()
    where id = v_row.id;
    perform public.fn_partner_bozza_esecuzione_interrotta(v_row.esecuzione_id);
    v_n := v_n + 1;
  end loop;

  for v_id in
    select e.id from public.partenariati_ai_esecuzioni e
    where e.servizio = 'partner_bozza'
      and e.stato = 'in_corso'
      and e.avviata_at <= v_soglia
      and not exists (
        select 1 from public.partner_bozze_documento b
        where b.esecuzione_id = e.id and b.stato = 'pending')
    order by e.avviata_at
    for update of e skip locked
  loop
    if public.fn_partner_bozza_esecuzione_interrotta(v_id) then
      v_n := v_n + 1;
    end if;
  end loop;

  return v_n;
end;
$$;

comment on function public.fn_partner_bozza_chiudi_stale(integer) is
  'Failsafe delle bozze dei documenti (soglia p_minuti, NULL = 10, minimo 1): bozze pending oltre la soglia → error interrotta (costo ignoto) e loro esecuzione chiusa da fn_partner_bozza_esecuzione_interrotta; esecuzioni partner_bozza in_corso oltre la soglia che nessuna bozza pending referenzia → stessa chiusura. Salta le righe bloccate. Ritorna quante bozze ed esecuzioni ha chiuso.';

-- ----------------------------------------------------------------------------
-- 8) Ridefinizione (STESSA firma) di fn_partenariati_limiti (0036): aggiunge
--    bozze_mese (NULL = illimitato; senza abbonamento attivo 0).
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
  v_bozze integer;  -- 0042
begin
  select sp.partner_calls_attive_max, sp.partner_candidature_mese, sp.partner_bozze_mese
    into v_calls, v_cand, v_bozze
  from public.user_subscriptions us
  join public.subscription_plans sp on sp.id = us.plan_id
  where us.user_id = p_owner and us.status = 'active'
  limit 1;

  if not found then
    -- Senza abbonamento attivo (owner inesistente, collegato, abbonamento
    -- scaduto o disdetto): niente partenariati.
    return jsonb_build_object(
      'calls_attive_max', 0, 'candidature_mese', 0, 'bozze_mese', 0, 'piano_attivo', false);
  end if;

  -- NULL resta null nel jsonb: illimitato.
  return jsonb_build_object(
    'calls_attive_max', v_calls, 'candidature_mese', v_cand, 'bozze_mese', v_bozze,
    'piano_attivo', true);
end;
$$;

comment on function public.fn_partenariati_limiti(uuid) is
  'Limiti di partenariato del piano attivo del titolare: {calls_attive_max, candidature_mese, bozze_mese, piano_attivo}. null = illimitato, 0 = esclusa; senza abbonamento attivo {0, 0, 0, false}. bozze_mese dalla 0042.';

-- ----------------------------------------------------------------------------
-- 9) Ridefinizione (STESSA firma) di fn_partenariati_snapshot (0039): aggiunge
--    bozze_mese con la stessa forma di candidature_mese; usate =
--    fn_partner_bozze_usate (la stessa formula della RPC), residuo = limite −
--    usate (mai negativo), NULL = illimitato.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariati_snapshot(p_owner uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_calls      integer;
  v_cand       integer;
  v_usate      integer;
  v_cand_usate integer;
  v_bozze       integer;  -- 0042
  v_bozze_usate integer;  -- 0042
  v_inizio     date := date_trunc('month', now() at time zone 'Europe/Rome')::date;
begin
  v_calls := public.fn_partner_call_limite_attive(p_owner);
  v_cand := public.fn_partner_candidature_limite(p_owner);
  v_usate := public.fn_partner_calls_attive_usate(p_owner);
  v_cand_usate := public.fn_partner_candidature_usate(p_owner);
  v_bozze := public.fn_partner_bozze_limite(p_owner);
  v_bozze_usate := public.fn_partner_bozze_usate(p_owner);

  return jsonb_build_object(
    'call_attive', jsonb_build_object(
      'limite', v_calls,
      'usate', v_usate,
      'residuo', case when v_calls is null then null else greatest(v_calls - v_usate, 0) end),
    'candidature_mese', jsonb_build_object(
      'limite', v_cand,
      'usate', v_cand_usate,
      'residuo', case when v_cand is null then null
                      else greatest(v_cand - v_cand_usate, 0) end,
      'periodo_inizio', v_inizio,
      'periodo_fine', (v_inizio + interval '1 month' - interval '1 day')::date),
    'bozze_mese', jsonb_build_object(
      'limite', v_bozze,
      'usate', v_bozze_usate,
      'residuo', case when v_bozze is null then null
                      else greatest(v_bozze - v_bozze_usate, 0) end,
      'periodo_inizio', v_inizio,
      'periodo_fine', (v_inizio + interval '1 month' - interval '1 day')::date));
end;
$$;

comment on function public.fn_partenariati_snapshot(uuid) is
  'Limiti e consumi di partenariato del titolare per /me/entitlements: {call_attive: {limite, usate, residuo}, candidature_mese: {limite, usate, residuo, periodo_inizio, periodo_fine}, bozze_mese: {limite, usate, residuo, periodo_inizio, periodo_fine}} (NULL = illimitato, mese solare Europe/Rome). Candidature usate = fn_partner_candidature_usate (0039), bozze usate = fn_partner_bozze_usate (0042): le stesse formule delle RPC.';

-- ----------------------------------------------------------------------------
-- 10) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite sulla tabella nuova; nessuna funzione della migration (seed,
--    interne e ridefinite comprese) eseguibile dai ruoli esposti (Supabase
--    concede EXECUTE di default a PUBLIC).
-- ----------------------------------------------------------------------------
alter table public.partner_bozze_documento enable row level security;

revoke all on public.partner_bozze_documento from anon, authenticated;

revoke execute on function public.fn_seed_limiti_bozze_0042()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozze_usate(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozze_limite(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_esecuzione_interrotta(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_prenota(uuid, uuid, uuid, uuid, text, jsonb, integer, integer, integer, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_concludi(uuid, uuid, text, jsonb, text, text, integer, integer, integer, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_chiudi_stale(integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariati_limiti(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariati_snapshot(uuid)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0042 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- delle bozze e seleziona la colonna partner_bozze_mese).
-- 1) Ripristino delle due funzioni ridefinite (stessa firma; le revoche
--    restano): rieseguire il blocco «create or replace function
--    public.fn_partenariati_snapshot» del file
--    0039_partenariato_candidature_chat.sql e il blocco «create or replace
--    function public.fn_partenariati_limiti» del file
--    0036_piani_partenariato.sql. Va fatto PRIMA dei punti 2 e 3: le
--    versioni 0042 usano fn_partner_bozze_* e partner_bozze_mese.
-- 2) drop function public.fn_partner_bozza_chiudi_stale(integer);
--    drop function public.fn_partner_bozza_concludi(uuid, uuid, text, jsonb, text, text,
--      integer, integer, integer, text, text);
--    drop function public.fn_partner_bozza_prenota(uuid, uuid, uuid, uuid, text, jsonb,
--      integer, integer, integer, integer);
--    drop function public.fn_partner_bozza_esecuzione_interrotta(uuid);
--    drop function public.fn_partner_bozze_limite(uuid);
--    drop function public.fn_partner_bozze_usate(uuid);
--    drop function public.fn_seed_limiti_bozze_0042();
-- 3) drop table public.partner_bozze_documento;   -- si perdono le bozze
--    alter table public.subscription_plans
--      drop column partner_bozze_mese;           -- si perdono i limiti impostati
-- Le esecuzioni partner_bozza nel registro della spesa (0034) e le righe di
-- api_usage_events restano (registri senza FK).
-- ============================================================================
