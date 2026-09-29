-- ============================================================================
-- BandoFit — DB primario, migration 0037: CALL DI PARTENARIATO (WP5, seconda
-- parte; piano docs/partenariati.md §2.1 T3-T7, §2.5 C1-C7, §3 riga 0037, §7,
-- §13 Q1/Q9/Q11/Q14/Q18).
--
--   1) partner_calls — la call (una non chiusa per azienda × bando, al massimo
--      N bozze per azienda): snapshot del bando, snapshot delle regole di
--      partenariato confermate dal creatore (matching e validatore leggono SOLO
--      questo), testi pubblici e riservati, budget in fascia (pubblico) ed
--      esatto (RISERVATO), stati, sospensione (le RPC arrivano in WP9), versione
--      e i due job AI asincroni (posizioni e testi);
--   2) partner_call_requisiti — requisiti tipizzati (criterio CriterioPartner +
--      ambito), con etichetta breve stabile e copertura del creatore;
--   3) partner_call_posizioni — posizioni cercate con array tipizzati;
--   4) partner_call_versioni — snapshot a ogni modifica dopo la pubblicazione
--      (righe immutabili);
--   5) partner_segnalazioni — registro DSA nella forma definitiva, SENZA FK (le
--      colonne di decisione e ricorso arrivano in WP9);
--   6) RPC: crea_bozza, aggiorna, conferma_regole, sostituisci_requisiti,
--      sostituisci_posizioni, pubblica, chiudi, chiudi_auto; job AI (prenota,
--      chiusura atomica, failsafe); fn_partenariati_snapshot per
--      /me/entitlements; funzioni interne di identità (stessa semantica delle
--      verifiche dentro fn_partner_consenso, 0035).
--
-- Chi agisce (T4, Q14): solo il titolare. Ogni RPC su una call la cerca con
-- id + company_profile_id = azienda attiva + family_parent_id = owner: un
-- Advisor con l'azienda A attiva non tocca le call di B (call_not_found).
--
-- ORDINE DEI LOCK (globale, da rispettare anche nei WP successivi):
--   owner (profiles FOR UPDATE) → azienda (company_profiles FOR NO KEY UPDATE:
--   serializza con UPDATE/DELETE dell'azienda senza bloccare gli insert con FK
--   verso di essa) → call (FOR UPDATE) → [candidatura, WP7] → lock advisory del
--   budget AI hashtext('partenariati_ai_budget') per ULTIMO → righe di
--   partenariati_ai_esecuzioni.
-- I limiti di piano (fn_partenariati_limiti, 0036) si applicano sotto il lock
-- dell'owner, sul modello di fn_create_company.
--
-- ADDITIVA: tabelle e funzioni nuove; nessuna funzione o tabella esistente
-- ridefinita o modificata. Il backend attuale non le usa. Da eseguire IN
-- UN'UNICA TRANSAZIONE (begin; ... commit;). Rollback documentato in coda.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Call di partenariato. Id del bando, programma e tipologia sono del DB
--    SECONDARIO: nessuna FK cross-database.
-- ----------------------------------------------------------------------------
create table public.partner_calls (
  id                          uuid primary key default gen_random_uuid(),
  company_profile_id          uuid not null
                                references public.company_profiles (id) on delete cascade,
  family_parent_id            uuid not null,
  creato_da                   uuid not null,
  -- Snapshot del bando (aggiornato alla pubblicazione e dallo scheduler).
  bando_id                    integer not null,
  bando_slug                  text not null
    constraint pcall_bando_slug_check check (char_length(bando_slug) between 1 and 300),
  bando_titolo                text not null
    constraint pcall_bando_titolo_check check (char_length(bando_titolo) >= 1),
  bando_scadenza              date,
  bando_programma_id          integer,
  bando_tipologia_id          integer,
  bando_stato_effettivo       text,
  bando_verificato_at         timestamptz,
  bando_mancante_dal          date,
  ruolo_creatore              text not null
    constraint pcall_ruolo_creatore_check
    check (ruolo_creatore in ('capofila', 'cerco_capofila')),
  forma_aggregazione_prevista text
    constraint pcall_forma_aggregazione_check
    check (forma_aggregazione_prevista is null
           or forma_aggregazione_prevista in ('ats', 'ati_rti', 'rete_contratto',
                                              'rete_soggetto', 'consorzio',
                                              'accordo_partenariato', 'consorzio_ue',
                                              'altra')),
  anonima                     boolean not null default true,
  titolo                      text
    constraint pcall_titolo_check
    check (titolo is null or char_length(titolo) between 10 and 140),
  descrizione_pubblica        text
    constraint pcall_descrizione_pubblica_check
    check (descrizione_pubblica is null or char_length(descrizione_pubblica) <= 3000),
  dettagli_riservati          text
    constraint pcall_dettagli_riservati_check
    check (dettagli_riservati is null or char_length(dettagli_riservati) <= 5000),
  profilo_partner_ideale      text
    constraint pcall_profilo_partner_ideale_check
    check (profilo_partner_ideale is null or char_length(profilo_partner_ideale) <= 2000),
  budget_fascia               text
    constraint pcall_budget_fascia_check
    check (budget_fascia is null
           or budget_fascia in ('fino_50k', '50k_150k', '150k_300k', '300k_500k', '500k_1m',
                                '1m_2m', '2m_5m', 'oltre_5m')),
  budget_progetto_eur         numeric(14, 2)
    constraint pcall_budget_progetto_eur_check
    check (budget_progetto_eur is null or budget_progetto_eur > 0),
  quota_creatore_pct          numeric(5, 2)
    constraint pcall_quota_creatore_pct_check
    check (quota_creatore_pct is null
           or (quota_creatore_pct > 0 and quota_creatore_pct <= 100)),
  scadenza_call               date,
  visibilita                  text not null default 'pubblica'
    constraint pcall_visibilita_check check (visibilita in ('pubblica', 'solo_invitati')),
  stato                       text not null default 'bozza'
    constraint pcall_stato_check
    check (stato in ('bozza', 'pubblicata', 'chiusa_completata', 'chiusa_annullata',
                     'scaduta', 'sospesa_moderazione')),
  motivo_chiusura             text
    constraint pcall_motivo_chiusura_check
    check (motivo_chiusura is null
           or motivo_chiusura in ('scadenza_call', 'bando_chiuso', 'bando_sospeso',
                                  'bando_revocato', 'bando_non_disponibile',
                                  'azienda_non_disponibile', 'creatore_completata',
                                  'creatore_annullata', 'moderazione')),
  override_non_ammesso_motivo text
    constraint pcall_override_non_ammesso_motivo_check
    check (override_non_ammesso_motivo is null
           or char_length(override_non_ammesso_motivo) between 20 and 1000),
  partenariato_ref            jsonb
    constraint pcall_partenariato_ref_check
    check (partenariato_ref is null or jsonb_typeof(partenariato_ref) = 'object'),
  regole_partenariato         jsonb
    constraint pcall_regole_partenariato_check
    check (regole_partenariato is null or jsonb_typeof(regole_partenariato) = 'object'),
  regole_confermate_at        timestamptz,
  esclusivita                 boolean not null default false,
  ai_check_id                 uuid references public.ai_checks (id) on delete set null,
  wizard_passo                smallint not null default 1
    constraint pcall_wizard_passo_check check (wizard_passo between 1 and 7),
  versione                    integer not null default 0
    constraint pcall_versione_check check (versione >= 0),
  pubblicata_at               timestamptz,
  chiusa_at                   timestamptz,
  sospesa_at                  timestamptz,
  sospeso_motivo              text
    constraint pcall_sospeso_motivo_check
    check (sospeso_motivo is null or char_length(sospeso_motivo) <= 500),
  sospeso_da                  uuid,
  stato_prima_sospensione     text
    constraint pcall_stato_prima_sospensione_check
    check (stato_prima_sospensione is null
           or stato_prima_sospensione in ('bozza', 'pubblicata')),
  -- Job AI asincroni (T7): proposta di posizioni e bozza dei testi.
  ai_posizioni_stato          text
    constraint pcall_ai_posizioni_stato_check
    check (ai_posizioni_stato in ('in_corso', 'pronta', 'errore')),
  ai_posizioni_proposta       jsonb
    constraint pcall_ai_posizioni_proposta_check
    check (ai_posizioni_proposta is null or jsonb_typeof(ai_posizioni_proposta) = 'object'),
  ai_posizioni_avviata_at     timestamptz,
  ai_posizioni_esecuzione_id  uuid,
  ai_posizioni_errore         text,
  ai_testi_stato              text
    constraint pcall_ai_testi_stato_check
    check (ai_testi_stato in ('in_corso', 'pronta', 'errore')),
  ai_testi_proposta           jsonb
    constraint pcall_ai_testi_proposta_check
    check (ai_testi_proposta is null or jsonb_typeof(ai_testi_proposta) = 'object'),
  ai_testi_avviata_at         timestamptz,
  ai_testi_esecuzione_id      uuid,
  ai_testi_errore             text,
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now(),
  -- Una call pubblicata (anche se poi chiusa o sospesa) è completa. Una bozza
  -- incompleta resta annullabile (pubblicata_at NULL).
  constraint pcall_pubblicata_completa
    check (pubblicata_at is null
           or (titolo is not null and descrizione_pubblica is not null
               and scadenza_call is not null and regole_confermate_at is not null)),
  -- Macchina a stati (§7): la bozza non è mai stata pubblicata; pubblicata,
  -- completata e scaduta lo sono state (una bozza si chiude solo annullata).
  constraint pcall_pubblicata_coerente
    check ((stato = 'bozza' and pubblicata_at is null)
           or (stato in ('pubblicata', 'chiusa_completata', 'scaduta')
               and pubblicata_at is not null)
           or stato in ('chiusa_annullata', 'sospesa_moderazione')),
  constraint pcall_chiusa_coerente
    check ((stato in ('chiusa_completata', 'chiusa_annullata', 'scaduta'))
           = (chiusa_at is not null and motivo_chiusura is not null)),
  constraint pcall_sospesa_coerente
    check (stato <> 'sospesa_moderazione'
           or (sospesa_at is not null and stato_prima_sospensione is not null)),
  constraint pcall_regole_confermate_coerente
    check (regole_confermate_at is null or regole_partenariato is not null),
  -- Un job in corso ha sempre la sua esecuzione e il suo avvio (failsafe).
  constraint pcall_ai_posizioni_in_corso_coerente
    check (ai_posizioni_stato is distinct from 'in_corso'
           or (ai_posizioni_esecuzione_id is not null and ai_posizioni_avviata_at is not null)),
  constraint pcall_ai_testi_in_corso_coerente
    check (ai_testi_stato is distinct from 'in_corso'
           or (ai_testi_esecuzione_id is not null and ai_testi_avviata_at is not null))
);

comment on table public.partner_calls is
  'Call di partenariato di un''azienda su un bando (WP5). Scritture SOLO tramite le RPC fn_partner_call_* (titolare, azienda attiva, lock owner → azienda → call). Verso terzi esce solo la proiezione a whitelist del backend (services/partenariato_accesso.py): mai company_profile_id, family_parent_id, creato_da, budget_progetto_eur, dettagli_riservati, coperture del creatore. Cascade dalla riga company_profiles (hard delete).';
comment on column public.partner_calls.family_parent_id is
  'Owner (titolare) dell''azienda al momento della creazione: pool dei limiti di piano (Q1). Senza FK.';
comment on column public.partner_calls.creato_da is
  'Utente che ha creato la call (il titolare, T4). Senza FK: MAI verso terzi.';
comment on column public.partner_calls.bando_mancante_dal is
  'Primo giorno in cui lo scheduler non ha trovato il bando in bando_pubblico; dopo 7 giorni la call si chiude (chiusa_annullata, bando_non_disponibile).';
comment on column public.partner_calls.anonima is
  'true = verso terzi «Azienda anonima» + regione, sezione ATECO e classe dimensionale (C3). Oggi le call sono SOLO anonime (NOMINATIVO_DISPONIBILE = False nel backend); la pubblicazione di una call nominativa richiede comunque il legale rappresentante verificato (Q9).';
comment on column public.partner_calls.dettagli_riservati is
  'Visibili solo al creatore (e, da WP7, alle aziende accettate). MAI nell''input dell''AI.';
comment on column public.partner_calls.budget_fascia is
  'Fascia PUBBLICA del budget di progetto (8 fasce, C4).';
comment on column public.partner_calls.budget_progetto_eur is
  'Budget esatto RISERVATO e facoltativo (C4): solo il creatore lo vede; alimenta le regole finanziarie nella vista «proprio». MAI verso terzi né nell''input dell''AI.';
comment on column public.partner_calls.regole_partenariato is
  'Snapshot delle regole di partenariato CONFERMATO dal creatore (fn_partner_call_conferma_regole): {versione: 1, fonte, modalita, forme_ammesse, costituzione, partner_min, partner_max, composizione, quote, vincoli, regole_finanziarie, documenti_richiesti}, ogni voce con origine_voce confermata | modificata | aggiunta (niente regole finanziarie aggiunte a mano, Q11). Matching (WP6) e validatore (WP8) leggono SOLO questo, mai l''estrazione grezza.';
