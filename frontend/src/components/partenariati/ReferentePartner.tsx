import { UserCheck, UserRound } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import {
  useInformativaPartner,
  useReferentePartner,
  useRispostaReferente,
} from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { PARTNER_COPY } from "../../lib/copy";
import type { PartnerProfile } from "../../types";
import { Button } from "../ui/Button";
import { Card } from "../ui/Card";
import { Dialog } from "../ui/Dialog";
import { SelectField } from "../ui/Field";
import { Skeleton } from "../ui/states";
import { TestoInformativa } from "./ConsensoPartnerDialog";

function Esito({ testo, errore }: { testo: string | null; errore: string | null }) {
  return (
    <>
      <div role="status" aria-live="polite">
        {testo && <p className="mt-3 text-sm text-emerald-700">{testo}</p>}
      </div>
      {errore && (
        <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
          {errore}
        </p>
      )}
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
    <Card className="p-5">
      <h3 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
        <UserRound className="size-4 text-brand-500" aria-hidden />
        {PARTNER_COPY.referenteTitolo}
      </h3>
      <p className="mt-0.5 text-sm text-slate-500">{PARTNER_COPY.referenteDescrizione}</p>

      <p className="mt-3 text-sm text-slate-700">
        {PARTNER_COPY.referenteAttuale(nomeAttuale)}
      </p>
      {attuale.proposto && (
        <p className="mt-1 text-sm text-amber-700">
          {PARTNER_COPY.referenteInAttesa(attuale.proposto.nome ?? "la persona scelta")}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
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
          <p className="text-sm text-slate-500">
            {PARTNER_COPY.referenteNessunMembro}{" "}
            <Link to="/app/collegati" className="font-medium text-brand-600 hover:text-brand-700">
              Account collegati →
            </Link>
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
    </Card>
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
    <Card className="border-brand-200 bg-brand-50/40 p-5">
      {proposto ? (
        <>
          <h3 className="inline-flex items-center gap-2 font-display text-base font-semibold text-slate-900">
            <UserCheck className="size-4 text-brand-500" aria-hidden />
            {PARTNER_COPY.referenteProposto}
          </h3>
          <div
            id={`${idBase}-informativa`}
            role="region"
            aria-label={PARTNER_COPY.referenteInformativa}
            tabIndex={0}
            className="mt-3 max-h-52 overflow-y-auto rounded-lg border border-slate-200 bg-white px-4 py-3 text-[13px] leading-relaxed text-slate-700 focus-visible:outline-2 focus-visible:outline-brand-500"
          >
            {informativa.isPending ? (
              <div className="space-y-2">
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-4 w-full" />
              </div>
            ) : informativa.data ? (
              <TestoInformativa testo={informativa.data.referente_testo} />
            ) : (
              <p className="text-red-700">
                {apiErrorMessage(informativa.error, PARTNER_COPY.informativaNonCaricata)}
              </p>
            )}
          </div>
          <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm font-medium text-slate-800">
            <input
              type="checkbox"
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={letta}
              onChange={(e) => setLetta(e.target.checked)}
              aria-describedby={`${idBase}-informativa`}
              disabled={!informativa.data}
            />
            {PARTNER_COPY.referenteCheckbox}
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
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
        </>
      ) : referenteAttivo ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="inline-flex items-center gap-2 text-sm font-medium text-slate-800">
            <UserCheck className="size-4 text-brand-500" aria-hidden />
            {PARTNER_COPY.referenteSeiTu}
          </p>
          <Button variant="secondary" size="sm" onClick={() => setConferma(true)}>
            {PARTNER_COPY.referenteRinuncia}
          </Button>
        </div>
      ) : null}
      <Esito testo={esito} errore={errore} />

      <Dialog
        open={conferma}
        onClose={() => setConferma(false)}
        title={PARTNER_COPY.referenteRinunciaTitolo}
        dismissible={!risposta.isPending}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConferma(false)}
              disabled={risposta.isPending}
            >
              {PARTNER_COPY.annulla}
            </Button>
            <Button
              variant="danger"
              loading={risposta.isPending}
              onClick={() =>
                void rispondi("revoca", "Hai rinunciato: il referente torna il titolare.")
              }
            >
              {PARTNER_COPY.referenteRinuncia}
            </Button>
          </>
        }
      >
        <p>{PARTNER_COPY.referenteRinunciaTesto}</p>
      </Dialog>
    </Card>
  );
}
