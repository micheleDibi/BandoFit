-- ============================================================================
-- BandoFit — DB primario, migration 0017: dominio delle consulenze.
--
-- Flusso: il titolare completa un AI-check e attiva l'addon «Consulto
-- esperto» → nasce una RICHIESTA (stato 'nuova') visibile a tutti i
-- progettisti → i progettisti inviano PROPOSTE → il titolare ne accetta una
-- (fn_accept_proposal: assegnazione DEFINITIVA, 1:1) e può prenotare uno
-- SLOT del progettista (fn_book_slot), contestualmente o dopo.
--
-- Stati (text + check, pattern ai_checks — più estendibile di un enum):
--   richiesta: nuova → assegnata (terminale) | annullata
--   proposta:  inviata → accettata | rifiutata | superata (auto) | ritirata
--   booking:   confermata → annullata (lo slot torna prenotabile)
--
-- Concorrenza, tutta a livello DB (il backend gira multi-worker):
--   · doppia prenotazione     → indice unico parziale su slot_id + slot FOR UPDATE
--   · accettazione vs ritiro  → FOR UPDATE anche sulla PROPOSTA
--   · retiming vs prenotazione→ fn_update_slot/fn_book_slot serializzano sullo slot
--   · slot sovrapposti        → exclusion constraint (btree_gist)
--   · doppia richiesta aperta → indice unico parziale (family_parent_id, bando_id)
--
-- FUSO ORARIO: gli slot sono timestamptz (UTC), mostrati nel fuso di ciascun
-- utente — divergenza DELIBERATA dal calendario personale (0008, wall-clock
-- italiano): qui due persone diverse guardano lo stesso istante.
--
-- Chi riferisce persone segue il pattern di ai_checks: cliente_id,
-- family_parent_id e i progettista_id di proposte/booking SENZA FK (lo
-- storico sopravvive alla cancellazione delle persone); company_profile_id
-- con FK cascade (lo storico muore con l'azienda: right to erasure del
-- titolare). Le prenotazioni portano lo snapshot di inizio/fine: la
-- cancellazione dell'account del progettista (cascade sugli slot) non li
-- perde e non resta bloccata.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Seed idempotente dell'addon «Consulto esperto».
-- ⚠️ PRIMA di eseguire in produzione: verifica da AdminAddon che l'addon
-- esistente (id=1) abbia slug 'consulto-esperto' — con uno slug diverso
-- questo insert creerebbe un DOPPIONE a catalogo. Su un DB ricostruito dalle
-- migrazioni garantisce che il flusso consulenze trovi il suo addon.
-- ---------------------------------------------------------------------------
insert into public.addons (nome, slug, descrizione, prezzo, tipo_prezzo, ordering, is_active)
values (
  'Consulto esperto',
  'consulto-esperto',
  'Trenta minuti di confronto con un progettista esperto in finanza agevolata sul bando che hai analizzato con l''AI-check.',
  0, 'gratis', 100, true
)
on conflict (slug) do nothing;

