import { useIdentitaAzienda } from "../../hooks/useIdentitaAzienda";
import { CHAT_COPY } from "../../lib/copy";
import { Alert } from "../ui/Alert";
import { TextLink } from "../ui/TextLink";
import { ANCORA_IDENTITA } from "./IdentitaAziendaBox";

/** Nota fissa della conversazione quando le identità non sono rivelate. Dal
 *  WP9 la rivelazione è simmetrica: avviene all'accettazione solo se tutte e
 *  due le aziende hanno l'identità verificata dalla piattaforma. Al titolare
 *  di un'azienda non verificata propone la verifica (per le prossime
 *  accettazioni). Testo fissato dal piano (`CHAT_COPY`), parola per parola. */
export function BannerIdentitaNonRivelata({ className }: { className?: string }) {
  const { data: identita } = useIdentitaAzienda();
  // Solo al titolare che può chiederla adesso (non già verificata né in attesa).
  const proponiVerifica = identita?.puo_richiedere === true;
  return (
    <Alert
      tono="info"
      className={className}
      azione={
        proponiVerifica ? (
          <TextLink to={`/app/azienda#${ANCORA_IDENTITA}`}>{CHAT_COPY.identitaVerificaCta}</TextLink>
        ) : undefined
      }
    >
      {CHAT_COPY.identitaNonRivelata}
    </Alert>
  );
}
