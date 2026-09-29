-- ============================================================================
-- BandoFit — DB primario, migration 0041: CONSULTO DALLA CALL, MODERAZIONE
-- DSA, ADMIN E METRICHE dei partenariati e VERIFICA DELL'IDENTITÀ DA PARTE
-- DELL'ADMIN (WP9, piano docs/partenariati.md §2.1 T3-T5, §2.9 W1-W3, §3
-- riga 0041, §13 Q9/Q19/Q22/Q23; decisione di Michele del 2026-09-29).
--
--   1) consulto dalla call (W1, Q19): consultation_requests.partner_call_id
--      (FK NO ACTION: una call con un consulto non si cancella da sola; la
--      cascade dell'azienda, nello stesso statement, passa) e l'indice
--      «una richiesta aperta» diviso in due (AI-check per owner × bando, da
--      call per call): i due consulti coesistono sullo stesso bando;
--      fn_create_consultation_request ridefinita con la STESSA firma: salva
--      partner_call_id e verifica che la call sia dell'azienda richiedente e
--      del suo owner (call_non_trovata);
--   2) partner_segnalazioni: colonne di decisione e ricorso (W2, DSA art.
--      16-20) con lo statement of reasons generato dal backend e l'azienda
--      autrice ricavata dall'oggetto;
--   3) verifica dell'identità da parte dell'admin (Q9 rivista): registro
--      APPEND-ONLY company_identita_verifiche (senza FK, come
--      partner_consents) e stato corrente company_identita_stato (1:1,
--      cascade) modificabile SOLO dalle RPC di questo file (trigger con la GUC
--      app.identita_azienda); revoca automatica (origine sistema) quando
--      l'azienda viene eliminata, archiviata o cambia P.IVA o ragione sociale;
--   4) funzioni interne: verifica dell'admin, azienda autrice di un oggetto
--      segnalato, applicazione e annullamento dell'effetto di moderazione,
--      lock per oggetto e inizio della restrizione in corso;
--      fn_partenariato_identita_forte (stato verificata + T5 ancora valido);
--   5) ridefinizioni con la STESSA firma (una sola funzione per nome):
--      - fn_partenariato_rappresentante_ok (0037) = identità forte +
--        azienda dell'owner (la verifica del CF resta solo informativa);
--      - fn_partner_consenso (0035): la verifica inline del rappresentante
--        diventa fn_partenariato_rappresentante_ok (unica modifica);
--      - fn_partner_decidi (0040): rivelazione SIMMETRICA, l'audit di
--        rivelazione si scrive solo se p_rivela E entrambe le aziende hanno
--        l'identità forte (unica modifica);
--   6) RPC dell'identità: fn_identita_richiedi (titolare), fn_identita_decidi
--      e fn_identita_revoca (admin);
--   7) RPC di moderazione (admin salvo il ricorso): presa in carico,
--      decisione motivata con effetto ATOMICO, ricorso (uno, entro 6 mesi),
--      decisione del ricorso, sospensione e ripristino diretti;
--   8) metriche e costi del modulo per l'admin (stable, aggregati in SQL: il
--      max-rows di PostgREST non li tronca), costi per provider e VALUTA
--      (EUR openapi, USD Anthropic: mai sommati tra loro);
--   9) call da rivalidare (passo ricalcolo_validazioni dello scheduler, WP8
--      P13a).
--
-- L'uscita di un membro dal consorzio di una call sospesa (WP8 P14) NON
-- richiede ridefinizioni: il ramo «membro» di fn_partner_membro_esci (0040)
-- non guarda lo stato della call (lo fissa test_migration_0041); il blocco
-- era solo nell'accesso del backend.
--
-- Chi agisce: la moderazione e le decisioni sull'identità le prende un admin
-- (profiles.role = admin e attivo, verificato anche qui: admin_non_autorizzato);
-- il ricorso lo presenta il titolare dell'azienda autrice o il segnalante; la
-- richiesta di verifica il titolare (T4). La moderazione tocca stati di
-- altri moduli (call, profilo, messaggio) SOLO tramite queste RPC.
--
-- ORDINE DEI LOCK (quello globale della 0037-0040, esteso):
--   segnalazione (FOR UPDATE, solo le RPC di moderazione) → oggetto moderato
--   (lock advisory fn_partner_moderazione_blocca, preso solo dalle RPC di
--   moderazione: serializza decisioni, ricorsi e azioni dirette sullo stesso
--   contenuto) → owner dell'oggetto (profiles FOR UPDATE) → azienda
--   (company_profiles FOR NO KEY UPDATE) → oggetto (call o profilo partner FOR
--   UPDATE). Il messaggio si blocca da solo (nessun'altra RPC blocca righe di
--   partner_messaggi).
--   Identità: azienda (FOR NO KEY UPDATE) → stato dell'identità (FOR UPDATE),
--   lo stesso ordine del trigger di revoca (che scatta con l'azienda già
--   bloccata dall'UPDATE).
--
-- COMPATIBILITÀ con il backend attuale (la migration va applicata PRIMA del
-- deploy del WP9): la sola modifica a un oggetto esistente usato dal backend
-- attuale è l'indice consultation_requests_one_open, ricreato con lo stesso
-- nome e la stessa semantica per i consulti AI-check (partner_call_id NULL:
-- tutte le righe create dal backend attuale); fn_create_consultation_request
-- ha la stessa firma, senza partner_call_id nel payload si comporta come la
-- 0028 e il detail request_gia_aperta non cambia. Le altre ridefinizioni
-- cambiano solo il nominativo e la rivelazione, che il backend attuale tiene
-- spenti (NOMINATIVO_DISPONIBILE, RIVELAZIONE_IDENTITA_DISPONIBILE = False).
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Rollback
-- documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Consulto dalla call (W1, Q19).
-- ----------------------------------------------------------------------------
alter table public.consultation_requests
  add column partner_call_id uuid
    constraint consultation_requests_partner_call_id_fkey
    references public.partner_calls (id);

comment on column public.consultation_requests.partner_call_id is
  'Consulto chiesto dalla call di partenariato (WP9, W1): NULL = consulto AI-check. FK NO ACTION: la call non si cancella con un consulto (la cascade dell''azienda, nello stesso statement, passa). La call è dell''azienda della richiesta e del suo owner (fn_create_consultation_request). Il progettista assegnato vede solo la proiezione dedicata del backend (CallVistaProgettista).';

-- FK (cancellazione della call) e letture per call.
create index consultation_requests_call_idx on public.consultation_requests (partner_call_id)
  where partner_call_id is not null;

-- «Una richiesta aperta» diviso in due: i consulti AI-check restano uno per
-- owner × bando (stesso nome e stessa semantica della 0017 per le righe senza
-- call), quelli da call uno per call. Un consulto AI-check e uno da call
-- coesistono sullo stesso bando.
drop index public.consultation_requests_one_open;
create unique index consultation_requests_one_open
  on public.consultation_requests (family_parent_id, bando_id)
  where stato = 'nuova' and partner_call_id is null;
create unique index consultation_requests_one_open_call
  on public.consultation_requests (partner_call_id)
  where stato = 'nuova' and partner_call_id is not null;

-- fn_create_consultation_request — STESSA firma, corpo della 0028 con le sole
-- aggiunte marcate «0041»: partner_call_id dal payload (assente o vuoto =
-- consulto AI-check, come prima) e, se c'è, la call deve essere dell'azienda
-- richiedente e del suo owner (FOR KEY SHARE: la stessa riga che l'FK
-- bloccherebbe, così verifica e insert vedono la stessa call).
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
  v_call  uuid;  -- 0041
begin
  select * into v_addon from public.addons
  where id = (p_payload ->> 'addon_id')::bigint and is_active;
  if not found then
    raise exception 'Addon non disponibile' using detail = 'addon_not_available';
  end if;

  -- 0041: consulto dalla call (W1).
  begin
    v_call := nullif(p_payload ->> 'partner_call_id', '')::uuid;
  exception when invalid_text_representation then
    raise exception 'Call di partenariato non trovata' using detail = 'call_non_trovata';
  end;
  if v_call is not null then
    perform 1 from public.partner_calls
    where id = v_call
      and company_profile_id = (p_payload ->> 'company_profile_id')::uuid
      and family_parent_id = (p_payload ->> 'family_parent_id')::uuid
    for key share;
    if not found then
      raise exception 'Call di partenariato non trovata' using detail = 'call_non_trovata';
    end if;
  end if;

  -- Gating pilotato dal catalogo: consumabile a pagamento → serve 1 unità.
  v_gate := (v_addon.tipo_fruizione = 'consumabile'
             and v_addon.tipo_prezzo = 'importo' and v_addon.prezzo > 0);

  begin
    insert into public.consultation_requests
      (cliente_id, family_parent_id, company_profile_id, ai_check_id, esito,
       punteggio, bando_id, bando_slug, bando_titolo, addon_id, addon_slug, addon_prezzo,
       partner_call_id)
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
       v_addon.id, v_addon.slug, v_addon.prezzo,
       v_call)
    returning * into v_req;
  exception when unique_violation then
    -- 23505 dell'indice consultation_requests_one_open: dentro una RPC
    -- arriverebbe come 502, lo si traduce in detail-code → 409.
    -- 0041: anche consultation_requests_one_open_call (un consulto aperto
    -- per call), stesso detail.
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

comment on function public.fn_create_consultation_request(jsonb) is
  'Richiesta di consulto + consumo di 1 unità dell''addon (consumabile a pagamento) nella stessa transazione (0028). Dalla 0041 il payload può avere partner_call_id (consulto dalla call, W1): la call deve essere dell''azienda (company_profile_id) e dell''owner (family_parent_id) del payload, altrimenti call_non_trovata. Un consulto aperto per owner × bando senza call e uno per call (request_gia_aperta). Ritorna {request, consumato, quantita_residua}. Detail: addon_not_available | call_non_trovata | request_gia_aperta | addon_credit_esaurito.';

-- ----------------------------------------------------------------------------
-- 2) Segnalazioni DSA: decisione e ricorso (W2). Stati (già nella 0037):
--    ricevuta → in_esame → decisa → ricorso_presentato → ricorso_deciso.
--    Le colonne si scrivono SOLO con le RPC di moderazione di questo file.
-- ----------------------------------------------------------------------------
alter table public.partner_segnalazioni
  add column autore_company_profile_id uuid,
  add column decisione                 text,
  add column motivazione               text,
  add column sor_testo                 text,
  add column deciso_da                 uuid,
  add column deciso_at                 timestamptz,
  add column ricorso_testo             text,
  add column ricorso_da_user_id        uuid,
  add column ricorso_at                timestamptz,
  add column ricorso_esito             text,
  add column ricorso_motivazione       text,
  add column ricorso_deciso_da         uuid,
  add column ricorso_deciso_at         timestamptz,
  add column effetto_revocato_at       timestamptz,
  add constraint ps_decisione_check
    check (decisione is null
           or decisione in ('nessuna_azione', 'contenuto_rimosso', 'call_sospesa',
                            'profilo_sospeso')),
  add constraint ps_motivazione_check
    check (motivazione is null or char_length(motivazione) between 20 and 2000),
  add constraint ps_sor_testo_check
    check (sor_testo is null or char_length(sor_testo) between 1 and 10000),
  add constraint ps_ricorso_testo_check
    check (ricorso_testo is null or char_length(ricorso_testo) between 20 and 2000),
  add constraint ps_ricorso_esito_check
    check (ricorso_esito is null or ricorso_esito in ('confermata', 'riformata')),
  add constraint ps_ricorso_motivazione_check
    check (ricorso_motivazione is null or char_length(ricorso_motivazione) between 20 and 2000),
  -- Deciso ⇔ decisione, motivazione, chi e quando; una restrizione ha sempre
  -- il suo statement of reasons.
  add constraint ps_decisione_coerente
    check ((stato in ('decisa', 'ricorso_presentato', 'ricorso_deciso'))
           = (decisione is not null and motivazione is not null
              and deciso_da is not null and deciso_at is not null)),
  add constraint ps_sor_coerente
    check (decisione is null or decisione = 'nessuna_azione' or sor_testo is not null),
  add constraint ps_ricorso_coerente
    check ((stato in ('ricorso_presentato', 'ricorso_deciso'))
           = (ricorso_testo is not null and ricorso_da_user_id is not null
              and ricorso_at is not null)),
  add constraint ps_ricorso_deciso_coerente
    check ((stato = 'ricorso_deciso')
           = (ricorso_esito is not null and ricorso_motivazione is not null
              and ricorso_deciso_da is not null and ricorso_deciso_at is not null)),
  add constraint ps_effetto_revocato_coerente
    check (effetto_revocato_at is null
           or stato in ('decisa', 'ricorso_presentato', 'ricorso_deciso'));

