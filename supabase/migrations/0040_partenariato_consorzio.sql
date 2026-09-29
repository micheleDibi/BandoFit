-- ============================================================================
-- BandoFit — DB primario, migration 0040: CONSORZIO DELLA CALL e dati del
-- validatore deterministico (WP8, piano docs/partenariati.md §2.1 T3-T5,
-- §2.8 V1-V3, §3 riga 0040, §7, §13 Q11/Q20/Q21).
--
--   1) partner_call_membri — il consorzio della call (V1): il creatore
--      (sempre presente per una call pubblicata), le aziende con candidatura
--      accettata e i membri ESTERNI inseriti dal creatore (Q20: nome, paese
--      ISO2, tipi soggetto, quota). Ruoli capofila | partner |
--      affiliated_entity | associated_partner (capofila unico tra i non
--      usciti), quota % (0,100], stati proposto ⇄ confermato → uscito →
--      proposto; transizioni SOLO tramite le RPC di questo file;
--   2) partner_call_documenti — checklist documentale per forma (V3), uno
--      stato per documento (i codici li valida il backend sul vocabolario);
--   3) partner_calls.validazione_esito / validazione_at /
--      copertura_gap_ratio — ultimo esito del validatore (calcolato in
--      Python, services/partenariato_validatore.py) persistito best-effort;
--   4) RPC: fn_partner_membro_aggiorna, fn_partner_membro_conferma,
--      fn_partner_membro_esci, fn_partner_membro_esterno,
--      fn_partner_documento_stato, fn_partner_call_validazione_salva
--      (interna), fn_partner_backfill_membri (richiamabile, eseguita qui);
--   5) ridefinizioni con la STESSA firma (una sola funzione per nome):
--      - fn_partner_call_aperta (0039): «azienda viva» uniforme, anche il
--        titolare deve essere attivo (come la controparte in fn_partner_decidi
--        e partenariato_indice.aziende_vive);
--      - fn_partner_esclusivita_violata (0039): gli impegni sul bando
--        comprendono i membri NON usciti delle altre call (la riga di un
--        membro, se c'è, prevale sulla candidatura accettata: chi è uscito non
--        è più impegnato) e il creatore di un'altra call non annullata che ha
--        ancora altri membri;
--      - fn_partner_decidi (0039): l'accettazione aggiunge la riga del
--        creatore se manca e quella di Y (proposto, posizione, quota della
--        posizione; riammette chi era uscito);
--      - fn_partner_call_pubblica (0039): alla pubblicazione crea la riga
--        del creatore. L'esclusività sui membri le arriva da
--        fn_partner_esclusivita_violata;
--      - fn_partner_call_sostituisci_posizioni (0039): una posizione con
--        membri non usciti del consorzio non si rimuove.
--
-- Chi agisce (T4, Q14): solo il titolare dell'azienda attiva (attore =
-- owner). Il creatore modifica ruoli, posizioni e quote, aggiunge gli
-- esterni, rimuove i membri e aggiorna i documenti; ogni azienda membro
-- conferma e lascia la propria riga; il creatore conferma la propria e quelle
-- degli esterni. Verso terzi il backend espone solo le proiezioni a
-- whitelist (partenariato_accesso): mai company_profile_id di altri.
--
-- ORDINE DEI LOCK (globale, quello della 0037 esteso dalla 0039):
--   owner (profiles FOR UPDATE) → azienda che agisce (company_profiles FOR NO
--   KEY UPDATE) → call (FOR UPDATE per il creatore, FOR SHARE per un membro)
--   → candidatura / membro (FOR UPDATE) → lock advisory per ultimo.
-- Le RPC dei membri leggono prima la riga del membro SENZA lock (per sapere
-- su quale call agiscono: partner_call_id non cambia mai), poi bloccano
-- nell'ordine e la rileggono FOR UPDATE. fn_partner_decidi scrive le righe
-- dei membri dopo il lock advisory dell'esclusività: nessuna funzione che
-- blocca righe dei membri prende quel lock, quindi niente cicli.
--
-- ADDITIVA: tabelle, colonne, vincoli, indici e funzioni nuove; cinque
-- funzioni della 0039 ridefinite con la stessa firma e lo stesso
-- comportamento verso il backend attuale (in più: righe dei membri,
-- esclusività sui membri, titolare attivo, posizioni con membri non
-- rimovibili; fn_partner_decidi aggiunge la chiave membro_id alla risposta). Le colonne nuove di partner_calls non sono
-- nella whitelist di fn_partner_call_campi_editabili (0037): crea_bozza e
-- aggiorna le rifiutano. Da eseguire IN UN'UNICA TRANSAZIONE (begin; ...
-- commit;). Rollback documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Membri del consorzio (V1, Q20). company_profile_id NULL = membro
--    esterno (non in piattaforma): allora esterno_* sono obbligatori; per le
--    aziende in piattaforma esterno_* sono NULL.
-- ----------------------------------------------------------------------------
create table public.partner_call_membri (
  id                    uuid primary key default gen_random_uuid(),
  partner_call_id       uuid not null
                          references public.partner_calls (id) on delete cascade,
  company_profile_id    uuid
                          references public.company_profiles (id) on delete cascade,
  candidatura_id        uuid
                          references public.partner_candidature (id) on delete set null
    constraint pcm_candidatura_key unique,
  esterno_denominazione text
    constraint pcm_esterno_denominazione_check
    check (esterno_denominazione is null
           or char_length(esterno_denominazione) between 2 and 200),
  esterno_paese         text
    constraint pcm_esterno_paese_check
    check (esterno_paese is null or esterno_paese ~ '^[A-Z]{2}$'),
  esterno_tipi_soggetto text[]
    constraint pcm_esterno_tipi_soggetto_check
    check (esterno_tipi_soggetto is null
           or (cardinality(esterno_tipi_soggetto) <= 5
               and array_to_string(esterno_tipi_soggetto, ',', '*')
                   ~ '^([a-z_]{2,40}(,[a-z_]{2,40})*)?$')),
  posizione_id          uuid
                          references public.partner_call_posizioni (id) on delete set null,
  ruolo                 text not null default 'partner'
    constraint pcm_ruolo_check
    check (ruolo in ('capofila', 'partner', 'affiliated_entity', 'associated_partner')),
  quota_percentuale     numeric(5, 2)
    constraint pcm_quota_check
    check (quota_percentuale is null
           or (quota_percentuale > 0 and quota_percentuale <= 100)),
  stato                 text not null default 'proposto'
    constraint pcm_stato_check check (stato in ('proposto', 'confermato', 'uscito')),
  confermato_da_user_id uuid,
  confermato_at         timestamptz,
  aggiornato_da_user_id uuid,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  -- Esterno ⇔ senza azienda: nome, paese e tipi solo (e sempre) per gli
  -- esterni; un esterno non ha candidatura.
  constraint pcm_esterno_coerente
    check ((company_profile_id is null
            and esterno_denominazione is not null and esterno_paese is not null
            and esterno_tipi_soggetto is not null and candidatura_id is null)
           or (company_profile_id is not null
               and esterno_denominazione is null and esterno_paese is null
               and esterno_tipi_soggetto is null)),
  -- Conferma: chi e quando, sempre insieme, e mai senza quota (salvo i
  -- partner associati, che non ricevono budget).
  constraint pcm_confermato_coerente
    check ((stato = 'confermato') = (confermato_at is not null)
           and (confermato_at is null) = (confermato_da_user_id is null)),
  constraint pcm_confermato_con_quota
    check (stato <> 'confermato' or quota_percentuale is not null
           or ruolo = 'associated_partner')
);

comment on table public.partner_call_membri is
  'Consorzio della call (WP8, V1): creatore (riga creata alla pubblicazione, non rimovibile), aziende con candidatura accettata (riga creata da fn_partner_decidi) ed esterni inseriti dal creatore (Q20). Stati proposto ⇄ confermato → uscito → proposto (nuova accettazione o esterno riproposto). Scritture SOLO tramite le RPC della 0040. Verso terzi solo la proiezione del backend: MAI company_profile_id di altri, candidatura_id, *_user_id. Cascade dalla call e dall''azienda del membro (Q21).';
comment on column public.partner_call_membri.company_profile_id is
  'Azienda in piattaforma (il creatore o un''azienda accettata); NULL = membro esterno.';
comment on column public.partner_call_membri.candidatura_id is
  'Candidatura accettata da cui nasce la riga (NULL per il creatore e gli esterni, o se la candidatura è stata cancellata).';
comment on column public.partner_call_membri.esterno_denominazione is
  'Nome dell''ente esterno (Q20) come lo scrive il creatore: dato dichiarato, non verificato sul registro. Chi lo vede lo decide la proiezione del backend.';
comment on column public.partner_call_membri.esterno_paese is
  'Paese dell''ente esterno, ISO-3166 alpha-2 maiuscolo (dichiarato).';
comment on column public.partner_call_membri.esterno_tipi_soggetto is
  'Tipi di soggetto dell''ente esterno (codici del vocabolario, validati dal backend; al massimo 5): nel validatore valgono come «dichiarato».';
