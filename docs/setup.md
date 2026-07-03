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
3. **Authentication → Sign In / Providers → Email**: disattivare **"Confirm email"** (in questa fase la registrazione deve restituire subito una sessione; la conferma email si riattiva quando ci sarà un dominio di produzione).
4. **SQL Editor**: eseguire in ordine i file di `supabase/migrations/`.

## 2. Variabili d'ambiente

- `backend/.env` (da `backend/.env.example`): URL e chiavi del primario e del secondario.
- `frontend/.env` (da `frontend/.env.example`): URL e anon key del **primario** + base URL del backend.

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
