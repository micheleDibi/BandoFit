-- ============================================================================
-- BandoFit — DB primario, migration 0038: MATCHING DEI PARTENARIATI (WP6,
-- piano docs/partenariati.md §2.1 T3/T8, §2.6 M1-M6, §3 riga 0038, §13
-- Q6/Q11/Q12/Q17/Q24/Q25).
--
--   1) company_collegamenti + company_collegamenti_stato — chiavi dei
--      collegamenti societari (M2, Q17): SOLO digest HMAC-SHA256 esadecimali
--      (hmac_dominio("partenariati.collegamenti.v1", valore normalizzato),
--      calcolati in Python da services/partenariato_collegamenti.py), mai
--      P.IVA, CF o nomi in chiaro; il marker con la versione dell'algoritmo
--      rende il matching fail-closed (senza marker aggiornato l'azienda non
--      si suggerisce);
--   2) partner_notifiche_proattive + fn_partner_claim_notifica — notifiche
--      «call per te» alla pubblicazione (M5): dedup per azienda × call e tetto
--      per azienda e settimana ISO (Europe/Rome), serializzati da un lock
--      advisory per azienda;
--   3) partner_calls.fanout_claim_at / fanout_completato_at +
--      fn_partner_fanout_claim — claim del fan-out con scadenza, riprendibile
--      dallo scheduler se il processo muore a metà;
--   4) partner_email_settings (digest ed email di evento, token di
--      disiscrizione proprio, Q6), partner_digest_runs (claim settimanale per
--      INSERT) e partner_digest_invii (ledger degli invii del digest);
--   5) partner_call_salvate — call salvate («segui») da un'azienda.
--
-- Nessuna riga di versione condivisa né trigger di bump (M4: l'indice è
-- in-process con TTL e invalidazione dai servizi; niente hot row).
--
-- ORDINE DEI LOCK (quello globale della 0037: owner → azienda → call →
-- [candidatura] → budget AI):
--   - fn_partner_claim_notifica: riga dell'azienda destinataria FOR KEY SHARE
--     (lo stesso lock della FK: non blocca gli UPDATE dell'azienda né le RPC
--     che la prendono FOR NO KEY UPDATE) → suo profilo partner FOR SHARE (come
--     la 0035: azienda → profilo; una revoca in corso si attende) → lock
--     advisory per azienda → call FOR SHARE (attende una chiusura o una
--     modifica in corso e rilegge la riga);
--   - fn_partner_fanout_claim: solo la riga della call (UPDATE), nessun altro
--     lock dopo.
--
-- ADDITIVA: tabelle, colonne, vincolo, indice e funzioni nuove; nessuna
-- funzione o tabella esistente ridefinita o eliminata. Le colonne nuove di
-- partner_calls non sono nella whitelist di fn_partner_call_campi_editabili
-- (0037): crea_bozza e aggiorna le rifiutano (campo_non_modificabile). Il
-- backend attuale non usa nulla di questo file. Da eseguire IN UN'UNICA
-- TRANSAZIONE (begin; ... commit;). Rollback documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Collegamenti societari (M2, Q17). Una riga per chiave: tipo della fonte
--    e digest HMAC del valore normalizzato. Tipi: identita (P.IVA/CF
--    dell'azienda), nome (ragione sociale normalizzata), socio (con quota),
--    partecipata (con quota), controllata, esponente (CF), gruppo (nome del
--    gruppo), capogruppo (nome della capogruppo: stesso prefisso di «nome»
--    nell'HMAC, così incrocia la ragione sociale dell'altra azienda).
--    Righe presenti SOLO per le aziende con opt-in visibile o con una call non
--    chiusa, e SOLO con il flag acceso (il backend le calcola e le cancella).
-- ----------------------------------------------------------------------------
create table public.company_collegamenti (
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  tipo               text not null
    constraint cc_tipo_check
    check (tipo in ('identita', 'nome', 'socio', 'partecipata', 'controllata', 'esponente',
                    'gruppo', 'capogruppo')),
  chiave             text not null
    constraint cc_chiave_check check (chiave ~ '^[0-9a-f]{64}$'),
  quota              numeric(6, 3)
    constraint cc_quota_check check (quota is null or (quota >= 0 and quota <= 100)),
  created_at         timestamptz not null default now(),
  constraint company_collegamenti_pkey primary key (company_profile_id, tipo, chiave)
);

comment on table public.company_collegamenti is
  'Chiavi dei collegamenti societari per il matching dei partenariati (WP6, M2/Q17): digest HMAC-SHA256 esadecimali (hmac_dominio "partenariati.collegamenti.v1" con chiave derivata da RATE_LIMIT_PEPPER), MAI P.IVA, CF o nomi in chiaro. Solo per aziende con opt-in visibile o con una call non chiusa, solo con il flag acceso; cancellate alla revoca dell''opt-in se l''azienda non ha call non chiuse. Scritte e lette SOLO dal backend (services/partenariato_collegamenti.py); il dettaglio non esce mai verso terzi. Cascade dalla riga company_profiles. Una rotazione del pepper (come un cambio di P.IVA o ragione sociale senza import) rende le chiavi salvate diverse da quelle attese: il marker non vale più (partenariato_collegamenti.marker_aggiornato) e il backfill ricalcola.';
comment on column public.company_collegamenti.tipo is
  'Fonte della chiave: identita | nome | socio | partecipata | controllata | esponente | gruppo | capogruppo (capogruppo usa lo stesso dominio di nome per incrociare la ragione sociale dell''altra azienda).';
comment on column public.company_collegamenti.chiave is
  'Digest HMAC-SHA256 esadecimale (64 caratteri minuscoli) del valore normalizzato: mai il valore in chiaro.';
comment on column public.company_collegamenti.quota is
  'Quota di partecipazione in percentuale (0..100) per socio e partecipata; NULL = ignota (collegamento «possibile», Q17).';

-- Incrocio tra aziende diverse sulla stessa chiave.
create index company_collegamenti_chiave_idx on public.company_collegamenti (chiave);

-- Marker del calcolo: senza riga, o con algoritmo_versione vecchia, o con
-- company_data.fetched_at più recente di fonte_fetched_at, i collegamenti
-- valgono «non calcolati» e l'azienda non si suggerisce (fail-closed).
create table public.company_collegamenti_stato (
  company_profile_id uuid primary key
                       references public.company_profiles (id) on delete cascade,
  algoritmo_versione smallint not null
    constraint ccs_algoritmo_versione_check check (algoritmo_versione >= 1),
  fonte_fetched_at   timestamptz,
  calcolato_at       timestamptz not null default now()
);

comment on table public.company_collegamenti_stato is
  'Marker del calcolo dei collegamenti societari di un''azienda (WP6): versione dell''algoritmo (partenariato_collegamenti.ALGORITMO_VERSIONE) e company_data.fetched_at usato. Assente o vecchio → collegamenti_non_calcolati (fail-closed) finché il backfill dello scheduler non passa. Chi ricostruisce le chiavi toglie PRIMA il marker e lo riscrive per ULTIMO.';
comment on column public.company_collegamenti_stato.fonte_fetched_at is
  'company_data.fetched_at dei dati usati per il calcolo; NULL = nessun import (solo le chiavi dalla riga company_profiles).';

-- ----------------------------------------------------------------------------
-- 2) Notifiche proattive «call per te» (M5). Una riga per azienda × call
--    (dedup per sempre, anche su settimane diverse); la settimana è il lunedì
--    ISO in Europe/Rome della notifica e alimenta il tetto per azienda. Le
--    righe servono anche alla rotazione del matching (esposizioni degli
--    ultimi 7 giorni) e al digest (digest_incluso_at). Righe create SOLO da
--    fn_partner_claim_notifica.
-- ----------------------------------------------------------------------------
create table public.partner_notifiche_proattive (
  id                 bigint generated always as identity primary key,
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  partner_call_id    uuid not null
                       references public.partner_calls (id) on delete cascade,
  settimana          date not null
    constraint pnp_settimana_check check (extract(isodow from settimana) = 1),
  copertura          smallint not null
    constraint pnp_copertura_check check (copertura >= 0),
  punteggio          smallint not null
    constraint pnp_punteggio_check check (punteggio between 0 and 100),
  digest_incluso_at  timestamptz,
  created_at         timestamptz not null default now(),
  constraint pnp_azienda_call_key unique (company_profile_id, partner_call_id)
);

