-- ============================================================================
-- BandoFit — DB primario, migration 0035: PROFILO PARTNER dell'azienda e
-- REGISTRO DEI CONSENSI (WP4, piano docs/partenariati.md §2.1 T4-T6, §2.4
-- P1-P7, §3 riga 0035, §13 Q2/Q9/Q12/Q13/Q21).
--
--   1) company_partner_profiles — profilo partner 1:1 con l'azienda (cascade
--      dalla riga company_profiles): opt-in, anonimato, referente, competenze,
--      interessi, esperienze, sospensione (le RPC di sospensione arrivano in
--      WP9) e bozza AI asincrona;
--   2) partner_consents — registro APPEND-ONLY dei consensi (opt-in, revoca,
--      anonimato, referente), SENZA FK: è la prova del consenso e deve
--      sopravvivere alle cancellazioni;
--   3) trg_cpp_campi_protetti — trigger con GUC: visibilità, anonimato,
--      consenso, referente, sospensione e chiavi del profilo cambiano SOLO
--      dentro le RPC (che accendono app.partner_consenso per la durata della
--      loro scrittura);
--   4) trg_cpp_revoca_su_cambio_azienda — revoca automatica (origine sistema)
--      se l'azienda di un profilo visibile viene eliminata, archiviata o
--      cambia P.IVA o ragione sociale (T5, Q21);
--   5) fn_partner_consenso — concedi / revoca / anonimato, con identità dal
--      registro (T5) e verifica del legale rappresentante per il nominativo
--      (Q9), registro + audit nella stessa transazione;
--   6) fn_partner_referente — proposta del titolare e accettazione del membro
--      (Q13), registro + audit;
--   7) fn_partner_bozza_ai_esecuzione_interrotta / _prenota / _concludi /
--      _chiudi_stale — bozza AI del profilo sul budget fail-closed del modulo
--      (0034, gruppo altri): prenotazione, chiusura atomica del job (bozza +
--      esecuzione) e failsafe, che registra il consumo delle esecuzioni che
--      chiude lui.
--
-- Ordine dei lock (uguale in tutte le RPC, coerente con la 0034): riga
-- company_profiles (FOR NO KEY UPDATE: serializza con UPDATE e DELETE
-- dell'azienda senza bloccare gli insert con FK verso di essa), poi riga del
-- profilo partner, poi (solo la bozza AI) il lock globale del budget, infine
-- la riga dell'esecuzione in partenariati_ai_esecuzioni.
--
-- ADDITIVA: tabelle, trigger e funzioni nuove; nessuna funzione esistente
-- ridefinita (fn_soft_delete_company, fn_reconcile_companies e l'import
-- restano intatti: la revoca la fa il trigger AFTER UPDATE). Il backend
-- attuale non le usa. Da eseguire IN UN'UNICA TRANSAZIONE (begin; ...
-- commit;). Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Profilo partner (1:1 con l'azienda). I codici (competenze, tipi soggetto)
--    li valida il backend sul vocabolario versionato in codice
--    (services/partenariato_vocabolario.py); settori, regioni e tipologie sono
--    id delle lookup del DB SECONDARIO: nessuna FK cross-database.
-- ----------------------------------------------------------------------------
create table public.company_partner_profiles (
  company_profile_id         uuid primary key
                               references public.company_profiles (id) on delete cascade,
  family_parent_id           uuid not null,
  codice_pubblico            uuid not null default gen_random_uuid()
    constraint cpp_codice_pubblico_key unique,
  visibile_come_partner      boolean not null default false,
  anonimo                    boolean not null default true,
  consenso_versione          text
    constraint cpp_consenso_versione_check
    check (consenso_versione is null or char_length(consenso_versione) between 1 and 50),
  consenso_at                timestamptz,
  accetta_inviti             boolean not null default true,
  descrizione_competenze     text
    constraint cpp_descrizione_competenze_check
    check (descrizione_competenze is null or char_length(descrizione_competenze) <= 2000),
  competenze                 text[] not null default '{}'
    constraint cpp_competenze_check check (cardinality(competenze) <= 15),
  competenze_libere          text[] not null default '{}'
    constraint cpp_competenze_libere_check check (cardinality(competenze_libere) <= 10),
  tipi_soggetto              text[] not null default '{}'
    constraint cpp_tipi_soggetto_check check (cardinality(tipi_soggetto) <= 8),
  ruoli_disponibili          text[] not null default '{partner}'
    constraint cpp_ruoli_disponibili_check
    check (cardinality(ruoli_disponibili) >= 1
           and ruoli_disponibili <@ array['capofila', 'partner']::text[]),
  settori_interesse          integer[] not null default '{}'
    constraint cpp_settori_interesse_check check (cardinality(settori_interesse) <= 20),
  regioni_interesse          integer[] not null default '{}'
    constraint cpp_regioni_interesse_check check (cardinality(regioni_interesse) <= 21),
  paesi_interesse            text[] not null default '{}'
    constraint cpp_paesi_interesse_check
    check (cardinality(paesi_interesse) <= 30
           and array_to_string(paesi_interesse, ',', '*') ~ '^([A-Z]{2}(,[A-Z]{2})*)?$'),
  forme_accettate            text[] not null default '{}'
    constraint cpp_forme_accettate_check
    check (forme_accettate <@ array['ats', 'ati_rti', 'rete_contratto', 'rete_soggetto',
                                    'consorzio', 'accordo_partenariato',
                                    'consorzio_ue']::text[]),
  esperienze                 jsonb not null default '[]'::jsonb
    constraint cpp_esperienze_check
    check (case when jsonb_typeof(esperienze) = 'array'
                then jsonb_array_length(esperienze) <= 20
                else false end),
  certificazioni             text[] not null default '{}'
    constraint cpp_certificazioni_check check (cardinality(certificazioni) <= 20),
  infrastrutture             text
    constraint cpp_infrastrutture_check
    check (infrastrutture is null or char_length(infrastrutture) <= 2000),
  categorie_bando_escluse    integer[] not null default '{}',
  vocabolario_versione       integer not null default 1,
  completezza                smallint not null default 0
    constraint cpp_completezza_check check (completezza between 0 and 100),
  referente_user_id          uuid references public.profiles (id) on delete set null,
  referente_proposto_user_id uuid references public.profiles (id) on delete set null,
  referente_proposto_at      timestamptz,
  sospeso_at                 timestamptz,
  sospeso_motivo             text
    constraint cpp_sospeso_motivo_check
    check (sospeso_motivo is null or char_length(sospeso_motivo) <= 500),
  sospeso_da                 uuid,
  bozza_ai                   jsonb
    constraint cpp_bozza_ai_check check (bozza_ai is null or jsonb_typeof(bozza_ai) = 'object'),
  bozza_ai_stato             text
    constraint cpp_bozza_ai_stato_check check (bozza_ai_stato in ('in_corso', 'pronta', 'errore')),
  bozza_ai_avviata_at        timestamptz,
  bozza_ai_at                timestamptz,
  bozza_ai_errore            text,
  bozza_ai_esecuzione_id     uuid,
  updated_by                 uuid,
  created_at                 timestamptz not null default now(),
  updated_at                 timestamptz not null default now(),
  -- La visibilità richiede un consenso registrato (versione + data).
  constraint cpp_visibile_richiede_consenso
    check (not visibile_come_partner
           or (consenso_versione is not null and consenso_at is not null)),
  -- Una bozza in corso ha sempre la sua esecuzione e il suo avvio (failsafe).
  constraint cpp_bozza_in_corso_coerente
    check (bozza_ai_stato is distinct from 'in_corso'
           or (bozza_ai_esecuzione_id is not null and bozza_ai_avviata_at is not null))
);

comment on table public.company_partner_profiles is
  'Profilo partner dell''azienda (1:1, cascade dalla riga company_profiles). visibile_come_partner, anonimo, consenso_*, referente_*, sospeso_*, codice_pubblico, company_profile_id e family_parent_id cambiano SOLO tramite le RPC (trigger trg_cpp_campi_protetti con la GUC app.partner_consenso); ogni cambio di consenso o anonimato scrive partner_consents e audit_log nella stessa transazione. Verso terzi esce solo la proiezione a whitelist del backend (profilo_pubblico).';
comment on column public.company_partner_profiles.family_parent_id is
  'Owner (titolare) dell''azienda: = company_profiles.parent_id (verificato dal trigger all''insert). Senza FK: denormalizzato per i filtri per owner.';
comment on column public.company_partner_profiles.codice_pubblico is
  'Handle OPACO verso terzi (mai company_profile_id).';
comment on column public.company_partner_profiles.visibile_come_partner is
  'Opt-in: l''azienda compare come partner suggerito. Cambia solo con fn_partner_consenso (o la revoca automatica del trigger su company_profiles).';
comment on column public.company_partner_profiles.anonimo is
  'true = verso terzi niente denominazione né dettagli identificanti (Q12). Default true; cambia solo con fn_partner_consenso.';
comment on column public.company_partner_profiles.consenso_versione is
  'Versione dell''informativa dell''ultimo consenso concesso; resta come storico dopo la revoca.';
comment on column public.company_partner_profiles.tipi_soggetto is
  'Solo i tipi DICHIARATI dall''utente: quelli dedotti dal registro si calcolano in lettura.';
comment on column public.company_partner_profiles.settori_interesse is
  'Id della lookup settori del DB secondario (nessuna FK).';
comment on column public.company_partner_profiles.regioni_interesse is
  'Id della lookup regioni del DB secondario (nessuna FK).';
comment on column public.company_partner_profiles.paesi_interesse is
  'Codici ISO-3166 alpha-2 maiuscoli.';
comment on column public.company_partner_profiles.categorie_bando_escluse is
  'Id della lookup tipologie_bando del DB secondario (nessuna FK).';
comment on column public.company_partner_profiles.esperienze is
  'Array (max 20) di {programma, programma_id, anno, ruolo, titolo, esito}: forma validata dal backend.';
comment on column public.company_partner_profiles.referente_user_id is
  'Referente che ha ACCETTATO (fn_partner_referente, Q13). NULL = il titolare. Il backend lo considera effettivo solo se la membership è ancora attiva con accesso all''azienda.';
comment on column public.company_partner_profiles.referente_proposto_user_id is
  'Membro proposto dal titolare come referente, in attesa della sua accettazione.';
comment on column public.company_partner_profiles.sospeso_at is
  'Sospensione da moderazione (RPC in WP9): un profilo sospeso non si mostra e non può concedere il consenso.';
comment on column public.company_partner_profiles.bozza_ai is
  'Proposta AI (descrizione + competenze): MAI visibile a terzi, la applica l''utente.';
comment on column public.company_partner_profiles.bozza_ai_esecuzione_id is
  'Esecuzione (partenariati_ai_esecuzioni.id) della bozza in corso o ultima: senza FK. Il job chiude la bozza solo se coincide.';

create trigger trg_cpp_updated_at
  before update on public.company_partner_profiles
  for each row execute function public.set_updated_at();

-- Candidati del matching (WP6): visibili e non sospesi.
create index cpp_visibili_idx on public.company_partner_profiles (company_profile_id)
  where visibile_come_partner and sospeso_at is null;
create index cpp_family_idx on public.company_partner_profiles (family_parent_id);
-- ON DELETE SET NULL verso profiles senza scansione completa.
create index cpp_referente_idx on public.company_partner_profiles (referente_user_id)
  where referente_user_id is not null;
create index cpp_referente_proposto_idx on public.company_partner_profiles
  (referente_proposto_user_id) where referente_proposto_user_id is not null;

-- ----------------------------------------------------------------------------
-- 2) Registro dei consensi: append-only, SENZA FK (prova del consenso: deve
--    sopravvivere alla cancellazione di aziende e utenti). origine 'sistema'
--    = revoca automatica: nessun attore e nessuna informativa.
-- ----------------------------------------------------------------------------
create table public.partner_consents (
  id                   bigint generated always as identity primary key,
  company_profile_id   uuid not null,
  family_parent_id     uuid not null,
  azione               text not null
    constraint pc_azione_check
    check (azione in ('concesso', 'revocato', 'anonimato', 'referente_concesso',
                      'referente_revocato')),
  informativa_versione text
    constraint pc_informativa_versione_check
    check (informativa_versione is null or char_length(informativa_versione) between 1 and 50),
  origine              text not null
    constraint pc_origine_check
    check (origine in ('import_piva', 'pagina_azienda', 'wizard_call', 'admin', 'sistema')),
  attore_user_id       uuid,
  anonimo              boolean,
  referente_user_id    uuid,
  motivo               text
    constraint pc_motivo_check check (motivo is null or char_length(motivo) <= 200),
  created_at           timestamptz not null default now(),
  constraint pc_versione_richiesta check (origine = 'sistema' or informativa_versione is not null),
  constraint pc_attore_richiesto check (origine = 'sistema' or attore_user_id is not null),
  -- Concessione e anonimato registrano la scelta; le azioni sul referente la persona.
  constraint pc_anonimo_registrato
    check (azione not in ('concesso', 'anonimato') or anonimo is not null),
  constraint pc_referente_registrato
    check (azione not in ('referente_concesso', 'referente_revocato')
           or referente_user_id is not null)
);

