import { formatDateNumeric, formatEur } from "../../../lib/format";
import type { CompanyDossier, DossierPerson } from "../../../types";
import { Accordion, type VoceAccordion } from "../../ui/Accordion";
import { Badge } from "../../ui/Badge";
import { TextLink } from "../../ui/TextLink";
import { DossierGrid, DossierRow } from "./DossierSection";
import { PeopleTable } from "./PeopleTable";

const FLAG_LABELS: Record<string, string> = {
  esportatore: "Esportatore",
  importatore: "Importatore",
  startup_innovativa: "Startup innovativa",
  pmi_innovativa: "PMI innovativa",
  impresa_artigiana: "Impresa artigiana",
  certificazione_soa: "Certificazione SOA",
  gruppo_societario: "Gruppo societario",
};

const CONTRATTI_LABELS: Record<string, string> = {
  tempo_indeterminato: "Tempo indeterminato",
  tempo_determinato: "Tempo determinato",
  full_time: "Full time",
  part_time: "Part time",
  impiegati: "Impiegati",
  operai: "Operai",
  apprendisti: "Apprendisti",
};

/** Il dossier certificato a sezioni richiudibili (`Accordion`): aperte solo
 *  le prime due. Condiviso tra la pagina Azienda (il titolare vede il proprio)
 *  e l'area progettista (vista full post-assegnazione, con accesso registrato
 *  lato server). `linkBilanci`: solo dove la pagina ha la scheda Bilanci. */
