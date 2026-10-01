import type { ReactNode } from "react";
import { Alert, type AlertTono } from "../components/ui/Alert";
import { Card } from "../components/ui/Card";
import { DefinitionList, type Definizione } from "../components/ui/Facts";
import { Page } from "../components/ui/Page";
import { PageHeader } from "../components/ui/PageHeader";
import { Section, SectionHeader } from "../components/ui/SectionHeader";
import { ErrorState, Skeleton } from "../components/ui/states";
import { Status, type TonoStatus } from "../components/ui/Status";
import { Table, Td, Th } from "../components/ui/Table";
import { useMonitoraggioCatalogo } from "../hooks/useAdminCatalogo";
import { apiErrorMessage } from "../lib/api";
import { formatDateTime } from "../lib/format";
import type {
  BustaMonitoraggio,
  GiroMonitoraggio,
  RiepilogoMonitoraggio,
  SegnaleMonitoraggio,
  StatoAccessoMonitoraggio,
} from "../types/monitoraggio";

/* Pannello admin «Catalogo» (`/app/admin/catalogo`): se la raccolta dei bandi
 * sta funzionando, dal monitoraggio del catalogo. Si mostra solo ciò che arriva:
 * nessun dato ricostruito, nessuna chiave sconosciuta interpretata. Un valore
 * nuovo di un'enumerazione compare com'è, in una pillola neutra; i segnali
 * hanno sempre il loro testo e il loro livello. */

/** Esito della lettura diverso da «ok»: che cosa dire e con quale tono. */
const ACCESSO: Record<Exclude<StatoAccessoMonitoraggio, "ok">, { tono: AlertTono; testo: string }> = {
  non_configurato: { tono: "attenzione", testo: "Monitoraggio non configurato." },
  chiave_non_valida: {
    tono: "errore",
    testo: "Chiave di monitoraggio non valida: va corretta nella configurazione.",
  },
  non_disponibile: { tono: "attenzione", testo: "Interfaccia non ancora disponibile." },
  accesso_db_non_valido: { tono: "errore", testo: "Accesso al catalogo non valido." },
  non_raggiungibile: { tono: "errore", testo: "Monitoraggio non raggiungibile." },
  formato_non_supportato: { tono: "errore", testo: "Formato del monitoraggio non supportato." },
};

type Pillola = { tono: TonoStatus; parola: string };

/** Valore sconosciuto di un'enumerazione: com'è, in una pillola neutra. */
const comArriva = (valore: string | null | undefined): Pillola => ({
  tono: "neutro",
  parola: valore ? leggibile(valore) : "—",
});

const STATO_RIEPILOGO: Record<string, Pillola> = {
  ok: { tono: "aperto", parola: "Regolare" },
  attenzione: { tono: "attenzione", parola: "Attenzione" },
  guasto: { tono: "errore", parola: "Guasto" },
};
const LIVELLO_SEGNALE: Record<string, Pillola> = {
  allarme: { tono: "errore", parola: "Allarme" },
  avviso: { tono: "attenzione", parola: "Avviso" },
};
const SERVIZIO: Record<string, Pillola> = {
  attivo: { tono: "aperto", parola: "Attivo" },
  non_attivo: { tono: "errore", parola: "Non attivo" },
  non_misurato: { tono: "neutro", parola: "Non misurato" },
};
const ESITO_GIRO: Record<string, Pillola> = {
  ok: { tono: "aperto", parola: "Riuscito" },
  errore: { tono: "errore", parola: "Errore" },
  saltato: { tono: "neutro", parola: "Saltato" },
  interrotto_per_tetto: { tono: "attenzione", parola: "Interrotto al limite" },
};
const ESITO_JOB: Record<string, Pillola> = {
  succeeded: { tono: "aperto", parola: "Riuscito" },
  failed: { tono: "errore", parola: "Fallito" },
  non_misurato: { tono: "neutro", parola: "Non misurato" },
};
const NON_MISURATI: Record<string, string> = {
  servizio: "stato del servizio",
  job_orario: "job orario",
  accesso_fonte_riservata: "accesso alla fonte riservata",
  credito_ricerca: "credito di ricerca",
  schede_con_sezione: "schede con sezione",
  da_verificare: "verifica dello stato",
};
const MOTIVI_DA_VERIFICARE: Record<string, string> = {
  data_apertura_passata: "Apertura non confermata",
  smentito_dalla_fonte: "Smentito dalla pagina ufficiale",
  previsione_scaduta: "Apertura prevista passata senza conferme",
  senza_conferma: "Senza conferma recente",
  termine_passato: "Termine non ufficiale passato",
};
/** I due rami di `da_verificare` con i conteggi per motivo. */
const RAMI_DA_VERIFICARE = [
  { chiave: "in_apertura", etichetta: "In apertura" },
  { chiave: "aperto", etichetta: "Aperti" },
] as const;