comment on table public.partner_consents is
  'Registro APPEND-ONLY dei consensi del profilo partner (opt-in, revoca, anonimato, referente), senza FK: sopravvive alle cancellazioni. Scritto SOLO dalle RPC fn_partner_consenso / fn_partner_referente e dal trigger di revoca automatica. Conservazione dichiarata: 5 anni dopo la revoca (Q22).';
comment on column public.partner_consents.informativa_versione is
  'Versione dell''informativa accettata (per revocato: quella del consenso revocato). NULL solo con origine sistema.';
comment on column public.partner_consents.origine is
  'import_piva | pagina_azienda | wizard_call (dal client) | admin | sistema (revoca automatica: attore NULL).';
comment on column public.partner_consents.anonimo is
  'Scelta di anonimato registrata con concesso / anonimato (e lo stato al momento della revoca).';
comment on column public.partner_consents.referente_user_id is
  'Persona interessata dalle azioni referente_concesso / referente_revocato.';
comment on column public.partner_consents.motivo is
  'Revoche automatiche: azienda_eliminata | azienda_archiviata | piva_cambiata | ragione_sociale_cambiata; referente: rinuncia | rimosso | torna_titolare | sostituito.';

create index partner_consents_company_idx on public.partner_consents
  (company_profile_id, created_at desc);

