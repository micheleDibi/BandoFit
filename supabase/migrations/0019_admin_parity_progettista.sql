-- ============================================================================
-- BandoFit — DB primario, migration 0019: parità admin ↔ progettista.
--
-- Decisione di prodotto: gli amministratori hanno ESATTAMENTE le stesse
-- funzioni dei progettisti (pool richieste, proposte, slot, appuntamenti),
-- senza cambiare ruolo. Due conseguenze a DB:
--
-- 1. Il codice PRG-xxxxx per un admin non si assegna alla nomina: lo assegna
--    pigramente il backend alla PRIMA proposta inviata, con
--    fn_ensure_progettista_codice (stessa sequence e stesso «insert solo se
--    assente» di fn_promote_progettista, 0015 — una futura promozione riusa
--    il codice pigro e viceversa).
-- 2. fn_accept_proposal viene ridefinita: la guardia sull'autore della
--    proposta accetta anche un admin attivo (con la 0017 una proposta di un
--    admin non sarebbe MAI stata accettabile).
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Codice pigro: riusa la riga se esiste (logica di fn_promote_progettista,
-- MA senza toccare il ruolo e senza audit di promozione). Il FOR UPDATE sul
-- profilo serializza due prime-proposte concorrenti dello stesso utente;
-- PK su user_id e unique sul codice restano la rete di sicurezza.
-- ---------------------------------------------------------------------------
create or replace function public.fn_ensure_progettista_codice(p_user_id uuid)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_codice text;
begin
  perform 1 from public.profiles where id = p_user_id for update;
  if not found then
    raise exception 'Utente non trovato' using detail = 'user_not_found';
  end if;

  select codice into v_codice from public.progettisti where user_id = p_user_id;
  if v_codice is null then
    v_codice := 'PRG-' || lpad(nextval('public.progettista_codice_seq')::text, 5, '0');
    insert into public.progettisti (user_id, codice) values (p_user_id, v_codice);
  end if;

  return v_codice;
end;
$$;

-- ---------------------------------------------------------------------------
-- fn_accept_proposal: corpo integrale dalla 0017 con UNA riga cambiata (la
-- guardia sull'autore: role in progettista/admin invece di solo progettista).
-- CREATE OR REPLACE conserva le ACL già revocate dalla 0017.
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

  -- Niente assegnazioni a ex-progettisti o account sospesi. Parità 0019:
  -- anche un admin attivo è un autore valido.
  select role, is_active into v_progettista
    from public.profiles where id = v_proposal.progettista_id;
  if not found
     or v_progettista.role not in ('progettista', 'admin')
     or not v_progettista.is_active then
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
-- Sicurezza: pattern del repo — PostgREST esporrebbe ogni funzione di public
-- come RPC, quindi revoke EXECUTE ai ruoli client sulla funzione nuova
-- (fn_accept_proposal conserva le revoche della 0017).
-- ---------------------------------------------------------------------------
revoke execute on function public.fn_ensure_progettista_codice(uuid) from public, anon, authenticated;
