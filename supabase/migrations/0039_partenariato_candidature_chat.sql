-- ============================================================================
-- BandoFit — DB primario, migration 0039: CANDIDATURE, INVITI E CHAT dei
-- partenariati (WP7, piano docs/partenariati.md §2.1 T3-T5, §2.7 K1-K4, §3
-- riga 0039, §7, §13 Q6/Q13/Q14/Q21/Q25).
--
--   1) partner_candidature — UNA tabella per candidature spontanee e inviti
--      (tipo), stati inviata → accettata | rifiutata | ritirata | scaduta, un
--      solo attivo (inviata o accettata) per call × azienda; transizioni SOLO
--      tramite le RPC di questo file;
--   2) partner_conversazioni — una per candidatura accettata (creata nella
--      stessa transazione dell'accettazione, K2);
--   3) partner_messaggi — testo 1..5000, IMMUTABILI (trigger: si può solo
--      oscurare per moderazione), idempotenti per client_msg_id;
--   4) partner_conversazione_letture — letture per utente e claim delle email
--      «una per raffica» (email_fino_a_id);
--   5) partner_segnalazioni.oggetto_tipo esteso a messaggio;
--   6) trigger: messaggi immutabili; chiusura della call → candidature e
--      inviti pendenti scaduti; revoca dell'opt-in (anche di sistema) → inviti
--      ricevuti scaduti e candidature inviate ritirate; le conversazioni
--      restano;
--   7) RPC: candidature usate (unica formula), candidatura, invito, decisione
--      (accettazione con conversazione e audit nella stessa transazione;
--      l'audit di rivelazione solo con p_rivela), ritiro, scadenze, messaggi,
--      letture, claim delle email, riepilogo, chiusura della conversazione;
--   8) ridefinizioni con la STESSA firma: fn_partner_call_sostituisci_posizioni
--      (0037: cancellava e reinseriva tutte le posizioni; ora aggiorna per id,
--      cancella solo le omesse e rifiuta di togliere una posizione con
--      candidature attive), fn_partenariati_snapshot (0037: candidature usate
--      contate con fn_partner_candidature_usate) e fn_partner_call_pubblica
--      (0037: ora controlla anche l'esclusività sulle candidature accettate).
--
-- Chi agisce (T4, Q14): solo il titolare dell'azienda attiva (attore = owner),
-- chat compresa. Verso terzi escono solo lo pseudonimo per call e la
-- valutazione in vista «terzi» calcolati dal backend: mai company_profile_id.
--
-- ORDINE DEI LOCK (quello globale della 0037, esteso):
--   owner (profiles FOR UPDATE) → azienda che agisce (company_profiles FOR NO
--   KEY UPDATE) → azienda e profilo partner della controparte Y (FOR KEY SHARE
--   / FOR SHARE, come la 0038: una revoca, una sospensione o un soft delete in
--   corso si attendono) → call (FOR UPDATE / FOR SHARE) → candidatura (FOR
--   UPDATE) → lock advisory per ultimo (esclusività).
-- Decisione e ritiro leggono prima la candidatura SENZA lock (per sapere chi
-- decide e su quale call) e poi bloccano nell'ordine. La chiusura della call
-- (call → candidature, via trigger) e la revoca dell'opt-in (azienda →
-- profilo → candidature, via trigger) rispettano lo stesso ordine.
--
-- ADDITIVA: tabelle, trigger e funzioni nuove; l'unico vincolo toccato è il
-- CHECK di partner_segnalazioni.oggetto_tipo (allargato); tre funzioni della
-- 0037 ridefinite con la stessa firma e lo stesso comportamento verso il
-- backend attuale (in più: posizioni con candidature, conteggio delle
-- candidature, esclusività alla pubblicazione). Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;).
-- Rollback documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Candidature e inviti (K1). company_profile_id = Y (chi si candida o è
--    invitato), creatore_company_profile_id = X (creatore della call).
-- ----------------------------------------------------------------------------
create table public.partner_candidature (
  id                          uuid primary key default gen_random_uuid(),
  partner_call_id             uuid not null
                                references public.partner_calls (id) on delete cascade,
  tipo                        text not null
    constraint pcand_tipo_check check (tipo in ('candidatura', 'invito')),
  company_profile_id          uuid not null
                                references public.company_profiles (id) on delete cascade,
  family_parent_id            uuid not null,
  creatore_company_profile_id uuid not null
                                references public.company_profiles (id) on delete cascade,
  posizione_id                uuid
                                references public.partner_call_posizioni (id) on delete set null,
  messaggio                   text,
  requisiti_dichiarati        uuid[] not null default '{}'
    constraint pcand_requisiti_dichiarati_check
    check (cardinality(requisiti_dichiarati) <= 40
           and array_position(requisiti_dichiarati, null) is null),
  valutazione                 jsonb not null default '{}'::jsonb
    constraint pcand_valutazione_check
    check (jsonb_typeof(valutazione) = 'object' and octet_length(valutazione::text) <= 65536),
  pseudonimo                  text not null
    constraint pcand_pseudonimo_check check (pseudonimo ~ '^[A-Z2-7]{16}$'),
  stato                       text not null default 'inviata'
    constraint pcand_stato_check
    check (stato in ('inviata', 'accettata', 'rifiutata', 'ritirata', 'scaduta')),
  motivo_chiusura             text
    constraint pcand_motivo_chiusura_check
    check (motivo_chiusura is null
           or motivo_chiusura in ('call_chiusa', 'ttl', 'opt_out', 'moderazione')),
  motivo_rifiuto              text
    constraint pcand_motivo_rifiuto_check
    check (motivo_rifiuto is null or char_length(motivo_rifiuto) <= 500),
  inviata_da_user_id          uuid not null,
  decisa_da_user_id           uuid,
  decisa_at                   timestamptz,
  chiusa_at                   timestamptz,
  scade_at                    timestamptz,
  conversazione_id            uuid,
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),
  constraint pcand_non_se_stessa check (company_profile_id <> creatore_company_profile_id),
  -- Candidatura: messaggio obbligatorio 50..2000; invito: facoltativo ≤ 1000.
  constraint pcand_messaggio_check
    check ((tipo <> 'candidatura'
            or (messaggio is not null and char_length(messaggio) between 50 and 2000))
           and (tipo <> 'invito' or messaggio is null or char_length(messaggio) <= 1000)),
  -- Solo la candidatura dichiara i requisiti coperti.
  constraint pcand_requisiti_solo_candidatura
    check (tipo = 'candidatura' or cardinality(requisiti_dichiarati) = 0),
  -- Solo l'invito scade (TTL), e ha sempre la scadenza.
  constraint pcand_scadenza_solo_invito check ((tipo = 'invito') = (scade_at is not null)),
  -- Macchina a stati (§7): decise = accettata | rifiutata (con chi e quando);
  -- chiuse senza decisione = ritirata | scaduta (quando, e per scaduta il perché).
  constraint pcand_decisa_coerente
    check ((stato in ('accettata', 'rifiutata')) = (decisa_at is not null)
           and (decisa_at is null) = (decisa_da_user_id is null)),
  constraint pcand_chiusa_coerente
    check ((stato in ('ritirata', 'scaduta')) = (chiusa_at is not null)),
  constraint pcand_motivo_chiusura_coerente
    check ((motivo_chiusura is null or stato in ('ritirata', 'scaduta'))
           and (stato <> 'scaduta' or motivo_chiusura is not null)),
  constraint pcand_motivo_rifiuto_coerente
    check (motivo_rifiuto is null or stato = 'rifiutata'),
  constraint pcand_conversazione_coerente
    check (conversazione_id is null or stato = 'accettata')
);

comment on table public.partner_candidature is
  'Candidature spontanee e inviti delle call di partenariato (WP7, K1): una sola tabella (tipo), stati inviata → accettata | rifiutata | ritirata | scaduta, un solo attivo (inviata o accettata) per call × azienda. Transizioni SOLO tramite le RPC della 0039 (lock owner → azienda → [controparte] → call → candidatura). Verso terzi solo pseudonimo e valutazione in vista «terzi»: MAI company_profile_id, family_parent_id, inviata_da_user_id. Cascade dalla call e dalle due aziende (Q21).';
comment on column public.partner_candidature.company_profile_id is
  'Y: l''azienda che si candida (tipo candidatura) o che è invitata (tipo invito).';
comment on column public.partner_candidature.family_parent_id is
  'Owner di Y al momento dell''invio: pool mensile delle candidature (Q1, solo tipo candidatura). Senza FK.';
comment on column public.partner_candidature.creatore_company_profile_id is
  'X: l''azienda creatrice della call (= partner_calls.company_profile_id).';
comment on column public.partner_candidature.posizione_id is
  'Posizione della call per cui Y si candida (obbligatoria per la candidatura, facoltativa per l''invito). Una posizione con candidature attive non si rimuove (fn_partner_call_sostituisci_posizioni); rimossa con sole candidature chiuse → NULL.';
comment on column public.partner_candidature.messaggio is
  'Messaggio di Y (candidatura, 50..2000) o di X (invito, ≤ 1000): controllato dal backend contro i recapiti (partenariato_anonimato) PRIMA dell''accettazione.';
comment on column public.partner_candidature.requisiti_dichiarati is
  'Requisiti della call che Y DICHIARA di coprire (id di partner_call_requisiti della stessa call al momento dell''invio): dato dichiarato, non verificato.';
comment on column public.partner_candidature.valutazione is
  'MatchOut in vista «terzi» calcolato dal backend all''invio (solo esiti e fasce, mai valori di bilancio né punteggio).';
comment on column public.partner_candidature.pseudonimo is
  'Pseudonimo di Y per QUESTA call (partenariato_accesso.pseudonimo: HMAC base32, 16 caratteri): l''handle con cui X vede Y nelle sue liste.';
