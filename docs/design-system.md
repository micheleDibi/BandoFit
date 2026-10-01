# Design system del frontend

Riferimento per chi scrive interfaccia. Le regole valgono per ogni pagina e ogni componente; le eccezioni si scrivono qui, non nel codice.

BandoFit aiuta aziende, enti e consulenti a capire in pochi secondi tre cose di un bando: **fa per me? quanto vale? entro quando?** Ogni schermata risponde a queste domande prima di ogni altra e porta a una sola azione.

## Principi

La veste è «Navy deciso» (ottobre 2026): il blu del marchio forte, un colore per area, stati colorati, un accento caldo corallo, card con ombra, grafici e movimento breve. Colorata ma ordinata: il colore orienta, la parola spiega.

1. **Card sul piano.** Lo sfondo dell'app è il piano (`desk`, azzurro pallido); il contenuto sta su fogli bianchi (`sheet`) con l'ombra `card`: `Card` per gli oggetti uguali da confrontare, `Panel` per un blocco di servizio, `KpiCard` per un indicatore. Dentro una card le sezioni si separano con un titolo e un filetto. Mai un riquadro dentro un riquadro.
2. **Un'azione primaria per schermata.** Un solo pulsante pieno; le altre azioni sono secondarie o testuali. Sulla fascia navy il primario è `inverse`.
3. **Ogni area ha il suo colore** (mappa qui sotto): l'icona nella barra laterale, la fascia dell'intestazione con il suo `IconChip`, le etichette e i grafici dell'area. Il colore d'area orienta, non significa uno stato.
4. **Lo stato è una pillola colorata con la parola** (`Status`): il colore fa trovare, la parola dice; una riga ha al massimo uno stato. La compatibilità è un contatore di requisiti (`Fit`), e una compatibilità bassa non è un errore: segmenti vuoti, mai rosso.
5. **Un nome per cosa**, uguale in menu, titolo di pagina, pulsante e messaggio (glossario in fondo).
6. **La scadenza è il segno ricorrente** (`Due`): una tessera colorata per urgenza con giorno e mese, poi il tempo relativo in parole; stessa forma in elenco, scheda, calendario e Home.
7. **Movimento breve e utile**: 150 ms al passaggio del mouse, 250 ms per le aperture, conteggi e grafici che si disegnano all'arrivo. Con `prefers-reduced-motion: reduce` niente animazioni: solo cambi istantanei.

## Token (`frontend/src/index.css`, blocco `@theme`)

### Colore

