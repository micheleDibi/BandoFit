# API Backend

Base URL: `http://localhost:8000/api/v1` (sviluppo). Documentazione interattiva: `http://localhost:8000/docs` (Swagger UI).

**Autenticazione**: header `Authorization: Bearer <access_token>` (JWT emesso da Supabase Auth del progetto primario). Il backend verifica firma (ES256/RS256 via JWKS, fallback HS256 legacy), `aud` e `iss`, poi carica il profilo: un account con `is_active=false` riceve `403`. Gli endpoint `/admin/*` richiedono `role='admin'`.

**Azienda attiva** (header opzionale `X-Active-Company: <uuid>`): seleziona su quale azienda operano gli endpoint scopati per azienda. **Gruppo B** (dati dell'azienda, 1:1): `GET /me/company`, `GET /me/company/facets`, `GET /me/company/dossier`, `GET /me/company/export/pdf`, `GET /me/company/dossier/pdf`, `GET /me/ai-checks` e `GET /me/ai-checks/{id}`, badge di compatibilità su `GET /bandi`. **Gruppo A** (dati per-utente, solo per gli **Advisor** multi-azienda): `/me/saved-bandi`, `/me/calendar`, `/me/preferences` — per un Advisor le righe sono segregate per azienda, per tutti gli altri restano legate al solo utente (`company_profile_id NULL`), **comportamento identico a prima**. L'header è ri-autorizzato a ogni richiesta: l'azienda deve appartenere all'utente ed essere viva (non cancellata né archiviata), altrimenti `404 not_found`; un valore non-UUID è anch'esso `404`. **Per un membro ATTIVO della famiglia** (0031) l'insieme utile è la sua **visibilità ∩ aziende vive** dell'owner: header fuori insieme → `404` (anche se l'azienda esiste ed è del titolare — non se ne rivela l'esistenza); senza header il default è l'**azienda di appartenenza** (fallback: la più vecchia visibile; nessuna visibile → nessuna azienda). **Senza l'header** (per i titolari) si usa l'azienda viva più vecchia dell'utente — che per gli abbonamenti non-Advisor è l'unica, quindi il comportamento è identico a quando l'header non esisteva. La quota AI-check resta un **pool unico** condiviso (non cambia con l'azienda attiva): cambia solo lo storico mostrato.

Lato **frontend** l'header è iniettato da un `ActiveCompanyProvider` (id in `localStorage`); al cambio azienda la cache delle query viene **svuotata** (`queryClient.clear()`) — nessun dato dell'azienda precedente sopravvive. Lo switcher e la pagina `/app/aziende` compaiono solo per gli Advisor (`me.max_aziende > 1`).

**Formato errori** (uniforme):
```json
{ "error": { "code": "not_found", "message": "Bando non trovato" } }
```
Codici: `unauthorized` (401), `forbidden` (403), `not_found` (404), `bad_request` (400), `conflict` (409), `rate_limited` (429, troppe richieste su un endpoint auth pubblico), `validation_error` (422), `auth_unavailable` (503, verifica token temporaneamente impossibile — es. JWKS irraggiungibile: è un errore transitorio, **non** una sessione scaduta), `search_timeout` (504), `upstream_error` (502), `upstream_timeout` (504). Modulo pagamenti: `payment_required` (409, il cambio richiesto passa dal checkout), `payments_not_configured` (503, chiave Revolut assente), `payment_provider_error` (502), `payment_provider_timeout` (504, esito ignoto: **mai** ripetere il pagamento, lo stato viene riconciliato).

Nota: se un utente autenticato risulta privo di profilo (provisioning fallito a monte), il backend lo crea al volo alla prima richiesta (con abbonamento Gratuito), evitando che l'account resti bloccato.

**Paginazione** (risposta uniforme per gli elenchi):
```json
{ "items": [...], "total": 137, "page": 1, "page_size": 20, "total_pages": 7 }
```

## Endpoint pubblici

### `GET /health`
Stato del servizio. → `{"status": "ok"}`

> **Link di dominio**: tutti i link nelle email (conferma, recovery, inviti) sono token **propri** di BandoFit (256 bit, salvati solo come SHA-256 in `auth_tokens`, monouso, con scadenza) e puntano al dominio dell'app. GoTrue non genera MAI link né invia email: Supabase è solo il deposito di utenti e dati (Admin API `create_user`/`update_user_by_id`).

### `POST /auth/register` (202)
Avvia la registrazione. Body: `email`, `nome`, `cognome`, `azienda?`, `telefono` (obbligatorio; normalizzato e validato in **E.164** dal validator Pydantic — «347 1234567» → `+393471234567`, prefisso `+39` di default, lo zero dei fissi si conserva), `job_position_slug` (obbligatorio, dalla lookup `GET /job-positions`), `job_position_altro?` (testo libero, tenuto solo se la posizione è «Altro»), `plan_slug`. Telefono e posizione viaggiano nello `user_metadata` e li scrive il trigger `handle_new_user` (0022).

Risposta **sempre neutra** `{"ok": true}` (anti-enumerazione, CWE-204): identica per un indirizzo libero e per uno già registrato. Se l'indirizzo è libero, l'utente viene creato via Admin API (non confermato, **senza password**) e parte l'email con `/conferma-email?token=...`; se è già registrato non si crea nulla e all'indirizzo parte un avviso fuori banda («hai già un account» se confermato, «completa la registrazione» se in attesa). L'esistenza dell'account la scopre solo chi possiede la casella.

> **Niente password nel body.** La password si sceglie confermando l'indirizzo (`POST /auth/confirm`), come nel flusso invito. **Non è una scelta di UX ma il fulcro della difesa**: creando l'utente con la password fornita da chi registra, lo stato dell'account diventerebbe osservabile dall'esterno anche a risposta neutra — Supabase Auth è raggiungibile dal browser con la anon key. Creare l'utente **senza password** rende i due casi indistinguibili anche da lì. Non reintrodurre il campo.

Errori (nessuno dipende dall'esistenza dell'indirizzo): `429` rate limit per IP (5/15min, 50/24h — vedi sotto), `400` se `plan_slug` punta a un piano `su_richiesta`, `400` se `job_position_slug` è ignoto o disattivato, `422` telefono/email non validi. Il **cooldown 60s non è più un `409`**: sopprime l'invio e risponde `202` come sempre, altrimenti reintrodurrebbe una risposta distinguibile. **Nessun `502`**, nemmeno con Supabase Auth irraggiungibile: solo uno dei due rami chiama `create_user`, quindi un errore upstream sarebbe di per sé un segnale. Il prezzo è che durante un guasto la registrazione fallisce in silenzio: il segnale operativo è un `logger.error`, che va monitorato.

