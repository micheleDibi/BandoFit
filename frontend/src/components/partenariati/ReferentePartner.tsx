import { useId, useState } from "react";
import {
  useInformativaPartner,
  useReferentePartner,
  useRispostaReferente,
} from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { PARTNER_COPY } from "../../lib/copy";
import type { PartnerProfile } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { SelectField } from "../ui/Field";
import { Panel } from "../ui/Panel";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { TestoInformativa } from "./ConsensoPartnerDialog";

/** Esiti: la regione `status` è sempre montata (l'esito va annunciato anche se
 *  il blocco che l'ha causato sparisce); l'errore è un `Alert` a sé. */
function Esito({ testo, errore }: { testo: string | null; errore: string | null }) {
  return (
    <>
      <div role="status" aria-live="polite">
        {testo && (
          <Alert tono="ok" ruolo="none">
            {testo}
          </Alert>
        )}
      </div>
      {errore && <Alert tono="errore">{errore}</Alert>}
    </>
  );
}

/** Referente per i partenariati, lato titolare: di default è il titolare;
 *  si può proporre un membro con accesso a questa azienda, che diventa
 *  referente solo quando accetta (con la sua informativa). */
export function ReferentePartner({ profilo }: { profilo: PartnerProfile }) {
  const referente = useReferentePartner();
  const [scelta, setScelta] = useState("");
  const [esito, setEsito] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);

  const { referente: attuale, referenti_possibili: possibili } = profilo;
  const nomeAttuale =
    attuale.tipo === "titolare" || attuale.sei_tu
      ? PARTNER_COPY.referenteTu
      : (attuale.nome ?? "un membro dell'azienda");

  const esegui = async (
    dati: Parameters<typeof referente.mutateAsync>[0],
    messaggio: string,
  ) => {
    setEsito(null);
    setErrore(null);
    try {
      await referente.mutateAsync(dati);
      setEsito(messaggio);
      setScelta("");
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  const proponi = () => {
    const persona = possibili.find((p) => p.user_id === scelta);
    if (!persona) return;
    void esegui(
      { azione: "proponi", user_id: persona.user_id },
      `Proposta inviata a ${persona.nome}: la vedrà nella pagina Azienda.`,
    );
  };

  // Annulla SOLO la proposta pendente: il referente in carica (anche un
  // membro che ha già accettato) resta com'è.
  const annullaProposta = () => {
    void esegui(
      { azione: "annulla_proposta" },
      PARTNER_COPY.referentePropostaAnnullata(
        attuale.tipo === "titolare" || attuale.sei_tu
          ? null
          : (attuale.nome ?? "il membro scelto"),
      ),
    );
  };

  return (
    <Panel titolo={PARTNER_COPY.referenteTitolo}>
      <p className="text-small text-ink-3">{PARTNER_COPY.referenteDescrizione}</p>

      <div className="flex flex-col gap-1">
        <p className="text-body text-ink">{PARTNER_COPY.referenteAttuale(nomeAttuale)}</p>
        {attuale.proposto && (
          <p className="text-body text-warning-ink">
            {PARTNER_COPY.referenteInAttesa(attuale.proposto.nome ?? "la persona scelta")}
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-2">
        {possibili.length > 0 ? (
          <>
            <div className="w-full max-w-xs">
              <SelectField
                label={PARTNER_COPY.referenteSceltaLabel}
                value={scelta}
                onChange={(e) => setScelta(e.target.value)}
              >
                <option value="">Scegli…</option>
                {possibili.map((p) => (
                  <option key={p.user_id} value={p.user_id}>
                    {p.nome}
                  </option>
                ))}
              </SelectField>
            </div>
            <Button
              variant="secondary"
              onClick={proponi}
              disabled={!scelta}
              loading={referente.isPending && !!scelta}
            >
              {PARTNER_COPY.referenteProponi}
            </Button>
          </>
        ) : (
          <p className="text-body text-ink-2">
            {PARTNER_COPY.referenteNessunMembro}{" "}
            <TextLink to="/app/collegati">Account collegati</TextLink>
          </p>
        )}
        {attuale.proposto && (
          <Button variant="ghost" onClick={annullaProposta} disabled={referente.isPending}>
            {PARTNER_COPY.referenteAnnullaProposta}
          </Button>
        )}
        {attuale.tipo === "membro" && (
          <Button
            variant="ghost"
            onClick={() => void esegui({ azione: "rimuovi" }, "Ora il referente sei tu.")}
            disabled={referente.isPending}
          >
            {PARTNER_COPY.referenteTorna}
          </Button>
        )}
      </div>
      <Esito testo={esito} errore={errore} />
    </Panel>
  );
}

/** Banner per un membro: la proposta di diventare referente (con
 *  l'informativa dedicata e «Accetta» / «Rifiuta») oppure, se lo è già, la
 *  rinuncia. Non rende nulla negli altri casi. */
export function ReferenteMembro({ profilo }: { profilo: PartnerProfile }) {
  const { referente } = profilo;
  const proposto = !!referente.proposto?.sei_tu;
  const referenteAttivo = referente.sei_tu && referente.tipo === "membro";
  const informativa = useInformativaPartner(proposto);
  const risposta = useRispostaReferente();
  const [letta, setLetta] = useState(false);
  const [conferma, setConferma] = useState(false);
  const [esito, setEsito] = useState<string | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const idBase = useId();

  if (!proposto && !referenteAttivo && !esito) return null;

  const rispondi = async (
    azione: "accetta" | "rifiuta" | "revoca",
    messaggio: string,
  ) => {
    setEsito(null);
    setErrore(null);
    try {
      await risposta.mutateAsync({
        azione,
        informativa_versione: azione === "accetta" ? informativa.data?.referente_versione : null,
      });
      setEsito(messaggio);
      setConferma(false);
    } catch (err) {
      if (apiErrorCode(err) === "informativa_superata") setLetta(false);
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {proposto ? (
        // Un `Panel`, non un `Alert`: l'avviso è una regione `status` e
        // annuncerebbe tutta l'informativa quando arriva.
        <Panel titolo={PARTNER_COPY.referenteProposto}>
          <div className="flex flex-col gap-3">
            <div
              id={`${idBase}-informativa`}
              role="region"
              aria-label={PARTNER_COPY.referenteInformativa}
              tabIndex={0}
              className="max-h-52 overflow-y-auto rounded-control border border-line bg-sheet px-4 py-3 text-small text-ink-2 focus-visible:outline-2 focus-visible:outline-accent"
            >
              {informativa.isPending ? (
                <div className="flex flex-col gap-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-4 w-full" />
                </div>
              ) : informativa.data ? (
                <TestoInformativa testo={informativa.data.referente_testo} />
              ) : (
                <p className="text-danger">
                  {apiErrorMessage(informativa.error, PARTNER_COPY.informativaNonCaricata)}
                </p>
              )}
            </div>
            <Checkbox
              label={PARTNER_COPY.referenteCheckbox}
              checked={letta}
              onChange={(e) => setLetta(e.target.checked)}
              aria-describedby={`${idBase}-informativa`}
              disabled={!informativa.data}
            />
            <div className="flex flex-wrap gap-2">
              <Button
                variant="secondary"
                onClick={() =>
                  void rispondi("accetta", "Hai accettato: ora sei il referente per i partenariati.")
                }
                disabled={!letta || !informativa.data || risposta.isPending}
                loading={risposta.isPending && letta}
              >
                {PARTNER_COPY.referenteAccetta}
              </Button>
              <Button
                variant="ghost"
                onClick={() => void rispondi("rifiuta", "Hai rifiutato la proposta.")}
                disabled={risposta.isPending}
              >
                {PARTNER_COPY.referenteRifiuta}
              </Button>
            </div>
          </div>
        </Panel>
      ) : referenteAttivo ? (
        <Alert
          tono="info"
          azione={
            <Button variant="secondary" size="sm" onClick={() => setConferma(true)}>
              {PARTNER_COPY.referenteRinuncia}
            </Button>
          }
        >
          {PARTNER_COPY.referenteSeiTu}
        </Alert>
      ) : null}
      <Esito testo={esito} errore={errore} />

      <ConfirmDialog
        open={conferma}
        titolo={PARTNER_COPY.referenteRinunciaTitolo}
        conferma={PARTNER_COPY.referenteRinuncia}
        annulla={PARTNER_COPY.annulla}
        distruttiva
        inCorso={risposta.isPending}
        onConferma={() =>
          void rispondi("revoca", "Hai rinunciato: il referente torna il titolare.")
        }
        onAnnulla={() => setConferma(false)}
      >
        <p>{PARTNER_COPY.referenteRinunciaTesto}</p>
      </ConfirmDialog>
    </div>
  );
}