comment on column public.partner_calls.esclusivita is
  'Il bando ammette la partecipazione a un solo partenariato (confermato dal creatore insieme alle regole).';
comment on column public.partner_calls.partenariato_ref is
  'Riferimento all''estrazione WP3 alla creazione: {prompt_version, estratta_at, modalita_effettiva}.';
comment on column public.partner_calls.versione is
  '0 in bozza; 1 alla pubblicazione; +1 a ogni modifica successiva (snapshot in partner_call_versioni).';
comment on column public.partner_calls.stato_prima_sospensione is
  'Stato da ripristinare alla fine della sospensione per moderazione (RPC in WP9).';
comment on column public.partner_calls.ai_posizioni_esecuzione_id is
  'Esecuzione (partenariati_ai_esecuzioni.id) del job posizioni in corso o ultimo: senza FK. La chiusura del job scrive solo se coincide.';
comment on column public.partner_calls.ai_testi_esecuzione_id is
  'Esecuzione (partenariati_ai_esecuzioni.id) del job testi in corso o ultimo: senza FK. La chiusura del job scrive solo se coincide.';

create trigger trg_partner_calls_updated_at
  before update on public.partner_calls
  for each row execute function public.set_updated_at();

-- Una call non chiusa per azienda × bando (bozza compresa).
create unique index partner_calls_una_attiva on public.partner_calls
  (company_profile_id, bando_id)
  where stato in ('bozza', 'pubblicata', 'sospesa_moderazione');
-- Scheduler (scadenze) e liste per stato.
create index partner_calls_stato_scadenza_idx on public.partner_calls (stato, scadenza_call);
-- Call aperte di un bando (bacheca, partenariato_service.get_stato).
create index partner_calls_bando_pubblicate_idx on public.partner_calls (bando_id)
  where stato = 'pubblicata';
-- Pool dei limiti per owner e liste del titolare.
create index partner_calls_family_idx on public.partner_calls (family_parent_id, created_at desc);
-- Liste per azienda e cascade dalla riga company_profiles.
create index partner_calls_company_idx on public.partner_calls
  (company_profile_id, created_at desc);
-- ON DELETE SET NULL verso ai_checks senza scansione completa.
create index partner_calls_ai_check_idx on public.partner_calls (ai_check_id)
  where ai_check_id is not null;
-- Failsafe dei job AI (anche in lettura).
create index partner_calls_ai_posizioni_in_corso_idx on public.partner_calls
  (ai_posizioni_avviata_at) where ai_posizioni_stato = 'in_corso';
create index partner_calls_ai_testi_in_corso_idx on public.partner_calls
  (ai_testi_avviata_at) where ai_testi_stato = 'in_corso';

-- ----------------------------------------------------------------------------
-- 2) Requisiti della call. criterio = CriterioPartner (validato dal backend;
--    qui solo forma e tipo); il testo libero non conta mai in automatico
--    (criterio manuale o NULL = non valutabile). copertura_nota solo da
--    template deterministici del backend.
-- ----------------------------------------------------------------------------
create table public.partner_call_requisiti (
  id                 uuid primary key default gen_random_uuid(),
  call_id            uuid not null references public.partner_calls (id) on delete cascade,
  origine            text not null
    constraint pcr_origine_check
    check (origine in ('ai_check', 'precheck', 'bando_partenariato', 'regola_finanziaria',
                       'manuale')),
  rif_origine        text
    constraint pcr_rif_origine_check
    check (rif_origine is null or char_length(rif_origine) <= 40),
  etichetta          text not null
    constraint pcr_etichetta_check check (char_length(etichetta) between 1 and 60),
  testo              text not null
    constraint pcr_testo_check check (char_length(testo) between 3 and 500),
  criterio           jsonb
    constraint pcr_criterio_check
    check (criterio is null
           or (jsonb_typeof(criterio) = 'object'
               and coalesce(criterio ->> 'tipo', '') in ('tipo_soggetto', 'tag', 'regione',
                                                         'paese', 'ateco', 'settore',
                                                         'dimensione', 'certificazione',
                                                         'esperienza', 'regola_finanziaria',
                                                         'manuale'))),
  ambito             text not null default 'consorzio'
    constraint pcr_ambito_check check (ambito in ('consorzio', 'ogni_membro')),
  copertura_creatore text
    constraint pcr_copertura_creatore_check
    check (copertura_creatore is null
           or copertura_creatore in ('coperto', 'non_coperto', 'dato_mancante', 'incerto',
                                     'non_valutabile')),
  copertura_fonte    text
    constraint pcr_copertura_fonte_check
    check (copertura_fonte is null
           or copertura_fonte in ('registro', 'bilanci', 'dichiarato', 'ai_check', 'nessuna')),
  copertura_nota     text
    constraint pcr_copertura_nota_check
    check (copertura_nota is null or char_length(copertura_nota) <= 300),
  cercato            boolean not null default false,
  citazione          jsonb
    constraint pcr_citazione_check
    check (citazione is null or jsonb_typeof(citazione) = 'object'),
  ordine             smallint not null default 0,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  -- Etichette brevi stabili («A», «B», …): uniche nella call.
  constraint pcr_etichetta_unica unique (call_id, etichetta)
);

comment on table public.partner_call_requisiti is
  'Requisiti della call (C1): criterio tipizzato (CriterioPartner) + ambito consorzio (gap: basta un membro) | ogni_membro (filtro rigido). Scritti SOLO da fn_partner_call_sostituisci_requisiti (replace-all). Le coperture del creatore NON escono mai verso terzi.';
comment on column public.partner_call_requisiti.etichetta is
  'Etichetta breve STABILE («A», «B», …, poi «AA»): assegnata in ordine alla sostituzione se il client non la indica; un requisito che conserva il suo id conserva l''etichetta.';
comment on column public.partner_call_requisiti.criterio is
  'CriterioPartner {tipo, …} validato dal backend. tipo regola_finanziaria solo con origine regola_finanziaria e con la regola IDENTICA a una di regole_partenariato confermate (Q11: niente regole finanziarie manuali).';
comment on column public.partner_call_requisiti.copertura_nota is
  'Solo da template deterministici (mai testo LLM, mai valori esatti di bilancio).';

create trigger trg_pcr_updated_at
  before update on public.partner_call_requisiti
  for each row execute function public.set_updated_at();

create index pcr_call_idx on public.partner_call_requisiti (call_id, ordine);

-- ----------------------------------------------------------------------------
-- 3) Posizioni cercate. Codici (tipi soggetto, competenze) validati dal
--    backend sul vocabolario; regioni = id della lookup del DB secondario.
-- ----------------------------------------------------------------------------
create table public.partner_call_posizioni (
  id                   uuid primary key default gen_random_uuid(),
  call_id              uuid not null references public.partner_calls (id) on delete cascade,
  titolo               text not null
    constraint pcp_titolo_check check (char_length(titolo) between 3 and 120),
  ruolo                text not null default 'partner'
    constraint pcp_ruolo_check check (ruolo in ('capofila', 'partner')),
  tipi_soggetto        text[] not null default '{}'
    constraint pcp_tipi_soggetto_check
    check (cardinality(tipi_soggetto) <= 5 and array_position(tipi_soggetto, null) is null),
  competenze           text[] not null default '{}'
    constraint pcp_competenze_check
    check (cardinality(competenze) <= 10 and array_position(competenze, null) is null),
  ateco_divisioni      text[] not null default '{}'
    constraint pcp_ateco_divisioni_check
    check (cardinality(ateco_divisioni) <= 10
           and array_to_string(ateco_divisioni, ',', '*') ~ '^([0-9]{2}(,[0-9]{2})*)?$'),
  regioni              integer[] not null default '{}'
    constraint pcp_regioni_check
    check (cardinality(regioni) <= 21 and array_position(regioni, null) is null),
  territorio_modalita  text not null default 'qualsiasi'
    constraint pcp_territorio_modalita_check
    check (territorio_modalita in ('qualsiasi', 'sede_attuale', 'sede_entro_erogazione')),
  paesi                text[] not null default '{}'
    constraint pcp_paesi_check
    check (cardinality(paesi) <= 30
           and array_to_string(paesi, ',', '*') ~ '^([A-Z]{2}(,[A-Z]{2})*)?$'),
  dimensioni           text[] not null default '{}'
    constraint pcp_dimensioni_check
    check (dimensioni <@ array['micro', 'piccola', 'media', 'grande']::text[]),
  quota_ipotizzata_pct numeric(5, 2)
    constraint pcp_quota_ipotizzata_pct_check
    check (quota_ipotizzata_pct is null
           or (quota_ipotizzata_pct > 0 and quota_ipotizzata_pct <= 100)),
  numero               smallint not null default 1
    constraint pcp_numero_check check (numero between 1 and 10),
  requisiti_ids        uuid[] not null default '{}'
    constraint pcp_requisiti_ids_check
    check (cardinality(requisiti_ids) <= 20 and array_position(requisiti_ids, null) is null),
  note                 text
    constraint pcp_note_check check (note is null or char_length(note) <= 500),
  ordine               smallint not null default 0,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

comment on table public.partner_call_posizioni is
  'Posizioni cercate dalla call (C1). Scritte SOLO da fn_partner_call_sostituisci_posizioni (replace-all; da WP7 ridefinita con la stessa firma per non rimuovere posizioni con candidature attive). Un id passato dal client si conserva.';
comment on column public.partner_call_posizioni.requisiti_ids is
  'Requisiti (partner_call_requisiti.id della STESSA call) che la posizione copre. Senza FK (array): li mantiene coerenti fn_partner_call_sostituisci_requisiti.';
comment on column public.partner_call_posizioni.regioni is
  'Id della lookup regioni del DB secondario (nessuna FK).';
comment on column public.partner_call_posizioni.paesi is
  'Codici ISO-3166 alpha-2 maiuscoli.';

create trigger trg_pcp_updated_at
  before update on public.partner_call_posizioni
  for each row execute function public.set_updated_at();

create index pcp_call_idx on public.partner_call_posizioni (call_id, ordine);

-- ----------------------------------------------------------------------------
-- 4) Versioni: uno snapshot (fn_partner_call_contenuto) alla pubblicazione e a
--    ogni modifica successiva. Immutabili; la cancellazione arriva solo dal
--    cascade della call.
-- ----------------------------------------------------------------------------
create table public.partner_call_versioni (
  id            bigint generated always as identity primary key,
  call_id       uuid not null references public.partner_calls (id) on delete cascade,
  versione      integer not null
    constraint pcv_versione_check check (versione >= 1),
  snapshot      jsonb not null
    constraint pcv_snapshot_check check (jsonb_typeof(snapshot) = 'object'),
  modificato_da uuid not null,
  created_at    timestamptz not null default now(),
  constraint pcv_call_versione_key unique (call_id, versione)
);

comment on table public.partner_call_versioni is
  'Storico della call dopo la pubblicazione: {call, requisiti, posizioni} a ogni versione (contiene anche i campi RISERVATI: verso terzi solo tramite proiezione). Righe immutabili (trigger); il cascade dalla call le cancella.';

create or replace function public.fn_partner_call_versioni_immutabile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  raise exception 'Le versioni della call non si modificano' using detail = 'versione_immutabile';
end;
$$;

comment on function public.fn_partner_call_versioni_immutabile() is
  'Trigger: rifiuta UPDATE su partner_call_versioni (lo storico non cambia). Detail: versione_immutabile.';

create trigger trg_pcv_immutabile
  before update on public.partner_call_versioni
  for each row execute function public.fn_partner_call_versioni_immutabile();

