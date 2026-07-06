# Changelog

Storico delle funzionalità e delle modifiche rilevanti. Formato: data — descrizione.

## 2026-07-06 — Dati aziendali certificati (openapi.it), preferenze e registro consumi

- **Import da P.IVA**: con un click il titolare importa la **visura completa** dell'azienda dal Registro Imprese via openapi.it (endpoint IT-full, ~0,30 € + IVA a chiamata): anagrafica, ATECO (anche secondari e storici), sede e unità locali, cariche/legale rappresentante/soci, dipendenti, dati economici, flag (startup innovativa, import/export, artigiana, SOA…). Il payload grezzo è persistito come fonte di verità (`company_data.raw`, migration 0005); l'autofill compila **solo i campi vuoti** del form aziendale (mai sovrascrivere l'utente: le differenze sono segnalate come conflitti) e gli ATECO secondari diventano suggerimenti per le preferenze.
- **Pagina «Azienda»** (`/app/azienda`): dossier certificato a sezioni collassabili con badge stato impresa/«Dati di test», persone e cariche, bottone «Aggiorna». Sola lettura per gli account collegati.
- **Protezioni di spesa**: validazione P.IVA locale gratuita (checksum), cooldown di 10 minuti tra import, lock atomico anti-concorrenza per azienda (`fn_acquire_import_lock`), retry mai su chiamate potenzialmente addebitate (timeout → esito ignoto, riprova l'utente), sandbox openapi per lo sviluppo a costo zero.
- **Verifica codice fiscale**: campo CF nei dati personali con validazione locale gratuita (checksum + omocodia) e verifica all'Anagrafe Tributaria (~0,05 €, `POST /me/verify-cf`, idempotente); badge «Verificato», il cambio del CF fa decadere la verifica (trigger DB). Niente servizi anagrafici persona (riservati contrattualmente al recupero crediti): i dati delle persone arrivano dalla visura aziendale.
- **Preferenze bandi per utente** (`user_preferences`, `GET/PUT /me/preferences`, card nel profilo): valori "seguiti" in aggiunta a quelli reali dell'azienda su tutte le 7 faccette dei filtri; preset **«Bandi per te»** sulla lista bandi = unione dati aziendali reali ∪ preferenze.
- **Registro consumi** (`api_usage_events`, senza FK): ogni chiamata a pagamento annotata con esito e costo — servirà anche al conteggio delle quote AI-check.
- Client openapi.it con token OAuth in-memory, switch sandbox/produzione, flusso asincrono IT-full → polling gratuito IT-check_id; fixture reali registrate per i test (197 test backend).

## 2026-07-03 — Link email 100% di dominio

- Eliminati tutti i link `*.supabase.co` dalle email: conferma indirizzo, recupero password e inviti azienda usano **token propri** (256 bit, SHA-256 a riposo, monouso, TTL 48h/1h/48h) su link del dominio BandoFit, emessi e verificati dal backend (nuova tabella `auth_tokens`, migration 0004). GoTrue non genera più link né invia email; utenti creati/aggiornati via Admin API. Nuovi endpoint `/auth/confirm`, `/auth/reset`, `/auth/invite-info`, `/auth/accept-invite`; reset e accettazione invito fanno auto-login. Su Supabase non servono più i Redirect URLs.

## 2026-07-03 — Terminologia "Azienda"

- In tutta l'interfaccia (e nelle email/messaggi d'errore) il gruppo di account si chiama ora **"Azienda"** invece di "Famiglia" — più professionale. I nomi tecnici interni (tabelle `family_members`, endpoint `/me/family`, componenti) restano invariati.

## 2026-07-03 — Branding

- Loghi ufficiali BandoFit (orizzontale, verticale, icona) con sfondo reso trasparente, nuovi favicon/apple-touch-icon; logo verticale nelle pagine auth, orizzontale in topbar e footer.
- Attribuzione **"powered by EduNews24"** (logo con link a edunews24.it) nel footer della landing, sotto le card auth e nel nuovo footer dell'app.

## 2026-07-03 — Tutte le email dal backend (mai dal mailer Supabase)

- Registrazione, recupero password, reinvio conferma e inviti famiglia ora generano i link firmati via Admin API (`generate_link`) e inviano le email **esclusivamente col provider del backend** (SMTP/OVH o Resend), con template brandizzati in italiano. Nuovi endpoint pubblici `POST /auth/register`, `/auth/recover`, `/auth/resend-confirmation` con risposta anti-enumerazione e cooldown anti-abuso (60s per destinatario).

## 2026-07-03 — Recupero password e conferma email

- **Recupero password**: link "Password dimenticata?" nel login, pagina `/recupera-password` (invio del link via Supabase, messaggio neutro anti-enumerazione) e `/reimposta-password` (atterraggio del link con gestione scadenza e form nuova password).
- **Conferma email** alla registrazione (da attivare su Supabase in produzione): pagina `/conferma-email` con redirect automatico, gestione link scaduto con **reinvio**, avviso dedicato al login per email non confermate con pulsante di reinvio.
- Estratto l'hook `useHashSession` che unifica la gestione dei link Supabase (hash, sessione, scadenza, recupero tardivo) per inviti, reset e conferme.

## 2026-07-03 — Deploy

- Deploy con Docker Compose: Dockerfile per backend (uvicorn) e frontend (build statica + nginx con fallback SPA), porte host configurabili da `.env` (`FRONTEND_PORT`/`BACKEND_PORT`, default bind su 127.0.0.1 per stare dietro reverse proxy), guida completa in `docs/deploy.md`.
- Email transazionali anche via **SMTP** (es. casella OVH: `ssl0.ovh.net:465`), con selezione automatica del provider: SMTP → Resend → solo log; messaggi multipart (testo + HTML) e protezione da header injection.

## 2026-07-03 — Account famiglia (fase 2a)

- **Famiglie di account**: il titolare di un piano multi-account (Pro, Advisor) invita account collegati entro il limite del piano (che include il titolare). Doppio flusso di invito: email nuove via invito nativo Supabase (pagina `/accetta-invito` per impostare la password), email già registrate via invito in piattaforma + notifica Resend. I figli attivi ereditano l'abbonamento (quote condivise, non ripartite) e non possono cambiarlo.
- **Dati aziendali**: sezione dedicata nel profilo del titolare (ragione sociale, P.IVA, ATECO/settore/regione dalle lookup del catalogo bandi, sede, dimensione, fatturato, contatti), condivisi in sola lettura con i figli.
- **Adeguamento automatico al cambio piano**: al downgrade vengono revocati gli inviti in attesa e retrocessi a Gratuito i figli più recenti, atomicamente; il titolare non è mai retrocesso. I retrocessi restano in elenco e sono riattivabili con un clic quando si libera un posto.
- **Gestione**: reinvio inviti, rimozione con conferma, banner di invito in-app, indicatori famiglia e piani ereditati nell'area admin (piano dei figli gestibile solo dal titolare), audit log delle operazioni sensibili.
- Email transazionali via Resend con fallback log-only in sviluppo; harness di test delle migration su Postgres usa-e-getta (27 test) + test dei servizi famiglia/azienda.

## 2026-07-03

- Frontend completo (fase 1): app shell con topbar responsive, lista bandi con ricerca full-text, sidebar filtri a faccette (stato, tipologia, regioni, settori, beneficiari, ATECO, modalità, programmi, importi, scadenze) con filtri serializzati nell'URL, card bando con badge/countdown, dettaglio con renderer del contenuto strutturato, pagina profilo con cambio piano, area admin (utenti e piani). Design system blu (Sora + Inter), stati loading/empty/error ovunque.
- Hardening da review multi-agente: revoca `EXECUTE` sulle RPC `fn_switch_plan`/`promote_to_admin` (altrimenti esposte da PostgREST a chiunque); verifica JWT che distingue guasti JWKS transitori (503) da token invalidi (401); normalizzazione di `contenuto` doppio-encodato (5 bandi reali) per evitare 500 sul dettaglio; sanitizzazione dei doppi apici nelle ricerche; provisioning del profilo al volo se mancante; accessibilità (nessun elemento interattivo annidato, label sui controlli, focus/Esc nel drawer filtri). Introdotto `tailwind-merge` per la corretta risoluzione degli override di classe.
- Backend FastAPI completo (fase 1): autenticazione JWT Supabase (JWKS + fallback HS256), profilo utente (`/me`), catalogo bandi con ricerca full-text e filtri combinabili (stato, tipologia, regioni, settori, beneficiari, ATECO, modalità, programmi, importi, scadenze), dettaglio bando, lookups con cache, piani di abbonamento con cambio piano, area admin (gestione utenti e piani). Test unitari del builder dei filtri PostgREST.
- Schema del DB primario: `profiles`, `subscription_plans`, `user_subscriptions`, trigger di provisioning alla registrazione, RPC `fn_switch_plan`, RLS deny-all; seed dei 4 piani (Gratuito, Smart, Pro, Advisor) e helper `promote_to_admin`.
- Impostazione iniziale del progetto: struttura monorepo (`frontend/`, `backend/`, `supabase/`, `docs/`), configurazione git e documentazione di base.
