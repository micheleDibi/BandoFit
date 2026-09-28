-- ============================================================================
-- BandoFit — DB primario, migration 0032: BILANCI STRUTTURATI all'import
-- (partenariati WP1, piano docs/partenariati.md §2.2 B1-B5, §3 riga 0032).
--
-- Oggi l'import salva solo il payload IT-full (company_data.raw): un solo
-- esercizio, con EBITDA e utile letti da percorsi sbagliati. Qui nasce il
-- modello dei bilanci per esercizio:
--   1) company_import_drafts + esito IT-advanced in staging e azienda del
--      draft (guardia B4: un draft pagato per A non si conferma su B);
--   2) company_financials_stato  — 1:1 con l'azienda: esito dell'ultimo
--      recupero IT-advanced, payload grezzo (mai al client), versione del
--      mapping (rimappatura pigra e gratuita, B5);
--   3) company_financials_fonti  — valori NORMALIZZATI per fonte, una riga per
--      (azienda, anno, fonte): l'input della fusione;
--   4) company_financials        — la riga FUSA per esercizio, quella che
--      leggono UI, AI-check e regole: per ogni campo vince il primo valore non
--      nullo nell'ordine xbrl (3) > it_full (2) > it_advanced (1), ricalcolato
--      da TUTTE le fonti a ogni scrittura (il risultato non dipende
--      dall'ordine di arrivo). L'arbitro è fn_bilanci_registra_fonte, sotto
--      FOR NO KEY UPDATE della company_profiles; il gemello Python è
--      bilanci_mapping.unisci_fonti (casi condivisi in
--      backend/tests/fixtures/bilanci/precedenza_casi.json);
--   5) lock di import con TOKEN (Q23 iii): il rilascio cancella solo il lock
--      di chi lo detiene. ADDITIVO: fn_acquire_import_lock /
--      fn_release_import_lock restano identiche per il backend già in
--      produzione fino al deploy;
--   6) quota giornaliera FAIL-CLOSED delle chiamate openapi a pagamento per
--      owner (Q10): 3 × aziende gestibili, giorno solare Europe/Rome.
--
-- ADDITIVA: il backend attuale continua a funzionare tra migration e deploy
-- (colonne nuove nullable o con default, nessuna firma esistente cambiata).
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;). Rollback
-- documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Draft dell'import: esito IT-advanced in staging + azienda del draft.
--    advanced_raw esiste solo con esito 'ok' (coalesce: un esito NULL con il
--    raw valorizzato NON passa). company_profile_id SENZA FK: il draft è
--    staging per owner e può precedere la creazione dell'azienda (primo
--    import); il backend lo confronta con l'azienda attiva nel riuso e nella
--    conferma (409 draft_mismatch).
-- ----------------------------------------------------------------------------
alter table public.company_import_drafts
  add column advanced_raw        jsonb,
  add column advanced_esito      text
    constraint company_import_drafts_advanced_esito_check
    check (advanced_esito in ('ok', 'non_disponibili', 'errore', 'timeout',
                              'saltato', 'mismatch')),
  add column advanced_motivo     text
    constraint company_import_drafts_advanced_motivo_check
    check (advanced_motivo in ('nessun_bilancio', 'forma_senza_bilancio',
                               'errore_provider', 'esito_incerto',
                               'tempo_insufficiente', 'dati_non_corrispondenti',
                               'non_richiesto', 'piva_diversa')),
  add column advanced_tentato_at timestamptz,
  add column company_profile_id  uuid,
  add constraint cid_advanced_raw_solo_ok
    check (advanced_raw is null or coalesce(advanced_esito = 'ok', false));

comment on column public.company_import_drafts.advanced_raw is
  'Payload IT-advanced (data[0]) già pagato, solo con advanced_esito = ''ok''. Mai esposto al client.';
comment on column public.company_import_drafts.advanced_esito is
  'Esito della chiamata IT-advanced dell''anteprima: ok | non_disponibili | errore | timeout | saltato | mismatch. NULL = draft precedente alla 0032 (trattato come non_richiesto).';
comment on column public.company_import_drafts.advanced_motivo is
  'Motivo dell''esito IT-advanced (vocabolario chiuso MotivoBilanci).';
comment on column public.company_import_drafts.advanced_tentato_at is
  'Quando è partita la chiamata IT-advanced a pagamento (base del cooldown di «Recupera i bilanci»).';
