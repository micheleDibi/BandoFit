import { useCompany } from "../../hooks/useCompany";
import { usePartnerProfile } from "../../hooks/usePartnerProfile";
import { BACHECA_COPY } from "../../lib/copy";
import { Alert } from "../ui/Alert";
import { TextLink } from "../ui/TextLink";

/** Sotto questa completezza del profilo partner si invita a completarlo. */
const COMPLETEZZA_CONSIGLIATA = 60;
const SEZIONE_PARTNER = "/app/azienda#partner";

/** Invito alla visibilità come partner, per «Per te» e per la vista pubblica
 *  di una call. «Per te» funziona anche senza (Q25): qui si spiega cosa
 *  cambia attivandola. Legge il profilo partner dell'azienda attiva; finché
 *  non arriva vale `optIn` del server («Per te»). L'azione solo al titolare:
 *  i membri leggono chi può attivarla. Nulla da dire → niente. */
export function BannerOptIn({ optIn }: { optIn?: boolean }) {
  const profiloQ = usePartnerProfile();
  const { data: azienda } = useCompany();
  const profilo = profiloQ.data;
  const editable = profilo?.editable ?? azienda?.editable ?? false;
  const visibile = profilo ? profilo.visibile : optIn;

  if (profilo?.sospeso) {
    return (
      <Alert tono="attenzione" ruolo="status" titolo="Profilo partner sospeso">
        {BACHECA_COPY.sospeso}
      </Alert>
    );
  }
  if (visibile === false) {
    return (
      <Alert
        tono="info"
        titolo={BACHECA_COPY.optInTitolo}
        azione={
          editable ? <TextLink to={SEZIONE_PARTNER}>{BACHECA_COPY.optInCta}</TextLink> : undefined
        }
      >
        <p>{BACHECA_COPY.optInTesto}</p>
        {!editable && <p className="text-small text-ink-3">{BACHECA_COPY.optInMembro}</p>}
      </Alert>
    );
  }
  if (profilo && profilo.visibile && profilo.completezza < COMPLETEZZA_CONSIGLIATA) {
    return (
      <Alert
        tono="info"
        titolo={BACHECA_COPY.completaTitolo}
        azione={
          editable ? <TextLink to={SEZIONE_PARTNER}>{BACHECA_COPY.completaCta}</TextLink> : undefined
        }
      >
        {BACHECA_COPY.completaTesto(profilo.completezza)}
      </Alert>
    );
  }
  return null;
}
