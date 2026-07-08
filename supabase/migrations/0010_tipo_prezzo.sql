-- ============================================================================
-- BandoFit — DB primario, migration 0010: modalità di visualizzazione prezzo
-- per piani e add-on.
--
-- Tre modalità (valori di dominio in italiano, come tipo_punteggio/esito):
--   'importo'      → comportamento attuale, prezzo in euro;
--   'gratis'       → la UI mostra «Gratis» al posto di «0 €»; l'item resta
--                    attivabile/acquisibile con lo stesso flusso;
--   'su_richiesta' → la UI mostra etichetta_prezzo al posto del prezzo e la
--                    CTA diventa «Richiedi una consulenza»: l'item NON è
--                    attivabile self-serve (blocco applicato dal backend:
--                    cambio piano e registrazione; l'attivazione resta
--                    possibile solo dall'area admin).
--
-- etichetta_prezzo è letta SOLO con tipo_prezzo = 'su_richiesta'; se NULL o
-- vuota la UI mostra il fallback «Su richiesta» — per questo non c'è alcun
-- check cross-campo. Il valore di prezzo resta comunque salvato (utile se si
-- torna alla modalità 'importo').
-- ============================================================================

alter table public.subscription_plans
  add column tipo_prezzo text not null default 'importo'
    check (tipo_prezzo in ('importo', 'gratis', 'su_richiesta')),
  add column etichetta_prezzo text;

comment on column public.subscription_plans.tipo_prezzo is
  'Come mostrare il prezzo: importo (€/anno), gratis («Gratis», stesso flusso di attivazione), su_richiesta (etichetta al posto del prezzo; il piano non è attivabile self-serve).';
comment on column public.subscription_plans.etichetta_prezzo is
  'Testo mostrato al posto del prezzo SOLO con tipo_prezzo = su_richiesta; se NULL o vuoto la UI mostra «Su richiesta».';

alter table public.addons
  add column tipo_prezzo text not null default 'importo'
    check (tipo_prezzo in ('importo', 'gratis', 'su_richiesta')),
  add column etichetta_prezzo text;

comment on column public.addons.tipo_prezzo is
  'Come mostrare il prezzo (una tantum): importo, gratis, su_richiesta — gemello di subscription_plans.tipo_prezzo.';
comment on column public.addons.etichetta_prezzo is
  'Testo mostrato al posto del prezzo SOLO con tipo_prezzo = su_richiesta; se NULL o vuoto la UI mostra «Su richiesta» — gemello di subscription_plans.etichetta_prezzo.';

-- Backfill una tantum: ciò che oggi è a prezzo zero diventa «Gratis» (nel
-- seed 0002 è il piano Gratuito). I nuovi record creati a 0 € dopo questa
-- migration restano 'importo': decide l'admin dall'interfaccia.
update public.subscription_plans set tipo_prezzo = 'gratis' where prezzo_annuale = 0;
update public.addons set tipo_prezzo = 'gratis' where prezzo = 0;
