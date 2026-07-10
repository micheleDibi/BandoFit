-- ============================================================================
-- BandoFit — DB primario, migration 0014: il ruolo «progettista».
--
-- SOLO l'estensione dell'enum, in un file a sé: un valore aggiunto a un enum
-- non è utilizzabile nella stessa transazione («unsafe use of new value»), e
-- sia lo SQL Editor di Supabase sia l'harness dei test (tests/db/conftest.py)
-- applicano ogni file come UNA transazione. Tabelle e funzioni che usano il
-- valore stanno nella 0015.
-- ============================================================================

alter type public.user_role add value if not exists 'progettista';
