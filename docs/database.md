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

**`api_usage_events`** — registro dei consumi API a pagamento, **senza FK** (come audit_log: sopravvive alle cancellazioni): provider/service/outcome (`success`/`error`/`timeout_unknown`), `cost_cents`, `response_status`, `request_meta` (mai dati personali in chiaro). Servirà anche al conteggio delle quote AI-check (`service='ai_check'`).

**`company_import_locks`** + `fn_acquire_import_lock(parent_id, ttl)` / `fn_release_import_lock(parent_id)` — lock anti doppia-spesa per l'import: la chiamata HTTP esterna avviene tra statement PostgREST, quindi il claim è un INSERT atomico con "furto" solo se il lock esistente è scaduto (TTL clampato a 600s).

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
