import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { BACHECA_COPY } from "../../lib/copy";
import type { FiltriBacheca as Filtri, FormaPrevistaCall, OrdineBacheca, RuoloPartner } from "../../types";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { Filter } from "../ui/Filter";
import { usePopover } from "../ui/Popover";
import { RadioGroup, type RadioOpzione } from "../ui/RadioGroup";
import { Select } from "../ui/Select";
import { etichettaForma } from "./PartenariatoRegole";

const ORDINI: OrdineBacheca[] = ["affinita", "recenti", "scadenza"];
const ORDINE_PREDEFINITO: OrdineBacheca = "affinita";
const RUOLI: RuoloPartner[] = ["capofila", "partner"];
const FORME: FormaPrevistaCall[] = [
  "ats",
  "ati_rti",
  "rete_contratto",
  "rete_soggetto",
  "consorzio",
  "accordo_partenariato",
  "consorzio_ue",
];
/** Id dell'opzione «nessun filtro» nei pannelli a scelta singola. */
const TUTTI = "tutti";

function interoPositivo(raw: string | null): number | null {
  if (!raw) return null;
  const n = Number(raw);
  return Number.isInteger(n) && n > 0 ? n : null;
}

function slug(raw: string | null): string | null {
  const s = (raw ?? "").trim();
  return s && s.length <= 200 ? s : null;
}

function tra<T extends string>(raw: string | null, ammessi: readonly T[]): T | null {
  return ammessi.includes(raw as T) ? (raw as T) : null;
}

/** Pagina corrente dei searchParams (`page`, da 1). */
export function paginaDa(params: URLSearchParams): number {
  return interoPositivo(params.get("page")) ?? 1;
}

/** Filtri della bacheca nei searchParams (condivisibili, back-friendly):
 *  `bando` (slug), `regione` (id), `forma`, `ruolo`, `ordine`, più `page`.
 *  Ogni cambio di filtro riparte dalla prima pagina. */
export function useFiltriBacheca() {
  const [params, setParams] = useSearchParams();

  const filtri: Filtri = useMemo(
    () => ({
      bando: slug(params.get("bando")),
      regione: interoPositivo(params.get("regione")),
      forma: tra(params.get("forma"), FORME),
      ruolo: tra(params.get("ruolo"), RUOLI),
      ordine: tra(params.get("ordine"), ORDINI) ?? ORDINE_PREDEFINITO,
    }),
    [params],
  );

  const aggiorna = useCallback(
    (cambi: Partial<Filtri>) =>
      setParams((prima) => {
        const dopo = new URLSearchParams(prima);
        for (const [chiave, valore] of Object.entries(cambi)) {
          if (valore === undefined) continue;
          const vuoto = valore === null || valore === "" || (chiave === "ordine" && valore === ORDINE_PREDEFINITO);
          if (vuoto) dopo.delete(chiave);
          else dopo.set(chiave, String(valore));
        }
        dopo.delete("page");
        return dopo;
      }),
    [setParams],
  );

  const azzera = useCallback(
    () =>
      setParams((prima) => {
        const dopo = new URLSearchParams(prima);
        // L'ordine non è un filtro: resta com'è.
        for (const chiave of ["bando", "regione", "forma", "ruolo", "page"]) dopo.delete(chiave);
        return dopo;
      }),
    [setParams],
  );

  const attivi =
    (filtri.bando ? 1 : 0) + (filtri.regione !== null ? 1 : 0) + (filtri.forma ? 1 : 0) + (filtri.ruolo ? 1 : 0);

  return { filtri, aggiorna, azzera, attivi };
}

/** Pannello di un filtro a scelta singola: la scelta si applica subito e il
 *  pannello si chiude. La prima opzione è «nessun filtro». */
