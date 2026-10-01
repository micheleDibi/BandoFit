import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { cn } from "../../lib/cn";
import { CALL_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import { nomePaese } from "../../lib/paesi";
import type { CallPubblica, PosizionePubblicaCall } from "../../types";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Facts, type Fatto } from "../ui/Facts";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { TextLink } from "../ui/TextLink";
import { CLASSI_DIMENSIONALI } from "./AnteprimaPartnerCard";
import { descriviCriterio, percentuale } from "./callDati";
import { etichettaForma } from "./PartenariatoRegole";
import { useNomiCall } from "./useNomiCall";

function Posizione({ p }: { p: PosizionePubblicaCall }) {
  const nomi = useNomiCall();
  const righe: Array<[string, string]> = [];
  if (p.tipi_soggetto.length) righe.push(["Tipo di soggetto", p.tipi_soggetto.map(nomi.tipo).join(", ")]);
  if (p.competenze.length) righe.push(["Competenze", p.competenze.map(nomi.competenza).join(", ")]);
  if (p.ateco_divisioni.length) righe.push(["Attività ATECO", p.ateco_divisioni.join(", ")]);
  if (p.territorio_modalita !== "qualsiasi") {
    const regioni = p.regioni_nomi.length ? p.regioni_nomi : p.regioni.map(nomi.regione);
    righe.push([CALL_COPY.territorio[p.territorio_modalita], regioni.join(", ")]);
  }
  if (p.paesi.length) righe.push(["Paesi", p.paesi.map(nomePaese).join(", ")]);
  if (p.dimensioni.length) {
    righe.push(["Dimensione", p.dimensioni.map((d) => CALL_COPY.dimensioni[d] ?? d).join(", ")]);
  }
  if (p.requisiti.length) righe.push(["Copre i requisiti", p.requisiti.join(", ")]);
  return (
    <li className="flex flex-col gap-1 border-b border-line py-3">
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="font-medium text-ink">{p.titolo}</span>
        <span className="text-small text-ink-2">{p.ruolo === "capofila" ? "Capofila" : "Partner"}</span>
        {p.numero > 1 && <span className="text-small text-ink-2">{p.numero} partner</span>}
        {p.quota_ipotizzata_pct && (
          <span className="text-small text-ink-2">Quota {percentuale(p.quota_ipotizzata_pct)}</span>
        )}
      </p>
      {righe.length > 0 && (
        <dl className="flex flex-col gap-0.5 text-small">
          {righe.map(([t, v]) => (
            <div key={t}>
              <dt className="inline text-ink-3">{t}: </dt>
              <dd className="inline text-ink-2">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {p.note && <p className="whitespace-pre-line text-small text-ink-2">{p.note}</p>}
    </li>
  );
}

/** La call come la vedono le altre aziende (proiezione a whitelist del server:
 *  il nome solo per una call con il nome di un'azienda verificata, niente
 *  budget esatto, niente dettagli riservati né coperture del creatore). I
 *  testi sono testo semplice, mai HTML né link. Sezioni con titolo e filetto,
 *  senza riquadro; `senzaTitolo` quando il titolo lo mette già l'intestazione
 *  della pagina (nell'anteprima del wizard resta qui). */
export function CallPubblicaCard({
  call,
  className,
  senzaTitolo = false,
}: {
  call: CallPubblica;
  className?: string;
  senzaTitolo?: boolean;
}) {
  const { data: vocabolario } = usePartenariatiVocabolario();
  const nomi = useNomiCall();
  const creatore = call.creatore;
  const classe = creatore.classe_dimensionale
    ? (CLASSI_DIMENSIONALI[creatore.classe_dimensionale] ?? creatore.classe_dimensionale)
    : null;
  const chi = [classe, creatore.regione, creatore.ateco_sezione?.descrizione ?? null]
    .filter(Boolean)
    .join(", ");

  const fatti: Fatto[] = [
    { etichetta: "Chi propone", valore: creatore.denominazione || CALL_COPY.aziendaAnonima, nota: chi || undefined },
    { etichetta: "Ruolo", valore: CALL_COPY.ruoliCreatoreBrevi[call.ruolo_creatore] },
  ];
  if (call.forma_aggregazione_prevista) {
    fatti.push({ etichetta: "Forma prevista", valore: etichettaForma(call.forma_aggregazione_prevista, vocabolario) });
  }
  if (call.budget_fascia) {
    fatti.push({ etichetta: "Budget", valore: CALL_COPY.fasceBudget[call.budget_fascia] });
  }
  if (call.scadenza_call) {
    fatti.push({ etichetta: "Candidature fino al", valore: formatDate(call.scadenza_call) });
  }

  return (
    <div className={cn("flex flex-col gap-6", className)}>
      {!senzaTitolo && (
        <div className="flex flex-col gap-1">
          <h2 className="font-sans text-row-title text-ink">{call.titolo || "Call senza titolo"}</h2>
          <p className="text-body text-ink-2">
            Per il bando <TextLink to={`/app/bandi/${call.bando.slug}`}>{call.bando.titolo}</TextLink>
          </p>
        </div>
      )}

      <Facts items={fatti} />

      {call.descrizione_pubblica && (
        <Section>
          <SectionHeader titolo="Il progetto" livello={3} />
          <p className="max-w-[680px] whitespace-pre-line text-prose text-ink">{call.descrizione_pubblica}</p>
        </Section>
      )}
      {call.profilo_partner_ideale && (
        <Section>
          <SectionHeader titolo="Il partner ideale" livello={3} />
          <p className="max-w-[680px] whitespace-pre-line text-prose text-ink">{call.profilo_partner_ideale}</p>
        </Section>
      )}
      <Section>
        <SectionHeader titolo="Posizioni cercate" livello={3} />
        {call.posizioni.length === 0 ? (
          <p className="text-body text-ink-3">Nessuna posizione indicata.</p>
        ) : (
          <ul className="flex flex-col">
            {call.posizioni.map((p) => (
              <Posizione key={p.id} p={p} />
            ))}
          </ul>
        )}
      </Section>
      <Section>
        <SectionHeader titolo="Requisiti cercati" livello={3} />
        {call.requisiti.length === 0 ? (
          <p className="text-body text-ink-3">Nessun requisito indicato.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {call.requisiti.map((r) => (
              <li key={r.etichetta} className="flex items-start gap-2 text-body text-ink">
                <Badge className="mt-0.5 shrink-0 tabular-nums">{r.etichetta}</Badge>
                <span>
                  {r.testo}
                  <span className="block text-small text-ink-3">
                    {descriviCriterio(r.criterio, nomi)}, {CALL_COPY.ambiti[r.ambito]}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {call.esclusivita && (
        <Alert tono="info" titolo="Esclusività">
          Il bando ammette la partecipazione a un solo partenariato: chi entra in questo non può
          partecipare ad altri sullo stesso bando.
        </Alert>
      )}
    </div>
  );
}
