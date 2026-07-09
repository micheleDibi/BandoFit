# Frontend

Stack: **Vite + React 18 + TypeScript**, **Tailwind CSS v4** (token di tema in `src/index.css` via `@theme`), **TanStack Query v5** (dati), **react-router v6** (routing), **@supabase/supabase-js** (solo autenticazione), **Axios** (chiamate al backend), **lucide-react** (icone SVG).

## Route

| Percorso | Pagina | Accesso |
|---|---|---|
| `/` | Landing pubblica (hero, funzionalità, come funziona, piani, FAQ) | pubblico (redirect a `/app/bandi` se loggato) |
| `/login` | Accesso | pubblico |
| `/registrati` | Registrazione in 2 step (dati → scelta piano) | pubblico |
| `/accetta-invito` | Atterraggio del link d'invito famiglia (set password + accettazione) | pubblico |
| `/recupera-password` | Richiesta del link di reset via email | pubblico |
| `/reimposta-password` | Atterraggio del link di reset (nuova password) | pubblico |
| `/conferma-email` | Atterraggio del link di conferma registrazione (con reinvio se scaduto) | pubblico |
| `/app/bandi` | Elenco bandi con filtri | autenticato |
| `/app/bandi/:slug` | Dettaglio bando | autenticato |
| `/app/salvati` | Bandi salvati (preferiti) | autenticato |
| `/app/calendario` | Calendario mensile con eventi personali e scadenze bandi | autenticato |
| `/app/azienda` | Tutto sull'azienda: dati aziendali, dossier certificato, documenti | autenticato |
| `/app/ai-check` | Cruscotto AI-check (quota del piano e storico per bando) | autenticato |
| `/app/abbonamento` | Piano attuale, cambio piano e catalogo add-on | autenticato |
| `/app/profilo` | Profilo personale e gestione account collegati | autenticato |
| `/app/admin/utenti` | Gestione utenti | solo admin |
| `/app/admin/piani` | Gestione piani di abbonamento | solo admin |
| `/app/admin/addon` | Gestione catalogo add-on | solo admin |

Guardie: `ProtectedRoute` (sessione Supabase) e `AdminRoute` (ruolo dal profilo via `/me`) in `src/components/layout/guards.tsx`.

**Navigazione** (`AppShell.tsx`): per non affollare la barra le voci sono raggruppate — link diretti **Bandi · Salvati · Calendario · AI-check** + menu a tendina **«Impostazioni»** (Azienda, Preferenze, Abbonamento) e, per gli admin, **«Admin»** (Utenti, Piani, Add-on, con icona scudo che sostituisce il vecchio badge). Il dropdown è `components/layout/NavMenu.tsx` (chiusura su selezione, click fuori ed Esc; trigger attivo quando la rotta corrente è nel gruppo). La nav per esteso entra da `lg`; sotto, il menu hamburger elenca gli stessi gruppi come sezioni con intestazione.

**Landing pubblica** (`pages/Landing.tsx`): pagina marketing per l'utente sloggato (redirect a `/app/bandi` se c'è sessione). Sezioni: header sticky con ancore (Funzionalità · Come funziona · Piani · FAQ, smooth-scroll) + Accedi/Registrati; hero a due colonne (gradiente firma `from-brand-950 via-brand-900 to-brand-700`) con `HeroShowcase` (mock di prodotto *illustrativo* costruito dai token — scheda bando + widget AI-check con anello punteggio via `lib/scoreColor.ts`, `aria-hidden`); problema→soluzione; griglia funzionalità con l'**AI-check in evidenza** (`FeatureCard` variante `featured`); «come funziona» in 4 passi; «perché BandoFit» + fascia di numeri **reali** (nessuna statistica di utenti inventata); piani (riuso di `usePlans`+`PlanCard`, gestione `su_richiesta` invariata); FAQ (`Faq`, accordion `<details>` nativo); CTA finale; footer ricco (ancore, Accedi/Registrati, `PoweredBy`). Copy onesto: gli **alert email non sono implementati** → il tema scadenze punta sul Calendario reale e gli alert sono citati come «in arrivo» sotto i piani. Componenti presentazionali co-locati in `components/landing/` (`HeroShowcase`, `SectionHeading`, `FeatureCard`, `Faq`), solo token, nessuna logica di rete.

