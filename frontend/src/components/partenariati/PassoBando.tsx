import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, Eye, EyeOff, Loader2, Search } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
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
import { Button, LinkButton } from "../ui/Button";
import { Card } from "../ui/Card";
import { Skeleton } from "../ui/states";
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
 *  verificata (lo decide il server a ogni lettura). */
export function NotaAnonima({ anonima = true }: { anonima?: boolean }) {
  const Icona = anonima ? EyeOff : Eye;
  return (
    <p
      role="note"
      className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600"
    >
      <Icona className="mt-0.5 size-4 shrink-0 text-slate-400" aria-hidden />
      {anonima ? CALL_COPY.notaAnonima : CALL_COPY.notaNominativa}
    </p>
  );
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
      <div className="space-y-2">
        <NotaAnonima />
        {identita && (
          <p className="px-1 text-xs text-slate-500">
            {CALL_COPY.nomeNonDisponibile}{" "}
            <Link
              to={`/app/azienda#${ANCORA_IDENTITA}`}
              className="font-medium text-brand-600 hover:text-brand-700"
            >
              {PARTNER_COPY.chiediVerifica} →
            </Link>
          </p>
        )}
        {!anonima && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <p className="min-w-0 flex-1">{CALL_COPY.nomeSenzaVerifica}</p>
            <Button variant="secondary" size="sm" onClick={() => onChange(true)} disabled={disabled}>
              Rendi anonima
            </Button>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-2">
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
    <Card className="p-5">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Bando</p>
      <p className="mt-1 font-display text-base font-semibold text-slate-900">{titolo}</p>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-600">
        {scadenza && (
          <span className="inline-flex items-center gap-1">
            <CalendarClock className="size-4 text-slate-400" aria-hidden />
            Scade il {dataConOra(scadenza, oraScadenza)}
          </span>
        )}
        <StatoBadge stato={stato} />
      </p>
      <div className="mt-3" aria-live="polite">
        {regole.isPending ? (
          <Skeleton className="h-5 w-40" />
        ) : regole.data && analisiInCorso(regole.data) && !regole.data.regole ? (
          <p className="inline-flex items-center gap-1.5 text-sm text-amber-700">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Stiamo leggendo le regole di partenariato del bando…
          </p>
        ) : modalita ? (
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <ModalitaBadge modalita={modalita} />
            <span>{PARTENARIATO_COPY.modalitaSpiegazione[modalita]}</span>
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            Le regole di partenariato del bando non sono ancora state lette: le vedrai al passo
            successivo.
          </p>
        )}
      </div>
    </Card>
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
    <div className="space-y-5">
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
      <div className="space-y-1.5">
        <label htmlFor={idForma} className="block text-sm font-medium text-slate-700">
          Forma di aggregazione prevista (facoltativa)
        </label>
        <select
          id={idForma}
          value={forma}
          disabled={disabled}
          onChange={(e) => onForma(e.target.value as FormaPrevistaCall | "")}
          className="h-10 w-full max-w-md cursor-pointer rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-900 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30 disabled:cursor-not-allowed disabled:bg-slate-50"
        >
          <option value="">Non ancora decisa</option>
          {forme.map((f) => (
            <option key={f.codice} value={f.codice}>
              {f.etichetta}
            </option>
          ))}
        </select>
      </div>
      {mostraOverride && (
        <div className="space-y-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="flex items-start gap-2 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            Dalle regole che abbiamo letto, questo bando non ammette partenariati. Se sei sicuro che
            li ammetta, spiega perché: la call parte sotto la tua responsabilità.
          </p>
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
    <Card className="p-5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">
        Per quale bando cerchi partner?
      </label>
      <p id={`${id}-aiuto`} className="mt-0.5 text-xs text-slate-500">
        Scrivi almeno 3 lettere del titolo. Puoi partire anche dalla scheda di un bando, con «Crea
        call».
      </p>
      <div className="relative mt-2 max-w-xl">
        <Search
          className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
          aria-hidden
        />
        <input
          id={id}
          type="search"
          value={testo}
          onChange={(e) => setTesto(e.target.value)}
          aria-describedby={`${id}-aiuto`}
          placeholder="Es. innovazione digitale"
          className="h-10 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
        />
      </div>
      <div className="mt-3" aria-live="polite">
        {q.length < 3 ? null : ricerca.isPending ? (
          <div className="space-y-2" aria-hidden>
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : ricerca.isError ? (
          <p className="text-sm text-red-700" role="alert">
            {apiErrorMessage(ricerca.error, "Impossibile cercare i bandi.")}
          </p>
        ) : (ricerca.data?.items ?? []).length === 0 ? (
          <p className="text-sm text-slate-500">Nessun bando trovato.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
            {ricerca.data!.items.map((b) => {
              const aperto = bandoAperto(statoDelBando(b));
              return (
                <li key={b.id}>
                  <button
                    type="button"
                    disabled={!aperto}
                    onClick={() => onScegli(b.slug)}
                    className="flex w-full cursor-pointer items-start justify-between gap-3 px-3.5 py-2.5 text-left text-sm hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <span className="min-w-0">
                      <span className="block font-medium text-slate-800">
                        {b.titolo_breve || b.titolo || b.slug}
                      </span>
                      {b.data_scadenza && (
                        <span className="text-xs text-slate-500">
                          Scade il {formatDate(b.data_scadenza)}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs text-slate-500">
                      {aperto ? "Scegli" : "Non aperto"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
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
      <Card className="p-5">
        <p className="text-sm text-red-700" role="alert">
          {apiErrorMessage(bando.error, "Impossibile caricare il bando.")}
        </p>
        <Button variant="secondary" size="sm" className="mt-3" onClick={() => onScegliBando(null)}>
          Scegli un altro bando
        </Button>
      </Card>
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
    <div className="space-y-4">
      <SchedaBando
        titolo={b.titolo || b.slug}
        scadenza={b.data_scadenza}
        oraScadenza={b.ora_scadenza}
        stato={statoDelBando(b)}
        slug={slug}
      />
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" size="sm" onClick={() => onScegliBando(null)}>
          Scegli un altro bando
        </Button>
      </div>

      {!editable && (
        <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">{CALL_COPY.soloTitolare}</p>
      )}
      {!aperto && (
        <p className="rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800" role="status">
          Il bando non è aperto: non si possono creare call.
        </p>
      )}
      {esistente && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-900">
          <p>Hai già una call per questo bando: puoi riprenderla da dove eri rimasto.</p>
          <LinkButton to={linkCall(esistente)} size="sm">
            Vai alla tua call
          </LinkButton>
        </div>
      )}
      <AvvisoLimiteCall limite={limite} editable={editable} />

      {!esistente && (
        <Card className="space-y-5 p-5">
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
                <LinkButton to="/app/partenariati?vista=mie" variant="secondary" size="sm">
                  Vai alle tue call
                </LinkButton>
              ) : (
                "La bozza non è visibile a nessuno finché non la pubblichi."
              )
            }
          />
        </Card>
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
    <div className="space-y-4">
      <SchedaBando
        titolo={call.bando.titolo}
        scadenza={call.bando.scadenza}
        stato={call.bando.stato_effettivo}
        slug={call.bando.slug}
      />
      <Card className="space-y-5 p-5">
        {!bozza && (
          <p className="text-sm text-slate-500">
            Dopo la pubblicazione ruolo e forma non si cambiano più.
          </p>
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
      </Card>
    </div>
  );
}
