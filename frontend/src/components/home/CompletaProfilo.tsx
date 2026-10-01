import { Building2, Check, Circle } from "lucide-react";
import { useAlertSettings } from "../../hooks/useAlertSettings";
import { useCompany } from "../../hooks/useCompany";
import { useCompanyDossier } from "../../hooks/useCompanyDossier";
import { useFunzioni } from "../../hooks/useFunzioni";
import { usePartnerProfile } from "../../hooks/usePartnerProfile";
import { usePreferences } from "../../hooks/usePreferences";
import { apiErrorCode } from "../../lib/api";
import { buildBandiPerTePreset, presetHasValues } from "../../lib/bandiPreset";
import { formatDate } from "../../lib/format";
import { Button } from "../ui/Button";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { ProgressRing } from "../ui/ProgressRing";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";

interface Passo {
  nome: string;
  fatto: boolean;
  dettaglio: string;
  /** Azione a destra. */
  azione?: { label: string; to: string };
  /** Azione sui dati dell'azienda (dossier, dati aziendali, profilo partner):
   *  il membro non la vede. Preferenze e avvisi sono personali e restano. */
  soloTitolare?: boolean;
}

/** «Completa il profilo dell'azienda»: i passi che rendono utili compatibilità
 *  e bandi adatti, con quanti sono fatti. Legge solo dati già in cache o
 *  endpoint di sola lettura (il dossier non avvia nessun recupero). Il membro
 *  vede tutti i passi, ma non le azioni sui dati dell'azienda. */
