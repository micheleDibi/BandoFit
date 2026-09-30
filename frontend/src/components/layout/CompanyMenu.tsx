import { Building2, Check, ChevronsUpDown, Settings2 } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { Link } from "react-router-dom";
import { useActiveCompany } from "../../hooks/useActiveCompany";
import { useCompany } from "../../hooks/useCompany";
import { useMe } from "../../hooks/useMe";
import { cn } from "../../lib/cn";
import type { CompanySummary } from "../../types";
import { Popover, usePopover } from "../ui/Popover";
import { spostaFocusVoce, voceMenu } from "./tastieraMenu";

const scatola =
  "flex h-10 w-full items-center gap-2.5 rounded-control border border-line-control bg-sheet px-3 text-left text-body";

/** Pulsante del selettore: `forwardRef` e props passate al `<button>`, è il
 *  trigger di `Popover`. */
const TriggerAzienda = forwardRef<
  HTMLButtonElement,
  ButtonHTMLAttributes<HTMLButtonElement> & { label: string }
>(({ label, className, ...props }, ref) => (
  <button
    ref={ref}
    type="button"
    aria-label={`Azienda attiva: ${label}. Cambia azienda`}
    className={cn(
      scatola,
      "cursor-pointer transition-colors duration-150 hover:bg-desk aria-expanded:bg-desk",
      className,
    )}
    {...props}
  >
    <Building2 className="size-4 shrink-0 text-ink-3" strokeWidth={1.75} aria-hidden />
    <span className="min-w-0 flex-1 truncate font-medium text-ink">{label}</span>
    <ChevronsUpDown className="size-4 shrink-0 text-ink-3" aria-hidden />
  </button>
));
TriggerAzienda.displayName = "TriggerAzienda";

function VociAziende({
  companies,
  activeCompanyId,
  setActiveCompany,
  conGestione,
  onNavigate,
}: {
  companies: CompanySummary[];
  activeCompanyId: string | null;
  setActiveCompany: (id: string) => void;
  conGestione: boolean;
  onNavigate?: () => void;
}) {
  const { chiudi } = usePopover();
  return (
    <div
      role="menu"
      aria-label="Le tue aziende"
      onKeyDown={spostaFocusVoce}
      className="flex flex-col gap-0.5"
    >
      {companies.length === 0 ? (
        <p className="px-2.5 py-2 text-body text-ink-3">Nessuna azienda: creane una.</p>
      ) : (
        companies.map((c) => {
          const isActive = c.id === activeCompanyId;
          return (
            <button
              key={c.id}
              type="button"
              role="menuitemradio"
              aria-checked={isActive}
              onClick={() => {
                setActiveCompany(c.id);
                chiudi();
              }}
              className={cn(
                voceMenu,
                // Due righe (ragione sociale e P.IVA): l'altezza segue il contenuto.
                "h-auto w-full cursor-pointer py-1.5 text-left",
                isActive ? "bg-desk text-ink" : "text-ink hover:bg-desk",
              )}
            >
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">{c.ragione_sociale}</span>
                <span className="truncate text-small font-normal text-ink-3 tabular-nums">
                  P.IVA {c.partita_iva}
                </span>
              </span>
              {isActive && <Check className="size-4 shrink-0 text-accent" aria-hidden />}
            </button>
          );
        })
      )}
      {conGestione && (
        <>
          <div role="separator" className="my-1 border-t border-line" />
          <Link
            to="/app/aziende"
            role="menuitem"
            onClick={() => {
              chiudi();
              onNavigate?.();
            }}
            className={cn(voceMenu, "text-ink hover:bg-desk")}
          >
            <Settings2 className="size-4 text-ink-3" aria-hidden />
            Aziende gestite
          </Link>
        </>
      )}
    </div>
  );
}

/** Selettore dell'azienda in cima alla barra laterale.
 *  - non‑Advisor (una sola azienda): il nome dell'azienda, non un menu.
 *  - Advisor (multi-azienda): menu su `Popover` con lo switch dell'azienda
 *    attiva e il collegamento ad «Aziende gestite»; Esc e clic fuori chiudono,
 *    il focus torna al pulsante, frecce/Home/End scorrono le voci. */
export function CompanyMenu({ onNavigate }: { onNavigate?: () => void }) {
  const { isMulti, companies, activeCompanyId, setActiveCompany } = useActiveCompany();
  const { data: me } = useMe();
  const { data: azienda } = useCompany();
  // Un membro attivo naviga le aziende VISIBILI ma non gestisce il
  // portafoglio (endpoint owner-only): niente «Aziende gestite».
  const isActiveChild = me?.family?.role === "child" && me.family.status === "active";

  if (!isMulti) {
    const nome = azienda?.company?.ragione_sociale ?? me?.profile.azienda ?? "La tua azienda";
    return (
      <div className={scatola}>
        <Building2 className="size-4 shrink-0 text-ink-3" strokeWidth={1.75} aria-hidden />
        <span className="min-w-0 flex-1 truncate font-medium text-ink">{nome}</span>
      </div>
    );
  }

  const attiva = companies.find((c) => c.id === activeCompanyId);
  const label = attiva?.ragione_sociale ?? "Azienda";

  return (
    <Popover trigger={<TriggerAzienda label={label} />} label="Le tue aziende">
      <VociAziende
        companies={companies}
        activeCompanyId={activeCompanyId}
        setActiveCompany={setActiveCompany}
        conGestione={!isActiveChild}
        onNavigate={onNavigate}
      />
    </Popover>
  );
}
