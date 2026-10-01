import { BadgeCheck, ShieldX } from "lucide-react";
import { useEffect, useId, useState } from "react";
import {
  useAdminIdentita,
  useDecidiIdentita,
  useRevocaIdentita,
} from "../../../hooks/useAdminPartenariati";
import { apiErrorMessage } from "../../../lib/api";
import { ADMIN_PARTENARIATI_COPY, IDENTITA_COPY, PARTNER_COPY } from "../../../lib/copy";
import { formatDate } from "../../../lib/format";
import type { FiltroIdentitaAdmin, IdentitaAdmin, MetodoVerificaIdentita } from "../../../types";
import { Alert } from "../../ui/Alert";
import { Button } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { SelectField } from "../../ui/Field";
import { Pagination } from "../../ui/Pagination";
import { Td, Th } from "../../ui/Table";
import { SceltaRadio, TestoLungo } from "../CampiCall";
import { StatoIdentitaBadge } from "../IdentitaAziendaBox";
import {
  Annuncio,
  Filtro,
  MotivazioneDialog,
  StatiLista,
  TabellaCard,
  thRigaClass,
} from "./comuni";
import { useRientroPagina } from "../useRientroPagina";

const METODI = Object.keys(IDENTITA_COPY.metodi) as MetodoVerificaIdentita[];

/** Recapiti della SEDE dal Registro Imprese (i soli da usare per verificare:
 *  mai quelli scritti dall'utente). */
function RecapitiRegistro({ riga }: { riga: IdentitaAdmin }) {
  const r = riga.registro;
  const sede = [r.comune, r.provincia ? `(${r.provincia})` : null].filter(Boolean).join(" ");
  const voci = [
    r.stato_impresa ? `Impresa ${r.stato_impresa.toLowerCase()}` : null,
    sede ? `Sede: ${sede}` : null,
    r.pec ? `PEC: ${r.pec}` : null,
    r.telefono ? `Tel.: ${r.telefono}` : null,
  ].filter(Boolean);
  if (voci.length === 0) return null;
  return (
    // `<details>` nativo e piccolo: dentro una cella l'`Accordion` (con i
    // filetti di sezione) sarebbe fuori scala.
    <details className="mt-1.5">
      <summary className="cursor-pointer rounded-mark text-small font-medium text-accent-hover">
        Recapiti dal Registro Imprese
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5 text-small text-ink-2">
        {voci.map((v) => (
          <li key={v}>{v}</li>
        ))}
      </ul>
    </details>
  );
}
const NOTA_MAX = 500;

/** Decisione su una richiesta di verifica: verificata (con il metodo,
 *  obbligatorio) o rifiutata, con una nota facoltativa (≤ 500, niente dati
 *  personali oltre il necessario). */
