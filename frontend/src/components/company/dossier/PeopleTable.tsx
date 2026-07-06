import { BadgeCheck } from "lucide-react";
import { formatDateNumeric } from "../../../lib/format";
import type { DossierPerson } from "../../../types";
import { Badge } from "../../ui/Badge";

const KIND_LABELS: Record<DossierPerson["kind"], string> = {
  manager: "Carica",
  shareholder: "Socio",
  auditor: "Organo di controllo",
};

function displayName(person: DossierPerson): string {
  if (person.denominazione) return person.denominazione;
  return [person.nome, person.cognome].filter(Boolean).join(" ") || "—";
}

/** Cariche, soci e organi di controllo dalla visura. */
export function PeopleTable({ people }: { people: DossierPerson[] }) {
  if (people.length === 0) {
    return (
      <p className="text-sm text-slate-400">
        Nessuna persona presente nei dati importati.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-slate-100">
      {people.map((person, index) => (
        <li key={`${person.codice_fiscale ?? person.nome}-${index}`} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-slate-900">
              {displayName(person)}
              {person.is_legale_rappresentante && (
                <span className="ml-2 inline-flex items-center gap-1 align-middle text-xs font-medium text-brand-600">
                  <BadgeCheck className="size-3.5" aria-hidden />
                  Legale rappresentante
                </span>
              )}
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {person.ruoli.length > 0
                ? person.ruoli.map((r) => r.description).filter(Boolean).join(", ")
                : KIND_LABELS[person.kind]}
              {person.quota_percentuale !== null && ` · quota ${person.quota_percentuale}%`}
              {person.data_inizio_carica &&
                ` · dal ${formatDateNumeric(person.data_inizio_carica)}`}
            </p>
            {(person.data_nascita || person.luogo_nascita) && (
              <p className="text-xs text-slate-400">
                {[
                  person.luogo_nascita,
                  person.data_nascita ? formatDateNumeric(person.data_nascita) : null,
                ]
                  .filter(Boolean)
                  .join(", ")}
              </p>
            )}
          </div>
          <Badge tone="slate">{KIND_LABELS[person.kind]}</Badge>
        </li>
      ))}
    </ul>
  );
}
