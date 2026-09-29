import { X } from "lucide-react";
import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { BACHECA_COPY } from "../../lib/copy";
import type { FiltriBacheca as Filtri, FormaPrevistaCall, OrdineBacheca, RuoloPartner } from "../../types";
import { Button } from "../ui/Button";
import { SelectField } from "../ui/Field";
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

/** Filtri della bacheca («Tutte le call»): regione, forma, ruolo e ordine in
 *  select con etichetta; il bando (arriva da un link della scheda del bando)
 *  come voce rimovibile. */
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

  return (
    <form
      role="search"
      aria-label="Filtri delle call"
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"
      onSubmit={(e) => e.preventDefault()}
    >
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SelectField
          label="Regione"
          value={filtri.regione ?? ""}
          onChange={(e) => aggiorna({ regione: interoPositivo(e.target.value) })}
        >
          <option value="">Tutte le regioni</option>
          {(lookups?.regioni ?? []).map((r) => (
            <option key={r.id} value={r.id}>
              {r.nome}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Forma prevista"
          value={filtri.forma ?? ""}
          onChange={(e) => aggiorna({ forma: tra(e.target.value, FORME) })}
        >
          <option value="">Tutte le forme</option>
          {FORME.map((f) => (
            <option key={f} value={f}>
              {etichettaForma(f, vocabolario)}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Il tuo ruolo"
          value={filtri.ruolo ?? ""}
          onChange={(e) => aggiorna({ ruolo: tra(e.target.value, RUOLI) })}
          helper="Il ruolo che avresti nel partenariato."
        >
          <option value="">Qualsiasi ruolo</option>
          {RUOLI.map((r) => (
            <option key={r} value={r}>
              {BACHECA_COPY.ruoli[r]}
            </option>
          ))}
        </SelectField>
        <SelectField
          label="Ordina per"
          value={filtri.ordine}
          onChange={(e) => aggiorna({ ordine: tra(e.target.value, ORDINI) ?? ORDINE_PREDEFINITO })}
        >
          {ORDINI.map((o) => (
            <option key={o} value={o}>
              {BACHECA_COPY.ordini[o]}
            </option>
          ))}
        </SelectField>
      </div>
      {(filtri.bando || attivi > 0) && (
        <div className="flex flex-wrap items-center gap-2">
          {filtri.bando && (
            <span className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-50 py-1 pl-3 pr-1 text-xs font-medium text-brand-700 ring-1 ring-inset ring-brand-200">
              <span className="truncate">Bando: {titoloBando || filtri.bando}</span>
              <button
                type="button"
                onClick={() => aggiorna({ bando: null })}
                className="inline-flex size-5 cursor-pointer items-center justify-center rounded-full hover:bg-brand-100 focus-visible:outline-2 focus-visible:outline-brand-500"
                aria-label="Togli il filtro sul bando"
              >
                <X className="size-3" aria-hidden />
              </button>
            </span>
          )}
          {attivi > 0 && (
            <Button type="button" variant="ghost" size="sm" onClick={azzera}>
              Azzera i filtri
            </Button>
          )}
        </div>
      )}
    </form>
  );
}
