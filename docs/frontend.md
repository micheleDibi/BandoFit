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
| `/app/azienda` | Dossier aziendale certificato (import openapi.it) | autenticato |
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

## Dossier aziendale (openapi.it)

- **Pagina «Azienda»** (`pages/Azienda.tsx`): dossier certificato a sezioni collassabili (`DossierSection`/`DossierRow` nascondono i campi vuoti) — Anagrafica, Attività e ATECO, Sede e unità locali, Persone e cariche (`PeopleTable`), Partecipazioni, Dipendenti, Dati economici, Contatti, Attributi. Header con badge stato impresa, «Startup innovativa» e «Dati di test» (sandbox), data di aggiornamento gg/mm/aaaa e bottone «Aggiorna» (solo per chi può modificare). Empty state con CTA «Importa da P.IVA» (o messaggio passivo per i figli attivi).
- **Import** (`ImportCompanyDialog`, aperto anche da `CompanyCard`): dialog con P.IVA precompilata e nota costo (~0,30 € + IVA), poi esito con campi compilati automaticamente, differenze rispetto ai valori utente (mai sovrascritti) e chip «ATECO secondari» da aggiungere alle preferenze con un click.
- **Hook**: `useCompanyDossier` (query key `["company-dossier"]`) e `useImportCompany` (aggiorna anche `["company"]`); `usePreferences`/`useSavePreferences` (key `["preferences"]`).
- **Documenti ufficiali** (`DocumentiCard`, nella pagina Azienda): richiesta della visura camerale con dialog e nota costo (2,90–4,90 € + IVA), stato In lavorazione/Pronta/Errore con refetch automatico ogni 10s finché pending (`useCompanyDocuments`, key `["company-documents"]`), download del PDF via blob (`downloadDocumentFile`). Visibile anche senza import IT-full (basta la P.IVA salvata); richiesta riservata a chi può modificare i dati aziendali.

## Preferenze e «Bandi per te»

- **Pagina «Preferenze»** (`/app/preferenze`, voce in navigazione; nel Profilo resta un rimando compatto `PreferenzeTeaser`): layout a due colonne — a sinistra il riquadro fisso **«La tua azienda»** con i valori **ereditati** dai dati aziendali (ATECO, settore, regione e beneficiari derivati dall'import) come chip bloccate con icona edificio (sempre inclusi in «Bandi per te», si modificano dai dati aziendali) + contatore dei valori seguiti; a destra una card per ciascuna delle 7 faccette con chip ereditate/rimovibili e **`TagSelect`** (nuovo componente ui: multi-selezione con ricerca in stile Combobox, tendina che resta aperta, valori ereditati marcati e non selezionabili). **Barra di salvataggio fissa** in basso che compare solo con modifiche non salvate (Annulla/Salva), più link «Vedi i bandi per te» che apre la lista con il preset applicato.
- **`BandiPerTeButton`** (toolbar di BandiList): preset che applica ai filtri URL l'**unione** dei valori reali dell'azienda e delle preferenze personali (helper condiviso `lib/bandiPreset.ts`); evidenziato quando i filtri correnti coincidono, secondo click = rimozione del preset. Visibile solo se c'è almeno un valore.

## Pattern chiave

- **Filtri nell'URL** (`src/hooks/useBandiFilters.ts`): tutti i filtri della lista bandi vivono nei searchParams (csv per le faccette). L'URL è condivisibile, il back del browser funziona, e i parametri sono la query key di TanStack Query. Ogni modifica ai filtri riporta a pagina 1; la ricerca testuale ha debounce di 400 ms.
- **Faccette M:N**: `FacetGroup` collassabile con contatore, ricerca interna per le liste lunghe (90 settori, 89 ATECO); OR dentro la faccetta, AND tra faccette (implementato dal backend).
- **Contenuto ricco** (`ContenutoRenderer`): il campo `contenuto` del bando è JSON strutturato (sections → segments) e viene mappato a elementi React puri — mai `dangerouslySetInnerHTML`.
- **Stati ovunque**: ogni vista dati ha skeleton (caricamento), empty state con azione di reset ed error state con retry.
- **Conferme**: le azioni con effetto (cambio piano, sospensione utente, cambio ruolo) passano da `Dialog` (elemento `<dialog>` nativo: focus trap ed Esc inclusi).

## Brand assets

- Loghi in `src/assets/` (PNG trasparenti, importati da Vite): `logo-orizzontale.png` (topbar e footer), `logo-verticale.png` (pagine auth), `logo-icona.png`; favicon e apple-touch-icon in `public/`.
- Componente `Logo` (`components/layout/Logo.tsx`) con varianti `horizontal` (default) / `vertical` / `icon`. **Regola di brand: il logo BandoFit porta sempre con sé l'attribuzione "powered by EduNews24"** — accanto (con separatore) nella variante orizzontale, sotto nella verticale; solo la variante `icon` (favicon) ne è priva. L'attribuzione nel lockup non è cliccabile (il Logo è spesso dentro un `<Link>`); il link a https://edunews24.it vive nel componente `PoweredBy` (`components/shared/PoweredBy.tsx`), usato nel footer dell'app.
- Gli screenshot sorgente dei loghi restano solo locali (`Screenshot *.png` in .gitignore).

## Design system

- **Colore primario**: blu `#1E5EFF` (scala `brand-50`→`brand-950`; hover `brand-600 #164BDB`, tint `brand-50 #EEF3FF`); neutri slate; sfondo app `#F7F9FC` (`bg-surface`).
- **Semantici**: aperto = smeraldo, chiuso = slate, in apertura = ambra; scadenza ≤ 7 giorni = rosso, ≤ 30 = ambra.
- **Tipografia**: Sora (titoli, 600/700) + Inter (testo) via Fontsource (self-hosted); cifre tabellari (classe `.tabular`) per importi e date.
- **Superfici**: card `rounded-xl` con ombre morbide (`shadow-card`, `shadow-card-hover`); topbar sticky bianca con bordo.
- **Localizzazione**: tutta l'interfaccia è in italiano (dare del tu); importi `Intl.NumberFormat('it-IT')`, date `it-IT`.
- **Accessibilità**: focus ring visibili (`focus-visible:outline-brand-500`), label su ogni input, `aria-label` sulle icone interattive, `prefers-reduced-motion` rispettato, contrasti AA.

## Variabili d'ambiente

`VITE_SUPABASE_URL` e `VITE_SUPABASE_ANON_KEY` (progetto **primario**, mai il secondario) + `VITE_API_BASE_URL` (default `http://localhost:8000/api/v1`).
