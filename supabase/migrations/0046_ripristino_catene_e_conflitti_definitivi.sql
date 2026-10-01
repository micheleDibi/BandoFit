-- ============================================================================
-- BandoFit — DB primario, migration 0046: RIPRISTINO LUNGO LE CATENE DI
-- FUSIONI E CONFLITTI DEFINITIVI DEL PERCORSO AUTOMATICO (complemento della
-- 0043, della 0044 e della 0045).
--
-- 1) Catene. Un doppione D fuso in M, poi M fuso in M2: il passo della
--    rimappatura sposta su M2 anche le righe nate su D (voce
--    catalogo.rimappatura_fuso con doppione M). Se poi D si separa, la 0044
--    confrontava la riga con il master della voce di D (M) e la contava in
--    conflitto per sempre. Ora il ripristino segue la catena delle voci di
--    rimappatura della stessa riga (stessa tabella e stesso id, operazione
--    'aggiornata', doppione uguale al master dell'anello precedente, più
--    recenti e non ancora ripristinate): la riga che sta sul master finale
--    della catena vale come «ancora di D». Quando la riga torna a D, ogni
--    anello si chiude con una voce catalogo.ripristino_rimappatura che lo cita
--    (operazione 'catena', payload.tramite = la voce di D): una separazione
--    successiva di M non lo riprova. La regola della 0044 sulla riga che
--    «sostiene» l'eliminazione della riga di un altro doppione nello stesso
--    ambito vale per ogni master della catena. Un anello che ha eliminato o
--    convertito la riga ferma la catena: quella riga resta in conflitto, da
--    vedere a mano come prima.
-- 2) Conflitti definitivi del percorso automatico. Se il doppione è già
--    presente nello stesso ambito utente × azienda (l'utente l'ha salvato di
--    nuovo o ha rimesso la scadenza in calendario), la 0044 contava la voce
--    in conflitto e il passo la riprovava a ogni giro: quando poi l'utente
--    toglieva quel preferito, il passo riportava il doppione contro la sua
--    scelta. Nel percorso automatico quel conflitto (solo saved_bandi e
--    calendar_events: le call non esprimono una scelta sulla stessa riga)
--    scrive una voce catalogo.conflitto_definitivo che cita la voce d'origine
--    in payload.voce, con la convenzione della 0044: il percorso automatico
--    non la riprova più e fn_doppioni_rimappati non la conta. Gli altri
--    conflitti restano ritentati a ogni passo.
--
--   fn_ripristina_rimappatura_voci(doppione, dal, prova, automatico) → jsonb
--   - il corpo del ripristino: quello della 0044, con la catena;
--   - automatico = true (il passo del backend): salta le voci marcate, scrive
--     i marcatori (mai in prova) e conta per tabella anche `definitivi`, i
--     conflitti che diventano definitivi nella chiamata (già compresi in
--     in_conflitto);
--   - automatico = false: ignora i marcatori e non ne scrive (procedura
--     manuale: decide chi la lancia).
--   fn_ripristina_rimappatura(doppione, dal, prova default true) → jsonb —
--   stessa firma e stesso ritorno della 0044 (la procedura manuale), ora con
--   la catena: chiama la funzione sopra con automatico = false.
--   fn_doppioni_rimappati() → integer[] — come la 0045, ma esclude anche le
--   voci con un marcatore.
--
-- Il backend con la 0046 chiama fn_ripristina_rimappatura_voci: senza questa
-- migration il passo conta un errore per ogni doppione separato e non
-- ripristina nulla (docs/deploy.md).
--
-- ADDITIVA: una funzione nuova e due ridefinite con create or replace (stessa
-- firma, SECURITY DEFINER, search_path e revoche delle originali); nessuna
-- tabella o riga toccata. Applicabile più volte. Da eseguire IN UN'UNICA
-- TRANSAZIONE (begin; ... commit;). Rollback in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Corpo del ripristino, con la catena e i conflitti definitivi.
-- ----------------------------------------------------------------------------
create or replace function public.fn_ripristina_rimappatura_voci(
  p_doppione   integer,
  p_dal        timestamptz,
  p_prova      boolean,
  p_automatico boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_origine    constant text := 'catalogo.rimappatura_fuso';
  v_azione     constant text := 'catalogo.ripristino_rimappatura';
  v_marcatore  constant text := 'catalogo.conflitto_definitivo';
  v_attivi     constant text[] := array['bozza', 'pubblicata', 'sospesa_moderazione'];
  r_voce       record;
  r_anello     record;
  r_sb         public.saved_bandi%rowtype;
  r_ce         public.calendar_events%rowtype;
  r_pc         public.partner_calls%rowtype;
  v_tabella    text;
  v_operazione text;
  v_id         uuid;
  v_master     integer;
  v_finale     integer;
  v_anelli     bigint[];
  v_masters    integer[];
  v_dal_master timestamptz[];
  v_prima      jsonb;
  v_bando      integer;
  v_utente     uuid;
  v_azienda    uuid;
  v_ambito     text;
  v_sovrascr   jsonb;
  v_sostiene   boolean;
  v_trovata    boolean;
  v_base       boolean;
  v_presente   boolean;
  v_ok         boolean;
  v_definitivo boolean;
  -- Righe e ambiti già ripristinati in questa chiamata: la prova, che non
  -- scrive, li vede come li vedrebbe la scrittura.
  v_righe      uuid[] := '{}';
  v_ambiti     text[] := '{}';
  v_sb_rip     integer := 0;
  v_sb_con     integer := 0;
  v_sb_def     integer := 0;
  v_ce_rip     integer := 0;
  v_ce_con     integer := 0;
  v_ce_def     integer := 0;
  v_pc_rip     integer := 0;
  v_pc_con     integer := 0;
  v_esito      jsonb;
begin
  if p_doppione is null or p_doppione <= 0 or p_dal is null or p_prova is null
     or p_automatico is null then
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
      -- Il percorso automatico non riprova una voce marcata.
      and (not p_automatico or not exists (
        select 1 from public.audit_log m
        where m.action = v_marcatore and m.payload->'voce' = to_jsonb(v.id)
      ))
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
    v_presente   := false;
    v_definitivo := false;

    -- Catena: se il master è stato a sua volta fuso, la stessa riga ha voci
    -- 'aggiornata' successive con doppione = il master precedente. La riga
    -- attesa sta sull'ultimo master della catena. Ogni anello ha un id
    -- maggiore del precedente: la catena finisce sempre.
    v_finale     := v_master;
    v_anelli     := '{}';
    v_masters    := array[v_master];
    v_dal_master := array[r_voce.created_at];
    if v_operazione = 'aggiornata' then
      loop
        select a.id, (a.payload->>'master')::integer as master, a.created_at
          into r_anello
        from public.audit_log a
        where a.action = v_origine
          and a.payload->'tabella' = r_voce.payload->'tabella'
          and a.payload->'id' = r_voce.payload->'id'
          and a.payload->>'operazione' = 'aggiornata'
          and a.payload->'doppione' = to_jsonb(v_finale)
          and a.id > coalesce(v_anelli[cardinality(v_anelli)], r_voce.id)
          and not exists (
            select 1 from public.audit_log r
            where r.action = v_azione and r.payload->'voce' = to_jsonb(a.id)
          )
        order by a.id
        limit 1;
        exit when not found;
        v_anelli     := v_anelli || r_anello.id;
        v_masters    := v_masters || r_anello.master;
        v_dal_master := v_dal_master || r_anello.created_at;
        v_finale     := r_anello.master;
      end loop;
    end if;

    -- Una riga portata su un master (della catena) può essere la ragione per
    -- cui, dopo, la rimappatura di un ALTRO doppione di quel master ha
    -- eliminato la riga di quel doppione nello stesso ambito: riportarla
    -- indietro lascerebbe l'ambito senza master né altro doppione. Finché
    -- quell'eliminazione non è ripristinata, la riga resta dov'è
    -- (in_conflitto).
    v_sostiene := v_operazione = 'aggiornata'
      and v_tabella in ('saved_bandi', 'calendar_events')
      and exists (
        select 1
        from public.audit_log o
        join unnest(v_masters, v_dal_master) as k(master, dal)
          on o.payload->'master' = to_jsonb(k.master) and o.created_at >= k.dal
        where o.action = v_origine
          and o.payload->'tabella' = r_voce.payload->'tabella'
          and o.payload->>'operazione' = 'eliminata'
          and o.payload->'doppione' <> to_jsonb(p_doppione)
          and o.target_user_id is not distinct from r_voce.target_user_id
          and o.payload->'company_profile_id' = r_voce.payload->'company_profile_id'
          and not exists (
            select 1 from public.audit_log r
            where r.action = v_azione and r.payload->'voce' = to_jsonb(o.id)
          )
      );

    -- In ogni ramo: v_base = la riga è come l'ha lasciata la rimappatura e
    -- nient'altro lo impedisce; v_presente = il doppione è già nello stesso
    -- ambito (nel DB o per un ripristino precedente di questa chiamata).
    -- Si ripristina con v_base e senza v_presente; v_base con v_presente è il
    -- conflitto che il percorso automatico rende definitivo.

    -- a) Righe eliminate: reinserite intere, se l'id è libero, utente e
    --    azienda esistono ancora e il doppione non è già nello stesso ambito.
    if v_operazione = 'eliminata' and v_tabella in ('saved_bandi', 'calendar_events') then
      v_utente  := (v_prima->>'user_id')::uuid;
      v_azienda := (v_prima->>'company_profile_id')::uuid;
      v_ambito  := format('%s|%s|%s|%s', v_tabella, v_utente, v_azienda, v_bando);
      v_base := exists (select 1 from public.profiles p where p.id = v_utente)
        and (v_azienda is null
             or exists (select 1 from public.company_profiles c where c.id = v_azienda))
        and not v_id = any (v_righe);
      if v_tabella = 'saved_bandi' then
        v_base := v_base
          and not exists (select 1 from public.saved_bandi s where s.id = v_id);
        v_presente := exists (
          select 1 from public.saved_bandi s
          where s.user_id = v_utente
            and s.company_profile_id is not distinct from v_azienda
            and s.bando_id = v_bando);
      else
        v_base := v_base
          and not exists (select 1 from public.calendar_events e where e.id = v_id);
        v_presente := exists (
          select 1 from public.calendar_events e
          where e.tipo = 'bando'
            and e.user_id = v_utente
            and e.company_profile_id is not distinct from v_azienda
            and e.bando_id = v_bando);
      end if;
      v_presente := v_presente or v_ambito = any (v_ambiti);
      v_ok := v_base and not v_presente;
      v_definitivo := v_base and v_presente;

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

    -- b) Bandi salvati rimappati: tornano al doppione se sono ancora sull'ultimo
    --    master della catena.
    elsif v_operazione = 'aggiornata' and v_tabella = 'saved_bandi' then
      if p_prova then
        select * into r_sb from public.saved_bandi s where s.id = v_id;
      else
        select * into r_sb from public.saved_bandi s where s.id = v_id for update;
      end if;
      v_trovata := found;
      v_ambito := format('%s|%s|%s|%s', v_tabella, r_sb.user_id, r_sb.company_profile_id,
                         v_bando);
      v_base := v_trovata and r_sb.bando_id = v_finale and not v_sostiene
        and not v_id = any (v_righe);
      v_presente := exists (
          select 1 from public.saved_bandi s
          where s.user_id = r_sb.user_id
            and s.company_profile_id is not distinct from r_sb.company_profile_id
            and s.bando_id = v_bando
            and s.id <> v_id)
        or v_ambito = any (v_ambiti);
      v_ok := v_base and not v_presente;
      v_definitivo := v_base and v_presente;

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
      v_trovata := found;
      v_ambito := format('%s|%s|%s|%s', v_tabella, r_ce.user_id, r_ce.company_profile_id,
                         v_bando);
      v_base := v_trovata
        and case v_operazione
              when 'aggiornata' then r_ce.tipo = 'bando' and r_ce.bando_id = v_finale
                                     and not v_sostiene
              else r_ce.tipo = 'personale' and r_ce.bando_id is null
                   and r_ce.updated_at <= r_voce.created_at
            end
        and not v_id = any (v_righe);
      v_presente := exists (
          select 1 from public.calendar_events e
          where e.tipo = 'bando'
            and e.user_id = r_ce.user_id
            and e.company_profile_id is not distinct from r_ce.company_profile_id
            and e.bando_id = v_bando
            and e.id <> v_id)
        or v_ambito = any (v_ambiti);
      v_ok := v_base and not v_presente;
      v_definitivo := v_base and v_presente;

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

    -- d) Call di partenariato rimappate: tornano al doppione se sono ancora
    --    sull'ultimo master della catena e, se attive, l'azienda non ha già
    --    un'altra call attiva sul doppione (partner_calls_una_attiva).
    --    bando_mancante_dal torna NULL: una data vecchia farebbe chiudere
    --    d'ufficio la call al primo passo dello scheduler in cui il doppione
    --    non fosse ancora nella vista. Nessun conflitto definitivo.
    elsif v_operazione = 'aggiornata' and v_tabella = 'partner_calls' then
      if p_prova then
        select * into r_pc from public.partner_calls c where c.id = v_id;
      else
        select * into r_pc from public.partner_calls c where c.id = v_id for update;
      end if;
      v_ok := found and r_pc.bando_id = v_finale;
      if r_pc.stato = any (v_attivi) then
        v_ambito := format('%s|%s|%s', v_tabella, r_pc.company_profile_id, v_bando);
        v_ok := v_ok
          and not exists (
            select 1 from public.partner_calls c
            where c.company_profile_id = r_pc.company_profile_id
              and c.bando_id = v_bando
              and c.stato = any (v_attivi)
              and c.id <> v_id)
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
    v_definitivo := p_automatico and not v_ok and coalesce(v_definitivo, false)
      and v_tabella in ('saved_bandi', 'calendar_events');
    if v_tabella = 'saved_bandi' then
      if v_ok then v_sb_rip := v_sb_rip + 1; else v_sb_con := v_sb_con + 1; end if;
      if v_definitivo then v_sb_def := v_sb_def + 1; end if;
    elsif v_tabella = 'calendar_events' then
      if v_ok then v_ce_rip := v_ce_rip + 1; else v_ce_con := v_ce_con + 1; end if;
      if v_definitivo then v_ce_def := v_ce_def + 1; end if;
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
      -- Gli anelli della catena sono superati: la riga è tornata al doppione
      -- d'origine.
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      select null, v_azione, a.target_user_id, a.family_parent_id,
             jsonb_build_object('tabella', a.payload->'tabella', 'id', a.payload->'id',
                                'operazione', 'catena',
                                'company_profile_id', a.payload->'company_profile_id',
                                'doppione', a.payload->'doppione',
                                'master', a.payload->'master',
                                'voce', a.id, 'tramite', r_voce.id, 'prima', null)
      from public.audit_log a
      where a.id = any (v_anelli)
      order by a.id;
    end if;

    if v_definitivo and not p_prova then
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (null, v_marcatore, r_voce.target_user_id, r_voce.family_parent_id,
              jsonb_build_object('tabella', v_tabella, 'id', v_id,
                                 'operazione', v_operazione,
                                 'company_profile_id', r_voce.payload->'company_profile_id',
                                 'doppione', p_doppione, 'master', v_master,
                                 'voce', r_voce.id,
                                 'motivo', 'doppione_nello_stesso_ambito'));
    end if;
  end loop;

  v_esito := jsonb_build_object(
    'saved_bandi', jsonb_build_object('ripristinate', v_sb_rip, 'in_conflitto', v_sb_con),
    'calendar_events', jsonb_build_object('ripristinate', v_ce_rip, 'in_conflitto', v_ce_con),
    'partner_calls', jsonb_build_object('ripristinate', v_pc_rip, 'in_conflitto', v_pc_con));
  if p_automatico then
    v_esito := jsonb_set(v_esito, '{saved_bandi,definitivi}', to_jsonb(v_sb_def));
    v_esito := jsonb_set(v_esito, '{calendar_events,definitivi}', to_jsonb(v_ce_def));
    v_esito := jsonb_set(v_esito, '{partner_calls,definitivi}', to_jsonb(0));
  end if;
  return v_esito;
