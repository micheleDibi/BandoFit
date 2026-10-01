import { useQueryClient } from "@tanstack/react-query";
import { Download, Eye, EyeOff, Handshake } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useCompany } from "../../hooks/useCompany";
import { useFunzioni } from "../../hooks/useFunzioni";
import {
  bozzaInCorsoRecente,
  PARTNER_PROFILE_ROOT,
  useConsensoPartner,
  usePartnerProfile,
} from "../../hooks/usePartnerProfile";
import { apiErrorCode, apiErrorMessage } from "../../lib/api";
import { PARTNER_COPY } from "../../lib/copy";
import { formatDate } from "../../lib/format";
import type { PartnerProfile, PartnerProfileInput } from "../../types";
import { Alert } from "../ui/Alert";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Panel } from "../ui/Panel";
import { ProgressBar } from "../ui/ProgressBar";
import { Section, SectionHeader } from "../ui/SectionHeader";
import { Spinner } from "../ui/Spinner";
import { EmptyState, ErrorState, Skeleton } from "../ui/states";
import { Status } from "../ui/Status";
import { TextLink } from "../ui/TextLink";
import { AnteprimaPartnerCard } from "./AnteprimaPartnerCard";
import { BozzaAiDialog, type SceltaBozza } from "./BozzaAiDialog";
import { ConsensoPartnerDialog } from "./ConsensoPartnerDialog";
import { ANCORA_IDENTITA, IdentitaAziendaBox } from "./IdentitaAziendaBox";
import { firmaProfilo, MAX_COMPETENZE, PartnerProfileForm } from "./PartnerProfileForm";
import { ReferenteMembro, ReferentePartner } from "./ReferentePartner";

const ANCORA = "partner";
const TITOLO_ID = "partner-titolo";

/** Completezza del profilo: la percentuale è anche in parole. Il testo ripete
 *  la barra per chi vede; ai lettori di schermo la percentuale arriva una
 *  volta sola, dalla barra (`aria-valuenow` su 100). */
function Completezza({ valore }: { valore: number }) {
  const n = Math.max(0, Math.min(100, Math.round(valore)));
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-body font-medium text-ink tabular-nums" aria-hidden>
        {PARTNER_COPY.completezza(n)}
      </p>
      <ProgressBar
        valore={n}
        massimo={100}
        label="Completezza del profilo"
        tono={n >= 100 ? "fit" : "accent"}
      />
      <p className="text-small text-ink-3">{PARTNER_COPY.completezzaNota}</p>
    </div>
  );
}

/** Stato della visibilità e azioni del titolare: attivare (dialog di
 *  consenso), revocare (con conferma), passare tra nome e forma anonima. */