-- Append-only anche per il service_role (che bypassa la RLS): modello
-- fn_addon_ledger_readonly (0028), più il blocco del TRUNCATE.
create or replace function public.fn_partner_consents_readonly()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception 'partner_consents è append-only' using detail = 'registro_append_only';
end;
$$;

comment on function public.fn_partner_consents_readonly() is
  'Trigger: rifiuta UPDATE, DELETE e TRUNCATE su partner_consents (registro append-only). Detail: registro_append_only.';

create trigger trg_partner_consents_readonly
  before update or delete on public.partner_consents
  for each row execute function public.fn_partner_consents_readonly();

create trigger trg_partner_consents_no_truncate
  before truncate on public.partner_consents
  for each statement execute function public.fn_partner_consents_readonly();

-- ----------------------------------------------------------------------------
-- 3) Campi protetti del profilo. Senza la GUC app.partner_consenso = 'on'
--    (accesa SOLO dalle RPC di questa migration, locale alla transazione e
--    ripristinata subito dopo la scrittura):
--      - INSERT: visibilità, anonimato, consenso_*, referente_* e sospeso_*
--        devono avere i default;
--      - UPDATE: visibile_come_partner, anonimo, consenso_versione,
--        consenso_at, referente_user_id, referente_proposto_user_id,
--        referente_proposto_at, sospeso_at, sospeso_motivo, sospeso_da,
--        codice_pubblico, company_profile_id e family_parent_id non cambiano.
--    Unica eccezione: l'ON DELETE SET NULL delle FK verso profiles (la riga
--    dell'utente referente o proposto non esiste più); la proposta orfana
--    perde anche la data.
--    Sempre, anche con la GUC: all'insert family_parent_id deve essere il
--    titolare dell'azienda.
--    Detail: campo_protetto.
-- ----------------------------------------------------------------------------
create or replace function public.fn_cpp_campi_protetti()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_referente_cancellato boolean := false;
  v_proposto_cancellato  boolean := false;
begin
  if tg_op = 'INSERT' and new.family_parent_id is distinct from (
       select cp.parent_id from public.company_profiles cp
       where cp.id = new.company_profile_id) then
    raise exception 'Il profilo partner appartiene al titolare dell''azienda'
      using detail = 'campo_protetto';
  end if;

  if coalesce(current_setting('app.partner_consenso', true), '') = 'on' then
    return new;
  end if;

  if tg_op = 'INSERT' then
    if new.visibile_come_partner is distinct from false
       or new.anonimo is distinct from true
       or new.consenso_versione is not null
       or new.consenso_at is not null
       or new.referente_user_id is not null
       or new.referente_proposto_user_id is not null
       or new.referente_proposto_at is not null
       or new.sospeso_at is not null
       or new.sospeso_motivo is not null
       or new.sospeso_da is not null then
      raise exception 'Consenso, anonimato, referente e sospensione del profilo partner si impostano solo con le funzioni dedicate'
        using detail = 'campo_protetto';
    end if;
    return new;
  end if;

  -- UPDATE: l'ON DELETE SET NULL arriva dopo la cancellazione dell'utente.
  if old.referente_user_id is not null and new.referente_user_id is null then
    v_referente_cancellato := not exists (
      select 1 from public.profiles p where p.id = old.referente_user_id);
  end if;
  if old.referente_proposto_user_id is not null and new.referente_proposto_user_id is null then
    v_proposto_cancellato := not exists (
      select 1 from public.profiles p where p.id = old.referente_proposto_user_id);
  end if;

  if new.visibile_come_partner is distinct from old.visibile_come_partner
     or new.anonimo is distinct from old.anonimo
     or new.consenso_versione is distinct from old.consenso_versione
     or new.consenso_at is distinct from old.consenso_at
     or (new.referente_user_id is distinct from old.referente_user_id
         and not v_referente_cancellato)
     or (new.referente_proposto_user_id is distinct from old.referente_proposto_user_id
         and not v_proposto_cancellato)
     or (new.referente_proposto_at is distinct from old.referente_proposto_at
         and not v_proposto_cancellato)
     or new.sospeso_at is distinct from old.sospeso_at
     or new.sospeso_motivo is distinct from old.sospeso_motivo
     or new.sospeso_da is distinct from old.sospeso_da
     or new.codice_pubblico is distinct from old.codice_pubblico
     or new.company_profile_id is distinct from old.company_profile_id
     or new.family_parent_id is distinct from old.family_parent_id then
    raise exception 'Consenso, anonimato, referente e sospensione del profilo partner cambiano solo con le funzioni dedicate'
      using detail = 'campo_protetto';
  end if;

  if v_proposto_cancellato then
    new.referente_proposto_at := null;
  end if;
  return new;
end;
$$;

comment on function public.fn_cpp_campi_protetti() is
  'Trigger BEFORE INSERT OR UPDATE su company_partner_profiles: senza la GUC app.partner_consenso = on i campi protetti (visibilità, anonimato, consenso_*, referente_*, sospeso_*, codice_pubblico, company_profile_id, family_parent_id) restano ai default (insert) o invariati (update), salvo l''ON DELETE SET NULL delle FK verso profiles. All''insert family_parent_id = titolare dell''azienda. Detail: campo_protetto.';

create trigger trg_cpp_campi_protetti
  before insert or update on public.company_partner_profiles
  for each row execute function public.fn_cpp_campi_protetti();

-- ----------------------------------------------------------------------------
-- 4) Revoca automatica (origine sistema) quando l'azienda di un profilo
--    VISIBILE viene soft-deleted o archiviata (da null a valorizzato) o cambia
--    P.IVA o ragione sociale: visibile_come_partner = false, riga
--    partner_consents 'revocato' senza attore con il motivo, audit
--    partner.consenso_revocato. Nessuna funzione esistente modificata: scatta
--    dentro fn_soft_delete_company, fn_reconcile_companies e gli update del
--    backend (import compreso). Profilo non visibile → nessun effetto.
-- ----------------------------------------------------------------------------
create or replace function public.fn_cpp_revoca_su_cambio_azienda()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivo   text;
  v_guc      text;
  v_prof     public.company_partner_profiles%rowtype;
  v_revocato boolean;