end;
$$;

comment on function public.fn_ripristina_rimappatura_voci(integer, timestamptz, boolean, boolean) is
  'Corpo del ripristino della rimappatura dei fusi (0044, ridefinito dalla 0046): riporta al doppione le righe delle voci audit_log catalogo.rimappatura_fuso scritte da p_dal in poi (dalla più recente), in una transazione e in modo idempotente, con le regole e i conflitti della 0044. Segue la catena delle voci aggiornata della stessa riga (doppione = master precedente, non ripristinate): la riga sull''ultimo master vale come ancora del doppione; a ripristino fatto ogni anello si chiude con una voce catalogo.ripristino_rimappatura (operazione catena, payload.tramite). p_automatico = true (il passo del backend): salta le voci citate da una voce catalogo.conflitto_definitivo, scrive quella voce (mai in prova) quando una riga di saved_bandi o calendar_events è ripristinabile ma il doppione è già nello stesso ambito, e ritorna anche definitivi per tabella (compresi in in_conflitto). p_automatico = false: ignora i marcatori e non ne scrive. Ritorna {saved_bandi, calendar_events, partner_calls: {ripristinate, in_conflitto[, definitivi]}}. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 2) Ripristino manuale: stessa firma, stesso default e stesso ritorno della
--    0044; il corpo è quello sopra, senza marcatori.
-- ----------------------------------------------------------------------------
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
begin
  return public.fn_ripristina_rimappatura_voci(p_doppione, p_dal, p_prova, false);
