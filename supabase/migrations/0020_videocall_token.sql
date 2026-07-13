-- ============================================================================
-- BandoFit — DB primario, migration 0020: token videochiamata (Jitsi) per
-- gli appuntamenti di consulenza.
--
-- L'istanza Jitsi self-hosted è APERTA (niente JWT): la sicurezza sta tutta
-- nel nome-stanza non indovinabile. Il token nasce col booking come default
-- di colonna — generato ESATTAMENTE una volta all'INSERT, gli UPDATE non lo
-- toccano mai (idempotenza per costruzione) — e non si persiste l'URL: lo
-- compone il backend come {JITSI_BASE_URL}/bandofit-{token}. Una
-- ri-prenotazione è una riga nuova → token nuovo per costruzione; l'annullo
-- lo nasconde da solo (le letture filtrano già stato='confermata').
--
-- Retroattività: ADD COLUMN con default VOLATILE (gen_random_uuid()) forza
-- il rewrite della tabella e Postgres valuta il default PER RIGA (docs
-- ALTER TABLE, Notes — il fast-default senza rewrite vale solo per i default
-- non volatili): ogni prenotazione esistente riceve un token proprio e
-- DISTINTO, nessun backfill necessario (e la UNIQUE sotto non può fallire).
-- ============================================================================

alter table public.consultation_bookings
  add column videocall_token uuid not null default gen_random_uuid();

alter table public.consultation_bookings
  add constraint consultation_bookings_videocall_token_key unique (videocall_token);

comment on column public.consultation_bookings.videocall_token is
  'Stanza Jitsi (bandofit-{token}): segreto non indovinabile, generato una sola volta alla nascita del booking e mai rigenerato; l''URL lo deriva il backend.';