-- ----------------------------------------------------------------------------
-- 5) Segnalazioni DSA (forma definitiva, SENZA FK: registro che sopravvive
--    alle cancellazioni). Inserite dal backend (service_role); 23505 sulla
--    segnalazione aperta = «Hai già segnalato questo contenuto». Stati:
--    ricevuta → in_esame → decisa → ricorso_presentato → ricorso_deciso. WP7
--    aggiunge oggetto_tipo messaggio, WP9 le colonne di decisione e ricorso.
-- ----------------------------------------------------------------------------
create table public.partner_segnalazioni (
  id                    uuid primary key default gen_random_uuid(),
  oggetto_tipo          text not null
    constraint ps_oggetto_tipo_check check (oggetto_tipo in ('call', 'profilo')),
  oggetto_id            text not null
    constraint ps_oggetto_id_check check (char_length(oggetto_id) between 1 and 100),
  segnalante_user_id    uuid not null,
  segnalante_company_id uuid,
  motivo                text not null
    constraint ps_motivo_check
    check (motivo in ('contenuto_illecito', 'dati_personali', 'spam_pubblicita',
                      'contatti_nel_testo', 'discriminatorio', 'impersonificazione', 'altro')),
  descrizione           text not null
    constraint ps_descrizione_check check (char_length(descrizione) between 10 and 2000),
  buona_fede            boolean not null
    constraint ps_buona_fede_check check (buona_fede),
  contenuto_snapshot    jsonb not null
    constraint ps_contenuto_snapshot_check check (jsonb_typeof(contenuto_snapshot) = 'object'),
  stato                 text not null default 'ricevuta'
    constraint ps_stato_check
    check (stato in ('ricevuta', 'in_esame', 'decisa', 'ricorso_presentato', 'ricorso_deciso')),
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

comment on table public.partner_segnalazioni is
  'Registro DSA delle segnalazioni (call e profili; WP7 aggiunge i messaggi). SENZA FK: sopravvive alle cancellazioni del contenuto e del segnalante. contenuto_snapshot = il contenuto come lo vedeva il segnalante (evidenza). Conservazione dichiarata: 24 mesi (Q22).';
comment on column public.partner_segnalazioni.oggetto_id is
  'Riferimento testuale all''oggetto: id pubblico della call, codice_pubblico del profilo (da WP7 anche l''id del messaggio, bigint).';
comment on column public.partner_segnalazioni.buona_fede is
  'Dichiarazione di buona fede del segnalante (DSA art. 16.2.d): sempre true.';

create trigger trg_partner_segnalazioni_updated_at
  before update on public.partner_segnalazioni
  for each row execute function public.set_updated_at();

-- Una segnalazione aperta per segnalante e oggetto.
create unique index ps_una_aperta on public.partner_segnalazioni
  (oggetto_tipo, oggetto_id, segnalante_user_id)
  where stato in ('ricevuta', 'in_esame');
-- Coda di moderazione (WP9) e storico per oggetto.
create index ps_coda_idx on public.partner_segnalazioni (stato, created_at);
create index ps_oggetto_idx on public.partner_segnalazioni (oggetto_tipo, oggetto_id);

-- ----------------------------------------------------------------------------
-- 6) Funzioni interne di identità (T5, Q9) con la STESSA semantica delle
--    verifiche dentro fn_partner_consenso (0035, non modificata):
--      - identità: company_data con piva_fetched = partita_iva ATTUALE
--        dell'azienda, lower(btrim(stato_impresa)) = 'attiva' e, se
--        p_richiedi_non_sandbox (NULL vale true: fail-closed), non sandbox;
--      - rappresentante: il titolare con CF verificato compare tra i legali
--        rappresentanti dell'azienda in company_people (upper(btrim()) sui CF).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariato_identita_ok(
  p_company              uuid,
  p_richiedi_non_sandbox boolean
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.company_data cd
    join public.company_profiles cp on cp.id = cd.company_profile_id
    where cd.company_profile_id = p_company
      and cd.piva_fetched = cp.partita_iva
      and lower(btrim(cd.stato_impresa)) = 'attiva'
      and (not coalesce(p_richiedi_non_sandbox, true) or not cd.sandbox)
  );
$$;

comment on function public.fn_partenariato_identita_ok(uuid, boolean) is
  'Identità dell''azienda verificata sul Registro Imprese (T5): company_data con piva_fetched = partita_iva attuale, impresa attiva e, se richiesto (NULL = true), non sandbox. Stessa semantica della verifica in fn_partner_consenso (0035).';

create or replace function public.fn_partenariato_rappresentante_ok(
  p_owner   uuid,
  p_company uuid
)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.profiles pr
    join public.company_people pe
      on upper(btrim(pe.codice_fiscale)) = upper(btrim(pr.codice_fiscale))
    where pr.id = p_owner
      and pr.cf_verified_at is not null
      and pe.company_profile_id = p_company
      and pe.is_legale_rappresentante
  );
$$;

comment on function public.fn_partenariato_rappresentante_ok(uuid, uuid) is
  'Il titolare (CF verificato) compare tra i legali rappresentanti dell''azienda in company_people (Q9: condizione necessaria, non sufficiente, per il nominativo). Stessa semantica della verifica in fn_partner_consenso (0035).';

-- ----------------------------------------------------------------------------
-- 7) Funzioni interne delle RPC.
-- ----------------------------------------------------------------------------

-- Lock owner → azienda (primi due passi dell'ordine globale). p_viva = true:
-- solo aziende non eliminate né archiviate.
create or replace function public.fn_partner_call_blocca_azienda(
  p_owner   uuid,
  p_company uuid,
  p_viva    boolean
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform 1 from public.profiles where id = p_owner for update;
  if not found then
    raise exception 'Titolare non trovato' using detail = 'owner_not_found';
  end if;

  perform 1 from public.company_profiles
  where id = p_company and parent_id = p_owner
    and (not coalesce(p_viva, true) or (deleted_at is null and archived_at is null))
  for no key update;
  if not found then
    raise exception 'Azienda non trovata' using detail = 'company_not_found';
  end if;
end;
$$;

comment on function public.fn_partner_call_blocca_azienda(uuid, uuid, boolean) is
  'Interna: lock della riga profiles dell''owner (FOR UPDATE) e poi della sua azienda (FOR NO KEY UPDATE; p_viva = solo se non eliminata né archiviata). Detail: owner_not_found | company_not_found.';

-- Lock owner → azienda → call (id + azienda attiva + owner).
create or replace function public.fn_partner_call_blocca(
  p_owner   uuid,
  p_company uuid,
  p_call    uuid,
  p_viva    boolean
)
returns public.partner_calls
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call public.partner_calls%rowtype;
begin
  perform public.fn_partner_call_blocca_azienda(p_owner, p_company, p_viva);

  select * into v_call from public.partner_calls
  where id = p_call and company_profile_id = p_company and family_parent_id = p_owner
  for update;
  if not found then
    raise exception 'Call di partenariato non trovata' using detail = 'call_not_found';
  end if;
  return v_call;
end;
$$;

comment on function public.fn_partner_call_blocca(uuid, uuid, uuid, boolean) is
  'Interna: fn_partner_call_blocca_azienda e poi la call FOR UPDATE, cercata con id + company_profile_id = azienda attiva + family_parent_id = owner (un''altra azienda dello stesso owner → call_not_found). Detail: owner_not_found | company_not_found | call_not_found.';

-- Campi scrivibili con p_dati / p_campi: in bozza tutti gli editabili, dopo la
-- pubblicazione la whitelist (C6).
create or replace function public.fn_partner_call_campi_editabili(p_pubblicata boolean)
returns text[]
language sql
immutable
security definer
set search_path = public
as $$
  select case when p_pubblicata then
    array['descrizione_pubblica', 'dettagli_riservati', 'profilo_partner_ideale',
          'scadenza_call', 'visibilita', 'budget_fascia', 'budget_progetto_eur',
          'quota_creatore_pct']
  else
    array['titolo', 'descrizione_pubblica', 'dettagli_riservati', 'profilo_partner_ideale',
          'scadenza_call', 'visibilita', 'budget_fascia', 'budget_progetto_eur',
          'quota_creatore_pct', 'ruolo_creatore', 'forma_aggregazione_prevista', 'anonima',
          'override_non_ammesso_motivo', 'partenariato_ref', 'ai_check_id', 'wizard_passo']
  end;
$$;

comment on function public.fn_partner_call_campi_editabili(boolean) is
  'Interna: campi della call scrivibili da fn_partner_call_crea_bozza / fn_partner_call_aggiorna (bozza) o, con p_pubblicata, la whitelist dopo la pubblicazione.';

-- Contenuto versionabile della call: la riga senza job AI, versione e
-- updated_at, più requisiti e posizioni in ordine (senza timestamp).
create or replace function public.fn_partner_call_contenuto(p_call uuid)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'call', to_jsonb(c) - array['ai_posizioni_stato', 'ai_posizioni_proposta',
                                'ai_posizioni_avviata_at', 'ai_posizioni_esecuzione_id',
                                'ai_posizioni_errore', 'ai_testi_stato', 'ai_testi_proposta',
                                'ai_testi_avviata_at', 'ai_testi_esecuzione_id',
                                'ai_testi_errore', 'versione', 'updated_at'],
    'requisiti', coalesce((
      select jsonb_agg(to_jsonb(r) - array['call_id', 'created_at', 'updated_at']
                       order by r.ordine, r.id)
      from public.partner_call_requisiti r where r.call_id = c.id), '[]'::jsonb),
    'posizioni', coalesce((
      select jsonb_agg(to_jsonb(p) - array['call_id', 'created_at', 'updated_at']
                       order by p.ordine, p.id)
      from public.partner_call_posizioni p where p.call_id = c.id), '[]'::jsonb))
  from public.partner_calls c
  where c.id = p_call;
$$;

comment on function public.fn_partner_call_contenuto(uuid) is
  'Interna: {call, requisiti, posizioni} della call (senza job AI, versione e timestamp di riga): snapshot delle versioni e confronto «è cambiato qualcosa».';

-- Nuova versione: versione + 1 e snapshot. Ritorna il numero.
create or replace function public.fn_partner_call_nuova_versione(p_call uuid, p_attore uuid)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_versione integer;
begin
  update public.partner_calls set versione = versione + 1
  where id = p_call
  returning versione into v_versione;

  insert into public.partner_call_versioni (call_id, versione, snapshot, modificato_da)
  values (p_call, v_versione, public.fn_partner_call_contenuto(p_call), p_attore);
  return v_versione;
end;
$$;

comment on function public.fn_partner_call_nuova_versione(uuid, uuid) is
  'Interna (chiamata con la call già bloccata): versione + 1 e riga in partner_call_versioni con fn_partner_call_contenuto. Ritorna la nuova versione.';

-- Call attive dell'owner per il pool dei limiti (Q1): pubblicate e sospese
-- delle sue aziende vive. Stesso conteggio in pubblicazione e nello snapshot.
create or replace function public.fn_partner_calls_attive_usate(p_owner uuid)
returns integer
language sql
stable
security definer
set search_path = public
as $$
  select count(*)::integer
  from public.partner_calls pc
  join public.company_profiles cp on cp.id = pc.company_profile_id
  where pc.family_parent_id = p_owner
    and pc.stato in ('pubblicata', 'sospesa_moderazione')
    and cp.deleted_at is null and cp.archived_at is null;
$$;

comment on function public.fn_partner_calls_attive_usate(uuid) is
  'Interna: call pubblicate o sospese delle aziende vive dell''owner (pool di Q1). Usata da fn_partner_call_pubblica (sotto il lock dell''owner) e da fn_partenariati_snapshot.';

-- Limite di call attive del piano: NULL = illimitato; chiave assente o jsonb
-- NULL = 0 (fail-closed).
create or replace function public.fn_partner_call_limite_attive(p_owner uuid)
returns integer
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_lim jsonb;
begin
  v_lim := public.fn_partenariati_limiti(p_owner);
  if v_lim is null or jsonb_typeof(v_lim) <> 'object' or not (v_lim ? 'calls_attive_max') then
    return 0;
  end if;
  return (v_lim ->> 'calls_attive_max')::integer;
end;
$$;

comment on function public.fn_partner_call_limite_attive(uuid) is
  'Interna: calls_attive_max di fn_partenariati_limiti (0036). NULL = illimitato; risposta senza la chiave = 0 (fail-closed).';

-- Snapshot delle regole valido (forma minima; i dettagli li valida il
-- backend con RegoleCallSnapshot): oggetto versione 1, al massimo 256 KB,
-- modalita presente, voci con origine_voce confermata | modificata |
-- aggiunta, nessuna regola finanziaria aggiunta a mano (Q11).
create or replace function public.fn_partner_call_regole_valide(p_regole jsonb)
returns boolean
language plpgsql
immutable
security definer
set search_path = public
as $$
declare
  v_chiave text;
  v_valore jsonb;
begin
  if p_regole is null or jsonb_typeof(p_regole) <> 'object'
     or p_regole -> 'versione' is distinct from '1'::jsonb
     or octet_length(p_regole::text) > 262144
     or jsonb_typeof(p_regole -> 'fonte') not in ('object', 'null')
     or jsonb_typeof(p_regole -> 'modalita') is distinct from 'object' then
    return false;
  end if;

  foreach v_chiave in array array['modalita', 'costituzione', 'partner_min', 'partner_max']
  loop
    v_valore := p_regole -> v_chiave;
    continue when v_valore is null or jsonb_typeof(v_valore) = 'null';
    if jsonb_typeof(v_valore) <> 'object'
       or coalesce(v_valore ->> 'origine_voce', '') not in ('confermata', 'modificata',
                                                            'aggiunta') then
      return false;
    end if;
  end loop;

  foreach v_chiave in array array['forme_ammesse', 'composizione', 'quote', 'vincoli',
                                  'regole_finanziarie', 'documenti_richiesti']
  loop
    v_valore := p_regole -> v_chiave;
    continue when v_valore is null or jsonb_typeof(v_valore) = 'null';
    if jsonb_typeof(v_valore) <> 'array' then
      return false;
    end if;
    if jsonb_array_length(v_valore) > 100
       or exists (
         select 1 from jsonb_array_elements(v_valore) e
         where jsonb_typeof(e) <> 'object'
            or coalesce(e ->> 'origine_voce', '') not in ('confermata', 'modificata', 'aggiunta')
            or (v_chiave = 'regole_finanziarie' and e ->> 'origine_voce' = 'aggiunta')
       ) then
      return false;
    end if;
  end loop;
  return true;
end;
$$;

comment on function public.fn_partner_call_regole_valide(jsonb) is
  'Interna: forma minima dello snapshot delle regole (oggetto versione 1 ≤ 256 KB, modalita presente, ogni voce con origine_voce confermata | modificata | aggiunta, niente regole finanziarie aggiunte: Q11).';

-- Regola finanziaria di un requisito: deve essere IDENTICA (sui campi di
-- RegolaFinanziaria) a una regola dello snapshot confermato (Q11: niente
-- regole finanziarie manuali, niente soglie ritoccate).
create or replace function public.fn_partner_call_regola_ok(p_regola jsonb, p_regole jsonb)
returns boolean
language sql
immutable
security definer
set search_path = public
as $$
  select coalesce(
    jsonb_typeof(p_regola) = 'object'
    and p_regola ->> 'id' is not null
    and exists (
      select 1
      from jsonb_array_elements(
        case when jsonb_typeof(p_regole -> 'regole_finanziarie') = 'array'
             then p_regole -> 'regole_finanziarie' else '[]'::jsonb end) v
      where jsonb_typeof(v) = 'object'
        and v ->> 'origine_voce' in ('confermata', 'modificata')
        and not exists (
          select 1
          from unnest(array['id', 'descrizione', 'ambito', 'numeratore', 'denominatore',
                            'operatore', 'soglia', 'soglia_variabile', 'soglia_coefficiente',
                            'unita']) k
          where nullif(p_regola -> k, 'null'::jsonb) is distinct from nullif(v -> k, 'null'::jsonb)
        )
    ), false);
$$;

comment on function public.fn_partner_call_regola_ok(jsonb, jsonb) is
  'Interna: la regola finanziaria di un requisito coincide (id, descrizione, ambito, numeratore, denominatore, operatore, soglia, soglia_variabile, soglia_coefficiente, unita) con una regola confermata o modificata dello snapshot (Q11).';

-- ----------------------------------------------------------------------------
-- 8) fn_partner_call_crea_bozza — nuova bozza sull'azienda attiva.
--    p_bando: {id, slug, titolo, scadenza?, programma_id?, tipologia_id?,
--    stato_effettivo?} (dal catalogo, verificato dal backend). p_dati: campi
--    della call (fn_partner_call_campi_editabili(false)); ruolo_creatore
--    obbligatorio. Nell'ordine: attore = titolare; parametri; lock owner e
--    azienda viva; piano con 0 call attive → piano_non_include_call; bozze
--    dell'azienda ≥ p_max_bozze (NULL = nessun tetto) → troppe_bozze; insert
--    (una call non chiusa per azienda × bando → call_gia_presente); audit
--    partenariato.call_creata. Ritorna la riga della call.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_crea_bozza(
  p_owner     uuid,
  p_company   uuid,
  p_attore    uuid,
  p_bando     jsonb,
  p_dati      jsonb,
  p_max_bozze integer
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_dati        jsonb := coalesce(p_dati, '{}'::jsonb);
  v_chiave      text;
  v_bando_id    integer;
  v_scadenza    date;
  v_programma   integer;
  v_tipologia   integer;
  v_limite      integer;
  v_n           integer;
  v_new         public.partner_calls%rowtype;
  v_call        public.partner_calls%rowtype;
  v_vincolo     text;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  if jsonb_typeof(p_bando) is distinct from 'object' then
    raise exception 'Dati del bando non validi' using detail = 'parametri_non_validi';
  end if;
  if exists (select 1 from jsonb_object_keys(p_bando) k
                where k not in ('id', 'slug', 'titolo', 'scadenza', 'programma_id',
                                'tipologia_id', 'stato_effettivo'))
     or coalesce(p_bando ->> 'id', '') !~ '^[0-9]{1,9}$'
     or jsonb_typeof(p_bando -> 'slug') is distinct from 'string'
     or btrim(p_bando ->> 'slug') = ''
     or jsonb_typeof(p_bando -> 'titolo') is distinct from 'string'
     or btrim(p_bando ->> 'titolo') = '' then
    raise exception 'Dati del bando non validi' using detail = 'parametri_non_validi';
  end if;
  begin
    v_bando_id := (p_bando ->> 'id')::integer;
    v_scadenza := (p_bando ->> 'scadenza')::date;
    v_programma := (p_bando ->> 'programma_id')::integer;
    v_tipologia := (p_bando ->> 'tipologia_id')::integer;
  exception when data_exception then
    raise exception 'Dati del bando non validi' using detail = 'parametri_non_validi';
  end;

  if jsonb_typeof(v_dati) <> 'object' then
    raise exception 'Dati della call non validi' using detail = 'parametri_non_validi';
  end if;
  select k into v_chiave from jsonb_object_keys(v_dati) k
  where not (k = any (public.fn_partner_call_campi_editabili(false)))
  limit 1;
  if v_chiave is not null then
    raise exception 'Campo non modificabile: %', v_chiave using detail = 'campo_non_modificabile';
  end if;
  begin
    v_new := jsonb_populate_record(null::public.partner_calls, v_dati);
  exception when data_exception then
    raise exception 'Dati della call non validi' using detail = 'dati_non_validi';
  end;
  if v_new.ruolo_creatore is null then
    raise exception 'Indica il ruolo della tua azienda nel partenariato'
      using detail = 'dati_non_validi';
  end if;

  perform public.fn_partner_call_blocca_azienda(p_owner, p_company, true);

  v_limite := public.fn_partner_call_limite_attive(p_owner);
  if v_limite is not null and v_limite <= 0 then
    raise exception 'Il tuo piano non include la creazione di call di partenariato'
      using detail = 'piano_non_include_call';
  end if;

  if p_max_bozze is not null then
    select count(*) into v_n from public.partner_calls
    where company_profile_id = p_company and stato = 'bozza';
    if v_n >= greatest(p_max_bozze, 0) then
      raise exception 'Hai troppe call in bozza per questa azienda: completane o annullane una'
        using detail = 'troppe_bozze';
    end if;
  end if;

  if v_new.ai_check_id is not null and not exists (
    select 1 from public.ai_checks
    where id = v_new.ai_check_id and company_profile_id = p_company and bando_id = v_bando_id
  ) then
    raise exception 'AI-check non valido per questa call' using detail = 'dati_non_validi';
  end if;

  begin
    insert into public.partner_calls
      (company_profile_id, family_parent_id, creato_da,
       bando_id, bando_slug, bando_titolo, bando_scadenza, bando_programma_id,
       bando_tipologia_id, bando_stato_effettivo, bando_verificato_at,
       ruolo_creatore, forma_aggregazione_prevista, anonima, titolo, descrizione_pubblica,
       dettagli_riservati, profilo_partner_ideale, budget_fascia, budget_progetto_eur,
       quota_creatore_pct, scadenza_call, visibilita, override_non_ammesso_motivo,
       partenariato_ref, ai_check_id, wizard_passo)
    values
      (p_company, p_owner, p_attore,
       v_bando_id, btrim(p_bando ->> 'slug'), btrim(p_bando ->> 'titolo'), v_scadenza,
       v_programma, v_tipologia, p_bando ->> 'stato_effettivo', now(),
       v_new.ruolo_creatore, v_new.forma_aggregazione_prevista, coalesce(v_new.anonima, true),
       v_new.titolo, v_new.descrizione_pubblica, v_new.dettagli_riservati,
       v_new.profilo_partner_ideale, v_new.budget_fascia, v_new.budget_progetto_eur,
       v_new.quota_creatore_pct, v_new.scadenza_call, coalesce(v_new.visibilita, 'pubblica'),
       v_new.override_non_ammesso_motivo, v_new.partenariato_ref, v_new.ai_check_id,
       coalesce(v_new.wizard_passo, 1))
    returning * into v_call;
  exception
    when unique_violation then
      get stacked diagnostics v_vincolo = constraint_name;
      if v_vincolo = 'partner_calls_una_attiva' then
        raise exception 'Hai già una call per questo bando' using detail = 'call_gia_presente';
      end if;
      raise;
    when check_violation or not_null_violation or foreign_key_violation
         or data_exception then
      raise exception 'Dati della call non validi' using detail = 'dati_non_validi';
  end;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.call_creata', p_owner, p_owner,
          jsonb_build_object('call_id', v_call.id, 'company_profile_id', p_company,
                             'bando_id', v_call.bando_id));

  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_crea_bozza(uuid, uuid, uuid, jsonb, jsonb, integer) is
  'Crea una bozza di call sull''azienda viva del titolare (lock owner → azienda): piano con 0 call attive → piano_non_include_call; bozze dell''azienda ≥ p_max_bozze (NULL = nessun tetto) → troppe_bozze; una call non chiusa per azienda × bando → call_gia_presente; audit partenariato.call_creata. Ritorna la riga. Detail: attore_non_titolare | parametri_non_validi | campo_non_modificabile | dati_non_validi | owner_not_found | company_not_found | piano_non_include_call | troppe_bozze | call_gia_presente.';

