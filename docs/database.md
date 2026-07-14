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
| `tipo_prezzo` | text | `importo`/`gratis`/`su_richiesta` (default `importo`, migration 0010): come mostrare il prezzo; con `su_richiesta` il piano **non è attivabile self-serve** (guard del backend su cambio piano e registrazione, resta assegnabile dall'admin) |
| `etichetta_prezzo` | text | mostrata al posto del prezzo SOLO con `su_richiesta`; NULL/vuota ⇒ la UI mostra «Su richiesta» (nessun check cross-campo) |
| `ai_check` | integer | numero di AI-check inclusi/anno |
| `alert_attivo` | boolean | se true, `alert_giorni_preavviso` è obbligatorio (check `plans_alert_coherence`) |
| `alert_giorni_preavviso` | integer | > 0 o NULL (preavviso scadenze — funzione futura) |
| `alert_ritardo_giorni` | integer | ≥ 0 o NULL (migration 0021): ritardo dell'alert nuovi-bandi dalla pubblicazione; NULL = feature esclusa dal piano |
| `num_account_aziendali` | integer | ≥ 1 |
| `ordering`, `is_active` | int, bool | ordinamento in UI; i piani non si eliminano, si disattivano |

**`profiles`** — 1:1 con `auth.users` (PK = `auth.users.id`, on delete cascade): `email` (denormalizzata per la ricerca admin), `nome`, `cognome`, `azienda`, `telefono` (in **E.164** per i valori scritti dalla 0022 in poi; i precedenti restano testo libero finché non modificati), `job_position_id`/`job_position_altro` (posizione aziendale, migration 0022 — vedi sotto), `role` (enum `admin`/`cliente`/`progettista` — il terzo valore arriva dalla migration 0014, in un file a sé perché un valore enum non è usabile nella stessa transazione che lo crea), `is_active` (bool, default true).

**`user_subscriptions`** — storico abbonamenti: `user_id` → profiles, `plan_id` → plans, `status` (enum `active`/`cancelled`/`expired`), `data_inizio`, `data_scadenza` (default +1 anno). Indice unico parziale `user_subscriptions_one_active` ⇒ **un solo abbonamento `active` per utente**; il cambio piano cancella l'attivo e ne crea uno nuovo (lo storico resta).

### Famiglie di account (migration 0003)

**`family_members`** — membership e inviti in un'unica tabella (una riga per "permanenza"): `parent_id`/`member_id` → profiles (FK con nomi espliciti per gli embed PostgREST), `denominazione`, `invited_email`, `invite_kind` (`new_user`/`existing_user`), `status` (`pending` → `active` → `demoted`; terminali: `removed`, `declined`), `invited_at`, `joined_at` (chiave d'ordine per le retrocessioni), `demoted_at`, `removed_at`. **Indice unico parziale**: un utente può avere una sola membership corrente (pending/active/demoted). Regole: il limite account del piano **include il padre**; i figli attivi **non hanno un abbonamento proprio** (ereditano dal padre); le quote sono condivise a livello famiglia.