begin
  v_motivo := case
    when old.deleted_at is null and new.deleted_at is not null then 'azienda_eliminata'
    when old.archived_at is null and new.archived_at is not null then 'azienda_archiviata'
    when new.partita_iva is distinct from old.partita_iva then 'piva_cambiata'
    when new.ragione_sociale is distinct from old.ragione_sociale then 'ragione_sociale_cambiata'
  end;
  if v_motivo is null then
    return null;
  end if;

  v_guc := current_setting('app.partner_consenso', true);
  perform set_config('app.partner_consenso', 'on', true);
  update public.company_partner_profiles
  set visibile_come_partner = false
  where company_profile_id = new.id and visibile_come_partner
  returning * into v_prof;
  v_revocato := found;
  perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);

  if v_revocato then
    insert into public.partner_consents
      (company_profile_id, family_parent_id, azione, informativa_versione, origine,
       attore_user_id, anonimo, motivo)
    values
      (new.id, v_prof.family_parent_id, 'revocato', v_prof.consenso_versione, 'sistema',
       null, v_prof.anonimo, v_motivo);

    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (null, 'partner.consenso_revocato', v_prof.family_parent_id, v_prof.family_parent_id,
            jsonb_build_object('company_profile_id', new.id, 'origine', 'sistema',
                               'motivo', v_motivo));
  end if;
  return null;
end;
$$;

comment on function public.fn_cpp_revoca_su_cambio_azienda() is
  'Trigger AFTER UPDATE su company_profiles: profilo partner visibile + azienda soft-deleted | archiviata | P.IVA cambiata | ragione sociale cambiata → visibile false, partner_consents revocato con origine sistema (attore NULL) e motivo azienda_eliminata | azienda_archiviata | piva_cambiata | ragione_sociale_cambiata, audit partner.consenso_revocato.';

create trigger trg_cpp_revoca_su_cambio_azienda
  after update of deleted_at, archived_at, partita_iva, ragione_sociale
  on public.company_profiles
  for each row
  when ((old.deleted_at is null and new.deleted_at is not null)
        or (old.archived_at is null and new.archived_at is not null)
        or old.partita_iva is distinct from new.partita_iva
        or old.ragione_sociale is distinct from new.ragione_sociale)
  execute function public.fn_cpp_revoca_su_cambio_azienda();

-- ----------------------------------------------------------------------------
-- 5) fn_partner_consenso — opt-in, revoca e anonimato del profilo partner.
--    Nell'ordine:
--      - p_azione in (concedi, revoca, anonimato) → azione_non_valida;
--        p_origine nei 5 valori → origine_non_valida; dal client (origine
--        import_piva | pagina_azienda | wizard_call) l'attore è il titolare,
--        con origine admin l'attore c'è → attore_non_titolare;
--      - azienda viva del titolare, bloccata → company_not_found; la riga del
--        profilo si crea se manca e si blocca;
--      - concedi: p_anonimo obbligatorio (anonimato_obbligatorio), versione
--        1..50 (versione_non_valida), non sospeso (profilo_sospeso), identità
--        dal registro T5 (identita_non_verificata: company_data con
--        piva_fetched = partita_iva dell'azienda, stato attiva e, se
--        p_richiedi_non_sandbox — NULL vale true — non sandbox), per il
--        nominativo il titolare con CF verificato tra i legali rappresentanti
--        in company_people (rappresentante_non_verificato). Già visibile con
--        stessa versione e anonimato → cambiato false senza scrivere nulla;
--        altrimenti visibile, consenso_versione/_at, anonimo + registro
--        concesso + audit partner.consenso_concesso;
--      - revoca: non visibile → cambiato false; altrimenti visibile false (i
--        consenso_* restano come storico) + registro revocato (con la
--        versione del consenso revocato) + audit partner.consenso_revocato;
--      - anonimato: p_anonimo obbligatorio; uguale → cambiato false; verso il
--        nominativo su un profilo visibile le stesse verifiche di identità e
--        rappresentante; update + registro anonimato (versione = p_versione,
--        altrimenti quella del consenso) + audit partner.anonimato_cambiato.
--    Ritorna {visibile, anonimo, consenso_versione, consenso_at, cambiato}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_consenso(
  p_owner                uuid,
  p_company              uuid,
  p_attore               uuid,
  p_azione               text,
  p_versione             text,
  p_origine              text,
  p_anonimo              boolean,
  p_richiedi_non_sandbox boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_guc      text;
  v_prof     public.company_partner_profiles%rowtype;
  v_versione text;
  v_cambiato boolean := false;
begin
  if p_azione is null or p_azione not in ('concedi', 'revoca', 'anonimato') then
    raise exception 'Azione sul consenso non valida' using detail = 'azione_non_valida';
  end if;
  if p_origine is null
     or p_origine not in ('import_piva', 'pagina_azienda', 'wizard_call', 'admin', 'sistema') then
    raise exception 'Origine del consenso non valida' using detail = 'origine_non_valida';
  end if;
  if (p_origine not in ('admin', 'sistema') and p_attore is distinct from p_owner)
     or (p_origine = 'admin' and p_attore is null) then
    raise exception 'Il profilo partner lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner
    and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  insert into public.company_partner_profiles (company_profile_id, family_parent_id)
  values (p_company, p_owner)
  on conflict (company_profile_id) do nothing;

  select * into v_prof from public.company_partner_profiles
  where company_profile_id = p_company
  for update;

  if p_azione in ('concedi', 'anonimato') and p_anonimo is null then
    raise exception 'Scegli se mostrare il nome dell''azienda o restare anonima'
      using detail = 'anonimato_obbligatorio';
  end if;

  if p_azione = 'concedi' then
    if p_versione is null or char_length(p_versione) not between 1 and 50 then
      raise exception 'Versione dell''informativa non valida' using detail = 'versione_non_valida';
    end if;
    if v_prof.sospeso_at is not null then
      raise exception 'Il profilo partner è sospeso' using detail = 'profilo_sospeso';
    end if;
  end if;

  -- Identità (T5) e rappresentante (Q9): alla concessione e al passaggio al
  -- nominativo di un profilo già visibile.
  if p_azione = 'concedi'
     or (p_azione = 'anonimato' and not p_anonimo and v_prof.anonimo
         and v_prof.visibile_come_partner) then
    if not exists (
      select 1
      from public.company_data cd
      join public.company_profiles cp on cp.id = cd.company_profile_id
      where cd.company_profile_id = p_company
        and cd.piva_fetched = cp.partita_iva
        and lower(btrim(cd.stato_impresa)) = 'attiva'
        and (not coalesce(p_richiedi_non_sandbox, true) or not cd.sandbox)
    ) then
      raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
        using detail = 'identita_non_verificata';
    end if;
    if not p_anonimo and not exists (
      select 1
      from public.profiles pr
      join public.company_people pe
        on upper(btrim(pe.codice_fiscale)) = upper(btrim(pr.codice_fiscale))
      where pr.id = p_owner
        and pr.cf_verified_at is not null
        and pe.company_profile_id = p_company
        and pe.is_legale_rappresentante
    ) then
      raise exception 'Il titolare non risulta legale rappresentante verificato'
        using detail = 'rappresentante_non_verificato';
    end if;
  end if;

  v_guc := current_setting('app.partner_consenso', true);

  if p_azione = 'concedi' then
    if not (v_prof.visibile_come_partner
            and v_prof.consenso_versione = p_versione
            and v_prof.anonimo = p_anonimo) then
      perform set_config('app.partner_consenso', 'on', true);
      update public.company_partner_profiles
      set visibile_come_partner = true,
          consenso_versione     = p_versione,
          consenso_at           = now(),
          anonimo               = p_anonimo
      where company_profile_id = p_company
      returning * into v_prof;
      perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);

      insert into public.partner_consents
        (company_profile_id, family_parent_id, azione, informativa_versione, origine,
         attore_user_id, anonimo)
      values
        (p_company, p_owner, 'concesso', p_versione, p_origine, p_attore, p_anonimo);
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (p_attore, 'partner.consenso_concesso', p_owner, p_owner,
              jsonb_build_object('company_profile_id', p_company, 'versione', p_versione,
                                 'origine', p_origine, 'anonimo', p_anonimo));
      v_cambiato := true;
    end if;

  elsif p_azione = 'revoca' then
    if v_prof.visibile_come_partner then
      perform set_config('app.partner_consenso', 'on', true);
      update public.company_partner_profiles
      set visibile_come_partner = false
      where company_profile_id = p_company
      returning * into v_prof;
      perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);

      insert into public.partner_consents
        (company_profile_id, family_parent_id, azione, informativa_versione, origine,
         attore_user_id, anonimo)
      values
        (p_company, p_owner, 'revocato', v_prof.consenso_versione, p_origine, p_attore,
         v_prof.anonimo);
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (p_attore, 'partner.consenso_revocato', p_owner, p_owner,
              jsonb_build_object('company_profile_id', p_company,
                                 'versione', v_prof.consenso_versione, 'origine', p_origine));
      v_cambiato := true;
    end if;

  elsif p_anonimo is distinct from v_prof.anonimo then
    -- anonimato
    v_versione := coalesce(p_versione, v_prof.consenso_versione);
    if (v_versione is null and p_origine <> 'sistema')
       or char_length(v_versione) not between 1 and 50 then
      raise exception 'Versione dell''informativa non valida' using detail = 'versione_non_valida';
    end if;

    perform set_config('app.partner_consenso', 'on', true);
    update public.company_partner_profiles
    set anonimo = p_anonimo
    where company_profile_id = p_company
    returning * into v_prof;
    perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);

    insert into public.partner_consents
      (company_profile_id, family_parent_id, azione, informativa_versione, origine,
       attore_user_id, anonimo)
    values
      (p_company, p_owner, 'anonimato', v_versione, p_origine, p_attore, p_anonimo);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, 'partner.anonimato_cambiato', p_owner, p_owner,
            jsonb_build_object('company_profile_id', p_company, 'anonimo', p_anonimo,
                               'origine', p_origine));
    v_cambiato := true;
  end if;

  return jsonb_build_object(
    'visibile', v_prof.visibile_come_partner,
    'anonimo', v_prof.anonimo,
    'consenso_versione', v_prof.consenso_versione,
    'consenso_at', v_prof.consenso_at,
    'cambiato', v_cambiato);
