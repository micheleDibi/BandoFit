import { CallAdminTab } from "../components/partenariati/admin/CallAdminTab";
import { EstrazioniTab } from "../components/partenariati/admin/EstrazioniTab";
import { IdentitaTab } from "../components/partenariati/admin/IdentitaTab";
import { CostiTab, MetricheTab } from "../components/partenariati/admin/MetricheTab";
import { SegnalazioniTab } from "../components/partenariati/admin/SegnalazioniTab";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useTab } from "../hooks/useTab";
import { ADMIN_PARTENARIATI_COPY } from "../lib/copy";

type Tab = keyof typeof ADMIN_PARTENARIATI_COPY.schede;

const TABS = Object.keys(ADMIN_PARTENARIATI_COPY.schede) as Tab[];
const PREFISSO = "admin-partenariati";

/** Pannello admin del modulo partenariati (WP9,
 *  `/app/admin/partenariati?tab=segnalazioni|identita|call|metriche|costi|estrazioni`):
 *  moderazione DSA, verifiche dell'identità, call, metriche, costi per valuta
 *  ed estrazioni delle regole. La scheda è nell'URL (`?tab=`, default
 *  «segnalazioni»): link condivisibili, «indietro» del browser. */
export default function AdminPartenariati() {
  const { tab, setTab } = useTab<Tab>(TABS, { default: "segnalazioni" });

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Partenariati"
        descrizione="Segnalazioni e ricorsi, verifiche dell'identità delle aziende, call, metriche, costi ed estrazioni delle regole dei bandi."
        area="admin"
      />
      <Tabs
        tabs={TABS.map((id) => ({ id, label: ADMIN_PARTENARIATI_COPY.schede[id] }))}
        attivo={tab}
        onChange={setTab}
        ariaLabel="Sezioni del pannello partenariati"
        prefisso={PREFISSO}
      />
      <TabPanel id="segnalazioni" attivo={tab} prefisso={PREFISSO}>
        <SegnalazioniTab />
      </TabPanel>
      <TabPanel id="identita" attivo={tab} prefisso={PREFISSO}>
        <IdentitaTab />
      </TabPanel>
      <TabPanel id="call" attivo={tab} prefisso={PREFISSO}>
        <CallAdminTab />
      </TabPanel>
      <TabPanel id="metriche" attivo={tab} prefisso={PREFISSO}>
        <MetricheTab />
      </TabPanel>
      <TabPanel id="costi" attivo={tab} prefisso={PREFISSO}>
        <CostiTab />
      </TabPanel>
      <TabPanel id="estrazioni" attivo={tab} prefisso={PREFISSO}>
        <EstrazioniTab />
      </TabPanel>
    </Page>
  );
}
