# Frontend

Stack: **Vite + React 18 + TypeScript**, **Tailwind CSS v4** (token di tema in `src/index.css` via `@theme`), **TanStack Query v5** (dati), **react-router v6** (routing), **@supabase/supabase-js** (solo autenticazione), **Axios** (chiamate al backend), **lucide-react** (icone SVG).

## Route

| Percorso | Pagina | Accesso |
|---|---|---|
| `/` | Landing (hero, feature, piani) | pubblico (redirect a `/app/bandi` se loggato) |
| `/login` | Accesso | pubblico |
| `/registrati` | Registrazione in 2 step (dati → scelta piano) | pubblico |
| `/accetta-invito` | Atterraggio del link d'invito famiglia (set password + accettazione) | pubblico |
| `/recupera-password` | Richiesta del link di reset via email | pubblico |
| `/reimposta-password` | Atterraggio del link di reset (nuova password) | pubblico |
| `/conferma-email` | Atterraggio del link di conferma registrazione (con reinvio se scaduto) | pubblico |
| `/app/bandi` | Elenco bandi con filtri | autenticato |
| `/app/bandi/:slug` | Dettaglio bando | autenticato |
| `/app/profilo` | Profilo, dati aziendali, gestione account collegati, abbonamento | autenticato |
| `/app/admin/utenti` | Gestione utenti | solo admin |
| `/app/admin/piani` | Gestione piani di abbonamento | solo admin |

Guardie: `ProtectedRoute` (sessione Supabase) e `AdminRoute` (ruolo dal profilo via `/me`) in `src/components/layout/guards.tsx`.

## Flusso di autenticazione

- `src/lib/supabase.ts`: client del progetto **primario**, usato solo per `signUp` / `signInWithPassword` / sessione.
- La registrazione invia i metadata `{nome, cognome, azienda, plan_slug}`: il trigger DB crea profilo e abbonamento.
- `src/lib/api.ts`: istanza Axios con interceptor che allega `Authorization: Bearer <token>`; su `401` fa `signOut` e riporta al login. `apiErrorMessage()` estrae il messaggio dal formato errori del backend.

## Famiglia di account

- **Profilo del titolare** (`Profilo.tsx`): card «Dati aziendali» (`CompanyCard`, form con `Combobox` con ricerca per ATECO/settori/regioni dalle lookup) e «Gestione account» (`FamilyCard`: contatore X di N, badge stato In attesa/Attivo/Retrocesso, azioni Reinvia/Riattiva/Rimuovi con conferma, dialog di invito). Il dialog di cambio piano avvisa se il downgrade retrocederà account.
- **Profilo del figlio attivo**: dati aziendali in sola lettura, card «Piano ereditato da …» al posto della griglia piani (nessuno switch).
- **Inviti**: `InviteBanner` (in `AppShell`) mostra agli utenti esistenti l'invito con Accetta (avvisando che l'abbonamento attuale verrà annullato) / Rifiuta; `/accetta-invito` gestisce il link Supabase degli utenti nuovi — cattura l'hash **prima** che supabase-js lo consumi per riconoscere i link scaduti (`otp_expired`), poi form password e accettazione automatica.
- **Admin**: colonna Famiglia (badge Titolare/In famiglia/Invitato/Retrocesso + email del titolare), piano «(ereditato)» e select disabilitata per i figli.

## Pattern chiave

- **Filtri nell'URL** (`src/hooks/useBandiFilters.ts`): tutti i filtri della lista bandi vivono nei searchParams (csv per le faccette). L'URL è condivisibile, il back del browser funziona, e i parametri sono la query key di TanStack Query. Ogni modifica ai filtri riporta a pagina 1; la ricerca testuale ha debounce di 400 ms.
- **Faccette M:N**: `FacetGroup` collassabile con contatore, ricerca interna per le liste lunghe (90 settori, 89 ATECO); OR dentro la faccetta, AND tra faccette (implementato dal backend).
- **Contenuto ricco** (`ContenutoRenderer`): il campo `contenuto` del bando è JSON strutturato (sections → segments) e viene mappato a elementi React puri — mai `dangerouslySetInnerHTML`.
- **Stati ovunque**: ogni vista dati ha skeleton (caricamento), empty state con azione di reset ed error state con retry.
- **Conferme**: le azioni con effetto (cambio piano, sospensione utente, cambio ruolo) passano da `Dialog` (elemento `<dialog>` nativo: focus trap ed Esc inclusi).

## Design system

- **Colore primario**: blu `#1E5EFF` (scala `brand-50`→`brand-950`; hover `brand-600 #164BDB`, tint `brand-50 #EEF3FF`); neutri slate; sfondo app `#F7F9FC` (`bg-surface`).
- **Semantici**: aperto = smeraldo, chiuso = slate, in apertura = ambra; scadenza ≤ 7 giorni = rosso, ≤ 30 = ambra.
- **Tipografia**: Sora (titoli, 600/700) + Inter (testo) via Fontsource (self-hosted); cifre tabellari (classe `.tabular`) per importi e date.
- **Superfici**: card `rounded-xl` con ombre morbide (`shadow-card`, `shadow-card-hover`); topbar sticky bianca con bordo.
- **Localizzazione**: tutta l'interfaccia è in italiano (dare del tu); importi `Intl.NumberFormat('it-IT')`, date `it-IT`.
- **Accessibilità**: focus ring visibili (`focus-visible:outline-brand-500`), label su ogni input, `aria-label` sulle icone interattive, `prefers-reduced-motion` rispettato, contrasti AA.

## Variabili d'ambiente

`VITE_SUPABASE_URL` e `VITE_SUPABASE_ANON_KEY` (progetto **primario**, mai il secondario) + `VITE_API_BASE_URL` (default `http://localhost:8000/api/v1`).
