# Changelog

Storico delle funzionalità e delle modifiche rilevanti. Formato: data — descrizione.

## 2026-07-09 — Pre-check: requisiti in OR, riquadro in colonna laterale

- **Correzione della formula.** Dentro un requisito le voci del bando sono **alternative**: un bando che elenca quattro settori li accetta tutti, non ne chiede quattro insieme. Contare le voci (`1/4` sui settori) penalizzava a torto: ora un requisito è **soddisfatto con anche una sola voce in comune** e il punteggio è **requisiti soddisfatti / requisiti valutabili** (es. `3/4`). Il caso «bando nazionale» smette di essere speciale — se il bando è aperto a tutte le regioni, una sede qualsiasi lo soddisfa da sé; il flag `nazionale` resta solo come nota per la UI. Nuovo campo `soddisfatta` per dimensione nella risposta API.
- **`CompatibilitaCard`** in cima alla colonna laterale del dettaglio (sopra l'AI-check), al posto della sezione full-width di poche ore prima, troppo ingombrante: frazione + barra e, per ogni requisito, le voci del bando con quelle in comune evidenziate e un'icona di esito. Un bando nazionale non elenca venti regioni (nota + sedi in comune); oltre sei voci le restanti si riassumono in «+N». **Assorbe la card «A chi si rivolge»** (stesse liste, ora con l'esito): in sidebar restano le Tematiche.
- **Card dell'elenco più pulita**: via il badge della modalità di erogazione (Fondo perduto / Contributo in conto interessi), che affollava la riga senza aiutare a scegliere (resta nell'header del dettaglio); il pre-check diventa **«Compatibilità 3/4»** con etichetta esplicita — la sola frazione non si capiva. La riga badge riserva spazio al toggle «salva» sovrapposto, che altrimenti la copriva.

## 2026-07-09 — Punteggio di compatibilità a-priori + AI-check multi-sede

- **Punteggio di compatibilità** azienda↔bando, **dinamico** (mai persistito), mostrato subito in elenco e dettaglio come frazione colorata per banda — prima e senza l'AI-check. Confronta i requisiti di catalogo del bando (regioni, divisioni ATECO, settori, beneficiari), tutti a **peso uguale**. **Tutte le sedi** valgono sul territorio; compare solo con **P.IVA importata** (`ateco_id`+`regione_id`), altrimenti nessun badge. (La formula esatta è quella corretta nella voce successiva, in cima.) Nuovo `services/compatibility.py` + `components/bandi/CompatibilitaBadge.tsx`; i due DB non si uniscono in SQL, quindi i facet azienda si costruiscono una volta per richiesta (cache TTL) e il confronto per-bando è Python puro — nessun round-trip in più (i facet del bando viaggiano nella stessa query dell'elenco).
- **AI-check esteso a tutte le sedi**: il pre-check territoriale (e il gate) considerano ora la sede legale **e** le unità locali (`company_data.derived.regioni_ids`, popolato all'import): basta una sede in una regione ammessa. Le unità locali erano già nel prompt LLM → costo token invariato. Aziende importate prima della modifica: fallback alla sola sede legale finché non rifanno «Aggiorna». Nessuna nuova migration.
- Hardening da review multi-agente avversariale (4 difetti confermati e corretti prima del rilascio): la cache dei facet aziendali viene **invalidata a ogni scrittura** (senza, dopo l'import P.IVA il badge non sarebbe comparso per 60s); la lettura dei dati aziendali **degrada a «nessun badge»** invece di far fallire elenco/dettaglio bandi; gli embed id-only per lo scoring si aggiungono **solo quando un punteggio verrà calcolato**; la riga badge della card riserva spazio al toggle «salva» sovrapposto, che altrimenti copriva proprio la frazione. 433 test backend (27 nuovi).

## 2026-07-09 — Nuovo logo e palette navy

- **Nuovo logo BandoFit** (brand ridisegnato: icona documento + check circolare, wordmark «Bando» navy / «FIT» teal). Fornito come singolo lockup orizzontale: sostituisce `logo-orizzontale.png` (topbar, footer, pagine auth — la variante `vertical` ora riusa l'orizzontale). Icona, **favicon** e apple-touch-icon sono **ritagliate dall'orizzontale** (solo il glifo documento+check). Rimossi `logo-verticale.png` e il file di deposito in radice. `edunews24.png` e il «powered by EduNews24» invariati.
- **Palette riallineata al logo**: colore primario da blu royal `#1E5EFF` a **navy `#2C56C9`** (scala `brand-50`→`brand-950` ancorata al navy `#182448` del wordmark, `brand-900 #182549`). Essendo la UI tokenizzata, il cambio agisce solo sugli 11 `--color-brand-*` in `index.css`: pulsanti, link, focus, nav e gradienti si aggiornano da soli, nessun componente toccato. Il verde di successo (`emerald`) resta com'è, già coerente col teal del logo. Solo frontend, nessuna modifica a backend/DB.

## 2026-07-08 — Redesign della landing page pubblica

- Nuova `pages/Landing.tsx`: hero a due colonne con un **mock di prodotto** (`HeroShowcase`, scheda bando + widget AI-check con anello punteggio) costruito dai token, sezione problema→soluzione, griglia funzionalità con l'**AI-check in evidenza**, «come funziona» in 4 passi, «perché BandoFit» con una fascia di **numeri reali**, piani (riuso `usePlans`+`PlanCard`), **FAQ** (accordion `<details>` nativo), CTA finale e footer ricco con ancore. Header sticky con navigazione ad ancore (smooth-scroll).
- **Copy più onesto**: gli alert email di scadenza non sono ancora implementati, quindi il tema scadenze punta sul **Calendario** reale e gli alert sono citati come «in arrivo» sotto i piani; nessuna statistica di utenti inventata (social proof assente nel prodotto), solo fatti reali (~1.200 bandi, copertura UE→locale, dati dal Registro Imprese, punteggio 0–100 con citazioni).
- Nuovi componenti presentazionali in `components/landing/` (`HeroShowcase`, `SectionHeading`, `FeatureCard`, `Faq`), coerenti al 100% col design system esistente: nessuna nuova dipendenza, nessun asset, palette/font/componenti invariati. Solo frontend.

## 2026-07-08 — Modalità prezzo «Gratis» e «Su richiesta» per piani e add-on

- **`tipo_prezzo`** su `subscription_plans` e `addons` (migration 0010): `importo` (comportamento attuale), `gratis` (la card mostra «Gratis» invece di «0 €»; stesso flusso di attivazione — il piano Gratuito viene **backfillato automaticamente**, insieme a qualunque record a prezzo 0 esistente) e `su_richiesta` (**`etichetta_prezzo`** personalizzabile al posto del prezzo, fallback «Su richiesta»).
- I piani/add-on **su richiesta non sono attivabili self-serve**: la CTA diventa «Richiedi una consulenza» (per gli add-on gratis: «Attiva»), il backend rifiuta con `400` sia il cambio piano sia la registrazione (guard prima del cooldown; in `/registrati` la card è visibile ma non selezionabile) e solo l'admin può assegnarli (`POST /admin/users/{id}/subscription`, `self_serve=False`).
- **Flusso di contatto rimandato a una fase successiva**: il click su «Richiedi una consulenza» passa dal punto di estensione `requestConsultation({kind, slug})` (`lib/consulenza.ts`, gemello di `purchaseAddon`) e per ora apre il dialog «Richiesta in arrivo». Il testo del prezzo è centralizzato in `prezzoDisplay` (`lib/prezzo.ts`); nei form admin il select «Prezzo mostrato come» disabilita prezzo/etichetta secondo la modalità. Il futuro endpoint reale di acquisto add-on dovrà rifiutare lato server gli add-on su richiesta.

## 2026-07-07 — Pagina Abbonamento dedicata e catalogo Add-on

- La sezione **abbonamenti** lascia il Profilo e vive nella nuova pagina **«Abbonamento»** (`/app/abbonamento`, voce in navigazione): stessa griglia piani, stesso cambio piano (avviso di retrocessione famiglia compreso), piano ereditato per gli account collegati. Nel Profilo resta un rimando compatto col piano attuale; i link «Vedi i piani»/«passa a un piano superiore» puntano alla nuova pagina. Registrazione invariata.
- **Add-on** (migration 0009, tabella `addons` gemella di `subscription_plans`): catalogo gestito dagli admin dalla nuova pagina **Admin → Add-on** con lo stesso CRUD dei piani (crea con slug stabile e unico, modifica, disattiva — mai eliminare; prezzo in € come i piani, mostrato una tantum). Lato cliente le card appaiono nella pagina Abbonamento con bottone **«Acquista»**: nessun acquisto per ora — si apre l'avviso «In arrivo» e il click passa dal punto di estensione `purchaseAddon(slug)` (`lib/addons.ts`), pronto per il flusso futuro. Con 7 voci cliente (+3 admin) la navigazione per esteso parte da `xl` (sotto: menu hamburger) e la voce admin dei piani si accorcia in «Piani»; errori di caricamento del catalogo mostrati con retry (mai confusi con un catalogo vuoto). 388 test backend (13 nuovi).

## 2026-07-07 — Bandi salvati e Calendario

- **Bandi salvati**: segnalibro su ogni card (lista e dettaglio) con stato ottimista; nuova scheda **«Salvati»** con i preferiti dell'utente. I preferiti sono **riferimenti** al catalogo (migration 0008, `saved_bandi`, snapshot denormalizzato senza FK cross-DB): un bando sparito dal catalogo resta visibile dallo snapshot con «Non più disponibile» e si può rimuovere; uno scaduto si mostra normalmente. Cap 200 per utente; salvataggio/rimozione idempotenti.
- **Calendario** (nuova scheda, vista mensile): griglia nativa `Intl it-IT` (lunedì per primo, nessuna libreria di date), agenda del giorno selezionato, CRUD eventi personali (titolo, data, «tutto il giorno», orari opzionali, note) con dialog e conferma di eliminazione a due passi. Da un bando (dettaglio o salvati) si aggiunge la **scadenza al calendario** con un click: evento di tipo «bando» evidenziato in ambra, **data in sola lettura** (deriva dal bando ufficiale, modificabili solo titolo e note), una sola scadenza per bando (idempotente). Preferiti ed eventi sono **indipendenti** (rimuovere l'uno non tocca l'altro). Date e orari in calendario italiano (wall-clock, senza fusi). Cap 500 eventi; 375 test backend (48 nuovi). Hardening da review multi-agente avversariale (18 difetti confermati e corretti prima del rilascio: scoping nei test, limiti int4/UUID normalizzati, titoli vuoti e date fuori intervallo respinti come 400, paginazione e selezione del mese senza stati orfani, nav desktop da lg, altezze uniformi delle card).
- **Calendario più grande e più diretto**: griglia a tutta larghezza con celle alte e agenda del giorno come colonna laterale sticky su desktop; **click su un giorno apre subito il form** del nuovo evento (data precompilata), click su un chip apre la modifica dell'evento.

## 2026-07-07 — Rifiniture AI-check e dettaglio bando

- **Elenchi e FAQ finalmente visibili nella scheda bando**: il renderer del contenuto gestiva solo il tipo `list`, ma il catalogo usa `bullet_list`/`numbered_list` (1.454 sezioni saltate in tutto il catalogo!) e `faq` — ora resi come elenchi puntati/numerati e riquadri domanda/risposta; i link dei segmenti leggono anche la chiave reale `url`.

- **Punteggio a colori** ovunque (report, card, cruscotto): rosso 0–39, arancione 40–59, giallo 60–79, verde 80–100 — numero e barra (`lib/scoreColor.ts`). Per l'esito negativo nessun badge («Da approfondire» eliminato): il colore e i verdetti dei singoli requisiti dicono già tutto; restano «In linea col bando» e «Dati da completare».
- Report più asciutto: via la nota sulla griglia negli allegati e via il «Dato aziendale usato» dai requisiti di ammissibilità (resta nei criteri).
- **Dettaglio bando senza box vuote**: i riquadri Dotazione/Max per progetto/Apertura/Scadenza compaiono solo se valorizzati, e la card «Candidatura» solo se esiste un link.

## 2026-07-07 — Tutti i dati aziendali nella pagina Azienda

- La pagina **Azienda** (`/app/azienda`) è ora l'unica casa dei dati aziendali, in tre sezioni: **«Dati aziendali»** (riepilogo leggibile dei campi compilati; il form completo si apre solo col bottone «Modifica», con Salva/Annulla — e non viene mai risincronizzato mentre si scrive), **«Dossier certificato»** dal Registro Imprese (con badge, data di aggiornamento e «Aggiorna») e **«Documenti ufficiali»**. Il titolo della pagina è la denominazione dell'azienda.
- Nel **Profilo** il form aziendale è sostituito da un rimando compatto alla pagina Azienda (`AziendaTeaser`): niente più dati aziendali in due posti.

## 2026-07-07 — AI-check: pagina dedicata, tono costruttivo, punteggi più discriminanti

- **Pagina «AI-check»** (`/app/ai-check`, in navigazione): cruscotto con la quota del piano (barra di consumo) e storico raggruppato per bando — ultima analisi in evidenza, numero versioni, «Apri report». Sostituisce la card compatta nella pagina Azienda.
- **Tono costruttivo**: mai «Non ammissibile» né «punteggio non rilevante» — il report è generato dall'AI e può sbagliare. Gli esiti diventano «In linea col bando» / «Dati da completare» / «Da approfondire» (mai rosso), col rimando ai dettagli e al testo ufficiale; il punteggio resta sempre leggibile.
- **Mai nomi tecnici in interfaccia**: i campi citati dall'AI (`derived.beneficiari[1].nome`…) sono tradotti in etichette italiane con fonte («Categorie di beneficiari (Registro Imprese)»); il prompt di matching impone linguaggio naturale nelle motivazioni; i dati mancanti guidano con link a Profilo (completa) e Azienda (importa).
- **Pesi euristici ribilanciati**: il punteggio ora premia soprattutto requisiti soddisfatti (30) e criteri del bando (40) — che cambiano da bando a bando — e meno i confronti di catalogo (settore 12, regione 9, beneficiari 9), quasi identici per bandi simili: prima quattro bandi diversi finivano tutti sullo stesso punteggio. 327 test backend.

## 2026-07-07 — AI-check di compatibilità azienda ↔ bando

- **AI-check** dal dettaglio bando: l'AI (API Anthropic, `claude-sonnet-5`, output vincolato da schema) estrae dal testo del bando i **requisiti obbligatori** e i **criteri di valutazione** — con citazione letterale di ogni passaggio — e li confronta punto-punto col profilo dell'azienda (dati form, dossier certificato, persone, testo della visura). **Ammissibilità e punteggio sono calcolati in Python, mai dal modello**: gate binario sui requisiti (uno mancato ⇒ non ammissibile, dato mancante ⇒ «da verificare», mai promosso d'ufficio), punteggio «stima» se il bando pubblica la griglia, «euristico interno» altrimenti; pre-check esatti sui facet del catalogo prevalgono sui verdetti del modello, e le citazioni non ritrovate nel testo vengono marcate.
- **Report verificabile e riconsultabile** (tabella `ai_checks`, migration 0007, storico versionato): ogni verdetto mostra la sezione del bando citata e il dato aziendale usato; punti di forza/debolezza, dati mancanti con invito a completarli, disclaimer. Nella pagina del bando: card in sidebar con quota residua + sezione report a tutta larghezza con selettore versioni; nella pagina Azienda lo storico compatto.
- **Quota e costi sotto controllo**: ogni generazione consuma 1 AI-check della quota annua del piano (condivisa dall'azienda, contata **atomicamente dalle righe dello storico** nella finestra dell'abbonamento — le analisi fallite non bruciano quota), cooldown 5 minuti per bando, lock anti-corsa, mai retry su chiamate addebitate, token e costo reale (~0,10–0,20 $/report) annotati su riga e registro consumi. L'**estrazione è cachata per bando** (`bando_requirements`): il costo si ammortizza tra tutte le aziende.
- Esecuzione asincrona in-process (riga `pending` + task in background, polling dal frontend ogni 4s, failsafe 10 minuti su ogni lettura E sull'avvio): nessuna coda esterna. Env: `ANTHROPIC_API_KEY`, `AI_CHECK_MODEL` (vuota = feature disattivata).
- **Hardening da review multi-agente avversariale** (31 difetti confermati e corretti prima del rilascio), tra cui: i vincoli di catalogo bocciati entrano nel gate anche se l'estrazione non ha prodotto il requisito corrispondente (voce sintetica nel report); ATECO e settore trattati come evidenze alternative (basta un match — mai un falso «non ammissibile» da un tag tematico); un pre-check non calcolabile non retrocede più i verdetti fondati sui dati del form (le aziende senza import certificato possono risultare ammissibili); quota contata atomicamente dalle righe dello storico (chiuse le corse quota/ledger); cache estrazioni invalidata sull'intero input serializzato (facet comprese); tetto token raddoppiato per il ragionamento adattivo del modello; CF personale del titolare mai nel contesto AI; id malformati → 404; failsafe e cooldown senza scappatoie. 326 test backend (86 nuovi).

## 2026-07-07 — Ordinamento bandi: chiusi sempre in coda, default «Più recenti»

- **I bandi chiusi vanno sempre in fondo all'elenco**, con qualunque ordinamento: "chiuso" significa stato `chiuso` nel catalogo **oppure** scadenza passata (rispetto a oggi nel fuso italiano), così l'ordine è corretto anche se lo stato non è aggiornato dalla pipeline. Implementato con due query complementari (PostgREST non ordina per espressioni) e paginazione che unisce le due code.
- L'ordinamento di **default diventa «Più recenti»** (`pubblicazione_desc`); con «Scadenza più vicina» la prima pagina mostra i bandi che scadono da oggi in poi (prima i bandi scaduti finivano in testa), e tra i chiusi in coda compare prima la chiusura più recente.

## 2026-07-06 — Pagina Preferenze

- Le preferenze bandi hanno una **pagina dedicata** (`/app/preferenze`, in navigazione): a sinistra il profilo **ereditato dall'azienda** (ATECO, settore, regione, beneficiari derivati — chip bloccate, sempre incluse in «Bandi per te»), a destra le 7 faccette con chip rimovibili e un nuovo multi-select con ricerca (`TagSelect`) che marca i valori già coperti dall'azienda. Barra di salvataggio fissa solo a modifiche presenti; nel Profilo resta un rimando compatto.

## 2026-07-06 — Visura camerale ufficiale

- **«Richiedi visura»** nella pagina Azienda: il PDF ufficiale del Registro Imprese (2,90 € imprese individuali/enti REA – 4,90 € società, +IVA) richiesto via openapi.it con flusso asincrono (di solito evaso in pochi secondi). Il tipo d'impresa giusto viene individuato **per tentativi a costo zero** (i rifiuti del Registro sono gratuiti), ordinati in base alla forma giuridica nota dall'import; gli enti iscritti solo al REA sono serviti dal canale impresa individuale (verificato sul campo).
- Il PDF è archiviato nel **bucket Storage `company-documents`** e scaricabile da titolare e account collegati; il **testo estratto** (pypdf) — oggetto sociale e poteri da statuto compresi — resta server-side come input pregiato per il futuro AI-check (tabella `company_documents`, migration 0006).
- Stesse protezioni di spesa delle altre chiamate a pagamento: lock per azienda, una sola richiesta in lavorazione per volta (indice unico parziale), registro consumi su ogni tentativo, nessun retry su esiti ignoti.

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