comment on column public.partner_call_membri.quota_percentuale is
  'Quota del budget del progetto (%). La conferma la richiede, salvo per un partner associato (non riceve budget: la sua quota non entra nella somma). Il costo della quota (quota × budget, esatto se il creatore l''ha indicato) lo calcola il validatore.';
comment on column public.partner_call_membri.stato is
  'proposto (da confermare) | confermato (dall''azienda del membro; per il creatore e gli esterni dal creatore) | uscito. Una modifica di ruolo, posizione o quota di un membro diverso dal creatore lo riporta a proposto.';
comment on column public.partner_call_membri.confermato_da_user_id is
  'Titolare che ha confermato. Senza FK: MAI verso terzi.';
comment on column public.partner_call_membri.aggiornato_da_user_id is
  'Titolare che ha scritto per ultimo la riga (NULL per le righe create dal sistema: backfill, riga del creatore aggiunta da un''accettazione). Senza FK: MAI verso terzi.';

create trigger trg_partner_call_membri_updated_at
  before update on public.partner_call_membri
  for each row execute function public.set_updated_at();

-- Una riga per azienda in piattaforma nella call (anche uscita: si riammette).
create unique index partner_membri_azienda_uq on public.partner_call_membri
  (partner_call_id, company_profile_id)
  where company_profile_id is not null;
-- Capofila unico tra i membri non usciti.
create unique index partner_membri_capofila_uq on public.partner_call_membri (partner_call_id)
  where ruolo = 'capofila' and stato <> 'uscito';
-- Membri di una call (consorzio, tetto degli esterni) e cascade dalla call.
create index partner_membri_call_idx on public.partner_call_membri (partner_call_id, stato);
-- Impegni di un'azienda (esclusività) e cascade dall'azienda.
create index partner_membri_company_idx on public.partner_call_membri
  (company_profile_id, stato) where company_profile_id is not null;
-- ON DELETE SET NULL dalle posizioni senza scansione completa.
create index partner_membri_posizione_idx on public.partner_call_membri (posizione_id)
  where posizione_id is not null;

-- ----------------------------------------------------------------------------
-- 2) Checklist documentale (V3, appendice A): uno stato per documento. I
--    codici (documenti di base e della forma) li valida il backend sul
--    vocabolario (partenariato_vocabolario); qui solo la forma del codice.
-- ----------------------------------------------------------------------------
create table public.partner_call_documenti (
  partner_call_id       uuid not null
                          references public.partner_calls (id) on delete cascade,
  codice                text not null
    constraint pcd_codice_check check (codice ~ '^[a-z_]{2,40}$'),
  stato                 text not null default 'da_fare'
    constraint pcd_stato_check
    check (stato in ('da_fare', 'in_corso', 'fatto', 'non_applicabile')),
  note                  text
    constraint pcd_note_check check (note is null or char_length(note) <= 500),
  aggiornato_da_user_id uuid,
  updated_at            timestamptz not null default now(),
  constraint partner_call_documenti_pkey primary key (partner_call_id, codice)
);

comment on table public.partner_call_documenti is
  'Stato dei documenti della checklist del consorzio (WP8, V3): una riga per documento toccato (senza riga = da_fare). Scritture SOLO tramite fn_partner_documento_stato (solo il creatore). Le note sono del creatore: verso le controparti le espone solo la proiezione del backend. Cascade dalla call.';
comment on column public.partner_call_documenti.codice is
  'Codice del documento (documenti di base o della forma in partenariato_vocabolario): validato dal backend.';
comment on column public.partner_call_documenti.aggiornato_da_user_id is
  'Titolare che ha aggiornato lo stato. Senza FK: MAI verso terzi.';

create trigger trg_partner_call_documenti_updated_at
  before update on public.partner_call_documenti
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------------------------
-- 3) Ultimo esito del validatore sulla call (WP8, V2): scritto best-effort dal
--    servizio dopo ogni mutazione (fn_partner_call_validazione_salva).
--    Colonne tecniche, fuori dalla whitelist delle RPC della call; come per
--    le colonne del fan-out (0038), trg_partner_calls_updated_at aggiorna
--    updated_at anche per queste scritture.
-- ----------------------------------------------------------------------------
alter table public.partner_calls
  add column validazione_esito   text,
  add column validazione_at      timestamptz,
  add column copertura_gap_ratio numeric(4, 3),
  add constraint pcall_validazione_esito_check
    check (validazione_esito is null or validazione_esito in ('verde', 'rosso', 'grigio')),
  add constraint pcall_validazione_coerente
    check ((validazione_esito is null) = (validazione_at is null)),
  add constraint pcall_copertura_gap_ratio_check
    check (copertura_gap_ratio is null or copertura_gap_ratio between 0 and 1);

comment on column public.partner_calls.validazione_esito is
  'Esito complessivo dell''ultima validazione del consorzio (verde | rosso | grigio), calcolato dal backend (partenariato_validatore) e salvato best-effort. Metriche (WP9) e liste del creatore.';
comment on column public.partner_calls.copertura_gap_ratio is
  'Requisiti cercati coperti dal consorzio / requisiti cercati (0..1) all''ultima validazione.';

-- ----------------------------------------------------------------------------
-- 4) Funzioni interne.
-- ----------------------------------------------------------------------------

