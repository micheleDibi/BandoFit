import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useConsensoPartner, useInformativaPartner } from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { cn } from "../../lib/cn";
import { PARTNER_COPY } from "../../lib/copy";
import type { OrigineConsensoPartner, PartnerProfile } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Dialog } from "../ui/Dialog";
import { RadioGroup } from "../ui/RadioGroup";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
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
            "mt-3 font-semibold text-ink",
            livello === 1 ? "text-body first:mt-0" : "text-small",
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
      <div className="flex flex-col gap-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    );
  } else if (informativa.isError || !informativa.data) {
    testo = (
      <div>
        <p className="text-danger">
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

  // «Mostra il nome»: la nota, e sotto il motivo quando non si può scegliere
  // (con il link alla verifica). Tutto nella descrizione dell'opzione, così
  // il radio resta descritto da entrambi.
  const descrizioneNome = (
    <>
      <span id={idNotaNome} className="block">
        {PARTNER_COPY.sceltaNomeNota}
      </span>
      {!nominativoPossibile && identita.motivo_nominativo && (
        <span id={idMotivoNome} className="mt-1 block text-ink-2">
          {PARTNER_COPY.motiviNominativo[identita.motivo_nominativo] ??
            PARTNER_COPY.motiviNominativo.identita_non_verificata_admin}
          {identita.motivo_nominativo !== "non_disponibile" && (
            <>
              {" "}
              {/* Il riquadro «Verifica dell'identità» sta nella pagina
                  Azienda: si chiude il consenso e ci si va. */}
              <TextLink to={`/app/azienda#${ANCORA_IDENTITA}`} onClick={onClose}>
                {PARTNER_COPY.chiediVerifica}
              </TextLink>
            </>
          )}
        </span>
      )}
    </>
  );

  const corpo = (
    <div className="flex flex-col gap-4">
      <p>{PARTNER_COPY.consensoIntro}</p>

      {!identita.verificata && identita.motivo && (
        <Alert tono="attenzione">{PARTNER_COPY.motiviIdentita[identita.motivo]}</Alert>
      )}

      <div
        ref={boxRef}
        id={idInformativa}
        role="region"
        aria-label={PARTNER_COPY.consensoInformativa}
        tabIndex={0}
        className="max-h-60 overflow-y-auto rounded-control bg-desk px-4 py-3 text-small text-ink-2 focus-visible:outline-2 focus-visible:outline-accent"
      >
        {testo}
      </div>

      {/* Mai preselezionata: `accetto` riparte da `false` a ogni apertura. */}
      <Checkbox
        label={PARTNER_COPY.consensoCheckbox}
        checked={accetto}
        onChange={(e) => setAccetto(e.target.checked)}
        aria-describedby={idInformativa}
        disabled={!informativa.data}
      />

      {/* Scelta obbligatoria, senza preselezione (`scelta` parte da `null`). */}
      <RadioGroup
        nome={`${idBase}-scelta`}
        legend={PARTNER_COPY.sceltaNomeTitolo}
        valore={scelta}
        onChange={(id) => setScelta(id as Scelta)}
        opzioni={[
          {
            id: "nome",
            label: (
              <>
                {PARTNER_COPY.sceltaNome}
                {identita.denominazione_registro && nominativoPossibile && (
                  <span className="text-ink-3"> ({identita.denominazione_registro})</span>
                )}
              </>
            ),
            descrizione: descrizioneNome,
            disabled: !nominativoPossibile,
          },
          {
            id: "anonima",
            label: PARTNER_COPY.sceltaAnonima,
            descrizione: <span id={idNotaAnonima}>{PARTNER_COPY.sceltaAnonimaNota}</span>,
          },
        ]}
      />

      {errore && (
        <div id={idErrore}>
          <Alert tono="errore">{errore}</Alert>
        </div>
      )}
    </div>
  );

  const bottoni = (
    <>
      <Button variant="secondary" onClick={onClose} disabled={consenso.isPending}>
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
        <div className="mt-5 flex flex-wrap justify-end gap-2">{bottoni}</div>
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