| Token | Valore | Utility | Uso |
|---|---|---|---|
| `brand-50…900` | scala esistente (`brand-500 #2c56c9`) | `bg-brand-*`, `text-brand-*` | Solo tramite i ruoli qui sotto |
| `ink` | `#212b50` | `text-ink` | Testo principale e titoli (il navy del logo). 13,8:1 su bianco |
| `ink-2` | `#45556c` | `text-ink-2` | Testo secondario: metadati, descrizioni, voci di menu. 7,6:1 |
| `ink-3` | `#566580` | `text-ink-3` | Testo terziario: etichette dei dati, note, aiuto, segnaposto. 5,9:1 |
| `ink-off` | `#90a1b9` | `text-ink-off` | Solo segni senza significato (punto neutro, icone disattivate). Mai per testo |
| `on-accent` | `#ffffff` | `text-on-accent` | Testo su `accent`, su `ink` e sulla fascia |
| `sheet` | `#ffffff` | `bg-sheet` | Il foglio: card, pannelli, campi, menu, finestre |
| `desk` | `#f3f6fb` | `bg-desk` | Il piano: lo sfondo dell'app (`body`), su cui stanno le card |
| `sunken` | `#eaeef6` | `bg-sunken` | Fondo incassato: segmenti, barre e piste vuote, scheletri, voce di menu al passaggio |
| `line` | `#e2e8f0` | `border-line` | Filetto che separa. Non delimita un controllo |
| `line-control` | `#8290a8` | `border-line-control` | Bordo dei controlli e della testata delle tabelle. 3,2:1 |
| `accent` | `= brand-500` | `bg-accent`, `text-accent` | Azione primaria, scheda attiva, anello del focus |
| `accent-hover` | `= brand-600` | | Primario al passaggio del mouse; testo dei link |
| `accent-soft` | `= brand-100` | | Fondo di «In apertura», dei filtri attivi, della scadenza lontana, del pulsante testuale al passaggio |
| `accent-line` | `= brand-200` | | Filetto dell'avviso informativo (`Alert` info) |
| `navy-950…200` | `#121a3d`, `#1a2350`, `#243066`, `#2f3d7d`, `#c9d0ea` | `bg-navy-*`, `text-navy-*` | Il navy della fascia e di Home; `navy-900` è il testo di `Button inverse` |
| `warm` | `#ef6a45` | `bg-warm` | L'accento caldo (corallo): urgenza, scadenza entro 7 giorni, «In scadenza». 3,1:1: solo segni, mai testo |
| `warm-ink` / `warm-soft` | `#b13b18` / `#fdebe5` | | Testo e fondo dell'accento caldo. 5,2:1 |
| `fit` | `#1aa38c` | `bg-fit` | Il verde del logo. Solo compatibilità, «Aperto», conferme. 3,2:1: mai per testo |
| `fit-ink` | `#0e7a68` | `text-fit-ink` | Testo di esito positivo e di «Aperto». 4,7:1 sul `fit-soft` |
| `fit-soft` | `#e6f5f2` | `bg-fit-soft` | Fondo di «Aperto», del `Badge` success e dell'avviso di conferma |
| `warning` | `#f59e0b` | `bg-warning` | Ambra piena, solo segni: bordo sinistro dell'avviso, pallino di «Attenzione». Mai testo |
| `warning-ink` / `warning-soft` / `warning-line` | `#973c00` / `#fef3c7` / `#f0c36b` | | Attenzione: scadenza entro 30 giorni, avvisi da guardare. 6,4:1. Il soft è più deciso di prima (`#fffbeb`): sul bianco si legge come tinta |
| `danger` / `danger-soft` / `danger-line` | `#c10007` / `#fef2f2` / `#f1a5a8` | | Errori, azioni distruttive |
| `neutral-ink` / `neutral-soft` | `#475569` / `#eef1f5` | | Pillole ed etichette senza tono: «Chiuso», «Spento», tag. 6,7:1 |
| `veil` | `rgb(33 43 80 / 0.4)` | `bg-veil` | Velo sotto finestre e cassetti |

Il colore non basta mai da solo: ogni stato ha una parola («Aperto», «tra 5 giorni», «2 su 4»), ogni grafico ha i numeri scritti. Le classi `slate-*`, `emerald-*`, `amber-*`, `red-*`, `violet-*` non si usano nelle pagine: si usano i token con un ruolo.

### Colori d'area

Ogni area ha tre token: **base** (segni, barre, anelli, bordo sinistro: mai sotto un testo), **soft** (fondo di chip, pillole, tessere) e **ink** (testo e icone: ≥ 5,2:1 sul soft e sul bianco). Le classi si prendono da `components/ui/area.ts` con `areaClassi(area)`, che restituisce `{ base, soft, ink, bordo, suSoft, tratto, riempimento }` (bordo = `border-l-4` nel colore base; suSoft = fondo soft + testo ink; tratto e riempimento = `stroke-*` e `fill-*` per i grafici), e `areaIcona[area]` per l'icona. Mai una classe costruita a runtime (`bg-area-${x}`): Tailwind non la troverebbe.