-- Inserisce la riga di un'azienda in piattaforma nel consorzio. Capofila
-- unico tra i non usciti: se ce n'è già uno (anche per una scrittura
-- concorrente, rilevata dall'indice unico), la riga entra come partner.
-- Riga già presente per (call, azienda): con p_riammetti una riga uscita
-- torna proposta con i dati nuovi (candidatura, posizione, ruolo, quota,
-- conferma azzerata), altrimenti non cambia. Ritorna l'id della riga
-- inserita o riammessa, NULL se non ha scritto nulla.
create or replace function public.fn_partner_membro_inserisci(
  p_call        uuid,
  p_company     uuid,
  p_candidatura uuid,
  p_posizione   uuid,
  p_ruolo       text,
  p_quota       numeric,
  p_attore      uuid,
  p_riammetti   boolean
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ruolo   text := coalesce(p_ruolo, 'partner');
  v_id      uuid;
  v_vincolo text;
begin
  if v_ruolo = 'capofila' and exists (
    select 1 from public.partner_call_membri
    where partner_call_id = p_call and ruolo = 'capofila' and stato <> 'uscito'
      and company_profile_id is distinct from p_company
  ) then
    v_ruolo := 'partner';
  end if;

  loop
    begin
      if coalesce(p_riammetti, false) then
        insert into public.partner_call_membri
          (partner_call_id, company_profile_id, candidatura_id, posizione_id, ruolo,
           quota_percentuale, aggiornato_da_user_id)
        values
          (p_call, p_company, p_candidatura, p_posizione, v_ruolo, p_quota, p_attore)
        on conflict (partner_call_id, company_profile_id) where company_profile_id is not null
        do update
          set candidatura_id        = excluded.candidatura_id,
              stato                 = 'proposto',
              posizione_id          = excluded.posizione_id,
              ruolo                 = excluded.ruolo,
              quota_percentuale     = excluded.quota_percentuale,
              confermato_da_user_id = null,
              confermato_at         = null,
              aggiornato_da_user_id = excluded.aggiornato_da_user_id
          where partner_call_membri.stato = 'uscito'
        returning id into v_id;
      else
        insert into public.partner_call_membri
          (partner_call_id, company_profile_id, candidatura_id, posizione_id, ruolo,
           quota_percentuale, aggiornato_da_user_id)
        values
          (p_call, p_company, p_candidatura, p_posizione, v_ruolo, p_quota, p_attore)
        on conflict (partner_call_id, company_profile_id) where company_profile_id is not null
        do nothing
        returning id into v_id;
      end if;
      return v_id;
    exception when unique_violation then
      get stacked diagnostics v_vincolo = constraint_name;
      if v_vincolo is distinct from 'partner_membri_capofila_uq' or v_ruolo <> 'capofila' then
        raise;
      end if;
      v_ruolo := 'partner';
    end;
  end loop;
end;
$$;

comment on function public.fn_partner_membro_inserisci(uuid, uuid, uuid, uuid, text, numeric, uuid, boolean) is
  'Interna: riga di un''azienda in piattaforma nel consorzio (proposto). Capofila già presente tra i non usciti (anche per concorrenza) → partner. Riga esistente per (call, azienda): con p_riammetti una riga uscita torna proposta con candidatura, posizione, ruolo e quota nuovi e conferma azzerata; altrimenti nulla. Ritorna l''id scritto o NULL.';

-- Riga del creatore nel consorzio, se manca: capofila se la call lo dice
-- (ruolo_creatore = capofila), altrimenti partner; quota = quota_creatore_pct.
create or replace function public.fn_partner_membro_creatore(p_call uuid, p_attore uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call public.partner_calls%rowtype;
begin
  select * into v_call from public.partner_calls where id = p_call;
  if not found then
    return null;
  end if;
  return public.fn_partner_membro_inserisci(
    p_call, v_call.company_profile_id, null, null,
    case v_call.ruolo_creatore when 'capofila' then 'capofila' else 'partner' end,
    v_call.quota_creatore_pct, p_attore, false);
end;
$$;

comment on function public.fn_partner_membro_creatore(uuid, uuid) is
  'Interna: aggiunge la riga del creatore se manca (capofila se ruolo_creatore = capofila e il posto è libero, altrimenti partner; quota = quota_creatore_pct). Ritorna l''id inserito o NULL.';

-- ----------------------------------------------------------------------------
-- 5) Ridefinizione (STESSA firma) di fn_partner_call_aperta (0039): il
--    creatore è vivo solo se l'azienda non è eliminata né archiviata E il
--    titolare è attivo — la stessa definizione della controparte in
--    fn_partner_decidi e di partenariato_indice.aziende_vive.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_aperta(
  p_stato          text,
  p_scadenza_call  date,
  p_bando_scadenza date,
  p_creatore       uuid
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    p_stato = 'pubblicata'
    and p_scadenza_call >= (now() at time zone 'Europe/Rome')::date
    and (p_bando_scadenza is null
         or p_bando_scadenza >= (now() at time zone 'Europe/Rome')::date)
    and exists (
      select 1 from public.company_profiles cp
      join public.profiles pr on pr.id = cp.parent_id and pr.is_active
      where cp.id = p_creatore and cp.deleted_at is null and cp.archived_at is null),
    false);
$$;

comment on function public.fn_partner_call_aperta(text, date, date, uuid) is
  'Interna: la call (stato, scadenza, scadenza del bando, azienda creatrice) è pubblicata, la sua scadenza e quella del bando non sono passate (Europe/Rome) e l''azienda creatrice è viva: non eliminata né archiviata, con il titolare attivo (0040).';

-- ----------------------------------------------------------------------------
-- 6) Ridefinizione (STESSA firma) di fn_partner_esclusivita_violata (0039),
--    estesa ai membri (WP8). L'azienda ha un altro impegno sullo stesso
--    bando se:
--      a) è creatrice di un'altra call pubblicata, oppure di un'altra call
--         non annullata (completata, scaduta, sospesa) con almeno un ALTRO
--         membro non uscito, in piattaforma o esterno: il partenariato può
--         essere andato avanti. La sola riga del creatore non conta, così una
--         call scaduta senza partner non blocca per sempre il bando al suo
--         creatore;
--      b) è membro NON uscito (non creatore) di un'altra call non annullata;
--      c) ha una candidatura accettata su un'altra call non annullata SENZA
--         una riga nel consorzio (dati precedenti al backfill): se la riga
--         c'è, vale b) e un membro uscito non è più impegnato.
--    E questa call (p_esclusiva, NULL = true) o quella dell'impegno è
--    esclusiva (il vincolo è del bando: basta che uno dei due creatori
--    l'abbia confermato).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_esclusivita_violata(
  p_company   uuid,
  p_call      uuid,
  p_bando     integer,
  p_esclusiva boolean
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
      select 1 from public.partner_calls c
      where c.company_profile_id = p_company
        and c.bando_id = p_bando
        and c.id <> p_call
        and (c.stato = 'pubblicata'
             or (c.stato not in ('bozza', 'chiusa_annullata')
                 and exists (
                   select 1 from public.partner_call_membri m
                   where m.partner_call_id = c.id
                     and m.stato <> 'uscito'
                     and m.company_profile_id is distinct from p_company)))
        and (coalesce(p_esclusiva, true) or c.esclusivita))
    or exists (
      select 1
      from public.partner_call_membri m
      join public.partner_calls c on c.id = m.partner_call_id
      where m.company_profile_id = p_company
        and m.stato <> 'uscito'
        and m.partner_call_id <> p_call
        and c.company_profile_id <> p_company
        and c.bando_id = p_bando
        and c.stato <> 'chiusa_annullata'
        and (coalesce(p_esclusiva, true) or c.esclusivita))
    or exists (
      select 1
      from public.partner_candidature k
      join public.partner_calls c on c.id = k.partner_call_id
      where k.company_profile_id = p_company
        and k.stato = 'accettata'
        and k.partner_call_id <> p_call
        and c.bando_id = p_bando
        and c.stato <> 'chiusa_annullata'
        and (coalesce(p_esclusiva, true) or c.esclusivita)
        and not exists (
          select 1 from public.partner_call_membri m
          where m.partner_call_id = k.partner_call_id
            and m.company_profile_id = k.company_profile_id));
$$;

comment on function public.fn_partner_esclusivita_violata(uuid, uuid, integer, boolean) is
  'Interna: l''azienda ha un altro impegno sullo stesso bando — creatrice di un''altra call pubblicata o di un''altra call non annullata con almeno un altro membro non uscito, membro non uscito (non creatore) di un''altra call non annullata, o candidatura accettata su un''altra call non annullata senza riga nel consorzio — e questa call (p_esclusiva, NULL = true) o quella dell''altro impegno è esclusiva. Ridefinita dalla 0040 (membri) con la firma della 0039.';

-- ----------------------------------------------------------------------------
-- 7) fn_partner_membro_aggiorna — il creatore modifica ruolo, posizione e
--    quota di un membro (anche la propria riga e gli esterni). Lettura senza
--    lock del membro (membro_non_trovato se la call non è dell'azienda
--    attiva del titolare), poi owner → azienda viva → call FOR UPDATE →
--    membro FOR UPDATE. Controlli: call pubblicata, scaduta o chiusa_completata
--    (call_non_modificabile), membro non uscito (membro_uscito), ruolo del
--    creatore solo capofila | partner (ruolo_non_ammesso), posizione della
--    call (posizione_non_valida), capofila unico (capofila_gia_presente).
--    Nulla cambia → nessuna scrittura. Un membro diverso dal creatore torna
--    proposto (conferma azzerata); la riga del creatore resta confermata,
--    salvo quota tolta. Audit partenariato.membro_aggiornato. Ritorna
--    {membro, stato_precedente, modificato}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_membro_aggiorna(
  p_membro    uuid,
  p_attore    uuid,
  p_owner     uuid,
  p_company   uuid,
  p_posizione uuid,
  p_ruolo     text,
  p_quota     numeric
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_m        public.partner_call_membri%rowtype;
  v_call     public.partner_calls%rowtype;
  v_quota    numeric := round(p_quota, 2);
  v_creatore boolean;
  v_prima    text;
  v_stato    text;
begin
  if p_membro is null or p_ruolo is null
     or p_ruolo not in ('capofila', 'partner', 'affiliated_entity', 'associated_partner')
     or (v_quota is not null and (v_quota <= 0 or v_quota > 100)) then
    raise exception 'Dati del membro non validi' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Il consorzio lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  -- Lettura senza lock: la call del membro (partner_call_id non cambia).
  select * into v_m from public.partner_call_membri where id = p_membro;
  if not found or not exists (
    select 1 from public.partner_calls
    where id = v_m.partner_call_id and company_profile_id = p_company
      and family_parent_id = p_owner
  ) then
    raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
  end if;

  select * into v_call
  from public.fn_partner_call_blocca(p_owner, p_company, v_m.partner_call_id, true);
  if v_call.stato not in ('pubblicata', 'scaduta', 'chiusa_completata') then
    raise exception 'Il consorzio di questa call non si può più modificare'
      using detail = 'call_non_modificabile';
  end if;

  select * into v_m from public.partner_call_membri where id = p_membro for update;
  if v_m.stato = 'uscito' then
    raise exception 'Il membro è uscito dal consorzio' using detail = 'membro_uscito';
  end if;
  v_creatore := coalesce(v_m.company_profile_id = v_call.company_profile_id, false);
  if v_creatore and p_ruolo not in ('capofila', 'partner') then
    raise exception 'Il creatore della call partecipa come capofila o partner'
      using detail = 'ruolo_non_ammesso';
  end if;
  if p_posizione is not null and not exists (
    select 1 from public.partner_call_posizioni where id = p_posizione and call_id = v_call.id
  ) then
    raise exception 'Posizione non trovata in questa call' using detail = 'posizione_non_valida';
  end if;
  if p_ruolo = 'capofila' and exists (
    select 1 from public.partner_call_membri
    where partner_call_id = v_call.id and ruolo = 'capofila' and stato <> 'uscito'
      and id <> p_membro
  ) then
    raise exception 'Il consorzio ha già un capofila' using detail = 'capofila_gia_presente';
  end if;

  v_prima := v_m.stato;
  if v_m.ruolo = p_ruolo and v_m.posizione_id is not distinct from p_posizione
     and v_m.quota_percentuale is not distinct from v_quota then
    return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                              'modificato', false);
  end if;

  v_stato := case when v_creatore and v_quota is not null then v_m.stato else 'proposto' end;
  begin
    update public.partner_call_membri
    set ruolo                 = p_ruolo,
        posizione_id          = p_posizione,
        quota_percentuale     = v_quota,
        stato                 = v_stato,
        confermato_da_user_id = case when v_stato = 'confermato' then confermato_da_user_id end,
        confermato_at         = case when v_stato = 'confermato' then confermato_at end,
        aggiornato_da_user_id = p_attore
    where id = p_membro
    returning * into v_m;
  exception
    when unique_violation then
      raise exception 'Il consorzio ha già un capofila' using detail = 'capofila_gia_presente';
    when check_violation or not_null_violation or foreign_key_violation then
      raise exception 'Dati del membro non validi' using detail = 'parametri_non_validi';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.membro_aggiornato',
          coalesce((select parent_id from public.company_profiles
                    where id = v_m.company_profile_id), p_owner),
          p_owner,
          jsonb_build_object('membro_id', v_m.id, 'call_id', v_m.partner_call_id,
                             'company_profile_id', v_m.company_profile_id,
                             'ruolo', v_m.ruolo, 'quota_percentuale', v_m.quota_percentuale,
                             'stato', v_m.stato, 'stato_precedente', v_prima));

  return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                            'modificato', true);
