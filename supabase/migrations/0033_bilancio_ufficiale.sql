-- ============================================================================
-- BandoFit — DB primario, migration 0033: BILANCIO UFFICIALE on-demand
-- (partenariati WP2, piano docs/partenariati.md §2.2 B10, §3 riga 0033, §13
-- Q3/Q4/Q5).
--
-- Il bilancio ottico di openapi (Visure Camerali) costa alla piattaforma a
-- ogni richiesta: lo paga l'addon consumabile «bilancio-ufficiale».
--   1) addons.sempre_a_pagamento + CHECK: un addon con un costo esterno non
--      può essere ATTIVO se non è consumabile a importo > 0 (nessun percorso,
--      nemmeno la console admin, lo rende gratis);
--   2) addon_ledger.request_id ora può essere anche una richiesta di bilancio
--      (i refund automatici diventano reali: Q5);
--   3) company_bilancio_richieste — una riga per richiesta al provider, con la
--      macchina a stati (una sola aperta per azienda) e i dati per i tetti;
--   4) company_bilancio_documenti — PDF (≤ 8 MB) e XBRL (≤ 10 MB) conservati
--      in bytea, cancellati a cascata con richiesta e azienda (Q4);
--   5) seed richiamabile dell'addon, creato INATTIVO (prezzo e attivazione li
--      fa l'admin da AdminAddon: Q3);
--   6) RPC di creazione (tetti + consumo ATOMICO), di chiusura (condizionata,
--      con rimborso una sola volta) e di claim del poll (un solo vincitore).
--   Le transizioni non terminali le fa il backend con update condizionati.
--
-- ADDITIVA: il backend attuale continua a funzionare tra migration e deploy
-- (colonna nuova con default, tabelle e funzioni nuove, nessuna firma
-- esistente cambiata). Da eseguire IN UN'UNICA TRANSAZIONE (begin; ...
-- commit;). Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Catalogo: addon con costo esterno, mai attivo gratis.
--    Il default false lascia valide tutte le righe esistenti.
-- ----------------------------------------------------------------------------
alter table public.addons
  add column sempre_a_pagamento boolean not null default false;

alter table public.addons add constraint addons_sempre_a_pagamento_coerente check (
  not sempre_a_pagamento
  or not is_active
  or (tipo_fruizione = 'consumabile' and tipo_prezzo = 'importo' and prezzo > 0)
);

comment on column public.addons.sempre_a_pagamento is
  'true = l''addon copre un costo esterno (es. bilancio ottico openapi): può essere attivo SOLO se consumabile, tipo_prezzo importo e prezzo > 0 (CHECK addons_sempre_a_pagamento_coerente) e il suo consumo non ha bypass gratuiti. Lo imposta la migration, non la console.';

-- ----------------------------------------------------------------------------
-- 2) Ledger: il request_id copre anche le richieste di bilancio. Dalla 0033
--    il tipo refund è emesso (rimborso automatico, una volta per richiesta).
-- ----------------------------------------------------------------------------
comment on column public.addon_ledger.request_id is
  'Richiesta che ha causato consume/refund: consultation_requests.id oppure company_bilancio_richieste.id (uuid, SENZA FK: il registro sopravvive al cascade dell''azienda).';

comment on table public.addon_ledger is
  'Registro APPEND-ONLY dei movimenti addon (il trigger vieta UPDATE/DELETE anche al service_role). Il saldo di user_addon_inventory è sum(delta). Idempotenza via indici parziali UNIQUE. Il tipo refund è emesso solo da fn_bilancio_richiesta_chiudi (rimborso automatico del bilancio ufficiale, al più uno per richiesta, 0033); i consulti non hanno rimborso automatico.';