function SceltaFiltro({
  nome,
  legend,
  opzioni,
  valore,
  onScegli,
}: {
  nome: string;
  legend: string;
  opzioni: readonly RadioOpzione[];
  valore: string;
  onScegli: (id: string) => void;
}) {
  const { chiudi } = usePopover();
  return (
    <RadioGroup
      nome={nome}
      legend={legend}
      opzioni={opzioni}
      valore={valore}
      onChange={(id) => {
        onScegli(id);
        chiudi();
      }}
      className="min-w-56"
    />
  );
}

/** Filtri della bacheca («Tutte le call»): regione, forma e ruolo come
 *  pulsanti-filtro che aprono un pannello a scelta singola; l'ordine in un
 *  `Select`; il bando (arriva da un link della scheda del bando) come filtro
 *  attivo rimovibile. */
export function FiltriBacheca({
  filtri,
  aggiorna,
  azzera,
  attivi,
  titoloBando,
}: ReturnType<typeof useFiltriBacheca> & {
  /** Titolo del bando filtrato, se la pagina lo conosce (dalle card). */
  titoloBando?: string | null;
}) {
  const { data: lookups } = useLookups();
  const { data: vocabolario } = usePartenariatiVocabolario();

  const regioni = lookups?.regioni ?? [];
  const nomeRegione = regioni.find((r) => r.id === filtri.regione)?.nome;

  return (
    <div role="search" aria-label="Filtri delle call" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Filter label="Regione" attivo={filtri.regione !== null} valore={nomeRegione}>
          <SceltaFiltro
            nome="regione"
            legend="Regione"
            opzioni={[
              { id: TUTTI, label: "Tutte le regioni" },
              ...regioni.map((r) => ({ id: String(r.id), label: r.nome })),
            ]}
            valore={filtri.regione === null ? TUTTI : String(filtri.regione)}
            onScegli={(id) => aggiorna({ regione: id === TUTTI ? null : interoPositivo(id) })}
          />
        </Filter>
        <Filter
          label="Forma prevista"
          attivo={filtri.forma !== null}
          valore={filtri.forma ? etichettaForma(filtri.forma, vocabolario) : undefined}
        >
          <SceltaFiltro
            nome="forma"
            legend="Forma prevista"
            opzioni={[
              { id: TUTTI, label: "Tutte le forme" },
              ...FORME.map((f) => ({ id: f, label: etichettaForma(f, vocabolario) })),
            ]}
            valore={filtri.forma ?? TUTTI}
            onScegli={(id) => aggiorna({ forma: tra(id, FORME) })}
          />
        </Filter>
        <Filter
          label="Il tuo ruolo"
          attivo={filtri.ruolo !== null}
          valore={filtri.ruolo ? BACHECA_COPY.ruoli[filtri.ruolo] : undefined}
        >
          <SceltaFiltro
            nome="ruolo"
            legend="Il ruolo che avresti nel partenariato"
            opzioni={[
              { id: TUTTI, label: "Qualsiasi ruolo" },
              ...RUOLI.map((r) => ({ id: r, label: BACHECA_COPY.ruoli[r] })),
            ]}
            valore={filtri.ruolo ?? TUTTI}
            onScegli={(id) => aggiorna({ ruolo: tra(id, RUOLI) })}
          />
        </Filter>
        {attivi > 0 && (
          <Button type="button" variant="ghost" size="sm" onClick={azzera}>
            Azzera i filtri
          </Button>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-small text-ink-3" aria-hidden>
            Ordina per
          </span>
          <Select
            label="Ordina per"
            value={filtri.ordine}
            onChange={(e) => aggiorna({ ordine: tra(e.target.value, ORDINI) ?? ORDINE_PREDEFINITO })}
          >
            {ORDINI.map((o) => (
              <option key={o} value={o}>
                {BACHECA_COPY.ordini[o]}
              </option>
            ))}
          </Select>
        </div>
      </div>
      {filtri.bando && (
        <div className="flex flex-wrap items-center gap-2">
          <Chip onRemove={() => aggiorna({ bando: null })} label="Togli il filtro sul bando">
            Bando: {titoloBando || filtri.bando}
          </Chip>
        </div>
      )}
    </div>
  );
}
