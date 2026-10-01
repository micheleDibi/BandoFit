import { CalendarPlus } from "lucide-react";
import type { ReactNode } from "react";
import { StatoBadge } from "../../../components/bandi/badges";
import { BandoRow, BandoRowSkeleton } from "../../../components/bandi/BandoRow";
import { BandoTestata } from "../../../components/bandi/BandoTestata";
import { NotaStatoBando } from "../../../components/bandi/NotaStato";
import { Button } from "../../../components/ui/Button";
import { tempoRelativo } from "../../../components/ui/Due";
import { formatDate, formatEur, toLocalIsoDate } from "../../../lib/format";
import type { BandoListItem } from "../../../types";

/** Sezione della vetrina (solo in sviluppo): il registro dei bandi e
 *  l'intestazione della scheda con dati finti, senza rete. Il segnalibro delle
 *  righe legge i bandi salvati solo con una sessione: qui resta vuoto. */
export const titolo = "Catalogo";

function Blocco({ titolo, nota, children }: { titolo: string; nota?: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="border-b border-line pb-3">
        <h3 className="text-title-section text-ink">{titolo}</h3>
        {nota && <p className="text-small text-ink-3">{nota}</p>}
      </div>
      {children}
    </section>
  );
}

/** Data ISO a N giorni da oggi (data locale di calendario). */
function traGiorni(n: number): string {
  return toLocalIsoDate(new Date(Date.now() + n * 86_400_000));
}

const REGIONE = { id: 1, nome: "Piemonte" };

function finto(sovrascrivi: Partial<BandoListItem> & { id: number }): BandoListItem {
  return {
    slug: `bando-finto-${sovrascrivi.id}`,
    titolo: "Bando finto",
    titolo_breve: null,
    descrizione_breve: null,
    stato_bando: "aperto",
    stato_effettivo: "aperto",
    livello: null,
    data_pubblicazione: traGiorni(-10),
    data_apertura: traGiorni(-5),
    data_scadenza: traGiorni(31),
    importo_totale_eur: null,
    importo_max_per_progetto_eur: null,
    ente_erogatore: "Regione Piemonte",
    tipologia: { id: 1, nome: "Fondo perduto" },
    modalita_erogazione: null,
    regioni: [REGIONE],
    compatibilita: null,
    ...sovrascrivi,
  };
}

const BANDI: BandoListItem[] = [
  finto({
    id: 1,
    titolo: "Contributi Piemonte 2026 per Società di Mutuo Soccorso storiche",
    descrizione_breve:
      "La Regione Piemonte finanzia con 200.000 euro a fondo perduto interventi di ristrutturazione, arredi e restauro.",
    importo_totale_eur: 200000,
    compatibilita: { punteggio: 50, matched: 2, totale: 4 },
  }),
  finto({
    id: 2,
    titolo: "Voucher fino a 3.500 euro per editori piemontesi al Salone del libro 2026",
    descrizione_breve:
      "La Regione Piemonte finanzia con voucher a fondo perduto la partecipazione delle micro e piccole imprese.",
    data_scadenza: traGiorni(5),
    importo_max_per_progetto_eur: 3500,
    compatibilita: { punteggio: 75, matched: 3, totale: 4 },
  }),
  finto({
    id: 3,
    titolo: "Contributi per la conservazione e la digitalizzazione degli archivi piemontesi",
    descrizione_breve:
      "La Regione Piemonte finanzia a fondo perduto interventi di conservazione, catalogazione e digitalizzazione.",
    stato_bando: "chiuso",
    stato_effettivo: "chiuso",
    data_scadenza: traGiorni(-1),
    importo_totale_eur: 150000,
    compatibilita: { punteggio: 75, matched: 3, totale: 4 },
  }),
  finto({
    id: 4,
    titolo: "Contributi a fondo perduto per gli ecomusei del Piemonte nel 2026",
    descrizione_breve:
      "La Regione Piemonte sostiene i programmi di attività degli ecomusei riconosciuti.",
    stato_bando: "in apertura prossimamente",
    stato_effettivo: "in apertura prossimamente",
    data_scadenza: null,
    data_apertura: null,
    ente_erogatore: "Regione Piemonte, Settore Valorizzazione del patrimonio",
  }),
];

