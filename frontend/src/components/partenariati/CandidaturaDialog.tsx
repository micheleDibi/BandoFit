import { useEffect, useId, useState } from "react";
import { useInviaCandidatura } from "../../hooks/useCandidature";
import { useEntitlements } from "../../hooks/useEntitlements";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CANDIDATURE_COPY } from "../../lib/copy";
import type { Candidatura, CallDettaglioAltraAzienda, PartenariatiLimiteMese } from "../../types";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextareaField } from "../ui/Field";
import { TextLink } from "../ui/TextLink";
import { ANCORA_IDENTITA } from "./IdentitaAziendaBox";

/** Limiti del messaggio della candidatura (come il server). */
export const MESSAGGIO_CANDIDATURA_MIN = 50;
export const MESSAGGIO_CANDIDATURA_MAX = 2000;

type Blocco = "non_incluso" | "esaurite" | "profilo";

/** I requisiti visibili della call che si possono dichiarare, con l'id che
 *  il server vuole: da `requisiti_dichiarabili` del dettaglio (tutti i
 *  visibili, anche senza confronto), altrimenti dal confronto (`match`, che
 *  ha l'id dei soli requisiti cercati). Senza id un requisito non si offre. */
function requisitiDichiarabili(call: CallDettaglioAltraAzienda) {
  const ids = new Map<string, string>(
    [...(call.match?.copre ?? []), ...(call.match?.non_copre ?? [])].map(
      (r) => [r.etichetta, r.requisito_id] as const,
    ),
  );
  for (const r of call.requisiti_dichiarabili ?? []) ids.set(r.etichetta, r.id);
  return call.requisiti.flatMap((r) => {
    const id = ids.get(r.etichetta);
    return id ? [{ id, etichetta: r.etichetta, testo: r.testo }] : [];
  });
}

/** Perché oggi non ci si può candidare, dai dati già noti (il server
 *  ricontrolla sempre). `limite` 0 = non incluse nel piano. */
function bloccoDa(limite: PartenariatiLimiteMese | null | undefined, optIn: boolean | undefined): Blocco | null {
  if (limite?.limite === 0) return "non_incluso";
  if (limite && limite.residuo !== null && limite.residuo <= 0) return "esaurite";
  if (optIn === false) return "profilo";
  return null;
}

/** Codice d'errore del server → avviso dedicato (gli altri: il messaggio). */
function bloccoDaErrore(codice: string | undefined): Blocco | null {
  if (codice === "funzione_non_inclusa") return "non_incluso";
  if (codice === "candidature_esaurite") return "esaurite";
  if (codice === "profilo_partner_non_attivo") return "profilo";
  return null;
}

function AvvisoBlocco({ blocco }: { blocco: Blocco }) {
  if (blocco === "profilo") {
    return (
      <Alert
        tono="info"
        titolo={CANDIDATURE_COPY.profiloNonAttivoTitolo}
        azione={<TextLink to="/app/azienda#partner">{CANDIDATURE_COPY.profiloCta}</TextLink>}
      >
        {CANDIDATURE_COPY.profiloNonAttivo}
      </Alert>
    );
  }
  const nonIncluso = blocco === "non_incluso";
  return (
    <Alert
      tono="attenzione"
      titolo={nonIncluso ? CANDIDATURE_COPY.gratuitoTitolo : CANDIDATURE_COPY.esauriteTitolo}
      azione={<TextLink to="/app/abbonamento">{CANDIDATURE_COPY.vediPiani}</TextLink>}
    >
      {nonIncluso ? CANDIDATURE_COPY.gratuito : CANDIDATURE_COPY.esaurite}
    </Alert>
  );
}

/** Candidatura spontanea a una call pubblica di un'altra azienda (solo il
 *  titolare): posizione (obbligatoria), messaggio di presentazione (da 50 a
 *  2000 caratteri, contatore collegato con `aria-describedby`), requisiti
 *  cercati che dichiari di avere (compaiono come «dichiarato»), la riga delle
 *  candidature usate nel mese. Se il piano non le include (Gratuito), se sono
 *  finite o se manca la visibilità come partner, al posto del modulo l'avviso
 *  con il rimando giusto; lo stesso sui rifiuti del server. */
