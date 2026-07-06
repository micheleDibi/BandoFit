# Architettura

> Documento in costruzione: viene ampliato ad ogni fase di sviluppo.

## Componenti

```
┌──────────────┐        HTTPS         ┌───────────────┐
│   Frontend   │ ───────────────────▶ │  Backend API   │
│ React + Vite │   Bearer JWT         │    FastAPI     │
└──────┬───────┘                      └───┬────────┬───┘
       │ solo auth                        │        │
       │ (signup/login/session)           │        │ anon key (read-only via RLS)
       ▼                                  ▼        ▼
┌──────────────────┐          ┌──────────────┐  ┌──────────────────┐
│ Supabase PRIMARIO │◀────────│ service_role │  │ Supabase          │
│ auth.users        │         │ (bypassa RLS)│  │ SECONDARIO        │
│ profiles, piani,  │         └──────────────┘  │ catalogo bandi    │
│ abbonamenti       │                           │ (SOLA LETTURA)    │
└──────────────────┘                            └──────────────────┘
```

## Decisioni chiave

1. **Il frontend parla con Supabase primario solo per l'autenticazione** (registrazione, login, sessione, refresh token via `supabase-js`). Tutti i dati — profili, abbonamenti, bandi — passano dal backend FastAPI.
2. **DB primario blindato**: RLS abilitata su tutte le tabelle senza alcuna policy (deny-all). Solo il backend, con la chiave `service_role`, può leggere/scrivere.
3. **DB secondario in sola lettura per costruzione**: il backend lo interroga con la chiave `anon`; le policy RLS del secondario consentono agli anonimi solo `SELECT`, e sui bandi solo quelli con `stato_processing='completed'` e `slug` valorizzato.
4. **Verifica JWT a doppio binario** nel backend: i token firmati ES256/RS256 vengono verificati tramite JWKS (`/auth/v1/.well-known/jwks.json`, con cache); il fallback HS256 usa il legacy JWT secret. Vengono validati `aud` e `iss`.
5. **Provisioning utenti via trigger DB**: alla registrazione un trigger su `auth.users` crea il profilo e l'abbonamento iniziale (piano scelto nei metadata di signup, fallback Gratuito) in modo atomico.
6. **Dati aziendali certificati via openapi.it** (marketplace Openapi SpA): il backend, su richiesta esplicita dell'utente, recupera la visura IT-full e la Verifica CF con un token OAuth (Basic email+API key → Bearer con scope minimi, cache in-memory). Le chiamate **costano credito**, quindi: validazioni locali gratuite prima di spendere, cooldown, lock per azienda, **mai retry su chiamate potenzialmente addebitate** e registro consumi (`api_usage_events`) su ogni tentativo. IT-full è asincrono quando il dato non è in cache al provider (302 PENDING → polling sull'endpoint gratuito IT-check_id). Sandbox (`test.*`) per lo sviluppo. Il payload grezzo resta server-side (uso esclusivo, CGC art. 7.3): il client riceve solo il dossier strutturato.

## Flusso di autenticazione

1. Il frontend chiama `supabase.auth.signUp()` con `options.data = {nome, cognome, azienda, plan_slug}`.
2. Il trigger `on_auth_user_created` crea `profiles` + `user_subscriptions`.
3. Il frontend ottiene la sessione e allega `Authorization: Bearer <access_token>` a ogni chiamata verso il backend.
4. Il backend verifica il token, carica il profilo (ruolo, stato attivo) e autorizza la richiesta; gli endpoint `/admin/*` richiedono `role='admin'`.