/** Gli stati del giro 3: sospeso, revocato e i due «da verificare». */
const BANDI_ALTRI_STATI: BandoListItem[] = [
  finto({
    id: 5,
    titolo: "Contributi per l'efficienza energetica delle piccole imprese",
    descrizione_breve: "Sportello sospeso dall'ente: può riaprire o chiudersi.",
    stato_bando: "sospeso",
    stato_effettivo: "sospeso",
    data_scadenza: traGiorni(5),
    importo_totale_eur: 500000,
  }),
  finto({
    id: 6,
    titolo: "Voucher per la digitalizzazione delle botteghe artigiane",
    descrizione_breve: "Bando revocato dall'ente.",
    stato_bando: "revocato",
    stato_effettivo: "revocato",
    data_scadenza: traGiorni(20),
  }),
  finto({
    id: 7,
    titolo: "Contributi per l'internazionalizzazione delle PMI piemontesi",
    descrizione_breve: "Aperto, ma la pagina ufficiale indica uno stato diverso.",
    stato_da_verificare: "smentito_dalla_fonte",
    data_scadenza: traGiorni(12),
    importo_max_per_progetto_eur: 40000,
  }),
  finto({
    id: 8,
    titolo: "Bando per le start-up innovative a vocazione sociale",
    descrizione_breve: "In apertura, ma la data di apertura è passata senza conferma.",
    stato_bando: "in apertura prossimamente",
    stato_effettivo: "in apertura prossimamente",
    stato_da_verificare: "data_apertura_passata",
    data_apertura: traGiorni(-2),
    data_scadenza: traGiorni(60),
  }),
];

const SCADENZA = traGiorni(31);

export default function Catalogo() {
  return (
    <div className="flex flex-col gap-12">
      <Blocco
        titolo="BandoRow"
        nota="Aperto con dotazione; in scadenza entro 7 giorni con importo per impresa; chiuso; in apertura senza importo né scadenza."
      >
        <ul className="flex flex-col border-t border-line">
          {BANDI.map((bando) => (
            <BandoRow key={bando.id} bando={bando} />
          ))}
        </ul>
      </Blocco>

      <Blocco
        titolo="Stati del bando"
        nota="Un solo stato per riga. Sospeso in ambra, Revocato come chiuso (senza conto alla rovescia); «da verificare» non cambia lo stato."
      >
        <div className="flex flex-wrap items-center gap-3">
          <StatoBadge stato="aperto" />
          <StatoBadge stato="aperto" daVerificare="senza_conferma" />
          <StatoBadge stato="in apertura prossimamente" />
          <StatoBadge stato="in apertura prossimamente" daVerificare="previsione_scaduta" />
          <StatoBadge stato="chiuso" />
          <StatoBadge stato="sospeso" />
          <StatoBadge stato="revocato" />
          <StatoBadge stato="chiuso" daVerificare="termine_passato" />
        </div>
        <ul className="flex flex-col border-t border-line">
          {BANDI_ALTRI_STATI.map((bando) => (
            <BandoRow key={bando.id} bando={bando} />
          ))}
        </ul>
      </Blocco>

      <Blocco
        titolo="NotaStatoBando"
        nota="Sotto la fascia della scheda: sospeso, revocato, aperto con un motivo da verificare. Il chiuso tiene la nota generica nella fascia."
      >
        <div className="flex flex-col gap-3">
          <NotaStatoBando stato="sospeso" />
          <NotaStatoBando stato="revocato" />
          <NotaStatoBando stato="aperto" daVerificare="data_apertura_passata" />
        </div>
      </Blocco>

      <Blocco titolo="BandoRowSkeleton" nota="Tre righe su sunken mentre arrivano i dati.">
        <ul className="flex flex-col border-t border-line">
          {Array.from({ length: 3 }).map((_, i) => (
            <BandoRowSkeleton key={i} />
          ))}
        </ul>
      </Blocco>

      <Blocco
        titolo="BandoTestata"
        nota="Intestazione della scheda: ritorno, stato e tipologia, titolo, ente, «Salva il bando» e «Vai al bando» (unico pieno), fatti chiave con l'azione del calendario."
      >
        <BandoTestata
          indietro={{ label: "Bandi", to: "/app/bandi" }}
          titolo="Contributi Piemonte 2026 per Società di Mutuo Soccorso storiche"
          stato="aperto"
          tipologia="Bando regionale"
          modalita="Fondo perduto"
          ente="Regione Piemonte"
          cta={{
            url: "https://example.org/bando",
            host: "bandi.regione.piemonte.it",
            origine: "fonte_ufficiale",
          }}
          azioni={
            <Button type="button" variant="secondary" className="w-full sm:w-auto">
              Salva il bando
            </Button>
          }
          fatti={[
            {
              etichetta: "Scadenza",
              valore: `${formatDate(SCADENZA)}, ore 12:00`,
              nota: tempoRelativo(SCADENZA),
            },
            { etichetta: "Dotazione", valore: formatEur(200000) },
            { etichetta: "Apertura", valore: formatDate(traGiorni(-5)) },
          ]}
          azioneFatti={
            <Button type="button" variant="ghost" size="sm">
              <CalendarPlus className="size-4" aria-hidden />
              Aggiungi la scadenza al calendario
            </Button>
          }
        />
      </Blocco>
    </div>
  );
}