comment on column public.company_import_drafts.company_profile_id is
  'Azienda attiva al momento dell''anteprima (NULL = primo import, azienda non ancora creata). Senza FK: staging per owner. Riuso e conferma del draft richiedono che coincida con l''azienda attiva.';

-- ----------------------------------------------------------------------------
-- 2) Stato del recupero pluriennale IT-advanced (1:1 con l'azienda).
-- ----------------------------------------------------------------------------
create table public.company_financials_stato (
  company_profile_id   uuid primary key
                         references public.company_profiles (id) on delete cascade,
  advanced_esito       text
    constraint company_financials_stato_advanced_esito_check
    check (advanced_esito in ('ok', 'non_disponibili', 'errore', 'timeout',
                              'saltato', 'mismatch')),
  advanced_motivo      text
    constraint company_financials_stato_advanced_motivo_check
    check (advanced_motivo in ('nessun_bilancio', 'forma_senza_bilancio',
                               'errore_provider', 'esito_incerto',
                               'tempo_insufficiente', 'dati_non_corrispondenti',
                               'non_richiesto', 'piva_diversa')),
  advanced_tentato_at  timestamptz,
  advanced_fetched_at  timestamptz,
  advanced_raw         jsonb,
  advanced_piva        text check (advanced_piva ~ '^[0-9]{11}$'),
  advanced_sandbox     boolean,
  advanced_fetch_count integer not null default 0 check (advanced_fetch_count >= 0),
  mapping_versione     smallint not null default 0 check (mapping_versione >= 0),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  constraint cfs_raw_coerente check (advanced_raw is null or advanced_fetched_at is not null)
);

comment on table public.company_financials_stato is
  'Stato dei bilanci dell''azienda (1:1): esito dell''ultimo tentativo IT-advanced, payload dell''ultimo successo e versione del mapping con cui sono state registrate le fonti.';
comment on column public.company_financials_stato.advanced_tentato_at is
  'Ultimo tentativo IT-advanced A PAGAMENTO (scritto prima della chiamata): base del cooldown di «Recupera i bilanci».';
comment on column public.company_financials_stato.advanced_fetched_at is
  'Ultimo recupero IT-advanced riuscito.';
comment on column public.company_financials_stato.advanced_raw is
  'data[0] dell''ultimo IT-advanced riuscito: fonte della rimappatura pigra gratuita. Mai esposto al client.';
comment on column public.company_financials_stato.mapping_versione is
  'Versione di bilanci_mapping con cui sono state registrate le fonti (MAPPING_BILANCI_VERSIONE). Più vecchia → rimappatura pigra alla lettura; si aggiorna solo dopo che TUTTE le registrazioni sono riuscite.';

create trigger trg_company_financials_stato_updated_at
  before update on public.company_financials_stato
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- 3) Valori normalizzati per fonte (input della fusione). Scritti SOLO da
--    fn_bilanci_registra_fonte. valori = oggetto {campo: "decimale"} con i
--    soli campi di bilancio non nulli, numeri come STRINGHE (nessun float).
-- ----------------------------------------------------------------------------
create table public.company_financials_fonti (
  company_profile_id uuid not null
                       references public.company_profiles (id) on delete cascade,
  anno               smallint not null check (anno between 1990 and 2100),
  fonte              text not null check (fonte in ('xbrl', 'it_full', 'it_advanced')),
  ruolo              text not null default 'corrente'
                       check (ruolo in ('corrente', 'comparativo')),
  data_chiusura      date,
  tipo_bilancio      text not null default 'ignoto'
                       check (tipo_bilancio in ('ordinario', 'abbreviato', 'micro', 'ignoto')),
  valori             jsonb not null check (jsonb_typeof(valori) = 'object'),
  riferimento        text not null
    constraint cff_riferimento_check
    check (riferimento in ('import', 'recupero', 'rimappatura')
           or riferimento ~ '^bilancio:[^[:space:]]{1,64}$'),
  fetched_at         timestamptz not null default now(),
  primary key (company_profile_id, anno, fonte),
  constraint cff_ruolo_solo_xbrl check (fonte = 'xbrl' or ruolo = 'corrente'),
  -- Anti-segnaposto anche per fonte: una riga senza alcun campo CORE
  -- produrrebbe una riga fusa che viola cf_non_segnaposto.
  constraint cff_non_segnaposto check (
    jsonb_strip_nulls(valori) ?| array['fatturato', 'valore_produzione',
                                       'risultato_esercizio', 'patrimonio_netto',
                                       'totale_attivo'])
);

