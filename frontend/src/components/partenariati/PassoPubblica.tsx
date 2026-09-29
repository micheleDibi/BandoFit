import { CheckCircle2, Handshake, Megaphone } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAggiornaCall, usePubblicaCall } from "../../hooks/useCallPartenariato";
import { usePartnerProfile } from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CALL_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate, todayItalyIso } from "../../lib/format";
import type { MotivoBloccoCall, VisibilitaCall } from "../../types";
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { TextField } from "../ui/Field";
import { BarraPasso } from "./CallStepper";
import { SceltaRadio } from "./CampiCall";
import { ConsensoPartnerDialog } from "./ConsensoPartnerDialog";
import { AvvisoLimiteCall, RiepilogoLimiteCall, statoLimite, useLimiteCall } from "./LimitiCall";
import { NotaAnonima } from "./PassoBando";
import { soloCambiati, vuoto, type PassoProps } from "./passoComune";

/** Scadenza proposta: come il server se non la indichi (60 giorni da oggi, ma
 *  non oltre la scadenza del bando; `partner_call_scadenza_default_giorni`). */
const GIORNI_SCADENZA_DEFAULT = 60;

function piuGiorni(isoData: string, giorni: number): string {
  const [a, m, g] = isoData.split("-").map(Number);
  const d = new Date(Date.UTC(a, m - 1, g + giorni));
  return d.toISOString().slice(0, 10);
}

type Destinazione = { passo: number } | { link: string; testo: string };

/** Dove si sistema un motivo di blocco (codici di `_motivi_blocco` del
 *  servizio): il passo del wizard o una pagina. Codici nuovi: solo il testo. */
const DESTINAZIONI: Record<string, Destinazione> = {
  identita_non_verificata: { link: "/app/azienda", testo: "Vai ai dati dell'azienda" },
  piano_non_include_call: { link: "/app/abbonamento", testo: CALL_COPY.vediPiani },
  limite_call_raggiunto: { link: "/app/abbonamento", testo: CALL_COPY.vediPiani },
  partenariato_non_ammesso: { passo: 1 },
  regole_non_confermate: { passo: 2 },
  nessun_requisito_cercato: { passo: 3 },
  requisiti_non_validi: { passo: 3 },
  posizioni_mancanti: { passo: 4 },
  titolo_mancante: { passo: 5 },
  descrizione_mancante: { passo: 5 },
  testo_non_conforme: { passo: 6 },
};
const destinazione = (m: MotivoBloccoCall): Destinazione | null => DESTINAZIONI[m.codice] ?? null;

/** Proposta di comparire anche come partner (consenso con origine
 *  `wizard_call`): solo se il profilo non è visibile e si può attivare. */
function PropostaVisibilita() {
  const { data: profilo } = usePartnerProfile();
  const [aperto, setAperto] = useState(false);
  const [fatto, setFatto] = useState(false);
  if (!profilo || !profilo.editable) return null;
  if (fatto) {
    return (
      <p className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">
        {PARTNER_COPY.attivato}
      </p>
    );
  }
  if (profilo.visibile || profilo.sospeso || !profilo.identita.verificata) return null;
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-slate-200 px-4 py-3">
      <div className="flex min-w-0 items-start gap-2 text-sm text-slate-700">
        <Handshake className="mt-0.5 size-4 shrink-0 text-brand-500" aria-hidden />
        <p>
          Vuoi comparire anche tu tra i partner suggeriti alle altre aziende? È facoltativo e puoi
          revocarlo quando vuoi.
        </p>
      </div>
      <Button variant="secondary" size="sm" onClick={() => setAperto(true)}>
        {PARTNER_COPY.attivaVisibilita}
      </Button>
      <ConsensoPartnerDialog
        open={aperto}
        profilo={profilo}
        origine="wizard_call"
        onClose={() => setAperto(false)}
        onAttivato={() => {
          setAperto(false);
          setFatto(true);
        }}
      />
    </div>
  );
}

/** Passo 7: scadenza, visibilità, limiti del piano e pubblicazione. Dopo la
 *  pubblicazione si cambiano solo scadenza e visibilità (con una nuova
 *  versione della call). */