end;
$$;

comment on function public.fn_partner_consenso(uuid, uuid, uuid, text, text, text, boolean, boolean) is
  'Consenso del profilo partner (azienda viva del titolare, lock riga azienda e profilo): concedi (anonimato obbligatorio, versione 1..50, non sospeso, identità dal registro T5 — company_data con piva_fetched = partita_iva, stato attiva, non sandbox se richiesto — e, per il nominativo, titolare con CF verificato tra i legali rappresentanti) | revoca | anonimato (verso il nominativo di un profilo visibile: stesse verifiche). Ogni cambio scrive partner_consents e audit_log; stato già uguale → cambiato false senza scritture. Ritorna {visibile, anonimo, consenso_versione, consenso_at, cambiato}. Detail: azione_non_valida | origine_non_valida | attore_non_titolare | company_not_found | anonimato_obbligatorio | versione_non_valida | profilo_sospeso | identita_non_verificata | rappresentante_non_verificato.';

-- ----------------------------------------------------------------------------
-- 6) fn_partner_referente — referente del profilo (Q13: un membro diventa
--    referente solo con la propria accettazione). Stessa guardia azienda di
--    fn_partner_consenso. Membro valido = family_members del titolare con
--    status active e visibilità (family_member_company_access) sull'azienda.
--      - proponi (titolare): p_user = titolare → il referente torna il
--        titolare (referente e proposta azzerati; registro referente_revocato
--        se c'era un membro); altrimenti membro valido (referente_non_valido)
--        → proposta; il referente effettivo NON cambia finché lui non accetta;
--        riproporre il referente in carica annulla la proposta pendente;
--      - annulla_proposta (titolare) → azzera SOLO la proposta pendente: il
--        referente in carica resta;
--      - accetta (il proposto, altrimenti nessuna_proposta_referente; versione
--        dell'informativa del referente 1..50; membro ancora valido) →
--        referente = lui, proposta azzerata, registro referente_concesso
--        (l'eventuale referente precedente: referente_revocato, sostituito);
--      - rifiuta (il proposto) → proposta azzerata;
--      - revoca (il referente corrente, altrimenti azione_non_valida) oppure
--        rimuovi (il titolare) → referente NULL + registro referente_revocato.
--    La versione di un referente_revocato è quella della sua accettazione
--    (ultimo referente_concesso), altrimenti p_versione.
--    Ritorna {referente_user_id, referente_proposto_user_id}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_referente(
  p_owner    uuid,
  p_company  uuid,
  p_attore   uuid,
  p_azione   text,
  p_user     uuid,
  p_versione text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_guc       text;
  v_prof      public.company_partner_profiles%rowtype;
  v_revocato  uuid;     -- membro che smette di essere referente
  v_motivo    text;
  v_concesso  uuid;     -- membro che diventa referente
  v_audit     text;     -- transizioni senza riga di registro
  v_target    uuid;
  v_versione  text;
begin
  if p_azione is null
     or p_azione not in ('proponi', 'annulla_proposta', 'accetta', 'rifiuta', 'revoca',
                         'rimuovi') then
    raise exception 'Azione sul referente non valida' using detail = 'azione_non_valida';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner
    and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  insert into public.company_partner_profiles (company_profile_id, family_parent_id)
  values (p_company, p_owner)
  on conflict (company_profile_id) do nothing;

  select * into v_prof from public.company_partner_profiles
  where company_profile_id = p_company
  for update;

  v_guc := current_setting('app.partner_consenso', true);

  if p_azione = 'proponi' then
    if p_attore is distinct from p_owner then
      raise exception 'Il referente lo sceglie il titolare dell''azienda'
        using detail = 'attore_non_titolare';
    end if;
    if p_user is null then
      raise exception 'Referente non indicato' using detail = 'referente_non_valido';
    end if;

    if p_user = p_owner then
      if v_prof.referente_user_id is not null or v_prof.referente_proposto_user_id is not null then
        v_revocato := v_prof.referente_user_id;
        v_motivo := 'torna_titolare';
        if v_revocato is null then
          v_audit := 'partner.referente_proposta_annullata';
          v_target := v_prof.referente_proposto_user_id;
        end if;
        perform set_config('app.partner_consenso', 'on', true);
        update public.company_partner_profiles
        set referente_user_id          = null,
            referente_proposto_user_id = null,
            referente_proposto_at      = null
        where company_profile_id = p_company
        returning * into v_prof;
        perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
      end if;
    else
      if not exists (
        select 1
        from public.family_members fm
        join public.family_member_company_access a on a.family_member_id = fm.id
        where fm.parent_id = p_owner and fm.member_id = p_user and fm.status = 'active'
          and a.company_profile_id = p_company
      ) then
        raise exception 'La persona scelta deve essere un membro attivo con accesso a questa azienda'
          using detail = 'referente_non_valido';
      end if;
      if p_user is distinct from v_prof.referente_user_id then
        perform set_config('app.partner_consenso', 'on', true);
        update public.company_partner_profiles
        set referente_proposto_user_id = p_user,
            referente_proposto_at      = now()
        where company_profile_id = p_company
        returning * into v_prof;
        perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
        v_audit := 'partner.referente_proposto';
        v_target := p_user;
      elsif v_prof.referente_proposto_user_id is not null then
        -- Il titolare riconferma il referente in carica: la proposta pendente
        -- verso un'altra persona non vale più.
        v_audit := 'partner.referente_proposta_annullata';
        v_target := v_prof.referente_proposto_user_id;
        perform set_config('app.partner_consenso', 'on', true);
        update public.company_partner_profiles
        set referente_proposto_user_id = null,
            referente_proposto_at      = null
        where company_profile_id = p_company
        returning * into v_prof;
        perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
      end if;
    end if;

  elsif p_azione = 'annulla_proposta' then
    if p_attore is distinct from p_owner then
      raise exception 'Il referente lo sceglie il titolare dell''azienda'
        using detail = 'attore_non_titolare';
    end if;
    if v_prof.referente_proposto_user_id is not null then
      v_audit := 'partner.referente_proposta_annullata';
      v_target := v_prof.referente_proposto_user_id;
      perform set_config('app.partner_consenso', 'on', true);
      update public.company_partner_profiles
      set referente_proposto_user_id = null,
          referente_proposto_at      = null
      where company_profile_id = p_company
      returning * into v_prof;
      perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
    end if;

  elsif p_azione in ('accetta', 'rifiuta') then
    if p_attore is null or p_attore is distinct from v_prof.referente_proposto_user_id then
      raise exception 'Nessuna proposta di referente da confermare'
        using detail = 'nessuna_proposta_referente';
    end if;

    if p_azione = 'accetta' then
      if p_versione is null or char_length(p_versione) not between 1 and 50 then
        raise exception 'Versione dell''informativa non valida'
          using detail = 'versione_non_valida';
      end if;
      if not exists (
        select 1
        from public.family_members fm
        join public.family_member_company_access a on a.family_member_id = fm.id
        where fm.parent_id = p_owner and fm.member_id = p_attore and fm.status = 'active'
          and a.company_profile_id = p_company
      ) then
        raise exception 'La persona scelta deve essere un membro attivo con accesso a questa azienda'
          using detail = 'referente_non_valido';
      end if;
      if v_prof.referente_user_id is not null and v_prof.referente_user_id <> p_attore then
        v_revocato := v_prof.referente_user_id;
        v_motivo := 'sostituito';
      end if;
      v_concesso := p_attore;
    else
      v_audit := 'partner.referente_rifiutato';
      v_target := p_attore;
    end if;

    perform set_config('app.partner_consenso', 'on', true);
    update public.company_partner_profiles
    set referente_user_id          = case when p_azione = 'accetta' then p_attore
                                          else referente_user_id end,
        referente_proposto_user_id = null,
        referente_proposto_at      = null
    where company_profile_id = p_company
    returning * into v_prof;
    perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);

  else
    -- revoca (il referente rinuncia) | rimuovi (il titolare)
    if p_azione = 'revoca'
       and (p_attore is null or p_attore is distinct from v_prof.referente_user_id) then
      raise exception 'Non sei il referente di questo profilo' using detail = 'azione_non_valida';
    end if;
    if p_azione = 'rimuovi' and p_attore is distinct from p_owner then
      raise exception 'Il referente lo sceglie il titolare dell''azienda'
        using detail = 'attore_non_titolare';
    end if;
    if v_prof.referente_user_id is not null then
      v_revocato := v_prof.referente_user_id;
      v_motivo := case p_azione when 'revoca' then 'rinuncia' else 'rimosso' end;
      perform set_config('app.partner_consenso', 'on', true);
      update public.company_partner_profiles
      set referente_user_id = null
      where company_profile_id = p_company
      returning * into v_prof;
      perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
    end if;
  end if;

  if v_revocato is not null then
    v_versione := coalesce(
      (select pc.informativa_versione from public.partner_consents pc
       where pc.company_profile_id = p_company and pc.azione = 'referente_concesso'
         and pc.referente_user_id = v_revocato
       order by pc.id desc limit 1),
      p_versione);
    if v_versione is null or char_length(v_versione) not between 1 and 50 then
      raise exception 'Versione dell''informativa non valida' using detail = 'versione_non_valida';
    end if;
    insert into public.partner_consents
      (company_profile_id, family_parent_id, azione, informativa_versione, origine,
       attore_user_id, referente_user_id, motivo)
    values
      (p_company, p_owner, 'referente_revocato', v_versione, 'pagina_azienda', p_attore,
       v_revocato, v_motivo);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, 'partner.referente_revocato', v_revocato, p_owner,
            jsonb_build_object('company_profile_id', p_company, 'motivo', v_motivo));
  end if;

  if v_concesso is not null then
    insert into public.partner_consents
      (company_profile_id, family_parent_id, azione, informativa_versione, origine,
       attore_user_id, referente_user_id)
    values
      (p_company, p_owner, 'referente_concesso', p_versione, 'pagina_azienda', p_attore,
       v_concesso);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, 'partner.referente_concesso', v_concesso, p_owner,
            jsonb_build_object('company_profile_id', p_company, 'versione', p_versione));
  end if;

  if v_audit is not null then
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, v_audit, v_target, p_owner,
            jsonb_build_object('company_profile_id', p_company));
  end if;

  return jsonb_build_object(
    'referente_user_id', v_prof.referente_user_id,
    'referente_proposto_user_id', v_prof.referente_proposto_user_id);