comment on column public.partner_candidature.motivo_chiusura is
  'Chiusura senza decisione: call_chiusa (trigger sulla call), ttl (invito scaduto), opt_out (revoca dell''opt-in di Y), moderazione (WP9). Il ritiro volontario non ha motivo.';
comment on column public.partner_candidature.inviata_da_user_id is
  'Utente che ha inviato la candidatura o l''invito (il titolare, T4). Senza FK: MAI verso terzi.';
comment on column public.partner_candidature.scade_at is
  'Solo inviti: scadenza (TTL, default 14 giorni dal backend). Scadenza pigra in lettura e nello scheduler (fn_partner_scadi_inviti).';
comment on column public.partner_candidature.conversazione_id is
  'Conversazione aperta all''accettazione (K2).';

create trigger trg_partner_candidature_updated_at
  before update on public.partner_candidature
  for each row execute function public.set_updated_at();

-- Un solo attivo (inviata o accettata) per call × azienda.
create unique index partner_candidature_una_attiva on public.partner_candidature
  (partner_call_id, company_profile_id)
  where stato in ('inviata', 'accettata');
-- Liste di Y (inviate/ricevute) e cascade dall'azienda.
create index partner_candidature_azienda_idx on public.partner_candidature
  (company_profile_id, created_at desc);
-- Liste di X (ricevute/inviate) e cascade dall'azienda creatrice.
create index partner_candidature_creatore_idx on public.partner_candidature
  (creatore_company_profile_id, stato, created_at desc);
-- Pool mensile delle candidature per owner (fn_partner_candidature_usate).
create index partner_candidature_quota_idx on public.partner_candidature
  (family_parent_id, created_at) where tipo = 'candidatura';
-- Scadenza degli inviti (scheduler e lettura).
create index partner_candidature_scadenza_idx on public.partner_candidature (scade_at)
  where tipo = 'invito' and stato = 'inviata';
-- Candidature di una call (trigger di chiusura, contatori, tetto degli inviti).
create index partner_candidature_call_idx on public.partner_candidature
  (partner_call_id, stato);
-- Posizioni con candidature e ON DELETE SET NULL senza scansione completa.
create index partner_candidature_posizione_idx on public.partner_candidature (posizione_id)
  where posizione_id is not null;

-- ----------------------------------------------------------------------------
-- 2) Conversazioni (K2-K4): una per candidatura accettata.
-- ----------------------------------------------------------------------------
create table public.partner_conversazioni (
  id                  uuid primary key default gen_random_uuid(),
  partner_call_id     uuid not null references public.partner_calls (id) on delete cascade,
  candidatura_id      uuid not null
                        references public.partner_candidature (id) on delete cascade
    constraint pconv_candidatura_key unique,
  company_creatore_id uuid not null references public.company_profiles (id) on delete cascade,
  company_partner_id  uuid not null references public.company_profiles (id) on delete cascade,
  stato               text not null default 'aperta'
    constraint pconv_stato_check check (stato in ('aperta', 'chiusa')),
  chiusa_da_user_id   uuid,
  chiusa_at           timestamptz,
  ultimo_messaggio_id bigint,
  ultimo_messaggio_at timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  constraint pconv_aziende_diverse check (company_creatore_id <> company_partner_id),
  constraint pconv_chiusa_coerente check ((stato = 'chiusa') = (chiusa_at is not null)),
  constraint pconv_ultimo_coerente
    check ((ultimo_messaggio_id is null) = (ultimo_messaggio_at is null))
);

comment on table public.partner_conversazioni is
  'Conversazioni in-app tra creatore (X) e partner accettato (Y) (WP7, K2-K4), create da fn_partner_decidi nella transazione dell''accettazione. aperta → chiusa (solo X); sola lettura derivata se una delle due aziende non è più viva. Le leggono SOLO le due aziende. Cascade dalla call, dalla candidatura e dalle due aziende (Q21).';
comment on column public.partner_conversazioni.chiusa_da_user_id is
  'Titolare di X che ha chiuso la conversazione. Senza FK.';
comment on column public.partner_conversazioni.ultimo_messaggio_id is
  'Ultimo messaggio (partner_messaggi.id), aggiornato da fn_partner_invia_messaggio: ordinamento delle liste e tetto delle letture.';

create trigger trg_partner_conversazioni_updated_at
  before update on public.partner_conversazioni
  for each row execute function public.set_updated_at();

create index partner_conv_creatore_idx on public.partner_conversazioni
  (company_creatore_id, ultimo_messaggio_at desc nulls last);
create index partner_conv_partner_idx on public.partner_conversazioni
  (company_partner_id, ultimo_messaggio_at desc nulls last);
create index partner_conv_call_idx on public.partner_conversazioni (partner_call_id);

alter table public.partner_candidature
  add constraint partner_candidature_conversazione_fk
  foreign key (conversazione_id) references public.partner_conversazioni (id)
  on delete set null;

create index partner_candidature_conversazione_idx on public.partner_candidature
  (conversazione_id) where conversazione_id is not null;

-- ----------------------------------------------------------------------------
-- 3) Messaggi (K3): immutabili, idempotenti per (conversazione, client_msg_id).
--    Nessun filtro anti-contatti in chat (i contatti si scambiano qui).
-- ----------------------------------------------------------------------------
create table public.partner_messaggi (
  id                          bigint generated always as identity primary key,
  conversazione_id            uuid not null
                                references public.partner_conversazioni (id) on delete cascade,
  mittente_company_profile_id uuid not null,
  mittente_user_id            uuid not null,
  testo                       text not null
    constraint pmsg_testo_check
    check (char_length(testo) between 1 and 5000 and testo ~ '[^[:space:]]'),
  client_msg_id               uuid not null,
  nascosto_moderazione_at     timestamptz,
  nascosto_da                 uuid,
  created_at                  timestamptz not null default now(),
  constraint pmsg_client_msg_key unique (conversazione_id, client_msg_id),
  constraint pmsg_nascosto_coerente
    check ((nascosto_moderazione_at is null) = (nascosto_da is null))
);

comment on table public.partner_messaggi is
  'Messaggi delle conversazioni di partenariato (WP7, K3): testo 1..5000, IMMUTABILI (trg_partner_messaggi_immutabili: cambiano solo nascosto_moderazione_at e nascosto_da, oscuramento di moderazione), idempotenti per client_msg_id. Scritti SOLO da fn_partner_invia_messaggio. Un messaggio oscurato esce come {testo: null, nascosto: true}. Cascade dalla conversazione.';
comment on column public.partner_messaggi.mittente_company_profile_id is
  'Azienda mittente (una delle due parti). Senza FK: la cascade passa dalla conversazione.';
comment on column public.partner_messaggi.mittente_user_id is
  'Titolare che ha scritto (Q14). Senza FK: MAI verso terzi.';
comment on column public.partner_messaggi.client_msg_id is
  'Chiave di idempotenza generata dal client: un secondo invio con la stessa chiave restituisce il messaggio già scritto.';
comment on column public.partner_messaggi.nascosto_da is
  'Admin che ha oscurato il messaggio (moderazione, WP9). Senza FK.';

create index partner_messaggi_conv_idx on public.partner_messaggi (conversazione_id, id);

-- ----------------------------------------------------------------------------
-- 4) Letture per utente e claim delle email «una per raffica».
-- ----------------------------------------------------------------------------
create table public.partner_conversazione_letture (
  conversazione_id   uuid not null
                       references public.partner_conversazioni (id) on delete cascade,
  user_id            uuid not null references public.profiles (id) on delete cascade,
  company_profile_id uuid not null references public.company_profiles (id) on delete cascade,
  letto_fino_a_id    bigint not null default 0
    constraint pcl_letto_check check (letto_fino_a_id >= 0),
  email_fino_a_id    bigint
    constraint pcl_email_check check (email_fino_a_id is null or email_fino_a_id >= 0),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint partner_conversazione_letture_pkey primary key (conversazione_id, user_id)
);

comment on table public.partner_conversazione_letture is
  'Letture per utente delle conversazioni (WP7, K3): letto_fino_a_id (fn_partner_segna_letto, solo in avanti) e email_fino_a_id (fn_partner_claim_email_chat: una email per raffica, cioè fino a quando l''utente non legge). company_profile_id = l''azienda per cui l''utente legge (titolare o membro attivo con visibilità). Cascade da conversazione, utente e azienda.';

create trigger trg_partner_conversazione_letture_updated_at
  before update on public.partner_conversazione_letture
  for each row execute function public.set_updated_at();

create index partner_letture_user_idx on public.partner_conversazione_letture (user_id);
create index partner_letture_company_idx on public.partner_conversazione_letture
  (company_profile_id);

-- ----------------------------------------------------------------------------
-- 5) Segnalazioni DSA: anche i messaggi (oggetto_id = id del messaggio).
-- ----------------------------------------------------------------------------
alter table public.partner_segnalazioni
  drop constraint ps_oggetto_tipo_check,
  add constraint ps_oggetto_tipo_check
    check (oggetto_tipo in ('call', 'profilo', 'messaggio'));

-- ----------------------------------------------------------------------------
-- 6) Funzioni interne.
-- ----------------------------------------------------------------------------

-- Utente dell'azienda: il titolare, oppure un membro ATTIVO della sua famiglia
-- con visibilità sull'azienda (0031); profilo attivo. Stessa regola dei
-- destinatari del backend (partenariato_notifiche.destinatari_azienda).
create or replace function public.fn_partner_utente_di_azienda(p_user uuid, p_company uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.company_profiles cp
    join public.profiles pr on pr.id = p_user and pr.is_active
    where cp.id = p_company
      and (cp.parent_id = p_user
           or exists (
             select 1
             from public.family_members fm
             join public.family_member_company_access a on a.family_member_id = fm.id
             where fm.parent_id = cp.parent_id
               and fm.member_id = p_user
               and fm.status = 'active'
               and a.company_profile_id = cp.id))
  );
