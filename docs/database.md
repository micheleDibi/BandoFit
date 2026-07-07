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

### Famiglie di account (migration 0003)

**`family_members`** — membership e inviti in un'unica tabella (una riga per "permanenza"): `parent_id`/`member_id` → profiles (FK con nomi espliciti per gli embed PostgREST), `denominazione`, `invited_email`, `invite_kind` (`new_user`/`existing_user`), `status` (`pending` → `active` → `demoted`; terminali: `removed`, `declined`), `invited_at`, `joined_at` (chiave d'ordine per le retrocessioni), `demoted_at`, `removed_at`. **Indice unico parziale**: un utente può avere una sola membership corrente (pending/active/demoted). Regole: il limite account del piano **include il padre**; i figli attivi **non hanno un abbonamento proprio** (ereditano dal padre); le quote sono condivise a livello famiglia.

**`company_profiles`** — dati aziendali, uno per padre: ragione sociale, P.IVA (check 11 cifre), forma giuridica, ATECO/settore/regione (id delle lookup del DB secondario + copie testo denormalizzate, nessuna FK cross-DB), sede legale (CAP check 5 cifre), classe dimensionale, dipendenti, fascia fatturato, PEC/telefono/sito.

**`audit_log`** — operazioni sensibili (inviti, retrocessioni, rimozioni, cambi piano, modifiche dati aziendali), senza FK: le righe sopravvivono alla cancellazione degli utenti. Consultabile dal SQL Editor.

**`auth_tokens`** (migration 0004) — token monouso per i link email di dominio (conferma indirizzo, recovery, inviti): `user_id` → profiles (cascade), `purpose`, `token_hash` (SHA-256, unico — il token in chiaro non viene mai salvato), `expires_at`, `used_at`. Consumo atomico via UPDATE condizionato.

### Dati certificati, preferenze e consumi (migration 0005)

**`profiles`** (colonne aggiunte) — `codice_fiscale` (16 caratteri maiuscoli, check di formato) e `cf_verified_at` (verifica all'Anagrafe Tributaria via openapi.it). Il trigger `fn_reset_cf_verification` azzera la verifica se il CF cambia, tranne quando lo statement imposta esplicitamente anche `cf_verified_at` (flusso di verifica).

**`company_data`** — la "visura" certificata da openapi.it (endpoint IT-full), **una riga per azienda** (`company_profile_id` unico → company_profiles, cascade): `raw` jsonb è il payload completo (fonte di verità), `derived` jsonb i valori calcolati all'import (divisione ATECO, match regione, beneficiari derivati, fasce), più estratti "caldi" (`denominazione`, `stato_impresa`), `piva_fetched` (check 11 cifre), `sandbox`, `fetch_count`, `fetched_at`. Solo l'ultima versione: lo storico dei recuperi vive in `audit_log` + `api_usage_events`.

**`company_people`** — persone estratte dalla visura a ogni import (replace-all): `kind` (`manager`/`shareholder`/`auditor`), anagrafica, `ruoli` jsonb, `is_legale_rappresentante`, `quota_percentuale`, frammento `raw` originale. Cascade da company_profiles.

**`user_preferences`** — preferenze di filtro/notifica **per utente** (anche i figli hanno le proprie): una riga per (utente, faccetta, id lookup del secondario) con `facet` vincolata alle 7 faccette dei bandi, `label` denormalizzata (nessuna FK cross-DB), unique su (user_id, facet, ref_id). Cascade da profiles.

**`api_usage_events`** — registro dei consumi API a pagamento, **senza FK** (come audit_log: sopravvive alle cancellazioni): provider/service/outcome (`success`/`error`/`timeout_unknown`), `cost_cents`, `response_status`, `request_meta` (mai dati personali in chiaro). Per l'AI-check annota ogni esito con `provider='anthropic'`, `service='ai_check'` e i token consumati nel `request_meta` — ma è un registro di SPESA best-effort: il **conteggio quota** usa le righe di `ai_checks` (`pending`+`ready` nella finestra dell'abbonamento), che cambiano stato atomicamente e sono transazionali col risultato.

