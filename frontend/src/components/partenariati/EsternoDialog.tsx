import { useEffect, useId, useMemo, useState } from "react";
import { useSalvaEsterno } from "../../hooks/useConsorzio";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import { nomePaese, paesiOrdinati } from "../../lib/paesi";
import type { MembroConsorzio, RuoloMembro, TipoSoggettoPartenariato } from "../../types";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";
import { Skeleton } from "../ui/states";
import { leggiPercentuale, mostraDecimale, percentuale } from "./callDati";
import { SceltaRadio } from "./CampiCall";
import { opzioniRuolo, RUOLI_MEMBRO, type PosizioneOpzione } from "./MembroRiga";

/** Limiti dello schema del server (`EsternoIn`). */
export const LIMITI_ESTERNO = { nomeMin: 2, nomeMax: 200, tipiMax: 5 } as const;

/** Spazi multipli e a capo contano come uno, come sul server. */
const pulisci = (testo: string) => testo.split(/\s+/).filter(Boolean).join(" ");

/** Aggiunge o modifica un membro esterno (non in piattaforma, Q20): solo chi
 *  ha creato la call. Nome, paese e tipi di soggetto li dichiara lui: la
 *  verifica li usa marcandoli «dichiarato» e non ne controlla collegamenti né
 *  bilanci. Modificare un esterno uscito lo ripropone. */
