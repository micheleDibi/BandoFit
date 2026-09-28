-- ============================================================================
-- BandoFit — DB primario, migration 0034: REGOLE DI PARTENARIATO per bando e
-- infrastruttura comune della spesa LLM del modulo partenariati (WP3, piano
-- docs/partenariati.md §2.1 T6, §2.3 R1-R9, §3 riga 0034, §13 Q15).
--
--   1) bando_partenariato — una riga per bando del catalogo (DB secondario:
--      SENZA FK cross-DB) con le regole estratte (extraction, mai esposta) e
--      post-elaborate (regole, ciò che esce dall'API), gli hash che decidono
--      se rigenerare, il claim con heartbeat e il backoff sugli errori;
--   2) partenariati_ai_esecuzioni — registro UNICO delle chiamate LLM a carico
--      della piattaforma (estrazione WP3, WP4, WP5, WP10): base del budget
--      giornaliero fail-closed per gruppo e dei limiti per utente/owner;
--   3) partenariati_runs — claim giornaliero dello scheduler del modulo;
--   4) RPC del budget: fn_partenariati_ai_prenota / fn_partenariati_ai_concludi;
--   5) RPC dell'estrazione per bando: fn_partenariato_prenota / _rinnova /
--      _concludi / _chiudi_stale (+ fn_partenariato_esecuzione_scaduta, che
--      chiude l'esecuzione di un claim scaduto secondo la fase);
--   6) fn_partenariato_bando_ids — gli id per il filtro «Ammette partenariato».
--
-- Giorno contabile: Europe/Rome ((now() at time zone 'Europe/Rome')::date).
-- Tutti i controlli di spesa sono fail-closed: budget mancante o ≤ 0 = spesa
-- negata; un esito ignoto (cost_cents NULL) lascia la riserva nel budget.
--
-- ADDITIVA: tabelle e funzioni nuove, nessuna firma o tabella esistente
-- toccata; il backend attuale non le usa. Da eseguire IN UN'UNICA TRANSAZIONE
-- (begin; ... commit;). Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Estrazione per bando. Stati: in_corso (claim attivo) | pronta (esito
--    presente: estratta o nessun_segnale) | errore (nessun risultato
--    precedente). Durante un in_corso le regole precedenti restano servite.
-- ----------------------------------------------------------------------------
create table public.bando_partenariato (
  id                     uuid primary key default gen_random_uuid(),
  bando_id               integer not null,
  bando_slug             text not null,
  bando_titolo           text not null,
  stato                  text not null
    constraint bp_stato_check check (stato in ('in_corso', 'pronta', 'errore')),
  fase                   text
    constraint bp_fase_check check (fase in ('documenti', 'lettura', 'analisi')),
  esito                  text
    constraint bp_esito_check check (esito in ('estratta', 'nessun_segnale')),
  modalita               text
    constraint bp_modalita_check
    check (modalita in ('obbligatorio', 'ammesso', 'non_ammesso', 'non_determinabile')),
  modalita_effettiva     text
    constraint bp_modalita_effettiva_check
    check (modalita_effettiva in ('obbligatorio', 'ammesso', 'non_ammesso',
                                  'non_determinabile')),
  extraction             jsonb,
  regole                 jsonb,
  preclassificazione     jsonb not null default '{}'::jsonb
    constraint bp_preclassificazione_check check (jsonb_typeof(preclassificazione) = 'object'),
  fonti_usate            jsonb not null default '[]'::jsonb
    constraint bp_fonti_usate_check check (jsonb_typeof(fonti_usate) = 'array'),
  catalogo_hash          text,
  content_hash           text,
  catalogo_aggiornato_at timestamptz,
  prompt_version         integer,
  schema_version         integer,
  model                  text,
  input_tokens           integer not null default 0 check (input_tokens >= 0),
  output_tokens          integer not null default 0 check (output_tokens >= 0),
  cost_cents             integer not null default 0 check (cost_cents >= 0),
  estratta_at            timestamptz,
  verificata_at          timestamptz,
  ultima_esecuzione_at   timestamptz,
  ultima_forzata_at      timestamptz,
  claim_token            uuid,
  claim_scade_at         timestamptz,
  esecuzione_id          uuid,
  errore_codice          text,
  errore_at              timestamptz,
  tentativi_falliti      integer not null default 0 check (tentativi_falliti >= 0),
  prossimo_tentativo_at  timestamptz,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),
  -- In corso se e solo se c'è un claim (token + scadenza).
  constraint bp_claim_coerente
    check ((stato = 'in_corso') = (claim_token is not null and claim_scade_at is not null)),
  -- Pronta solo con un risultato.
  constraint bp_pronta_ha_esito check (stato <> 'pronta' or esito is not null),
  -- La fase esiste solo dentro un in_corso.
  constraint bp_fase_solo_in_corso check (fase is null or stato = 'in_corso')
);

comment on table public.bando_partenariato is
  'Regole di partenariato per bando del catalogo (una riga per bando_id, senza FK cross-DB). Scritta SOLO dalle RPC fn_partenariato_prenota / _rinnova / _concludi / _chiudi_stale: claim atomico con heartbeat (al massimo una chiamata LLM per bando alla volta), cooldown e backoff.';
comment on column public.bando_partenariato.bando_id is
  'Id del bando nel DB secondario (catalogo, sola lettura): nessuna FK.';
comment on column public.bando_partenariato.bando_slug is
  'Slug del bando all''ultima prenotazione (snapshot).';
comment on column public.bando_partenariato.bando_titolo is
  'Titolo del bando all''ultima prenotazione (snapshot).';
comment on column public.bando_partenariato.stato is
  'in_corso (claim attivo, le regole precedenti restano servite) | pronta (c''è un risultato: esito) | errore (ultima esecuzione fallita e nessun risultato precedente).';
comment on column public.bando_partenariato.fase is
  'Avanzamento dentro un in_corso: documenti → lettura → analisi (fn_partenariato_rinnova). NULL fuori da in_corso.';
