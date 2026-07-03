# Database

> Documento in costruzione: viene ampliato ad ogni fase di sviluppo.

## DB primario (Supabase, lettura/scrittura)

Contiene i dati della piattaforma. Schema definito in `supabase/migrations/`.

Tabelle previste (dettagli nella migration `0001_initial_schema.sql`):
- `profiles` — anagrafica utente collegata 1:1 ad `auth.users`, ruolo (`admin`/`cliente`), stato attivo.
- `subscription_plans` — piani di abbonamento annuali con parametri modificabili dall'admin: prezzo, `ai_check`, `alert_attivo` + `alert_giorni_preavviso`, `num_account_aziendali`.
- `user_subscriptions` — storico abbonamenti; un solo abbonamento `active` per utente (indice unico parziale).

RLS: abilitata su tutte le tabelle, **nessuna policy** → accesso esclusivamente dal backend con `service_role`.

## DB secondario (Supabase, SOLA LETTURA)

Catalogo dei bandi, alimentato esternamente. **Non va mai scritto da BandoFit.** Il dump di riferimento è in `database_secondario_dump/` (schema + dati, formato `pg_dump` plain SQL).

Tabelle rilevanti:
- `bando` — fatti principali (~4.000 righe, di cui ~1.260 `completed` visibili pubblicamente): titolo, descrizione, date (pubblicazione/apertura/scadenza), importi, ente erogatore, stato (`aperto`/`chiuso`/`in apertura prossimamente`), contenuto ricco in JSONB.
- Junction M:N: `bando_regioni`, `bando_settori`, `bando_beneficiari`, `bando_codici_ateco`.
- Lookup: `regioni` (20), `settori` (90), `beneficiari` (31), `codici_ateco` (89), `tipologie_bando` (5), `modalita_erogazione` (4), `programmi` (56).

RLS del secondario (già impostata a monte): il ruolo `anon` legge i bandi solo con `stato_processing='completed' AND slug IS NOT NULL`; lookup e junction sono leggibili liberamente. Timeout statement per `anon`: 3 secondi.