| Area | Base / soft / ink | Icona | Pagine (chi passa `area` a `PageHeader`) |
|---|---|---|---|
| `home` | navy `#243066` / `#c9d0ea` / `#1a2350` | `House` | Home: fascia navy senza colore d'area |
| `bandi` | `#2c56c9` / `#e7eefc` / `#1f3f99` | `FileText` | Bandi, scheda del bando, Bandi salvati |
| `aicheck` | `#6d4ae0` / `#efeafd` / `#5232c4` | `Sparkles` | AI-check |
| `scadenze` | `#ef6a45` / `#fdebe5` / `#b13b18` (= warm) | `CalendarDays` | Calendario |
| `partenariati` | `#0e9488` / `#e2f5f2` / `#0a6b62` | `Users` | Partenariati (tutte le pagine) |
| `consulenze` | `#d99206` / `#fdf3dc` / `#8a5a00` | `MessageSquare` | Consulenze e area progettista |
| `azienda` | `#3d5cb8` / `#e8ecf8` / `#2f3d7d` | `Building2` | Dati azienda, Aziende gestite |
| `account` | `#5b6b8c` / `#edf0f5` / `#3f4b66` | `CircleUser` | Abbonamento, checkout, Profilo, Preferenze, Account collegati, Notifiche |
| `admin` | `#475569` / `#eef1f5` / `#334155` | `ShieldCheck` | Admin |

Nella barra laterale ogni voce ha l'icona nel colore (ink) della sua area, con la stessa mappa. Il base di `consulenze` (2,6:1) e di `scadenze` (3,1:1) è chiaro: nei grafici il valore sta sempre scritto accanto alla barra o al centro dell'anello.

### Tipografia

| Stile | Utility | Font | Taglia | Uso |
|---|---|---|---|---|
| `title-bando` | `text-title-bando` | Sora 600 | 28/36, -0.015em | Il titolo del bando nella sua scheda e la promessa del pannello laterale delle pagine di accesso |
| `title-hero` | `text-title-hero` | Sora 600 | 32/40 sotto `sm`, 40/48 da `sm`, -0.02em | Solo il titolo dell'hero della landing. Le variabili sono `--title-hero-*`, fuori dal gruppo `--text-*`: lì Tailwind genererebbe una seconda `text-title-hero` a taglia fissa, che annullerebbe il passaggio di taglia |
| `title-page` | `text-title-page` | Sora 600 | 24/32, -0.01em | Titolo di pagina, uno per schermata, uguale alla voce di menu |
| `title-section` | `text-title-section` | Sora 600 | 18/26 | Titolo di sezione sopra un filetto |
| `figure` | `text-figure` | Sora 600 | 28/32 | Cifra in evidenza, sempre tabellare |
| `figure-sm` | `text-figure-sm` | Sora 600 | 18/24 | Valore nei fatti chiave, importo nelle righe |
| `due-day` | `text-due-day` | Sora 600 | 26/28, -0.02em | Il giorno nel segno della scadenza |
| `prose` | `text-prose` | Inter 400 | 16/26 | Testo lungo (descrizione del bando, report); righe sotto gli 80 caratteri |
| `row-title` | `text-row-title` | Inter 600 | 16/24 | Titolo di una riga dell'elenco |
| `body` | `text-body` | Inter 400 | 14/22 | Taglia di base dell'interfaccia |
| `title-group` | `text-title-group` | Inter 600 | 14/20 | Titolo di gruppo, testo dei pulsanti |
| `small` | `text-small` | Inter 400 | 13/20 | Metadati, etichette dei campi e dei dati, aiuto |
| `caption` | `text-caption` | Inter 500 | 12/16 | La taglia minima: tempo relativo, contatori, etichette di gruppo del menu |

Sora solo tramite gli stili `title-*` (compreso `title-hero`), `figure*` e `due-day` (peso unico 600: si carica solo `@fontsource/sora/600.css`). Il numero di `KpiCard` è `figure`; nei grafici etichette e valori sono `caption`. Inter in tre pesi: 400 testo, 500 etichette e menu, 600 titoli di gruppo, titoli di riga, pulsanti. Niente taglie arbitrarie (`text-[11px]`), niente `uppercase`, niente `tracking-wide`. Cifre sempre tabellari (`tabular-nums`).

### Raggi, ombre, spazi

