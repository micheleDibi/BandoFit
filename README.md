# BandoFit

Piattaforma di consulenza sui bandi pubblici: catalogo bandi (europei, nazionali, regionali e locali) con ricerca e filtri avanzati, abbonamenti a piani annuali e area di amministrazione.

## Architettura in breve

| Componente | Tecnologia | Percorso |
|---|---|---|
| Frontend | React + Vite + TypeScript + Tailwind CSS | `frontend/` |
| Backend API | Python + FastAPI | `backend/` |
| DB primario | Supabase (utenti, ruoli, abbonamenti) | migrazioni in `supabase/migrations/` |
| DB secondario | Supabase (catalogo bandi, **sola lettura**) | dump di riferimento in `database_secondario_dump/` |

Il frontend usa Supabase **solo per l'autenticazione**; tutti i dati passano dal backend FastAPI, che interroga il DB primario con la chiave `service_role` e il DB secondario con la chiave `anon` (accesso in sola lettura garantito dalle policy RLS).

## Avvio rapido

Prerequisiti: Node 20+, Python 3.12+, un progetto Supabase primario configurato (vedi [docs/setup.md](docs/setup.md)).

```bash
# Backend (porta 8000)
cd backend
cp .env.example .env   # inserire le credenziali
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload

# Frontend (porta 5173)
cd frontend
cp .env.example .env   # inserire le credenziali
npm install
npm run dev
```

## Documentazione

La documentazione completa vive in [`/docs`](docs/README.md) e **va aggiornata ad ogni modifica di codice o funzionalità** (è una regola del progetto, non un suggerimento):

- [Architettura](docs/architecture.md) — componenti, flussi, decisioni
- [Database](docs/database.md) — schema primario e riferimento al secondario
- [API](docs/api.md) — endpoint del backend con esempi
- [Frontend](docs/frontend.md) — route, design system, pattern
- [Setup](docs/setup.md) — configurazione ambienti e primo avvio
- [Deploy](docs/deploy.md) — messa in produzione con Docker Compose (porte configurabili)
- [Changelog](docs/changelog.md) — storico delle funzionalità
