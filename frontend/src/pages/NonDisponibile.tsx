import { LinkButton } from "../components/ui/Button";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";

/** L'unica pagina di accesso negato dentro la cornice: per le aree admin e
 *  progettista, per i partenariati a modulo spento e per gli URL sconosciuti
 *  sotto /app. Non dice il perché: non rivela che cosa esiste. */
export default function NonDisponibile() {
  return (
    <Page variante="sezioni">
      <PageHeader
        titolo="Questa pagina non è disponibile"
        descrizione="L'indirizzo non corrisponde a nessuna pagina del tuo account."
      />
      <div>
        <LinkButton to="/app" variant="secondary">
          Torna alla Home
        </LinkButton>
      </div>
    </Page>
  );
}