| Token | Valore | Uso |
|---|---|---|
| `radius-mark` | 4px | Caselle di spunta, segmenti di `Fit`, scheletri |
| `radius-control` | 8px | Pulsanti, campi, filtri, avvisi, tessera di `Due`, `IconChip` piccoli |
| `radius-panel` | 12px | Pannelli, card, fascia, menu, finestre, `IconChip` grande |
| `radius-pill` | 999px | Avatar, contatori, `Badge`, `Status`, filtri attivi, barre |
| `shadow-card` | `0 1px 2px rgb(22 32 74 / .06), 0 2px 8px rgb(22 32 74 / .06)` | Ciò che sta sul piano: `Card`, `Panel`, `KpiCard`, la fascia, il pulsante primario |
| `shadow-card-hover` | `0 6px 20px rgb(22 32 74 / .12)` | La card interattiva al passaggio del mouse (con mezzo passo in su) |
| `shadow-overlay` | `0 12px 32px -8px rgb(33 43 80 / .28), 0 2px 6px rgb(33 43 80 / .08)` | Ciò che sta sopra la pagina: menu, finestre, notifiche a comparsa |

In `lib/cn.ts` il gruppo `shadow` conosce `card`, `card-hover` e `overlay`: un `shadow-*` passato in `className` sostituisce quello del componente.

### Gradiente

Uno solo: la **fascia** (`bg-banda` = `linear-gradient(120deg, #1a2350 0%, #24357a 55%, #2c56c9 100%)`, con `navy-900` pieno sotto). Si usa solo per `PageHeader` con `area`, per l'hero e la card d'invito finale della landing e per il pannello laterale di `AuthLayout`. Il testo sulla fascia è bianco (titolo) o bianco/80 (descrizione, ritorno, riga sopra): ≥ 4,7:1 anche sul lato chiaro. Il riflesso degli scheletri (`riflesso`) è un segnale di caricamento, non una decorazione.

### Movimento

| Token | Valore | Uso |
|---|---|---|
| `ease-uscita` | `cubic-bezier(0.2, 0.8, 0.2, 1)` | L'andamento di tutte le transizioni e animazioni |
| durata 150 ms | `duration-150` | Passaggio del mouse: colori, bordi, ombre, card interattiva |
| durata 250 ms | `duration-250` | Aperture: indicatore delle schede, `Accordion`, entrata degli overlay |
| `animate-entrata` | dissolvenza + 8px di risalita, 250 ms | `Dialog`, `Popover`, `Menu`, `Toast` |
| `animate-entrata-sinistra` / `-destra` | dissolvenza + 24px dal lato, 250 ms | `Drawer` |
| `animate-shimmer` | banda chiara che attraversa, 1,6 s | `Skeleton` (utility `riflesso`) |
| `animate-crescita` | le barre crescono dalla base, 600 ms | `BarChart` |

Nei componenti il movimento sta dietro `motion-safe:` (o si spegne con `motion-reduce:`); in più `index.css` azzera durate e ripetizioni con `prefers-reduced-motion: reduce`. I conteggi (`KpiCard`) e gli anelli (`ProgressRing`) arrivano subito al valore. Mai un'animazione che si ripete senza motivo (solo il riflesso, finché si carica).

Spaziature dalla scala di Tailwind, con questi ruoli: 1 (4px) fra etichetta e valore; 2 (8px) fra icona e testo e fra pulsanti; 3 (12px) dentro i controlli; 4 (16px) fra gli elementi di una sezione; 6 (24px) fra colonne e sezioni; 8 (32px) sopra la pagina; 10 (40px) ai lati della pagina su desktop; 12 (48px) fra blocchi maggiori e fra il contenuto e la colonna laterale di `Page variante="dettaglio"` (i 24px valgono per le colonne interne).

Larghezze: barra laterale 248px; elenco e dettaglio 1280px; pagina a sezioni con schede 1040px; flusso a passi 760px; colonna laterale 340px; testo lungo 680px. Il contenuto è centrato nel piano (`mx-auto`). Le pagine non scrivono `max-w-*`: usano `Page` con la sua variante; per il testo lungo dentro una colonna c'è il token `max-w-lettura` (680px, `--container-lettura` in `index.css`). Fuori dalla cornice dell'app: le pagine di accesso usano `AuthLayout` (colonna del modulo da 560px con il modulo largo 380px, a destra il pannello sulla fascia blu `bg-banda`); la landing ha un contenitore centrato a 1112px (`components/landing/Sezione.tsx`), l'unico `max-w` scritto fuori da `Page`.