-- ----------------------------------------------------------------------------
-- 9) fn_partner_call_aggiorna — aggiornamento parziale (p_campi: solo le
--    chiavi da cambiare; JSON null = svuota). In bozza tutti i campi
--    editabili; in pubblicata la whitelist (un altro campo che CAMBIA →
--    campo_non_modificabile; lo stesso valore è ammesso), scadenza cambiata
--    tra oggi (Europe/Rome) e la scadenza del bando, testi e scadenza non
--    svuotabili (call_incompleta), versione + 1 con snapshot e audit
--    partenariato.call_modificata se qualcosa è cambiato. Altri stati →
--    stato_call_non_valido. Ritorna la riga.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_aggiorna(
  p_owner   uuid,
  p_company uuid,
  p_attore  uuid,
  p_call    uuid,
  p_campi   jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi     constant date := (now() at time zone 'Europe/Rome')::date;
  v_call     public.partner_calls%rowtype;
  v_new      public.partner_calls%rowtype;
  v_chiave   text;
  v_cambiati jsonb;
  v_prima    jsonb;
  v_versione integer;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;
  if jsonb_typeof(p_campi) is distinct from 'object' then
    raise exception 'Dati della call non validi' using detail = 'parametri_non_validi';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato not in ('bozza', 'pubblicata') then
    raise exception 'La call non è modificabile in questo stato'
      using detail = 'stato_call_non_valido';
  end if;

  select k into v_chiave from jsonb_object_keys(p_campi) k
  where not (k = any (public.fn_partner_call_campi_editabili(false)))
  limit 1;
  if v_chiave is not null then
    raise exception 'Campo non modificabile: %', v_chiave using detail = 'campo_non_modificabile';
  end if;

  begin
    v_new := jsonb_populate_record(v_call, p_campi);
  exception when data_exception then
    raise exception 'Dati della call non validi' using detail = 'dati_non_validi';
  end;

  if v_call.stato = 'pubblicata' then
    select k into v_chiave from jsonb_object_keys(p_campi) k
    where not (k = any (public.fn_partner_call_campi_editabili(true)))
      and (to_jsonb(v_new) -> k) is distinct from (to_jsonb(v_call) -> k)
    limit 1;
    if v_chiave is not null then
      raise exception 'Dopo la pubblicazione questo campo non si modifica: %', v_chiave
        using detail = 'campo_non_modificabile';
    end if;
    if nullif(btrim(v_new.descrizione_pubblica), '') is null or v_new.scadenza_call is null then
      raise exception 'Una call pubblicata deve avere descrizione e scadenza'
        using detail = 'call_incompleta';
    end if;
    if v_new.scadenza_call is distinct from v_call.scadenza_call
       and (v_new.scadenza_call < v_oggi
            or (v_call.bando_scadenza is not null and v_new.scadenza_call > v_call.bando_scadenza))
    then
      raise exception 'La scadenza della call deve essere tra oggi e la scadenza del bando'
        using detail = 'scadenza_call_non_valida';
    end if;
  end if;

  if v_new.ai_check_id is distinct from v_call.ai_check_id
     and v_new.ai_check_id is not null
     and not exists (
       select 1 from public.ai_checks
       where id = v_new.ai_check_id and company_profile_id = p_company
         and bando_id = v_call.bando_id
     ) then
    raise exception 'AI-check non valido per questa call' using detail = 'dati_non_validi';
  end if;

  -- Nulla cambia → nessuna scrittura (né versione).
  select jsonb_agg(k order by k) into v_cambiati
  from jsonb_object_keys(p_campi) k
  where (to_jsonb(v_new) -> k) is distinct from (to_jsonb(v_call) -> k);
  if v_cambiati is null then
    return to_jsonb(v_call);
  end if;

  v_prima := public.fn_partner_call_contenuto(p_call);
  begin
    update public.partner_calls
    set titolo                      = v_new.titolo,
        descrizione_pubblica        = v_new.descrizione_pubblica,
        dettagli_riservati          = v_new.dettagli_riservati,
        profilo_partner_ideale      = v_new.profilo_partner_ideale,
        scadenza_call               = v_new.scadenza_call,
        visibilita                  = v_new.visibilita,
        budget_fascia               = v_new.budget_fascia,
        budget_progetto_eur         = v_new.budget_progetto_eur,
        quota_creatore_pct          = v_new.quota_creatore_pct,
        ruolo_creatore              = v_new.ruolo_creatore,
        forma_aggregazione_prevista = v_new.forma_aggregazione_prevista,
        anonima                     = v_new.anonima,
        override_non_ammesso_motivo = v_new.override_non_ammesso_motivo,
        partenariato_ref            = v_new.partenariato_ref,
        ai_check_id                 = v_new.ai_check_id,
        wizard_passo                = v_new.wizard_passo
    where id = p_call
    returning * into v_call;
  exception
    when check_violation or not_null_violation or foreign_key_violation
         or data_exception then
      raise exception 'Dati della call non validi' using detail = 'dati_non_validi';
  end;

  if v_call.stato = 'pubblicata'
     and public.fn_partner_call_contenuto(p_call) is distinct from v_prima then
    v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);
    insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
    values (p_attore, 'partenariato.call_modificata', p_owner, p_owner,
            jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                               'versione', v_versione, 'campi', v_cambiati));
    select * into v_call from public.partner_calls where id = p_call;
  end if;

  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_aggiorna(uuid, uuid, uuid, uuid, jsonb) is
  'Aggiornamento parziale della call (lock owner → azienda viva → call): bozza = tutti i campi editabili; pubblicata = whitelist descrizione_pubblica, dettagli_riservati, profilo_partner_ideale, scadenza_call, visibilita, budget_fascia, budget_progetto_eur, quota_creatore_pct (altri campi che cambiano → campo_non_modificabile), con versione + 1, snapshot e audit partenariato.call_modificata (campi = quelli cambiati). Nulla cambia → nessuna scrittura. Ritorna la riga. Detail: attore_non_titolare | parametri_non_validi | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | campo_non_modificabile | dati_non_validi | call_incompleta | scadenza_call_non_valida.';

