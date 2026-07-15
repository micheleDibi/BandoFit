# API Backend

Base URL: `http://localhost:8000/api/v1` (sviluppo). Documentazione interattiva: `http://localhost:8000/docs` (Swagger UI).

**Autenticazione**: header `Authorization: Bearer <access_token>` (JWT emesso da Supabase Auth del progetto primario). Il backend verifica firma (ES256/RS256 via JWKS, fallback HS256 legacy), `aud` e `iss`, poi carica il profilo: un account con `is_active=false` riceve `403`. Gli endpoint `/admin/*` richiedono `role='admin'`.

**Azienda attiva** (header opzionale `X-Active-Company: <uuid>`): seleziona su quale azienda operano gli endpoint sui dati aziendali (`GET /me/company`, `GET /me/company/facets`, `GET /me/company/dossier`, `GET /me/ai-checks` e `GET /me/ai-checks/{id}`, badge di compatibilità su `GET /bandi`). È ri-autorizzato a ogni richiesta: l'azienda deve appartenere all'utente ed essere viva (non cancellata né archiviata), altrimenti `404 not_found`; un valore non-UUID è anch'esso `404`. **Senza l'header** si usa l'azienda viva più vecchia dell'utente — che per gli abbonamenti non-Advisor è l'unica, quindi il comportamento è identico a quando l'header non esisteva. La quota AI-check resta un **pool unico** condiviso (non cambia con l'azienda attiva): cambia solo lo storico mostrato.

**Formato errori** (uniforme):
```json
{ "error": { "code": "not_found", "message": "Bando non trovato" } }
```
Codici: `unauthorized` (401), `forbidden` (403), `not_found` (404), `bad_request` (400), `conflict` (409), `validation_error` (422), `auth_unavailable` (503, verifica token temporaneamente impossibile — es. JWKS irraggiungibile: è un errore transitorio, **non** una sessione scaduta), `search_timeout` (504), `upstream_error` (502), `upstream_timeout` (504).

Nota: se un utente autenticato risulta privo di profilo (provisioning fallito a monte), il backend lo crea al volo alla prima richiesta (con abbonamento Gratuito), evitando che l'account resti bloccato.

**Paginazione** (risposta uniforme per gli elenchi):
```json
{ "items": [...], "total": 137, "page": 1, "page_size": 20, "total_pages": 7 }
```

## Endpoint pubblici

### `GET /health`
Stato del servizio. → `{"status": "ok"}`

> **Link di dominio**: tutti i link nelle email (conferma, recovery, inviti) sono token **propri** di BandoFit (256 bit, salvati solo come SHA-256 in `auth_tokens`, monouso, con scadenza) e puntano al dominio dell'app. GoTrue non genera MAI link né invia email: Supabase è solo il deposito di utenti e dati (Admin API `create_user`/`update_user_by_id`).

### `POST /auth/register` (201)
Registrazione. Body: `email`, `password` (≥8), `nome`, `cognome`, `azienda?`, `telefono` (obbligatorio; normalizzato e validato in **E.164** dal validator Pydantic — «347 1234567» → `+393471234567`, prefisso `+39` di default, lo zero dei fissi si conserva), `job_position_slug` (obbligatorio, dalla lookup `GET /job-positions`), `job_position_altro?` (testo libero, tenuto solo se la posizione è «Altro»), `plan_slug`. Crea l'utente via Admin API (non confermato) e invia l'email di conferma col link `/conferma-email?token=...`. → `{"confirmation_required": true}`. Errori: `409` email già registrata o cooldown 60s, `400` password non valida (rifiuto di GoTrue, a valle del cooldown), `400` se `plan_slug` punta a un piano `su_richiesta`, `400` se `job_position_slug` è ignoto o disattivato, `422` telefono non valido — questi ultimi tre avvengono PRIMA del cooldown e non lo consumano. Telefono e posizione viaggiano nello `user_metadata` e li scrive il trigger `handle_new_user` (0022).

### `POST /auth/confirm`
Body: `{"token": "..."}` (dal link email, monouso, TTL 48h). Conferma l'indirizzo e sblocca il login. → `{"email": "..."}` (per il prefill di `/login?email=`). `404` se non valido/scaduto/già usato.

### `POST /auth/recover` (202)
Richiesta reimpostazione password. Body: `{"email": "..."}`. Risposta **sempre neutra** `{"ok": true}` (anti-enumerazione); se l'account esiste parte l'email con `/reimposta-password?token=...` (TTL 1h). `409` cooldown 60s.

### `POST /auth/reset`
Body: `{"token": "...", "password": "..."}`. Consuma il token di recovery e imposta la nuova password via Admin API. → `{"email"}` (il frontend fa auto-login). `404` token non valido.

### `POST /auth/resend-confirmation` (202)
Nuovo token + email di conferma per un utente non ancora confermato. Risposta sempre neutra; cooldown 60s.

### `GET /auth/invite-info?token=...`
Contesto dell'invito azienda per la pagina di accettazione (**non consuma** il token): `{email, denominazione, parent_display_name}`. `404` se non valido.

### `POST /auth/accept-invite`
Body: `{"token", "password"}`. Consuma il token d'invito (TTL 48h): imposta la password, conferma l'email e attiva la membership. → `{"email"}` (auto-login del frontend).

### `GET /plans`
Piani di abbonamento attivi, ordinati per `ordering`. Usato dallo step 2 della registrazione.

```json
[{ "id": 1, "nome": "Gratuito", "slug": "gratuito", "descrizione": "...", "prezzo_annuale": "0.00",
   "tipo_prezzo": "gratis", "etichetta_prezzo": null,
   "ai_check": 0, "alert_attivo": false, "alert_giorni_preavviso": null,
   "num_account_aziendali": 1, "ordering": 1, "is_active": true, "updated_at": "..." }]
```

`tipo_prezzo` (`importo`/`gratis`/`su_richiesta`) decide come la UI mostra il prezzo; con `su_richiesta` il piano **non è selezionabile alla registrazione** (`POST /auth/register` risponde `400` se lo slug punta a un piano su richiesta) né attivabile con il cambio piano self-serve — l'`etichetta_prezzo` sostituisce l'importo (fallback UI «Su richiesta»).

### `GET /job-positions`
Posizioni aziendali **attive**, ordinate per `ordering`: `[{id, nome, slug}]`. Pubblico come `GET /plans` (serve al form di registrazione, non autenticato). Lo `slug` è l'identificativo stabile che il form invia a `POST /auth/register`; il catalogo (migration 0022) è soft-disable e si amministra via SQL.

## Endpoint autenticati

### `GET /addons`
Catalogo **add-on attivi**, ordinati per `ordering`: `[{id, nome, slug, descrizione, prezzo, tipo_prezzo, etichetta_prezzo, ordering, is_active, updated_at}]`. Lo `slug` è l'identificativo STABILE a cui verranno agganciate le funzionalità future. A differenza di `GET /plans` (pubblico perché serve alla registrazione) la rotta è **autenticata**: il catalogo si vede solo dentro l'app. Il flusso di acquisto non esiste ancora (il bottone «Acquista» — «Attiva» per gli add-on `gratis` — mostra l'avviso «In arrivo» tramite lo stub `purchaseAddon`); per gli add-on `su_richiesta` la CTA è «Richiedi una consulenza» e passa dallo stub `requestConsultation` (il futuro endpoint di acquisto dovrà rifiutarli lato server). **Eccezione: `consulto-esperto`** — è l'aggancio del flusso consulenze (`POST /me/consulenze`, senza pagamento in questa fase) e si attiva dalla pagina del bando dopo un AI-check, non dalla card in Abbonamento.

### `GET /me`
Profilo dell'utente corrente + abbonamento attivo con il piano.
```json
{ "profile": { "id": "uuid", "email": "...", "nome": "...", "cognome": "...", "azienda": null,
               "telefono": null, "job_position_id": 3,
               "job_position": { "id": 3, "nome": "CTO / Direttore Tecnico", "slug": "cto" },
               "job_position_altro": null,
               "role": "cliente", "is_active": true, "created_at": "..." },
  "subscription": { "id": "uuid", "status": "active", "data_inizio": "2026-07-03",
                    "data_scadenza": "2027-07-03", "plan": { ...come /plans... } } }
```
`job_position` è presente anche se la voce è stata **disattivata** nel frattempo (catalogo soft-disable): chi l'aveva scelta continua a vederla.
`role` ∈ `admin`/`cliente`/`progettista`. Per i progettisti — e per gli admin che hanno già un codice — la risposta include anche `progettista: {codice}` (il codice `PRG-00001`, assegnato dal sistema alla promozione, o alla prima proposta per gli admin, e immutabile); il progettista conserva tutte le funzionalità cliente.

### `PATCH /me`
Aggiorna l'anagrafica. Body (tutti opzionali): `nome`, `cognome`, `azienda`, `telefono`, `job_position_id`, `job_position_altro`. → come `GET /me`. Regole: il telefono è validato in E.164 **solo se la chiave è presente** (il client la omette quando il campo non cambia, così i valori pre-0022 a testo libero non bloccano il salvataggio degli altri campi; stringa vuota = azzeramento); `job_position_id` deve esistere ed essere **attivo** (`400` altrimenti — la FK da sola non intercetterebbe le voci disattivate), `null` azzera; `job_position_altro` sopravvive solo se la posizione della riga è «Altro» — lo impone il trigger `trg_profiles_job_position_altro` a livello di riga (race-free anche con PATCH concorrenti).

### `POST /me/verify-cf`
Verifica il **codice fiscale personale** all'Anagrafe Tributaria via openapi.it (**a pagamento**, ~0,05 € + IVA). Body: `{ "codice_fiscale": "..." }`. La validazione strutturale (checksum, omocodia inclusa) è locale e gratuita: gli input malformati non generano spesa. Idempotente: lo stesso CF già verificato risponde senza nuova chiamata. Protetta da lock per utente (doppio click/tab concorrenti) e cooldown tra tentativi a pagamento.
Esiti: `200 {codice_fiscale, cf_verified_at}`; `400 cf_invalid` (malformato) / `400 cf_not_valid` (formalmente corretto ma non registrato — salvato non verificato SOLO se non c'è già un CF verificato: una verifica fallita non cancella mai un dato buono); `409 verify_in_progress`; `429 verify_cooldown`; `502 openapi_error`; `503 openapi_not_configured`; `504 openapi_timeout` (esito ignoto, nessun retry automatico). Il profilo (`GET /me`, `PATCH /me`) espone/accetta anche `codice_fiscale` (il cambio del CF azzera la verifica).

### `POST /me/subscription`
Cambio piano (senza pagamento in questa fase). Body: `{"plan_id": 3}`. L'abbonamento attivo passa a `cancelled` e ne viene creato uno nuovo annuale. → come `GET /me`; se il downgrade ha retrocesso membri della famiglia, la risposta include `plan_switch_adjustment: {demoted, revoked_pending}`. Errori: `400` piano inesistente/non attivo; `400` piano `su_richiesta` («disponibile solo su richiesta», il guard scatta PRIMA della RPC — l'assegnazione resta possibile via `POST /admin/users/{id}/subscription`); `403 child_plan_locked` se l'utente è un figlio attivo (il piano si gestisce sul titolare).

## Azienda (gruppo di account)

> **Terminologia**: nell'interfaccia utente il gruppo si chiama **"Azienda"**; internamente (tabelle, endpoint, tipi) resta il nome tecnico *family* (`family_members`, `/me/family`, ecc.) — non rinominare.

Il limite account (`num_account_aziendali` del piano) **include il titolare**. `GET /me` restituisce `family`: per il titolare `{role:'parent', used, limit}` (presente solo se limite > 1 o se ha membri), per un membro `{role:'child', status, denominazione, parent_display_name}`; un figlio attivo riceve `subscription` del titolare con `inherited: true`.

### `GET /me/family` *(solo titolare)*
`{ "limit": 3, "used": 2, "members": [{ "id": "membership-uuid", "member_id": "uuid", "denominazione": "Sede di Bari", "email": "...", "status": "pending|active|demoted", "invite_kind": "new_user|existing_user", "invited_at": "...", "joined_at": null, "demoted_at": null }] }`

### `POST /me/family/members` (201) *(solo titolare)*
Body: `{"email": "...", "denominazione": "..."}`. Email nuova → invito nativo Supabase (l'utente imposta la password da `/accetta-invito`); email già registrata → invito in piattaforma + email di notifica via Resend (best-effort: `email_sent` in risposta). → `{family, email_sent}`. Errori (409 salvo indicato): `family_limit_reached`, `already_in_family`, `invite_already_pending`, `target_is_admin`, `target_is_parent`, `cannot_invite_self` (400), `not_family_parent` (403).

### `POST /me/family/members/{id}/resend` *(solo titolare)*
Reinvia l'invito (pending): per gli utenti creati dall'invito rigenera il link Supabase, per gli esistenti reinvia l'email.

### `POST /me/family/members/{id}/reactivate` *(solo titolare)*
Riattiva un membro retrocesso se c'è un posto libero (`409 family_limit_reached` altrimenti). Il suo abbonamento proprio viene annullato: torna a ereditare.

### `DELETE /me/family/members/{id}` *(solo titolare)*
Rimuove un membro: un attivo diventa indipendente con piano Gratuito; un invito pending viene annullato (e l'utente mai attivato eliminato). → `family` aggiornata.

### `GET /me/invitations`
Inviti in attesa ricevuti dall'utente: `[{id, denominazione, parent_display_name, invited_at}]` (alimenta il banner in-app).

### `POST /me/invitations/{id}/accept`
Accetta l'invito: l'eventuale abbonamento proprio viene annullato, da lì si eredita quello della famiglia. → come `GET /me`. `409 family_full` se non c'è più posto.

### `POST /me/invitations/{id}/decline`
Rifiuta l'invito. → elenco inviti aggiornato.

## Dati aziendali

### `GET /me/company`
`{ "editable": true|false, "company": {...} | null }` — dell'**azienda attiva** (header `X-Active-Company`; senza header è l'unica/più vecchia). Il titolare (o un utente singolo) vede e modifica i propri; un **figlio attivo** vede quelli della famiglia in sola lettura (`editable: false`). `company: null` se il titolare non ha ancora alcuna azienda.

### `PUT /me/company`
Upsert dei dati aziendali. Bloccato (`403`) SOLO per i **figli attivi**, che ereditano i dati della famiglia; titolari, utenti singoli, pending e retrocessi scrivono i propri. Campi: `ragione_sociale`*, `forma_giuridica`, `partita_iva`* (11 cifre, prefisso IT tollerato), `codice_fiscale`, `ateco_id`/`settore_id`/`regione_id` (id delle lookup del DB secondario → il backend denormalizza `ateco_codice`, `settore_nome`, `regione_nome`; `400` se sconosciuti), **`beneficiari_ids`** (lista di id della lookup `beneficiari`, max 50, deduplicata → in risposta arriva `beneficiari: [{id, nome}]`; `400` se un id è sconosciuto), `anno_fondazione` (1800-2100), `indirizzo`, `comune`, `provincia`, `cap` (5 cifre), `classe_dimensionale` (`micro|piccola|media|grande`), `numero_dipendenti`, `fascia_fatturato` (`fino_100k|100k_500k|500k_2m|2m_10m|10m_50m|oltre_50m`), `pec`, `telefono`, `sito_web`.

### `GET /me/company/facets`
Cosa l'azienda **è davvero**, negli id delle lookup del catalogo: `{ regioni, ateco, settori, beneficiari, sufficiente }`. Non è un doppione di `GET /me/company`, che restituisce i campi del **form** (una regione, un ATECO): qui `regioni` copre **tutte le sedi** (legale + unità locali, da `company_data.derived.regioni_ids`) e `ateco` include le **divisioni secondarie** certificate. Stessa funzione che alimenta il badge di compatibilità e l'AI-check, così i tre non possono divergere. Un figlio attivo vede i facet della famiglia. `sufficiente: true` = P.IVA importata (ATECO e regione valorizzati): è la condizione del **badge**, non del preset «Bandi per te», che filtra utilmente anche con i soli beneficiari dichiarati a mano. Azienda assente → tutti gli array vuoti, `sufficiente: false` (mai `404`).

### Import da P.IVA — due fasi

L'import avviene in due chiamate: **l'anteprima paga e non scrive, la conferma scrive e non paga.** Il payload recuperato resta in staging (`company_import_drafts`, TTL 30 min) così la conferma non deve ripagare i ~0,30 € della chiamata. Annullare dopo l'anteprima non salva nulla, e riaprirla entro il TTL è gratuito.

### `POST /me/company/import/preview`
Recupera i dati da openapi.it (endpoint IT-full, **a pagamento**, ~0,30 € + IVA per chiamata) e li restituisce **in sola lettura**: nessuna scrittura su `company_profiles`, `company_data` o `company_people`. Body: `{ "partita_iva": "..." }` (facoltativa: default quella salvata). Risposta: `{ azienda: {partita_iva, ragione_sociale, codice_fiscale, forma_giuridica, stato_impresa, sede, regione, ateco, legale_rappresentante, numero_persone}, autofill: {applied, conflicts}, suggestions: {codici_ateco}, fetched_at, draft_expires_at, reused, sandbox }`.
`applied` sono i campi **vuoti** che la conferma compilerà, `conflicts` quelli già valorizzati che differiscono e che **non** verranno toccati: entrambi calcolati con la stessa funzione che userà la conferma. `reused: true` = l'anteprima viene da un payload già pagato (nessun nuovo addebito, e il cooldown non si applica).
Qui vivono **cooldown, lock e registro consumi**. Errori: `400 bad_request` (P.IVA assente/checksum errata), `403 forbidden` (figlio attivo), `404 not_found` (P.IVA non nel Registro Imprese), `409 import_cooldown` (recupero recente: il cooldown guarda l'ultimo fetch pagato, sia esso un import confermato o un'anteprima in staging) / `409 import_in_progress` (lock, con i minuti residui nel messaggio), `502 openapi_error`, `503 openapi_not_configured`, `504 openapi_timeout` (esito ignoto: nessun retry automatico, il lock scade da solo per non pagare due volte al buio).

### `POST /me/company/import/confirm` (201)
Scrive i dati dell'anteprima e compila i campi aziendali **vuoti** (i valori inseriti dall'utente non vengono mai sovrascritti). **Nessuna chiamata al provider**: non costa nulla e non passa dal cooldown. Body: `{ "partita_iva": "..." }` — è una guardia, non una scelta: deve combaciare col draft. Il draft viene consumato, quindi una seconda conferma non trova nulla. Risposta: `{ company, dossier, people, autofill: {applied, conflicts}, suggestions: {codici_ateco}, fetched_at, sandbox }` — `suggestions.codici_ateco` sono le divisioni degli ATECO secondari da aggiungere alle preferenze con un click.
Errori: `403 forbidden` (figlio attivo), `409 draft_not_found` (anteprima scaduta o già consumata: va rifatta), `409 draft_mismatch` (l'anteprima è di un'altra P.IVA), `409 import_in_progress` (conferma concorrente).

### `GET /me/company/dossier`
Dossier certificato importato: `{ editable, imported, fetched_at, sandbox, dossier: {anagrafica, attivita, sede, contatti, dipendenti, bilanci, partecipazioni, flags} | null, people: [...], derived: {...} }`. Stessa visibilità di `GET /me/company` (figlio attivo → sola lettura). `sandbox: true` = dati di test. Il payload grezzo del provider non viene mai esposto integralmente.

## AI-check

Analisi di compatibilità **azienda ↔ bando** con LLM (API Anthropic) e punteggio deterministico. Consuma **1 AI-check della quota annua del piano** (`subscription_plans.ai_check`, condivisa da tutta l'azienda) a ogni generazione, rigenerazioni comprese; costo API ~0,10–0,20 $ a report (l'estrazione dei requisiti è cachata per bando). L'esito distingue sempre **ammissibilità** (gate binario sui requisiti obbligatori: uno solo mancato ⇒ `non_ammissibile`; dato mancante ⇒ `da_verificare`, mai promosso) e **punteggio** (`stima` se il bando pubblica la griglia, `euristico` con pesi interni altrimenti).

### `POST /me/ai-checks` (201)
Body `{ "bando_slug": "..." }`. Avvia l'analisi (solo titolare) e risponde subito con la riga `pending`: la generazione gira in background (1–2 minuti) e si segue con la GET (polling). I guasti del provider AI (timeout compreso: esito ignoto, nessun retry automatico) non arrivano mai come errore HTTP di questa POST — emergono come `status: "error"` con `error_detail` sulla riga letta via GET. Un'analisi `pending` da oltre 10 minuti viene chiusa come `error` alla lettura/POST successiva (failsafe per i riavvii).
Errori: `503 ai_not_configured`, `403 forbidden` (figlio attivo), `400 bad_request` (dati aziendali insufficienti), `404 not_found` (bando), `409 ai_check_in_progress` (analisi già in corso o altra operazione sull'azienda), `429 ai_quota_exceeded`, `429 ai_check_cooldown` (5 min per coppia azienda×bando).

### `GET /me/ai-checks?bando_slug=&page=&page_size=`
Storico dell'**azienda attiva** (vedi header `X-Active-Company`; più recenti prima): `{ editable, quota: {totale, usati, rimanenti, periodo_inizio, periodo_fine}, items, total }`. La `quota` resta quella del pool condiviso, non dipende dall'azienda attiva. Con `bando_slug` gli item includono il **`report` completo** (storico versionato del bando, il primo è l'ultima analisi); senza, la lista è sintetica (esito/punteggio come colonne). Item: `{id, bando_id, bando_slug, bando_titolo, status: pending|ready|error, error_detail, esito: ammissibile|non_ammissibile|da_verificare, punteggio (0-100), tipo_punteggio: stima|euristico, model, extraction_cached, created_at, ready_at, report?}`.

Il `report` (jsonb, `schema_version: 1`) è verificabile punto-punto: `requisiti[]` e `criteri[]` con verdetto (`soddisfatto|parzialmente_soddisfatto|non_soddisfatto|dato_mancante`), **`riferimento_bando`** (sezione + testo citato alla lettera, con flag `verificata`), **`dato_azienda`** (campo esatto + valore usato) e motivazione; `verifiche_strutturate` (pre-check esatti su regione/ATECO/settore/beneficiari/stato); `griglia` (presente/fonte/soglia, punti stimati); `punti_di_forza`/`punti_di_debolezza`/`dati_mancanti`; `disclaimer`.

### `GET /me/ai-checks/quota`
`{ totale, usati, rimanenti, periodo_inizio, periodo_fine }` — quota del periodo di abbonamento attivo, contata dalle righe di `ai_checks` (`pending` + `ready`) nella finestra `data_inizio..data_scadenza`: le analisi fallite non consumano. Nota: la finestra segue l'abbonamento attivo — un cambio piano la fa ripartire (accettato in fase 1, senza pagamenti).

### `GET /me/ai-checks/{id}`
Singolo report completo (anche per i figli attivi). `404` se non appartiene all'azienda attiva.

## Preferenze

### `GET /me/preferences` · `PUT /me/preferences`
Preferenze di filtro/notifica **personali** (anche gli account collegati hanno le proprie): valori "seguiti" IN AGGIUNTA a quelli reali dell'azienda (es. un ATECO in più). Forma (uguale in lettura e scrittura, `PUT` = sostituzione dell'intero set):
```json
{ "regioni": [9], "settori": [], "beneficiari": [], "codici_ateco": [45],
  "tipologie": [], "modalita": [], "programmi": [] }
```
Gli id puntano alle lookup del catalogo (`GET /lookups`; `tipologie`/`modalita` → `tipologie_bando`/`modalita_erogazione`); id sconosciuto → `400`. Il backend denormalizza le etichette (nessuna FK cross-DB) e scrive a diff. Il preset «Bandi per te» del frontend unisce questi id ai valori aziendali reali e li applica ai filtri di `GET /bandi`.

### `GET /lookups`
Valori delle faccette di filtro, dal DB secondario (cache server 1h, `Cache-Control: private, max-age=3600`):
```json
{ "regioni": [{"id": 10, "nome": "Lombardia"}], "settori": [...], "beneficiari": [...],
  "codici_ateco": [{"id": 3, "codice": "49", "descrizione": "Trasporto terrestre"}],
  "tipologie_bando": [...], "modalita_erogazione": [...], "programmi": [...] }
```

### `GET /bandi`
Elenco paginato dei bandi (solo quelli pubblicabili: `stato_processing='completed'`).

**I bandi chiusi vanno sempre in coda**, qualunque ordinamento: "chiuso" = `stato_bando='chiuso'` **oppure** `data_scadenza` passata rispetto a oggi nel fuso italiano (robusto anche se lo stato nel catalogo non è aggiornato). PostgREST non ordina per espressioni, quindi l'elenco è servito da due query complementari (non chiusi + chiusi) con paginazione che unisce le due code; con `scadenza_asc` i chiusi in coda sono ordinati dalla chiusura più recente.

Parametri query:
| Parametro | Tipo | Note |
|---|---|---|
| `page` / `page_size` | int | default 1 / 20, `page_size` max 50 |
| `sort` | string | `pubblicazione_desc` (default: più recenti prima), `scadenza_asc`, `scadenza_desc`, `importo_desc` |
| `q` | string | ricerca full-text italiana (websearch) su titoli e descrizioni, sia grezzi (`titolo_raw`, `descrizione_raw`) sia rielaborati mostrati in UI (`titolo`, `titolo_breve`, `descrizione_breve`) |
| `stato` | csv | tra `aperto`, `chiuso`, `in apertura prossimamente` |
| `livello` | string | `flash_bando` o `guida_bando` |
| `tipologie`, `modalita`, `programmi` | csv di id | filtri su colonne dirette |
| `regioni`, `settori`, `beneficiari`, `ateco` | csv di id | filtri M:N via junction (OR dentro la faccetta, AND tra faccette) |
| `importo_min`, `importo_max` | int (€) | su `importo_totale_eur` |
| `scadenza_da`, `scadenza_a` | date ISO | intervallo su `data_scadenza` |
| `scade_entro_giorni` | int 1-365 | da oggi a oggi+N |

Item della risposta: `id`, `slug`, `titolo`, `titolo_breve`, `descrizione_breve`, `stato_bando`, `livello`, date, importi, `ente_erogatore`, `tipologia {id,nome}`, `modalita_erogazione {id,nome}`, `regioni [{id,nome}]`, `compatibilita` (vedi sotto).

**`compatibilita`** — punteggio a-priori azienda↔bando, **dinamico** (mai persistito), calcolato server-side per ogni item ed esposto sia in elenco sia in dettaglio: `{ punteggio (0-100, %), matched, totale, dimensioni: { regioni|ateco|settori|beneficiari: {soddisfatta, matched, totale, matched_ids[], nazionale} } }`, es. `3/4`.

`matched`/`totale` in cima sono **requisiti soddisfatti / requisiti valutabili**. Dentro un requisito le voci sono **alternative (OR)**: `soddisfatta` è vera con **anche una sola** voce in comune — un bando che elenca quattro settori li accetta tutti, non ne chiede quattro insieme. I campi `matched`/`totale`/`matched_ids` della singola dimensione sono solo dettaglio (voci in comune / voci elencate dal bando) e **non pesano** sul punteggio. Tra requisiti si somma, tutti a **peso uguale**.

**Tutte le sedi** (sede legale + unità locali) valgono sul territorio: basta una sede in una regione ammessa. Ne segue che un bando `nazionale` (tutte le regioni del catalogo) soddisfa il territorio da sé — il flag serve solo alla UI, che altrimenti elencherebbe venti voci. Una dimensione **assente** da `dimensioni` non è valutabile (l'azienda non ha quel dato) e non entra nel denominatore: è il caso del settore non compilato e delle **categorie di beneficiario non dichiarate** (`company_profiles.beneficiari`, vedi `PUT /me/company`). È **`null`** se il profilo non è sufficiente (P.IVA non importata: mancano `ateco_id`/`regione_id`) o il bando non ha requisiti valutabili. I due DB non si uniscono in SQL: i facet azienda si costruiscono una volta per richiesta (cache TTL breve per owner) e il confronto per-bando è Python puro (`services/compatibility.py`).

### `GET /bandi/{slug}`
Dettaglio completo: campi dell'elenco (`compatibilita` compreso) + `area_geografica`, `tematica[]`, `link_bando`, `link_candidatura`, `contenuto` (JSON strutturato a sezioni/segmenti, renderizzato dal frontend), `allegati[]`, `programma`, `settori[]`, `beneficiari[]`, `codici_ateco[]`. `404` se lo slug non esiste o il bando non è pubblicabile.

**Filtro domini esclusi** (`services/link_policy.py`): i rimandi a domini di aggregatori concorrenti (oggi `obiettivoeuropa.com`, sottodomini compresi) non escono mai dall'API — `link_bando`/`link_candidatura` diventano `null`, gli allegati bloccati vengono rimossi, e dentro `contenuto` qualunque segmento col dominio nel testo visibile cade per intero, un segmento «link» bloccato senza menzione visibile perde solo il link (degrada a testo) e le menzioni testuali spariscono anche dai testi fuori dai segmenti. Lo stesso filtro si applica alla riga passata all'AI-check (il modello non deve citare quei link nel report) e, in lettura, ai report AI-check storici — su ogni via: cliente e dettaglio richiesta dei progettisti. Vedi docs/architecture.md, decisione 11.

## Bandi salvati

Preferiti **per utente** sul DB primario: RIFERIMENTI al catalogo (bando_id + snapshot di slug/titolo/scadenza/stato), non copie. Se il bando sparisce dal catalogo la riga resta e viene servita dallo snapshot con `disponibile: false`. Cap: 200 bandi salvati per utente.

### `POST /me/saved-bandi` (201)
Body `{ "bando_slug": "..." }`. **Idempotente** (è un toggle): già salvato → ritorna la riga esistente. Risposta `SavedBandoItem`: `{ bando: <item della lista bandi>, disponibile, in_calendario, salvato_il }`.
Errori: `404 not_found` (bando non nel catalogo), `400 bad_request` (limite raggiunto).

### `GET /me/saved-bandi?page=&page_size=`
Elenco paginato (`page_size` max 50, i salvati più di recente per primi): pagina sul primario, poi UNA query al catalogo per i dati vivi della pagina; i bandi spariti arrivano dallo snapshot con `disponibile: false`. `in_calendario` indica se la scadenza è già in calendario.

### `GET /me/saved-bandi/ids`
`{ "bando_ids": [int] }` — id salvati (per lo stato dei toggle nelle liste, chiamata leggera).

### `DELETE /me/saved-bandi/{bando_id}` (204)
Idempotente. L'eventuale evento scadenza in calendario NON viene toccato (indipendenti).

## Calendario

Eventi **per utente** sul DB primario, vista mensile. Date e orari sono di **calendario italiano** (wall-clock, senza fuso): il client li mostra così come sono. Due tipi: `personale` (CRUD completo) e `bando` (scadenza derivata dal catalogo: **data in sola lettura**, modificabili solo titolo e note). Cap: 500 eventi per utente. Niente ricorrenze in v1.

### `GET /me/calendar?anno=&mese=`
Eventi del mese (`anno` 2000-2100, `mese` 1-12): `{ items: [{id, titolo, data, tutto_il_giorno, ora_inizio, ora_fine, note, tipo, bando_id, bando_slug, created_at, updated_at}] }`, ordinati per data e ora (i «tutto il giorno» in testa). Non tocca mai il DB secondario.

### `POST /me/calendar` (201)
Crea un evento **personale** (il `tipo` non arriva mai dal client). Body: `titolo` (≤200, non vuoto), `data` (anno 2000-2100, l'intervallo visualizzabile), `tutto_il_giorno` (default true — azzera gli orari), `ora_inizio`/`ora_fine` opzionali (con orari serve l'inizio; la fine deve seguire l'inizio), `note` (≤2000).
Errori: `422 validation_error`, `400 bad_request` (limite raggiunto).

### `POST /me/calendar/bando` (201)
Body `{ "bando_slug": "..." }`. Aggiunge la **scadenza del bando** come evento tipo `bando` (data derivata dal catalogo, titolo «Scadenza: …», tutto il giorno). **Idempotente**: evento già presente → lo ritorna (una sola scadenza per bando per utente). Non richiede che il bando sia tra i salvati.
Errori: `404 not_found` (bando sparito), `400 bad_request` (bando senza scadenza / limite raggiunto).

### `PATCH /me/calendar/{event_id}`
Aggiorna i campi passati (tutti opzionali). Per gli eventi `bando` sono modificabili SOLO `titolo` e `note` (`400 bad_request` sugli altri: la data è la scadenza ufficiale). La coerenza degli orari viene rivalidata sul merge.
Errori: `404 not_found` (evento inesistente/altrui/id malformato), `400 bad_request`.

### `DELETE /me/calendar/{event_id}` (204)
Elimina l'evento (`404` se inesistente o di un altro utente). Per gli eventi `bando` NON tocca il bando salvato.

## Notifiche in-app

Il canale **affidabile** degli eventi (le email sono best-effort). Idempotenti per `(user_id, dedup_key)`: i retry non creano doppioni. I contenuti sono minimizzati (nessun dato personale di terzi, e MAI il link videochiamata — è una credenziale, l'istanza Jitsi è aperta): i dettagli si leggono seguendo `url`, dove vale l'autorizzazione dell'endpoint di destinazione.

### `GET /me/notifications?page=&page_size=`
Pagina di notifiche (`page_size` max 50) + **`non_lette`** complessive (il numero sul badge): `{ items: [{id, tipo, titolo, corpo, url, read_at, created_at}], total, page, page_size, total_pages, non_lette }`. Il frontend la interroga in polling (60s).

### `POST /me/notifications/read` (204)
Body: `{"all": true}` oppure `{"ids": [1, 2]}` (almeno uno dei due). Segna come lette solo le proprie non lette.

## Alert email sui nuovi bandi

Quando un bando diventa disponibile, gli utenti con azienda **compatibile** (pre-check con punteggio ≥ 60) lo ricevono in un'email **digest** giornaliera, con il ritardo del piano (`alert_ritardo_giorni`: Advisor 1 · Pro 7 · Smart 14 giorni dalla pubblicazione). Nessuna pre-schedulazione: l'idoneità si ricalcola a ogni run (piano corrente, opt-in, email verificata su `auth.users`, account attivo, bando ancora aperto); il ledger `(utente, bando)` garantisce che nessun bando venga inviato due volte. Il digest include per ogni bando: titolo, ente, importo, **scadenza evidenziata**, «perché lo vedi» (le dimensioni di compatibilità con i nomi) e il link; header `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058).

### `GET /me/alert-settings` · `PUT /me/alert-settings`
`{abilitati, piano_include_alert, ritardo_giorni}` — il piano effettivo per i collegati attivi è quello del titolare. Il PUT (`{"abilitati": bool}`) è consentito anche se il piano non include gli alert (il gate vero è alla run). Stessa riga di verità del link di disiscrizione nelle email.

### `POST /alerts/unsubscribe?token=` · `GET /alerts/unsubscribe?token=` *(pubblici, nessuna auth)*
Disiscrizione **a un clic** (RFC 8058): il POST è idempotente e risponde **sempre allo stesso modo** (204; pagina HTML di conferma se la richiesta arriva da un form del browser) con token valido, ignoto o malformato — nessuna enumerazione possibile. Il GET mostra solo una pagina con un bottone di conferma e **non muta nulla**: gli scanner antispam pre-aprono i link GET delle email.

## Consulenze (lato cliente)

Flusso: AI-check completato → attivazione dell'addon `consulto-esperto` → richiesta nel pool dei progettisti → proposte → **accettazione = assegnazione definitiva 1:1** (+ prenotazione slot opzionale, contestuale o successiva). Le **azioni** (creare, accettare, rifiutare, annullare, prenotare) sono riservate al **titolare** dell'Azienda; gli account collegati leggono. Eventi: ogni transizione genera notifica in-app + email (vedi `docs/database.md`, audit incluso).

### `GET /me/consulenze` · `GET /me/consulenze/{id}`
Richieste dell'Azienda (visibilità per `family_parent_id`). Item: `{id, stato, bando_id, bando_slug, bando_titolo, esito, punteggio, created_at, assigned_at, editable, progettista, proposte_aperte, proposte, appuntamento}` — `stato` ∈ `nuova`/`assegnata`/`annullata`; `progettista = {codice, nome}` (assegnato; la UI mostra **nome e cognome** — più umano — e il codice resta nel payload per usi interni); `proposte` (solo nel dettaglio): `[{id, codice_progettista, nome_progettista, messaggio, stato, created_at}]` — anche qui il cliente vede l'autore per `nome_progettista`; `appuntamento = {id, inizio, fine, stato, videocall_url}` in UTC — `videocall_url` è la stanza Jitsi dedicata (`{JITSI_BASE_URL}/bandofit-{token}`, derivata dal token a DB; solo prenotazioni confermate). La notifica in-app dell'evento 2 resta minimizzata (solo il bando); il nome dell'autore compare nell'email, effimera.

### `POST /me/consulenze` (201) *(solo titolare)*
Body: `{"ai_check_id": "uuid"}` (un AI-check `ready` della propria Azienda). Crea la richiesta con gli snapshot (esito/punteggio, bando, addon+prezzo) e avvisa **tutti i progettisti e gli admin attivi** (evento 1; parità admin). Il pagamento dell'addon è fuori scope: l'innesto del checkout è in `consulting_service.create_request`. Errori: `403` account collegato; `404` AI-check non trovato / addon non a catalogo; `409` AI-check non completato / **richiesta già aperta per questo bando** (una sola `nuova` per bando per Azienda).

### `POST /me/consulenze/{id}/proposte/{pid}/accetta` *(solo titolare)*
Body: `{"slot_id": "uuid" | null}`. Accetta la proposta = assegna la consulenza in via definitiva (RPC atomica: le altre proposte diventano `superate`); con `slot_id` prenota nella stessa transazione (**all-or-nothing**: `409 slot_taken` ⇒ non resta nemmeno l'assegnazione, si riprova). Eventi 4 (+3 se prenota) al progettista. Errori: `409` richiesta non più aperta / proposta non più disponibile / progettista non più disponibile / slot preso.

### `POST /me/consulenze/{id}/proposte/{pid}/rifiuta` *(solo titolare)*
Rifiuto esplicito di una singola proposta (il progettista può inviarne una nuova). `409` se non più `inviata`.

### `GET /me/consulenze/{id}/slots?proposta=`
Slot **liberi e futuri** del progettista assegnato o — con `proposta` — di quello della proposta indicata (per prenotare contestualmente all'accettazione). In UTC: la UI li mostra nel fuso del browser. Item: `{id, inizio, fine, prenotato, serie_id}` (`serie_id` = raggruppamento di ricorrenza, uuid opaco).

### `POST /me/consulenze/{id}/prenota` (201) · `POST /me/consulenze/{id}/prenotazione/annulla` *(solo titolare)*
Prenota uno slot dopo l'assegnazione (`{"slot_id"}`; RPC serializzata: `409 slot_taken` se appena preso, `409` se esiste già un appuntamento) / annulla l'appuntamento confermato (lo slot torna prenotabile; il progettista riceve una notifica in-app). Evento 3 sulla prenotazione: notifica al progettista + email col **link videochiamata** a ENTRAMBI (al cliente arriva l'email di conferma con orario e link). Un annullo nasconde il link; una ri-prenotazione genera un link **nuovo** (token per prenotazione).

### `POST /me/consulenze/{id}/annulla` *(solo titolare)*
Annulla la richiesta finché è `nuova`: esce dal pool, le proposte aperte diventano `superate` e i loro autori ricevono una notifica in-app. `409` se non più aperta.

## Area progettista

Tutte dietro `require_progettista` (il ruolo si legge dal DB a ogni richiesta, non dal JWT). **Parità admin**: il gate ammette anche il ruolo `admin` — gli amministratori hanno esattamente le stesse funzioni dell'area progettista (le loro proposte sono accettabili grazie alla guardia ridefinita in migration 0019, e il codice PRG viene assegnato pigramente alla prima proposta). Il progettista vede: nel **pool** i dati PARZIALI del requisito (ragione sociale, P.IVA, denominazione utente, email del titolare, bando, esito+punteggio e report dell'AI-check); i dati **FULL** (tutti i dati aziendali + dossier certificato) **solo per le consulenze assegnate a lui**, con ogni accesso registrato in `audit_log`.

### `GET /progettista/richieste`
`{ aperte: [...], assegnate: [...] }` — le richieste `nuova` di tutte le aziende (pool globale) + le proprie assegnate. Item: `{id, stato, ragione_sociale, partita_iva, denominazione_utente, email, bando_id, bando_slug, bando_titolo, esito, punteggio, created_at, assegnata_a_me, mia_proposta_stato, appuntamento}`. Le richieste annullate o assegnate ad altri **non esistono** per il progettista (404 sul dettaglio).

### `GET /progettista/richieste/{id}`
Dettaglio (pool o assegnata a sé): campi della lista + **`ai_check`** completo (report con verdetti e citazioni — richiesto dal flusso: è ciò su cui il progettista valuta se proporsi) + `mie_proposte`.

### `POST /progettista/richieste/{id}/proposte` (201) · `POST /progettista/proposte/{pid}/ritira` (204)
Invia una proposta (`{"messaggio"}`, ≤4000; solo su richieste `nuova`; **una sola proposta aperta** per richiesta → `409`; il titolare riceve evento 2) / ritira la propria proposta ancora `inviata` (`409` altrimenti; dopo il ritiro se ne può inviare una nuova).

### `GET /progettista/richieste/{id}/dossier`
Vista FULL, **solo se assegnata a sé** (`403` altrimenti): `{ company: {...dati dichiarati...}, dossier: {...come GET /me/company/dossier...} }`. **Ogni lettura scrive `consulenza.dossier_accessed` in audit_log.** Il `raw` di `company_data` non esce mai dal server (vale l'invariante dell'import).

### `GET /progettista/appuntamenti` · `POST /progettista/appuntamenti/{id}/annulla` (204)
Appuntamenti confermati (`[{id, request_id, inizio, fine, stato, bando_titolo, ragione_sociale, email, videocall_url}]`, in UTC) / annullo da parte del progettista (il titolare riceve una notifica in-app; lo slot torna prenotabile). Anche l'`appuntamento` del pool (`GET /progettista/richieste*`) porta `videocall_url`: le richieste aperte non hanno booking, quindi il link è visibile solo all'assegnato.

### `GET/POST/PATCH/DELETE /progettista/slots`
CRUD degli slot di disponibilità: `{inizio, fine}` timestamp ISO **con offset** (UTC; durata 15 min–12 h, solo futuri; `prenotato` derivato nei GET; `serie_id` = raggruppamento di ricorrenza, `null` per gli slot singoli). Sovrapposizioni rifiutate a livello DB (`409 slot_overlap`); modifica/cancellazione di uno slot prenotato rifiutate (`409 slot_booked`) e serializzate contro le prenotazioni concorrenti (RPC con `FOR UPDATE`). Il PATCH di una singola occorrenza **non** la stacca dalla sua serie.

### `POST /progettista/slots/serie` (201)
Crea una serie di slot ricorrenti. Body: `{"occorrenze": [{inizio, fine}, …]}` (1–370 occorrenze, ognuna validata come uno slot singolo: futura, 15 min–12 h). L'**espansione della ricorrenza è a carico del client** (`lib/ricorrenza.ts`): solo il browser conosce il fuso dell'utente, e «ogni settimana alle 10:00» deve restare alle 10:00 a muro anche attraverso i cambi di ora legale. Le occorrenze che si sovrappongono a slot esistenti (o tra loro) vengono **saltate**, non fanno fallire la serie (RPC `fn_create_slot_serie`, transazione unica). Risposta: `{serie_id, creati: [SlotOut…], saltati}`. Errori: `400` occorrenza non valida (nessuna scrittura); `409 serie_tutta_sovrapposta` se nessuna occorrenza entra.

### `DELETE /progettista/slots/serie/{serie_id}` (200)
Elimina gli slot **liberi** della serie; quelli prenotati non si toccano mai. Risposta con conteggi per la UI: `{eliminati, mantenuti}`. `404 serie_not_found` se la serie non esiste o è di un altro progettista.

## Endpoint admin

### `GET /admin/users`
Elenco utenti con abbonamento attivo. Parametri: `q` (cerca in email/nome/cognome/azienda), `role` (`admin`|`cliente`|`progettista`), `page`, `page_size` (max 100). Item: `{ "profile": {...}, "subscription": {...} | null, "family": {...} | null, "progettista": {codice} | null }` — per i figli `family = {type:'child', status, parent_email}` e `subscription` è quella ereditata (`inherited: true`); per i titolari `family = {type:'parent', members_count}`.

### `PATCH /admin/users/{user_id}`
Body (opzionali): `role` (`admin`|`cliente`|`progettista`), `is_active` (bool). La promozione a progettista passa da `fn_promote_progettista` (assegna il codice `PRG-…`, riusandolo alla ri-promozione, e finisce in audit_log); la demozione cambia solo il ruolo (la riga `progettisti` e il codice restano). Protezioni: un admin non può togliersi il ruolo (verso **qualunque** ruolo) né disattivarsi da solo (`400`).

### `POST /admin/users/{user_id}/subscription`
Cambio piano forzato per un utente. Body: `{"plan_id": 2}`. `403` sui figli di famiglia (pending/attivi): il piano si gestisce sull'account titolare; forzare il piano di un titolare applica le stesse retrocessioni automatiche del cambio normale. **Scavalca il guard `su_richiesta`** (`self_serve=False`): assegnare da qui un piano su richiesta è il completamento manuale di quel flusso.

### `GET /admin/plans`
Tutti i piani, inclusi i disattivati.

### `POST /admin/plans` (201)
Crea un piano. Body: `nome`, `slug` (`[a-z0-9-]+`, unico → `409` se duplicato), `descrizione?`, `prezzo_annuale`, `tipo_prezzo?` (`importo`/`gratis`/`su_richiesta`, default `importo`), `etichetta_prezzo?` (≤100, usata solo con `su_richiesta`), `ai_check`, `alert_attivo`, `alert_giorni_preavviso` (obbligatorio se `alert_attivo=true`), `num_account_aziendali`, `ordering`, `is_active`.

### `PATCH /admin/plans/{plan_id}`
Aggiornamento parziale (stessi campi, tranne `slug`). I piani **non si eliminano** (lo storico abbonamenti li referenzia): si disattivano con `is_active=false`, che li nasconde dalla registrazione e dal cambio piano.

### `GET /admin/addons` · `POST /admin/addons` (201) · `PATCH /admin/addons/{addon_id}`
Gestione del catalogo add-on, gemella di `/admin/plans` (stessi permessi admin): GET tutti (anche disattivati), POST crea (`nome`, `slug` — unico, immutabile, `[a-z0-9-]+` → `409` se duplicato —, `descrizione?`, `prezzo ≥ 0` in €, `tipo_prezzo?`/`etichetta_prezzo?` come per i piani, `ordering`, `is_active`), PATCH aggiorna i campi passati (slug escluso) o disattiva. Gli add-on **non si eliminano**: si disattivano.

### `POST /admin/alerts/run` · `GET /admin/alerts/runs?limit=`
Esegue subito la run giornaliera degli alert (senza `ripeti`: `409` se quella di oggi è già stata eseguita; `ripeti=true` riesegue — il ledger impedisce comunque i doppi invii) e ritorna i contatori `{giorno, esito, bandi_candidati, destinatari, email_inviate, email_fallite, dettagli}`. `GET /runs` = registro delle esecuzioni (osservabilità).