## Modelli di pagina

| Modello | Componente | Dove |
|---|---|---|
| Elenco | `Page variante="elenco"` | Bandi, Bandi salvati, AI-check, Partenariati, Notifiche, Calendario, Consulenze, Richieste di consulenza, Aziende gestite, Account collegati, Utenti e Pagamenti (admin) |
| Dettaglio con colonna laterale | `Page variante="dettaglio"` | Home, bando, call, consulenza, richiesta di consulenza |
| Sezioni con schede | `Page variante="sezioni"` | Dati azienda, Abbonamento, Preferenze, Profilo, Piani e Add-on (admin), conversazione e segnalazione dei partenariati, «Questa pagina non è disponibile» |
| Flusso a passi | `Page variante="flusso"` | Nuova call, checkout ed esito del pagamento |

Le pagine di dettaglio mostrano gli stati 404 e 410 in `Page variante="sezioni"`, senza colonna laterale. Landing e pagine di accesso stanno fuori dalla cornice e non usano `Page`.

Ogni pagina comincia con `PageHeader` (ritorno, titolo, una riga di descrizione, azioni a destra); le pagine della cornice dell'app gli passano la loro `area` (mappa in «Colori d'area»), che lo trasforma nella fascia navy. Lo stato delle schede sta nell'URL (`?tab=`, hook `useTab`).

## Componenti (`frontend/src/components/ui/`)

Un solo posto per ogni cosa. Se una pagina ha bisogno di un pattern che non c'è, si aggiunge qui, non si rifà a mano.

| Componente | Sostituisce |
|---|---|
| `Button` (primary, secondary, ghost = testuale, danger, inverse; sm, md, lg; loading), `IconButton`, `TextLink` | i 4 stili di «X» e le 3 regole di sottolineatura |
| `Field` (`TextField`, `TextareaField`, `SelectField`), `Select`, `SearchInput`, `Checkbox`, `RadioGroup`, `Switch`, `PasswordField`, `PasswordStrengthMeter` | input e select scritti a mano, le 4 copie del campo password |
| `Filter`, `Segment`, `Chip`, `Badge` (pillola colorata) | filtri della lista, chip dei filtri attivi, pill a mano |
| `Tabs` e `TabPanel` (con `useTab`), `Stepper`, `Accordion` | `Schede` dei partenariati, la tablist a mano, i 18 `<details>` |
| `PageHeader` (con la fascia), `Page`, `SectionHeader` e `Section`, `BackLink` | le 26 h1 a mano e le larghezze diverse |
| `Status`, `Due`, `Fit`, `Facts`, `DefinitionList`, `Table`, `BandoRow` | badge di stato, MetaTile/StatTile, le 7 tabelle con 3 testate |
| `KpiCard`, `ProgressRing`, `BarChart`, `IconChip` (con `area.ts`) | indicatori, anelli e grafici a mano, icone colorate a mano |
| `Alert`, `InlineError`, `EmptyState`, `ErrorState`, `Skeleton`, `Spinner`, `ProgressBar` | i ~90 avvisi e ~75 errori a mano, le barre con `style width` |
| `Popover`, `Menu`, `Dialog`, `ConfirmDialog`, `Drawer`, `Toast` (`useToast`), `Tooltip` | i 5 dropdown, le 16 conferme rifatte, i flash con `setTimeout`, i `title=` |
| `Card`, `Panel`, `Avatar`, `Pagination`, `Combobox`, `TagSelect`, `AuthLayout` | |

`BandoRow` è l'unico della tabella che vive fuori da `ui/`, in `components/bandi/`: la riga del registro dei bandi.

