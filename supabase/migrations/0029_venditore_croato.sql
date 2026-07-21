-- ============================================================================
-- BandoFit — DB primario, migration 0029: VENDITORE CROATO + tipi soggetto a 2
-- + features_override sui piani.
--
-- Il venditore diventa ADVENTUS CONSULTING j.d.o.o. (Umag/Umago, Croazia,
-- OIB 95855486565, IVA standard 25%). Conseguenze su questo schema:
--   1) billing_profiles.tipo_soggetto si riduce a 2 valori: 'azienda' e
--      'privato' (spariscono azienda_it / privato_it / azienda_ue: la
--      distinzione IT/UE non ha più senso — l'Italia è un paese estero UE
--      come gli altri, dal punto di vista del venditore croato);
--   2) codice_destinatario e pec vengono CONGELATE (non droppate — precedente
--      user_addons/0028): erano il recapito SDI dell'era «venditore italiano»,
--      il backend non le legge/scrive più; il NOT NULL default '0000000' di
--      codice_destinatario si auto-soddisfa sugli insert che omettono la
--      colonna;
--   3) vies_valid cambia semantica: ora persistono anche gli esiti negativi.
--      NULL = mai verificata o VIES irraggiungibile all'ultimo salvataggio;
--      false = verificata e NON valida (si salva comunque: IVA 25%);
--      true = prova del reverse charge 0% (aziende UE ≠ HR).
--      L'aliquota la decide il backend (pricing) leggendo questo esito:
--      fail-open sul salvataggio, fail-closed sull'aliquota.
--   4) subscription_plans.features_override: bullet custom della card piano
--      (per il piano «tailored»); NULL = bullet derivate dai campi numerici.
--
-- Da eseguire IN UN'UNICA TRANSAZIONE (begin; ... commit;).
-- Rollback documentato in coda al file.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) tipo_soggetto: da 3 valori a 2. Ordine obbligato: drop CHECK → UPDATE →
--    add CHECK (le righe esistenti violerebbero il vincolo nuovo).
-- ----------------------------------------------------------------------------

alter table public.billing_profiles
  drop constraint billing_profiles_tipo_soggetto_check;

update public.billing_profiles
set tipo_soggetto = case tipo_soggetto
  when 'azienda_it' then 'azienda'
  when 'azienda_ue' then 'azienda'
  when 'privato_it' then 'privato'
  else tipo_soggetto  -- idempotente: 'azienda'/'privato' restano invariati
end;

alter table public.billing_profiles
  add constraint billing_profiles_tipo_soggetto_check
  check (tipo_soggetto in ('azienda', 'privato'));

-- Le vecchie azienda_ue conservano il loro paese; le azienda_it/privato_it
-- hanno già paese='IT' (colonna NOT NULL default 'IT' dalla 0026). vies_valid
-- resta com'è: i profili migrati hanno NULL → IVA 25% finché l'utente non
-- ri-salva l'anagrafica (che ri-esegue la verifica VIES).

-- ----------------------------------------------------------------------------
-- 2) Colonne SDI congelate + commenti di semantica.
-- ----------------------------------------------------------------------------

comment on column public.billing_profiles.codice_destinatario is
  'CONGELATA dalla 0029: recapito SDI dell''era «venditore italiano». Il '
  'backend non la legge/scrive più; il default ''0000000'' copre gli insert.';

comment on column public.billing_profiles.pec is
  'CONGELATA dalla 0029: recapito SDI alternativo dell''era «venditore '
  'italiano». Il backend non la legge/scrive più.';

comment on column public.billing_profiles.paese is
  'ISO 3166-1 alpha-2, QUALSIASI paese (dalla 0029; prima solo IT o UE). '
  'Decide la regola IVA: UE ≠ HR con VIES valido → reverse charge 0%.';

comment on column public.billing_profiles.vies_valid is
  'Esito VIES (solo aziende con paese UE ≠ HR). NULL = mai verificata o VIES '
  'irraggiungibile all''ultimo salvataggio; false = verificata e non valida '
  '(IVA 25%); true = prova del reverse charge 0%. Dalla 0029 il salvataggio '
  'NON è più bloccato da un esito negativo o da un guasto del VIES.';

-- ----------------------------------------------------------------------------
-- 3) Bullet custom della card piano (piano «tailored»).
-- ----------------------------------------------------------------------------

alter table public.subscription_plans
  add column features_override text[];

comment on column public.subscription_plans.features_override is
  'Bullet della card piano, una voce per elemento (ordine preservato). '
  'NULL = testo derivato dai campi numerici (template PlanCard: AI-check, '
  'avvisi, account). Usata dal piano «tailored».';

-- ----------------------------------------------------------------------------
-- 4) Verifica: nessuna riga fuori dai valori nuovi. La prova della
--    rimappatura vive QUI (l'harness dei test applica le migration su un DB
--    vuoto e non può simulare righe pre-0029).
-- ----------------------------------------------------------------------------

do $$
begin
  if exists (
    select 1 from public.billing_profiles
    where tipo_soggetto not in ('azienda', 'privato')
  ) then
    raise exception '0029: tipo_soggetto non mappato — rimappatura incompleta';
  end if;
end $$;

-- ============================================================================
-- ROLLBACK 0029 (eseguire in transazione). Il back-mapping è LOSSY: i vecchi
-- tipi erano tutti italiani o UE, mentre la 0029 ammette QUALSIASI paese. Le
-- righe estere senza equivalente vecchio (aziende extra-UE; privati con paese
-- ≠ IT, che diventerebbero a torto 'privato_it') vanno riclassificate a mano
-- prima — il DO le blocca.
--   do $$ begin
--     if exists (select 1 from public.billing_profiles
--                where (tipo_soggetto = 'azienda'
--                       and paese not in ('IT','AT','BE','BG','CY','CZ','DE','DK',
--                         'EE','ES','FI','FR','GR','HR','HU','IE','LT','LU','LV',
--                         'MT','NL','PL','PT','RO','SE','SI','SK'))
--                   or (tipo_soggetto = 'privato' and paese <> 'IT')) then
--       raise exception 'rollback bloccato: righe estere senza equivalente pre-0029';
--     end if;
--   end $$;
-- 1) alter table public.billing_profiles
--      drop constraint billing_profiles_tipo_soggetto_check;
-- 2) update public.billing_profiles set tipo_soggetto = case
--      when tipo_soggetto = 'azienda' and paese = 'IT' then 'azienda_it'
--      when tipo_soggetto = 'azienda' then 'azienda_ue'
--      when tipo_soggetto = 'privato' then 'privato_it'
--      else tipo_soggetto end;
-- 3) alter table public.billing_profiles
--      add constraint billing_profiles_tipo_soggetto_check
--      check (tipo_soggetto in ('azienda_it', 'privato_it', 'azienda_ue'));
-- 4) alter table public.subscription_plans drop column features_override;
-- 5) I comment on column sono cosmetici: nessun revert necessario.
-- ============================================================================
