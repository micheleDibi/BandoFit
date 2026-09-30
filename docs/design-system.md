# Design system del frontend

Riferimento per chi scrive interfaccia. Le regole valgono per ogni pagina e ogni componente; le eccezioni si scrivono qui, non nel codice.

BandoFit aiuta aziende, enti e consulenti a capire in pochi secondi tre cose di un bando: **fa per me? quanto vale? entro quando?** Ogni schermata risponde a queste domande prima di ogni altra e porta a una sola azione.

## Principi

1. **Meno riquadri, più struttura.** Il contenuto sta sul foglio bianco (`sheet`); la navigazione sta sul piano (`desk`). Le sezioni si separano con un titolo e un filetto, non con un riquadro. Un riquadro con bordo (`Card`) solo per oggetti uguali da confrontare; un pannello su `desk` (`Panel`) per un blocco di servizio accanto al contenuto. Mai un riquadro dentro un riquadro.
2. **Un'azione primaria per schermata.** Un solo pulsante pieno; le altre azioni sono secondarie o testuali.
3. **Lo stato in parole.** Uno stato è una parola con un punto (`Status`), mai un badge colorato; una riga ha al massimo uno stato. La compatibilità è un contatore di requisiti (`Fit`), e una compatibilità bassa non è un errore: barre vuote, mai rosso.
4. **Un nome per cosa**, uguale in menu, titolo di pagina, pulsante e messaggio (glossario in fondo).
5. **La scadenza è il segno ricorrente** (`Due`): giorno, mese, tempo relativo in parole; stessa forma in elenco, scheda, calendario e Home.
6. **Niente decorazione**: niente gradienti, niente ombre sulle superfici della pagina, niente icone nei cerchi, niente etichette tutte maiuscole, niente animazioni d'ingresso.

## Token (`frontend/src/index.css`, blocco `@theme`)

### Colore

| Token | Valore | Utility | Uso |
|---|---|---|---|
| `brand-50…900` | scala esistente (`brand-500 #2c56c9`) | `bg-brand-*`, `text-brand-*` | Solo tramite i ruoli qui sotto |
| `ink` | `#212b50` | `text-ink` | Testo principale e titoli (il navy del logo). 13,8:1 su bianco |
| `ink-2` | `#45556c` | `text-ink-2` | Testo secondario: metadati, descrizioni, voci di menu. 7,6:1 |
| `ink-3` | `#566580` | `text-ink-3` | Testo terziario: etichette dei dati, note, aiuto, segnaposto. 5,9:1 |
| `ink-off` | `#90a1b9` | `text-ink-off` | Solo segni senza significato (punto neutro, icone disattivate). Mai per testo |
| `on-accent` | `#ffffff` | `text-on-accent` | Testo su `accent` e su `ink` |
| `sheet` | `#ffffff` | `bg-sheet` | Il foglio: contenuto, campi, menu, finestre |
| `desk` | `#f7f9fc` | `bg-desk` | Il piano: barra laterale, pannelli |
| `sunken` | `#eef2f7` | `bg-sunken` | Fondo incassato: etichette, segmenti, barre vuote, voce di menu al passaggio |
| `line` | `#e2e8f0` | `border-line` | Filetto che separa. Non delimita un controllo |
| `line-control` | `#8290a8` | `border-line-control` | Bordo dei controlli e della testata delle tabelle. 3,2:1 |
| `accent` | `= brand-500` | `bg-accent`, `text-accent` | Azione primaria, scheda attiva, anello del focus |
| `accent-hover` | `= brand-600` | | Primario al passaggio del mouse; testo dei link |
| `accent-soft` | `= brand-100` | | Fondo del pulsante testuale al passaggio; avatar |
| `accent-line` | `= brand-200` | | Filetto dell'avviso informativo (`Alert` info) |
| `fit` | `#1aa38c` | `bg-fit` | Il verde del logo. Solo compatibilità, «Aperto», conferme. 3,2:1: mai per testo |
| `fit-ink` | `#0e7a68` | `text-fit-ink` | Testo di esito positivo e di «Aperto». 5,3:1 |
| `fit-soft` | `#e6f5f2` | `bg-fit-soft` | Fondo dell'avviso di conferma |
| `warning-ink` / `warning-soft` / `warning-line` | `#973c00` / `#fffbeb` / `#f0c36b` | | Attenzione: scadenza entro 30 giorni, «In apertura» |
| `danger` / `danger-soft` / `danger-line` | `#c10007` / `#fef2f2` / `#f1a5a8` | | Errori, scadenza entro 7 giorni, azioni distruttive |
| `veil` | `rgb(33 43 80 / 0.4)` | `bg-veil` | Velo sotto finestre e cassetti |

Il colore non basta mai da solo: ogni stato ha una parola («Aperto», «tra 5 giorni», «2 su 4»). Le classi `slate-*`, `emerald-*`, `amber-*`, `red-*`, `violet-*` non si usano più nelle pagine.

### Tipografia

| Stile | Utility | Font | Taglia | Uso |
|---|---|---|---|---|
| `title-bando` | `text-title-bando` | Sora 600 | 28/36, -0.015em | Solo il titolo del bando nella sua scheda |
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

Sora solo tramite gli stili `title-*`, `figure*` e `due-day` (peso unico 600: si carica solo `@fontsource/sora/600.css`). Inter in tre pesi: 400 testo, 500 etichette e menu, 600 titoli di gruppo, titoli di riga, pulsanti. Niente taglie arbitrarie (`text-[11px]`), niente `uppercase`, niente `tracking-wide`. Cifre sempre tabellari (`tabular-nums`).