export function CandidaturaDialog({
  open,
  onClose,
  call,
  onInviata,
}: {
  open: boolean;
  onClose: () => void;
  call: CallDettaglioAltraAzienda;
  onInviata: (candidatura: Candidatura) => void;
}) {
  const id = useId();
  const invia = useInviaCandidatura(call.id);
  const { data: entitlements } = useEntitlements();
  const limite = entitlements?.partenariati?.candidature_mese ?? null;
  const compatibili = new Set(call.match?.posizioni_compatibili.map((p) => p.id) ?? []);
  const coperti = new Set(call.match?.copre.map((r) => r.etichetta) ?? []);
  const dichiarabili = requisitiDichiarabili(call);
  const posizioneIniziale =
    call.posizioni.length === 1
      ? call.posizioni[0].id
      : (call.posizioni.find((p) => compatibili.has(p.id))?.id ?? "");

  const [posizione, setPosizione] = useState(posizioneIniziale);
  const [messaggio, setMessaggio] = useState("");
  /** Id dei requisiti dichiarati. */
  const [dichiarati, setDichiarati] = useState<string[]>([]);
  const [tentato, setTentato] = useState(false);

  // A ogni apertura si riparte da zero.
  useEffect(() => {
    if (!open) return;
    setPosizione(posizioneIniziale);
    setMessaggio("");
    setDichiarati([]);
    setTentato(false);
    invia.reset();
    // Solo all'apertura (`invia` cambia a ogni render).
  }, [open]);

  const blocco = bloccoDaErrore(apiErrorCode(invia.error)) ?? bloccoDa(limite, call.opt_in);
  const lunghezza = messaggio.trim().length;
  const messaggioOk =
    lunghezza >= MESSAGGIO_CANDIDATURA_MIN && lunghezza <= MESSAGGIO_CANDIDATURA_MAX;
  const valida = !!posizione && messaggioOk;
  const erroreGenerico = invia.isError && !bloccoDaErrore(apiErrorCode(invia.error));

  const alterna = (id: string) =>
    setDichiarati((prima) => (prima.includes(id) ? prima.filter((e) => e !== id) : [...prima, id]));

  const conferma = () => {
    setTentato(true);
    if (!valida || blocco) return;
    invia.mutate(
      {
        posizione_id: posizione,
        messaggio: messaggio.trim(),
        // Nell'ordine della call.
        requisiti_dichiarati: dichiarabili.map((r) => r.id).filter((id) => dichiarati.includes(id)),
      },
      {
        onSuccess: (candidatura) => {
          onClose();
          onInviata(candidatura);
        },
      },
    );
  };

  const contatore =
    lunghezza < MESSAGGIO_CANDIDATURA_MIN
      ? `${lunghezza} caratteri: ne servono almeno ${MESSAGGIO_CANDIDATURA_MIN}.`
      : `${lunghezza.toLocaleString("it-IT")} caratteri su ${MESSAGGIO_CANDIDATURA_MAX.toLocaleString("it-IT")}.`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      dismissible={!invia.isPending}
      title="Candidati a questa call"
      footer={
        blocco ? (
          <Button type="button" variant="secondary" onClick={onClose}>
            Chiudi
          </Button>
        ) : (
          <>
            <Button type="button" variant="secondary" onClick={onClose} disabled={invia.isPending}>
              Annulla
            </Button>
            <Button type="button" onClick={conferma} loading={invia.isPending}>
              Invia la candidatura
            </Button>
          </>
        )
      }
    >
      {blocco ? (
        <AvvisoBlocco blocco={blocco} />
      ) : (
        <div className="flex flex-col gap-4">
          <p>
            Chi ha creato la call vede la tua candidatura con il profilo partner della tua azienda,
            in forma anonima, e decide se aprire una conversazione.
          </p>
          <SelectField
            label="Per quale posizione"
            required
            value={posizione}
            onChange={(e) => setPosizione(e.target.value)}
            error={tentato && !posizione ? "Scegli la posizione per cui ti candidi" : undefined}
          >
            <option value="">Scegli una posizione…</option>
            {call.posizioni.map((p) => (
              <option key={p.id} value={p.id}>
                {p.titolo}
                {compatibili.has(p.id) ? " (adatta alla tua azienda)" : ""}
              </option>
            ))}
          </SelectField>

          <div className="flex flex-col gap-1">
            <TextareaField
              label="Presentati"
              required
              rows={6}
              maxLength={MESSAGGIO_CANDIDATURA_MAX}
              value={messaggio}
              onChange={(e) => setMessaggio(e.target.value)}
              aria-describedby={`${id}-aiuto ${id}-contatore`}
              error={
                tentato && !messaggioOk
                  ? `Scrivi almeno ${MESSAGGIO_CANDIDATURA_MIN} caratteri`
                  : undefined
              }
            />
            <p id={`${id}-aiuto`} className="text-small text-ink-3">
              Spiega come la tua azienda contribuirebbe al progetto. {CANDIDATURE_COPY.notaContatti}
            </p>
            <p
              id={`${id}-contatore`}
              className={`text-right text-small tabular-nums ${lunghezza < MESSAGGIO_CANDIDATURA_MIN ? "text-warning-ink" : "text-ink-3"}`}
            >
              {contatore}
            </p>
          </div>

          {dichiarabili.length > 0 && (
            <fieldset aria-describedby={`${id}-dichiarati`} className="flex flex-col gap-2">
              <legend className="text-small font-medium text-ink">
                Requisiti della call che dichiari di avere
              </legend>
              <p id={`${id}-dichiarati`} className="text-small text-ink-3">
                {CANDIDATURE_COPY.notaDichiarati}
              </p>
              <ul className="flex flex-col gap-2">
                {dichiarabili.map((r) => {
                  const scelto = dichiarati.includes(r.id);
                  return (
                    <li key={r.id}>
                      <Checkbox
                        checked={scelto}
                        onChange={() => alterna(r.id)}
                        label={
                          <>
                            <Badge className="mr-1.5 tabular-nums">{r.etichetta}</Badge>
                            {r.testo}
                            {scelto && (
                              <span className="ml-1.5 text-small text-ink-3">({CANDIDATURE_COPY.dichiarato})</span>
                            )}
                          </>
                        }
                        descrizione={
                          coperti.has(r.etichetta) ? (
                            <span className="text-fit-ink">Dai dati della tua azienda risulta coperto.</span>
                          ) : undefined
                        }
                      />
                    </li>
                  );
                })}
              </ul>
            </fieldset>
          )}

          {limite && limite.limite !== 0 && (
            <p className="text-body text-ink-2 tabular-nums">
              {CANDIDATURE_COPY.quota(limite.usate, limite.limite)}
              <span className="block text-small text-ink-3">{CANDIDATURE_COPY.quotaNota}</span>
            </p>
          )}

          {erroreGenerico && (
            <Alert
              tono="errore"
              azione={
                apiErrorCode(invia.error) === "identita_non_verificata" ? (
                  <TextLink to={`/app/azienda#${ANCORA_IDENTITA}`}>Dati azienda</TextLink>
                ) : undefined
              }
            >
              {apiErrorMessage(invia.error)}
            </Alert>
          )}
        </div>
      )}
    </Dialog>
  );
}
