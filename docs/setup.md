# Setup

> Documento in costruzione: viene completato con l'avanzare dello sviluppo.

## Prerequisiti

- Node.js 20+
- Python 3.12+
- Un progetto Supabase per il **DB primario** (da creare, vedi sotto)
- Credenziali del **DB secondario** (URL + anon key), fornite dall'amministratore del catalogo bandi

## 1. Creazione del progetto Supabase primario

1. Su [supabase.com](https://supabase.com) creare un nuovo progetto (regione consigliata: EU).
2. Da **Project Settings → API** copiare: Project URL, `anon` key, `service_role` key.
3. **Authentication → Sign In / Providers → Email** → opzione **"Confirm email"**:
   - **sviluppo locale**: disattivata (login permesso anche senza conferma; il link di conferma compare comunque nei log del backend);
   - **produzione**: attivata — è l'enforcement che blocca il login ai non confermati. Tutti i link email (conferma, recupero password, inviti) sono **token di dominio** gestiti dal backend: **non servono Redirect URLs** su Supabase.
4. **SQL Editor**: eseguire in ordine i file di `supabase/migrations/`.

## 2. Variabili d'ambiente

- `backend/.env` (da `backend/.env.example`): URL e chiavi del primario e del secondario; `FRONTEND_URL` (redirect degli inviti famiglia); per le email di invito agli utenti esistenti, **SMTP** (`SMTP_HOST/PORT/USER/PASSWORD`, es. casella OVH) oppure `RESEND_API_KEY`, più `EMAIL_FROM` (tutto vuoto = le email vengono solo loggate, utile in sviluppo — gli inviti a email nuove usano comunque le email native di Supabase).
- **openapi.it** (import dati aziendali + verifica CF): `OPENAPI_EMAIL`, `OPENAPI_API_KEY`, `OPENAPI_ENV`. In sviluppo usare `OPENAPI_ENV=sandbox` con la **chiave di test** (gratuita, dati finti); in produzione `production` con la chiave reale — ogni import IT-full costa ~0,30 € + IVA. Le API "Company" e "Risk" vanno attivate una tantum dalla **Libreria API** di console.openapi.com; dal modulo bilanci servono anche **Company IT-advanced** (storico dei bilanci, ~0,10 € + IVA a import) e **Visure Camerali** (`bilancio-ottico`, `impresa`: bilancio ufficiale on-demand, 4,50 € + IVA a richiesta, coperti dal prezzo dell'addon `bilancio-ufficiale`), da attivare sia in sandbox sia in produzione. Storico e bilancio ufficiale funzionano solo con `BILANCI_STORICO_ATTIVO=true` (default `false`, accensione in `docs/deploy.md`, «Accensione delle funzioni»; da spento l'anteprima dell'import non chiama IT-advanced, le rotte del recupero e del bilancio ufficiale rispondono 404 e il frontend non le mostra); in sviluppo lo si accende nel `.env` per provarli in sandbox. L'addon `bilancio-ufficiale` nasce **inattivo** con la migration 0033: prezzo e attivazione si impostano da AdminAddon (non può essere attivato gratis) e hanno senso solo a storico acceso. Il client usa un token separato per ciascun gruppo: un'API non attivata fa fallire solo il suo prodotto. Per verificare le risposte reali in sandbox c'è `backend/scripts/verifica_sandbox_openapi.py` (legge `OPENAPI_SANDBOX_EMAIL`/`OPENAPI_SANDBOX_API_KEY` dalla shell, usa solo gli host di test e salva fixture anonimizzate). Credenziali vuote = pulsanti di import/verifica disattivati, il resto dell'app funziona normalmente.
- **AI-check** (API Anthropic): `ANTHROPIC_API_KEY` (da console.anthropic.com) e `AI_CHECK_MODEL` (default `claude-sonnet-5`). Chiave vuota = AI-check disattivato (503), il resto dell'app funziona. **Ogni report costa ~0,10–0,20 $ di API** (meno se l'estrazione del bando è già in cache; il ragionamento del modello è conteggiato come output): in sviluppo lasciare la chiave vuota se non serve.
- **Pagamenti** (Revolut Merchant API, migration 0026): `REVOLUT_SECRET_KEY` — chiave segreta del Merchant account (Revolut Business → APIs → Merchant API); vuota = modulo pagamenti disattivato (503), il resto dell'app funziona. `REVOLUT_ENV` (`sandbox`/`production`) — la **sandbox è un account Business separato** (sandbox-business.revolut.com) e le chiavi dei due ambienti sono **diverse**: la chiave deve corrispondere all'ambiente; in sviluppo usare sempre la sandbox. `REVOLUT_WEBHOOK_SECRET` — signing secret (`wsk_...`) restituito alla registrazione del webhook via API, serve alla verifica della firma HMAC degli eventi.
- **Partenariati** (modulo a flag, docs/partenariati.md): `PARTENARIATI_ATTIVO` (default `false`: rotte del modulo in 404 e nessuna voce nel frontend). Indipendente dal modulo, `BILANCI_STORICO_ATTIVO` (default `false`) accende storico IT-advanced e bilancio ufficiale (vedi il punto openapi.it). Le regole di partenariato usano `ANTHROPIC_API_KEY` con `PARTENARIATO_AI_MODEL` (default `claude-sonnet-5`), a **carico della piattaforma**, dentro budget giornalieri fail-closed in **centesimi di USD**: `PARTENARIATO_BUDGET_CENTS_GIORNO` (500) per le estrazioni, `PARTENARIATI_AI_BUDGET_CENTS_GIORNO_ALTRI` (200) per gli altri servizi AI del modulo, `PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO` (0 = batch notturno spento). Gli altri parametri (`PARTENARIATO_*` e `PARTENARIATI_*` in `core/config.py`: tetti di documenti, pagine, caratteri e tempi, limiti per utente, cooldown, TTL del claim, ora dello scheduler) hanno già i valori di produzione come default. Ogni estrazione costa tipicamente 0,15–0,20 $ (riserva al caso peggiore ~0,31 $); in sviluppo lasciare il flag spento se non serve. Profilo partner (WP4, migration 0035): la bozza AI del profilo usa lo stesso modello nel budget «altri», con `PARTNER_BOZZA_AI_LIMITE_GIORNO` (3 bozze al giorno per azienda), `PARTNER_BOZZA_AI_LIMITE_UTENTE_GIORNO` (10 al giorno per titolare, su tutte le sue aziende), `PARTNER_BOZZA_AI_MAX_TOKENS` (4000), `PARTNER_BOZZA_AI_TIMEOUT_SECONDS` (60) e `PARTNER_BOZZA_AI_STALE_MINUTI` (10, dopo i quali una bozza in corso è orfana); i default sono quelli di produzione. Una bozza costa pochi centesimi (riserva al caso peggiore ~0,05 $). Le informative del profilo partner sono **segnaposto**: vanno riviste con il legale e poi portate nel codice. Call di partenariato (WP5, migration 0036 e 0037): le proposte AI di posizioni e testi usano lo stesso modello nel budget «altri», con `PARTNER_CALL_AI_LIMITE_GIORNO` (10 proposte al giorno per call e per servizio), `PARTNER_CALL_AI_LIMITE_OWNER_GIORNO` (30 al giorno per titolare, su tutti i servizi delle call insieme), `PARTNER_CALL_AI_MAX_TOKENS` (6000), `PARTNER_CALL_AI_TIMEOUT_SECONDS` (90) e `PARTNER_CALL_AI_STALE_MINUTI` (10, dopo i quali una proposta in corso è orfana); in più `PARTNER_CALL_BOZZE_MAX` (5 bozze aperte per azienda), `PARTNER_CALL_SCADENZA_DEFAULT_GIORNI` (60, scadenza proposta alla pubblicazione, mai oltre quella del bando) e `PARTNER_SEGNALAZIONI_LIMITE_GIORNO` (10 segnalazioni al giorno per utente). Tutte facoltative, i default sono quelli di produzione. Una proposta costa pochi centesimi (riserva al caso peggiore ~0,07–0,09 $). Per creare e pubblicare call serve un piano che le includa: i limiti si impostano da AdminPiani (seed della 0036: Gratuito 0, Smart 1, Pro 3, Advisor 10 call attive). Matching, notifiche e digest (WP6, migration 0038): **nessuna chiamata a modelli e nessun costo**; parametri facoltativi con i default di produzione — pesi del punteggio `PARTENARIATO_PESO_COPERTURA` (0.50), `PARTENARIATO_PESO_AFFINITA` (0.20), `PARTENARIATO_PESO_COMPLEMENTARITA` (0.10), `PARTENARIATO_PESO_COMPLETEZZA` (0.10), `PARTENARIATO_PESO_ROTAZIONE` (0.10) (somma 1), `PARTENARIATO_PENALITA_DATO_MANCANTE` (5 punti per voce da verificare) e `PARTENARIATO_PENALITA_MAX` (20); `PARTENARIATO_SUGGERITI_PAGINA` (20 aziende suggerite per pagina); notifiche proattive alla pubblicazione `PARTENARIATO_NOTIFICHE_TOP_K` (20), `PARTENARIATO_NOTIFICHE_SOGLIA` (50, punteggio minimo) e `PARTENARIATO_NOTIFICHE_TETTO_SETTIMANA` (3 a settimana per azienda); `PARTENARIATO_INDICE_TTL_SECONDS` (60, validità dell'indice in memoria); digest `PARTENARIATO_DIGEST_GIORNO` (0 = lunedì) e `PARTENARIATO_DIGEST_ORA` (`08:30`, fuso `ALERT_FUSO`). Il digest usa il canale email già configurato (SMTP o Resend; senza nessuno dei due le email si registrano solo nei log, con l'indirizzo mascherato) e, per i link, `FRONTEND_URL` e `API_PUBLIC_URL` come gli alert sui bandi. Le chiavi dei collegamenti societari sono HMAC con una chiave derivata da `RATE_LIMIT_PEPPER`: in sviluppo, senza pepper, si usa la chiave di sviluppo. Candidature, inviti e chat (WP7, migration 0039): `PARTNER_INVITO_TTL_GIORNI` (14, giorni di validità di un invito, 1..90), `PARTNER_INVITI_MAX_PER_CALL` (30 inviti in attesa per call) e i limiti **anti-abuso** per utente `PARTNER_CANDIDATURE_LIMITE_GIORNO` (10 al giorno), `PARTNER_INVITI_LIMITE_GIORNO` (50 al giorno), `PARTNER_MESSAGGI_LIMITE_ORA` (120 all'ora): facoltative, i default sono quelli di produzione; la quota mensile delle candidature sta sui piani (AdminPiani), non nell'`.env`. Le email di evento (inviti, candidature, risposte, nuovi messaggi) usano lo stesso canale del digest. Nessuna chiamata a modelli. Bozze AI dei documenti (WP10, migration 0042: lettera d'intenti, NDA, term sheet): stesso modello nel budget «altri», con `PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO` (10 bozze al giorno per titolare, su tutte le sue aziende, anche con un piano illimitato), `PARTNER_BOZZE_DOCUMENTO_MAX_TOKENS` (8000), `PARTNER_BOZZE_DOCUMENTO_TIMEOUT_SECONDS` (150) e `PARTNER_BOZZE_DOCUMENTO_STALE_MINUTI` (10, dopo i quali una bozza in preparazione è orfana): facoltative, i default sono quelli di produzione; il limite mensile sta sui piani (AdminPiani, seed della 0042: Gratuito 0, Smart 3, Pro 10, Advisor 30), non nell'`.env`. Una bozza costa pochi centesimi (riserva al caso peggiore ~0,09 $).
- **Rimappatura dei bandi fusi** (migration 0043, `services/catalogo_scheduler.py`): `RIMAPPATURA_FUSI_MODALITA` (default `spenta`; `prova` conta e scrive il riassunto nel log senza modificare nulla; `attiva` sposta preferiti, eventi del calendario e call attive dai bandi fusi nel catalogo al loro bando principale; un valore diverso lascia la rimappatura spenta con un log ERROR all'avvio) e `RIMAPPATURA_FUSI_INTERVALLO_MINUTI` (60; un valore non intero o minore di 5 vale 60, con un log ERROR all'avvio, senza bloccarlo). In sviluppo lasciarla spenta: `prova` e `attiva` richiedono la 0043 applicata sul primario, altrimenti ogni passo registra un errore e non fa nulla. Procedura di accensione in [deploy.md](deploy.md).
- **Valutazione delle regole di partenariato** (WP3, CLI da `backend/`, non tocca l'app): `python -m app.services.partenariato_valutazione --offline | --prepara OUT_DIR | --reale [--locale] --tetto-cents N [--conferma] [--out FILE] [--solo ID,ID]`. `--reale --locale` è la valutazione reale **senza DB primario**: servono solo il catalogo in sola lettura (`SECONDARY_SUPABASE_URL` / `SECONDARY_SUPABASE_ANON_KEY`, oppure `SUPABASE_URL_BANDI` / `PUBLIC_SUPABASE_BANDI_ANON_KEY`, dall'ambiente o dal `.env`) e `ANTHROPIC_API_KEY`; nessuna scrittura su database, spesa fail-closed in memoria entro il tetto (centesimi di USD, **per singola esecuzione**: una prova o una ripresa con `--solo` va lanciata col tetto residuo), stima stampata prima e nessuna chiamata senza `--conferma`, risultato JSON su stdout o in `--out` **fuori dal repository**, con le metriche di campo sulle sole risposte del sistema e gli errori contati a parte. `--reale` senza `--locale` usa invece il DB primario (budget `valutazione`, migration 0034). Con `--rivaluta FILE --out FILE2` ripete convalida, post-elaborazione e metriche sull'output grezzo salvato da un'esecuzione locale, senza modello né rete (le modifiche alle regole si misurano gratis).
- **Sonda degli schemi AI** (`backend/scripts/sonda_schemi_ai.py`, da `backend/`: `python -m scripts.sonda_schemi_ai [--conferma] [--solo estrazione,bozza_profilo,posizioni,testi]`): senza `--conferma` stampa solo le dimensioni; con `--conferma` fa una chiamata minima per schema (tetto 20 cent) e dice se l'API lo accetta. Legge `ANTHROPIC_API_KEY` solo dall'ambiente.
- `frontend/.env` (da `frontend/.env.example`): URL e anon key del **primario** + base URL del backend.

> Per gli inviti famiglia a email nuove, verifica in **Authentication → URL Configuration** che `FRONTEND_URL` (es. `http://localhost:5173`) sia tra i **Redirect URLs** consentiti, altrimenti il link d'invito non reindirizzerà a `/accetta-invito`.

I file `.env` sono in `.gitignore` e non vanno mai committati.

## 3. Primo utente amministratore

1. Registrarsi normalmente dall'interfaccia (`/registrati`).
2. Nel SQL Editor del progetto primario eseguire:
   ```sql
   select public.promote_to_admin('email-del-tuo-account@example.com');
   ```
3. Ricaricare l'app: compare l'area di amministrazione.

## 4. Avvio locale

```bash
# Backend — http://localhost:8000 (Swagger su /docs)
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload

# Frontend — http://localhost:5173
cd frontend
npm install
npm run dev
```

## 5. Test end-to-end (smoke test)

1. `/registrati` (solo dati anagrafici: il piano non si sceglie più qui, si parte da Gratuito) → atterraggio su `/app/bandi` con la lista popolata.
2. Cerca "PNRR", filtra per regione e attiva "solo aperti" → apri un dettaglio (il contenuto viene renderizzato, i link esterni funzionano).
3. `/app/abbonamento` → passa al piano Pro (la card si aggiorna).
4. Promuovi il tuo utente ad admin (passo 3) → ricarica → compaiono `Utenti`, `Piani` e `Add-on`.
5. In `Piani` (admin) cambia il prezzo di Smart → verifica su `/app/abbonamento` (griglia piani).

Per test puntuali delle API, la Swagger UI è su `http://localhost:8000/docs`.

## Appendice — DB secondario in locale (opzionale)

Non necessario per lo sviluppo: il DB secondario esiste già in cloud ed è in sola lettura. Se serve lavorare offline, il dump in `database_secondario_dump/` è SQL semplice ma include COPY su schemi `auth.*`/`storage.*` e policy che referenziano `auth.role()`, quindi va ripristinato dentro lo stack locale di Supabase (`supabase start`, richiede Docker) e non in un Postgres «liscio»:

```bash
supabase start
psql "$(supabase status -o env | grep DB_URL | cut -d= -f2)" \
  -f database_secondario_dump/schema.sql \
  -f database_secondario_dump/data.sql
```

Poi puntare `SECONDARY_SUPABASE_URL`/`SECONDARY_SUPABASE_ANON_KEY` allo stack locale.
