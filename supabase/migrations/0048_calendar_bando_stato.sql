-- ============================================================================
-- BandoFit — DB primario, migration 0048: STATO DEL BANDO SULLE SCADENZE IN
-- CALENDARIO.
--
-- Una scadenza in calendario (calendar_events, tipo 'bando') nasce come
-- istantanea del catalogo: data e ora della scadenza al momento della
-- creazione. Il catalogo ora cambia da solo (chiusure, proroghe, rettifiche di
-- date, sospensioni, revoche): il passo periodico del backend riallinea data,
-- ora e stato delle scadenze già in calendario. Questa colonna conserva lo
-- stato del bando (stato_effettivo del catalogo) all'ultimo allineamento, così
-- il calendario può mostrare una scadenza di un bando chiuso, sospeso o
-- revocato senza rileggere il catalogo a ogni richiesta.
--
-- bando_stato: NULL per gli eventi personali e finché lo stato non è noto
-- (eventi creati prima di questa migration: li riempie il primo riallineamento).
-- Nessun CHECK: i valori vengono dal catalogo, che può introdurne di nuovi.
-- Nessun backfill, nessun default, nessun indice (si legge per riga, mai si
-- filtra).
--
-- ADDITIVA e applicabile più volte (if not exists). Una colonna nullable senza
-- default si aggiunge senza riscrivere la tabella: il lock è istantaneo. Si
-- esegue con la procedura di docs/deploy.md (in un'unica transazione, con
-- lock_timeout). Rollback in coda.
-- ============================================================================

alter table public.calendar_events
  add column if not exists bando_stato text;

comment on column public.calendar_events.bando_stato is
  'Stato del bando (stato_effettivo del catalogo) all''ultimo allineamento della scadenza: lo scrivono la creazione e il riallineamento periodico del backend. NULL per gli eventi personali e finché non è noto. Nessun CHECK: i valori vengono dal catalogo.';

-- ============================================================================
-- ROLLBACK 0048 (in transazione, DOPO aver riportato il backend alla versione
-- precedente: quella nuova legge e scrive la colonna):
--   alter table public.calendar_events drop column if exists bando_stato;
-- Si perde solo lo stato copiato dal catalogo: il riallineamento lo riscrive
-- quando la colonna torna.
-- ============================================================================
