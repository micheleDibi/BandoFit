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
| `REVOLUT_SECRET_KEY` + `REVOLUT_ENV` + `REVOLUT_WEBHOOK_SECRET` | pagamenti (Revolut Merchant API, migration 0026): chiave segreta del Merchant account (Revolut Business → APIs → Merchant API), ambiente (`production` in deploy: la **sandbox è un account Business separato** — sandbox-business.revolut.com — con chiavi **diverse**, da far corrispondere all'ambiente) e signing secret `wsk_...` restituito alla registrazione del webhook via API (verifica della firma HMAC — vedi «Pagamenti» sotto). Chiave vuota = modulo pagamenti disattivato (503), il resto dell'app funziona |
| `FATTURA_DENOMINAZIONE` + `FATTURA_PARTITA_IVA` | emittente (cedente) delle fatture elettroniche SDI (migration 0027): denominazione e P.IVA. **Vuote = fatturazione disattivata** (i pagamenti funzionano ma i purchase pagati restano senza fattura). Dati fiscali reali: solo nel `.env`, mai nel repo |
| `FATTURA_CODICE_FISCALE` | codice fiscale del cedente (facoltativo) |
| `FATTURA_REGIME` | regime fiscale FatturaPA del cedente, default `RF01` (ordinario) |
| `FATTURA_SEDE_INDIRIZZO` / `FATTURA_SEDE_COMUNE` / `FATTURA_SEDE_PROVINCIA` / `FATTURA_SEDE_CAP` | sede del cedente riportata in fattura |
| `FATTURA_SERIE` | serie della numerazione fatture (default vuota: numerazione unica per anno) — sceglierla una volta e non cambiarla in corso d'anno |
| `PAYMENT_SCHEDULER_ATTIVO` / `PAYMENT_ORA_ESECUZIONE` | scheduler dei pagamenti (preavvisi, rinnovi automatici, retry, fine grazia, fatture): attivo di default, run giornaliera alle `06:00` locali (Europe/Rome). `false` = nessun rinnovo/downgrade automatico (utile in sviluppo) |
| `VITE_REVOLUT_MODE` | modalità del widget Revolut nel **browser**: `prod` in produzione — il default è `sandbox`, che non muove denaro vero e in produzione non funzionerebbe. Variabile `VITE_*`: cotta nel bundle, rebuild del frontend dopo la modifica |
| `RATE_LIMIT_PEPPER` | **obbligatoria in deploy**: con `ENV=production` il backend si rifiuta di partire senza. Generarla con `openssl rand -hex 32`. Sceglierla **una volta sola** — cambiarla azzera i contatori anti-enumerazione in corso, perché i bucket derivano da lei |
| `TRUSTED_PROXY_HOPS` | quanti proxy fidati stanno davanti al backend, default **2** (Cloudflare + reverse proxy). Vedi «IP del client» sotto: da regolare solo se la catena è diversa |

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

### Pagamenti Revolut: registrazione del webhook e fatturazione (una tantum)

Prerequisiti: migration **0026, 0027 e 0028** eseguite sul DB primario **prima** del deploy di questo backend (checkout e fatture leggono `purchases`/`invoices`; l'inventario add-on e il consulto a pagamento leggono `user_addon_inventory`/`addon_ledger`). La **0028** va rilasciata **in modo atomico** con backend e frontend (R3/R4/R5 — inventario add-on, consumo del consulto e grant admin sono un blocco unico); dal suo rilascio il **consulto esperto diventa a pagamento** appena la riga di catalogo `consulto-esperto` è consumabile a pagamento, senza periodo di omaggio. In produzione `REVOLUT_ENV=production` con la chiave del **Merchant account reale**; la sandbox è un account Business **separato** (sandbox-business.revolut.com) con chiavi proprie — webhook e secret vanno registrati **per ciascun ambiente**.

Il backend riceve gli esiti su `POST /api/v1/webhooks/revolut`, ma il provider non lo sa finché il webhook non viene **registrato via API** (non c'è UI):

```bash
curl -X POST https://merchant.revolut.com/api/webhooks \
  -H "Authorization: Bearer $REVOLUT_SECRET_KEY" \
  -H "Revolut-Api-Version: 2024-09-01" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://bandofit.example.com/api/v1/webhooks/revolut",
       "events": ["ORDER_COMPLETED", "ORDER_FAILED", "ORDER_CANCELLED",
                  "ORDER_PAYMENT_DECLINED", "ORDER_PAYMENT_FAILED"]}'
```

- L'`url` deve essere **pubblico e HTTPS**: niente `localhost` né IP nudi (il provider li rifiuta). In sandbox l'host è `sandbox-merchant.revolut.com`.
- La risposta contiene il **`signing_secret` (`wsk_...`)**: va copiato in `REVOLUT_WEBHOOK_SECRET` — viene mostrato **solo alla registrazione** (o alla rotazione). Senza, l'endpoint webhook risponde `503` e Revolut ritenta: il deploy va corretto, non ignorato.
- Se l'API dell'endpoint `/api/` è chiusa agli IP di Cloudflare (vhost sopra), il webhook passa comunque dall'edge come ogni altra richiesta: nessuna eccezione da aprire.

Per la **fatturazione SDI** compilare i `FATTURA_*` (tabella sopra): senza, i pagamenti funzionano ma le fatture non partono. L'invio usa le **stesse credenziali openapi.it** dell'import dati — il backend richiede anche gli scope SDI (`sdi.openapi.it`: invio con conservazione a norma + ricerca fatture; in sandbox `test.sdi.openapi.it`), quindi come per Company e Risk l'API **SDI** va attivata una tantum dalla Libreria API di console.openapi.com, o il token non viene emesso.

> ⚠️ **`docker-compose.yml` non inoltra ancora le variabili dei pagamenti al container**: `REVOLUT_*`, `FATTURA_*` e `PAYMENT_*` non compaiono nell'`environment` del servizio backend (né `VITE_REVOLUT_MODE` negli `args` di build del frontend), e il `.env` alla radice non è montato nel container. Finché non vengono aggiunte lì, valorizzarle nel `.env` non basta: il modulo resta spento (503) e il widget resta in sandbox.

> Le variabili `VITE_*` vengono **cotte nel bundle** alla build del frontend: se le cambi, serve `docker compose up -d --build frontend`.

## 2. Avvia

```bash
docker compose up -d --build
docker compose ps           # entrambi i servizi "running"
curl http://127.0.0.1:3002/api/v1/health   # {"status":"ok"}
curl -I http://127.0.0.1:3001/             # 200
```

> **Export PDF (WeasyPrint).** L'immagine backend installa le librerie di sistema per la generazione PDF (pango/cairo/gdk-pixbuf + font DejaVu, vedi `backend/Dockerfile`): dopo un aggiornamento che le introduce serve `docker compose up -d --build backend`. Il motore si sceglie con `PDF_ENGINE` (default `auto`: WeasyPrint, con fallback a ReportLab che è pure-Python); non è necessario impostarlo. Verifica veloce: da `docker compose exec backend python -c "import weasyprint"` non deve dare errore.

## 3. Reverse proxy

Esempio di virtual host **nginx** (adattare dominio e porte; per caddy/traefik la logica è identica: `/api/` → backend, tutto il resto → frontend). Chi usa **Nginx Proxy Manager** legga anche la variante in fondo alla sezione: l'UI genera un nginx che da questo differisce nei punti che contano.

```nginx
server {
    server_name bandofit.example.com;

    # API: il backend serve già i percorsi /api/v1/*, nessuna riscrittura
    location /api/ {
        # Dietro Cloudflare: solo l'edge può parlare all'API. Senza questo,
        # CF-Connecting-IP è un campo libero — vedi «IP del client» sotto.
        include /etc/nginx/cloudflare-ips.conf;   # allow <rete>; ... deny all;

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

`cloudflare-ips.conf` è un `allow` per ogni rete pubblicata su [cloudflare.com/ips](https://www.cloudflare.com/ips/) (una quindicina IPv4 più una manciata IPv6) seguito da `deny all;`. La lista cambia di rado ma cambia: quando succede il sintomo è un 403 per gli utenti dietro le reti nuove, quindi vale un promemoria periodico o un cron che la riscarichi.

### IP del client (rate limiting di `/auth/register`)

Il rate limit anti-enumerazione conta per IP, quindi l'IP dev'essere quello vero. **`request.client.host` non lo è**: il backend gira in Docker con il mapping `127.0.0.1:3002 → 8000`, quindi il peer che uvicorn vede è il gateway della bridge — **identico per ogni utente del pianeta**. Usarlo significherebbe un unico contatore condiviso, e il primo abusatore bloccherebbe tutti gli altri.

L'IP si ricava quindi dagli header (`app/core/net.py`), in ordine: **`CF-Connecting-IP`**, altrimenti `X-Forwarded-For` contato **da destra** per `TRUSTED_PROXY_HOPS` posizioni (default **2** = Cloudflare + reverse proxy). Contare da destra è ciò che rende l'header non falsificabile — ma **solo se ogni hop appende** (`$proxy_add_x_forwarded_for`, come nel vhost sopra): quello che inietta il client resta allora in testa, dove non lo guardiamo. Un proxy che invece **sovrascrive** l'header accorcia la catena e il conteggio va rifatto: è il caso di Nginx Proxy Manager, sotto.

Tre cose da sapere:

- **`FORWARDED_ALLOW_IPS=*` non è la scorciatoia: è un peggioramento.** Con `*`, uvicorn (`ProxyHeadersMiddleware`, ramo `always_trust`) prende il **primo** elemento di `X-Forwarded-For` — cioè proprio quello iniettabile dal client. Meglio un IP ignoto che uno falsificabile. Non impostarla.
- **`CF-Connecting-IP` vale quanto vale il reverse proxy.** Se accetta connessioni da chiunque, chi scopre l'IP origin lo raggiunge scavalcando Cloudflare e scrive quell'header a mano: il limite per IP diventa un ornamento, perché ogni richiesta cade in un bucket nuovo. Vanno accettati solo gli [IP di Cloudflare](https://www.cloudflare.com/ips/), come nel vhost sopra.
- **`allow`/`deny` e `set_real_ip_from` sono alternative, non complementi.** Il modulo realip gira nella fase POST_READ, `allow`/`deny` nella fase ACCESS, che viene dopo: con entrambi attivi il confronto cade su un `$remote_addr` **già riscritto all'IP del visitatore**, che un IP Cloudflare non è — quindi `deny all` e 403 a ogni utente vero. Col solo `allow`/`deny`, `$remote_addr` resta l'edge, il confronto è quello giusto e il backend si legge `CF-Connecting-IP` da sé: non serve altro.

Se l'IP non è determinabile (nessun proxy davanti, o catena diversa da quella dichiarata) il limite per IP semplicemente **non si applica** e resta un warning nei log: è deliberato, perché contare tutti su una chiave sbagliata è peggio che non contare. In sviluppo è il caso normale; per usare il peer come IP (localhost, senza proxy) si mette `TRUSTED_PROXY_HOPS=0`.

Per verificare che la chiusura tenga, dal proprio computer:

```bash
# Attraverso Cloudflare: deve rispondere
curl -s https://bandofit.example.com/api/v1/health                 # {"status":"ok"}

# Dritti all'origin, scavalcando Cloudflare: deve essere respinto
curl -sk -o /dev/null -w '%{http_code}\n' \
  --resolve bandofit.example.com:443:203.0.113.5 \
  https://bandofit.example.com/api/v1/health                       # 403
```

`--resolve` fa collegare curl all'IP indicato presentando però dominio, SNI e `Host` corretti: è la simulazione più fedele di chi scavalca il CDN. (`-H "Host: ..."` darebbe lo stesso esito — nginx instrada sull'header **`Host`**, non sull'SNI, che sceglie solo il certificato — ma è una prova che vale meno, perché non esercita la selezione del vhost per come la esercita un browser vero.) Il `-k` serve se l'origin monta un certificato Cloudflare Origin CA, che pubblicamente attendibile non è.

### Variante: Nginx Proxy Manager

NPM genera il vhost da sé, quindi il blocco di sopra non si incolla da nessuna parte. Due differenze cambiano la sostanza:

- **L'`allow`/`deny` va nella custom location `/api`**, dietro l'**ingranaggio** accanto al percorso, che apre la config avanzata di *quella* location (nel template `_location.conf` è la prima riga dentro il `location`). Non nell'**Access List** dell'UI: `_access.conf` finisce anche in `location /`, quindi si porterebbe dietro la SPA. E non nella tab **Advanced** del proxy host, che è a livello server e non è scopata su `/api`.
- **`X-Forwarded-For` viene sovrascritto, non appeso.** NPM emette `proxy_set_header X-Forwarded-For $remote_addr;`, quindi al backend l'header arriva con **un solo elemento**, l'edge Cloudflare. Il fallback XFF di `net.py` non può restituire l'IP vero e tutto poggia su `CF-Connecting-IP` — cioè sull'`allow`/`deny` del punto precedente, che qui non è una misura in più: è l'unica. In compenso l'`X-Forwarded-For` iniettato dal client viene buttato via a prescindere.

`TRUSTED_PROXY_HOPS` resta comunque a **2**: `CF-Connecting-IP` si legge per primo e il conteggio degli hop non entra mai in gioco. Se Cloudflare sparisse, con `2` il limite per IP si spegne lasciando un warning; con `1` conterebbe tutti gli utenti di uno stesso edge sullo stesso bucket. Il default sbaglia dalla parte giusta.

Se NPM gira su una macchina diversa dal backend serve `BIND_ADDRESS=0.0.0.0`, e con quello il backend ascolta sulla rete privata: l'`allow`/`deny` difende la 443 di NPM, non la porta del backend. Su una rete non fidata va chiusa a parte, col firewall, lasciando passare il solo IP di NPM.

### Supabase Auth: hardening obbligatorio

Nella dashboard, **Authentication → General Configuration → «Allow new users to sign up»: OFF**. L'app non ne ha bisogno — registrazione e inviti passano dall'**Admin API** (`create_user`), che ignora quel flag, e il login non è un signup — mentre lasciarlo attivo tiene aperta una via d'ingresso parallela che non passa dalle difese di `POST /api/v1/auth/register`. Va verificato dopo ogni intervento sulla configurazione del progetto.

**Da non fare**: disattivare il provider Email per spegnere magic-link/OTP. Non esiste un toggle separato — magic link e OTP condividono l'implementazione con il provider Email, quindi spegnerlo spegnerebbe anche `signInWithPassword`, cioè il login di tutti.

**Limiti residui.** Le difese descritte sopra coprono gli endpoint di *questa* API. Finché `supabase-js` e la anon key vivono nel browser (`frontend/src/lib/supabase.ts`, iniettata a build time da `frontend/Dockerfile`), gli endpoint di Supabase Auth restano raggiungibili direttamente e conservano caratteristiche che non dipendono dal nostro codice: sono limiti noti della piattaforma, censiti nel **piano di sicurezza interno** insieme alle mitigazioni disponibili. La chiusura completa richiederebbe di spostare tutta l'autenticazione dietro il backend e smettere di spedire la anon key — una riscrittura del modello di sessione, fuori dallo scope di questo intervento.

Tenere quindi aggiornate le impostazioni di Authentication → Rate Limits, e rivalutare il tema se l'esposizione del prodotto cresce.

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
