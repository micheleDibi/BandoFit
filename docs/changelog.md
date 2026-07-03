# Changelog

Storico delle funzionalità e delle modifiche rilevanti. Formato: data — descrizione.

## 2026-07-03

- Frontend completo (fase 1): app shell con topbar responsive, lista bandi con ricerca full-text, sidebar filtri a faccette (stato, tipologia, regioni, settori, beneficiari, ATECO, modalità, programmi, importi, scadenze) con filtri serializzati nell'URL, card bando con badge/countdown, dettaglio con renderer del contenuto strutturato, pagina profilo con cambio piano, area admin (utenti e piani). Design system blu (Sora + Inter), stati loading/empty/error ovunque.
- Hardening da review multi-agente: revoca `EXECUTE` sulle RPC `fn_switch_plan`/`promote_to_admin` (altrimenti esposte da PostgREST a chiunque); verifica JWT che distingue guasti JWKS transitori (503) da token invalidi (401); normalizzazione di `contenuto` doppio-encodato (5 bandi reali) per evitare 500 sul dettaglio; sanitizzazione dei doppi apici nelle ricerche; provisioning del profilo al volo se mancante; accessibilità (nessun elemento interattivo annidato, label sui controlli, focus/Esc nel drawer filtri). Introdotto `tailwind-merge` per la corretta risoluzione degli override di classe.
- Backend FastAPI completo (fase 1): autenticazione JWT Supabase (JWKS + fallback HS256), profilo utente (`/me`), catalogo bandi con ricerca full-text e filtri combinabili (stato, tipologia, regioni, settori, beneficiari, ATECO, modalità, programmi, importi, scadenze), dettaglio bando, lookups con cache, piani di abbonamento con cambio piano, area admin (gestione utenti e piani). Test unitari del builder dei filtri PostgREST.
- Schema del DB primario: `profiles`, `subscription_plans`, `user_subscriptions`, trigger di provisioning alla registrazione, RPC `fn_switch_plan`, RLS deny-all; seed dei 4 piani (Gratuito, Smart, Pro, Advisor) e helper `promote_to_admin`.
- Impostazione iniziale del progetto: struttura monorepo (`frontend/`, `backend/`, `supabase/`, `docs/`), configurazione git e documentazione di base.