$$;

comment on function public.fn_partner_utente_di_azienda(uuid, uuid) is
  'Interna: l''utente (profilo attivo) è il titolare dell''azienda o un membro attivo della sua famiglia con visibilità su di lei (0031).';

-- Lock owner → azienda che agisce (viva e del titolare).
create or replace function public.fn_partner_cand_blocca_azienda(p_owner uuid, p_company uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform 1 from public.profiles where id = p_owner for update;
  if not found then
    raise exception 'Titolare non trovato' using detail = 'owner_not_found';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner and deleted_at is null and archived_at is null
  for no key update;
  if not found then
    raise exception 'Azienda non disponibile' using detail = 'azienda_non_disponibile';
  end if;
end;
$$;

comment on function public.fn_partner_cand_blocca_azienda(uuid, uuid) is
  'Interna: lock della riga profiles dell''owner (FOR UPDATE) e poi della sua azienda viva (FOR NO KEY UPDATE). Detail: owner_not_found | azienda_non_disponibile.';

-- Candidature usate nel mese solare Europe/Rome dal pool dell'owner: righe
-- tipo candidatura di tutte le sue aziende, in qualunque stato (anche
-- ritirate, rifiutate o scadute). Formula UNICA: RPC e snapshot.
create or replace function public.fn_partner_candidature_usate(p_owner uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select count(*)::integer
  from public.partner_candidature
  where family_parent_id = p_owner
    and tipo = 'candidatura'
    and created_at >= (date_trunc('month', now() at time zone 'Europe/Rome')
                       at time zone 'Europe/Rome')
    and created_at < ((date_trunc('month', now() at time zone 'Europe/Rome')
                       + interval '1 month') at time zone 'Europe/Rome');
$$;

comment on function public.fn_partner_candidature_usate(uuid) is
  'Candidature spontanee (tipo candidatura, qualunque stato) inviate nel mese solare corrente Europe/Rome da tutte le aziende dell''owner (pool, Q1). Unica formula di fn_partner_invia_candidatura e fn_partenariati_snapshot.';

-- Limite mensile del piano: NULL = illimitato; chiave assente o risposta non
-- valida = 0 (fail-closed), come fn_partner_call_limite_attive (0037).
create or replace function public.fn_partner_candidature_limite(p_owner uuid)
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
  if v_lim is null or jsonb_typeof(v_lim) <> 'object' or not (v_lim ? 'candidature_mese') then
    return 0;
  end if;
  return (v_lim ->> 'candidature_mese')::integer;
end;
$$;

comment on function public.fn_partner_candidature_limite(uuid) is
  'Interna: candidature_mese di fn_partenariati_limiti (0036). NULL = illimitato, 0 = esclusa; risposta senza la chiave = 0 (fail-closed).';

-- La call accetta ancora candidature, inviti e accettazioni: pubblicata, non
-- scaduta (Europe/Rome), bando non scaduto, creatore vivo (filtro 1 del
-- matching, Appendice B).
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
      where cp.id = p_creatore and cp.deleted_at is null and cp.archived_at is null),
    false);
$$;

comment on function public.fn_partner_call_aperta(text, date, date, uuid) is
  'Interna: la call (stato, scadenza, scadenza del bando, azienda creatrice) è pubblicata, la sua scadenza e quella del bando non sono passate (Europe/Rome) e l''azienda creatrice è viva.';

-- Esclusività (§2.7, Appendice B filtro 9): Y ha un altro impegno sullo
-- stesso bando — creatrice di un'altra call pubblicata, oppure un'altra
-- candidatura accettata su una call non annullata (chiusa_annullata: il
-- progetto non c'è più e l'accettata non si può ritirare; una call
-- completata, scaduta o sospesa conta ancora, perché il partenariato può
-- essere andato avanti) — e o questa call
-- o quella dell'altro impegno è esclusiva (il vincolo è del bando: basta che
-- uno dei due creatori l'abbia confermato). WP8 la ridefinisce con la stessa
-- firma per i membri.
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
        and c.stato = 'pubblicata'
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
        and (coalesce(p_esclusiva, true) or c.esclusivita));
$$;

comment on function public.fn_partner_esclusivita_violata(uuid, uuid, integer, boolean) is
  'Interna: l''azienda ha un altro impegno sullo stesso bando (creatrice di un''altra call pubblicata o candidatura accettata su un''altra call non annullata) e questa call (p_esclusiva, NULL = true) o quella dell''altro impegno è esclusiva. WP8 la ridefinisce con la stessa firma per i membri.';

-- ----------------------------------------------------------------------------
-- 7) Trigger.
-- ----------------------------------------------------------------------------

-- Messaggi immutabili: si può cambiare solo l'oscuramento di moderazione.
-- Niente trigger su DELETE (bloccherebbe la cascade, Q21).
create or replace function public.fn_partner_messaggi_immutabili()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.id is distinct from old.id
     or new.conversazione_id is distinct from old.conversazione_id
     or new.mittente_company_profile_id is distinct from old.mittente_company_profile_id
     or new.mittente_user_id is distinct from old.mittente_user_id
     or new.testo is distinct from old.testo
     or new.client_msg_id is distinct from old.client_msg_id
     or new.created_at is distinct from old.created_at then
    raise exception 'I messaggi non si modificano' using detail = 'messaggio_immutabile';
  end if;
  return new;
end;
$$;

comment on function public.fn_partner_messaggi_immutabili() is
  'Trigger BEFORE UPDATE su partner_messaggi: cambiano solo nascosto_moderazione_at e nascosto_da (oscuramento di moderazione). Detail: messaggio_immutabile.';

create trigger trg_partner_messaggi_immutabili
  before update on public.partner_messaggi
  for each row execute function public.fn_partner_messaggi_immutabili();

