-- ============================================================================
-- BandoFit — DB primario, migration 0044: RIPRISTINO DELLA RIMAPPATURA DEI
-- BANDI FUSI (complemento della 0043).
--
-- Se il catalogo separa un doppione che la 0043 ha già rimappato sul master,
-- le righe del primario vanno riportate al doppione. La 0043 scrive in
-- audit_log una voce catalogo.rimappatura_fuso per ogni riga toccata, con i
-- valori precedenti: questa funzione le rilegge e le riapplica al contrario,
-- al posto delle query a mano in coda alla 0043.
--
--   fn_ripristina_rimappatura(doppione, dal, prova default true) → jsonb
--   - legge le voci del doppione scritte da `dal` in poi, dalla più recente
--     alla più vecchia, escluse quelle già ripristinate;
--   - operazione 'eliminata' (saved_bandi, calendar_events): reinserisce la
--     riga intera con lo stesso id; 'aggiornata': riporta bando_id e
--     bando_slug ai valori precedenti (per le call bando_mancante_dal torna
--     NULL e lo scheduler lo rivaluta da capo); 'convertita': l'evento
--     personale torna scadenza del bando;
--   - una riga cambiata nel frattempo (non è più sul master; l'evento
--     convertito è stato modificato; utente o azienda non esistono più) o che
--     collide con un vincolo di unicità (il doppione è già presente nello
--     stesso ambito utente × azienda, o l'azienda ha già una call attiva sul
--     doppione) NON si tocca e si conta in_conflitto; così anche una riga
--     aggiornata sul master per cui, dopo, la rimappatura di un altro
--     doppione dello stesso master ha eliminato la riga di quel doppione
--     nello stesso ambito, finché quell'eliminazione non è ripristinata;
--   - ogni riga ripristinata scrive una voce catalogo.ripristino_rimappatura
--     che cita la voce d'origine (payload.voce) e lo stato sovrascritto: una
--     seconda chiamata non la riapplica;
--   - p_prova = true (DEFAULT, prudente): stessi conteggi, nessuna scrittura.
--   Ritorna {saved_bandi, calendar_events, partner_calls} ciascuno con
--   {ripristinate, in_conflitto}.
--
-- Si lancia a mano (SQL editor), con la rimappatura in prova o spenta e dopo
-- che il catalogo ha tolto la fusione: altrimenti il passo successivo rimappa
-- di nuovo. Procedura in docs/deploy.md.
--
-- Concorrenza: la scrittura prende lo stesso lock advisory della 0043 e blocca
-- ogni riga prima di controllarla; un inserimento concorrente che collide col
-- ripristino di una riga la conta in_conflitto senza annullare le altre.
--
-- ADDITIVA: una funzione nuova; nessuna tabella o funzione esistente
-- modificata. Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;).
-- Rollback in coda.
-- ============================================================================

