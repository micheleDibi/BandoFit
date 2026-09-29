import { useEffect, useId, useMemo, useState } from "react";
import { useSalvaEsterno } from "../../hooks/useConsorzio";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { apiErrorMessage } from "../../lib/api";
import { CONSORZIO_COPY } from "../../lib/copy";
import { nomePaese, paesiOrdinati } from "../../lib/paesi";
import type { MembroConsorzio, RuoloMembro, TipoSoggettoPartenariato } from "../../types";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { SelectField, TextField } from "../ui/Field";
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
          <Button variant="ghost" onClick={onClose} disabled={salva.isPending}>
            Annulla
          </Button>
          <Button onClick={conferma} loading={salva.isPending}>
            {membro ? "Salva" : "Aggiungi"}
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          conferma();
        }}
      >
        <p className="text-sm text-slate-600">
          Un ente o un'azienda che non usa la piattaforma, per esempio un partner di un altro paese.{" "}
          {CONSORZIO_COPY.notaEsterni}
        </p>
        {membro?.stato === "uscito" && (
          <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
            Questo membro era uscito: salvando torna nel consorzio, da confermare.
          </p>
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

        <fieldset aria-describedby={`${id}-tipi-aiuto`}>
          <legend className="text-sm font-medium text-slate-700">
            Tipo di soggetto
            <span className="text-red-500" aria-hidden>
              {" "}
              *
            </span>
          </legend>
          <p id={`${id}-tipi-aiuto`} className="mt-0.5 text-xs text-slate-500">
            Da 1 a {LIMITI_ESTERNO.tipiMax}: servono a controllare la composizione chiesta dal bando.
            {tipi.length > 0 ? ` Ne hai scelti ${tipi.length}.` : ""}
          </p>
          {vocabolarioInArrivo ? (
            <Skeleton className="mt-2 h-32 w-full" />
          ) : (
            <div className="mt-2 max-h-52 space-y-1.5 overflow-y-auto rounded-lg border border-slate-200 px-3 py-2">
              {(vocabolario?.tipi_soggetto ?? []).map((t) => {
                const scelto = tipi.includes(t.codice);
                return (
                  <label
                    key={t.codice}
                    className="flex cursor-pointer items-center gap-2 text-sm text-slate-700 has-[:disabled]:cursor-not-allowed has-[:disabled]:text-slate-400"
                  >
                    <input
                      type="checkbox"
                      className="size-4 shrink-0 cursor-pointer accent-brand-500 disabled:cursor-not-allowed"
                      checked={scelto}
                      disabled={!scelto && pieno}
                      onChange={() => alternaTipo(t.codice)}
                    />
                    {t.etichetta}
                  </label>
                );
              })}
            </div>
          )}
          {errori.tipi && (
            <p className="mt-1.5 text-sm text-red-600" role="alert">
              {errori.tipi}
            </p>
          )}
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
        {salva.isError && (
          <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {apiErrorMessage(salva.error)}
          </p>
        )}
      </form>
    </Dialog>
  );
}
