# Deploy su server (Docker Compose)

BandoFit gira in due container: `backend` (FastAPI/uvicorn) e `frontend` (build statica servita da nginx interno al container). Le porte pubblicate sull'host e tutte le credenziali si configurano nel file `.env` alla radice del repo (mai committato).

```
Internet ──HTTPS──▶ reverse proxy del server ──▶ 127.0.0.1:FRONTEND_PORT (frontend)
                          └── /api/ ──────────▶ 127.0.0.1:BACKEND_PORT  (backend)
```

## Prerequisiti

- Docker + plugin Compose sul server (`docker compose version`).
- Progetto Supabase **primario** con tutte le migration di `supabase/migrations/` eseguite in ordine di numero, oggi dalla 0001 alla 0047 (vedi [setup.md](setup.md) e «Migration del DB primario» sotto).
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
| `OPENAPI_EMAIL` + `OPENAPI_API_KEY` + `OPENAPI_ENV` | credenziali openapi.it per l'import dei dati aziendali e la verifica CF (da console.openapi.com; le API "Company" e "Risk" vanno attivate una tantum dalla Libreria API; dal modulo bilanci anche **IT-advanced** e **Visure Camerali** (`bilancio-ottico`, `impresa`) — se mancano fallisce solo il prodotto nuovo, grazie ai token separati; dopo il deploy, e solo a storico acceso (`BILANCI_STORICO_ATTIVO`, riga sotto), prezzo e attivazione dell'addon `bilancio-ufficiale` si fanno da AdminAddon). `OPENAPI_ENV=production` in deploy; le chiavi sandbox/produzione sono diverse. Vuote = importazione disattivata, il resto dell'app funziona. **Ogni import consuma credito** (IT-full ~0,30 € + IVA) |
| `BILANCI_STORICO_ATTIVO` | storico dei bilanci (IT-advanced nell'anteprima dell'import e in «Recupera i bilanci») e bilancio ufficiale, entrambi a pagamento: **spento di default**. Da spento l'anteprima non chiama IT-advanced, le rotte del recupero e del bilancio ufficiale rispondono 404 e il frontend non le mostra; l'import IT-full e la lettura dei bilanci già salvati restano come sono. `docker-compose.yml` la passa al container (default `false`): si imposta nel `.env`. Procedura, con le prove su un'azienda propria, in «Accensione delle funzioni» sotto. A storico acceso parte anche il failsafe del bilancio ufficiale. Prima di rispegnerla non devono esserci richieste di bilancio ufficiale ancora aperte: a storico spento non avanzano |
| `ANTHROPIC_API_KEY` + `AI_CHECK_MODEL` | chiave API Anthropic per l'AI-check (da console.anthropic.com); modello default `claude-sonnet-5`. Vuota = AI-check disattivato, il resto dell'app funziona. **Ogni report consuma credito API** (~0,10–0,20 $; meno con l'estrazione del bando in cache). Le quote per gli utenti si impostano dai piani (campo AI-check) |
| `PARTENARIATI_ATTIVO` (+ `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO`) | modulo partenariati (migration 0034-0042, da eseguire tutte in ordine dallo SQL Editor del primario **prima** del deploy, anche a modulo spento: il backend legge colonne della 0036, 0041 e 0042 qualunque sia il flag; procedura in «Migration del DB primario» sotto): spento di default. `docker-compose.yml` passa al container `PARTENARIATI_ATTIVO` e i tre budget giornalieri qui sotto, con gli stessi default di `config.py`: si impostano nel `.env`, procedura in «Accensione delle funzioni» sotto. Le altre variabili facoltative del modulo no: se servono, **azione manuale**, vanno aggiunte all'`environment` del servizio backend. Budget giornalieri fail-closed in centesimi USD: `PARTENARIATO_BUDGET_CENTS_GIORNO` (500, estrazioni delle regole), `PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI` (200), batch notturno `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO` (0 = spento; consigliato 300 se lo accendi). Usa `ANTHROPIC_API_KEY`: senza chiave le estrazioni e la bozza AI del profilo rispondono 503. Profilo partner (migration 0035): `PARTNER_BOZZA_AI_LIMITE_GIORNO` (3 per azienda), `PARTNER_BOZZA_AI_LIMITE_UTENTE_GIORNO` (10 per titolare), `PARTNER_BOZZA_AI_MAX_TOKENS`, `PARTNER_BOZZA_AI_TIMEOUT_SECONDS`, `PARTNER_BOZZA_AI_STALE_MINUTI`: facoltative, i default sono quelli di produzione (se servono, stessa azione manuale sull'`environment`). Call di partenariato (migration 0036 e 0037): `PARTNER_CALL_BOZZE_MAX` (5 bozze per azienda), `PARTNER_CALL_AI_LIMITE_GIORNO` (10 proposte AI al giorno per call e servizio), `PARTNER_CALL_AI_LIMITE_OWNER_GIORNO` (30 per titolare su tutti i servizi delle call), `PARTNER_CALL_AI_MAX_TOKENS`, `PARTNER_CALL_AI_TIMEOUT_SECONDS`, `PARTNER_CALL_AI_STALE_MINUTI`, `PARTNER_CALL_SCADENZA_DEFAULT_GIORNI` (60), `PARTNER_SEGNALAZIONI_LIMITE_GIORNO` (10): facoltative, stessi default di produzione e stessa azione manuale se servono. I limiti di call attive e candidature stanno sui piani (AdminPiani), non nell'`.env`. Matching, notifiche e digest (migration 0038): `PARTENARIATO_PESO_COPERTURA` / `_AFFINITA` / `_COMPLEMENTARITA` / `_COMPLETEZZA` / `_ROTAZIONE` (0.50/0.20/0.10/0.10/0.10), `PARTENARIATO_PENALITA_DATO_MANCANTE` (5), `PARTENARIATO_PENALITA_MAX` (20), `PARTENARIATO_SUGGERITI_PAGINA` (20), `PARTENARIATO_NOTIFICHE_TOP_K` (20), `PARTENARIATO_NOTIFICHE_SOGLIA` (50), `PARTENARIATO_NOTIFICHE_TETTO_SETTIMANA` (3), `PARTENARIATO_INDICE_TTL_SECONDS` (60), `PARTENARIATO_DIGEST_GIORNO` (0 = lunedì), `PARTENARIATO_DIGEST_ORA` (`08:30`): facoltative, stessi default di produzione e stessa azione manuale se servono. Il digest settimanale usa l'email già configurata (`SMTP_*`/`RESEND_API_KEY`, `EMAIL_FROM`) con `FRONTEND_URL` e `API_PUBLIC_URL` per i link, come gli alert sui bandi: nessuna variabile nuova per l'invio. La disiscrizione pubblica `/api/v1/partenariati/email/unsubscribe` risponde anche a modulo spento. Candidature, inviti e chat (migration 0039): `PARTNER_INVITO_TTL_GIORNI` (14), `PARTNER_INVITI_MAX_PER_CALL` (30), `PARTNER_CANDIDATURE_LIMITE_GIORNO` (10), `PARTNER_INVITI_LIMITE_GIORNO` (50), `PARTNER_MESSAGGI_LIMITE_ORA` (120): facoltative, stessi default di produzione e stessa azione manuale se servono; le email di evento usano lo stesso canale del digest. Consulto dalla call, moderazione, admin e verifica dell'identità (migration 0041): nessuna variabile nuova (le email di moderazione usano lo stesso canale; il passo notturno `ricalcolo_validazioni` gira nella run dello scheduler del modulo). Bozze AI dei documenti (migration 0042): `PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO` (10 bozze al giorno per titolare, anche con un piano illimitato), `PARTNER_BOZZE_DOCUMENTO_MAX_TOKENS` (8000), `PARTNER_BOZZE_DOCUMENTO_TIMEOUT_SECONDS` (150), `PARTNER_BOZZE_DOCUMENTO_STALE_MINUTI` (10): facoltative, stessi default di produzione e stessa azione manuale se servono; il limite mensile sta sui piani (AdminPiani), il failsafe `failsafe_bozze` gira nella run dello scheduler del modulo; il PDF usa lo stesso motore degli altri export. I testi legali segnaposto vanno rivisti con il legale e poi portati nel codice (vedi «Partenariati: testi legali da rivedere» sotto) |
| `REVOLUT_SECRET_KEY` + `REVOLUT_ENV` + `REVOLUT_WEBHOOK_SECRET` | pagamenti (Revolut Merchant API, migration 0026): chiave segreta del Merchant account (Revolut Business → APIs → Merchant API), ambiente (`production` in deploy: la **sandbox è un account Business separato** — sandbox-business.revolut.com — con chiavi **diverse**, da far corrispondere all'ambiente) e signing secret `wsk_...` restituito alla registrazione del webhook via API (verifica della firma HMAC — vedi «Pagamenti» sotto). Chiave vuota = modulo pagamenti disattivato (503), il resto dell'app funziona |
| `PAYMENT_SCHEDULER_ATTIVO` / `PAYMENT_ORA_ESECUZIONE` | scheduler dei pagamenti (preavvisi, rinnovi automatici, retry, fine grazia, recupero righe del registro fatture): attivo di default, run giornaliera alle `06:00` locali (Europe/Rome). `false` = nessun rinnovo/downgrade automatico (utile in sviluppo) |
| `RIMAPPATURA_FUSI_MODALITA` / `RIMAPPATURA_FUSI_INTERVALLO_MINUTI` | rimappatura periodica dei bandi fusi nel catalogo (migration 0043): **spenta di default**; `prova` conta senza modificare nulla, `attiva` scrive; un passo ogni 60 minuti. Un valore non ammesso non blocca l'avvio e lascia un log ERROR: una modalità sconosciuta lascia la rimappatura spenta, un intervallo non intero o minore di 5 vale 60. `docker-compose.yml` le passa entrambe al container: si impostano nel `.env`. Procedura in «Accensione delle funzioni» sotto |
| `VITE_REVOLUT_MODE` | modalità del widget Revolut nel **browser**: `prod` in produzione — il default è `sandbox`, che non muove denaro vero e in produzione non funzionerebbe. Variabile `VITE_*`: cotta nel bundle, rebuild del frontend dopo la modifica |
| `RATE_LIMIT_PEPPER` | **obbligatoria in deploy**: con `ENV=production` il backend si rifiuta di partire senza. Generarla con `openssl rand -hex 32`. Sceglierla **una volta sola** — cambiarla azzera i contatori anti-enumerazione in corso, perché i bucket derivano da lei; a modulo partenariati acceso rende da ricalcolare anche le chiavi dei collegamenti societari: finché il backfill notturno non le ricalcola (fino a 200 aziende per notte) quelle aziende non compaiono tra i suggerimenti né ricevono notifiche proattive |
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

### Pagamenti Revolut: registrazione del webhook (una tantum)

Prerequisiti: migration **0026, 0027, 0028 e 0029** eseguite sul DB primario **prima** del deploy di questo backend (checkout e registro fatture leggono `purchases`/`invoices`; l'inventario add-on e il consulto a pagamento leggono `user_addon_inventory`/`addon_ledger`). La **0028** va rilasciata **in modo atomico** con backend e frontend (R3/R4/R5 — inventario add-on, consumo del consulto e grant admin sono un blocco unico); dal suo rilascio il **consulto esperto diventa a pagamento** appena la riga di catalogo `consulto-esperto` è consumabile a pagamento, senza periodo di omaggio. La **0029** (venditore croato: tipi soggetto a 2, IVA 25% + reverse charge VIES, `features_override` sui piani) è senza nuove variabili d'ambiente e il backend tollera i tipi di soggetto vecchi (`_map`), quindi il deploy è sicuro in entrambi gli ordini — idealmente eseguirla subito dopo il deploy del codice. Dopo il deploy: compilare le «Caratteristiche personalizzate» del piano `tailored` da AdminPiani. In produzione `REVOLUT_ENV=production` con la chiave del **Merchant account reale**; la sandbox è un account Business **separato** (sandbox-business.revolut.com) con chiavi proprie — webhook e secret vanno registrati **per ciascun ambiente**.

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

Le fatture **non partono dalla piattaforma**: l'emissione fiscale è del titolare, fuori piattaforma (il backend tiene solo il registro interno — tab admin «Fatture», sola lettura). Le credenziali openapi.it servono per import dati aziendali, verifica CF e VIES; nessuno scope SDI viene richiesto.

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

**Log di accesso.** Nel backend, le righe di log delle richieste HTTP, in uscita e in entrata, non contengono query string né credenziali. Quelle di nginx sul server sì: il formato predefinito registra il path con la query e il `Referer`, che può contenerla a sua volta. Il formato va configurato a mano, per esempio così:

```nginx
# nel blocco http {}
log_format senza_query '$remote_addr - [$time_local] "$request_method $uri $server_protocol" '
                       '$status $body_bytes_sent "$http_user_agent"';

# nel blocco server {} di BandoFit, al posto dell'access_log predefinito
access_log /var/log/nginx/bandofit.access.log senza_query;
```

Anche l'`error_log` di nginx, sugli errori verso il backend (502/504), scrive la richiesta con la query e il referrer, e il suo formato non si configura. Si può alzarne il livello (`error_log /var/log/nginx/bandofit.error.log crit;`), rinunciando però ai messaggi sugli errori verso il backend; altrimenti va tenuto presente chi accede a quel file.

Con Nginx Proxy Manager il log del proxy host usa un formato proprio, con path, query e referer, che dall'interfaccia non si cambia: vale la stessa avvertenza sull'accesso a quei file.

Il nginx interno al container frontend non tiene log di accesso (`access_log off;` in `frontend/deploy/nginx.conf`): li tiene il proxy davanti. Vale dalla prima ricostruzione del frontend (`docker compose up -d --build`).

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

## Partenariati: verifica dell'identità di un'azienda (procedura per l'admin)

Dal WP9 il nome di un'azienda nel profilo partner e la rivelazione delle identità dopo un'accettazione richiedono che l'identità dell'azienda sia **verificata da un amministratore** (migration 0041). Il titolare chiede la verifica dalla pagina Azienda; la richiesta compare in **Admin → Partenariati → Identità** (`/app/admin/partenariati?tab=identita`). Procedura consigliata:

1. Controlla che il riquadro del **Registro Imprese** sia coerente (nessun avviso «I dati dell'azienda non corrispondono più al Registro Imprese»): se non lo è, la verifica non si può confermare e il titolare deve prima aggiornare l'import dei dati ufficiali.
2. Contatta l'azienda **solo con i recapiti ufficiali della sede presi dal Registro Imprese** (telefono o PEC mostrati nel riquadro), mai con quelli scritti dal titolare nella nota o nel profilo: la nota dice solo come e quando preferisce essere contattato.
3. Accerta che chi ha chiesto la verifica agisca per l'azienda: per esempio una telefonata al centralino della sede che confermi la richiesta e il titolare, una PEC di conferma dalla PEC ufficiale dell'azienda, oppure un documento del legale rappresentante. Scegli il **metodo** corrispondente (telefonata alla sede, documento del legale rappresentante, PEC dell'azienda, altro).
4. Registra l'esito: «Identità verificata» con il metodo, oppure «Verifica non riuscita». Nella nota scrivi solo ciò che serve a ricostruire la verifica (per esempio «confermato dal centralino il 29/09»): **mai dati personali** oltre il necessario, mai copie o numeri di documenti. Il titolare riceve una notifica in-app.
5. Se in seguito emergono dubbi, **revoca** la verifica con un motivo: le viste successive non mostrano più il nome dell'azienda (gli audit restano). La verifica si revoca anche da sola quando l'azienda cambia partita IVA o ragione sociale, viene eliminata o archiviata.

Ogni passaggio resta nel registro delle verifiche (append-only) e nell'audit.

## Partenariati: testi legali da rivedere

Questi testi sono segnaposto: vanno rivisti con il legale e poi portati nel codice, facendo salire la loro versione:
- l'informativa per il profilo partner e quella per il referente (`partenariato_informativa.py`, versione `2026-10-bozza-2`, marcate «BOZZA — DA RIVEDERE CON IL LEGALE»): descrivono la verifica dell'identità da parte dell'admin, il nome solo se scelto e verificato, la rivelazione solo tra aziende verificate, l'accesso della moderazione ai messaggi segnalati e la conservazione; restano da completare titolare del trattamento e contatto privacy, la base giuridica di verifica e moderazione e la durata di conservazione del registro delle verifiche (segnaposto tra parentesi quadre). Chi aveva acconsentito alla versione precedente vede un banner di riconferma: se il legale chiede un nuovo consenso esplicito invece del banner, va cambiata la regola in `partner_profile_service`;
- lo **statement of reasons** della moderazione (`partenariato_moderazione_testi.py`, marcato «BOZZA — DA RIVEDERE CON IL LEGALE»): riferimento puntuale ai Termini d'uso, fondamento normativo, indirizzo per i riesami delle decisioni d'ufficio e delle restrizioni nate da un ricorso;
- i testi del ricorso e delle vie di ricorso esterne e la nota di segnalazione nella UI (`lib/copy.ts`: `MODERAZIONE_COPY`, `CALL_COPY`);
- i Termini d'uso della piattaforma, con la sezione «Moderazione dei contenuti» a cui gli statement rimandano;
- dal WP10, il **disclaimer delle bozze dei documenti** (`DISCLAIMER` in `schemas/partenariato_bozze.py` e `BOZZE_COPY.disclaimer` in `lib/copy.ts`, stesso testo) e le istruzioni di stesura dei tre documenti (`partenariato_bozze_prompts.py`, `BOZZE_PROMPT_VERSION`): una modifica delle istruzioni fa salire la versione.

## Catalogo: rimappatura dei bandi fusi

Il catalogo dei bandi può unire due schede dello stesso bando: il doppione esce dall'elenco pubblico e resta il bando principale (master). Preferiti, scadenze nel calendario e call di partenariato salvati sul doppione vanno spostati sul master, altrimenti restano legati a un bando che non si legge più. Lo fa un passo periodico del backend: idempotente, **spento di default**.

Cosa fa un passo in modalità `attiva`:
- **preferiti**: passano al master; se lo stesso utente, per la stessa azienda, ha già salvato il master, quello del doppione si elimina;
- **eventi del calendario** del bando: passano al master; se l'evento del master c'è già, quello del doppione si elimina se non ha note, altrimenti diventa un evento personale;
- **call di partenariato attive**: passano al master; se l'azienda ha già una call attiva sul master restano dove sono e compaiono nel log come «in collisione» a ogni passo. Le call chiuse non cambiano;
- storico degli AI-check, alert inviati, estrazioni, consulenze e acquisti conservano il bando originale;
- ogni riga modificata o eliminata lascia una voce in `audit_log` (`catalogo.rimappatura_fuso`) con i valori precedenti: se il catalogo separa di nuovo il doppione **e il doppione è di nuovo in `bando_pubblico`**, il passo successivo, prima di rimappare, riporta indietro le righe, anche se nel frattempo il master è stato fuso in un altro bando (servono la 0045 e la 0046; altrimenti restano sul master e si riprova al passo dopo). La funzione non cancella mai righe; quelle in conflitto restano sul master e si vedono a mano, tranne quando l'utente ha già rimesso il doppione nello stesso ambito: è una sua scelta, il conflitto diventa definitivo e il passo non lo riprova (vedi «Ripristino dopo la separazione di un doppione» in «Accensione delle funzioni»);
- il riassunto di ogni passo conta anche le righe di `bando_fusione` del catalogo intero (`coppie_catalogo`), per il confronto con il produttore: `fusi` conta solo le coppie fra gli id in uso nel primario (un doppione che nessun utente ha salvato non compare e non ha nulla da rimappare).

Accensione, controlli e spegnimento: «Accensione delle funzioni» qui sotto, punto 1.

## Catalogo: come ottenere le richieste R1-R8

La conferma scritta del passo c2 (contratto DB bandi §10.1) elenca le otto richieste del §12 che il backend manda al catalogo. I log dell'applicazione non riportano la query string (per scelta: le query del DB primario contengono dati riservati), quindi le richieste si prendono dal codice, senza rete e senza credenziali:

```bash
cd backend && .venv/bin/python -m scripts.stampa_richieste_catalogo
```

Lo script esegue le funzioni reali dei servizi (elenco, dettaglio con la risoluzione degli slug, preferiti, calendario, alert, cataloghi) contro un client PostgREST vero con un trasporto finto, che registra ogni richiesta e risponde vuoto: per ogni Rn stampa metodo, percorso, query string e l'header `Prefer`. Nessuna chiave compare (l'indirizzo è fittizio, nessun `.env` letto) e i valori sono d'esempio (slug `esempio-di-slug`, id 1 e 2, la data di Roma del giorno). Di default la query è decodificata come nel §12; con `--come-inviata` è quella sul filo (`,` `(` `)` codificati), cioè la forma che compare nei log del gateway del catalogo. Il confronto con la «Versione (c)» del §12 e con i log di produzione si fa a mano; nella conferma si indicano commit e data della stampa. Cambia il codice, cambia la stampa: non c'è nessuna copia a mano delle query.

## Accensione delle funzioni

Tre funzioni nascono spente e si accendono in produzione una alla volta, in quest'ordine: rimappatura dei bandi fusi, storico dei bilanci e bilancio ufficiale, partenariati. Si passa alla successiva solo quando i controlli della precedente sono puliti.

### Come si cambia una variabile (vale per tutte)

1. Le variabili si impostano **solo nel `.env`** accanto a `docker-compose.yml`. Il compose le passa già al container del backend, con gli stessi default di `config.py` (funzioni spente): `BILANCI_STORICO_ATTIVO`, `RIMAPPATURA_FUSI_MODALITA`, `RIMAPPATURA_FUSI_INTERVALLO_MINUTI`, `PARTENARIATI_ATTIVO` e i tre budget giornalieri dei partenariati. `docker-compose.yml` non si modifica.
2. Si applica con `docker compose up -d backend`, che ricrea il container con il nuovo environment. **Mai `docker compose restart`**: riavvia il container con l'environment di prima e la modifica non ha effetto.
3. Si controlla che il container veda il valore giusto:
   ```bash
   docker compose exec backend env | grep -E 'RIMAPPATURA_FUSI|BILANCI_STORICO|PARTENARIAT'
   ```
   Poi `docker compose logs --since 5m backend` non deve mostrare errori all'avvio (per esempio «RIMAPPATURA_FUSI_MODALITA non valida» o «RIMAPPATURA_FUSI_INTERVALLO_MINUTI non valido»).
4. Si spegne allo stesso modo: il valore spento nel `.env` (oppure si toglie la riga, e vale il default spento), poi `docker compose up -d backend` e lo stesso controllo.

### 1. Rimappatura dei bandi fusi

Prerequisiti: la migration **0043** applicata sul primario, con il reload dello schema («Migration del DB primario» sotto); senza, ogni passo registra l'errore «fn_bandi_in_uso assente … la migration 0043 va applicata» e non fa nulla. Prima di passare ad `attiva` vanno applicate anche la **0044**, che permette di annullare un passo, la **0045**, che dà al passo l'elenco dei doppioni rimappati per rilevare le separazioni (senza, ogni passo registra l'errore «fn_doppioni_rimappati assente … la migration 0045 va applicata» e conta un errore, ma rimappa lo stesso), e la **0046**, la funzione che il passo chiama per ripristinare (senza, per ogni doppione separato il passo registra l'errore «fn_ripristina_rimappatura_voci assente … la migration 0046 va applicata», conta un errore e non ripristina nulla; vedi «Ripristino dopo la separazione di un doppione» sotto).

1. Nel `.env`: `RIMAPPATURA_FUSI_MODALITA=prova`. L'intervallo resta di 60 minuti se `RIMAPPATURA_FUSI_INTERVALLO_MINUTI` non si imposta. Poi `docker compose up -d backend` e il controllo con `env | grep`.
2. Controllare i log: `docker compose logs backend | grep "rimappatura fusi"`. Il primo passo parte all'avvio, poi uno a ogni intervallo. Ogni passo scrive un riassunto: modalità, id in uso, coppie trovate fra gli id in uso (`fusi`) e nel catalogo intero (`coppie_catalogo`: tutte le righe di `bando_fusione`; `None` se la lettura non è riuscita), scartate, errori, totali per tabella, le prime 50 coppie (doppione → master) e le separazioni (`separazioni_rilevate`, righe `ripristinate` e `in_conflitto`, e fra queste i `conflitti_definitivi`). In `prova` nulla viene modificato. Il riassunto è a **WARNING** quando il passo ha errori, coppie scartate o righe ripristinate (in `prova`: da ripristinare), altrimenti a INFO: `docker compose logs backend | grep "WARNING bandofit.catalogo_scheduler"` mostra solo i passi da guardare. Gli errori di una singola coppia (per esempio un salvataggio concorrente dello stesso bando) si ripetono al passo dopo.
3. Se i numeri sono plausibili e non ci sono errori che si ripetono, `RIMAPPATURA_FUSI_MODALITA=attiva`, `docker compose up -d backend` e il controllo con `env | grep`.
4. Dopo l'accensione: il primo passo in `attiva` parte all'avvio: il suo riassunto inizia con `rimappatura fusi (attiva)` e riporta i totali delle righe spostate. Dal passo successivo i totali tornano a zero, salvo le call «in collisione», che ricompaiono a ogni passo finché l'azienda ha una call attiva anche sul master.
5. **Prima di ogni lotto di fusioni o di una separazione nel catalogo** si torna a `prova` (`.env` e `docker compose up -d backend`). Si controllano i riassunti dei passi con il catalogo aggiornato (in `prova` il passo rileva anche le separazioni e riporta a WARNING le righe che ripristinerebbe, senza scrivere) e solo dopo si torna ad `attiva`.

Per spegnerla: `RIMAPPATURA_FUSI_MODALITA=spenta` e `docker compose up -d backend`. Le righe già spostate restano sul master.

#### Ripristino dopo la separazione di un doppione

Servono le migration 0044 (`fn_ripristina_rimappatura`), 0045 (`fn_doppioni_rimappati`) e 0046 (`fn_ripristina_rimappatura_voci`, il percorso automatico), applicate dopo la 0043. **Lo fa il passo periodico.** A ogni passo, **prima** della rimappatura (così, se nello stesso aggiornamento del catalogo il doppione si separa e il suo master viene fuso in un altro bando, le righe tornano al doppione invece di seguire il master), legge i doppioni con righe rimappate non ancora ripristinate né marcate come conflitto definitivo (`fn_doppioni_rimappati`) e li cerca in `bando_fusione`: chi manca è stato separato. Si ripristina **solo se il doppione è di nuovo in `bando_pubblico`** (lettura per id), mai verso un id assente dalla vista: se non c'è ancora, le righe restano sul master e il passo successivo riprova (riga INFO «separato ma non ancora nella vista»). In `attiva` il passo rilegge `bando_fusione` per quel doppione subito prima di scrivere e chiama `fn_ripristina_rimappatura_voci` in scrittura nel percorso automatico; in `prova` la chiama in prova e il riassunto riporta a WARNING le righe che ripristinerebbe. La funzione non cancella mai righe: reinserisce quelle eliminate e riporta al doppione quelle aggiornate o convertite. **Segue le catene**: se il master del doppione è stato a sua volta fuso in un altro bando (D → M, poi M → M2), la riga che sta sull'ultimo master della catena vale come riga del doppione e torna a D; le voci della catena (M → M2) si chiudono insieme, così una separazione successiva di M non le riprova. Le righe `in_conflitto` restano sul master, si contano nel riassunto e non fermano il passo; un doppione che resta in conflitto per più di 24 passi consecutivi compare nel log a WARNING una volta al giorno («da vedere a mano»): si segue il punto 4 della procedura qui sotto. **Conflitti definitivi**: se il doppione è già nello stesso ambito (stesso utente e stessa azienda) perché l'utente l'ha salvato di nuovo, ha rimesso la scadenza in calendario o simili, in `attiva` la funzione scrive una voce `catalogo.conflitto_definitivo` che cita la voce d'origine: il passo non la riprova più (nemmeno se poi l'utente toglie il doppione) e il doppione esce dall'elenco se non ha altre voci aperte. Il riassunto li conta in `conflitti_definitivi` (compresi in `in_conflitto`) con una riga INFO «conflitti resi definitivi» (in `prova`: «da rendere definitivi», senza scrivere nulla); non contano per l'avviso dei 24 passi. Gli altri conflitti restano ritentati a ogni passo.

La procedura manuale resta come ripiego (per esempio con la 0045 o la 0046 non ancora applicate, o con la rimappatura spenta). Si lavora dallo SQL Editor del primario:

1. Nel `.env` `RIMAPPATURA_FUSI_MODALITA=prova` (o `spenta`), poi `docker compose up -d backend`. Verificare che il catalogo non elenchi più il doppione in `bando_fusione`, altrimenti il passo successivo lo rimappa di nuovo. Il catalogo si legge solo con la chiave anon, dal container del backend; il comando deve stampare `[]`:
   ```bash
   docker compose exec backend python -c "import os, urllib.request as u; r = u.Request(os.environ['SECONDARY_SUPABASE_URL'] + '/rest/v1/bando_fusione?select=bando_id&bando_id=eq.<doppione>', headers={'apikey': os.environ['SECONDARY_SUPABASE_ANON_KEY']}); print(u.urlopen(r).read().decode())"
   ```
   Verificare anche che il doppione sia **di nuovo in `bando_pubblico`**: lo stesso comando con `/rest/v1/bando_pubblico?select=id&id=eq.<doppione>` deve stampare una riga (`[{"id": <doppione>}]`). Se stampa `[]` **non si ripristina**: le righe restano sul master e si ricontrolla al passo successivo o dopo l'avviso del produttore del catalogo. Si può tornare ad `attiva`: senza la riga in `bando_fusione` la rimappatura non tocca più il doppione, e il passo ripristinerà da solo quando il bando tornerà nella vista.
2. Prova, che non scrive nulla (è il default): `select public.fn_ripristina_rimappatura(<doppione>, '<dal>'::timestamptz);`. Il risultato riporta, per tabella, i conteggi `{ripristinate, in_conflitto}`. `<dal>` è un istante anteriore alla prima rimappatura del doppione; nel dubbio `'-infinity'`.
3. Ripetere il controllo su `bando_pubblico` del punto 1 (fra la prova e la scrittura può passare tempo), poi la scrittura: `select public.fn_ripristina_rimappatura(<doppione>, '<dal>'::timestamptz, false);`. Deve restituire gli stessi conteggi della prova. Rilanciarla non riapplica nulla. Ogni riga ripristinata lascia una voce `catalogo.ripristino_rimappatura` in `audit_log`.
4. Le righe `in_conflitto` non vengono toccate e restano da vedere a mano. Sono le voci `catalogo.rimappatura_fuso` del doppione che nessuna voce `catalogo.ripristino_rimappatura` cita (in quest'ultima, `payload->'voce'` è l'id della voce d'origine). Se più doppioni dello stesso master tornano separati, si ripristinano tutti, poi si rilancia il ripristino (prova, poi scrittura) di quelli con righe `in_conflitto`. Un preferito o una scadenza restano sul master finché non è ripristinata la riga di un altro doppione dello stesso master che la rimappatura aveva eliminato per causa loro. Se quell'altro doppione resta fuso, la riga resta sul master e va vista a mano. Dalla 0046 anche questa regola segue la catena: vale per ogni master su cui la riga è passata. La funzione manuale (`fn_ripristina_rimappatura`) segue le catene come il passo, ma **ignora i marcatori** `catalogo.conflitto_definitivo` e non ne scrive: decide chi la lancia.

   **Chiusura a mano di un conflitto permanente.** Alcuni conflitti non si risolvono da soli: la riga è stata cancellata dall'utente, l'evento convertito è stato modificato dopo, l'utente o l'azienda non esistono più. Finché una voce resta non citata, il passo riprova a ogni giro (letture sul catalogo e una chiamata alla funzione) e dopo 24 passi avvisa una volta al giorno. Si elencano le voci aperte del doppione:
   ```sql
   select v.id, v.created_at, v.payload->>'tabella' as tabella, v.payload->>'operazione' as operazione,
          v.payload->>'id' as riga, v.target_user_id, v.payload->>'company_profile_id' as azienda,
          exists (select 1 from public.audit_log m
                  where m.action = 'catalogo.conflitto_definitivo' and m.payload->'voce' = to_jsonb(v.id)) as definitiva
   from public.audit_log v
   where v.action = 'catalogo.rimappatura_fuso' and v.payload->'doppione' = to_jsonb(<doppione>::int)
     and not exists (select 1 from public.audit_log r
                     where r.action = 'catalogo.ripristino_rimappatura' and r.payload->'voce' = to_jsonb(v.id))
   order by v.id;
   ```
   Per ciascuna si controlla la riga nella sua tabella (`riga` è l'id); `definitiva` indica una voce che il passo non riprova più (scelta dell'utente: in genere va solo chiusa). Se non deve tornare indietro, si chiude la voce scrivendo a mano la voce di ripristino che la cita: da quel momento né la funzione né il passo la riprovano, e quando tutte le voci del doppione sono citate il doppione esce da `fn_doppioni_rimappati`.
   ```sql
   insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
   select null, 'catalogo.ripristino_rimappatura', v.target_user_id, v.family_parent_id,
          jsonb_build_object('tabella', v.payload->'tabella', 'id', v.payload->'id',
                             'operazione', 'chiusa_a_mano', 'company_profile_id', v.payload->'company_profile_id',
                             'doppione', v.payload->'doppione', 'master', v.payload->'master',
                             'voce', v.id, 'prima', null, 'motivo', '<perché non si ripristina>')
   from public.audit_log v where v.id = <id della voce>;
   ```
   Attenzione: chiudere una voce `eliminata` sblocca il ripristino delle righe `aggiornata` degli altri doppioni dello stesso master nello stesso ambito (il vincolo descritto sopra): si chiude solo se quella riga non deve davvero tornare.
5. Solo dopo, se serve, si torna ad `attiva`.

**Limite noto: il ripristino automatico e le scelte dell'utente.** Dalla 0046 il caso tipico è coperto: se dopo una separazione un utente, che aveva il doppione tra i preferiti (portato sul master dalla rimappatura), lo **risalva** e poi lo **toglie**, il passo non riporta più la riga dal master al doppione contro la sua scelta: la prima volta che in `attiva` vede il conflitto («doppione già nell'ambito») lo rende definitivo e non lo riprova. Lo stesso vale per una scadenza in calendario, per una riga eliminata dalla rimappatura e per un evento convertito. Anche la catena di fusioni (D → M, poi M → M2) si segue da sola: la riga nata su D torna a D. Restano tre casi: (1) in `prova` i marcatori non si scrivono: se l'utente rimette e toglie il doppione mentre il passo è in `prova` (per esempio nella finestra del punto 5 dell'accensione), al primo passo in `attiva` la riga torna al doppione; in `prova` il riassunto lo annuncia con la riga INFO «conflitti da rendere definitivi» per quel doppione, e si può chiudere la voce a mano prima di tornare ad `attiva`; (2) se l'utente rimette e toglie il doppione fra un passo e l'altro, il passo non vede mai il conflitto e la riga torna al doppione; (3) se un anello della catena ha eliminato o convertito la riga (l'utente aveva già l'ultimo master), la riga resta in conflitto e si vede a mano con il punto 4. Come rimediare se la riga è tornata al doppione contro la scelta dell'utente: la voce `catalogo.ripristino_rimappatura` scritta dal passo ha in `payload->'prima'` lo stato sovrascritto (`bando_id` e `bando_slug` del master): si riporta la riga `payload->>'id'` a quei valori con un `update`; la voce d'origine resta citata e il passo non la riprova.

### 2. Storico dei bilanci e bilancio ufficiale

Prerequisiti:
- le migration 0032 e 0033 applicate (fanno parte del rilascio 0032-0042);
- nella console openapi di **produzione** (non la sandbox) attivati Company **IT-advanced** e **Visure Camerali** (`bilancio-ottico`, `impresa`), con il credito controllato: senza, falliscono solo questi prodotti;
- `OPENAPI_ENV=production` con le chiavi di produzione.

La procedura non presuppone la verifica in sandbox G1 (`docs/partenariati.md` §2.1 T1): le prove dei passi 3 e 4 su un'azienda propria sono il primo controllo sulle risposte reali.

1. Nel `.env`: `BILANCI_STORICO_ATTIVO=true`, poi `docker compose up -d backend` e il controllo con `env | grep`.
2. Controlli dopo l'accensione:
   - nessun errore nei log d'avvio;
   - nella pagina Azienda compare «Recupera i bilanci»;
   - parte anche il failsafe del bilancio ufficiale, un giro ogni 10 minuti. Con openapi non configurato non parte e lo dice con un WARNING («failsafe non avviato, openapi non configurato»): nessuna via fa avanzare le richieste aperte, né il failsafe né la lettura della pagina, e restano aperte finché openapi non torna configurato. Esamina solo le richieste nate nell'ambiente openapi in uso. Nei log scrive `bilancio ufficiale: failsafe, N richieste aperte esaminate, M saltate perché nate nell'altro ambiente openapi` solo quando N o M sono diversi da 0. Se M è maggiore di 0 ci sono richieste aperte nate nell'altro ambiente (per esempio dopo un cambio di `OPENAPI_ENV`), che il failsafe non interroga: si chiudono tornando all'ambiente in cui sono nate, oppure a mano con un accredito.
3. Prova su un'azienda propria (lo storico IT-advanced costa circa 0,10 €):
   - importazione da partita IVA: l'anteprima mostra gli anni dei bilanci recuperati;
   - dopo la conferma, la sezione Bilanci li riporta anno per anno;
   - un secondo import della stessa azienda non ripaga lo storico: nel registro consumi (`api_usage_events`, service `IT-advanced`) non compare una riga nuova.
4. **Solo dopo** questi controlli: prezzo e attivazione dell'addon `bilancio-ufficiale` in AdminAddon. A storico spento non ha senso, e un addon gratuito non si può attivare. Poi la prova su un'azienda propria:
   - acquisto di 1 unità;
   - richiesta con un **anno esplicito** (4,50 € al provider);
   - esito di solito entro 15 minuti, con il PDF scaricabile e i numeri nella sezione Bilanci.
5. Da tenere d'occhio: `docker compose logs backend | grep "chiusa senza rimborso"`. Ogni richiesta chiusa senza rimborso automatico (scaduta, o esito ignoto oltre 24 ore) va valutata per un accredito manuale.

Per spegnerlo:
1. Prima si disattiva l'addon `bilancio-ufficiale` in AdminAddon: niente nuovi acquisti né richieste.
2. Poi si aspetta che non restino richieste aperte. Controllo dallo SQL Editor:
   ```sql
   select stato, count(*) from company_bilancio_richieste
   where stato in ('in_invio', 'in_lavorazione', 'esito_ignoto') group by stato;
   ```
   A storico spento le richieste aperte non avanzano più, né dal failsafe né dalle letture.
3. Infine `BILANCI_STORICO_ATTIVO=false`, `docker compose up -d backend` e il controllo con `env | grep`.

Le rotte tornano 404. I bilanci già registrati restano nella sezione Bilanci, ma i PDF del bilancio ufficiale non si scaricano finché la funzione non si riaccende.

### 3. Partenariati

Prerequisiti:
- le migration 0032-0042 applicate (le richiede già il rilascio);
- `ANTHROPIC_API_KEY` impostata: senza chiave le estrazioni delle regole e le bozze AI rispondono 503;
- l'invio email già configurato (`SMTP_*` o `RESEND_API_KEY`, `EMAIL_FROM`), con `FRONTEND_URL` e `API_PUBLIC_URL` per i link: digest ed email di evento usano lo stesso canale degli alert;
- nella console Anthropic, un **tetto di spesa mensile** sull'organizzazione della chiave. La chiave è la stessa dell'AI-check, quindi il tetto vale per entrambi: va dimensionato sui budget giornalieri del modulo (con i default circa 7 $ al giorno) più il consumo dell'AI-check. Raggiunto il tetto, il provider rifiuta le chiamate, anche quelle dell'AI-check;
- i testi legali segnaposto: vedi «Partenariati: testi legali da rivedere» sopra.

1. Nel `.env`: `PARTENARIATI_ATTIVO=true`. I budget giornalieri, in centesimi di USD e fail-closed, restano ai default se non si impostano: `PARTENARIATO_BUDGET_CENTS_GIORNO=500` (estrazioni delle regole), `PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI=200` (bozza del profilo, proposte della call, bozze dei documenti), `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO=0` (batch notturno spento). Poi `docker compose up -d backend` e il controllo con `env | grep`.
2. In AdminPiani, che mostra i campi solo a modulo acceso: per i piani diversi da Gratuito, Smart, Pro e Advisor (per esempio quelli su misura) si impostano «Call di partenariato attive», «Candidature al mese» e «Bozze di documenti al mese». Partono da 0, cioè non incluse: finché restano a 0, i clienti di quei piani non creano call né candidature.
3. Controlli dopo l'accensione:
   - nessun errore nei log d'avvio;
   - dal browser, con un utente loggato: nella risposta di `GET /api/v1/me` (strumenti per sviluppatori → Rete) `funzioni.partenariati` è `true`;
   - nel menu dell'app compare «Partenariati», e per gli admin anche in Admin;
   - lo scheduler del modulo scrive una riga al giorno in `partenariati_runs`. Se l'accensione è dopo le 05:30 (ora di Roma), la riga di oggi arriva subito dopo l'avvio, altrimenti alle 05:30. Dallo SQL Editor: `select giorno, riepilogo from partenariati_runs order by giorno desc limit 3;`. Nel `riepilogo` nessun passo deve valere `"errore"`;
   - disiscrizione: `curl -s -o /dev/null -w '%{http_code}\n' 'https://bandofit.example.com/api/v1/partenariati/email/unsubscribe?token=prova&tipo=digest'` risponde 200 (pagina di conferma: il GET non cambia nulla). Alla prima email ricevuta (digest del lunedì alle 08:30 o email di evento), il link in fondo porta alla stessa pagina: dopo il bottone, nelle Preferenze l'email risulta disattivata.
4. Da tenere d'occhio: le regole salvate prima del prompt v3 risultano da aggiornare e si riestraggono a pagamento all'apertura della sezione, dentro il budget del giorno. Il batch notturno resta spento finché il suo budget è 0.

Per fermare solo la spesa AI senza spegnere il modulo: `PARTENARIATO_BUDGET_CENTS_GIORNO=0` e `PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI=0`, poi `docker compose up -d backend`. Le nuove chiamate al modello vengono rifiutate, il resto del modulo funziona.

Per spegnerlo: `PARTENARIATI_ATTIVO=false`, poi `docker compose up -d backend` e il controllo con `env | grep`.
- Le rotte del modulo tornano 404, il menu sparisce, scheduler e digest non partono.
- La disiscrizione `/api/v1/partenariati/email/unsubscribe` continua a rispondere, perché un link già inviato deve funzionare.
- I dati restano; i limiti in AdminPiani restano salvati ma nascosti.

## Operazioni ricorrenti

```bash
# aggiornamento all'ultima versione
# (prima le migration nuove, se ce ne sono: «Migration del DB primario» qui sotto)
git pull && docker compose up -d --build

# log
docker compose logs -f backend
docker compose logs -f frontend

# cambiare porta: modifica .env, poi
docker compose up -d

# stop
docker compose down
```

> **Dopo il rilascio del 01/10 (rilievi minori)**: al primo AI-check su un bando con etichette degli allegati sporche, lunghe o ripetute la cache delle estrazioni si rinnova una volta (una chiamata a pagamento in più per quel bando). È atteso.

### Migration del DB primario (prima del deploy)

Le migration nuove (`supabase/migrations/NNNN_nome.sql`, segnalate con ⚠️ nel changelog) si eseguono dallo SQL Editor del primario, in ordine di numero, **prima** del deploy del backend che le usa. Per il rilascio dei bilanci e del modulo partenariati (branch `feat/partenariati`) servono **tutte** le migration dalla 0032 alla 0042, in ordine, prima del deploy e qualunque sia lo stato dei flag: il backend legge colonne della 0036, 0041 e 0042 anche a modulo spento. Poi la **0043** (rimappatura dei bandi fusi), che serve prima di impostare `RIMAPPATURA_FUSI_MODALITA` diversa da `spenta`, e la **0044** (ripristino della rimappatura: aggiunge solo una funzione), da applicare prima di passare ad `attiva`, e la **0045** (doppioni rimappati non ancora ripristinati: aggiunge solo una funzione di lettura), che serve al passo per rilevare le separazioni; senza, il passo conta un errore a ogni giro ma rimappa lo stesso. Poi la **0046** (ripristino lungo le catene di fusioni e conflitti definitivi: una funzione nuova e due ridefinite, nessuna tabella; si può rieseguire), da applicare **prima** del deploy del backend che la chiama: senza, per ogni doppione separato il passo conta un errore e non ripristina nulla, ma rimappa lo stesso (vedi «Accensione delle funzioni» sopra). Poi la **0047** (indici parziali di `audit_log` per le funzioni della rimappatura: solo indici, nessuna funzione o tabella toccata; si può rieseguire), che si applica come le altre, prima del deploy: è **solo prestazioni**, senza tutto funziona come prima (con un registro grande il ripristino e l'elenco dei doppioni rimappati scorrono tutto `audit_log`). La costruzione degli indici blocca per pochi istanti le scritture su `audit_log`: se va in timeout sul lock, `rollback;` e la si riesegue. Ogni file va eseguito dentro una transazione, con un limite all'attesa dei lock subito dopo `begin;`:

```sql
begin;
set local lock_timeout = '5s';
-- contenuto del file NNNN_nome.sql
commit;
```

Il file è transazionale: se va in timeout sul lock (una tabella occupata da una richiesta in corso) o si ferma con un errore, non resta applicato a metà; si esegue `rollback;` e poi lo si riesegue così com'è. Un file che contiene già `begin;`/`commit;` propri o istruzioni che non possono stare in una transazione (per esempio `create index concurrently`) si esegue così com'è, senza questo involucro. Dopo l'ultima migration, e prima del deploy, si ricarica lo schema di PostgREST, così le API vedono subito tabelle e funzioni nuove:

```sql
notify pgrst, 'reload schema';
```

## Risoluzione problemi

- **502 dal proxy** → `docker compose ps` (container su?), porte in `.env` allineate col vhost.
- **Errore CORS nel browser** → `CORS_ORIGINS` deve essere l'origine esatta del frontend (con `https://`, senza slash finale). Con il proxy stessa-origine di sopra non dovrebbe mai comparire.
- **Login ok ma dati vuoti/errore** → `VITE_API_BASE_URL` sbagliato (ricorda: rebuild del frontend dopo la modifica).
- **Link d'invito che non reindirizza** → Redirect URLs su Supabase (passo 4).
- **`docker compose logs backend`** mostra anche le email loggate quando `RESEND_API_KEY` è vuota.