comment on table public.company_financials_fonti is
  'Valori di bilancio normalizzati per (azienda, esercizio, fonte): input della fusione in company_financials. Scritti solo da fn_bilanci_registra_fonte.';
comment on column public.company_financials_fonti.ruolo is
  'corrente = bilancio dell''esercizio stesso; comparativo = colonna «anno precedente» di un''istanza XBRL successiva (solo fonte xbrl). Un comparativo non sostituisce mai un corrente.';
comment on column public.company_financials_fonti.valori is
  'Oggetto {campo: "decimale"} con i soli campi di bilancio non nulli; numeri come stringhe decimali canoniche (mai float).';
comment on column public.company_financials_fonti.riferimento is
  'Origine della registrazione: import | recupero | rimappatura | bilancio:<id richiesta> (WP2).';

-- ----------------------------------------------------------------------------
-- 4) Riga fusa per esercizio (ciò che legge l'app). Scritta SOLO da
--    fn_bilanci_ricalcola_anno. L'ordine delle 15 colonne di bilancio è
--    quello di CAMPI_BILANCIO (bilanci_mapping.py): il test DB lo confronta.
-- ----------------------------------------------------------------------------
create table public.company_financials (
  company_profile_id       uuid not null
                             references public.company_profiles (id) on delete cascade,
  anno                     smallint not null check (anno between 1990 and 2100),
  data_chiusura            date,
  fatturato                numeric(18,2),
  valore_produzione        numeric(18,2),
  risultato_esercizio      numeric(18,2),
  patrimonio_netto         numeric(18,2),
  capitale_sociale         numeric(18,2),
  totale_attivo            numeric(18,2),
  debiti_totali            numeric(18,2),
  disponibilita_liquide    numeric(18,2),
  ebitda                   numeric(18,2),
  ebit                     numeric(18,2),
  cash_flow                numeric(18,2),
  oneri_finanziari         numeric(18,2),
  dipendenti               numeric(10,2),
  costo_personale          numeric(18,2),
  retribuzione_media_lorda numeric(12,2),
  tipo_bilancio            text not null default 'ignoto'
                             check (tipo_bilancio in ('ordinario', 'abbreviato', 'micro', 'ignoto')),
  fonte_per_campo          jsonb not null default '{}'::jsonb
                             check (jsonb_typeof(fonte_per_campo) = 'object'),
  fetched_at               timestamptz not null default now(),
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  primary key (company_profile_id, anno),
  constraint cf_non_segnaposto check (
    coalesce(fatturato, valore_produzione, risultato_esercizio,
             patrimonio_netto, totale_attivo) is not null)
);

comment on table public.company_financials is
  'Bilancio FUSO per esercizio: per ogni campo il primo valore non nullo per rango di fonte (xbrl > it_full > it_advanced). Ricalcolato da fn_bilanci_ricalcola_anno a ogni registrazione; mai scritto a mano.';
comment on column public.company_financials.risultato_esercizio is
  'Utile (o perdita) d''esercizio. ATTENZIONE: da IT-advanced arriva come netWorth, che è l''UTILE e non il patrimonio netto.';
comment on column public.company_financials.patrimonio_netto is
  'Patrimonio netto: da IT-full ecofin.netWorth (solo l''ultimo esercizio) o dall''XBRL. IT-advanced NON lo fornisce.';
comment on column public.company_financials.ebitda is
  'MOL/EBITDA: da IT-full operatingResults.ebitda. In v1 non si deriva dall''XBRL (definizioni diverse).';
comment on column public.company_financials.dipendenti is
  'Dipendenti medi dell''esercizio (può essere frazionario).';
comment on column public.company_financials.fonte_per_campo is
  'Mappa {campo: fonte} dei soli campi non nulli: da quale fonte viene ciascun valore.';
comment on column public.company_financials.fetched_at is
  'Il più recente fetched_at tra le fonti dell''esercizio.';

create trigger trg_company_financials_updated_at
  before update on public.company_financials
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- 5) Ricalcolo della riga fusa di un esercizio. INTERNA (chiamata da
--    fn_bilanci_registra_fonte sotto il lock dell'azienda; il lock è ripreso
--    anche qui, così una chiamata isolata resta sicura).
--    Nessuna fonte per l'anno → la riga fusa sparisce.
-- ----------------------------------------------------------------------------
create or replace function public.fn_bilanci_ricalcola_anno(p_company_id uuid, p_anno smallint)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  -- Stesso ordine di CAMPI_BILANCIO in bilanci_mapping.py.
  v_campi   constant text[] := array[
    'fatturato', 'valore_produzione', 'risultato_esercizio', 'patrimonio_netto',
    'capitale_sociale', 'totale_attivo', 'debiti_totali', 'disponibilita_liquide',
    'ebitda', 'ebit', 'cash_flow', 'oneri_finanziari', 'dipendenti',
    'costo_personale', 'retribuzione_media_lorda'];
  v_campo   text;
  v_val     text;
  v_fonte   text;
  v_valori  jsonb := '{}'::jsonb;
  v_origine jsonb := '{}'::jsonb;
  v_data    date;
  v_tipo    text;
  v_fetched timestamptz;
