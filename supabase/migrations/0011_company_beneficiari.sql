-- ============================================================================
-- BandoFit — DB primario, migration 0011: categorie di beneficiario dichiarate
-- dall'azienda.
--
-- Sostituiscono le categorie DEDOTTE dalla visura (`company_data.derived
-- .beneficiari`, ora rimosse): dalla visura si ricavano solo dimensione e
-- forma giuridica, mentre il catalogo bandi distingue anche Istituti
-- Scolastici, Organismi di formazione, Enti pubblici… — categorie che nessun
-- attributo camerale può dedurre. Ora le dichiara l'utente.
--
-- Un'azienda può appartenere a PIÙ categorie: si conservano id + nome della
-- lookup `beneficiari` del DB SECONDARIO. Nessuna FK (progetti Supabase
-- distinti), stessa denormalizzazione già usata per settore_id/settore_nome:
-- il nome serve al prompt dell'AI-check e alle etichette del report senza
-- interrogare il catalogo.
--
-- La colonna sta su company_profiles (non su una tabella ponte) perché il
-- pre-check di compatibilità la rilegge a OGNI richiesta di GET /bandi, dove
-- company_profiles è già nella query: zero letture aggiuntive.
--
-- Nessun backfill: le categorie dedotte non vengono migrate. Erano inaffidabili
-- (una qualsiasi società cooperativa risultava «Cooperativa sociale» per un
-- match per sottostringa) e il campo, ora dichiarato, deve essere confermato
-- dall'utente. Finché è vuoto il requisito «beneficiari» resta NON VALUTATO e
-- non entra nel punteggio di compatibilità: nessuna azienda è penalizzata.
-- ============================================================================

alter table public.company_profiles
  add column if not exists beneficiari jsonb not null default '[]'::jsonb;

-- Solo array: il codice si aspetta [{id, nome}, ...] e itera senza guardie.
alter table public.company_profiles
  drop constraint if exists company_profiles_beneficiari_is_array;
alter table public.company_profiles
  add constraint company_profiles_beneficiari_is_array
  check (jsonb_typeof(beneficiari) = 'array');

comment on column public.company_profiles.beneficiari is
  'Categorie di beneficiario dichiarate: [{"id": int, "nome": text}] dalla lookup beneficiari del DB secondario. Nessuna FK cross-database.';