end;
$$;

comment on function public.fn_partner_referente(uuid, uuid, uuid, text, uuid, text) is
  'Referente del profilo partner (Q13): proponi (titolare; p_user = titolare → torna il titolare, altrimenti membro attivo con accesso all''azienda; il referente in carica → annulla la proposta pendente) | annulla_proposta (titolare: solo la proposta, il referente resta) | accetta (il proposto, con la versione dell''informativa del referente) | rifiuta (il proposto) | revoca (il referente) | rimuovi (il titolare). Registro referente_concesso / referente_revocato + audit. Ritorna {referente_user_id, referente_proposto_user_id}. Detail: azione_non_valida | company_not_found | attore_non_titolare | referente_non_valido | nessuna_proposta_referente | versione_non_valida.';

-- ----------------------------------------------------------------------------
-- 7) fn_partner_bozza_ai_esecuzione_interrotta — chiude un'esecuzione della
--    bozza AI rimasta in_corso senza più un job che la chiuda (riavvio,
--    crash, cancellazione a metà). La chiamata al modello può essere partita
--    e addebitata: costo ignoto (NULL: la riserva resta nel budget) e una riga
--    timeout_unknown in api_usage_events con la riserva come costo, come
--    fn_partenariato_esecuzione_scaduta (0034) in fase analisi. Chi chiude
--    l'esecuzione ne registra il consumo: una sola riga per esecuzione.
--    Solo da in_corso e solo per il servizio partner_profilo_ai, altrimenti
--    nessun effetto. Ritorna true se l'ha chiusa.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_ai_esecuzione_interrotta(
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
  where id = p_esecuzione_id and servizio = 'partner_profilo_ai' and stato = 'in_corso'
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
                        'esecuzione_id', v_exec.id, 'esito', 'interrotta',
                        'failsafe', true));
  perform public.fn_partenariati_ai_concludi(
    p_esecuzione_id, 'interrotta', null, 0, 0, null, 'interrotta');
  return true;