export function PassoPubblica({ call, onIndietro, onDirty, onVai }: PassoProps) {
  const navigate = useNavigate();
  const idVisibilita = useId();
  const aggiorna = useAggiornaCall(call.id);
  const pubblica = usePubblicaCall(call.id);
  const limite = useLimiteCall(call.limiti?.call_attive);
  const bozza = call.stato === "bozza";

  const oggi = todayItalyIso();
  const massimo = call.bando.scadenza ?? undefined;
  const proposta = (() => {
    const tra = piuGiorni(oggi, GIORNI_SCADENZA_DEFAULT);
    return massimo && massimo < tra ? massimo : tra;
  })();
  const [scadenza, setScadenza] = useState(call.scadenza_call ?? proposta);
  const [visibilita, setVisibilita] = useState<VisibilitaCall>(call.visibilita);
  const [errore, setErrore] = useState<string | null>(null);
  const [annuncio, setAnnuncio] = useState<string | null>(null);

  // In bozza la scadenza va nel corpo della pubblicazione; dopo, con un PATCH.
  const campi = soloCambiati(
    call,
    bozza ? { visibilita } : { scadenza_call: scadenza || null, visibilita },
  );
  const dirty = bozza
    ? visibilita !== call.visibilita || scadenza !== (call.scadenza_call ?? proposta)
    : !vuoto(campi);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const statoPiano = statoLimite(limite);
  const bloccatoPiano = bozza && (statoPiano === "non_incluso" || statoPiano === "esaurito");
  const motivi = call.motivi_blocco;

  const conferma = async () => {
    setErrore(null);
    setAnnuncio(null);
    if (!scadenza) {
      setErrore("Indica fino a quando accetti candidature.");
      return;
    }
    if (scadenza < oggi || (massimo && scadenza > massimo)) {
      setErrore(
        massimo
          ? `La scadenza della call va da oggi al ${formatDate(massimo)}, la scadenza del bando.`
          : "La scadenza della call non può essere nel passato.",
      );
      return;
    }
    try {
      if (!vuoto(campi)) await aggiorna.mutateAsync(campi);
      if (!bozza) {
        setAnnuncio("Modifiche salvate: la call è aggiornata.");
        return;
      }
      const pubblicata = await pubblica.mutateAsync({ scadenza_call: scadenza });
      navigate(`/app/partenariati/call/${pubblicata.id}?tab=panoramica`, {
        state: { annuncio: "Fatto: la call è pubblicata." },
      });
    } catch {
      // mostrato nella barra
    }
  };

  const erroreServer = aggiorna.isError
    ? apiErrorMessage(aggiorna.error)
    : pubblica.isError
      ? apiErrorMessage(pubblica.error)
      : null;
  const codicePubblica = pubblica.isError ? apiErrorCode(pubblica.error) : undefined;

  return (
    <div className="space-y-4">
      {bozza && (
        <Card className="space-y-3 p-5">
          <h3 className="text-sm font-semibold text-slate-800">Prima di pubblicare</h3>
          {call.puo_pubblicare && motivi.length === 0 ? (
            <p className="inline-flex items-center gap-2 text-sm text-emerald-800">
              <CheckCircle2 className="size-4" aria-hidden />
              È tutto pronto per la pubblicazione.
            </p>
          ) : (
            <ul className="space-y-2">
              {motivi.map((m) => {
                const dove = destinazione(m);
                return (
                  <li
                    key={m.codice}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900"
                  >
                    <span>{m.messaggio}</span>
                    {dove && "passo" in dove ? (
                      <Button variant="secondary" size="sm" onClick={() => onVai(dove.passo)}>
                        Vai al passo {dove.passo}
                      </Button>
                    ) : dove ? (
                      <LinkButton to={dove.link} variant="secondary" size="sm">
                        {dove.testo}
                      </LinkButton>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
      )}

      <Card className="space-y-5 p-5">
        {!bozza && call.pubblicata_at && (
          <p className="inline-flex items-center gap-2 text-sm text-emerald-800">
            <Megaphone className="size-4" aria-hidden />
            Pubblicata il {formatDate(call.pubblicata_at)}. Puoi cambiare scadenza e visibilità.
          </p>
        )}
        <div className="max-w-xs">
          <TextField
            label="Candidature fino al"
            type="date"
            required
            min={oggi}
            max={massimo}
            value={scadenza}
            onChange={(e) => setScadenza(e.target.value)}
            helper={
              massimo
                ? `Al più tardi il ${formatDate(massimo)}, quando scade il bando.`
                : "Dopo questa data la call si chiude da sola."
            }
          />
        </div>
        <SceltaRadio<VisibilitaCall>
          legenda="Chi può vedere la call"
          nome={idVisibilita}
          valore={visibilita}
          onChange={setVisibilita}
          opzioni={[
            {
              valore: "pubblica",
              etichetta: CALL_COPY.visibilita.pubblica,
              nota: "Compare tra le call delle altre aziende e nei suggerimenti.",
            },
            {
              valore: "solo_invitati",
              etichetta: CALL_COPY.visibilita.solo_invitati,
              nota: "Non compare nella bacheca: la vedono solo le aziende che inviti tu.",
            },
          ]}
        />
        <NotaAnonima />
        {bozza && (
          <>
            <RiepilogoLimiteCall limite={limite} />
            <AvvisoLimiteCall limite={limite} editable={call.editable} />
            <PropostaVisibilita />
          </>
        )}

        <div role="status" aria-live="polite">
          {annuncio && <p className="text-sm text-emerald-700">{annuncio}</p>}
        </div>
        <BarraPasso
          onIndietro={onIndietro}
          onAvanti={() => void conferma()}
          etichettaAvanti={bozza ? "Pubblica la call" : "Salva le modifiche"}
          inCorso={aggiorna.isPending || pubblica.isPending}
          disabilitato={bozza ? bloccatoPiano : !dirty}
          errore={errore ?? erroreServer}
          nota={
            codicePubblica === "limite_call_raggiunto" || codicePubblica === "piano_non_include_call" ? (
              <LinkButton to="/app/abbonamento" variant="secondary" size="sm">
                {CALL_COPY.vediPiani}
              </LinkButton>
            ) : bozza ? (
              "Dopo la pubblicazione potrai cambiare testi, scadenza, visibilità, budget, requisiti e posizioni, ma non titolo e regole."
            ) : undefined
          }
        />
      </Card>
    </div>
  );
}