begin
  perform 1 from public.company_profiles where id = p_company_id for no key update;

  if not exists (
    select 1 from public.company_financials_fonti
    where company_profile_id = p_company_id and anno = p_anno
  ) then
    delete from public.company_financials
    where company_profile_id = p_company_id and anno = p_anno;
    return;
  end if;

  -- Per ogni campo: il primo valore non nullo per rango decrescente.
  foreach v_campo in array v_campi loop
    select f.valori ->> v_campo, f.fonte
      into v_val, v_fonte
    from public.company_financials_fonti f
    where f.company_profile_id = p_company_id
      and f.anno = p_anno
      and f.valori ->> v_campo is not null
    order by case f.fonte when 'xbrl' then 3 when 'it_full' then 2 else 1 end desc
    limit 1;
    if found then
      v_valori  := v_valori  || jsonb_build_object(v_campo, v_val);
      v_origine := v_origine || jsonb_build_object(v_campo, v_fonte);
    end if;
  end loop;

  select f.data_chiusura into v_data
  from public.company_financials_fonti f
  where f.company_profile_id = p_company_id and f.anno = p_anno
    and f.data_chiusura is not null
  order by case f.fonte when 'xbrl' then 3 when 'it_full' then 2 else 1 end desc
  limit 1;

  select f.tipo_bilancio into v_tipo
  from public.company_financials_fonti f
  where f.company_profile_id = p_company_id and f.anno = p_anno
    and f.tipo_bilancio <> 'ignoto'
  order by case f.fonte when 'xbrl' then 3 when 'it_full' then 2 else 1 end desc
  limit 1;

  select max(f.fetched_at) into v_fetched
  from public.company_financials_fonti f
  where f.company_profile_id = p_company_id and f.anno = p_anno;

  insert into public.company_financials (
    company_profile_id, anno, data_chiusura,
    fatturato, valore_produzione, risultato_esercizio, patrimonio_netto,
    capitale_sociale, totale_attivo, debiti_totali, disponibilita_liquide,
    ebitda, ebit, cash_flow, oneri_finanziari, dipendenti,
    costo_personale, retribuzione_media_lorda,
    tipo_bilancio, fonte_per_campo, fetched_at
  )
  select p_company_id, p_anno, v_data,
         r.fatturato, r.valore_produzione, r.risultato_esercizio, r.patrimonio_netto,
         r.capitale_sociale, r.totale_attivo, r.debiti_totali, r.disponibilita_liquide,
         r.ebitda, r.ebit, r.cash_flow, r.oneri_finanziari, r.dipendenti,
         r.costo_personale, r.retribuzione_media_lorda,
         coalesce(v_tipo, 'ignoto'), v_origine, coalesce(v_fetched, now())
  from jsonb_populate_record(null::public.company_financials, v_valori) r
  on conflict (company_profile_id, anno) do update
    set data_chiusura            = excluded.data_chiusura,
        fatturato                = excluded.fatturato,
        valore_produzione        = excluded.valore_produzione,
        risultato_esercizio      = excluded.risultato_esercizio,
        patrimonio_netto         = excluded.patrimonio_netto,
        capitale_sociale         = excluded.capitale_sociale,
        totale_attivo            = excluded.totale_attivo,
        debiti_totali            = excluded.debiti_totali,
        disponibilita_liquide    = excluded.disponibilita_liquide,
        ebitda                   = excluded.ebitda,
        ebit                     = excluded.ebit,
        cash_flow                = excluded.cash_flow,
        oneri_finanziari         = excluded.oneri_finanziari,
        dipendenti               = excluded.dipendenti,
        costo_personale          = excluded.costo_personale,
        retribuzione_media_lorda = excluded.retribuzione_media_lorda,
        tipo_bilancio            = excluded.tipo_bilancio,
        fonte_per_campo          = excluded.fonte_per_campo,
        fetched_at               = excluded.fetched_at;