## Flusso di autenticazione

- `src/lib/supabase.ts`: client del progetto **primario**, usato solo per `signUp` / `signInWithPassword` / sessione.
- La registrazione invia i metadata `{nome, cognome, azienda, plan_slug}`: il trigger DB crea profilo e abbonamento.
- `src/lib/api.ts`: istanza Axios con interceptor che allega `Authorization: Bearer <token>`; su `401` fa `signOut` e riporta al login. `apiErrorMessage()` estrae il messaggio dal formato errori del backend.

## Famiglia di account

- **Profilo** (`Profilo.tsx`): dati personali (con verifica CF), rimandi compatti ad Azienda (`AziendaTeaser`), Preferenze (`PreferenzeTeaser`) e Abbonamento (`AbbonamentoTeaser` — piano attuale + link) e «Gestione account» (`FamilyCard`: contatore X di N, badge stato, azioni Reinvia/Riattiva/Rimuovi, dialog di invito).
- **Pagina «Abbonamento»** (`/app/abbonamento`, voce in navigazione): la sezione abbonamenti spostata INVARIATA dal Profilo — griglia `PlanCard` con cambio piano (il dialog di conferma avvisa se il downgrade retrocederà account; figlio attivo → card «Piano ereditato da …» senza switch) — più la sezione **Add-on**: catalogo gestito dagli admin (`useAddons`, key `["addons"]`, sezione nascosta se vuoto), card `AddonCard` (nome, descrizione, prezzo una tantum senza «/anno») con bottone «Acquista» che passa dal **punto di estensione `purchaseAddon(slug)` in `lib/addons.ts`** — oggi stub: apre il dialog «Acquisto in arrivo» (nessun addebito); collegare il flusso reale = riempire quella funzione. Admin: `/app/admin/addon` (`AdminAddon.tsx`, clone di `AdminPiani`: card-editor, slug immutabile, «non si eliminano: si disattivano», hook `useAdminAddons`/`Create`/`Update` con doppia invalidazione).
- **Modalità prezzo** (`tipo_prezzo` su piani e add-on): l'helper puro **`prezzoDisplay(tipo, etichetta, valore)` in `lib/prezzo.ts`** è l'unico punto che decide il testo del prezzo — `importo` → `formatPrezzo` (+ « /anno» sui piani), `gratis` → «Gratis», `su_richiesta` → `etichetta_prezzo` o fallback «Su richiesta». Con `su_richiesta` la CTA diventa «Richiedi una consulenza» (piani in `Abbonamento.tsx`, add-on in `AddonCard`) e passa dal **punto di estensione `requestConsultation({kind, slug})` in `lib/consulenza.ts`** — oggi stub gemello di `purchaseAddon`: apre il dialog «Richiesta in arrivo»; il blocco vero è nel backend (400 su cambio piano e registrazione). Add-on `gratis` → CTA «Attiva», stesso flusso `purchaseAddon`. In `Register.tsx` i piani su richiesta sono **visibili ma non selezionabili** (card non interattiva + nota; il fallback dello slug `?piano=` salta i piani su richiesta) e nella **Landing** pubblica la loro CTA diventa «Richiedi una consulenza» → `/registrati` senza deep-link `?piano=` (che verrebbe scartato). Nei form admin il select «Prezzo mostrato come» disabilita prezzo/etichetta secondo la modalità (pattern del campo «Giorni di preavviso») e il controllo di validità sul prezzo si applica solo in modalità «importo».
- **Inviti**: `InviteBanner` (in `AppShell`) mostra agli utenti esistenti l'invito con Accetta (avvisando che l'abbonamento attuale verrà annullato) / Rifiuta; `/accetta-invito` gestisce il link Supabase degli utenti nuovi — cattura l'hash **prima** che supabase-js lo consumi per riconoscere i link scaduti (`otp_expired`), poi form password e accettazione automatica.
- **Admin**: colonna Famiglia (badge Titolare/In famiglia/Invitato/Retrocesso + email del titolare), piano «(ereditato)» e select disabilitata per i figli.