function StatoVisibilita({
  profilo,
  onAttiva,
  onAnnuncio,
}: {
  profilo: PartnerProfile;
  onAttiva: () => void;
  onAnnuncio: (testo: string) => void;
}) {
  const consenso = useConsensoPartner();
  const [revocaAperta, setRevocaAperta] = useState(false);
  const [nomeAperto, setNomeAperto] = useState(false);
  const [errore, setErrore] = useState<string | null>(null);
  const { identita } = profilo;

  const invia = async (azione: "revoca" | "anonimato", anonimo: boolean | null) => {
    setErrore(null);
    try {
      await consenso.mutateAsync({
        azione,
        // La versione del consenso DATO, mai quella corrente: qui
        // l'informativa non si mostra (il server registra comunque quella del
        // consenso esistente, e per il nome chiede la versione corrente).
        informativa_versione: profilo.consenso?.versione ?? profilo.informativa_versione_corrente,
        origine: "pagina_azienda",
        anonimo,
      });
      onAnnuncio(
        azione === "revoca"
          ? PARTNER_COPY.revocata
          : anonimo
            ? PARTNER_COPY.anonimatoOraAnonima
            : PARTNER_COPY.anonimatoOraConNome,
      );
      setRevocaAperta(false);
      setNomeAperto(false);
    } catch (err) {
      setErrore(apiErrorMessage(err));
      setRevocaAperta(false);
      setNomeAperto(false);
    }
  };

  const puoAttivare = identita.verificata && !profilo.sospeso;

  return (
    <Panel>
      <div className="grid gap-6 md:grid-cols-2">
        <Completezza valore={profilo.completezza} />
        <div className="flex flex-col items-start gap-3">
          {profilo.visibile ? (
            <>
              <p className="text-body text-ink-2">
                {profilo.consenso
                  ? `Visibile dal ${formatDate(profilo.consenso.at)}.`
                  : "Visibile come partner."}{" "}
                {profilo.anonimo
                  ? "Le altre aziende non vedono il nome."
                  : identita.verifica.verificata
                    ? "Le altre aziende vedono il nome dell'azienda."
                    : // Salvato con il nome, ma senza la verifica di oggi
                      // (revocata, o dati del registro cambiati) le altre
                      // aziende lo vedono anonimo.
                      PARTNER_COPY.nomeSalvatoNonMostrato}
              </p>
              <div className="flex flex-wrap gap-2">
                {profilo.anonimo ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={!identita.puo_essere_nominativo || consenso.isPending}
                    // Informativa cambiata dopo il consenso: il nome si mostra
                    // solo riconfermando con il testo nuovo (dialog completo).
                    onClick={() => (profilo.riconsenso_suggerito ? onAttiva() : setNomeAperto(true))}
                    aria-describedby={
                      !identita.puo_essere_nominativo ? "partner-motivo-nome" : undefined
                    }
                  >
                    <Eye className="size-4" aria-hidden />
                    {PARTNER_COPY.anonimatoCambiaInNome}
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={consenso.isPending && !revocaAperta}
                    onClick={() => void invia("anonimato", true)}
                  >
                    <EyeOff className="size-4" aria-hidden />
                    {PARTNER_COPY.anonimatoCambiaInAnonima}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setRevocaAperta(true)}
                  disabled={consenso.isPending}
                >
                  {PARTNER_COPY.revoca}
                </Button>
              </div>
              <p className="text-small text-ink-3">{PARTNER_COPY.revocaSuCambioAzienda}</p>
              {profilo.anonimo && !identita.puo_essere_nominativo && identita.motivo_nominativo && (
                <p id="partner-motivo-nome" className="text-small text-ink-3">
                  {PARTNER_COPY.motiviNominativo[identita.motivo_nominativo] ??
                    PARTNER_COPY.motiviNominativo.identita_non_verificata_admin}
                  {identita.motivo_nominativo !== "non_disponibile" && (
                    <>
                      {" "}
                      {/* Il riquadro della verifica è in questa stessa sezione. */}
                      <TextLink href={`#${ANCORA_IDENTITA}`}>{PARTNER_COPY.chiediVerifica}</TextLink>
                    </>
                  )}
                </p>
              )}
            </>
          ) : (
            <>
              <p className="text-body text-ink-2">
                Oggi l'azienda non compare tra i partner suggeriti. Compila il profilo, poi attiva
                la visibilità: sei tu a scegliere se mostrare il nome.
              </p>
              <Button onClick={onAttiva} disabled={!puoAttivare}>
                <Handshake className="size-4" aria-hidden />
                {PARTNER_COPY.attivaVisibilita}
              </Button>
            </>
          )}
          {errore && (
            <Alert tono="errore" className="self-stretch">
              {errore}
            </Alert>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={revocaAperta}
        titolo={PARTNER_COPY.revocaTitolo}
        conferma={PARTNER_COPY.revocaConferma}
        annulla={PARTNER_COPY.annulla}
        distruttiva
        inCorso={consenso.isPending}
        onConferma={() => void invia("revoca", null)}
        onAnnulla={() => setRevocaAperta(false)}
      >
        <p>{PARTNER_COPY.revocaTesto}</p>
      </ConfirmDialog>

      <ConfirmDialog
        open={nomeAperto}
        titolo="Mostrare il nome dell'azienda?"
        conferma={PARTNER_COPY.anonimatoCambiaInNome}
        annulla={PARTNER_COPY.annulla}
        inCorso={consenso.isPending}
        onConferma={() => void invia("anonimato", false)}
        onAnnulla={() => setNomeAperto(false)}
      >
        <p>
          Le altre aziende vedranno il nome registrato al Registro Imprese
          {identita.denominazione_registro ? ` («${identita.denominazione_registro}»)` : ""}, e
          più dettagli del profilo: tutte le fasce dei bilanci, anno e ruolo delle esperienze,
          le certificazioni e le infrastrutture. Puoi tornare anonima quando vuoi.
        </p>
      </ConfirmDialog>
    </Panel>
  );
}

/** Sezione «Profilo partner» della pagina Azienda (ancora
 *  `#partner`): stato e consenso, referente, bozza AI, editor del profilo e
 *  anteprima «come ti vedono». Esiste solo a modulo acceso: un 404 del
 *  server (modulo spento) la fa sparire. */
export function PartnerSection({ onImporta }: { onImporta?: () => void }) {
  const { partenariatiAttivo } = useFunzioni();
  const { data, isPending, isError, error, refetch } = usePartnerProfile();
  const { data: azienda } = useCompany();
  const queryClient = useQueryClient();
  const { hash } = useLocation();
  const [form, setForm] = useState<PartnerProfileInput | null>(null);
  const precedente = useRef<PartnerProfileInput | undefined>(undefined);
  const [consensoAperto, setConsensoAperto] = useState(false);
  const [bozzaAperta, setBozzaAperta] = useState(false);
  const [annuncio, setAnnuncio] = useState<string | null>(null);

  const salvato = data?.profilo;

  // Il form segue il profilo salvato finché non ci sono modifiche: un refetch
  // (polling della bozza, focus della finestra) non cancella ciò che si sta
  // scrivendo.
  useEffect(() => {
    if (!salvato) return;
    const prima = precedente.current;
    precedente.current = salvato;
    setForm((attuale) =>
      !attuale || !prima || firmaProfilo(attuale) === firmaProfilo(prima) ? salvato : attuale,
    );
  }, [salvato]);

  // Ragione sociale o P.IVA salvate dalla card dell'azienda: il database
  // revoca da solo la visibilità, quindi lo stato mostrato va riletto subito
  // (non allo scadere della cache).
  const identitaAzienda = azienda?.company
    ? `${azienda.company.ragione_sociale}|${azienda.company.partita_iva}`
    : null;
  const identitaVista = useRef<string | null>(null);
  useEffect(() => {
    if (identitaAzienda === null) return;
    if (identitaVista.current !== null && identitaVista.current !== identitaAzienda) {
      void queryClient.invalidateQueries({ queryKey: PARTNER_PROFILE_ROOT });
    }
    identitaVista.current = identitaAzienda;
  }, [identitaAzienda, queryClient]);

  // Link diretto a #partner (menu «Profilo partner») o a #identita (verifica
  // dell'identità, dalle notifiche e dai motivi di «Mostra il nome»): la
  // sezione c'è solo a dati arrivati, quindi lo scroll si fa allora.
  const pronto = !!data;
  useEffect(() => {
    if ((hash !== `#${ANCORA}` && hash !== `#${ANCORA_IDENTITA}`) || !pronto) return;
    document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [hash, pronto]);

  if (!partenariatiAttivo || (isError && apiErrorCode(error) === "not_found")) return null;

  const applicaBozza = (scelta: SceltaBozza) => {
    if (!form) return;
    const unione = [...form.competenze];
    for (const c of scelta.competenze) if (!unione.includes(c)) unione.push(c);
    const competenze = unione.slice(0, MAX_COMPETENZE);
    setForm({
      ...form,
      descrizione_competenze: scelta.descrizione ?? form.descrizione_competenze,
      competenze,
    });
    setBozzaAperta(false);
    setAnnuncio(
      competenze.length < unione.length
        ? `${PARTNER_COPY.bozzaApplicata} Alcune competenze non sono entrate: il massimo è ${MAX_COMPETENZE}.`
        : PARTNER_COPY.bozzaApplicata,
    );
  };

  let corpo: ReactNode;
  if (isPending) {
    corpo = (
      <div className="flex flex-col gap-6" aria-hidden>
        <Skeleton className="h-28 w-full" />
        <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
          <Skeleton className="h-96 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  } else if (isError || !data) {
    corpo = (
      <ErrorState
        message={apiErrorMessage(error, "Impossibile caricare il profilo partner.")}
        onRetry={() => void refetch()}
      />
    );
  } else if (!data.esiste && !data.editable) {
    corpo = (
      <EmptyState
        title="Profilo partner non ancora compilato"
        description={PARTNER_COPY.nessunProfilo}
      />
    );
  } else {
    const { identita } = data;
    const bozza = data.bozza_ai;
    const bozzaInCorso = bozza?.stato === "in_corso";
    corpo = (
      <div className="flex flex-col gap-6">
        {data.sospeso && (
          <Alert tono="errore" titolo={PARTNER_COPY.statoSospeso}>
            {PARTNER_COPY.sospeso}
          </Alert>
        )}

        {!identita.verificata && (
          <Alert
            tono="attenzione"
            titolo={PARTNER_COPY.identitaTitolo}
            azione={
              data.editable && onImporta ? (
                <Button variant="secondary" size="sm" onClick={onImporta}>
                  <Download className="size-4" aria-hidden />
                  {PARTNER_COPY.importa}
                </Button>
              ) : undefined
            }
          >
            {identita.motivo ? PARTNER_COPY.motiviIdentita[identita.motivo] : null}
          </Alert>
        )}

        {data.editable && data.visibile && data.riconsenso_suggerito && (
          <Alert
            tono="info"
            titolo={PARTNER_COPY.riconsensoTitolo}
            azione={
              <Button variant="secondary" size="sm" onClick={() => setConsensoAperto(true)}>
                {PARTNER_COPY.riconsensoCta}
              </Button>
            }
          >
            {PARTNER_COPY.riconsensoTesto}
          </Alert>
        )}

        <ReferenteMembro profilo={data} />

        {data.editable ? (
          <StatoVisibilita
            profilo={data}
            onAttiva={() => setConsensoAperto(true)}
            onAnnuncio={setAnnuncio}
          />
        ) : (
          <Panel>
            <Completezza valore={data.completezza} />
            <p className="text-body text-ink-2">{PARTNER_COPY.soloTitolare}</p>
          </Panel>
        )}

        {/* Verifica dell'identità da parte della piattaforma (WP9): sblocca il
            nome dell'azienda e la rivelazione tra aziende verificate. */}
        <IdentitaAziendaBox
          onImporta={data.editable ? onImporta : undefined}
          motivoRegistro={identita.motivo}
        />

        {data.editable && <ReferentePartner profilo={data} />}

        {data.editable && (
          <div className="flex flex-wrap items-center gap-3 rounded-panel bg-desk p-5">
            <p className="min-w-0 flex-1 text-body text-ink-2" aria-live="polite">
              {bozzaInCorso && bozzaInCorsoRecente(data) ? (
                <span className="inline-flex items-center gap-2">
                  <Spinner size="sm" />
                  {PARTNER_COPY.bozzaInCorso}
                </span>
              ) : bozza?.stato === "pronta" && bozza.proposta ? (
                PARTNER_COPY.bozzaProntaBreve
              ) : (
                "Non sai da dove partire? L'AI può proporti una descrizione e le competenze a partire dai dati del Registro Imprese."
              )}
            </p>
            <Button variant="secondary" size="sm" onClick={() => setBozzaAperta(true)}>
              {bozza?.stato === "pronta" && bozza.proposta
                ? PARTNER_COPY.bozzaRivedi
                : PARTNER_COPY.bozzaCta}
            </Button>
          </div>
        )}

        <div className="grid items-start gap-6 lg:grid-cols-[3fr_2fr]">
          {form && salvato ? (
            <PartnerProfileForm
              valore={form}
              onChange={setForm}
              salvato={salvato}
              editable={data.editable}
              anonimo={data.anonimo}
              dedotti={data.tipi_soggetto_dedotti}
              avvisiAnonimato={data.avvisi_anonimato}
            />
          ) : (
            <Skeleton className="h-96 w-full" />
          )}
          {/* Anche prima del primo salvataggio: il server mostra i soli dati
              del registro. */}
          {/* Su desktop non c'è la barra in alto: stesso scarto della colonna
              laterale fissa di `Page`. */}
          <AnteprimaPartnerCard visibile={data.visibile} className="lg:sticky lg:top-8" />
        </div>

        {data.editable && (
          <>
            <ConsensoPartnerDialog
              open={consensoAperto}
              profilo={data}
              origine="pagina_azienda"
              onClose={() => setConsensoAperto(false)}
              onAttivato={() => {
                setConsensoAperto(false);
                setAnnuncio(PARTNER_COPY.attivato);
              }}
            />
            <BozzaAiDialog
              open={bozzaAperta}
              onClose={() => setBozzaAperta(false)}
              profilo={data}
              onApplica={applicaBozza}
            />
          </>
        )}
      </div>
    );
  }

  // Lo stato in parole: uno solo («Sospeso», «Visibile come partner», «Non
  // visibile»); il modo (anonima o con il nome) è un'etichetta neutra.
  const stato = data ? (
    <div className="flex flex-wrap items-center gap-2">
      {data.sospeso ? (
        <Status tono="attenzione">{PARTNER_COPY.statoSospeso}</Status>
      ) : data.visibile ? (
        <Status tono="aperto">{PARTNER_COPY.statoVisibile}</Status>
      ) : (
        <Status tono="chiuso">{PARTNER_COPY.statoNonVisibile}</Status>
      )}
      {data.visibile && <Badge>{data.anonimo ? PARTNER_COPY.anonima : PARTNER_COPY.conNome}</Badge>}
    </div>
  ) : undefined;

  return (
    <Section id={ANCORA} aria-labelledby={TITOLO_ID} className="scroll-mt-16">
      <SectionHeader id={TITOLO_ID} titolo={PARTNER_COPY.titoloSezione} azione={stato} />
      <p className="max-w-lettura text-body text-ink-2">{PARTNER_COPY.descrizioneSezione}</p>

      {/* Sempre montata: gli esiti (consenso, revoca, bozza applicata) vanno
          annunciati anche quando il blocco che li ha causati sparisce. */}
      <div role="status" aria-live="polite">
        {annuncio && (
          <Alert tono="ok" ruolo="none">
            {annuncio}
          </Alert>
        )}
      </div>

      {corpo}
    </Section>
  );
}
