# Database

## DB primario (Supabase, lettura/scrittura)

Contiene i dati della piattaforma. Schema in `supabase/migrations/` (eseguire in ordine).

### Tabelle

**`subscription_plans`** — piani di abbonamento annuali, gestibili dall'admin:
| Colonna | Tipo | Note |
|---|---|---|
| `id` | bigint identity | PK |
| `nome`, `slug` | text | `slug` unico (`[a-z0-9-]`), immutabile dall'interfaccia |
| `descrizione` | text | opzionale |
| `prezzo_annuale` | numeric(10,2) | ≥ 0 |
| `ai_check` | integer | numero di AI-check inclusi/anno |
| `alert_attivo` | boolean | se true, `alert_giorni_preavviso` è obbligatorio (check `plans_alert_coherence`) |
| `alert_giorni_preavviso` | integer | > 0 o NULL |
| `num_account_aziendali` | integer | ≥ 1 |
| `ordering`, `is_active` | int, bool | ordinamento in UI; i piani non si eliminano, si disattivano |

**`profiles`** — 1:1 con `auth.users` (PK = `auth.users.id`, on delete cascade): `email` (denormalizzata per la ricerca admin), `nome`, `cognome`, `azienda`, `telefono`, `role` (enum `admin`/`cliente`, default `cliente`), `is_active` (bool, default true).

**`user_subscriptions`** — storico abbonamenti: `user_id` → profiles, `plan_id` → plans, `status` (enum `active`/`cancelled`/`expired`), `data_inizio`, `data_scadenza` (default +1 anno). Indice unico parziale `user_subscriptions_one_active` ⇒ **un solo abbonamento `active` per utente**; il cambio piano cancella l'attivo e ne crea uno nuovo (lo storico resta).

### Funzioni e trigger

- `handle_new_user()` — trigger `AFTER INSERT ON auth.users`: crea profilo (dai metadata di signup) + abbonamento iniziale (`plan_slug` dai metadata, fallback `gratuito`). **Difensiva: non solleva mai eccezioni** (un errore bloccherebbe la registrazione per tutti).
- `fn_switch_plan(p_user_id, p_plan_id)` — cambio piano atomico, chiamata dal backend via RPC con service_role. Rifiuta piani inesistenti o disattivati.
- `promote_to_admin(p_email)` — da eseguire nel SQL Editor per promuovere il primo admin.
- `set_updated_at()` — mantiene `updated_at` su profiles e subscription_plans.

### Sicurezza

- **RLS deny-all**: RLS abilitata su tutte e tre le tabelle senza alcuna policy → `anon` e `authenticated` non leggono/scrivono nulla; il backend usa `service_role` che bypassa la RLS. In più, `REVOKE ALL` esplicito sui ruoli client.
- **RPC bloccate**: Supabase concede di default `EXECUTE` ad `anon`/`authenticated` su ogni funzione di `public`, e PostgREST le espone come `POST /rest/v1/rpc/<nome>`. Le migration revocano esplicitamente `EXECUTE` su `fn_switch_plan` e `promote_to_admin` dai ruoli client: senza questa revoca chiunque potrebbe auto-promuoversi admin o cambiare piano ad altri.

## DB secondario (Supabase, SOLA LETTURA)

Catalogo dei bandi, alimentato esternamente. **BandoFit non vi scrive mai**: il backend usa la chiave `anon`, che per costruzione (RLS del secondario) consente solo SELECT. Il dump di riferimento è in `database_secondario_dump/` (solo locale, escluso da git).

Tabelle usate:
- **`bando`** (~4.000 righe): titolo/titolo_breve/descrizione_breve, `slug` (unico), date (pubblicazione/apertura/scadenza), `importo_totale_eur`/`importo_max_per_progetto_eur`, `ente_erogatore`, `stato_bando` (`aperto`/`chiuso`/`in apertura prossimamente`), `livello` (`flash_bando`/`guida_bando`), `contenuto` (JSONB a sezioni), `allegati` (JSONB), FK verso `tipologie_bando`, `modalita_erogazione`, `programmi`. La RLS anon espone solo `stato_processing='completed' AND slug IS NOT NULL` (~1.260 righe).
- **Junction M:N** (tutte `UNIQUE(bando_id, dim_id)`, indicizzate): `bando_regioni`, `bando_settori`, `bando_beneficiari`, `bando_codici_ateco`.
- **Lookup**: `regioni` (20), `settori` (90), `beneficiari` (31), `codici_ateco` (89, divisioni a 2 cifre), `tipologie_bando` (5), `modalita_erogazione` (4), `programmi` (56).

Vincoli operativi: statement timeout di **3 secondi** per il ruolo `anon` → il backend limita `page_size` a 50 e filtra su colonne indicizzate; ricerca full-text con indici GIN italiani su titolo/descrizione.