comment on column public.partner_segnalazioni.autore_company_profile_id is
  'Azienda autrice del contenuto segnalato (creatrice della call, titolare del profilo, mittente del messaggio): dal backend alla creazione o ricavata dall''oggetto alla presa in carico o alla decisione. Senza FK. MAI verso il segnalante.';
comment on column public.partner_segnalazioni.decisione is
  'nessuna_azione | contenuto_rimosso (messaggio oscurato) | call_sospesa | profilo_sospeso, coerente con oggetto_tipo (fn_partner_segnalazione_decidi, effetto nella stessa transazione).';
comment on column public.partner_segnalazioni.motivazione is
  'Motivazione della decisione scritta dall''admin (20..2000).';
comment on column public.partner_segnalazioni.sor_testo is
  'Statement of reasons (DSA art. 17) generato dal backend dal template versionato e inviato all''autore: obbligatorio per le restrizioni, facoltativo con nessuna_azione. Mai l''identità del segnalante.';
comment on column public.partner_segnalazioni.ricorso_testo is
  'Ricorso interno (DSA art. 20): uno solo, entro 6 mesi dalla decisione, dell''autore contro una restrizione o del segnalante contro nessuna_azione.';
comment on column public.partner_segnalazioni.ricorso_esito is
  'confermata | riformata (riformata: restrizione annullata, o applicata se la decisione era nessuna_azione).';
comment on column public.partner_segnalazioni.effetto_revocato_at is
  'Ripristino diretto dell''admin (fn_partner_admin_ripristina) che ha tolto la restrizione di questa decisione ancora valida: da lì la decisione non regge più la restrizione dell''oggetto (fn_partner_ricorso_decidi non la conta). La decisione e il suo ricorso restano.';

-- ----------------------------------------------------------------------------
-- 3) Identità verificata dall'admin (decisione di Michele, Q9 rivista).
-- ----------------------------------------------------------------------------

-- 3a) Registro APPEND-ONLY, SENZA FK (prova delle verifiche: sopravvive alle
--     cancellazioni). metodo solo per verificata; origine sistema = revoca
--     automatica senza attore.
create table public.company_identita_verifiche (
  id                 bigint generated always as identity primary key,
  company_profile_id uuid not null,
  family_parent_id   uuid not null,
  azione             text not null
    constraint civ_azione_check
    check (azione in ('richiesta', 'verificata', 'rifiutata', 'revocata')),
  metodo             text
    constraint civ_metodo_check
    check (metodo is null
           or metodo in ('telefonata_sede', 'documento_legale_rappresentante', 'pec', 'altro')),
  nota               text
    constraint civ_nota_check check (nota is null or char_length(nota) <= 500),
  attore_user_id     uuid,
  origine            text not null
    constraint civ_origine_check check (origine in ('utente', 'admin', 'sistema')),
  motivo             text
    constraint civ_motivo_check check (motivo is null or char_length(motivo) <= 500),
  created_at         timestamptz not null default now(),
  constraint civ_metodo_solo_verificata check ((azione = 'verificata') = (metodo is not null)),
  constraint civ_attore_coerente check ((origine = 'sistema') = (attore_user_id is null)),
  constraint civ_origine_coerente
    check ((azione = 'richiesta' and origine = 'utente')
           or (azione in ('verificata', 'rifiutata') and origine = 'admin')
           or (azione = 'revocata' and origine in ('admin', 'sistema')))
);

comment on table public.company_identita_verifiche is
  'Registro APPEND-ONLY delle verifiche dell''identità delle aziende da parte dell''admin (WP9, Q9 rivista): richiesta (titolare), verificata | rifiutata (admin), revocata (admin o sistema). Senza FK: sopravvive alle cancellazioni. Scritto SOLO dalle RPC fn_identita_* e dal trigger di revoca automatica.';
comment on column public.company_identita_verifiche.nota is
  'Nota del titolare (come preferisce essere contattato) o dell''admin, ≤ 500: MAI dati personali oltre il necessario.';
comment on column public.company_identita_verifiche.motivo is
  'Revoche: motivo dell''admin oppure, per origine sistema, azienda_eliminata | azienda_archiviata | piva_cambiata | ragione_sociale_cambiata.';

create index company_identita_verifiche_company_idx on public.company_identita_verifiche
  (company_profile_id, created_at desc);

create or replace function public.fn_identita_verifiche_readonly()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception 'company_identita_verifiche è append-only' using detail = 'registro_append_only';
end;
$$;

comment on function public.fn_identita_verifiche_readonly() is
  'Trigger: rifiuta UPDATE, DELETE e TRUNCATE su company_identita_verifiche (registro append-only). Detail: registro_append_only.';

create trigger trg_identita_verifiche_readonly
  before update or delete on public.company_identita_verifiche
  for each row execute function public.fn_identita_verifiche_readonly();

create trigger trg_identita_verifiche_no_truncate
  before truncate on public.company_identita_verifiche
  for each statement execute function public.fn_identita_verifiche_readonly();

-- 3b) Stato corrente (1:1 con l'azienda, cascade). Nessuna riga =
--     non_richiesta. verificata ⇔ metodo, verificata_at e verificata_da.
create table public.company_identita_stato (
  company_profile_id uuid primary key
                       references public.company_profiles (id) on delete cascade,
  stato              text not null default 'non_richiesta'
    constraint cis_stato_check
    check (stato in ('non_richiesta', 'richiesta', 'verificata', 'rifiutata')),
  metodo             text
    constraint cis_metodo_check
    check (metodo is null
           or metodo in ('telefonata_sede', 'documento_legale_rappresentante', 'pec', 'altro')),
  verificata_at      timestamptz,
  verificata_da      uuid,
  richiesta_at       timestamptz,
  aggiornato_at      timestamptz not null default now(),
  constraint cis_verificata_coerente
    check ((stato = 'verificata')
           = (metodo is not null and verificata_at is not null and verificata_da is not null)),
  constraint cis_richiesta_coerente check (stato <> 'richiesta' or richiesta_at is not null)
);

comment on table public.company_identita_stato is
  'Stato corrente della verifica dell''identità dell''azienda da parte dell''admin (WP9): sblocca profilo nominativo, call nominative e rivelazione simmetrica (fn_partenariato_identita_forte). Modificabile SOLO dalle RPC fn_identita_* e dal trigger di revoca automatica (trigger trg_identita_stato_protetto con la GUC app.identita_azienda). Cascade dall''azienda; il registro resta.';
comment on column public.company_identita_stato.verificata_da is
  'Admin che ha verificato. Senza FK: MAI verso i clienti.';
comment on column public.company_identita_stato.richiesta_at is
  'Ultima richiesta del titolare (resta come storico dopo la decisione).';

-- Coda dell'admin (richieste in attesa, dalla più vecchia).
create index company_identita_stato_richieste_idx on public.company_identita_stato
  (richiesta_at) where stato = 'richiesta';

create or replace function public.fn_identita_stato_protetto()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if coalesce(current_setting('app.identita_azienda', true), '') = 'on' then
    return new;
  end if;
  raise exception 'La verifica dell''identità cambia solo con le funzioni dedicate'
    using detail = 'campo_protetto';
end;
$$;

comment on function public.fn_identita_stato_protetto() is
  'Trigger BEFORE INSERT OR UPDATE su company_identita_stato: senza la GUC app.identita_azienda = on (accesa SOLO dalle RPC fn_identita_* e dal trigger di revoca, locale alla transazione e ripristinata subito dopo la scrittura) nessuna scrittura. Nessun trigger su DELETE: la cascade dell''azienda passa. Detail: campo_protetto.';

create trigger trg_identita_stato_protetto
  before insert or update on public.company_identita_stato
  for each row execute function public.fn_identita_stato_protetto();

-- 3c) Revoca AUTOMATICA (origine sistema) di un'identità verificata quando
--     l'azienda viene soft-deleted o archiviata (da null a valorizzato) o
--     cambia P.IVA o ragione sociale, come trg_cpp_revoca_su_cambio_azienda
--     (0035): stato → non_richiesta, registro revocata senza attore, audit.
--     Nessun effetto se non era verificata (una richiesta in attesa resta:
--     l'admin decide sui dati attuali e fn_identita_decidi richiede T5).
create or replace function public.fn_identita_revoca_su_cambio_azienda()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivo   text;
  v_guc      text;
  v_revocata boolean;
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

  v_guc := current_setting('app.identita_azienda', true);
  perform set_config('app.identita_azienda', 'on', true);
  update public.company_identita_stato
  set stato         = 'non_richiesta',
      metodo        = null,
      verificata_at = null,
      verificata_da = null,
      aggiornato_at = now()
  where company_profile_id = new.id and stato = 'verificata';
  v_revocata := found;
  perform set_config('app.identita_azienda', coalesce(v_guc, ''), true);

  if v_revocata then
    insert into public.company_identita_verifiche
      (company_profile_id, family_parent_id, azione, origine, attore_user_id, motivo)
    values
      (new.id, new.parent_id, 'revocata', 'sistema', null, v_motivo);

    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (null, 'identita.revocata', new.parent_id, new.parent_id,
            jsonb_build_object('company_profile_id', new.id, 'origine', 'sistema',
                               'motivo', v_motivo));
  end if;
  return null;
end;
$$;

comment on function public.fn_identita_revoca_su_cambio_azienda() is
  'Trigger AFTER UPDATE su company_profiles: identità verificata + azienda soft-deleted | archiviata | P.IVA cambiata | ragione sociale cambiata → stato non_richiesta, registro revocata con origine sistema (attore NULL) e motivo azienda_eliminata | azienda_archiviata | piva_cambiata | ragione_sociale_cambiata, audit identita.revocata.';

create trigger trg_identita_revoca_su_cambio_azienda
  after update of deleted_at, archived_at, partita_iva, ragione_sociale
  on public.company_profiles
  for each row
  when ((old.deleted_at is null and new.deleted_at is not null)
        or (old.archived_at is null and new.archived_at is not null)
        or old.partita_iva is distinct from new.partita_iva
        or old.ragione_sociale is distinct from new.ragione_sociale)
  execute function public.fn_identita_revoca_su_cambio_azienda();

-- ----------------------------------------------------------------------------
-- 4) Funzioni interne.
-- ----------------------------------------------------------------------------

