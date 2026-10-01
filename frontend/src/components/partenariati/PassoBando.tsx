import { FileText } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useBando } from "../../hooks/useBandi";
import { useCompany } from "../../hooks/useCompany";
import { useAggiornaCall, useCreaCall, useMieCall } from "../../hooks/useCallPartenariato";
import { useDebounce } from "../../hooks/useDebounce";
import { identitaVerificata, useIdentitaAzienda } from "../../hooks/useIdentitaAzienda";
import { usePartenariatiVocabolario } from "../../hooks/usePartenariatiVocabolario";
import { analisiInCorso, usePartenariatoBando } from "../../hooks/usePartenariatoBando";
import { api, apiErrorCode, apiErrorMessage } from "../../lib/api";
import { CALL_COPY, PARTENARIATO_COPY, PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type {
  BandoListItem,
  FormaPrevistaCall,
  ModalitaPartenariato,
  Page,
  RuoloCreatoreCall,
} from "../../types";
import { StatoBadge } from "../bandi/badges";
import { dataConOra, statoDelBando } from "../bandi/stato";
import { Alert } from "../ui/Alert";
import { Button, LinkButton } from "../ui/Button";
import { SelectField } from "../ui/Field";
import { InlineError } from "../ui/InlineError";
import { Panel } from "../ui/Panel";
import { SearchInput } from "../ui/SearchInput";
import { Spinner } from "../ui/Spinner";
import { Skeleton } from "../ui/states";
import { TextLink } from "../ui/TextLink";
import { BarraPasso } from "./CallStepper";
import { callAperta, bandoAperto, LIMITI_CALL, linkCall } from "./callDati";
import { SceltaRadio, TestoLungo } from "./CampiCall";
import { ANCORA_IDENTITA } from "./IdentitaAziendaBox";
import { AvvisoLimiteCall, statoLimite, useLimiteCall } from "./LimitiCall";
import { ModalitaBadge } from "./ModalitaBadge";
import { avanzamento, soloCambiati, vuoto, type PassoProps } from "./passoComune";

const RUOLI: RuoloCreatoreCall[] = ["capofila", "cerco_capofila"];
const NOTE_RUOLO: Record<RuoloCreatoreCall, string> = {
  capofila: "Guidi il progetto e presenti la domanda: cerchi le aziende che completano il partenariato.",
  cerco_capofila: "Vuoi partecipare come partner: cerchi chi guidi il progetto e presenti la domanda.",
};

/** Nota su come la call compare alle altre aziende: anonima (default) o, dal
 *  WP9, con il nome del Registro Imprese finché l'identità dell'azienda resta
 *  verificata (lo decide il server a ogni lettura). Testo da `CALL_COPY`. */
export function NotaAnonima({ anonima = true }: { anonima?: boolean }) {
  return <Alert tono="info">{anonima ? CALL_COPY.notaAnonima : CALL_COPY.notaNominativa}</Alert>;
}

type SceltaNome = "anonima" | "nome";

/** Anonima o con il nome (WP9, decisione di Michele): la scelta c'è solo se
 *  l'azienda ha l'identità verificata dalla piattaforma (il server rifiuta le
 *  altre, 409 `identita_non_verificata_admin`); altrimenti la call è anonima,
 *  con il link per chiedere la verifica. Una call rimasta «con il nome» senza
 *  verifica (per esempio dopo una revoca) si può solo rendere anonima. */
function SceltaNomeCall({
  anonima,
  onChange,
  disabled,
}: {
  anonima: boolean;
  onChange: (anonima: boolean) => void;
  disabled: boolean;
}) {
  const nome = useId();
  const { data: identita } = useIdentitaAzienda();
  const verificata = identitaVerificata(identita);
  if (!verificata) {
    return (
      <div className="flex flex-col gap-2">
        <NotaAnonima />
        {identita && (
          <p className="text-small text-ink-3">
            {CALL_COPY.nomeNonDisponibile}{" "}
            <TextLink to={`/app/azienda#${ANCORA_IDENTITA}`}>{PARTNER_COPY.chiediVerifica}</TextLink>
          </p>
        )}
        {!anonima && (
          <Alert
            tono="attenzione"
            azione={
              <Button variant="secondary" size="sm" onClick={() => onChange(true)} disabled={disabled}>
                Rendi anonima
              </Button>
            }
          >
            {CALL_COPY.nomeSenzaVerifica}
          </Alert>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <SceltaRadio<SceltaNome>
        legenda={CALL_COPY.sceltaNomeTitolo}
        nome={nome}
        valore={anonima ? "anonima" : "nome"}
        onChange={(v) => onChange(v === "anonima")}
        disabled={disabled}
        opzioni={[
          { valore: "anonima", etichetta: CALL_COPY.sceltaAnonima, nota: CALL_COPY.sceltaAnonimaNota },
          { valore: "nome", etichetta: CALL_COPY.sceltaNome, nota: CALL_COPY.sceltaNomeNota },
        ]}
      />
      <NotaAnonima anonima={anonima} />
    </div>
  );
}

/** Scheda sintetica del bando e modalità di partecipazione dalle regole. */
function SchedaBando({
  titolo,
  scadenza,
  oraScadenza,
  stato,
  slug,
}: {
  titolo: string;
  scadenza: string | null;
  oraScadenza?: string | null;
  stato: string | null;
  slug: string;
}) {
  const regole = usePartenariatoBando(slug);
  const modalita: ModalitaPartenariato | null = regole.data?.regole?.modalita_effettiva ?? null;
  return (
    // Dentro la card del passo: riquadro incassato (`desk`), senza ombra.
    <Panel titolo="Bando" icon={FileText} area="bandi" className="bg-desk shadow-none">
      <p className="font-medium text-ink">{titolo}</p>
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
        {scadenza && <span>Scade il {dataConOra(scadenza, oraScadenza)}</span>}
        <StatoBadge stato={stato} />
      </p>
      <div aria-live="polite">
        {regole.isPending ? (
          <Skeleton className="h-5 w-40" />
        ) : regole.data && analisiInCorso(regole.data) && !regole.data.regole ? (
          <p className="inline-flex items-center gap-2 text-small text-ink-2">
            <Spinner size="sm" />
            Stiamo leggendo le regole di partenariato del bando…
          </p>
        ) : modalita ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-small text-ink-2">
            <ModalitaBadge modalita={modalita} />
            <span>{PARTENARIATO_COPY.modalitaSpiegazione[modalita]}</span>
          </div>
        ) : (
          <p className="text-small text-ink-3">
            Le regole di partenariato del bando non sono ancora state lette: le vedrai al passo
            successivo.
          </p>
        )}
      </div>
    </Panel>
  );
}

/** Ruolo, forma prevista e (se il bando dice «non ammesso») motivo. */
function DatiCall({
  ruolo,
  onRuolo,
  forma,
  onForma,
  override,
  onOverride,
  mostraOverride,
  disabled,
  erroreOverride,
}: {
  ruolo: RuoloCreatoreCall;
  onRuolo: (r: RuoloCreatoreCall) => void;
  forma: FormaPrevistaCall | "";
  onForma: (f: FormaPrevistaCall | "") => void;
  override: string;
  onOverride: (v: string) => void;
  mostraOverride: boolean;
  disabled: boolean;
  erroreOverride?: string;
}) {
  const idForma = useId();
  const { data: vocabolario } = usePartenariatiVocabolario();
  const forme = (vocabolario?.forme ?? []).filter(
    (f): f is typeof f & { codice: FormaPrevistaCall } => f.codice !== "altra",
  );
  return (
    <div className="flex flex-col gap-6">
      <SceltaRadio
        legenda="Il tuo ruolo"
        nome={`${idForma}-ruolo`}
        valore={ruolo}
        onChange={onRuolo}
        disabled={disabled}
        opzioni={RUOLI.map((r) => ({
          valore: r,
          etichetta: CALL_COPY.ruoliCreatore[r],
          nota: NOTE_RUOLO[r],
        }))}
      />
      <div className="max-w-md">
        <SelectField
          id={idForma}
          label="Forma di aggregazione prevista (facoltativa)"
          value={forma}
          disabled={disabled}
          onChange={(e) => onForma(e.target.value as FormaPrevistaCall | "")}
        >
          <option value="">Non ancora decisa</option>
          {forme.map((f) => (
            <option key={f.codice} value={f.codice}>
              {f.etichetta}
            </option>
          ))}
        </SelectField>
      </div>
      {mostraOverride && (
        <div className="flex flex-col gap-3">
          <Alert tono="attenzione">
            Dalle regole che abbiamo letto, questo bando non ammette partenariati. Se sei sicuro che
            li ammetta, spiega perché: la call parte sotto la tua responsabilità.
          </Alert>
          <TestoLungo
            etichetta={`Perché il bando ammette il partenariato (almeno ${LIMITI_CALL.overrideMin} caratteri)`}
            valore={override}
            onChange={onOverride}
            massimo={LIMITI_CALL.overrideMax}
            righe={3}
            disabled={disabled}
            errore={erroreOverride}
            required
          />
        </div>
      )}
    </div>
  );
}

/** Ricerca del bando per chi non arriva dalla scheda di un bando. */
function RicercaBando({ onScegli }: { onScegli: (slug: string) => void }) {
  const id = useId();
  const [testo, setTesto] = useState("");
  const q = useDebounce(testo.trim(), 400);
  const ricerca = useQuery({
    queryKey: ["bandi", "ricerca-call", q],
    queryFn: async () =>
      (await api.get<Page<BandoListItem>>("/bandi", { params: { q, page: 1, page_size: 8 } }))
        .data,
    enabled: q.length >= 3,
    staleTime: 60_000,
  });
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <p className="text-small font-medium text-ink">Per quale bando cerchi partner?</p>
        <p id={`${id}-aiuto`} className="text-small text-ink-3">
          Scrivi almeno 3 lettere del titolo. Puoi partire anche dalla scheda di un bando, con «Crea
          call».
        </p>
        <div className="max-w-xl">
          <SearchInput
            label="Cerca il bando"
            value={testo}
            onChange={setTesto}
            placeholder="Es. innovazione digitale"
            aria-describedby={`${id}-aiuto`}
          />
        </div>
      </div>
      <div aria-live="polite">
        {q.length < 3 ? null : ricerca.isPending ? (
          <div className="flex flex-col gap-2" aria-hidden>
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : ricerca.isError ? (
          <InlineError>{apiErrorMessage(ricerca.error, "Impossibile cercare i bandi.")}</InlineError>
        ) : (ricerca.data?.items ?? []).length === 0 ? (
          <p className="text-body text-ink-3">Nessun bando trovato.</p>
        ) : (
          <ul className="flex flex-col border-t border-line">
            {ricerca.data!.items.map((b) => {
              const aperto = bandoAperto(statoDelBando(b));
              return (
                <li key={b.id} className="border-b border-line">
                  <button
                    type="button"
                    disabled={!aperto}
                    onClick={() => onScegli(b.slug)}
                    className="flex w-full cursor-pointer items-start justify-between gap-3 rounded-mark px-2 py-3 text-left text-body hover:bg-sunken disabled:cursor-not-allowed disabled:text-ink-3"
                  >
                    <span className="min-w-0">
                      <span className="block font-medium">{b.titolo_breve || b.titolo || b.slug}</span>
                      {b.data_scadenza && (
                        <span className="text-small text-ink-3">Scade il {formatDate(b.data_scadenza)}</span>
                      )}
                    </span>
                    <span className="shrink-0 text-small text-ink-3">{aperto ? "Scegli" : "Non aperto"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

/** Passo 1 senza call: scelta del bando e creazione della bozza. */
export function PassoBandoNuova({
  slug,
  onScegliBando,
}: {
  slug: string | null;
  onScegliBando: (slug: string | null) => void;
}) {
  const navigate = useNavigate();
  const bando = useBando(slug ?? undefined);
  const regole = usePartenariatoBando(slug ?? undefined);
  const mie = useMieCall();
  const { data: azienda } = useCompany();
  const limite = useLimiteCall();
  const crea = useCreaCall();
  const [ruolo, setRuolo] = useState<RuoloCreatoreCall>("capofila");
  const [forma, setForma] = useState<FormaPrevistaCall | "">("");
  const [anonima, setAnonima] = useState(true);
  const [override, setOverride] = useState("");
  const [serveOverride, setServeOverride] = useState(false);
  const [erroreOverride, setErroreOverride] = useState<string | undefined>();

  const editable = azienda?.editable ?? false;
  const nonAmmesso = regole.data?.regole?.modalita_effettiva === "non_ammesso";
  const mostraOverride = nonAmmesso || serveOverride;
  const esistente = slug
    ? (mie.data?.items ?? []).find((c) => c.mia && c.bando.slug === slug && callAperta(c.stato))
    : undefined;
  const statoPiano = statoLimite(limite);

  if (!slug) return <RicercaBando onScegli={onScegliBando} />;

  if (bando.isPending) {
    return <Skeleton className="h-40 w-full" />;
  }
  if (bando.isError || !bando.data) {
    return (
      <div className="flex flex-col gap-3">
        <Alert tono="errore">{apiErrorMessage(bando.error, "Impossibile caricare il bando.")}</Alert>
        <div>
          <Button variant="secondary" size="sm" onClick={() => onScegliBando(null)}>
            Scegli un altro bando
          </Button>
        </div>
      </div>
    );
  }

  const b = bando.data;
  const aperto = bandoAperto(statoDelBando(b));
  const bloccato = !editable || !aperto || !!esistente || statoPiano === "non_incluso";

  const invia = async () => {
    setErroreOverride(undefined);
    const motivo = override.trim();
    if (mostraOverride && motivo.length < LIMITI_CALL.overrideMin) {
      setErroreOverride(`Scrivi almeno ${LIMITI_CALL.overrideMin} caratteri.`);
      return;
    }
    try {
      const call = await crea.mutateAsync({
        bando_slug: slug,
        ruolo_creatore: ruolo,
        forma_aggregazione_prevista: forma || null,
        override_non_ammesso_motivo: mostraOverride ? motivo : null,
        // «Con il nome» solo se scelto (azienda verificata): il server lo
        // ricontrolla.
        ...(anonima ? {} : { anonima: false }),
      });
      navigate(`/app/partenariati/call/${call.id}/modifica?passo=2`, { replace: true });
    } catch (err) {
      if (apiErrorCode(err) === "partenariato_non_ammesso") setServeOverride(true);
    }
  };

  const codice = crea.isError ? apiErrorCode(crea.error) : undefined;

  return (
    <div className="flex flex-col gap-6">
      <SchedaBando
        titolo={b.titolo || b.slug}
        scadenza={b.data_scadenza}
        oraScadenza={b.ora_scadenza}
        stato={statoDelBando(b)}
        slug={slug}
      />
      <div>
        <Button variant="ghost" size="sm" onClick={() => onScegliBando(null)}>
          Scegli un altro bando
        </Button>
      </div>

      {!editable && <p className="text-small text-ink-3">{CALL_COPY.soloTitolare}</p>}
      {!aperto && <Alert tono="attenzione">Il bando non è aperto: non si possono creare call.</Alert>}
      {esistente && (
        <Alert
          tono="info"
          azione={
            <LinkButton to={linkCall(esistente)} variant="secondary" size="sm">
              Vai alla tua call
            </LinkButton>
          }
        >
          Hai già una call per questo bando: puoi riprenderla da dove eri rimasto.
        </Alert>
      )}
      <AvvisoLimiteCall limite={limite} editable={editable} />

      {!esistente && (
        <>
          <DatiCall
            ruolo={ruolo}
            onRuolo={setRuolo}
            forma={forma}
            onForma={setForma}
            override={override}
            onOverride={setOverride}
            mostraOverride={mostraOverride}
            disabled={bloccato}
            erroreOverride={erroreOverride}
          />
          <SceltaNomeCall anonima={anonima} onChange={setAnonima} disabled={bloccato} />
          <BarraPasso
            onAvanti={() => void invia()}
            etichettaAvanti="Crea la bozza e continua"
            inCorso={crea.isPending}
            disabilitato={bloccato}
            errore={crea.isError ? apiErrorMessage(crea.error) : null}
            nota={
              codice === "call_gia_presente" || codice === "troppe_bozze" ? (
                <LinkButton to="/app/partenariati?tab=mie" variant="secondary" size="sm">
                  Vai alle tue call
                </LinkButton>
              ) : (
                "La bozza non è visibile a nessuno finché non la pubblichi."
              )
            }
          />
        </>
      )}
    </div>
  );
}

/** Passo 1 di una call già creata: il bando non cambia; ruolo, forma e
 *  motivo sì (solo in bozza). */
export function PassoBando({ call, onAvanti, onDirty }: PassoProps) {
  const regole = usePartenariatoBando(call.bando.slug);
  const aggiorna = useAggiornaCall(call.id);
  const [ruolo, setRuolo] = useState<RuoloCreatoreCall>(call.ruolo_creatore);
  const [forma, setForma] = useState<FormaPrevistaCall | "">(call.forma_aggregazione_prevista ?? "");
  const [anonima, setAnonima] = useState(call.anonima);
  const [override, setOverride] = useState(call.override_non_ammesso_motivo ?? "");
  const [erroreOverride, setErroreOverride] = useState<string | undefined>();

  const bozza = call.stato === "bozza";
  const mostraOverride =
    regole.data?.regole?.modalita_effettiva === "non_ammesso" || !!call.override_non_ammesso_motivo;
  const campi = soloCambiati(call, {
    ruolo_creatore: ruolo,
    forma_aggregazione_prevista: forma || null,
    anonima,
    ...(mostraOverride ? { override_non_ammesso_motivo: override.trim() || null } : {}),
  });
  const dirty = bozza && !vuoto(campi);
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  const salva = async () => {
    setErroreOverride(undefined);
    if (!bozza) {
      onAvanti();
      return;
    }
    if (mostraOverride && override.trim().length < LIMITI_CALL.overrideMin) {
      setErroreOverride(`Scrivi almeno ${LIMITI_CALL.overrideMin} caratteri.`);
      return;
    }
    const corpo = { ...campi, ...avanzamento(call, 2) };
    try {
      if (!vuoto(corpo)) await aggiorna.mutateAsync(corpo);
      onAvanti();
    } catch {
      // mostrato nella barra
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <SchedaBando
        titolo={call.bando.titolo}
        scadenza={call.bando.scadenza}
        stato={call.bando.stato_effettivo}
        slug={call.bando.slug}
      />
      {!bozza && (
        <p className="text-small text-ink-3">Dopo la pubblicazione ruolo e forma non si cambiano più.</p>
      )}
      <DatiCall
        ruolo={ruolo}
        onRuolo={setRuolo}
        forma={forma}
        onForma={setForma}
        override={override}
        onOverride={setOverride}
        mostraOverride={mostraOverride}
        disabled={!bozza}
        erroreOverride={erroreOverride}
      />
      {bozza ? (
        <SceltaNomeCall anonima={anonima} onChange={setAnonima} disabled={false} />
      ) : (
        <NotaAnonima anonima={call.anonima} />
      )}
      <BarraPasso
        onAvanti={() => void salva()}
        etichettaAvanti={bozza ? "Salva e continua" : "Continua"}
        inCorso={aggiorna.isPending}
        errore={aggiorna.isError ? apiErrorMessage(aggiorna.error) : null}
      />
    </div>
  );
}