-- Chiusura della call (pubblicata o sospesa → chiusa o scaduta, da qualunque
-- percorso: creatore, scheduler, moderazione): candidature e inviti pendenti
-- diventano scaduti (call_chiusa, o moderazione se la call è chiusa per
-- moderazione). Le conversazioni restano aperte: servono dopo
-- chiusa_completata. Ordine call → candidature (la call è già bloccata
-- dall'UPDATE che fa scattare il trigger).
create or replace function public.fn_partner_calls_chiudi_candidature()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.partner_candidature
  set stato           = 'scaduta',
      motivo_chiusura = case when new.motivo_chiusura = 'moderazione' then 'moderazione'
                             else 'call_chiusa' end,
      chiusa_at       = now()
  where partner_call_id = new.id and stato = 'inviata';
  return null;
end;
$$;

comment on function public.fn_partner_calls_chiudi_candidature() is
  'Trigger AFTER UPDATE OF stato su partner_calls (pubblicata | sospesa_moderazione → chiusa_completata | chiusa_annullata | scaduta): candidature e inviti inviata → scaduta con motivo call_chiusa (moderazione se la call è chiusa per moderazione). Accettate e conversazioni restano.';

create trigger trg_partner_calls_chiudi_candidature
  after update of stato on public.partner_calls
  for each row
  when (old.stato in ('pubblicata', 'sospesa_moderazione')
        and new.stato in ('chiusa_completata', 'chiusa_annullata', 'scaduta'))
  execute function public.fn_partner_calls_chiudi_candidature();

-- Revoca dell'opt-in (Q25, P6): scatta su OGNI riga revocato del registro
-- dei consensi — fn_partner_consenso e revoca automatica di sistema
-- (trg_cpp_revoca_su_cambio_azienda, 0035) — nella stessa transazione, dopo i
-- lock azienda → profilo di chi revoca. fn_partner_consenso non si ridefinisce.
create or replace function public.fn_partner_consents_chiudi_pendenti()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.fn_partner_scadi_per_opt_out(new.company_profile_id);
  return null;
end;
$$;

comment on function public.fn_partner_consents_chiudi_pendenti() is
  'Trigger AFTER INSERT su partner_consents con azione revocato (utente o sistema): fn_partner_scadi_per_opt_out sull''azienda che revoca.';

-- ----------------------------------------------------------------------------
-- 8) fn_partner_scadi_per_opt_out — revoca dell'opt-in di Y: inviti ricevuti
--    pendenti → scaduta (opt_out), candidature inviate pendenti → ritirata
--    (opt_out). Accettate e conversazioni restano. Ritorna quante righe.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_scadi_per_opt_out(p_company uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_n integer;
begin
  update public.partner_candidature
  set stato           = case tipo when 'invito' then 'scaduta' else 'ritirata' end,
      motivo_chiusura = 'opt_out',
      chiusa_at       = now()
  where company_profile_id = p_company and stato = 'inviata';
  get diagnostics v_n = row_count;
  return v_n;
end;
$$;

comment on function public.fn_partner_scadi_per_opt_out(uuid) is
  'Revoca dell''opt-in di un''azienda (chiamata dal trigger su partner_consents): inviti ricevuti inviata → scaduta, candidature inviate inviata → ritirata, entrambe con motivo opt_out. Ritorna le righe chiuse.';

create trigger trg_partner_consents_chiudi_pendenti
  after insert on public.partner_consents
  for each row
  when (new.azione = 'revocato')
  execute function public.fn_partner_consents_chiudi_pendenti();

-- ----------------------------------------------------------------------------
-- 9) fn_partner_invia_candidatura — candidatura spontanea di Y. p_payload:
--    {owner_id, company_id, attore_id, call_id, posizione_id, messaggio,
--    requisiti_dichiarati?, valutazione, pseudonimo, richiedi_non_sandbox?}
--    (nient'altro; richiedi_non_sandbox assente o null = true). Nell'ordine:
--      - parametri (parametri_non_validi, requisiti duplicati →
--        requisiti_non_validi); attore = titolare (attore_non_titolare);
--      - lock owner → azienda viva (owner_not_found | azienda_non_disponibile);
--      - piano con 0 candidature al mese → funzione_non_inclusa;
--      - profilo partner visibile e non sospeso, FOR SHARE (Q25:
--        profilo_partner_non_attivo); identità dal registro (T5:
--        identita_non_verificata);
--      - call FOR SHARE: aperta (call_non_attiva), non della stessa azienda o
--        dello stesso owner (stesso_gruppo), visibile a tutti
--        (call_solo_invitati);
--      - posizione della call (posizione_non_valida), requisiti dichiarati
--        della call (requisiti_non_validi);
--      - un invito scaduto per TTL ma non ancora marcato si chiude; un altro
--        attivo → invito_gia_attivo | candidatura_gia_attiva;
--      - candidature del mese ≥ limite → candidature_esaurite (NULL =
--        illimitato);
--      - insert + audit partenariato.candidatura_inviata.
--    Ritorna {candidatura, usate, limite}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_invia_candidatura(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi      constant text[] := array['owner_id', 'company_id', 'attore_id', 'call_id',
                                         'posizione_id', 'messaggio', 'requisiti_dichiarati',
                                         'valutazione', 'pseudonimo', 'richiedi_non_sandbox'];
  v_owner       uuid;
  v_company     uuid;
  v_attore      uuid;
  v_call_id     uuid;
  v_posizione   uuid;
  v_messaggio   text;
  v_requisiti   uuid[];
  v_valutazione jsonb;
  v_pseudonimo  text;
  v_non_sandbox boolean;
  v_limite      integer;
  v_usate       integer;
  v_prof        public.company_partner_profiles%rowtype;
  v_call        public.partner_calls%rowtype;
  v_esistente   text;
  v_cand        public.partner_candidature%rowtype;
begin
  if jsonb_typeof(p_payload) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_payload) k where not (k = any (v_chiavi))) then
    raise exception 'Parametri della candidatura non validi' using detail = 'parametri_non_validi';
  end if;
  begin
    v_owner := (p_payload ->> 'owner_id')::uuid;
    v_company := (p_payload ->> 'company_id')::uuid;
    v_attore := (p_payload ->> 'attore_id')::uuid;
    v_call_id := (p_payload ->> 'call_id')::uuid;
    v_posizione := (p_payload ->> 'posizione_id')::uuid;
    v_requisiti := case
      when jsonb_typeof(p_payload -> 'requisiti_dichiarati') is null
           or jsonb_typeof(p_payload -> 'requisiti_dichiarati') = 'null' then '{}'::uuid[]
      else array(select e::uuid
                 from jsonb_array_elements_text(p_payload -> 'requisiti_dichiarati') e)
    end;
    v_non_sandbox := (p_payload ->> 'richiedi_non_sandbox')::boolean;
  exception when data_exception then
    raise exception 'Parametri della candidatura non validi' using detail = 'parametri_non_validi';
  end;
  v_messaggio := btrim(p_payload ->> 'messaggio');
  v_valutazione := p_payload -> 'valutazione';
  v_pseudonimo := p_payload ->> 'pseudonimo';
  if v_owner is null or v_company is null or v_call_id is null or v_posizione is null
     or v_messaggio is null or char_length(v_messaggio) not between 50 and 2000
     or jsonb_typeof(v_valutazione) is distinct from 'object'
     or v_pseudonimo is null or v_pseudonimo !~ '^[A-Z2-7]{16}$'
     or cardinality(v_requisiti) > 40 or array_position(v_requisiti, null) is not null then
    raise exception 'Parametri della candidatura non validi' using detail = 'parametri_non_validi';
  end if;
  if (select count(distinct r) from unnest(v_requisiti) r) <> cardinality(v_requisiti) then
    raise exception 'Requisiti dichiarati ripetuti' using detail = 'requisiti_non_validi';
  end if;
  if v_attore is null or v_attore is distinct from v_owner then
    raise exception 'Le candidature le invia il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform public.fn_partner_cand_blocca_azienda(v_owner, v_company);

  v_limite := public.fn_partner_candidature_limite(v_owner);
  if v_limite is not null and v_limite <= 0 then
    raise exception 'Il tuo piano non include le candidature spontanee'
      using detail = 'funzione_non_inclusa';
  end if;

  select * into v_prof from public.company_partner_profiles
  where company_profile_id = v_company
  for share;
  if not found or not v_prof.visibile_come_partner or v_prof.sospeso_at is not null then
    raise exception 'Per candidarti attiva la visibilità come partner'
      using detail = 'profilo_partner_non_attivo';
  end if;
  if not public.fn_partenariato_identita_ok(v_company, v_non_sandbox) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;

  select * into v_call from public.partner_calls where id = v_call_id for share;
  if not found or not public.fn_partner_call_aperta(v_call.stato, v_call.scadenza_call,
                                                v_call.bando_scadenza,
                                                v_call.company_profile_id) then
    raise exception 'La call non accetta candidature' using detail = 'call_non_attiva';
  end if;
  if v_call.company_profile_id = v_company or v_call.family_parent_id = v_owner
     or exists (select 1 from public.company_profiles
                where id = v_call.company_profile_id and parent_id = v_owner) then
    raise exception 'Non puoi candidarti a una call del tuo gruppo' using detail = 'stesso_gruppo';
  end if;
  if v_call.visibilita <> 'pubblica' then
    raise exception 'La call è solo su invito' using detail = 'call_solo_invitati';
  end if;

  if not exists (select 1 from public.partner_call_posizioni
                 where id = v_posizione and call_id = v_call_id) then
    raise exception 'Posizione non trovata in questa call' using detail = 'posizione_non_valida';
  end if;
  if not (v_requisiti <@ array(select id from public.partner_call_requisiti
                               where call_id = v_call_id)) then
    raise exception 'Requisito non trovato in questa call' using detail = 'requisiti_non_validi';
  end if;

  -- Un invito scaduto per TTL ma non ancora marcato non occupa il posto.
  update public.partner_candidature
  set stato = 'scaduta', motivo_chiusura = 'ttl', chiusa_at = now()
  where partner_call_id = v_call_id and company_profile_id = v_company
    and tipo = 'invito' and stato = 'inviata' and scade_at <= now();

  select tipo into v_esistente from public.partner_candidature
  where partner_call_id = v_call_id and company_profile_id = v_company
    and stato in ('inviata', 'accettata')
  limit 1;
  if v_esistente = 'invito' then
    raise exception 'Hai già un invito per questa call' using detail = 'invito_gia_attivo';
  elsif v_esistente is not null then
    raise exception 'Hai già una candidatura per questa call'
      using detail = 'candidatura_gia_attiva';
  end if;

  if v_limite is not null then
    v_usate := public.fn_partner_candidature_usate(v_owner);
    if v_usate >= v_limite then
      raise exception 'Hai usato tutte le candidature del mese'
        using detail = 'candidature_esaurite';
    end if;
  end if;

  begin
    insert into public.partner_candidature
      (partner_call_id, tipo, company_profile_id, family_parent_id,
       creatore_company_profile_id, posizione_id, messaggio, requisiti_dichiarati,
       valutazione, pseudonimo, inviata_da_user_id)
    values
      (v_call_id, 'candidatura', v_company, v_owner, v_call.company_profile_id, v_posizione,
       v_messaggio, v_requisiti, v_valutazione, v_pseudonimo, v_attore)
    returning * into v_cand;
  exception
    when unique_violation then
      raise exception 'Hai già una candidatura per questa call'
        using detail = 'candidatura_gia_attiva';
    when check_violation or not_null_violation or foreign_key_violation
         or data_exception then
      raise exception 'Parametri della candidatura non validi'
        using detail = 'parametri_non_validi';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (v_attore, 'partenariato.candidatura_inviata', v_call.family_parent_id, v_owner,
          jsonb_build_object('candidatura_id', v_cand.id, 'call_id', v_call_id,
                             'company_profile_id', v_company,
                             'creatore_company_profile_id', v_call.company_profile_id));

  return jsonb_build_object(
    'candidatura', to_jsonb(v_cand),
    'usate', public.fn_partner_candidature_usate(v_owner),
    'limite', v_limite);
end;
$$;

comment on function public.fn_partner_invia_candidatura(jsonb) is
  'Candidatura spontanea di Y (payload {owner_id, company_id, attore_id, call_id, posizione_id, messaggio 50..2000, requisiti_dichiarati?, valutazione, pseudonimo, richiedi_non_sandbox?}; lock owner → azienda → profilo partner → call FOR SHARE): piano (0 → funzione_non_inclusa, NULL = illimitato, pool dell''owner nel mese Europe/Rome), opt-in visibile e non sospeso, identità dal registro, call aperta, non del proprio gruppo, pubblica, posizione e requisiti della call, un solo attivo. Audit partenariato.candidatura_inviata. Ritorna {candidatura, usate, limite}. Detail: parametri_non_validi | requisiti_non_validi | attore_non_titolare | owner_not_found | azienda_non_disponibile | funzione_non_inclusa | profilo_partner_non_attivo | identita_non_verificata | call_non_attiva | stesso_gruppo | call_solo_invitati | posizione_non_valida | invito_gia_attivo | candidatura_gia_attiva | candidature_esaurite.';