-- ----------------------------------------------------------------------------
-- 3) Richieste di bilancio ufficiale. family_parent_id = owner da cui si
--    consuma l'unità, richiesto_da = attore: entrambi SENZA FK (come
--    ai_checks); la riga muore col cascade dell'azienda.
--    Stati: aperti in_invio | in_lavorazione | esito_ignoto; terminali
--    completata | non_disponibile | annullata | errore (solo via
--    fn_bilancio_richiesta_chiudi).
-- ----------------------------------------------------------------------------
create table public.company_bilancio_richieste (
  id                   uuid primary key default gen_random_uuid(),
  company_profile_id   uuid not null
                         references public.company_profiles (id) on delete cascade,
  family_parent_id     uuid not null,
  richiesto_da         uuid not null,
  partita_iva          text not null check (partita_iva ~ '^[0-9]{11}$'),
  anno_richiesto       smallint check (anno_richiesto between 2000 and 2100),
  anno_bilancio        smallint check (anno_bilancio between 1990 and 2100),
  stato                text not null default 'in_invio'
    constraint cbr_stato_check
    check (stato in ('in_invio', 'in_lavorazione', 'esito_ignoto', 'completata',
                     'non_disponibile', 'annullata', 'errore')),
  stato_provider       text,
  provider_request_id  text,
  errore_codice        text
    constraint cbr_errore_codice_check
    check (errore_codice in ('bilancio_non_disponibile', 'forma_non_ammessa',
                             'identificativo_non_valido', 'credito_provider',
                             'non_inviata', 'errore_provider', 'scaduta',
                             'esito_ignoto_scaduto')),
  xbrl_esito           text
    constraint cbr_xbrl_esito_check
    check (xbrl_esito in ('ok', 'assente', 'firmato_non_leggibile', 'non_valido',
                          'consolidato', 'cf_non_corrispondente', 'troppo_grande')),
  avvisi               jsonb not null default '[]'::jsonb
                         check (jsonb_typeof(avvisi) = 'array'),
  addon_id             bigint not null references public.addons (id),
  addon_prezzo         numeric(10,2) not null,
  costo_provider_cents integer not null default 0 check (costo_provider_cents >= 0),
  sandbox              boolean not null default false,
  ultimo_poll_at       timestamptz,
  inviata_at           timestamptz,
  completata_at        timestamptz,
  rimborsata_at        timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  -- In lavorazione solo con l'id del provider (serve al poll).
  constraint cbr_provider_id check (stato <> 'in_lavorazione' or provider_request_id is not null),
  -- Il rimborso esiste solo su una richiesta chiusa senza bilancio.
  constraint cbr_rimborso_terminale check (
    rimborsata_at is null or stato in ('non_disponibile', 'annullata', 'errore')),
  -- Bersaglio della FK composta dei documenti (stessa azienda della richiesta).
  constraint cbr_id_azienda_uniq unique (id, company_profile_id)
);

comment on table public.company_bilancio_richieste is
  'Richieste di bilancio ufficiale (bilancio ottico openapi) per azienda: una sola aperta per azienda (cbr_una_aperta). Creata da fn_bilancio_richiesta_crea (consumo atomico di 1 unità dell''addon), chiusa SOLO da fn_bilancio_richiesta_chiudi; le transizioni non terminali sono update condizionati del backend.';
comment on column public.company_bilancio_richieste.family_parent_id is
  'Owner dell''inventario da cui è stata consumata l''unità (senza FK). Base del tetto giornaliero per owner.';
comment on column public.company_bilancio_richieste.richiesto_da is
  'Utente che ha fatto la richiesta (owner o membro con permesso di modifica; senza FK). Riceve la notifica.';
comment on column public.company_bilancio_richieste.anno_richiesto is
  'Anno di chiusura chiesto al provider; NULL = ultimo disponibile.';
comment on column public.company_bilancio_richieste.anno_bilancio is
  'Anno dell''esercizio effettivamente consegnato (dall''XBRL).';
comment on column public.company_bilancio_richieste.stato_provider is
  'Ultimo stato letto dal provider (testo libero, es. «Dati disponibili»).';
comment on column public.company_bilancio_richieste.provider_request_id is
  'Id della richiesta presso openapi (unico). Mai esposto al client.';
comment on column public.company_bilancio_richieste.errore_codice is
  'Perché la richiesta è finita senza bilancio (vocabolario chiuso). Decide il rimborso automatico lato backend (Q5).';
comment on column public.company_bilancio_richieste.xbrl_esito is
  'Esito della lettura dell''XBRL su una richiesta completata: ok | assente | firmato_non_leggibile | non_valido | consolidato | cf_non_corrispondente | troppo_grande.';
comment on column public.company_bilancio_richieste.addon_prezzo is
  'Prezzo di catalogo dell''addon al momento della richiesta (snapshot).';