-- ----------------------------------------------------------------------------
-- 10) fn_partner_call_conferma_regole — snapshot delle regole confermato dal
--    creatore (solo bozza): regole_partenariato, esclusivita e
--    regole_confermate_at. Ritorna la riga.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_conferma_regole(
  p_owner       uuid,
  p_company     uuid,
  p_attore      uuid,
  p_call        uuid,
  p_regole      jsonb,
  p_esclusivita boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call public.partner_calls%rowtype;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato <> 'bozza' then
    raise exception 'Le regole si confermano solo prima della pubblicazione'
      using detail = 'stato_call_non_valido';
  end if;
  if p_esclusivita is null or not public.fn_partner_call_regole_valide(p_regole) then
    raise exception 'Regole di partenariato non valide' using detail = 'regole_non_valide';
  end if;

  update public.partner_calls
  set regole_partenariato  = p_regole,
      esclusivita          = p_esclusivita,
      regole_confermate_at = now()
  where id = p_call
  returning * into v_call;

  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_conferma_regole(uuid, uuid, uuid, uuid, jsonb, boolean) is
  'Conferma lo snapshot delle regole di partenariato (solo bozza; forma minima con fn_partner_call_regole_valide, niente regole finanziarie aggiunte a mano) e l''esclusività. Ritorna la riga. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | regole_non_valide.';

-- ----------------------------------------------------------------------------
-- 11) fn_partner_call_sostituisci_requisiti — replace-all (bozza o
--    pubblicata). p_requisiti: array (≤ 40) di oggetti con le chiavi id?,
--    etichetta?, testo, criterio?, ambito?, cercato?, origine, rif_origine?,
--    citazione?, copertura_creatore?, copertura_fonte?, copertura_nota?
--    (nient'altro).
--      - id: requisito esistente della call da conservare (id ed etichetta);
--        assente = nuovo requisito;
--      - etichetta: se assente, quella del requisito conservato oppure la
--        prima libera in ordine («A», «B», …, «Z», «AA»…); duplicati →
--        requisiti_non_validi;
--      - criterio regola_finanziaria solo con origine regola_finanziaria e
--        regola identica a una dello snapshot confermato (Q11);
--      - posizioni: requisiti_ids aggiornati (restano gli id conservati; un
--        requisito rimosso passa al nuovo con la stessa etichetta ESPLICITA,
--        altrimenti esce dalla posizione);
--      - in pubblicata almeno un requisito cercato (call_incompleta) e, se
--        il contenuto cambia, versione + 1 e audit.
--    Ritorna i requisiti in ordine.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_sostituisci_requisiti(
  p_owner     uuid,
  p_company   uuid,
  p_attore    uuid,
  p_call      uuid,
  p_requisiti jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi   constant text[] := array['id', 'etichetta', 'testo', 'criterio', 'ambito',
                                      'cercato', 'origine', 'rif_origine', 'citazione',
                                      'copertura_creatore', 'copertura_fonte',
                                      'copertura_nota'];
  v_call     public.partner_calls%rowtype;
  v_prima    jsonb;
  v_item     jsonb;
  v_ord      bigint;
  v_id       uuid;
  v_etich    text;
  v_creato   timestamptz;
  v_tenuti   uuid[] := '{}';
  v_usate    text[] := '{}';
  v_nuove    jsonb := '{}'::jsonb;   -- etichetta esplicita → id dei requisiti nuovi
  v_mappa    jsonb := '{}'::jsonb;   -- id rimosso → id nuovo con la stessa etichetta
  v_righe    jsonb := '[]'::jsonb;
  v_auto     integer := 0;
  v_x        integer;
  v_versione integer;
  v_esplicita_nuova boolean;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato not in ('bozza', 'pubblicata') then
    raise exception 'La call non è modificabile in questo stato'
      using detail = 'stato_call_non_valido';
  end if;
  if jsonb_typeof(p_requisiti) is distinct from 'array' or jsonb_array_length(p_requisiti) > 40
  then
    raise exception 'Requisiti non validi' using detail = 'requisiti_non_validi';
  end if;

  -- Prima passata: forma, id conservati, etichette esplicite o conservate.
  for v_item in select e from jsonb_array_elements(p_requisiti) e
  loop
    if jsonb_typeof(v_item) <> 'object' then
      raise exception 'Requisiti non validi' using detail = 'requisiti_non_validi';
    end if;
    if exists (select 1 from jsonb_object_keys(v_item) k where not (k = any (v_chiavi))) then
      raise exception 'Requisiti non validi' using detail = 'requisiti_non_validi';
    end if;
    v_id := null;
    if jsonb_typeof(v_item -> 'id') is distinct from 'null' and v_item ? 'id' then
      begin
        v_id := (v_item ->> 'id')::uuid;
      exception when data_exception then
        raise exception 'Requisiti non validi' using detail = 'requisiti_non_validi';
      end;
      if v_id = any (v_tenuti) or not exists (
        select 1 from public.partner_call_requisiti where id = v_id and call_id = p_call
      ) then
        raise exception 'Requisito non trovato in questa call'
          using detail = 'requisiti_non_validi';
      end if;
      v_tenuti := v_tenuti || v_id;
    end if;
    v_etich := nullif(btrim(v_item ->> 'etichetta'), '');
    if v_etich is null and v_id is not null then
      select etichetta into v_etich from public.partner_call_requisiti where id = v_id;
    end if;
    if v_etich is not null then
      if v_etich = any (v_usate) then
        raise exception 'Etichetta ripetuta: %', v_etich using detail = 'requisiti_non_validi';
      end if;
      v_usate := v_usate || v_etich;
    end if;
    -- Q11: una regola finanziaria viene solo dallo snapshot confermato.
    if v_item -> 'criterio' ->> 'tipo' = 'regola_finanziaria'
       and (v_item ->> 'origine' is distinct from 'regola_finanziaria'
            or v_call.regole_confermate_at is null
            or not public.fn_partner_call_regola_ok(v_item -> 'criterio' -> 'regola',
                                                    v_call.regole_partenariato)) then
      raise exception 'Le regole finanziarie vengono solo dalle regole del bando confermate'
        using detail = 'requisiti_non_validi';
    end if;
  end loop;

  -- Seconda passata: righe finali con id, etichetta e ordine.
  for v_item, v_ord in
    select e, o from jsonb_array_elements(p_requisiti) with ordinality as t(e, o)
  loop
    v_id := null;
    v_creato := now();
    if jsonb_typeof(v_item -> 'id') is distinct from 'null' and v_item ? 'id' then
      v_id := (v_item ->> 'id')::uuid;
      select created_at into v_creato from public.partner_call_requisiti where id = v_id;
    end if;
    v_etich := nullif(btrim(v_item ->> 'etichetta'), '');
    v_esplicita_nuova := v_etich is not null and v_id is null;
    if v_etich is null and v_id is not null then
      select etichetta into v_etich from public.partner_call_requisiti where id = v_id;
    end if;
    if v_etich is null then
      loop
        v_auto := v_auto + 1;
        v_x := v_auto;
        v_etich := '';
        while v_x > 0 loop
          v_x := v_x - 1;
          v_etich := chr(65 + v_x % 26) || v_etich;
          v_x := v_x / 26;
        end loop;
        exit when not (v_etich = any (v_usate));
      end loop;
      v_usate := v_usate || v_etich;
    end if;
    v_id := coalesce(v_id, gen_random_uuid());
    if v_esplicita_nuova then
      v_nuove := v_nuove || jsonb_build_object(v_etich, v_id);
    end if;
    v_righe := v_righe || jsonb_build_array(
      (v_item - 'id' - 'etichetta')
      || jsonb_build_object('id', v_id, 'etichetta', v_etich, 'ordine', v_ord - 1,
                            'created_at', v_creato));
  end loop;

  -- Requisiti rimossi con un nuovo requisito di pari etichetta esplicita.
  select coalesce(jsonb_object_agg(r.id::text, v_nuove -> r.etichetta), '{}'::jsonb)
    into v_mappa
  from public.partner_call_requisiti r
  where r.call_id = p_call and not (r.id = any (v_tenuti))
    and jsonb_typeof(v_nuove -> r.etichetta) = 'string';

  v_prima := public.fn_partner_call_contenuto(p_call);

  begin
    delete from public.partner_call_requisiti where call_id = p_call;
    insert into public.partner_call_requisiti
      (id, call_id, origine, rif_origine, etichetta, testo, criterio, ambito,
       copertura_creatore, copertura_fonte, copertura_nota, cercato, citazione, ordine,
       created_at)
    select r.id, p_call, r.origine, r.rif_origine, r.etichetta, r.testo, r.criterio,
           coalesce(r.ambito, 'consorzio'), r.copertura_creatore, r.copertura_fonte,
           r.copertura_nota, coalesce(r.cercato, false), r.citazione, r.ordine, r.created_at
    from jsonb_populate_recordset(null::public.partner_call_requisiti, v_righe) r;
  exception
    when check_violation or not_null_violation or unique_violation or data_exception then
      raise exception 'Requisiti non validi' using detail = 'requisiti_non_validi';
  end;

  update public.partner_call_posizioni p
  set requisiti_ids = array(
    select coalesce(case when u.e = any (v_tenuti) then u.e end,
                    (v_mappa ->> u.e::text)::uuid)
    from unnest(p.requisiti_ids) with ordinality as u(e, o)
    where u.e = any (v_tenuti) or v_mappa ? u.e::text
    order by u.o)
  where p.call_id = p_call;

  if v_call.stato = 'pubblicata' then
    if not exists (select 1 from public.partner_call_requisiti
                   where call_id = p_call and cercato) then
      raise exception 'Una call pubblicata deve cercare almeno un requisito'
        using detail = 'call_incompleta';
    end if;
    if public.fn_partner_call_contenuto(p_call) is distinct from v_prima then
      v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (p_attore, 'partenariato.call_modificata', p_owner, p_owner,
              jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                                 'versione', v_versione, 'campi', jsonb_build_array('requisiti')));
    end if;
  end if;

  return coalesce((
    select jsonb_agg(to_jsonb(r) order by r.ordine)
    from public.partner_call_requisiti r where r.call_id = p_call), '[]'::jsonb);
end;
$$;

comment on function public.fn_partner_call_sostituisci_requisiti(uuid, uuid, uuid, uuid, jsonb) is
  'Replace-all dei requisiti (bozza o pubblicata; lock owner → azienda viva → call): id passati conservati (con l''etichetta), etichette mancanti assegnate in ordine («A», «B», …) saltando quelle usate, regole finanziarie solo identiche allo snapshot confermato (Q11), requisiti_ids delle posizioni riallineati; in pubblicata ≥ 1 cercato e versione + 1 se il contenuto cambia. Ritorna i requisiti. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | requisiti_non_validi | call_incompleta.';

-- ----------------------------------------------------------------------------
-- 12) fn_partner_call_sostituisci_posizioni — replace-all (bozza o
--    pubblicata). p_posizioni: array (≤ 10) di oggetti con le chiavi id?,
--    titolo, ruolo?, tipi_soggetto?, competenze?, ateco_divisioni?, regioni?,
--    territorio_modalita?, paesi?, dimensioni?, quota_ipotizzata_pct?,
--    numero?, requisiti_ids?, note? (nient'altro). id = posizione esistente
--    della call da conservare. requisiti_ids ⊆ requisiti della call. In
--    pubblicata almeno una posizione (call_incompleta) e, se il contenuto
--    cambia, versione + 1 e audit. WP7 la ridefinisce con la STESSA firma
--    (niente rimozione di posizioni con candidature attive). Ritorna le
--    posizioni in ordine.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_sostituisci_posizioni(
  p_owner     uuid,
  p_company   uuid,
  p_attore    uuid,
  p_call      uuid,
  p_posizioni jsonb
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_chiavi   constant text[] := array['id', 'titolo', 'ruolo', 'tipi_soggetto', 'competenze',
                                      'ateco_divisioni', 'regioni', 'territorio_modalita',
                                      'paesi', 'dimensioni', 'quota_ipotizzata_pct', 'numero',
                                      'requisiti_ids', 'note'];
  v_call     public.partner_calls%rowtype;
  v_prima    jsonb;
  v_item     jsonb;
  v_ord      bigint;
  v_id       uuid;
  v_creato   timestamptz;
  v_tenuti   uuid[] := '{}';
  v_requisiti uuid[];
  v_righe    jsonb := '[]'::jsonb;
  v_versione integer;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato not in ('bozza', 'pubblicata') then
    raise exception 'La call non è modificabile in questo stato'
      using detail = 'stato_call_non_valido';
  end if;
  if jsonb_typeof(p_posizioni) is distinct from 'array' or jsonb_array_length(p_posizioni) > 10
  then
    raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
  end if;

  v_requisiti := array(select id from public.partner_call_requisiti where call_id = p_call);

  for v_item, v_ord in
    select e, o from jsonb_array_elements(p_posizioni) with ordinality as t(e, o)
  loop
    if jsonb_typeof(v_item) <> 'object' then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
    end if;
    if exists (select 1 from jsonb_object_keys(v_item) k where not (k = any (v_chiavi))) then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
    end if;
    v_id := null;
    v_creato := now();
    if jsonb_typeof(v_item -> 'id') is distinct from 'null' and v_item ? 'id' then
      begin
        v_id := (v_item ->> 'id')::uuid;
      exception when data_exception then
        raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
      end;
      select created_at into v_creato from public.partner_call_posizioni
      where id = v_id and call_id = p_call;
      if not found or v_id = any (v_tenuti) then
        raise exception 'Posizione non trovata in questa call'
          using detail = 'posizioni_non_valide';
      end if;
      v_tenuti := v_tenuti || v_id;
    end if;
    v_righe := v_righe || jsonb_build_array(
      (v_item - 'id')
      || jsonb_build_object('id', coalesce(v_id, gen_random_uuid()), 'ordine', v_ord - 1,
                            'created_at', v_creato));
  end loop;

  v_prima := public.fn_partner_call_contenuto(p_call);

  begin
    if exists (
      select 1 from jsonb_populate_recordset(null::public.partner_call_posizioni, v_righe) r
      where not (coalesce(r.requisiti_ids, '{}') <@ v_requisiti)
    ) then
      raise exception 'Requisito non trovato in questa call'
        using detail = 'posizioni_non_valide';
    end if;

    delete from public.partner_call_posizioni where call_id = p_call;
    insert into public.partner_call_posizioni
      (id, call_id, titolo, ruolo, tipi_soggetto, competenze, ateco_divisioni, regioni,
       territorio_modalita, paesi, dimensioni, quota_ipotizzata_pct, numero, requisiti_ids,
       note, ordine, created_at)
    select r.id, p_call, r.titolo, coalesce(r.ruolo, 'partner'),
           coalesce(r.tipi_soggetto, '{}'), coalesce(r.competenze, '{}'),
           coalesce(r.ateco_divisioni, '{}'), coalesce(r.regioni, '{}'),
           coalesce(r.territorio_modalita, 'qualsiasi'), coalesce(r.paesi, '{}'),
           coalesce(r.dimensioni, '{}'), r.quota_ipotizzata_pct, coalesce(r.numero, 1),
           coalesce(r.requisiti_ids, '{}'), r.note, r.ordine, r.created_at
    from jsonb_populate_recordset(null::public.partner_call_posizioni, v_righe) r;
  exception
    when check_violation or not_null_violation or unique_violation or data_exception then
      raise exception 'Posizioni non valide' using detail = 'posizioni_non_valide';
  end;

  if v_call.stato = 'pubblicata' then
    if not exists (select 1 from public.partner_call_posizioni where call_id = p_call) then
      raise exception 'Una call pubblicata deve avere almeno una posizione'
        using detail = 'call_incompleta';
    end if;
    if public.fn_partner_call_contenuto(p_call) is distinct from v_prima then
      v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);
      insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
      values (p_attore, 'partenariato.call_modificata', p_owner, p_owner,
              jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                                 'versione', v_versione, 'campi', jsonb_build_array('posizioni')));
    end if;
  end if;

  return coalesce((
    select jsonb_agg(to_jsonb(p) order by p.ordine)
    from public.partner_call_posizioni p where p.call_id = p_call), '[]'::jsonb);