-- Chi decide è un admin attivo (difesa in profondità: il backend passa
-- l'AdminUser). Nessun lock sulla riga dell'admin.
create or replace function public.fn_partner_admin_verifica(p_admin uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform 1 from public.profiles where id = p_admin and role = 'admin' and is_active;
  if not found then
    raise exception 'Operazione riservata agli amministratori'
      using detail = 'admin_non_autorizzato';
  end if;
end;
$$;

comment on function public.fn_partner_admin_verifica(uuid) is
  'Interna: p_admin è un profilo admin attivo, altrimenti admin_non_autorizzato. Nessun lock.';

-- Azienda autrice dell'oggetto di una segnalazione (riferimento testuale come
-- in partner_segnalazioni.oggetto_id: id della call, codice_pubblico del
-- profilo, id del messaggio). Riferimento malformato o oggetto sparito → NULL.
create or replace function public.fn_partner_moderazione_autore(
  p_oggetto_tipo text,
  p_oggetto_id   text
)
returns uuid
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_autore uuid;
begin
  if p_oggetto_tipo = 'call' and p_oggetto_id ~* v_uuid then
    select company_profile_id into v_autore from public.partner_calls
    where id = p_oggetto_id::uuid;
  elsif p_oggetto_tipo = 'profilo' and p_oggetto_id ~* v_uuid then
    select company_profile_id into v_autore from public.company_partner_profiles
    where codice_pubblico = p_oggetto_id::uuid;
  elsif p_oggetto_tipo = 'messaggio' and p_oggetto_id ~ '^[0-9]{1,18}$' then
    select mittente_company_profile_id into v_autore from public.partner_messaggi
    where id = p_oggetto_id::bigint;
  end if;
  return v_autore;
end;
$$;

comment on function public.fn_partner_moderazione_autore(text, text) is
  'Interna: azienda autrice dell''oggetto segnalato (call → creatrice, profilo per codice_pubblico → azienda del profilo, messaggio → mittente); riferimento malformato o oggetto sparito → NULL.';

-- Applica la restrizione all'oggetto: call → sospesa_moderazione (solo da
-- bozza o pubblicata, stato precedente salvato), profilo → sospeso, messaggio
-- → oscurato. Lock: owner → azienda → call/profilo FOR UPDATE; il messaggio
-- da solo. Non solleva per l'esito: ritorna {esito: applicato | gia_applicato
-- | oggetto_non_trovato | non_sospendibile, stato_precedente?, stato?,
-- autore_company_id, autore_owner_id}. Il motivo salvato sull'oggetto è la
-- motivazione troncata a 500 caratteri.
create or replace function public.fn_partner_moderazione_applica(
  p_oggetto_tipo text,
  p_oggetto_id   text,
  p_admin        uuid,
  p_motivazione  text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uuid    constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_motivo  text := left(btrim(p_motivazione), 500);
  v_owner   uuid;
  v_company uuid;
  v_call    public.partner_calls%rowtype;
  v_prof    public.company_partner_profiles%rowtype;
  v_msg     public.partner_messaggi%rowtype;
  v_guc     text;
begin
  if p_oggetto_tipo = 'call' then
    if p_oggetto_id !~* v_uuid then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select family_parent_id, company_profile_id into v_owner, v_company
    from public.partner_calls where id = p_oggetto_id::uuid;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    perform 1 from public.profiles where id = v_owner for update;
    perform 1 from public.company_profiles where id = v_company for no key update;
    select * into v_call from public.partner_calls where id = p_oggetto_id::uuid for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    if v_call.stato = 'sospesa_moderazione' then
      return jsonb_build_object('esito', 'gia_applicato', 'stato', v_call.stato,
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    if v_call.stato not in ('bozza', 'pubblicata') then
      return jsonb_build_object('esito', 'non_sospendibile', 'stato', v_call.stato,
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    update public.partner_calls
    set stato                   = 'sospesa_moderazione',
        stato_prima_sospensione = v_call.stato,
        sospesa_at              = now(),
        sospeso_motivo          = v_motivo,
        sospeso_da              = p_admin
    where id = v_call.id;
    return jsonb_build_object('esito', 'applicato', 'stato_precedente', v_call.stato,
                              'stato', 'sospesa_moderazione',
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);

  elsif p_oggetto_tipo = 'profilo' then
    if p_oggetto_id !~* v_uuid then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select company_profile_id into v_company from public.company_partner_profiles
    where codice_pubblico = p_oggetto_id::uuid;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    perform 1 from public.profiles where id = v_owner for update;
    perform 1 from public.company_profiles where id = v_company for no key update;
    select * into v_prof from public.company_partner_profiles
    where company_profile_id = v_company
    for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    if v_prof.sospeso_at is not null then
      return jsonb_build_object('esito', 'gia_applicato',
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    v_guc := current_setting('app.partner_consenso', true);
    perform set_config('app.partner_consenso', 'on', true);
    update public.company_partner_profiles
    set sospeso_at     = now(),
        sospeso_motivo = v_motivo,
        sospeso_da     = p_admin
    where company_profile_id = v_company;
    perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
    return jsonb_build_object('esito', 'applicato',
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);

  elsif p_oggetto_tipo = 'messaggio' then
    if p_oggetto_id !~ '^[0-9]{1,18}$' then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select * into v_msg from public.partner_messaggi where id = p_oggetto_id::bigint for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    v_company := v_msg.mittente_company_profile_id;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    if v_msg.nascosto_moderazione_at is not null then
      return jsonb_build_object('esito', 'gia_applicato',
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    update public.partner_messaggi
    set nascosto_moderazione_at = now(),
        nascosto_da             = p_admin
    where id = v_msg.id;
    return jsonb_build_object('esito', 'applicato',
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);
  end if;

  raise exception 'Tipo di contenuto non valido' using detail = 'parametri_non_validi';
end;
$$;

comment on function public.fn_partner_moderazione_applica(text, text, uuid, text) is
  'Interna: restrizione di moderazione sull''oggetto (call → sospesa_moderazione da bozza | pubblicata con stato_prima_sospensione, sospesa_at, sospeso_motivo (motivazione ≤ 500), sospeso_da; profilo → sospeso_* con la GUC app.partner_consenso; messaggio → nascosto_moderazione_at, nascosto_da). Lock owner → azienda → call/profilo FOR UPDATE, il messaggio da solo. Ritorna {esito: applicato | gia_applicato | oggetto_non_trovato | non_sospendibile, stato_precedente?, stato?, autore_company_id, autore_owner_id}. Detail: parametri_non_validi (tipo sconosciuto).';

-- Annulla la restrizione: call sospesa → stato precedente, oppure scaduta
-- (chiusa ora, motivo scadenza_call) se era pubblicata e nel frattempo è
-- passata scadenza_call (Europe/Rome); colonne di sospensione azzerate.
-- Profilo → sospeso_* NULL; messaggio → visibile. Stessi lock di
-- fn_partner_moderazione_applica. Ritorna {esito: annullato | non_sospeso |
-- oggetto_non_trovato, stato?, autore_company_id, autore_owner_id}.
create or replace function public.fn_partner_moderazione_annulla(
  p_oggetto_tipo text,
  p_oggetto_id   text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uuid    constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_oggi    constant date := (now() at time zone 'Europe/Rome')::date;
  v_owner   uuid;
  v_company uuid;
  v_call    public.partner_calls%rowtype;
  v_prof    public.company_partner_profiles%rowtype;
  v_msg     public.partner_messaggi%rowtype;
  v_stato   text;
  v_guc     text;
begin
  if p_oggetto_tipo = 'call' then
    if p_oggetto_id !~* v_uuid then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select family_parent_id, company_profile_id into v_owner, v_company
    from public.partner_calls where id = p_oggetto_id::uuid;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    perform 1 from public.profiles where id = v_owner for update;
    perform 1 from public.company_profiles where id = v_company for no key update;
    select * into v_call from public.partner_calls where id = p_oggetto_id::uuid for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    if v_call.stato <> 'sospesa_moderazione' then
      return jsonb_build_object('esito', 'non_sospeso', 'stato', v_call.stato,
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    v_stato := v_call.stato_prima_sospensione;
    if v_stato = 'pubblicata' and v_call.scadenza_call < v_oggi then
      v_stato := 'scaduta';
    end if;
    update public.partner_calls
    set stato                   = v_stato,
        chiusa_at               = case when v_stato = 'scaduta' then now() end,
        motivo_chiusura         = case when v_stato = 'scaduta' then 'scadenza_call' end,
        stato_prima_sospensione = null,
        sospesa_at              = null,
        sospeso_motivo          = null,
        sospeso_da              = null
    where id = v_call.id;
    return jsonb_build_object('esito', 'annullato', 'stato', v_stato,
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);

  elsif p_oggetto_tipo = 'profilo' then
    if p_oggetto_id !~* v_uuid then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select company_profile_id into v_company from public.company_partner_profiles
    where codice_pubblico = p_oggetto_id::uuid;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    perform 1 from public.profiles where id = v_owner for update;
    perform 1 from public.company_profiles where id = v_company for no key update;
    select * into v_prof from public.company_partner_profiles
    where company_profile_id = v_company
    for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    if v_prof.sospeso_at is null then
      return jsonb_build_object('esito', 'non_sospeso',
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    v_guc := current_setting('app.partner_consenso', true);
    perform set_config('app.partner_consenso', 'on', true);
    update public.company_partner_profiles
    set sospeso_at     = null,
        sospeso_motivo = null,
        sospeso_da     = null
    where company_profile_id = v_company;
    perform set_config('app.partner_consenso', coalesce(v_guc, ''), true);
    return jsonb_build_object('esito', 'annullato',
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);

  elsif p_oggetto_tipo = 'messaggio' then
    if p_oggetto_id !~ '^[0-9]{1,18}$' then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    select * into v_msg from public.partner_messaggi where id = p_oggetto_id::bigint for update;
    if not found then
      return jsonb_build_object('esito', 'oggetto_non_trovato');
    end if;
    v_company := v_msg.mittente_company_profile_id;
    select parent_id into v_owner from public.company_profiles where id = v_company;
    if v_msg.nascosto_moderazione_at is null then
      return jsonb_build_object('esito', 'non_sospeso',
                                'autore_company_id', v_company, 'autore_owner_id', v_owner);
    end if;
    update public.partner_messaggi
    set nascosto_moderazione_at = null,
        nascosto_da             = null
    where id = v_msg.id;
    return jsonb_build_object('esito', 'annullato',
                              'autore_company_id', v_company, 'autore_owner_id', v_owner);
  end if;

  raise exception 'Tipo di contenuto non valido' using detail = 'parametri_non_validi';
end;
$$;

comment on function public.fn_partner_moderazione_annulla(text, text) is
  'Interna: annulla la restrizione di moderazione (call sospesa → stato_prima_sospensione, o scaduta con chiusa_at = now() e motivo scadenza_call se era pubblicata e scadenza_call < oggi Europe/Rome; colonne di sospensione azzerate; profilo → sospeso_* NULL; messaggio → visibile). Stessi lock di fn_partner_moderazione_applica. Ritorna {esito: annullato | non_sospeso | oggetto_non_trovato, stato?, autore_company_id, autore_owner_id}. Detail: parametri_non_validi (tipo sconosciuto).';

-- Serializzazione PER OGGETTO delle decisioni di moderazione: lock advisory
-- sul riferimento canonico (uuid in minuscolo, id del messaggio senza zeri
-- iniziali), preso da decisione, decisione del ricorso, sospensione e
-- ripristino diretti DOPO l'eventuale lock della segnalazione e PRIMA dei lock
-- di riga (owner → azienda → oggetto). Così il controllo sulle altre
-- decisioni dello stesso oggetto e l'effetto vedono lo stato già committato
-- delle transazioni concorrenti. → il riferimento canonico.
create or replace function public.fn_partner_moderazione_blocca(
  p_oggetto_tipo text,
  p_oggetto_id   text
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_rif text := lower(btrim(coalesce(p_oggetto_id, '')));
begin
  if p_oggetto_tipo = 'messaggio' and v_rif ~ '^[0-9]{1,18}$' then
    v_rif := (v_rif::bigint)::text;
  end if;
  perform pg_advisory_xact_lock(hashtext('partner_moderazione'),
                                hashtext(coalesce(p_oggetto_tipo, '') || ':' || v_rif));
  return v_rif;
end;
$$;

comment on function public.fn_partner_moderazione_blocca(text, text) is
  'Interna: lock advisory di transazione sull''oggetto moderato (tipo + riferimento canonico), preso dalle RPC di decisione, ricorso, sospensione e ripristino dopo il lock della segnalazione e prima dei lock di riga. Ritorna il riferimento canonico (uuid minuscolo, id del messaggio senza zeri iniziali).';

-- Inizio della restrizione IN CORSO sull'oggetto (call sospesa: sospesa_at;
-- profilo: sospeso_at; messaggio: nascosto_moderazione_at); NULL se non è
-- ristretto o non esiste. Chi applica la restrizione scrive questo istante
-- con now(), nella stessa transazione della decisione che la causa: una
-- restrizione il cui inizio non coincide con nessuna decisione è d'ufficio.
create or replace function public.fn_partner_moderazione_inizio(
  p_oggetto_tipo text,
  p_oggetto_id   text
)
returns timestamptz
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_uuid   constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_inizio timestamptz;
begin
  if p_oggetto_tipo = 'call' and p_oggetto_id ~* v_uuid then
    select sospesa_at into v_inizio from public.partner_calls
    where id = p_oggetto_id::uuid and stato = 'sospesa_moderazione';
  elsif p_oggetto_tipo = 'profilo' and p_oggetto_id ~* v_uuid then
    select sospeso_at into v_inizio from public.company_partner_profiles
    where codice_pubblico = p_oggetto_id::uuid;
  elsif p_oggetto_tipo = 'messaggio' and p_oggetto_id ~ '^[0-9]{1,18}$' then
    select nascosto_moderazione_at into v_inizio from public.partner_messaggi
    where id = p_oggetto_id::bigint;
  end if;
  return v_inizio;
end;
$$;

comment on function public.fn_partner_moderazione_inizio(text, text) is
  'Interna: istante di inizio della restrizione in corso sull''oggetto (call sospesa_moderazione → sospesa_at, profilo → sospeso_at, messaggio → nascosto_moderazione_at); NULL se non ristretto, sparito o riferimento malformato.';

-- Identità FORTE (decisione di Michele): stato verificata dall'admin E T5
-- ancora valido (company_data con piva_fetched = partita_iva attuale e
-- impresa attiva). Il requisito «non sandbox» resta alle RPC che hanno
-- p_richiedi_non_sandbox e lo controllano con fn_partenariato_identita_ok
-- (consenso, pubblicazione, decisione): questa funzione non sa l'ambiente.
create or replace function public.fn_partenariato_identita_forte(p_company uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
      select 1 from public.company_identita_stato s
      where s.company_profile_id = p_company and s.stato = 'verificata')
    and public.fn_partenariato_identita_ok(p_company, false);
$$;

comment on function public.fn_partenariato_identita_forte(uuid) is
  'Identità dell''azienda verificata dall''admin (company_identita_stato = verificata) E ancora coerente col Registro Imprese (T5: fn_partenariato_identita_ok senza il controllo sandbox, che resta alle RPC con p_richiedi_non_sandbox). Sblocca profilo nominativo, call nominative e rivelazione simmetrica.';

-- ----------------------------------------------------------------------------
-- 5) Ridefinizioni con la STESSA firma.
-- ----------------------------------------------------------------------------

-- 5a) fn_partenariato_rappresentante_ok (0037): ora identità forte e azienda
--     dell'owner. La verifica del CF del titolare tra i legali rappresentanti
--     non è più una condizione (resta un dato informativo del backend).
create or replace function public.fn_partenariato_rappresentante_ok(
  p_owner   uuid,
  p_company uuid
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
      select 1 from public.company_profiles cp
      where cp.id = p_company and cp.parent_id = p_owner)
    and public.fn_partenariato_identita_forte(p_company);
$$;

comment on function public.fn_partenariato_rappresentante_ok(uuid, uuid) is
  'Dalla 0041 (decisione di Michele, Q9 rivista): l''azienda è del titolare e ha l''identità forte (fn_partenariato_identita_forte: verificata dall''admin + T5). Condizione per il profilo nominativo (fn_partner_consenso) e per la call nominativa (fn_partner_call_pubblica); il CF verificato tra i legali rappresentanti resta solo informativo.';

-- 5b) fn_partner_consenso (0035): corpo copiato, con la verifica inline del
--     legale rappresentante sostituita da fn_partenariato_rappresentante_ok
--     (unica modifica, marcata «0041»).
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
    -- 0041: il nominativo richiede l'identità forte (verificata dall'admin)
    -- dell'azienda del titolare; il CF resta solo informativo.
    if not p_anonimo
       and not public.fn_partenariato_rappresentante_ok(p_owner, p_company) then
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
  'Consenso del profilo partner (azienda viva del titolare, lock riga azienda e profilo): concedi (anonimato obbligatorio, versione 1..50, non sospeso, identità dal registro T5 — company_data con piva_fetched = partita_iva, stato attiva, non sandbox se richiesto — e, per il nominativo, fn_partenariato_rappresentante_ok: dalla 0041 identità verificata dall''admin) | revoca | anonimato (verso il nominativo di un profilo visibile: stesse verifiche). Ogni cambio scrive partner_consents e audit_log; stato già uguale → cambiato false senza scritture. Ritorna {visibile, anonimo, consenso_versione, consenso_at, cambiato}. Detail: azione_non_valida | origine_non_valida | attore_non_titolare | company_not_found | anonimato_obbligatorio | versione_non_valida | profilo_sospeso | identita_non_verificata | rappresentante_non_verificato.';

-- 5c) fn_partner_decidi (0040): corpo copiato; l'audit di rivelazione
--     (identita_rivelata + contatti_rivelati) solo se p_rivela E entrambe le
--     aziende hanno l'identità forte (rivelazione simmetrica, decisione di
--     Michele). Unica modifica, marcata «0041»; la risposta non cambia.
create or replace function public.fn_partner_decidi(
  p_candidatura          uuid,
  p_attore               uuid,
  p_owner                uuid,
  p_company              uuid,
  p_decisione            text,
  p_motivo               text,
  p_rivela               boolean,
  p_richiedi_non_sandbox boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_k          public.partner_candidature%rowtype;
  v_call       public.partner_calls%rowtype;
  v_prof       public.company_partner_profiles%rowtype;
  v_motivo     text;
  v_decide     uuid;
  v_controparte uuid;
  v_x_owner    uuid;
  v_y_owner    uuid;
  v_conv       uuid;
  v_payload    jsonb;
  v_pos_ruolo  text;
  v_pos_quota  numeric;
  v_membro     uuid;
begin
  if p_candidatura is null or p_decisione is null or p_decisione not in ('accetta', 'rifiuta')
  then
    raise exception 'Decisione non valida' using detail = 'parametri_non_validi';
  end if;
  if p_decisione = 'rifiuta' then
    v_motivo := nullif(btrim(p_motivo), '');
    if char_length(v_motivo) > 500 then
      raise exception 'Motivo del rifiuto troppo lungo' using detail = 'parametri_non_validi';
    end if;
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le candidature le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  -- Lettura senza lock: chi decide e su quale call (colonne immutabili).
  select * into v_k from public.partner_candidature where id = p_candidatura;
  if not found then
    raise exception 'Candidatura non trovata' using detail = 'candidatura_non_trovata';
  end if;
  v_decide := case v_k.tipo when 'candidatura' then v_k.creatore_company_profile_id
                            else v_k.company_profile_id end;
  v_controparte := case v_k.tipo when 'candidatura' then v_k.company_profile_id
                                 else v_k.creatore_company_profile_id end;
  if p_company is null or p_company is distinct from v_decide then
    raise exception 'Candidatura non trovata' using detail = 'candidatura_non_trovata';
  end if;

  perform public.fn_partner_cand_blocca_azienda(p_owner, p_company);

  if p_decisione = 'accetta' then
    if v_k.company_profile_id <> p_company then
      perform 1 from public.company_profiles where id = v_k.company_profile_id for key share;
    end if;
    select * into v_prof from public.company_partner_profiles
    where company_profile_id = v_k.company_profile_id
    for share;
  end if;

  select * into v_call from public.partner_calls where id = v_k.partner_call_id for share;

  select * into v_k from public.partner_candidature where id = p_candidatura for update;
  if not found then
    raise exception 'Candidatura non trovata' using detail = 'candidatura_non_trovata';
  end if;
  if v_k.stato <> 'inviata' then
    raise exception 'La candidatura è già stata decisa o chiusa'
      using detail = 'candidatura_gia_decisa';
  end if;
  if v_k.tipo = 'invito' and v_k.scade_at <= now() then
    raise exception 'L''invito è scaduto' using detail = 'invito_scaduto';
  end if;

  if p_decisione = 'rifiuta' then
    update public.partner_candidature
    set stato             = 'rifiutata',
        motivo_rifiuto    = v_motivo,
        decisa_da_user_id = p_attore,
        decisa_at         = now()
    where id = p_candidatura
    returning * into v_k;

    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, 'partenariato.candidatura_rifiutata',
            (select parent_id from public.company_profiles where id = v_controparte), p_owner,
            jsonb_build_object('candidatura_id', v_k.id, 'call_id', v_k.partner_call_id,
                               'tipo', v_k.tipo, 'company_profile_id', v_k.company_profile_id,
                               'creatore_company_profile_id',
                               v_k.creatore_company_profile_id));

    return jsonb_build_object('candidatura', to_jsonb(v_k), 'conversazione_id', null);
  end if;

  -- Accettazione.
  if v_call.id is null
     or not public.fn_partner_call_aperta(v_call.stato, v_call.scadenza_call,
                                          v_call.bando_scadenza, v_call.company_profile_id)
  then
    raise exception 'La call non è attiva' using detail = 'call_non_attiva';
  end if;
  if not public.fn_partenariato_identita_ok(p_company, p_richiedi_non_sandbox) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;
  if v_prof.company_profile_id is null or not v_prof.visibile_come_partner
     or v_prof.sospeso_at is not null then
    if v_k.company_profile_id = p_company then
      raise exception 'Per accettare l''invito attiva la visibilità come partner'
        using detail = 'profilo_partner_non_attivo';
    end if;
    raise exception 'L''altra azienda non è più disponibile'
      using detail = 'controparte_non_disponibile';
  end if;
  -- Controparte viva come per il backend (partenariato_indice.aziende_vive):
  -- non eliminata né archiviata, con il profilo dell'owner attivo.
  if not exists (select 1 from public.company_profiles cp
                 join public.profiles pr on pr.id = cp.parent_id and pr.is_active
                 where cp.id = v_controparte and cp.deleted_at is null
                   and cp.archived_at is null)
     or not public.fn_partenariato_identita_ok(v_controparte, p_richiedi_non_sandbox) then
    raise exception 'L''altra azienda non è più disponibile'
      using detail = 'controparte_non_disponibile';
  end if;
  select parent_id into v_x_owner from public.company_profiles
  where id = v_k.creatore_company_profile_id;
  select parent_id into v_y_owner from public.company_profiles
  where id = v_k.company_profile_id;
  if v_x_owner = v_y_owner then
    raise exception 'Le due aziende sono dello stesso gruppo' using detail = 'stesso_gruppo';
  end if;

  -- Esclusività: per ultimo (dopo i lock di riga), serializzata per Y × bando.
  perform pg_advisory_xact_lock(hashtext('partner_esclusivita'),
                                hashtext(v_k.company_profile_id::text || ':'
                                         || v_call.bando_id::text));
  if public.fn_partner_esclusivita_violata(v_k.company_profile_id, v_call.id, v_call.bando_id,
                                           v_call.esclusivita) then
    raise exception 'Il bando ammette un solo partenariato per azienda'
      using detail = 'esclusivita_violata';
  end if;

  insert into public.partner_conversazioni
    (partner_call_id, candidatura_id, company_creatore_id, company_partner_id)
  values
    (v_k.partner_call_id, v_k.id, v_k.creatore_company_profile_id, v_k.company_profile_id)
  returning id into v_conv;

  update public.partner_candidature
  set stato             = 'accettata',
      decisa_da_user_id = p_attore,
      decisa_at         = now(),
      conversazione_id  = v_conv
  where id = p_candidatura
  returning * into v_k;

  v_payload := jsonb_build_object(
    'candidatura_id', v_k.id, 'call_id', v_k.partner_call_id, 'tipo', v_k.tipo,
    'conversazione_id', v_conv, 'company_profile_id', v_k.company_profile_id,
    'creatore_company_profile_id', v_k.creatore_company_profile_id);
  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.candidatura_accettata',
          case when v_x_owner = p_owner then v_y_owner else v_x_owner end, p_owner, v_payload);
  -- 0041: rivelazione SIMMETRICA (decisione di Michele): solo se anche oggi
  -- entrambe le aziende hanno l'identità forte; altrimenti restano anonime e
  -- non si scrive l'audit di rivelazione.
  if coalesce(p_rivela, false)
     and public.fn_partenariato_identita_forte(v_k.creatore_company_profile_id)
     and public.fn_partenariato_identita_forte(v_k.company_profile_id) then
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values
      (p_attore, 'partenariato.identita_rivelata',
       case when v_x_owner = p_owner then v_y_owner else v_x_owner end, p_owner, v_payload),
      (p_attore, 'partenariato.contatti_rivelati',
       case when v_x_owner = p_owner then v_y_owner else v_x_owner end, p_owner, v_payload);
  end if;

  -- 0040: consorzio (V1). La riga del creatore se manca (scritta dal sistema),
  -- poi quella di Y con la posizione e la quota della posizione.
  perform public.fn_partner_membro_creatore(v_k.partner_call_id, null);
  select ruolo, quota_ipotizzata_pct into v_pos_ruolo, v_pos_quota
  from public.partner_call_posizioni where id = v_k.posizione_id;
  v_membro := public.fn_partner_membro_inserisci(
    v_k.partner_call_id, v_k.company_profile_id, v_k.id, v_k.posizione_id,
    case when v_pos_ruolo = 'capofila' then 'capofila' else 'partner' end,
    v_pos_quota, p_attore, true);
  if v_membro is null then
    select id into v_membro from public.partner_call_membri
    where partner_call_id = v_k.partner_call_id and company_profile_id = v_k.company_profile_id;
  end if;

  return jsonb_build_object('candidatura', to_jsonb(v_k), 'conversazione_id', v_conv,
                            'membro_id', v_membro);
end;
$$;

comment on function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean, boolean) is
  'Decisione su una candidatura (X) o su un invito (Y): accetta | rifiuta. Lettura senza lock, poi owner → azienda che decide → (accetta) azienda Y FOR KEY SHARE e profilo Y FOR SHARE → call FOR SHARE → candidatura FOR UPDATE → (accetta) advisory esclusività Y × bando → righe del consorzio. Accettazione nella stessa transazione: conversazione, stato accettata, audit partenariato.candidatura_accettata e, solo con p_rivela e (dalla 0041) entrambe le aziende con l''identità forte, partenariato.identita_rivelata + partenariato.contatti_rivelati; dalla 0040 la riga del creatore se manca e quella di Y (proposto, posizione e quota della posizione, riammessa se uscita). Rifiuto con motivo ≤ 500. p_richiedi_non_sandbox NULL o assente = true. Ritorna {candidatura, conversazione_id} (+ membro_id all''accettazione). Detail: parametri_non_validi | attore_non_titolare | candidatura_non_trovata | owner_not_found | azienda_non_disponibile | candidatura_gia_decisa | invito_scaduto | call_non_attiva | identita_non_verificata | profilo_partner_non_attivo | controparte_non_disponibile | stesso_gruppo | esclusivita_violata.';