end;
$$;

comment on function public.fn_bilanci_ricalcola_anno(uuid, smallint) is
  'Ricalcola la riga fusa di company_financials per (azienda, anno) da tutte le fonti: per campo il primo non nullo per rango (xbrl > it_full > it_advanced); data_chiusura idem; tipo_bilancio = primo diverso da ignoto; fetched_at = max. Nessuna fonte → riga cancellata. Interna.';

-- ----------------------------------------------------------------------------
-- 6) L'arbitro: registra le righe di UNA fonte e ricalcola gli anni toccati.
--    p_righe = array di {anno, data_chiusura, tipo_bilancio, ruolo, valori}
--    (forma di bilanci_mapping.a_payload_rpc). Tutto o niente: una riga non
--    valida annulla l'intera chiamata.
--    p_sostituisci = true: le righe di QUELLA fonte con anni assenti da
--    p_righe vengono cancellate (mai per it_full nella rimappatura: B5).
--    Ritorna {"anni": [...]} = anni registrati (quelli di p_righe, crescenti).
--    Errori (detail): fonte_non_valida | azienda_non_trovata | righe_non_valide.
-- ----------------------------------------------------------------------------
create or replace function public.fn_bilanci_registra_fonte(
  p_company_id  uuid,
  p_fonte       text,
  p_righe       jsonb,
  p_riferimento text,
  p_sostituisci boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  -- Stesso ordine di CAMPI_BILANCIO / CAMPI_CORE in bilanci_mapping.py.
  v_campi   constant text[] := array[
    'fatturato', 'valore_produzione', 'risultato_esercizio', 'patrimonio_netto',
    'capitale_sociale', 'totale_attivo', 'debiti_totali', 'disponibilita_liquide',
    'ebitda', 'ebit', 'cash_flow', 'oneri_finanziari', 'dipendenti',
    'costo_personale', 'retribuzione_media_lorda'];
  v_core    constant text[] := array[
    'fatturato', 'valore_produzione', 'risultato_esercizio', 'patrimonio_netto',
    'totale_attivo'];
  -- Stringa decimale: segno facoltativo, niente spazi, NaN o Infinity;
  -- esponente ammesso ma corto (lo produce str(Decimal)).
  v_re_num  constant text := '^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]{1,2})?$';
  v_riga    jsonb;
  v_valori  jsonb;
  v_norm    jsonb;
  v_chiave  text;
  v_val     jsonb;
  v_num     numeric;
  v_anno_n  numeric;
  v_anno    smallint;
  v_ruolo   text;
  v_tipo    text;
  v_data    date;
  v_righe   jsonb := '[]'::jsonb;
  v_anni    smallint[] := '{}';
  v_rimossi smallint[] := '{}';
begin
  if p_fonte is null or p_fonte not in ('xbrl', 'it_full', 'it_advanced') then
    raise exception 'Fonte di bilancio non valida: %', coalesce(p_fonte, 'NULL')
      using detail = 'fonte_non_valida';
  end if;

  -- FOR NO KEY UPDATE (non FOR UPDATE): serializza le registrazioni della
  -- stessa azienda senza bloccare gli insert con FK verso di essa (FOR KEY
  -- SHARE), cioè il resto dell'import e le altre tabelle dell'azienda.
  perform 1 from public.company_profiles where id = p_company_id for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'azienda_non_trovata';
  end if;

  if p_righe is null or jsonb_typeof(p_righe) <> 'array' then
    raise exception 'Righe di bilancio non valide: serve un array'
      using detail = 'righe_non_valide';
  end if;

  -- 1) Validazione e normalizzazione di TUTTE le righe prima di scrivere.
  for v_riga in select value from jsonb_array_elements(p_righe) loop
    if jsonb_typeof(v_riga) <> 'object' then
      raise exception 'Righe di bilancio non valide: ogni riga deve essere un oggetto'
        using detail = 'righe_non_valide';
    end if;

    -- anno: numero intero 1990..2100, una sola riga per anno.
    if jsonb_typeof(v_riga -> 'anno') is distinct from 'number' then
      raise exception 'Righe di bilancio non valide: anno mancante o non numerico'
        using detail = 'righe_non_valide';
    end if;
    v_anno_n := (v_riga ->> 'anno')::numeric;
    if v_anno_n <> trunc(v_anno_n) or v_anno_n not between 1990 and 2100 then
      raise exception 'Righe di bilancio non valide: anno % non ammesso', v_anno_n
        using detail = 'righe_non_valide';
    end if;
    v_anno := v_anno_n::smallint;
    if v_anno = any(v_anni) then
      raise exception 'Righe di bilancio non valide: anno % ripetuto', v_anno
        using detail = 'righe_non_valide';
    end if;

    -- ruolo: corrente (default) | comparativo, quest'ultimo solo per xbrl.
    v_ruolo := coalesce(v_riga ->> 'ruolo', 'corrente');
    if v_ruolo not in ('corrente', 'comparativo')
       or (v_ruolo = 'comparativo' and p_fonte <> 'xbrl') then
      raise exception 'Righe di bilancio non valide: ruolo % non ammesso per la fonte %',
        v_ruolo, p_fonte
        using detail = 'righe_non_valide';
    end if;

    v_tipo := coalesce(v_riga ->> 'tipo_bilancio', 'ignoto');
    if v_tipo not in ('ordinario', 'abbreviato', 'micro', 'ignoto') then
      raise exception 'Righe di bilancio non valide: tipo_bilancio % non ammesso', v_tipo
        using detail = 'righe_non_valide';
    end if;

    -- data_chiusura: NULL o data ISO (YYYY-MM-DD) valida.
    v_data := null;
    if coalesce(jsonb_typeof(v_riga -> 'data_chiusura'), 'null') <> 'null' then
      if jsonb_typeof(v_riga -> 'data_chiusura') <> 'string'
         or (v_riga ->> 'data_chiusura') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' then
        raise exception 'Righe di bilancio non valide: data_chiusura non in formato ISO'
          using detail = 'righe_non_valide';
      end if;
      begin
        v_data := (v_riga ->> 'data_chiusura')::date;
      exception when invalid_datetime_format or datetime_field_overflow then
        raise exception 'Righe di bilancio non valide: data_chiusura % inesistente',
          v_riga ->> 'data_chiusura'
          using detail = 'righe_non_valide';
      end;
    end if;

    -- valori: solo campi di bilancio, stringhe decimali o null, entro la
    -- precisione della colonna fusa; almeno un campo CORE (anti-segnaposto).
    v_valori := v_riga -> 'valori';
    if v_valori is null or jsonb_typeof(v_valori) <> 'object' then
      raise exception 'Righe di bilancio non valide: valori deve essere un oggetto'
        using detail = 'righe_non_valide';
    end if;
    v_norm := '{}'::jsonb;
    for v_chiave, v_val in select key, value from jsonb_each(v_valori) loop
      if not (v_chiave = any(v_campi)) then
        raise exception 'Righe di bilancio non valide: campo sconosciuto %', v_chiave
          using detail = 'righe_non_valide';
      end if;
      continue when jsonb_typeof(v_val) = 'null';
      if jsonb_typeof(v_val) <> 'string' or (v_val #>> '{}') !~ v_re_num then
        raise exception 'Righe di bilancio non valide: % non è una stringa decimale', v_chiave
          using detail = 'righe_non_valide';
      end if;
      v_num := (v_val #>> '{}')::numeric;
      -- (CASE tra parentesi: l'IF di plpgsql si ferma al primo THEN.)
      if abs(round(v_num, 2)) >= (case v_chiave
                                    when 'dipendenti' then 1e8
                                    when 'retribuzione_media_lorda' then 1e10
                                    else 1e16
                                  end) then
        raise exception 'Righe di bilancio non valide: % fuori scala', v_chiave
          using detail = 'righe_non_valide';
      end if;
      v_norm := v_norm || jsonb_build_object(v_chiave, v_num::text);
    end loop;
    if not (v_norm ?| v_core) then
      raise exception 'Righe di bilancio non valide: anno % senza alcun valore principale (segnaposto)',
        v_anno
        using detail = 'righe_non_valide';
    end if;

    v_anni  := v_anni || v_anno;
    v_righe := v_righe || jsonb_build_array(jsonb_build_object(
      'anno', v_anno, 'ruolo', v_ruolo, 'tipo_bilancio', v_tipo,
      'data_chiusura', v_data, 'valori', v_norm));
  end loop;

  -- 2) Sostituzione: via gli anni di QUESTA fonte che non ci sono più.
  if coalesce(p_sostituisci, false) then
    with rimossi as (
      delete from public.company_financials_fonti
      where company_profile_id = p_company_id
        and fonte = p_fonte
        and anno <> all(v_anni)
      returning anno
    )
    select coalesce(array_agg(anno), '{}') into v_rimossi from rimossi;
  end if;

  -- 3) Upsert: un comparativo non sostituisce mai un corrente.
  for v_riga in select value from jsonb_array_elements(v_righe) loop
    insert into public.company_financials_fonti (
      company_profile_id, anno, fonte, ruolo, data_chiusura, tipo_bilancio,
      valori, riferimento, fetched_at
    )
    values (
      p_company_id, (v_riga ->> 'anno')::smallint, p_fonte, v_riga ->> 'ruolo',
      (v_riga ->> 'data_chiusura')::date, v_riga ->> 'tipo_bilancio',
      v_riga -> 'valori', p_riferimento, now()
    )
    on conflict (company_profile_id, anno, fonte) do update
      set ruolo         = excluded.ruolo,
          data_chiusura = excluded.data_chiusura,
          tipo_bilancio = excluded.tipo_bilancio,
          valori        = excluded.valori,
          riferimento   = excluded.riferimento,
          fetched_at    = excluded.fetched_at
      where not (company_financials_fonti.ruolo = 'corrente'
                 and excluded.ruolo = 'comparativo');
  end loop;

  -- 4) Ricalcolo di ogni anno toccato (registrati ∪ rimossi).
  for v_anno in select distinct a from unnest(v_anni || v_rimossi) as a order by a loop
    perform public.fn_bilanci_ricalcola_anno(p_company_id, v_anno);
  end loop;

  return jsonb_build_object(
    'anni', coalesce((select jsonb_agg(a order by a) from unnest(v_anni) as a), '[]'::jsonb));
