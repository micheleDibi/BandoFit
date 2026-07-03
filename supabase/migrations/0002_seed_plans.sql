-- ============================================================================
-- BandoFit — DB primario, migration 0002: seed dei piani di abbonamento
-- e funzione di promozione ad admin del primo utente.
-- I valori sono iniziali: l'admin li modifica dall'interfaccia.
-- ============================================================================

insert into public.subscription_plans
  (nome, slug, descrizione, prezzo_annuale, ai_check, alert_attivo, alert_giorni_preavviso, num_account_aziendali, ordering, is_active)
values
  ('Gratuito', 'gratuito', 'Per iniziare a esplorare i bandi',                    0.00, 0,   false, null, 1,  1, true),
  ('Smart',    'smart',    'Per professionisti che seguono pochi bandi mirati',  99.00, 5,   true,  7,    1,  2, true),
  ('Pro',      'pro',      'Per aziende e consulenti con esigenze continuative', 299.00, 20,  true,  15,   3,  3, true),
  ('Advisor',  'advisor',  'Per studi e advisor che gestiscono più clienti',     699.00, 100, true,  30,   10, 4, true);

-- ---------------------------------------------------------------------------
-- Promozione ad admin per email (da eseguire nel SQL Editor dopo la
-- registrazione del primo utente):
--   select public.promote_to_admin('email@example.com');
-- ---------------------------------------------------------------------------
create or replace function public.promote_to_admin(p_email text)
returns void
language sql
security definer
set search_path = public
as $$
  update public.profiles
  set role = 'admin'
  where lower(email) = lower(p_email);
$$;
