# Progetto: BandoFit

Piattaforma di consulenza sui bandi pubblici: catalogo bandi con ricerca/filtri, abbonamenti annuali, AI-check, pagamenti, area admin.
Sviluppo in locale; produzione con Docker Compose dietro reverse proxy (Cloudflare + nginx), vedi `docs/deploy.md`.

## Stack
- Backend: Python 3.12 (`requires-python >=3.11`), FastAPI + uvicorn, Pydantic v2, supabase-py
- Frontend: React 18 + Vite 6 + TypeScript 5.6 + Tailwind 4, TanStack Query, Axios, React Router 6
- Database: due progetti Supabase — **primario** (utenti, ruoli, abbonamenti, pagamenti) e **secondario** (catalogo bandi, sola lettura)
- Servizi esterni: Anthropic (AI-check), openapi.it (dati aziendali/CF/VIES), Revolut Merchant API, SMTP o Resend

## Mappa del codice
- `backend/app/api/routers/` — endpoint per dominio; `api/deps.py` — auth, ruoli, azienda attiva
- `backend/app/services/` — logica di business; `app/clients/` — client esterni (supabase, anthropic, openapi, revolut)
- `backend/app/schemas/` — modelli Pydantic; `app/core/` — config, errori (`AppError`), sicurezza, IP client
- `backend/tests/` — test unitari/API; `tests/db/` — test delle migration su Postgres usa-e-getta
- `backend/scripts/` — script di sviluppo lanciati a mano (es. verifica della sandbox openapi); non fanno parte dell'app
- `frontend/src/pages/`, `components/`, `hooks/` (uno per dominio, TanStack Query), `lib/` (`api.ts` = istanza Axios)
- `supabase/migrations/` — migration SQL del DB primario, numerate `NNNN_nome.sql`, da applicare in ordine
- `docs/` — documentazione di progetto (architettura, DB, API, frontend, setup, deploy, changelog)
- Punto di ingresso: `backend/app/main.py`, `frontend/src/main.tsx`
- Configurazione: `backend/app/core/config.py`, `.env` / `.env.example`, `docker-compose.yml` (NON modificare senza chiedere)

## Comandi
- Avvio locale backend (porta 8000): `cd backend && .venv/bin/uvicorn app.main:app --reload`
- Avvio locale frontend (porta 5173): `cd frontend && npm run dev`
- Test di un singolo file: `cd backend && .venv/bin/pytest tests/test_<nome>.py` (oppure `-k <espressione>`)
- Test completi: `cd backend && .venv/bin/pytest` (circa 40 s, ~1200 test)
- Lint backend: `cd backend && .venv/bin/ruff check .` (oggi segnala 13 errori preesistenti)
- Typecheck frontend: `cd frontend && npm run typecheck`; build: `npm run build`
- Frontend: nessun test né linter configurati in `package.json`
- Swagger locale: `http://localhost:8000/docs`

## Convenzioni
- **Ogni commit che introduce o modifica una funzionalità aggiorna i documenti pertinenti in `docs/` e aggiunge una voce a `docs/changelog.md`.** Doc non aggiornata = bug.
- Nella UI il gruppo di account si chiama «Azienda», mai «Famiglia» (nel codice resta `family`)
- Codice, commenti, messaggi e nomi di dominio in italiano (es. `bandi_service`, `useBandiFilters`); ruff con `line-length = 100`
- Errori backend: sottoclassi di `AppError` (`core/errors.py`) con `status_code`, `code` macchina e messaggio per l'utente
- Il frontend usa Supabase **solo per l'autenticazione**: tutti i dati passano dal backend FastAPI
- Backend: DB primario con chiave `service_role`, DB secondario con chiave `anon`
- Frontend: filtri della lista bandi nei searchParams (`hooks/useBandiFilters.ts`); nessun timeout Axios globale, lo imposta la singola chiamata
- Testo dei prezzi solo tramite `prezzoDisplay` in `frontend/src/lib/prezzo.ts`
- Link del catalogo sempre filtrati da `services/link_policy.py` (nessun rimando ai domini concorrenti)
- AI-check: il modello estrae e valuta, ma non decide **mai** esito né punteggio
- Pagamenti: le righe di `purchases` sono immutabili; mai piani a pagamento gratis, da nessun percorso

## Da non toccare
- DB secondario: sola lettura per costruzione (anon key + RLS). Contratto in `docs/contratto-db-bandi.md` (copia locale, non tracciata). Dalla fase (c) il catalogo si legge da `bando_pubblico` e `bando_link` (più le tabelle di risoluzione `bando_fusione`/`bando_slug_storico` e i lookup): mai la tabella `bando`; le colonne deprecate solo tramite `COLONNE_RIPIEGO_51`
- `database_secondario_dump/`: dati proprietari, solo riferimento locale (ignorato da git)
- `.env` e `.env.*`: mai committare credenziali (solo `.env.example`)
- Tabella `user_addons`: deprecata, congelata in sola lettura come rete di rollback
- Versione dell'API Revolut in `clients/revolut.py`: fissata; si cambia solo ri-validando in sandbox

## Trappole note
- I test `tests/db/` richiedono i binari di PostgreSQL (initdb): se mancano la suite viene saltata
- Le variabili `VITE_*` sono cotte nel bundle al build: se cambiano serve il rebuild del frontend
- openapi.it e AI-check costano credito a ogni chiamata; le chiavi sandbox/produzione openapi sono diverse e devono corrispondere a `OPENAPI_ENV`
- `RATE_LIMIT_PEPPER` è obbligatorio in deploy; cambiarlo azzera i contatori del rate limit
- IP client: non usare `FORWARDED_ALLOW_IPS=*` (prende il primo elemento di X-Forwarded-For, falsificabile). Vedi `core/net.py` e `docs/deploy.md`
- DB secondario: PostgREST ha `max-rows` = 1000: una richiesta senza `limit` non restituisce mai più di 1000 righe
- Rimappatura dei bandi fusi (`RIMAPPATURA_FUSI_MODALITA` = spenta | prova | attiva): richiede la migration 0043 sul primario; la variabile arriva al container solo se aggiunta all'`environment` del backend in `docker-compose.yml`