end;
$$;

comment on function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb) is
  'Replace-all delle posizioni (bozza o pubblicata; lock owner → azienda viva → call): id passati conservati, requisiti_ids solo della stessa call; in pubblicata ≥ 1 posizione e versione + 1 se il contenuto cambia. WP7 la ridefinisce con la stessa firma. Ritorna le posizioni. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | posizioni_non_valide | call_incompleta.';

-- ----------------------------------------------------------------------------
-- 13) fn_partner_call_pubblica — pubblicazione di una bozza. Nell'ordine:
--      - lock owner → azienda viva → call; stato bozza (stato_call_non_valido);
--      - identità dal registro (identita_non_verificata) e, per una call
--        NOMINATIVA, il titolare legale rappresentante verificato
--        (rappresentante_non_verificato);
--      - stato live del bando aperto | in apertura prossimamente
--        (bando_non_disponibile);
--      - scadenza (p_scadenza_call, altrimenti quella salvata) tra oggi
--        Europe/Rome e la scadenza del bando (scadenza_call_non_valida);
--      - titolo, descrizione, regole confermate, ≥ 1 posizione e ≥ 1
--        requisito cercato (call_incompleta); regole finanziarie dei
--        requisiti ancora identiche allo snapshot (requisiti_non_validi);
--      - limiti del piano sul pool dell'owner: 0 → piano_non_include_call,
--        call attive ≥ limite → limite_call_raggiunto (NULL = illimitato);
--      - stato pubblicata, snapshot del bando, versione 1 e audit
--        partenariato.call_pubblicata. Ritorna la riga.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_pubblica(
  p_owner                uuid,
  p_company              uuid,
  p_attore               uuid,
  p_call                 uuid,
  p_bando_stato          text,
  p_bando_scadenza       date,
  p_scadenza_call        date,
  p_richiedi_non_sandbox boolean
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi     constant date := (now() at time zone 'Europe/Rome')::date;
  v_call     public.partner_calls%rowtype;
  v_scadenza date;
  v_limite   integer;
  v_versione integer;
begin
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato <> 'bozza' then
    raise exception 'Si può pubblicare solo una bozza' using detail = 'stato_call_non_valido';
  end if;

  if not public.fn_partenariato_identita_ok(p_company, p_richiedi_non_sandbox) then
    raise exception 'Identità dell''azienda non verificata sul Registro Imprese'
      using detail = 'identita_non_verificata';
  end if;
  if not v_call.anonima
     and not public.fn_partenariato_rappresentante_ok(p_owner, p_company) then
    raise exception 'Il titolare non risulta legale rappresentante verificato'
      using detail = 'rappresentante_non_verificato';
  end if;

  if coalesce(lower(btrim(p_bando_stato)), '') not in ('aperto', 'in apertura prossimamente')
  then
    raise exception 'Il bando non è aperto' using detail = 'bando_non_disponibile';
  end if;

  v_scadenza := coalesce(p_scadenza_call, v_call.scadenza_call);
  if v_scadenza is null or v_scadenza < v_oggi
     or (p_bando_scadenza is not null and v_scadenza > p_bando_scadenza) then
    raise exception 'La scadenza della call deve essere tra oggi e la scadenza del bando'
      using detail = 'scadenza_call_non_valida';
  end if;

  if nullif(btrim(v_call.titolo), '') is null
     or nullif(btrim(v_call.descrizione_pubblica), '') is null
     or v_call.regole_confermate_at is null
     or not exists (select 1 from public.partner_call_posizioni where call_id = p_call)
     or not exists (select 1 from public.partner_call_requisiti
                    where call_id = p_call and cercato) then
    raise exception 'La call non è completa' using detail = 'call_incompleta';
  end if;
  if exists (
    select 1 from public.partner_call_requisiti
    where call_id = p_call and criterio ->> 'tipo' = 'regola_finanziaria'
      and not public.fn_partner_call_regola_ok(criterio -> 'regola',
                                               v_call.regole_partenariato)
  ) then
    raise exception 'Le regole finanziarie dei requisiti non corrispondono più alle regole confermate'
      using detail = 'requisiti_non_validi';
  end if;

  v_limite := public.fn_partner_call_limite_attive(p_owner);
  if v_limite is not null and v_limite <= 0 then
    raise exception 'Il tuo piano non include la creazione di call di partenariato'
      using detail = 'piano_non_include_call';
  end if;
  if v_limite is not null and public.fn_partner_calls_attive_usate(p_owner) >= v_limite then
    raise exception 'Hai raggiunto il numero massimo di call attive del tuo piano'
      using detail = 'limite_call_raggiunto';
  end if;

  update public.partner_calls
  set stato                 = 'pubblicata',
      pubblicata_at         = now(),
      scadenza_call         = v_scadenza,
      bando_stato_effettivo = p_bando_stato,
      bando_scadenza        = p_bando_scadenza,
      bando_verificato_at   = now(),
      bando_mancante_dal    = null
  where id = p_call;

  v_versione := public.fn_partner_call_nuova_versione(p_call, p_attore);

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.call_pubblicata', p_owner, p_owner,
          jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                             'bando_id', v_call.bando_id, 'versione', v_versione));

  select * into v_call from public.partner_calls where id = p_call;
  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean) is
  'Pubblica una bozza (lock owner → azienda viva → call): identità dal registro (T5; p_richiedi_non_sandbox NULL = true) e, per una call nominativa, legale rappresentante verificato (Q9); bando aperto | in apertura prossimamente; scadenza tra oggi (Europe/Rome) e la scadenza del bando; completa (titolo, descrizione, regole confermate, ≥ 1 posizione, ≥ 1 requisito cercato); limiti del piano sul pool dell''owner (NULL = illimitato). Versione 1 + audit partenariato.call_pubblicata. Ritorna la riga. Detail: attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | identita_non_verificata | rappresentante_non_verificato | bando_non_disponibile | scadenza_call_non_valida | call_incompleta | requisiti_non_validi | piano_non_include_call | limite_call_raggiunto.';

-- ----------------------------------------------------------------------------
-- 14) fn_partner_call_chiudi — chiusura dal creatore: completata (solo da
--    pubblicata) o annullata (da bozza, anche vuota, pubblicata o sospesa).
--    L'azienda può anche non essere più viva. Audit partenariato.call_chiusa.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_chiudi(
  p_owner   uuid,
  p_company uuid,
  p_attore  uuid,
  p_call    uuid,
  p_esito   text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_call   public.partner_calls%rowtype;
  v_stato  text;
  v_motivo text;
begin
  if p_esito is null or p_esito not in ('completata', 'annullata') then
    raise exception 'Esito di chiusura non valido' using detail = 'parametri_non_validi';
  end if;
  if p_attore is null or p_attore is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, false);

  if (p_esito = 'completata' and v_call.stato <> 'pubblicata')
     or (p_esito = 'annullata'
         and v_call.stato not in ('bozza', 'pubblicata', 'sospesa_moderazione')) then
    raise exception 'La call non si può chiudere in questo stato'
      using detail = 'stato_call_non_valido';
  end if;

  v_stato := case p_esito when 'completata' then 'chiusa_completata' else 'chiusa_annullata' end;
  v_motivo := case p_esito when 'completata' then 'creatore_completata'
                           else 'creatore_annullata' end;

  update public.partner_calls
  set stato           = v_stato,
      chiusa_at       = now(),
      motivo_chiusura = v_motivo
  where id = p_call
  returning * into v_call;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (p_attore, 'partenariato.call_chiusa', p_owner, p_owner,
          jsonb_build_object('call_id', p_call, 'company_profile_id', p_company,
                             'stato', v_stato, 'motivo', v_motivo, 'origine', 'creatore'));

  return to_jsonb(v_call);
end;
$$;

comment on function public.fn_partner_call_chiudi(uuid, uuid, uuid, uuid, text) is
  'Chiusura dal creatore (lock owner → azienda, anche non più viva → call): completata solo da pubblicata; annullata da bozza, pubblicata o sospesa_moderazione. Audit partenariato.call_chiusa. Ritorna la riga. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido.';