-- ----------------------------------------------------------------------------
-- 10) fn_partner_invita — invito di X a Y (anche Gratuito: nessun limite di
--    piano per chi riceve). p_payload: {owner_id, company_id, attore_id,
--    call_id, invitato_company_id, posizione_id?, messaggio?, valutazione,
--    pseudonimo, max_inviti, ttl_giorni} (nient'altro). invitato_company_id
--    è Y risolto dal backend dallo pseudonimo (mai dal client). Nell'ordine:
--      - parametri (max_inviti 0..1000, ttl_giorni 1..90, messaggio ≤ 1000);
--        attore = titolare;
--      - lock owner → azienda viva di X;
--      - Y: azienda viva FOR KEY SHARE e profilo partner FOR SHARE visibile,
--        non sospeso, che accetta inviti, di un altro owner → altrimenti
--        partner_non_disponibile (codice NEUTRO unico);
--      - call di X FOR UPDATE (call_not_found), aperta (call_non_attiva);
--      - posizione della call se indicata (posizione_non_valida);
--      - inviti della call scaduti per TTL → scaduta (ttl); un attivo per Y →
--        invito_gia_attivo | candidatura_gia_attiva; un invito che Y ha già
--        rifiutato su questa call → partner_non_disponibile (il rifiuto vale
--        per la call); inviti inviata della call ≥ max_inviti →
--        inviti_esauriti_call;
--      - insert con scade_at = now() + ttl_giorni + audit
--        partenariato.invito_inviato.
--    Ritorna {candidatura, inviti_attivi, max_inviti}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_invita(p_payload jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi      constant text[] := array['owner_id', 'company_id', 'attore_id', 'call_id',
                                         'invitato_company_id', 'posizione_id', 'messaggio',
                                         'valutazione', 'pseudonimo', 'max_inviti',
                                         'ttl_giorni'];
  v_owner       uuid;
  v_company     uuid;
  v_attore      uuid;
  v_call_id     uuid;
  v_y           uuid;
  v_posizione   uuid;
  v_messaggio   text;
  v_valutazione jsonb;
  v_pseudonimo  text;
  v_max         integer;
  v_ttl         integer;
  v_y_owner     uuid;
  v_prof        public.company_partner_profiles%rowtype;
  v_call        public.partner_calls%rowtype;
  v_esistente   text;
  v_n           integer;
  v_cand        public.partner_candidature%rowtype;
begin
  if jsonb_typeof(p_payload) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_payload) k where not (k = any (v_chiavi))) then
    raise exception 'Parametri dell''invito non validi' using detail = 'parametri_non_validi';
  end if;
  begin
    v_owner := (p_payload ->> 'owner_id')::uuid;
    v_company := (p_payload ->> 'company_id')::uuid;
    v_attore := (p_payload ->> 'attore_id')::uuid;
    v_call_id := (p_payload ->> 'call_id')::uuid;
    v_y := (p_payload ->> 'invitato_company_id')::uuid;
    v_posizione := (p_payload ->> 'posizione_id')::uuid;
    v_max := (p_payload ->> 'max_inviti')::integer;
    v_ttl := (p_payload ->> 'ttl_giorni')::integer;
  exception when data_exception then
    raise exception 'Parametri dell''invito non validi' using detail = 'parametri_non_validi';
  end;
  v_messaggio := nullif(btrim(p_payload ->> 'messaggio'), '');
  v_valutazione := p_payload -> 'valutazione';
  v_pseudonimo := p_payload ->> 'pseudonimo';
  if v_owner is null or v_company is null or v_call_id is null or v_y is null
     or v_max is null or v_max not between 0 and 1000
     or v_ttl is null or v_ttl not between 1 and 90
     or char_length(v_messaggio) > 1000
     or jsonb_typeof(v_valutazione) is distinct from 'object'
     or v_pseudonimo is null or v_pseudonimo !~ '^[A-Z2-7]{16}$' then
    raise exception 'Parametri dell''invito non validi' using detail = 'parametri_non_validi';
  end if;
  if v_attore is null or v_attore is distinct from v_owner then
    raise exception 'Gli inviti li invia il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform public.fn_partner_cand_blocca_azienda(v_owner, v_company);

  -- Livello azienda della controparte: una revoca, una sospensione o un soft
  -- delete in corso si attendono e si rileggono.
  select parent_id into v_y_owner from public.company_profiles
  where id = v_y and deleted_at is null and archived_at is null
  for key share;
  if not found or v_y = v_company or v_y_owner = v_owner then
    raise exception 'Azienda non disponibile per un invito' using detail = 'partner_non_disponibile';
  end if;
  select * into v_prof from public.company_partner_profiles
  where company_profile_id = v_y
  for share;
  if not found or not v_prof.visibile_come_partner or v_prof.sospeso_at is not null
     or not v_prof.accetta_inviti then
    raise exception 'Azienda non disponibile per un invito' using detail = 'partner_non_disponibile';
  end if;

  select * into v_call from public.partner_calls
  where id = v_call_id and company_profile_id = v_company and family_parent_id = v_owner
  for update;
  if not found then
    raise exception 'Call di partenariato non trovata' using detail = 'call_not_found';
  end if;
  if not public.fn_partner_call_aperta(v_call.stato, v_call.scadenza_call,
                                                v_call.bando_scadenza,
                                                v_call.company_profile_id) then
    raise exception 'La call non accetta inviti' using detail = 'call_non_attiva';
  end if;

  if v_posizione is not null and not exists (
    select 1 from public.partner_call_posizioni where id = v_posizione and call_id = v_call_id
  ) then
    raise exception 'Posizione non trovata in questa call' using detail = 'posizione_non_valida';
  end if;

  -- Inviti della call scaduti per TTL ma non ancora marcati: si chiudono qui
  -- (liberano il posto e il tetto).
  update public.partner_candidature
  set stato = 'scaduta', motivo_chiusura = 'ttl', chiusa_at = now()
  where partner_call_id = v_call_id and tipo = 'invito' and stato = 'inviata'
    and scade_at <= now();

  select tipo into v_esistente from public.partner_candidature
  where partner_call_id = v_call_id and company_profile_id = v_y
    and stato in ('inviata', 'accettata')
  limit 1;
  if v_esistente = 'invito' then
    raise exception 'Questa azienda è già stata invitata' using detail = 'invito_gia_attivo';
  elsif v_esistente is not null then
    raise exception 'Questa azienda si è già candidata' using detail = 'candidatura_gia_attiva';
  end if;
  -- Un invito che Y ha già RIFIUTATO su questa call non si ripete: il
  -- rifiuto vale per tutta la call (codice neutro). Y può sempre candidarsi.
  if exists (select 1 from public.partner_candidature
             where partner_call_id = v_call_id and company_profile_id = v_y
               and tipo = 'invito' and stato = 'rifiutata') then
    raise exception 'Azienda non disponibile per un invito' using detail = 'partner_non_disponibile';
  end if;

  select count(*) into v_n from public.partner_candidature
  where partner_call_id = v_call_id and tipo = 'invito' and stato = 'inviata';
  if v_n >= v_max then
    raise exception 'Hai raggiunto il numero massimo di inviti in attesa per questa call'
      using detail = 'inviti_esauriti_call';
  end if;

  begin
    insert into public.partner_candidature
      (partner_call_id, tipo, company_profile_id, family_parent_id,
       creatore_company_profile_id, posizione_id, messaggio, valutazione, pseudonimo,
       inviata_da_user_id, scade_at)
    values
      (v_call_id, 'invito', v_y, v_y_owner, v_company, v_posizione, v_messaggio,
       v_valutazione, v_pseudonimo, v_attore, now() + make_interval(days => v_ttl))
    returning * into v_cand;
  exception
    when unique_violation then
      raise exception 'Questa azienda è già stata invitata' using detail = 'invito_gia_attivo';
    when check_violation or not_null_violation or foreign_key_violation
         or data_exception then
      raise exception 'Parametri dell''invito non validi' using detail = 'parametri_non_validi';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (v_attore, 'partenariato.invito_inviato', v_y_owner, v_owner,
          jsonb_build_object('candidatura_id', v_cand.id, 'call_id', v_call_id,
                             'company_profile_id', v_y,
                             'creatore_company_profile_id', v_company));

  return jsonb_build_object(
    'candidatura', to_jsonb(v_cand),
    'inviti_attivi', v_n + 1,
    'max_inviti', v_max);
end;
$$;

comment on function public.fn_partner_invita(jsonb) is
  'Invito di X a Y (payload {owner_id, company_id, attore_id, call_id, invitato_company_id, posizione_id?, messaggio? ≤ 1000, valutazione, pseudonimo, max_inviti 0..1000, ttl_giorni 1..90}; lock owner X → azienda X → azienda Y FOR KEY SHARE → profilo Y FOR SHARE → call FOR UPDATE): Y viva, visibile, non sospesa, che accetta inviti e di un altro owner (altrimenti partner_non_disponibile, neutro); call di X aperta; inviti scaduti per TTL chiusi; un solo attivo per Y; nessun invito già rifiutato da Y sulla call (partner_non_disponibile); inviti inviata della call < max_inviti. scade_at = now() + ttl_giorni. Audit partenariato.invito_inviato. Ritorna {candidatura, inviti_attivi, max_inviti}. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | azienda_non_disponibile | partner_non_disponibile | call_not_found | call_non_attiva | posizione_non_valida | invito_gia_attivo | candidatura_gia_attiva | inviti_esauriti_call.';

