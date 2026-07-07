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
- **openapi.it** (import dati aziendali + verifica CF): `OPENAPI_EMAIL`, `OPENAPI_API_KEY`, `OPENAPI_ENV`. In sviluppo usare `OPENAPI_ENV=sandbox` con la **chiave di test** (gratuita, dati finti); in produzione `production` con la chiave reale — ogni import IT-full costa ~0,30 € + IVA. Le API "Company" e "Risk" vanno attivate una tantum dalla **Libreria API** di console.openapi.com. Credenziali vuote = pulsanti di import/verifica disattivati, il resto dell'app funziona normalmente.
- **AI-check** (API Anthropic): `ANTHROPIC_API_KEY` (da console.anthropic.com) e `AI_CHECK_MODEL` (default `claude-sonnet-5`). Chiave vuota = AI-check disattivato (503), il resto dell'app funziona. **Ogni report costa ~0,10–0,20 $ di API** (meno se l'estrazione del bando è già in cache; il ragionamento del modello è conteggiato come output): in sviluppo lasciare la chiave vuota se non serve.
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

1. `/registrati` con il piano Smart → atterraggio su `/app/bandi` con la lista popolata.
2. Cerca "PNRR", filtra per regione e attiva "solo aperti" → apri un dettaglio (il contenuto viene renderizzato, i link esterni funzionano).
3. `/app/abbonamento` → passa al piano Pro (la card si aggiorna).
4. Promuovi il tuo utente ad admin (passo 3) → ricarica → compaiono `Utenti`, `Piani` e `Add-on`.
5. In `Piani` (admin) cambia il prezzo di Smart → verifica su `/registrati` (step 2).

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
