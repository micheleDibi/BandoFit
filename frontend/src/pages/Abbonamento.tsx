import { X } from "lucide-react";
import { SchedaAcquisti } from "../components/abbonamento/SchedaAcquisti";
import { SchedaAddon } from "../components/abbonamento/SchedaAddon";
import { SchedaPagamento } from "../components/abbonamento/SchedaPagamento";
import { aPagamento, SchedaPiano } from "../components/abbonamento/SchedaPiano";
import { Alert } from "../components/ui/Alert";
import { LinkButton } from "../components/ui/Button";
import { IconButton } from "../components/ui/IconButton";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { ErrorState, Skeleton } from "../components/ui/states";
import { TabPanel, Tabs } from "../components/ui/Tabs";
import { useAuth } from "../hooks/useAuth";
import { useMe } from "../hooks/useMe";
import { usePlans } from "../hooks/usePlans";
import { useSessionDismissible } from "../hooks/useSessionDismissible";
import { useTab } from "../hooks/useTab";
import { apiErrorMessage } from "../lib/api";

const SCHEDE = [
  { id: "piano", label: "Piano" },
  { id: "addon", label: "Add-on" },
  { id: "pagamento", label: "Pagamento e fatturazione" },
  { id: "acquisti", label: "Acquisti" },
] as const;
const IDS = SCHEDE.map((s) => s.id) as readonly (typeof SCHEDE)[number]["id"][];
const PREFISSO = "abbonamento";

/** Abbonamento: una pagina a schede (`?tab=`): Piano, Add-on, Pagamento e
 *  fatturazione, Acquisti. Checkout ed Esito restano pagine a sé. */
export default function Abbonamento() {
  const { tab, setTab } = useTab(IDS);
  const { data: me, isPending, isError, error, refetch } = useMe();
  const { data: plans } = usePlans();
  // Intento d'acquisto dalla registrazione: il ?piano= scelto sulla landing
  // viaggia come plan_slug nello user_metadata di Supabase (lo scrive
  // auth_service alla creazione dell'utente) — da qui lo si rilegge senza
  // toccare il backend.
  const { session } = useAuth();
  const intento = useSessionDismissible("intento-piano");

  if (isPending) {
    return (
      <Page variante="sezioni">
        <PageHeader area="account" titolo="Abbonamento" />
        <div className="flex flex-col gap-4" aria-hidden>
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </Page>
    );
  }
  if (isError || !me) {
    return (
      <Page variante="sezioni">
        <PageHeader area="account" titolo="Abbonamento" />
        <ErrorState
          title="Non siamo riusciti a caricare il tuo abbonamento."
          message={apiErrorMessage(error)}
          onRetry={() => refetch()}
        />
      </Page>
    );
  }

  const isActiveChild = me.family?.role === "child" && me.family.status === "active";
  const currentPaid = !!me.subscription && aPagamento(me.subscription.plan);

  // Avviso «volevi il piano X»: solo se il piano desiderato in registrazione
  // esiste ancora, è a pagamento acquistabile, e l'utente NON è già su un
  // piano pagato — a quel punto l'intento è soddisfatto (o superato) e
  // l'avviso sparisce da solo.
  const metaSlug: unknown = session?.user?.user_metadata?.plan_slug;
  const pianoIntento =
    typeof metaSlug === "string" ? (plans?.find((p) => p.slug === metaSlug) ?? null) : null;
  const mostraIntento =
    !intento.dismissed &&
    !!pianoIntento &&
    pianoIntento.is_active &&
    aPagamento(pianoIntento) &&
    me.subscription?.plan.tipo_prezzo !== "importo";

  return (
    <Page variante="sezioni">
      <PageHeader
        area="account"
        titolo="Abbonamento"
        descrizione="Il tuo piano, gli add-on che lo estendono, il pagamento e lo storico degli acquisti."
      />

      {mostraIntento && pianoIntento && (
        <Alert
          tono="info"
          azione={
            <div className="flex items-center gap-1">
              <LinkButton to={`/app/checkout?piano=${pianoIntento.slug}`} variant="secondary" size="sm">
                Completa l'acquisto
              </LinkButton>
              <IconButton
                label="Nascondi questo avviso"
                icon={<X />}
                size="sm"
                onClick={intento.dismiss}
              />
            </div>
          }
        >
          Volevi il piano <strong>{pianoIntento.nome}</strong>: completa l'acquisto quando vuoi.
        </Alert>
      )}

      <Tabs
        tabs={SCHEDE}
        attivo={tab}
        onChange={setTab}
        ariaLabel="Sezioni dell'abbonamento"
        prefisso={PREFISSO}
      />

      <TabPanel id="piano" attivo={tab} prefisso={PREFISSO}>
        <SchedaPiano vaiAPagamento={() => setTab("pagamento")} />
      </TabPanel>
      <TabPanel id="addon" attivo={tab} prefisso={PREFISSO}>
        <SchedaAddon />
      </TabPanel>
      <TabPanel id="pagamento" attivo={tab} prefisso={PREFISSO}>
        <SchedaPagamento
          mostraGestione={!isActiveChild}
          pianoAPagamento={currentPaid}
          pianoNome={me.subscription?.plan.nome ?? null}
        />
      </TabPanel>
      <TabPanel id="acquisti" attivo={tab} prefisso={PREFISSO}>
        <SchedaAcquisti />
      </TabPanel>
    </Page>
  );
}