-- ----------------------------------------------------------------------------
-- 6) RPC dell'identità verificata dall'admin.
-- ----------------------------------------------------------------------------

-- 6a) fn_identita_richiedi — il titolare chiede la verifica. Azienda viva del
--     titolare (lock FOR NO KEY UPDATE: serializza con le altre RPC
--     dell'identità e con il trigger di revoca), T5 (identita_non_verificata),
--     una richiesta aperta alla volta (identita_richiesta_aperta), non già
--     verificata (identita_gia_verificata). Nota facoltativa ≤ 500 (come
--     preferisce essere contattato, niente dati personali obbligatori).
create or replace function public.fn_identita_richiedi(
  p_owner   uuid,
  p_company uuid,
  p_attore  uuid,
  p_nota    text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_nota  text := nullif(btrim(p_nota), '');
  v_guc   text;
  v_stato public.company_identita_stato%rowtype;
begin
  if char_length(v_nota) > 500 then
    raise exception 'Nota troppo lunga' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'La verifica dell''identità la chiede il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;
  if not public.fn_partenariato_identita_ok(p_company, false) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;

  select * into v_stato from public.company_identita_stato
  where company_profile_id = p_company
  for update;
  if v_stato.stato = 'richiesta' then
    raise exception 'C''è già una richiesta di verifica in attesa'
      using detail = 'identita_richiesta_aperta';
  end if;
  if v_stato.stato = 'verificata' then
    raise exception 'L''identità dell''azienda è già verificata'
      using detail = 'identita_gia_verificata';
  end if;

  v_guc := current_setting('app.identita_azienda', true);
  perform set_config('app.identita_azienda', 'on', true);
  insert into public.company_identita_stato
    (company_profile_id, stato, richiesta_at, aggiornato_at)
  values
    (p_company, 'richiesta', now(), now())
  on conflict (company_profile_id) do update
    set stato         = 'richiesta',
        metodo        = null,
        verificata_at = null,
        verificata_da = null,
        richiesta_at  = now(),
        aggiornato_at = now()
  returning * into v_stato;
  perform set_config('app.identita_azienda', coalesce(v_guc, ''), true);

  insert into public.company_identita_verifiche
    (company_profile_id, family_parent_id, azione, nota, attore_user_id, origine)
  values
    (p_company, p_owner, 'richiesta', v_nota, p_attore, 'utente');

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'identita.richiesta', p_owner, p_owner,
          jsonb_build_object('company_profile_id', p_company));

  return jsonb_build_object('stato', to_jsonb(v_stato), 'family_parent_id', p_owner,
                            'modificato', true);
end;
$$;

comment on function public.fn_identita_richiedi(uuid, uuid, uuid, text) is
  'Il titolare chiede la verifica dell''identità dell''azienda (lock azienda viva del titolare → stato): T5 dal registro, una richiesta aperta alla volta, non già verificata; nota ≤ 500. Stato richiesta + registro richiesta (origine utente) + audit identita.richiesta. Ritorna {stato, family_parent_id, modificato}. Detail: parametri_non_validi | attore_non_titolare | company_not_found | identita_non_verificata | identita_richiesta_aperta | identita_gia_verificata.';

-- 6b) fn_identita_decidi — l'admin verifica (metodo obbligatorio) o rifiuta
--     una richiesta in attesa (identita_non_richiesta). Azienda viva; per
--     verificata T5 ancora valido. Nota ≤ 500; con rifiutata il metodo si
--     ignora.
create or replace function public.fn_identita_decidi(
  p_company uuid,
  p_admin   uuid,
  p_esito   text,
  p_metodo  text,
  p_nota    text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_nota   text := nullif(btrim(p_nota), '');
  v_metodo text;
  v_owner  uuid;
  v_guc    text;
  v_stato  public.company_identita_stato%rowtype;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if p_esito is null or p_esito not in ('verificata', 'rifiutata')
     or char_length(v_nota) > 500 then
    raise exception 'Decisione sull''identità non valida' using detail = 'parametri_non_validi';
  end if;
  if p_esito = 'verificata' then
    if p_metodo is null
       or p_metodo not in ('telefonata_sede', 'documento_legale_rappresentante', 'pec', 'altro')
    then
      raise exception 'Indica come è stata verificata l''identità'
        using detail = 'metodo_obbligatorio';
    end if;
    v_metodo := p_metodo;
  end if;

  select parent_id into v_owner from public.company_profiles
  where id = p_company and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  select * into v_stato from public.company_identita_stato
  where company_profile_id = p_company
  for update;
  if v_stato.stato is distinct from 'richiesta' then
    raise exception 'Nessuna richiesta di verifica in attesa per questa azienda'
      using detail = 'identita_non_richiesta';
  end if;
  if p_esito = 'verificata' and not public.fn_partenariato_identita_ok(p_company, false) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;

  v_guc := current_setting('app.identita_azienda', true);
  perform set_config('app.identita_azienda', 'on', true);
  update public.company_identita_stato
  set stato         = p_esito,
      metodo        = v_metodo,
      verificata_at = case when p_esito = 'verificata' then now() end,
      verificata_da = case when p_esito = 'verificata' then p_admin end,
      aggiornato_at = now()
  where company_profile_id = p_company
  returning * into v_stato;
  perform set_config('app.identita_azienda', coalesce(v_guc, ''), true);

  insert into public.company_identita_verifiche
    (company_profile_id, family_parent_id, azione, metodo, nota, attore_user_id, origine)
  values
    (p_company, v_owner, p_esito, v_metodo, v_nota, p_admin, 'admin');

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin, 'identita.' || p_esito, v_owner, v_owner,
          jsonb_build_object('company_profile_id', p_company, 'metodo', v_metodo));

  return jsonb_build_object('stato', to_jsonb(v_stato), 'family_parent_id', v_owner,
                            'modificato', true);