-- ----------------------------------------------------------------------------
-- 15) fn_partner_call_chiudi_auto — chiusura automatica (scheduler e
--    controllo in lettura), CONDIZIONATA: solo da bozza o pubblicata,
--    altrimenti false senza effetti. Una bozza non «scade» (§7): da bozza lo
--    stato finale è sempre chiusa_annullata, con il motivo indicato. Stesso
--    ordine dei lock (owner → azienda → call). Audit con actor NULL.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_chiudi_auto(
  p_call        uuid,
  p_nuovo_stato text,
  p_motivo      text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner   uuid;
  v_company uuid;
  v_call    public.partner_calls%rowtype;
  v_stato   text;
begin
  if p_nuovo_stato is null or p_nuovo_stato not in ('scaduta', 'chiusa_annullata')
     or p_motivo is null
     or p_motivo not in ('scadenza_call', 'bando_chiuso', 'bando_sospeso', 'bando_revocato',
                         'bando_non_disponibile', 'azienda_non_disponibile') then
    raise exception 'Parametri di chiusura automatica non validi'
      using detail = 'parametri_non_validi';
  end if;

  select family_parent_id, company_profile_id into v_owner, v_company
  from public.partner_calls where id = p_call;
  if not found then
    return false;
  end if;

  perform 1 from public.profiles where id = v_owner for update;
  perform 1 from public.company_profiles where id = v_company for no key update;

  select * into v_call from public.partner_calls
  where id = p_call and stato in ('bozza', 'pubblicata')
  for update;
  if not found then
    return false;
  end if;

  v_stato := case when v_call.stato = 'bozza' then 'chiusa_annullata' else p_nuovo_stato end;

  update public.partner_calls
  set stato           = v_stato,
      chiusa_at       = now(),
      motivo_chiusura = p_motivo
  where id = p_call;

  insert into public.audit_log (actor_id, action, target_user_id, family_parent_id, payload)
  values (null, 'partenariato.call_chiusa', v_owner, v_owner,
          jsonb_build_object('call_id', p_call, 'company_profile_id', v_company,
                             'stato', v_stato, 'motivo', p_motivo, 'origine', 'sistema'));
  return true;
end;
$$;

comment on function public.fn_partner_call_chiudi_auto(uuid, text, text) is
  'Chiusura automatica condizionata (scheduler, controllo in lettura): solo da bozza o pubblicata (altrimenti false); p_nuovo_stato scaduta | chiusa_annullata (da bozza sempre chiusa_annullata), p_motivo scadenza_call | bando_chiuso | bando_sospeso | bando_revocato | bando_non_disponibile | azienda_non_disponibile. Lock owner → azienda → call. Audit partenariato.call_chiusa con actor NULL. Detail: parametri_non_validi.';

-- ----------------------------------------------------------------------------
-- 16) Job AI della call (posizioni e testi) sul budget fail-closed del modulo
--    (0034, gruppo altri, origine call), come la bozza AI del profilo (0035).
-- ----------------------------------------------------------------------------

-- Chiude un'esecuzione rimasta in_corso senza più un job che la chiuda: costo
-- ignoto (NULL: la riserva resta nel budget) e una riga timeout_unknown in
-- api_usage_events con la riserva. Chi chiude l'esecuzione ne registra il
-- consumo: una sola riga per esecuzione.
create or replace function public.fn_partner_call_ai_esecuzione_interrotta(
  p_esecuzione_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_exec public.partenariati_ai_esecuzioni%rowtype;
begin
  select * into v_exec from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id
    and servizio in ('partner_call_posizioni', 'partner_call_testi')
    and stato = 'in_corso'
  for update;
  if not found then
    return false;
  end if;

  insert into public.api_usage_events
    (user_id, family_parent_id, provider, service, outcome, cost_cents, request_meta)
  values
    (v_exec.richiedente_user_id, v_exec.owner_id, 'anthropic', v_exec.servizio,
     'timeout_unknown', v_exec.costo_riservato_cents,
     jsonb_build_object('company_profile_id', v_exec.company_profile_id,
                        'bando_id', v_exec.bando_id, 'esecuzione_id', v_exec.id,
                        'esito', 'interrotta', 'failsafe', true));
  perform public.fn_partenariati_ai_concludi(
    p_esecuzione_id, 'interrotta', null, 0, 0, null, 'interrotta');
  return true;
end;
$$;

comment on function public.fn_partner_call_ai_esecuzione_interrotta(uuid) is
  'Chiude un''esecuzione partner_call_posizioni | partner_call_testi ancora in_corso come interrotta a costo ignoto (la riserva resta nel budget) e scrive la sua riga timeout_unknown in api_usage_events con la riserva. Già chiusa, inesistente o di un altro servizio → nessun effetto (false).';

-- ----------------------------------------------------------------------------
-- 17) fn_partner_call_ai_prenota — prenotazione ATOMICA di un job AI della
--    call. Lock owner → azienda viva → call, poi il lock globale del budget
--    (ultimo, come la 0035):
--      - servizio partner_call_posizioni | partner_call_testi
--        (parametri_non_validi); richiedente = titolare (attore_non_titolare);
--        solo bozza (stato_call_non_valido);
--      - job dello stesso servizio in_corso da meno di 10 minuti → ai_in_corso;
--        se più vecchio è orfano: la sua esecuzione si chiude con
--        fn_partner_call_ai_esecuzione_interrotta e si prosegue;
--      - esecuzioni di oggi (Europe/Rome) dello stesso servizio per la call —
--        cioè per azienda × bando: annullare e ricreare la bozza non azzera il
--        conteggio — con la stessa esclusione di fn_partenariati_ai_prenota ≥
--        p_limite_call (NULL = nessun limite) → ai_limite_call;
--      - esecuzioni di oggi dell'owner su TUTTI i servizi delle call
--        (partner_call_posizioni + partner_call_testi), stessa esclusione,
--        ≥ p_limite_owner (NULL = nessun limite) → ai_limite_owner: il tetto
--        per titolare è unico, non uno per servizio;
--      - fn_partenariati_ai_prenota(servizio, 'call', 'altri', …, senza
--        limite per owner: l'ha già applicato questa funzione) →
--        ai_budget_esaurito | parametri_non_validi;
--      - job in_corso con la nuova esecuzione.
--    Ritorna l'id dell'esecuzione.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_ai_prenota(
  p_owner                 uuid,
  p_company               uuid,
  p_call                  uuid,
  p_richiedente           uuid,
  p_servizio              text,
  p_budget_cents          integer,
  p_costo_riservato_cents integer,
  p_limite_call           integer,
  p_limite_owner          integer
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_oggi    constant date := (now() at time zone 'Europe/Rome')::date;
  v_call    public.partner_calls%rowtype;
  v_stato   text;
  v_avviata timestamptz;
  v_esec    uuid;
  v_n       bigint;
  v_id      uuid;
begin
  if p_servizio is null or p_servizio not in ('partner_call_posizioni', 'partner_call_testi')
  then
    raise exception 'Servizio AI della call non valido' using detail = 'parametri_non_validi';
  end if;
  if p_richiedente is null or p_richiedente is distinct from p_owner then
    raise exception 'Le call di partenariato le gestisce il titolare dell''azienda'
      using detail = 'attore_non_titolare';
  end if;

  select * into v_call from public.fn_partner_call_blocca(p_owner, p_company, p_call, true);

  if v_call.stato <> 'bozza' then
    raise exception 'Le proposte AI servono solo prima della pubblicazione'
      using detail = 'stato_call_non_valido';
  end if;

  -- Dopo i lock di riga (ordine globale).
  perform pg_advisory_xact_lock(hashtext('partenariati_ai_budget'));

  if p_servizio = 'partner_call_posizioni' then
    v_stato := v_call.ai_posizioni_stato;
    v_avviata := v_call.ai_posizioni_avviata_at;
    v_esec := v_call.ai_posizioni_esecuzione_id;
  else
    v_stato := v_call.ai_testi_stato;
    v_avviata := v_call.ai_testi_avviata_at;
    v_esec := v_call.ai_testi_esecuzione_id;
  end if;

  if v_stato = 'in_corso' then
    if v_avviata > now() - interval '10 minutes' then
      raise exception 'La proposta è già in preparazione' using detail = 'ai_in_corso';
    end if;
    perform public.fn_partner_call_ai_esecuzione_interrotta(v_esec);
  end if;

  if p_limite_call is not null then
    select count(*) into v_n
    from public.partenariati_ai_esecuzioni
    where company_profile_id = p_company
      and bando_id = v_call.bando_id
      and giorno = v_oggi
      and servizio = p_servizio
      and not (not llm_eseguito
               and (stato in ('riusata', 'nessun_segnale')
                    or (stato in ('errore', 'interrotta')
                        and cost_cents is not distinct from 0)));
    if v_n >= greatest(p_limite_call, 0) then
      raise exception 'Hai raggiunto le proposte di oggi per questa call: riprova domani'
        using detail = 'ai_limite_call';
    end if;
  end if;

  -- Tetto per titolare su tutti i servizi delle call (fn_partenariati_ai_prenota
  -- conterebbe un servizio alla volta).
  if p_limite_owner is not null then
    select count(*) into v_n
    from public.partenariati_ai_esecuzioni
    where owner_id = p_owner
      and giorno = v_oggi
      and servizio in ('partner_call_posizioni', 'partner_call_testi')
      and not (not llm_eseguito
               and (stato in ('riusata', 'nessun_segnale')
                    or (stato in ('errore', 'interrotta')
                        and cost_cents is not distinct from 0)));
    if v_n >= greatest(p_limite_owner, 0) then
      raise exception 'Hai raggiunto le proposte automatiche di oggi: riprova domani'
        using detail = 'ai_limite_owner';
    end if;
  end if;

  v_id := public.fn_partenariati_ai_prenota(
    p_servizio              => p_servizio,
    p_origine               => 'call',
    p_gruppo                => 'altri',
    p_budget_cents          => p_budget_cents,
    p_costo_riservato_cents => p_costo_riservato_cents,
    p_richiedente           => p_richiedente,
    p_limite_richiedente    => null,
    p_owner                 => p_owner,
    p_limite_owner          => null,
    p_company               => p_company,
    p_bando_id              => v_call.bando_id);

  if p_servizio = 'partner_call_posizioni' then
    update public.partner_calls
    set ai_posizioni_stato         = 'in_corso',
        ai_posizioni_avviata_at    = now(),
        ai_posizioni_esecuzione_id = v_id,
        ai_posizioni_errore        = null
    where id = p_call;
  else
    update public.partner_calls
    set ai_testi_stato         = 'in_corso',
        ai_testi_avviata_at    = now(),
        ai_testi_esecuzione_id = v_id,
        ai_testi_errore        = null
    where id = p_call;
  end if;

  return v_id;
end;
$$;

comment on function public.fn_partner_call_ai_prenota(uuid, uuid, uuid, uuid, text, integer, integer, integer, integer) is
  'Prenota un job AI della call (solo bozza; lock owner → azienda viva → call → budget): job dello stesso servizio in_corso < 10 minuti → ai_in_corso (se più vecchio la sua esecuzione si chiude come interrotta); limite giornaliero per call = azienda × bando (Europe/Rome, stessa esclusione delle esecuzioni senza LLM; NULL = nessun limite) → ai_limite_call; limite giornaliero per owner su TUTTI i servizi delle call insieme (stessa esclusione; NULL = nessun limite) → ai_limite_owner; poi fn_partenariati_ai_prenota(servizio, call, altri) per il budget. Imposta il job in_corso e ritorna l''id dell''esecuzione. Detail: parametri_non_validi | attore_non_titolare | owner_not_found | company_not_found | call_not_found | stato_call_non_valido | ai_in_corso | ai_limite_call | ai_limite_owner | ai_budget_esaurito.';

-- ----------------------------------------------------------------------------
-- 18) fn_partner_call_ai_concludi — chiusura ATOMICA di un job AI della call
--    (modello fn_partner_bozza_ai_concludi, 0035). Nella stessa transazione:
--      - il job diventa pronta (con la proposta, un oggetto) o errore (con il
--        codice; la proposta si svuota) SOLO se è ancora quello di questa
--        esecuzione e in_corso (failsafe o un nuovo job vincono);
--      - l'esecuzione si chiude con costo e token (fn_partenariati_ai_concludi)
--        SOLO se è ancora in_corso (anche se la call non esiste più).
--    Ritorna {job_scritto, esecuzione_chiusa}: il job registra il consumo solo
--    se ha chiuso lui l'esecuzione (chi chiude registra, una volta).
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_ai_concludi(
  p_call          uuid,
  p_esecuzione_id uuid,
  p_servizio      text,
  p_job_stato     text,
  p_proposta      jsonb,
  p_job_errore    text,
  p_stato         text,
  p_cost_cents    integer,
  p_input_tokens  integer,
  p_output_tokens integer,
  p_model         text,
  p_errore        text
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_scritto boolean;
  v_chiusa  boolean;
begin
  if p_call is null or p_esecuzione_id is null
     or p_servizio is null or p_servizio not in ('partner_call_posizioni', 'partner_call_testi')
     or p_job_stato is null or p_job_stato not in ('pronta', 'errore')
     or (p_job_stato = 'pronta' and jsonb_typeof(p_proposta) is distinct from 'object') then
    raise exception 'Parametri di chiusura del job non validi'
      using detail = 'parametri_non_validi';
  end if;

  if p_servizio = 'partner_call_posizioni' then
    update public.partner_calls
    set ai_posizioni_stato    = p_job_stato,
        ai_posizioni_proposta = case when p_job_stato = 'pronta' then p_proposta end,
        ai_posizioni_errore   = case when p_job_stato = 'errore' then p_job_errore end
    where id = p_call
      and ai_posizioni_esecuzione_id = p_esecuzione_id
      and ai_posizioni_stato = 'in_corso';
  else
    update public.partner_calls
    set ai_testi_stato    = p_job_stato,
        ai_testi_proposta = case when p_job_stato = 'pronta' then p_proposta end,
        ai_testi_errore   = case when p_job_stato = 'errore' then p_job_errore end
    where id = p_call
      and ai_testi_esecuzione_id = p_esecuzione_id
      and ai_testi_stato = 'in_corso';
  end if;
  v_scritto := found;

  perform 1 from public.partenariati_ai_esecuzioni
  where id = p_esecuzione_id and servizio = p_servizio and stato = 'in_corso'
  for update;
  v_chiusa := found;
  if v_chiusa then
    perform public.fn_partenariati_ai_concludi(
      p_esecuzione_id, p_stato, p_cost_cents, p_input_tokens, p_output_tokens, p_model,
      p_errore);
  end if;

  return jsonb_build_object('job_scritto', v_scritto, 'esecuzione_chiusa', v_chiusa);
end;
$$;

comment on function public.fn_partner_call_ai_concludi(uuid, uuid, text, text, jsonb, text, text, integer, integer, integer, text, text) is
  'Chiusura atomica di un job AI della call: job pronta (proposta oggetto) | errore solo se è ancora in_corso con questa esecuzione, e nella stessa transazione esecuzione chiusa (fn_partenariati_ai_concludi) solo se ancora in_corso. Ritorna {job_scritto, esecuzione_chiusa}. Detail: parametri_non_validi | stato_non_valido.';

-- ----------------------------------------------------------------------------
-- 19) fn_partner_call_ai_chiudi_stale — failsafe (in lettura e nello
--    scheduler), soglia p_minuti (NULL = 10, minimo 1):
--      - i job in_corso avviati da almeno la soglia diventano errore
--        (errore interrotta, proposta svuotata) e la loro esecuzione si chiude
--        con fn_partner_call_ai_esecuzione_interrotta;
--      - le esecuzioni dei due servizi ancora in_corso, avviate da almeno la
--        soglia e che nessun job in_corso referenzia più (call cancellata
--        durante il job, chiusura rimasta a metà) si chiudono allo stesso modo.
--    Salta le righe bloccate. Ritorna quanti job ed esecuzioni ha chiuso.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partner_call_ai_chiudi_stale(p_minuti integer)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_soglia constant timestamptz :=
    now() - make_interval(mins => greatest(coalesce(p_minuti, 10), 1));
  v_row record;
  v_id  uuid;
  v_n   integer := 0;
begin
  for v_row in
    select id, ai_posizioni_esecuzione_id as esecuzione_id from public.partner_calls
    where ai_posizioni_stato = 'in_corso' and ai_posizioni_avviata_at <= v_soglia
    order by ai_posizioni_avviata_at
    for update skip locked
  loop
    update public.partner_calls
    set ai_posizioni_stato    = 'errore',
        ai_posizioni_errore   = 'interrotta',
        ai_posizioni_proposta = null
    where id = v_row.id;
    perform public.fn_partner_call_ai_esecuzione_interrotta(v_row.esecuzione_id);
    v_n := v_n + 1;
  end loop;

  for v_row in
    select id, ai_testi_esecuzione_id as esecuzione_id from public.partner_calls
    where ai_testi_stato = 'in_corso' and ai_testi_avviata_at <= v_soglia
    order by ai_testi_avviata_at
    for update skip locked
  loop
    update public.partner_calls
    set ai_testi_stato    = 'errore',
        ai_testi_errore   = 'interrotta',
        ai_testi_proposta = null
    where id = v_row.id;
    perform public.fn_partner_call_ai_esecuzione_interrotta(v_row.esecuzione_id);
    v_n := v_n + 1;
  end loop;

  for v_id in
    select e.id from public.partenariati_ai_esecuzioni e
    where e.servizio in ('partner_call_posizioni', 'partner_call_testi')
      and e.stato = 'in_corso'
      and e.avviata_at <= v_soglia
      and not exists (
        select 1 from public.partner_calls c
        where (c.ai_posizioni_esecuzione_id = e.id and c.ai_posizioni_stato = 'in_corso')
           or (c.ai_testi_esecuzione_id = e.id and c.ai_testi_stato = 'in_corso'))
    order by e.avviata_at
    for update of e skip locked
  loop
    if public.fn_partner_call_ai_esecuzione_interrotta(v_id) then
      v_n := v_n + 1;
    end if;
  end loop;

  return v_n;
end;
$$;

comment on function public.fn_partner_call_ai_chiudi_stale(integer) is
  'Failsafe dei job AI della call (soglia p_minuti, NULL = 10, minimo 1): job in_corso oltre la soglia → errore interrotta (proposta svuotata) e loro esecuzione chiusa da fn_partner_call_ai_esecuzione_interrotta; esecuzioni partner_call_* in_corso oltre la soglia che nessun job in_corso referenzia → stessa chiusura. Salta le righe bloccate. Ritorna quanti job ed esecuzioni ha chiuso.';

-- ----------------------------------------------------------------------------
-- 20) fn_partenariati_snapshot — limiti e consumi per /me/entitlements (C5).
--    {call_attive: {limite, usate, residuo}, candidature_mese: {limite,
--    usate: 0, residuo, periodo_inizio, periodo_fine}}: limite NULL =
--    illimitato (residuo NULL); usate = call pubblicate o sospese delle
--    aziende vive dell'owner; mese solare Europe/Rome. WP7 la ridefinisce con
--    la STESSA firma per contare le candidature.
-- ----------------------------------------------------------------------------
create or replace function public.fn_partenariati_snapshot(p_owner uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_lim    jsonb;
  v_calls  integer;
  v_cand   integer;
  v_usate  integer;
  v_inizio date := date_trunc('month', now() at time zone 'Europe/Rome')::date;
begin
  v_lim := public.fn_partenariati_limiti(p_owner);
  v_calls := public.fn_partner_call_limite_attive(p_owner);
  v_cand := case when jsonb_typeof(v_lim) = 'object' and v_lim ? 'candidature_mese'
                 then (v_lim ->> 'candidature_mese')::integer else 0 end;
  v_usate := public.fn_partner_calls_attive_usate(p_owner);

  return jsonb_build_object(
    'call_attive', jsonb_build_object(
      'limite', v_calls,
      'usate', v_usate,
      'residuo', case when v_calls is null then null else greatest(v_calls - v_usate, 0) end),
    'candidature_mese', jsonb_build_object(
      'limite', v_cand,
      'usate', 0,
      'residuo', case when v_cand is null then null else greatest(v_cand, 0) end,
      'periodo_inizio', v_inizio,
      'periodo_fine', (v_inizio + interval '1 month' - interval '1 day')::date));
end;
$$;

comment on function public.fn_partenariati_snapshot(uuid) is
  'Limiti e consumi di partenariato del titolare per /me/entitlements: {call_attive: {limite, usate, residuo}, candidature_mese: {limite, usate, residuo, periodo_inizio, periodo_fine}} (NULL = illimitato, mese solare Europe/Rome). In WP5 le candidature usate valgono 0: WP7 la ridefinisce con la stessa firma.';

-- ----------------------------------------------------------------------------
-- 21) Sicurezza: pattern del repo — RLS deny-all (nessuna policy) + revoche
--    esplicite su ogni tabella nuova; nessuna funzione nuova (trigger e
--    interne comprese) eseguibile dai ruoli esposti (Supabase concede EXECUTE
--    di default a PUBLIC). I trigger scattano comunque.
-- ----------------------------------------------------------------------------
alter table public.partner_calls enable row level security;
alter table public.partner_call_requisiti enable row level security;
alter table public.partner_call_posizioni enable row level security;
alter table public.partner_call_versioni enable row level security;
alter table public.partner_segnalazioni enable row level security;

