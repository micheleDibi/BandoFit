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

/** Cariche, soci e organi di controllo dalla visura: righe con filetto. */
export function PeopleTable({ people }: { people: DossierPerson[] }) {
  if (people.length === 0) {
    return <p className="text-body text-ink-2">Nessuna persona presente nei dati importati.</p>;
  }
  return (
    <ul className="flex flex-col">
      {people.map((person, index) => (
        <li
          key={`${person.codice_fiscale ?? person.nome}-${index}`}
          className="flex flex-wrap items-center gap-3 border-b border-line py-3 last:border-b-0"
        >
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <p className="text-body font-medium text-ink">
              {displayName(person)}
              {person.is_legale_rappresentante && (
                <span className="ml-2 inline-flex items-center gap-1 align-middle text-small font-medium text-ink-2">
                  <BadgeCheck className="size-4" aria-hidden />
                  Legale rappresentante
                </span>
              )}
            </p>
            <p className="text-small text-ink-2">
              {person.ruoli.length > 0
                ? person.ruoli.map((r) => r.description).filter(Boolean).join(", ")
                : KIND_LABELS[person.kind]}
              {person.quota_percentuale !== null && `, quota ${person.quota_percentuale}%`}
              {person.data_inizio_carica &&
                `, dal ${formatDateNumeric(person.data_inizio_carica)}`}
            </p>
            {(person.data_nascita || person.luogo_nascita) && (
              <p className="text-small text-ink-3">
                {[
                  person.luogo_nascita,
                  person.data_nascita ? formatDateNumeric(person.data_nascita) : null,
                ]
                  .filter(Boolean)
                  .join(", ")}
              </p>
            )}
          </div>
          <Badge>{KIND_LABELS[person.kind]}</Badge>
        </li>
      ))}
    </ul>
  );
}