comment on column public.company_bilancio_richieste.costo_provider_cents is
  'Costo sostenuto presso il provider (0 finché la POST non è accettata).';
comment on column public.company_bilancio_richieste.ultimo_poll_at is
  'Ultimo claim del poll (fn_bilancio_richiesta_claim_poll): un solo poller alla volta.';
comment on column public.company_bilancio_richieste.completata_at is
  'Quando la richiesta è stata chiusa (qualunque stato terminale).';
comment on column public.company_bilancio_richieste.rimborsata_at is
  'Quando l''unità è stata restituita automaticamente (refund nel ledger, al più una volta).';

create trigger trg_company_bilancio_richieste_updated_at
  before update on public.company_bilancio_richieste
  for each row execute function public.set_updated_at();

-- Una sola richiesta APERTA per azienda (23505 → bilancio_in_corso nella RPC).
create unique index cbr_una_aperta on public.company_bilancio_richieste (company_profile_id)
  where stato in ('in_invio', 'in_lavorazione', 'esito_ignoto');
-- Un id del provider appartiene a una sola richiesta (riconciliazione).
create unique index cbr_provider_id_uniq on public.company_bilancio_richieste (provider_request_id)
  where provider_request_id is not null;
-- Storico per azienda (lista, più recenti prima).
create index cbr_company_idx on public.company_bilancio_richieste (company_profile_id, created_at desc);
-- Tetto giornaliero della piattaforma.
create index cbr_created_idx on public.company_bilancio_richieste (created_at);
-- Tetto giornaliero per owner.
create index cbr_owner_idx on public.company_bilancio_richieste (family_parent_id, created_at);
-- Failsafe sulle richieste aperte.
create index cbr_aperte_idx on public.company_bilancio_richieste (stato, created_at)
  where stato in ('in_invio', 'in_lavorazione', 'esito_ignoto');

-- ----------------------------------------------------------------------------
-- 4) Documenti conservati (Q4): PDF ≤ 8 MB, XBRL ≤ 10 MB; il verbale non si
--    conserva. La FK composta lega il documento alla STESSA azienda della
--    richiesta; la FK sull'azienda porta via i documenti anche da sola.
--    dimensione = byte reali del contenuto (i tetti valgono sul contenuto).
-- ----------------------------------------------------------------------------
create table public.company_bilancio_documenti (
  id                 uuid primary key default gen_random_uuid(),
  richiesta_id       uuid not null,
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  tipo               text not null check (tipo in ('pdf', 'xbrl')),
  nome_file          text not null check (length(nome_file) between 1 and 200),
  dimensione         integer not null,
  sha256             text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  contenuto          bytea not null,
  created_at         timestamptz not null default now(),
  constraint cbd_richiesta_fk foreign key (richiesta_id, company_profile_id)
    references public.company_bilancio_richieste (id, company_profile_id) on delete cascade,
  constraint cbd_richiesta_tipo_uniq unique (richiesta_id, tipo),
  constraint cbd_dimensione_check check (
    (tipo = 'pdf'  and dimensione between 1 and 8388608)
    or (tipo = 'xbrl' and dimensione between 1 and 10485760)),
  constraint cbd_dimensione_reale check (octet_length(contenuto) = dimensione)
);

comment on table public.company_bilancio_documenti is
  'File del bilancio ufficiale conservati (Q4): al più un PDF e un XBRL per richiesta. Scritti dal backend con upsert on conflict (richiesta_id, tipo) do nothing; scaricati solo dal backend con autorizzazione live. Cancellati a cascata con richiesta e azienda.';
comment on column public.company_bilancio_documenti.nome_file is
  'Nome generato dal server (es. bilancio-2024.pdf), mai quello dell''archivio del provider.';
comment on column public.company_bilancio_documenti.dimensione is
  'Byte del contenuto (= octet_length(contenuto)): PDF 1..8388608, XBRL 1..10485760.';
comment on column public.company_bilancio_documenti.sha256 is
  'SHA-256 esadecimale minuscolo del contenuto.';
comment on column public.company_bilancio_documenti.contenuto is
  'Il file. L''XBRL si conserva sempre (ri-parsing gratuito); il PDF solo entro 8 MB.';