end;
$$;

comment on function public.fn_identita_decidi(uuid, uuid, text, text, text) is
  'L''admin decide una richiesta di verifica in attesa (lock azienda viva → stato): verificata (metodo telefonata_sede | documento_legale_rappresentante | pec | altro, obbligatorio; T5 ancora valido) | rifiutata (metodo ignorato); nota ≤ 500. Stato + registro (origine admin) + audit identita.verificata | identita.rifiutata. Ritorna {stato, family_parent_id (owner da notificare), modificato}. Detail: admin_non_autorizzato | parametri_non_validi | metodo_obbligatorio | company_not_found | identita_non_richiesta | identita_non_verificata.';

-- 6c) fn_identita_revoca — l'admin revoca una verifica (motivo obbligatorio
--     ≤ 500). Non verificata → nessuna scrittura (modificato false). Anche
--     per un'azienda non più viva.
create or replace function public.fn_identita_revoca(
  p_company uuid,
  p_admin   uuid,
  p_motivo  text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivo text := nullif(btrim(p_motivo), '');
  v_owner  uuid;
  v_guc    text;
  v_stato  public.company_identita_stato%rowtype;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if v_motivo is null or char_length(v_motivo) > 500 then
    raise exception 'Indica il motivo della revoca' using detail = 'motivo_obbligatorio';
  end if;

  select parent_id into v_owner from public.company_profiles
  where id = p_company
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  select * into v_stato from public.company_identita_stato
  where company_profile_id = p_company
  for update;
  if v_stato.stato is distinct from 'verificata' then
    return jsonb_build_object(
      'stato', case when v_stato.company_profile_id is null then null else to_jsonb(v_stato) end,
      'family_parent_id', v_owner, 'modificato', false);
  end if;

  v_guc := current_setting('app.identita_azienda', true);
  perform set_config('app.identita_azienda', 'on', true);
  update public.company_identita_stato
  set stato         = 'non_richiesta',
      metodo        = null,
      verificata_at = null,
      verificata_da = null,
      aggiornato_at = now()
  where company_profile_id = p_company
  returning * into v_stato;
  perform set_config('app.identita_azienda', coalesce(v_guc, ''), true);

  insert into public.company_identita_verifiche
    (company_profile_id, family_parent_id, azione, attore_user_id, origine, motivo)
  values
    (p_company, v_owner, 'revocata', p_admin, 'admin', v_motivo);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin, 'identita.revocata', v_owner, v_owner,
          jsonb_build_object('company_profile_id', p_company, 'origine', 'admin'));

  return jsonb_build_object('stato', to_jsonb(v_stato), 'family_parent_id', v_owner,
                            'modificato', true);
end;
$$;

comment on function public.fn_identita_revoca(uuid, uuid, text) is
  'L''admin revoca una verifica dell''identità (lock azienda → stato; motivo obbligatorio ≤ 500): stato non_richiesta + registro revocata (origine admin) + audit identita.revocata. Non verificata → modificato false senza scritture. Le viste future non mostrano più l''identità (gli audit di rivelazione restano). Ritorna {stato, family_parent_id, modificato}. Detail: admin_non_autorizzato | motivo_obbligatorio | company_not_found.';

-- ----------------------------------------------------------------------------
-- 7) RPC di moderazione (W2). Motivazioni dell'admin 20..2000.
-- ----------------------------------------------------------------------------