export function EsternoDialog({
  open,
  onClose,
  callId,
  membro,
  posizioni,
  onSalvato,
}: {
  open: boolean;
  onClose: () => void;
  callId: string;
  /** Il membro da modificare; null per aggiungerne uno. */
  membro: MembroConsorzio | null;
  posizioni: PosizioneOpzione[];
  onSalvato: (annuncio: string) => void;
}) {
  const id = useId();
  const salva = useSalvaEsterno(callId);
  const { data: vocabolario, isPending: vocabolarioInArrivo } = usePartenariatiVocabolario();
  const [nome, setNome] = useState("");
  const [paese, setPaese] = useState("");
  const [tipi, setTipi] = useState<TipoSoggettoPartenariato[]>([]);
  const [ruolo, setRuolo] = useState<RuoloMembro>("partner");
  const [posizione, setPosizione] = useState("");
  const [quota, setQuota] = useState("");
  const [errori, setErrori] = useState<Record<string, string>>({});

  // A ogni apertura si riparte dal membro (o da zero).
  useEffect(() => {
    if (!open) return;
    setNome(membro?.nome ?? "");
    setPaese(membro?.paese ?? "");
    setTipi(membro ? [...membro.tipi_soggetto] : []);
    setRuolo(membro?.ruolo ?? "partner");
    setPosizione(membro?.posizione?.id ?? "");
    setQuota(mostraDecimale(membro?.quota_percentuale));
    setErrori({});
    salva.reset();
    // Solo all'apertura (`salva` cambia a ogni render).
  }, [open]);

  const paesi = useMemo(() => {
    const elenco = paesiOrdinati();
    // Un paese salvato fuori dall'elenco resta selezionabile.
    return paese && !elenco.includes(paese) ? [paese, ...elenco] : elenco;
  }, [paese]);
  const salvata = membro?.posizione;
  const opzioniPosizione: PosizioneOpzione[] =
    salvata && !posizioni.some((p) => p.id === salvata.id)
      ? [...posizioni, { id: salvata.id, titolo: salvata.titolo, ruolo: "partner", quota_ipotizzata_pct: null }]
      : posizioni;
  const pieno = tipi.length >= LIMITI_ESTERNO.tipiMax;

  const alternaTipo = (codice: TipoSoggettoPartenariato) =>
    setTipi((prima) =>
      prima.includes(codice)
        ? prima.filter((c) => c !== codice)
        : prima.length < LIMITI_ESTERNO.tipiMax
          ? [...prima, codice]
          : prima,
    );

  const conferma = () => {
    const problemi: Record<string, string> = {};
    const denominazione = pulisci(nome);
    if (denominazione.length < LIMITI_ESTERNO.nomeMin || denominazione.length > LIMITI_ESTERNO.nomeMax) {
      problemi.nome = `Il nome deve avere tra ${LIMITI_ESTERNO.nomeMin} e ${LIMITI_ESTERNO.nomeMax} caratteri`;
    }
    if (!paese) problemi.paese = "Scegli il paese";
    if (tipi.length === 0) problemi.tipi = "Scegli almeno un tipo di soggetto";
    const lettura = leggiPercentuale(quota);
    if (!lettura.ok) problemi.quota = lettura.errore;
    setErrori(problemi);
    if (Object.keys(problemi).length > 0 || !lettura.ok) return;
    salva.mutate(
      {
        membroId: membro?.id ?? null,
        dati: {
          denominazione,
          paese,
          tipi_soggetto: tipi,
          ruolo,
          posizione_id: posizione || null,
          quota_percentuale: lettura.valore,
        },
      },
      {
        onSuccess: () => {
          onSalvato(
            membro
              ? `${denominazione}: modifiche salvate.`
              : `${denominazione} è nel consorzio: confermalo quando i dati sono giusti.`,
          );
          onClose();
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      dismissible={!salva.isPending}
      title={
        !membro
          ? "Aggiungi un membro esterno"
          : membro.stato === "uscito"
            ? "Riproponi il membro esterno"
            : "Modifica il membro esterno"
      }
      footer={
        <>
          <Button type="button" variant="secondary" onClick={onClose} disabled={salva.isPending}>
            Annulla
          </Button>
          <Button type="button" onClick={conferma} loading={salva.isPending}>
            {membro ? "Salva" : "Aggiungi"}
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          conferma();
        }}
      >
        <p>
          Un ente o un'azienda che non usa la piattaforma, per esempio un partner di un altro paese.{" "}
          {CONSORZIO_COPY.notaEsterni}
        </p>
        {membro?.stato === "uscito" && (
          <Alert tono="info">Questo membro era uscito: salvando torna nel consorzio, da confermare.</Alert>
        )}
        <TextField
          label="Nome"
          required
          maxLength={LIMITI_ESTERNO.nomeMax}
          value={nome}
          onChange={(e) => setNome(e.target.value)}
          error={errori.nome}
          helper="Il nome dell'ente o dell'azienda, senza contatti (email, telefono, sito): lo vedono gli altri membri."
        />
        <SelectField
          label="Paese"
          required
          value={paese}
          onChange={(e) => setPaese(e.target.value)}
          error={errori.paese}
        >
          <option value="">Scegli un paese…</option>
          {paesi.map((c) => (
            <option key={c} value={c}>
              {nomePaese(c)}
            </option>
          ))}
        </SelectField>

        <fieldset aria-describedby={`${id}-tipi-aiuto`} className="flex flex-col gap-1.5">
          <legend className="text-small font-medium text-ink">
            Tipo di soggetto
            <span className="text-danger" aria-hidden>
              {" "}
              *
            </span>
          </legend>
          <p id={`${id}-tipi-aiuto`} className="text-small text-ink-3">
            Da 1 a {LIMITI_ESTERNO.tipiMax}: servono a controllare la composizione chiesta dal bando.
            {tipi.length > 0 ? ` Ne hai scelti ${tipi.length}.` : ""}
          </p>
          {vocabolarioInArrivo ? (
            <Skeleton className="h-32 w-full" />
          ) : (
            <div className="flex max-h-52 flex-col gap-2 overflow-y-auto rounded-control border border-line-control px-3 py-2">
              {(vocabolario?.tipi_soggetto ?? []).map((t) => {
                const scelto = tipi.includes(t.codice);
                return (
                  <Checkbox
                    key={t.codice}
                    label={t.etichetta}
                    checked={scelto}
                    disabled={!scelto && pieno}
                    onChange={() => alternaTipo(t.codice)}
                  />
                );
              })}
            </div>
          )}
          {errori.tipi && <InlineError>{errori.tipi}</InlineError>}
        </fieldset>

        <SceltaRadio
          legenda="Ruolo nel consorzio"
          nome={`${id}-ruolo`}
          opzioni={opzioniRuolo(RUOLI_MEMBRO)}
          valore={ruolo}
          onChange={setRuolo}
        />
        {opzioniPosizione.length > 0 && (
          <SelectField
            label="Posizione della call (facoltativa)"
            value={posizione}
            onChange={(e) => setPosizione(e.target.value)}
          >
            <option value="">Nessuna</option>
            {opzioniPosizione.map((p) => (
              <option key={p.id} value={p.id}>
                {p.titolo}
                {p.quota_ipotizzata_pct ? ` (quota prevista ${percentuale(p.quota_ipotizzata_pct)})` : ""}
              </option>
            ))}
          </SelectField>
        )}
        <TextField
          label="Quota del progetto (%)"
          inputMode="decimal"
          value={quota}
          onChange={(e) => setQuota(e.target.value)}
          placeholder="Es. 20"
          error={errori.quota}
          helper="Facoltativa ora, serve per confermare il membro."
        />
        {salva.isError && <Alert tono="errore">{apiErrorMessage(salva.error)}</Alert>}
      </form>
    </Dialog>
  );
}