end;
$$;

comment on function public.fn_partner_bozza_ai_esecuzione_interrotta(uuid) is
  'Chiude un''esecuzione partner_profilo_ai ancora in_corso come interrotta a costo ignoto (la riserva resta nel budget) e scrive la sua riga timeout_unknown in api_usage_events con la riserva. Già chiusa, inesistente o di un altro servizio → nessun effetto (false).';

-- ----------------------------------------------------------------------------
-- 8) fn_partner_bozza_ai_prenota — prenotazione ATOMICA della bozza AI del
--    profilo (servizio partner_profilo_ai, origine utente, gruppo altri).
--    Guardia azienda e riga del profilo (creata se manca) bloccate, poi il
--    lock globale del budget (lo stesso di fn_partenariati_ai_prenota,
--    rientrante nella transazione):
--      - bozza in_corso avviata da meno di 10 minuti → bozza_in_corso; se più
--        vecchia è orfana: la sua esecuzione si chiude con
--        fn_partner_bozza_ai_esecuzione_interrotta e si prosegue;
--      - esecuzioni di oggi (Europe/Rome) del servizio per l'azienda, con la
--        stessa esclusione di fn_partenariati_ai_prenota (non contano, senza
--        LLM, riusata/nessun_segnale ed errore/interrotta a costo 0) ≥
--        p_limite_azienda (NULL = nessun limite) → ai_limite_azienda;
--      - fn_partenariati_ai_prenota (limite per richiedente — il titolare, su
--        tutte le sue aziende —, budget del gruppo altri) → ai_limite_utente |
--        ai_budget_esaurito | parametri_non_validi;
--      - bozza in_corso con la nuova esecuzione.
--    Ritorna l'id dell'esecuzione.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_ai_prenota(
  p_owner                 uuid,
  p_company               uuid,
  p_richiedente           uuid,
  p_budget_cents          integer,
  p_costo_riservato_cents integer,
  p_limite_azienda        integer,
  p_limite_richiedente    integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi constant date := (now() at time zone 'Europe/Rome')::date;
  v_prof public.company_partner_profiles%rowtype;
  v_n    bigint;
  v_id   uuid;
begin
  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner
    and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  insert into public.company_partner_profiles (company_profile_id, family_parent_id)
  values (p_company, p_owner)
  on conflict (company_profile_id) do nothing;

  select * into v_prof from public.company_partner_profiles
  where company_profile_id = p_company
  for update;

  -- Dopo i lock di riga, come fn_partenariato_prenota (0034).
  perform pg_advisory_xact_lock(hashtext('partenariati_ai_budget'));

  if v_prof.bozza_ai_stato = 'in_corso' then
    if v_prof.bozza_ai_avviata_at > now() - interval '10 minutes' then
      raise exception 'La bozza del profilo è già in preparazione'
        using detail = 'bozza_in_corso';
    end if;
    perform public.fn_partner_bozza_ai_esecuzione_interrotta(v_prof.bozza_ai_esecuzione_id);
  end if;

  if p_limite_azienda is not null then
    select count(*) into v_n
    from public.partenariati_ai_esecuzioni
    where company_profile_id = p_company
      and giorno = v_oggi
      and servizio = 'partner_profilo_ai'
      and not (not llm_eseguito
               and (stato in ('riusata', 'nessun_segnale')
                    or (stato in ('errore', 'interrotta')
                        and cost_cents is not distinct from 0)));
    if v_n >= greatest(p_limite_azienda, 0) then
      raise exception 'Hai raggiunto le bozze di oggi per questa azienda: riprova domani'
        using detail = 'ai_limite_azienda';
    end if;
  end if;

  v_id := public.fn_partenariati_ai_prenota(
    p_servizio              => 'partner_profilo_ai',
    p_origine               => 'utente',
    p_gruppo                => 'altri',
    p_budget_cents          => p_budget_cents,
    p_costo_riservato_cents => p_costo_riservato_cents,
    p_richiedente           => p_richiedente,
    p_limite_richiedente    => p_limite_richiedente,
    p_owner                 => p_owner,
    p_limite_owner          => null,
    p_company               => p_company,
    p_bando_id              => null);

  update public.company_partner_profiles
  set bozza_ai_stato         = 'in_corso',
      bozza_ai_avviata_at    = now(),
      bozza_ai_esecuzione_id = v_id,
      bozza_ai_errore        = null
  where company_profile_id = p_company;

  return v_id;
end;
$$;

comment on function public.fn_partner_bozza_ai_prenota(uuid, uuid, uuid, integer, integer, integer, integer) is
  'Prenota la bozza AI del profilo partner (azienda viva del titolare; riga profilo creata se manca): bozza in_corso < 10 minuti → bozza_in_corso (se più vecchia la sua esecuzione si chiude con fn_partner_bozza_ai_esecuzione_interrotta); limite giornaliero per azienda (Europe/Rome, stessa esclusione delle esecuzioni senza LLM di fn_partenariati_ai_prenota; NULL = nessun limite); poi fn_partenariati_ai_prenota(partner_profilo_ai, utente, altri) con il limite per richiedente. Imposta la bozza in_corso e ritorna l''id dell''esecuzione. Detail: company_not_found | bozza_in_corso | ai_limite_azienda | ai_limite_utente | ai_budget_esaurito | parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 9) fn_partner_bozza_ai_concludi — chiusura ATOMICA del job della bozza.
--    Nella stessa transazione:
--      - la bozza diventa pronta (con la proposta) o errore (con il codice),
--        SOLO se è ancora quella di questa esecuzione e in_corso (il
--        failsafe, uno scarto o una nuova bozza vincono);
--      - l'esecuzione si chiude con costo e token (fn_partenariati_ai_concludi),
--        SOLO se è ancora in_corso.
--    Così non resta mai un'esecuzione in_corso con la bozza già chiusa: se
--    la chiamata fallisce restano in corso entrambe e le chiude il failsafe.
--    Ritorna {bozza_scritta, esecuzione_chiusa}: il job registra il consumo
--    solo se ha chiuso lui l'esecuzione (chi chiude registra, una volta).
--    Detail: parametri_non_validi (e stato_non_valido dalla 0034).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_ai_concludi(
  p_company       uuid,
  p_esecuzione_id uuid,
  p_bozza_stato   text,
  p_bozza         jsonb,
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
  if p_company is null or p_esecuzione_id is null
     or p_bozza_stato is null or p_bozza_stato not in ('pronta', 'errore') then
    raise exception 'Parametri di chiusura della bozza non validi'
      using detail = 'parametri_non_validi';
  end if;

  update public.company_partner_profiles
  set bozza_ai_stato  = p_bozza_stato,
      bozza_ai        = case when p_bozza_stato = 'pronta' then p_bozza end,
      bozza_ai_errore = case when p_bozza_stato = 'errore' then p_bozza_errore end,
      bozza_ai_at     = now()
  where company_profile_id = p_company
    and bozza_ai_esecuzione_id = p_esecuzione_id
    and bozza_ai_stato = 'in_corso';
  v_scritta := found;

  perform 1 from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id and servizio = 'partner_profilo_ai'
    and company_profile_id = p_company and stato = 'in_corso'
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

comment on function public.fn_partner_bozza_ai_concludi(uuid, uuid, text, jsonb, text, text, integer, integer, integer, text, text) is
  'Chiusura atomica del job della bozza AI: bozza pronta | errore solo se è ancora in_corso con questa esecuzione, e nella stessa transazione esecuzione chiusa (fn_partenariati_ai_concludi) solo se ancora in_corso. Ritorna {bozza_scritta, esecuzione_chiusa}. Detail: parametri_non_validi | stato_non_valido.';

-- ----------------------------------------------------------------------------
-- 10) fn_partner_bozza_ai_chiudi_stale — failsafe (in lettura e nello
--    scheduler), soglia p_minuti (NULL = 10, minimo 1):
--      - le bozze in_corso avviate da almeno la soglia diventano errore con
--        bozza_ai_errore = 'interrotta' e la loro esecuzione si chiude con
--        fn_partner_bozza_ai_esecuzione_interrotta (costo ignoto + riga
--        timeout_unknown; nessun effetto se è già chiusa);
--      - le esecuzioni partner_profilo_ai ancora in_corso, avviate da almeno
--        la soglia e che nessuna bozza in_corso referenzia più (azienda
--        cancellata durante il job, chiusura rimasta a metà) si chiudono
--        allo stesso modo.
--    Salta le righe bloccate. Ritorna quante bozze ed esecuzioni ha chiuso.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_bozza_ai_chiudi_stale(p_minuti integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_soglia constant timestamptz :=
    now() - make_interval(mins => greatest(coalesce(p_minuti, 10), 1));
  v_row public.company_partner_profiles%rowtype;
  v_id  uuid;
  v_n   integer := 0;
begin
  for v_row in
    select * from public.company_partner_profiles
    where bozza_ai_stato = 'in_corso'
      and bozza_ai_avviata_at <= v_soglia
    order by bozza_ai_avviata_at
    for update skip locked
  loop
    update public.company_partner_profiles
    set bozza_ai_stato  = 'errore',
        bozza_ai_errore = 'interrotta'
    where company_profile_id = v_row.company_profile_id;

    perform public.fn_partner_bozza_ai_esecuzione_interrotta(v_row.bozza_ai_esecuzione_id);
    v_n := v_n + 1;
  end loop;

  for v_id in
    select e.id from public.partenariati_ai_esecuzioni e
    where e.servizio = 'partner_profilo_ai'
      and e.stato = 'in_corso'
      and e.avviata_at <= v_soglia
      and not exists (
        select 1 from public.company_partner_profiles p
        where p.bozza_ai_esecuzione_id = e.id and p.bozza_ai_stato = 'in_corso')
    order by e.avviata_at
    for update of e skip locked
  loop
    if public.fn_partner_bozza_ai_esecuzione_interrotta(v_id) then
      v_n := v_n + 1;
    end if;
  end loop;

  return v_n;
end;
$$;

comment on function public.fn_partner_bozza_ai_chiudi_stale(integer) is
  'Failsafe delle bozze AI del profilo (soglia p_minuti, NULL = 10, minimo 1): bozze in_corso oltre la soglia → errore (bozza_ai_errore interrotta) e loro esecuzione chiusa da fn_partner_bozza_ai_esecuzione_interrotta; esecuzioni partner_profilo_ai in_corso oltre la soglia che nessuna bozza in_corso referenzia → stessa chiusura. Salta le righe bloccate. Ritorna quante bozze ed esecuzioni ha chiuso.';

-- ----------------------------------------------------------------------------
-- 11) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova (trigger
--    compresi) eseguibile dai ruoli esposti (Supabase concede EXECUTE di
--    default a PUBLIC). I trigger scattano comunque: il privilegio EXECUTE
--    si verifica solo alla creazione del trigger.
-- ----------------------------------------------------------------------------
alter table public.company_partner_profiles enable row level security;
alter table public.partner_consents enable row level security;

revoke all on public.company_partner_profiles from anon, authenticated;
revoke all on public.partner_consents from anon, authenticated;

revoke execute on function public.fn_partner_consents_readonly()
  from public, anon, authenticated;
revoke execute on function public.fn_cpp_campi_protetti()
  from public, anon, authenticated;
revoke execute on function public.fn_cpp_revoca_su_cambio_azienda()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_consenso(uuid, uuid, uuid, text, text, text, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_referente(uuid, uuid, uuid, text, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_ai_esecuzione_interrotta(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_ai_prenota(uuid, uuid, uuid, integer, integer, integer, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_ai_concludi(uuid, uuid, text, jsonb, text, text, integer, integer, integer, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_bozza_ai_chiudi_stale(integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0035 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle).
-- 1) drop trigger trg_cpp_revoca_su_cambio_azienda on public.company_profiles;
-- 2) drop function public.fn_partner_bozza_ai_chiudi_stale(integer);
--    drop function public.fn_partner_bozza_ai_concludi(uuid, uuid, text, jsonb, text,
--      text, integer, integer, integer, text, text);
--    drop function public.fn_partner_bozza_ai_prenota(uuid, uuid, uuid, integer, integer,
--      integer, integer);
--    drop function public.fn_partner_bozza_ai_esecuzione_interrotta(uuid);
--    drop function public.fn_partner_referente(uuid, uuid, uuid, text, uuid, text);
--    drop function public.fn_partner_consenso(uuid, uuid, uuid, text, text, text, boolean,
--      boolean);
-- 3) drop table public.company_partner_profiles;   -- si perdono profili e opt-in
--      (nessun'altra tabella li referenzia in 0035);
--    drop table public.partner_consents;           -- si perde la PROVA dei consensi:
--      esportarla prima (conservazione dichiarata 5 anni dopo la revoca, Q22);
-- 4) drop function public.fn_cpp_revoca_su_cambio_azienda();
--    drop function public.fn_cpp_campi_protetti();
--    drop function public.fn_partner_consents_readonly();
-- Le esecuzioni partner_profilo_ai in partenariati_ai_esecuzioni (0034) e le
-- righe di api_usage_events restano: sono il registro della spesa.
-- ============================================================================