export function CompletaProfilo() {
  const { partenariatiAttivo } = useFunzioni();
  const dossier = useCompanyDossier();
  const azienda = useCompany();
  const preferenze = usePreferences();
  const avvisi = useAlertSettings();
  const partner = usePartnerProfile(partenariatiAttivo);
  // Chi non può modificare i dati dell'azienda (il membro attivo) non vede le
  // azioni su di essi. Un collegato in attesa o retrocesso è titolare della sua.
  const membro = azienda.data?.editable === false;
  // Senza azienda (nasce al primo salvataggio o import) il profilo partner
  // risponde 404 `not_found`: non è un errore, il passo non c'è.
  const partnerAssente =
    partenariatiAttivo && partner.isError && apiErrorCode(partner.error) === "not_found";

  const inCaricamento =
    dossier.isPending ||
    azienda.isPending ||
    preferenze.isPending ||
    avvisi.isPending ||
    (partenariatiAttivo && partner.isPending);
  const errore =
    dossier.isError ||
    azienda.isError ||
    preferenze.isError ||
    avvisi.isError ||
    (partenariatiAttivo && partner.isError && !partnerAssente);

  if (inCaricamento) {
    return (
      <Panel titolo="Completa il profilo dell'azienda" icon={Building2} area="azienda">
        <div className="flex flex-col gap-2" aria-hidden>
          <div className="flex items-center gap-4">
            <Skeleton className="size-16 shrink-0 rounded-pill" />
            <Skeleton className="h-4 w-1/3" />
          </div>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </Panel>
    );
  }
  if (errore) {
    return (
      <Panel titolo="Completa il profilo dell'azienda" icon={Building2} area="azienda">
        <InlineError>Non siamo riusciti a leggere il profilo dell'azienda.</InlineError>
        <div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => {
              if (dossier.isError) void dossier.refetch();
              if (azienda.isError) void azienda.refetch();
              if (preferenze.isError) void preferenze.refetch();
              if (avvisi.isError) void avvisi.refetch();
              if (partner.isError && !partnerAssente) void partner.refetch();
            }}
          >
            Riprova
          </Button>
        </div>
      </Panel>
    );
  }

  const importato = dossier.data?.imported ?? false;
  const datiCompilati = !!azienda.data?.company?.ragione_sociale && !!azienda.data?.company?.partita_iva;
  const interessi = presetHasValues(buildBandiPerTePreset(undefined, preferenze.data));
  const avvisiAttivi = avvisi.data?.abilitati ?? false;
  // «Compilato» = il profilo esiste ed è completo (non: visibile come partner).
  const partnerEsiste = partner.data?.esiste ?? false;
  const partnerCompletezza = partner.data?.completezza ?? 0;
  const partnerCompilato = partnerEsiste && partnerCompletezza >= 100;

  const passi: Passo[] = [
    {
      nome: "Dossier del Registro Imprese",
      fatto: importato,
      dettaglio:
        importato && dossier.data?.fetched_at
          ? `Aggiornato il ${formatDate(dossier.data.fetched_at)}`
          : importato
            ? "Importato"
            : "Da importare",
      azione: { label: importato ? "Aggiorna" : "Importa", to: "/app/azienda?tab=dossier" },
      soloTitolare: true,
    },
    {
      nome: "Dati aziendali",
      fatto: datiCompilati,
      dettaglio: datiCompilati ? "Compilati" : "Da compilare",
      azione: datiCompilati ? undefined : { label: "Compila", to: "/app/azienda" },
      soloTitolare: true,
    },
    {
      nome: "Interessi sui bandi",
      fatto: interessi,
      dettaglio: interessi ? "Impostati" : "Da scegliere",
      azione: interessi ? undefined : { label: "Scegli", to: "/app/preferenze" },
    },
  ];
  // Avvisi email solo se il piano li include: altrimenti non è un passo.
  if (avvisi.data?.piano_include_alert !== false) {
    passi.push({
      nome: "Avvisi email",
      fatto: avvisiAttivi,
      dettaglio: avvisiAttivi ? "Attivi" : "Da attivare",
      azione: avvisiAttivi ? undefined : { label: "Attiva", to: "/app/preferenze?tab=avvisi" },
    });
  }
  if (partenariatiAttivo && !partnerAssente) {
    passi.push({
      nome: "Profilo partner",
      fatto: partnerCompilato,
      dettaglio: !partnerEsiste
        ? "Da compilare"
        : partnerCompilato
          ? "Compilato"
          : `Compilato al ${partnerCompletezza}%`,
      azione: partnerCompilato
        ? undefined
        : { label: partnerEsiste ? "Completa" : "Compila", to: "/app/azienda#partner" },
      soloTitolare: true,
    });
  }

  const fatti = passi.filter((p) => p.fatto).length;

  if (fatti === passi.length) {
    return (
      <Panel titolo="Profilo dell'azienda" icon={Building2} area="azienda">
        <p className="text-body text-ink-2">
          Profilo completo: compatibilità e bandi adatti usano tutti i dati della tua azienda.
        </p>
      </Panel>
    );
  }

  return (
    <Panel titolo="Completa il profilo dell'azienda" icon={Building2} area="azienda">
      <div className="flex items-center gap-4">
        <ProgressRing
          value={fatti}
          max={passi.length}
          size={64}
          tono="azienda"
          label={`Passi completati: ${fatti} su ${passi.length}`}
        >
          {fatti}/{passi.length}
        </ProgressRing>
        <p className="text-small text-ink-2" aria-hidden>
          {fatti} {fatti === 1 ? "passo" : "passi"} su {passi.length}
        </p>
      </div>
      <ul className="flex flex-col">
        {passi.map((passo) => (
          <li
            key={passo.nome}
            className="flex items-start gap-2.5 border-b border-line py-2.5 last:border-b-0"
          >
            {passo.fatto ? (
              <Check className="mt-0.5 size-4 shrink-0 text-fit-ink" aria-hidden />
            ) : (
              <Circle className="mt-0.5 size-4 shrink-0 text-ink-off" aria-hidden />
            )}
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-body font-semibold text-ink">
                {passo.nome}
                <span className="sr-only">: {passo.fatto ? "fatto" : "da fare"}</span>
              </span>
              <span className="text-small text-ink-2">{passo.dettaglio}</span>
            </div>
            {passo.azione && !(membro && passo.soloTitolare) && (
              <TextLink to={passo.azione.to} className="shrink-0 text-small font-medium">
                {passo.azione.label}
              </TextLink>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
