import { ShieldOff, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useAdminCall, useRipristinaOggetto, useSospendiOggetto } from "../../../hooks/useAdminPartenariati";
import { useDebounce } from "../../../hooks/useDebounce";
import { apiErrorMessage } from "../../../lib/api";
import { ADMIN_PARTENARIATI_COPY, CALL_COPY } from "../../../lib/copy";
import { formatDate } from "../../../lib/format";
import type { CallAdmin, StatoCall } from "../../../types";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Pagination } from "../../ui/Pagination";
import { SearchInput } from "../../ui/SearchInput";
import { Td, Th } from "../../ui/Table";
import { TextLink } from "../../ui/TextLink";
import { CallStatoBadge } from "../CallStatoBadge";
import { EsitoVoceBadge } from "../ValidatoreChecklist";
import {
  Annuncio,
  Filtro,
  MotivazioneDialog,
  numero,
  StatiLista,
  TabellaCard,
  thRigaClass,
} from "./comuni";
import { useRientroPagina } from "../useRientroPagina";

const STATI = Object.keys(CALL_COPY.stati) as StatoCall[];

type Azione = { tipo: "sospendi" | "ripristina"; call: CallAdmin };

/** Elenco delle call per l'admin (filtri per stato e testo) con
 *  sospensione e ripristino diretti, motivati. Il ripristino riporta la call
 *  allo stato precedente, o la chiude come scaduta se nel frattempo è passata
 *  la scadenza (lo decide il server). */
