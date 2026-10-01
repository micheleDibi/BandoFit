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
  - WP1 e WP2 vanno senza il flag del modulo, ma le loro chiamate a pagamento stanno dietro un interruttore proprio, `BILANCI_STORICO_ATTIVO` (`Settings.bilanci_storico_attivo=False`, `MeOut.funzioni.bilanci_storico`), **spento di default**: niente storico IT-advanced (né nell'anteprima né in «Recupera i bilanci») e niente bilancio ufficiale, con le rotte relative in 404 (dipendenza `require_bilanci_storico_attivo` sulla singola rotta). L'import IT-full, la quota giornaliera, il legame della P.IVA e `GET /me/company/bilanci` restano attivi. L'accensione non presuppone la verifica in sandbox G1: le risposte reali di IT-advanced e del bilancio ottico restano da confermare con le prove su un'azienda propria (procedura in `docs/deploy.md`, «Accensione delle funzioni»); G1 resta consigliata. A storico acceso parte anche il failsafe del bilancio ufficiale (B10).
  - In produzione `docker-compose.yml` passa già al container `PARTENARIATI_ATTIVO` e `BILANCI_STORICO_ATTIVO`, con i default spenti: si accendono dal `.env` del server, con la procedura «Accensione delle funzioni» di `docs/deploy.md`.
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
  - Gli schemi passati al modello contengono solo tipi ed enum, senza vincoli numerici, perché un valore fuori range non deve far fallire una chiamata già pagata. I range si validano nel post-processing, che declassa la voce a `da_verificare`. Gli schemi con output strutturato strict devono restare piccoli (budget di dimensione fissato dai test): l'estrazione WP3, troppo grande per la grammatica strict, usa uno strumento forzato **non strict** con convalida tollerante nel codice (§16).
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
  - storico già recuperato per la stessa P.IVA (esito `ok` nello stato, stesso ambiente sandbox o produzione): si riusa gratis, senza chiamata né riga nel registro consumi. Il draft lo porta con esito `ok` e senza `tentato_at`, quindi l'anteprima lo mostra come recuperato e il cooldown di «Recupera» non parte. La conferma non riscrive né lo stato né le righe `it_advanced`. Se lo stato non si legge non si chiama (`saltato`, `errore_provider`);
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
  - `avanza` decide solo sulla riga riletta dopo il claim: il chiamante (follower, lettura, failsafe) può averne una copia vecchia. Nella riconciliazione l'id del provider della riga stessa non conta come «già usato», e prima di chiudere una riga `esito_ignoto` (rimborso «mai partita» o scadenza) la si rilegge ancora: si chiude solo se è ancora `esito_ignoto` senza id del provider. Così una richiesta già riconciliata e pagata non sembra mai «mai partita».
  - Richieste dell'altro ambiente openapi (colonna `sandbox`, dopo un cambio di `OPENAPI_ENV`): nessuna via le interroga o le chiude, perché il provider non le conosce e le chiuderebbe o rimborserebbe a torto. Restano aperte finché non si torna al loro ambiente, oppure si chiudono a mano. Lo stesso con openapi non configurato: né il failsafe, né il follower, né le letture le fanno avanzare o le chiudono a tempo (niente claim). Restano aperte finché il provider non torna configurato, così nessuna si chiude senza averlo sentito.
  - Failsafe `services/bilancio_ufficiale_scheduler.py`: task in-process avviato nel lifespan solo a storico acceso. Ogni 10 minuti fa avanzare fino a 100 richieste aperte, dalla più vecchia, con lo stesso `avanza` (claim compreso). Così completamento, rimborso e scadenza arrivano anche dopo un riavvio (che perde il follower) e senza che nessuno riapra la pagina. Esamina solo le richieste dell'ambiente openapi in uso; quelle dell'altro si contano nel log. Con openapi non configurato non parte (un WARNING): senza sentire il provider chiuderebbe come scadute richieste forse pronte e già pagate. Non solleva mai; nei log solo conteggi, id e codici.
  - «Ultimo disponibile» (anno assente): l'esercizio che il provider restituirà non si conosce prima di pagare. Se l'azienda possiede già un bilancio ufficiale da anno corrente − 2 in poi, la richiesta è rifiutata con 409 `bilancio_gia_presente`, senza spesa, e si chiede un anno esplicito. Posseduti: gli anni della fonte `xbrl` più l'anno (letto o richiesto) delle richieste `completata`, anche senza numeri leggibili; una completata ad anno ignoto vale come anno della richiesta − 1. Un anno esplicito già posseduto è rifiutato allo stesso modo. Il frontend non propone «Ultimo disponibile» negli stessi casi, tranne l'anno ignoto, per cui resta il 409.
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
  - più le voci del jsonb `allegati` della riga, sempre e senza doppioni per URL (dalla fase c del contratto DB bandi, §16; prima erano solo il ripiego su un errore di `bando_link`);
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
- **R7. Schema dell'estrazione** (strumento forzato non strict dal 2026-09-29, vedi §16): `modalita`, `forme_ammesse`, `costituzione`, `partner_min/max`, `composizione`, `quote`, `vincoli`, `regole_finanziarie` (contratto B7), `documenti_richiesti`, `fonti_insufficienti`. Ogni voce ha una citazione, verificata dal nuovo `services/citazioni.py`:
  - NFKC, apostrofi, sillabazione, soft hyphen;
  - pagine adiacenti ed ellissi;
  - `_citation_verified` dell'AI-check resta invariata.

  Le voci non verificate diventano `da_verificare`. **Fa fede solo il testo dei documenti ufficiali** (pagine `D<n>-p<m>`, `partenariato_regole._da_fonte_ufficiale`): una voce citata dalla scheda del catalogo (META, S1…) resta visibile con la sua citazione («Scheda del bando»), ma è `da_verificare` con l'avviso «Dalla scheda del catalogo: da verificare sul bando ufficiale». Motivo: il `contenuto` delle schede e le junction dei beneficiari sono testo generato o classificato dal produttore del catalogo, non estratto dall'atto (la generazione SEO ha scritto «in forma singola o associata» e «partenariato pubblico-privato» in schede i cui atti non lo dicono). `modalita_effettiva` è diversa da `non_determinabile` solo se la citazione della modalità è verificata **su un documento ufficiale**.
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
  - Ordinamento per gap coperti, poi punteggio, poi tie-break `sha256(call:azienda:settimana ISO)` (rotazione settimanale deterministica); nei suggeriti al massimo 2 aziende dello stesso owner sull'**intera lista** (dal WP7, non per pagina: un'azienda spostata a una pagina successiva rivelerebbe quali anonimi hanno lo stesso titolare), in «Per te» al massimo 2 call dello stesso owner per pagina.
  - I pesi sono `Settings` con default documentati (formule, pesi ed esempio guida nell'Appendice B).
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
| 0041 | `partenariato_moderazione_consulto` | 9 | `consultation_requests.partner_call_id` + i due indici `one_open`; `fn_create_consultation_request` ridefinita con la stessa firma e la verifica di appartenenza; colonne di decisione e ricorso sulle segnalazioni; RPC di decisione, presa in carico, ricorso, sospensione e ripristino; `fn_admin_metriche_partenariati`, `fn_admin_costi_partenariati`; **dalla decisione di Michele del 2026-09-29** anche la verifica dell'identità da parte dell'admin (`company_identita_verifiche`, `company_identita_stato`, RPC `fn_identita_*`, `fn_partenariato_identita_forte`, ridefinizioni di `fn_partenariato_rappresentante_ok`, `fn_partner_consenso` e `fn_partner_decidi`; vedi §16 e `docs/database.md`) | Coesistenza di consulto AI-check e consulto da call; call di un'altra azienda rifiutata; effetti atomici; ricorso unico entro 6 mesi; metriche su un seed noto |
| 0042 | `partenariato_bozze` | 10 | `partner_bozze_documento`; `subscription_plans.partner_bozze_mese` **default 0** + seed (Q7); `fn_partner_bozza_prenota` / `_concludi` / `_chiudi_stale` (come implementata: §16 WP10) | Limiti 0/N/NULL; una pending; un errore pagato conta |

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

- **Esito IT-advanced**: `ok | non_disponibili(nessun_bilancio) | errore | timeout(esito_incerto) | saltato(forma_senza_bilancio | tempo_insufficiente | piva_diversa | errore_provider) | mismatch`; `ok` senza `tentato_at` = storico già salvato, riusato nell'anteprima senza chiamata. Stato derivato dei bilanci: `mai_richiesti → disponibili | non_disponibili`.
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
| Prompt injection dai PDF | Output vincolato, citazioni verificate, snapshot confermato dal creatore come unica fonte deterministica, URL dei documenti (fonti e citazioni) solo https e ammessi dal filtro dei link della scheda, in lettura e quindi anche per le righe storiche (`link_policy.url_documento_pubblicabile`; ciò che non passa esce come `null`, il testo resta), testo LLM mostrato senza link automatici |
| Copertura documentale del 61% | Estrazione anche dal catalogo, «non determinabile» dichiarato, correzione nel passo «Regole del bando» |
| Deadlock tra RPC | Ordine di lock globale (owner → call → candidatura, profilo → inventario), testato con due connessioni |
| Proxy più corto della catena dei tempi | T7 + Q23 |
| Cold start (pochi opt-in) | «Per te» visibile anche senza opt-in, stati vuoti con CTA, digest solo con contenuto |
| Testi legali non ancora rivisti | Segnaposto marcati «BOZZA»; revisione del legale consigliata prima di accendere il flag (G4). Il flag si accende dal `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md` |

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
| 3 Regole citate, `modalita` ≥ 90% | test WP3 + **G3**: 67% alla prima misura reale, **96%** dopo il giro di qualità del 2026-09-29 (misura ottimistica: stesso campione usato per correggere; serve un secondo campione di verifica, §16), **68%** con la regola delle fonti ufficiali (le modalità citate solo dalla scheda del catalogo non valgono, §16): criterio di nuovo aperto |
| 4 Esempio guida end-to-end | test d'integrazione unico |
| 5 Collegate e senza opt-in mai; revoca immediata | matching + test d'integrazione |
| 6 Gratuito / limiti in RPC | test DB 0036-0039 |
| 7 Controlli verdi e documentazione | fine di ogni WP |

---

## 11. Sequenza, commit, migration e gate

1. **WP0** — commit `docs/partenariati.md` (questo piano + appendice A) + indice in `docs/README.md` + fixture del campione (solo id) + script sandbox (`backend/scripts/verifica_sandbox_openapi.py`, legge le chiavi dalle variabili d'ambiente e salva fixture anonimizzate).
2. **WP1** → ⚠️ 0032. **WP2** → ⚠️ 0033. Vanno senza il flag del modulo; storico IT-advanced e bilancio ufficiale restano spenti dietro `BILANCI_STORICO_ATTIVO` (§2.1 T1), già nel compose con default spento: si accende dal `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md`, e G1 resta consigliata prima.
3. **WP3** → ⚠️ 0034. **WP4** → ⚠️ 0035. **WP5** → ⚠️ 0036, 0037. **WP6** → ⚠️ 0038. **WP7** → ⚠️ 0039. **WP8** → ⚠️ 0040. **WP9** → ⚠️ 0041. **WP10** → ⚠️ 0042.

Regole di commit:
- uno o più commit per WP, con messaggio in italiano; ogni commit aggiorna `docs/` e `docs/changelog.md` («- ⚠️ Migration **00NN** da eseguire dallo SQL Editor del DB primario **prima** del deploy.»);
- autore solo Michele, nessun `Co-Authored-By`; `git add` per singolo file (mai `-A`);
- `Claude outputs/` e `docs/contratto-db-bandi.md` restano fuori.

Branch `feat/partenariati` da `main`: la spec qui prevale sul `claude/<…>` del CLAUDE.md globale.

Gate (non bloccano lo sviluppo, ma il rilascio sì):
- **G1** sandbox openapi: le tue azioni in §14, poi lanci lo script. Serve a sostituire le fixture sintetiche e a confermare i codici CEE; senza G1 il fix del mapping resta «verificato solo su OAS». G1 è consigliata prima di accendere `BILANCI_STORICO_ATTIVO`.
- **G2** migration nello SQL Editor prima di ogni deploy.
- **G3** credito Anthropic, poi `partenariato_valutazione --reale --locale --tetto-cents 800 --conferma --out <file fuori dal repo>` (modalità locale: nessuna scrittura su DB reali; tetto sulla somma delle esecuzioni).
- **G4** testi legali (informativa, Termini/DSA, disclaimer): revisione consigliata prima di accendere il flag in produzione.

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
| Q2 | Testo dell'informativa partner | Segnaposto versionato `2026-10-bozza-1` (dal completamento del WP9 `2026-10-bozza-2`) da far rivedere al legale. Deve coprire: dati mostrati, reveal dopo l'accettazione, referente, consulenti incaricati, collegamenti societari (HMAC), conservazione, diritti. A una nuova versione il consenso resta valido e compare un banner di riconferma (salvo diverso parere del legale) |
| Q3 | Prezzo dell'addon «Bilancio ufficiale» (costo 4,50 € + IVA) | **≥ 7,90 € + IVA**. Viene creato inattivo e lo attivi tu |
| Q4 | Destino del PDF del bilancio ottico | **(C)** in DB (`bytea`), tetto 8 MB, cancellato a cascata con l'azienda, download dal backend con autorizzazione live. L'XBRL si conserva sempre (serve a ri-parsare gratis), il verbale si scarta. Alternative: (A) scartarlo dopo il parsing; (B) bucket privato. Il round-trip di 8 MB via PostgREST lo verifico nel harness: se non regge passo a B |
| Q5 | Rimborso automatico (deviazione da 0028) | **Sì** per i rifiuti sincroni (278/213/275), per le richieste mai inviate (anche quelle dimostrate dalla riconciliazione dopo 30 minuti), per il credito del provider esaurito e per «Annullata». **No** per esito ignoto, scadenza ed errori di parsing: decidi tu con il grant |
| Q6 | Digest e provider email | Digest **settimanale dedicato**, con opt-out e token propri, solo per le aziende con opt-in. Email di evento (inviti, candidature, esiti, messaggi) attive di default con opt-out. In produzione **SMTP OVH** (circa 3.000 email al mese) |
| Q7 | Bozze AI | **Spostate in WP10**. `partner_bozze_mese` 0/3/10/30 (Gratuito/Smart/Pro/Advisor); gli errori pagati contano nel limite |
| Q8 | pgvector | **No** in v1: matching deterministico |
| Q9 | Verifica d'identità oltre alla coerenza con il registro (T5) | Per call nominative e reveal: il CF dell'utente verificato (`verify_cf`) deve comparire tra i legali rappresentanti in `company_people`. In alternativa basta T5. **Rivista in WP4** (§16): questa verifica è necessaria ma non sufficiente; il nominativo resta spento finché non c'è una prova di controllo dell'impresa. **Decisione di Michele del 2026-09-29 (WP9)**: la prova è una **verifica manuale dell'admin** (telefonata alla sede, documento del legale rappresentante, PEC dell'azienda, altro), chiesta dal titolare con i dati del registro coerenti (T5), registrata in un registro append-only e revocata da sola al cambio di P.IVA o ragione sociale, all'eliminazione o all'archiviazione dell'azienda. «Identità forte» = verificata e T5 ancora valido. Sblocca il profilo nominativo e la **rivelazione simmetrica** (solo se entrambe le aziende sono verificate, ricontrollato nella RPC); le viste future mostrano l'identità solo se oggi è ancora verificata. Le call nominative si accettano solo da aziende verificate ma non hanno ancora una proiezione con il nome verso terzi (§16, WP9). Il CF del titolare tra i legali rappresentanti resta solo informativo |
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
3. **Storico e bilancio ufficiale**: `BILANCI_STORICO_ATTIVO` è già nel compose, con default spento. Si accende dal `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md`, con G1 consigliata prima. Dopo l'accensione, prezzo e attivazione dell'addon `bilancio-ufficiale` in AdminAddon (ha senso solo a storico acceso).
4. Per il modulo partenariati: le migration 0034–0042 in ordine, **prima del deploy e anche a modulo spento**, perché il backend legge colonne della 0036, 0041 e 0042 qualunque sia il flag (con la 0032 e la 0033 del punto 2: tutte dalla 0032 alla 0042, in ordine). `PARTENARIATI_ATTIVO` è già nel compose con default spento: il modulo si accende dal `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md`. Dopo la 0036, in AdminPiani (i campi compaiono a modulo acceso), i limiti di partenariato dei piani diversi da Gratuito, Smart, Pro e Advisor (per esempio `tailored`): partono da 0, cioè senza call né candidature; dopo la 0042 lo stesso per «Bozze di documenti al mese».
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
  - «ultimo disponibile» rifiutato (409) se l'azienda possiede già un bilancio ufficiale da anno corrente − 2 in poi (all'inizio solo l'ultimo esercizio chiuso; regola estesa in seguito, vedi B10);
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
- **WP6 (fatto)** — matching, «Per te», bacheca, suggeriti, notifiche proattive e digest (migration 0038). Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **punteggio interno**: ordina le liste e decide le notifiche ma non esce dalle API in nessuna vista (nel `MatchOut` restano copertura, requisiti coperti e non coperti, voci da verificare, posizioni, fasce e spiegazione; la UI non mostra più un'«affinità» numerica). Affinità, complementarità e rotazione usano dati della controparte che la sua scheda non mostra (competenze e sedi del creatore, notifiche ricevute dal candidato): un numero esatto, ricalcolato al variare del proprio profilo, li farebbe ricostruire. Per la stessa ragione il filtro per regione della bacheca usa solo la sede mostrata sulla card e le regioni dei requisiti visibili e delle posizioni;
  - filtri rigidi: in testa `call_non_attiva` (call non pubblicata, sospesa o scaduta, bando scaduto, creatore non vivo); tra i vincoli di ogni membro escludono **solo** territorio, paese e dimensione (elenco chiuso di M1): gli altri requisiti `ogni_membro` non coperti (tag, certificazioni, esperienze, ATECO, settore, tipo di soggetto) sono voci «da verificare» con la penalità, perché per un dato dichiarato «non coperto» vuol dire «non dichiarato»;
  - collegamenti: oltre a versione e import, il marker vale solo se le chiavi d'identità salvate sono quelle che l'anagrafica dell'azienda genera adesso con la chiave corrente (P.IVA e ragione sociale nell'indice, anche il codice fiscale nel backfill): un'identità corretta dalla scheda dell'azienda senza un nuovo import o una rotazione di `RATE_LIMIT_PEPPER` escludono l'azienda finché il backfill non ricalcola. «Per te» senza opt-in: le chiavi di un'azienda senza visibilità e senza call non si calcolano (M2), quindi per lei l'esclusione delle collegate vale da quando attiva la visibilità o pubblica una call (la scoperta non permette contatti);
  - vista pubblica: `GET /partenariati/call/{id}` porta già il confronto dell'azienda attiva, con i suoi numeri (`match`), lo stato «salvata» e `opt_in`; resta anche `GET …/match`. Una call scaduta ma non ancora chiusa dallo scheduler è 404 per le altre aziende, senza scritture; l'admin la vede come un visitatore;
  - verso il creatore i suggeriti hanno lo pseudonimo della call (HMAC di call e codice pubblico, 16 caratteri base32) e il profilo pubblico **senza** `codice_pubblico`; le call solo su invito non compaiono mai in «Per te» (gli inviti arrivano con il WP7) e non generano notifiche proattive, mentre tra i loro suggeriti restano solo le aziende che accettano inviti;
  - fan-out: si dichiara completato solo se ha valutato la call senza errori; una call fuori dall'indice (per esempio bando momentaneamente assente dal catalogo) o con i collegamenti del creatore non ancora calcolati resta pendente e la riprende lo scheduler, dopo il backfill dei collegamenti; alla ripresa le aziende già reclamate da un giro interrotto ricevono la notifica in modo idempotente;
  - chi segue una call riceve «modificata» solo se la scrittura crea una nuova versione, cambia la proiezione pubblica (non per budget esatto, dettagli riservati, quota del creatore o requisiti non visibili) e la call è visibile a tutti prima e dopo; «chiusa» solo se la vedeva;
  - digest: ogni destinatario è isolato (un errore conta nel riepilogo e non ferma gli altri) e le notifiche si marcano incluse subito dopo un invio riuscito; un Advisor riceve una sola email con una sezione per azienda, un membro solo le aziende che vede; le letture dei destinatari sono a blocchi di 100 owner;
  - il badge del menu conta le call «Per te» pubblicate negli ultimi 7 giorni, non le non lette (testo e lettore di schermo lo dicono);
  - nei log degli invii email (tutte le email, non solo il digest) l'indirizzo è mascherato e l'errore del provider riporta solo la classe e il codice;
  - prerequisiti dal WP4-WP5 chiusi qui: gli intervalli aperti delle fasce (budget `oltre_5m` contro una fascia di bilancio aperta) non sollevano più ma danno un esito prudente (dato mancante); il controllo anti-contatti sulla forma canonica e sul nome scritto attaccato vive in `partenariato_anonimato` e vale anche per il profilo partner, i cui testi si salvano senza caratteri invisibili;
  - esempio guida: fixture sintetica con i soli codici del vocabolario v1, verificata sulla parte pura, sui servizi (indice, fan-out, digest su un primario finto) e sulle API (Appendice B); la parte con candidature, quote e piano Gratuito del test d'integrazione di §10 arriva con WP7 e WP8. Benchmark opzionale (`BANDOFIT_BENCH=1`, 500 aziende e 200 call);
  - `eventi_abilitati` c'è già nelle preferenze, ma le email di evento arrivano con il WP7.
- **WP7 (fatto)** — candidature, inviti e chat (migration 0039). Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **rivelazione dell'identità spenta** (come il profilo nominativo del WP4, per la stessa ragione): implementata e coperta dai test, ma la costante `RIVELAZIONE_IDENTITA_DISPONIBILE` la tiene disattivata. Di K2 restano la conversazione, i dettagli riservati e il budget esatto per la controparte; identità e contatti no: le aziende restano anonime l'una per l'altra, la chat ha un banner fisso sull'identità non verificata e la RPC non scrive l'audit di rivelazione. Accesa, l'identità esce solo per le accettazioni con quell'audit;
  - revoca dell'opt-in: `fn_partner_consenso` non si ridefinisce (§3 lo prevedeva); un trigger AFTER INSERT su `partner_consents` per le righe `revocato` chiude inviti e candidature in attesa, così vale anche per la revoca automatica di sistema. Anche la chiusura della call chiude i pendenti con un trigger, da qualunque percorso;
  - chi agisce: solo il titolare dell'azienda attiva, chat compresa (Q14); i membri leggono e segnano come letti (la lettura è per utente);
  - invito solo con lo **pseudonimo della call**, risolto sull'indice e accettato solo se l'azienda è ancora tra i suggeriti di quella call e, al ricontrollo in tempo reale, visibile e disponibile agli inviti; ogni altro caso risponde `partner_non_disponibile` (codice neutro unico). Un invito **rifiutato** non si ripete sulla stessa call (l'azienda può sempre candidarsi); dopo un ritiro o una scadenza sì. I suggeriti portano lo stato del contatto (`stato_contatto`);
  - limite per owner (prerequisito dalle revisioni del WP6): al massimo 2 aziende dello stesso owner sull'**intera lista** dei suggeriti, prima dei filtri facoltativi (M3 e Appendice B aggiornati); le notifiche proattive invece non hanno il limite, come nel WP6, perché non si mostrano al creatore;
  - accesso alla call: i ruoli `candidato` e `invitato` valgono solo per una riga **in attesa** (e, se invito, non scaduta): una riga chiusa non apre più una call solo su invito, e la propria candidatura chiusa si vede con la call solo finché questa è visibile a tutti; la controparte accettata vede la vista con i riservati anche se il creatore non è più attivo, ma non una call sospesa per moderazione; nelle liste di candidature e conversazioni titolo della call, titoli delle posizioni ed etichette dei requisiti escono senza gli identificativi del creatore, come nella vista pubblica;
  - «azienda viva» per la controparte, nelle RPC di messaggi e accettazioni, è la stessa del backend (non eliminata né archiviata, titolare attivo): la sola lettura della chat e la RPC dicono la stessa cosa;
  - esclusività controllata anche alla **pubblicazione** (C6): la 0039 ridefinisce anche `fn_partner_call_pubblica` con la stessa firma (non era nel contratto del WP7), con lo stesso lock advisory per azienda × bando dell'accettazione. Una candidatura accettata su una call **annullata** non impegna più (un'accettata non si ritira); su una completata, scaduta o sospesa sì. L'indice del matching applica la stessa regola, simmetrica (`impegni_esclusivi`);
  - posizioni: la 0037 cancellava e reinseriva tutte le posizioni, quindi `fn_partner_call_sostituisci_posizioni` è ridefinita con la stessa firma (aggiorna per id, cancella solo le omesse, rifiuta di togliere una posizione con candidature attive) invece di un trigger su DELETE;
  - candidatura: messaggio da 50 a 2000 caratteri e motivo del rifiuto controllati contro recapiti e identificativi della propria azienda; requisiti dichiarati solo tra quelli visibili della call, con gli id in `requisiti_dichiarabili` del dettaglio (i requisiti pubblici non hanno id), così si dichiarano anche senza confronto; il confronto in vista «terzi» si salva all'invio e un'azienda esclusa da un filtro rigido può candidarsi (il creatore vede solo che non risultava compatibile, mai il motivo). Il creatore vede il profilo pubblico solo nel dettaglio della candidatura («Vedi il profilo»);
  - quota: candidature spontanee del mese solare, in qualunque stato, su tutte le aziende del titolare; una sola funzione per la RPC e per `/me/entitlements` (ridefinizione di `fn_partenariati_snapshot`); limiti anti-abuso per utente (candidature, inviti, messaggi) fail-open;
  - rotazione: le esposizioni contano anche gli inviti ricevuti negli ultimi 7 giorni ancora in attesa o accettati, letti insieme alle candidature attive dentro il budget di query dell'indice;
  - notifiche ed email di evento: in-app sempre, email solo con `eventi_abilitati` e recapitabili, mai testi di messaggi, candidature o motivi; i link di un rifiuto portano chi aveva mandato la riga alla sotto-lista delle inviate; per i messaggi un avviso per raffica;
  - chat (frontend): polling a scheda visibile, la cache dei messaggi si butta all'uscita dalla pagina e un polling fallito non smonta thread e composer;
  - deep link nel wizard della call (prerequisito dalle revisioni del WP6): `useAziendaDaLink` e redirect con la query string;
  - `candidature_ricevute` delle card della bacheca resta 0: il conteggio non è ancora collegato;
  - esempio guida esteso: Y si candida e X accetta (conversazione, audit, primo messaggio); con Y sul Gratuito l'invito passa, la candidatura spontanea no (criterio 6 di §10).
- **WP8 (fatto)** — consorzio della call e validatore deterministico (migration 0040). Nessuna chiamata a modelli. Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **membri**: il creatore entra nel consorzio alla pubblicazione (capofila se `ruolo_creatore = capofila`, quota = `quota_creatore_pct`), Y all'accettazione (proposto, con la posizione della candidatura e la sua quota prevista; capofila solo se la posizione lo è e il posto è libero); backfill idempotente per i dati precedenti. Esterni (Q20) al più 30 membri non usciti e 60 righe esterne per call; il nome di un esterno non ammette contatti né identificativi del creatore;
  - **stati in cui il consorzio si modifica**: pubblicata, **scaduta** e chiusa come completata (il contratto diceva solo pubblicata e completata: alla scadenza della ricerca di partner, stato terminale per Q18, il partenariato può andare avanti e i documenti delle fasi successive vanno aggiornati); annullata o sospesa no. Il budget si modifica solo con la call pubblicata (stessa RPC e stessi effetti del PATCH). Un'azienda esce da sé in qualunque stato;
  - **conferma sui termini visti**: la conferma porta ruolo, posizione e quota mostrati dalla pagina e la RPC li confronta con la riga bloccata (`409 membro_modificato`): una modifica del creatore, anche concorrente, non viene confermata da un clic su una pagina vecchia. Una posizione assegnata a un membro non uscito non si toglie (`posizione_con_membri`, ridefinizione con la stessa firma di `fn_partner_call_sostituisci_posizioni`), così la posizione confermata non cambia senza una nuova conferma;
  - **partner associati**: non ricevono budget, quindi confermano anche senza quota e non entrano né nella somma delle quote né nelle quote per categoria; contano invece nell'indipendenza. Le entità affiliate restano fuori dall'indipendenza (collegate per definizione al beneficiario), unica eccezione a «tutte le coppie»;
  - **uscita**: chi esce, da sé o tolto dal creatore, non è più controparte (niente consorzio, dettagli riservati né budget esatto; la conversazione resta) e non è più impegnato sul bando; la candidatura resta accettata (un'accettata non si ritira) e la riga uscita prevale;
  - **esclusività estesa** (`fn_partner_esclusivita_violata`, stessa firma): conta anche essere membro non uscito di un'altra call non annullata e, per il creatore, avere un'altra call non annullata con altri membri non usciti (il partenariato è partito; una call scaduta senza partner non blocca). Stessa regola nel validatore e nell'indice del matching (che la ricava dalle candidature accettate: un consorzio con soli esterni lo ferma la RPC);
  - **proiezione per destinatario**: membri in piattaforma sempre anonimi tra loro (pseudonimo della call e profilo anonimo, anche se il profilo è nominativo: la rivelazione è spenta), il creatore «Azienda anonima» senza pseudonimo; degli altri membri solo esiti sulle fasce, mai valori; verso chi non ha creato la call la matrice mostra solo i requisiti visibili ai terzi e le note dei documenti escono senza gli identificativi del creatore. I membri non più attivi in piattaforma (azienda eliminata o archiviata, titolare disattivato) restano nella lista, senza profilo pubblico e senza valutazioni sui loro dati;
  - **validazione salvata** sulla call (esito nella vista del creatore e copertura dei requisiti cercati) dopo ogni scrittura sul consorzio e in lettura se è cambiata, ma mai sopra un valore salvato dopo l'inizio della lettura; notifiche in-app `partenariato.consorzio_aggiornato` alla richiesta di una nuova conferma, alla rimozione e all'uscita;
  - **checklist documentale**: documenti di base e della forma prevista dal vocabolario (appendice A) più quelli richiesti dal bando nello snapshot; lo stato lo cambia solo il creatore, le note non ammettono contatti;
  - **prerequisiti** chiusi qui: «azienda viva» con il titolare attivo anche in `fn_partner_call_aperta` e nel controllo del backend; `candidature_ricevute` collegato nelle card (candidature spontanee in attesa o accettate, una lettura per pagina fuori dall'indice). Non fatto, facoltativo: le esposizioni dei 7 giorni non contano gli inviti già chiusi (servirebbe una lettura oltre il budget di query dell'indice);
  - **esempio guida, punto 5**: il piano è incoerente se la call ha anche il requisito F (costo della quota ≤ 60% del fatturato medio, per ciascun partner). In produzione un requisito finanziario nasce solo da una regola confermata dello snapshot; con la regola F confermata e il budget della guida (3,1 M€) il 70/30 **non può essere verde** (X: 2,17 M€ / 3,2 M€ = 0,68). I test del punto 5 usano quindi la call senza il requisito F, coerente con le regole del punto 5 (almeno 2 partner, un organismo di ricerca, una PMI, quote 10-70%), e un test a parte documenta il rosso con F;
  - annotati, non risolti: un'azienda uscita (o tolta) non rientra nella stessa call, perché la sua candidatura resta accettata e occupa il posto dell'unica candidatura attiva (il ramo di riammissione della RPC di decisione resta pronto); dopo un'accettazione l'esito salvato sulla call si aggiorna alla prima apertura del consorzio, non subito; durante una sospensione per moderazione la call non si raggiunge, nemmeno per uscire dal consorzio.

  **Controlli del validatore** (`services/partenariato_validatore.py`, voci verde / rossa / grigia, «Il bando non lo indica» → grigio):
  - `numero_partner`: minimo e massimo del bando su capofila e partner (entità affiliate e partner associati non contano); senza un minimo, controllo di coerenza «almeno 2 partner, capofila compreso»;
  - `composizione`: per ogni voce del bando, i membri col tipo di soggetto, il ruolo e il vincolo territoriale (sì / no / da verificare: un capofila vale forse tra i «partner», affiliati e associati forse tra i soggetti «qualsiasi»); esito certo solo se lo è con qualunque scelta dei «da verificare»; esterni con i tipi dichiarati, marcati «dichiarato»;
  - `somma_quote`: 100% con tolleranza 0,01 sui membri che ricevono budget; una quota mancante → grigio;
  - `quota_partner`, `quota_categoria` (somma certa e possibile dei membri della categoria), `quota_capofila`; una regola che fa perdere solo una maggiorazione è grigia, non rossa; con una categoria, `quota_partner` e `quota_capofila` valgono solo per i membri di quel tipo (vedi più sotto, quote del partenariato);
  - `indipendenza` su tutte le coppie di membri in piattaforma tranne le entità affiliate: collegamento certo → rosso (grigio se il bando non chiede l'indipendenza), possibile → grigio, collegamenti non verificati o membri esterni → grigio «da verificare con visura»; mai quale socio o esponente collega due membri;
  - `paesi_distinti`: paesi di capofila e partner contro il numero richiesto (paese non noto o scritto per nome → da verificare);
  - `regola_finanziaria` per ciascun partner (sulla quota di ogni beneficiario) o per il capofila, con il costo della quota = quota × budget (esatto se indicato, altrimenti l'intervallo della fascia): esatta per il membro stesso, sulle fasce per gli altri; `partenariato_totale` → grigio (a mano);
  - `media_pesata`: solo se ogni membro ha la quota; verde se tutti i membri rispettano la regola su tutto il loro intervallo, rossa se nessuno, altrimenti grigia; verso tutti solo l'esito;
  - `esclusivita`: un membro con un impegno su un'altra call dello stesso bando quando una delle due è esclusiva → rosso; impegni non letti → grigio; call esclusiva con membri esterni → grigio;
  - `vincolo_membro`: i requisiti della call «di ogni membro» su ogni beneficiario (regione con la regola «tutte le sedi»); quelli finanziari una volta sola, dalla voce dello snapshot se la regola c'è, altrimenti qui;
  - `vincolo_da_verificare`: vincoli del bando solo a testo (requisito del capofila, altro) e la sede in una regione se nessun requisito «di ogni membro» sulla regione la traduce; la scadenza di costituzione no (è una data, la ricorda la checklist dei documenti);
  - `membri_attivi`: un'azienda del consorzio non più attiva in piattaforma → grigio;
  - `regole`: snapshot delle regole assente → grigio, con i soli controlli di coerenza.

  Esito complessivo: rosso se c'è un rosso, altrimenti grigio se c'è un grigio, altrimenti verde; quello salvato sulla call è nella vista del creatore. Matrice di copertura con la stessa `valuta_criterio` dei suggerimenti (vista «proprio» nella colonna di chi guarda, «terzi» nelle altre).

  **Regola di origine** di ogni voce (rifinitura dopo il WP10, regola delle fonti ufficiali del WP3): «Regola del bando», con il passaggio, solo per una voce `confermata` dello snapshot o un requisito generato dalle regole o dall'AI-check con la citazione verificata da una pagina di un documento ufficiale (`D<n>-p<m>`, `partner_call_gap.da_pagina_ufficiale`); voci modificate o aggiunte, voci citate dalla scheda del catalogo (anche in uno snapshot confermato prima della regola), requisiti manuali e pre-check del catalogo sono del creatore.
- **Estrazione WP3 rivista dopo la prima valutazione reale (G3, 2026-09-29)**:
  - lo schema di output strutturato strict dell'estrazione è rifiutato dall'API («compiled grammar is too large», nessun limite numerico documentato); anche ridotto (v2: nessun campo nullable, codici come stringhe mappate sul vocabolario nella post-elaborazione, 5 enum piccoli) resta rifiutato, mentre gli schemi WP4 e WP5 ridotti sono accettati;
  - l'estrazione usa quindi **uno strumento forzato non strict** (`anthropic_ai.estrai_con_strumento`, `tool_choice` sullo strumento, niente `output_config`): lo schema guida il modello ma nessuna grammatica si compila; il JSON restituito si convalida in modo **tollerante** (`convalida_tollerante`: campi mancanti → assenti, conversioni semplici, valori presenti ma non leggibili segnalati e mai trasformati in «assente», voci con codici chiusi ignoti scartate con avviso, risposta vuota o senza campi dell'estrazione = risposta non valida pagata); la post-elaborazione deterministica (citazioni, range, vocabolario, `modalita_effettiva`) è invariata;
  - WP4 (bozza profilo) e WP5 (posizioni, testi) restano con output strutturato strict, con schemi ridotti; un test fissa il budget di dimensione di ogni schema;
  - un errore 4xx del provider non transitorio senza usage (richiesta rifiutata prima della generazione) chiude a **costo 0** (`ai_richiesta_rifiutata`) invece della riserva;
  - nuova valutazione **locale** (`--reale --locale`, nessun DB primario, spesa fail-closed in memoria, stop al primo errore non transitorio o dopo 3 errori consecutivi) e **sonda degli schemi** (`backend/scripts/sonda_schemi_ai.py`: una chiamata minima per schema per sapere se l'API lo accetta);
  - **prima misura reale** (25 bandi, 23 con il modello, 2,87 $, 11 cent e 29 s in media per bando): `modalita` corretta nel **67%** (obiettivo 90%), `partner_min`/`partner_max` esatti al 92%, citazioni verificate all'83%, quote con molti falsi positivi. La modalità grezza del modello coincide con l'etichetta in 20 casi su 24: la differenza nasce da un controllo di coerenza che scatta quando il modello scrive a parole l'assenza di un conteggio, da 3 citazioni non ritrovate nel testo e da 4 «non ammesso» dedotti da elenchi di beneficiari senza un'esclusione esplicita. Correzioni fatte nel giro di qualità qui sotto.
  - **giro di qualità** (prompt v2): conteggi assenti scritti a parole o come virgolette vuote trattati come assenti (il modello scriveva letteralmente `""`), «non ammesso» valido solo se la frase citata contiene un'**esclusione esplicita** della forma associata (non bastano un elenco di beneficiari o «una domanda per impresa»), definizione stretta delle quote (le intensità di aiuto, i cofinanziamenti e le soglie di spesa non sono quote: segnalate e da verificare, mai scartate), citazioni verificate anche con gli spazi spuri che il lettore dei PDF inserisce e a cavallo tra la pagina precedente e quella indicata (mai parafrasi); la valutazione locale salva l'output grezzo del modello e le sezioni inviate (fuori dal repo) e `--rivaluta` ripete convalida, post-elaborazione e metriche **senza chiamare il modello**;
  - **misura dopo il giro di qualità** (25 bandi, 2,81 $): `modalita` corretta nel **96%** (24/25; «obbligatorio» 7/7, «non ammesso» 1/1), `partner_min` 96%, `partner_max` 88%, citazioni verificate al 92%, quote precision 0,50 e recall 0,38. **Misura ottimistica**: le regole sono state corrette guardando lo stesso campione; serve un secondo campione di verifica prima di considerare chiuso il criterio 3. Le 6 etichette dubbie (18145, 18177, 18278, 171905, 18446, 907910) sono state validate il 2026-09-29 con analisi delle fonti ufficiali e verifica avversaria indipendente: modalità e conteggi confermati, corrette solo le forme di 18177, 18446 e 907910 (le metriche non cambiano). Le altre 19 etichette restano quelle compilate in WP3.
  - **regola delle fonti ufficiali** (2026-09-29): il produttore del catalogo ha confermato che il `contenuto` delle schede (sezioni S1…, META) e le junction dei beneficiari sono testo **generato o classificato**, non estratto dall'atto: la generazione SEO ha inserito «in forma singola o associata» e «partenariato pubblico-privato» in schede i cui atti non lo dicono. Una citazione ritrovata solo nella scheda non rende più verificata la voce: una sola regola per tutte le voci con citazione (`_da_fonte_ufficiale`: sezione `D<n>-p<m>` nel formato di `normalizza_sezione`), la voce resta visibile con la citazione («Scheda del bando», `verificata` = ritrovata alla lettera) ma è `da_verificare` con l'avviso «Dalla scheda del catalogo: da verificare sul bando ufficiale»; `modalita_effettiva` vale solo con la citazione della modalità su un documento ufficiale. Estrazione e prompt invariati. A valle: il filtro «Ammette/Richiede partenariato» non vede più questi bandi, nel wizard WP5 le voci entrano nello snapshot solo come `modificata` (mai `confermata`: la conferma vuole `stato` `verificata`, anche lato server), validatore e matching leggono lo snapshot confermato; un requisito generato da una voce `modificata` (`partner_call_gap.requisiti_da_regole`) porta la citazione come riferimento ma con `verificata` falso, quindi il validatore lo attribuisce al creatore e non come «Regola del bando», come già per le voci dello snapshot;
  - **rivalutazione senza modello** della stessa misura reale (`--rivaluta` sull'output grezzo salvato): `modalita` dal 96% al **68%** (17/25; precision di «obbligatorio», «ammesso», «non ammesso» sempre 1,0, recall 0,71, 0,45 e 1,0), conteggi e quote invariati, citazioni ritrovate invariate al 92% (ma 40 su 197 stanno nella scheda), voci verificate da 172 a 132 su 197. Cambiano 8 bandi: in 7 (18117, 18455, 18351, 18362, 18460, 18559, 18495) la modalità, giusta, era citata dalla scheda e ora è `non_determinabile`; in 18207 una voce diventa da verificare. In 4 di questi (18117, 18455, 18351, 18362) le pagine ufficiali inviate parlano di partenariato ma il modello ha citato la scheda; 18559 e 18495 non hanno pagine ufficiali lette. Nessun bando è finito in una modalità sbagliata. Le leve per tornare sopra il 90% sono far citare al modello i documenti ufficiali (prompt, non cambiato qui) e leggere più atti. Le estrazioni già salvate conservano il giudizio precedente finché non si rifanno (una riverifica con lo stesso testo inviato chiude `riusata` e non ripassa dalla post-elaborazione: il ramo `riusata` di `fn_partenariato_concludi` non scrive `regole` né `modalita_effettiva`). Per riallinearle serve una nuova estrazione (`forza`, a pagamento) o una migration che faccia riscrivere al ramo `riusata` le regole ricalcolate dal servizio; oggi riguarda solo i DB di sviluppo e prova, il branch non è in produzione.
  - **quote del partenariato, giro di qualità** (2026-09-29): nessun cambio di versione (prompt 2, schema e vocabolario invariati, così `--rivaluta` confronta la stessa cosa), nessuna migration, percorso `riusata` invariato. Una quota non si scarta mai: quando è dubbia resta visibile come `da_verificare`, con un avviso.
    - **metrica principale `quote_usate`**: precision e recall sulle sole quote `verificata`, cioè quelle che il wizard della call preseleziona; sta accanto a `quote` (tutte le quote, ora metrica secondaria) in `metriche` e in `metriche.raggiungibili`, ed è la prima nelle righe «Prima/Dopo» di `--rivaluta`. Motivo: `quote` contava anche le quote da verificare, che il prodotto non usa;
    - **esclusioni forti**: una percentuale che il passaggio citato riferisce a una quota di adesione o d'iscrizione, a una quota associativa, a un cofinanziamento, a un'intensità di aiuto, al contributo concesso («contributo (pubblico) concesso», anche «è/viene/sarà concesso») o a un contributo a fondo perduto non ripartisce il costo del progetto tra i partner. La quota resta `da_verificare` con l'avviso delle non-quote, anche se la frase nomina il partenariato; «quota di partecipazione» resta una quota;
    - **la percentuale deve comparire nel passaggio citato**: in cifre con «%» o «per cento» (virgola decimale ammessa, tolleranza 0,01; accettato anche il numero ripetuto in lettere tra parentesi, «30 (trenta) per cento») oppure come frazione (tolleranza 0,5); gli intervalli «tra il X e il Y%» (anche «ed il»), «dal X al Y%», «X-Y%» valgono per entrambi i numeri; se manca, la quota è `da_verificare` con l'avviso «La percentuale non compare nel passaggio citato»;
    - **frase della citazione ed ellissi**: la frase che contiene il passaggio citato si ricostruisce dal documento con le stesse tolleranze della verifica della citazione (spazi spuri del PDF, trattini, virgolette e punteggiatura ai bordi); un'ellissi iniziale o finale si ignora, una interna vale solo se i frammenti stanno nella stessa frase. Se la citazione è ritrovata ma la sua frase non si ricostruisce, con o senza ellissi, i controlli sul solo frammento potrebbero saltare l'esclusione o il soggetto: la quota resta visibile ma `da_verificare`, con l'avviso «Passaggio citato non ricostruibile per intero». Il recupero dai vincoli usa solo frasi ricostruite dal documento;
    - **confini di frase**: nella ricostruzione tollerante della frase citata anche gli a capo contano. Una riga di elenco («•», «-», «a)», «1.») o un titolo riconoscibile («Art. N», «Capo/Titolo/Sezione/Allegato N», righe numerate come «2.1 Beneficiari», righe tutte maiuscole) chiude la frase anche senza punto, mentre una riga di prosa che va a capo resta nella stessa frase;
    - **frazioni**: per il minimo e il massimo delle quote si leggono `2/3`, `1/2`, `1/3`, `3/4`, `1/4`, `1/5` e «metà», «un terzo», «due terzi», «un quarto», «tre quarti», «un quinto», convertite a due decimali (due terzi → 66,67); non valgono come frazione un ordinale seguito da un soggetto o da una sequenza («un terzo soggetto», «un quarto lotto») né «metà» in senso temporale («a metà del periodo»);
    - **deduplica**: le quote della stessa regola, per esempio citate da due documenti, si mostrano una volta sola, con l'avviso globale «Una quota compariva in più documenti: è mostrata una volta sola.» (una volta sola, senza id interni). Stessa regola vuol dire stessi ambito, categoria, minimo e massimo, con base di calcolo ed effetto della violazione compatibili: uguali, oppure non indicati in una delle due; due quote restano separate se base o effetto sono indicati in entrambe e diversi. Una quota entra in un gruppo solo se è compatibile con tutti i suoi membri (la compatibilità con «non indicato» non è transitiva); di ogni gruppo resta la voce verificata, poi la più completa (base ed effetto indicati), a parità la prima. Si deduplicano solo le quote confrontabili: almeno una percentuale letta, nessuna percentuale illeggibile, nessuna categoria «altro» e, per `per_categoria`, la categoria presente. Le altre si tengono tutte, così nessuna voce diversa si perde;
    - **recupero dai vincoli**: un vincolo di tipo «altro» diventa anche una quota per partner (solo il massimo, arrotondato a due decimali; id `Q-<id del vincolo>`, sempre unico e al massimo di 20 caratteri) quando la sua citazione è verificata su un documento ufficiale, la frase ha la forma stretta «nessun(a) impresa/partner/soggetto/… sostiene (da sola) più di X» o «oltre X» (X in percentuale, «per cento» o frazione), si riferisce al costo o alle spese complessive del progetto (totale, complessivo, ammissibili, progetto, budget) (un limite su una singola voce di spesa, come consulenze o personale, non è una quota), non contiene parole di aiuto né le esclusioni forti, non contiene un'eccezione («ad eccezione di», «salvo», «tranne», «eccetto») né un obbligo negato («nessun partner è tenuto a…», «obbligat…»: non è un tetto) e non c'è già una quota con gli stessi ambito, categoria, minimo e massimo (stessa tolleranza di 0,5 punti della metrica; base ed effetto qui non contano, per non creare doppioni). La provenienza va negli avvisi globali («Una quota è stata ricavata da un vincolo del bando (limite per singolo partner).», una volta sola, senza id interni); la quota ricavata è comunque SEMPRE `da_verificare`, con l'avviso «Ricavata da un vincolo: da confermare»: si vede e chi crea la call può confermarla, ma non viene mai preselezionata. Il vincolo resta com'è;
    - **categoria rispettata** sulle quote `per_partner` e `capofila`: nel validatore del consorzio (WP8) la quota per partner con una categoria vale solo per i membri di quel tipo (se nel consorzio non ce n'è nessuno la voce è verde, «Nel consorzio non ci sono partner di tipo «X»»), e un membro di tipo incerto dà grigio solo se la sua quota manca o viola i limiti (come i membri incerti di `per_categoria`); la quota del capofila con una categoria è grigia se il capofila non è di quel tipo o se il suo tipo è incerto, anche con la quota nei limiti, perché la regola può presupporre un capofila di quel tipo. Titoli «Quota di ogni partner: X» e «Quota del capofila: X»; codici delle voci invariati. Negli avvisi sulle posizioni proposte dall'AI (WP5) minimo e massimo per partner con una categoria valgono solo per le posizioni di quel tipo, con un avviso condizionale quando il tipo non è certo: posizione senza tipi o con altri tipi insieme, «altro», tipi che si sovrappongono (tra i tipi di impresa, liberi professionisti ed enti del Terzo settore; università e organismi di ricerca; enti locali, scuole e università rispetto agli enti pubblici; fondazioni e associazioni rispetto agli enti del Terzo settore) e la quota di chi crea la call. Nel wizard l'editor della quota non azzera più la categoria per `per_partner` e `capofila`, e il titolo della quota la mostra;
    - **etichette 18351 e 18207 rivalidate** (analisi delle fonti ufficiali e verifica avversaria, 2026-09-29): in 18351 la quota delle grandi imprese è per partner con categoria (non per categoria) e tra le forme entrano ATS e ATI/RTI; in 18207 `partner_min` è 2;
    - **limiti noti** (di copertura, da riprendere): la ricostruzione della frase e i controlli sul passaggio citato non coprono ancora le eccezioni scritte in forme diverse da quelle riconosciute, le premesse di elenchi separate da «;» («come segue: a) …; b) …»), le citazioni ripetute nella stessa pagina (conta la prima occorrenza), le citazioni oltre 4000 caratteri, le frasi molto lunghe (si considera un contesto limitato attorno alla citazione) e alcune abbreviazioni come «D. Lgs.» con lo spazio; in certi casi con effetti diversi il raggruppamento dei duplicati dipende dall'ordine; per la modalità si usa ancora la ricerca letterale del passaggio;
    - **misura senza modello** (`--rivaluta` sull'output grezzo della misura reale del 2026-09-29, 25 bandi, campione con le etichette rivalidate): quote usate (verificate) con precisione da 0,75 a 1,00 e richiamo invariato a 0,375; sulle sole quote raggiungibili precisione e richiamo da 0,75/0,75 a 1,00/0,75; su tutte le quote, comprese quelle da verificare, precisione da 0,50 a 0,57 e richiamo da 0,375 a 0,50. Modalità e conteggi non cambiano per effetto di questo lavoro. La misura è sullo stesso campione usato per correggere: la conferma verrà dal campione di verifica v2.
  - **prompt v3** (2026-09-29): `PARTENARIATO_PROMPT_VERSION` 3; schema (2) e vocabolario (1) invariati. Il prompt dichiara la scheda del catalogo (META, S…) un riassunto redazionale, non il bando: si cita la pagina di un documento ufficiale che contiene la regola (anche per la frase della modalità) e la scheda solo se nessun documento fornito la contiene, sapendo che quella voce resterà da verificare. Quote: le forme negative e distributive («nessun partner sostiene da solo più di X», «ciascun partner almeno X», «a carico del capofila almeno X») sono quote e non vincoli; la categoria su `per_partner` solo se la quota vale per ciascun partner di quel tipo; le frazioni si riportano come nel testo («due terzi», «2/3», «metà») e le converte il codice, che accetta anche un articolo iniziale («i due terzi», «il 30%»); esclusioni estese a quote di adesione, d'iscrizione o associative, importi in euro e limiti di una voce di spesa anche quando valgono per ciascun piano, progetto o partner. Con la versione 3 le regole già salvate risultano da aggiornare: a modulo acceso si riestraggono a pagamento, all'apertura e nelle estrazioni notturne se il loro budget non è zero; oggi in produzione il modulo è spento.
    - **misura reale** (2026-09-29, valutazione locale, 4,92 $ in tutto). Campione 1 (25 bandi, lo stesso usato per correggere): quote usate (verificate) precisione 1,00 e richiamo 0,50, sulle sole raggiungibili 1,00/1,00; tutte le quote 0,67/0,50; modalità 0,68, `partner_min` 0,88, `partner_max` 0,88. Campione 2 (18 bandi, di verifica, mai usato per correggere): quote usate precisione 1,00 e richiamo 0,41; tutte le quote precisione 0,92 e richiamo 0,71; modalità 0,61, `partner_min` 0,50, `partner_max` 0,83;
    - **lettura**: su entrambi i campioni nessuna quota verificata è sbagliata. Il richiamo delle quote verificate è limitato soprattutto dal controllo sul passaggio citato che deve ripartire il costo del progetto: lascia da verificare anche quote corrette, che restano visibili. La modalità resta bassa perché, con la regola delle fonti ufficiali, una citazione della sola scheda non basta più e il modello a volte dichiara «non determinabile»: è il prossimo lavoro.
- **WP9 (fatto)** — consulto dalla call, moderazione DSA, admin e metriche, verifica dell'identità da parte dell'admin (migration 0041). Nessuna chiamata a modelli. Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **verifica dell'identità** (decisione di Michele, Q9): registro append-only `company_identita_verifiche` e stato `company_identita_stato` scritto solo dalle RPC, revoca automatica sui quattro eventi dell'azienda; `fn_partenariato_rappresentante_ok` ridefinita con la stessa firma = identità forte (il CF del titolare resta informativo), `fn_partner_consenso` e `fn_partner_decidi` ridefinite con modifiche minime. Le rotte del titolare sono `GET/POST /me/partner-profile/identita` e non `/me/identita-azienda` come nel contratto: stesso flag, stessa azienda attiva, nessun router nuovo (da confermare con Michele); coda e decisioni dell'admin in `/admin/partenariati/identita`. Procedura consigliata all'admin in `docs/deploy.md`;
  - **cosa sblocca**: il profilo con il nome dell'azienda e la rivelazione **simmetrica** all'accettazione (entrambe verificate: controllo nel servizio e ricontrollo nella RPC; una lettura non riuscita non decide); le viste future (profilo, suggeriti, candidature, controparte, conversazione) mostrano nome, identità e le fasce di bilancio oltre al fatturato solo se la verifica vale oggi, anche per le valutazioni salvate con una candidatura o un invito. Le **call nominative** si salvano solo per aziende verificate (`409 identita_non_verificata_admin`, ricontrollato alla pubblicazione); la proiezione con il nome verso terzi è arrivata con il completamento (sotto);
  - **consulto dalla call**: dalle call in bozza, pubblicate, scadute o completate (mai annullate o sospese); il bando si risolve come la scheda (slug spostato → il bando canonico, ritirato → `410 bando_ritirato`); stessi errori dei consulti esistenti. Nel pool dei progettisti non assegnati anche il report dell'AI-check resta nascosto; la vista del progettista assegnato riusa la vista del creatore in sola lettura, con il consorzio nella proiezione del creatore ma i partner senza pseudonimo né profilo. La CTA del bando conta solo i consulti da AI-check (i due coesistono);
  - **statement of reasons**: template versionato e deterministico (`partenariato_moderazione_testi`, segnaposto «BOZZA — DA RIVEDERE CON IL LEGALE»), con un'anteprima per l'admin identica al testo inviato (rotta `…/anteprima`, non nel contratto). Salvato nella segnalazione; per una restrizione nata dal ricorso accolto di chi aveva segnalato si rigenera identico e resta anche nell'audit. Per una sospensione **d'ufficio**, che non ha una pagina della segnalazione, arriva in-app nella forma breve con la motivazione per intero (per questo la motivazione di una sospensione d'ufficio è al massimo di 500 caratteri, la stessa lunghezza salvata sul contenuto), per email completo, e resta nell'audit; il riesame di una decisione d'ufficio si chiede per iscritto;
  - **chi vede la segnalazione**: chi l'ha fatta e l'azienda autrice solo se una restrizione l'ha riguardata (non prima della decisione né con «nessuna azione»); `decisione_effettiva` dice la decisione dopo il ricorso (l'autore di un contenuto sospeso dal ricorso di chi aveva segnalato legge in testa la restrizione). La conferma di ricezione resta `partenariato.segnalazione_ricevuta` del WP5 (il contratto diceva `moderazione.ricevuta`), ora con il link alla pagina della segnalazione; le altre notifiche sono `moderazione.decisione`, `moderazione.ricorso_ricevuto`, `moderazione.ricorso_deciso`, `moderazione.ripristino`;
  - **ricorso**: uno solo per segnalazione, di chi ne ha interesse (l'autore contro una restrizione, chi ha segnalato contro «nessuna azione»: il ricorso di chi non ne ha interesse non consuma quello dell'altro). Un ricorso accolto toglie la restrizione solo se nient'altro la regge: un'altra decisione valida sullo stesso contenuto o una sospensione d'ufficio in corso (effetto «mantenuto», e l'autore lo sa dalla notifica); un ripristino diretto dell'admin fa sì che le decisioni precedenti non reggano più la restrizione tolta. Decisioni, ricorsi e azioni dirette sullo stesso contenuto si serializzano. Il testo della UI non promette un esaminatore diverso da chi ha deciso;
  - **contesto per l'admin**: la finestra di ±10 messaggi è una `GET`; la conversazione intera una `POST` con la motivazione nel corpo (il contratto la voleva in query string, che finisce nei log di accesso del server e dei proxy). Il contesto nel frontend ha una chiave di cache fuori da quelle invalidate dalle azioni di moderazione: si rilegge solo per un'azione dell'admin;
  - **uscita dal consorzio di una call sospesa** (nota WP8 P14): la RPC non cambia (il ramo «membro» non guarda lo stato della call); il backend la consente dalla sola propria riga e, del consorzio di una call sospesa, restituisce all'azienda solo la propria riga; nella pagina della call un'azienda del consorzio trova «Esci dal consorzio» al posto di «Call non trovata»;
  - **metriche**: coorte delle call pubblicate nel periodo; la percentuale con almeno una candidatura entro 30 giorni solo sulle call pubblicate da almeno 30 giorni; «consorzi validati in verde» dall'esito salvato, riallineato ogni notte dal passo `ricalcolo_validazioni` dello scheduler (a blocchi di 100, best-effort: la prima opzione della nota del contratto). Costi per provider, servizio, esito e valuta, totali per valuta;
  - **pannello admin**: `pages/AdminPartenariati.tsx` (non `pages/admin/`), con le schede `segnalazioni`, `identita`, `call`, `metriche`, `costi` ed `estrazioni`; nel menu Admin solo a modulo acceso. La ricerca delle call è per titolo della call o del bando, per id e, dal completamento, per denominazione del registro dell'azienda creatrice;
  - **Completamento WP9** (dopo la revisione del diff, decisione di Michele: la verifica sblocca le call nominative). Nessuna migration nuova:
    - **call con il nome verso terzi**: il creatore di una call con `anonima` false compare con la sola denominazione del Registro Imprese (`company_data.denominazione`, dati del registro coerenti T5) se OGGI l'azienda ha l'identità forte, letta a ogni vista e per le card della bacheca e di «Per te» con una lettura live della pagina (l'indice non la conserva: una revoca vale subito); altrimenti «Azienda anonima» (revoca, interruttore globale spento, lettura non riuscita). Vale per dettaglio pubblico, bacheca, «Per te», vista della controparte, riga del creatore nel consorzio visto dagli altri membri, anteprima e «Le mie call». Mai altri dati nominativi; i testi liberi restano ripuliti dagli identificativi come per le anonime (la rivelazione resta simmetrica). Nel wizard la scelta si offre solo alle aziende verificate (il backend rifiuta le altre); il prompt delle proposte AI resta anonimo;
    - **prova dei requisiti decisa dal server**: al salvataggio `citazione.verificata` inviata dal client non conta; il server ricalcola la citazione di ogni requisito dalle stesse fonti della proposta: voce `confermata` dello snapshot citata da una pagina di un documento ufficiale (`D<n>-p<m>`, anche per gli snapshot confermati prima della regola del WP3) per `bando_partenariato` e `regola_finanziaria`, la voce dell'ultimo AI-check con lo stesso riferimento (id~impronta) per `ai_check`, in entrambi i casi solo se testo, criterio e ambito sono ancora quelli generati; `manuale` e `precheck` mai verificati (la citazione del client resta come riferimento). Nemmeno l'origine dichiarata fa fede: un `precheck` resta tale solo se coincide con un pre-check che il server genera ora dai facet del bando, altrimenti (riscritto, inventato, catalogo non leggibile) si salva `manuale` ed è del creatore; la proposta successiva ripropone il pre-check originale accanto a quello riscritto. Le citazioni costruite dalla scheda del catalogo escono non verificate, sia quelle dei pre-check sia quelle dell'AI-check (sezioni `META`, `S1`…: fa fede solo una pagina di un documento ufficiale), con requisito e copertura invariati;
    - **informativa partner v2** (`2026-10-bozza-2`, anche quella del referente; restano BOZZA — DA RIVEDERE CON IL LEGALE): verifica dell'identità su richiesta, nome solo se scelto e verificato (anche nelle call), rivelazione solo tra aziende entrambe verificate (altrimenti anonime, ci si presenta in chat), accesso dello staff di moderazione ai messaggi segnalati (finestra di contesto; conversazione intera solo con motivazione registrata; si registrano gli accessi al contesto e le decisioni), conservazione di segnalazioni (24 mesi, Q22) e del registro delle verifiche (durata da definire con il legale). Chi aveva acconsentito alla v1 vede il banner di riconferma (`riconsenso_suggerito`); il consenso resta valido;
    - **area Consulenze del cliente**: badge «Dalla call di partenariato» in elenco e nel dettaglio, e nel dettaglio il link alla call con `?azienda=` (nuovo campo `company_profile_id` nelle risposte delle consulenze, additivo), solo a modulo acceso;
    - **audit del progettista**: la vista della call del consulto usava una chiave di cache sotto la radice invalidata da ritiro di proposte e annullamento di appuntamenti, quindi si rileggeva (e registrava un nuovo accesso) a ogni azione: ora ha una chiave propria, e così il dossier dell'azienda (anche senza rilettura al focus della finestra);
    - la chat resta anonima anche per il creatore di una call nominativa: il nome nella chat e i recapiti seguono la rivelazione simmetrica.
- **WP10 (fatto)** — bozze AI di lettera d'intenti, NDA e term sheet (§2.9 W4, T6-T8, Q7, migration 0042). Scostamenti decisi in implementazione o dopo la revisione del diff:
  - **router nuovo** `partenariati_bozze.py` (`/partenariati/call/{id}/bozze`, flag sul router e sulla route class), non dentro `partner_calls.py`; la RPC di prenotazione ha in più `p_prompt_version` (salvata con la bozza) e le RPC sono tre (`fn_partner_bozza_prenota`, `_concludi`, `_chiudi_stale`) invece di `fn_partner_bozza_crea`;
  - **chi**: avvia solo il titolare dell'azienda creatrice (call in qualunque stato) o di un membro non uscito del consorzio (call non sospesa); leggono anche i membri con visibilità dell'azienda; candidati e invitati in attesa, aziende uscite e admin ricevono `404`. Nel frontend la scheda compare da quando la call è pubblicata (ruoli e quote vengono dal consorzio);
  - **output strutturato strict** (`genera`): lo schema `BozzaDocumentoAi` sta nel budget di `tests/test_schemi_ai_dimensione.py` ed è nella sonda (`bozza_documento`); la risposta ha in più `avvisi` (cosa il controllo automatico ha tolto o sostituito). Segnaposto ammessi: quelli delle parti e i generici (`[Data]`, `[Luogo]`, `[Firma]`, `[Legale rappresentante]`, `[Sede legale]`, `[Titolo del progetto]`, `[Durata]`, `[Foro competente]`, `[Importo]`); ogni altro «[…]» diventa `[da completare]`;
  - **controllo del testo generato** (dopo la revisione): i contatti si cercano sul testo intero con gli spazi già compattati, prima di separare i segnaposto, così un'email o un indirizzo offuscati con le quadre o un numero spezzato da un a capo non passano; gli identificativi della propria azienda solo nel testo tra i segnaposto (un segnaposto sopravvive anche a una ragione sociale fatta di una parola comune);
  - **nome della propria azienda** (con la casella): la denominazione del Registro Imprese se i dati importati sono coerenti (T5), altrimenti la ragione sociale; senza la casella gli identificativi della propria azienda si tolgono dal testo generato;
  - **costi per ramo** come WP3-WP5, con i codici d'errore della bozza: 4xx non transitorio senza usage → costo 0 (`ai_richiesta_rifiutata`); errore transitorio del provider (429, 5xx, 529) senza usage → costo ignoto per il budget (`ai_non_disponibile`); nessuna risposta HTTP → costo ignoto (`ai_rete`). **Nel limite mensile** (dopo la revisione) non conta `ai_non_disponibile`: il servizio AI ha risposto senza generare, e un'ora di sovraccarico del provider non deve consumare le bozze del mese; contano invece la rete e gli altri errori a costo ignoto (interruzione, guasto dopo l'invio);
  - **PDF** (dopo la revisione): il `footer` di `pdf_service` è un paragrafo in coda al documento, non un piè di pagina; il disclaimer va quindi a piè di **ogni** pagina sul PDF già composto, con qualunque motore (`pypdf`, già dipendenza), oltre alla sezione «Avvertenza» in testa; se il piè di pagina non riesce, `503 pdf_unavailable` e nessun PDF;
  - **`/me/entitlements`**: `partenariati.bozze_mese` è facoltativo nello schema (uno snapshot senza la chiave non invalida gli altri limiti; la UI non mostra il contatore);
  - **accessibilità** (dopo la revisione): l'esito di una bozza si annuncia anche mentre un'altra è in preparazione;
  - **tetto giornaliero per titolare** (rifinitura): `fn_partner_bozza_prenota` ha in più `p_limite_owner` (ultimo parametro, NULL = nessun tetto), passato a `fn_partenariati_ai_prenota` e contato sulle sole esecuzioni delle bozze dell'owner nel giorno; il backend passa `PARTNER_BOZZE_DOCUMENTO_LIMITE_OWNER_GIORNO` (10), così anche un piano illimitato non consuma in un giorno il budget «altri» condiviso con WP4 e WP5 (`429 ai_limite_giornaliero`). Il conteggio usa l'esclusione della 0034 (solo gli errori senza LLM a costo 0 non contano): a differenza del limite mensile conta anche `ai_non_disponibile`, perché è a costo ignoto e la sua riserva pesa sul budget del giorno, che è ciò che il tetto protegge.
- **Fase (c) del contratto del DB bandi** — catalogo letto dalla vista pubblica e bandi fusi (migration 0043):
  - il modulo legge il catalogo solo da `bando_pubblico`, mai dalla tabella `bando` né con il predicato di pubblicazione. La GET delle regole, la creazione della call e le facet dei pre-check passano da `carica_per_slug`: uno slug spostato o fuso porta al master, uno ritirato è 410. Lo slug dall'id (estrazione forzata dall'admin, valutazione) legge `bando_pubblico` e sul miss `bando_fusione`. Il batch notturno usa il segmento «aperti» di `stato_effettivo`;
  - `GET /bandi/{slug}/partenariato` ha in più `stato_effettivo`, letto dalla vista (`stato_bando` resta);
  - **bandi fusi**: un id assente da `bando_pubblico` ma presente in `bando_fusione` è un doppione fuso, non un bando sparito.
    - Lo scheduler (`chiusura_call`) non marca la sua call con `bando_mancante_dal` e non la chiude per `bando_non_disponibile`, nemmeno con una marca di 7 giorni o più messa prima. Vale lo stesso per il controllo in lettura del dettaglio.
    - Se `bando_fusione` non si legge, nessun bando assente è valutabile: un errore non vale «non fuso».
    - L'indice di scoperta (`partenariato_indice`, bacheca, suggeriti e notifiche proattive) controlla su `bando_fusione` gli id assenti dalla vista. La call su un doppione resta proposta con lo stato e la scadenza del suo snapshot, come la call «congelata» dello scheduler, e lo stato va in cache per 10 minuti come gli altri. Se `bando_fusione` non si legge vale lo snapshot, senza cache, e la ricarica successiva riprova. Solo un id assente e non fuso fa uscire la call dalla scoperta;
    - Creazione e pubblicazione su un doppione restano bloccate: `409 bando_non_disponibile` con il messaggio «Il bando è stato unito a un altro bando: per ora la call non si può pubblicare.». Stesso testo nel motivo di blocco della bozza. Il messaggio non invita a ricreare la call sul master (una seconda call attiva della stessa azienda sul master è una collisione che la rimappatura non risolve) e non promette lo spostamento, che non avviene a rimappatura spenta né in collisione;
  - **rimappatura dei fusi** (`services/rimappatura_fusi.py`, `services/catalogo_scheduler.py`; spenta di default, procedura in docs/deploy.md): le call attive su un doppione passano al master e perdono `bando_mancante_dal`.
    - Una call in collisione con un'altra call attiva della stessa azienda sul master non si tocca: si conta come `in_collisione` e ricompare a ogni passo.
    - La call rimappata tiene lo snapshot del doppione (titolo, programma, tipologia), che di norma coincide con quello del master; stato e scadenza del bando si riallineano con lo scheduler.
    - Le call chiuse e le versioni non cambiano;
  - **call «congelate» a rimappatura spenta**: finché la rimappatura resta spenta, una call su un doppione non si chiude se il master diventa chiuso, sospeso o revocato, e il suo snapshot non si aggiorna. Si risolve accendendo la rimappatura (prima `prova`, poi `attiva`);
  - **documenti del WP3**: i candidati sono sempre l'unione delle righe di `bando_link` e del jsonb `allegati` della riga, senza doppioni per URL.
    - A parità di URL vince la riga di `bando_link`; un'etichetta vuota prende il `label` del jsonb.
    - Motivo: molte righe che sostituiscono il jsonb non sono ancora leggibili.
    - Cambia il `catalogo_hash`: alla prossima estrazione i documenti si riacquisiscono, ma nessuna estrazione riparte da sola.
- **Correzioni per l'accensione** (2026-09-30):
  - **disiscritti e email verificate oltre 1000 righe**: `filtra_recapitabili`, da cui passano digest, email di evento e statement di moderazione (e gli alert sui bandi), legge la suppression list per intero, a keyset su `id`, e verifica le email a blocchi di 100 utenti. Oltre il max-rows di PostgREST (1000 righe) un indirizzo soppresso non riceve email e un utente verificato non viene scartato;
  - **tempo lineare sui testi dei documenti**: il pre-classificatore e la pulizia dei domini esclusi (`scrub_menzioni`) lavorano in tempo lineare anche su input patologici. Il pre-classificatore dà lo stesso esito di prima, salvo ai confini della vecchia finestra di ricerca delle esclusioni; la pulizia dà lo stesso risultato della regex;
  - **bandi fusi nell'indice di scoperta**: vedi «bandi fusi» sopra.
- **Modulo completo (WP0-WP10)**. Resta per l'accensione in produzione:
  - **G1** sandbox openapi (§14): fixture reali al posto di quelle sintetiche e codici CEE ancora da verificare. Fino ad allora storico IT-advanced e bilancio ufficiale (WP1-WP2) sono in produzione ma spenti dietro `BILANCI_STORICO_ATTIVO` (§2.1 T1), già nel compose con default spento. G1 è consigliata prima dell'accensione, che si fa dal `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md`; dopo, prezzo e attivazione dell'addon `bilancio-ufficiale`;
  - **G2** tutte le migration dalla 0032 alla 0042, in ordine dallo SQL Editor del primario, **prima** del deploy e qualunque sia il flag: il backend legge colonne della 0036, 0041 e 0042 anche a modulo spento;
  - **G4** revisione legale di informative, Termini d'uso e DSA, statement of reasons e disclaimer (anche quello delle bozze dei documenti e le istruzioni di stesura dei tre documenti), poi la loro versione nel codice;
  - **qualità dell'estrazione WP3** (§16 WP3): con il prompt v3 le quote verificate hanno precisione 1,00 su entrambi i campioni, ma la `modalita` è corretta nel 68% (campione 1) e nel 61% (campione 2, selezionato dalle fonti ufficiali), sotto l'obiettivo del 90%, perché vale solo con una citazione da un documento ufficiale: il prossimo intervento va deciso;
  - in AdminPiani il limite delle bozze dei piani su misura (partono da 0); consigliato dimensionare il budget giornaliero «altri» (condiviso da bozza del profilo, proposte della call e bozze dei documenti, ciascuno con il suo tetto giornaliero per titolare) sul numero di clienti attivi;
  - l'accensione: `docker-compose.yml` passa già `PARTENARIATI_ATTIVO` e i tre budget giornalieri al backend (default spenti); si impostano nel `.env` con la procedura «Accensione delle funzioni» di `docs/deploy.md` (variabili, AdminPiani, tetto di spesa nella console Anthropic, controlli, spegnimento).

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

---

## Appendice B — Matching (WP6): filtri, punteggio ed esempio guida

Implementazione in `backend/app/services/partenariato_matching.py` (modulo puro, nessun LLM), indice in `partenariato_indice.py`, collegamenti in `partenariato_collegamenti.py`, notifiche e digest in `partenariato_notifiche.py`.

**Filtri rigidi**, in ordine; escludono solo su esito certo e il loro codice resta interno (mai mostrato al creatore per le aziende di terzi):
1. call non attiva (non pubblicata, sospesa, scaduta, bando scaduto, creatore non vivo);
2. candidato senza opt-in visibile (non vale per «Per te», che è scoperta), sospeso, non vivo, impresa non attiva nel registro;
3. stesso owner (anche la stessa azienda);
4. collegamenti non calcolati (del creatore sempre, del candidato se visibile) o collegata, certo o possibile;
5. categoria del bando tra quelle escluse dal candidato, forma prevista non accettata;
6. nessuna posizione compatibile per ruolo disponibile e tipo di soggetto;
7. territorio, paese, dimensione: requisiti `ogni_membro`, poi vincoli delle posizioni (regola «tutte le sedi» di `valuta_criterio`);
8. regola economica non soddisfatta sulla **fascia** del candidato, su ogni posizione rimasta (le regole «per ciascun partner» sempre, quelle del capofila solo sulle posizioni da capofila; medie pesate e totali sono del validatore, WP8);
9. esclusività: il candidato ha già un impegno sullo stesso bando (una sua call pubblicata o, dal WP7, una candidatura accettata su una call non annullata) e questa call o quella dell'impegno è esclusiva, la stessa regola della RPC che decide all'accettazione e alla pubblicazione;
10. call solo su invito: in «Per te» solo con un invito in attesa (non scaduto) o accettato; tra i suggeriti solo chi accetta inviti.

Dato mancante o esito incerto non escludono: diventano voci «da verificare» con la penalità. Lo stesso vale per un requisito `ogni_membro` diverso da territorio, paese e dimensione che il candidato non ha dichiarato.

**Punteggio** (0–100, interno: non esce dalle API):

`punteggio = arrotonda(100 · (p_cop·copertura_gap + p_aff·affinità + p_compl·complementarità + p_prof·completezza + p_rot·rotazione)) − min(penalità_max, penalità · voci_da_verificare)`

limitato a 0..100, con arrotondamento half-up su frazioni esatte.

| Componente | Definizione | Peso di default (variabile) |
|---|---|---|
| copertura_gap | requisiti cercati coperti / requisiti cercati; senza requisiti cercati 1 se una posizione è coperta, altrimenti 0 | 0,50 (`PARTENARIATO_PESO_COPERTURA`) |
| affinità | media di tre indicatori 0/1: esperienza dichiarata nel programma del bando; una divisione ATECO in comune con il creatore o un settore (proprio o d'interesse) uguale al suo; una regione (sede o d'interesse) tra quelle della call | 0,20 (`PARTENARIATO_PESO_AFFINITA`) |
| complementarità | competenze del candidato che il creatore non ha / competenze del candidato (0 senza competenze) | 0,10 (`PARTENARIATO_PESO_COMPLEMENTARITA`) |
| completezza | completezza del profilo partner / 100 | 0,10 (`PARTENARIATO_PESO_COMPLETEZZA`) |
| rotazione | max(0, 1 − esposizioni degli ultimi 7 giorni / 5): notifiche proattive ricevute e, dal WP7, inviti ricevuti ancora in attesa o accettati | 0,10 (`PARTENARIATO_PESO_ROTAZIONE`) |
| penalità | per ogni voce da verificare, fino a un massimo | 5 e 20 (`PARTENARIATO_PENALITA_DATO_MANCANTE`, `PARTENARIATO_PENALITA_MAX`) |

**Ordine** di suggeriti, «Per te» e bacheca per affinità: requisiti cercati coperti (decrescente), punteggio (decrescente), poi `sha256(call:codice_pubblico:settimana ISO)`: deterministico nella settimana, diverso la settimana dopo. Nei suggeriti al massimo 2 aziende dello stesso owner sull'**intera lista** (WP7): le altre non compaiono in nessuna pagina né nel totale, e il limite si applica prima dei filtri facoltativi (per posizione), così un filtro non fa comparire un'azienda che la lista completa nasconde; l'invito accetta solo le aziende di questa lista. In «Per te» al massimo 2 call dello stesso owner per pagina (chi slitta passa alla pagina dopo).

**Notifiche proattive**: alla pubblicazione le prime 20 aziende (`PARTENARIATO_NOTIFICHE_TOP_K`) con almeno un requisito cercato coperto e punteggio ≥ 50 (`PARTENARIATO_NOTIFICHE_SOGLIA`), senza il limite per owner dei suggeriti (la lista non si mostra al creatore), al più 3 a settimana per azienda (`PARTENARIATO_NOTIFICHE_TETTO_SETTIMANA`), una volta per azienda × call. Digest il lunedì dalle 08:30 (`PARTENARIATO_DIGEST_GIORNO`, `PARTENARIATO_DIGEST_ORA`) con le notifiche degli ultimi 14 giorni non ancora incluse.

**Esempio guida** (fixture sintetica `backend/tests/fixtures/partenariati/esempio_guida.py`, solo codici del vocabolario v1). X (capofila; ATECO 62, piccola, sede in Calabria) pubblica una call su un bando Horizon con i requisiti A (organismo di ricerca), B (ATECO 62), C (competenza `prototipazione_testing`), D (micro, piccola o media), tutti «consorzio»: X copre B e D, quindi cerca A e C. Vincoli di ogni membro: E (sede in Calabria) ed F (costo della quota / fatturato medio degli ultimi 2 anni ≤ 0,6, per ciascun partner). Posizione P1: organismo di ricerca con `prototipazione_testing`, quota 20%; budget in fascia `2m_5m`, quindi costo della quota tra 400 k€ e 1 M€.

| Azienda | Situazione | Esito |
|---|---|---|
| Y | organismo di ricerca, `prototipazione_testing`, sede legale nel Lazio e unità locale in Calabria, fatturato medio 2,5 M€ (fascia `2m_10m`), opt-in | prima nei suggeriti di X, e la call prima nel suo «Per te»: «Copri «A» e «C», che mancano al capofila.»; punteggio interno 90 |
| T | come Y, senza bilanci | sotto Y, con «Bilanci non disponibili: la regola «F» non si può verificare»; 85 (penalità 5) |
| Z | come Y, sedi solo in Lombardia | esclusa (territorio) |
| W | come Y, collegata a X (socio comune al 60%) | esclusa (collegata) |
| V | come Y, senza opt-in | esclusa dai suggeriti; la call le compare comunque in «Per te» |
| U | come Y, fatturato medio 200 k€ (fascia `100k_500k`) | esclusa (regola economica: 0,6 × 500 k€ < 400 k€ su tutta la fascia) |

Punteggio di Y, a mano: copertura 2/2 = 1; affinità 2/3 (esperienza nel programma sì, ATECO o settore in comune con X no, regione in comune sì); complementarità 3/3 = 1; completezza 0,70; rotazione 1 → 100 · (0,50 + 0,20 · 2/3 + 0,10 + 0,07 + 0,10) = 90,33 → **90**. Scostamenti dai numeri di §10: la fascia «1,0–1,2 M€» non esiste nell'enum delle call (si usa `2m_5m` con la quota al 20%) e le regole verso terzi si valutano sulle fasce: con la quota al 30% o una fascia di budget più bassa l'esito di U o di Y sarebbe «dipende dalla fascia», non certo.
