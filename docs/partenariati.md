# Partenariati — piano (WP0)

> **Stato**: piano approvato il 2026-09-28 (WP0). Le decisioni della §13 sono quelle raccomandate, approvate insieme al piano.
> Metodo: mappa del codice, tre progetti indipendenti (WP1-2, WP3-5, WP6-10) e revisione avversariale su tre fronti (sicurezza/privacy, correttezza/spesa, completezza). Questo documento è la sintesi consolidata e resta la fonte di riferimento del modulo: ogni WP lo aggiorna se l'implementazione se ne discosta.

---

## 0. Contesto e obiettivo

Quattro funzionalità collegate:
1. bilanci aziendali da openapi.it (pluriennali all'import, ufficiali on-demand);
2. profilo partner con opt-in;
3. call di partenariato con matching bidirezionale sui requisiti del bando;
4. bacheca con «Per te», candidature, inviti, chat, validatore del consorzio, bozze AI e consulto con un progettista.

Obiettivo: far «saltare all'occhio» a un'azienda Y le call in cui copre ciò che manca al capofila X, e viceversa. La regola d'oro vale ovunque: **l'AI estrae, redige e spiega; esiti, ammissibilità, punteggi e validità del consorzio li calcolano funzioni Python deterministiche e testate**.

---

## 1. Esito delle verifiche del WP0

### 1.1 openapi.it — verificato sulla spec OAS ufficiale, NON in sandbox

- **La sandbox richiede credenziali separate**: API key di test distinta, account sandbox da attivare («ACTIVATE SANDBOX»), credito virtuale da ricaricare, attivazione in console di Company IT-advanced e di Visure Camerali. Come chiedevi, mi fermo e te lo segnalo: le risposte reali e le fixture sandbox sono il **gate G1** (§11). Tutto il resto viene dalla OAS pubblica e dagli esempi ufficiali.
- **IT-advanced** (`GET company.openapi.com/IT-advanced/{piva}`):
  - sincrono; `data` è un **array** di un solo elemento;
  - `balanceSheets.last` (nullable) e `balanceSheets.all[]` con `year, balanceSheetDate, turnover, netWorth, employees, shareCapital, totalStaffCost, totalAssets, avgGrossSalary`, tutti nullable tranne `year`; storico fino a circa 7 anni; righe segnaposto con `turnover`, `netWorth` e `balanceSheetDate` a null;
  - 0,10 € a chiamata (30 gratis al mese);
  - **Correzione alla spec: `netWorth` è l'UTILE d'esercizio** («annual profit»), non il patrimonio netto. L'ho verificato incrociando gli esempi ufficiali della stessa società: `netWorth` 2021 = 469.366, identico all'utile di IT-full (`annualResult` IIC179), mentre il PN di IT-full è 563.473. Quindi **il PN pluriennale non esiste in IT-advanced**.
- **IT-full** copre **un solo esercizio**:
  - `ecofin` = `{balanceSheetDate, turnoverYear, turnover, shareCapital, netWorth (= PN), enterpriseSize…}`;
  - `operatingResults` = `{ebitda, ebit, cashFlow, *L2Y}`;
  - voci CEE come liste `{code,value}` con prefisso **IIC o IPL**: utile IIC179/IPL179, totale attivo IIC074, valore della produzione IIC130, ricavi IIC124;
  - nessun blocco `balanceSheets`.
- **Bug di produzione confermato** in `openapi_mapping.py`:
  - `ecofin.ebitda` (:584) ed `ecofin.profit` (:585) non esistono, quindi EBITDA e utile del dossier valgono sempre `None`;
  - il path `("balanceSheets","turnover")` (:169) non esiste in nessuno dei due prodotti;
  - `ecofin.turnover/netWorth/shareCapital` invece sono corretti;
  - l'unica fixture reale è un'associazione senza bilanci: il mapping finanziario non è mai stato esercitato su dati reali.
- **Forma giuridica**: `legalForm.legalForm.code` vale SC (capitali), SP (persone) o AL. Attenzione: in `detailedLegalForm` «SP» significa SpA.
- **Bilancio ottico**:
  - `POST visurecamerali.openapi.it/bilancio-ottico` con `{cf_piva_id, anno_chiusura?}`, solo per società di capitali;
  - stati: In ricerca → In erogazione → Dati disponibili (nell'enum OAS c'è il refuso «Dati disponbili») / Visura evasa / Annullata;
  - allegati: ZIP in base64 con PDF, XBRL e verbale;
  - errori: `404/278` bilancio non disponibile, `422/273` allegato non ancora pronto;
  - costo **4,50 € + IVA**, consegna in circa 15 minuti;
  - pre-check quasi gratuito con `GET /impresa/{cf}` (`chiamate_disponibili`);
  - **non documentato** se si paga anche su 278 o su «Annullata».
- **Nel repo**:
  - la visura (stesso host, stesso ZIP) è stata rimossa con il commit `5927066`; codice e fixture si recuperano da `5927066^`;
  - `tests/test_openapi_client.py:116-117` vieta gli scope visurecamerali e va aggiornato di proposito;
  - rischio: il client minta **un solo token con tutti gli scope**, quindi un'API non attivata romperebbe anche l'import IT-full. Si passa a un token per gruppo di scope.
- **Alternativa da conoscere** (la decisione resta il bilancio ottico): DocuEngine «Bilancio Riclassificato» in JSON a 5,00 €, con una search da 0,10 € che elenca gli esercizi disponibili prima di pagare.

### 1.2 Catalogo — verificato sul DB bandi VIVO (anon key, sola lettura)

Il dump locale è del 3 luglio e precede la v1.1: non va usato per queste stime.

- **La fase (b) del contratto v11 è applicata.**
  - `bando_pubblico`: 2162 righe; `stato_effettivo` aperto 1254, chiuso 724, in apertura 184, quindi **1438 non chiusi**.
  - `bando_link`: 5687 righe; `select=*` risponde 42501, come da contratto.
  - `bando_evento`: 2849 righe.
  - `fonte_ufficiale_*`: trovata 614, in verifica 1031.
- Il backend attuale non legge nulla di tutto questo. **Le nuove funzionalità leggono `bando_pubblico` e `bando_link` direttamente, selezionando le colonne per nome.** I percorsi esistenti restano come sono.
- `bando_link.tipo` reale: `pagina_bando` 3627, `allegato` 2059, `candidatura` 1, **nessun `atto`**. `content_type` è misto (`application/pdf` o l'estensione nuda `pdf`) e va normalizzato; l'arbitro è il controllo dei magic bytes `%PDF-`.
- **PDF ufficiali**: 1426 bandi pubblicati su 2162 ne hanno almeno uno. **Tra i non chiusi sono 873 su 1438 (61%)**, di solito un PDF per bando.
- **Cosa intendevo con «estrazione da catalogo»**: il modello legge solo ciò che il DB bandi contiene già, cioè il `contenuto` (la sintesi editoriale a sezioni di edunews24) e i metadati (date, enti, beneficiari, regioni). Non legge i PDF dell'ente.
  - Nel `contenuto` le formule forti compaiono in 99 bandi (76 non chiusi); i **vincoli** (quote, «un solo partenariato», «non collegate») quasi mai.
  - Quindi le regole vere si prendono dai PDF (61% dei non chiusi) e, dove non ci sono, dalla sola sintesi, con esito spesso «non determinabile».
- La lookup `beneficiari` non ha ATS, reti o consorzi: la tassonomia delle forme nasce nel DB primario.
- **Campione di valutazione WP3** (non chiusi, con PDF, stratificato; nel repo pubblico vanno solo id ed etichette):
  - positivi: 17693, 18117 (UE); 18455, 18351, 18362, 18460, 18559, 171905 (nazionali); 17580, 2242, 17631, 18145, 18177, 18278 (regionali); 18495 (internazionale);
  - negativi: 2231, 18071, 18346, 109259, 547011.
  - Nel WP3 scelgo altri 5 positivi regionali con la stessa regola. Le etichette «vere» le preparo io leggendo i PDF pubblici e le rivedi tu.

### 1.3 Già deciso con te

| Tema | Decisione |
|---|---|
| Testo dei PDF | Nuova dipendenza **`pypdf>=6.19`** (BSD-3, pure-Python) in `pyproject.toml`; XBRL con la sola stdlib |
| Valutazione AI reale | **Rimandata** finché non ricarichi il credito. Script pronto con tetto di spesa; nel frattempo solo misure offline |
| Chiamate reali in sviluppo | Nessuna: Anthropic sempre mockato, openapi solo in sandbox (G1) |

---

## 2. Decisioni di architettura (consolidate)

### 2.1 Trasversali
- **T1. Flag.**
  - `Settings.partenariati_attivo=False` (env `PARTENARIATI_ATTIVO`).
  - Dependency `require_partenariati_attivo` che risponde **404** (pattern nuovo): a livello di router per i router nuovi, **a livello di rotta** per le aggiunte a router esistenti (`/bandi`, `/me`, `/progettista`), così le rotte esistenti non cambiano.
  - Verso il frontend: `MeOut.funzioni = {partenariati: bool}`. Con il flag spento, menu e card (Partenariato in BandoDetail, profilo partner in Azienda, passo di consenso nell'import) non si rendono.
  - WP1 e WP2 vanno senza flag.
  - In produzione il flag si accende solo con una riga in `docker-compose.yml`, che aggiungi tu.
- **T2. Nomi unici.**
  - Scheduler `services/partenariati_scheduler.py` + tabella `partenariati_runs` (claim per giorno) + `partenariati_scheduler_attivo`/`partenariati_ora_esecuzione="05:30"`. Nasce in WP3; ogni WP aggiunge passi isolati.
  - Errori in `services/partenariato_errori.py`: `RPC_ERRORS: dict[detail, (status, code, messaggio)]` → `AppError`. I code sono specifici, perché il frontend ci ramifica sopra.
  - Privacy in `app/core/privacy.py` (`mask_piva`, `mask_cf`, `mask_email`, `hmac_dominio`), creato in WP1.
  - Query key del frontend con radice unica `["partenariati", …]`.
- **T3. Autorizzazione cross-tenant unica** (`services/partenariato_accesso.py`, dal WP5).
  - Oggi ogni scoping è interno all'owner; qui nasce il primo accesso tra owner diversi.
  - Ruoli sulla risorsa: `creatore | controparte | candidato | pubblico | progettista | admin`, ciascuno con una **proiezione tipizzata a whitelist**.
  - Fuori autorizzazione si risponde 404.
  - Ogni RPC su una call filtra per `id + company_profile_id = azienda attiva + family_parent_id = owner`: niente scritture su un'altra azienda dello stesso Advisor.
  - Verso i terzi escono solo handle opachi (`codice_pubblico` del profilo, pseudonimo per call `HMAC(call_id|codice_pubblico)`), **mai `company_profile_id`**.
- **T4. Chi agisce.**
  - Ogni scrittura (profilo, consenso, call, candidature, inviti, chat, consorzio, spese a pagamento) richiede `active.editable`, cioè il titolare.
  - I membri con visibilità leggono.
  - Per l'Advisor vale l'azienda attiva, una per volta.
- **T5. Identità verificata sul registro (rilievo bloccante della revisione).**
  - Ciò che i terzi vedono di un'azienda (nome, dimensione, fasce) deve venire dal Registro Imprese, non da campi che l'utente modifica.
  - Precondizione **fail-closed** nelle RPC di consenso, pubblicazione, candidatura e accettazione: deve esistere `company_data` con `piva_fetched = company_profiles.partita_iva`, impresa attiva e, in produzione, non sandbox. Altrimenti `409 identita_non_verificata`.
  - Verso i terzi il nome è `company_data.denominazione`; dimensione e fasce vengono solo da `derived` e `company_financials`.
  - Se cambiano P.IVA o ragione sociale con l'opt-in attivo, il consenso viene revocato con origine `sistema`.
  - Una verifica più forte è la domanda Q9.
- **T6. Spesa LLM con un tetto fail-closed unico.**
  - Registro `partenariati_ai_esecuzioni` + prenotazione atomica (advisory lock, costo riservato al caso peggiore, budget giornaliero per gruppo) per **tutti** i servizi LLM del modulo: estrazione WP3, bozza profilo WP4, posizioni e testi WP5, bozze WP10.
  - `rate_limit_service` (fail-open) resta solo come anti-abuso, mai come tetto di spesa.
  - Il client Anthropic allega l'`usage` anche alle eccezioni (output troncato, JSON non valido): una chiamata pagata non viene mai registrata a costo 0.
  - Gli schemi passati al modello contengono solo tipi ed enum, senza vincoli numerici, perché un valore fuori range non deve far fallire una chiamata già pagata. I range si validano nel post-processing, che declassa la voce a `da_verificare`.
  - Tabella prezzi unica `services/ai_prezzi.py` (sonnet-5 a $2/$10, da riconfermare). I nuovi moduli **non scrivono mai in `ai_checks`**, che vale come quota.
- **T7. Niente chiamate a pagamento in richieste HTTP lunghe.** Le generazioni AI nuove (WP4, WP5, WP10) sono job asincroni: 202 + poll-on-read + failsafe. Il proxy (nginx a 60 s documentato, Cloudflare a 100 s) taglierebbe le richieste mentre il server continua a pagare. Per l'anteprima import vedi Q23.
- **T8. Minimizzazione.**
  - Nei nuovi percorsi niente P.IVA, CF o email in chiaro nei log.
  - Nessun payload grezzo verso il client.
  - Verso i terzi mai valori esatti di bilancio, solo fasce ed esiti (CGC 7.3).

### 2.2 Bilanci (WP1–WP2)
- **B1. Tre tabelle, fusione per campo.**
  - `company_financials_fonti` (una riga per azienda × anno × fonte), `company_financials` (riga fusa, quella che legge l'app), `company_financials_stato` (1:1 con l'azienda: esito IT-advanced, `advanced_raw`, versione del mapping).
  - Per ogni campo vince il primo valore non nullo nell'ordine **xbrl > it_full > it_advanced**, ricalcolato da tutte le fonti a ogni scrittura, quindi il risultato non dipende dall'ordine di arrivo.
  - L'arbitro è la RPC `fn_bilanci_registra_fonte`, sotto `FOR NO KEY UPDATE` della `company_profiles`. Il gemello Python `unisci_fonti` condivide con l'SQL una tabella di casi di test.
- **B2. Mapping corretto.**
  - IT-advanced: `netWorth` → `risultato_esercizio`.
  - IT-full: `ecofin.netWorth` → `patrimonio_netto`; EBITDA ed EBIT da `operatingResults`; utile da IIC179/IPL179.
  - L'anno è quello di `balanceSheetDate`. Se `turnoverYear` è diverso, il fatturato di `ecofin` non va nella stessa riga e scatta un avviso.
  - I codici CEE non ancora verificati (debiti, liquidità, oneri, personale) restano null fino a G1.
  - Righe segnaposto e valori di segno impossibile vengono scartati.
- **B3. IT-advanced nell'anteprima, sotto lo stesso lock, in sequenza dopo IT-full.** **Non si paga** in questi casi:
  - IT-full fallito o P.IVA non corrispondente;
  - draft riusato;
  - società di persone (SP);
  - P.IVA ≠ `company_profiles.partita_iva` già impostata;
  - meno di 25 s rimasti (budget 250 s).

  Nuova catena dei tempi: server ≤ 277 s < frontend 290 s < TTL del lock 330 s. IT-advanced **non blocca** l'import: in caso di problemi lo stato bilanci diventa `non_disponibili` con motivo, e c'è la CTA «Recupera i bilanci», un endpoint dedicato con validazione locale, cooldown persistente, lock (TTL 120 s) e registro consumi.
- **B4. Guardia sull'azienda nel draft** (bug esistente reso grave dai bilanci). Nuova colonna `company_import_drafts.company_profile_id`, verificata sia nel riuso sia nella conferma (409 `draft_mismatch`).
- **B5. Rimappatura pigra gratuita.** Alla lettura, se `mapping_versione` è vecchia, si rimappa da `company_data.raw`/`advanced_raw`/XBRL conservato. Per `it_full` **non si usa mai «sostituisci»**, così gli anni di PN accumulati dagli import precedenti non si perdono. La versione del mapping si aggiorna solo dopo che tutte le registrazioni sono riuscite. È anche il backfill gratuito per le aziende già importate.
- **B6. `services/bilanci_indicatori.py` (puro, `Decimal(str(x))`).**
  - Indicatori: crescita, fatturato medio a 2 e 3 anni (anni consecutivi), PN/attivo, MOL/fatturato, oneri/fatturato. La copertura delle immobilizzazioni non è calcolabile in v1.
  - Fasce per i terzi: fatturato, PN, dipendenti, trend.
  - `valuta_regola_finanziaria(regola, bilanci, costo_quota: Intervallo|None, *, vista)` → `soddisfatto|non_soddisfatto|dato_mancante` più il motivo.
  - **Vista «terzi»: si valuta sull'intervallo della fascia pubblica del candidato, mai sui valori esatti** (così interrogazioni ripetute non rivelano più della fascia, Q11). Vista «proprio»: valori esatti.
- **B7. Contratto unico `app/schemas/regole_finanziarie.py`** (creato in WP1, importato da WP3/WP6/WP8):
  - `RegolaFinanziaria{id, descrizione, ambito: ciascun_partner|capofila|media_pesata_quote|partenariato_totale, numeratore, denominatore?, operatore: lt|le|gt|ge, soglia?|soglia_variabile+soglia_coefficiente, unita}`;
  - variabili da un Literal chiuso allineato alle colonne (`fatturato`, `fatturato_medio_2/3`, `patrimonio_netto`, `risultato_esercizio`, `mol`, `oneri_finanziari`, `bilanci_approvati_n`, `costo_quota`, …);
  - valutabili subito con i dati inclusi nell'import: «costo quota / fatturato medio 2 ≤ 0,6», «≥ 2 bilanci», «PN > costo/2» (solo sull'ultimo esercizio). «Oneri/fatturato» e il PN degli anni passati richiedono l'XBRL.
- **B8. AI-check.**
  - `EXTRACT_PROMPT_VERSION` resta 1 (chiave della cache: nessuna ri-spesa su `bando_requirements`); nuova `MATCH_PROMPT_VERSION = 2`.
  - Il pack riceve «## Bilanci per esercizio» con nomi stabili (`bilanci.2024.fatturato`) e gli **indicatori già calcolati**, così il modello non fa aritmetica.
  - La filosofia dell'AI-check resta quella di oggi: verdetti per voce, esito deterministico. Gli esiti finanziari deterministici sono del modulo partenariati.
  - `invalidate_company_facets` dopo import, recupero e XBRL.
- **B9. Token openapi per gruppo di scope.**
  - `core`: i 4 scope di oggi, invariati.
  - `advanced`: IT-advanced.
  - `visure`: bilancio-ottico e impresa.

  Se un'API non è attivata in console fallisce solo il suo prodotto. Nuova regola di spesa: dopo un `ConnectError` il secondo tentativo classifica `ConnectError`/`PoolTimeout` come «non inviata»; ogni altro errore resta **esito ignoto**, e su una POST addebitabile non dà mai rimborso.
- **B10. Bilancio ufficiale (WP2).**
  - Addon consumabile `bilancio-ufficiale` con la nuova colonna `addons.sempre_a_pagamento`. Un CHECK impedisce che sia attivo se gratuito: nessun percorso rende gratis una spesa del provider.
  - Consumo atomico nella RPC di creazione, sotto il lock dell'owner.
  - Tetti fail-closed in RPC: 30 richieste al giorno sulla piattaforma e 5 per owner.
  - Pre-check gratuito sulla forma giuridica (IT-full, oppure `/impresa` per AL o forma ignota).
  - POST inline, poi un follower in-process (60 s per 20 minuti, poi ogni 5 minuti) più poll-on-read con claim a DB; transizioni condizionate, con un solo vincitore.
  - Mai retry su una POST.
  - Gli errori di parsing chiudono come `completata` con `xbrl_esito`, conservando il PDF; si ritentano solo gli errori infrastrutturali.
  - Rimborso: Q5. Destino del PDF: Q4.
- **B11. XBRL con la sola stdlib.**
  - `XMLParser` con un target che solleva su `doctype()`, feed a blocchi da 64 KB con deadline di 5 s, tetti di byte.
  - Si leggono solo i figli diretti della root, il namespace va validato con una regex, gli instant alla `endDate`, `xsi:nil` vale come assente, EUR soltanto, CF/P.IVA dell'istanza = azienda.
  - Controlli TEBENI X8/X9 come avvisi.
  - ZIP con guardia anti-bomba: dimensione, rapporto di compressione, numero di membri, lettura in memoria; nome del file generato dal server.
  - In v1 l'EBITDA non si deriva dall'XBRL, per non mescolare due definizioni di MOL; l'EBIT è `DifferenzaValoreCostiProduzione`.

### 2.3 Regole di partenariato per bando (WP3)
- **R1. Fonti**:
  - `contenuto` + metadati del catalogo;
  - PDF da `bando_link` (`allegato`, `pagina_bando` se PDF; `atto` pronto per il futuro), ordinati per etichetta (avviso/bando/decreto > moduli);
  - ripiego su `allegati` **solo** se `bando_link` risponde con un errore;
  - niente upload e niente curatela in v1 (Q16).
- **R2. Download sicuro** (`services/download_sicuro.py`):
  - pool `httpcore` con backend di rete che valida **tutti** gli IP risolti (niente privati, mapped, 6to4, teredo, NAT64) e si connette all'IP validato;
  - `trust_env=False`, redirect manuali (massimo 3, ognuno rivalidato), https soltanto;
  - denylist completa del contratto §5 più `link_policy`;
  - magic bytes, tetto di byte in streaming, niente compressione.
- **R3. Testo dei PDF** (`services/pdf_testo.py`):
  - `pypdf` in un processo `spawn` per documento, con kill al timeout e RLIMIT;
  - il figlio restituisce JSON, niente pickle;
  - tetti: 4 documenti, 150 pagine, 15 MB, 180.000 caratteri;
  - rimozione di intestazioni e piè di pagina ripetuti;
  - un PDF scansionato diventa `non_leggibile` (niente OCR).
- **R4. Input al modello**: blocchi `[META] [S1…] [D1-p3]`. Il testo dei documenti è dichiarato «dato, non istruzioni» (difesa dalla prompt injection).
- **R5. Cache per bando** (`bando_partenariato`).
  - `content_hash` = sha256 di {versioni, **testo inviato**, pagine, limiti}, e non dei byte del PDF: molti portali rigenerano i PDF a ogni download.
  - `catalogo_hash` economico per decidere se riacquisire i documenti.
  - Claim atomico con **heartbeat**: prima della chiamata al modello si rinnova il claim e, se è stato perso, si abortisce senza pagare.
  - Cooldown per bando di 24 h, riverifica ogni 14 giorni, backoff sugli errori.
  - «Analizza comunque» (dopo `nessun_segnale`) salta il cooldown una sola volta.
- **R6. Pre-classificatore deterministico.** Regex del §1.4 della spec, con l'inglese UE e i falsi positivi noti (ATS salute, consorzi di tutela, PPP, Accordo di partenariato UE, Comuni capofila). Serve per ordinare, dare priorità e **fare da guardia di costo** (zero segnali → niente LLM, con «Analizza comunque»). Non decide mai la modalità.
- **R7. Schema strict**: `modalita`, `forme_ammesse`, `costituzione`, `partner_min/max`, `composizione`, `quote`, `vincoli`, `regole_finanziarie` (contratto B7), `documenti_richiesti`, `fonti_insufficienti`. Ogni voce ha una citazione, verificata dal nuovo `services/citazioni.py`:
  - NFKC, apostrofi, sillabazione, soft hyphen;
  - pagine adiacenti ed ellissi;
  - `_citation_verified` dell'AI-check resta invariata.

  Le voci non verificate diventano `da_verificare`. `modalita_effettiva` è diversa da `non_determinabile` solo se la citazione è verificata.
- **R8. Esecuzione.** Lazy all'apertura della sezione e alla creazione di una call, best-effort. Costo della piattaforma con budget fail-closed (T6) e limiti per utente. Batch notturno spento (`PARTENARIATO_BATCH_BUDGET_CENTS_GIORNO=0`).
- **R9. Filtro «Ammette partenariato»** (`GET /bandi?partenariato=`): gli id vengono dalla RPC del primario (un solo array, con tetto di 1000) e diventano `id=in.(…)` sui due segmenti. La UI dichiara che il filtro vale solo sui bandi già analizzati.

### 2.4 Profilo partner (WP4)
- **P1.** Profilo 1:1 con l'azienda.
  - `visibile_come_partner` (default false) e `anonimo` cambiano **solo** tramite `fn_partner_consenso`: registro append-only `partner_consents` (senza FK) + audit, nella stessa transazione.
  - Un trigger con una GUC di sessione blocca ogni altra scrittura su quelle colonne.
  - L'origine accettata dal client è solo `import_piva | pagina_azienda | wizard_call`.
- **P2. Vocabolario controllato versionato in codice** (`services/partenariato_vocabolario.py`, v1 in appendice A): tipi soggetto mappati sui `beneficiari`, 42 competenze in 6 aree, 7 forme con checklist e responsabilità, ruoli. È l'unica fonte per lo schema LLM, il profilo, le posizioni e il matching; un test ne verifica l'uguaglianza con i `Literal`.
- **P3. Tipi soggetto.** Quelli dedotti dal registro (dimensione, startup/PMI innovativa, cooperativa) prevalgono. Gli altri (organismo di ricerca, università, ente pubblico…) sono «dichiarati» e marcati come tali ovunque.
- **P4. Profilo pubblico a whitelist**:
  - regioni, sezione ATECO, classe dimensionale, fasce, competenze, esperienze, completezza;
  - handle `codice_pubblico`;
  - **controllo anti-contatti su tutti i testi liberi**, anche nei profili nominativi;
  - per i profili anonimi le informazioni si riducono: Q12.
- **P5. Referente.** Per default è il titolare. Un altro utente può diventarlo solo con una propria accettazione registrata e revocabile (Q13).
- **P6. Revoca immediata.**
  - Il matching ricontrolla live opt-in, sospensione e azienda viva sulla pagina restituita.
  - Da WP7 la revoca chiude inviti e candidature pendenti.
  - Soft delete o archiviazione dell'azienda revocano il consenso (origine `sistema`).
- **P7. «Genera profilo competenze».** Job asincrono. L'input è minimizzato: ATECO, flag del registro, regione, niente persone né contatti. L'esito è una **bozza** che non diventa visibile senza conferma.

### 2.5 Call (WP5)
- **C1. Requisiti e posizioni tipizzati**, necessari per un matching deterministico.
  - Ogni requisito ha un `criterio` di tipo `CriterioPartner` (unione discriminata: `tipo_soggetto | tag | regione | paese | ateco | settore | dimensione | certificazione | esperienza | regola_finanziaria | manuale`) e un `ambito`: `consorzio` (basta un membro, cioè i gap) oppure `ogni_membro` (filtro rigido).
  - `valuta_criterio()` è una funzione pura unica, usata da gap analysis, matching, candidature e validatore.
- **C2. Wizard in 7 passi**:
  1. bando (non chiuso, sospeso o revocato secondo `bando_pubblico.stato_effettivo`; se l'estrazione dice `non_ammesso`, blocco con override motivato);
  2. **regole del bando confermate o corrette dal creatore**, salvate come snapshot `partner_calls.regole_partenariato` (solo voci verificate, marcate confermata/modificata/aggiunta) più il flag `esclusivita`. **Matching e validatore leggono solo questo snapshot**, mai l'estrazione grezza;
  3. gap analysis: ultimo AI-check `ready` (nuovo helper), altrimenti la proposta di lanciarne uno che consuma la quota, detto in UI; pre-check; regole finanziarie sulla quota;
  4. posizioni proposte dall'AI e modificabili;
  5. testi scritti dall'AI, con anonimizzazione deterministica;
  6. anteprima «come ti vedono»;
  7. pubblicazione.
- **C3. Proiezione pubblica.**
  - Call nominativa: nome dal registro + testi pubblici + requisiti cercati + posizioni.
  - **Call anonima**: solo regione, sezione ATECO, classe dimensionale, requisiti cercati e posizioni. Niente fasce e niente coperture del creatore.
  - `copertura_nota` solo da template, mai da testo LLM.
- **C4. Budget.** Una fascia pubblica (8 fasce, più fini di quelle proposte) più il budget esatto **riservato** e facoltativo (`budget_progetto_eur`). Il budget esatto lo vede solo il creatore; nella vista «proprio» del creatore alimenta le regole finanziarie.
- **C5. Limiti di piano.**
  - Colonne `partner_calls_attive_max` e `partner_candidature_mese`: NULL = illimitato, 0 = esclusa. La semantica è **opposta** a `alert_ritardo_giorni` ed è documentata in AdminPiani.
  - Il default della colonna è 0.
  - Enforcement in RPC sotto lock dell'owner, sul modello di `fn_create_company`; funzione dedicata `fn_partenariati_limiti` (jsonb). `fn_entitlement_detail` non si tocca.
  - Esposti come chiave top-level `partenariati` in `/me/entitlements`.
- **C6. Ciclo di vita.**
  - Una call non chiusa per azienda × bando; massimo 5 bozze per azienda.
  - Dopo la pubblicazione le modifiche sono ammesse su una whitelist, con versione e snapshot.
  - Chiusura automatica nello scheduler, più un controllo in lettura: scadenza della call, bando chiuso o sospeso → `scaduta`; revocato o assente da 7 giorni → `chiusa_annullata`; azienda non viva → `chiusa_annullata`. A ogni chiusura automatica parte una notifica al creatore e al titolare (`partenariato.call_chiusa`).
  - Da WP7 non si può rimuovere una posizione che ha candidature attive (`fn_partner_call_sostituisci_posizioni` ridefinita con la stessa firma).
  - Esclusività controllata sia alla pubblicazione sia all'accettazione.
- **C7. Moderazione di base.**
  - `link_policy` e controllo anti-contatti sui testi pubblici.
  - Tabella **definitiva** `partner_segnalazioni` già in WP5: `buona_fede`, `contenuto_snapshot`, `oggetto_id text`, stati `ricevuta → in_esame → decisa → ricorso_presentato → ricorso_deciso`.
  - Conferma di ricezione al segnalante.

### 2.6 Matching (WP6)
- **M1. Filtri rigidi**, che escludono solo su esito certo:
  1. opt-in, sospensione, azienda viva, impresa non cessata;
  2. stesso owner;
  3. collegamenti non calcolati o **collegata**;
  4. categoria di bando esclusa, forma non accettata;
  5. nessuna posizione compatibile;
  6. territorio, paese o dimensione (`ogni_membro`, regola «tutte le sedi»);
  7. regola finanziaria `non_soddisfatto` sulla fascia (B6);
  8. esclusività;
  9. call `solo_invitati` senza invito.

  `dato_mancante` penalizza e viene mostrato.
- **M2. Collegamenti societari** (`company_collegamenti`).
  - Chiavi **HMAC** (non sha256 semplice, che si inverte per enumerazione) su P.IVA/CF dell'azienda, soci, partecipate, controllate, esponenti e gruppo.
  - Calcolate **solo** per le aziende con opt-in o con una call, e solo con il flag attivo; cancellate alla revoca.
  - Soglie in Q17. Il dettaglio mostrato è generico: «possibile collegamento societario: verifica con visura».
- **M3. Punteggio.**
  - Formula: `100·(0,50·copertura_gap + 0,20·affinità + 0,10·complementarità + 0,10·completezza + 0,10·rotazione) − 5 per ogni dato mancante (massimo 20)`.
  - Ordinamento per gap coperti, poi punteggio, poi tie-break `sha256(call:azienda:settimana ISO)` (rotazione settimanale deterministica); al massimo 2 aziende dello stesso owner per pagina.
  - I pesi sono `Settings` con default documentati.
  - **Nessun LLM, nemmeno nelle spiegazioni v1**: template deterministici («Copri «A» e «C», che mancano al capofila.»). Niente pgvector.
- **M4. Indice in-process.** Carico bulk paginato a keyset, senza `raw` e senza CF in chiaro, con TTL di 60 s e invalidazione dai nostri servizi. **Ricontrollo live** di opt-in, sospensione, `accetta_inviti` e azienda viva sulla pagina restituita. Oggi c'è un solo processo uvicorn: niente riga di versione condivisa, che creerebbe hot row e deadlock.
- **M5. Notifiche proattive.**
  - Alla pubblicazione: fan-out verso le top-K (K=20) con copertura ≥ 1 e punteggio ≥ 50, tetto di 3 a settimana per azienda (RPC con advisory lock), dedup.
  - Claim `fanout_claim_at` + `fanout_completato_at`, con recupero nello scheduler.
  - In-app sempre; email **solo nel digest settimanale dedicato**, con opt-out e token propri, `filtra_recapitabili` e solo per chi ha l'opt-in (Q6).
  - Deep link `?azienda=<id>` + hook `useAziendaDaLink` sulle pagine nuove e su `/app/azienda`. La campanella resta com'è (bug esistente, §15).
- **M6. Bacheca.**
  - `GET /partenariati/call` (tutte, mie, salvate), con filtri nei searchParams, ordinamento per affinità, recenti o scadenza, e contatori per call (candidature ricevute, posti coperti);
  - «Salva» vale come «segui»: notifica su modifica o chiusura;
  - «Per te» visibile anche senza opt-in (solo scoperta, con CTA per attivarlo).

### 2.7 Candidature, inviti e chat (WP7)
- **K1. Una sola tabella `partner_candidature(tipo: candidatura|invito)`.**
  - Stati: `inviata → accettata | rifiutata | ritirata | scaduta`. Indice unico sugli attivi per `(call, azienda)`.
  - Transizioni solo via RPC, con **ordine di lock globale owner → call → candidatura**, testato anche contro la chiusura di una call concorrente.
  - Candidarsi richiede l'opt-in e un piano che includa `partner_candidature_mese` (conteggio a righe, mese solare Europe/Rome, pool per owner).
  - L'invito si riceve anche con il Gratuito e rispetta `accetta_inviti`.
- **K2. Accettazione.** Nella stessa transazione:
  - si crea la conversazione;
  - si rivelano l'identità (se la call o il profilo sono anonimi), i dettagli riservati e i contatti (Q13);
  - si scrive l'audit **dentro la RPC**.
- **K3. Messaggi.**
  - Testo ≤ 5000 caratteri, immutabili (tranne l'oscuramento di moderazione), idempotenti via `client_msg_id`.
  - Letti e non letti **per utente**; polling ogni 10 s solo con la tab visibile.
  - Email raggruppata senza scheduler, con un claim a UPDATE condizionato: una per «raffica».
  - Banner antitrust fisso e «Segnala messaggio».
- **K4. Chiusura.** La chiude X; lo storico resta in sola lettura. Cancellazioni: Q21.

### 2.8 Consorzio e validatore (WP8)
- **V1. Membri** in `partner_call_membri`: accettati e creatore, con posizione, ruolo (anche `affiliated_entity` e `associated_partner`), quota % e stato `proposto | confermato | uscito`. Membri esterni: Q20.
- **V2. `valida_consorzio()` puro.** Checklist verde/rossa/grigia, ogni voce con la citazione della regola:
  - numero min/max (esclusi affiliati e associati), composizione, somma quote = 100 (tolleranza 0,01), quote per partner, per categoria e del capofila;
  - indipendenza tra **tutte** le coppie (certo → rosso, possibile → grigio);
  - paesi distinti;
  - regole finanziarie per membro sulla sua quota: fascia per gli altri, esatto per sé;
  - media pesata, se il bando la usa (all'esterno solo l'esito);
  - esclusività.

  «Il bando non lo indica» → grigio.
- **V3.** Matrice di copertura requisiti × membri con la stessa `valuta_criterio`. Checklist documentale per forma (appendice A) con stato per documento.
- **V4.** Le bozze AI passano al **WP10** (te lo dico, come chiedevi): WP8 resta interamente deterministico.

### 2.9 Progettisti, moderazione, admin, metriche (WP9) e bozze (WP10)
- **W1. Consulto dalla call.**
  - Nuova colonna `consultation_requests.partner_call_id`; l'indice `one_open` si divide in due.
  - `fn_create_consultation_request` viene ridefinita con la **stessa firma** e verifica che la call appartenga all'azienda richiedente.
  - L'AI-check diventa facoltativo.
  - Il progettista vede la proiezione dedicata `CallVistaProgettista`: niente contatti e niente messaggi, dei partner solo esiti e fasce.
  - Audit **fail-closed** (senza audit non si servono dati) e letture sempre per id dell'azienda della richiesta (Q23).
- **W2. Moderazione DSA.**
  - Decisione motivata in RPC con effetto atomico: sospensione della call o del profilo, oscuramento del messaggio.
  - Statement of reasons con template (fondamento, uso di mezzi automatizzati, vie di ricorso).
  - Un ricorso entro 6 mesi.
  - Il contesto per l'admin è una finestra di ±10 messaggi; l'intera conversazione solo con motivazione registrata in audit.
  - Termini e testi al legale; un canale per i non utenti (azione manuale).
- **W3. Admin e metriche.**
  - Elenco delle call, sospensione e ripristino.
  - Costi del modulo da `api_usage_events`, **raggruppati per provider con la valuta** (EUR per openapi, USD per Anthropic).
  - Metriche via RPC: call pubblicate, tasso di candidature e accettazioni, ore mediane alla prima candidatura, copertura media dei gap, consorzi validati in verde.
- **W4. WP10 — bozze AI** (lettera d'intenti, NDA, term sheet).
  - Job asincrono.
  - Input a whitelist: denominazioni già rivelate, ruoli, quote, forma, bando; mai bilanci o contatti di altri.
  - Disclaimer «la bozza non costituisce consulenza legale».
  - PDF via `pdf_service`.
  - Limite `partner_bozze_mese` (**default 0** + seed per slug); gli errori pagati contano.

---

## 3. Modello dati per migration

Regole comuni a tutte:
- RLS senza policy + `revoke all` su ogni tabella;
- ogni funzione `security definer set search_path=public` + `revoke execute from public, anon, authenticated`, **compresi seed, backfill e funzioni interne**. Ogni `test_migration_NNNN.py` ha un test generico: nessuna `fn_%` della migration è eseguibile da anon o authenticated;
- cambio di firma solo con `DROP` esplicito;
- seed e backfill come funzioni richiamabili, con `DO` di verifica a WARNING (il DB del harness è vuoto).

| # | File | WP | Contenuto essenziale | Il test DB verifica |
|---|---|---|---|---|
| 0032 | `bilanci_strutturati` | 1 | `company_import_drafts` + `advanced_raw/esito/motivo/tentato_at` + **`company_profile_id`**; `company_financials_stato` (1:1, cascade); `company_financials_fonti` (PK azienda+anno+fonte, `valori` jsonb con stringhe decimali, ruolo corrente/comparativo); `company_financials` (PK azienda+anno, 15 colonne `numeric`, `tipo_bilancio`, `fonte_per_campo`, CHECK anti-segnaposto); RPC `fn_bilanci_registra_fonte(company, fonte, righe, riferimento, sostituisci)` + `fn_bilanci_ricalcola_anno` | CHECK, cascade, RLS/privilegi, tutti i casi di `precedenza_casi.json` (gli stessi del test Python), detail `fonte_non_valida`/`righe_non_valide`/`azienda_non_trovata`, concorrenza con due connessioni |
| 0033 | `bilancio_ufficiale` | 2 | `addons.sempre_a_pagamento` + CHECK di coerenza; seed `bilancio-ufficiale` **inattivo** (prezzo lo metti tu); `company_bilancio_richieste` (stati `in_invio, in_lavorazione, esito_ignoto, completata, non_disponibile, annullata, errore`; indice «una aperta per azienda»; `provider_request_id` unico; `ultimo_poll_at`); `company_bilancio_documenti` (secondo Q4); commento su `addon_ledger.request_id`; RPC `fn_bilancio_richiesta_crea` (consumo **sempre**, tetti in RPC) e `fn_bilancio_richiesta_chiudi` (chiusura condizionata + rimborso `refund` una sola volta, secondo Q5) | Attivazione gratis → 23514; seed idempotente; consumo atomico / saldo 0 senza riga; seconda richiesta aperta; tetti; rimborso una volta; azienda di un altro owner |
| 0034 | `partenariato_regole` | 3 | `bando_partenariato` (una riga per bando, `regole` esposte ed `extraction` interna, hash, claim/heartbeat, backoff); `partenariati_ai_esecuzioni` (registro unico: servizio, origine, giorno Europe/Rome, riserva, costo, token); `partenariati_runs`; RPC `fn_partenariati_ai_prenota` (budget per gruppo + limiti per utente/owner, fail-closed), `fn_partenariato_prenota` / `_concludi` / `_rinnova` / `_chiudi_stale`, `fn_partenariato_bando_ids` | Prenota / in corso / claim scaduto; heartbeat perso → concludi false; limiti; budget; confine del giorno su Roma; backoff; «forza» una volta; concorrenza |
| 0035 | `profili_partner` | 4 | `company_partner_profiles` (default non visibile e anonimo, `codice_pubblico`, vocabolario, `accetta_inviti`, `referente_user_id`, **`sospeso_at/motivo/da`**, `bozza_ai`); `partner_consents` append-only (azioni concesso, revocato, anonimato, referente_concesso, referente_revocato); trigger GUC; `fn_partner_consenso` | Default; CHECK visibile ⇒ consenso; registro immutabile; anonimato solo via RPC; update diretto bloccato; hard delete: il profilo sparisce, i consensi restano |
| 0036 | `piani_partenariato` | 5 | `subscription_plans.partner_calls_attive_max`, `partner_candidature_mese` (default 0, NULL = illimitato); seed per slug (Q1); `fn_partenariati_limiti(owner) → jsonb`, `fn_partenariati_snapshot(owner) → jsonb` | Valori per slug, default 0, NULL ammesso, utente senza abbonamento |
| 0037 | `call_partenariato` | 5 | `partner_calls` (snapshot bando con programma e tipologia, `regole_partenariato`, `esclusivita`, `anonima`, testi pubblici e riservati, `budget_fascia` (8 fasce) + `budget_progetto_eur numeric(14,2)` riservato, visibilità, stati, colonne di sospensione, versione; CHECK `pubblicata_at is null or completa`); `partner_call_requisiti` (`criterio` jsonb, `ambito`, `etichetta`, copertura del creatore, `cercato`, citazione); `partner_call_posizioni` (array tipizzati, `territorio_modalita`, quota); `partner_call_versioni`; `partner_segnalazioni` (forma definitiva); RPC con `p_owner` + **`p_company`**: `crea_bozza`, `aggiorna`, `conferma_regole`, `sostituisci_requisiti`, `sostituisci_posizioni`, `pubblica` (limiti, bando aperto, esclusività, identità verificata), `chiudi`, `chiudi_auto` | Unicità azienda × bando; annullo di una bozza vuota; limiti per piano e pool dell'owner; Advisor con A attiva che non tocca B (404); whitelist dopo la pubblicazione; cascade; lock dell'owner con seconda connessione |
| 0038 | `partenariato_matching` | 6 | `company_collegamenti` (chiavi HMAC) + `_stato`; `partner_notifiche_proattive` + `fn_partner_claim_notifica` (dedup + tetto settimanale); `partner_calls.fanout_claim_at/fanout_completato_at`; `partner_email_settings` (digest + eventi + token); `partner_digest_runs`/`_invii`; `partner_call_salvate` | Claim e tetto; dedup; token unico; cascade |
| 0039 | `partenariato_candidature_chat` | 7 | `partner_candidature`, `partner_conversazioni`, `partner_messaggi` (immutabili), `partner_conversazione_letture` (per utente, `email_fino_a_id`); trigger di chiusura delle candidature alla chiusura della call; RPC `invia_candidatura`, `invita`, `decidi`, `ritira`, `scadi_inviti`, `scadi_per_opt_out`, `invia_messaggio`, `segna_letto`, `claim_email_chat`, `chiudi_conversazione` (ognuna verifica che l'azienda sia parte); **ridefinizioni con la stessa firma** di `fn_partner_consenso` (revoca → chiusura dei pendenti), `fn_partenariati_snapshot` (conteggio delle candidature) e `fn_partner_call_sostituisci_posizioni` (niente rimozione di posizioni con candidature attive); `partner_segnalazioni.oggetto_tipo` + `messaggio` | Quote (Gratuito/Smart/NULL, cambio di mese, pool Advisor); `stesso_gruppo`; `solo_invitati`; `accetta_inviti`; accettazione → conversazione + 3 righe di audit; esclusività; **deadlock test** chiusura call vs decisione; idempotenza dei messaggi; cascade |
| 0040 | `partenariato_consorzio` | 8 | `partner_call_membri` (esterni secondo Q20, capofila unico); `partner_call_documenti`; `partner_calls.validazione_esito/at`, `copertura_gap_ratio`; ridefinizioni con la stessa firma di `fn_partner_decidi` (aggiunge i membri) e `fn_partner_call_pubblica` (esclusività sui membri); backfill dei membri | Una sola `fn_partner_decidi`; riammissione; quota modificata → torna proposto; backfill idempotente |
| 0041 | `partenariato_moderazione_consulto` | 9 | `consultation_requests.partner_call_id` + i due indici `one_open`; `fn_create_consultation_request` ridefinita con la stessa firma e la verifica di appartenenza; colonne di decisione e ricorso sulle segnalazioni; RPC di decisione, presa in carico, ricorso, sospensione e ripristino; `fn_admin_metriche_partenariati`, `fn_admin_costi_partenariati` | Coesistenza di consulto AI-check e consulto da call; call di un'altra azienda rifiutata; effetti atomici; ricorso unico entro 6 mesi; metriche su un seed noto |
| 0042 | `partenariato_bozze` | 10 | `partner_bozze_documento`; `subscription_plans.partner_bozze_mese` **default 0** + seed (Q7); `fn_partner_bozza_crea` | Limiti 0/N/NULL; una pending; un errore pagato conta |

---

## 4. Backend — moduli per WP (nuovi / toccati)

- **WP1**:
  - nuovi: `core/privacy.py`, `schemas/regole_finanziarie.py`, `schemas/bilanci.py`, `services/bilanci_mapping.py` (puro), `services/bilanci_indicatori.py` (puro), `services/bilanci_service.py`;
  - toccati: `clients/openapi.py` (token per gruppo, `it_advanced`, host `visure`, classificazione non inviata / esito ignoto), `openapi_mapping.py` (fix), `openapi_service.py` (anteprima, draft, conferma; costanti `COST_IT_ADVANCED_CENTS=10`, TTL 330; P.IVA mascherata nei rami toccati), `ai_check_prompts.py` (versioni, pack), `ai_check_service.py` (contesto), `company_pdf_service.py` (sezione pluriennale), `routers/company.py`.
- **WP2**:
  - nuovi: `services/xbrl_bilancio.py` (puro), `services/bilancio_ufficiale_service.py`, `schemas/bilancio_ufficiale.py`;
  - toccati: `addon_service.update_addon` (23514 → 400), `addon_inventory_service` (rimborsi in un contatore a parte), `tests/test_openapi_client.py` (divieto degli scope aggiornato di proposito).
- **WP3**:
  - nuovi: `ai_prezzi.py`, `partenariati_ai_budget.py`, `bando_fonti_service.py`, `download_sicuro.py`, `pdf_testo.py`, `citazioni.py`, `partenariato_preclassificatore.py`, `partenariato_prompts.py`, `partenariato_regole.py`, `partenariato_vocabolario.py`, `partenariato_errori.py`, `partenariato_service.py`, `partenariati_scheduler.py`, `partenariato_valutazione.py` (CLI `--prepara`, `--offline`, `--reale --tetto-cents`), `schemas/partenariato.py`, router `partenariati_bandi.py` e `admin_partenariati.py`;
  - toccati: `clients/anthropic_ai.py` (`genera(...)` generico, usage allegato alle eccezioni; `extract`/`match` invariati), `ai_check_prompts.serializza_sezioni` (wrapper pubblico; `build_bando_input` identico byte per byte, con test), `bandi_service.BandiFilters.bando_ids`, `routers/bandi.py`, `core/config.py`, `main.py` (router e task con import locali), `api/deps.py`, `schemas/user.py` + `user_service` (`funzioni`), `pyproject.toml` (`pypdf>=6.19`).
- **WP4**: nuovi `partner_profile_service.py`, `partenariato_informativa.py` (testo segnaposto versionato «BOZZA — DA RIVEDERE CON IL LEGALE»), `partenariato_anonimato.py` (`trova_rilievi`, `anonimizza`), `schemas/partner_profile.py`, router `partner_profile.py` (`/me/partner-profile`) e `partenariati.py` (vocabolario, informativa).
- **WP5**:
  - nuovi: `schemas/partenariato_criteri.py`, `services/partenariato_criteri.py` (`valuta_criterio`, puro), `partenariato_accesso.py`, `partner_call_service.py`, `partner_call_gap.py` (puro, `criterio_da_requisito`), `partner_call_ai.py` (job asincroni), `schemas/partner_call.py`, router `partner_calls.py`;
  - toccati: `ai_check_service.ultimo_ready`, `schemas/plan.py` + `plan_service.PLAN_SELECT` + i 3 embed di `user_service`, `schemas/entitlement.py` + `entitlement_service`.
- **WP6**: nuovi `partenariato_collegamenti.py` (chiamata best-effort da `_persist_import` solo con il flag), `partenariato_matching.py` (puro), `partenariato_indice.py`, `partenariato_notifiche.py`; nuove funzioni `send_partner_*` in `email_service` (sempre tramite `filtra_recapitabili`).
- **WP7**: nuovi `partenariato_candidature_service.py`, `partenariato_chat_service.py`, router `partenariati_candidature.py`, `partenariati_chat.py`, `partenariati_email.py` (disiscrizione pubblica, **fuori dal flag**).
- **WP8**: nuovi `partenariato_validatore.py` (puro), `partenariato_documenti.py`, `partenariato_consorzio_service.py`, router `partenariati_consorzio.py`.
- **WP9**: `consulting_service` (`create_request_da_call`, `get_call_per_progettista`), `partenariato_moderazione_service.py`, `partenariato_admin_service.py`; nuova rotta in `progettista.py`, con il flag sulla singola rotta.
- **WP10**: `partenariato_bozze_service.py`, prompt e schema, builder PDF.

Nuove Settings, con default uguali ai valori di produzione:
- `partenariati_attivo=False`;
- scheduler: `partenariati_scheduler_attivo=True`, `partenariati_ora_esecuzione="05:30"`;
- WP3:
  - `partenariato_ai_model="claude-sonnet-5"`;
  - budget: `partenariato_budget_cents_giorno=500` (centesimi USD), `partenariati_ai_budget_cents_giorno_altri=200`, `partenariato_batch_budget_cents_giorno=0`;
  - limiti per utente: 10 al giorno (3 per il Gratuito, con email verificata);
  - tetti sui documenti (§2.3);
- WP6:
  - pesi `partenariato_peso_*`;
  - notifiche: top-K 20, soglia 50, tetto 3 a settimana;
  - digest: lunedì alle 08:30;
- WP7: TTL dell'invito 14 giorni, massimo 30 inviti in attesa per call.

---

## 5. API (prefisso `/api/v1`; [F] = dietro il flag, 404 se spento)

| Area | Endpoint |
|---|---|
| Bilanci (WP1–2) | `POST /me/company/import/preview` (+`bilanci`) · `POST /me/company/import/confirm` · `GET /me/company/bilanci` · `POST /me/company/bilanci/recupera` · `GET /me/company/dossier(/pdf)` · `GET/POST /me/company/bilanci/ufficiale` · `GET /me/company/bilanci/ufficiale/{id}` · `GET …/{id}/pdf` |
| Regole (WP3) [F] | `GET /bandi/{slug}/partenariato` · `POST /bandi/{slug}/partenariato/analisi` (202) · `GET /bandi?partenariato=ammesso\|obbligatorio` (con il flag spento il parametro viene ignorato) · `GET/POST /admin/partenariati/estrazioni…` · `POST /admin/partenariati/run` |
| Profilo (WP4) [F] | `GET/PUT /me/partner-profile` · `POST /me/partner-profile/consenso` · `POST /me/partner-profile/bozza-ai` (202) + GET dello stato · `GET /me/partner-profile/anteprima` · `GET /partenariati/vocabolario` · `GET /partenariati/informativa` |
| Call (WP5–6) [F] | `GET /partenariati/call?vista=tutte\|mie\|salvate&…` · `POST /partenariati/call` · `GET/PATCH /partenariati/call/{id}` · `POST …/{id}/regole` · `POST …/requisiti/genera`, `PUT …/requisiti` · `POST …/posizioni/proposta` (202), `PUT …/posizioni` · `POST …/testi/proposta` (202) · `GET …/anteprima` · `POST …/pubblica` · `POST …/chiudi` · `GET …/versioni` · `GET …/suggeriti` · `GET …/match` · `POST/DELETE …/salva` · `GET /partenariati/per-te` · `GET /partenariati/riepilogo` · `GET/PUT /me/partenariati/email-settings` · `GET/POST /partenariati/email/unsubscribe` (pubblico, fuori dal flag) |
| Candidature e chat (WP7) [F] | `POST …/call/{id}/candidature` · `POST …/call/{id}/inviti` (con handle opaco) · `GET /partenariati/candidature(/{id})` · `POST …/{id}/accetta\|rifiuta\|ritira` · `GET /partenariati/conversazioni(/{id})` · `GET/POST …/{id}/messaggi` · `POST …/{id}/letto` · `POST …/{id}/chiudi` · `POST /partenariati/segnalazioni` |
| Consorzio (WP8) [F] | `GET /partenariati/call/{id}/consorzio` · `PUT …/membri/{mid}` · `POST …/membri/{mid}/conferma\|esci` · `POST …/esterni` · `PUT …/budget` · `PUT …/documenti/{codice}` |
| WP9–10 [F] | `POST /partenariati/call/{id}/consulto` · `GET /progettista/richieste/{id}/call` · `GET/POST /partenariati/segnalazioni/{id}(/ricorso)` · admin: coda, decisione, ricorso, sospensione e ripristino di call e profili, elenco call, metriche, costi · `POST/GET /partenariati/call/{id}/bozze…` (+pdf) |
| Trasversale | `GET /me` (+`funzioni`) · `GET /me/entitlements` (+`partenariati`) · admin dei piani con le nuove colonne |

Per ogni endpoint il formato di `docs/api.md` (Body, → risposta, Errori con i code letterali, regole di spesa per esteso) viene scritto nel commit del suo WP.

---

## 6. Frontend

- **Rotte** (dentro `/app`, guard `PartenariatiRoute` che rende `NotFound` a flag spento):
  - `partenariati?vista=per-te|tutte|mie|salvate|candidature|conversazioni` → `Partenariati.tsx`;
  - `partenariati/call/nuova?bando=` e `…/call/:id/modifica?passo=` → `CallWizard.tsx`;
  - `partenariati/call/:id?tab=panoramica|suggeriti|candidature|consorzio|bozze` → `CallPartenariato.tsx`;
  - `partenariati/conversazioni/:id` → `ConversazionePartenariato.tsx`;
  - `partenariati/segnalazioni/:id`;
  - `admin/partenariati?tab=segnalazioni|call|metriche|estrazioni`.
- **Navigazione**: link «Partenariati» con badge (da `riepilogo`), in desktop **e** mobile. Se l'header va a capo a `lg`/`xl` diventa un `NavMenu`.
- **Pagine esistenti toccate**:
  - `BandoDetail`: `PartenariatoCard` compatta in sidebar e `PartenariatoSection` a tutta larghezza sotto l'AI-check, chiusa di default, con citazioni espandibili (link `#page=N`), disclaimer, stati per fase in `aria-live`;
  - lista bandi: filtro «Ammette partenariato» nei searchParams, con la nota «solo tra i bandi già analizzati»;
  - `/app/azienda`: sezioni «Bilanci» (tabella pluriennale accessibile, trend a barre div, indicatori, fonti, CTA «Recupera») e «Bilancio ufficiale» (prezzo **solo** via `prezzoDisplay`, storico, scarica PDF), sezione «Visibilità come partner»;
  - `ImportCompanyDialog`: blocco bilanci nell'anteprima + passo di consenso **non preselezionato** (solo con flag, `editable` e profilo non visibile);
  - `AdminPiani`: campi «Illimitate / Limite n» con la nota «0 = esclusa»;
  - `Preferenze`: email sui partenariati.
- **Hook** (radice `["partenariati", …]`, uno per dominio): `useBilanci`, `useBilanciUfficiali`, `usePartenariatoBando`, `usePartnerProfile`, `useCallPartenariato`, `usePartenariati` (per te, bacheca, riepilogo), `useCandidature`, `useConversazioni`, `useConsorzio`, `useSegnalazioni`, `useAdminPartenariati`, `useAziendaDaLink`.
  - Timeout Axios impostato per singola chiamata; polling solo sui job e sulla chat.
- **Componenti nuovi**:
  - tab accessibili (frecce, `aria-controls`, stato nei searchParams);
  - `ChatThread` (`role="log"`), `ChatComposer`, `AntitrustBanner`;
  - `MatchBadge`/`MatchSpiegazione` (icona **e** testo), `ValidatoreChecklist` (`aria-live`), `MatriceCopertura` (tabella con `th scope`), `ConsensoPartnerDialog` (checkbox non preselezionata + scelta dell'anonimato obbligatoria).
- Copy in italiano, dando del tu, **mai «Famiglia»**. I testi legali (consenso, antitrust, disclaimer) stanno in `lib/copy.ts`.

---

## 7. Macchine a stati (sintesi)

- **Esito IT-advanced**: `ok | non_disponibili(nessun_bilancio) | errore | timeout(esito_incerto) | saltato(forma_senza_bilancio | tempo_insufficiente | piva_diversa) | mismatch`. Stato derivato dei bilanci: `mai_richiesti → disponibili | non_disponibili`.
- **Richiesta di bilancio ufficiale**:
  - `in_invio → in_lavorazione | non_disponibile[R] | errore[R] | esito_ignoto`;
  - `esito_ignoto → in_lavorazione` (riconciliazione con la lista) oppure `errore[R, se non è mai arrivata]`;
  - `in_lavorazione → completata{xbrl_esito} | annullata[R] | errore(scaduta)`;
  - [R] = rimborso, secondo Q5.
- **Estrazione per bando**: `in_corso → pronta(estratta | nessun_segnale | riusata) | errore`. Un rinnovo parte da `pronta` o `errore` con prenotazione; `fase` avanza documenti → lettura → analisi, con heartbeat.
- **Consenso**: `non_visibile ⇄ visibile`, solo tramite RPC. Revoca automatica su soft delete o archiviazione, e su cambio di P.IVA o ragione sociale.
- **Call**:
  - `bozza → pubblicata → chiusa_completata | chiusa_annullata | scaduta`;
  - `bozza → chiusa_annullata`;
  - `pubblicata ⇄ sospesa_moderazione`: il ripristino torna allo stato precedente, oppure a `scaduta` se la scadenza è passata.
- **Candidatura o invito**: `inviata → accettata | rifiutata | ritirata | scaduta`.
- **Conversazione**: `aperta → chiusa`; sola lettura derivata se una delle due aziende non è più viva.
- **Membro**: `proposto ⇄ confermato → uscito → proposto`.
- **Segnalazione**: `ricevuta → in_esame → decisa → ricorso_presentato → ricorso_deciso`.
- **Bozza**: `pending → ready | error`.

---

## 8. Costi

**Unitari** (listini senza IVA; per Anthropic il listino in cache al 2026-06-24, $2/$10 per MTok su sonnet-5, da riconfermare):

| Operazione | Costo | Chi paga / tetto |
|---|---|---|
| IT-full (esistente) | 0,30 € | piattaforma |
| IT-advanced (in anteprima o recupero) | 0,10 € (30 gratis al mese) | piattaforma; saltato nei casi di B3 |
| Bilancio ottico | 4,50 € (best 2,95 €) | coperto dal prezzo dell'addon; tetto 30 al giorno |
| GET stato / lista / `/impresa` | 0,001 € (1440 al giorno gratis) | registrati a costo 0 |
| Estrazione WP3 con PDF (~25 pagine) | ~$0,14 (massimo ~$0,27 con i tetti) | piattaforma; budget di $5 al giorno |
| Estrazione WP3 dal solo catalogo | ~$0,06; con `nessun_segnale` $0 | idem |
| Bozza profilo / posizioni / testi | ~$0,02–0,05 ciascuna | budget «altri servizi» di $2 al giorno |
| Bozza documento (WP10) | ~$0,04 | limite di piano |
| Matching, validatore, notifiche | 0 | CPU |
| Email (~3.000 al mese) | 0 con SMTP OVH; la soglia gratuita di Resend non basta | Q6 |

**Mensile a 500 aziende.**

Ipotesi:
- ~100 import al mese, 15% di società di persone;
- 250 aziende attive;
- estrazioni: circa 600 nel primo mese (avvio a freddo), poi circa 200 al mese;
- 30% di opt-in;
- 40 call nuove al mese;
- 8% delle aziende all'anno compra un bilancio ufficiale.

| Voce | Primo mese | A regime |
|---|---|---|
| openapi IT-advanced (aggiuntivo) | ~6 € | ~6 € |
| Bilancio ottico | ~15 € (coperti dall'addon) | idem |
| Anthropic WP3 | ~$63 | ~$20 |
| Anthropic WP4, WP5, WP10 | ~$12 | ~$10 |
| **Totale a carico della piattaforma** | **~75 €** | **~35 €** |

Il **caso peggiore** è limitato dai tetti fail-closed: WP3 $5 al giorno + altri servizi $2 al giorno, cioè al massimo circa $210 al mese, qualunque sia l'uso. Il batch notturno è spento; se acceso, circa $145 una tantum per coprire il catalogo aperto.

---

## 9. Rischi principali e mitigazioni

| Rischio | Mitigazione |
|---|---|
| Fuga cross-tenant (primo accesso tra owner diversi) | Modulo unico di accesso, proiezioni a whitelist, handle opachi, 404, **test canary** con stringhe marcatrici su ogni endpoint e ruolo, reveal solo in RPC con audit |
| Impersonificazione di un'azienda | T5 (coerenza con il registro, fail-closed) + Q9 |
| Oracolo sui valori esatti di bilancio | Valutazione dei terzi sulla fascia; niente regole finanziarie manuali; test d'inferenza (30 budget → l'esito cambia solo ai bordi di fascia) |
| Spesa AI o openapi fuori controllo | Budget fail-closed unico, riserva al caso peggiore, usage allegato agli errori, heartbeat del claim, mai retry su esito ignoto, tetti in RPC |
| SSRF, PDF, ZIP o XML ostili | §2.2 B11 e §2.3 R2-R3; test con resolver finto, proxy in ambiente, bombe |
| Prompt injection dai PDF | Output vincolato, citazioni verificate, snapshot confermato dal creatore come unica fonte deterministica, URL solo https, testo LLM mostrato senza link automatici |
| Copertura documentale del 61% | Estrazione anche dal catalogo, «non determinabile» dichiarato, correzione nel passo «Regole del bando» |
| Deadlock tra RPC | Ordine di lock globale (owner → call → candidatura, profilo → inventario), testato con due connessioni |
| Proxy più corto della catena dei tempi | T7 + Q23 |
| Cold start (pochi opt-in) | «Per te» visibile anche senza opt-in, stati vuoti con CTA, digest solo con contenuto |
| Testi legali non ancora rivisti | Segnaposto marcati «BOZZA»; il flag resta spento in produzione finché il legale non li approva (G4) |

---

## 10. Piano di test (sintesi) e criteri di accettazione

- **Unit (puri)**:
  - mapping IT-full e IT-advanced (fixture sintetiche derivate dagli esempi OAS, sostituite da quelle sandbox a G1; `netWorth` di IT-advanced deve andare in `risultato_esercizio`);
  - precedenza delle fonti (vettori condivisi con il test DB);
  - indicatori, fasce e regole: i tre esiti, i bordi (0,6 ≤ 0,6), vista terzi senza valori esatti;
  - XBRL (ordinario, abbreviato, micro, nil, instant d'inizio, tuple, DOCTYPE, bombe);
  - citazioni (con la proprietà «accetta tutto ciò che accetta la vecchia»), pre-classificatore, `normalizza_content_type`, PDF sintetici generati con reportlab, download sicuro;
  - vocabolario, anonimato, `valuta_criterio`, matching (ogni filtro, determinismo, rotazione, cap per owner), collegamenti, validatore (verde, rosso, grigio), documenti, proiezioni.
- **Servizi** (FakePrimary, FakeAi e FakeHTTP per file):
  - niente doppia spesa (ogni ramo di B3), lock e cooldown, completamento WP2 idempotente, claim e heartbeat WP3;
  - un'estrazione WP3 con `max_tokens` scala comunque il budget;
  - notifiche senza dati di terzi, digest con `filtra_recapitabili`.
- **API** (mini-app + `dependency_overrides`):
  - a flag spento **ogni** rotta del modulo risponde 404 anche senza token, mentre `/progettista` e `/bandi` esistenti restano 200;
  - isolamento tra 3 owner, un Advisor con 2 aziende, membri con e senza visibilità, progettista assegnato e non assegnato: body scansionati per stringhe canary (ragione sociale, P.IVA, email del referente, fatturato esatto, dettagli riservati, uuid interni).
- **DB**: un test per migration (§3) + il test generico «nessuna `fn_%` eseguibile dai client».
- **Esempio guida — un unico test d'integrazione** (`tests/db/test_partenariato_flusso_guida.py`, criterio 4):
  1. X (Smart) e Y (Smart, con opt-in); call con requisiti tipizzati A–D, dove X copre B e D;
  2. `per_te(Y)` mette la call in cima con «Copri «A» e «C», che mancano al capofila.»; `suggeriti(X)` mette Y in cima con la stessa spiegazione;
  3. il claim della notifica restituisce true;
  4. Y si candida, X accetta: conversazione, audit `identita_rivelata`, messaggio;
  5. quote 70/30 → verde, 75/25 → rosso, quota mancante → grigio;
  6. W collegata a X, V senza opt-in e Z fuori regione non compaiono mai;
  7. revoca di Y → sparisce subito;
  8. variante con Y Gratuito: l'invito passa, la candidatura spontanea no (criterio 6).
- **Performance**: benchmark sintetico con 500 aziende e 200 call (`BANDOFIT_BENCH=1`): ranking per call p95 < 50 ms, «Per te» p95 < 20 ms, reload dell'indice ≤ 20 query.
- **Valutazione WP3**: offline subito; reale al gate G3 (obiettivo `modalita` ≥ 90% sul campione).
- **Per ogni WP**: test pertinenti durante lo sviluppo; alla fine `pytest` completo, `ruff` (nessun errore oltre i 13 esistenti), `npm run typecheck`, `npm run build`.

| Criterio | Dove si verifica |
|---|---|
| 1 Import + IT-advanced, fasce ai terzi, niente doppia spesa | test WP1 + G1 in sandbox |
| 2 Bilancio ufficiale → addon → XBRL prevalente | test WP2 + G1 |
| 3 Regole citate, `modalita` ≥ 90% | test WP3 + **G3** (non misurabile finché non c'è credito) |
| 4 Esempio guida end-to-end | test d'integrazione unico |
| 5 Collegate e senza opt-in mai; revoca immediata | matching + test d'integrazione |
| 6 Gratuito / limiti in RPC | test DB 0036-0039 |
| 7 Controlli verdi e documentazione | fine di ogni WP |

---

## 11. Sequenza, commit, migration e gate

1. **WP0** — commit `docs/partenariati.md` (questo piano + appendice A) + indice in `docs/README.md` + fixture del campione (solo id) + script sandbox (`backend/scripts/verifica_sandbox_openapi.py`, legge le chiavi dalle variabili d'ambiente e salva fixture anonimizzate).
2. **WP1** → ⚠️ 0032. **WP2** → ⚠️ 0033. Vanno senza flag.
3. **WP3** → ⚠️ 0034. **WP4** → ⚠️ 0035. **WP5** → ⚠️ 0036, 0037. **WP6** → ⚠️ 0038. **WP7** → ⚠️ 0039. **WP8** → ⚠️ 0040. **WP9** → ⚠️ 0041. **WP10** → ⚠️ 0042.

Regole di commit:
- uno o più commit per WP, con messaggio in italiano; ogni commit aggiorna `docs/` e `docs/changelog.md` («- ⚠️ Migration **00NN** da eseguire dallo SQL Editor del DB primario **prima** del deploy.»);
- autore solo Michele, nessun `Co-Authored-By`; `git add` per singolo file (mai `-A`);
- `Claude outputs/` e `docs/contratto-db-bandi.md` restano fuori.

Branch `feat/partenariati` da `main`: la spec qui prevale sul `claude/<…>` del CLAUDE.md globale.

Gate (non bloccano lo sviluppo, ma il rilascio sì):
- **G1** sandbox openapi: le tue azioni in §14, poi lanci lo script. Serve a sostituire le fixture sintetiche e a confermare i codici CEE; senza G1 il fix del mapping resta «verificato solo su OAS».
- **G2** migration nello SQL Editor prima di ogni deploy.
- **G3** credito Anthropic, poi `partenariato_valutazione --reale --tetto-cents 800`.
- **G4** testi legali (informativa, Termini/DSA, disclaimer) prima di accendere il flag in produzione.

Durante l'esecuzione mi fermo solo per ambiguità che cambiano comportamento o costo e che non sono coperte da §13.

---

## 12. Documentazione da aggiornare

- `docs/architecture.md`: decisioni chiave nuove, numerate in coda (14…) al momento di ogni commit.
- `docs/database.md`: tabelle, RPC, invarianti, semantica NULL/0 dei piani.
- `docs/api.md`: endpoint, errori, regole di spesa, pattern del 404 a flag spento.
- `docs/frontend.md`: rotte, guard, hook e query key.
- `docs/setup.md` e `docs/deploy.md`: variabili nuove, attivazione delle API openapi in console, riga compose da aggiungere a mano, `proxy_read_timeout`.
- `.env.example` (radice e backend), **se confermi Q-A2**.
- `docs/partenariati.md`: pesi, vocabolario, esempio guida, cascade e conservazione, costi.
- `docs/changelog.md` a ogni commit.
- `CLAUDE.md` non si tocca: non nascono cartelle nuove.

---

## 13. Decisioni (approvate con il piano: vale la colonna «Decisione»)

| # | Tema | Decisione |
|---|---|---|
| Q1 | Limiti per piano (call attive / candidature al mese) | Gratuito 0/0 · Smart 1/5 · Pro 3/20 · Advisor 10/50 · tailored 0 finché non lo imposti. Pool per owner, mese solare Europe/Rome, contano le call pubblicate e sospese, al massimo 5 bozze per azienda |
| Q2 | Testo dell'informativa partner | Segnaposto versionato `2026-10-bozza-1` da far rivedere al legale. Deve coprire: dati mostrati, reveal dopo l'accettazione, referente, consulenti incaricati, collegamenti societari (HMAC), conservazione, diritti. A una nuova versione il consenso resta valido e compare un banner di riconferma (salvo diverso parere del legale) |
| Q3 | Prezzo dell'addon «Bilancio ufficiale» (costo 4,50 € + IVA) | **≥ 7,90 € + IVA**. Viene creato inattivo e lo attivi tu |
| Q4 | Destino del PDF del bilancio ottico | **(C)** in DB (`bytea`), tetto 8 MB, cancellato a cascata con l'azienda, download dal backend con autorizzazione live. L'XBRL si conserva sempre (serve a ri-parsare gratis), il verbale si scarta. Alternative: (A) scartarlo dopo il parsing; (B) bucket privato. Il round-trip di 8 MB via PostgREST lo verifico nel harness: se non regge passo a B |
| Q5 | Rimborso automatico (deviazione da 0028) | **Sì** per i rifiuti sincroni (278/213/275), per le richieste mai inviate (anche quelle dimostrate dalla riconciliazione dopo 30 minuti), per il credito del provider esaurito e per «Annullata». **No** per esito ignoto, scadenza ed errori di parsing: decidi tu con il grant |
| Q6 | Digest e provider email | Digest **settimanale dedicato**, con opt-out e token propri, solo per le aziende con opt-in. Email di evento (inviti, candidature, esiti, messaggi) attive di default con opt-out. In produzione **SMTP OVH** (circa 3.000 email al mese) |
| Q7 | Bozze AI | **Spostate in WP10**. `partner_bozze_mese` 0/3/10/30 (Gratuito/Smart/Pro/Advisor); gli errori pagati contano nel limite |
| Q8 | pgvector | **No** in v1: matching deterministico |
| Q9 | Verifica d'identità oltre alla coerenza con il registro (T5) | Per call nominative e reveal: il CF dell'utente verificato (`verify_cf`) deve comparire tra i legali rappresentanti in `company_people`. In alternativa basta T5. **Rivista in WP4** (§16): questa verifica è necessaria ma non sufficiente; il nominativo resta spento finché non c'è una prova di controllo dell'impresa |
| Q10 | Import a pagamento e P.IVA | Dopo il primo import confermato la P.IVA resta legata all'azienda (per cambiarla serve un'altra azienda o l'admin) + tetto fail-closed di 3 chiamate openapi a pagamento al giorno per owner. **Cambia il comportamento dell'import esistente** |
| Q11 | Esiti finanziari verso terzi sulla fascia, non sui valori esatti + divieto di regole finanziarie manuali | **Sì**: qualche «incerto» in più, ma licenza CGC 7.3 e antitrust rispettati |
| Q12 | Profili anonimi | Si mostrano solo classe dimensionale + fascia di fatturato; esperienze senza anno né ruolo; certificazioni per categoria; più l'anteprima «come ti vedono» |
| Q13 | Referente e «reveal dei contatti» | Si rivelano i dati d'impresa (ragione sociale, sito, PEC dal registro) + nome e ruolo del referente + la chat. **L'email personale del referente mai** (la spec WP4 dice «mai email in chiaro»), salvo un suo consenso esplicito. Un referente diverso dal titolare deve accettare lui stesso |
| Q14 | Chi agisce | Solo il titolare, **chat compresa**, in v1; i membri leggono |
| Q15 | WP3: innesco, modello, budget, limiti | Innesco automatico all'apertura della sezione (zero segnali → niente LLM); `claude-sonnet-5`; $5 al giorno di budget (e $2 per gli altri servizi); 10 analisi al giorno per utente, 3 per il Gratuito con email verificata; cooldown 24 h; riverifica ogni 14 giorni; tetti 4 documenti / 150 pagine / 15 MB / 180.000 caratteri; batch spento (se lo accendi: $3 a notte) |
| Q16 | Fonti WP3, cioè la tua domanda: curatela o upload? | **Nessuna** in v1: catalogo + PDF di `bando_link` (61% dei non chiusi), con il creatore che corregge nel passo «Regole del bando». L'upload del PDF da parte del creatore resta un'estensione futura |
| Q17 | Matching: soglie di collegamento, pesi, notifiche | **Certo**: partecipazione diretta ≥ 25%, stesso gruppo, o socio comune > 50%. **Possibile**: quota ignota, socio comune ≥ 25%, o esponente comune. Si escludono entrambi. Pesi .50/.20/.10/.10/.10, penalità 5 (massimo 20), top-K 20, soglia 50, tetto 3 a settimana |
| Q18 | Bando sospeso, e call dopo un downgrade | La call diventa `scaduta` (terminale); dopo un downgrade le call già pubblicate restano fino alla chiusura |
| Q19 | Consulto dalla call | Indici separati (coesiste con un consulto AI-check); AI-check facoltativo; la call non si cancella con un consulto aperto; il progettista vede la proiezione dedicata (W1), con audit fail-closed |
| Q20 | Membri esterni (non in piattaforma) nel consorzio | **Sì** (nome, paese, tipo, quota): senza, i requisiti «≥ 3 paesi UE» restano sempre rossi |
| Q21 | Cancellazione definitiva di un utente o di un'azienda | **Cascade** (politica di oggi: sparisce anche lo storico della controparte). Il soft delete revoca il consenso e mette le conversazioni in sola lettura |
| Q22 | Conservazione dei dati | La dichiaro in doc (consensi 5 anni dopo la revoca, esecuzioni AI 12 mesi, segnalazioni 24 mesi). La pulizia automatica si implementa dopo il parere del legale, non in v1 |
| Q23 | Correzioni collegate al lavoro | (i) guardia sull'azienda nel draft (B4); (ii) letture per id dell'azienda nei percorsi del progettista (WP1); (iii) rilascio del lock condiviso condizionato a un token (nuova firma con DROP); (iv) anteprima import: resta sincrona con la nuova catena dei tempi finché non sono noti i timeout reali del proxy; se risultano inferiori a 280 s diventa un job asincrono |
| Q24 | Deep link per l'Advisor | `?azienda=<id>` + hook unico sulle pagine nuove e su `/app/azienda`; la campanella resta com'è (fuori perimetro, segnalato) |
| Q25 | «Per te» e candidature | «Per te» visibile anche senza opt-in; per candidarsi serve l'opt-in; la revoca chiude inviti e candidature pendenti |
| Q-A1 | File non tracciati `Claude outputs/` e `docs/contratto-db-bandi.md` | Restano **fuori** dai commit (il contratto lo versioni tu, se vuoi) |
| Q-A2 | La spec vieta `.env*` ma chiede di aggiornare `.env.example` | Aggiorno **solo** `.env.example` (radice e backend), senza segreti; mai `.env` né `docker-compose.yml` |

---

## 14. Azioni manuali (Michele)

1. **G1 sandbox openapi**:
   - su console.openapi.com attiva la sandbox e ricarica il credito virtuale;
   - attiva in sandbox Company **IT-advanced** e **Visure Camerali** (`bilancio-ottico`, `impresa`);
   - esporta nella shell `OPENAPI_SANDBOX_EMAIL` e `OPENAPI_SANDBOX_API_KEY`;
   - lancia `! backend/.venv/bin/python backend/scripts/verifica_sandbox_openapi.py`.
2. **Prima del deploy di WP1/WP2**: attiva le stesse API anche in **produzione** (senza, falliscono solo i nuovi prodotti, grazie ai token separati); poi le migration 0032 e 0033 dallo SQL Editor.
3. Prezzo e attivazione dell'addon `bilancio-ufficiale` in AdminAddon.
4. Per il modulo partenariati: le migration 0034–0042 in ordine, prima dei rispettivi deploy; la riga `PARTENARIATI_ATTIVO: ${PARTENARIATI_ATTIVO:-false}` in `docker-compose.yml` quando vorrai accendere il flag. Dopo la 0036, in AdminPiani, i limiti di partenariato dei piani diversi da Gratuito, Smart, Pro e Advisor (per esempio `tailored`): partono da 0, cioè senza call né candidature.
5. Revisione legale di informativa, Termini/DSA e disclaimer (G4); un canale di segnalazione per chi non è utente.
6. Credito Anthropic per la valutazione reale (G3).
7. Dati che mi servono: `proxy_read_timeout` di nginx/NPM e piano Cloudflare; valore «Max rows» del DB primario (Supabase → Settings → API).
8. Push e deploy (li fai tu).

---

## 15. Problemi fuori perimetro notati (solo segnalati)

- `bando_alert_service.carica_candidati` non pagina: con circa 1440 bandi non chiusi potrebbe avvicinarsi al max-rows di 1000 (il filtro sugli ultimi 60 giorni probabilmente lo tiene sotto soglia: da verificare).
- AI-check: prezzi cablati a $3/$15 contro il listino $2/$10 (sovrastima dei costi registrati); il modello non entra nella chiave di cache; `api_usage_events.cost_cents` mescola centesimi EUR (openapi) e USD (Anthropic).
- La campanella non cambia azienda al click (Advisor).
- `consultation_requests_one_open` è per famiglia, non per azienda.
- `fetch_bando_for_ai` non esclude i bandi sospesi o revocati; `DETAIL_SELECT` usa colonne che spariranno nella fase (d); `link_policy` blocca solo un dominio, mentre la denylist del contratto è più ampia.
- `addon_risorsa_solo_titolare` non è mappato (risponde 502); commenti e test parlano di «402» invece di 409; `ADDON_MOVIMENTO_LABELS.consume` dice «Consulenza richiesta» per qualunque addon.
- OAuth v1 di openapi è deprecato dal 2027-12-31.
- Venv locale disallineato rispetto all'immagine (Python 3.12.0 / expat 2.5.0 contro 3.12.14 / expat 2.8.3).
- Documentazione stantia: `architecture.md` §9 (cache «per owner»), `database.md` su `content_hash`, `frontend.md` senza `/app/admin/pagamenti`.
- `AiCheckCard` non annuncia lo stato «in corso» agli screen reader; `oggetto sociale` non è mappato.

---

## 16. Stato dell'implementazione e scostamenti dal piano

- **WP1 (fatto)** — scostamenti decisi in implementazione o dopo la revisione del diff:
  - il recupero dei bilanci usa **solo** la P.IVA già importata (`company_data.piva_fetched`): senza import, o con la P.IVA dei dati aziendali diversa, risponde 400;
  - quota giornaliera: un'anteprima o un recupero valgono **un'operazione** (IT-full + IT-advanced insieme); la quota si prenota **dopo** il lock, così un lock occupato non la consuma;
  - `ImportPreview.bilanci.stato` è `disponibili` anche con il solo esercizio della visura, con il motivo dello storico mancante;
  - `dossier.bilanci.anno_fatturato` (nuovo) quando il fatturato della visura è di un anno diverso da quello di chiusura;
  - nel pack dell'AI-check `bilanci.numero_esercizi` diventa «almeno N» se lo storico non è completo;
  - un tentativo fallito non declassa uno storico già completo della stessa P.IVA; uno storico di un'altra P.IVA viene scartato;
  - il mint del token è fuori dalla finestra del timeout della chiamata (un mint lento non diventa un «esito incerto» pagato);
  - la query key del frontend è `["company-bilanci", activeCompanyId]`;
  - lock di import con token aggiunto accanto alle vecchie funzioni (niente DROP: le migration girano prima del deploy);
  - annotato, non corretto: l'anteprima non consulta il cooldown dell'ultimo recupero dei bilanci (impatto massimo 0,10 €, limitato dalla quota giornaliera).
- **WP2 (fatto)** — scostamenti:
  - la lista delle richieste del provider vale come «nessuna richiesta» solo con 404 **e** codice 270; ogni risposta dubbia è un errore e una lista paginata non prova mai un'assenza;
  - la riconciliazione cerca prima l'id annotato nel registro consumi, poi la lista; il rimborso «mai inviata» richiede che nessuna voce della stessa P.IVA non attribuita sia nata dopo la richiesta;
  - failsafe della lavorazione: 72 ore se l'ultimo tentativo è fallito per un guasto, 24 ore negli altri casi; ogni chiusura senza rimborso si logga a livello ERROR;
  - «ultimo disponibile» rifiutato (409) se l'ultimo esercizio chiuso è già posseduto;
  - la verifica `/impresa` (forme diverse dalle società di capitali) passa dal tetto giornaliero condiviso con l'import;
  - PDF conservato: esclusi i nomi con «verbale»/«assemblea», poi il primo con «bilancio» nel nome, altrimenti il più grande;
  - notifica «Bilancio ufficiale non utilizzabile» quando mancano sia PDF sia numeri;
  - con l'addon disattivato la card resta visibile se esistono richieste passate;
  - da verificare in sandbox: forma reale della lista delle richieste e nomi dei file nello ZIP; round-trip di un `bytea` da 8 MB via PostgREST (se non regge, bucket privato).
- **WP3 (fatto)** — scostamenti decisi in implementazione o dopo la revisione del diff:
  - `tipo_soggetto` `altro` → beneficiari 1/5/7/10 del catalogo; la competenza `altro` sta in un'area propria;
  - `TipoDocumentoRichiesto` = tutti i documenti del vocabolario più `altro` (non solo i documenti base);
  - errori nuovi: `403 email_non_verificata` (Gratuito senza email verificata) e `409 forza_non_ammessa` («Analizza comunque» fuori dalle condizioni);
  - `leggi_stato_bandi` legge `bando_pubblico` a blocchi di 100 id;
  - il download ha un **budget di rete** di 20 s che scorre solo quando il documento ha il turno (1 per portale, 2 in totale): l'attesa in coda non lo consuma; host del link normalizzato come nel browser (IDNA, percent-decoding) prima della denylist; bloccato anche `fec0::/10`; il link di un documento bloccato per policy non si mostra;
  - la guardia «nessun segnale» vale solo se almeno un documento è stato letto o se i documenti mancano per cause permanenti: con soli errori transitori (timeout, rete, 5xx, lettura oltre il tempo) l'esito è `errore` `documenti_non_raggiungibili`, costo 0, con backoff;
  - riserva al caso peggiore con **2,5 caratteri per token** (non 3,5): il tokenizer di `claude-sonnet-5` produce fino a ~1,35 volte i token dei modelli precedenti; sul timeout si registra il massimo tra riserva e stima dell'input inviato; da riconfermare con `count_tokens` sui PDF del campione;
  - claim scaduto o perso **prima** della fase `analisi`: esecuzione chiusa a costo 0 (`fn_partenariato_esecuzione_scaduta`, nuova); in fase `analisi` costo ignoto (la riserva resta) e riga `timeout_unknown` nel registro consumi; il limite per utente non conta le esecuzioni `errore`/`interrotta` a costo 0 senza LLM; su cancellazione del task (spegnimento) la pipeline chiude e registra prima di uscire;
  - la citazione della **modalità** deve avere almeno 3 parole e 15 caratteri normalizzati per fondare `modalita_effettiva` (le altre voci no: citano spesso numeri brevi);
  - pulizia dei domini esclusi e numerazione di pagina in tempo lineare (testo dei PDF e output del modello sono input non fidati, sull'event loop);
  - tetto del filtro «Ammette partenariato» a **500** id (non 1000): con 1000 id l'URL delle query del catalogo supera gli 8 KB; da misurare il limite reale del gateway;
  - `partner_min`/`partner_max` contano il capofila anche quando il bando lo chiama promotore; etichette 18177 e 17509 del campione riallineate (da validare);
  - valutazione: `nessun_segnale` vale `non_determinabile`, il recall del pre-classificatore usa l'etichetta (il gruppo solo come strato), campo facoltativo `non_raggiungibili` per i campi che la pipeline non può leggere;
  - annotati, non risolti: con un «Analizza comunque» finito in errore o timeout la forzatura non si ripete (contratto; la UI ora lo dice), niente link alla conferma dell'email nell'errore `email_non_verificata` (non esiste una pagina in-app), nessun semaforo globale sulle pipeline in volo.
- **WP4 (fatto)** — profilo partner, consensi, referente e bozza AI (migration 0035). Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **profilo nominativo spento** (Q9 rivista): si compare solo in forma anonima. `anonimo: false` risponde `409 nominativo_non_disponibile` e `identita.motivo_nominativo` vale `non_disponibile`; l'interruttore è una costante nel codice (`NOMINATIVO_DISPONIBILE`), non una setting. Mostrare il nome di un'impresa, pubblicare call nominative e rivelare i contatti (WP5, WP7) richiedono una prova che chi agisce controlli davvero l'impresa: la verifica del codice fiscale del profilo non basta da sola. Da decidere prima di riaccenderlo (per esempio un codice monouso alla PEC del Registro Imprese, SPID/CIE o una verifica dell'admin); le RPC mantengono la verifica del legale rappresentante come condizione necessaria;
  - informativa: la versione inviata deve essere quella corrente solo per concedere e per passare al nome; per passare al nome un profilo già visibile serve anche un consenso registrato su quella versione (altrimenti si riconferma dal dialog completo); revoca e ritorno all'anonimato non si bloccano mai e il registro riporta la versione del consenso esistente, mai quella inviata dal client;
  - dati del registro di prova ammessi per il consenso solo con `OPENAPI_ENV=sandbox` (fail-closed: qualunque altro valore li rifiuta, non solo `production`);
  - referente: nuova azione `annulla_proposta` (toglie solo la proposta pendente, il referente in carica resta); riproporre il referente in carica annulla la proposta pendente; `referenti_possibili` esclude il referente in carica; una revoca registra la versione accettata dal referente; proposte, rifiuti e annullamenti vanno solo nell'audit;
  - bozza AI: chiusura **atomica** di bozza ed esecuzione (`fn_partner_bozza_ai_concludi`, nuova) e una sola riga di consumo per esecuzione, scritta da chi la chiude; il failsafe (`fn_partner_bozza_ai_esecuzione_interrotta`, nuova) registra la riserva come `timeout_unknown`, come la 0034 in fase di analisi, e chiude anche le esecuzioni rimaste in corso senza una bozza in corso; una cancellazione del task in qualunque punto completa solo i passi non ancora tentati;
  - oltre al limite per azienda, **limite per titolare** su tutte le sue aziende (`PARTNER_BOZZA_AI_LIMITE_UTENTE_GIORNO`, 10, tramite il limite per richiedente di `fn_partenariati_ai_prenota`: la bozza la avvia solo il titolare), come chiesto dalla revisione della spesa; lo stato della bozza si legge dalla GET del profilo (nessuna rotta di stato separata);
  - controllo anti-contatti: riconosce anche link brevi di messaggistica, domini con il punto staccato e numeri spezzati da trattini bassi o scritti a cifre singole; non blocca più fasce orarie («08-12 14-18»), sigle con barra («MI/012345»), numeri preceduti da parole di registro (protocollo, iscrizione, autorizzazione, accreditamento…) e «ad.es.»;
  - la revoca automatica su `company_profiles` è un trigger `AFTER UPDATE OF` sulle sole colonne interessate; l'anteprima di un profilo mai salvato mostra i soli dati del registro (`codice_pubblico` tutto zeri); nel PUT gli id del catalogo si verificano fail-closed; `DELETE /me/partner-profile/bozza-ai` risponde `409 bozza_in_corso` durante la preparazione;
  - la sezione rilegge il profilo quando cambiano ragione sociale o P.IVA dalla card dell'azienda e avvisa che quel cambio revoca la visibilità;
  - annotati, non risolti: nei profili anonimi una ragione sociale fatta solo di parole comuni (per esempio «Energia S.r.l.») blocca quelle parole nei testi (da declassare ad avviso con un lessico di parole comuni); un controllo testuale non intercetta ogni modo di scrivere un recapito (la moderazione arriva con il WP9); informative ancora segnaposto da far rivedere al legale; `fn_partner_consenso` andrà ridefinita in WP7 per chiudere inviti e candidature alla revoca.
- **WP5 (fatto)** — limiti di piano e call di partenariato (migration 0036 e 0037). Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **call solo anonime**, come il profilo (Q9 rivista in WP4): `anonima: false` in creazione o aggiornamento risponde `409 nominativo_non_disponibile` e il wizard non offre la scelta (nota fissa sull'anonimato); la RPC di pubblicazione conserva la verifica del legale rappresentante per un'eventuale call nominativa futura. Verso terzi il creatore è «Azienda anonima» con regione della sede, sezione ATECO e classe dimensionale, solo dal Registro Imprese e solo se i dati importati sono dell'azienda (T5);
  - nel WP5 il dettaglio `GET /partenariati/call/{id}`, l'anteprima e le versioni servono **solo** all'azienda creatrice (titolare e membri con visibilità) e la lista ha solo `vista=mie`: bacheca, vista pubblica della call e «Segnala» dalla pagina della call arrivano con il WP6 (il ramo pubblico di `CallPartenariato` è già pronto); `POST /partenariati/segnalazioni` è già attiva (C7) per le call e i profili che l'utente vede;
  - job AI: il limite «per call» si conta per **azienda × bando** e per servizio (annullare e ricreare la bozza non lo azzera) e il limite per titolare vale su **tutti** i servizi delle call insieme; `ai_limite_call` e `ai_limite_owner` rispondono `429 ai_limite_giornaliero`, il budget esaurito `429 ai_sospesa_oggi`; una nuova prenotazione chiude il job del servizio rimasto in corso oltre 10 minuti;
  - requisiti: identità dei requisiti dall'AI-check e dalle voci dello snapshot con l'**impronta del contenuto** nel riferimento (un requisito rinumerato da una nuova estrazione non eredita id, etichetta, «cercato» né il verdetto dell'AI-check); l'etichetta indicata dal client vale solo per i requisiti conservati, così un requisito nuovo non riceve i collegamenti delle posizioni a uno rimosso; al salvataggio i requisiti tipizzati riprendono i verdetti dell'AI-check che assorbono nella proposta (la copertura salvata è quella mostrata); le regole finanziarie tengono l'id della regola dello snapshot (Q11);
  - anti-contatti delle call: controllo sulla forma canonica del testo e con il nome dell'azienda anche scritto attaccato; i testi si salvano senza caratteri di formato invisibili; si controllano anche i testi dei requisiti visibili ai terzi, qualunque origine dichiari il client (un recapito copiato dal bando in un requisito cercato va tolto o riscritto); i `dettagli_riservati` ammettono il nome dell'azienda ma non contatti;
  - la bozza nasce con il passo 1 salvato (`wizard_passo` 2): «Riprendi» porta al passo delle regole; il passo 3 del wizard raccoglie anche budget e quota del creatore;
  - `calls_aperte` del bando conta solo le call pubblicate, non scadute e visibili a tutti (le `solo_invitati` non si rivelano); `/me/entitlements.partenariati` ha le candidature usate a 0 finché il WP7 non ridefinisce `fn_partenariati_snapshot` con la stessa firma;
  - segnalazioni: snapshot della proiezione pubblica come evidenza, una aperta per contenuto e segnalante (`409 segnalazione_gia_presente`), limite anti-abuso giornaliero per utente (`429 limite_segnalazioni`), conferma di ricezione in-app con il codice della segnalazione;
  - scheduler: nel passo `chiusura_call` sono isolati, oltre a ogni call, anche i singoli aggiornamenti a blocchi di `bando_mancante_dal` e dello snapshot del bando;
  - accessibilità: l'interruttore «Lo cerchi / Cercalo» ha un nome con il requisito, e la conferma della pubblicazione si annuncia dopo il montaggio della pagina;
  - restano per i WP successivi, come da piano: esclusività sui membri (WP8), divieto di rimuovere posizioni con candidature (WP7), conteggio delle candidature (WP7), sospensione e decisione delle segnalazioni (WP9).
- **WP6–WP10** — da fare.

---

## Appendice A — vocabolario v1 (proposta da approvare)

**Tipi soggetto** (→ id `beneficiari`):
- `impresa` (16), `micro_impresa` (22), `piccola_impresa` (26), `media_impresa`, `pmi` (27), `grande_impresa` (15);
- `startup_innovativa` (30), `pmi_innovativa`, `impresa_artigiana`, `cooperativa` (28), `impresa_sociale` (17, 8);
- `libero_professionista` (21), `organismo_ricerca` (31), `universita` (31);
- `ente_pubblico` (12), `ente_locale` (13), `ente_terzo_settore` (11), `associazione_categoria` (2, 24);
- `organismo_formazione` (23), `istituto_scolastico` (20), `istituto_cultura` (19), `fondazione`, `consorzio_rete_imprese`;
- `intermediario_finanziario` (4, 6, 18), `ente_sportivo` (3, 9, 14, 29), `persona_fisica` (25), `altro`.

**Competenze** (42):
- **Ricerca e innovazione**: ricerca_industriale, sviluppo_sperimentale, prototipazione_testing, trasferimento_tecnologico, proprieta_intellettuale.
- **Digitale**: sviluppo_software, intelligenza_artificiale_dati, cybersecurity, iot_elettronica_embedded, cloud_infrastrutture_it, automazione_industria40, marketing_digitale_ecommerce.
- **Energia e ambiente**: efficienza_energetica, energie_rinnovabili, economia_circolare, rifiuti_bonifiche, mobilita_sostenibile, edilizia_sostenibile, risorse_idriche.
- **Produzione**: meccanica_meccatronica, materiali_chimica, agroalimentare, tessile_moda_design, biotech_farmaceutica, dispositivi_medici_salute, aerospazio, nautica_trasporti, costruzioni_impianti, logistica_supply_chain.
- **Servizi**: formazione_competenze, consulenza_organizzativa, progettazione_rendicontazione_fondi, comunicazione_disseminazione, internazionalizzazione_export, servizi_sociali_welfare, turismo_cultura_creativita, servizi_finanziari.
- **Infrastrutture**: laboratorio_prove_accreditato, impianto_pilota, living_lab_sperimentazione, capacita_produttiva_scala, altro.

**Forme e checklist documentale** (base per tutte: NDA, lettera d'intenti, term sheet/MoU, dichiarazioni sostitutive):

| Forma | Documenti specifici | Costituzione e responsabilità |
|---|---|---|
| `ats` | mandato collettivo speciale, atto costitutivo | costituenda alla domanda, costituita prima della concessione; pro quota |
| `ati_rti` | impegno a costituire, mandato con scrittura privata autenticata | solidale; esclusione se si partecipa a più raggruppamenti |
| `rete_contratto` | contratto di rete, programma di rete | obbligazioni in capo ai singoli partecipanti |
| `rete_soggetto` | come `rete_contratto`, più fondo patrimoniale, organo comune, iscrizione al registro | — |
| `consorzio` | atto costitutivo e statuto | — |
| `accordo_partenariato` | accordo, mandato per atto pubblico o scrittura privata autenticata | nota FAQ MIMIT: ATS e RTI non sono beneficiari diretti |
| `consorzio_ue` | Consortium Agreement (DESCA), dichiarazioni delle affiliated entities, lettere degli associated partners | affiliati e associati non contano per il numero minimo |

**Ruoli**: capofila, partner.