end;
$$;

comment on function public.fn_partner_membro_aggiorna(uuid, uuid, uuid, uuid, uuid, text, numeric) is
  'Il creatore modifica ruolo (capofila | partner | affiliated_entity | associated_partner; per la propria riga solo capofila | partner), posizione (della call o NULL) e quota ((0,100] o NULL) di un membro (lock owner → azienda viva → call FOR UPDATE → membro FOR UPDATE). Call pubblicata, scaduta o chiusa_completata. Nulla cambia → nessuna scrittura. Membro diverso dal creatore → proposto con conferma azzerata; la riga del creatore resta confermata salvo quota tolta. Audit partenariato.membro_aggiornato. Ritorna {membro, stato_precedente, modificato}. Detail: parametri_non_validi | attore_non_titolare | membro_non_trovato | owner_not_found | company_not_found | call_not_found | call_non_modificabile | membro_uscito | ruolo_non_ammesso | posizione_non_valida | capofila_gia_presente.';

-- ----------------------------------------------------------------------------
-- 8) fn_partner_membro_conferma — conferma della propria riga (azienda del
--    membro, creatore compreso) o, per il creatore, di quella di un esterno.
--    Per chiunque altro membro_non_trovato. Lock: creatore → owner →
--    azienda viva → call FOR UPDATE; membro → owner → azienda viva → call
--    FOR SHARE; poi il membro FOR UPDATE. Call pubblicata, scaduta o
--    chiusa_completata (call_non_modificabile), membro non uscito
--    (membro_uscito), termini VISTI da chi conferma (p_ruolo, p_posizione,
--    p_quota) uguali a quelli della riga bloccata (membro_modificato: una
--    pagina non aggiornata non conferma termini che non ha mostrato), quota
--    presente salvo per un partner associato, che non riceve budget
--    (quota_mancante). Già confermato → nessuna scrittura. Audit
--    partenariato.membro_confermato. Ritorna {membro, stato_precedente,
--    modificato}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_membro_conferma(
  p_membro    uuid,
  p_attore    uuid,
  p_owner     uuid,
  p_company   uuid,
  p_ruolo     text,
  p_posizione uuid,
  p_quota     numeric
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_m        public.partner_call_membri%rowtype;
  v_call     public.partner_calls%rowtype;
  v_quota    numeric := round(p_quota, 2);
  v_creatore boolean;
  v_prima    text;
begin
  if p_membro is null or p_ruolo is null
     or p_ruolo not in ('capofila', 'partner', 'affiliated_entity', 'associated_partner')
     or (v_quota is not null and (v_quota <= 0 or v_quota > 100)) then
    raise exception 'Membro non valido' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Il consorzio lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_m from public.partner_call_membri where id = p_membro;
  if not found then
    raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
  end if;
  select * into v_call from public.partner_calls where id = v_m.partner_call_id;
  v_creatore := coalesce(v_call.company_profile_id = p_company
                         and v_call.family_parent_id = p_owner, false);
  if not (coalesce(v_m.company_profile_id = p_company, false)
          or (v_creatore and v_m.company_profile_id is null)) then
    raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
  end if;

  if v_creatore then
    select * into v_call
    from public.fn_partner_call_blocca(p_owner, p_company, v_m.partner_call_id, true);
  else
    perform public.fn_partner_cand_blocca_azienda(p_owner, p_company);
    select * into v_call from public.partner_calls where id = v_m.partner_call_id for share;
  end if;
  if v_call.stato not in ('pubblicata', 'scaduta', 'chiusa_completata') then
    raise exception 'Il consorzio di questa call non si può più modificare'
      using detail = 'call_non_modificabile';
  end if;

  select * into v_m from public.partner_call_membri where id = p_membro for update;
  v_prima := v_m.stato;
  if v_m.stato = 'uscito' then
    raise exception 'Il membro è uscito dal consorzio' using detail = 'membro_uscito';
  end if;
  -- Si conferma solo ciò che si è visto: una modifica del creatore arrivata
  -- dopo (anche in concorrenza: questa riga è bloccata) va riletta.
  if v_m.ruolo is distinct from p_ruolo
     or v_m.posizione_id is distinct from p_posizione
     or v_m.quota_percentuale is distinct from v_quota then
    raise exception 'Ruolo, posizione o quota del membro sono cambiati: ricarica e controlla'
      using detail = 'membro_modificato';
  end if;
  if v_m.stato = 'confermato' then
    return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                              'modificato', false);
  end if;
  if v_m.quota_percentuale is null and v_m.ruolo <> 'associated_partner' then
    raise exception 'Per confermare serve la quota del membro' using detail = 'quota_mancante';
  end if;

  update public.partner_call_membri
  set stato                 = 'confermato',
      confermato_da_user_id = p_attore,
      confermato_at         = now(),
      aggiornato_da_user_id = p_attore
  where id = p_membro
  returning * into v_m;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.membro_confermato', v_call.family_parent_id, p_owner,
          jsonb_build_object('membro_id', v_m.id, 'call_id', v_m.partner_call_id,
                             'company_profile_id', v_m.company_profile_id,
                             'quota_percentuale', v_m.quota_percentuale));

  return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                            'modificato', true);
end;
$$;

comment on function public.fn_partner_membro_conferma(uuid, uuid, uuid, uuid, text, uuid, numeric) is
  'Conferma di un membro: la propria riga (azienda del membro, creatore compreso) o, per il creatore, quella di un esterno; per chiunque altro membro_non_trovato. Lock owner → azienda viva → call (FOR UPDATE il creatore, FOR SHARE un membro) → membro FOR UPDATE. Call pubblicata, scaduta o chiusa_completata, membro non uscito, ruolo, posizione e quota visti da chi conferma (p_ruolo, p_posizione, p_quota) uguali a quelli della riga, quota presente (non per un partner associato). Già confermato → nessuna scrittura. Audit partenariato.membro_confermato. Ritorna {membro, stato_precedente, modificato}. Detail: parametri_non_validi | attore_non_titolare | membro_non_trovato | owner_not_found | company_not_found | call_not_found | azienda_non_disponibile | call_non_modificabile | membro_uscito | membro_modificato | quota_mancante.';

-- ----------------------------------------------------------------------------
-- 9) fn_partner_membro_esci — il creatore rimuove un membro (call pubblicata,
--    scaduta o chiusa_completata, altrimenti call_non_modificabile; lock owner →
--    azienda viva → call FOR UPDATE) oppure un'azienda membro esce da sé (in
--    qualunque stato della call e anche con l'azienda non più viva: uscire è
--    sempre possibile; lock owner → azienda → call FOR SHARE). La riga del
--    creatore non si tocca (membro_non_rimovibile); per chiunque altro
--    membro_non_trovato. Già uscito → nessuna scrittura. Conferma azzerata.
--    Audit partenariato.membro_uscito. Ritorna {membro, stato_precedente,
--    modificato, origine: creatore | membro}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_membro_esci(
  p_membro  uuid,
  p_attore  uuid,
  p_owner   uuid,
  p_company uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_m        public.partner_call_membri%rowtype;
  v_call     public.partner_calls%rowtype;
  v_creatore boolean;
  v_origine  text;
  v_prima    text;
begin
  if p_membro is null then
    raise exception 'Membro non valido' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Il consorzio lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_m from public.partner_call_membri where id = p_membro;
  if not found then
    raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
  end if;
  select * into v_call from public.partner_calls where id = v_m.partner_call_id;
  v_creatore := coalesce(v_call.company_profile_id = p_company
                         and v_call.family_parent_id = p_owner, false);

  if v_creatore then
    if coalesce(v_m.company_profile_id = p_company, false) then
      raise exception 'Il creatore della call non esce dal proprio consorzio'
        using detail = 'membro_non_rimovibile';
    end if;
    v_origine := 'creatore';
    select * into v_call
    from public.fn_partner_call_blocca(p_owner, p_company, v_m.partner_call_id, true);
    if v_call.stato not in ('pubblicata', 'scaduta', 'chiusa_completata') then
      raise exception 'Il consorzio di questa call non si può più modificare'
        using detail = 'call_non_modificabile';
    end if;
  elsif coalesce(v_m.company_profile_id = p_company, false) then
    v_origine := 'membro';
    perform public.fn_partner_call_blocca_azienda(p_owner, p_company, false);
    select * into v_call from public.partner_calls where id = v_m.partner_call_id for share;
  else
    raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
  end if;

  select * into v_m from public.partner_call_membri where id = p_membro for update;
  v_prima := v_m.stato;
  if v_m.stato = 'uscito' then
    return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                              'modificato', false, 'origine', v_origine);
  end if;

  update public.partner_call_membri
  set stato                 = 'uscito',
      confermato_da_user_id = null,
      confermato_at         = null,
      aggiornato_da_user_id = p_attore
  where id = p_membro
  returning * into v_m;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.membro_uscito',
          case when v_origine = 'creatore'
               then coalesce((select parent_id from public.company_profiles
                              where id = v_m.company_profile_id), p_owner)
               else v_call.family_parent_id end,
          p_owner,
          jsonb_build_object('membro_id', v_m.id, 'call_id', v_m.partner_call_id,
                             'company_profile_id', v_m.company_profile_id,
                             'origine', v_origine, 'stato_precedente', v_prima));

  return jsonb_build_object('membro', to_jsonb(v_m), 'stato_precedente', v_prima,
                            'modificato', true, 'origine', v_origine);
