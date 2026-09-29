import { Link } from "react-router-dom";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useConsensoPartner, useInformativaPartner } from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTNER_COPY } from "../../lib/copy";
import type { OrigineConsensoPartner, PartnerProfile } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { Skeleton } from "../ui/states";
import { ANCORA_IDENTITA } from "./IdentitaAziendaBox";

// ---- Testo dell'informativa -----------------------------------------------

/** Rende il markdown semplice delle informative (titoli `#`/`##`, elenchi
 *  `- `, paragrafi) come testo: niente HTML dal server, niente link. */
export function TestoInformativa({ testo }: { testo: string }) {
  const blocchi: ReactNode[] = [];
  let paragrafo: string[] = [];
  let elenco: string[] = [];

  const chiudiParagrafo = () => {
    if (paragrafo.length === 0) return;
    blocchi.push(
      <p key={`p${blocchi.length}`} className="mt-2 first:mt-0">
        {paragrafo.join(" ")}
      </p>,
    );
    paragrafo = [];
  };
  const chiudiElenco = () => {
    if (elenco.length === 0) return;
    blocchi.push(
      <ul key={`u${blocchi.length}`} className="mt-1.5 list-disc space-y-1 pl-5">
        {elenco.map((voce, i) => (
          <li key={i}>{voce}</li>
        ))}
      </ul>,
    );
    elenco = [];
  };

  for (const grezza of testo.split("\n")) {
    const riga = grezza.trim().replace(/\*\*/g, "");
    if (!riga) {
      chiudiParagrafo();
      chiudiElenco();
    } else if (riga.startsWith("#")) {
      chiudiParagrafo();
      chiudiElenco();
      const livello = riga.startsWith("##") ? 2 : 1;
      const titolo = riga.replace(/^#+\s*/, "");
      blocchi.push(
        <p
          key={`h${blocchi.length}`}
          className={cn(
            "font-semibold text-slate-900",
            livello === 1 ? "mt-3 text-sm first:mt-0" : "mt-3 text-[13px]",
          )}
        >
          {titolo}
        </p>,
      );
    } else if (/^[-*]\s+/.test(riga)) {
      chiudiParagrafo();
      elenco.push(riga.replace(/^[-*]\s+/, ""));
    } else if (elenco.length > 0) {
      // Continuazione di una voce d'elenco andata a capo.
      elenco[elenco.length - 1] += ` ${riga}`;
    } else {
      paragrafo.push(riga);
    }
  }
  chiudiParagrafo();
  chiudiElenco();
  return <>{blocchi}</>;
}

// ---- Consenso ---------------------------------------------------------------

type Scelta = "nome" | "anonima";

export interface ConsensoPartnerDialogProps {
  profilo: PartnerProfile;
  origine: OrigineConsensoPartner;
  /** Uscita senza consenso: «Annulla» nel dialog, «Non ora» nell'import. */
  onClose: () => void;
  /** Consenso registrato: la risposta del server è già in cache. */
  onAttivato?: (profilo: PartnerProfile) => void;
  /** `true` = contenuto e bottoni dentro un altro contenitore (passo finale
   *  dell'import), senza la modale. */
  inline?: boolean;
  /** Solo per la variante modale. */
  open?: boolean;
  etichettaAnnulla?: string;
}

/** Consenso a comparire come partner: informativa leggibile in un box che
 *  scorre, checkbox NON preselezionata e scelta obbligatoria (senza
 *  preselezione) tra nome dell'azienda e forma anonima. «Attiva» resta
 *  disabilitato finché mancano entrambe. Invia la versione dell'informativa
 *  mostrata: se nel frattempo è cambiata, il testo si ricarica e si chiede di
 *  confermare di nuovo. */
export function ConsensoPartnerDialog({
  profilo,
  origine,
  onClose,
  onAttivato,
  inline = false,
  open = true,
  etichettaAnnulla,
}: ConsensoPartnerDialogProps) {
  const informativa = useInformativaPartner(open);
  const consenso = useConsensoPartner();
  const [accetto, setAccetto] = useState(false);
  const [scelta, setScelta] = useState<Scelta | null>(null);
  const [errore, setErrore] = useState<string | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const idBase = useId();
  const idInformativa = `${idBase}-informativa`;
  const idNotaNome = `${idBase}-nota-nome`;
  const idNotaAnonima = `${idBase}-nota-anonima`;
  const idMotivoNome = `${idBase}-motivo-nome`;
  const idErrore = `${idBase}-errore`;

  const { identita } = profilo;
  const nominativoPossibile = identita.puo_essere_nominativo;

  // A ogni apertura si riparte da zero: nessuna scelta ereditata da prima.
  // Il focus va sul testo dell'informativa (si scorre con le frecce): la
  // modale nativa l'ha già aperta, perché gli effetti dei figli girano prima.
  useEffect(() => {
    if (!open) return;
    setAccetto(false);
    setScelta(null);
    setErrore(null);
    const frame = requestAnimationFrame(() => boxRef.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [open]);

  // Una scelta «nome» diventata impossibile (dati cambiati) non resta attiva.
  useEffect(() => {
    if (!nominativoPossibile && scelta === "nome") setScelta(null);
  }, [nominativoPossibile, scelta]);

  const pronto =
    accetto && scelta !== null && !!informativa.data && identita.verificata && !consenso.isPending;

  const handleAttiva = async () => {
    if (!pronto || !informativa.data) return;
    setErrore(null);
    try {
      const risposta = await consenso.mutateAsync({
        azione: "concedi",
        informativa_versione: informativa.data.versione,
        origine,
        anonimo: scelta === "anonima",
      });
      onAttivato?.(risposta);
    } catch (err) {
      if (apiErrorCode(err) === "informativa_superata") {
        // Il testo nuovo arriva con il refetch: va riletto e riconfermato.
        setAccetto(false);
        setErrore(PARTNER_COPY.informativaAggiornata);
        boxRef.current?.scrollTo({ top: 0 });
      } else {
        setErrore(apiErrorMessage(err));
      }
    }
  };

  let testo: ReactNode;
  if (informativa.isPending) {
    testo = (
      <div className="space-y-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  } else if (informativa.isError || !informativa.data) {
    testo = (
      <div>
        <p className="text-sm text-red-700">
          {apiErrorMessage(informativa.error, PARTNER_COPY.informativaNonCaricata)}
        </p>
        <Button
          variant="secondary"
          size="sm"
          className="mt-2"
          loading={informativa.isFetching}
          onClick={() => void informativa.refetch()}
        >
          Riprova
        </Button>
      </div>
    );
  } else {
    testo = <TestoInformativa testo={informativa.data.testo} />;
  }

  const corpo = (
    <div className="space-y-4">
      <p>{PARTNER_COPY.consensoIntro}</p>

      {!identita.verificata && identita.motivo && (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800" role="alert">
          {PARTNER_COPY.motiviIdentita[identita.motivo]}
        </p>
      )}

      <div
        ref={boxRef}
        id={idInformativa}
        role="region"
        aria-label={PARTNER_COPY.consensoInformativa}
        tabIndex={0}
        className="max-h-60 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-[13px] leading-relaxed text-slate-700 focus-visible:outline-2 focus-visible:outline-brand-500"
      >
        {testo}
      </div>

      <label className="flex cursor-pointer items-start gap-2.5 text-sm font-medium text-slate-800">
        <input
          type="checkbox"
          className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
          checked={accetto}
          onChange={(e) => setAccetto(e.target.checked)}
          aria-describedby={idInformativa}
          disabled={!informativa.data}
        />
        {PARTNER_COPY.consensoCheckbox}
      </label>

      <fieldset>
        <legend className="text-sm font-medium text-slate-800">
          {PARTNER_COPY.sceltaNomeTitolo}
        </legend>
        <div className="mt-2 space-y-2">
          <label
            className={cn(
              "flex items-start gap-2.5 rounded-lg border px-3 py-2.5",
              nominativoPossibile
                ? "cursor-pointer border-slate-200 hover:border-brand-300"
                : "cursor-not-allowed border-slate-100 bg-slate-50",
              scelta === "nome" && "border-brand-400 bg-brand-50/60",
            )}
          >
            <input
              type="radio"
              name={`${idBase}-scelta`}
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
              checked={scelta === "nome"}
              onChange={() => setScelta("nome")}
              disabled={!nominativoPossibile}
              aria-describedby={nominativoPossibile ? idNotaNome : `${idNotaNome} ${idMotivoNome}`}
            />
            <span>
              <span
                className={cn(
                  "block text-sm font-medium",
                  nominativoPossibile ? "text-slate-800" : "text-slate-400",
                )}
              >
                {PARTNER_COPY.sceltaNome}
                {identita.denominazione_registro && nominativoPossibile && (
                  <span className="font-normal text-slate-500">
                    {" "}
                    ({identita.denominazione_registro})
                  </span>
                )}
              </span>
              <span id={idNotaNome} className="block text-xs text-slate-500">
                {PARTNER_COPY.sceltaNomeNota}
              </span>
            </span>
          </label>
          {!nominativoPossibile && identita.motivo_nominativo && (
            <p id={idMotivoNome} className="px-1 text-xs text-slate-600">
              {PARTNER_COPY.motiviNominativo[identita.motivo_nominativo] ??
                PARTNER_COPY.motiviNominativo.identita_non_verificata_admin}
              {identita.motivo_nominativo !== "non_disponibile" && (
                <>
                  {" "}
                  {/* Il riquadro «Verifica dell'identità» sta nella pagina
                      Azienda: si chiude il consenso e ci si va. */}
                  <Link
                    to={`/app/azienda#${ANCORA_IDENTITA}`}
                    onClick={onClose}
                    className="font-medium text-brand-600 hover:text-brand-700"
                  >
                    {PARTNER_COPY.chiediVerifica} →
                  </Link>
                </>
              )}
            </p>
          )}
          <label
            className={cn(
              "flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-200 px-3 py-2.5 hover:border-brand-300",
              scelta === "anonima" && "border-brand-400 bg-brand-50/60",
            )}
          >
            <input
              type="radio"
              name={`${idBase}-scelta`}
              className="mt-0.5 size-4 shrink-0 cursor-pointer accent-brand-500"
              checked={scelta === "anonima"}
              onChange={() => setScelta("anonima")}
              aria-describedby={idNotaAnonima}
            />
            <span>
              <span className="block text-sm font-medium text-slate-800">
                {PARTNER_COPY.sceltaAnonima}
              </span>
              <span id={idNotaAnonima} className="block text-xs text-slate-500">
                {PARTNER_COPY.sceltaAnonimaNota}
              </span>
            </span>
          </label>
        </div>
      </fieldset>

      {errore && (
        <p
          id={idErrore}
          className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
          role="alert"
        >
          {errore}
        </p>
      )}
    </div>
  );

  const bottoni = (
    <>
      <Button variant="ghost" onClick={onClose} disabled={consenso.isPending}>
        {etichettaAnnulla ?? PARTNER_COPY.annulla}
      </Button>
      <Button
        onClick={() => void handleAttiva()}
        disabled={!pronto}
        loading={consenso.isPending}
        aria-describedby={errore ? idErrore : undefined}
      >
        {PARTNER_COPY.attiva}
      </Button>
    </>
  );

  if (inline) {
    return (
      <div>
        {corpo}
        <div className="mt-5 flex justify-end gap-2">{bottoni}</div>
      </div>
    );
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={PARTNER_COPY.consensoTitolo}
      size="lg"
      dismissible={!consenso.isPending}
      footer={bottoni}
    >
      {corpo}
    </Dialog>
  );
}