create or replace function public.fn_ripristina_rimappatura(
  p_doppione integer,
  p_dal      timestamptz,
  p_prova    boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_origine    constant text := 'catalogo.rimappatura_fuso';
  v_azione     constant text := 'catalogo.ripristino_rimappatura';
  v_attivi     constant text[] := array['bozza', 'pubblicata', 'sospesa_moderazione'];
  r_voce       record;
  r_sb         public.saved_bandi%rowtype;
  r_ce         public.calendar_events%rowtype;
  r_pc         public.partner_calls%rowtype;
  v_tabella    text;
  v_operazione text;
  v_id         uuid;
  v_master     integer;
  v_prima      jsonb;
  v_bando      integer;
  v_utente     uuid;
  v_azienda    uuid;
  v_ambito     text;
  v_sovrascr   jsonb;
  v_sostiene   boolean;
  v_ok         boolean;
  -- Righe e ambiti già ripristinati in questa chiamata: la prova, che non
  -- scrive, li vede come li vedrebbe la scrittura.
  v_righe      uuid[] := '{}';
  v_ambiti     text[] := '{}';
  v_sb_rip     integer := 0;
  v_sb_con     integer := 0;
  v_ce_rip     integer := 0;
  v_ce_con     integer := 0;
  v_pc_rip     integer := 0;
  v_pc_con     integer := 0;
begin
  if p_doppione is null or p_doppione <= 0 or p_dal is null or p_prova is null then
    raise exception 'Parametri del ripristino non validi'
      using detail = 'parametri_non_validi';
  end if;

  -- Ripristino e passo della rimappatura non si intrecciano; la prova non
  -- scrive e non attende.
  if not p_prova then
    perform pg_advisory_xact_lock(hashtext('catalogo_rimappatura_fusi'));
  end if;

  for r_voce in
    select v.id, v.created_at, v.target_user_id, v.family_parent_id, v.payload
    from public.audit_log v
    where v.action = v_origine
      and v.payload->'doppione' = to_jsonb(p_doppione)
      and v.created_at >= p_dal
      and not exists (
        select 1 from public.audit_log r
        where r.action = v_azione and r.payload->'voce' = to_jsonb(v.id)
      )
    order by v.id desc
  loop
    v_tabella    := r_voce.payload->>'tabella';
    v_operazione := r_voce.payload->>'operazione';
    v_id         := (r_voce.payload->>'id')::uuid;
    v_master     := (r_voce.payload->>'master')::integer;
    v_prima      := r_voce.payload->'prima';
    v_bando      := (v_prima->>'bando_id')::integer;
    v_ambito     := null;
    v_sovrascr   := null;

    -- Una riga portata sul master può essere la ragione per cui, dopo, la
    -- rimappatura di un ALTRO doppione dello stesso master ha eliminato la
    -- riga di quel doppione nello stesso ambito: riportarla indietro lascerebbe
    -- l'ambito senza master né altro doppione. Finché quell'eliminazione non è
    -- ripristinata, la riga resta sul master (in_conflitto).
    v_sostiene := v_operazione = 'aggiornata'
      and v_tabella in ('saved_bandi', 'calendar_events')
      and exists (
        select 1 from public.audit_log o
        where o.action = v_origine
          and o.payload->'tabella' = r_voce.payload->'tabella'
          and o.payload->>'operazione' = 'eliminata'
          and o.payload->'master' = r_voce.payload->'master'
          and o.payload->'doppione' <> to_jsonb(p_doppione)
          and o.target_user_id is not distinct from r_voce.target_user_id
          and o.payload->'company_profile_id' = r_voce.payload->'company_profile_id'
          and o.created_at >= r_voce.created_at
          and not exists (
            select 1 from public.audit_log r
            where r.action = v_azione and r.payload->'voce' = to_jsonb(o.id)
          )
      );

    -- a) Righe eliminate: reinserite intere, se l'id è libero, utente e
    --    azienda esistono ancora e il doppione non è già nello stesso ambito.
    if v_operazione = 'eliminata' and v_tabella in ('saved_bandi', 'calendar_events') then
      v_utente  := (v_prima->>'user_id')::uuid;
      v_azienda := (v_prima->>'company_profile_id')::uuid;
      v_ambito  := format('%s|%s|%s|%s', v_tabella, v_utente, v_azienda, v_bando);
      v_ok := exists (select 1 from public.profiles p where p.id = v_utente)
        and (v_azienda is null
             or exists (select 1 from public.company_profiles c where c.id = v_azienda));
      if v_tabella = 'saved_bandi' then
        v_ok := v_ok
          and not exists (select 1 from public.saved_bandi s where s.id = v_id)
          and not exists (
            select 1 from public.saved_bandi s
            where s.user_id = v_utente
              and s.company_profile_id is not distinct from v_azienda
              and s.bando_id = v_bando);
      else
        v_ok := v_ok
          and not exists (select 1 from public.calendar_events e where e.id = v_id)
          and not exists (
            select 1 from public.calendar_events e
            where e.tipo = 'bando'
              and e.user_id = v_utente
              and e.company_profile_id is not distinct from v_azienda
              and e.bando_id = v_bando);
      end if;
      v_ok := v_ok and not v_id = any (v_righe) and not v_ambito = any (v_ambiti);

      if v_ok and not p_prova then
        begin
          if v_tabella = 'saved_bandi' then
            insert into public.saved_bandi (id, user_id, company_profile_id, bando_id,
                                            bando_slug, bando_titolo, data_scadenza,
                                            stato_bando, created_at)
            select r.id, r.user_id, r.company_profile_id, r.bando_id, r.bando_slug,
                   r.bando_titolo, r.data_scadenza, r.stato_bando, r.created_at
            from jsonb_populate_record(null::public.saved_bandi, v_prima) r;
          else
            insert into public.calendar_events (id, user_id, company_profile_id, titolo, data,
                                                tutto_il_giorno, ora_inizio, ora_fine, note,
                                                tipo, bando_id, bando_slug, created_at,
                                                updated_at)
            select r.id, r.user_id, r.company_profile_id, r.titolo, r.data,
                   r.tutto_il_giorno, r.ora_inizio, r.ora_fine, r.note,
                   r.tipo, r.bando_id, r.bando_slug, r.created_at, r.updated_at
            from jsonb_populate_record(null::public.calendar_events, v_prima) r;
          end if;
        exception when unique_violation or foreign_key_violation then
          v_ok := false;
        end;
      end if;

    -- b) Bandi salvati rimappati: tornano al doppione se sono ancora sul master.
    elsif v_operazione = 'aggiornata' and v_tabella = 'saved_bandi' then
      if p_prova then
        select * into r_sb from public.saved_bandi s where s.id = v_id;
      else
        select * into r_sb from public.saved_bandi s where s.id = v_id for update;
      end if;
      v_ok := found and r_sb.bando_id = v_master and not v_sostiene
        and not exists (
          select 1 from public.saved_bandi s
          where s.user_id = r_sb.user_id
            and s.company_profile_id is not distinct from r_sb.company_profile_id
            and s.bando_id = v_bando);
      v_ambito := format('%s|%s|%s|%s', v_tabella, r_sb.user_id, r_sb.company_profile_id,
                         v_bando);
      v_ok := v_ok and not v_id = any (v_righe) and not v_ambito = any (v_ambiti);

      if v_ok and not p_prova then
        v_sovrascr := jsonb_build_object('bando_id', r_sb.bando_id,
                                         'bando_slug', r_sb.bando_slug);
        begin
          update public.saved_bandi s
          set bando_id = v_bando, bando_slug = v_prima->>'bando_slug'
          where s.id = v_id;
        exception when unique_violation then
          v_ok := false;
        end;
      end if;

    -- c) Scadenze in calendario rimappate o convertite in evento personale.
    --    Una convertita torna al bando solo se l'utente non l'ha modificata
    --    dopo la conversione (conversione e voce hanno lo stesso istante di
    --    transazione).
    elsif v_operazione in ('aggiornata', 'convertita') and v_tabella = 'calendar_events' then
      if p_prova then
        select * into r_ce from public.calendar_events e where e.id = v_id;
      else
        select * into r_ce from public.calendar_events e where e.id = v_id for update;
      end if;
      v_ok := found
        and case v_operazione
              when 'aggiornata' then r_ce.tipo = 'bando' and r_ce.bando_id = v_master
                                     and not v_sostiene
              else r_ce.tipo = 'personale' and r_ce.bando_id is null
                   and r_ce.updated_at <= r_voce.created_at
            end
        and not exists (
          select 1 from public.calendar_events e
          where e.tipo = 'bando'
            and e.user_id = r_ce.user_id
            and e.company_profile_id is not distinct from r_ce.company_profile_id
            and e.bando_id = v_bando);
      v_ambito := format('%s|%s|%s|%s', v_tabella, r_ce.user_id, r_ce.company_profile_id,
                         v_bando);
      v_ok := v_ok and not v_id = any (v_righe) and not v_ambito = any (v_ambiti);

      if v_ok and not p_prova then
        v_sovrascr := jsonb_build_object('tipo', r_ce.tipo, 'bando_id', r_ce.bando_id,
                                         'bando_slug', r_ce.bando_slug);
        begin
          update public.calendar_events e
          set tipo = 'bando', bando_id = v_bando, bando_slug = v_prima->>'bando_slug'
          where e.id = v_id;
        exception when unique_violation then
          v_ok := false;
        end;
      end if;

    -- d) Call di partenariato rimappate: tornano al doppione se sono ancora sul
    --    master e, se attive, l'azienda non ha già un'altra call attiva sul
    --    doppione (partner_calls_una_attiva). bando_mancante_dal torna NULL:
    --    una data vecchia farebbe chiudere d'ufficio la call al primo passo
    --    dello scheduler in cui il doppione non fosse ancora nella vista.
    elsif v_operazione = 'aggiornata' and v_tabella = 'partner_calls' then
      if p_prova then
        select * into r_pc from public.partner_calls c where c.id = v_id;
      else
        select * into r_pc from public.partner_calls c where c.id = v_id for update;
      end if;
      v_ok := found and r_pc.bando_id = v_master;
      if r_pc.stato = any (v_attivi) then
        v_ambito := format('%s|%s|%s', v_tabella, r_pc.company_profile_id, v_bando);
        v_ok := v_ok
          and not exists (
            select 1 from public.partner_calls c
            where c.company_profile_id = r_pc.company_profile_id
              and c.bando_id = v_bando
              and c.stato = any (v_attivi))
          and not v_ambito = any (v_ambiti);
      end if;
      v_ok := v_ok and not v_id = any (v_righe);

      if v_ok and not p_prova then
        v_sovrascr := jsonb_build_object('bando_id', r_pc.bando_id,
                                         'bando_slug', r_pc.bando_slug,
                                         'bando_mancante_dal', r_pc.bando_mancante_dal);
        begin
          update public.partner_calls c
          set bando_id = v_bando, bando_slug = v_prima->>'bando_slug',
              bando_mancante_dal = null
          where c.id = v_id;
        exception when unique_violation then
          v_ok := false;
        end;
      end if;

    else
      -- Nessuna voce della 0043 ha altre forme.
      continue;
    end if;

    v_ok := coalesce(v_ok, false);
    if v_tabella = 'saved_bandi' then
      if v_ok then v_sb_rip := v_sb_rip + 1; else v_sb_con := v_sb_con + 1; end if;
    elsif v_tabella = 'calendar_events' then
      if v_ok then v_ce_rip := v_ce_rip + 1; else v_ce_con := v_ce_con + 1; end if;
    else
      if v_ok then v_pc_rip := v_pc_rip + 1; else v_pc_con := v_pc_con + 1; end if;
    end if;

    if v_ok then
      v_righe := v_righe || v_id;
      if v_ambito is not null then
        v_ambiti := v_ambiti || v_ambito;
      end if;
    end if;

    if v_ok and not p_prova then
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (null, v_azione, r_voce.target_user_id, r_voce.family_parent_id,
              jsonb_build_object('tabella', v_tabella, 'id', v_id,
                                 'operazione', v_operazione,
                                 'company_profile_id', r_voce.payload->'company_profile_id',
                                 'doppione', p_doppione, 'master', v_master,
                                 'voce', r_voce.id, 'prima', v_sovrascr));
    end if;
  end loop;

  return jsonb_build_object(
    'saved_bandi', jsonb_build_object('ripristinate', v_sb_rip, 'in_conflitto', v_sb_con),
    'calendar_events', jsonb_build_object('ripristinate', v_ce_rip, 'in_conflitto', v_ce_con),
    'partner_calls', jsonb_build_object('ripristinate', v_pc_rip, 'in_conflitto', v_pc_con));