-- 7a) fn_partner_segnalazione_prendi — presa in carico: ricevuta → in_esame
--     (azienda autrice ricavata dall'oggetto se manca); già in_esame →
--     nessuna scrittura; decisa o oltre → segnalazione_gia_decisa.
create or replace function public.fn_partner_segnalazione_prendi(
  p_id    uuid,
  p_admin uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_s public.partner_segnalazioni%rowtype;
begin
  perform public.fn_partner_admin_verifica(p_admin);

  select * into v_s from public.partner_segnalazioni where id = p_id for update;
  if not found then
    raise exception 'Segnalazione non trovata' using detail = 'segnalazione_non_trovata';
  end if;
  if v_s.stato = 'in_esame' then
    return jsonb_build_object('segnalazione', to_jsonb(v_s), 'modificato', false);
  end if;
  if v_s.stato <> 'ricevuta' then
    raise exception 'La segnalazione è già stata decisa' using detail = 'segnalazione_gia_decisa';
  end if;

  update public.partner_segnalazioni
  set stato                     = 'in_esame',
      autore_company_profile_id = coalesce(autore_company_profile_id,
                                           public.fn_partner_moderazione_autore(oggetto_tipo,
                                                                                oggetto_id))
  where id = p_id
  returning * into v_s;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin, 'moderazione.presa_in_carico', null, null,
          jsonb_build_object('segnalazione_id', v_s.id, 'oggetto_tipo', v_s.oggetto_tipo,
                             'oggetto_id', v_s.oggetto_id));

  return jsonb_build_object('segnalazione', to_jsonb(v_s), 'modificato', true);
end;
$$;

comment on function public.fn_partner_segnalazione_prendi(uuid, uuid) is
  'Presa in carico di una segnalazione da parte dell''admin (lock della segnalazione): ricevuta → in_esame, azienda autrice ricavata dall''oggetto se manca, audit moderazione.presa_in_carico; già in_esame → modificato false. Ritorna {segnalazione, modificato}. Detail: admin_non_autorizzato | segnalazione_non_trovata | segnalazione_gia_decisa.';

-- 7b) fn_partner_segnalazione_decidi — decisione motivata con effetto
--     ATOMICO. Coerenza con l'oggetto: call → call_sospesa | nessuna_azione,
--     messaggio → contenuto_rimosso | nessuna_azione, profilo →
--     profilo_sospeso | nessuna_azione (decisione_non_valida). Una
--     restrizione richiede lo statement of reasons (statement_mancante) e un
--     oggetto esistente e sospendibile (oggetto_non_trovato |
--     oggetto_non_sospendibile; già sospeso da un'altra decisione → la
--     decisione vale, l'effetto è gia_applicato). Solo da ricevuta o in_esame
--     (segnalazione_gia_decisa). Lock: segnalazione → oggetto (advisory) →
--     owner → azienda → oggetto (riga). Audit moderazione.decisione (mai
--     l'identità del segnalante).
create or replace function public.fn_partner_segnalazione_decidi(
  p_id          uuid,
  p_admin       uuid,
  p_decisione   text,
  p_motivazione text,
  p_sor_testo   text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivazione text := btrim(p_motivazione);
  v_sor         text := nullif(btrim(p_sor_testo), '');
  v_s           public.partner_segnalazioni%rowtype;
  v_effetto     jsonb;
  v_autore      uuid;
  v_owner       uuid;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if p_decisione is null
     or p_decisione not in ('nessuna_azione', 'contenuto_rimosso', 'call_sospesa',
                            'profilo_sospeso') then
    raise exception 'Decisione non valida' using detail = 'decisione_non_valida';
  end if;
  if v_motivazione is null or char_length(v_motivazione) not between 20 and 2000 then
    raise exception 'La motivazione deve avere tra 20 e 2000 caratteri'
      using detail = 'motivazione_non_valida';
  end if;
  if char_length(v_sor) > 10000 then
    raise exception 'Statement of reasons troppo lungo' using detail = 'parametri_non_validi';
  end if;
  if p_decisione <> 'nessuna_azione' and v_sor is null then
    raise exception 'Una restrizione richiede lo statement of reasons per l''autore'
      using detail = 'statement_mancante';
  end if;

  select * into v_s from public.partner_segnalazioni where id = p_id for update;
  if not found then
    raise exception 'Segnalazione non trovata' using detail = 'segnalazione_non_trovata';
  end if;
  if v_s.stato not in ('ricevuta', 'in_esame') then
    raise exception 'La segnalazione è già stata decisa' using detail = 'segnalazione_gia_decisa';
  end if;
  if p_decisione <> 'nessuna_azione'
     and p_decisione is distinct from (case v_s.oggetto_tipo
                                         when 'call' then 'call_sospesa'
                                         when 'messaggio' then 'contenuto_rimosso'
                                         when 'profilo' then 'profilo_sospeso' end) then
    raise exception 'Decisione non ammessa per questo tipo di contenuto'
      using detail = 'decisione_non_valida';
  end if;

  -- Serializzata per oggetto con le altre decisioni, i ricorsi e le azioni
  -- dirette sullo stesso contenuto (fn_partner_moderazione_blocca).
  perform public.fn_partner_moderazione_blocca(v_s.oggetto_tipo, v_s.oggetto_id);

  if p_decisione <> 'nessuna_azione' then
    v_effetto := public.fn_partner_moderazione_applica(v_s.oggetto_tipo, v_s.oggetto_id,
                                                       p_admin, v_motivazione);
    if v_effetto ->> 'esito' = 'oggetto_non_trovato' then
      raise exception 'Il contenuto segnalato non esiste più' using detail = 'oggetto_non_trovato';
    end if;
    if v_effetto ->> 'esito' = 'non_sospendibile' then
      raise exception 'Il contenuto non si può sospendere in questo stato'
        using detail = 'oggetto_non_sospendibile';
    end if;
  end if;

  v_autore := coalesce(v_s.autore_company_profile_id,
                       (v_effetto ->> 'autore_company_id')::uuid,
                       public.fn_partner_moderazione_autore(v_s.oggetto_tipo, v_s.oggetto_id));
  select parent_id into v_owner from public.company_profiles where id = v_autore;

  update public.partner_segnalazioni
  set stato                     = 'decisa',
      decisione                 = p_decisione,
      motivazione               = v_motivazione,
      sor_testo                 = v_sor,
      deciso_da                 = p_admin,
      deciso_at                 = now(),
      autore_company_profile_id = v_autore
  where id = p_id
  returning * into v_s;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin, 'moderazione.decisione', v_owner, v_owner,
          jsonb_build_object('segnalazione_id', v_s.id, 'oggetto_tipo', v_s.oggetto_tipo,
                             'oggetto_id', v_s.oggetto_id, 'decisione', p_decisione,
                             'effetto', v_effetto ->> 'esito',
                             'autore_company_profile_id', v_autore));

  return jsonb_build_object('segnalazione', to_jsonb(v_s), 'effetto', v_effetto ->> 'esito',
                            'autore_company_id', v_autore, 'autore_owner_id', v_owner);
end;
$$;

comment on function public.fn_partner_segnalazione_decidi(uuid, uuid, text, text, text) is
  'Decisione motivata dell''admin su una segnalazione ricevuta o in esame (lock segnalazione → oggetto advisory → owner → azienda → oggetto), effetto ATOMICO: call_sospesa (call → sospesa_moderazione con stato precedente), contenuto_rimosso (messaggio oscurato), profilo_sospeso, oppure nessuna_azione; coerente con oggetto_tipo; motivazione 20..2000; statement of reasons obbligatorio per le restrizioni (≤ 10000). Stato decisa + audit moderazione.decisione. Ritorna {segnalazione, effetto (applicato | gia_applicato | NULL), autore_company_id, autore_owner_id}. Detail: admin_non_autorizzato | decisione_non_valida | motivazione_non_valida | parametri_non_validi | statement_mancante | segnalazione_non_trovata | segnalazione_gia_decisa | oggetto_non_trovato | oggetto_non_sospendibile.';

-- 7c) fn_partner_segnalazione_ricorso — ricorso interno (DSA art. 20): UNO
--     solo, entro 6 mesi dalla decisione, su una segnalazione decisa. Lo
--     presenta il titolare dell'azienda autrice (p_company = autrice) contro
--     una restrizione, oppure il segnalante contro nessuna_azione: così il
--     ricorso di chi non ha interesse non consuma quello dell'altro. Chi non
--     è né autore né segnalante → segnalazione_non_trovata (nessuna prova
--     dell'esistenza). Testo 20..2000.
create or replace function public.fn_partner_segnalazione_ricorso(
  p_id      uuid,
  p_user    uuid,
  p_company uuid,
  p_testo   text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_testo      text := btrim(p_testo);
  v_s          public.partner_segnalazioni%rowtype;
  v_autore     boolean;
  v_segnalante boolean;
  v_ruolo      text;
begin
  if v_testo is null or char_length(v_testo) not between 20 and 2000 then
    raise exception 'Il ricorso deve avere tra 20 e 2000 caratteri'
      using detail = 'ricorso_testo_non_valido';
  end if;

  select * into v_s from public.partner_segnalazioni where id = p_id for update;
  if not found then
    raise exception 'Segnalazione non trovata' using detail = 'segnalazione_non_trovata';
  end if;
  v_autore := coalesce(v_s.autore_company_profile_id = p_company, false)
              and exists (select 1 from public.company_profiles
                          where id = p_company and parent_id = p_user);
  v_segnalante := coalesce(v_s.segnalante_user_id = p_user, false);
  if not (v_autore or v_segnalante) then
    raise exception 'Segnalazione non trovata' using detail = 'segnalazione_non_trovata';
  end if;

  if v_s.stato <> 'decisa' or v_s.ricorso_testo is not null
     or v_s.deciso_at < now() - interval '6 months' then
    raise exception 'Il ricorso non è ammesso' using detail = 'ricorso_non_ammesso';
  end if;
  v_ruolo := case
    when v_autore and v_s.decisione <> 'nessuna_azione' then 'autore'
    when v_segnalante and v_s.decisione = 'nessuna_azione' then 'segnalante'
  end;
  if v_ruolo is null then
    raise exception 'Il ricorso non è ammesso' using detail = 'ricorso_non_ammesso';
  end if;

  update public.partner_segnalazioni
  set stato              = 'ricorso_presentato',
      ricorso_testo      = v_testo,
      ricorso_da_user_id = p_user,
      ricorso_at         = now()
  where id = p_id
  returning * into v_s;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_user, 'moderazione.ricorso_presentato', p_user,
          case when v_ruolo = 'autore' then p_user end,
          jsonb_build_object('segnalazione_id', v_s.id, 'ruolo', v_ruolo));

  return jsonb_build_object('segnalazione', to_jsonb(v_s), 'ruolo', v_ruolo);
end;
$$;

comment on function public.fn_partner_segnalazione_ricorso(uuid, uuid, uuid, text) is
  'Ricorso interno su una segnalazione decisa (lock della segnalazione): uno solo, entro 6 mesi da deciso_at; il titolare dell''azienda autrice (p_company) contro una restrizione, il segnalante contro nessuna_azione; testo 20..2000. Stato ricorso_presentato + audit moderazione.ricorso_presentato. Ritorna {segnalazione, ruolo: autore | segnalante}. Detail: ricorso_testo_non_valido | segnalazione_non_trovata | ricorso_non_ammesso.';

-- 7d) fn_partner_ricorso_decidi — l'admin decide il ricorso con motivazione.
--     confermata: nulla cambia. riformata: la restrizione si annulla
--     (fn_partner_moderazione_annulla), salvo che la regga ancora qualcos'altro
--     (effetto mantenuto): un'altra decisione valida sullo stesso oggetto il
--     cui effetto non sia stato tolto da un ripristino diretto
--     (effetto_revocato_at), oppure una sospensione D'UFFICIO in corso (la
--     restrizione in corso non è iniziata con nessuna decisione:
--     fn_partner_moderazione_inizio). Se la decisione era nessuna_azione
--     (ricorso del segnalante) la restrizione dell'oggetto si applica ora
--     (best-effort: oggetto sparito o non sospendibile → esito nel
--     risultato). Lock come la decisione (segnalazione → oggetto advisory →
--     owner → azienda → oggetto): il controllo vede le decisioni concorrenti
--     sullo stesso oggetto già committate.
create or replace function public.fn_partner_ricorso_decidi(
  p_id          uuid,
  p_admin       uuid,
  p_esito       text,
  p_motivazione text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivazione text := btrim(p_motivazione);
  v_s           public.partner_segnalazioni%rowtype;
  v_effetto     jsonb;
  v_owner       uuid;
  v_inizio      timestamptz;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if p_esito is null or p_esito not in ('confermata', 'riformata') then
    raise exception 'Esito del ricorso non valido' using detail = 'parametri_non_validi';
  end if;
  if v_motivazione is null or char_length(v_motivazione) not between 20 and 2000 then
    raise exception 'La motivazione deve avere tra 20 e 2000 caratteri'
      using detail = 'motivazione_non_valida';
  end if;

  select * into v_s from public.partner_segnalazioni where id = p_id for update;
  if not found then
    raise exception 'Segnalazione non trovata' using detail = 'segnalazione_non_trovata';
  end if;
  if v_s.stato <> 'ricorso_presentato' then
    raise exception 'Nessun ricorso da decidere' using detail = 'ricorso_non_in_attesa';
  end if;

  -- Serializzata per oggetto: le decisioni e i ricorsi concorrenti sullo
  -- stesso contenuto si vedono committati nei controlli qui sotto.
  perform public.fn_partner_moderazione_blocca(v_s.oggetto_tipo, v_s.oggetto_id);

  if p_esito = 'riformata' then
    if v_s.decisione = 'nessuna_azione' then
      v_effetto := public.fn_partner_moderazione_applica(v_s.oggetto_tipo, v_s.oggetto_id,
                                                         p_admin, v_motivazione);
    else
      v_inizio := public.fn_partner_moderazione_inizio(v_s.oggetto_tipo, v_s.oggetto_id);
      if v_inizio is not null and (
        -- un'altra decisione valida, con l'effetto non tolto da un ripristino
        -- diretto dell'admin, regge la restrizione;
        exists (
          select 1 from public.partner_segnalazioni s2
          where s2.oggetto_tipo = v_s.oggetto_tipo
            and s2.oggetto_id = v_s.oggetto_id
            and s2.id <> v_s.id
            and s2.effetto_revocato_at is null
            and ((s2.decisione <> 'nessuna_azione'
                  and (s2.stato in ('decisa', 'ricorso_presentato')
                       or (s2.stato = 'ricorso_deciso' and s2.ricorso_esito = 'confermata')))
                 or (s2.decisione = 'nessuna_azione' and s2.stato = 'ricorso_deciso'
                     and s2.ricorso_esito = 'riformata'))
        )
        -- oppure la restrizione in corso è D'UFFICIO: non è iniziata con
        -- nessuna decisione su una segnalazione (chi la applica scrive lo
        -- stesso now() della decisione, nella stessa transazione).
        or not exists (
          select 1 from public.partner_segnalazioni s3
          where s3.oggetto_tipo = v_s.oggetto_tipo
            and s3.oggetto_id = v_s.oggetto_id
            and ((s3.decisione <> 'nessuna_azione' and s3.deciso_at = v_inizio)
                 or (s3.decisione = 'nessuna_azione' and s3.ricorso_esito = 'riformata'
                     and s3.ricorso_deciso_at = v_inizio))
        )
      ) then
        v_effetto := jsonb_build_object('esito', 'mantenuto');
      else
        v_effetto := public.fn_partner_moderazione_annulla(v_s.oggetto_tipo, v_s.oggetto_id);
      end if;
    end if;
  end if;

  update public.partner_segnalazioni
  set stato               = 'ricorso_deciso',
      ricorso_esito       = p_esito,
      ricorso_motivazione = v_motivazione,
      ricorso_deciso_da   = p_admin,
      ricorso_deciso_at   = now(),
      autore_company_profile_id = coalesce(autore_company_profile_id,
                                           (v_effetto ->> 'autore_company_id')::uuid)
  where id = p_id
  returning * into v_s;
  select parent_id into v_owner from public.company_profiles
  where id = v_s.autore_company_profile_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_admin, 'moderazione.ricorso_deciso', v_owner, v_owner,
          jsonb_build_object('segnalazione_id', v_s.id, 'esito', p_esito,
                             'effetto', v_effetto ->> 'esito'));

  return jsonb_build_object('segnalazione', to_jsonb(v_s), 'effetto', v_effetto ->> 'esito',
                            'autore_company_id', v_s.autore_company_profile_id,
                            'autore_owner_id', v_owner);