export function CallAdminTab() {
  const [stato, setStato] = useState<StatoCall | "">("");
  const [testo, setTesto] = useState("");
  const q = useDebounce(testo.trim(), 400);
  const [page, setPage] = useState(1);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const [azione, setAzione] = useState<Azione | null>(null);
  useEffect(() => setPage(1), [stato, q]);
  const lista = useAdminCall({ stato, q, page });
  const fuoriPagina = useRientroPagina(lista.data, page, lista.isPlaceholderData, setPage);
  const sospendi = useSospendiOggetto();
  const ripristina = useRipristinaOggetto();
  const mutazione = azione?.tipo === "ripristina" ? ripristina : sospendi;

  const esegui = (motivazione: string) => {
    if (!azione) return;
    const { tipo, call } = azione;
    mutazione.mutate(
      { oggetto: "call", id: call.id, motivazione },
      {
        onSuccess: (esito) => {
          setAzione(null);
          if (!esito.modificato) {
            setAnnuncio(
              tipo === "sospendi"
                ? "La call era già sospesa: nulla è cambiato."
                : "La call non era sospesa: nulla è cambiato.",
            );
            return;
          }
          const statoNuovo = esito.stato as StatoCall | null;
          setAnnuncio(
            tipo === "sospendi"
              ? "Fatto: la call è sospesa."
              : `Fatto: la call è ripristinata${
                  statoNuovo && CALL_COPY.stati[statoNuovo]
                    ? ` (${CALL_COPY.stati[statoNuovo].toLowerCase()})`
                    : ""
                }.`,
          );
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Filtro<StatoCall>
          etichetta="Stato"
          valore={stato}
          onChange={setStato}
          opzioni={[
            { valore: "", etichetta: "Tutti gli stati" },
            ...STATI.map((s) => ({ valore: s, etichetta: CALL_COPY.stati[s] })),
          ]}
        />
        <SearchInput
          label="Cerca"
          value={testo}
          onChange={setTesto}
          placeholder="Titolo della call o del bando, nome dell'azienda, oppure l'id della call"
          className="min-w-64 max-w-md flex-1"
        />
      </div>
      <Annuncio testo={annuncio} />
      <StatiLista
        isPending={lista.isPending || fuoriPagina}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto="Nessuna call"
        descrizioneVuoto={stato || q ? "Con questi filtri non c'è nulla." : "Non è stata ancora creata nessuna call."}
      >
        <p className="text-small text-ink-3 tabular-nums" role="status" aria-live="polite">
          {lista.isPlaceholderData
            ? "Aggiornamento…"
            : lista.data?.total === 1
              ? "1 call"
              : `${numero(lista.data?.total)} call`}
        </p>
        <TabellaCard caption="Call di partenariato" attenuata={lista.isPlaceholderData}>
          <thead>
            <tr>
              <Th>Call</Th>
              <Th>Azienda</Th>
              <Th>Stato</Th>
              <Th numerica>Candidature</Th>
              <Th numerica>Membri</Th>
              <Th>Consorzio</Th>
              <Th numerica>Segnalazioni aperte</Th>
              <Th numerica>Azioni</Th>
            </tr>
          </thead>
          <tbody>
            {lista.data?.items.map((c) => (
              <tr key={c.id}>
                <th scope="row" className={`${thRigaClass} max-w-72`}>
                  {c.stato === "pubblicata" ? (
                    <TextLink to={`/app/partenariati/call/${c.id}`} className="font-medium">
                      {c.titolo || "Call senza titolo"}
                    </TextLink>
                  ) : (
                    <p className="font-medium text-ink">{c.titolo || "Call senza titolo"}</p>
                  )}
                  <p className="text-small text-ink-2">{c.bando.titolo ?? "Bando non più nel catalogo"}</p>
                  <p className="flex flex-wrap gap-x-3 text-small text-ink-3">
                    <span>
                      {c.pubblicata_at ? `Pubblicata il ${formatDate(c.pubblicata_at)}` : "Mai pubblicata"}
                    </span>
                    {c.scadenza_call && <span>Fino al {formatDate(c.scadenza_call)}</span>}
                  </p>
                </th>
                <Td className="text-ink-2">
                  <p>{c.creatore.ragione_sociale ?? "—"}</p>
                  <p className="mt-1 flex flex-wrap gap-1">
                    {c.anonima === false && <Badge>Con il nome</Badge>}
                    {c.visibilita === "solo_invitati" && <Badge>Solo invitati</Badge>}
                  </p>
                </Td>
                <Td>
                  <CallStatoBadge stato={c.stato} />
                  {c.stato === "sospesa_moderazione" && (
                    <p className="mt-1 max-w-48 text-small text-ink-3">
                      {c.sospesa_at ? `Dal ${formatDate(c.sospesa_at)}` : ""}
                      {c.sospeso_motivo ? `: ${c.sospeso_motivo}` : ""}
                    </p>
                  )}
                </Td>
                <Td numerica className="text-ink-2">
                  {numero(c.candidature)}
                  <p className="text-small text-ink-3">
                    {c.inviti === 1 ? "1 invito" : `${numero(c.inviti)} inviti`}
                  </p>
                </Td>
                <Td numerica className="text-ink-2">
                  {numero(c.membri)}
                </Td>
                <Td>
                  {c.validazione_esito ? (
                    <EsitoVoceBadge esito={c.validazione_esito} />
                  ) : (
                    <span className="text-small text-ink-3">Non verificato</span>
                  )}
                </Td>
                <Td numerica>
                  {c.segnalazioni_aperte > 0 ? (
                    <span className="font-semibold text-danger">{numero(c.segnalazioni_aperte)}</span>
                  ) : (
                    <span className="text-ink-3">0</span>
                  )}
                </Td>
                <Td className="text-right">
                  {(c.stato === "bozza" || c.stato === "pubblicata") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Sospendi la call ${c.titolo || "senza titolo"}`}
                      onClick={() => {
                        sospendi.reset();
                        setAzione({ tipo: "sospendi", call: c });
                      }}
                    >
                      <ShieldOff className="size-4" aria-hidden />
                      Sospendi
                    </Button>
                  )}
                  {c.stato === "sospesa_moderazione" && (
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Ripristina la call ${c.titolo || "senza titolo"}`}
                      onClick={() => {
                        ripristina.reset();
                        setAzione({ tipo: "ripristina", call: c });
                      }}
                    >
                      <Undo2 className="size-4" aria-hidden />
                      Ripristina
                    </Button>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </TabellaCard>
        {lista.data && (
          <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={setPage} />
        )}
      </StatiLista>

      <MotivazioneDialog
        open={azione !== null}
        onClose={() => setAzione(null)}
        titolo={azione?.tipo === "ripristina" ? "Ripristinare la call?" : "Sospendere la call?"}
        descrizione={
          <p>
            «{azione?.call.titolo || "Call senza titolo"}».{" "}
            {azione?.tipo === "ripristina"
              ? "Torna allo stato di prima della sospensione; se nel frattempo è scaduta, si chiude come scaduta."
              : "Sparisce dalla bacheca e dai suggerimenti finché non la ripristini. I membri possono comunque uscire dal consorzio."}
          </p>
        }
        etichetta="Motivazione"
        min={ADMIN_PARTENARIATI_COPY.motivazioneMin}
        max={
          azione?.tipo === "sospendi"
            ? ADMIN_PARTENARIATI_COPY.motivazioneSospensioneMax
            : ADMIN_PARTENARIATI_COPY.motivazioneMax
        }
        etichettaConferma={azione?.tipo === "ripristina" ? "Ripristina la call" : "Sospendi la call"}
        pericolosa={azione?.tipo === "sospendi"}
        inCorso={mutazione.isPending}
        errore={mutazione.isError ? apiErrorMessage(mutazione.error) : null}
        onConferma={esegui}
      >
        {azione?.tipo === "sospendi" && (
          <p className="text-small text-ink-3">
            {ADMIN_PARTENARIATI_COPY.motivazioneSospensioneAiuto}
          </p>
        )}
      </MotivazioneDialog>
    </div>
  );
}
