import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";

/** Home: segnaposto dell'ondata 1. I blocchi (Completa il profilo, Da fare,
 *  Prossime scadenze, Nuovi bandi adatti, AI-check disponibili) arrivano con
 *  l'ondata 2. */
export default function Home() {
  return (
    <Page variante="elenco">
      <PageHeader titolo="Home" />
      <p className="text-body text-ink-2">In arrivo con la prossima ondata</p>
    </Page>
  );
}