comment on table public.partner_notifiche_proattive is
  'Notifiche proattive «call per te» (WP6, M5): una per azienda destinataria × call, create SOLO da fn_partner_claim_notifica (dedup + tetto settimanale per azienda). Nessun dato di terzi: solo riferimenti, copertura e punteggio del match. Cascade dall''azienda e dalla call.';
comment on column public.partner_notifiche_proattive.settimana is
  'Lunedì della settimana ISO (Europe/Rome) della notifica: chiave del tetto settimanale.';
comment on column public.partner_notifiche_proattive.copertura is
  'Requisiti cercati coperti dall''azienda al momento della notifica.';
comment on column public.partner_notifiche_proattive.digest_incluso_at is
  'Quando la notifica è entrata in un digest settimanale inviato (NULL = non ancora inclusa).';

-- Tetto per settimana, esposizioni e contenuto del digest per azienda.
create index pnp_azienda_settimana_idx on public.partner_notifiche_proattive
  (company_profile_id, settimana);
-- Cascade dalla call senza scansione completa.
create index pnp_call_idx on public.partner_notifiche_proattive (partner_call_id);

-- fn_partner_claim_notifica — claim ATOMICO di una notifica proattiva.
--   - parametri NULL, settimana che non è un lunedì, tetto < 0, copertura
--     fuori da 0..32767, punteggio fuori da 0..100 → parametri_non_validi;
--   - azienda destinataria inesistente, eliminata o archiviata → false;
--   - azienda senza opt-in visibile o con il profilo sospeso → false;
--   - lock advisory per azienda (chiave: hashtext('partner_notifiche_proattive'),
--     hashtext(azienda::text)): i claim della stessa azienda sono serializzati;
--   - call inesistente, non pubblicata, non pubblica (solo_invitati), della
--     stessa azienda o dello stesso owner → false;
--   - notifica già presente per azienda × call (qualunque settimana) → false;
--   - notifiche dell'azienda nella settimana ≥ p_tetto → false;
--   - altrimenti insert → true.
create or replace function public.fn_partner_claim_notifica(
  p_company   uuid,
  p_call      uuid,
  p_settimana date,
  p_tetto     integer,
  p_copertura integer,
  p_punteggio integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner uuid;
  v_call  public.partner_calls%rowtype;
  v_n     bigint;
begin
  if p_company is null or p_call is null or p_settimana is null
     or extract(isodow from p_settimana) <> 1
     or p_tetto is null or p_tetto < 0
     or p_copertura is null or p_copertura not between 0 and 32767
     or p_punteggio is null or p_punteggio not between 0 and 100 then
    raise exception 'Parametri della notifica proattiva non validi'
      using detail = 'parametri_non_validi';
  end if;

  -- Livello «azienda»: la riga dell'azienda destinataria (FOR KEY SHARE, lo
  -- stesso lock che la FK prenderebbe all'insert, preso qui per restare
  -- nell'ordine azienda → call), il suo profilo partner (FOR SHARE: una revoca
  -- o una sospensione in corso si attende e si rilegge; anche il soft delete
  -- dell'azienda passa di qui, perché revoca la visibilità) e poi il lock
  -- advisory che serializza i claim dell'azienda.
  select parent_id into v_owner
  from public.company_profiles
  where id = p_company and deleted_at is null and archived_at is null
  for key share;
  if not found then
    return false;
  end if;

  perform 1 from public.company_partner_profiles
  where company_profile_id = p_company
    and visibile_come_partner
    and sospeso_at is null
  for share;
  if not found then
    return false;
  end if;

  perform pg_advisory_xact_lock(hashtext('partner_notifiche_proattive'),
                                hashtext(p_company::text));

  -- Livello «call»: FOR SHARE attende una chiusura o una modifica in corso e
  -- rilegge la riga aggiornata (FOR KEY SHARE non la rileggerebbe).
  select * into v_call
  from public.partner_calls
  where id = p_call
  for share;
  if not found
     or v_call.stato <> 'pubblicata'
     or v_call.visibilita <> 'pubblica'
     or v_call.company_profile_id = p_company
     or v_call.family_parent_id = v_owner then
    return false;
  end if;

  if exists (
    select 1 from public.partner_notifiche_proattive
    where company_profile_id = p_company and partner_call_id = p_call
  ) then
    return false;
  end if;

  select count(*) into v_n
  from public.partner_notifiche_proattive
  where company_profile_id = p_company and settimana = p_settimana;
  if v_n >= p_tetto then
    return false;
  end if;

  insert into public.partner_notifiche_proattive
    (company_profile_id, partner_call_id, settimana, copertura, punteggio)
  values
    (p_company, p_call, p_settimana, p_copertura, p_punteggio);
  return true;
end;
$$;

comment on function public.fn_partner_claim_notifica(uuid, uuid, date, integer, integer, integer) is
  'Claim atomico di una notifica proattiva «call per te» (WP6, M5): lock azienda (FOR KEY SHARE) → profilo partner (FOR SHARE) → advisory per azienda (hashtext(''partner_notifiche_proattive''), hashtext(azienda)) → call (FOR SHARE). false se l''azienda non è viva, la call non è pubblicata e pubblica, è della stessa azienda o dello stesso owner, l''azienda non ha l''opt-in visibile (o è sospesa), la notifica esiste già (dedup azienda × call) o il tetto della settimana è raggiunto; altrimenti insert → true. p_settimana = lunedì ISO Europe/Rome. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 3) Fan-out alla pubblicazione: claim con scadenza + completamento (M5).
--    Il claim lo prende fn_partner_fanout_claim; il completamento lo scrive
--    il backend a fan-out finito (update di fanout_completato_at). Un claim
--    scaduto senza completamento si riprende (scheduler, passo
--    fanout_pendenti): il dedup di fn_partner_claim_notifica rende sicura la
--    ripresa. Le colonne non sono nella whitelist delle RPC della call.
--    Nota: trg_partner_calls_updated_at (0037) aggiorna updated_at anche per
--    queste scritture tecniche (come per bando_verificato_at): per ordinare
--    le call «recenti» si usa pubblicata_at, non updated_at.
-- ----------------------------------------------------------------------------
alter table public.partner_calls
  add column fanout_claim_at      timestamptz,
  add column fanout_completato_at timestamptz,
  add constraint pcall_fanout_coerente
    check (fanout_completato_at is null or fanout_claim_at is not null);

comment on column public.partner_calls.fanout_claim_at is
  'Ultimo claim del fan-out delle notifiche proattive (fn_partner_fanout_claim): riprendibile dopo il TTL se fanout_completato_at è ancora NULL. Colonna tecnica: MAI verso terzi.';
comment on column public.partner_calls.fanout_completato_at is
  'Fan-out delle notifiche proattive concluso (scritto dal backend dopo il claim): da qui in poi nessun nuovo claim. Colonna tecnica: MAI verso terzi.';

-- Call pubblicate con il fan-out da fare o da riprendere (scheduler).
create index partner_calls_fanout_pendenti_idx on public.partner_calls (pubblicata_at)
  where stato = 'pubblicata' and fanout_completato_at is null;

-- fn_partner_fanout_claim — claim del fan-out di una call: vince solo se la
-- call è pubblicata, il fan-out non è completato e il claim è assente o più
-- vecchio di p_ttl_secondi (1..86400, altrimenti parametri_non_validi). Un
-- solo UPDATE condizionato: lock della sola riga della call.
create or replace function public.fn_partner_fanout_claim(
  p_call        uuid,
  p_ttl_secondi integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_call is null or p_ttl_secondi is null or p_ttl_secondi not between 1 and 86400 then
    raise exception 'Parametri del claim del fan-out non validi'
      using detail = 'parametri_non_validi';
  end if;

  update public.partner_calls
  set fanout_claim_at = now()
  where id = p_call
    and stato = 'pubblicata'
    and fanout_completato_at is null
    and (fanout_claim_at is null
         or fanout_claim_at <= now() - make_interval(secs => p_ttl_secondi));
  return found;
end;
$$;

comment on function public.fn_partner_fanout_claim(uuid, integer) is
  'Claim del fan-out delle notifiche proattive di una call (WP6, M5): true se la call è pubblicata, fanout_completato_at è NULL e fanout_claim_at è NULL o più vecchio di p_ttl_secondi (1..86400); imposta fanout_claim_at = now(). Lock della sola riga della call. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 4) Email del modulo (Q6): impostazioni per utente separate da
--    bando_alert_settings (riusarle spegnerebbe anche gli alert sui bandi).
--    Riga PIGRA: l'assenza vale «abilitati»; il token di disiscrizione (RFC
--    8058) punta a questa riga ed è la stessa fonte di verità del toggle in
--    Preferenze.
-- ----------------------------------------------------------------------------
create table public.partner_email_settings (
  user_id           uuid primary key references public.profiles (id) on delete cascade,
  digest_abilitato  boolean not null default true,
  eventi_abilitati  boolean not null default true,
  unsubscribe_token uuid not null default gen_random_uuid()
    constraint partner_email_settings_unsubscribe_token_key unique,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

comment on table public.partner_email_settings is
  'Opt-out delle email dei partenariati (WP6, Q6): digest settimanale ed email di evento (inviti, candidature, esiti, messaggi). Assenza della riga = entrambi abilitati. unsubscribe_token = link di disiscrizione pubblico (RFC 8058), separato da quello degli alert sui bandi.';
comment on column public.partner_email_settings.unsubscribe_token is
  'Token opaco della disiscrizione pubblica (GET non mutante, POST neutro): MAI nei log.';

create trigger trg_partner_email_settings_updated_at
  before update on public.partner_email_settings
  for each row execute function public.set_updated_at();

-- Registro delle esecuzioni del digest: la PK sulla settimana (lunedì ISO) è
-- il claim (INSERT: un 23505 = già eseguito altrove); riepilogo = contatori.
create table public.partner_digest_runs (
  settimana  date primary key
    constraint pdr_settimana_check check (extract(isodow from settimana) = 1),
  created_at timestamptz not null default now(),
  riepilogo  jsonb not null default '{}'::jsonb
    constraint pdr_riepilogo_check check (jsonb_typeof(riepilogo) = 'object')
);

comment on table public.partner_digest_runs is
  'Claim settimanale del digest dei partenariati (WP6): l''INSERT della settimana (lunedì ISO Europe/Rome) vince una volta sola (23505 = già eseguito). riepilogo = contatori senza dati personali.';

-- Ledger degli invii del digest: idempotenza a livello DB (claim per INSERT
-- sull'unicità utente × settimana). Stati: in_invio → inviata | fallita ;
-- incerta = esecuzione interrotta tra invio e conferma, MAI ritentata
-- (at-most-once). Le transizioni sono UPDATE condizionati del backend.
create table public.partner_digest_invii (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references public.profiles (id) on delete cascade,
  settimana  date not null
    constraint pdi_settimana_check check (extract(isodow from settimana) = 1),
  stato      text not null default 'in_invio'
    constraint pdi_stato_check check (stato in ('in_invio', 'inviata', 'fallita', 'incerta')),
  errore     text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint pdi_utente_settimana_key unique (user_id, settimana)
);

comment on table public.partner_digest_invii is
  'Ledger del digest settimanale dei partenariati (WP6): una riga per utente × settimana (claim per INSERT). Nessun contenuto personale oltre i riferimenti; errore senza indirizzi email (minimizzazione, T8).';

create trigger trg_partner_digest_invii_updated_at
  before update on public.partner_digest_invii
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- 5) Call salvate («Salva» vale come «segui», M6): per azienda, con l'utente
--    che l'ha salvata (senza FK: il salvataggio resta dell'azienda).
-- ----------------------------------------------------------------------------
create table public.partner_call_salvate (
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  partner_call_id    uuid not null
                       references public.partner_calls (id) on delete cascade,
  user_id            uuid not null,
  created_at         timestamptz not null default now(),
  constraint partner_call_salvate_pkey primary key (company_profile_id, partner_call_id)
);

comment on table public.partner_call_salvate is
  'Call di partenariato salvate da un''azienda (WP6, M6): «Salva» vale come «segui» (notifica su modifica o chiusura). Cascade dall''azienda e dalla call. Scritte e lette solo dal backend.';
comment on column public.partner_call_salvate.user_id is
  'Utente che ha salvato la call (il titolare, T4). Senza FK: MAI verso terzi.';

-- Chi segue una call (notifica di modifica o chiusura) e cascade dalla call.
create index pcs_call_idx on public.partner_call_salvate (partner_call_id);

-- ----------------------------------------------------------------------------
-- 6) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova eseguibile dai
--    ruoli esposti (Supabase concede EXECUTE di default a PUBLIC).
-- ----------------------------------------------------------------------------
alter table public.company_collegamenti enable row level security;
alter table public.company_collegamenti_stato enable row level security;
alter table public.partner_notifiche_proattive enable row level security;
alter table public.partner_email_settings enable row level security;
alter table public.partner_digest_runs enable row level security;
alter table public.partner_digest_invii enable row level security;
alter table public.partner_call_salvate enable row level security;