comment on column public.bando_partenariato.esito is
  'Risultato corrente: estratta (regole dal modello) | nessun_segnale (pre-classificatore senza segnali, nessuna chiamata LLM).';
comment on column public.bando_partenariato.modalita is
  'Modalità dichiarata dal modello: obbligatorio | ammesso | non_ammesso | non_determinabile.';
comment on column public.bando_partenariato.modalita_effettiva is
  'Modalità usata dal filtro e dai motori: = modalita solo se la sua citazione è verificata e coerente, altrimenti non_determinabile (post-elaborazione deterministica).';
comment on column public.bando_partenariato.extraction is
  'Output grezzo del modello, validato sullo schema (audit). MAI esposto al client.';
comment on column public.bando_partenariato.regole is
  'Regole post-elaborate con l''esito della verifica di ogni citazione: è ciò che esce dall''API.';
comment on column public.bando_partenariato.fonti_usate is
  'Documenti considerati (array): stato di download e lettura, pagine incluse, troncamenti.';
comment on column public.bando_partenariato.catalogo_hash is
  'sha256 economico di META + sezioni del catalogo + URL candidati: decide se riacquisire i documenti.';
comment on column public.bando_partenariato.content_hash is
  'sha256 di {versioni, testo inviato al modello, pagine incluse, limiti} (MAI i byte del PDF): se non cambia la riverifica chiude come riusata, a costo 0.';
comment on column public.bando_partenariato.catalogo_aggiornato_at is
  'bando_pubblico.ultimo_cambiamento_at letto alla generazione (segnale di cambiamento del catalogo).';
comment on column public.bando_partenariato.cost_cents is
  'Costo in CENTESIMI DI USD della generazione che ha prodotto il risultato corrente (0 per nessun_segnale).';
comment on column public.bando_partenariato.estratta_at is
  'Quando è stato prodotto il risultato corrente (estratta o nessun_segnale).';
comment on column public.bando_partenariato.verificata_at is
  'Ultima conferma che il risultato corrente è attuale (generazione o riusata).';
comment on column public.bando_partenariato.ultima_esecuzione_at is
  'Fine dell''ultima esecuzione CONCLUSA (qualunque esito, anche interrotta dal failsafe): base del cooldown quando l''ultima esecuzione non è in errore.';
comment on column public.bando_partenariato.ultima_forzata_at is
  'Ultima prenotazione con p_ignora_cooldown («Analizza comunque» o forzatura admin).';
comment on column public.bando_partenariato.claim_token is
  'Token del claim in corso: solo chi lo possiede rinnova e conclude. Mai esposto al client.';
comment on column public.bando_partenariato.claim_scade_at is
  'Scadenza del claim (TTL 60..1800 s, rinnovato dall''heartbeat): scaduto → riacquisibile e chiuso dal failsafe.';
comment on column public.bando_partenariato.esecuzione_id is
  'Esecuzione (partenariati_ai_esecuzioni.id) in corso o ultima: senza FK.';
comment on column public.bando_partenariato.errore_codice is
  'Codice dell''ultimo errore (errore | timeout | interrotta | codice del servizio); NULL dopo un esito riuscito. Se valorizzato il cooldown segue prossimo_tentativo_at.';
comment on column public.bando_partenariato.tentativi_falliti is
  'Errori consecutivi: backoff = least(6 h · 2^(n-1), 72 h). Azzerato da un esito riuscito.';
comment on column public.bando_partenariato.prossimo_tentativo_at is
  'Primo istante utile per riprovare dopo un errore (backoff).';

create trigger trg_bando_partenariato_updated_at
  before update on public.bando_partenariato
  for each row execute function public.set_updated_at();

create unique index bando_partenariato_bando_key on public.bando_partenariato (bando_id);
-- Filtro «Ammette partenariato» (fn_partenariato_bando_ids).
create index bando_partenariato_modalita_idx on public.bando_partenariato
  (modalita_effettiva, estratta_at desc) where esito = 'estratta';
-- Failsafe sui claim scaduti (fn_partenariato_chiudi_stale).
create index bando_partenariato_in_corso_idx on public.bando_partenariato (claim_scade_at)
  where stato = 'in_corso';

-- ----------------------------------------------------------------------------
-- 2) Registro unico della spesa LLM del modulo (senza FK: deve sopravvivere
--    alle cancellazioni). Un'esecuzione nasce in_corso con la riserva al caso
--    peggiore; il budget del giorno somma coalesce(cost_cents, riserva).
-- ----------------------------------------------------------------------------
create table public.partenariati_ai_esecuzioni (
  id                    uuid primary key default gen_random_uuid(),
  servizio              text not null
    constraint pae_servizio_check
    check (servizio in ('partenariato_estrazione', 'partner_profilo_ai',
                        'partner_call_posizioni', 'partner_call_testi', 'partner_bozza')),
  origine               text not null
    constraint pae_origine_check
    check (origine in ('utente', 'call', 'batch', 'admin', 'valutazione', 'sistema')),
  gruppo                text not null
    constraint pae_gruppo_check check (gruppo in ('bando', 'altri', 'batch', 'valutazione')),
  bando_id              integer,
  company_profile_id    uuid,
  owner_id              uuid,
  richiedente_user_id   uuid,
  giorno                date not null default ((now() at time zone 'Europe/Rome')::date),
  stato                 text not null default 'in_corso'
    constraint pae_stato_check
    check (stato in ('in_corso', 'riusata', 'conclusa', 'nessun_segnale', 'errore',
                     'timeout', 'interrotta')),
  llm_eseguito          boolean not null default false,
  costo_riservato_cents integer not null check (costo_riservato_cents >= 0),
  cost_cents            integer check (cost_cents >= 0),
  input_tokens          integer not null default 0 check (input_tokens >= 0),
  output_tokens         integer not null default 0 check (output_tokens >= 0),
  model                 text,
  content_hash          text,
  errore_codice         text,
  avviata_at            timestamptz not null default now(),
  conclusa_at           timestamptz,
  -- Conclusa se e solo se non è più in corso.
  constraint pae_conclusa_coerente check ((stato = 'in_corso') = (conclusa_at is null))
);