end;
$$;

comment on function public.fn_partner_ricorso_decidi(uuid, uuid, text, text) is
  'L''admin decide un ricorso presentato (lock segnalazione → oggetto advisory → owner → azienda → oggetto): confermata | riformata, motivazione 20..2000. riformata annulla la restrizione (call → stato precedente o scaduta, profilo riattivato, messaggio visibile) salvo che la regga un''altra decisione valida sullo stesso oggetto con l''effetto non revocato da un ripristino diretto, o una sospensione d''ufficio in corso (effetto mantenuto); su nessuna_azione applica la restrizione dell''oggetto. Stato ricorso_deciso + audit moderazione.ricorso_deciso. Ritorna {segnalazione, effetto (annullato | non_sospeso | mantenuto | applicato | gia_applicato | oggetto_non_trovato | non_sospendibile | NULL), autore_company_id, autore_owner_id}. Detail: admin_non_autorizzato | parametri_non_validi | motivazione_non_valida | segnalazione_non_trovata | ricorso_non_in_attesa.';

-- 7e) fn_partner_admin_sospendi — sospensione diretta dell'admin, senza
--     segnalazione (oggetto_tipo call | profilo | messaggio, riferimento come
--     in partner_segnalazioni.oggetto_id). Già sospeso → modificato false
--     senza audit. Audit admin.partner_call_sospesa | moderazione.profilo_sospeso
--     | moderazione.messaggio_oscurato. Lock: oggetto (advisory) → owner →
--     azienda → oggetto (riga).
create or replace function public.fn_partner_admin_sospendi(
  p_oggetto_tipo text,
  p_oggetto_id   text,
  p_admin        uuid,
  p_motivazione  text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivazione text := btrim(p_motivazione);
  v_effetto     jsonb;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if p_oggetto_tipo is null or p_oggetto_tipo not in ('call', 'profilo', 'messaggio')
     or p_oggetto_id is null then
    raise exception 'Contenuto non valido' using detail = 'parametri_non_validi';
  end if;
  if v_motivazione is null or char_length(v_motivazione) not between 20 and 2000 then
    raise exception 'La motivazione deve avere tra 20 e 2000 caratteri'
      using detail = 'motivazione_non_valida';
  end if;

  perform public.fn_partner_moderazione_blocca(p_oggetto_tipo, p_oggetto_id);
  v_effetto := public.fn_partner_moderazione_applica(p_oggetto_tipo, p_oggetto_id, p_admin,
                                                     v_motivazione);
  if v_effetto ->> 'esito' = 'oggetto_non_trovato' then
    raise exception 'Contenuto non trovato' using detail = 'oggetto_non_trovato';
  end if;
  if v_effetto ->> 'esito' = 'non_sospendibile' then
    raise exception 'Il contenuto non si può sospendere in questo stato'
      using detail = 'oggetto_non_sospendibile';
  end if;

  if v_effetto ->> 'esito' = 'applicato' then
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_admin,
            case p_oggetto_tipo when 'call' then 'admin.partner_call_sospesa'
                                when 'profilo' then 'moderazione.profilo_sospeso'
                                else 'moderazione.messaggio_oscurato' end,
            (v_effetto ->> 'autore_owner_id')::uuid, (v_effetto ->> 'autore_owner_id')::uuid,
            jsonb_build_object('oggetto_tipo', p_oggetto_tipo, 'oggetto_id', p_oggetto_id,
                               'origine', 'admin', 'motivazione', v_motivazione,
                               'stato_precedente', v_effetto ->> 'stato_precedente'));
  end if;

  return v_effetto || jsonb_build_object('modificato', v_effetto ->> 'esito' = 'applicato');
end;
$$;

comment on function public.fn_partner_admin_sospendi(text, text, uuid, text) is
  'Sospensione diretta dell''admin (call → sospesa_moderazione da bozza | pubblicata, profilo → sospeso, messaggio → oscurato; riferimento come partner_segnalazioni.oggetto_id; motivazione 20..2000; lock oggetto advisory → owner → azienda → oggetto). Già sospeso → modificato false senza scritture. Audit admin.partner_call_sospesa | moderazione.profilo_sospeso | moderazione.messaggio_oscurato. Ritorna {esito, stato_precedente?, stato?, autore_company_id, autore_owner_id, modificato}. Detail: admin_non_autorizzato | parametri_non_validi | motivazione_non_valida | oggetto_non_trovato | oggetto_non_sospendibile.';

-- 7f) fn_partner_admin_ripristina — ripristino diretto dell'admin: call →
--     stato precedente (o scaduta se nel frattempo è passata la scadenza),
--     profilo → sospeso_at NULL, messaggio → visibile. Non sospeso →
--     modificato false senza audit. Audit admin.partner_call_ripristinata |
--     moderazione.profilo_ripristinato | moderazione.messaggio_ripristinato.
--     Le decisioni ancora valide sull'oggetto ricevono effetto_revocato_at:
--     non reggono più la restrizione tolta. Lock come la sospensione.
create or replace function public.fn_partner_admin_ripristina(
  p_oggetto_tipo text,
  p_oggetto_id   text,
  p_admin        uuid,
  p_motivazione  text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_motivazione text := btrim(p_motivazione);
  v_effetto     jsonb;
  v_rif         text;
begin
  perform public.fn_partner_admin_verifica(p_admin);
  if p_oggetto_tipo is null or p_oggetto_tipo not in ('call', 'profilo', 'messaggio')
     or p_oggetto_id is null then
    raise exception 'Contenuto non valido' using detail = 'parametri_non_validi';
  end if;
  if v_motivazione is null or char_length(v_motivazione) not between 20 and 2000 then
    raise exception 'La motivazione deve avere tra 20 e 2000 caratteri'
      using detail = 'motivazione_non_valida';
  end if;

  v_rif := public.fn_partner_moderazione_blocca(p_oggetto_tipo, p_oggetto_id);
  v_effetto := public.fn_partner_moderazione_annulla(p_oggetto_tipo, p_oggetto_id);
  if v_effetto ->> 'esito' = 'oggetto_non_trovato' then
    raise exception 'Contenuto non trovato' using detail = 'oggetto_non_trovato';
  end if;

  if v_effetto ->> 'esito' = 'annullato' then
    -- Le decisioni ancora valide che reggevano la restrizione tolta non la
    -- reggono più: un ricorso accolto su una decisione successiva non la
    -- mantiene per causa loro (fn_partner_ricorso_decidi).
    update public.partner_segnalazioni
    set effetto_revocato_at = now()
    where oggetto_tipo = p_oggetto_tipo
      and oggetto_id = v_rif
      and effetto_revocato_at is null
      and ((decisione <> 'nessuna_azione'
            and (stato in ('decisa', 'ricorso_presentato')
                 or (stato = 'ricorso_deciso' and ricorso_esito = 'confermata')))
           or (decisione = 'nessuna_azione' and stato = 'ricorso_deciso'
               and ricorso_esito = 'riformata'));

    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_admin,
            case p_oggetto_tipo when 'call' then 'admin.partner_call_ripristinata'
                                when 'profilo' then 'moderazione.profilo_ripristinato'
                                else 'moderazione.messaggio_ripristinato' end,
            (v_effetto ->> 'autore_owner_id')::uuid, (v_effetto ->> 'autore_owner_id')::uuid,
            jsonb_build_object('oggetto_tipo', p_oggetto_tipo, 'oggetto_id', p_oggetto_id,
                               'origine', 'admin', 'motivazione', v_motivazione,
                               'stato', v_effetto ->> 'stato'));
  end if;

  return v_effetto || jsonb_build_object('modificato', v_effetto ->> 'esito' = 'annullato');
end;
$$;

comment on function public.fn_partner_admin_ripristina(text, text, uuid, text) is
  'Ripristino diretto dell''admin (call sospesa → stato_prima_sospensione, o scaduta se era pubblicata e scadenza_call < oggi Europe/Rome; profilo → sospeso_at NULL; messaggio → visibile; motivazione 20..2000; lock oggetto advisory → owner → azienda → oggetto). Le decisioni ancora valide sull''oggetto ricevono effetto_revocato_at (non reggono più la restrizione). Non sospeso → modificato false senza scritture. Audit admin.partner_call_ripristinata | moderazione.profilo_ripristinato | moderazione.messaggio_ripristinato. Ritorna {esito, stato?, autore_company_id, autore_owner_id, modificato}. Detail: admin_non_autorizzato | parametri_non_validi | motivazione_non_valida | oggetto_non_trovato.';

-- ----------------------------------------------------------------------------
-- 8) Metriche e costi del modulo per l'admin (W3). Periodo [p_da, p_a] in
--    giorni Europe/Rome, estremi compresi, al massimo 3660 giorni.
-- ----------------------------------------------------------------------------

-- 8a) Metriche sulla COORTE delle call pubblicate nel periodo (pubblicata_at):
--     candidature spontanee e inviti di quelle call, in qualunque momento;
--     % con ≥ 1 candidatura entro 30 giorni dalla pubblicazione solo sulle
--     call pubblicate da almeno 30 giorni (le altre non sono ancora
--     osservabili); tasso di accettazione per tipo = accettate / (accettate +
--     rifiutate); ore mediane alla prima candidatura spontanea; copertura
--     media dei requisiti cercati e consorzi con l'ultima validazione verde
--     (validazione salvata, aggiornata dal passo ricalcolo_validazioni dello
--     scheduler).
create or replace function public.fn_admin_metriche_partenariati(p_da date, p_a date)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_inizio      timestamptz;
  v_fine        timestamptz;
  v_call        integer;
  v_cand        integer;
  v_inviti      integer;
  v_osservabili integer;
  v_con_cand    integer;
  v_mediana     double precision;
  v_copertura   numeric;
  v_validati    integer;
  v_verdi       integer;
  v_tassi       jsonb;
begin
  if p_da is null or p_a is null or p_da > p_a or p_a - p_da > 3660 then
    raise exception 'Periodo non valido' using detail = 'periodo_non_valido';
  end if;
  v_inizio := p_da::timestamp at time zone 'Europe/Rome';
  v_fine := (p_a + 1)::timestamp at time zone 'Europe/Rome';

  select count(*)::integer,
         count(*) filter (where c.pubblicata_at <= now() - interval '30 days')::integer,
         round(avg(c.copertura_gap_ratio), 3),
         count(*) filter (where c.validazione_esito is not null)::integer,
         count(*) filter (where c.validazione_esito = 'verde')::integer
    into v_call, v_osservabili, v_copertura, v_validati, v_verdi
  from public.partner_calls c
  where c.pubblicata_at >= v_inizio and c.pubblicata_at < v_fine;

  select count(*) filter (where k.tipo = 'candidatura')::integer,
         count(*) filter (where k.tipo = 'invito')::integer
    into v_cand, v_inviti
  from public.partner_candidature k
  join public.partner_calls c on c.id = k.partner_call_id
  where c.pubblicata_at >= v_inizio and c.pubblicata_at < v_fine;

  select count(*)::integer into v_con_cand
  from public.partner_calls c
  where c.pubblicata_at >= v_inizio and c.pubblicata_at < v_fine
    and c.pubblicata_at <= now() - interval '30 days'
    and exists (select 1 from public.partner_candidature k
                where k.partner_call_id = c.id and k.tipo = 'candidatura'
                  and k.created_at <= c.pubblicata_at + interval '30 days');

  select percentile_cont(0.5) within group (order by p.ore)
    into v_mediana
  from (
    select extract(epoch from (min(k.created_at) - c.pubblicata_at)) / 3600.0 as ore
    from public.partner_calls c
    join public.partner_candidature k on k.partner_call_id = c.id and k.tipo = 'candidatura'
    where c.pubblicata_at >= v_inizio and c.pubblicata_at < v_fine
    group by c.id, c.pubblicata_at
  ) p;

  select jsonb_object_agg(t.tipo, jsonb_build_object(
           'accettate', coalesce(a.accettate, 0),
           'rifiutate', coalesce(a.rifiutate, 0),
           'tasso', case when coalesce(a.accettate, 0) + coalesce(a.rifiutate, 0) > 0
                         then round(a.accettate::numeric / (a.accettate + a.rifiutate), 3) end))
    into v_tassi
  from (values ('candidatura'), ('invito')) t(tipo)
  left join (
    select k.tipo,
           count(*) filter (where k.stato = 'accettata')::integer as accettate,
           count(*) filter (where k.stato = 'rifiutata')::integer as rifiutate
    from public.partner_candidature k
    join public.partner_calls c on c.id = k.partner_call_id
    where c.pubblicata_at >= v_inizio and c.pubblicata_at < v_fine
    group by k.tipo
  ) a on a.tipo = t.tipo;

  return jsonb_build_object(
    'da', p_da,
    'a', p_a,
    'call_pubblicate', v_call,
    'candidature', v_cand,
    'inviti', v_inviti,
    'candidature_per_call',
      case when v_call > 0 then round(v_cand::numeric / v_call, 2) end,
    'call_osservabili_30_giorni', v_osservabili,
    'call_con_candidatura_30_giorni', v_con_cand,
    'percentuale_call_con_candidatura_30_giorni',
      case when v_osservabili > 0 then round(100.0 * v_con_cand / v_osservabili, 1) end,
    'accettazione', v_tassi,
    'ore_mediane_prima_candidatura', round(v_mediana::numeric, 1),
    'copertura_media_gap', v_copertura,
    'consorzi_validati', v_validati,
    'consorzi_validati_verde', v_verdi);
