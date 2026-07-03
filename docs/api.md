# API Backend

Base URL: `http://localhost:8000/api/v1` (sviluppo). Documentazione interattiva: `http://localhost:8000/docs` (Swagger UI).

**Autenticazione**: header `Authorization: Bearer <access_token>` (JWT emesso da Supabase Auth del progetto primario). Il backend verifica firma (ES256/RS256 via JWKS, fallback HS256 legacy), `aud` e `iss`, poi carica il profilo: un account con `is_active=false` riceve `403`. Gli endpoint `/admin/*` richiedono `role='admin'`.

**Formato errori** (uniforme):
```json
{ "error": { "code": "not_found", "message": "Bando non trovato" } }
```
Codici: `unauthorized` (401), `forbidden` (403), `not_found` (404), `bad_request` (400), `conflict` (409), `validation_error` (422), `search_timeout` (504), `upstream_error` (502), `upstream_timeout` (504).

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
Cambio piano (senza pagamento in questa fase). Body: `{"plan_id": 3}`. L'abbonamento attivo passa a `cancelled` e ne viene creato uno nuovo annuale. → come `GET /me`. Errori: `400` se il piano non esiste o non è attivo.

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
Elenco utenti con abbonamento attivo. Parametri: `q` (cerca in email/nome/cognome/azienda), `role` (`admin`|`cliente`), `page`, `page_size` (max 100). Item: `{ "profile": {...}, "subscription": {...} | null }`.

### `PATCH /admin/users/{user_id}`
Body (opzionali): `role` (`admin`|`cliente`), `is_active` (bool). Protezioni: un admin non può togliersi il ruolo né disattivarsi da solo (`400`).

### `POST /admin/users/{user_id}/subscription`
Cambio piano forzato per un utente. Body: `{"plan_id": 2}`.

### `GET /admin/plans`
Tutti i piani, inclusi i disattivati.

### `POST /admin/plans` (201)
Crea un piano. Body: `nome`, `slug` (`[a-z0-9-]+`, unico → `409` se duplicato), `descrizione?`, `prezzo_annuale`, `ai_check`, `alert_attivo`, `alert_giorni_preavviso` (obbligatorio se `alert_attivo=true`), `num_account_aziendali`, `ordering`, `is_active`.

### `PATCH /admin/plans/{plan_id}`
Aggiornamento parziale (stessi campi, tranne `slug`). I piani **non si eliminano** (lo storico abbonamenti li referenzia): si disattivano con `is_active=false`, che li nasconde dalla registrazione e dal cambio piano.