end;
$$;

comment on function public.fn_partner_membro_esci(uuid, uuid, uuid, uuid) is
  'Uscita dal consorzio: il creatore rimuove un membro (call pubblicata, scaduta o chiusa_completata; lock owner → azienda viva → call FOR UPDATE) o un''azienda membro esce da sé (qualunque stato della call, anche con l''azienda non più viva; lock owner → azienda → call FOR SHARE); poi il membro FOR UPDATE. La riga del creatore non si tocca. Già uscito → nessuna scrittura. Stato uscito, conferma azzerata, audit partenariato.membro_uscito. Ritorna {membro, stato_precedente, modificato, origine}. Detail: parametri_non_validi | attore_non_titolare | membro_non_trovato | membro_non_rimovibile | owner_not_found | company_not_found | call_not_found | call_non_modificabile.';

-- ----------------------------------------------------------------------------
-- 10) fn_partner_membro_esterno — il creatore aggiunge o modifica un membro
--     esterno (Q20). p_payload: {membro_id?, denominazione, paese,
--     tipi_soggetto?, ruolo?, quota?, posizione_id?} (nient'altro; ruolo
--     assente = partner, tipi assenti = nessuno). Lock owner → azienda viva
--     → call FOR UPDATE (call_not_found per chi non è il creatore). Call
--     pubblicata, scaduta o chiusa_completata (call_non_modificabile). Tetto: 30
--     membri non usciti per call e 60 righe esterne in tutto (anti-abuso:
--     un esterno uscito si ripropone modificandolo) → limite_membri. Con
--     membro_id: solo un esterno della call (membro_non_trovato); nulla
--     cambia → nessuna scrittura; altrimenti torna proposto (anche se era
--     uscito). Capofila unico (capofila_gia_presente). Audit
--     partenariato.membro_aggiunto | partenariato.membro_aggiornato (senza il
--     nome dell'esterno). Ritorna {membro, creato, stato_precedente,
--     modificato}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_membro_esterno(
  p_call    uuid,
  p_attore  uuid,
  p_owner   uuid,
  p_company uuid,
  p_payload jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi       constant text[] := array['membro_id', 'denominazione', 'paese',
                                          'tipi_soggetto', 'ruolo', 'quota', 'posizione_id'];
  v_membro       uuid;
  v_denominazione text;
  v_paese        text;
  v_tipi         text[];
  v_ruolo        text;
  v_quota        numeric;
  v_posizione    uuid;
  v_call         public.partner_calls%rowtype;
  v_m            public.partner_call_membri%rowtype;
  v_prima        text;
  v_creato       boolean := false;
begin
  if jsonb_typeof(p_payload) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_payload) k where not (k = any (v_chiavi)))
     or (jsonb_typeof(p_payload -> 'tipi_soggetto') not in ('array', 'null'))
     or exists (
       select 1
       from jsonb_array_elements(case when jsonb_typeof(p_payload -> 'tipi_soggetto') = 'array'
                                      then p_payload -> 'tipi_soggetto' else '[]'::jsonb end) e
       where jsonb_typeof(e) <> 'string') then
    raise exception 'Dati del membro esterno non validi' using detail = 'parametri_non_validi';
  end if;
  begin
    v_membro := (p_payload ->> 'membro_id')::uuid;
    v_quota := round((p_payload ->> 'quota')::numeric, 2);
    v_posizione := (p_payload ->> 'posizione_id')::uuid;
  exception when data_exception then
    raise exception 'Dati del membro esterno non validi' using detail = 'parametri_non_validi';
  end;
  v_denominazione := btrim(p_payload ->> 'denominazione');
  v_paese := p_payload ->> 'paese';
  v_ruolo := coalesce(nullif(p_payload ->> 'ruolo', ''), 'partner');
  v_tipi := case when jsonb_typeof(p_payload -> 'tipi_soggetto') = 'array'
                 then array(select e from jsonb_array_elements_text(p_payload -> 'tipi_soggetto') e)
                 else '{}'::text[] end;
  if v_denominazione is null or char_length(v_denominazione) not between 2 and 200
     or v_paese is null or v_paese !~ '^[A-Z]{2}$'
     or v_ruolo not in ('capofila', 'partner', 'affiliated_entity', 'associated_partner')
     or (v_quota is not null and (v_quota <= 0 or v_quota > 100))
     or cardinality(v_tipi) > 5
     or array_to_string(v_tipi, ',', '*') !~ '^([a-z_]{2,40}(,[a-z_]{2,40})*)?$'
     or (select count(distinct t) from unnest(v_tipi) t) <> cardinality(v_tipi) then
    raise exception 'Dati del membro esterno non validi' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Il consorzio lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);
  if v_call.stato not in ('pubblicata', 'scaduta', 'chiusa_completata') then
    raise exception 'Il consorzio di questa call non si può più modificare'
      using detail = 'call_non_modificabile';
  end if;
  if v_posizione is not null and not exists (
    select 1 from public.partner_call_posizioni where id = v_posizione and call_id = p_call
  ) then
    raise exception 'Posizione non trovata in questa call' using detail = 'posizione_non_valida';
  end if;

  if v_membro is not null then
    select * into v_m from public.partner_call_membri
    where id = v_membro and partner_call_id = p_call and company_profile_id is null
    for update;
    if not found then
      raise exception 'Membro non trovato' using detail = 'membro_non_trovato';
    end if;
    v_prima := v_m.stato;
    if v_m.stato <> 'uscito'
       and v_m.esterno_denominazione = v_denominazione and v_m.esterno_paese = v_paese
       and v_m.esterno_tipi_soggetto = v_tipi and v_m.ruolo = v_ruolo
       and v_m.quota_percentuale is not distinct from v_quota
       and v_m.posizione_id is not distinct from v_posizione then
      return jsonb_build_object('membro', to_jsonb(v_m), 'creato', false,
                                'stato_precedente', v_prima, 'modificato', false);
    end if;
  end if;

  if v_membro is null or v_prima = 'uscito' then
    if (select count(*) from public.partner_call_membri
        where partner_call_id = p_call and stato <> 'uscito') >= 30
       or (v_membro is null
           and (select count(*) from public.partner_call_membri
                where partner_call_id = p_call and company_profile_id is null) >= 60) then
      raise exception 'Il consorzio ha raggiunto il numero massimo di membri'
        using detail = 'limite_membri';
    end if;
  end if;
  if v_ruolo = 'capofila' and exists (
    select 1 from public.partner_call_membri
    where partner_call_id = p_call and ruolo = 'capofila' and stato <> 'uscito'
      and id is distinct from v_membro
  ) then
    raise exception 'Il consorzio ha già un capofila' using detail = 'capofila_gia_presente';
  end if;

  begin
    if v_membro is null then
      insert into public.partner_call_membri
        (partner_call_id, esterno_denominazione, esterno_paese, esterno_tipi_soggetto,
         posizione_id, ruolo, quota_percentuale, aggiornato_da_user_id)
      values
        (p_call, v_denominazione, v_paese, v_tipi, v_posizione, v_ruolo, v_quota, p_attore)
      returning * into v_m;
      v_creato := true;
    else
      update public.partner_call_membri
      set esterno_denominazione = v_denominazione,
          esterno_paese         = v_paese,
          esterno_tipi_soggetto = v_tipi,
          posizione_id          = v_posizione,
          ruolo                 = v_ruolo,
          quota_percentuale     = v_quota,
          stato                 = 'proposto',
          confermato_da_user_id = null,
          confermato_at         = null,
          aggiornato_da_user_id = p_attore
      where id = v_membro
      returning * into v_m;
    end if;
  exception
    when unique_violation then
      raise exception 'Il consorzio ha già un capofila' using detail = 'capofila_gia_presente';
    when check_violation or not_null_violation or foreign_key_violation then
      raise exception 'Dati del membro esterno non validi' using detail = 'parametri_non_validi';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore,
          case when v_creato then 'partenariato.membro_aggiunto'
               else 'partenariato.membro_aggiornato' end,
          p_owner, p_owner,
          jsonb_build_object('membro_id', v_m.id, 'call_id', p_call, 'esterno', true,
                             'ruolo', v_m.ruolo, 'quota_percentuale', v_m.quota_percentuale,
                             'stato', v_m.stato, 'stato_precedente', v_prima));

  return jsonb_build_object('membro', to_jsonb(v_m), 'creato', v_creato,
                            'stato_precedente', v_prima, 'modificato', true);