Props da conoscere, oltre a quelle ovvie:
- `area` (tipo `Area` di `ui/area.ts`): su `PageHeader` accende la fascia navy con l'`IconChip` inverse dell'area (e `icon` per cambiarne l'icona); senza `area` l'intestazione resta chiara come prima. Su `Card` è il bordo sinistro di 4px; su `Panel`, `KpiCard` ed `EmptyState` è il colore dell'`IconChip` (con `icon`); su `Badge` è la pillola nel colore dell'area.
- `Button` `inverse`: fondo bianco, testo navy, anello del focus bianco. Solo sulla fascia, dove fa la parte del primario (un solo pulsante pieno vale anche lì). L'azione secondaria sulla fascia è un pulsante a contorno bianco (bordo bianco/60, testo bianco, fondo trasparente, hover bianco/10): classi in `components/shared/fascia.ts`.
- `Badge` `tone`: `neutral` (default), `info`, `success`, `warning`, `danger`, `warm`; i nomi vecchi `brand`, `emerald`, `amber`, `slate`, `red` valgono come info, success, warning, neutral, danger.
- `Status` `tono`: `aperto` (fit), `in-apertura` (accent), `in-scadenza` (warm), `attenzione` (warning), `errore` (danger), `chiuso` e `neutro` (neutral). Il pallino cambia anche forma: pieno, vuoto, quadrato. Uso: `in-apertura` solo per ciò che è in corso o in attesa (inviata, in esame, in attesa); `attenzione` per ciò che va controllato (da verificare, dato mancante, incerto, non utilizzabile, sospeso); `errore` per un esito non riuscito.
- `Due`: tessera warm entro 7 giorni, warning entro 30, accent oltre; neutra se passata o con `conConto={false}` (bando non in corso).
- `Fit` `variante="anello"`: un `ProgressRing` verde con N al centro e «su M» accanto, per i riquadri in evidenza; di default i segmenti.
- `Card` `interattiva`: ombra `card-hover` e mezzo passo in su al passaggio, solo se la card intera porta da qualche parte.
- `KpiCard` `valore`: un numero si conta da 0 (lo screen reader legge solo il finale); una stringa («1.250.000 €») si mostra com'è. Con `to` la card intera è un link: niente altri elementi interattivi dentro.
- `ProgressRing` e `BarChart`: `tono` è `fit`, `accent`, `warm` o un'area; `label` / `ariaLabel` dicono il grafico in parole, con i numeri che contano. `BarChart` scrive il valore sopra ogni barra.
- `IconChip` `icon`: il componente di lucide (`FileText`); accetta anche un elemento (`<FileText />`). Sempre `aria-hidden`: accanto c'è la parola.
- `Stepper` `raggiunto`: indice dell'ultimo passo raggiunto (default il corrente). Con `onVai` sono cliccabili tutti i passi fino a quello, anche oltre il corrente: in un flusso salvato a passi (la bozza di una call) si torna avanti fin dove si era arrivati.
- `Alert` `ruolo`: di default `status` per info e ok, `alert` per attenzione ed errore; `none` quando l'avviso sta dentro una regione `aria-live` già montata, che annuncia da sé (due regioni annidate rischiano il doppio annuncio).
- `RadioGroup` `descrizione`: aiuto sotto la legenda del gruppo, collegato con `aria-describedby`; anche ogni opzione accetta una sua `descrizione`.
- `PasswordStrengthMeter`: solo informativo, non blocca mai l'invio; va sotto un `PasswordField` con `autoComplete="new-password"`.

Icone: `lucide-react`, 16 o 20px (24px nell'`IconChip` grande), tratto 1,75 (lo impone `index.css` con `.lucide { stroke-width: 1.75 }`, non serve passarlo all'icona), nel colore del testo accanto o nell'ink della sua area. Un'icona accompagna una parola; un pulsante di sola icona ha sempre `aria-label`. Un'icona colorata sta in un `IconChip` (quadrato arrotondato su fondo soft), mai in un cerchio. `Sparkles` solo per l'AI-check. Mai emoji.

## Mai e sempre

| Mai | Sempre |
|---|---|
| Più di un pulsante pieno nella stessa schermata | Un primario (sulla fascia: `inverse`); gli altri secondari o testuali |
| Un colore senza la sua parola | `Status` e `Badge` con il testo, `Due` con il tempo relativo, grafici con i numeri scritti |
| Più di uno stato per riga; un `Badge` al posto di uno stato | Uno `Status` per riga; il `Badge` per tipologie, ruoli, etichette |
| Il colore di un'area usato per uno stato (o viceversa) | Area = dove sei (fascia, icona, etichette dell'area); stato = com'è la cosa (fit, accent, warm, warning, danger, neutral) |
| Classi costruite a runtime (`bg-${colore}`) | Le mappe statiche di `ui/area.ts` e dei componenti |
| Testo su un colore base d'area o su `warm`/`fit`/`warning` pieni | Testo ink sul soft (≥ 4,5:1); il base solo per segni, barre e bordi |
| Riquadri annidati, una card per ogni frase | Card sul piano; dentro, sezioni con titolo e filetto |
| Ombre a mano (`shadow-lg`, `shadow-[…]`) | `shadow-card` sul piano, `shadow-card-hover` al passaggio, `shadow-overlay` sopra la pagina |
| Gradienti fuori dalla fascia | Tinte piene; `bg-banda` solo in `PageHeader` con `area`, nell'hero e nell'invito finale della landing e nel pannello di `AuthLayout` |
| `hover:-translate-y` su ciò che non porta da nessuna parte | `Card interattiva` o `KpiCard` con `to`: la card intera è il link |
| Animazioni lunghe, ripetute o senza `motion-safe:` | 150 / 250 ms con `ease-uscita`; con il movimento ridotto niente animazioni |
| Icone in un cerchio, icone colorate sciolte | `IconChip` nel colore dell'area, con la parola accanto |
| Etichette tutte maiuscole sopra i titoli | Un titolo di gruppo in frase |
| Metadati uniti da «·» | Elementi separati con `gap` |
| «→» in coda a link e pulsanti | Il verbo basta; per i link esterni l'icona `ExternalLink` |
| Stato vuoto centrato | `EmptyState`: (`IconChip`), frase, spiegazione e azione a sinistra |
| Compatibilità bassa in rosso | Segmenti vuoti |
| Testo in `ink-off` o sotto 12px | `ink-3`, taglia `caption` o superiore |
| Colori `slate/emerald/amber/red/violet` nelle pagine | I token con un ruolo e i colori d'area |