## Pagina «Azienda» (tutto in un posto)

- **`pages/Azienda.tsx`** è l'unica casa dei dati aziendali, in tre sezioni: **1) «Dati aziendali»** (`CompanyCard`): riepilogo in sola lettura dei campi compilati con bottoni «Importa da P.IVA» e «Modifica» — il form completo (Combobox con ricerca per ATECO/settori/regioni, sede, dimensione, contatti) si apre solo in modifica, con Salva/Annulla; i figli attivi vedono il solo riepilogo. Il form non viene mai risincronizzato durante la modifica (un refetch non cancella ciò che si sta scrivendo); senza alcun dato la card parte direttamente dal form. **2) «Dossier certificato»**: sezioni collassabili (`DossierSection`/`DossierRow` nascondono i campi vuoti) — Anagrafica, Attività e ATECO, Sede e unità locali, Persone e cariche (`PeopleTable`), Partecipazioni, Dipendenti, Dati economici, Contatti, Attributi — con badge stato impresa/«Startup innovativa»/«Dati di test», data di aggiornamento e bottone «Aggiorna»; empty state con CTA «Importa da P.IVA». **3) «Documenti ufficiali»** (`DocumentiCard`). Nel Profilo resta solo il rimando `AziendaTeaser`.
- **Import** (`ImportCompanyDialog`, aperto da `CompanyCard` e dalla sezione dossier): dialog con P.IVA precompilata e nota costo (~0,30 € + IVA), poi esito con campi compilati automaticamente, differenze rispetto ai valori utente (mai sovrascritti) e chip «ATECO secondari» da aggiungere alle preferenze con un click.
- **Hook**: `useCompanyDossier` (query key `["company-dossier"]`) e `useImportCompany` (aggiorna anche `["company"]`); `usePreferences`/`useSavePreferences` (key `["preferences"]`).
- **Documenti ufficiali** (`DocumentiCard`, nella pagina Azienda): richiesta della visura camerale con dialog e nota costo (2,90–4,90 € + IVA), stato In lavorazione/Pronta/Errore con refetch automatico ogni 10s finché pending (`useCompanyDocuments`, key `["company-documents"]`), download del PDF via blob (`downloadDocumentFile`). Visibile anche senza import IT-full (basta la P.IVA salvata); richiesta riservata a chi può modificare i dati aziendali.

## AI-check