**Rate limiting** (migration 0025, tabella `auth_rate_limits` + `fn_consume_auth_rate_limit`): contatori a finestra nel DB — durable e condivisi, a differenza del cooldown in-process che si azzera a ogni deploy. Chiavi HMAC (`rate_limit_pepper`): a DB non finiscono mai IP o email in chiaro. Per IP **5/15min** e **50/24h** (`429`); per email **5/h**, che sopprime solo l'invio e **mai** la creazione dell'account (bloccarla permetterebbe a un anonimo di impedire l'iscrizione a una persona precisa, in silenzio) — con un'eccezione: **l'unica email di un account appena creato parte sempre**, budget o no, altrimenti un paio di fallimenti transitori di `create_user` basterebbero a lasciare l'account senza password, senza token e senza email. Cap globale **200/h di solo allarme**: se rifiutasse, un singolo IP spegnerebbe la registrazione a tutti. Se l'RPC non risponde si passa comunque (fail-open) e resta un log di errore. L'IP arriva da `CF-Connecting-IP`, o da `X-Forwarded-For` contato **da destra** per `trusted_proxy_hops` (default 2: Cloudflare + nginx) — vedi `docs/deploy.md`.

La risposta dura almeno `register_latency_target_seconds` (1,5s): senza, il tempo direbbe ciò che il corpo tace. Il `429` è escluso dal livellamento, altrimenti chi martella terrebbe aperte le connessioni.

### `POST /auth/confirm`
Body: `{"token": "...", "password": "..."}` (token dal link email, monouso, TTL 48h). Conferma l'indirizzo **e imposta la password scelta ora**, poi sblocca il login. → `{"email": "..."}` (per il prefill di `/login?email=`). `404` se non valido/scaduto/già usato, `400` password rifiutata. Il token si consuma **solo a conferma riuscita**: una password rifiutata non brucia il link.

### `POST /auth/recover` (202)
Richiesta reimpostazione password. Body: `{"email": "..."}`. Risposta **sempre neutra** `{"ok": true}` (anti-enumerazione); se l'account esiste parte l'email con `/reimposta-password?token=...` (TTL 1h). `409` cooldown 60s (non correla con l'esistenza: il contatore si arma prima di sapere se l'indirizzo è registrato), `429` rate limit per IP.

Ha le **stesse difese di `/auth/register`**, e non per simmetria estetica: il corpo era già neutro, ma il ramo «l'account esiste» emetteva un token e aspettava l'SMTP in linea, mentre quello «non esiste» tornava subito — il cronometro rivelava tutto, ed era l'oracolo più comodo dei due perché qui non serve nemmeno creare niente. Quindi: invio in **background**, **pavimento di latenza**, **rate limit per IP** (contatori in comune con register: chi alterna gli endpoint non trova budget fresco) e **budget per casella**, che è anche l'argine alle molestie — senza, chiunque poteva far piovere email di recupero su una vittima.

### `POST /auth/reset`
Body: `{"token": "...", "password": "..."}`. Consuma il token di recovery e imposta la nuova password via Admin API. → `{"email"}` (il frontend fa auto-login). `404` token non valido.

### `POST /auth/resend-confirmation` (202)
Nuovo token + email di conferma per un utente non ancora confermato. Risposta sempre neutra; cooldown 60s; stesse difese di `/auth/recover` (background, pavimento, rate limit per IP e per casella).

### `GET /auth/invite-info?token=...`
Contesto dell'invito azienda per la pagina di accettazione (**non consuma** il token): `{email, denominazione, parent_display_name}`. `404` se non valido.

### `POST /auth/accept-invite`
Body: `{"token", "password"}`. Consuma il token d'invito (TTL 48h): imposta la password, conferma l'email e attiva la membership. → `{"email"}` (auto-login del frontend).

### `GET /plans`
Piani di abbonamento attivi, ordinati per `ordering`. Usato dalla landing e dalla pagina Abbonamento (e da `Register.tsx` per qualificare l'intento `?piano=`: il piano non si sceglie più alla registrazione).

```json
[{ "id": 1, "nome": "Gratuito", "slug": "gratuito", "descrizione": "...", "prezzo_annuale": "0.00",
   "tipo_prezzo": "gratis", "etichetta_prezzo": null,
   "ai_check": 0, "alert_attivo": false, "alert_giorni_preavviso": null,
   "num_account_aziendali": 1, "max_aziende": 1, "features_override": null, "ordering": 1, "is_active": true, "updated_at": "..." }]
```
`num_account_aziendali` (posti persona della famiglia) e `max_aziende` (numero di aziende gestibili, >1 per l'Advisor) sono **assi distinti**. `features_override` (lista di stringhe o `null`, migration 0029): se valorizzato sostituisce le tre bullet standard della card (usato dal piano `tailored`).

`tipo_prezzo` (`importo`/`gratis`/`su_richiesta`) decide come la UI mostra il prezzo; con `su_richiesta` il piano **non è selezionabile alla registrazione** (`POST /auth/register` risponde `400` se lo slug punta a un piano su richiesta) né attivabile con il cambio piano self-serve — l'`etichetta_prezzo` sostituisce l'importo (fallback UI «Su richiesta»).

### `GET /job-positions`
Posizioni aziendali **attive**, ordinate per `ordering`: `[{id, nome, slug}]`. Pubblico come `GET /plans` (serve al form di registrazione, non autenticato). Lo `slug` è l'identificativo stabile che il form invia a `POST /auth/register`; il catalogo (migration 0022) è soft-disable e si amministra via SQL.

## Endpoint autenticati

### `GET /addons`
Catalogo **add-on attivi**, ordinati per `ordering`: `[{id, nome, slug, descrizione, prezzo, tipo_prezzo, tipo_fruizione, risorsa, etichetta_prezzo, ordering, is_active, updated_at, acquistabile, motivo_non_acquistabile}]` — `tipo_fruizione` (`consumabile`/`permanente`, migration 0028) dice se l'add-on si consuma a quantità o è un possesso binario; `risorsa` (`seats`/`companies`/null, migration 0030) è l'aggancio al motore entitlement. **`acquistabile`** è calcolata per l'UTENTE della richiesta (solo `tipo_prezzo='importo'`): `false` con motivo `solo_titolare` (collegato attivo: il checkout gli risponderebbe 403) o `piano_non_idoneo` (add-on allocativo su un piano la cui base non abilita la risorsa — l'extra sarebbe dormiente); è il segnale per la CTA, **il gate vero resta nel checkout** (`409` sugli allocativi non idonei). Lo `slug` è l'identificativo STABILE a cui sono agganciate le funzionalità. A differenza di `GET /plans` (pubblico) la rotta è **autenticata**: il catalogo si vede solo dentro l'app. Gli add-on **a pagamento** (`tipo_prezzo='importo'`, prezzo > 0) si comprano dal **checkout** (`POST /me/checkout` con `addon_slug`; il bottone «Acquista» porta a `/app/checkout?addon=`): l'acquisto pagato accredita un'**unità** nell'inventario add-on (`user_addon_inventory` + `addon_ledger`, migration 0028; vedi `GET /me/addons`). Per gli add-on `gratis` la CTA «Attiva» resta sullo stub `purchaseAddon` (dialog «In arrivo»); per i `su_richiesta` la CTA è «Richiedi una consulenza» via lo stub `requestConsultation` — il checkout li rifiuta comunque lato server (`400`). **Eccezione: `consulto-esperto`** — è l'aggancio del flusso consulenze (`POST /me/consulenze`) e si attiva dalla pagina del bando dopo un AI-check, non dalla card in Abbonamento; se a listino è consumabile a pagamento, la richiesta consuma un'unità in inventario (vedi `POST /me/consulenze`).

### `GET /me`
Profilo dell'utente corrente + abbonamento attivo con il piano.
```json
{ "profile": { "id": "uuid", "email": "...", "nome": "...", "cognome": "...", "azienda": null,
               "telefono": null, "job_position_id": 3,
               "job_position": { "id": 3, "nome": "CTO / Direttore Tecnico", "slug": "cto" },
               "job_position_altro": null,
               "role": "cliente", "is_active": true, "created_at": "..." },
  "subscription": { "id": "uuid", "status": "active", "data_inizio": "2026-07-03",
                    "data_scadenza": "2027-07-03", "plan": { ...come /plans... } },
  "max_aziende": 1 }
```
`max_aziende` è il limite **effettivo** di aziende gestibili (override utente > piano > 1): >1 identifica un Advisor multi-azienda, e il frontend lo usa per mostrare lo switcher azienda. Il piano espone anche `plan.max_aziende` (default del piano, prima dell'override).
`job_position` è presente anche se la voce è stata **disattivata** nel frattempo (catalogo soft-disable): chi l'aveva scelta continua a vederla.
`role` ∈ `admin`/`cliente`/`progettista`. Per i progettisti — e per gli admin che hanno già un codice — la risposta include anche `progettista: {codice}` (il codice `PRG-00001`, assegnato dal sistema alla promozione, o alla prima proposta per gli admin, e immutabile); il progettista conserva tutte le funzionalità cliente.

### `PATCH /me`
Aggiorna l'anagrafica. Body (tutti opzionali): `nome`, `cognome`, `azienda`, `telefono`, `job_position_id`, `job_position_altro`. → come `GET /me`. Regole: il telefono è validato in E.164 **solo se la chiave è presente** (il client la omette quando il campo non cambia, così i valori pre-0022 a testo libero non bloccano il salvataggio degli altri campi; stringa vuota = azzeramento); `job_position_id` deve esistere ed essere **attivo** (`400` altrimenti — la FK da sola non intercetterebbe le voci disattivate), `null` azzera; `job_position_altro` sopravvive solo se la posizione della riga è «Altro» — lo impone il trigger `trg_profiles_job_position_altro` a livello di riga (race-free anche con PATCH concorrenti).

### `POST /me/verify-cf`
Verifica il **codice fiscale personale** all'Anagrafe Tributaria via openapi.it (**a pagamento**, ~0,05 € + IVA). Body: `{ "codice_fiscale": "..." }`. La validazione strutturale (checksum, omocodia inclusa) è locale e gratuita: gli input malformati non generano spesa. Idempotente: lo stesso CF già verificato risponde senza nuova chiamata. Protetta da lock per utente (doppio click/tab concorrenti) e cooldown tra tentativi a pagamento.
Esiti: `200 {codice_fiscale, cf_verified_at}`; `400 cf_invalid` (malformato) / `400 cf_not_valid` (formalmente corretto ma non registrato — salvato non verificato SOLO se non c'è già un CF verificato: una verifica fallita non cancella mai un dato buono); `409 verify_in_progress`; `429 verify_cooldown`; `502 openapi_error`; `503 openapi_not_configured`; `504 openapi_timeout` (esito ignoto, nessun retry automatico). Il profilo (`GET /me`, `PATCH /me`) espone/accetta anche `codice_fiscale` (il cambio del CF azzera la verifica).

### `POST /me/subscription`
Cambio piano **self-serve verso piani gratuiti**: dal modulo pagamenti i piani a pagamento non si attivano più da qui. Body: `{"plan_id": 3}`. Tre esiti a seconda della destinazione:
- **piano a pagamento** (`tipo_prezzo='importo'`, prezzo > 0) → **`409 payment_required`**: il piano si attiva solo con un acquisto — il frontend intercetta il codice e porta al checkout (`/app/checkout?piano=<slug>`, vedi `POST /me/checkout`);
- **piano gratuito, ma il piano corrente è A PAGAMENTO e non scaduto** → il cambio **non è immediato**: diventa un **downgrade programmato alla scadenza** (riga in `scheduled_plan_changes`, motivo `disdetta`, sostituisce un eventuale cambio già programmato) — chi ha pagato non perde il periodo residuo. Risposta come `GET /me` col piano ancora invariato: il cambio è un'intenzione, la applica lo scheduler a scadenza;
- **piano gratuito da piano gratuito (o scaduto)** → cambio immediato come sempre: l'abbonamento attivo passa a `cancelled` e ne viene creato uno nuovo annuale.

→ come `GET /me`; se il downgrade ha retrocesso membri della famiglia, la risposta include `plan_switch_adjustment: {demoted, revoked_pending}`. **Downgrade da Advisor**: le aziende oltre il nuovo `max_aziende` (le più recenti) vengono **archiviate** (sola lettura, escluse da switch/alert/export; i dati restano) e riattivate risalendo di piano — nessuna cancellazione. Errori: `409 payment_required` (sopra); `400` piano inesistente/non attivo; `400` piano `su_richiesta` («disponibile solo su richiesta», il guard scatta PRIMA della RPC — l'assegnazione resta possibile via `POST /admin/users/{id}/subscription`); `403 child_plan_locked` se l'utente è un figlio attivo (il piano si gestisce sul titolare).

### `GET /me/addons`
**Inventario add-on** dell'utente (gate `CurrentUser`): le unità possedute per add-on — **dalla 0030 incluse le voci a quantità 0** (un consumabile esaurito resta visibile in «I miei addon»). → `[{addon_id, slug, nome, descrizione, tipo_fruizione, risorsa, quantita, acquistate, consumate, updated_at}]`: `acquistate` = accrediti totali dal ledger (acquisti + grant + rimborsi), `consumate` = SOLI consumi (le revoche admin riducono `quantita` senza contare come consumo). Il saldo è la cache materializzata del ledger (migration 0028).

### `GET /me/addons/ledger?addon_id=&limit=`
**Storico movimenti** dell'utente (acquisti, consumi, accrediti, revoche), più recenti prima (`limit` 1–100, default 20; `addon_id` opzionale per filtrare un solo add-on). → `[{tipo: purchase|admin_grant|consume|refund|admin_revoke, delta, note, created_at}]`.

### `GET /me/aziende/visibili`
Le aziende su cui l'utente può **operare**, per lo switcher (0031): per un membro ATTIVO la sua visibilità ∩ vive (`attiva` = il default del resolver: appartenenza, o la più vecchia visibile); per titolari/pending/retrocessi le proprie vive. → `[{id, ragione_sociale, partita_iva, created_at, attiva}]`. A differenza di `GET /me/aziende` non è ParentUser-gated.

### `PATCH /me/family/members/{membership_id}` (0031)
Modifica di un membro da parte del titolare — applica SOLO i campi presenti nel body: `company_profile_id` (appartenenza: azienda VIVA dell'owner; non azzerabile), `aziende_visibili` (uuid[], DEVE includere l'appartenenza — `400 membership_access_required`; la visibilità di aziende non in lista viene revocata), `ai_check_budget` (null esplicito = illimitato; N ≥ 0 = tetto per ciclo). Ogni campo passa dalla sua RPC atomica (lock sul padre + audit `family.member_company_changed`/`member_access_changed`/`member_budget_changed`). → `FamilyOut` aggiornata. Errori: `404` membro/azienda; `400` visibilità senza appartenenza.

### `GET /me/entitlements`
**Le quote dell'account in un'unica risposta** (migration 0030) — la fonte da cui il frontend legge i limiti, senza mai ricalcolarli: `{editable, seats, companies, ai_checks}` dove ogni risorsa è `{base, extra, effettivo, usato, residuo}` (per `ai_checks` anche `periodo_inizio`/`periodo_fine`, la finestra del ciclo attivo). `base` viene dal piano attivo del titolare (per `companies` include l'eventuale override admin), `extra` dalle unità di **addon allocativi** possedute (`addons.risorsa`), `effettivo` dalla formula unica SQL (`fn_entitlement_detail`, dormienza inclusa: l'extra conta solo se la base abilita la capability). Per un **collegato attivo** lo snapshot è quello del **titolare** (pool condiviso) con `editable: false`; titolari, pending e retrocessi vedono il proprio. Gli stessi numeri alimentano `family.limit` di `GET /me`, `GET /me/family` e la quota di `GET /me/ai-checks`: nessuna divergenza possibile tra display e arbitri (che applicano lo stesso effettivo via `fn_family_limit`/`fn_effective_max_aziende`).

## Anagrafica di fatturazione (modulo pagamenti)

Anagrafica del soggetto a cui sono intestate le fatture (migration 0026). È lo stato **corrente editabile**: le fatture citano sempre lo snapshot congelato in `purchases.billing_snapshot` al momento dell'acquisto, mai questa anagrafica. Il gate è **`require_billing_account`** (vale per TUTTI gli endpoint di pagamento sotto `/me/checkout*`, `/me/purchases*`, `/me/subscription/*`, `/me/payment-method`): `403 forbidden` SOLO per i **collegati attivi** di una famiglia (ereditano il piano — il piano e i pagamenti si gestiscono sull'account titolare, stessa regola di `child_plan_locked`); i membri `pending` e `demoted` sono account indipendenti con piano proprio e possono operare (`require_parent` li escluderebbe a torto).

### `GET /me/billing-profile`
L'anagrafica corrente, o `null` se mai compilata: `{tipo_soggetto, denominazione, nome, cognome, partita_iva, codice_fiscale, paese, indirizzo, comune, provincia, cap, vies_valid, vies_checked_at, completo}`. (I profili salvati coi vecchi tipi vengono rimappati dalla 0029; il backend tollera comunque i valori storici.)

### `GET /me/billing-profile/prefill`
Proposta di **precompilazione** dai dati dell'azienda (la più vecchia non cancellata, come `GET /me/company`): `{tipo_soggetto, denominazione, partita_iva, codice_fiscale, indirizzo, comune, provincia, cap}` — tutti opzionali; `tipo_soggetto` proposto `azienda` se l'azienda ha la P.IVA. **Mai persistita da sola**: nulla viene salvato finché l'utente non conferma col PUT. Senza aziende → tutti i campi `null`.

### `PUT /me/billing-profile`
Upsert dell'anagrafica (risposta come la GET). Body: `tipo_soggetto` (`azienda`/`privato`) + i campi sopra. Il **paese è qualunque** (ISO alpha-2) per entrambi i tipi. La validazione **dipende dal tipo e dal paese** (i vincoli di forma rispondono `422 validation_error`):
- **`azienda`** — `denominazione` obbligatoria + partita IVA: 11 cifre se paese `IT`, forma VIES (2–12 alfanumerici, il prefisso paese digitato si toglie) se paese UE, libera (≥2) per l'extra-UE.
- **`privato`** — `nome`+`cognome`; codice fiscale di 16 caratteri **solo** con paese `IT` (i privati esteri non lo forniscono).
- **Comuni** — `indirizzo`/`comune`/`cap` obbligatori; per l'Italia CAP a 5 cifre e provincia obbligatoria, per l'estero liberi.

**VIES (non bloccante).** Per le aziende con paese **UE ≠ HR** (l'Italia inclusa: il venditore è croato) la P.IVA passa dal VIES (openapi.it, scope EU-start; il prefisso della Grecia è `EL`, a DB resta `GR`) al salvataggio, con timeout dedicato di 8s. **Il salvataggio riesce sempre**: l'esito seleziona solo l'aliquota, non è un requisito. `vies_valid`: `true` = reverse charge 0% provato; `false` = P.IVA verificata e non valida (IVA 25%); `null` = verifica non riuscita (VIES giù/timeout, o openapi non configurato) → IVA 25%, si ritenta ri-salvando. Ogni salvataggio azzera i campi VIES e li ricalcola. Errori: `403 forbidden` (collegato attivo), `422 validation_error` (vincoli di forma). Il VIES **non** produce più `400`/`502`/`503`/`504`: un suo guasto non blocca il salvataggio.

## Checkout e acquisti (modulo pagamenti)

Acquisto self-serve di piani (upgrade) e add-on via **Revolut Merchant API**: il backend crea un **purchase immutabile** + un **ordine** sul provider, il frontend apre il widget Revolut col token dell'ordine (i dati carta vivono SOLO nel popup del provider), l'esito arriva dal **webhook** (o dal `/sync` di fallback) e viene applicato **rileggendo sempre l'ordine dal provider** con le RPC atomiche della 0026. Tutti gli endpoint: gate `require_billing_account` (403 per i collegati attivi) e `503 payments_not_configured` se la chiave Revolut manca.

### `POST /me/checkout/preview`
Preventivo **puro** (nessun effetto). Body: `{"plan_slug": "..."}` **oppure** `{"addon_slug": "..."}` (uno e uno solo, `422` altrimenti) + `quantita` opzionale (1..100, default 1 — **solo addon**: con `plan_slug` una quantità ≠ 1 è `422`; su un addon `permanente` è `400`). → `{kind, oggetto_slug, oggetto_nome, quantita, listino_cents, credito_cents, imponibile_cents, iva_cents, iva_aliquota, natura_iva, totale_cents, valuta, scadenza_risultante, dettaglio}` — importi in **centesimi**. Regole (in `services/pricing.py`, solo `Decimal` con `ROUND_HALF_UP`): i piani si comprano **solo in upgrade** (ordering superiore al corrente; per scendere c'è il downgrade programmato) e l'imponibile è `prezzo_nuovo − credito_residuo` con credito `min(prezzo_vecchio × giorni_residui/365, prezzo_vecchio)` (formula congelata in `dettaglio`); listino **IVA esclusa**, **IVA 25%** (venditore croato ADVENTUS CONSULTING j.d.o.o.) aggiunta sull'imponibile — **0% reverse charge** (natura `RC-UE`) SOLO per `tipo_soggetto='azienda'` con paese UE ≠ HR e `vies_valid=true` a DB; in ogni altro caso (HR domestica, UE senza VIES valido, extra-UE, privati, anagrafica assente) IVA 25%. Il VIES **non** si interroga al checkout: si legge l'esito persistito nel profilo. Gli addon si comprano a listino pieno (solo `tipo_prezzo='importo'` con prezzo > 0); con `quantita` N: `listino_cents` resta il prezzo **unitario**, `imponibile/totale` sono già moltiplicati (×N esatto in centesimi, IVA sul totale) e il dettaglio congela `quantita` + `prezzo_unitario_cents`. Errori: `404` piano/addon non a catalogo; `400` non acquistabile online (gratuito o `su_richiesta`), piano non superiore al corrente, importo dell'upgrade nullo; `409` add-on **allocativo** su un piano la cui base non abilita la risorsa (eleggibilità derivata 0030: seats richiede `num_account_aziendali > 1`, companies `coalesce(override, max_aziende) > 1` — vale per qualunque piano, anche creato da console).

### `POST /me/checkout`
Crea il purchase e l'ordine di pagamento. Body: come la preview (inclusa `quantita`, persistita su `purchases.quantita`: a pagamento riuscito `fn_complete_purchase` accredita N unità in inventario) + `auto_renew` (bool, default `true`; solo per i piani, ignorato sugli addon — la scelta viene congelata sul purchase e applicata a pagamento riuscito). Richiede l'**anagrafica di fatturazione compilata** (`400` altrimenti: viene congelata in `billing_snapshot`). Crea la riga `purchases` `in_attesa` (importi e calcolo immutabili) e l'ordine Revolut (scadenza 1h; se la creazione dell'ordine fallisce il purchase viene annullato, non resta a bloccare l'utente). → `{purchase_id, revolut_order_token, checkout_url, totale_cents, valuta}`: il frontend apre il widget col `revolut_order_token`. Un ordine declinato riaccetta tentativi con lo **stesso token**; un ordine pagato rifiuta altri pagamenti (guardia anti doppio addebito del provider). Errori: `409 conflict` se esiste già un purchase `in_attesa` (uno solo per utente); `400`/`404` come la preview; `502`/`504` provider irraggiungibile / esito ignoto (**mai ripetere il pagamento**: si riconcilia).

### `GET /me/purchases?page=&page_size=`
Storico acquisti dell'utente, più recenti prima (`page_size` max 100). Item `PurchaseOut`: `{id, kind: piano|rinnovo|addon|cambio_admin|addon_admin, status: in_attesa|pagato|fallito|scaduto|annullato|gratuito, oggetto_slug, oggetto_nome, descrizione, quantita (unità dell'oggetto, >1 solo per gli addon — 0030; la descrizione porta già il «× N»), imponibile_cents, iva_cents, totale_cents, iva_aliquota, natura_iva, valuta, decline_reason, motivazione (cambio_admin/addon_admin), created_at, paid_at}` — `addon_admin` è l'accredito add-on gratuito da amministratore (riga a 0 €, migration 0028).

### `GET /me/purchases/{id}`
Singolo acquisto (`404` se non dell'utente).

### `POST /me/purchases/{id}/sync`
**Riconciliazione on-demand** (usata dalla pagina esito del checkout quando il webhook tarda o si perde): se il purchase è `in_attesa` con un ordine, rilegge l'ordine dal provider e fa avanzare lo stato con le stesse RPC idempotenti del webhook — chiamarla più volte è sicuro. → `PurchaseOut` aggiornato. `404` se non dell'utente.

## Gestione abbonamento (rinnovo, disdetta, metodo di pagamento)

Le **intenzioni** sull'abbonamento (cambio programmato, rinnovo automatico) e il metodo di pagamento salvato nel vault del provider (a DB solo l'id del metodo, mai dati carta). Stesso gate `require_billing_account`.

### `GET /me/subscription/management`
→ `{auto_renew, data_scadenza, metodo: {presente, label}, cambio_programmato: {to_plan_slug, to_plan_nome, effective_date, motivo} | null}` — `label` è il solo suffisso mascherato della carta (es. `•••• 4242`).

### `POST /me/subscription/downgrade`
Programma il passaggio a un piano **inferiore** alla scadenza (verso `gratuito` = **disdetta**): l'utente resta sul piano attuale fino a quel giorno. Body: `{"plan_slug": "..."}`. Sostituisce un eventuale cambio già programmato (uno solo per utente). → come la GET. Errori: `400` se la destinazione non è inferiore per `ordering` («per salire usa l'acquisto»); `404` piano non disponibile / nessun abbonamento attivo; `409` abbonamento già scaduto.

### `DELETE /me/subscription/scheduled-change`
Annulla il cambio programmato. → come la GET. `404` se non c'è nulla da annullare.

### `POST /me/subscription/auto-renew`
Body: `{"enabled": bool}`. Attivarlo richiede un metodo di pagamento salvato → `409 conflict` altrimenti (il frontend apre il flusso di aggiunta e riprova). Spegnerlo **non** tocca `grace_until`: chi è in grazia ci resta fino alla fine. `404` senza abbonamento attivo.

### `POST /me/payment-method` · `DELETE /me/payment-method`
Il POST avvia il salvataggio di un metodo **senza acquisto**: ordine a 0 € con scopo `add_method` → `{revolut_order_token}` per il widget; il metodo viene persistito alla riconciliazione dell'ordine completato (webhook/sync), non alla risposta. Il DELETE revoca il metodo salvato **e spegne il rinnovo automatico** (senza toccare `grace_until`). → come la GET di management.

## Webhook di pagamento

### `POST /webhooks/revolut` *(pubblico, nessuna auth utente)*
Riceve gli eventi del provider (`ORDER_COMPLETED`/`ORDER_FAILED`/`ORDER_CANCELLED`/`ORDER_PAYMENT_DECLINED`/`ORDER_PAYMENT_FAILED`). La prova di autenticità è la **firma HMAC verificata** (header di firma + timestamp con tolleranza anti-replay di 5 minuti; firme multiple accettate durante la rotazione del secret). Risponde **subito `204`** e elabora in background: il payload è thin (`{event, order_id}`) e vale come *suggerimento* — lo stato vero si rilegge sempre dal provider (`elabora_ordine`, la stessa strada del `/sync`). Dedup su `webhook_events` (order-level: un evento per ordine, i retry si scartano; payment-level: sempre riprocessabili — le RPC a valle sono idempotenti). Risposte: `204` (anche per eventi non pertinenti), `401` firma/timestamp non validi, `400` payload non JSON, `503` se `REVOLUT_WEBHOOK_SECRET` non è configurato (il provider ritenterà), `500` registrazione evento fallita (idem).

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

## Aziende gestite (Advisor multi-azienda)

Gestione dell'elenco delle aziende di un owner (piano **Advisor**: fino a `max_aziende`). **Owner-only**: un account collegato a una famiglia riceve `403` (in v1 Advisor e collegati sono mutuamente esclusivi). L'azienda su cui operano gli altri endpoint si sceglie con l'header `X-Active-Company` (vedi sopra); questi endpoint gestiscono l'insieme.

### `GET /me/aziende`
`{ aziende: [{id, ragione_sociale, partita_iva, created_at, attiva}], max_aziende, usate }` — solo le aziende **vive** (né cancellate né archiviate), dalla più vecchia; `attiva: true` sulla prima (quella che il resolver userebbe senza header). `max_aziende` è il limite effettivo, `usate` quante ne sono in uso (la UI disabilita «crea» quando `usate >= max_aziende`).

### `POST /me/aziende` (201)
Crea una nuova azienda. Body: `{ "ragione_sociale": "...", "partita_iva": "01234567890" }` (entrambi obbligatori subito: niente azienda vuota). → `CompanySummary`. Errori: `409` limite del piano raggiunto (`company_limit_reached`, race-free), `400` P.IVA non valida / ragione sociale mancante. L'import IT-full e il resto dei campi sono azioni successive (`PUT /me/company` / import, con l'azienda come attiva).

### `DELETE /me/aziende/{id}` (204)
Soft-delete di un'azienda gestita: i dati restano (recuperabili), ma l'azienda esce da switch/alert/export. `404` se non è dell'owner o è già rimossa.

## Dati aziendali

### `GET /me/company`
`{ "editable": true|false, "company": {...} | null }` — dell'**azienda attiva** (header `X-Active-Company`; senza header è l'unica/più vecchia). Il titolare (o un utente singolo) vede e modifica i propri; un **figlio attivo** vede quelli della famiglia in sola lettura (`editable: false`). `company: null` se il titolare non ha ancora alcuna azienda.

### `PUT /me/company`
Upsert dei dati dell'**azienda attiva** (header `X-Active-Company`). Se l'owner non ha ancora alcuna azienda, è il **bootstrap** della prima. Bloccato (`403`) SOLO per i **figli attivi**, che ereditano i dati della famiglia; titolari, utenti singoli, pending e retrocessi scrivono i propri. Campi: `ragione_sociale`*, `forma_giuridica`, `partita_iva`* (11 cifre, prefisso IT tollerato), `codice_fiscale`, `ateco_id`/`settore_id`/`regione_id` (id delle lookup del DB secondario → il backend denormalizza `ateco_codice`, `settore_nome`, `regione_nome`; `400` se sconosciuti), **`beneficiari_ids`** (lista di id della lookup `beneficiari`, max 50, deduplicata → in risposta arriva `beneficiari: [{id, nome}]`; `400` se un id è sconosciuto), `anno_fondazione` (1800-2100), `indirizzo`, `comune`, `provincia`, `cap` (5 cifre), `classe_dimensionale` (`micro|piccola|media|grande`), `numero_dipendenti`, `fascia_fatturato` (`fino_100k|100k_500k|500k_2m|2m_10m|10m_50m|oltre_50m`), `pec`, `telefono`, `sito_web`.

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

### `GET /me/company/export/pdf`
PDF (`application/pdf`, `Content-Disposition: attachment`) della **scheda** dell'azienda attiva: i dati **dichiarati** (`GET /me/company`) più le preferenze di ricerca seguite. Non certificato. `404 not_found` se l'azienda non ha ancora un profilo. Titolare o figlio attivo (sola lettura). Se nessun motore PDF è disponibile sull'ambiente → `503 pdf_unavailable`.

### `GET /me/company/dossier/pdf`
PDF (`application/pdf`, download) del **dossier certificato** dell'azienda attiva (stesse 8 sezioni + persone di `GET /me/company/dossier`). Rendering **server-side**: il payload grezzo del provider non esce mai (si parte da `GET /me/company/dossier`, già ripulito). `404 not_found` se non c'è un dossier importato; `503 pdf_unavailable` se manca il motore PDF.

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

Preferiti **per utente** sul DB primario: RIFERIMENTI al catalogo (bando_id + snapshot di slug/titolo/scadenza/stato), non copie. Se il bando sparisce dal catalogo la riga resta e viene servita dallo snapshot con `disponibile: false`. Cap: 200 bandi salvati per utente. Per un **Advisor** i preferiti sono segregati per **azienda attiva** (header `X-Active-Company`); per gli altri restano legati al solo utente (comportamento invariato).

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

### `GET /me/notifications?page=&page_size=&company_id=`
Pagina di notifiche (`page_size` max 50) + **`non_lette`** complessive (il numero sul badge): `{ items: [{id, tipo, titolo, corpo, url, company_profile_id, read_at, created_at}], total, page, page_size, total_pages, non_lette }`. `company_profile_id` è l'azienda cui la notifica si riferisce (Advisor multi-azienda; `null` se generale). Il parametro **`company_id`** (opzionale) filtra gli item per azienda — è il filtro del centro alert `/app/notifiche` per gli Advisor — ma **`non_lette` resta aggregato** su tutte le aziende (il badge della campanella non filtra mai). Il frontend interroga la pagina 1 in polling (60s) per la campanella.

### `POST /me/notifications/read` (204)
Body: `{"all": true}` oppure `{"ids": [1, 2]}` (almeno uno dei due). Segna come lette solo le proprie non lette.

## Alert email sui nuovi bandi

Quando un bando diventa disponibile, gli utenti con azienda **compatibile** (pre-check con punteggio ≥ 60) lo ricevono in un'email **digest** giornaliera, con il ritardo del piano (`alert_ritardo_giorni`: Advisor 1 · Pro 7 · Smart 14 giorni dalla pubblicazione). Nessuna pre-schedulazione: l'idoneità si ricalcola a ogni run (piano corrente, opt-in, email verificata su `auth.users`, account attivo, bando ancora aperto); il ledger `(utente, azienda, bando)` garantisce che nessun bando venga inviato due volte per azienda. Il digest include per ogni bando: titolo, ente, importo, **scadenza evidenziata**, «perché lo vedi» (le dimensioni di compatibilità con i nomi) e il link; header `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058). Per un **Advisor** con più aziende idonee la run fa **fan-out per azienda**: una sola email con una **sezione per azienda** e una notifica in-app per azienda (con `company_profile_id`, filtrabile nel centro alert `/app/notifiche`); per tutti gli altri l'email e la notifica sono quelle aggregate di sempre.

### `GET /me/alert-settings` · `PUT /me/alert-settings`
`{abilitati, piano_include_alert, ritardo_giorni}` — il piano effettivo per i collegati attivi è quello del titolare. Il PUT (`{"abilitati": bool}`) è consentito anche se il piano non include gli alert (il gate vero è alla run). Stessa riga di verità del link di disiscrizione nelle email.

### `POST /alerts/unsubscribe?token=` · `GET /alerts/unsubscribe?token=` *(pubblici, nessuna auth)*
Disiscrizione **a un clic** (RFC 8058): il POST è idempotente e risponde **sempre allo stesso modo** (204; pagina HTML di conferma se la richiesta arriva da un form del browser) con token valido, ignoto o malformato — nessuna enumerazione possibile. Il GET mostra solo una pagina con un bottone di conferma e **non muta nulla**: gli scanner antispam pre-aprono i link GET delle email.

## Consulenze (lato cliente)

Flusso: AI-check completato → attivazione dell'addon `consulto-esperto` → richiesta nel pool dei progettisti → proposte → **accettazione = assegnazione definitiva 1:1** (+ prenotazione slot opzionale, contestuale o successiva). Le **azioni** (creare, accettare, rifiutare, annullare, prenotare) sono riservate al **titolare** dell'Azienda; gli account collegati leggono. Eventi: ogni transizione genera notifica in-app + email (vedi `docs/database.md`, audit incluso).

### `GET /me/consulenze` · `GET /me/consulenze/{id}`
Richieste dell'Azienda (visibilità per `family_parent_id`). Item: `{id, stato, bando_id, bando_slug, bando_titolo, esito, punteggio, created_at, assigned_at, editable, progettista, proposte_aperte, proposte, appuntamento}` — `stato` ∈ `nuova`/`assegnata`/`annullata`; `progettista = {codice, nome}` (assegnato; la UI mostra **nome e cognome** — più umano — e il codice resta nel payload per usi interni); `proposte` (solo nel dettaglio): `[{id, codice_progettista, nome_progettista, messaggio, stato, created_at}]` — anche qui il cliente vede l'autore per `nome_progettista`; `appuntamento = {id, inizio, fine, stato, videocall_url}` in UTC — `videocall_url` è la stanza Jitsi dedicata (`{JITSI_BASE_URL}/bandofit-{token}`, derivata dal token a DB; solo prenotazioni confermate). La notifica in-app dell'evento 2 resta minimizzata (solo il bando); il nome dell'autore compare nell'email, effimera.

### `POST /me/consulenze` (201) *(solo titolare)*
Body: `{"ai_check_id": "uuid"}` (un AI-check `ready` della propria Azienda). Crea la richiesta con gli snapshot (esito/punteggio, bando, addon+prezzo) e avvisa **tutti i progettisti e gli admin attivi** (evento 1; parità admin). **Gating a pagamento (migration 0028)** — CAMBIO DI COMPORTAMENTO: se l'addon `consulto-esperto` è **consumabile a pagamento** (`tipo_fruizione='consumabile'`, `tipo_prezzo='importo'`, prezzo > 0) la creazione **consuma 1 unità dall'inventario add-on** (comprata via `POST /me/checkout` con `addon_slug`, o accreditata da un admin); il consumo è **atomico all'insert** nella stessa transazione (`fn_create_consultation_request`) — senza unità la richiesta **non nasce**. Un pre-check di cortesia dà subito il `409 payment_required` quando il saldo è a zero, ma l'arbitro è la RPC (consumo atomico). Con l'addon `gratis` (seed attuale) il flusso resta senza pagamento. Errori: `403` account collegato; `404` AI-check non trovato / addon non a catalogo; `409` AI-check non completato / **richiesta già aperta per questo bando** (una sola `nuova` per bando per Azienda); **`409 payment_required`** se l'addon è consumabile a pagamento e non c'è un'unità in inventario (si passa dal checkout).

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
Elenco utenti con abbonamento attivo. Parametri: `q` (cerca in email/nome/cognome/azienda), `role` (`admin`|`cliente`|`progettista`), `page`, `page_size` (max 100). Item: `{ "profile": {...}, "subscription": {...} | null, "family": {...} | null, "progettista": {codice} | null, "azienda_nome": string | null }` — per i figli `family = {type:'child', status, parent_email}` e `subscription` è quella ereditata (`inherited: true`); per i titolari `family = {type:'parent', members_count}`. **`azienda_nome`** è la ragione sociale mostrata come azienda dell'utente: dal **dossier** (`company_profiles`, la più vecchia non cancellata) con fallback al testo libero `profiles.azienda`; per i **collegati attivi** è quella del titolare (stessa priorità di `parent_display_name`).

### `PATCH /admin/users/{user_id}`
Body (opzionali): `role` (`admin`|`cliente`|`progettista`), `is_active` (bool), `max_aziende_override` (intero ≥1, oppure `null` esplicito per rimuovere l'override e tornare al default di piano). La promozione a progettista passa da `fn_promote_progettista` (assegna il codice `PRG-…`, riusandolo alla ri-promozione, e finisce in audit_log); la demozione cambia solo il ruolo (la riga `progettisti` e il codice restano). Cambiare `max_aziende_override` **riconcilia** subito le aziende dell'utente (archivia le eccedenti se il limite scende, riattiva se risale). Protezioni: un admin non può togliersi il ruolo (verso **qualunque** ruolo) né disattivarsi da solo (`400`).

### `POST /admin/users/{user_id}/subscription`
Cambio piano **gratuito** forzato per un utente (nessun pagamento). Body: `{"plan_id": 2, "motivazione": "..."}` — la motivazione è **obbligatoria** (1–500 caratteri) e finisce nello storico: il cambio passa da `fn_registra_cambio_admin`, che registra in audit l'**attore vero** (l'admin) e crea un purchase `kind='cambio_admin'` / `status='gratuito'` a totale 0. Se l'utente ha un checkout in corso, l'**ordine sul provider viene cancellato PRIMA** dell'annullo a DB (così un pagamento tardivo su un purchase annullato non lascia soldi orfani); vengono annullati anche i cambi programmati. `403` sui figli attivi di famiglia: il piano si gestisce sull'account titolare; forzare il piano di un titolare applica le stesse retrocessioni automatiche del cambio normale. **Scavalca il guard `su_richiesta`** (`self_serve=False`): assegnare da qui un piano su richiesta è il completamento manuale di quel flusso. `404` utente inesistente.

### `GET /admin/users/{user_id}/addons`
Inventario add-on di un utente (gate `AdminUser`; stesso payload di `GET /me/addons`): serve al dialog «Assegna add-on» per mostrare cosa possiede già.

### `POST /admin/users/{user_id}/addons`
**Accredito gratuito** di N unità di un add-on (migration 0028). Body: `{"addon_id": 3, "quantita": 1, "motivazione": "..."}` — quantità **1–100**, **motivazione obbligatoria** (1–500). Passa da `fn_admin_grant_addon`: crea una riga `purchases` `kind='addon_admin'`/`status='gratuito'` a totale 0 (compare nello storico dell'utente, esclusa dai ricavi; dalla 0030 la quantità è persistita anche in `purchases.quantita`) e un movimento `admin_grant` nel ledger, con audit `addon.granted` e notifica in-app all'utente (best-effort). Dalla 0030 un add-on **allocativo** (`risorsa` valorizzata) non si accredita a un membro di famiglia ATTIVO (`400 addon_risorsa_solo_titolare`: l'extra conta sull'inventario del titolare) e la revoca di un allocativo **riconcilia subito** (B3: seats → retrocessioni, companies → archiviazioni). Gli add-on **permanenti** si concedono in unità singola e solo se non già posseduti. → `{purchase_id, quantita_residua}`. Errori: `400` motivazione mancante o quantità non valida; `404` utente/add-on inesistente; `409` add-on permanente già posseduto.

### `POST /admin/users/{user_id}/addons/{addon_id}/revoke`
**Revoca** unità add-on (`fn_admin_revoke_addon`). Body: `{"quantita": 1, "motivazione": "..."}` (stessi limiti del grant). Le unità sono **clampate al residuo** — mai quelle già consumate — con audit `addon.revoked`. → `{quantita_revocata, quantita_residua}`. Errori: `400` motivazione/quantità; `409 conflict` se non c'è nessuna unità da revocare.

### `GET /admin/plans`
Tutti i piani, inclusi i disattivati.

### `POST /admin/plans` (201)
Crea un piano. Body: `nome`, `slug` (`[a-z0-9-]+`, unico → `409` se duplicato), `descrizione?`, `prezzo_annuale`, `tipo_prezzo?` (`importo`/`gratis`/`su_richiesta`, default `importo`), `etichetta_prezzo?` (≤100, usata solo con `su_richiesta`), `ai_check`, `alert_attivo`, `alert_giorni_preavviso` (obbligatorio se `alert_attivo=true`), `num_account_aziendali` (posti persona), `max_aziende` (≥1, default 1: numero di aziende gestibili — asse distinto), `features_override?` (lista di stringhe, migration 0029: bullet custom della card; trim + via le vuote, max 8 voci × 120 char, `[]`→`null`; se presente sostituisce i tre punti standard), `ordering`, `is_active`. Su `PATCH` un `features_override: null` esplicito azzera l'override.

### `PATCH /admin/plans/{plan_id}`
Aggiornamento parziale (stessi campi, tranne `slug`). I piani **non si eliminano** (lo storico abbonamenti li referenzia): si disattivano con `is_active=false`, che li nasconde dalla registrazione e dal cambio piano.

### `GET /admin/addons` · `POST /admin/addons` (201) · `PATCH /admin/addons/{addon_id}`
Gestione del catalogo add-on, gemella di `/admin/plans` (stessi permessi admin): GET tutti (anche disattivati), POST crea (`nome`, `slug` — unico, immutabile, `[a-z0-9-]+` → `409` se duplicato —, `descrizione?`, `prezzo ≥ 0` in €, `tipo_prezzo?`/`etichetta_prezzo?` come per i piani, **`tipo_fruizione?`** `consumabile`/`permanente` (default `consumabile`, migration 0028), `ordering`, `is_active`), PATCH aggiorna i campi passati (slug escluso) o disattiva. Il **`tipo_fruizione` è immutabile come lo slug**: presente solo in POST, **assente in PATCH** (cambiarlo con inventario circolante ha semantica indefinita). Gli add-on **non si eliminano**: si disattivano.

### `GET /admin/purchases?status=&kind=&page=&page_size=`
Storico acquisti di **tutti** gli utenti (più recenti prima, `page_size` max 100), filtrabile per `status` e `kind`. Item come `PurchaseOut` di `GET /me/purchases`.

### `GET /admin/invoices?stato=&page=&page_size=`
**Registro fatture interno** (migration 0027), sola lettura: `{items: [{id, purchase_id, anno, serie, numero, data_documento, stato, provider_id, totale_cents, tentativi, created_at, emessa_at}], total, page, page_size}`. Le righe nuove nascono e restano `da_emettere` (l'emissione fiscale è fuori piattaforma); gli altri stati (`in_invio`/`inviata`/`consegnata`/`non_consegnata`/`scartata`/`errore`) e i campi `numero`/`provider_id`/`tentativi` valorizzati sono **storici**, di quando la piattaforma trasmetteva a SDI.

### `GET /admin/payment-anomalies?stato=aperta|risolta`
**Incassi orfani** da riconciliare (default `aperta`): pagamenti incassati dal provider che non corrispondono a un purchase applicabile (annullato/scaduto/già coperto, o purchase inesistente) — in v1 la risoluzione è manuale (verifica + eventuale rimborso). Le anomalie vivono in `audit_log` (`payments.orphan`; la risoluzione aggiunge `payments.orphan_resolved`): `{items: [{audit_id, payload, created_at, risolta}]}`.

### `POST /admin/payment-anomalies/{audit_id}/resolve`
Marca l'anomalia come risolta (scrive `payments.orphan_resolved` con l'admin come attore). → `{ok: true}`.

### `POST /admin/alerts/run` · `GET /admin/alerts/runs?limit=`
Esegue subito la run giornaliera degli alert (senza `ripeti`: `409` se quella di oggi è già stata eseguita; `ripeti=true` riesegue — il ledger impedisce comunque i doppi invii) e ritorna i contatori `{giorno, esito, bandi_candidati, destinatari, email_inviate, email_fallite, dettagli}`. `GET /runs` = registro delle esecuzioni (osservabilità).