**`company_profiles`** — dati aziendali, uno per padre: ragione sociale, P.IVA (check 11 cifre), forma giuridica, ATECO/settore/regione (id delle lookup del DB secondario + copie testo denormalizzate, nessuna FK cross-DB), **`beneficiari` jsonb** `[{id, nome}]` (categorie **dichiarate** dall'utente dalla stessa lookup del catalogo, multi-valore, check `jsonb_typeof = 'array'`: non si deducono dalla visura — vedi 0011), sede legale (CAP check 5 cifre), classe dimensionale, dipendenti, fascia fatturato, PEC/telefono/sito.

**`audit_log`** — operazioni sensibili (inviti, retrocessioni, rimozioni, cambi piano, modifiche dati aziendali), senza FK: le righe sopravvivono alla cancellazione degli utenti. Consultabile dal SQL Editor.

**`auth_tokens`** (migration 0004) — token monouso per i link email di dominio (conferma indirizzo, recovery, inviti): `user_id` → profiles (cascade), `purpose`, `token_hash` (SHA-256, unico — il token in chiaro non viene mai salvato), `expires_at`, `used_at`. Consumo atomico via UPDATE condizionato.

### Dati certificati, preferenze e consumi (migration 0005)

**`profiles`** (colonne aggiunte) — `codice_fiscale` (16 caratteri maiuscoli, check di formato) e `cf_verified_at` (verifica all'Anagrafe Tributaria via openapi.it). Il trigger `fn_reset_cf_verification` azzera la verifica se il CF cambia, tranne quando lo statement imposta esplicitamente anche `cf_verified_at` (flusso di verifica).

**`company_data`** — la "visura" certificata da openapi.it (endpoint IT-full), **una riga per azienda** (`company_profile_id` unico → company_profiles, cascade): `raw` jsonb è il payload completo (fonte di verità), `derived` jsonb i valori calcolati all'import (divisione ATECO, ATECO secondari, match regione, `regioni_ids` di tutte le sedi, classe dimensionale, fasce — **non** i beneficiari, che sono dichiarati su `company_profiles`), più estratti "caldi" (`denominazione`, `stato_impresa`), `piva_fetched` (check 11 cifre), `sandbox`, `fetch_count`, `fetched_at`. Solo l'ultima versione: lo storico dei recuperi vive in `audit_log` + `api_usage_events`.

**`company_people`** — persone estratte dalla visura a ogni import (replace-all): `kind` (`manager`/`shareholder`/`auditor`), anagrafica, `ruoli` jsonb, `is_legale_rappresentante`, `quota_percentuale`, frammento `raw` originale. Cascade da company_profiles.

**`user_preferences`** — preferenze di filtro/notifica **per utente** (anche i figli hanno le proprie): una riga per (utente, faccetta, id lookup del secondario) con `facet` vincolata alle 7 faccette dei bandi, `label` denormalizzata (nessuna FK cross-DB), unique su (user_id, facet, ref_id). Cascade da profiles.

**`api_usage_events`** — registro dei consumi API a pagamento, **senza FK** (come audit_log: sopravvive alle cancellazioni): provider/service/outcome (`success`/`error`/`timeout_unknown`), `cost_cents`, `response_status`, `request_meta` (mai dati personali in chiaro). Per l'AI-check annota ogni esito con `provider='anthropic'`, `service='ai_check'` e i token consumati nel `request_meta` — ma è un registro di SPESA best-effort: il **conteggio quota** usa le righe di `ai_checks` (`pending`+`ready` nella finestra dell'abbonamento), che cambiano stato atomicamente e sono transazionali col risultato.

**`company_import_locks`** + `fn_acquire_import_lock(parent_id, ttl)` / `fn_release_import_lock(parent_id)` — lock anti doppia-spesa per l'import: la chiamata HTTP esterna avviene tra statement PostgREST, quindi il claim è un INSERT atomico con "furto" solo se il lock esistente è scaduto (TTL clampato a 600s). Riusato anche dalla verifica CF.

**`company_import_drafts`** (migration 0013) — payload IT-full **già pagato**, in attesa che l'utente confermi l'anteprima: una riga per titolare (`parent_id` PK → profiles, cascade), `partita_iva` (check 11 cifre), `raw` jsonb, `sandbox`, `fetched_at`, `expires_at` (indicizzato). Esiste perché la chiamata costa: rifarla alla conferma raddoppierebbe la spesa, e una cache in memoria non sopravvive al multi-worker. **Non è un dato aziendale** — nessuna pagina lo legge, non entra nell'AI-check né nel punteggio di compatibilità — quindi il vincolo «nulla è salvato finché non confermi» resta rispettato. Le letture filtrano sempre su `expires_at > now()`: la scadenza è un filtro, non una cancellazione (non c'è job di pulizia; un nuovo recupero sostituisce la riga con un upsert). Chi annulla non lo perde: entro il TTL l'anteprima si ricostruisce gratis. RLS deny-all + revoke, come i lock.

### Documenti ufficiali (migration 0006, rimossa dalla 0012)

