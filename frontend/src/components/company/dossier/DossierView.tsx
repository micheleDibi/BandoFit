import {
  Activity,
  Building2,
  Contact,
  FileSpreadsheet,
  Landmark,
  MapPin,
  Tags,
  Users,
} from "lucide-react";
import { Badge } from "../../ui/Badge";
import { formatDateNumeric, formatEur } from "../../../lib/format";
import type { CompanyDossier, DossierPerson } from "../../../types";
import { DossierGrid, DossierRow, DossierSection } from "./DossierSection";
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

/** Rendering a sezioni del dossier certificato. Condiviso tra la pagina
 *  Azienda (il titolare vede il proprio) e l'area progettista (vista full
 *  post-assegnazione, con accesso registrato lato server). */
export function DossierView({
  dossier,
  people,
}: {
  dossier: CompanyDossier;
  people: DossierPerson[];
}) {
  const { anagrafica, attivita, sede, contatti, dipendenti, bilanci, partecipazioni, flags } =
    dossier;
  const contratti = dipendenti.percentuali_contratti;
  const activeFlags = Object.entries(flags ?? {}).filter(([, v]) => v === true);
  const hasBilanci = Object.values(bilanci ?? {}).some((v) => v !== null);

  return (
    <div className="mt-4 space-y-4">
      <DossierSection title="Anagrafica" icon={<Building2 className="size-4" aria-hidden />}>
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
            value={anagrafica.data_costituzione ? formatDateNumeric(anagrafica.data_costituzione) : null}
          />
          <DossierRow
            label="Inizio attività"
            value={anagrafica.data_inizio_attivita ? formatDateNumeric(anagrafica.data_inizio_attivita) : null}
          />
          <DossierRow label="Gruppo societario" value={anagrafica.gruppo_societario} />
          <DossierRow label="Capogruppo" value={anagrafica.capogruppo} />
        </DossierGrid>
      </DossierSection>

      <DossierSection title="Attività e ATECO" icon={<Activity className="size-4" aria-hidden />}>
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
      </DossierSection>

      <DossierSection title="Sede e unità locali" icon={<MapPin className="size-4" aria-hidden />}>
        <DossierGrid>
          <DossierRow
            label="Sede legale"
            value={[sede.indirizzo, sede.cap, sede.comune, sede.provincia]
              .filter(Boolean)
              .join(", ") || null}
          />
          <DossierRow label="Regione" value={sede.regione} />
          <DossierRow label="Numero sedi" value={sede.numero_sedi} />
        </DossierGrid>
        {sede.unita_locali.length > 0 && (
          <div className="mt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Tutte le sedi
            </p>
            <ul className="mt-2 divide-y divide-slate-100">
              {sede.unita_locali.map((unita, index) => (
                <li key={index} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span className="text-slate-800">
                    {[unita.indirizzo, unita.cap, unita.comune, unita.provincia]
                      .filter(Boolean)
                      .join(", ")}
                  </span>
                  {unita.regione && <Badge tone="slate">{unita.regione}</Badge>}
                  {unita.tipo && <span className="text-xs text-slate-400">{unita.tipo}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </DossierSection>

      <DossierSection title="Persone e cariche" icon={<Users className="size-4" aria-hidden />}>
        <PeopleTable people={people} />
      </DossierSection>

      {partecipazioni.length > 0 && (
        <DossierSection title="Partecipazioni" icon={<Landmark className="size-4" aria-hidden />}>
          <ul className="divide-y divide-slate-100">
            {partecipazioni.map((p, index) => (
              <li key={index} className="flex flex-wrap items-center justify-between gap-2 py-2 first:pt-0 last:pb-0 text-sm">
                <span className="font-medium text-slate-800">{p.denominazione ?? "—"}</span>
                <span className="text-slate-500">
                  {p.codice_fiscale && `CF ${p.codice_fiscale}`}
                  {p.quota !== null && ` · ${p.quota}%`}
                </span>
              </li>
            ))}
          </ul>
        </DossierSection>
      )}

      <DossierSection title="Dipendenti" icon={<Contact className="size-4" aria-hidden />}>
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
          <div className="mt-4">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Composizione (%)
            </p>
            <dl className="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-4">
              {Object.entries(contratti)
                .filter(([, v]) => v !== null)
                .map(([key, value]) => (
                  <div key={key} className="flex items-baseline justify-between gap-2 sm:block">
                    <dt className="text-xs text-slate-400">{CONTRATTI_LABELS[key] ?? key}</dt>
                    <dd className="text-sm font-medium tabular-nums text-slate-800">{value}%</dd>
                  </div>
                ))}
            </dl>
          </div>
        )}
      </DossierSection>

      {hasBilanci && (
        <DossierSection title="Dati economici" icon={<FileSpreadsheet className="size-4" aria-hidden />}>
          <DossierGrid>
            <DossierRow label="Dimensione impresa" value={bilanci.dimensione_impresa} />
            <DossierRow
              label="Fatturato"
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
            <DossierRow
              label="Utile"
              value={bilanci.utile !== null ? formatEur(bilanci.utile) : null}
            />
          </DossierGrid>
        </DossierSection>
      )}

      <DossierSection title="Contatti" icon={<Contact className="size-4" aria-hidden />}>
        <DossierGrid>
          <DossierRow label="PEC" value={contatti.pec} />
          <DossierRow label="Email" value={contatti.email} />
          <DossierRow label="Telefono" value={contatti.telefono} />
          <DossierRow label="Fax" value={contatti.fax} />
          <DossierRow label="Sito web" value={contatti.sito_web} />
        </DossierGrid>
      </DossierSection>

      {activeFlags.length > 0 && (
        <DossierSection title="Attributi" icon={<Tags className="size-4" aria-hidden />}>
          <div className="flex flex-wrap gap-2">
            {activeFlags.map(([key]) => (
              <Badge key={key} tone="brand">
                {FLAG_LABELS[key] ?? key}
              </Badge>
            ))}
          </div>
        </DossierSection>
      )}
    </div>
  );
}
