import { AiCheckDisponibili } from "../components/home/AiCheckDisponibili";
import { BandiAdatti } from "../components/home/BandiAdatti";
import { CompletaProfilo } from "../components/home/CompletaProfilo";
import { DaFare } from "../components/home/DaFare";
import { IndicatoriHome } from "../components/home/IndicatoriHome";
import { ProssimeScadenze } from "../components/home/ProssimeScadenze";
import { RichiesteAppuntamenti } from "../components/home/RichiesteAppuntamenti";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { useCompany } from "../hooks/useCompany";
import { useMe } from "../hooks/useMe";
import { hasAreaProgettista } from "../lib/roles";

const giornoDiOggi = new Intl.DateTimeFormat("it-IT", {
  weekday: "long",
  day: "numeric",
  month: "long",
  timeZone: "Europe/Rome",
});

/** Home (tavola `Home`): che cosa c'è da fare oggi per l'azienda. In cima la
 *  fascia navy con il saluto e, sotto, la riga degli indicatori (dai dati che
 *  i blocchi caricano già). Ogni blocco ha i suoi hook e i suoi stati di
 *  caricamento ed errore: un blocco che non risponde non blocca gli altri. Per
 *  progettisti e admin, in cima, le richieste di consulenza e gli
 *  appuntamenti. */
export default function Home() {
  const { data: me } = useMe();
  const { data: azienda } = useCompany();
  const progettista = hasAreaProgettista(me?.profile.role);
  const nome = azienda?.company?.ragione_sociale;
  const nomeUtente = me?.profile.nome?.trim();
  const oggi = giornoDiOggi.format(new Date());

  return (
    <Page
      variante="dettaglio"
      intestazione={
        <>
          <PageHeader
            area="home"
            sopra={
              <span>{nomeUtente ? `Ciao ${nomeUtente}, oggi è ${oggi}` : `Oggi è ${oggi}`}</span>
            }
            titolo="Home"
            descrizione={`Cosa c'è da fare oggi per ${nome ?? "la tua azienda"}`}
          />
          <IndicatoriHome />
        </>
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