end;
$$;

comment on function public.fn_ripristina_rimappatura(integer, timestamptz, boolean) is
  'Ripristina le righe che la rimappatura dei fusi (0043) ha spostato dal doppione al master, dalle voci audit_log catalogo.rimappatura_fuso del doppione scritte da p_dal in poi (dalla più recente), in una transazione e in modo idempotente: eliminata → riga reinserita con lo stesso id; aggiornata → bando_id e bando_slug precedenti (per le call bando_mancante_dal NULL e non il valore precedente: con una data vecchia lo scheduler chiuderebbe d''ufficio la call se il doppione non fosse ancora nella vista; con NULL la rivaluta da capo); convertita → di nuovo scadenza del bando. Una riga non più sul master, un evento convertito modificato dopo, utente o azienda inesistenti, un vincolo di unicità o una riga sul master che giustifica un''eliminazione successiva, non ripristinata, di un altro doppione dello stesso master nello stesso ambito: nessuna modifica, conteggio in_conflitto. Ogni riga ripristinata → audit_log catalogo.ripristino_rimappatura con la voce d''origine (payload.voce) e lo stato sovrascritto (payload.prima); le voci già ripristinate si saltano. p_prova = true (default): stessi conteggi, nessuna scrittura. Lock advisory della 0043 per le chiamate che scrivono. Ritorna {saved_bandi, calendar_events, partner_calls: {ripristinate, in_conflitto}}. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- Sicurezza: non eseguibile dai ruoli esposti (Supabase concede EXECUTE di
-- default a PUBLIC); si lancia a mano dal SQL editor.
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_ripristina_rimappatura(integer, timestamptz, boolean)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0044 (in transazione):
--   drop function public.fn_ripristina_rimappatura(integer, timestamptz, boolean);
-- Il drop non annulla i ripristini già scritti: le voci
-- catalogo.ripristino_rimappatura restano in audit_log (registro senza FK),
-- con lo stato sovrascritto in payload->'prima'.
-- ============================================================================