/** «ricerca_fonti» → «ricerca fonti»: solo per leggerlo, senza interpretarlo. */
function leggibile(codice: string): string {
  return codice.replace(/_/g, " ");
}

/** Solo le chiavi proprie della mappa (mai quelle ereditate da `Object`). */
function voce<T>(mappa: Record<string, T>, chiave: string | null | undefined): T | undefined {
  return chiave && Object.prototype.hasOwnProperty.call(mappa, chiave) ? mappa[chiave] : undefined;
}

function pillola(mappa: Record<string, Pillola>, valore: string | null | undefined): Pillola {
  return voce(mappa, valore) ?? comArriva(valore);
}

/** «1 minuto fa», «15 minuti fa». */
const minutiFa = (n: number) => (n === 1 ? "1 minuto fa" : `${numero(n)} minuti fa`);

function StatoPillola({ p }: { p: Pillola }) {
  return <Status tono={p.tono}>{p.parola}</Status>;
}

const numeroFormatter = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 1 });
const numero = (n: number | null | undefined) =>
  typeof n === "number" && Number.isFinite(n) ? numeroFormatter.format(n) : "—";

/** Coppie motivo → conteggio di un ramo di `da_verificare`: solo numeri, il
 *  resto si ignora (forma libera). */
function conteggi(valore: unknown): Array<[string, number]> {
  if (!valore || typeof valore !== "object" || Array.isArray(valore)) return [];
  return Object.entries(valore).filter(
    (coppia): coppia is [string, number] =>
      typeof coppia[1] === "number" && Number.isFinite(coppia[1]),
  );
}

function Blocco({ titolo, children }: { titolo: string; children: ReactNode }) {
  return (
    <Card className="flex flex-col gap-4">
      <Section>
        <SectionHeader titolo={titolo} />
        {children}
      </Section>
    </Card>
  );
}

function StatoComplessivo({ busta, riepilogo }: { busta: BustaMonitoraggio; riepilogo: RiepilogoMonitoraggio }) {
  const nonMisurati = (riepilogo.non_misurati ?? []).filter((v) => typeof v === "string");
  return (
    <Blocco titolo="Stato complessivo">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <StatoPillola p={pillola(STATO_RIEPILOGO, riepilogo.stato)} />
        <span className="text-small text-ink-2">
          Riepilogo aggiornato il {formatDateTime(busta.aggiornato_at)}
          {typeof busta.minuti_dal_calcolo === "number" && ` (${minutiFa(busta.minuti_dal_calcolo)})`}
        </span>
      </div>
      {busta.orologio_disallineato === true && (
        <p className="text-small text-ink-3">
          L'ora di calcolo del riepilogo non coincide con quella del catalogo: è solo indicativa.
        </p>
      )}
      {nonMisurati.length > 0 && (
        <p className="text-small text-ink-2">
          Non misurati oggi: {nonMisurati.map((v) => voce(NON_MISURATI, v) ?? leggibile(v)).join(", ")}.
        </p>
      )}
    </Blocco>
  );
}

/** Segnali nell'ordine in cui arrivano, con il loro livello e il loro testo
 *  (anche con un codice sconosciuto). Senza segnali e con lo stato diverso da
 *  «ok» (caso anomalo) non si dice «nessun segnale». */