export function DossierView({
  dossier,
  people,
  linkBilanci = false,
}: {
  dossier: CompanyDossier;
  people: DossierPerson[];
  linkBilanci?: boolean;
}) {
  const { anagrafica, attivita, sede, contatti, dipendenti, bilanci, partecipazioni, flags } =
    dossier;
  const contratti = dipendenti.percentuali_contratti;
  const activeFlags = Object.entries(flags ?? {}).filter(([, v]) => v === true);
  // Anno e data di chiusura da soli non fanno una sezione: servono dei valori.
  // Un dossier salvato prima dei bilanci per esercizio non ha `anno` né
  // `data_chiusura`: restano undefined e la riga «Esercizio» non compare.
  const {
    anno: annoBilancio,
    data_chiusura: chiusuraBilancio,
    anno_fatturato: annoFatturato,
    ...valoriBilancio
  } = bilanci ?? ({} as Partial<CompanyDossier["bilanci"]>);
  const hasBilanci = Object.values(valoriBilancio).some((v) => v !== null && v !== undefined);
  // Il fatturato della visura può riferirsi a un anno diverso dall'esercizio
  // (la tabella dei bilanci, per la stessa ragione, non lo mette in quell'anno).
  const etichettaFatturato =
    annoFatturato && annoBilancio && annoFatturato !== annoBilancio
      ? `Fatturato (${annoFatturato})`
      : "Fatturato";

  const sezioni: VoceAccordion[] = [
    {
      id: "dossier-anagrafica",
      titolo: "Anagrafica",
      aperto: true,
      children: (
        <DossierGrid>
          <DossierRow label="Denominazione" value={anagrafica.denominazione} />
          <DossierRow label="Partita IVA" value={anagrafica.partita_iva} />
          <DossierRow label="Codice fiscale" value={anagrafica.codice_fiscale} />
          <DossierRow
            label="Forma giuridica"
            value={anagrafica.forma_giuridica_dettaglio ?? anagrafica.forma_giuridica}
          />
          <DossierRow label="REA" value={anagrafica.rea} />
          <DossierRow label="CCIAA" value={anagrafica.cciaa} />
          <DossierRow
            label="Data di costituzione"
            value={
              anagrafica.data_costituzione ? formatDateNumeric(anagrafica.data_costituzione) : null
            }
          />
          <DossierRow
            label="Inizio attività"
            value={
              anagrafica.data_inizio_attivita
                ? formatDateNumeric(anagrafica.data_inizio_attivita)
                : null
            }
          />
          <DossierRow label="Gruppo societario" value={anagrafica.gruppo_societario} />
          <DossierRow label="Capogruppo" value={anagrafica.capogruppo} />
        </DossierGrid>
      ),
    },
    {
      id: "dossier-attivita",
      titolo: "Attività e ATECO",
      aperto: true,
      children: (
        <DossierGrid>
          <DossierRow
            label="ATECO principale"
            value={
              attivita.ateco.codice
                ? `${attivita.ateco.codice}${attivita.ateco.descrizione ? ` — ${attivita.ateco.descrizione}` : ""}`
                : null
            }
          />
          <DossierRow
            label="ATECO 2022"
            value={
              attivita.ateco_2022.codice && attivita.ateco_2022.codice !== attivita.ateco.codice
                ? `${attivita.ateco_2022.codice}${attivita.ateco_2022.descrizione ? ` — ${attivita.ateco_2022.descrizione}` : ""}`
                : null
            }
          />
          <DossierRow
            label="ATECO secondari"
            value={attivita.ateco_secondari.length ? attivita.ateco_secondari.join(", ") : null}
          />
          <DossierRow label="NACE" value={attivita.nace} />
          <DossierRow label="SAE" value={attivita.sae} />
        </DossierGrid>
      ),
    },
    {
      id: "dossier-sede",
      titolo: "Sede e unità locali",
      children: (
        <div className="flex flex-col gap-4">
          <DossierGrid>
            <DossierRow
              label="Sede legale"
              value={
                [sede.indirizzo, sede.cap, sede.comune, sede.provincia].filter(Boolean).join(", ") ||
                null
              }
            />
            <DossierRow label="Regione" value={sede.regione} />
            <DossierRow label="Numero sedi" value={sede.numero_sedi} />
          </DossierGrid>
          {sede.unita_locali.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-small text-ink-3">Tutte le sedi</p>
              <ul className="flex flex-col">
                {sede.unita_locali.map((unita, index) => (
                  <li
                    key={index}
                    className="flex flex-wrap items-center gap-2 border-b border-line py-2 text-body last:border-b-0"
                  >
                    <span className="text-ink">
                      {[unita.indirizzo, unita.cap, unita.comune, unita.provincia]
                        .filter(Boolean)
                        .join(", ")}
                    </span>
                    {unita.regione && <Badge>{unita.regione}</Badge>}
                    {unita.tipo && <span className="text-small text-ink-3">{unita.tipo}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ),
    },
    {
      id: "dossier-persone",
      titolo: "Persone e cariche",
      children: <PeopleTable people={people} />,
    },
  ];

  if (partecipazioni.length > 0) {
    sezioni.push({
      id: "dossier-partecipazioni",
      titolo: "Partecipazioni",
      children: (
        <ul className="flex flex-col">
          {partecipazioni.map((p, index) => (
            <li
              key={index}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 text-body last:border-b-0"
            >
              <span className="font-medium text-ink">{p.denominazione ?? "—"}</span>
              <span className="text-ink-2">
                {[p.codice_fiscale ? `CF ${p.codice_fiscale}` : null, p.quota !== null ? `${p.quota}%` : null]
                  .filter(Boolean)
                  .join(", ")}
              </span>
            </li>
          ))}
        </ul>
      ),
    });
  }

  sezioni.push({
    id: "dossier-dipendenti",
    titolo: "Dipendenti",
    children: (
      <div className="flex flex-col gap-4">
        <DossierGrid>
          <DossierRow label="Numero dipendenti" value={dipendenti.numero} />
          <DossierRow label="Fascia" value={dipendenti.fascia} />
          <DossierRow
            label="Tendenza"
            value={
              dipendenti.trend !== null
                ? `${dipendenti.trend > 0 ? "+" : ""}${dipendenti.trend}%`
                : null
            }
          />
        </DossierGrid>
        {contratti && Object.values(contratti).some((v) => v !== null) && (
          <div className="flex flex-col gap-1">
            <p className="text-small text-ink-3">Composizione (%)</p>
            <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-4">
              {Object.entries(contratti)
                .filter(([, v]) => v !== null)
                .map(([key, value]) => (
                  <div key={key} className="flex items-baseline justify-between gap-2 sm:block">
                    <dt className="text-small text-ink-3">{CONTRATTI_LABELS[key] ?? key}</dt>
                    <dd className="text-body font-medium text-ink tabular-nums">{value}%</dd>
                  </div>
                ))}
            </dl>
          </div>
        )}
      </div>
    ),
  });

  if (hasBilanci) {
    sezioni.push({
      id: "dossier-economici",
      titolo: "Dati economici",
      children: (
        <div className="flex flex-col gap-3">
          {annoBilancio && (
            <p className="text-body text-ink-2">
              Esercizio <span className="font-medium text-ink tabular-nums">{annoBilancio}</span>
              {chiusuraBilancio && (
                <span className="tabular-nums">, chiuso il {formatDateNumeric(chiusuraBilancio)}</span>
              )}
            </p>
          )}
          <DossierGrid>
            <DossierRow label="Dimensione impresa" value={bilanci.dimensione_impresa} />
            <DossierRow
              label={etichettaFatturato}
              value={bilanci.fatturato !== null ? formatEur(bilanci.fatturato) : null}
            />
            <DossierRow
              label="Capitale sociale"
              value={bilanci.capitale_sociale !== null ? formatEur(bilanci.capitale_sociale) : null}
            />
            <DossierRow
              label="Patrimonio netto"
              value={bilanci.patrimonio_netto !== null ? formatEur(bilanci.patrimonio_netto) : null}
            />
            <DossierRow
              label="EBITDA"
              value={bilanci.ebitda !== null ? formatEur(bilanci.ebitda) : null}
            />
            <DossierRow label="Utile" value={bilanci.utile !== null ? formatEur(bilanci.utile) : null} />
          </DossierGrid>
          {linkBilanci && (
            <p className="text-small">
              <TextLink to="?tab=bilanci#bilanci" className="font-medium">
                Vedi tutti i bilanci
              </TextLink>
            </p>
          )}
        </div>
      ),
    });
  }

  sezioni.push({
    id: "dossier-contatti",
    titolo: "Contatti",
    children: (
      <DossierGrid>
        <DossierRow label="PEC" value={contatti.pec} />
        <DossierRow label="Email" value={contatti.email} />
        <DossierRow label="Telefono" value={contatti.telefono} />
        <DossierRow label="Fax" value={contatti.fax} />
        <DossierRow label="Sito web" value={contatti.sito_web} />
      </DossierGrid>
    ),
  });

  if (activeFlags.length > 0) {
    sezioni.push({
      id: "dossier-attributi",
      titolo: "Attributi",
      children: (
        <div className="flex flex-wrap gap-2">
          {activeFlags.map(([key]) => (
            <Badge key={key}>{FLAG_LABELS[key] ?? key}</Badge>
          ))}
        </div>
      ),
    });
  }

  return <Accordion items={sezioni} />;
}