### Raggi, ombre, spazi

| Token | Valore | Uso |
|---|---|---|
| `radius-mark` | 4px | Etichette, caselle di spunta |
| `radius-control` | 8px | Pulsanti, campi, filtri, avvisi |
| `radius-panel` | 12px | Pannelli, card, menu, finestre |
| `radius-pill` | 999px | Solo avatar, contatori, filtri attivi, barre |
| `shadow-overlay` | `0 12px 32px -8px rgb(33 43 80 / .28), 0 2px 6px rgb(33 43 80 / .08)` | L'unica ombra: menu, finestre, notifiche a comparsa |

Spaziature dalla scala di Tailwind, con questi ruoli: 1 (4px) fra etichetta e valore; 2 (8px) fra icona e testo e fra pulsanti; 3 (12px) dentro i controlli; 4 (16px) fra gli elementi di una sezione; 6 (24px) fra colonne e sezioni; 8 (32px) sopra la pagina; 10 (40px) ai lati della pagina su desktop; 12 (48px) fra blocchi maggiori.

Larghezze: barra laterale 248px; elenco 1112px; pagina a sezioni con schede 880px; flusso a passi 720px; colonna laterale 320px; testo lungo 680px. Le pagine non scrivono `max-w-*`: usano `Page` con la sua variante.

## Modelli di pagina

| Modello | Componente | Dove |
|---|---|---|
| Elenco | `Page variante="elenco"` | Bandi, Bandi salvati, Partenariati, Notifiche, tabelle admin, Home |
| Dettaglio con colonna laterale | `Page variante="dettaglio"` | Bando, call, consulenza, richiesta |
| Sezioni con schede | `Page variante="sezioni"` | Dati azienda, Abbonamento, Preferenze, Profilo, Account collegati |
| Flusso a passi | `Page variante="flusso"` | Nuova call, checkout |

Ogni pagina comincia con `PageHeader` (ritorno, titolo, una riga di descrizione, azioni a destra). Lo stato delle schede sta nell'URL (`?tab=`, hook `useTab`).

## Componenti (`frontend/src/components/ui/`)

Un solo posto per ogni cosa. Se una pagina ha bisogno di un pattern che non c'è, si aggiunge qui, non si rifà a mano.

| Componente | Sostituisce |
|---|---|
| `Button` (primary, secondary, ghost = testuale, danger; sm, md, lg; loading), `IconButton`, `TextLink` | i 4 stili di «X» e le 3 regole di sottolineatura |
| `Field` (`TextField`, `TextareaField`, `SelectField`), `Select`, `SearchInput`, `Checkbox`, `RadioGroup`, `Switch`, `PasswordField` | input e select scritti a mano, le 4 copie del campo password |
| `Filter`, `Segment`, `Chip`, `Badge` (etichetta neutra) | filtri della lista, chip dei filtri attivi, pill a mano |
| `Tabs` (con `useTab`), `Stepper`, `Accordion` | `Schede` dei partenariati, la tablist a mano, i 18 `<details>` |
| `PageHeader`, `Page`, `SectionHeader`, `BackLink` | le 26 h1 a mano e le larghezze diverse |
| `Status`, `Due`, `Fit`, `Facts`, `DefinitionList`, `Table`, `BandoRow` | badge di stato, MetaTile/StatTile, le 7 tabelle con 3 testate |
| `Alert`, `InlineError`, `EmptyState`, `ErrorState`, `Skeleton`, `Spinner`, `ProgressBar` | i ~90 avvisi e ~75 errori a mano, le barre con `style width` |
| `Popover`, `Menu`, `Dialog`, `ConfirmDialog`, `Drawer`, `Toast` (`useToast`), `Tooltip` | i 5 dropdown, le 16 conferme rifatte, i flash con `setTimeout`, i `title=` |
| `Card`, `Panel`, `Avatar`, `Pagination`, `Combobox`, `TagSelect`, `AuthLayout` | |

Icone: `lucide-react`, 16 o 20px, tratto 1,75, nel colore del testo accanto. Un'icona accompagna una parola; un pulsante di sola icona ha sempre `aria-label`. `Sparkles` solo per l'AI-check. Mai emoji.

## Mai e sempre

| Mai | Invece |
|---|---|
| Più di un pulsante pieno nella stessa schermata | Un primario; gli altri secondari o testuali |
| Badge colorati, o più di uno stato per riga | `Status` in parole e `Fit` per la compatibilità |
| Un riquadro per ogni blocco, riquadri annidati | Sezioni con titolo e filetto; `Panel` solo nella colonna laterale |
| Etichette tutte maiuscole sopra i titoli | Un titolo di gruppo in frase |
| Gradienti, ombre decorative, `hover:-translate-y` | Tinta piatta; l'ombra solo per ciò che sta sopra la pagina |
| Bordo colorato a sinistra di un riquadro | `Alert` con bordo intero, icona e parola |
| Metadati uniti da «·» | Elementi separati con `gap` |
| «→» in coda a link e pulsanti | Il verbo basta; per i link esterni l'icona `ExternalLink` |
| Stato vuoto centrato con icona in un cerchio | `EmptyState`: frase, spiegazione e azione a sinistra |
| Compatibilità bassa in rosso | Barre vuote |
| Testo in `ink-off` o sotto 12px | `ink-3`, taglia `caption` o superiore |
| Colori `slate/emerald/amber/red` nelle pagine | I token con un ruolo |

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