function DecidiIdentitaDialog({
  riga,
  onClose,
  onFatto,
}: {
  riga: IdentitaAdmin | null;
  onClose: () => void;
  onFatto: (annuncio: string) => void;
}) {
  const nome = useId();
  const decidi = useDecidiIdentita();
  const [esito, setEsito] = useState<"verificata" | "rifiutata" | null>(null);
  const [metodo, setMetodo] = useState<MetodoVerificaIdentita | "">("");
  const [nota, setNota] = useState("");
  const [errore, setErrore] = useState<string | null>(null);

  useEffect(() => {
    if (!riga) return;
    setEsito(null);
    setMetodo("");
    setNota("");
    setErrore(null);
    decidi.reset();
    // Solo all'apertura.
  }, [riga?.company_profile_id]);

  if (!riga) return null;

  const conferma = async () => {
    setErrore(null);
    if (!esito) {
      setErrore("Scegli l'esito della verifica.");
      return;
    }
    if (esito === "verificata" && !metodo) {
      setErrore("Indica come hai verificato l'identità.");
      return;
    }
    try {
      await decidi.mutateAsync({
        companyId: riga.company_profile_id,
        esito,
        metodo: esito === "verificata" ? (metodo as MetodoVerificaIdentita) : null,
        nota: nota.trim() || null,
      });
      onFatto(
        esito === "verificata"
          ? "Identità verificata: il titolare riceve la notifica."
          : "Verifica rifiutata: il titolare riceve la notifica.",
      );
    } catch (err) {
      setErrore(apiErrorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Decidi la verifica dell'identità"
      dismissible={!decidi.isPending}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={decidi.isPending}>
            Annulla
          </Button>
          <Button loading={decidi.isPending} onClick={() => void conferma()}>
            Registra l'esito
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <p className="flex flex-wrap gap-x-3">
          <span className="font-medium text-ink">
            {riga.denominazione_registro ?? riga.ragione_sociale ?? "Azienda"}
          </span>
          {riga.partita_iva && <span className="tabular-nums">P.IVA {riga.partita_iva}</span>}
        </p>
        {!riga.registro_ok && (
          <Alert tono="attenzione" ruolo="none">
            I dati dell'azienda non corrispondono più al Registro Imprese: la verifica non si può
            confermare finché il titolare non li aggiorna.
          </Alert>
        )}
        <SceltaRadio<"verificata" | "rifiutata">
          legenda="Esito"
          nome={nome}
          valore={esito}
          onChange={setEsito}
          opzioni={[
            {
              valore: "verificata",
              etichetta: "Identità verificata",
              nota: "Sblocca il nome dell'azienda nel profilo partner e nelle call, e la rivelazione tra aziende verificate.",
            },
            {
              valore: "rifiutata",
              etichetta: "Verifica non riuscita",
              nota: "Il titolare potrà chiederla di nuovo.",
            },
          ]}
        />
        {esito === "verificata" && (
          <SelectField
            label="Come l'hai verificata"
            required
            value={metodo}
            onChange={(e) => setMetodo(e.target.value as MetodoVerificaIdentita | "")}
          >
            <option value="">Scegli il metodo…</option>
            {METODI.map((m) => (
              <option key={m} value={m}>
                {IDENTITA_COPY.metodi[m]}
              </option>
            ))}
          </SelectField>
        )}
        <TestoLungo
          etichetta="Nota (facoltativa)"
          aiuto="Solo ciò che serve a ricostruire la verifica: niente dati personali oltre il necessario."
          valore={nota}
          onChange={setNota}
          massimo={NOTA_MAX}
          righe={3}
        />
        {errore && <Alert tono="errore">{errore}</Alert>}
      </div>
    </Dialog>
  );
}

/** Verifiche dell'identità delle aziende (decisione di Michele): coda delle
 *  richieste, decisione con il metodo, revoca motivata. */
export function IdentitaTab() {
  const [stato, setStato] = useState<FiltroIdentitaAdmin>("richiesta");
  const [page, setPage] = useState(1);
  const [annuncio, setAnnuncio] = useState<string | null>(null);
  const [daDecidere, setDaDecidere] = useState<IdentitaAdmin | null>(null);
  const [daRevocare, setDaRevocare] = useState<IdentitaAdmin | null>(null);
  const revoca = useRevocaIdentita();
  useEffect(() => setPage(1), [stato]);
  const lista = useAdminIdentita(stato, page);
  const fuoriPagina = useRientroPagina(lista.data, page, lista.isPlaceholderData, setPage);

  return (
    <div className="flex flex-col gap-4">
      <Alert tono="info" titolo="Come verificare">
        Usa un canale che l'utente non controlla: il telefono della sede o la PEC presi dal
        Registro Imprese, oppure un documento del legale rappresentante indicato nel registro. Non
        usare i recapiti scritti dall'utente nella nota o nel profilo.
      </Alert>
      <div className="flex flex-wrap items-end gap-3">
        <Filtro<FiltroIdentitaAdmin>
          etichetta="Stato"
          valore={stato}
          onChange={(v) => setStato(v || "richiesta")}
          opzioni={[
            { valore: "richiesta", etichetta: "Richieste in attesa" },
            { valore: "verificata", etichetta: IDENTITA_COPY.stati.verificata },
            { valore: "rifiutata", etichetta: IDENTITA_COPY.stati.rifiutata },
            { valore: "non_richiesta", etichetta: IDENTITA_COPY.stati.non_richiesta },
            { valore: "tutte", etichetta: "Tutte" },
          ]}
        />
      </div>
      <Annuncio testo={annuncio} />
      <StatiLista
        isPending={lista.isPending || fuoriPagina}
        isError={lista.isError}
        error={lista.error}
        onRetry={() => void lista.refetch()}
        vuoto={(lista.data?.items.length ?? 0) === 0}
        titoloVuoto={stato === "richiesta" ? "Nessuna richiesta in attesa" : "Nessuna azienda"}
        descrizioneVuoto="Quando un titolare chiede la verifica, la trovi qui."
      >
        <TabellaCard caption="Verifiche dell'identità delle aziende" attenuata={lista.isPlaceholderData}>
          <thead>
            <tr>
              <Th>Azienda</Th>
              <Th>Titolare</Th>
              <Th>Stato</Th>
              <Th>Nota del titolare</Th>
              <Th numerica>Azioni</Th>
            </tr>
          </thead>
          <tbody>
            {lista.data?.items.map((r) => (
              <tr key={r.company_profile_id}>
                <th scope="row" className={thRigaClass}>
                  <p className="font-medium text-ink">
                    {r.denominazione_registro ?? r.ragione_sociale ?? "—"}
                  </p>
                  {r.partita_iva && (
                    <p className="text-small text-ink-3 tabular-nums">P.IVA {r.partita_iva}</p>
                  )}
                  {/* In parole e senza punto: lo stato della riga è quello della verifica. */}
                  {r.registro_ok ? (
                    <p className="mt-1 text-small text-ink-3">Registro Imprese coerente</p>
                  ) : (
                    <p className="mt-1 text-small font-medium text-warning-ink">
                      Registro Imprese da aggiornare
                    </p>
                  )}
                  {!r.registro_ok && r.registro_motivo && (
                    <p className="max-w-64 text-small text-warning-ink">
                      {PARTNER_COPY.motiviIdentita[r.registro_motivo] ?? r.registro_motivo}
                    </p>
                  )}
                  <RecapitiRegistro riga={r} />
                </th>
                <Td className="text-ink-2">
                  <p>{r.titolare?.nome ?? "—"}</p>
                  {r.titolare?.email && <p className="text-small text-ink-3">{r.titolare.email}</p>}
                </Td>
                <Td>
                  <StatoIdentitaBadge stato={r.stato} />
                  <p className="mt-1 text-small text-ink-3">
                    {r.stato === "verificata" && r.verificata_at
                      ? `Dal ${formatDate(r.verificata_at)}${r.metodo ? `, ${IDENTITA_COPY.metodi[r.metodo]}` : ""}`
                      : r.richiesta_at
                        ? `Richiesta del ${formatDate(r.richiesta_at)}`
                        : null}
                  </p>
                </Td>
                <Td className="max-w-64 text-ink-2">
                  {r.nota ? <p className="whitespace-pre-line">{r.nota}</p> : <span className="text-ink-3">—</span>}
                </Td>
                <Td className="text-right">
                  {r.stato === "richiesta" && (
                    <Button variant="secondary" size="sm" onClick={() => setDaDecidere(r)}>
                      <BadgeCheck className="size-4" aria-hidden />
                      Decidi
                    </Button>
                  )}
                  {r.stato === "verificata" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        revoca.reset();
                        setDaRevocare(r);
                      }}
                    >
                      <ShieldX className="size-4" aria-hidden />
                      Revoca
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

      <DecidiIdentitaDialog
        riga={daDecidere}
        onClose={() => setDaDecidere(null)}
        onFatto={(t) => {
          setDaDecidere(null);
          setAnnuncio(t);
        }}
      />
      <MotivazioneDialog
        open={daRevocare !== null}
        onClose={() => setDaRevocare(null)}
        titolo="Revocare la verifica dell'identità?"
        descrizione={
          <p>
            {daRevocare?.denominazione_registro ?? daRevocare?.ragione_sociale ?? "L'azienda"} torna
            non verificata: il nome non si mostra più e le identità già rivelate non compaiono più
            nelle pagine (i registri restano).
          </p>
        }
        etichetta="Motivo della revoca"
        min={1}
        max={ADMIN_PARTENARIATI_COPY.motivoRevocaMax}
        etichettaConferma="Revoca la verifica"
        pericolosa
        inCorso={revoca.isPending}
        errore={revoca.isError ? apiErrorMessage(revoca.error) : null}
        onConferma={(motivo) => {
          if (!daRevocare) return;
          revoca.mutate(
            { companyId: daRevocare.company_profile_id, motivo },
            {
              onSuccess: (esito) => {
                setDaRevocare(null);
                setAnnuncio(
                  esito.modificato
                    ? "Verifica revocata: il titolare riceve la notifica."
                    : "L'azienda non era verificata: nulla è cambiato.",
                );
              },
            },
          );
        }}
      />
    </div>
  );
}