-- ----------------------------------------------------------------------------
-- 5) Seed richiamabile dell'addon (pattern fn_canonizza_addon_0030): il
--    harness di test gira su DB vuoto e la invoca dopo aver inscenato le
--    righe. Se la riga esiste la marca sempre_a_pagamento e consumabile (se
--    in quel momento è attiva ma non a pagamento la disattiva prima, con un
--    WARNING); altrimenti la crea INATTIVA a prezzo 0: prezzo e attivazione
--    li fa l'admin da AdminAddon. Nome, descrizione e prezzo di una riga
--    esistente non si toccano.
-- ----------------------------------------------------------------------------
create or replace function public.fn_seed_addon_bilancio_ufficiale_0033()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_addon       public.addons%rowtype;
  v_disattivare boolean := false;
begin
  select * into v_addon from public.addons where slug = 'bilancio-ufficiale' for update;

  if found then
    if v_addon.is_active
       and not (v_addon.tipo_prezzo = 'importo' and v_addon.prezzo > 0) then
      v_disattivare := true;
      raise warning 'seed 0033: l''addon bilancio-ufficiale era attivo ma non a pagamento (tipo_prezzo %, prezzo %): DISATTIVATO. Fissare un prezzo e riattivarlo da AdminAddon',
        v_addon.tipo_prezzo, v_addon.prezzo;
    end if;

    -- Un solo UPDATE: il CHECK vede già la riga disattivata.
    update public.addons
    set is_active          = is_active and not v_disattivare,
        sempre_a_pagamento = true,
        tipo_fruizione     = 'consumabile'
    where id = v_addon.id;

    return jsonb_build_object('creati', 0, 'aggiornati', 1,
                              'disattivati', case when v_disattivare then 1 else 0 end);
  end if;

  insert into public.addons
    (nome, slug, descrizione, prezzo, tipo_prezzo, is_active, tipo_fruizione,
     sempre_a_pagamento, ordering)
  values
    ('Bilancio ufficiale', 'bilancio-ufficiale',
     'Il bilancio depositato in Camera di Commercio per la tua azienda: il PDF da scaricare e i dati del bilancio letti dal file ufficiale, che aggiornano i bilanci del tuo profilo. Ogni unità vale una richiesta.',
     0, 'importo', false, 'consumabile', true, 300);

  return jsonb_build_object('creati', 1, 'aggiornati', 0, 'disattivati', 0);
end;
$$;

comment on function public.fn_seed_addon_bilancio_ufficiale_0033() is
  'Seed idempotente dell''addon bilancio-ufficiale: riga esistente → sempre_a_pagamento e consumabile (disattivata con WARNING se attiva e non a pagamento); assente → creata INATTIVA a prezzo 0. Ritorna {creati, aggiornati, disattivati}. Interna.';

select public.fn_seed_addon_bilancio_ufficiale_0033();

-- Verifica: la riga deve esistere marcata; se è inattiva l'admin deve ancora
-- fissare il prezzo e attivarla → WARNING, non abort (il DB del harness è
-- vuoto e in produzione la riga nasce qui).
do $$
declare
  v_addon public.addons%rowtype;