end;
$$;

comment on function public.fn_partner_membro_esterno(uuid, uuid, uuid, uuid, jsonb) is
  'Il creatore aggiunge o modifica un membro esterno (Q20; payload {membro_id?, denominazione 2..200, paese ISO2 maiuscolo, tipi_soggetto? ≤ 5 codici distinti, ruolo? (default partner), quota? (0,100], posizione_id?}; lock owner → azienda viva → call FOR UPDATE). Call pubblicata, scaduta o chiusa_completata; tetto 30 membri non usciti e 60 righe esterne per call; capofila unico. Modifica: solo esterni della call, nulla cambia → nessuna scrittura, altrimenti proposto (anche da uscito) con conferma azzerata. Audit partenariato.membro_aggiunto | partenariato.membro_aggiornato senza il nome. Ritorna {membro, creato, stato_precedente, modificato}. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | company_not_found | call_not_found | call_non_modificabile | posizione_non_valida | membro_non_trovato | limite_membri | capofila_gia_presente.';

-- ----------------------------------------------------------------------------
-- 11) fn_partner_documento_stato — il creatore imposta lo stato (e le note,
--     ≤ 500, vuote = NULL) di un documento della checklist: upsert per
--     (call, codice). Lock owner → azienda viva → call FOR UPDATE; call
--     pubblicata, scaduta o chiusa_completata (call_non_modificabile). Codice
--     ^[a-z_]{2,40}$ (documento_non_valido; l'appartenenza al vocabolario la
--     verifica il backend). Ritorna la riga.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_documento_stato(
  p_call    uuid,
  p_attore  uuid,
  p_owner   uuid,
  p_company uuid,
  p_codice  text,
  p_stato   text,
  p_note    text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call public.partner_calls%rowtype;
  v_note text := nullif(btrim(p_note), '');
  v_doc  public.partner_call_documenti%rowtype;
begin
  if p_codice is null or p_codice !~ '^[a-z_]{2,40}$' then
    raise exception 'Documento non valido' using detail = 'documento_non_valido';
  end if;
  if p_stato is null or p_stato not in ('da_fare', 'in_corso', 'fatto', 'non_applicabile')
     or char_length(v_note) > 500 then
    raise exception 'Stato del documento non valido' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Il consorzio lo gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);
  if v_call.stato not in ('pubblicata', 'scaduta', 'chiusa_completata') then
    raise exception 'Il consorzio di questa call non si può più modificare'
      using detail = 'call_non_modificabile';
  end if;

  insert into public.partner_call_documenti
    (partner_call_id, codice, stato, note, aggiornato_da_user_id)
  values
    (p_call, p_codice, p_stato, v_note, p_attore)
  on conflict (partner_call_id, codice) do update
    set stato                 = excluded.stato,
        note                  = excluded.note,
        aggiornato_da_user_id = excluded.aggiornato_da_user_id
  returning * into v_doc;

  return to_jsonb(v_doc);
end;
$$;

comment on function public.fn_partner_documento_stato(uuid, uuid, uuid, uuid, text, text, text) is
  'Stato di un documento della checklist del consorzio (solo il creatore; lock owner → azienda viva → call FOR UPDATE; call pubblicata, scaduta o chiusa_completata): upsert per (call, codice) con stato da_fare | in_corso | fatto | non_applicabile e note ≤ 500 (vuote = NULL). Ritorna la riga. Detail: documento_non_valido | parametri_non_validi | attore_non_titolare | owner_not_found | company_not_found | call_not_found | call_non_modificabile.';

-- ----------------------------------------------------------------------------
-- 12) fn_partner_call_validazione_salva — interna, chiamata dal servizio dopo
--     ogni mutazione del consorzio (best-effort): esito complessivo e
--     copertura dei requisiti cercati. Un solo UPDATE della riga della call
--     (nessun altro lock). Ritorna false se la call non esiste.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_validazione_salva(
  p_call      uuid,
  p_esito     text,
  p_copertura numeric
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_call is null or p_esito is null or p_esito not in ('verde', 'rosso', 'grigio')
     or (p_copertura is not null and (p_copertura < 0 or p_copertura > 1)) then
    raise exception 'Esito della validazione non valido' using detail = 'parametri_non_validi';
  end if;

  update public.partner_calls
  set validazione_esito   = p_esito,
      validazione_at      = now(),
      copertura_gap_ratio = round(p_copertura, 3)
  where id = p_call;
  return found;
end;
$$;

comment on function public.fn_partner_call_validazione_salva(uuid, text, numeric) is
  'Interna (servizio del consorzio, best-effort): salva sulla call l''esito complessivo del validatore (verde | rosso | grigio), l''istante e la copertura dei requisiti cercati (0..1, NULL ammesso). Un solo UPDATE. Ritorna false se la call non esiste. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 13) Ridefinizione (STESSA firma) di fn_partner_decidi (0039): stesse
--     guardie, errori, lock, conversazione e audit; in più, nella stessa
--     transazione dell'accettazione, le righe del consorzio (V1): quella del
--     creatore se manca e quella di Y (proposto, posizione della
--     candidatura, quota = posizione.quota_ipotizzata_pct, capofila se la
--     posizione lo è e il posto è libero, altrimenti partner; riga uscita →
--     riammessa). Le righe si scrivono dopo il lock advisory
--     dell'esclusività (vedi l'intestazione). La risposta ha in più membro_id.
-- ----------------------------------------------------------------------------
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
  if coalesce(p_rivela, false) then
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
  'Decisione su una candidatura (X) o su un invito (Y): accetta | rifiuta. Lettura senza lock, poi owner → azienda che decide → (accetta) azienda Y FOR KEY SHARE e profilo Y FOR SHARE → call FOR SHARE → candidatura FOR UPDATE → (accetta) advisory esclusività Y × bando → righe del consorzio. Accettazione nella stessa transazione: conversazione, stato accettata, audit partenariato.candidatura_accettata e, solo con p_rivela, partenariato.identita_rivelata + partenariato.contatti_rivelati; dalla 0040 la riga del creatore se manca e quella di Y (proposto, posizione e quota della posizione, riammessa se uscita). Rifiuto con motivo ≤ 500. p_richiedi_non_sandbox NULL o assente = true. Ritorna {candidatura, conversazione_id} (+ membro_id all''accettazione). Detail: parametri_non_validi | attore_non_titolare | candidatura_non_trovata | owner_not_found | azienda_non_disponibile | candidatura_gia_decisa | invito_scaduto | call_non_attiva | identita_non_verificata | profilo_partner_non_attivo | controparte_non_disponibile | stesso_gruppo | esclusivita_violata.';

