import { Handshake } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { CallAdminTab } from "../components/partenariati/admin/CallAdminTab";
import { EstrazioniTab } from "../components/partenariati/admin/EstrazioniTab";
import { IdentitaTab } from "../components/partenariati/admin/IdentitaTab";
import { CostiTab, MetricheTab } from "../components/partenariati/admin/MetricheTab";
import { SegnalazioniTab } from "../components/partenariati/admin/SegnalazioniTab";
import { Schede } from "../components/partenariati/Schede";
import { ADMIN_PARTENARIATI_COPY } from "../lib/copy";

type Tab = keyof typeof ADMIN_PARTENARIATI_COPY.schede;

const TABS = Object.keys(ADMIN_PARTENARIATI_COPY.schede) as Tab[];

/** Pannello admin del modulo partenariati (WP9,
 *  `/app/admin/partenariati?tab=segnalazioni|identita|call|metriche|costi|estrazioni`):
 *  moderazione DSA, verifiche dell'identità, call, metriche, costi per valuta
 *  ed estrazioni delle regole. La scheda è nei searchParams (link
 *  condivisibili, back del browser). */
export default function AdminPartenariati() {
  const [params, setParams] = useSearchParams();
  const richiesta = params.get("tab");
  const tab: Tab = TABS.includes(richiesta as Tab) ? (richiesta as Tab) : "segnalazioni";
  const cambia = (t: Tab) =>
    setParams(
      (p) => {
        const nuovi = new URLSearchParams(p);
        nuovi.set("tab", t);
        return nuovi;
      },
      { replace: true },
    );

  return (
    <div className="space-y-5">
      <div>
        <h1 className="inline-flex items-center gap-2 font-display text-2xl font-bold tracking-tight text-slate-900">
          <Handshake className="size-6 text-brand-500" aria-hidden />
          Partenariati
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Segnalazioni e ricorsi, verifiche dell'identità delle aziende, call, metriche, costi ed
          estrazioni delle regole dei bandi.
        </p>
      </div>
      <Schede
        etichetta="Sezioni del pannello partenariati"
        schede={TABS.map((id) => ({ id, etichetta: ADMIN_PARTENARIATI_COPY.schede[id] }))}
        attiva={tab}
        onCambia={cambia}
      >
        {tab === "segnalazioni" && <SegnalazioniTab />}
        {tab === "identita" && <IdentitaTab />}
        {tab === "call" && <CallAdminTab />}
        {tab === "metriche" && <MetricheTab />}
        {tab === "costi" && <CostiTab />}
        {tab === "estrazioni" && <EstrazioniTab />}
      </Schede>
    </div>
  );
}
