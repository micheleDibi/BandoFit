# Deploy su server (Docker Compose)

BandoFit gira in due container: `backend` (FastAPI/uvicorn) e `frontend` (build statica servita da nginx interno al container). Le porte pubblicate sull'host e tutte le credenziali si configurano nel file `.env` alla radice del repo (mai committato).

```
Internet ──HTTPS──▶ reverse proxy del server ──▶ 127.0.0.1:FRONTEND_PORT (frontend)
                          └── /api/ ──────────▶ 127.0.0.1:BACKEND_PORT  (backend)
```

## Prerequisiti

- Docker + plugin Compose sul server (`docker compose version`).
- Progetto Supabase **primario** creato con le 3 migration eseguite (vedi [setup.md](setup.md)).
- Credenziali del **secondario** (URL + anon key).
- Un dominio puntato al server, con il reverse proxy già in uso (nginx/caddy/traefik).

## 1. Clona e configura

```bash
git clone https://github.com/micheleDibi/BandoFit.git
cd BandoFit
cp .env.example .env
nano .env
```

Compila `.env`:

| Variabile | Valore |
|---|---|
| `FRONTEND_PORT` / `BACKEND_PORT` | Porte libere sull'host (es. 3001 / 3002) |
| `BIND_ADDRESS` | `127.0.0.1` dietro reverse proxy (default); `0.0.0.0` solo per esporre direttamente |
| `PRIMARY_SUPABASE_URL` + `PRIMARY_SUPABASE_SERVICE_ROLE_KEY` | dal progetto primario (Project Settings → API) |
| `SECONDARY_SUPABASE_URL` + `SECONDARY_SUPABASE_ANON_KEY` | dal progetto secondario |
| `CORS_ORIGINS` e `FRONTEND_URL` | l'origine pubblica, es. `https://bandofit.example.com` |
| `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` | URL e **anon key** del PRIMARIO (solo auth) |
| `VITE_API_BASE_URL` | come il **browser** raggiunge il backend: `https://bandofit.example.com/api/v1` |
| `API_PUBLIC_URL` | come il browser raggiunge il backend (es. `https://bandofit.example.com/api/v1`): serve ai link di **disiscrizione** nelle email degli alert |
| `ALERT_DATA_ATTIVAZIONE` | data (YYYY-MM-DD) da cui gli alert considerano i bandi: **impostarla alla data del deploy** — un valore retrodatato manderebbe al primo run una valanga di arretrati |
| `ALERT_ORA_INVIO` / `ALERT_SCHEDULER_ATTIVO` | ora locale (Europe/Rome) della run giornaliera, default `08:00`; lo scheduler si può spegnere con `false` (la run resta lanciabile da `POST /admin/alerts/run`) |
| `SMTP_HOST/PORT/USER/PASSWORD` + `EMAIL_FROM` | casella SMTP per le email di invito (es. OVH, vedi sotto); in alternativa `RESEND_API_KEY`; senza nessuno dei due le email vengono solo loggate |
| `OPENAPI_EMAIL` + `OPENAPI_API_KEY` + `OPENAPI_ENV` | credenziali openapi.it per l'import dei dati aziendali e la verifica CF (da console.openapi.com; le API "Company" e "Risk" vanno attivate una tantum dalla Libreria API). `OPENAPI_ENV=production` in deploy; le chiavi sandbox/produzione sono diverse. Vuote = importazione disattivata, il resto dell'app funziona. **Ogni import consuma credito** (IT-full ~0,30 € + IVA) |
| `ANTHROPIC_API_KEY` + `AI_CHECK_MODEL` | chiave API Anthropic per l'AI-check (da console.anthropic.com); modello default `claude-sonnet-5`. Vuota = AI-check disattivato, il resto dell'app funziona. **Ogni report consuma credito API** (~0,10–0,20 $; meno con l'estrazione del bando in cache). Le quote per gli utenti si impostano dai piani (campo AI-check) |

### Deliverability degli alert (SPF/DKIM/DMARC) — azione DNS a tuo carico

Gli alert sui nuovi bandi aumentano il volume di invii: senza autenticazione del dominio mittente finiscono in spam. Sul DNS del dominio di `EMAIL_FROM`:
- **SPF**: record TXT con l'include dell'infrastruttura di invio (OVH: `v=spf1 include:mx.ovh.com ~all`; Resend: l'include indicato nella dashboard Domains).
- **DKIM**: attivare la firma dal pannello del provider (OVH MX Plan → gestione DKIM; Resend → record CNAME/TXT da dashboard).
- **DMARC**: TXT su `_dmarc` — partire in osservazione con `v=DMARC1; p=none; rua=mailto:postmaster@<dominio>`, poi passare a `p=quarantine`.
`EMAIL_FROM` deve appartenere al dominio autenticato. Le email degli alert includono gli header `List-Unsubscribe`/`List-Unsubscribe-Post` (RFC 8058). **Bounce**: con Resend si può aggiungere il webhook (fase successiva); con SMTP puro i bounce arrivano come NDR nella casella mittente → esclusioni manuali con `insert into email_suppressions (email, motivo) values ('...', 'manuale')` dal SQL Editor.

### Email via SMTP (es. casella OVH)

Il backend invia email (inviti famiglia a utenti già registrati, reinvii) tramite il primo provider configurato: **SMTP** se `SMTP_HOST` è valorizzato, altrimenti **Resend**. Per una casella OVH (MX Plan):

```env
SMTP_HOST=ssl0.ovh.net
SMTP_PORT=465            # 465 = SSL/TLS implicito; 587 = STARTTLS
SMTP_USER=noreply@tuodominio.it   # l'indirizzo COMPLETO della casella
SMTP_PASSWORD=la-password-della-casella
EMAIL_FROM=BandoFit <noreply@tuodominio.it>
```

> **TUTTE le email della piattaforma escono da qui**: conferma registrazione, recupero password, inviti famiglia. Il mailer di Supabase non viene mai usato — i link firmati vengono generati via Admin API (`generate_link`) e spediti dal backend col provider configurato. Non serve configurare nulla nelle SMTP Settings di Supabase.

> Le variabili `VITE_*` vengono **cotte nel bundle** alla build del frontend: se le cambi, serve `docker compose up -d --build frontend`.

## 2. Avvia

```bash
docker compose up -d --build
docker compose ps           # entrambi i servizi "running"
curl http://127.0.0.1:3002/api/v1/health   # {"status":"ok"}
curl -I http://127.0.0.1:3001/             # 200
```

## 3. Reverse proxy

Esempio di virtual host **nginx** (adattare dominio e porte; per caddy/traefik la logica è identica: `/api/` → backend, tutto il resto → frontend):

```nginx
server {
    server_name bandofit.example.com;

    # API: il backend serve già i percorsi /api/v1/*, nessuna riscrittura
    location /api/ {
        proxy_pass http://127.0.0.1:3002;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Frontend (SPA)
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_set_header Host $host;
    }

    # ... blocco listen 443/ssl gestito come per gli altri servizi (certbot ecc.)
}
```

Con questo schema frontend e API stanno sulla **stessa origine** (`https://bandofit.example.com`), quindi CORS non entra mai in gioco lato browser.

## 4. Supabase: URL pubblici

I link nelle email sono **token di dominio** gestiti interamente dal backend: su Supabase **non servono Redirect URLs** né configurazioni email. L'unica impostazione che conta è **Authentication → Sign In / Providers → Email → "Confirm email" = attivo** in produzione: è l'enforcement che impedisce il login agli utenti non confermati (la conferma la applica il backend via Admin API quando l'utente clicca il link di dominio).

> Richiede la migration `0004_auth_tokens.sql` applicata sul progetto primario.

## 5. Primo admin e smoke test

1. `https://bandofit.example.com/registrati` → registrati con un piano.
2. SQL Editor del primario: `select public.promote_to_admin('tua-email');` → ricarica: compaiono le sezioni admin.
3. Verifica: elenco bandi popolato e filtri funzionanti, dettaglio bando, cambio piano, dati aziendali, invito famiglia.

## Operazioni ricorrenti

```bash
# aggiornamento all'ultima versione
git pull && docker compose up -d --build

# log
docker compose logs -f backend
docker compose logs -f frontend

# cambiare porta: modifica .env, poi
docker compose up -d

# stop
docker compose down
```

## Risoluzione problemi

- **502 dal proxy** → `docker compose ps` (container su?), porte in `.env` allineate col vhost.
- **Errore CORS nel browser** → `CORS_ORIGINS` deve essere l'origine esatta del frontend (con `https://`, senza slash finale). Con il proxy stessa-origine di sopra non dovrebbe mai comparire.
- **Login ok ma dati vuoti/errore** → `VITE_API_BASE_URL` sbagliato (ricorda: rebuild del frontend dopo la modifica).
- **Link d'invito che non reindirizza** → Redirect URLs su Supabase (passo 4).
- **`docker compose logs backend`** mostra anche le email loggate quando `RESEND_API_KEY` è vuota.