_(La tabella `company_documents` e il bucket `company-documents` sono stati **rimossi** con la migration 0012: la visura camerale PDF non è più una funzionalità. Il dossier, l'anagrafica, le sedi e le persone vengono dal payload IT-full in `company_data.raw`, mai dal PDF.)_

### AI-check (migration 0007)

**`bando_requirements`** — cache delle estrazioni LLM per bando (indipendente dall'azienda, una riga per `bando_id` — id intero del DB secondario, **senza FK cross-database**): `content_hash` (hash_bando del catalogo, fallback sha256 dell'input serializzato), `prompt_version`, `model`, **`extraction` jsonb** (requisiti obbligatori + criteri di valutazione + griglia, con citazioni), token consumati. Aggiornata in place quando cambia hash o versione dei prompt: l'estrazione si paga una volta sola per bando, tutte le aziende la riusano.

**`ai_checks`** — report di compatibilità azienda↔bando (**storico versionato**: ogni generazione è una nuova riga, la più recente è in evidenza): `company_profile_id` (cascade), `user_id` (richiedente, senza FK), `family_parent_id` (chiave di visibilità e quota), `bando_id`/`bando_slug`/`bando_titolo` denormalizzati (lo storico sopravvive al bando), `status` (`pending`→`ready`/`error`), **`esito`** (`ammissibile`/`non_ammissibile`/`da_verificare`), **`punteggio`** (0-100) e **`tipo_punteggio`** (`stima`/`euristico`) come colonne per le liste, **`report` jsonb** completo (requisiti con verdetti e citazioni, criteri, verifiche strutturate, punti di forza/debolezza, dati mancanti), `model`, `prompt_version`, `extraction_cached`, token e `cost_cents` reali. Indice unico parziale: al massimo UNA analisi `pending` per coppia azienda×bando.

### Bandi salvati e calendario (migration 0008)

**`saved_bandi`** — preferiti per utente: riferimento al catalogo del secondario **senza FK cross-database** (`bando_id` intero) con snapshot denormalizzati (`bando_slug`, `bando_titolo`, `data_scadenza`, `stato_bando`) che sopravvivono alla scomparsa del bando e fanno da fallback di visualizzazione. `unique (user_id, bando_id)`; cascade da profiles; righe immutabili (niente updated_at).

**`calendar_events`** — eventi del calendario personale: `titolo` (1-200), **`data` `date` + `ora_inizio`/`ora_fine` `time` senza fuso** (calendario italiano wall-clock, come `data_scadenza` del catalogo), `tutto_il_giorno`, `note` (≤2000), `tipo` (`personale`/`bando`). CHECK di coerenza: `tipo='bando'` ⇔ `bando_id`+`bando_slug` presenti; tutto il giorno ⇒ niente orari, con orari ⇒ inizio obbligatorio e fine successiva. Indice unico parziale `(user_id, bando_id) where tipo='bando'`: **una sola scadenza in calendario per bando per utente** (idempotenza). Nessuna FK verso saved_bandi: preferiti ed eventi sono indipendenti.

### Add-on (migration 0009)

**`addons`** — catalogo add-on acquistabili gestito dagli admin, gemello di `subscription_plans`: `nome`, **`slug` unico** (identificativo STABILE per agganciare le funzionalità future), `descrizione`, `prezzo numeric(10,2) ≥ 0` (euro, stessa convenzione dei piani), `tipo_prezzo`/`etichetta_prezzo` (migration 0010, stessa semantica dei piani), `ordering`, `is_active`. Come i piani, **non si eliminano: si disattivano**. Il catalogo parte vuoto, con un'eccezione: la migration 0017 garantisce con un seed idempotente l'addon **`consulto-esperto`** (aggancio del flusso consulenze; slug in `consulting_addon_slug` del backend). Il flusso di acquisto non è ancora implementato: attivare il consulto crea direttamente la richiesta (l'innesto del checkout è documentato in `consulting_service.create_request`).

### Modalità di visualizzazione prezzo (migration 0010)

Su `subscription_plans` e `addons`: `tipo_prezzo text not null default 'importo'` con check sui tre valori (`importo`, `gratis`, `su_richiesta` — valori di dominio in italiano come `tipo_punteggio`/`esito`) ed `etichetta_prezzo text` libera. La migration fa un **backfill una tantum**: tutto ciò che aveva prezzo 0 diventa `gratis` (nel seed è il piano Gratuito); i record creati a 0 € in seguito restano `importo` finché l'admin non li cambia. Il valore numerico del prezzo resta sempre salvato, anche quando non è mostrato.

### Progettisti (migration 0014–0015)

**`progettisti`** — attributi del ruolo progettista, 1:1 con profiles (PK `user_id`, cascade): **`codice`** leggibile `PRG-00001` (sequence, unico, **immutabile** — un trigger `BEFORE UPDATE` rifiuta il cambio con detail `codice_immutabile`), `bio`, `specializzazioni` (descrittiva, predisposta per un futuro routing per specializzazione, oggi non filtrata). La riga **sopravvive alla demozione**: una ri-promozione riusa lo stesso codice, così i riferimenti storici restano coerenti.

`fn_promote_progettista(p_user_id, p_actor_id) → codice` — promozione atomica (lock `FOR UPDATE` sul profilo): imposta `role='progettista'`, assegna il codice solo se assente e registra `admin.progettista_promoted` in audit_log. Chiamata da `admin_update_user`; la demozione è un update di ruolo normale (anch'esso in audit_log come `admin.role_changed`).

### Notifiche in-app (migration 0016)

**`notifications`** — il canale AFFIDABILE degli eventi applicativi (le email sono best-effort): `user_id` → profiles (cascade), `tipo` (codice macchina, es. `consulenza.nuova_richiesta`), `titolo` (1-200), `corpo` (≤1000), `url` (deep-link in-app), **`dedup_key` NOT NULL** con **constraint pieno** `unique (user_id, dedup_key)` — è l'arbiter dell'upsert ignore-duplicates di PostgREST (un indice parziale non lo sarebbe): il retry di un fan-out inserisce solo i destinatari mancanti. `read_at` per il badge (indice parziale sulle non lette). **Minimizzazione**: titolo/corpo non contengono dati personali di terzi; i dettagli si leggono seguendo `url`, dove l'endpoint applica l'autorizzazione live — una notifica recapitata a un progettista non trattiene dati di un cliente che poi si cancella.

### Consulenze (migration 0017)

Flusso: richiesta (`nuova`) → proposte dei progettisti → accettazione = **assegnazione definitiva 1:1** (+ prenotazione slot opzionale). Stati con `text + check` (pattern ai_checks). Chi riferisce persone segue il pattern di `ai_checks`: `cliente_id`, `family_parent_id`, i `progettista_id` di proposte/booking **senza FK** (lo storico sopravvive alle persone); `company_profile_id` con cascade (lo storico muore con l'azienda: right to erasure del titolare).

**`availability_slots`** — disponibilità del progettista in **UTC** (`timestamptz` — divergenza deliberata dal wall-clock di `calendar_events`: qui due utenti diversi guardano lo stesso istante, la UI mostra il fuso del browser). Anti-sovrapposizione a livello DB: `exclude using gist (progettista_id with =, tstzrange(inizio, fine) with &&)` (estensione `btree_gist`; range `[)`: slot adiacenti validi). Nessuna colonna di stato: «libero» = nessun booking confermato. Dalla **0018**: `serie_id uuid` nullable (indice parziale `where serie_id is not null`) — raggruppa gli slot nati da una ricorrenza, nessuna tabella madre; modificare una singola occorrenza non la stacca dalla serie.

**`consultation_requests`** — la richiesta di consulto: snapshot dell'AI-check (`ai_check_id` set null + `esito`/`punteggio`), bando denormalizzato, `addon_id`/`addon_slug`/`addon_prezzo` (innesto del futuro pagamento), `stato` (`nuova`/`assegnata`/`annullata`, con check di coerenza: assegnata ⇔ progettista+proposta+data valorizzati). Indice unico parziale `(family_parent_id, bando_id) where stato='nuova'`: **una sola richiesta aperta per bando per azienda** — una richiesta già assegnata non blocca un futuro secondo consulto sullo stesso bando.

**`consultation_proposals`** — proposte dei progettisti: `stato` (`inviata`/`accettata`/`rifiutata`/`superata`/`ritirata`). Indice unico parziale `(request_id, progettista_id) where stato='inviata'`: una sola proposta **aperta** per progettista; dopo un ritiro o un rifiuto se ne può inviare una nuova.

**`consultation_bookings`** — appuntamenti: `slot_id` → availability_slots **on delete set null** + snapshot `inizio`/`fine` (l'appuntamento sopravvive allo slot e alla cancellazione dell'account del progettista). Indici unici parziali `(slot_id) where stato='confermata'` (anti doppia prenotazione a livello DB) e `(request_id) where stato='confermata'` (un appuntamento attivo per consulenza). L'annullamento libera lo slot da solo. Dalla **0020**: `videocall_token uuid` (default `gen_random_uuid()`, unique) — la stanza Jitsi dell'appuntamento.

RPC (SECURITY DEFINER, errori a detail-code, ordine di lock uniforme richiesta→slot):
- `fn_accept_proposal(request, proposal, cliente, slot?)` — `FOR UPDATE` su richiesta **e proposta** (un ritiro concorrente non può essere sovrascritto: l'accettazione si mette in coda dietro il suo lock); guardie su titolare, stato, e autore ancora attivo con ruolo progettista **o admin** (`progettista_not_available`; parità admin, 0019); chiude le altre proposte come `superate`; con `slot` prenota nella stessa transazione (**all-or-nothing**: slot preso ⇒ salta anche l'assegnazione, detail `slot_taken`). Audit `consulenza.assigned` (+ `consulenza.booked`).
- `fn_book_slot(request, slot, actor)` — serializza sullo slot (`FOR UPDATE`); l'indice parziale resta il backstop e il suo 23505 viene ricatturato come `slot_taken` (senza, il client vedrebbe 502 invece di 409).
- `fn_update_slot` / `fn_delete_slot` — anche il CRUD dello slot serializza sulla riga: un update condizionale non basterebbe (in READ COMMITTED la subquery userebbe lo snapshot preso prima del lock e non vedrebbe una prenotazione appena committata). Slot prenotato ⇒ `slot_booked`.

### Serie di slot ricorrenti (migration 0018)

Le **occorrenze** di una ricorrenza («ogni giorno / giorno feriale / settimana / mese») le materializza il **browser**, non il DB: solo il client conosce il fuso dell'utente, e l'orario a muro deve restare stabile attraverso i cambi di ora legale. Il backend valida ogni occorrenza come uno slot singolo e passa dalle RPC:
- `fn_create_slot_serie(progettista, occorrenze jsonb) → jsonb` — transazione unica con blocco `begin/exception` per riga: le occorrenze che violano l'exclusion constraint vengono **saltate** (anche quelle sovrapposte tra loro nel payload), non abortiscono la serie — un bulk insert PostgREST sarebbe all-or-nothing. Guardie: `serie_vuota`, `serie_troppo_lunga` (>370, allineato a `MAX_OCCORRENZE_SERIE` del backend), `serie_tutta_sovrapposta` se nessuna entra (409). Ritorna `{serie_id, creati, saltati}`.
- `fn_delete_slot_serie(serie, progettista) → jsonb` — `FOR UPDATE` sulle righe della serie prima del delete (stessa ragione di `fn_update_slot`: il `not exists` sul booking va rivalutato dopo il lock), poi cancella i soli slot **senza** prenotazione confermata. Ownership = coppia `(serie_id, progettista_id)`, altrimenti `serie_not_found`. Ritorna `{eliminati, mantenuti}`.

### Parità admin (migration 0019)

Decisione di prodotto: gli **admin hanno le stesse funzioni dei progettisti** (pool, proposte, slot, appuntamenti), senza cambiare ruolo. A DB:
- `fn_ensure_progettista_codice(user) → text` — codice PRG **pigro**: il backend la chiama alla prima proposta di un admin. `FOR UPDATE` sul profilo (serializza prime-proposte concorrenti; `user_not_found` se assente), poi «insert solo se assente» in `progettisti` con la stessa sequence di `fn_promote_progettista` — le due funzioni condividono la riga: una promozione riusa il codice pigro e viceversa. Nessun cambio di ruolo, nessun audit di promozione.
- `fn_accept_proposal` **ridefinita** (corpo integrale della 0017 con una riga cambiata): la guardia sull'autore della proposta accetta `role in ('progettista','admin')` attivo — con la 0017 una proposta di un admin non sarebbe mai stata accettabile. CREATE OR REPLACE conserva le revoche.

### Videochiamate (migration 0020)

Ogni appuntamento nasce con la sua stanza Jitsi: colonna `videocall_token uuid not null default gen_random_uuid()` **unique** su `consultation_bookings`. L'istanza Jitsi self-hosted è **aperta** (niente JWT): la sicurezza sta nel nome-stanza non indovinabile, quindi il token è un **segreto** — generato una sola volta all'INSERT (gli update non lo toccano mai: idempotenza per costruzione), mai esposto nelle notifiche conservate. A DB vive solo il token; l'**URL** (`{JITSI_BASE_URL}/bandofit-{token}`) lo deriva il backend, così un cambio di istanza non richiede backfill. Ri-prenotazione = riga nuova = token nuovo; l'annullo lo nasconde da solo (le letture filtrano `stato='confermata'`). Retroattività: l'`ADD COLUMN` con default **volatile** riscrive la tabella valutando il default per riga → anche le prenotazioni pre-esistenti hanno ricevuto un token proprio e distinto.

### Alert nuovi bandi (migration 0021)

**Nessuna pre-schedulazione**: il job giornaliero (scheduler in-process, claim per insert su `bando_alert_runs` — PK `giorno`, un 23505 = già eseguita) ricalcola l'idoneità a OGNI run dallo stato corrente; gli edge case su cambio piano, ritiro del bando e opt-out tardivo si risolvono da soli. Oggetti:
- `subscription_plans.alert_ritardo_giorni` — giorni di ritardo dalla pubblicazione (0 = stesso giorno; NULL = feature esclusa dal piano). Semantica DIVERSA da `alert_giorni_preavviso` (promemoria scadenze, funzione futura, intatto).
- **`bando_alert_settings`** — opt-in/out per utente (riga pigra: assenza = abilitati) + `unsubscribe_token` unico: il link RFC 8058 nelle email e il toggle in Preferenze scrivono la STESSA riga.
- **`bando_alert_sends`** — ledger idempotenza, `unique (user_id, bando_id)` come arbiter del claim-by-insert. Stati: `in_invio → inviata | fallita` (ritentabile fino a 3 tentativi) ; `incerta` = run interrotta tra invio e conferma, MAI ritentata (at-most-once). Nessun contenuto personale (minimizzazione).
- **`bando_alert_runs`** — una riga per esecuzione con i contatori (candidati/destinatari/inviate/fallite): l'osservabilità del job.
- **`email_suppressions`** — indirizzi da non contattare mai (hard bounce/reclami/manuale), unicità case-insensitive su `lower(email)`.
- RPC **`fn_email_verificate(uuid[])`** SECURITY DEFINER — ponte su `auth.users.email_confirmed_at` (PostgREST non espone lo schema auth); EXECUTE revocato ai client.

Base giuridica (GDPR): esecuzione del contratto — gli avvisi sono una feature del piano — con opt-out immediato (toggle in-app + one-click nelle email, stessa fonte di verità); il riferimento temporale è `coalesce(data_pubblicazione, created_at)` in Europe/Rome e il gate `ALERT_DATA_ATTIVAZIONE` impedisce il backfill al primo avvio.

### Posizioni aziendali e telefono (migration 0022)

- **`job_positions`** — lookup delle posizioni selezionabili alla registrazione e nel profilo, gemella di `addons` (id identity, `nome`, `slug` unico = identificativo STABILE, `ordering`, `is_active`): le voci **non si eliminano, si disattivano**. Seed di 29 posizioni («Altro» sempre in coda, slug `altro`); si amministra via SQL, non c'è CRUD admin.
- **`profiles.job_position_id`** (FK, nullable, indicizzata) + **`profiles.job_position_altro`** (testo libero, valorizzato solo con la posizione «Altro» — il backend lo azzera negli altri casi). **Nessun backfill**: gli utenti pre-0022 e gli invitati in azienda restano NULL e completano dal Profilo; l'obbligatorietà vive SOLO nella validazione del form di registrazione (client + server), mai come vincolo di schema.
- **Percorso dati alla registrazione**: form → `RegisterIn` → `user_metadata` (`telefono` già in E.164, `job_position_slug`, `job_position_altro`) → `handle_new_user` ridefinita, che risolve lo slug a id (`where slug = … and is_active`) con fallback **NULL** per slug ignoto/disattivato: il signup non si blocca mai. Il self-heal `ensure_profile` replica la stessa risoluzione.

### Funzioni e trigger

- `handle_new_user()` — trigger `AFTER INSERT ON auth.users`: crea profilo + abbonamento iniziale (fallback `gratuito`); dalla 0022 scrive anche `telefono`, `job_position_id` (slug del metadata risolto a id, NULL se ignoto/disattivato) e `job_position_altro` (solo con posizione «Altro»); per gli utenti invitati in famiglia (metadata `family_invite='true'`) crea **solo il profilo**, senza abbonamento. **Difensiva: non solleva mai eccezioni.**
- `fn_switch_plan(p_user_id, p_plan_id) → jsonb` — cambio piano atomico e family-aware: blocca i figli attivi (`child_plan_locked`); al downgrade revoca prima gli inviti pending (più recenti prima) e poi retrocede i figli attivi più recenti finché la famiglia rientra nel limite; i retrocessi ricevono un abbonamento Gratuito fresco. Ritorna `{demoted, revoked_pending}`.
- `fn_create_family_member` / `fn_accept_invitation` / `fn_decline_invitation` / `fn_remove_family_member` / `fn_reactivate_family_member` — ciclo di vita dei membri, tutte sotto lock del padre (`FOR UPDATE`, serializza le race sul limite) e con errori a codice macchina (`detail`) per la mappatura API. L'accettazione e la riattivazione cancellano l'abbonamento proprio del membro (da lì eredita).
- `fn_block_parent_delete()` — trigger `BEFORE DELETE` su profiles: un padre con membri collegati non è cancellabile.
- `promote_to_admin(p_email)` — da eseguire nel SQL Editor per promuovere il primo admin.
- `set_updated_at()` — mantiene `updated_at`.

### Sicurezza

- **RLS deny-all**: RLS abilitata su tutte le tabelle (incluse `family_members`, `company_profiles`, `audit_log`) senza alcuna policy → `anon` e `authenticated` non leggono/scrivono nulla; il backend usa `service_role` che bypassa la RLS. In più, `REVOKE ALL` esplicito sui ruoli client.
- **RPC bloccate**: Supabase concede di default `EXECUTE` ad `anon`/`authenticated` su ogni funzione di `public`, e PostgREST le espone come `POST /rest/v1/rpc/<nome>`. Le migration revocano esplicitamente `EXECUTE` su `fn_switch_plan`, `promote_to_admin`, `fn_promote_progettista`, `fn_ensure_progettista_codice`, `fn_email_verificate` e sulle RPC delle consulenze (`fn_accept_proposal`, `fn_book_slot`, `fn_update_slot`, `fn_delete_slot`, `fn_create_slot_serie`, `fn_delete_slot_serie`) dai ruoli client: senza queste revoche chiunque potrebbe auto-promuoversi o assegnarsi consulenze.
- **Audit degli accessi full**: ogni lettura dei dati completi di un'azienda da parte del progettista assegnato scrive `consulenza.dossier_accessed` in `audit_log` (oltre alle transizioni: `consulenza.created` / `proposal_sent` / `assigned` / `booked` / `cancelled` / `booking_cancelled`).

## DB secondario (Supabase, SOLA LETTURA)

Catalogo dei bandi, alimentato esternamente. **BandoFit non vi scrive mai**: il backend usa la chiave `anon`, che per costruzione (RLS del secondario) consente solo SELECT. Il dump di riferimento è in `database_secondario_dump/` (solo locale, escluso da git).

Tabelle usate:
- **`bando`** (~4.000 righe): titolo/titolo_breve/descrizione_breve, `slug` (unico), date (pubblicazione/apertura/scadenza), `importo_totale_eur`/`importo_max_per_progetto_eur`, `ente_erogatore`, `stato_bando` (`aperto`/`chiuso`/`in apertura prossimamente`), `livello` (`flash_bando`/`guida_bando`), `contenuto` (JSONB a sezioni), `allegati` (JSONB), FK verso `tipologie_bando`, `modalita_erogazione`, `programmi`. La RLS anon espone solo `stato_processing='completed' AND slug IS NOT NULL` (~1.260 righe).
- **Junction M:N** (tutte `UNIQUE(bando_id, dim_id)`, indicizzate): `bando_regioni`, `bando_settori`, `bando_beneficiari`, `bando_codici_ateco`.
- **Lookup**: `regioni` (20), `settori` (90), `beneficiari` (31), `codici_ateco` (89, divisioni a 2 cifre), `tipologie_bando` (5), `modalita_erogazione` (4), `programmi` (56).

Vincoli operativi: statement timeout di **3 secondi** per il ruolo `anon` → il backend limita `page_size` a 50 e filtra su colonne indicizzate; ricerca full-text con indici GIN italiani su titolo/descrizione.