end;
$$;

comment on function public.fn_admin_metriche_partenariati(date, date) is
  'Metriche del modulo sulle call pubblicate nel periodo [p_da, p_a] (giorni Europe/Rome, estremi compresi, ≤ 3660 giorni), aggregate in SQL: {da, a, call_pubblicate, candidature, inviti, candidature_per_call, call_osservabili_30_giorni (pubblicate da almeno 30 giorni), call_con_candidatura_30_giorni, percentuale_call_con_candidatura_30_giorni, accettazione: {candidatura|invito: {accettate, rifiutate, tasso}}, ore_mediane_prima_candidatura, copertura_media_gap, consorzi_validati, consorzi_validati_verde}; valori non calcolabili NULL. Detail: periodo_non_valido.';

-- 8b) Costi del modulo da api_usage_events, per provider, servizio, esito e
--     VALUTA (EUR per openapi, USD per Anthropic), con i totali per valuta:
--     mai sommati tra valute diverse. Servizi del modulo: Anthropic
--     (estrazione WP3, bozza profilo, posizioni e testi della call, bozze
--     WP10) e openapi (IT-advanced del WP1, bilancio ottico, stato e visura
--     impresa del WP2); IT-full e verifica CF sono dell'import esistente.
create or replace function public.fn_admin_costi_partenariati(p_da date, p_a date)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_inizio timestamptz;
  v_fine   timestamptz;
  v_out    jsonb;
begin
  if p_da is null or p_a is null or p_da > p_a or p_a - p_da > 3660 then
    raise exception 'Periodo non valido' using detail = 'periodo_non_valido';
  end if;
  v_inizio := p_da::timestamp at time zone 'Europe/Rome';
  v_fine := (p_a + 1)::timestamp at time zone 'Europe/Rome';

  with servizi (provider, service, valuta) as (
    values
      ('anthropic', 'partenariato_estrazione', 'USD'),
      ('anthropic', 'partner_profilo_ai', 'USD'),
      ('anthropic', 'partner_call_posizioni', 'USD'),
      ('anthropic', 'partner_call_testi', 'USD'),
      ('anthropic', 'partner_bozza', 'USD'),
      ('openapi', 'IT-advanced', 'EUR'),
      ('openapi', 'bilancio-ottico', 'EUR'),
      ('openapi', 'bilancio-ottico-stato', 'EUR'),
      ('openapi', 'visure-impresa', 'EUR')
  ),
  voci as (
    select e.provider, e.service, e.outcome, s.valuta,
           count(*)::bigint as eventi, sum(e.cost_cents)::bigint as cost_cents
    from public.api_usage_events e
    join servizi s on s.provider = e.provider and s.service = e.service
    where e.created_at >= v_inizio and e.created_at < v_fine
    group by e.provider, e.service, e.outcome, s.valuta
  ),
  totali as (
    select valuta, sum(eventi)::bigint as eventi, sum(cost_cents)::bigint as cost_cents
    from voci
    group by valuta
  )
  select jsonb_build_object(
    'da', p_da,
    'a', p_a,
    'voci', coalesce((
      select jsonb_agg(jsonb_build_object('provider', v.provider, 'service', v.service,
                                          'outcome', v.outcome, 'valuta', v.valuta,
                                          'eventi', v.eventi, 'cost_cents', v.cost_cents)
                       order by v.valuta collate "C", v.provider collate "C",
                                v.service collate "C", v.outcome collate "C")
      from voci v), '[]'::jsonb),
    'totali', coalesce((
      select jsonb_agg(jsonb_build_object('valuta', t.valuta, 'eventi', t.eventi,
                                          'cost_cents', t.cost_cents)
                       order by t.valuta collate "C")
      from totali t), '[]'::jsonb))
  into v_out;
  return v_out;
end;
$$;

comment on function public.fn_admin_costi_partenariati(date, date) is
  'Costi del modulo nel periodo [p_da, p_a] (giorni Europe/Rome, estremi compresi, ≤ 3660 giorni) da api_usage_events, aggregati in SQL: {da, a, voci: [{provider, service, outcome, valuta, eventi, cost_cents}] (ordinate per valuta, provider, service, outcome, collation C), totali: [{valuta, eventi, cost_cents}]}; cost_cents nella valuta della voce (EUR openapi, USD Anthropic), MAI sommati tra valute. Detail: periodo_non_valido.';

-- ----------------------------------------------------------------------------
-- 9) Call da rivalidare (WP8 P13a): passo ricalcolo_validazioni dello
--    scheduler, a blocchi e best-effort. Call pubblicate, scadute o
--    completate (gli stati in cui il consorzio si modifica) mai validate o con
--    un membro scritto dopo l'ultima validazione; le più vecchie prima.
--    p_limite 1..500 (NULL = 100).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_validazioni_da_ricalcolare(p_limite integer)
returns setof uuid
language sql
stable
security definer
set search_path = public
as $$
  select c.id
  from public.partner_calls c
  where c.stato in ('pubblicata', 'scaduta', 'chiusa_completata')
    and (c.validazione_at is null
         or exists (select 1 from public.partner_call_membri m
                    where m.partner_call_id = c.id and m.updated_at > c.validazione_at))
  order by c.validazione_at nulls first, c.id
  limit greatest(1, least(coalesce(p_limite, 100), 500));
$$;

comment on function public.fn_partner_call_validazioni_da_ricalcolare(integer) is
  'Interna (scheduler, passo ricalcolo_validazioni): id delle call pubblicate | scadute | chiuse_completate mai validate o con un membro del consorzio scritto (updated_at) dopo validazione_at, le più vecchie prima; p_limite 1..500 (NULL = 100). Il backend ricalcola e salva con fn_partner_call_validazione_salva.';

-- ----------------------------------------------------------------------------
-- 10) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova o ridefinita
--    (trigger e interne comprese) eseguibile dai ruoli esposti (Supabase
--    concede EXECUTE di default a PUBLIC; create or replace conserva i
--    permessi, la revoca si ripete comunque). I trigger scattano comunque.
-- ----------------------------------------------------------------------------
alter table public.company_identita_verifiche enable row level security;
alter table public.company_identita_stato enable row level security;

revoke all on public.company_identita_verifiche from anon, authenticated;
revoke all on public.company_identita_stato from anon, authenticated;

revoke execute on function public.fn_create_consultation_request(jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_identita_verifiche_readonly()
  from public, anon, authenticated;
revoke execute on function public.fn_identita_stato_protetto()
  from public, anon, authenticated;
revoke execute on function public.fn_identita_revoca_su_cambio_azienda()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_admin_verifica(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_moderazione_autore(text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_moderazione_applica(text, text, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_moderazione_annulla(text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_moderazione_blocca(text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_moderazione_inizio(text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_identita_forte(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_rappresentante_ok(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_consenso(uuid, uuid, uuid, text, text, text, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_identita_richiedi(uuid, uuid, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_identita_decidi(uuid, uuid, text, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_identita_revoca(uuid, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_segnalazione_prendi(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_segnalazione_decidi(uuid, uuid, text, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_segnalazione_ricorso(uuid, uuid, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_ricorso_decidi(uuid, uuid, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_admin_sospendi(text, text, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_admin_ripristina(text, text, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_admin_metriche_partenariati(date, date)
  from public, anon, authenticated;
revoke execute on function public.fn_admin_costi_partenariati(date, date)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_validazioni_da_ricalcolare(integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0041 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle e le colonne nuove).
-- 1) Ripristino delle funzioni ridefinite: rieseguire i blocchi
--    «create or replace function public.fn_create_consultation_request» di
--    0028_addon_inventory_ledger.sql, «…fn_partenariato_rappresentante_ok» di
--    0037_call_partenariato.sql, «…fn_partner_consenso» di
--    0035_profili_partner.sql e «…fn_partner_decidi» di
--    0040_partenariato_consorzio.sql (stessa firma; le revoche restano). Va
--    fatto PRIMA dei punti successivi: le versioni 0041 usano le funzioni e le
--    tabelle nuove.
-- 2) drop trigger trg_identita_revoca_su_cambio_azienda on public.company_profiles;
--    drop function public.fn_partner_call_validazioni_da_ricalcolare(integer);
--    drop function public.fn_admin_costi_partenariati(date, date);
--    drop function public.fn_admin_metriche_partenariati(date, date);
--    drop function public.fn_partner_admin_ripristina(text, text, uuid, text);
--    drop function public.fn_partner_admin_sospendi(text, text, uuid, text);
--    drop function public.fn_partner_ricorso_decidi(uuid, uuid, text, text);
--    drop function public.fn_partner_segnalazione_ricorso(uuid, uuid, uuid, text);
--    drop function public.fn_partner_segnalazione_decidi(uuid, uuid, text, text, text);
--    drop function public.fn_partner_segnalazione_prendi(uuid, uuid);
--    drop function public.fn_identita_revoca(uuid, uuid, text);
--    drop function public.fn_identita_decidi(uuid, uuid, text, text, text);
--    drop function public.fn_identita_richiedi(uuid, uuid, uuid, text);
--    drop function public.fn_partenariato_identita_forte(uuid);
--    drop function public.fn_partner_moderazione_inizio(text, text);
--    drop function public.fn_partner_moderazione_blocca(text, text);
--    drop function public.fn_partner_moderazione_annulla(text, text);
--    drop function public.fn_partner_moderazione_applica(text, text, uuid, text);
--    drop function public.fn_partner_moderazione_autore(text, text);
--    drop function public.fn_partner_admin_verifica(uuid);
-- 3) drop table public.company_identita_stato;      -- si perdono le verifiche
--    drop table public.company_identita_verifiche;  -- (esportare prima il registro)
--    drop function public.fn_identita_revoca_su_cambio_azienda();
--    drop function public.fn_identita_stato_protetto();
--    drop function public.fn_identita_verifiche_readonly();
--    Le call e i profili sospesi restano sospesi (ripristinarli prima, se serve).
-- 4) alter table public.partner_segnalazioni    -- esportare prima decisioni e
--      drop constraint ps_effetto_revocato_coerente,
--      drop constraint ps_ricorso_deciso_coerente, -- ricorsi (DSA, Q22)
--      drop constraint ps_ricorso_coerente,
--      drop constraint ps_sor_coerente,
--      drop constraint ps_decisione_coerente,
--      drop constraint ps_ricorso_motivazione_check,
--      drop constraint ps_ricorso_esito_check,
--      drop constraint ps_ricorso_testo_check,
--      drop constraint ps_sor_testo_check,
--      drop constraint ps_motivazione_check,
--      drop constraint ps_decisione_check,
--      drop column ricorso_deciso_at, drop column ricorso_deciso_da,
--      drop column ricorso_motivazione, drop column ricorso_esito,
--      drop column ricorso_at, drop column ricorso_da_user_id,
--      drop column ricorso_testo, drop column deciso_at, drop column deciso_da,
--      drop column sor_testo, drop column motivazione, drop column decisione,
--      drop column autore_company_profile_id, drop column effetto_revocato_at;
--    Le righe decise restano con stato decisa | ricorso_*: riportarle a
--    ricevuta se il backend precedente deve ripresentarle.
-- 5) Consulti dalla call: annullare prima quelli aperti (un consulto AI-check
--    e uno da call aperti sullo stesso bando farebbero fallire l'indice
--    originale), poi
--    drop index public.consultation_requests_one_open_call;
--    drop index public.consultation_requests_one_open;
--    create unique index consultation_requests_one_open
--      on public.consultation_requests (family_parent_id, bando_id)
--      where stato = 'nuova';
--    drop index public.consultation_requests_call_idx;
--    alter table public.consultation_requests drop column partner_call_id;
-- Le righe di audit_log (moderazione.*, admin.partner_call_*, identita.*)
-- restano.
-- ============================================================================
