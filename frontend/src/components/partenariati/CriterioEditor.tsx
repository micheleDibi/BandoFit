import { useId, useMemo } from "react";
import { useLookups } from "../../hooks/useLookups";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { CALL_COPY } from "../../lib/copy";
import type {
  CategoriaCertificazione,
  CriterioPartner,
  DimensioneImpresa,
  RuoloPartner,
  TipoCriterio,
  TipoSoggettoPartenariato,
} from "../../types";
import { SceltaCodici, SceltaDivisioni, SceltaPaesi, SceltaRadio } from "./CampiCall";
import { LIMITI_CALL } from "./callDati";
import { GruppoCheckbox, SceltaLookup } from "./PartnerProfileForm";

/* Editor tipizzato di un criterio (C1): il tipo e i suoi valori, con i codici
 * del vocabolario e gli id del catalogo. Le regole economiche non si scrivono
 * a mano (Q11): arrivano solo dalle regole del bando confermate e qui si
 * vedono in sola lettura. */

/** Tipi che il creatore può scegliere (tutti tranne la regola economica). */
export const TIPI_CRITERIO_SCEGLIBILI: Exclude<TipoCriterio, "regola_finanziaria">[] = [
  "manuale",
  "tipo_soggetto",
  "tag",
  "regione",
  "paese",
  "ateco",
  "settore",
  "dimensione",
  "certificazione",
  "esperienza",
];

const DIMENSIONI: DimensioneImpresa[] = ["micro", "piccola", "media", "grande"];
const CATEGORIE = Object.keys(CALL_COPY.certificazioni) as CategoriaCertificazione[];

/** Criterio nuovo del tipo scelto, con le liste vuote da riempire. */
export function criterioVuoto(tipo: Exclude<TipoCriterio, "regola_finanziaria">): CriterioPartner {
  switch (tipo) {
    case "tipo_soggetto":
      return { tipo, valori: [] };
    case "tag":
      return { tipo, tags: [], modalita: "almeno_uno" };
    case "regione":
      return { tipo, regioni_ids: [], modalita: "sede_attuale" };
    case "paese":
      return { tipo, paesi: [], escludi: false };
    case "ateco":
      return { tipo, divisioni: [] };
    case "settore":
      return { tipo, settori_ids: [] };
    case "dimensione":
      return { tipo, valori: [] };
    case "certificazione":
      return { tipo, categorie: [] };
    case "esperienza":
      return { tipo, programmi_ids: [], ruolo: null };
    case "manuale":
      return { tipo };
  }
}

/** Il server vuole liste non vuote: altrimenti si usa «Solo a parole». */
export function erroreCriterio(c: CriterioPartner | null): string | null {
  if (!c) return null;
  const vuoto =
    (c.tipo === "tipo_soggetto" && c.valori.length === 0) ||
    (c.tipo === "tag" && c.tags.length === 0) ||
    (c.tipo === "regione" && c.regioni_ids.length === 0) ||
    (c.tipo === "paese" && c.paesi.length === 0) ||
    (c.tipo === "ateco" && c.divisioni.length === 0) ||
    (c.tipo === "settore" && c.settori_ids.length === 0) ||
    (c.tipo === "dimensione" && c.valori.length === 0) ||
    (c.tipo === "certificazione" && c.categorie.length === 0) ||
    (c.tipo === "esperienza" && c.programmi_ids.length === 0);
  return vuoto
    ? "Scegli almeno un valore per il criterio, oppure descrivi il requisito solo a parole."
    : null;
}

