import { ChartColumn, FileText, Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { BilanciSection } from "../components/company/bilanci/BilanciSection";
import { CompanyCard, type FormState as BozzaDatiAzienda } from "../components/company/CompanyCard";
import { DossierView } from "../components/company/dossier/DossierView";
import { ImportCompanyDialog } from "../components/company/ImportCompanyDialog";
import { PartnerSection } from "../components/partenariati/PartnerSection";
import { ExportPdfButton } from "../components/shared/ExportPdfButton";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card } from "../components/ui/Card";
import { IconChip } from "../components/ui/IconChip";
import { FOCUS_SU_FASCIA, GHOST_SU_FASCIA } from "../components/shared/fascia";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { EmptyState, ErrorState, Skeleton } from "../components/ui/states";
import { Status } from "../components/ui/Status";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useActiveCompany } from "../hooks/useActiveCompany";
import { useAziendaDaLink } from "../hooks/useAziendaDaLink";
import { useCompany } from "../hooks/useCompany";
import { useCompanyDossier } from "../hooks/useCompanyDossier";
import { useFunzioni } from "../hooks/useFunzioni";
import { useTab } from "../hooks/useTab";
import { formatDateNumeric } from "../lib/format";

const IDS = ["dati", "dossier", "bilanci", "partner"] as const;
type SchedaId = (typeof IDS)[number];
const PREFISSO = "azienda";

/** I vecchi deep link con l'ancora aprono la scheda giusta: `#bilanci` apre
 *  Bilanci; `#partner` e `#identita` aprono Profilo partner (gli id restano nel
 *  DOM e lo scroll lo fanno le sezioni). */
function schedaDaHash(hash: string): SchedaId | undefined {
  if (hash === "#bilanci") return "bilanci";
  if (hash === "#partner" || hash === "#identita") return "partner";
  return undefined;
}

/** Stessa normalizzazione dello slug backend (`_slug`): NFKD + rimozione dei
 *  diacritici, così il nome del file coincide con il Content-Disposition. */
function slugFile(nome: string): string {
  return (
    nome
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "") || "azienda"
  );
}

/** «Dati azienda»: una pagina a schede — Dati aziendali, Dossier, Bilanci,
 *  Profilo partner (a modulo acceso). La scheda è DERIVATA da `?tab=` o
 *  dall'hash dei vecchi link, senza un secondo `navigate` (nessun conflitto
 *  con `useAziendaDaLink`, che toglie `?azienda=` e lascia l'hash). */