revoke all on public.partner_calls from anon, authenticated;
revoke all on public.partner_call_requisiti from anon, authenticated;
revoke all on public.partner_call_posizioni from anon, authenticated;
revoke all on public.partner_call_versioni from anon, authenticated;
revoke all on public.partner_segnalazioni from anon, authenticated;

revoke execute on function public.fn_partner_call_versioni_immutabile()
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_identita_ok(uuid, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariato_rappresentante_ok(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_blocca_azienda(uuid, uuid, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_blocca(uuid, uuid, uuid, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_campi_editabili(boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_contenuto(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_nuova_versione(uuid, uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_calls_attive_usate(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_limite_attive(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_regole_valide(jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_regola_ok(jsonb, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_crea_bozza(uuid, uuid, uuid, jsonb, jsonb, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_aggiorna(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_conferma_regole(uuid, uuid, uuid, uuid, jsonb, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_sostituisci_requisiti(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date, boolean)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_chiudi(uuid, uuid, uuid, uuid, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_chiudi_auto(uuid, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_ai_esecuzione_interrotta(uuid)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_ai_prenota(uuid, uuid, uuid, uuid, text, integer, integer, integer, integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_ai_concludi(uuid, uuid, text, text, jsonb, text, text, integer, integer, integer, text, text)
  from public, anon, authenticated;
revoke execute on function public.fn_partner_call_ai_chiudi_stale(integer)
  from public, anon, authenticated;
revoke execute on function public.fn_partenariati_snapshot(uuid)
  from public, anon, authenticated;

-- ============================================================================
-- ROLLBACK 0037 (eseguire in transazione, DOPO aver riportato il backend alla
-- versione precedente o spento PARTENARIATI_ATTIVO: quella nuova chiama le RPC
-- e legge le tabelle; /me/entitlements chiama fn_partenariati_snapshot solo
-- con il flag acceso).
-- 1) drop function public.fn_partenariati_snapshot(uuid);
--    drop function public.fn_partner_call_ai_chiudi_stale(integer);
--    drop function public.fn_partner_call_ai_concludi(uuid, uuid, text, text, jsonb, text,
--      text, integer, integer, integer, text, text);
--    drop function public.fn_partner_call_ai_prenota(uuid, uuid, uuid, uuid, text, integer,
--      integer, integer, integer);
--    drop function public.fn_partner_call_ai_esecuzione_interrotta(uuid);
--    drop function public.fn_partner_call_chiudi_auto(uuid, text, text);
--    drop function public.fn_partner_call_chiudi(uuid, uuid, uuid, uuid, text);
--    drop function public.fn_partner_call_pubblica(uuid, uuid, uuid, uuid, text, date, date,
--      boolean);
--    drop function public.fn_partner_call_sostituisci_posizioni(uuid, uuid, uuid, uuid, jsonb);
--    drop function public.fn_partner_call_sostituisci_requisiti(uuid, uuid, uuid, uuid, jsonb);
--    drop function public.fn_partner_call_conferma_regole(uuid, uuid, uuid, uuid, jsonb,
--      boolean);
--    drop function public.fn_partner_call_aggiorna(uuid, uuid, uuid, uuid, jsonb);
--    drop function public.fn_partner_call_crea_bozza(uuid, uuid, uuid, jsonb, jsonb, integer);
--    drop function public.fn_partner_call_regola_ok(jsonb, jsonb);
--    drop function public.fn_partner_call_regole_valide(jsonb);
--    drop function public.fn_partner_call_limite_attive(uuid);
--    drop function public.fn_partner_calls_attive_usate(uuid);
--    drop function public.fn_partner_call_nuova_versione(uuid, uuid);
--    drop function public.fn_partner_call_contenuto(uuid);
--    drop function public.fn_partner_call_campi_editabili(boolean);
--    drop function public.fn_partner_call_blocca(uuid, uuid, uuid, boolean);
--    drop function public.fn_partner_call_blocca_azienda(uuid, uuid, boolean);
--    drop function public.fn_partenariato_rappresentante_ok(uuid, uuid);
--    drop function public.fn_partenariato_identita_ok(uuid, boolean);
-- 2) drop table public.partner_call_versioni;    -- si perde lo storico delle call
--    drop table public.partner_call_posizioni;
--    drop table public.partner_call_requisiti;
--    drop table public.partner_calls;            -- si perdono tutte le call
--    drop table public.partner_segnalazioni;     -- si perde il REGISTRO DSA:
--      esportarlo prima (conservazione dichiarata 24 mesi, Q22);
-- 3) drop function public.fn_partner_call_versioni_immutabile();
-- Le esecuzioni partner_call_* in partenariati_ai_esecuzioni (0034) e le
-- righe di api_usage_events restano: sono il registro della spesa.
-- ============================================================================
