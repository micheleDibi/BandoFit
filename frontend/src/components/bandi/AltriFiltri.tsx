import { useEffect, useId, useRef } from "react";
import { useFunzioni } from "../../hooks/useFunzioni";
import { PARTENARIATO_COPY } from "../../lib/copy";
import type { Lookups } from "../../types";
import { Button } from "../ui/Button";
import { Drawer } from "../ui/Drawer";
import { RadioGroup } from "../ui/RadioGroup";
import { Skeleton } from "../ui/states";
import { FacetGroup } from "./FacetGroup";
import {
  ImportoCampi,
  ScadenzaOpzioni,
  StatoOpzioni,
  type BozzaImporto,
  type FiltriControlli,
} from "./FiltriBandi";

export interface AltriFiltriDrawerProps extends FiltriControlli {
  open: boolean;
  onClose: () => void;
  lookups: Lookups | undefined;
  onReset: () => void;
  activeCount: number;
  /** Bandi trovati con i filtri correnti, per il pulsante «Mostra N bandi». */
  totale: number | undefined;
}

/** Il cassetto «Filtri»: su desktop contiene i gruppi che non stanno nella
 *  barra (settori, ATECO, modalità, programmi, partenariato); sotto `lg`, dove
 *  la barra non c'è, contiene tutto (tavola `MobileBandi`). Ogni scelta si
 *  applica subito, tranne l'importo, che ha il suo «Applica»: le cifre scritte
 *  e non applicate si applicano quando il cassetto si chiude (con il pulsante
 *  in fondo, la X, Esc o il velo), invece di perdersi. */
export function AltriFiltriDrawer({
  open,
  onClose,
  lookups,
  filters,
  onToggleFacet,
  onToggleStato,
  onUpdate,
  onReset,
  activeCount,
  totale,
}: AltriFiltriDrawerProps) {
  const { partenariatiAttivo } = useFunzioni();
  const notaId = useId();
  const chiaveImporto = `${filters.importo_min ?? ""}-${filters.importo_max ?? ""}`;

  // Cifre dell'importo scritte e non applicate. Quando l'importo dell'URL cambia
  // (applicato, azzerato) i campi ripartono da lì e la bozza non vale più.
  const bozzaImporto = useRef<BozzaImporto | null>(null);
  useEffect(() => {
    bozzaImporto.current = null;
  }, [chiaveImporto]);

  const chiudi = () => {
    const bozza = bozzaImporto.current;
    bozzaImporto.current = null;
    if (
      bozza &&
      (bozza.importo_min !== filters.importo_min || bozza.importo_max !== filters.importo_max)
    ) {
      onUpdate(bozza);
    }
    onClose();
  };

  return (
    <Drawer open={open} onClose={chiudi} lato="destra" titolo="Filtri">
      {/* Montato solo da aperto: da chiuso i gruppi non stanno nel DOM e non si
          ri-renderizzano a ogni filtro. */}
      {!open ? null : !lookups ? (
        <div className="flex flex-col gap-3 pt-2" aria-hidden>
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-6 pt-2">
          {/* Sotto lg la barra dei filtri non c'è: qui stanno anche i suoi gruppi. */}
          <div className="flex flex-col gap-6 lg:hidden">
            <StatoOpzioni filters={filters} onToggleStato={onToggleStato} />
            <FacetGroup
              title="Regione"
              options={lookups.regioni.map((r) => ({ id: r.id, label: r.nome }))}
              selected={filters.regioni}
              onToggle={(id) => onToggleFacet("regioni", id)}
              searchable
            />
            <FacetGroup
              title="Tipologia"
              options={lookups.tipologie_bando.map((t) => ({ id: t.id, label: t.nome }))}
              selected={filters.tipologie}
              onToggle={(id) => onToggleFacet("tipologie", id)}
            />
            <FacetGroup
              title="Beneficiari"
              options={lookups.beneficiari.map((b) => ({ id: b.id, label: b.nome }))}
              selected={filters.beneficiari}
              onToggle={(id) => onToggleFacet("beneficiari", id)}
              searchable
            />
            <ImportoCampi
              key={chiaveImporto}
              filters={filters}
              onUpdate={onUpdate}
              onBozza={(bozza) => {
                bozzaImporto.current = bozza;
              }}
            />
            <ScadenzaOpzioni filters={filters} onUpdate={onUpdate} nome="scadenza-cassetto" />
          </div>

          <FacetGroup
            title="Settori"
            options={lookups.settori.map((s) => ({ id: s.id, label: s.nome }))}
            selected={filters.settori}
            onToggle={(id) => onToggleFacet("settori", id)}
            searchable
          />
          <FacetGroup
            title="Codici ATECO"
            options={lookups.codici_ateco.map((c) => ({
              id: c.id,
              label: c.codice,
              sublabel: c.descrizione ?? undefined,
            }))}
            selected={filters.ateco}
            onToggle={(id) => onToggleFacet("ateco", id)}
            searchable
          />
          <FacetGroup
            title="Modalità di erogazione"
            options={lookups.modalita_erogazione.map((m) => ({ id: m.id, label: m.nome }))}
            selected={filters.modalita}
            onToggle={(id) => onToggleFacet("modalita", id)}
          />
          <FacetGroup
            title="Programmi"
            options={lookups.programmi.map((p) => ({ id: p.id, label: p.nome }))}
            selected={filters.programmi}
            onToggle={(id) => onToggleFacet("programmi", id)}
            searchable
          />

          {/* Partenariato (modulo partenariati): vale solo sui bandi già
              analizzati, quindi lo si dice sotto le opzioni. */}
          {partenariatiAttivo && (
            <div
              role="group"
              aria-label={PARTENARIATO_COPY.filtroTitolo}
              aria-describedby={notaId}
              className="flex flex-col gap-2"
            >
              <RadioGroup
                nome="partenariato-cassetto"
                legend={PARTENARIATO_COPY.filtroTitolo}
                opzioni={[
                  { id: "", label: PARTENARIATO_COPY.filtroTutti },
                  { id: "ammesso", label: PARTENARIATO_COPY.filtro.ammesso },
                  { id: "obbligatorio", label: PARTENARIATO_COPY.filtro.obbligatorio },
                ]}
                valore={filters.partenariato ?? ""}
                onChange={(id) =>
                  onUpdate({
                    partenariato: id === "ammesso" || id === "obbligatorio" ? id : null,
                  })
                }
              />
              <p id={notaId} className="text-small text-ink-3">
                {PARTENARIATO_COPY.filtroNota}
              </p>
            </div>
          )}

          <div className="sticky bottom-0 -mx-3 -mb-4 flex gap-2 border-t border-line bg-desk px-3 py-3">
            <Button type="button" onClick={chiudi} className="flex-1">
              {totale === undefined
                ? "Mostra i risultati"
                : `Mostra ${totale.toLocaleString("it-IT")} ${totale === 1 ? "bando" : "bandi"}`}
            </Button>
            {activeCount > 0 && (
              <Button type="button" variant="ghost" onClick={onReset}>
                Azzera
              </Button>
            )}
          </div>
        </div>
      )}
    </Drawer>
  );
}