revoke all on public.company_collegamenti from anon, authenticated;
revoke all on public.company_collegamenti_stato from anon, authenticated;
revoke all on public.partner_notifiche_proattive from anon, authenticated;
revoke all on public.partner_email_settings from anon, authenticated;
revoke all on public.partner_digest_runs from anon, authenticated;
revoke all on public.partner_digest_invii from anon, authenticated;
revoke all on public.partner_call_salvate from anon, authenticated;

revoke execute on function public.fn_partner_claim_notifica(uuid, uuid, date, integer, integer, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_fanout_claim(uuid, integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0038 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle; la disiscrizione pubblica dal digest legge
-- partner_email_settings anche a flag spento).
-- 1) drop function public.fn_partner_fanout_claim(uuid, integer);
--    drop function public.fn_partner_claim_notifica(uuid, uuid, date, integer, integer,
--      integer);
-- 2) drop table public.partner_call_salvate;         -- si perdono le call seguite
--    drop table public.partner_digest_invii;         -- si perde il ledger del digest
--    drop table public.partner_digest_runs;
--    drop table public.partner_email_settings;       -- si perdono le disiscrizioni:
--      esportarle prima, altrimenti al ritorno tutti tornano iscritti;
--    drop table public.partner_notifiche_proattive;  -- si perdono tetto e dedup
--    drop table public.company_collegamenti_stato;
--    drop table public.company_collegamenti;
-- 3) drop index public.partner_calls_fanout_pendenti_idx;
--    alter table public.partner_calls
--      drop constraint pcall_fanout_coerente,
--      drop column fanout_completato_at,
--      drop column fanout_claim_at;
-- ============================================================================