end;
$$;

comment on function public.fn_ripristina_rimappatura(integer, timestamptz, boolean) is
  'Ripristina le righe che la rimappatura dei fusi (0043) ha spostato dal doppione al master, dalle voci audit_log catalogo.rimappatura_fuso del doppione scritte da p_dal in poi (dalla più recente), in una transazione e in modo idempotente: eliminata → riga reinserita con lo stesso id; aggiornata → bando_id e bando_slug precedenti (per le call bando_mancante_dal NULL e non il valore precedente: con una data vecchia lo scheduler chiuderebbe d''ufficio la call se il doppione non fosse ancora nella vista; con NULL la rivaluta da capo); convertita → di nuovo scadenza del bando. Dalla 0046 segue la catena delle voci (master fuso a sua volta): la riga sull''ultimo master vale come ancora del doppione, e gli anelli si chiudono con il ripristino. Una riga non più lì, un evento convertito modificato dopo, utente o azienda inesistenti, un vincolo di unicità o una riga sul master che giustifica un''eliminazione successiva, non ripristinata, di un altro doppione dello stesso master nello stesso ambito: nessuna modifica, conteggio in_conflitto. Ogni riga ripristinata → audit_log catalogo.ripristino_rimappatura con la voce d''origine (payload.voce) e lo stato sovrascritto (payload.prima); le voci già ripristinate si saltano. Procedura manuale: ignora le voci catalogo.conflitto_definitivo e non ne scrive (fn_ripristina_rimappatura_voci con p_automatico = false). p_prova = true (default): stessi conteggi, nessuna scrittura. Lock advisory della 0043 per le chiamate che scrivono. Ritorna {saved_bandi, calendar_events, partner_calls: {ripristinate, in_conflitto}}. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 3) Doppioni rimappati: come la 0045, escluse anche le voci marcate.