begin
  select * into v_addon from public.addons where slug = 'bilancio-ufficiale';
  if not found or not v_addon.sempre_a_pagamento or v_addon.tipo_fruizione <> 'consumabile' then
    raise exception 'seed 0033: addon bilancio-ufficiale assente o non marcato sempre_a_pagamento';
  end if;
  if not v_addon.is_active then
    raise warning 'seed 0033: l''addon bilancio-ufficiale è INATTIVO: fissare il prezzo (consigliato ≥ 7,90 € + IVA) e attivarlo da AdminAddon';
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 6) fn_bilancio_richiesta_crea — tetti + insert + consumo ATOMICI.
--    p_payload: company_profile_id, family_parent_id, richiesto_da,
--    partita_iva, anno_richiesto (null = ultimo), addon_id, sandbox,
--    max_piattaforma, max_owner. I tetti mancanti o < 1 chiudono (fail-closed).
--    Il consumo avviene SEMPRE (sempre_a_pagamento: nessun bypass gratuito).
--    Ritorna {richiesta, quantita_residua}.
--    Detail: bilanci_limite_piattaforma | owner_not_found |
--    bilanci_limite_owner | addon_not_available | company_not_found |
--    bilancio_in_corso | addon_credit_esaurito.
-- ----------------------------------------------------------------------------
create or replace function public.fn_bilancio_richiesta_crea(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner   uuid    := (p_payload ->> 'family_parent_id')::uuid;
  v_company uuid    := (p_payload ->> 'company_profile_id')::uuid;
  v_max_pf  integer := (p_payload ->> 'max_piattaforma')::integer;
  v_max_own integer := (p_payload ->> 'max_owner')::integer;
  v_addon   public.addons%rowtype;
  v_req     public.company_bilancio_richieste%rowtype;
  v_n       bigint;
  v_qty     integer;
begin
  -- Un solo creatore alla volta su tutta la piattaforma (volumi minimi): il
  -- conteggio del tetto non ha finestre di check-then-act.
  perform pg_advisory_xact_lock(hashtext('fn_bilancio_richiesta_crea'));

  select count(*) into v_n
  from public.company_bilancio_richieste
  where created_at > now() - interval '24 hours';
  if v_n >= greatest(coalesce(v_max_pf, 0), 0) then
    raise exception 'Richieste di bilancio sospese: raggiunto il tetto giornaliero della piattaforma'
      using detail = 'bilanci_limite_piattaforma';
  end if;

  -- Lock dell'owner (convenzione 0023/0024): serializza con archiviazione e
  -- creazione delle aziende.
  perform 1 from public.profiles where id = v_owner for update;
  if not found then
    raise exception 'Owner non trovato' using detail = 'owner_not_found';
  end if;

  select count(*) into v_n
  from public.company_bilancio_richieste
  where family_parent_id = v_owner and created_at > now() - interval '24 hours';
  if v_n >= greatest(coalesce(v_max_own, 0), 0) then
    raise exception 'Raggiunto il numero di bilanci ufficiali richiedibili oggi'
      using detail = 'bilanci_limite_owner';
  end if;

  select * into v_addon from public.addons
  where id = (p_payload ->> 'addon_id')::bigint and is_active and sempre_a_pagamento;
  if not found then
    raise exception 'Addon non disponibile' using detail = 'addon_not_available';
  end if;

  perform 1 from public.company_profiles
  where id = v_company and parent_id = v_owner
    and deleted_at is null and archived_at is null;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;

  begin
    insert into public.company_bilancio_richieste
      (company_profile_id, family_parent_id, richiesto_da, partita_iva, anno_richiesto,
       addon_id, addon_prezzo, sandbox)
    values
      (v_company, v_owner,
       (p_payload ->> 'richiesto_da')::uuid,
       p_payload ->> 'partita_iva',
       nullif(p_payload ->> 'anno_richiesto', '')::smallint,
       v_addon.id, v_addon.prezzo,
       coalesce((p_payload ->> 'sandbox')::boolean, false))
    returning * into v_req;
  exception when unique_violation then
    -- 23505 di cbr_una_aperta: dentro una RPC arriverebbe come 502.
    raise exception 'C''è già una richiesta di bilancio in corso per questa azienda'
      using detail = 'bilancio_in_corso';
  end;

  -- Consumo di 1 unità: senza saldo l'eccezione (addon_credit_esaurito)
  -- annulla anche l'insert. Una volta per richiesta: addon_ledger_consume_once.
  v_qty := public.fn_addon_apply_movement(
    v_owner, v_addon.id, 'consume', -1, null, v_req.id, v_req.richiesto_da, null);

  return jsonb_build_object('richiesta', to_jsonb(v_req), 'quantita_residua', v_qty);
end;
$$;

comment on function public.fn_bilancio_richiesta_crea(jsonb) is
  'Crea una richiesta di bilancio ufficiale in_invio consumando 1 unità dell''addon (atomico): tetto piattaforma e per owner nelle ultime 24 h (fail-closed), addon attivo e sempre_a_pagamento, azienda viva dell''owner, una sola aperta per azienda. Ritorna {richiesta, quantita_residua}. Detail: bilanci_limite_piattaforma | owner_not_found | bilanci_limite_owner | addon_not_available | company_not_found | bilancio_in_corso | addon_credit_esaurito.';

-- ----------------------------------------------------------------------------
-- 7) fn_bilancio_richiesta_chiudi — UNICO passaggio a uno stato terminale.
--    Update condizionato dagli stati aperti: una richiesta già chiusa non
--    cambia ({aggiornata: false}, idempotente). p_campi ammette solo
--    errore_codice, stato_provider, anno_bilancio, xbrl_esito, avvisi (le
--    chiavi assenti lasciano il valore attuale). Con p_rimborsa, se la
--    richiesta ha consumato un'unità, la restituisce (refund +1) una sola
--    volta. Ritorna {aggiornata, rimborsata, quantita_residua}.
--    Detail: stato_non_valido | stato_non_rimborsabile | campi_non_validi.
-- ----------------------------------------------------------------------------
create or replace function public.fn_bilancio_richiesta_chiudi(
  p_richiesta_id uuid,
  p_stato        text,
  p_campi        jsonb,
  p_rimborsa     boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ammessi  constant text[] := array[
    'errore_codice', 'stato_provider', 'anno_bilancio', 'xbrl_esito', 'avvisi'];
  v_campi    jsonb := coalesce(p_campi, '{}'::jsonb);
  v_rimborsa boolean := coalesce(p_rimborsa, false);
  v_req      public.company_bilancio_richieste%rowtype;
  v_consumo  public.addon_ledger%rowtype;
  v_qty      integer := null;
  v_rimborso boolean := false;
begin
  if p_stato is null
     or p_stato not in ('completata', 'non_disponibile', 'annullata', 'errore') then
    raise exception 'Stato di chiusura non valido: %', coalesce(p_stato, 'NULL')
      using detail = 'stato_non_valido';
  end if;
  if v_rimborsa and p_stato = 'completata' then
    raise exception 'Una richiesta completata non si rimborsa'
      using detail = 'stato_non_rimborsabile';
  end if;
  if jsonb_typeof(v_campi) <> 'object'
     or exists (select 1 from jsonb_object_keys(v_campi) as k where k <> all(v_ammessi)) then
    raise exception 'Campi di chiusura non validi' using detail = 'campi_non_validi';
  end if;

  update public.company_bilancio_richieste
  set stato          = p_stato,
      errore_codice  = case when v_campi ? 'errore_codice'
                            then v_campi ->> 'errore_codice' else errore_codice end,
      stato_provider = case when v_campi ? 'stato_provider'
                            then v_campi ->> 'stato_provider' else stato_provider end,
      anno_bilancio  = case when v_campi ? 'anno_bilancio'
                            then (v_campi ->> 'anno_bilancio')::smallint else anno_bilancio end,
      xbrl_esito     = case when v_campi ? 'xbrl_esito'
                            then v_campi ->> 'xbrl_esito' else xbrl_esito end,
      avvisi         = case when v_campi ? 'avvisi'
                            then v_campi -> 'avvisi' else avvisi end,
      completata_at  = now()
  where id = p_richiesta_id
    and stato in ('in_invio', 'in_lavorazione', 'esito_ignoto')
  returning * into v_req;

  if not found then
    -- Già chiusa (o inesistente): nessun effetto, nessun secondo rimborso.
    return jsonb_build_object('aggiornata', false, 'rimborsata', false,
                              'quantita_residua', null);
  end if;

  if v_rimborsa then
    -- Si restituisce solo ciò che è stato consumato, a chi l'ha pagato.
    select * into v_consumo from public.addon_ledger
    where tipo = 'consume' and request_id = v_req.id;
    if found and not exists (
      select 1 from public.addon_ledger where tipo = 'refund' and request_id = v_req.id
    ) then
      v_qty := public.fn_addon_apply_movement(
        v_consumo.user_id, v_consumo.addon_id, 'refund', 1, null, v_req.id, null,
        'rimborso automatico: ' || coalesce(v_req.errore_codice, v_req.stato));
      update public.company_bilancio_richieste
      set rimborsata_at = now()
      where id = v_req.id;
      v_rimborso := true;
    end if;
  end if;

  return jsonb_build_object('aggiornata', true, 'rimborsata', v_rimborso,
                            'quantita_residua', v_qty);
end;
$$;

comment on function public.fn_bilancio_richiesta_chiudi(uuid, text, jsonb, boolean) is
  'Chiude una richiesta APERTA di bilancio ufficiale (completata | non_disponibile | annullata | errore) applicando i campi ammessi di p_campi; con p_rimborsa restituisce l''unità consumata (refund +1, al più una volta). Già chiusa → {aggiornata: false}. Ritorna {aggiornata, rimborsata, quantita_residua}. Detail: stato_non_valido | stato_non_rimborsabile | campi_non_validi.';

-- ----------------------------------------------------------------------------
-- 8) fn_bilancio_richiesta_claim_poll — un solo poller alla volta per
--    richiesta aperta: vince chi aggiorna ultimo_poll_at (nullo o più vecchio
--    di p_min_secondi, minimo 1 s; NULL = 60 s).
-- ----------------------------------------------------------------------------
create or replace function public.fn_bilancio_richiesta_claim_poll(
  p_richiesta_id uuid,
  p_min_secondi  integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ok boolean;
begin
  update public.company_bilancio_richieste
  set ultimo_poll_at = now()
  where id = p_richiesta_id
    and stato in ('in_invio', 'in_lavorazione', 'esito_ignoto')
    and (ultimo_poll_at is null
         or ultimo_poll_at < now() - make_interval(
              secs => greatest(coalesce(p_min_secondi, 60), 1)))
  returning true into v_ok;
  return coalesce(v_ok, false);
end;
$$;

comment on function public.fn_bilancio_richiesta_claim_poll(uuid, integer) is
  'Claim del poll di una richiesta aperta: true (e ultimo_poll_at = now()) se nessuno l''ha presa negli ultimi p_min_secondi; false altrimenti o se la richiesta è chiusa. Un solo vincitore anche in concorrenza.';

-- ----------------------------------------------------------------------------
-- 9) Le transizioni non terminali (in_invio → in_lavorazione con
--    provider_request_id, → esito_ignoto, esito_ignoto → in_lavorazione) le
--    fa il backend via PostgREST con update condizionati sullo stato di
--    partenza (.eq('stato', da)): un solo vincitore per costruzione.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 10) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--     esplicite su ogni tabella nuova; nessuna funzione nuova (seed compreso)
--     eseguibile dai ruoli esposti (Supabase concede EXECUTE di default).
-- ----------------------------------------------------------------------------
alter table public.company_bilancio_richieste enable row level security;
alter table public.company_bilancio_documenti enable row level security;