-- ----------------------------------------------------------------------------
-- 14) Ridefinizione (STESSA firma) di fn_partner_call_pubblica (0039): stesse
--     guardie, errori, versioni, esclusività (ora anche sui membri, tramite
--     fn_partner_esclusivita_violata) e audit; in più la riga del creatore
--     nel consorzio (V1), nella stessa transazione.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_pubblica(
  p_owner                uuid,
  p_company              uuid,
  p_attore               uuid,
  p_call                 uuid,
  p_bando_stato          text,
  p_bando_scadenza       date,
  p_scadenza_call        date,
  p_richiedi_non_sandbox boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi     constant date := (now() at time zone 'Europe/Rome')::date;
  v_call     public.partner_calls%rowtype;
  v_scadenza date;
  v_limite   integer;
  v_versione integer;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato <> 'bozza' then
    raise exception 'Si può pubblicare solo una bozza' using detail = 'stato_call_non_valido';
  end if;

  if not public.fn_partenariato_identita_ok(p_company, p_richiedi_non_sandbox) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;
  if not v_call.anonima
     and not public.fn_partenariato_rappresentante_ok(p_owner, p_company) then
    raise exception 'Il titolare non risulta legale rappresentante verificato'
      using detail = 'rappresentante_non_verificato';
  end if;

  if coalesce(lower(btrim(p_bando_stato)), '') not in ('aperto', 'in apertura prossimamente')
  then
    raise exception 'Il bando non è aperto' using detail = 'bando_non_disponibile';
  end if;

  v_scadenza := coalesce(p_scadenza_call, v_call.scadenza_call);
  if v_scadenza is null or v_scadenza < v_oggi
     or (p_bando_scadenza is not null and v_scadenza > p_bando_scadenza) then
    raise exception 'La scadenza della call deve essere tra oggi e la scadenza del bando'
      using detail = 'scadenza_call_non_valida';
  end if;

  if nullif(btrim(v_call.titolo), '') is null
     or nullif(btrim(v_call.descrizione_pubblica), '') is null
     or v_call.regole_confermate_at is null
     or not exists (select 1 from public.partner_call_posizioni where call_id = p_call)
     or not exists (select 1 from public.partner_call_requisiti
                    where call_id = p_call and cercato) then
    raise exception 'La call non è completa' using detail = 'call_incompleta';
  end if;
  if exists (
    select 1 from public.partner_call_requisiti
    where call_id = p_call and criterio ->> 'tipo' = 'regola_finanziaria'
      and not public.fn_partner_call_regola_ok(criterio -> 'regola',
                                               v_call.regole_partenariato)
  ) then
    raise exception 'Le regole finanziarie dei requisiti non corrispondono più alle regole confermate'
      using detail = 'requisiti_non_validi';
  end if;

  v_limite := public.fn_partner_call_limite_attive(p_owner);
  if v_limite is not null and v_limite <= 0 then
    raise exception 'Il tuo piano non include la creazione di call di partenariato'
      using detail = 'piano_non_include_call';
  end if;
  if v_limite is not null and public.fn_partner_calls_attive_usate(p_owner) >= v_limite then
    raise exception 'Hai raggiunto il numero massimo di call attive del tuo piano'
      using detail = 'limite_call_raggiunto';
  end if;

  -- 0039: esclusività anche alla pubblicazione (C6). Per ultimo (dopo i lock
  -- di riga), con lo stesso lock advisory di fn_partner_decidi per azienda ×
  -- bando: un'accettazione e una pubblicazione della stessa azienda sullo
  -- stesso bando si serializzano. Dalla 0040 conta anche essere membro non
  -- uscito di un'altra call.
  perform pg_advisory_xact_lock(hashtext('partner_esclusivita'),
                                hashtext(p_company::text || ':' || v_call.bando_id::text));
  if public.fn_partner_esclusivita_violata(p_company, p_call, v_call.bando_id,
                                           v_call.esclusivita) then
    raise exception 'Il bando ammette un solo partenariato per azienda'
      using detail = 'esclusivita_violata';
  end if;

  update public.partner_calls
  set stato                 = 'pubblicata',
      pubblicata_at         = now(),
      scadenza_call         = v_scadenza,
      bando_stato_effettivo = p_bando_stato,
      bando_scadenza        = p_bando_scadenza,
      bando_verificato_at   = now(),
      bando_mancante_dal    = null
  where id = p_call;

  v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);

  -- 0040: la riga del creatore nel consorzio (V1).
  perform public.fn_partner_membro_creatore(p_call, p_attore);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.call_pubblicata', p_owner, p_owner,
          jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                             'bando_id', v_call.bando_id, 'versione', v_versione));

  select * into v_call from public.partner_calls where id = p_call;
  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean) is
  'Pubblica una bozza (lock owner → azienda viva → call): identità dal registro (T5; p_richiedi_non_sandbox NULL = true) e, per una call nominativa, legale rappresentante verificato (Q9); bando aperto | in apertura prossimamente; scadenza tra oggi (Europe/Rome) e la scadenza del bando; completa (titolo, descrizione, regole confermate, ≥ 1 posizione, ≥ 1 requisito cercato); limiti del piano sul pool dell''owner (NULL = illimitato); esclusività (fn_partner_esclusivita_violata, dalla 0040 anche sui membri; lock advisory azienda × bando come fn_partner_decidi). Versione 1, riga del creatore nel consorzio (0040) + audit partenariato.call_pubblicata. Ritorna la riga. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | identita_non_verificata | rappresentante_non_verificato | bando_non_disponibile | scadenza_call_non_valida | call_incompleta | requisiti_non_validi | piano_non_include_call | limite_call_raggiunto | esclusivita_violata.';

-- ----------------------------------------------------------------------------
-- 14-bis) Ridefinizione (STESSA firma) di fn_partner_call_sostituisci_posizioni
--     (0039): corpo copiato dalla 0039 senza altre modifiche che una guardia
--     in più: una posizione omessa a cui punta un membro NON uscito del
--     consorzio → posizione_con_membri (prima la cancellazione azzerava
--     posizione_id con ON DELETE SET NULL lasciando la conferma). Lock
--     invariati: la call FOR UPDATE serializza con le RPC dei membri.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_sostituisci_posizioni(
  p_owner     uuid,
  p_company   uuid,
  p_attore    uuid,
  p_call      uuid,
  p_posizioni jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi   constant text[] := array['id', 'titolo', 'ruolo', 'tipi_soggetto', 'competenze',
                                      'ateco_divisioni', 'regioni', 'territorio_modalita',
                                      'paesi', 'dimensioni', 'quota_ipotizzata_pct', 'numero',
                                      'requisiti_ids', 'note'];
  v_call     public.partner_calls%rowtype;
  v_prima    jsonb;
  v_item     jsonb;
  v_ord      bigint;
  v_id       uuid;
  v_tenuti   uuid[] := '{}';
  v_requisiti uuid[];
  v_righe    jsonb := '[]'::jsonb;
  v_versione integer;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato not in ('bozza', 'pubblicata') then
    raise exception 'La call non è modificabile in questo stato'
      using detail = 'stato_call_non_valido';
  end if;
  if jsonb_typeof(p_posizioni) is distinct from 'array' or jsonb_array_length(p_posizioni) > 10
  then
    raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
  end if;

  v_requisiti := array(select id from public.partner_call_requisiti where call_id = p_call);

  for v_item, v_ord in
    select e, o from jsonb_array_elements(p_posizioni) with ordinality as t(e, o)
  loop
    if jsonb_typeof(v_item) <> 'object' then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
    end if;
    if exists (select 1 from jsonb_object_keys(v_item) k where not (k = any (v_chiavi))) then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
    end if;
    v_id := null;
    if jsonb_typeof(v_item -> 'id') is distinct from 'null' and v_item ? 'id' then
      begin
        v_id := (v_item ->> 'id')::uuid;
      exception when data_exception then
        raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
      end;
      if v_id = any (v_tenuti) or not exists (
        select 1 from public.partner_call_posizioni where id = v_id and call_id = p_call
      ) then
        raise exception 'Posizione non trovata in questa call'
          using detail = 'posizioni_non_valide';
      end if;
      v_tenuti := v_tenuti || v_id;
    end if;
    v_righe := v_righe || jsonb_build_array(
      (v_item - 'id')
      || jsonb_build_object('id', coalesce(v_id, gen_random_uuid()), 'ordine', v_ord - 1));
  end loop;

  v_prima := public.fn_partner_call_contenuto(p_call);

  begin
    if exists (
      select 1 from jsonb_populate_recordset(null::public.partner_call_posizioni, v_righe) r
      where not (coalesce(r.requisiti_ids, '{}') <@ v_requisiti)
    ) then
      raise exception 'Requisito non trovato in questa call'
        using detail = 'posizioni_non_valide';
    end if;

    -- WP7: una posizione con candidature attive non si rimuove.
    if exists (
      select 1
      from public.partner_call_posizioni p
      join public.partner_candidature k on k.posizione_id = p.id
      where p.call_id = p_call
        and not (p.id = any (v_tenuti))
        and k.stato in ('inviata', 'accettata')
    ) then
      raise exception 'Una posizione con candidature attive non si può rimuovere'
        using detail = 'posizione_con_candidature';
    end if;

    -- 0040: né una posizione occupata da un membro non uscito del consorzio
    -- (spostato lì dal creatore o esterno): toglierla cambierebbe i termini
    -- che il membro ha confermato senza chiedergli una nuova conferma.
    if exists (
      select 1
      from public.partner_call_posizioni p
      join public.partner_call_membri m on m.posizione_id = p.id
      where p.call_id = p_call
        and not (p.id = any (v_tenuti))
        and m.stato <> 'uscito'
    ) then
      raise exception 'Una posizione con membri del consorzio non si può rimuovere'
        using detail = 'posizione_con_membri';
    end if;

    delete from public.partner_call_posizioni
    where call_id = p_call and not (id = any (v_tenuti));

    update public.partner_call_posizioni p
    set titolo               = r.titolo,
        ruolo                = coalesce(r.ruolo, 'partner'),
        tipi_soggetto        = coalesce(r.tipi_soggetto, '{}'),
        competenze           = coalesce(r.competenze, '{}'),
        ateco_divisioni      = coalesce(r.ateco_divisioni, '{}'),
        regioni              = coalesce(r.regioni, '{}'),
        territorio_modalita  = coalesce(r.territorio_modalita, 'qualsiasi'),
        paesi                = coalesce(r.paesi, '{}'),
        dimensioni           = coalesce(r.dimensioni, '{}'),
        quota_ipotizzata_pct = r.quota_ipotizzata_pct,
        numero               = coalesce(r.numero, 1),
        requisiti_ids        = coalesce(r.requisiti_ids, '{}'),
        note                 = r.note,
        ordine               = r.ordine
    from jsonb_populate_recordset(null::public.partner_call_posizioni, v_righe) r
    where p.id = r.id and p.call_id = p_call and r.id = any (v_tenuti);

    insert into public.partner_call_posizioni
      (id, call_id, titolo, ruolo, tipi_soggetto, competenze, ateco_divisioni, regioni,
       territorio_modalita, paesi, dimensioni, quota_ipotizzata_pct, numero, requisiti_ids,
       note, ordine)
    select r.id, p_call, r.titolo, coalesce(r.ruolo, 'partner'),
           coalesce(r.tipi_soggetto, '{}'), coalesce(r.competenze, '{}'),
           coalesce(r.ateco_divisioni, '{}'), coalesce(r.regioni, '{}'),
           coalesce(r.territorio_modalita, 'qualsiasi'), coalesce(r.paesi, '{}'),
           coalesce(r.dimensioni, '{}'), r.quota_ipotizzata_pct, coalesce(r.numero, 1),
           coalesce(r.requisiti_ids, '{}'), r.note, r.ordine
    from jsonb_populate_recordset(null::public.partner_call_posizioni, v_righe) r
    where not (r.id = any (v_tenuti));
  exception
    when check_violation or not_null_violation or unique_violation or data_exception then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
  end;

  if v_call.stato = 'pubblicata' then
    if not exists (select 1 from public.partner_call_posizioni where call_id = p_call) then
      raise exception 'Una call pubblicata deve avere almeno una posizione'
        using detail = 'call_incompleta';
    end if;
    if public.fn_partner_call_contenuto(p_call) is distinct from v_prima then
      v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (p_attore, 'partenariato.call_modificata', p_owner, p_owner,
              jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                                 'versione', v_versione, 'campi', jsonb_build_array('posizioni')));
    end if;
  end if;

  return coalesce((
    select jsonb_agg(to_jsonb(p) order by p.ordine)
    from public.partner_call_posizioni p where p.call_id = p_call), '[]'::jsonb);