export default function Azienda() {
  const { data, isPending, isError, refetch } = useCompanyDossier();
  const { data: companyData } = useCompany();
  const { activeCompanyId } = useActiveCompany();
  const { partenariatiAttivo } = useFunzioni();
  const location = useLocation();
  const { tab: tabUrl, setTab } = useTab(IDS, { default: schedaDaHash(location.hash) ?? "dati" });
  // A modulo spento la scheda partner non esiste: si cade su Dati aziendali.
  const tab: SchedaId = tabUrl === "partner" && !partenariatiAttivo ? "dati" : tabUrl;
  const [importOpen, setImportOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  // Bozza del modulo «Dati aziendali» in modifica: la scheda si smonta
  // cambiando scheda, le modifiche non salvate restano qui.
  const [bozza, setBozza] = useState<BozzaDatiAzienda | null>(null);
  // Link delle notifiche (`?azienda=<id>#bilanci`): per un Advisor rende
  // attiva l'azienda della notifica prima di mostrare i dati.
  const { avviso } = useAziendaDaLink();
  const imported = data?.imported ?? false;

  // Un Advisor che cambia azienda mentre modifica esce dalla modifica: il
  // modulo era dell'altra azienda (e il PUT andrebbe su quella appena
  // attivata). Solo ai cambi, non al primo montaggio: lì è `CompanyCard` a
  // decidere (alla prima compilazione si parte già in modifica).
  const aziendaPrecedente = useRef(activeCompanyId);
  useEffect(() => {
    if (aziendaPrecedente.current === activeCompanyId) return;
    aziendaPrecedente.current = activeCompanyId;
    setEditing(false);
    setBozza(null);
  }, [activeCompanyId]);

  // Un import (dall'intestazione o dalla scheda Dossier) riscrive i dati
  // aziendali: la modifica in corso si chiude, così una bozza vecchia non li
  // sovrascrive al salvataggio. L'import si riconosce dal nuovo `fetched_at`
  // del dossier; il primo valore letto non conta.
  const importVisto = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (data === undefined) return;
    const corrente = data.fetched_at ?? null;
    if (importVisto.current === undefined || importVisto.current === corrente) {
      importVisto.current = corrente;
      return;
    }
    importVisto.current = corrente;
    setEditing(false);
    setBozza(null);
  }, [data]);

  // `#bilanci` (link «Vedi tutti i bilanci» e notifiche del bilancio
  // ufficiale): la sezione esiste solo con la scheda montata e il dossier
  // caricato, quindi lo scroll si fa quando c'è.
  useEffect(() => {
    if (location.hash !== "#bilanci" || !imported || tab !== "bilanci") return;
    document.getElementById("bilanci")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [location.hash, location.key, imported, tab]);

  const schede = [
    { id: "dati" as const, label: "Dati aziendali" },
    { id: "dossier" as const, label: "Dossier" },
    { id: "bilanci" as const, label: "Bilanci" },
    ...(partenariatiAttivo ? [{ id: "partner" as const, label: "Profilo partner" }] : []),
  ];

  if (isPending) {
    return (
      <Page variante="sezioni">
        <PageHeader area="azienda" titolo="Dati azienda" />
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Page>
    );
  }
  if (isError || !data) {
    return (
      <Page variante="sezioni">
        <PageHeader area="azienda" titolo="Dati azienda" />
        <ErrorState
          title="Non siamo riusciti a caricare il dossier aziendale."
          onRetry={() => refetch()}
        />
      </Page>
    );
  }

  const azienda = companyData?.company ?? null;
  // La partita IVA appena scritta nel modulo vale per l'import (come in HEAD),
  // altrimenti quella salvata.
  const defaultPiva = bozza?.partita_iva || azienda?.partita_iva || null;
  const nome =
    azienda?.ragione_sociale ?? (data.imported ? data.dossier?.anagrafica.denominazione : null);
  const slug = slugFile(nome ?? "azienda");
  const puoModificare = companyData?.editable ?? false;

  const importa = (
    <Button type="button" variant="secondary" onClick={() => setImportOpen(true)}>
      Importa da partita IVA
    </Button>
  );

  return (
    <Page variante="sezioni">
      <PageHeader
        area="azienda"
        titolo="Dati azienda"
        descrizione={
          nome
            ? `I dati di ${nome}, alla base della compatibilità e dell'AI-check.`
            : "I dati della tua azienda, alla base della compatibilità e dell'AI-check."
        }
        azioni={
          // Senza dati la scheda parte già in modifica: l'import resta a portata.
          tab === "dati" && puoModificare && (!editing || !azienda) ? (
            <>
              <Button
                type="button"
                variant="ghost"
                className={GHOST_SU_FASCIA}
                onClick={() => setImportOpen(true)}
              >
                Importa da partita IVA
              </Button>
              {!editing && (
                <Button
                  type="button"
                  variant="secondary"
                  className={FOCUS_SU_FASCIA}
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="size-4" aria-hidden />
                  Modifica
                </Button>
              )}
            </>
          ) : undefined
        }
      />

      {avviso && <Alert tono="attenzione">{avviso}</Alert>}

      <Tabs
        tabs={schede}
        attivo={tab}
        onChange={setTab}
        ariaLabel="Sezioni dei dati azienda"
        prefisso={PREFISSO}
      />

      <TabPanel id="dati" attivo={tab} prefisso={PREFISSO}>
        {puoModificare && (
          <p className="text-body text-ink-2">
            Li compili tu e li vedono anche gli account collegati.
          </p>
        )}
        <Card area="azienda" className="sm:p-6">
          <CompanyCard
            editing={editing}
            onEditingChange={setEditing}
            bozza={bozza}
            onBozzaChange={setBozza}
          />
        </Card>
      </TabPanel>

      <TabPanel id="dossier" attivo={tab} prefisso={PREFISSO}>
        <Card className="sm:p-6">
          <Section aria-label="Dossier certificato">
            <SectionHeader
              titolo={
                <span className="flex items-center gap-3">
                  <IconChip icon={FileText} area="azienda" size="sm" />
                  Dossier certificato
                </span>
              }
              azione={
                data.imported && data.dossier ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <ExportPdfButton
                      url="/me/company/dossier/pdf"
                      filename={`dossier-${slug}.pdf`}
                      label="Esporta il dossier in PDF"
                      size="sm"
                    />
                    {data.editable && (
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => setImportOpen(true)}
                      >
                        Aggiorna
                      </Button>
                    )}
                  </div>
                ) : undefined
              }
            />
            {data.imported && data.dossier ? (
              <>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  {data.dossier.anagrafica.stato && (
                    <Status tono={data.dossier.anagrafica.stato === "Attiva" ? "aperto" : "neutro"}>
                      {data.dossier.anagrafica.stato}
                    </Status>
                  )}
                  {data.dossier.flags?.startup_innovativa && (
                    <Badge area="azienda">Startup innovativa</Badge>
                  )}
                  {data.sandbox && <Badge tone="warning">Dati di test</Badge>}
                  <span className="text-small text-ink-3">
                    Registro Imprese, aggiornato il {formatDateNumeric(data.fetched_at)}
                  </span>
                </div>
                <DossierView dossier={data.dossier} people={data.people} linkBilanci />
              </>
            ) : (
              <EmptyState
                icon={FileText}
                area="azienda"
                title="Nessun dato importato."
                description={
                  data.editable
                    ? "Importa la visura completa della tua azienda dal Registro Imprese: anagrafica, ATECO, sedi, cariche e molto altro."
                    : "Il titolare non ha ancora importato i dati aziendali."
                }
                action={data.editable ? importa : undefined}
              />
            )}
          </Section>
        </Card>
      </TabPanel>

      <TabPanel id="bilanci" attivo={tab} prefisso={PREFISSO}>
        {/* I bilanci arrivano con l'import. La key azzera l'esito di un
            recupero quando un Advisor cambia azienda. */}
        {data.imported ? (
          <BilanciSection key={activeCompanyId ?? "azienda"} />
        ) : (
          <EmptyState
            icon={ChartColumn}
            area="azienda"
            title="Prima importa il dossier."
            description="I bilanci arrivano con il dossier del Registro Imprese: dopo l'import li trovi qui, esercizio per esercizio."
            action={data.editable ? importa : undefined}
          />
        )}
      </TabPanel>

      {partenariatiAttivo && (
        <TabPanel id="partner" attivo={tab} prefisso={PREFISSO}>
          {/* Profilo partner (C2.3): la sezione gestisce da sola stato,
              consenso e gli id `partner`/`identita`. */}
          <PartnerSection
            key={`partner-${activeCompanyId ?? "azienda"}`}
            onImporta={data.editable ? () => setImportOpen(true) : undefined}
          />
        </TabPanel>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line pt-4">
        <p className="max-w-lettura text-small text-ink-3">
          Dati provenienti da fonti pubbliche (Registro Imprese) tramite openapi.it, per uso
          esclusivo del titolare e degli account collegati.
        </p>
        {azienda && (
          <ExportPdfButton
            url="/me/company/export/pdf"
            filename={`scheda-${slug}.pdf`}
            label="Esporta la scheda in PDF"
            size="sm"
          />
        )}
      </div>

      <ImportCompanyDialog
        open={importOpen}
        onClose={() => setImportOpen(false)}
        defaultPiva={defaultPiva}
      />
    </Page>
  );
}
