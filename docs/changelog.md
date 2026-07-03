# Changelog

Storico delle funzionalità e delle modifiche rilevanti. Formato: data — descrizione.

## 2026-07-03

- Backend FastAPI completo (fase 1): autenticazione JWT Supabase (JWKS + fallback HS256), profilo utente (`/me`), catalogo bandi con ricerca full-text e filtri combinabili (stato, tipologia, regioni, settori, beneficiari, ATECO, modalità, programmi, importi, scadenze), dettaglio bando, lookups con cache, piani di abbonamento con cambio piano, area admin (gestione utenti e piani). Test unitari del builder dei filtri PostgREST.
- Schema del DB primario: `profiles`, `subscription_plans`, `user_subscriptions`, trigger di provisioning alla registrazione, RPC `fn_switch_plan`, RLS deny-all; seed dei 4 piani (Gratuito, Smart, Pro, Advisor) e helper `promote_to_admin`.
- Impostazione iniziale del progetto: struttura monorepo (`frontend/`, `backend/`, `supabase/`, `docs/`), configurazione git e documentazione di base.