-- ----------------------------------------------------------------------------
create or replace function public.fn_doppioni_rimappati()
returns integer[]
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(array_agg(d.doppione order by d.doppione), '{}'::integer[])
  from (
    select distinct (v.payload->>'doppione')::integer as doppione
    from public.audit_log v
    where v.action = 'catalogo.rimappatura_fuso'
      and jsonb_typeof(v.payload->'doppione') = 'number'
      and not exists (
        select 1 from public.audit_log r
        where r.action in ('catalogo.ripristino_rimappatura', 'catalogo.conflitto_definitivo')
          and r.payload->'voce' = to_jsonb(v.id)
      )
  ) d;
$$;

comment on function public.fn_doppioni_rimappati() is
  'Array degli id distinti e crescenti dei doppioni con almeno una voce audit_log catalogo.rimappatura_fuso (0043) che nessuna voce catalogo.ripristino_rimappatura (0044) né catalogo.conflitto_definitivo (0046) cita in payload->''voce'': i doppioni la cui separazione nel catalogo va rilevata dal passo della rimappatura (0045). Un solo valore, così il max-rows di PostgREST non lo tronca; senza voci restituisce ''{}'', mai NULL.';

-- ----------------------------------------------------------------------------
-- Sicurezza: nessuna delle tre è eseguibile dai ruoli esposti (Supabase
-- concede EXECUTE di default a PUBLIC); le chiama il backend con service_role,
-- o l'operatore dal SQL editor.
-- ----------------------------------------------------------------------------
revoke execute on function public.fn_ripristina_rimappatura_voci(integer, timestamptz, boolean, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_ripristina_rimappatura(integer, timestamptz, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_doppioni_rimappati()
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0046 (in transazione, DOPO aver riportato il backend alla versione
-- precedente o spento la rimappatura: quella nuova chiama
-- fn_ripristina_rimappatura_voci):
-- 1) rieseguire il contenuto della 0044 e della 0045 (create or replace:
--    tornano le definizioni precedenti, con le stesse revoche);
-- 2) drop function public.fn_ripristina_rimappatura_voci(integer, timestamptz, boolean, boolean);
-- Nessun dato da annullare: i ripristini già scritti (anche le voci con
-- operazione 'catena') e le voci catalogo.conflitto_definitivo restano in
-- audit_log (registro senza FK). Con le definizioni della 0044 e della 0045 i
-- marcatori non contano più: i doppioni con voci marcate tornano nell'elenco
-- e il passo le riprova.
-- ============================================================================