-- ----------------------------------------------------------------------------
-- 11) fn_partner_decidi — accetta o rifiuta. Decide X le candidature e Y gli
--    inviti (l'altra parte, o chiunque altro → candidatura_non_trovata).
--    Lettura senza lock della candidatura, poi: owner → azienda che decide →
--    (accettazione) azienda di Y FOR KEY SHARE e profilo partner di Y FOR
--    SHARE → call FOR SHARE → candidatura FOR UPDATE → (accettazione) lock
--    advisory dell'esclusività per Y × bando. Controlli: stato inviata
--    (candidatura_gia_decisa), invito non scaduto (invito_scaduto); per
--    accettare: call aperta (call_non_attiva), identità di chi decide
--    (identita_non_verificata), profilo partner di Y visibile e non sospeso
--    (se decide Y: profilo_partner_non_attivo), controparte viva (non
--    eliminata né archiviata, owner attivo) e con identità verificata
--    (controparte_non_disponibile), owner diversi
--    (stesso_gruppo), esclusività (esclusivita_violata). Accettazione (K2), in
--    questa transazione: conversazione, candidatura accettata, audit
--    partenariato.candidatura_accettata e, SOLO con p_rivela,
--    partenariato.identita_rivelata e partenariato.contatti_rivelati (oggi
--    il backend passa false: RIVELAZIONE_IDENTITA_DISPONIBILE). Rifiuto:
--    motivo facoltativo ≤ 500 + audit partenariato.candidatura_rifiutata.
--    p_richiedi_non_sandbox (NULL o assente = true) come le altre RPC con
--    identità (T5). Ritorna {candidatura, conversazione_id}.
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

  return jsonb_build_object('candidatura', to_jsonb(v_k), 'conversazione_id', v_conv);
end;
$$;

comment on function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean, boolean) is
  'Decisione su una candidatura (X) o su un invito (Y): accetta | rifiuta. Lettura senza lock, poi owner → azienda che decide → (accetta) azienda Y FOR KEY SHARE e profilo Y FOR SHARE → call FOR SHARE → candidatura FOR UPDATE → (accetta) advisory esclusività Y × bando. Accettazione nella stessa transazione: conversazione, stato accettata, audit partenariato.candidatura_accettata e, solo con p_rivela, partenariato.identita_rivelata + partenariato.contatti_rivelati. Rifiuto con motivo ≤ 500. p_richiedi_non_sandbox NULL o assente = true. Ritorna {candidatura, conversazione_id}. Detail: parametri_non_validi | attore_non_titolare | candidatura_non_trovata | owner_not_found | azienda_non_disponibile | candidatura_gia_decisa | invito_scaduto | call_non_attiva | identita_non_verificata | profilo_partner_non_attivo | controparte_non_disponibile | stesso_gruppo | esclusivita_violata.';

-- ----------------------------------------------------------------------------
-- 12) fn_partner_ritira — Y ritira la propria candidatura, X il proprio
--    invito (altrimenti candidatura_non_trovata). Solo da inviata
--    (candidatura_gia_decisa; invito scaduto → invito_scaduto). Lock owner →
--    azienda → call FOR SHARE → candidatura FOR UPDATE. Audit
--    partenariato.candidatura_ritirata. Ritorna {candidatura}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_ritira(
  p_candidatura uuid,
  p_attore      uuid,
  p_owner       uuid,
  p_company     uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_k      public.partner_candidature%rowtype;
  v_ritira uuid;
begin
  if p_candidatura is null then
    raise exception 'Candidatura non valida' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le candidature le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_k from public.partner_candidature where id = p_candidatura;
  if not found then
    raise exception 'Candidatura non trovata' using detail = 'candidatura_non_trovata';
  end if;
  v_ritira := case v_k.tipo when 'candidatura' then v_k.company_profile_id
                            else v_k.creatore_company_profile_id end;
  if p_company is null or p_company is distinct from v_ritira then
    raise exception 'Candidatura non trovata' using detail = 'candidatura_non_trovata';
  end if;

  perform public.fn_partner_cand_blocca_azienda(p_owner, p_company);
  perform 1 from public.partner_calls where id = v_k.partner_call_id for share;

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

  update public.partner_candidature
  set stato = 'ritirata', chiusa_at = now()
  where id = p_candidatura
  returning * into v_k;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.candidatura_ritirata', p_owner, p_owner,
          jsonb_build_object('candidatura_id', v_k.id, 'call_id', v_k.partner_call_id,
                             'tipo', v_k.tipo, 'company_profile_id', v_k.company_profile_id,
                             'creatore_company_profile_id', v_k.creatore_company_profile_id));

  return jsonb_build_object('candidatura', to_jsonb(v_k));
end;
$$;

comment on function public.fn_partner_ritira(uuid, uuid, uuid, uuid) is
  'Ritiro: Y la propria candidatura, X il proprio invito (lock owner → azienda viva → call FOR SHARE → candidatura FOR UPDATE), solo da inviata. Audit partenariato.candidatura_ritirata. Ritorna {candidatura}. Detail: parametri_non_validi | attore_non_titolare | candidatura_non_trovata | owner_not_found | azienda_non_disponibile | candidatura_gia_decisa | invito_scaduto.';

-- ----------------------------------------------------------------------------
-- 13) fn_partner_scadi_inviti — inviti inviata con scade_at passata →
--    scaduta (ttl), i più vecchi per primi, al massimo p_limite (NULL = 500,
--    1..5000), saltando le righe bloccate (una decisione in corso vince).
--    Scheduler e lettura pigra. Ritorna quante righe.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_scadi_inviti(p_limite integer default 500)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_n integer;
begin
  with da_scadere as (
    select id from public.partner_candidature
    where tipo = 'invito' and stato = 'inviata' and scade_at <= now()
    order by scade_at, id
    limit least(greatest(coalesce(p_limite, 500), 1), 5000)
    for update skip locked
  )
  update public.partner_candidature k
  set stato = 'scaduta', motivo_chiusura = 'ttl', chiusa_at = now()
  from da_scadere d
  where k.id = d.id;
  get diagnostics v_n = row_count;
  return v_n;
end;
$$;

comment on function public.fn_partner_scadi_inviti(integer) is
  'Scadenza degli inviti: inviata con scade_at <= now() → scaduta (ttl), al massimo p_limite (NULL = 500, 1..5000), i più vecchi per primi, saltando le righe bloccate. Ritorna le righe chiuse.';

-- ----------------------------------------------------------------------------
-- 14) fn_partner_invia_messaggio — messaggio del titolare di una delle due
--    parti (Q14). Lock: owner FOR KEY SHARE → azienda FOR KEY SHARE (gli
--    stessi lock delle FK delle letture, presi prima) → conversazione FOR
--    UPDATE. Idempotente: la stessa client_msg_id della stessa azienda
--    restituisce il messaggio già scritto (anche a conversazione chiusa).
--    Altrimenti: conversazione aperta (conversazione_chiusa), controparte
--    viva: non eliminata né archiviata, owner attivo
--    (controparte_non_disponibile), insert, ultimo_messaggio_* e lettura
--    del mittente fino al suo messaggio. Ritorna {messaggio,
--    company_destinataria_id, duplicato}.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_invia_messaggio(
  p_conversazione uuid,
  p_attore        uuid,
  p_owner         uuid,
  p_company       uuid,
  p_testo         text,
  p_client_msg_id uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_conv        public.partner_conversazioni%rowtype;
  v_msg         public.partner_messaggi%rowtype;
  v_controparte uuid;
begin
  if p_conversazione is null or p_client_msg_id is null or p_testo is null
     or char_length(p_testo) not between 1 and 5000 or p_testo !~ '[^[:space:]]' then
    raise exception 'Messaggio non valido' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'In chat scrive il titolare dell''azienda' using detail = 'attore_non_titolare';
  end if;

  perform 1 from public.profiles where id = p_owner for key share;
  if not found then
    raise exception 'Titolare non trovato' using detail = 'owner_not_found';
  end if;
  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner and deleted_at is null and archived_at is null
  for key share;
  if not found then
    raise exception 'Azienda non disponibile' using detail = 'azienda_non_disponibile';
  end if;

  select * into v_conv from public.partner_conversazioni
  where id = p_conversazione
    and (company_creatore_id = p_company or company_partner_id = p_company)
  for update;
  if not found then
    raise exception 'Conversazione non trovata' using detail = 'conversazione_non_trovata';
  end if;
  v_controparte := case when v_conv.company_creatore_id = p_company
                        then v_conv.company_partner_id else v_conv.company_creatore_id end;

  select * into v_msg from public.partner_messaggi
  where conversazione_id = p_conversazione and client_msg_id = p_client_msg_id;
  if found then
    if v_msg.mittente_company_profile_id <> p_company then
      raise exception 'Messaggio non valido' using detail = 'parametri_non_validi';
    end if;
    return jsonb_build_object('messaggio', to_jsonb(v_msg),
                              'company_destinataria_id', v_controparte,
                              'duplicato', true);
  end if;

  if v_conv.stato <> 'aperta' then
    raise exception 'La conversazione è chiusa' using detail = 'conversazione_chiusa';
  end if;
  -- Controparte viva come per il backend (partenariato_indice.aziende_vive,
  -- da cui la sola lettura): non eliminata né archiviata, owner attivo.
  if not exists (select 1 from public.company_profiles cp
                 join public.profiles pr on pr.id = cp.parent_id and pr.is_active
                 where cp.id = v_controparte and cp.deleted_at is null
                   and cp.archived_at is null) then
    raise exception 'L''altra azienda non è più disponibile'
      using detail = 'controparte_non_disponibile';
  end if;

  insert into public.partner_messaggi
    (conversazione_id, mittente_company_profile_id, mittente_user_id, testo, client_msg_id)
  values
    (p_conversazione, p_company, p_attore, p_testo, p_client_msg_id)
  returning * into v_msg;

  update public.partner_conversazioni
  set ultimo_messaggio_id = v_msg.id,
      ultimo_messaggio_at = v_msg.created_at
  where id = p_conversazione;

  insert into public.partner_conversazione_letture
    (conversazione_id, user_id, company_profile_id, letto_fino_a_id)
  values
    (p_conversazione, p_attore, p_company, v_msg.id)
  on conflict (conversazione_id, user_id) do update
    set letto_fino_a_id = greatest(partner_conversazione_letture.letto_fino_a_id,
                                   excluded.letto_fino_a_id);

  return jsonb_build_object('messaggio', to_jsonb(v_msg),
                            'company_destinataria_id', v_controparte,
                            'duplicato', false);
end;
$$;

comment on function public.fn_partner_invia_messaggio(uuid, uuid, uuid, uuid, text, uuid) is
  'Messaggio in chat del titolare di una delle due aziende (testo 1..5000 non vuoto; lock owner FOR KEY SHARE → azienda FOR KEY SHARE → conversazione FOR UPDATE). Idempotente per client_msg_id (stessa azienda: restituisce il messaggio già scritto con duplicato true). Conversazione aperta, controparte viva (non eliminata né archiviata, owner attivo); aggiorna ultimo_messaggio_* e la lettura del mittente. Ritorna {messaggio, company_destinataria_id, duplicato}. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | azienda_non_disponibile | conversazione_non_trovata | conversazione_chiusa | controparte_non_disponibile.';

-- ----------------------------------------------------------------------------
-- 15) fn_partner_segna_letto — lettura di un utente dell'azienda (titolare o
--    membro attivo con visibilità) su una conversazione di cui l'azienda è
--    parte (altrimenti conversazione_non_trovata, anche per un utente
--    estraneo). Solo in avanti e mai oltre l'ultimo messaggio (p_fino_a NULL
--    = fino all'ultimo). Ritorna letto_fino_a_id.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_segna_letto(
  p_conversazione uuid,
  p_user          uuid,
  p_company       uuid,
  p_fino_a        bigint
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ultimo bigint;
  v_letto  bigint;