**`company_import_locks`** + `fn_acquire_import_lock(parent_id, ttl)` / `fn_release_import_lock(parent_id)` — lock anti doppia-spesa per l'import: la chiamata HTTP esterna avviene tra statement PostgREST, quindi il claim è un INSERT atomico con "furto" solo se il lock esistente è scaduto (TTL clampato a 600s). Riusato anche dalla verifica CF e dalle richieste di documenti.

### Documenti ufficiali (migration 0006)

**`company_documents`** — visure camerali richieste a openapi.it (documenti asincroni): `kind` (`visura`), `endpoint` (la variante che ha accettato: capitale/persone/impresa-individuale — il tipo giusto si scopre per tentativi, i rifiuti sono gratuiti), `request_id` del provider, `status` (`pending`→`ready`/`error`), riferimento al PDF nel bucket Storage **`company-documents`** (`file_path`), **`extracted_text`** (testo del PDF via pypdf: oggetto sociale e poteri inclusi — input per l'AI-check), `cost_cents`, `sandbox`. Indice unico parziale: al massimo UNA richiesta `pending` per azienda e tipo. Cascade da company_profiles; il file nel bucket viene rimosso dal backend alla cancellazione.

### AI-check (migration 0007)

**`bando_requirements`** — cache delle estrazioni LLM per bando (indipendente dall'azienda, una riga per `bando_id` — id intero del DB secondario, **senza FK cross-database**): `content_hash` (hash_bando del catalogo, fallback sha256 dell'input serializzato), `prompt_version`, `model`, **`extraction` jsonb** (requisiti obbligatori + criteri di valutazione + griglia, con citazioni), token consumati. Aggiornata in place quando cambia hash o versione dei prompt: l'estrazione si paga una volta sola per bando, tutte le aziende la riusano.

**`ai_checks`** — report di compatibilità azienda↔bando (**storico versionato**: ogni generazione è una nuova riga, la più recente è in evidenza): `company_profile_id` (cascade), `user_id` (richiedente, senza FK), `family_parent_id` (chiave di visibilità e quota), `bando_id`/`bando_slug`/`bando_titolo` denormalizzati (lo storico sopravvive al bando), `status` (`pending`→`ready`/`error`), **`esito`** (`ammissibile`/`non_ammissibile`/`da_verificare`), **`punteggio`** (0-100) e **`tipo_punteggio`** (`stima`/`euristico`) come colonne per le liste, **`report` jsonb** completo (requisiti con verdetti e citazioni, criteri, verifiche strutturate, punti di forza/debolezza, dati mancanti), `model`, `prompt_version`, `extraction_cached`, token e `cost_cents` reali. Indice unico parziale: al massimo UNA analisi `pending` per coppia azienda×bando.

### Bandi salvati e calendario (migration 0008)

**`saved_bandi`** — preferiti per utente: riferimento al catalogo del secondario **senza FK cross-database** (`bando_id` intero) con snapshot denormalizzati (`bando_slug`, `bando_titolo`, `data_scadenza`, `stato_bando`) che sopravvivono alla scomparsa del bando e fanno da fallback di visualizzazione. `unique (user_id, bando_id)`; cascade da profiles; righe immutabili (niente updated_at).

**`calendar_events`** — eventi del calendario personale: `titolo` (1-200), **`data` `date` + `ora_inizio`/`ora_fine` `time` senza fuso** (calendario italiano wall-clock, come `data_scadenza` del catalogo), `tutto_il_giorno`, `note` (≤2000), `tipo` (`personale`/`bando`). CHECK di coerenza: `tipo='bando'` ⇔ `bando_id`+`bando_slug` presenti; tutto il giorno ⇒ niente orari, con orari ⇒ inizio obbligatorio e fine successiva. Indice unico parziale `(user_id, bando_id) where tipo='bando'`: **una sola scadenza in calendario per bando per utente** (idempotenza). Nessuna FK verso saved_bandi: preferiti ed eventi sono indipendenti.

### Funzioni e trigger

- `handle_new_user()` — trigger `AFTER INSERT ON auth.users`: crea profilo + abbonamento iniziale (fallback `gratuito`); per gli utenti invitati in famiglia (metadata `family_invite='true'`) crea **solo il profilo**, senza abbonamento. **Difensiva: non solleva mai eccezioni.**
- `fn_switch_plan(p_user_id, p_plan_id) → jsonb` — cambio piano atomico e family-aware: blocca i figli attivi (`child_plan_locked`); al downgrade revoca prima gli inviti pending (più recenti prima) e poi retrocede i figli attivi più recenti finché la famiglia rientra nel limite; i retrocessi ricevono un abbonamento Gratuito fresco. Ritorna `{demoted, revoked_pending}`.
- `fn_create_family_member` / `fn_accept_invitation` / `fn_decline_invitation` / `fn_remove_family_member` / `fn_reactivate_family_member` — ciclo di vita dei membri, tutte sotto lock del padre (`FOR UPDATE`, serializza le race sul limite) e con errori a codice macchina (`detail`) per la mappatura API. L'accettazione e la riattivazione cancellano l'abbonamento proprio del membro (da lì eredita).
- `fn_block_parent_delete()` — trigger `BEFORE DELETE` su profiles: un padre con membri collegati non è cancellabile.
- `promote_to_admin(p_email)` — da eseguire nel SQL Editor per promuovere il primo admin.
- `set_updated_at()` — mantiene `updated_at`.

### Sicurezza

- **RLS deny-all**: RLS abilitata su tutte le tabelle (incluse `family_members`, `company_profiles`, `audit_log`) senza alcuna policy → `anon` e `authenticated` non leggono/scrivono nulla; il backend usa `service_role` che bypassa la RLS. In più, `REVOKE ALL` esplicito sui ruoli client.
- **RPC bloccate**: Supabase concede di default `EXECUTE` ad `anon`/`authenticated` su ogni funzione di `public`, e PostgREST le espone come `POST /rest/v1/rpc/<nome>`. Le migration revocano esplicitamente `EXECUTE` su `fn_switch_plan` e `promote_to_admin` dai ruoli client: senza questa revoca chiunque potrebbe auto-promuoversi admin o cambiare piano ad altri.

## DB secondario (Supabase, SOLA LETTURA)

Catalogo dei bandi, alimentato esternamente. **BandoFit non vi scrive mai**: il backend usa la chiave `anon`, che per costruzione (RLS del secondario) consente solo SELECT. Il dump di riferimento è in `database_secondario_dump/` (solo locale, escluso da git).

Tabelle usate:
- **`bando`** (~4.000 righe): titolo/titolo_breve/descrizione_breve, `slug` (unico), date (pubblicazione/apertura/scadenza), `importo_totale_eur`/`importo_max_per_progetto_eur`, `ente_erogatore`, `stato_bando` (`aperto`/`chiuso`/`in apertura prossimamente`), `livello` (`flash_bando`/`guida_bando`), `contenuto` (JSONB a sezioni), `allegati` (JSONB), FK verso `tipologie_bando`, `modalita_erogazione`, `programmi`. La RLS anon espone solo `stato_processing='completed' AND slug IS NOT NULL` (~1.260 righe).
- **Junction M:N** (tutte `UNIQUE(bando_id, dim_id)`, indicizzate): `bando_regioni`, `bando_settori`, `bando_beneficiari`, `bando_codici_ateco`.
- **Lookup**: `regioni` (20), `settori` (90), `beneficiari` (31), `codici_ateco` (89, divisioni a 2 cifre), `tipologie_bando` (5), `modalita_erogazione` (4), `programmi` (56).

Vincoli operativi: statement timeout di **3 secondi** per il ruolo `anon` → il backend limita `page_size` a 50 e filtra su colonne indicizzate; ricerca full-text con indici GIN italiani su titolo/descrizione.