export function CriterioEditor({
  valore,
  onChange,
}: {
  valore: CriterioPartner | null;
  onChange: (c: CriterioPartner | null) => void;
}) {
  const idTipo = useId();
  const vocabolario = usePartenariatiVocabolario();
  const { data: lookups } = useLookups();
  const criterio = valore ?? { tipo: "manuale" as const };

  const tipi = useMemo(
    () =>
      vocabolario.data?.tipi_soggetto.map((t) => ({ codice: t.codice, etichetta: t.etichetta })),
    [vocabolario.data],
  );
  const competenze = useMemo(
    () =>
      vocabolario.data?.competenze.map((c) => ({
        codice: c.codice,
        etichetta: c.etichetta,
        gruppo: c.area,
      })),
    [vocabolario.data],
  );

  if (criterio.tipo === "regola_finanziaria") {
    return (
      <div className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
        <p className="font-medium text-slate-700">{CALL_COPY.tipiCriterio.regola_finanziaria}</p>
        <p className="mt-0.5">{criterio.regola.descrizione}</p>
        <p className="mt-1 text-xs text-slate-500">
          Viene dalle regole del bando che hai confermato: non si modifica a mano. Puoi solo
          toglierla o decidere se la cerchi.
        </p>
      </div>
    );
  }

  let campi = null;
  switch (criterio.tipo) {
    case "tipo_soggetto":
      campi = (
        <SceltaCodici
          etichetta="Tipi di soggetto (basta uno)"
          opzioni={tipi}
          scelti={criterio.valori}
          onChange={(v) => onChange({ ...criterio, valori: v as TipoSoggettoPartenariato[] })}
          massimo={30}
        />
      );
      break;
    case "tag":
      campi = (
        <>
          <SceltaCodici
            etichetta="Competenze"
            opzioni={competenze}
            scelti={criterio.tags}
            onChange={(v) => onChange({ ...criterio, tags: v })}
            massimo={15}
          />
          <SceltaRadio
            legenda="Quante ne servono"
            nome={`${idTipo}-modalita-tag`}
            valore={criterio.modalita}
            onChange={(m) => onChange({ ...criterio, modalita: m })}
            opzioni={[
              { valore: "almeno_uno", etichetta: "Almeno una" },
              { valore: "tutti", etichetta: "Tutte" },
            ]}
          />
        </>
      );
      break;
    case "regione":
      campi = (
        <>
          <SceltaLookup
            etichetta="Regioni"
            opzioni={lookups?.regioni}
            scelti={criterio.regioni_ids}
            onChange={(v) => onChange({ ...criterio, regioni_ids: v })}
            massimo={LIMITI_CALL.regioniMax}
          />
          <SceltaRadio
            legenda="Quando serve la sede"
            nome={`${idTipo}-modalita-regione`}
            valore={criterio.modalita}
            onChange={(m) => onChange({ ...criterio, modalita: m })}
            opzioni={[
              { valore: "sede_attuale", etichetta: "Già oggi", nota: "Una sede o unità locale nella regione." },
              {
                valore: "sede_entro_erogazione",
                etichetta: "Entro l'erogazione",
                nota: "Basta aprirla prima dell'erogazione del contributo.",
              },
            ]}
          />
        </>
      );
      break;
    case "paese":
      campi = (
        <>
          <SceltaPaesi
            etichetta="Paesi"
            scelti={criterio.paesi}
            onChange={(v) => onChange({ ...criterio, paesi: v })}
            massimo={LIMITI_CALL.paesiMax}
          />
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={criterio.escludi}
              onChange={(e) => onChange({ ...criterio, escludi: e.target.checked })}
            />
            <span>Escludi questi paesi (il partner NON deve essere di uno di questi)</span>
          </label>
        </>
      );
      break;
    case "ateco":
      campi = (
        <SceltaDivisioni
          scelte={criterio.divisioni}
          onChange={(v) => onChange({ ...criterio, divisioni: v })}
          massimo={20}
        />
      );
      break;
    case "settore":
      campi = (
        <SceltaLookup
          etichetta="Settori"
          opzioni={lookups?.settori}
          scelti={criterio.settori_ids}
          onChange={(v) => onChange({ ...criterio, settori_ids: v })}
          massimo={30}
        />
      );
      break;
    case "dimensione":
      campi = (
        <GruppoCheckbox
          legenda="Dimensioni ammesse (dal Registro Imprese)"
          opzioni={DIMENSIONI.map((d) => ({ codice: d, etichetta: CALL_COPY.dimensioni[d] }))}
          scelti={criterio.valori}
          onToggle={(d) =>
            onChange({
              ...criterio,
              valori: DIMENSIONI.filter((x) =>
                x === d ? !criterio.valori.includes(d) : criterio.valori.includes(x),
              ),
            })
          }
        />
      );
      break;
    case "certificazione":
      campi = (
        <GruppoCheckbox
          legenda="Categorie di certificazione (basta una)"
          opzioni={CATEGORIE.map((c) => ({ codice: c, etichetta: CALL_COPY.certificazioni[c] }))}
          scelti={criterio.categorie}
          onToggle={(c) =>
            onChange({
              ...criterio,
              categorie: CATEGORIE.filter((x) =>
                x === c ? !criterio.categorie.includes(c) : criterio.categorie.includes(x),
              ),
            })
          }
        />
      );
      break;
    case "esperienza":
      campi = (
        <>
          <SceltaLookup
            etichetta="Programmi di finanziamento"
            opzioni={lookups?.programmi}
            scelti={criterio.programmi_ids}
            onChange={(v) => onChange({ ...criterio, programmi_ids: v })}
            massimo={30}
          />
          <SceltaRadio<"qualsiasi" | RuoloPartner>
            legenda="Con quale ruolo"
            nome={`${idTipo}-ruolo-esperienza`}
            valore={criterio.ruolo ?? "qualsiasi"}
            onChange={(r) => onChange({ ...criterio, ruolo: r === "qualsiasi" ? null : r })}
            opzioni={[
              { valore: "qualsiasi", etichetta: "Qualsiasi ruolo" },
              { valore: "capofila", etichetta: "Come capofila" },
              { valore: "partner", etichetta: "Come partner" },
            ]}
          />
        </>
      );
      break;
    case "manuale":
      campi = (
        <p className="text-xs text-slate-500">
          Un requisito solo a parole non si controlla in automatico: lo verifichi tu con le aziende
          che si candidano.
        </p>
      );
      break;
  }

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <label htmlFor={idTipo} className="block text-sm font-medium text-slate-700">
          Come si verifica
        </label>
        <select
          id={idTipo}
          value={criterio.tipo}
          onChange={(e) => {
            const tipo = e.target.value as Exclude<TipoCriterio, "regola_finanziaria">;
            onChange(tipo === "manuale" ? null : criterioVuoto(tipo));
          }}
          className="h-10 w-full max-w-md cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
        >
          {TIPI_CRITERIO_SCEGLIBILI.map((t) => (
            <option key={t} value={t}>
              {CALL_COPY.tipiCriterio[t]}
            </option>
          ))}
        </select>
      </div>
      {campi}
    </div>
  );
}