end;
$$;

comment on function public.fn_bilanci_registra_fonte(uuid, text, jsonb, text, boolean) is
  'Registra le righe di una fonte di bilancio (xbrl | it_full | it_advanced) per l''azienda, sotto FOR NO KEY UPDATE della company_profiles, e ricalcola la riga fusa degli anni toccati. p_sostituisci cancella gli anni della fonte assenti da p_righe. Ritorna {"anni": [...]}. Detail: fonte_non_valida | azienda_non_trovata | righe_non_valide.';

-- ----------------------------------------------------------------------------
-- 7) Lock di import con TOKEN (Q23 iii). Il lock è condiviso (import,
--    conferma, verify_cf, AI-check, recupero bilanci): senza token un
--    detentore scaduto cancellava il lock di chi glielo aveva «rubato».
--    ADDITIVO: fn_acquire_import_lock / fn_release_import_lock restano
--    invariate (le usa il backend in produzione fino al deploy). Le righe
--    create dalla vecchia acquire ricevono un token casuale di default.
-- ----------------------------------------------------------------------------
alter table public.company_import_locks
  add column token uuid not null default gen_random_uuid();

comment on column public.company_import_locks.token is
  'Token del detentore: cambia a ogni acquisizione (anche al furto dopo la scadenza) e solo chi lo presenta può rilasciare il lock (fn_release_import_lock_token).';

