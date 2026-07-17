-- ============================================================================
-- BandoFit — DB primario, migration 0027: fatturazione elettronica (SDI).
--
-- Ogni purchase 'pagato' genera una fattura elettronica trasmessa a SDI via
-- openapi.it. Qui vivono la tabella delle fatture e la numerazione atomica;
-- l'emissione (XML FatturaPA, invio, esiti) è nel backend (invoice_service).
--
-- Invarianti fiscali (fatte rispettare da vincoli + protocollo del worker):
--   * data_documento = data dell'INCASSO (purchases.paid_at), non dell'invio:
--     per i servizi il momento impositivo è l'incasso e sbagliarla sposta
--     l'esigibilità IVA di liquidazione;
--   * numero progressivo per (anno, serie), assegnato al PRIMO invio e
--     CONGELATO sulla riga: uno scarto SDI si ritrasmette con lo STESSO numero
--     e la STESSA data (una fattura scartata "non è emessa", il numero resta);
--   * importi copiati dal purchase e immutabili (mai ricalcolati dai listini).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) Fatture. Una per purchase pagato (UNIQUE). L'XML è la copia dell'originale
--    fiscale (la conservazione a norma 10 anni è del provider).
-- ----------------------------------------------------------------------------
create table public.invoices (
  id                uuid primary key default gen_random_uuid(),
  purchase_id       uuid not null unique references public.purchases (id),
  tipo_documento    text not null default 'TD01',   -- TD01 fattura; TD04 nota di credito (v2)
  anno              integer not null,
  serie             text not null default '',
  numero            integer,                          -- assegnato all'invio (NULL finché da_emettere)
  data_documento    date not null,                    -- = data incasso (Europe/Rome)
  stato             text not null default 'da_emettere'
                    check (stato in ('da_emettere', 'in_invio', 'inviata', 'consegnata',
                                     'non_consegnata', 'scartata', 'errore')),
  provider_id       text,                             -- uuid openapi per il tracking
  sdi_identificativo text,
  imponibile_cents  integer not null,
  iva_cents         integer not null,
  totale_cents      integer not null,
  cliente_snapshot  jsonb not null,
  xml               text,
  ultimo_esito      jsonb,
  tentativi         integer not null default 0,
  emessa_at         timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  -- il numero è univoco entro anno+serie, ma solo quando assegnato
  constraint invoices_numero_progressivo unique (anno, serie, numero)
);

comment on table public.invoices is
  'Fatture elettroniche SDI (una per purchase pagato). data_documento = data incasso; numero congelato al primo invio; XML = copia (la conservazione a norma è del provider openapi).';

create index invoices_stato_idx on public.invoices (stato)
  where stato in ('da_emettere', 'in_invio', 'inviata', 'errore');

create trigger invoices_updated_at
  before update on public.invoices
  for each row execute function public.set_updated_at();

alter table public.invoices enable row level security;
revoke all on public.invoices from anon, authenticated;

-- ----------------------------------------------------------------------------
-- 2) Contatore progressivo per (anno, serie) + assegnazione atomica.
--    Il numero si assegna al PRIMO invio (worker), così un purchase fallito
--    non lascia buchi nella numerazione.
-- ----------------------------------------------------------------------------
create table public.invoice_counters (
  anno          integer not null,
  serie         text not null default '',
  ultimo_numero integer not null default 0,
  primary key (anno, serie)
);

alter table public.invoice_counters enable row level security;
revoke all on public.invoice_counters from anon, authenticated;

create or replace function public.fn_next_invoice_number(p_anno integer, p_serie text)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_numero integer;
begin
  insert into public.invoice_counters (anno, serie, ultimo_numero)
  values (p_anno, coalesce(p_serie, ''), 1)
  on conflict (anno, serie)
  do update set ultimo_numero = public.invoice_counters.ultimo_numero + 1
  returning ultimo_numero into v_numero;
  return v_numero;
end;
$$;

revoke execute on function public.fn_next_invoice_number(integer, text)
  from public, anon, authenticated;