revoke all on public.company_bilancio_richieste from anon, authenticated;
revoke all on public.company_bilancio_documenti from anon, authenticated;

revoke execute on function public.fn_seed_addon_bilancio_ufficiale_0033()
  from public, anon, authenticated;
revoke execute on function public.fn_bilancio_richiesta_crea(jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_bilancio_richiesta_chiudi(uuid, text, jsonb, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_bilancio_richiesta_claim_poll(uuid, integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0033 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente: quella nuova legge le tabelle e chiama le RPC).
-- PRECONDIZIONE: nessuna richiesta aperta (select count(*) from
-- public.company_bilancio_richieste where stato in ('in_invio',
-- 'in_lavorazione', 'esito_ignoto') = 0): le unità consumate da richieste
-- aperte andrebbero restituite a mano con il grant admin.
-- 1) drop function fn_bilancio_richiesta_claim_poll(uuid, integer);
--    drop function fn_bilancio_richiesta_chiudi(uuid, text, jsonb, boolean);
--    drop function fn_bilancio_richiesta_crea(jsonb);
--    drop function fn_seed_addon_bilancio_ufficiale_0033();
-- 2) drop table public.company_bilancio_documenti;
--    drop table public.company_bilancio_richieste;
--    (si perdono PDF e XBRL già pagati: esportarli prima se servono. Il
--    ledger resta coerente: consume/refund sono append-only e senza FK.)
-- 3) alter table public.addons drop constraint addons_sempre_a_pagamento_coerente;
--    alter table public.addons drop column sempre_a_pagamento;
--    L'addon bilancio-ufficiale NON si elimina (gli addon si disattivano:
--    0009): update public.addons set is_active = false
--    where slug = 'bilancio-ufficiale';
-- 4) Commenti del ledger: comment on column public.addon_ledger.request_id is
--    null; commento della tabella: ripristinare quello della 0028 §4 (verbatim).
-- ============================================================================
