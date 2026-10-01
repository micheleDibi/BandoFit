import { AiCheckDisponibili } from "../components/home/AiCheckDisponibili";
import { BandiAdatti } from "../components/home/BandiAdatti";
import { CompletaProfilo } from "../components/home/CompletaProfilo";
import { DaFare } from "../components/home/DaFare";
import { ProssimeScadenze } from "../components/home/ProssimeScadenze";
import { RichiesteAppuntamenti } from "../components/home/RichiesteAppuntamenti";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { useCompany } from "../hooks/useCompany";
import { useMe } from "../hooks/useMe";
import { hasAreaProgettista } from "../lib/roles";

/** Home (tavola `Home`): che cosa c'è da fare oggi per l'azienda. Ogni
 *  blocco ha i suoi hook e i suoi stati di caricamento ed errore: un blocco
 *  che non risponde non blocca gli altri. Per progettisti e admin, in cima,
 *  le richieste di consulenza e gli appuntamenti. */
export default function Home() {
  const { data: me } = useMe();
  const { data: azienda } = useCompany();
  const progettista = hasAreaProgettista(me?.profile.role);
  const nome = azienda?.company?.ragione_sociale;

  return (
    <Page
      variante="dettaglio"
      intestazione={
        <PageHeader
          titolo="Home"
          descrizione={`Cosa c'è da fare oggi per ${nome ?? "la tua azienda"}`}
        />
      }
      laterale={
        <>
          <DaFare />
          <CompletaProfilo />
          <AiCheckDisponibili />
        </>
      }
    >
      {progettista && <RichiesteAppuntamenti />}
      <ProssimeScadenze />
      <BandiAdatti />
    </Page>
  );
}