## Scrittura

- Italiano semplice, dare del tu, frasi corte. Si dice la conseguenza pratica, non il meccanismo. Mai «pool», «tetto», «ciclo» (si dice «anno»).
- Solo l'iniziale maiuscola: «Bandi salvati», «Vai al bando».
- Pulsanti: un verbo che dice che cosa succede, con lo stesso nome lungo tutto il flusso («Invita» → «Invita un account» → «Invito inviato»).
- Importi `200.000 €`; date `31 ott 2026`; tempo relativo in parole («tra 5 giorni», «scaduto il 31 lug 2026»).
- Stato vuoto: che cosa manca e un'azione per cominciare. Errore: che cosa è successo e come rimediare, senza scuse.
- Informative, consensi, avvisi legali, note sui pagamenti e disclaimer dell'AI si riportano parola per parola.

## Glossario

| Si chiama | Non si chiama |
|---|---|
| Azienda, titolare, membro | Famiglia, Persone |
| Consulenza, richiesta di consulenza, proposta | Consulto, consulto esperto |
| Compatibilità (controllo immediato) | — |
| AI-check, report AI-check | AI-check di compatibilità, Report di compatibilità |
| Add-on | addon |
| Bandi salvati, call salvate | Salvati, salvate |
| Call di partenariato | — |
| Profilo partner | Visibilità come partner |
| Abbonamento (schede Piano, Add-on, Pagamento e fatturazione, Acquisti) | I tuoi acquisti, Dati di fatturazione come pagine a sé |
| Dati azienda (schede Dati aziendali, Dossier, Bilanci, Profilo partner) | — |
| Preferenze (schede Interessi sui bandi, Avvisi email) | Preferenze bandi |
| Aziende gestite | Gestisci aziende |
| anno, limite, disponibili | ciclo, tetto, pool |

Il titolo di pagina è uguale alla voce di menu; gli URL non sono copy e non si rinominano per questo.