- **Tono costruttivo, mai bocciature secche**: il report è generato da un modello e può sbagliare — gli esiti in UI sono «In linea col bando» (emerald) e «Dati da completare» (amber); per l'esito negativo NESSUN badge — parlano il colore del punteggio e i verdetti dei singoli requisiti. Il punteggio non viene mai "svalutato" o nascosto.
- **Mai nomi tecnici davanti all'utente**: i campi citati dall'AI (`derived.beneficiari[1].nome`, `settore_nome`…) passano da `lib/aiCheckFields.ts` → etichette italiane con fonte («Categorie di beneficiari (Registro Imprese)»); il prompt di matching impone linguaggio naturale nelle motivazioni. I dati mancanti guidano con due link: completa i dati (Profilo) o importa dal Registro Imprese (Azienda).
- **`AiCheckCard`** (prima card della sidebar in `BandoDetail.tsx`): quota residua del piano, CTA «Verifica compatibilità» con dialog di conferma (consumo quota + durata 1-2 minuti), stato «Analisi in corso…» con polling ogni 4s (`useAiChecksForBando`, key `["ai-check", slug]` — stesso pattern dei documenti: il polling si spegne per le pending oltre 12 minuti, il backend le chiude a 10), esito sintetico + punteggio con ancora al report. I figli attivi vedono i risultati ma non avviano.
- **`AiCheckReport`** (sezione full-width `#ai-check-report` sotto la scheda del bando): esito + punteggio a colori (rosso→verde per fasce, numero e barra, lib/scoreColor.ts) + badge «Stima del punteggio ufficiale»/«Punteggio euristico interno», selettore delle versioni (storico, con data e ora), righe espandibili (`<details>`) per ogni requisito/criterio con **citazione del bando** (sezione + testo, flag «citazione non verificata») e **dato aziendale usato** in linguaggio naturale, punti di forza/debolezza, callout ambra dei dati mancanti, disclaimer.
- **Pagina «AI-check»** (`/app/ai-check`, voce in navigazione): cruscotto con la quota del piano (disponibili quest'anno, con barra di consumo) e storico **raggruppato per bando** — l'analisi più recente in evidenza con badge, mini-barra punteggio, numero versioni e bottone «Apri report» che porta al report sul dettaglio bando (`useAiChecks`, key `["ai-checks"]`, polling 10s se c'è un'analisi in corso).
- Hook in `hooks/useAiCheck.ts`; `useRequestAiCheck` invalida `["ai-check", slug]` e `["ai-checks"]`.

## Punteggio di compatibilità (pre-check)

- La frazione è **requisiti soddisfatti / requisiti valutabili** (es. `3/4`): dentro un requisito le voci sono alternative, una in comune basta. Vedi `docs/api.md`.
- **`CompatibilitaBadge`** (`components/bandi/`): pill **«Compatibilità 3/4»** — etichetta esplicita + frazione colorata per banda con la stessa scala dell'AI-check (`lib/scoreColor.ts`); percentuale ed esito per requisito nel tooltip/`aria-label`. Presente sulla riga badge di `BandoCard` e nell'header di `BandoDetail`, **subito e senza azioni** dell'utente.
- **`CompatibilitaCard`** (`components/bandi/`, in cima alla colonna laterale del dettaglio, sopra l'AI-check): frazione + barra colorata e, per ogni requisito (Regioni, Codici ATECO, Settori, Beneficiari), le voci del bando con **in comune evidenziate in emerald** (`matched_ids`) e un'icona di esito (`CircleCheck` / `CircleX` / `CircleDashed` per «non valutato», quando l'azienda non ha quel dato). Le voci in comune sono ordinate per prime; oltre `MAX_CHIP = 6` le altre si riassumono in un «+N» col `title` completo, perché in 320 px una lista lunga sfonda la colonna. Un bando **nazionale** non elenca venti regioni: mostra le sedi in comune e la nota «aperto a tutte le regioni». Senza P.IVA importata la card resta (le voci del bando sono comunque utili: è la vecchia **«A chi si rivolge»**, che assorbe) con un callout ambra e link ad Azienda; in sidebar restano poi solo le Tematiche.
- La **card dell'elenco** non mostra più la modalità di erogazione (Fondo perduto, Contributo in conto interessi…): affollava la riga senza aiutare a scegliere. Resta nell'header del dettaglio. La riga badge ha `pr-10`: il toggle «salva» è un overlay opaco in alto a destra e coprirebbe l'ultimo badge.
- Il valore arriva già calcolato dal backend nel campo `compatibilita` di `BandoListItem`/`BandoDetail` (tipi `Compatibilita`/`CompatibilitaDimensione` in `types/index.ts`): il frontend non ricalcola nulla e **rende badge e punteggio solo se il campo è presente** (profilo aziendale sufficiente, cioè P.IVA importata). Nessun nuovo hook o round-trip: viaggia con la lista e col dettaglio.

## Bandi salvati e Calendario

- **Salvataggio** (`SaveBandoButton`): toggle segnalibro con stato **ottimista sul Set degli id** (`useSavedIds`, key `["saved-bandi","ids"]`; rollback su errore, poi invalidazione della lista). Sulle card della lista il bottone è un **fratello sovrapposto** al link (`SavableBandoCard`: wrapper `relative` + bottone `absolute` — mai annidato nel `<Link>`, vedi Pattern chiave); sul dettaglio bando è un bottone inline accanto ad «Aggiungi scadenza al calendario» (visibile solo con una scadenza).
- **Pagina «Salvati»** (`/app/salvati`): griglia di `SavableBandoCard` con azione «Aggiungi scadenza al calendario» sotto ogni card (o link «Nel calendario» se già presente); i bandi **spariti dal catalogo** restano visibili come card tratteggiata non-cliccabile costruita dallo snapshot (badge «Non più disponibile», data di salvataggio, bottone Rimuovi). Paginazione a 20, hook `useSavedBandi(page)` (key `["saved-bandi", page]`).
- **Pagina «Calendario»** (`/app/calendario`, mese nell'URL `?m=YYYY-MM`): un'unica Card a tutta larghezza con **toolbar integrata** (mese + frecce + «Oggi»; legenda nell'intestazione di pagina) e griglia mensile nativa compatta in verticale (lunedì per primo, 6 settimane fisse, bordi interni sottili, numero in alto a sinistra, hint «+» al passaggio del mouse, `Intl it-IT` — nessuna libreria di date). **Niente pannello agenda**: click su un giorno → si apre subito il form di creazione (data precompilata); click su un chip evento → dialog di modifica; «+N altri» (celle affollate) e il tap su un giorno con eventi su mobile aprono il **dialog-elenco del giorno** (`DayEventsDialog`, righe → modifica, footer «Aggiungi evento»). Niente interattivi annidati: la cella è un div con un bottone di sfondo (`absolute inset-0`) e i chip come bottoni FRATELLI sovrapposti (`z-10`); su mobile i chip sono pallini presentazionali. `EventDialog`: titolo, data (`type="date"`), «Tutto il giorno», orari (`type="time"`), note (`TextareaField`, nuovo componente ui); per gli eventi bando la data è una riga fissa («deriva dal bando ufficiale») con link al bando; eliminazione con conferma a due passi. Hook `useCalendarEvents(anno, mese)` (key `["calendar", anno, mese]`); le mutazioni invalidano il prefisso `["calendar"]`; `useAddBandoDeadline` invalida anche `["saved-bandi"]`.

## Preferenze e «Bandi per te»

- **Pagina «Preferenze»** (`/app/preferenze`, voce in navigazione; nel Profilo resta un rimando compatto `PreferenzeTeaser`): layout a due colonne — a sinistra il riquadro fisso **«La tua azienda»** con i valori **ereditati** dai dati aziendali (ATECO, settore, regione e beneficiari derivati dall'import) come chip bloccate con icona edificio (sempre inclusi in «Bandi per te», si modificano dai dati aziendali) + contatore dei valori seguiti; a destra una card per ciascuna delle 7 faccette con chip ereditate/rimovibili e **`TagSelect`** (nuovo componente ui: multi-selezione con ricerca in stile Combobox, tendina che resta aperta, valori ereditati marcati e non selezionabili). **Barra di salvataggio fissa** in basso che compare solo con modifiche non salvate (Annulla/Salva), più link «Vedi i bandi per te» che apre la lista con il preset applicato.
- **`BandiPerTeButton`** (toolbar di BandiList): preset che applica ai filtri URL l'**unione** dei valori reali dell'azienda e delle preferenze personali (helper condiviso `lib/bandiPreset.ts`); evidenziato quando i filtri correnti coincidono, secondo click = rimozione del preset. Visibile solo se c'è almeno un valore.

## Pattern chiave

- **Filtri nell'URL** (`src/hooks/useBandiFilters.ts`): tutti i filtri della lista bandi vivono nei searchParams (csv per le faccette). L'URL è condivisibile, il back del browser funziona, e i parametri sono la query key di TanStack Query. Ogni modifica ai filtri riporta a pagina 1; la ricerca testuale ha debounce di 400 ms. Ordinamento di default «Più recenti» (`pubblicazione_desc`, omesso dall'URL); i bandi chiusi vengono sempre in coda (lato backend).
- **Faccette M:N**: `FacetGroup` collassabile con contatore, ricerca interna per le liste lunghe (90 settori, 89 ATECO); OR dentro la faccetta, AND tra faccette (implementato dal backend).
- **Contenuto ricco** (`ContenutoRenderer`): il campo `contenuto` del bando è JSON strutturato (sections → segments) e viene mappato a elementi React puri — mai `dangerouslySetInnerHTML`.
- **Stati ovunque**: ogni vista dati ha skeleton (caricamento), empty state con azione di reset ed error state con retry.
- **Conferme**: le azioni con effetto (cambio piano, sospensione utente, cambio ruolo) passano da `Dialog` (elemento `<dialog>` nativo: focus trap ed Esc inclusi).
- **Azioni sopra le card-link**: mai un bottone DENTRO un `<Link>` (interattivi annidati vietati) — il pattern è un wrapper `relative` con la card-link e il bottone come **fratello** `absolute` (es. `SavableBandoCard`); nelle celle del calendario, un solo bottone per cella e contenuti presentazionali.

## Brand assets

- Loghi in `src/assets/` (PNG trasparenti, importati da Vite): `logo-orizzontale.png` (unico lockup fornito: topbar, footer **e** pagine auth) e `logo-icona.png` (sola icona documento+check, **ritagliata dall'orizzontale**); favicon e apple-touch-icon in `public/` sono la stessa icona ritagliata (512²/180²). Non esiste più un `logo-verticale.png` dedicato.
- Componente `Logo` (`components/layout/Logo.tsx`) con varianti `horizontal` (default) / `vertical` / `icon`. Il brand è un **singolo lockup orizzontale**: la variante `vertical` riusa la stessa immagine (più piccola) impilando sotto l'attribuzione. **Regola di brand: il logo BandoFit porta sempre con sé l'attribuzione "powered by EduNews24"** — accanto (con separatore) nella variante orizzontale, sotto nella verticale; solo la variante `icon` (favicon) ne è priva. L'attribuzione nel lockup non è cliccabile (il Logo è spesso dentro un `<Link>`); il link a https://edunews24.it vive nel componente `PoweredBy` (`components/shared/PoweredBy.tsx`), usato nel footer dell'app.
- Gli screenshot sorgente dei loghi restano solo locali (`Screenshot *.png` in .gitignore).

## Design system

- **Colore primario**: navy `#2C56C9` (scala `brand-50`→`brand-950` ancorata al logo — `brand-900 #182549` ≈ navy del wordmark; hover `brand-600 #2246A7`, tint `brand-50 #F3F5F9`); neutri slate; sfondo app `#F7F9FC` (`bg-surface`). Tutta la UI passa dai token `brand-*`: la palette si ritara dagli 11 `--color-brand-*` in `index.css` senza toccare i componenti. Il verde `emerald` (stati di successo) è volutamente vicino al teal del logo.
- **Semantici**: aperto = smeraldo, chiuso = slate, in apertura = ambra; scadenza ≤ 7 giorni = rosso, ≤ 30 = ambra.
- **Tipografia**: Sora (titoli, 600/700) + Inter (testo) via Fontsource (self-hosted); cifre tabellari (classe `.tabular`) per importi e date.
- **Superfici**: card `rounded-xl` con ombre morbide (`shadow-card`, `shadow-card-hover`); topbar sticky bianca con bordo.
- **Localizzazione**: tutta l'interfaccia è in italiano (dare del tu); importi `Intl.NumberFormat('it-IT')`, date `it-IT`.
- **Accessibilità**: focus ring visibili (`focus-visible:outline-brand-500`), label su ogni input, `aria-label` sulle icone interattive, `prefers-reduced-motion` rispettato, contrasti AA.

## Variabili d'ambiente

`VITE_SUPABASE_URL` e `VITE_SUPABASE_ANON_KEY` (progetto **primario**, mai il secondario) + `VITE_API_BASE_URL` (default `http://localhost:8000/api/v1`).
