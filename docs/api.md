# API Backend

Base URL: `http://localhost:8000/api/v1` (sviluppo). Documentazione interattiva: `http://localhost:8000/docs` (Swagger UI).

**Autenticazione**: header `Authorization: Bearer <access_token>` (JWT emesso da Supabase Auth del progetto primario). Il backend verifica firma (ES256/RS256 via JWKS, fallback HS256 legacy), `aud` e `iss`, poi carica il profilo: un account con `is_active=false` riceve `403`. Gli endpoint `/admin/*` richiedono `role='admin'`.

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

### `GET /plans`
Piani di abbonamento attivi, ordinati per `ordering`. Usato dallo step 2 della registrazione.

```json
[{ "id": 1, "nome": "Gratuito", "slug": "gratuito", "descrizione": "...", "prezzo_annuale": "0.00",
   "ai_check": 0, "alert_attivo": false, "alert_giorni_preavviso": null,
   "num_account_aziendali": 1, "ordering": 1, "is_active": true, "updated_at": "..." }]
```

## Endpoint autenticati

### `GET /me`
Profilo dell'utente corrente + abbonamento attivo con il piano.
```json
{ "profile": { "id": "uuid", "email": "...", "nome": "...", "cognome": "...", "azienda": null,
               "telefono": null, "role": "cliente", "is_active": true, "created_at": "..." },
  "subscription": { "id": "uuid", "status": "active", "data_inizio": "2026-07-03",
                    "data_scadenza": "2027-07-03", "plan": { ...come /plans... } } }
```

### `PATCH /me`
Aggiorna l'anagrafica. Body (tutti opzionali): `nome`, `cognome`, `azienda`, `telefono`. → come `GET /me`.

### `POST /me/subscription`
Cambio piano (senza pagamento in questa fase). Body: `{"plan_id": 3}`. L'abbonamento attivo passa a `cancelled` e ne viene creato uno nuovo annuale. → come `GET /me`; se il downgrade ha retrocesso membri della famiglia, la risposta include `plan_switch_adjustment: {demoted, revoked_pending}`. Errori: `400` piano inesistente/non attivo; `403 child_plan_locked` se l'utente è un figlio attivo (il piano si gestisce sul titolare).

## Famiglia di account

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
`{ "editable": true|false, "company": {...} | null }` — il titolare (o un utente singolo) vede e modifica i propri; un **figlio attivo** vede quelli della famiglia in sola lettura (`editable: false`).

### `PUT /me/company` *(solo titolare)*
Upsert dei dati aziendali. Campi: `ragione_sociale`*, `forma_giuridica`, `partita_iva`* (11 cifre, prefisso IT tollerato), `codice_fiscale`, `ateco_id`/`settore_id`/`regione_id` (id delle lookup del DB secondario → il backend denormalizza `ateco_codice`, `settore_nome`, `regione_nome`; `400` se sconosciuti), `anno_fondazione` (1800-2100), `indirizzo`, `comune`, `provincia`, `cap` (5 cifre), `classe_dimensionale` (`micro|piccola|media|grande`), `numero_dipendenti`, `fascia_fatturato` (`fino_100k|100k_500k|500k_2m|2m_10m|10m_50m|oltre_50m`), `pec`, `telefono`, `sito_web`.

### `GET /lookups`
Valori delle faccette di filtro, dal DB secondario (cache server 1h, `Cache-Control: private, max-age=3600`):
```json
{ "regioni": [{"id": 10, "nome": "Lombardia"}], "settori": [...], "beneficiari": [...],
  "codici_ateco": [{"id": 3, "codice": "49", "descrizione": "Trasporto terrestre"}],
  "tipologie_bando": [...], "modalita_erogazione": [...], "programmi": [...] }
```

### `GET /bandi`
Elenco paginato dei bandi (solo quelli pubblicabili: `stato_processing='completed'`).

Parametri query:
| Parametro | Tipo | Note |
|---|---|---|
| `page` / `page_size` | int | default 1 / 20, `page_size` max 50 |
| `sort` | string | `scadenza_asc` (default), `scadenza_desc`, `pubblicazione_desc`, `importo_desc` |
| `q` | string | ricerca full-text italiana (websearch) su titolo e descrizione |
| `stato` | csv | tra `aperto`, `chiuso`, `in apertura prossimamente` |
| `livello` | string | `flash_bando` o `guida_bando` |
| `tipologie`, `modalita`, `programmi` | csv di id | filtri su colonne dirette |
| `regioni`, `settori`, `beneficiari`, `ateco` | csv di id | filtri M:N via junction (OR dentro la faccetta, AND tra faccette) |
| `importo_min`, `importo_max` | int (€) | su `importo_totale_eur` |
| `scadenza_da`, `scadenza_a` | date ISO | intervallo su `data_scadenza` |
| `scade_entro_giorni` | int 1-365 | da oggi a oggi+N |

Item della risposta: `id`, `slug`, `titolo`, `titolo_breve`, `descrizione_breve`, `stato_bando`, `livello`, date, importi, `ente_erogatore`, `tipologia {id,nome}`, `modalita_erogazione {id,nome}`, `regioni [{id,nome}]`.

### `GET /bandi/{slug}`
Dettaglio completo: campi dell'elenco + `area_geografica`, `tematica[]`, `link_bando`, `link_candidatura`, `contenuto` (JSON strutturato a sezioni/segmenti, renderizzato dal frontend), `allegati[]`, `programma`, `settori[]`, `beneficiari[]`, `codici_ateco[]`. `404` se lo slug non esiste o il bando non è pubblicabile.

## Endpoint admin

### `GET /admin/users`
Elenco utenti con abbonamento attivo. Parametri: `q` (cerca in email/nome/cognome/azienda), `role` (`admin`|`cliente`), `page`, `page_size` (max 100). Item: `{ "profile": {...}, "subscription": {...} | null, "family": {...} | null }` — per i figli `family = {type:'child', status, parent_email}` e `subscription` è quella ereditata (`inherited: true`); per i titolari `family = {type:'parent', members_count}`.

### `PATCH /admin/users/{user_id}`
Body (opzionali): `role` (`admin`|`cliente`), `is_active` (bool). Protezioni: un admin non può togliersi il ruolo né disattivarsi da solo (`400`).

### `POST /admin/users/{user_id}/subscription`
Cambio piano forzato per un utente. Body: `{"plan_id": 2}`. `403` sui figli di famiglia (pending/attivi): il piano si gestisce sull'account titolare; forzare il piano di un titolare applica le stesse retrocessioni automatiche del cambio normale.

### `GET /admin/plans`
Tutti i piani, inclusi i disattivati.

### `POST /admin/plans` (201)
Crea un piano. Body: `nome`, `slug` (`[a-z0-9-]+`, unico → `409` se duplicato), `descrizione?`, `prezzo_annuale`, `ai_check`, `alert_attivo`, `alert_giorni_preavviso` (obbligatorio se `alert_attivo=true`), `num_account_aziendali`, `ordering`, `is_active`.

### `PATCH /admin/plans/{plan_id}`
Aggiornamento parziale (stessi campi, tranne `slug`). I piani **non si eliminano** (lo storico abbonamenti li referenzia): si disattivano con `is_active=false`, che li nasconde dalla registrazione e dal cambio piano.