comment on table public.partenariati_ai_esecuzioni is
  'Registro UNICO delle chiamate LLM a carico della piattaforma nel modulo partenariati (WP3 estrazione, WP4 profilo, WP5 call, WP10 bozze). Righe create SOLO da fn_partenariati_ai_prenota (advisory lock globale, limiti e budget fail-closed) e chiuse SOLO da fn_partenariati_ai_concludi. Senza FK: sopravvive alle cancellazioni. Non scrive mai in ai_checks.';
comment on column public.partenariati_ai_esecuzioni.gruppo is
  'Gruppo di budget: bando (estrazioni WP3 utente/call/admin) | altri (WP4/WP5/WP10) | batch (scheduler notturno) | valutazione (script, budget dedicato).';
comment on column public.partenariati_ai_esecuzioni.owner_id is
  'Owner (family_parent_id) per il limite per owner; senza FK.';
comment on column public.partenariati_ai_esecuzioni.richiedente_user_id is
  'Utente che ha causato la spesa (limite per utente); NULL per batch/sistema. Senza FK.';
comment on column public.partenariati_ai_esecuzioni.giorno is
  'Giorno contabile Europe/Rome dell''avvio: base di budget e limiti giornalieri.';
comment on column public.partenariati_ai_esecuzioni.stato is
  'in_corso | riusata | conclusa | nessun_segnale | errore | timeout | interrotta (claim scaduto o sottratto).';
comment on column public.partenariati_ai_esecuzioni.llm_eseguito is
  'true se la chiamata al modello ha prodotto costo o token. Le esecuzioni riusata/nessun_segnale senza LLM non contano nel limite per utente/owner.';
comment on column public.partenariati_ai_esecuzioni.costo_riservato_cents is
  'Riserva al caso peggiore (centesimi USD) presa alla prenotazione: conta nel budget finché cost_cents è NULL.';
comment on column public.partenariati_ai_esecuzioni.cost_cents is
  'Costo reale in CENTESIMI DI USD; NULL = ignoto (in corso, timeout, interrotta): la riserva resta nel budget.';

create index pae_giorno_gruppo_idx on public.partenariati_ai_esecuzioni (giorno, gruppo);
create index pae_richiedente_idx on public.partenariati_ai_esecuzioni
  (richiedente_user_id, giorno, servizio);
create index pae_owner_idx on public.partenariati_ai_esecuzioni (owner_id, giorno, servizio);
create index pae_bando_idx on public.partenariati_ai_esecuzioni (bando_id, avviata_at desc);

-- ----------------------------------------------------------------------------
-- 3) Scheduler del modulo: una riga per giorno (claim per INSERT: 23505 =
--    run già fatta), con il riepilogo dei passi.
-- ----------------------------------------------------------------------------
create table public.partenariati_runs (
  giorno     date primary key,
  created_at timestamptz not null default now(),
  riepilogo  jsonb not null default '{}'::jsonb
    constraint pr_riepilogo_check check (jsonb_typeof(riepilogo) = 'object')
);

comment on table public.partenariati_runs is
  'Claim giornaliero dello scheduler partenariati (services/partenariati_scheduler.py): l''INSERT del giorno vince una volta sola (23505 = già eseguita). riepilogo = esito dei passi.';

