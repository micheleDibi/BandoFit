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
| `OPENAPI_EMAIL` + `OPENAPI_API_KEY` + `OPENAPI_ENV` | credenziali openapi.it per l'import dei dati aziendali e la verifica CF (da console.openapi.com; le API "Company" e "Risk" vanno attivate una tantum dalla Libreria API; dal modulo bilanci anche **IT-advanced** e **Visure Camerali** (`bilancio-ottico`, `impresa`) — se mancano fallisce solo il prodotto nuovo, grazie ai token separati; dopo il deploy, e solo a storico acceso (`BILANCI_STORICO_ATTIVO`, riga sotto), prezzo e attivazione dell'addon `bilancio-ufficiale` si fanno da AdminAddon). `OPENAPI_ENV=production` in deploy; le chiavi sandbox/produzione sono diverse. Vuote = importazione disattivata, il resto dell'app funziona. **Ogni import consuma credito** (IT-full ~0,30 € + IVA) |
| `BILANCI_STORICO_ATTIVO` | storico dei bilanci (IT-advanced nell'anteprima dell'import e in «Recupera i bilanci») e bilancio ufficiale, entrambi a pagamento: **spento di default**. Da spento l'anteprima non chiama IT-advanced, le rotte del recupero e del bilancio ufficiale rispondono 404 e il frontend non le mostra; l'import IT-full e la lettura dei bilanci già salvati restano come sono. Si accende **dopo G1**, la verifica in sandbox delle risposte reali (`docs/partenariati.md` §14). **Azione manuale**: `docker-compose.yml` non passa la variabile al container, per accenderla va aggiunta all'`environment` del servizio backend. Prima di rispegnerla non devono esserci richieste di bilancio ufficiale ancora aperte: a storico spento non avanzano |
| `ANTHROPIC_API_KEY` + `AI_CHECK_MODEL` | chiave API Anthropic per l'AI-check (da console.anthropic.com); modello default `claude-sonnet-5`. Vuota = AI-check disattivato, il resto dell'app funziona. **Ogni report consuma credito API** (~0,10–0,20 $; meno con l'estrazione del bando in cache). Le quote per gli utenti si impostano dai piani (campo AI-check) |
| `PARTENARIATI_ATTIVO` (+ `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO`) | modulo partenariati (migration 0034-0042, da eseguire tutte in ordine dallo SQL Editor del primario **prima** del deploy, anche a modulo spento: il backend legge colonne della 0036, 0041 e 0042 qualunque sia il flag; procedura in «Migration del DB primario» sotto): spento di default. **Azione manuale**: `docker-compose.yml` non passa queste variabili al container, per accenderlo vanno aggiunte a mano all'`environment` del servizio backend. Budget giornalieri fail-closed in centesimi USD: `PARTENARIATO_BUDGET_CENTS_GIORNO` (500, estrazioni delle regole), `PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI` (200), batch notturno `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO` (0 = spento; consigliato 300 se lo accendi). Usa `ANTHROPIC_API_KEY`: senza chiave le estrazioni e la bozza AI del profilo rispondono 503. Profilo partner (migration 0035): `PARTNER_BOZZA_AI_LIMITE_GIORNO` (3 per azienda), `PARTNER_BOZZA_AI_LIMITE_UTENTE_GIORNO` (10 per titolare), `PARTNER_BOZZA_AI_MAX_TOKENS`, `PARTNER_BOZZA_AI_TIMEOUT_SECONDS`, `PARTNER_BOZZA_AI_STALE_MINUTI`: facoltative, i default sono quelli di produzione (se servono, stessa azione manuale sull'`environment`). Call di partenariato (migration 0036 e 0037): `PARTNER_CALL_BOZZE_MAX` (5 bozze per azienda), `PARTNER_CALL_AI_LIMITE_GIORNO` (10 proposte AI al giorno per call e servizio), `PARTNER_CALL_AI_LIMITE_OWNER_GIORNO` (30 per titolare su tutti i servizi delle call), `PARTNER_CALL_AI_MAX_TOKENS`, `PARTNER_CALL_AI_TIMEOUT_SECONDS`, `PARTNER_CALL_AI_STALE_MINUTI`, `PARTNER_CALL_SCADENZA_DEFAULT_GIORNI` (60), `PARTNER_SEGNALAZIONI_LIMITE_GIORNO` (10): facoltative, stessi default di produzione e stessa azione manuale se servono. I limiti di call attive e candidature stanno sui piani (AdminPiani), non nell'`.env`. Matching, notifiche e digest (migration 0038): `PARTENARIATO_PESO_COPERTURA` / `_AFFINITA` / `_COMPLEMENTARITA` / `_COMPLETEZZA` / `_ROTAZIONE` (0.50/0.20/0.10/0.10/0.10), `PARTENARIATO_PENALITA_DATO_MANCANTE` (5), `PARTENARIATO_PENALITA_MAX` (20), `PARTENARIATO_SUGGERITI_PAGINA` (20), `PARTENARIATO_NOTIFICHE_TOP_K` (20), `PARTENARIATO_NOTIFICHE_SOGLIA` (50), `PARTENARIATO_NOTIFICHE_TETTO_SETTIMANA` (3), `PARTENARIATO_INDICE_TTL_SECONDS` (60), `PARTENARIATO_DIGEST_GIORNO` (0 = lunedì), `PARTENARIATO_DIGEST_ORA` (`08:30`): facoltative, stessi default di produzione e stessa azione manuale se servono. Il digest settimanale usa l'email già configurata (`SMTP_*`/`RESEND_API_KEY`, `EMAIL_FROM`) con `FRONTEND_URL` e `API_PUBLIC_URL` per i link, come gli alert sui bandi: nessuna variabile nuova per l'invio. La disiscrizione pubblica `/api/v1/partenariati/email/unsubscribe` risponde anche a modulo spento. Candidature, inviti e chat (migration 0039): `PARTNER_INVITO_TTL_GIORNI` (14), `PARTNER_INVITI_MAX_PER_CALL` (30), `PARTNER_CANDIDATURE_LIMITE_GIORNO` (10), `PARTNER_INVITI_LIMITE_GIORNO` (50), `PARTNER_MESSAGGI_LIMITE_ORA` (120): facoltative, stessi default di produzione e stessa azione manuale se servono; le email di evento usano lo stesso canale del digest. Consulto dalla call, moderazione, admin e verifica dell'identità (migration 0041): nessuna variabile nuova (le email di moderazione usano lo stesso canale; il passo notturno `ricalcolo_validazioni` gira nella run dello scheduler del modulo). Bozze AI dei documenti (migration 0042): `PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO` (10 bozze al giorno per titolare, anche con un piano illimitato), `PARTNER_BOZZE_DOCUMENTO_MAX_TOKENS` (8000), `PARTNER_BOZZE_DOCUMENTO_TIMEOUT_SECONDS` (150), `PARTNER_BOZZE_DOCUMENTO_STALE_MINUTI` (10): facoltative, stessi default di produzione e stessa azione manuale se servono; il limite mensile sta sui piani (AdminPiani), il failsafe `failsafe_bozze` gira nella run dello scheduler del modulo; il PDF usa lo stesso motore degli altri export. Prima di accendere il flag in produzione i testi legali segnaposto vanno rivisti dal legale (vedi «Partenariati: testi legali da rivedere» sotto) |
| `REVOLUT_SECRET_KEY` + `REVOLUT_ENV` + `REVOLUT_WEBHOOK_SECRET` | pagamenti (Revolut Merchant API, migration 0026): chiave segreta del Merchant account (Revolut Business → APIs → Merchant API), ambiente (`production` in deploy: la **sandbox è un account Business separato** — sandbox-business.revolut.com — con chiavi **diverse**, da far corrispondere all'ambiente) e signing secret `wsk_...` restituito alla registrazione del webhook via API (verifica della firma HMAC — vedi «Pagamenti» sotto). Chiave vuota = modulo pagamenti disattivato (503), il resto dell'app funziona |
| `PAYMENT_SCHEDULER_ATTIVO` / `PAYMENT_ORA_ESECUZIONE` | scheduler dei pagamenti (preavvisi, rinnovi automatici, retry, fine grazia, recupero righe del registro fatture): attivo di default, run giornaliera alle `06:00` locali (Europe/Rome). `false` = nessun rinnovo/downgrade automatico (utile in sviluppo) |
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

Prima di accendere `PARTENARIATI_ATTIVO` in produzione vanno rivisti con il legale, e poi aggiornati nel codice facendo salire la loro versione:
- l'informativa per il profilo partner e quella per il referente (`partenariato_informativa.py`, versione `2026-10-bozza-2`, marcate «BOZZA — DA RIVEDERE CON IL LEGALE»): descrivono la verifica dell'identità da parte dell'admin, il nome solo se scelto e verificato, la rivelazione solo tra aziende verificate, l'accesso della moderazione ai messaggi segnalati e la conservazione; restano da completare titolare del trattamento e contatto privacy, la base giuridica di verifica e moderazione e la durata di conservazione del registro delle verifiche (segnaposto tra parentesi quadre). Chi aveva acconsentito alla versione precedente vede un banner di riconferma: se il legale chiede un nuovo consenso esplicito invece del banner, va cambiata la regola in `partner_profile_service`;
- lo **statement of reasons** della moderazione (`partenariato_moderazione_testi.py`, marcato «BOZZA — DA RIVEDERE CON IL LEGALE»): riferimento puntuale ai Termini d'uso, fondamento normativo, indirizzo per i riesami delle decisioni d'ufficio e delle restrizioni nate da un ricorso;
- i testi del ricorso e delle vie di ricorso esterne e la nota di segnalazione nella UI (`lib/copy.ts`: `MODERAZIONE_COPY`, `CALL_COPY`);
- i Termini d'uso della piattaforma, con la sezione «Moderazione dei contenuti» a cui gli statement rimandano;
- dal WP10, il **disclaimer delle bozze dei documenti** (`DISCLAIMER` in `schemas/partenariato_bozze.py` e `BOZZE_COPY.disclaimer` in `lib/copy.ts`, stesso testo) e le istruzioni di stesura dei tre documenti (`partenariato_bozze_prompts.py`, `BOZZE_PROMPT_VERSION`): una modifica delle istruzioni fa salire la versione.

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

### Migration del DB primario (prima del deploy)

Le migration nuove (`supabase/migrations/NNNN_nome.sql`, segnalate con ⚠️ nel changelog) si eseguono dallo SQL Editor del primario, in ordine di numero, **prima** del deploy del backend che le usa. Per il rilascio dei bilanci e del modulo partenariati (branch `feat/partenariati`) servono **tutte** le migration dalla 0032 alla 0042, in ordine, prima del deploy e qualunque sia lo stato dei flag: il backend legge colonne della 0036, 0041 e 0042 anche a modulo spento. Ogni file va eseguito dentro una transazione, con un limite all'attesa dei lock subito dopo `begin;`:

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
