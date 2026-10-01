import { Check, Minus } from "lucide-react";
import { useCompany } from "../../hooks/useCompany";
import type { BandoDetail, CompatibilitaDimensione } from "../../types";
import { TextLink } from "../ui/TextLink";

/** Oltre questa soglia le voci si riassumono in «e altri N». */
const MAX_VOCI = 6;

interface Voce {
  id: number;
  label: string;
}

function elenco(voci: Voce[]): string {
  const visibili = voci.slice(0, MAX_VOCI).map((v) => v.label).join(", ");
  const resto = voci.length - MAX_VOCI;
  return resto > 0 ? `${visibili} e altri ${resto}` : visibili;
}

type Esito = "ok" | "no" | "neutro";

const PAROLA_ESITO: Record<Esito, string> = {
  ok: "soddisfatto",
  no: "non soddisfatto",
  neutro: "non valutato",
};

/** Un requisito del bando, spiegato in una riga. Le sue voci sono ALTERNATIVE:
 *  ne basta una in comune perché il requisito sia soddisfatto (un bando su
 *  quattro settori non chiede di operare in tutti e quattro). `dim` assente =
 *  requisito non valutabile: l'azienda non ha quel dato e non entra nel conto. */
function Requisito({
  nome,
  voci,
  dim,
  valutabile,
}: {
  nome: string;
  voci: Voce[];
  dim?: CompatibilitaDimensione;
  valutabile: boolean;
}) {
  if (voci.length === 0) return null;

  const inComune = voci.filter((v) => dim?.matched_ids.includes(v.id));
  let esito: Esito;
  let spiegazione: string;
  if (!valutabile) {
    esito = "neutro";
    spiegazione = `Il bando chiede: ${elenco(voci)}`;
  } else if (!dim) {
    esito = "neutro";
    spiegazione = "Non valutato: manca il dato nella tua azienda";
  } else if (dim.nazionale) {
    esito = "ok";
    spiegazione = "Il bando è aperto a tutte le regioni";
  } else if (dim.soddisfatta) {
    esito = "ok";
    spiegazione = elenco(inComune);
  } else {
    esito = "no";
    spiegazione = `Il bando chiede: ${elenco(voci)}`;
  }

  return (
    <li className="flex items-start gap-2.5 border-b border-line py-2.5 last:border-b-0">
      {esito === "ok" ? (
        <Check className="mt-0.5 size-4 shrink-0 text-fit-ink" aria-hidden />
      ) : (
        <Minus className="mt-0.5 size-4 shrink-0 text-ink-3" aria-hidden />
      )}
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-body font-semibold text-ink">
          {nome}
          {valutabile && <span className="sr-only">: {PAROLA_ESITO[esito]}</span>}
        </p>
        <p className="text-small text-ink-2">{spiegazione}</p>
      </div>
    </li>
  );
}

/** Compatibilità nel pannello «Fa per te?»: i requisiti di catalogo del bando
 *  (regione, settore, ATECO, beneficiari) e quali la tua azienda soddisfa,
 *  tutte le sedi comprese. Il contatore (`Fit`) lo mette il pannello; qui le
 *  righe spiegate. Non sostituisce l'AI-check, che legge il testo del bando. */
export function CompatibilitaCard({ bando }: { bando: BandoDetail }) {
  const { data: azienda } = useCompany();
  const compat = bando.compatibilita ?? null;
  const dims = compat?.dimensioni ?? undefined;

  const voci = {
    regioni: bando.regioni.map((r) => ({ id: r.id, label: r.nome })),
    settori: bando.settori.map((s) => ({ id: s.id, label: s.nome })),
    ateco: bando.codici_ateco.map((c) => ({ id: c.id, label: c.codice })),
    beneficiari: bando.beneficiari.map((b) => ({ id: b.id, label: b.nome })),
  };

  // Il bando non dichiara alcun requisito di catalogo: niente da confrontare.
  if (Object.values(voci).every((v) => v.length === 0)) return null;

  const nomeAzienda = azienda?.company?.ragione_sociale;

  return (
    <div id="compatibilita" className="flex flex-col gap-2">
      {compat ? (
        <p className="text-small text-ink-2">
          Requisiti del bando soddisfatti {nomeAzienda ? `da ${nomeAzienda}` : "dalla tua azienda"},
          tutte le sedi comprese.
        </p>
      ) : (
        <p className="text-small text-ink-2">
          A chi si rivolge questo bando.{" "}
          <TextLink to="/app/azienda">Importa la tua azienda</TextLink> da P.IVA per vedere
          quanto sei compatibile.
        </p>
      )}
      <ul className="flex flex-col">
        <Requisito nome="Regione" voci={voci.regioni} dim={dims?.regioni} valutabile={!!compat} />
        <Requisito nome="Settore" voci={voci.settori} dim={dims?.settori} valutabile={!!compat} />
        <Requisito nome="Codice ATECO" voci={voci.ateco} dim={dims?.ateco} valutabile={!!compat} />
        <Requisito
          nome="Beneficiari"
          voci={voci.beneficiari}
          dim={dims?.beneficiari}
          valutabile={!!compat}
        />
      </ul>
    </div>
  );
}