begin
  if p_conversazione is null or p_user is null or p_company is null
     or (p_fino_a is not null and p_fino_a < 0) then
    raise exception 'Parametri non validi' using detail = 'parametri_non_validi';
  end if;

  select coalesce(ultimo_messaggio_id, 0) into v_ultimo
  from public.partner_conversazioni
  where id = p_conversazione
    and (company_creatore_id = p_company or company_partner_id = p_company);
  if not found or not public.fn_partner_utente_di_azienda(p_user, p_company) then
    raise exception 'Conversazione non trovata' using detail = 'conversazione_non_trovata';
  end if;

  insert into public.partner_conversazione_letture
    (conversazione_id, user_id, company_profile_id, letto_fino_a_id)
  values
    (p_conversazione, p_user, p_company, least(coalesce(p_fino_a, v_ultimo), v_ultimo))
  on conflict (conversazione_id, user_id) do update
    set letto_fino_a_id = greatest(partner_conversazione_letture.letto_fino_a_id,
                                   excluded.letto_fino_a_id)
  returning letto_fino_a_id into v_letto;
  return v_letto;
end;
$$;

comment on function public.fn_partner_segna_letto(uuid, uuid, uuid, bigint) is
  'Lettura per utente: l''azienda è parte della conversazione e l''utente ne è titolare o membro attivo con visibilità (altrimenti conversazione_non_trovata). letto_fino_a_id = max(attuale, min(p_fino_a, ultimo messaggio)); p_fino_a NULL = fino all''ultimo. Ritorna letto_fino_a_id. Detail: parametri_non_validi | conversazione_non_trovata.';

-- ----------------------------------------------------------------------------
-- 16) fn_partner_claim_email_chat — claim delle email «una per raffica» per
--    gli utenti dell'azienda DESTINATARIA p_company (parte della
--    conversazione, altrimenti conversazione_non_trovata). p_user_ids è
--    filtrato: restano solo titolare e membri attivi con visibilità
--    sull'azienda (gli estranei si ignorano). Il riferimento è l'ultimo
--    messaggio dell'ALTRA azienda fino a p_ultimo_id (nessuno → nessun
--    claim). Un utente si rivendica se non ha già letto fino a lì e se non ha
--    un'email pendente (email_fino_a_id NULL o ≤ letto_fino_a_id): update
--    condizionato, quindi una sola email finché non legge. Ritorna gli
--    utenti rivendicati.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_claim_email_chat(
  p_conversazione uuid,
  p_company       uuid,
  p_user_ids      uuid[],
  p_ultimo_id     bigint
)
returns setof uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_ultimo bigint;
  v_utenti uuid[];
begin
  if p_conversazione is null or p_company is null or p_ultimo_id is null then
    raise exception 'Parametri non validi' using detail = 'parametri_non_validi';
  end if;
  perform 1 from public.partner_conversazioni
  where id = p_conversazione
    and (company_creatore_id = p_company or company_partner_id = p_company);
  if not found then
    raise exception 'Conversazione non trovata' using detail = 'conversazione_non_trovata';
  end if;

  select max(id) into v_ultimo from public.partner_messaggi
  where conversazione_id = p_conversazione
    and id <= p_ultimo_id
    and mittente_company_profile_id <> p_company;
  if v_ultimo is null then
    return;
  end if;

  v_utenti := array(
    select distinct u from unnest(coalesce(p_user_ids, '{}'::uuid[])) u
    where u is not null and public.fn_partner_utente_di_azienda(u, p_company)
    order by u);
  if cardinality(v_utenti) = 0 then
    return;
  end if;

  insert into public.partner_conversazione_letture (conversazione_id, user_id, company_profile_id)
  select p_conversazione, u, p_company from unnest(v_utenti) u
  on conflict (conversazione_id, user_id) do nothing;

  return query
    with rivendicati as (
      update public.partner_conversazione_letture l
      set email_fino_a_id = v_ultimo
      where l.conversazione_id = p_conversazione
        and l.user_id = any (v_utenti)
        and l.letto_fino_a_id < v_ultimo
        and (l.email_fino_a_id is null or l.email_fino_a_id <= l.letto_fino_a_id)
      returning l.user_id
    )
    select r.user_id from rivendicati r;
end;
$$;

comment on function public.fn_partner_claim_email_chat(uuid, uuid, uuid[], bigint) is
  'Claim delle email di chat «una per raffica» per l''azienda destinataria (parte della conversazione): utenti filtrati (titolare o membri attivi con visibilità; gli altri ignorati), riferimento = ultimo messaggio dell''altra azienda ≤ p_ultimo_id; rivendica chi non ha letto fin lì e non ha un''email pendente (email_fino_a_id NULL o ≤ letto_fino_a_id), impostando email_fino_a_id. Ritorna gli utenti rivendicati. Detail: parametri_non_validi | conversazione_non_trovata.';

-- ----------------------------------------------------------------------------
-- 17) fn_partner_conversazioni_riepilogo — conversazioni dell'azienda per un
--    suo utente (nessuna riga per un utente estraneo) con i non letti (messaggi
--    dell'altra azienda oltre la sua lettura, esclusi gli oscurati), in un
--    solo round trip.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_conversazioni_riepilogo(
  p_user    uuid,
  p_company uuid
)
returns table (conversazione_id uuid, non_letti integer, ultimo_messaggio_at timestamptz)
language sql
stable
security definer
set search_path = public
as $$
  select c.id,
         (select count(*)::integer
          from public.partner_messaggi m
          where m.conversazione_id = c.id
            and m.id > coalesce(l.letto_fino_a_id, 0)
            and m.mittente_company_profile_id <> p_company
            and m.nascosto_moderazione_at is null),
         c.ultimo_messaggio_at
  from public.partner_conversazioni c
  left join public.partner_conversazione_letture l
    on l.conversazione_id = c.id and l.user_id = p_user
  where (c.company_creatore_id = p_company or c.company_partner_id = p_company)
    and public.fn_partner_utente_di_azienda(p_user, p_company)
  order by c.ultimo_messaggio_at desc nulls last, c.created_at desc, c.id;
$$;

comment on function public.fn_partner_conversazioni_riepilogo(uuid, uuid) is
  'Conversazioni dell''azienda con i non letti dell''utente (messaggi dell''altra azienda oltre letto_fino_a_id, esclusi gli oscurati) e l''ultimo messaggio; nessuna riga se l''utente non è titolare né membro attivo con visibilità.';

-- ----------------------------------------------------------------------------
-- 18) fn_partner_chiudi_conversazione — la chiude SOLO il titolare del
--    creatore (X); per chiunque altro conversazione_non_trovata (difesa in
--    profondità: il backend risponde prima). aperta → chiusa + audit
--    partenariato.conversazione_chiusa. Lo storico resta in sola lettura
--    (K4). Ritorna la riga.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_chiudi_conversazione(
  p_conversazione uuid,
  p_attore        uuid,
  p_owner         uuid,
  p_company       uuid
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_conv public.partner_conversazioni%rowtype;
begin
  if p_conversazione is null then
    raise exception 'Conversazione non valida' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'La conversazione la chiude il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner and deleted_at is null and archived_at is null
  for key share;
  if not found then
    raise exception 'Azienda non disponibile' using detail = 'azienda_non_disponibile';
  end if;

  select * into v_conv from public.partner_conversazioni
  where id = p_conversazione and company_creatore_id = p_company
  for update;
  if not found then
    raise exception 'Conversazione non trovata' using detail = 'conversazione_non_trovata';
  end if;
  if v_conv.stato <> 'aperta' then
    raise exception 'La conversazione è già chiusa' using detail = 'conversazione_chiusa';
  end if;

  update public.partner_conversazioni
  set stato = 'chiusa', chiusa_at = now(), chiusa_da_user_id = p_attore
  where id = p_conversazione
  returning * into v_conv;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.conversazione_chiusa', p_owner, p_owner,
          jsonb_build_object('conversazione_id', v_conv.id, 'call_id', v_conv.partner_call_id,
                             'company_creatore_id', v_conv.company_creatore_id,
                             'company_partner_id', v_conv.company_partner_id));

  return to_jsonb(v_conv);
