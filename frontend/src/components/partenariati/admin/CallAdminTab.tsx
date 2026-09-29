import { Search, ShieldOff, Undo2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { useAdminCall, useRipristinaOggetto, useSospendiOggetto } from "../../../hooks/useAdminPartenariati";
import { useDebounce } from "../../../hooks/useDebounce";
import { apiErrorMessage } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { ADMIN_PARTENARIATI_COPY, CALL_COPY } from "../../../lib/copy";
import { formatDate } from "../../../lib/format";
import type { CallAdmin, StatoCall } from "../../../types";
import { Badge } from "../../ui/Badge";
import { Button } from "../../ui/Button";
import { Pagination } from "../../ui/Pagination";
import { CallStatoBadge } from "../CallStatoBadge";
import { EsitoVoceBadge } from "../ValidatoreChecklist";
import { Annuncio, Filtro, MotivazioneDialog, numero, StatiLista, TabellaCard, thClass } from "./comuni";

const STATI = Object.keys(CALL_COPY.stati) as StatoCall[];

type Azione = { tipo: "sospendi" | "ripristina"; call: CallAdmin };

/** Elenco delle call per l'admin (filtri per stato e testo) con
 *  sospensione e ripristino diretti, motivati. Il ripristino riporta la call
 *  allo stato precedente, o la chiude come scaduta se nel frattempo è passata
 *  la scadenza (lo decide il server). */
export function CallAdminTab() {
  const idRicerca = useId();
  const [stato, setStato] = useState<StatoCall | "">("");
  const [testo, setTesto] = useState("");
  const q = useDebounce(testo.trim(), 400);
  const [page, setPage] = useState(1);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const [azione, setAzione] = useState<Azione | null>(null);
  useEffect(() => setPage(1), [stato, q]);
  const lista = useAdminCall({ stato, q, page });
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
    <div className="space-y-4">
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
        <div className="flex min-w-64 flex-1 flex-col gap-1">
          <label htmlFor={idRicerca} className="text-sm font-medium text-slate-700">
            Cerca
          </label>
          <div className="relative max-w-md">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400"
              aria-hidden
            />
            <input
              id={idRicerca}
              type="search"
              value={testo}
              onChange={(e) => setTesto(e.target.value)}
              placeholder="Titolo della call o del bando, oppure l'id della call"
              className="h-10 w-full rounded-lg border border-slate-300 bg-white pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-2 focus:outline-offset-0 focus:outline-brand-500/30"
            />
          </div>
        </div>
      </div>
      <Annuncio testo={annuncio} />
      <StatiLista
        isPending={lista.isPending}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto="Nessuna call"
        descrizioneVuoto={stato || q ? "Con questi filtri non c'è nulla." : "Non è stata ancora creata nessuna call."}
      >
        <p className="text-sm text-slate-500" role="status" aria-live="polite">
          {lista.isPlaceholderData
            ? "Aggiornamento…"
            : lista.data?.total === 1
              ? "1 call"
              : `${numero(lista.data?.total)} call`}
        </p>
        <TabellaCard caption="Call di partenariato" attenuata={lista.isPlaceholderData}>
          <thead>
            <tr className="border-b border-slate-200 bg-slate-50/70 text-xs uppercase tracking-wide text-slate-500">
              <th scope="col" className={thClass}>Call</th>
              <th scope="col" className={thClass}>Azienda</th>
              <th scope="col" className={thClass}>Stato</th>
              <th scope="col" className={cn(thClass, "text-right")}>Candidature</th>
              <th scope="col" className={cn(thClass, "text-right")}>Membri</th>
              <th scope="col" className={thClass}>Consorzio</th>
              <th scope="col" className={cn(thClass, "text-right")}>Segnalazioni aperte</th>
              <th scope="col" className={cn(thClass, "text-right")}>Azioni</th>
            </tr>
          </thead>
          <tbody>
            {lista.data?.items.map((c) => (
              <tr key={c.id} className="border-b border-slate-100 align-top last:border-b-0">
                <th scope="row" className="max-w-72 px-4 py-3 text-left font-normal">
                  {c.stato === "pubblicata" ? (
                    <Link
                      to={`/app/partenariati/call/${c.id}`}
                      className="font-medium text-brand-600 hover:text-brand-700"
                    >
                      {c.titolo || "Call senza titolo"}
                    </Link>
                  ) : (
                    <p className="font-medium text-slate-900">{c.titolo || "Call senza titolo"}</p>
                  )}
                  <p className="text-xs text-slate-500">{c.bando.titolo ?? "Bando non più nel catalogo"}</p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {c.pubblicata_at ? `Pubblicata il ${formatDate(c.pubblicata_at)}` : "Mai pubblicata"}
                    {c.scadenza_call ? ` · fino al ${formatDate(c.scadenza_call)}` : ""}
                  </p>
                </th>
                <td className="px-4 py-3 text-slate-700">
                  <p>{c.creatore.ragione_sociale ?? "—"}</p>
                  <p className="mt-0.5 flex flex-wrap gap-1">
                    {c.anonima === false && <Badge tone="brand">Con il nome</Badge>}
                    {c.visibilita === "solo_invitati" && <Badge tone="slate">Solo invitati</Badge>}
                  </p>
                </td>
                <td className="px-4 py-3">
                  <CallStatoBadge stato={c.stato} />
                  {c.stato === "sospesa_moderazione" && (
                    <p className="mt-1 max-w-48 text-xs text-slate-500">
                      {c.sospesa_at ? `Dal ${formatDate(c.sospesa_at)}` : ""}
                      {c.sospeso_motivo ? `: ${c.sospeso_motivo}` : ""}
                    </p>
                  )}
                </td>
                <td className="px-4 py-3 text-right tabular text-slate-700">
                  {numero(c.candidature)}
                  <p className="text-xs text-slate-400">
                    {c.inviti === 1 ? "1 invito" : `${numero(c.inviti)} inviti`}
                  </p>
                </td>
                <td className="px-4 py-3 text-right tabular text-slate-700">{numero(c.membri)}</td>
                <td className="px-4 py-3">
                  {c.validazione_esito ? (
                    <EsitoVoceBadge esito={c.validazione_esito} />
                  ) : (
                    <span className="text-xs text-slate-400">Non verificato</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right tabular">
                  {c.segnalazioni_aperte > 0 ? (
                    <Badge tone="red">{numero(c.segnalazioni_aperte)}</Badge>
                  ) : (
                    <span className="text-slate-400">0</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  {(c.stato === "bozza" || c.stato === "pubblicata") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-red-700 hover:bg-red-50 hover:text-red-800"
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
                </td>
              </tr>
            ))}
          </tbody>
        </TabellaCard>
        {lista.data && (
          <div className="mt-4">
            <Pagination page={lista.data.page} totalPages={lista.data.total_pages} onChange={setPage} />
          </div>
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
          <p className="text-xs text-slate-500">
            {ADMIN_PARTENARIATI_COPY.motivazioneSospensioneAiuto}
          </p>
        )}
      </MotivazioneDialog>
    </div>
  );
}