-- ---------------------------------------------------------------------------
-- Slot di disponibilità del progettista.
-- Nessuna colonna di stato: «libero» = nessuna prenotazione confermata (la
-- verità sta nell'indice parziale dei booking, non in un flag da sincronizzare).
-- ---------------------------------------------------------------------------
create extension if not exists btree_gist;

create table public.availability_slots (
  id             uuid primary key default gen_random_uuid(),
  progettista_id uuid not null references public.profiles (id) on delete cascade,
  inizio         timestamptz not null,
  fine           timestamptz not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint availability_slots_ordine check (fine > inizio),
  -- Un progettista non può avere due slot sovrapposti (range [): slot
  -- adiacenti che condividono il confine sono validi).
  constraint availability_slots_no_overlap exclude using gist (
    progettista_id with =,
    tstzrange(inizio, fine) with &&
  )
);

comment on table public.availability_slots is
  'Disponibilità a calendario del progettista (UTC). Libero = nessun booking confermato; CRUD serializzato via fn_update_slot/fn_delete_slot.';

create index availability_slots_progettista_idx
  on public.availability_slots (progettista_id, inizio);

create trigger trg_availability_slots_updated_at
  before update on public.availability_slots
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Richieste di consulto.
-- ---------------------------------------------------------------------------
create table public.consultation_requests (
  id                      uuid primary key default gen_random_uuid(),
  cliente_id              uuid not null,
  family_parent_id        uuid not null,
  company_profile_id      uuid not null references public.company_profiles (id) on delete cascade,
  ai_check_id             uuid references public.ai_checks (id) on delete set null,
  esito                   text,
  punteggio               integer check (punteggio is null or punteggio between 0 and 100),
  bando_id                integer not null,
  bando_slug              text not null,
  bando_titolo            text not null,
  addon_id                bigint references public.addons (id),
  addon_slug              text not null,
  addon_prezzo            numeric(10, 2) not null default 0,
  stato                   text not null default 'nuova'
                            check (stato in ('nuova', 'assegnata', 'annullata')),
  assigned_progettista_id uuid,
  assigned_at             timestamptz,
  accepted_proposal_id    uuid,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  -- Assegnata ⇔ progettista, proposta accettata e data valorizzati insieme.
  constraint consultation_requests_assegnazione_coerente check (
    (stato = 'assegnata')
    = (assigned_progettista_id is not null
       and accepted_proposal_id is not null
       and assigned_at is not null)
  )
);

comment on table public.consultation_requests is
  'Richieste di consulto (dall''addon consulto-esperto, post AI-check). esito/punteggio sono snapshot dell''AI-check; bando denormalizzato dal catalogo secondario come in ai_checks. addon_id/addon_slug/addon_prezzo = innesto del futuro pagamento.';
comment on column public.consultation_requests.esito is
  'Snapshot dell''esito AI-check alla creazione: la lista pool non dipende dalla sopravvivenza della riga ai_checks.';

-- Una sola richiesta APERTA per bando per azienda: una richiesta già
-- assegnata NON blocca un futuro secondo consulto sullo stesso bando
-- (coerente con lo storico versionato degli AI-check).
create unique index consultation_requests_one_open
  on public.consultation_requests (family_parent_id, bando_id)
  where stato = 'nuova';

create index consultation_requests_pool_idx
  on public.consultation_requests (stato, created_at desc);
create index consultation_requests_assigned_idx
  on public.consultation_requests (assigned_progettista_id)
  where assigned_progettista_id is not null;
create index consultation_requests_family_idx
  on public.consultation_requests (family_parent_id, created_at desc);

create trigger trg_consultation_requests_updated_at
  before update on public.consultation_requests
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Proposte dei progettisti.
-- ---------------------------------------------------------------------------
create table public.consultation_proposals (
  id             uuid primary key default gen_random_uuid(),
  request_id     uuid not null references public.consultation_requests (id) on delete cascade,
  progettista_id uuid not null,
  messaggio      text not null check (char_length(messaggio) between 1 and 4000),
  stato          text not null default 'inviata'
                   check (stato in ('inviata', 'accettata', 'rifiutata', 'superata', 'ritirata')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

comment on table public.consultation_proposals is
  'Proposte inviate dai progettisti su una richiesta. Una sola APERTA per progettista per richiesta: dopo un ritiro o un rifiuto se ne può inviare una nuova (l''unicità piena sarebbe un forfeit permanente).';

create unique index consultation_proposals_one_open
  on public.consultation_proposals (request_id, progettista_id)
  where stato = 'inviata';

create index consultation_proposals_request_idx
  on public.consultation_proposals (request_id, created_at desc);
create index consultation_proposals_progettista_idx
  on public.consultation_proposals (progettista_id, created_at desc);

create trigger trg_consultation_proposals_updated_at
  before update on public.consultation_proposals
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Prenotazioni (appuntamenti).
-- inizio/fine sono uno SNAPSHOT dello slot: l'appuntamento sopravvive alla
-- cancellazione dello slot (FK set null) e dell'account del progettista.
-- ---------------------------------------------------------------------------
create table public.consultation_bookings (
  id             uuid primary key default gen_random_uuid(),
  request_id     uuid not null references public.consultation_requests (id) on delete cascade,
  slot_id        uuid references public.availability_slots (id) on delete set null,
  cliente_id     uuid not null,
  progettista_id uuid not null,
  inizio         timestamptz not null,
  fine           timestamptz not null,
  stato          text not null default 'confermata'
                   check (stato in ('confermata', 'annullata')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint consultation_bookings_ordine check (fine > inizio)
);

comment on table public.consultation_bookings is
  'Appuntamenti su slot. L''indice parziale su slot_id è l''anti doppia-prenotazione a livello DB; l''annullamento libera lo slot da solo.';

create unique index consultation_bookings_one_per_slot
  on public.consultation_bookings (slot_id)
  where stato = 'confermata';
create unique index consultation_bookings_one_per_request
  on public.consultation_bookings (request_id)
  where stato = 'confermata';
create index consultation_bookings_progettista_idx
  on public.consultation_bookings (progettista_id, inizio);

create trigger trg_consultation_bookings_updated_at
  before update on public.consultation_bookings
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- Prenotazione di uno slot (chiamata dal backend per conto del titolare,
-- direttamente o dentro fn_accept_proposal). Serializza sulla RICHIESTA e
-- poi sullo SLOT (stesso ordine ovunque: niente inversioni → niente deadlock).
-- Errori con detail = codice macchina (pattern 0003).
-- ---------------------------------------------------------------------------
create or replace function public.fn_book_slot(
  p_request_id uuid,
  p_slot_id uuid,
  p_actor_id uuid
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_request record;
  v_slot record;
  v_booking_id uuid;
begin
  select id, stato, cliente_id, family_parent_id, assigned_progettista_id
    into v_request
    from public.consultation_requests
    where id = p_request_id
    for update;
  if not found then
    raise exception 'Consulenza non trovata' using detail = 'request_not_found';
  end if;
  if v_request.cliente_id <> p_actor_id then
    raise exception 'Solo il titolare della richiesta può prenotare'
      using detail = 'not_request_owner';
  end if;
  if v_request.stato <> 'assegnata' then
    raise exception 'La consulenza non è ancora assegnata'
      using detail = 'request_not_assigned';
  end if;

  -- FOR UPDATE: serializza con fn_update_slot/fn_delete_slot e con le
  -- prenotazioni concorrenti sullo stesso slot.
  select id, progettista_id, inizio, fine
    into v_slot
    from public.availability_slots
    where id = p_slot_id
    for update;
  if not found then
    raise exception 'Slot non trovato' using detail = 'slot_not_found';
  end if;
  if v_slot.progettista_id <> v_request.assigned_progettista_id then
    raise exception 'Lo slot non appartiene al progettista assegnato'
      using detail = 'slot_wrong_progettista';
  end if;
  if v_slot.inizio <= now() then
    raise exception 'Lo slot è già passato' using detail = 'slot_in_past';
  end if;
  if exists (
    select 1 from public.consultation_bookings
    where slot_id = p_slot_id and stato = 'confermata'
  ) then
    raise exception 'Slot già prenotato' using detail = 'slot_taken';
  end if;
  if exists (
    select 1 from public.consultation_bookings
    where request_id = p_request_id and stato = 'confermata'
  ) then
    raise exception 'Questa consulenza ha già un appuntamento'
      using detail = 'booking_already_exists';
  end if;

  begin
    insert into public.consultation_bookings
      (request_id, slot_id, cliente_id, progettista_id, inizio, fine)
    values
      (p_request_id, p_slot_id, v_request.cliente_id,
       v_slot.progettista_id, v_slot.inizio, v_slot.fine)
    returning id into v_booking_id;
  exception when unique_violation then
    -- Backstop: la corsa è già serializzata dal FOR UPDATE sullo slot; un
    -- 23505 grezzo arriverebbe al client come 502 invece di 409.
    raise exception 'Slot già prenotato' using detail = 'slot_taken';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_actor_id, 'consulenza.booked', v_slot.progettista_id, v_request.family_parent_id,
          jsonb_build_object('request_id', p_request_id, 'booking_id', v_booking_id,
                             'slot_id', p_slot_id, 'inizio', v_slot.inizio, 'fine', v_slot.fine));

  return v_booking_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- Accettazione di una proposta = ASSEGNAZIONE definitiva (+ prenotazione
-- opzionale, all-or-nothing: se lo slot è appena stato preso fallisce TUTTA
-- la RPC e il titolare riprova con un altro slot).
-- ---------------------------------------------------------------------------
create or replace function public.fn_accept_proposal(
  p_request_id uuid,
  p_proposal_id uuid,
  p_cliente_id uuid,
  p_slot_id uuid default null
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_request record;
  v_proposal record;
  v_progettista record;
  v_booking_id uuid;
begin
  select id, stato, cliente_id, family_parent_id, bando_id, bando_slug
    into v_request
    from public.consultation_requests
    where id = p_request_id
    for update;
  if not found then
    raise exception 'Consulenza non trovata' using detail = 'request_not_found';
  end if;
  if v_request.cliente_id <> p_cliente_id then
    raise exception 'Solo il titolare della richiesta può accettare'
      using detail = 'not_request_owner';
  end if;
  if v_request.stato <> 'nuova' then
    raise exception 'La richiesta non è più aperta' using detail = 'request_not_open';
  end if;

  -- FOR UPDATE anche sulla proposta: senza, un ritiro concorrente committato
  -- dopo questa guardia verrebbe sovrascritto (proposta ritirata che risulta
  -- accettata).
  select id, progettista_id, stato
    into v_proposal
    from public.consultation_proposals
    where id = p_proposal_id and request_id = p_request_id
    for update;
  if not found then
    raise exception 'Proposta non trovata' using detail = 'proposal_not_found';
  end if;
  if v_proposal.stato <> 'inviata' then
    raise exception 'La proposta non è più disponibile' using detail = 'proposal_not_open';
  end if;

  -- Niente assegnazioni a ex-progettisti o account sospesi.
  select role, is_active into v_progettista
    from public.profiles where id = v_proposal.progettista_id;
  if not found or v_progettista.role <> 'progettista' or not v_progettista.is_active then
    raise exception 'Il progettista non è più disponibile'
      using detail = 'progettista_not_available';
  end if;

  update public.consultation_requests
     set stato = 'assegnata',
         assigned_progettista_id = v_proposal.progettista_id,
         accepted_proposal_id = v_proposal.id,
         assigned_at = now()
   where id = p_request_id;

  update public.consultation_proposals
     set stato = 'accettata'
   where id = p_proposal_id;

  update public.consultation_proposals
     set stato = 'superata'
   where request_id = p_request_id and stato = 'inviata' and id <> p_proposal_id;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_cliente_id, 'consulenza.assigned', v_proposal.progettista_id,
          v_request.family_parent_id,
          jsonb_build_object('request_id', p_request_id, 'proposal_id', p_proposal_id,
                             'bando_id', v_request.bando_id,
                             'bando_slug', v_request.bando_slug));

  if p_slot_id is not null then
    -- Riusa fn_book_slot: la richiesta è già 'assegnata' in questa
    -- transazione e il ri-lock della stessa riga nella stessa transazione
    -- non blocca. Un errore (slot preso) annulla anche l'assegnazione.
    v_booking_id := public.fn_book_slot(p_request_id, p_slot_id, p_cliente_id);
  end if;

  return jsonb_build_object(
    'progettista_id', v_proposal.progettista_id,
    'proposal_id', v_proposal.id,
    'booking_id', v_booking_id
  );
end;
$$;

-- ---------------------------------------------------------------------------
-- Modifica/cancellazione slot: serializzate sulla riga (FOR UPDATE) perché
-- un update condizionale «where not exists booking» NON basta — in READ
-- COMMITTED la subquery userebbe lo snapshot preso prima del lock e non
-- vedrebbe una prenotazione appena committata.
-- ---------------------------------------------------------------------------
create or replace function public.fn_update_slot(
  p_slot_id uuid,
  p_progettista_id uuid,
  p_inizio timestamptz,
  p_fine timestamptz
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform 1 from public.availability_slots
    where id = p_slot_id and progettista_id = p_progettista_id
    for update;
  if not found then
    raise exception 'Slot non trovato' using detail = 'slot_not_found';
  end if;
  if exists (
    select 1 from public.consultation_bookings
    where slot_id = p_slot_id and stato = 'confermata'
  ) then
    raise exception 'Lo slot è prenotato' using detail = 'slot_booked';
  end if;

  begin
    update public.availability_slots
       set inizio = p_inizio, fine = p_fine
     where id = p_slot_id;
  exception when exclusion_violation then
    raise exception 'Lo slot si sovrappone a un''altra disponibilità'
      using detail = 'slot_overlap';
  end;
end;
$$;

create or replace function public.fn_delete_slot(
  p_slot_id uuid,
  p_progettista_id uuid
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform 1 from public.availability_slots
    where id = p_slot_id and progettista_id = p_progettista_id
    for update;
  if not found then
    raise exception 'Slot non trovato' using detail = 'slot_not_found';
  end if;
  if exists (
    select 1 from public.consultation_bookings
    where slot_id = p_slot_id and stato = 'confermata'
  ) then
    raise exception 'Lo slot è prenotato' using detail = 'slot_booked';
  end if;
  -- Lo storico annullato non blocca: slot_id dei vecchi booking → null (FK),
  -- gli orari restano nello snapshot del booking.
  delete from public.availability_slots where id = p_slot_id;
end;
$$;

-- ---------------------------------------------------------------------------
-- Sicurezza: pattern del repo — RLS deny-all + revoche; revoke EXECUTE sulle
-- funzioni (PostgREST esporrebbe ogni funzione di public come RPC).
-- ---------------------------------------------------------------------------
alter table public.availability_slots enable row level security;
alter table public.consultation_requests enable row level security;
alter table public.consultation_proposals enable row level security;
alter table public.consultation_bookings enable row level security;

revoke all on public.availability_slots from anon, authenticated;
revoke all on public.consultation_requests from anon, authenticated;
revoke all on public.consultation_proposals from anon, authenticated;
revoke all on public.consultation_bookings from anon, authenticated;

revoke execute on function public.fn_book_slot(uuid, uuid, uuid) from public, anon, authenticated;
revoke execute on function public.fn_accept_proposal(uuid, uuid, uuid, uuid) from public, anon, authenticated;
revoke execute on function public.fn_update_slot(uuid, uuid, timestamptz, timestamptz) from public, anon, authenticated;
revoke execute on function public.fn_delete_slot(uuid, uuid) from public, anon, authenticated;