end;
$$;

comment on function public.fn_partner_chiudi_conversazione(uuid, uuid, uuid, uuid) is
  'Chiusura della conversazione da parte del titolare del creatore X (azienda viva; lock azienda FOR KEY SHARE → conversazione FOR UPDATE): aperta → chiusa + audit partenariato.conversazione_chiusa. Ritorna la riga. Detail: parametri_non_validi | attore_non_titolare | azienda_non_disponibile | conversazione_non_trovata | conversazione_chiusa.';

-- ----------------------------------------------------------------------------
-- 19) Ridefinizione (STESSA firma) di fn_partner_call_sostituisci_posizioni
--    (0037). Stesse chiavi, guardie, errori, versioni e audit; la 0037
--    cancellava e reinseriva TUTTE le posizioni (con gli stessi id), che con
--    le candidature avrebbe azzerato posizione_id (ON DELETE SET NULL). Ora:
--    le posizioni conservate si aggiornano per id, si cancellano solo le
--    omesse e si inseriscono le nuove; una posizione omessa con candidature
--    attive (inviata o accettata) → posizione_con_candidature. Le
--    candidature si serializzano sulla call (FOR SHARE contro FOR UPDATE).
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
  'Replace-all delle posizioni (bozza o pubblicata; lock owner → azienda viva → call): posizioni con id conservate e aggiornate sul posto (id, created_at e candidature restano), omesse cancellate, nuove inserite; requisiti_ids solo della stessa call; una posizione omessa con candidature attive (inviata | accettata) → posizione_con_candidature (0039); in pubblicata ≥ 1 posizione e versione + 1 se il contenuto cambia. Ritorna le posizioni. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | posizioni_non_valide | posizione_con_candidature | call_incompleta.';

-- ----------------------------------------------------------------------------
-- 20) Ridefinizione (STESSA firma) di fn_partenariati_snapshot (0037): le
--    candidature usate del mese si contano con fn_partner_candidature_usate
--    (la stessa formula della RPC), residuo = limite − usate (mai negativo).
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
  v_inizio     date := date_trunc('month', now() at time zone 'Europe/Rome')::date;
begin
  v_calls := public.fn_partner_call_limite_attive(p_owner);
  v_cand := public.fn_partner_candidature_limite(p_owner);
  v_usate := public.fn_partner_calls_attive_usate(p_owner);
  v_cand_usate := public.fn_partner_candidature_usate(p_owner);

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
      'periodo_fine', (v_inizio + interval '1 month' - interval '1 day')::date));
end;
$$;

comment on function public.fn_partenariati_snapshot(uuid) is
  'Limiti e consumi di partenariato del titolare per /me/entitlements: {call_attive: {limite, usate, residuo}, candidature_mese: {limite, usate, residuo, periodo_inizio, periodo_fine}} (NULL = illimitato, mese solare Europe/Rome). Candidature usate = fn_partner_candidature_usate (0039, stessa formula della RPC).';

-- ----------------------------------------------------------------------------
-- 21) Ridefinizione (STESSA firma) di fn_partner_call_pubblica (0037): stesse
--    guardie, errori, versioni e audit, più l'esclusività alla pubblicazione
--    (C6): l'azienda che pubblica non deve avere una candidatura accettata
--    su un'altra call non annullata dello stesso bando se una delle due
--    call è esclusiva (fn_partner_esclusivita_violata, stessa regola
--    dell'accettazione) → esclusivita_violata. Lock advisory azienda ×
--    bando per ultimo, lo stesso di fn_partner_decidi. WP8 la ridefinisce
--    con la stessa firma per i membri.
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
  -- stesso bando si serializzano.
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

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.call_pubblicata', p_owner, p_owner,
          jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                             'bando_id', v_call.bando_id, 'versione', v_versione));

  select * into v_call from public.partner_calls where id = p_call;
  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean) is
  'Pubblica una bozza (lock owner → azienda viva → call): identità dal registro (T5; p_richiedi_non_sandbox NULL = true) e, per una call nominativa, legale rappresentante verificato (Q9); bando aperto | in apertura prossimamente; scadenza tra oggi (Europe/Rome) e la scadenza del bando; completa (titolo, descrizione, regole confermate, ≥ 1 posizione, ≥ 1 requisito cercato); limiti del piano sul pool dell''owner (NULL = illimitato); esclusività (0039: fn_partner_esclusivita_violata, lock advisory azienda × bando come fn_partner_decidi). Versione 1 + audit partenariato.call_pubblicata. Ritorna la riga. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | identita_non_verificata | rappresentante_non_verificato | bando_non_disponibile | scadenza_call_non_valida | call_incompleta | requisiti_non_validi | piano_non_include_call | limite_call_raggiunto | esclusivita_violata.';


-- ----------------------------------------------------------------------------
-- 22) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova o ridefinita
--    (trigger e interne comprese) eseguibile dai ruoli esposti (Supabase
--    concede EXECUTE di default a PUBLIC; create or replace conserva i
--    permessi, la revoca si ripete comunque). I trigger scattano comunque.
-- ----------------------------------------------------------------------------
alter table public.partner_candidature enable row level security;
alter table public.partner_conversazioni enable row level security;
alter table public.partner_messaggi enable row level security;
alter table public.partner_conversazione_letture enable row level security;

revoke all on public.partner_candidature from anon, authenticated;
revoke all on public.partner_conversazioni from anon, authenticated;
revoke all on public.partner_messaggi from anon, authenticated;
revoke all on public.partner_conversazione_letture from anon, authenticated;

revoke execute on function public.fn_partner_utente_di_azienda(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_cand_blocca_azienda(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_candidature_usate(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_candidature_limite(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_aperta(text, date, date, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_esclusivita_violata(uuid, uuid, integer, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_messaggi_immutabili()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_calls_chiudi_candidature()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_consents_chiudi_pendenti()
  from public, anon, authenticated;
revoke execute on function public.fn_partner_scadi_per_opt_out(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_invia_candidatura(jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_invita(jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_ritira(uuid, uuid, uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_scadi_inviti(integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_invia_messaggio(uuid, uuid, uuid, uuid, text, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_segna_letto(uuid, uuid, uuid, bigint)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_claim_email_chat(uuid, uuid, uuid[], bigint)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_conversazioni_riepilogo(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_chiudi_conversazione(uuid, uuid, uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariati_snapshot(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0039 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle).
-- 1) Ripristino delle tre funzioni della 0037: rieseguire i blocchi
--    «create or replace function public.fn_partner_call_sostituisci_posizioni»,
--    «create or replace function public.fn_partenariati_snapshot» e
--    «create or replace function public.fn_partner_call_pubblica» del file
--    0037_call_partenariato.sql (stessa firma; le revoche restano). Va fatto
--    PRIMA del punto 3: la versione 0039 di fn_partner_call_pubblica usa
--    fn_partner_esclusivita_violata.
-- 2) drop trigger trg_partner_consents_chiudi_pendenti on public.partner_consents;
--    drop trigger trg_partner_calls_chiudi_candidature on public.partner_calls;
--    drop trigger trg_partner_messaggi_immutabili on public.partner_messaggi;
-- 3) drop function public.fn_partner_chiudi_conversazione(uuid, uuid, uuid, uuid);
--    drop function public.fn_partner_conversazioni_riepilogo(uuid, uuid);
--    drop function public.fn_partner_claim_email_chat(uuid, uuid, uuid[], bigint);
--    drop function public.fn_partner_segna_letto(uuid, uuid, uuid, bigint);
--    drop function public.fn_partner_invia_messaggio(uuid, uuid, uuid, uuid, text, uuid);
--    drop function public.fn_partner_scadi_inviti(integer);
--    drop function public.fn_partner_ritira(uuid, uuid, uuid, uuid);
--    drop function public.fn_partner_decidi(uuid, uuid, uuid, uuid, text, text, boolean,
--      boolean);
--    drop function public.fn_partner_invita(jsonb);
--    drop function public.fn_partner_invia_candidatura(jsonb);
--    drop function public.fn_partner_scadi_per_opt_out(uuid);
--    drop function public.fn_partner_consents_chiudi_pendenti();
--    drop function public.fn_partner_calls_chiudi_candidature();
--    drop function public.fn_partner_messaggi_immutabili();
--    drop function public.fn_partner_esclusivita_violata(uuid, uuid, integer, boolean);
--    drop function public.fn_partner_call_aperta(text, date, date, uuid);
--    drop function public.fn_partner_candidature_limite(uuid);
--    drop function public.fn_partner_candidature_usate(uuid);
--    drop function public.fn_partner_cand_blocca_azienda(uuid, uuid);
--    drop function public.fn_partner_utente_di_azienda(uuid, uuid);
-- 4) delete from public.partner_segnalazioni where oggetto_tipo = 'messaggio';
--      -- si perdono le segnalazioni dei messaggi: esportarle prima (DSA, Q22);
--    alter table public.partner_segnalazioni
--      drop constraint ps_oggetto_tipo_check,
--      add constraint ps_oggetto_tipo_check check (oggetto_tipo in ('call', 'profilo'));
-- 5) drop table public.partner_conversazione_letture;
--    drop table public.partner_messaggi;           -- si perdono le chat
--    alter table public.partner_candidature drop constraint partner_candidature_conversazione_fk;
--    drop table public.partner_conversazioni;
--    drop table public.partner_candidature;        -- si perdono candidature e inviti
-- Le righe di audit_log (partenariato.candidatura_*, invito_inviato,
-- identita_rivelata, contatti_rivelati, conversazione_chiusa) restano.
-- ============================================================================