create or replace function public.fn_acquire_import_lock_token(
  p_parent_id   uuid,
  p_ttl_seconds integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ttl   integer := greatest(1, least(coalesce(p_ttl_seconds, 120), 600));
  v_token uuid;
begin
  insert into public.company_import_locks (parent_id, expires_at, token)
  values (p_parent_id, now() + make_interval(secs => v_ttl), gen_random_uuid())
  on conflict (parent_id) do update
    set expires_at = excluded.expires_at,
        created_at = now(),
        token      = excluded.token
    where company_import_locks.expires_at < now()
  returning token into v_token;
  return v_token;
end;
$$;

comment on function public.fn_acquire_import_lock_token(uuid, integer) is
  'Come fn_acquire_import_lock (TTL limitato a 1..600 s, furto solo se scaduto) ma restituisce il token del nuovo detentore; NULL se un''operazione è già in corso.';

create or replace function public.fn_release_import_lock_token(p_parent_id uuid, p_token uuid)
returns void
language sql
security definer
set search_path = public
as $$
  delete from public.company_import_locks
  where parent_id = p_parent_id and token = p_token;
$$;

comment on function public.fn_release_import_lock_token(uuid, uuid) is
  'Rilascia il lock di import solo se p_token è quello del detentore attuale (un lock già scaduto e ripreso da altri resta intatto).';

-- ----------------------------------------------------------------------------
-- 8) Quota giornaliera FAIL-CLOSED delle chiamate openapi a pagamento (Q10):
--    per owner e giorno solare Europe/Rome, limite = p_per_azienda × aziende
--    gestibili (fn_effective_max_aziende, almeno 1). Il conteggio sale solo
--    se la prenotazione riesce; l'upsert condizionato è atomico (nessun
--    check-then-act). Il backend tratta ogni errore della RPC come «esaurita».
-- ----------------------------------------------------------------------------
create table public.openapi_quota_giornaliera (
  owner_id  uuid not null references public.profiles (id) on delete cascade,
  giorno    date not null,
  conteggio integer not null check (conteggio >= 0),
  primary key (owner_id, giorno)
);