function Segnali({ segnali, statoOk }: { segnali: SegnaleMonitoraggio[]; statoOk: boolean }) {
  return (
    <Blocco titolo="Segnali">
      {segnali.length === 0 ? (
        <p className="text-body text-ink-2">
          {statoOk ? "Nessun segnale acceso." : "Nessun dettaglio disponibile."}
        </p>
      ) : (
        <ul className="flex flex-col">
          {segnali.map((s, i) => (
            <li
              key={`${s.codice ?? ""}-${i}`}
              className="flex flex-col gap-1 border-b border-line py-3 first:pt-0 last:border-b-0 last:pb-0 sm:flex-row sm:items-start sm:gap-4"
            >
              <StatoPillola p={pillola(LIVELLO_SEGNALE, s.livello)} />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-body text-ink">{s.testo || "Segnale senza testo."}</span>
                {s.dal && <span className="text-small text-ink-3">Dal {formatDateTime(s.dal)}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Blocco>
  );
}

function Giri({ giri }: { giri: GiroMonitoraggio[] }) {
  return (
    <Blocco titolo="Ultimi giri">
      {giri.length === 0 ? (
        <p className="text-body text-ink-2">Nessun giro registrato.</p>
      ) : (
        <Table className="min-w-[640px]">
          <caption className="sr-only">Ultimi giri della raccolta dei bandi</caption>
          <thead>
            <tr>
              <Th>Avvio</Th>
              <Th>Giro</Th>
              <Th numerica>Durata</Th>
              <Th>Esito</Th>
              <Th>Passi non riusciti</Th>
            </tr>
          </thead>
          <tbody>
            {giri.map((g, i) => (
              <tr key={`${g.id ?? ""}-${i}`}>
                <Td>{formatDateTime(g.avviato_at)}</Td>
                <Td>{etichettaGiro(g.giro)}</Td>
                <Td numerica>{typeof g.durata_min === "number" ? `${numero(g.durata_min)} min` : "—"}</Td>
                <Td>
                  <StatoPillola p={pillola(ESITO_GIRO, g.esito)} />
                </Td>
                <Td>
                  {(g.passi_non_ok ?? []).length > 0
                    ? (g.passi_non_ok ?? []).map(leggibile).join(", ")
                    : "—"}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Blocco>
  );
}

/** «00», «06»… → «Ore 06»; avvio e manuale in parole; altro com'è. */
function etichettaGiro(giro: string | null | undefined): string {
  if (!giro) return "—";
  if (/^\d{2}$/.test(giro)) return `Ore ${giro}`;
  if (giro === "avvio") return "All'avvio";
  if (giro === "manuale") return "Manuale";
  return leggibile(giro);
}

function DaVerificare({ valore }: { valore: Record<string, unknown> | null | undefined }) {
  if (!valore) {
    return (
      <Blocco titolo="Verifica dello stato">
        <p className="text-body text-ink-2">Verifica dello stato non ancora attiva.</p>
      </Blocco>
    );
  }
  const rami = RAMI_DA_VERIFICARE.map((r) => ({ ...r, voci: new Map(conteggi(valore[r.chiave])) }));
  const motivi = [...new Set(rami.flatMap((r) => [...r.voci.keys()]))];
  return (
    <Blocco titolo="Verifica dello stato">
      {motivi.length === 0 ? (
        <p className="text-body text-ink-2">Nessun bando da verificare.</p>
      ) : (
        <Table>
          <caption className="sr-only">Bandi da verificare per motivo</caption>
          <thead>
            <tr>
              <Th>Motivo</Th>
              {rami.map((r) => (
                <Th key={r.chiave} numerica>
                  {r.etichetta}
                </Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {motivi.map((m) => (
              <tr key={m}>
                <Td>{voce(MOTIVI_DA_VERIFICARE, m) ?? leggibile(m)}</Td>
                {rami.map((r) => (
                  <Td key={r.chiave} numerica>
                    {numero(r.voci.get(m) ?? 0)}
                  </Td>
                ))}
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Blocco>
  );
}

function Riepilogo({ busta, riepilogo }: { busta: BustaMonitoraggio; riepilogo: RiepilogoMonitoraggio }) {
  const produttore = riepilogo.produttore;
  const job = riepilogo.job_orario;
  const ingresso = riepilogo.ingresso;
  const eventi = riepilogo.eventi;

  const raccolta: Definizione[] = [
    {
      etichetta: "Ultimo giro",
      valore: formatDateTime(produttore?.ultimo_giro_at),
      nota:
        typeof produttore?.ore_dall_ultimo_giro === "number"
          ? produttore.ore_dall_ultimo_giro === 1
            ? "1 ora fa"
            : `${numero(produttore.ore_dall_ultimo_giro)} ore fa`
          : undefined,
    },
    { etichetta: "Giri nelle ultime 24 ore", valore: numero(produttore?.giri_24h) },
    { etichetta: "Riavvii nelle ultime 24 ore", valore: numero(produttore?.riavvii_24h) },
    { etichetta: "Servizio", valore: <StatoPillola p={pillola(SERVIZIO, produttore?.servizio)} /> },
  ];
  const jobOrario: Definizione[] = [
    { etichetta: "Ultimo avvio", valore: formatDateTime(job?.ultimo_avvio_at) },
    { etichetta: "Ultimo esito", valore: <StatoPillola p={pillola(ESITO_JOB, job?.ultimo_esito)} /> },
    { etichetta: "Ultimo riuscito", valore: formatDateTime(job?.ultimo_ok_at) },
    { etichetta: "Falliti nelle ultime 24 ore", valore: numero(job?.falliti_24h) },
  ];
  const ingressoEventi: Definizione[] = [
    { etichetta: "Fermi in ingresso", valore: numero(ingresso?.fermi_in_ingresso) },
    { etichetta: "Fermi in lavorazione", valore: numero(ingresso?.fermi_in_lavorazione) },
    { etichetta: "Ultimo bando nuovo", valore: formatDateTime(ingresso?.ultimo_bando_nuovo_at) },
    { etichetta: "Eventi ammessi non applicati", valore: numero(eventi?.ammessi_non_applicati) },
    { etichetta: "Eventi proposti negli ultimi 7 giorni", valore: numero(eventi?.proposte_7g) },
    { etichetta: "Eventi in attesa di pubblicazione", valore: numero(eventi?.in_attesa_pubblicazione) },
  ];

  return (
    <>
      <StatoComplessivo busta={busta} riepilogo={riepilogo} />
      <Segnali
        segnali={Array.isArray(riepilogo.segnali) ? riepilogo.segnali : []}
        statoOk={riepilogo.stato === "ok"}
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Blocco titolo="Raccolta dei bandi">
          <DefinitionList items={raccolta} />
        </Blocco>
        <Blocco titolo="Job orario">
          <DefinitionList items={jobOrario} />
        </Blocco>
        <Blocco titolo="Ingresso ed eventi">
          <DefinitionList items={ingressoEventi} />
        </Blocco>
        <DaVerificare valore={riepilogo.da_verificare} />
      </div>
      <Giri giri={Array.isArray(riepilogo.giri) ? riepilogo.giri : []} />
    </>
  );
}

/** Banner sopra tutto il resto: riepilogo più vecchio della soglia o assente. */
function BannerRitardo({ busta }: { busta: BustaMonitoraggio }) {
  return (
    <Alert tono="attenzione" titolo="Dati non aggiornati">
      {typeof busta.minuti_dal_calcolo === "number"
        ? `L'ultimo riepilogo è di ${minutiFa(busta.minuti_dal_calcolo)}: quello che segue potrebbe non essere attuale.`
        : "Il riepilogo non è ancora disponibile."}
    </Alert>
  );
}

function Caricamento() {
  return (
    <div className="flex flex-col gap-6" aria-hidden>
      <Skeleton className="h-28 w-full rounded-panel" />
      <Skeleton className="h-40 w-full rounded-panel" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Skeleton className="h-48 w-full rounded-panel" />
        <Skeleton className="h-48 w-full rounded-panel" />
      </div>
    </div>
  );
}

/** Pannello admin «Catalogo»: stato della raccolta dei bandi, segnali, giri,
 *  job orario, ingresso ed eventi, verifica dello stato. Si rilegge ogni
 *  minuto (il riepilogo cambia ogni 15 minuti circa). */
export default function AdminCatalogo() {
  const q = useMonitoraggioCatalogo();
  const dati = q.data;

  let contenuto: ReactNode;
  if (q.isPending) {
    contenuto = <Caricamento />;
  } else if (!dati) {
    contenuto = (
      <ErrorState
        message={apiErrorMessage(q.error, "Impossibile leggere il monitoraggio del catalogo.")}
        onRetry={() => void q.refetch()}
      />
    );
  } else {
    // Una busta di un'altra versione non si interpreta.
    const busta = dati.stato_accesso === "ok" && dati.busta?.versione === 1 ? dati.busta : null;
    const riepilogo = busta?.riepilogo ?? null;
    const accesso =
      dati.stato_accesso === "ok"
        ? busta
          ? null
          : ACCESSO.formato_non_supportato
        : (voce(ACCESSO, dati.stato_accesso) ?? {
            tono: "attenzione" as const,
            testo: "Stato del monitoraggio non riconosciuto.",
          });
    contenuto = (
      <>
        {busta?.in_ritardo === true && <BannerRitardo busta={busta} />}
        {/* Una rilettura non riuscita lascia in vista l'ultima riuscita. */}
        {q.isError && (
          <Alert tono="attenzione">
            Aggiornamento non riuscito: i dati sono quelli dell'ultima lettura riuscita.
          </Alert>
        )}
        {accesso && <Alert tono={accesso.tono}>{accesso.testo}</Alert>}
        {busta &&
          (riepilogo ? (
            <Riepilogo busta={busta} riepilogo={riepilogo} />
          ) : (
            <Card>
              <p className="text-body text-ink-2">Il primo riepilogo non è ancora stato calcolato.</p>
            </Card>
          ))}
        <p className="text-small text-ink-3">
          Letto il {formatDateTime(dati.letto_at)}. Si aggiorna ogni minuto.
        </p>
      </>
    );
  }

  return (
    <Page variante="elenco">
      <PageHeader
        titolo="Catalogo"
        descrizione="Se la raccolta dei bandi sta funzionando: stato, segnali, giri e verifica dello stato."
        area="admin"
      />
      {contenuto}
    </Page>
  );
}