end;
$$;

comment on function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb) is
  'Replace-all delle posizioni (bozza o pubblicata; lock owner → azienda viva → call): posizioni con id conservate e aggiornate sul posto (id, created_at e candidature restano), omesse cancellate, nuove inserite; requisiti_ids solo della stessa call; una posizione omessa con candidature attive (inviata | accettata) → posizione_con_candidature (0039), con membri non usciti del consorzio → posizione_con_membri (0040); in pubblicata ≥ 1 posizione e versione + 1 se il contenuto cambia. Ritorna le posizioni. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | posizioni_non_valide | posizione_con_candidature | posizione_con_membri | call_incompleta.';

-- ----------------------------------------------------------------------------
-- 15) Backfill richiamabile dei membri: la riga del creatore per ogni call
--     pubblicata almeno una volta (pubblicata_at non NULL, qualunque stato
--     attuale) e quella di Y per ogni candidatura accettata, come se la 0040
--     ci fosse sempre stata. Idempotente (le righe esistenti non cambiano).
--     Ritorna quante righe ha inserito. Il DB del harness è vuoto: la
--     verifica sotto è a WARNING.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_backfill_membri()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  r   record;
  v_n integer := 0;
begin
  for r in
    select id from public.partner_calls
    where pubblicata_at is not null
    order by pubblicata_at, id
  loop
    if public.fn_partner_membro_creatore(r.id, null) is not null then
      v_n := v_n + 1;
    end if;
  end loop;

  for r in
    select k.id, k.partner_call_id, k.company_profile_id, k.posizione_id,
           p.ruolo as pos_ruolo, p.quota_ipotizzata_pct
    from public.partner_candidature k
    left join public.partner_call_posizioni p on p.id = k.posizione_id
    where k.stato = 'accettata'
    order by k.decisa_at, k.id
  loop
    if public.fn_partner_membro_inserisci(
         r.partner_call_id, r.company_profile_id, r.id, r.posizione_id,
         case when r.pos_ruolo = 'capofila' then 'capofila' else 'partner' end,
         r.quota_ipotizzata_pct, null, false) is not null then
      v_n := v_n + 1;
    end if;
  end loop;

  return v_n;
end;
$$;

comment on function public.fn_partner_backfill_membri() is
  'Backfill idempotente del consorzio: riga del creatore per ogni call con pubblicata_at (capofila se ruolo_creatore = capofila, quota_creatore_pct) e riga di Y per ogni candidatura accettata (proposto, posizione e quota della posizione). Le righe esistenti non cambiano. Ritorna le righe inserite.';

select public.fn_partner_backfill_membri();

-- Verifica: ogni call pubblicata ha la riga del creatore e ogni candidatura
-- accettata la riga di Y. Se no, WARNING e non abort (si rilancia il backfill).
do $$
declare
  v_senza_creatore integer;
  v_senza_membro   integer;
begin
  select count(*) into v_senza_creatore
  from public.partner_calls c
  where c.pubblicata_at is not null
    and not exists (select 1 from public.partner_call_membri m
                    where m.partner_call_id = c.id
                      and m.company_profile_id = c.company_profile_id);
  select count(*) into v_senza_membro
  from public.partner_candidature k
  where k.stato = 'accettata'
    and not exists (select 1 from public.partner_call_membri m
                    where m.partner_call_id = k.partner_call_id
                      and m.company_profile_id = k.company_profile_id);
  if v_senza_creatore > 0 or v_senza_membro > 0 then
    raise warning 'backfill 0040: % call pubblicate senza la riga del creatore e % candidature accettate senza la riga del membro: rieseguire select public.fn_partner_backfill_membri()',
      v_senza_creatore, v_senza_membro;
  end if;
end;
$$;

-- ----------------------------------------------------------------------------
-- 16) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova o ridefinita
--    (interne e backfill comprese) eseguibile dai ruoli esposti (Supabase
--    concede EXECUTE di default a PUBLIC; create or replace conserva i
--    permessi, la revoca si ripete comunque).
-- ----------------------------------------------------------------------------
alter table public.partner_call_membri enable row level security;
alter table public.partner_call_documenti enable row level security;

revoke all on public.partner_call_membri from anon, authenticated;
revoke all on public.partner_call_documenti from anon, authenticated;

revoke execute on function public.fn_partner_membro_inserisci(uuid, uuid, uuid, uuid, text, numeric, uuid, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_membro_creatore(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_aperta(text, date, date, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_esclusivita_violata(uuid, uuid, integer, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_membro_aggiorna(uuid, uuid, uuid, uuid, uuid, text, numeric)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_membro_conferma(uuid, uuid, uuid, uuid, text, uuid, numeric)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_membro_esci(uuid, uuid, uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_membro_esterno(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_documento_stato(uuid, uuid, uuid, uuid, text, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_validazione_salva(uuid, text, numeric)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_backfill_membri()
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0040 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle).
-- 1) Ripristino delle cinque funzioni della 0039: rieseguire i blocchi
--    «create or replace function public.fn_partner_call_aperta»,
--    «create or replace function public.fn_partner_esclusivita_violata»,
--    «create or replace function public.fn_partner_decidi»,
--    «create or replace function public.fn_partner_call_pubblica» e
--    «create or replace function public.fn_partner_call_sostituisci_posizioni» del file
--    0039_partenariato_candidature_chat.sql (stessa firma; le revoche
--    restano). Va fatto PRIMA del punto 2: le versioni 0040 usano le
--    funzioni e le tabelle nuove.
-- 2) drop function public.fn_partner_backfill_membri();
--    drop function public.fn_partner_call_validazione_salva(uuid, text, numeric);
--    drop function public.fn_partner_documento_stato(uuid, uuid, uuid, uuid, text, text, text);
--    drop function public.fn_partner_membro_esterno(uuid, uuid, uuid, uuid, jsonb);
--    drop function public.fn_partner_membro_esci(uuid, uuid, uuid, uuid);
--    drop function public.fn_partner_membro_conferma(uuid, uuid, uuid, uuid, text, uuid,
--      numeric);
--    drop function public.fn_partner_membro_aggiorna(uuid, uuid, uuid, uuid, uuid, text,
--      numeric);
--    drop function public.fn_partner_membro_creatore(uuid, uuid);
--    drop function public.fn_partner_membro_inserisci(uuid, uuid, uuid, uuid, text, numeric,
--      uuid, boolean);
-- 3) alter table public.partner_calls
--      drop constraint pcall_copertura_gap_ratio_check,
--      drop constraint pcall_validazione_coerente,
--      drop constraint pcall_validazione_esito_check,
--      drop column copertura_gap_ratio,
--      drop column validazione_at,
--      drop column validazione_esito;
-- 4) drop table public.partner_call_documenti;   -- si perde la checklist
--    drop table public.partner_call_membri;      -- si perdono i consorzi
-- Le righe di audit_log (partenariato.membro_*) restano.
-- ============================================================================