-- ----------------------------------------------------------------------------
-- 4) fn_partenariati_ai_prenota — prenotazione ATOMICA di una spesa LLM.
--    Serializzata da un advisory lock globale (nessun check-then-act
--    parallelo). Nell'ordine:
--      - limite per richiedente (se richiedente e limite sono valorizzati):
--        esecuzioni di oggi dello stesso servizio, escluse quelle senza LLM
--        riusata e nessun_segnale, ed errore o interrotta chiuse a costo 0
--        (il modello non è stato chiamato: documenti non raggiungibili,
--        claim perso prima dell'analisi) → ai_limite_utente;
--      - limite per owner (NULL = nessun limite), stesso conteggio →
--        ai_limite_owner;
--      - budget del gruppo: budget NULL o ≤ 0, oppure
--        sum(coalesce(cost_cents, riserva)) di oggi + riserva > budget →
--        ai_budget_esaurito;
--    poi insert in_corso → id dell'esecuzione.
--    Detail: parametri_non_validi | ai_limite_utente | ai_limite_owner |
--    ai_budget_esaurito.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariati_ai_prenota(
  p_servizio              text,
  p_origine               text,
  p_gruppo                text,
  p_budget_cents          integer,
  p_costo_riservato_cents integer,
  p_richiedente           uuid,
  p_limite_richiedente    integer,
  p_owner                 uuid,
  p_limite_owner          integer,
  p_company               uuid,
  p_bando_id              integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi  constant date := (now() at time zone 'Europe/Rome')::date;
  v_n     bigint;
  v_spesa bigint;
  v_id    uuid;
begin
  if p_servizio is null or p_origine is null or p_gruppo is null
     or p_costo_riservato_cents is null or p_costo_riservato_cents < 0 then
    raise exception 'Parametri di prenotazione della spesa AI non validi'
      using detail = 'parametri_non_validi';
  end if;

  -- Un solo prenotante alla volta su tutta la piattaforma.
  perform pg_advisory_xact_lock(hashtext('partenariati_ai_budget'));

  if p_richiedente is not null and p_limite_richiedente is not null then
    select count(*) into v_n
    from public.partenariati_ai_esecuzioni
    where richiedente_user_id = p_richiedente
      and giorno = v_oggi
      and servizio = p_servizio
      and not (not llm_eseguito
               and (stato in ('riusata', 'nessun_segnale')
                    or (stato in ('errore', 'interrotta')
                        and cost_cents is not distinct from 0)));
    if v_n >= greatest(p_limite_richiedente, 0) then
      raise exception 'Hai raggiunto il numero di analisi di oggi: riprova domani'
        using detail = 'ai_limite_utente';
    end if;
  end if;

  if p_owner is not null and p_limite_owner is not null then
    select count(*) into v_n
    from public.partenariati_ai_esecuzioni
    where owner_id = p_owner
      and giorno = v_oggi
      and servizio = p_servizio
      and not (not llm_eseguito
               and (stato in ('riusata', 'nessun_segnale')
                    or (stato in ('errore', 'interrotta')
                        and cost_cents is not distinct from 0)));
    if v_n >= greatest(p_limite_owner, 0) then
      raise exception 'Hai raggiunto il numero di analisi di oggi: riprova domani'
        using detail = 'ai_limite_owner';
    end if;
  end if;

  if p_budget_cents is null or p_budget_cents <= 0 then
    raise exception 'L''analisi automatica è sospesa per oggi: riprova domani'
      using detail = 'ai_budget_esaurito';
  end if;

  select coalesce(sum(coalesce(cost_cents, costo_riservato_cents)), 0) into v_spesa
  from public.partenariati_ai_esecuzioni
  where giorno = v_oggi and gruppo = p_gruppo;
  if v_spesa + p_costo_riservato_cents > p_budget_cents then
    raise exception 'L''analisi automatica è sospesa per oggi: riprova domani'
      using detail = 'ai_budget_esaurito';
  end if;

  insert into public.partenariati_ai_esecuzioni
    (servizio, origine, gruppo, bando_id, company_profile_id, owner_id,
     richiedente_user_id, giorno, costo_riservato_cents)
  values
    (p_servizio, p_origine, p_gruppo, p_bando_id, p_company, p_owner,
     p_richiedente, v_oggi, p_costo_riservato_cents)
  returning id into v_id;

  return v_id;
end;
$$;

comment on function public.fn_partenariati_ai_prenota(text, text, text, integer, integer, uuid, integer, uuid, integer, uuid, integer) is
  'Prenota una spesa LLM del modulo partenariati (advisory lock globale, giorno Europe/Rome, fail-closed): limite per richiedente e per owner (NULL = nessun limite; non contano, senza LLM, riusata/nessun_segnale ed errore/interrotta a costo 0), budget del gruppo = sum(coalesce(cost_cents, riserva)) + riserva ≤ budget (budget NULL o ≤ 0 = negato). Ritorna l''id dell''esecuzione in_corso. Detail: parametri_non_validi | ai_limite_utente | ai_limite_owner | ai_budget_esaurito.';

-- ----------------------------------------------------------------------------
-- 5) fn_partenariati_ai_concludi — chiude un'esecuzione, SOLO da in_corso
--    (altrimenti nessun effetto: idempotente). p_cost_cents NULL = costo
--    ignoto, la riserva resta nel budget. llm_eseguito = costo o token > 0.
--    Detail: stato_non_valido.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariati_ai_concludi(
  p_esecuzione_id uuid,
  p_stato         text,
  p_cost_cents    integer,
  p_input_tokens  integer,
  p_output_tokens integer,
  p_model         text,
  p_errore        text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_stato is null or p_stato not in ('riusata', 'conclusa', 'nessun_segnale', 'errore',
                                        'timeout', 'interrotta') then
    raise exception 'Stato di chiusura dell''esecuzione non valido: %', coalesce(p_stato, 'NULL')
      using detail = 'stato_non_valido';
  end if;

  update public.partenariati_ai_esecuzioni
  set stato         = p_stato,
      cost_cents    = p_cost_cents,
      input_tokens  = coalesce(p_input_tokens, 0),
      output_tokens = coalesce(p_output_tokens, 0),
      model         = coalesce(p_model, model),
      errore_codice = p_errore,
      llm_eseguito  = coalesce(p_cost_cents, 0) > 0
                      or coalesce(p_input_tokens, 0) > 0
                      or coalesce(p_output_tokens, 0) > 0,
      conclusa_at   = now()
  where id = p_esecuzione_id and stato = 'in_corso';
end;
$$;

comment on function public.fn_partenariati_ai_concludi(uuid, text, integer, integer, integer, text, text) is
  'Chiude un''esecuzione in_corso (riusata | conclusa | nessun_segnale | errore | timeout | interrotta) con costo e token; già chiusa o inesistente → nessun effetto. p_cost_cents NULL = costo ignoto (la riserva resta nel budget). Detail: stato_non_valido.';

-- ----------------------------------------------------------------------------
-- 5b) fn_partenariato_esecuzione_scaduta — chiude l'esecuzione di un claim
--     scaduto (failsafe o riacquisizione in fn_partenariato_prenota). La
--     fase dice se il modello può essere stato chiamato: la pipeline lo
--     chiama SOLO dopo un heartbeat riuscito verso 'analisi', che a claim
--     scaduto o sottratto non riesce più.
--       - fase documenti | lettura → interrotta a costo 0: la riserva esce
--         dal budget e l'esecuzione non conta nei limiti giornalieri;
--       - fase analisi (o ignota) → interrotta con costo NULL (la riserva
--         resta: la chiamata può essere stata addebitata) e una riga
--         timeout_unknown in api_usage_events con la riserva come costo,
--         come fa la pipeline sul timeout.
--     Solo da in_corso: altrimenti nessun effetto (idempotente).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_esecuzione_scaduta(
  p_esecuzione_id uuid,
  p_fase          text,
  p_bando_slug    text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_exec public.partenariati_ai_esecuzioni%rowtype;
begin
  select * into v_exec from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id and stato = 'in_corso'
  for update;
  if not found then
    return;
  end if;

  if p_fase in ('documenti', 'lettura') then
    perform public.fn_partenariati_ai_concludi(
      p_esecuzione_id, 'interrotta', 0, 0, 0, null, 'claim_scaduto');
    return;
  end if;

  insert into public.api_usage_events
    (user_id, family_parent_id, provider, service, outcome, cost_cents, request_meta)
  values
    (v_exec.richiedente_user_id, v_exec.owner_id, 'anthropic', v_exec.servizio,
     'timeout_unknown', v_exec.costo_riservato_cents,
     jsonb_build_object('bando_id', v_exec.bando_id, 'bando_slug', p_bando_slug,
                        'origine', v_exec.origine, 'esecuzione_id', v_exec.id,
                        'esito', 'interrotta', 'failsafe', true));
  perform public.fn_partenariati_ai_concludi(
    p_esecuzione_id, 'interrotta', null, 0, 0, null, 'claim_scaduto');
end;
$$;

comment on function public.fn_partenariato_esecuzione_scaduta(uuid, text, text) is
  'Chiude l''esecuzione in_corso di un claim scaduto: fase documenti/lettura → interrotta a costo 0 (modello mai chiamato); fase analisi o ignota → interrotta con costo NULL (la riserva resta) e riga timeout_unknown in api_usage_events con la riserva. Già chiusa → nessun effetto.';

-- ----------------------------------------------------------------------------
-- 6) fn_partenariato_prenota — claim ATOMICO dell'estrazione di un bando.
--    Advisory lock per bando, poi riga FOR UPDATE:
--      - claim valido → {esito: in_corso} (nessuna nuova esecuzione; token e
--        id NON restituiti: il claim resta di chi lo ha);
--      - claim scaduto → la vecchia esecuzione diventa interrotta
--        (fn_partenariato_esecuzione_scaduta: costo 0 se il claim non era
--        arrivato all'analisi, altrimenti NULL e la riserva resta) e si
--        prosegue;
--      - cooldown, salvo p_ignora_cooldown: se l'ultima esecuzione è in
--        errore (errore_codice valorizzato) vale prossimo_tentativo_at,
--        altrimenti ultima_esecuzione_at + p_cooldown_minuti (NULL = 24 h) →
--        partenariato_cooldown;
--      - fn_partenariati_ai_prenota (servizio partenariato_estrazione; gruppo
--        batch/valutazione per quelle origini, altrimenti bando; limite per
--        richiedente = p_limite_utente; nessun limite per owner);
--      - insert o update della riga direttamente in in_corso, fase
--        documenti, nuovo claim (TTL limitato a 60..1800 s, NULL = 900);
--        ultima_forzata_at se p_ignora_cooldown.
--    Ritorna {esito: prenotata|in_corso, claim_token, esecuzione_id}.
--    Detail: parametri_non_validi | partenariato_cooldown | ai_limite_utente |
--    ai_budget_esaurito.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_prenota(
  p_bando_id              integer,
  p_bando_slug            text,
  p_bando_titolo          text,
  p_origine               text,
  p_richiedente           uuid,
  p_company               uuid,
  p_owner                 uuid,
  p_budget_cents          integer,
  p_costo_riservato_cents integer,
  p_limite_utente         integer,
  p_cooldown_minuti       integer,
  p_ttl_secondi           integer,
  p_ignora_cooldown       boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ttl      constant integer := greatest(60, least(1800, coalesce(p_ttl_secondi, 900)));
  v_cooldown constant integer := greatest(coalesce(p_cooldown_minuti, 1440), 0);
  v_ignora   constant boolean := coalesce(p_ignora_cooldown, false);
  v_token    constant uuid := gen_random_uuid();
  v_row      public.bando_partenariato%rowtype;
  v_esiste   boolean;
  v_libera   timestamptz;
  v_gruppo   text;
  v_exec     uuid;
begin
  if p_bando_id is null then
    raise exception 'Bando non indicato' using detail = 'parametri_non_validi';
  end if;

  -- Un solo prenotante alla volta per bando (spazio di chiavi a due interi:
  -- non collide con il lock globale del budget).
  perform pg_advisory_xact_lock(hashtext('bando_partenariato'), p_bando_id);

  select * into v_row from public.bando_partenariato
  where bando_id = p_bando_id
  for update;
  v_esiste := found;

  if v_esiste and v_row.stato = 'in_corso' then
    if v_row.claim_scade_at > now() then
      return jsonb_build_object('esito', 'in_corso', 'claim_token', null,
                                'esecuzione_id', null);
    end if;
    -- Claim scaduto: la vecchia esecuzione si chiude secondo la fase (in
    -- analisi l'esito è ignoto: costo NULL, la riserva resta nel budget). Il
    -- suo concludi perderà (token cambiato).
    perform public.fn_partenariato_esecuzione_scaduta(
      v_row.esecuzione_id, v_row.fase, v_row.bando_slug);
  end if;

  if v_esiste and not v_ignora then
    v_libera := case
      when v_row.errore_codice is not null then v_row.prossimo_tentativo_at
      else v_row.ultima_esecuzione_at + make_interval(mins => v_cooldown)
    end;
    if v_libera is not null and v_libera > now() then
      raise exception 'L''analisi di questo bando è stata fatta da poco: riprova più tardi'
        using detail = 'partenariato_cooldown';
    end if;
  end if;

  v_gruppo := case p_origine
    when 'batch' then 'batch'
    when 'valutazione' then 'valutazione'
    else 'bando'
  end;

  v_exec := public.fn_partenariati_ai_prenota(
    'partenariato_estrazione', p_origine, v_gruppo, p_budget_cents, p_costo_riservato_cents,
    p_richiedente, p_limite_utente, p_owner, null, p_company, p_bando_id);

  if v_esiste then
    update public.bando_partenariato
    set stato             = 'in_corso',
        fase              = 'documenti',
        claim_token       = v_token,
        claim_scade_at    = now() + make_interval(secs => v_ttl),
        esecuzione_id     = v_exec,
        bando_slug        = coalesce(p_bando_slug, bando_slug),
        bando_titolo      = coalesce(p_bando_titolo, bando_titolo),
        ultima_forzata_at = case when v_ignora then now() else ultima_forzata_at end
    where id = v_row.id;
  else
    insert into public.bando_partenariato
      (bando_id, bando_slug, bando_titolo, stato, fase, claim_token, claim_scade_at,
       esecuzione_id, ultima_forzata_at)
    values
      (p_bando_id, p_bando_slug, p_bando_titolo, 'in_corso', 'documenti', v_token,
       now() + make_interval(secs => v_ttl), v_exec,
       case when v_ignora then now() end);
  end if;

  return jsonb_build_object('esito', 'prenotata', 'claim_token', v_token,
                            'esecuzione_id', v_exec);
end;
$$;

comment on function public.fn_partenariato_prenota(integer, text, text, text, uuid, uuid, uuid, integer, integer, integer, integer, integer, boolean) is
  'Claim atomico dell''estrazione di un bando (advisory lock per bando): claim valido → {esito: in_corso} senza nuova esecuzione; claim scaduto → vecchia esecuzione interrotta (fn_partenariato_esecuzione_scaduta: costo 0 prima dell''analisi, altrimenti NULL) e riacquisizione; cooldown (prossimo_tentativo_at se in errore, altrimenti ultima_esecuzione_at + p_cooldown_minuti) salvo p_ignora_cooldown; prenotazione della spesa (fn_partenariati_ai_prenota, gruppo bando | batch | valutazione); riga in_corso, fase documenti, TTL 60..1800 s. Ritorna {esito: prenotata|in_corso, claim_token, esecuzione_id}. Detail: parametri_non_validi | partenariato_cooldown | ai_limite_utente | ai_budget_esaurito.';

-- ----------------------------------------------------------------------------
-- 7) fn_partenariato_rinnova — heartbeat: aggiorna fase (NULL = invariata) e
--    scadenza SOLO se il token coincide e il claim non è scaduto. false =
--    claim perso: la pipeline si ferma senza chiamare il modello.
--    Detail: fase_non_valida.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_rinnova(
  p_bando_id    integer,
  p_claim_token uuid,
  p_fase        text,
  p_ttl_secondi integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ok boolean;
begin
  if p_fase is not null and p_fase not in ('documenti', 'lettura', 'analisi') then
    raise exception 'Fase non valida: %', p_fase using detail = 'fase_non_valida';
  end if;

  update public.bando_partenariato
  set fase           = coalesce(p_fase, fase),
      claim_scade_at = now() + make_interval(
                         secs => greatest(60, least(1800, coalesce(p_ttl_secondi, 900))))
  where bando_id = p_bando_id
    and claim_token = p_claim_token
    and stato = 'in_corso'
    and claim_scade_at > now()
  returning true into v_ok;

  return coalesce(v_ok, false);
end;
$$;

comment on function public.fn_partenariato_rinnova(integer, uuid, text, integer) is
  'Heartbeat del claim: con il token giusto e il claim non scaduto aggiorna fase (NULL = invariata) e scadenza (TTL 60..1800 s) → true; altrimenti false (claim perso: non chiamare il modello). Detail: fase_non_valida.';

-- ----------------------------------------------------------------------------
-- 8) fn_partenariato_concludi — chiusura dell'estrazione, un solo vincitore
--    (claim_token = p_claim_token e stato in_corso, anche a claim scaduto se
--    nessuno l'ha ripreso). false = perso, nessun effetto.
--    p_dati (oggetto; chiavi ignote ignorate): modalita, modalita_effettiva,
--    extraction, regole, preclassificazione, fonti_usate, catalogo_hash,
--    content_hash, catalogo_aggiornato_at, prompt_version, schema_version,
--    model, input_tokens, output_tokens, cost_cents (NULL o assente = costo
--    ignoto: la riserva resta), errore_codice.
--      - estratta | nessun_segnale → pronta con quell'esito; modalita,
--        modalita_effettiva, extraction e regole = p_dati (assenti = NULL);
--        preclassificazione e fonti_usate = p_dati se presenti; hash, data
--        del catalogo e versioni = p_dati se la chiave c'è; model, token e
--        costo della generazione; estratta_at = verificata_at = now();
--      - riusata → pronta con il risultato corrente invariato, verificata_at
--        = now(), meta (preclassificazione, fonti, hash, versioni) da p_dati
--        se presenti; senza un risultato precedente vale come errore
--        riusata_senza_estrazione;
--      - errore | timeout → pronta con il risultato precedente (se c'è) più
--        errore_codice, altrimenti errore; tentativi_falliti + 1,
--        prossimo_tentativo_at = now() + least(6 h · 2^(n-1), 72 h);
--    sempre: claim e fase azzerati, ultima_esecuzione_at = now(); gli esiti
--    riusciti azzerano errore e backoff. L'esecuzione si chiude SEMPRE
--    (estratta → conclusa) con costo, token, modello e content_hash di p_dati.
--    Detail: esito_non_valido.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_concludi(
  p_bando_id    integer,
  p_claim_token uuid,
  p_esito       text,
  p_dati        jsonb
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_dati   constant jsonb := case when jsonb_typeof(p_dati) = 'object' then p_dati
                                  else '{}'::jsonb end;
  v_cost   constant integer := (v_dati ->> 'cost_cents')::integer;
  v_in     constant integer := coalesce((v_dati ->> 'input_tokens')::integer, 0);
  v_out    constant integer := coalesce((v_dati ->> 'output_tokens')::integer, 0);
  v_model  constant text := v_dati ->> 'model';
  v_row    public.bando_partenariato%rowtype;
  v_esito  text := p_esito;
  v_errore text;
begin
  if p_esito is null
     or p_esito not in ('estratta', 'nessun_segnale', 'riusata', 'errore', 'timeout') then
    raise exception 'Esito di chiusura non valido: %', coalesce(p_esito, 'NULL')
      using detail = 'esito_non_valido';
  end if;

  select * into v_row from public.bando_partenariato
  where bando_id = p_bando_id and claim_token = p_claim_token and stato = 'in_corso'
  for update;
  if not found then
    -- Claim perso (ripreso da altri o chiuso dal failsafe): la sua esecuzione
    -- è già interrotta, con la riserva nel budget.
    return false;
  end if;

  if v_esito = 'riusata' and v_row.esito is null then
    -- Non c'è nulla da riusare: errore di pipeline, mai una pronta vuota.
    v_esito := 'errore';
    v_errore := 'riusata_senza_estrazione';
  end if;

  if v_esito in ('estratta', 'nessun_segnale') then
    update public.bando_partenariato
    set stato                  = 'pronta',
        esito                  = v_esito,
        fase                   = null,
        claim_token            = null,
        claim_scade_at         = null,
        modalita               = v_dati ->> 'modalita',
        modalita_effettiva     = v_dati ->> 'modalita_effettiva',
        extraction             = nullif(v_dati -> 'extraction', 'null'::jsonb),
        regole                 = nullif(v_dati -> 'regole', 'null'::jsonb),
        preclassificazione     = coalesce(nullif(v_dati -> 'preclassificazione', 'null'::jsonb),
                                          preclassificazione),
        fonti_usate            = coalesce(nullif(v_dati -> 'fonti_usate', 'null'::jsonb),
                                          fonti_usate),
        catalogo_hash          = case when v_dati ? 'catalogo_hash'
                                      then v_dati ->> 'catalogo_hash' else catalogo_hash end,
        content_hash           = case when v_dati ? 'content_hash'
                                      then v_dati ->> 'content_hash' else content_hash end,
        catalogo_aggiornato_at = case when v_dati ? 'catalogo_aggiornato_at'
                                      then (v_dati ->> 'catalogo_aggiornato_at')::timestamptz
                                      else catalogo_aggiornato_at end,
        prompt_version         = case when v_dati ? 'prompt_version'
                                      then (v_dati ->> 'prompt_version')::integer
                                      else prompt_version end,
        schema_version         = case when v_dati ? 'schema_version'
                                      then (v_dati ->> 'schema_version')::integer
                                      else schema_version end,
        model                  = v_model,
        input_tokens           = v_in,
        output_tokens          = v_out,
        cost_cents             = coalesce(v_cost, 0),
        estratta_at            = now(),
        verificata_at          = now(),
        ultima_esecuzione_at   = now(),
        errore_codice          = null,
        errore_at              = null,
        tentativi_falliti      = 0,
        prossimo_tentativo_at  = null
    where id = v_row.id;
  elsif v_esito = 'riusata' then
    update public.bando_partenariato
    set stato                  = 'pronta',
        fase                   = null,
        claim_token            = null,
        claim_scade_at         = null,
        preclassificazione     = coalesce(nullif(v_dati -> 'preclassificazione', 'null'::jsonb),
                                          preclassificazione),
        fonti_usate            = coalesce(nullif(v_dati -> 'fonti_usate', 'null'::jsonb),
                                          fonti_usate),
        catalogo_hash          = case when v_dati ? 'catalogo_hash'
                                      then v_dati ->> 'catalogo_hash' else catalogo_hash end,
        content_hash           = case when v_dati ? 'content_hash'
                                      then v_dati ->> 'content_hash' else content_hash end,
        catalogo_aggiornato_at = case when v_dati ? 'catalogo_aggiornato_at'
                                      then (v_dati ->> 'catalogo_aggiornato_at')::timestamptz
                                      else catalogo_aggiornato_at end,
        prompt_version         = case when v_dati ? 'prompt_version'
                                      then (v_dati ->> 'prompt_version')::integer
                                      else prompt_version end,
        schema_version         = case when v_dati ? 'schema_version'
                                      then (v_dati ->> 'schema_version')::integer
                                      else schema_version end,
        verificata_at          = now(),
        ultima_esecuzione_at   = now(),
        errore_codice          = null,
        errore_at              = null,
        tentativi_falliti      = 0,
        prossimo_tentativo_at  = null
    where id = v_row.id;
  else
    -- errore | timeout: il risultato precedente (se c'è) resta servito.
    v_errore := coalesce(v_errore, nullif(v_dati ->> 'errore_codice', ''), v_esito);
    update public.bando_partenariato
    set stato                 = case when esito is not null then 'pronta' else 'errore' end,
        fase                  = null,
        claim_token           = null,
        claim_scade_at        = null,
        errore_codice         = v_errore,
        errore_at             = now(),
        tentativi_falliti     = tentativi_falliti + 1,
        -- Esponente limitato: 6 h · 2^5 supera già il tetto di 72 h.
        prossimo_tentativo_at = now() + least(
                                  interval '6 hours' * power(2, least(tentativi_falliti, 5)),
                                  interval '72 hours'),
        ultima_esecuzione_at  = now()
    where id = v_row.id;
  end if;

  perform public.fn_partenariati_ai_concludi(
    v_row.esecuzione_id,
    case v_esito when 'estratta' then 'conclusa' else v_esito end,
    v_cost, v_in, v_out, v_model, v_errore);

  if v_dati ? 'content_hash' then
    update public.partenariati_ai_esecuzioni
    set content_hash = v_dati ->> 'content_hash'
    where id = v_row.esecuzione_id;
  end if;

  return true;
end;
$$;

comment on function public.fn_partenariato_concludi(integer, uuid, text, jsonb) is
  'Chiude l''estrazione di un bando con un solo vincitore (token del claim e stato in_corso): estratta | nessun_segnale → pronta con i campi di p_dati; riusata → pronta, risultato invariato e verificata_at = now() (senza risultato precedente: errore riusata_senza_estrazione); errore | timeout → pronta con le regole precedenti + errore_codice se c''erano, altrimenti errore, con backoff least(6 h · 2^(n-1), 72 h). Chiude sempre l''esecuzione (costo NULL = ignoto, la riserva resta). false = claim perso. Detail: esito_non_valido.';

-- ----------------------------------------------------------------------------
-- 9) fn_partenariato_chiudi_stale — failsafe (in lettura e nello scheduler):
--    ogni riga in_corso con claim scaduto si chiude come un errore con codice
--    interrotta (pronta se c'era un risultato, altrimenti errore; backoff) e
--    la sua esecuzione diventa interrotta (fn_partenariato_esecuzione_scaduta:
--    costo 0 se il claim non era arrivato all'analisi, altrimenti costo NULL,
--    la riserva resta, e riga timeout_unknown nel registro consumi).
--    Le righe bloccate da una transazione in corso si saltano (SKIP LOCKED):
--    la lettura non attende. Ritorna il numero di righe chiuse.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_chiudi_stale()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_row public.bando_partenariato%rowtype;
  v_n   integer := 0;
begin
  for v_row in
    select * from public.bando_partenariato
    where stato = 'in_corso' and claim_scade_at <= now()
    order by claim_scade_at
    for update skip locked
  loop
    update public.bando_partenariato
    set stato                 = case when esito is not null then 'pronta' else 'errore' end,
        fase                  = null,
        claim_token           = null,
        claim_scade_at        = null,
        errore_codice         = 'interrotta',
        errore_at             = now(),
        tentativi_falliti     = tentativi_falliti + 1,
        prossimo_tentativo_at = now() + least(
                                  interval '6 hours' * power(2, least(tentativi_falliti, 5)),
                                  interval '72 hours'),
        ultima_esecuzione_at  = now()
    where id = v_row.id;

    perform public.fn_partenariato_esecuzione_scaduta(
      v_row.esecuzione_id, v_row.fase, v_row.bando_slug);
    v_n := v_n + 1;
  end loop;

  return v_n;
end;
$$;

comment on function public.fn_partenariato_chiudi_stale() is
  'Failsafe dei claim scaduti: righe in_corso scadute → pronta (se c''era un risultato) o errore, errore_codice interrotta, backoff; esecuzione interrotta via fn_partenariato_esecuzione_scaduta (costo 0 prima dell''analisi; in analisi costo NULL, la riserva resta nel budget, e riga timeout_unknown in api_usage_events). Salta le righe bloccate. Ritorna quante righe ha chiuso.';

-- ----------------------------------------------------------------------------
-- 10) fn_partenariato_bando_ids — id dei bandi con esito estratta e
--     modalita_effettiva tra quelle chieste, dai più recenti (estratta_at
--     desc, poi bando_id), al massimo least(p_limite, 2000) (NULL = 2000).
--     Un solo array: nessun tetto max-rows di PostgREST.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_bando_ids(
  p_modalita text[],
  p_limite   integer
)
returns integer[]
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(array_agg(s.bando_id order by s.estratta_at desc nulls last, s.bando_id),
                  '{}'::integer[])
  from (
    select bp.bando_id, bp.estratta_at
    from public.bando_partenariato bp
    where bp.esito = 'estratta'
      and bp.modalita_effettiva = any (p_modalita)
    order by bp.estratta_at desc nulls last, bp.bando_id
    limit greatest(least(p_limite, 2000), 0)
  ) s;
$$;

comment on function public.fn_partenariato_bando_ids(text[], integer) is
  'Id dei bandi con esito estratta e modalita_effettiva = any(p_modalita), ordinati per estratta_at desc (poi bando_id), al massimo least(p_limite, 2000); NULL = 2000. Array vuoto se nessuno.';

-- ----------------------------------------------------------------------------
-- 11) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--     esplicite su ogni tabella nuova; nessuna funzione nuova eseguibile dai
--     ruoli esposti (Supabase concede EXECUTE di default a PUBLIC).
-- ----------------------------------------------------------------------------
alter table public.bando_partenariato enable row level security;
alter table public.partenariati_ai_esecuzioni enable row level security;
alter table public.partenariati_runs enable row level security;

revoke all on public.bando_partenariato from anon, authenticated;
revoke all on public.partenariati_ai_esecuzioni from anon, authenticated;
revoke all on public.partenariati_runs from anon, authenticated;

revoke execute on function public.fn_partenariati_ai_prenota(text, text, text, integer, integer, uuid, integer, uuid, integer, uuid, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariati_ai_concludi(uuid, text, integer, integer, integer, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_esecuzione_scaduta(uuid, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_prenota(integer, text, text, text, uuid, uuid, uuid, integer, integer, integer, integer, integer, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_rinnova(integer, uuid, text, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_concludi(integer, uuid, text, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_chiudi_stale()
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_bando_ids(text[], integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0034 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle). Nessun dato di altre tabelle dipende da queste.
-- 1) drop function public.fn_partenariato_bando_ids(text[], integer);
--    drop function public.fn_partenariato_chiudi_stale();
--    drop function public.fn_partenariato_concludi(integer, uuid, text, jsonb);
--    drop function public.fn_partenariato_rinnova(integer, uuid, text, integer);
--    drop function public.fn_partenariato_prenota(integer, text, text, text, uuid, uuid,
--      uuid, integer, integer, integer, integer, integer, boolean);
--    drop function public.fn_partenariato_esecuzione_scaduta(uuid, text, text);
--    drop function public.fn_partenariati_ai_concludi(uuid, text, integer, integer,
--      integer, text, text);
--    drop function public.fn_partenariati_ai_prenota(text, text, text, integer, integer,
--      uuid, integer, uuid, integer, uuid, integer);
-- 2) drop table public.partenariati_runs;
--    drop table public.partenariati_ai_esecuzioni;   -- si perde il registro della
--      spesa LLM: esportarlo prima se serve alla contabilità (api_usage_events
--      resta comunque la fonte dei consumi per provider).
--    drop table public.bando_partenariato;           -- si perdono le regole
--      estratte (rigenerabili, con nuova spesa).
-- ============================================================================