comment on table public.openapi_quota_giornaliera is
  'Chiamate openapi a pagamento prenotate per owner e giorno (Europe/Rome): tetto fail-closed contro la spesa illimitata dall''import. Scritta solo da fn_openapi_prenota_operazione.';

create or replace function public.fn_openapi_prenota_operazione(
  p_owner       uuid,
  p_per_azienda integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_giorno date := (now() at time zone 'Europe/Rome')::date;
  v_limite bigint;
  v_ok     boolean;
begin
  -- Parametri insensati: fail-closed, nessuna prenotazione.
  if p_owner is null or p_per_azienda is null or p_per_azienda < 1 then
    return false;
  end if;

  v_limite := p_per_azienda::bigint
              * greatest(1, coalesce(public.fn_effective_max_aziende(p_owner), 1));

  insert into public.openapi_quota_giornaliera (owner_id, giorno, conteggio)
  values (p_owner, v_giorno, 1)
  on conflict (owner_id, giorno) do update
    set conteggio = openapi_quota_giornaliera.conteggio + 1
    where openapi_quota_giornaliera.conteggio < v_limite
  returning true into v_ok;

  return coalesce(v_ok, false);
end;
$$;

comment on function public.fn_openapi_prenota_operazione(uuid, integer) is
  'Prenota una chiamata openapi a pagamento per l''owner nel giorno Europe/Rome: true se prenotata, false se il tetto (p_per_azienda × aziende gestibili) è esaurito.';

-- ----------------------------------------------------------------------------
-- 9) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova eseguibile dai
--    ruoli esposti (Supabase concede EXECUTE di default).
-- ----------------------------------------------------------------------------
alter table public.company_financials_stato   enable row level security;
alter table public.company_financials_fonti   enable row level security;
alter table public.company_financials         enable row level security;
alter table public.openapi_quota_giornaliera  enable row level security;

revoke all on public.company_financials_stato  from anon, authenticated;
revoke all on public.company_financials_fonti  from anon, authenticated;
revoke all on public.company_financials        from anon, authenticated;
revoke all on public.openapi_quota_giornaliera from anon, authenticated;

revoke execute on function public.fn_bilanci_ricalcola_anno(uuid, smallint)
  from public, anon, authenticated;
revoke execute on function public.fn_bilanci_registra_fonte(uuid, text, jsonb, text, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_acquire_import_lock_token(uuid, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_release_import_lock_token(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_openapi_prenota_operazione(uuid, integer)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0032 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente: quella nuova usa le funzioni *_token e la quota).
-- 1) drop function fn_openapi_prenota_operazione(uuid, integer);
--    drop table public.openapi_quota_giornaliera;
-- 2) drop function fn_release_import_lock_token(uuid, uuid);
--    drop function fn_acquire_import_lock_token(uuid, integer);
--    alter table public.company_import_locks drop column token;
-- 3) drop function fn_bilanci_registra_fonte(uuid, text, jsonb, text, boolean);
--    drop function fn_bilanci_ricalcola_anno(uuid, smallint);
-- 4) drop table public.company_financials;
--    drop table public.company_financials_fonti;
--    drop table public.company_financials_stato;
--    (i bilanci si ricostruiscono gratis dalla rimappatura pigra solo finché
--    esistono company_data.raw e company_financials_stato.advanced_raw: il
--    drop dello stato perde il payload IT-advanced già pagato)
-- 5) alter table public.company_import_drafts
--      drop constraint cid_advanced_raw_solo_ok,
--      drop column company_profile_id,
--      drop column advanced_tentato_at,
--      drop column advanced_motivo,
--      drop column advanced_esito,
--      drop column advanced_raw;
-- ============================================================================
