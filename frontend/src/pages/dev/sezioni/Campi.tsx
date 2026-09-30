import { useState, type ReactNode } from "react";
import { AuthLayout } from "../../../components/ui/AuthLayout";
import { Avatar } from "../../../components/ui/Avatar";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Chip } from "../../../components/ui/Chip";
import { TextField } from "../../../components/ui/Field";
import { Filter } from "../../../components/ui/Filter";
import { PasswordField } from "../../../components/ui/PasswordField";
import { PasswordStrengthMeter } from "../../../components/ui/PasswordStrengthMeter";
import { usePopover } from "../../../components/ui/Popover";
import { RadioGroup } from "../../../components/ui/RadioGroup";
import { SearchInput } from "../../../components/ui/SearchInput";
import { Segment } from "../../../components/ui/Segment";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";

export const titolo = "Campi e accesso";

/** Un blocco della vetrina: nome del componente, una nota e gli esempi in riga. */
function Blocco({ nome, nota, children }: { nome: string; nota?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-title-group text-ink">{nome}</h3>
        {nota && <p className="text-small text-ink-3">{nota}</p>}
      </div>
      <div className="flex flex-wrap items-start gap-4">{children}</div>
    </div>
  );
}

const FILTRI_INIZIALI = ["Piemonte", "Fondo perduto", "Micro e piccole imprese"];

const SEGMENTO_BANDI = [
  { id: "tutti", label: "Tutti" },
  { id: "adatti", label: "Adatti alla tua azienda" },
] as const;

const SEGMENTO_TRE = [
  { id: "aperte", label: "Aperte" },
  { id: "chiuse", label: "Chiuse" },
  { id: "tutte", label: "Tutte" },
] as const;

const RUOLI = [
  { id: "partner", label: "Partner" },
  {
    id: "capofila",
    label: "Capofila",
    descrizione: "Guida il progetto e tiene i rapporti con l'ente.",
  },
] as const;

/** Contenuto di esempio del pannello di un `Filter`: caselle e «Applica»,
 *  che chiude il pannello con `usePopover().chiudi()`. */
function PannelloStato() {
  const { chiudi } = usePopover();
  return (
    <div className="flex flex-col gap-3">
      <Checkbox label="Aperto" defaultChecked />
      <Checkbox label="In apertura" defaultChecked />
      <Checkbox label="Chiuso" />
      <div className="flex justify-end gap-2 border-t border-line pt-3">
        <Button variant="ghost" size="sm" onClick={chiudi}>
          Annulla
        </Button>
        <Button size="sm" onClick={chiudi}>
          Applica
        </Button>
      </div>
    </div>
  );
}

export default function Campi() {
  const [chips, setChips] = useState<string[]>(FILTRI_INIZIALI);
  const [segmento, setSegmento] = useState<string>("tutti");
  const [segmentoTre, setSegmentoTre] = useState<string>("aperte");
  const [ricerca, setRicerca] = useState("");
  const [ruolo, setRuolo] = useState<string | null>("partner");
  const [ruoloErrore, setRuoloErrore] = useState<string | null>(null);
  const [avvisi, setAvvisi] = useState(true);
  const [profilo, setProfilo] = useState(false);
  const [nuovaPassword, setNuovaPassword] = useState("");

  return (
    <div className="flex flex-col gap-10">
      <Blocco nome="Avatar" nota="Iniziali su accent-soft; sm 24px, md 32px.">
        <Avatar nome="Giulia Rinaldi" size="sm" />
        <Avatar nome="Giulia Rinaldi" />
        <Avatar nome="Marta" />
        <Avatar nome="Officine Rinaldi S.r.l." />
      </Blocco>

      <Blocco nome="Chip" nota="Filtro attivo rimovibile; senza onRemove è un valore fisso.">
        <Chip>Solo lettura</Chip>
        {chips.map((c) => (
          <Chip
            key={c}
            label={`Rimuovi il filtro ${c}`}
            onRemove={() => setChips((prev) => prev.filter((x) => x !== c))}
          >
            {c}
          </Chip>
        ))}
        <Chip className="max-w-48" onRemove={() => undefined} label="Rimuovi il filtro lungo">
          Un valore molto lungo che viene troncato con i puntini
        </Chip>
        {chips.length < FILTRI_INIZIALI.length && (
          <Button variant="ghost" size="sm" onClick={() => setChips(FILTRI_INIZIALI)}>
            Ripristina i filtri
          </Button>
        )}
      </Blocco>

      <Blocco
        nome="Filter"
        nota="Pulsante-filtro della barra: con children apre un Popover; senza è il solo pulsante."
      >
        <Filter label="Tipologia" />
        <Filter label="Stato" attivo valore="2">
          <PannelloStato />
        </Filter>
        <Filter label="Regione" attivo valore="Piemonte" />
        <Filter label="Importo" disabled />
      </Blocco>

      <Blocco nome="Segment" nota="Due o tre scelte; oltre, Tabs o Select.">
        <Segment
          opzioni={SEGMENTO_BANDI}
          valore={segmento}
          onChange={setSegmento}
          ariaLabel="Quali bandi mostrare"
        />
        <Segment
          opzioni={SEGMENTO_TRE}
          valore={segmentoTre}
          onChange={setSegmentoTre}
          ariaLabel="Quali call mostrare"
        />
      </Blocco>

      <Blocco nome="SearchInput" nota="Etichetta nascosta, icona Search, anello del focus sul contenitore.">
        <SearchInput
          className="w-96"
          label="Cerca nei bandi"
          placeholder="Cerca per parola chiave, per esempio «digitalizzazione»"
          value={ricerca}
          onChange={setRicerca}
        />
        <SearchInput
          className="w-64"
          label="Cerca (disattivata)"
          placeholder="Cerca per email, nome o azienda"
          value=""
          onChange={() => undefined}
          disabled
        />
      </Blocco>

      <Blocco nome="Select" nota="Senza etichetta visibile, per le barre (aria-label obbligatoria).">
        <Select label="Ordina per" defaultValue="scadenza">
          <option value="scadenza">Scadenza più vicina</option>
          <option value="recenti">Più recenti</option>
          <option value="importo">Importo più alto</option>
        </Select>
        <Select label="Ruolo (disattivato)" disabled defaultValue="">
          <option value="">Ruolo</option>
        </Select>
      </Blocco>

      <Blocco nome="Checkbox" nota="Etichetta e aiuto a destra; l'aiuto è collegato con aria-describedby.">
        <div className="flex flex-col gap-3">
          <Checkbox label="Micro" defaultChecked />
          <Checkbox label="Piccola" />
          <Checkbox
            label="Conduzione dei terreni da irrigare"
            descrizione="Requisito salvato nel passo «Requisiti»."
            defaultChecked
          />
          <Checkbox label="Media (non disponibile)" disabled />
          <Checkbox label="Grande (non disponibile)" disabled defaultChecked />
        </div>
      </Blocco>

      <Blocco nome="RadioGroup" nota="Fieldset con legend; errore sotto il gruppo con InlineError.">
        <RadioGroup
          nome="ruolo"
          legend="Ruolo"
          opzioni={RUOLI}
          valore={ruolo}
          onChange={(id) => {
            setRuolo(id);
            setRuoloErrore(null);
          }}
        />
        <RadioGroup
          nome="ruolo-errore"
          legend="Ruolo (con errore)"
          opzioni={RUOLI}
          valore={null}
          onChange={() => setRuoloErrore(null)}
          error={ruoloErrore ?? "Scegli un ruolo per continuare."}
        />
        <RadioGroup
          nome="ruolo-spento"
          legend="Ruolo (disattivato)"
          opzioni={RUOLI}
          valore="partner"
          onChange={() => undefined}
          disabled
        />
      </Blocco>

      <Blocco nome="Switch" nota="Effetto immediato; role=switch e aria-checked.">
        <Switch label="Avvisi email" checked={avvisi} onChange={setAvvisi} />
        <Switch
          label="Profilo partner visibile"
          descrizione="Le altre aziende vedono il tuo profilo nelle call."
          checked={profilo}
          onChange={setProfilo}
        />
        <Switch label="Non modificabile" checked disabled onChange={() => undefined} />
      </Blocco>

      <Blocco nome="PasswordField" nota="«Mostra / Nascondi» dentro il campo; sostituisce le quattro copie.">
        <div className="flex w-full max-w-md flex-col gap-4">
          <PasswordField
            label="Password"
            autoComplete="current-password"
            defaultValue="una-password"
            helper="Almeno 8 caratteri."
          />
          <PasswordField
            label="Nuova password"
            autoComplete="new-password"
            required
            error="La password è troppo corta: servono almeno 8 caratteri."
          />
          <PasswordField label="Password (disattivata)" disabled defaultValue="segreta" />
        </div>
      </Blocco>

      <Blocco
        nome="PasswordStrengthMeter"
        nota="Sotto la password nuova: tre segmenti e una parola, informativo (non blocca l'invio). Scrivi per vederlo."
      >
        <div className="flex w-full max-w-md flex-col gap-2">
          <PasswordField
            label="Scegli una password"
            autoComplete="new-password"
            value={nuovaPassword}
            onChange={(e) => setNuovaPassword(e.target.value)}
          />
          <PasswordStrengthMeter password={nuovaPassword} userInputs={["bandofit"]} />
        </div>
      </Blocco>

      <Blocco nome="AuthLayout" nota="Due colonne da lg (modulo a sinistra, laterale su desk); qui in un riquadro.">
        <AuthLayout
          className="w-full min-h-0 overflow-hidden rounded-panel border border-line"
          laterale={
            <div className="flex flex-col gap-6">
              <p className="text-title-page text-ink">
                Fa per me?
                <br />
                Quanto vale?
                <br />
                Entro quando?
              </p>
              <p className="text-body text-ink-2">
                Qui vanno due righe di esempio dell'elenco (arrivano con BandoRow).
              </p>
            </div>
          }
        >
          <form className="flex flex-col gap-4" onSubmit={(e) => e.preventDefault()}>
            <h2 className="text-title-page text-ink">Accedi</h2>
            <TextField label="Email" type="email" autoComplete="email" />
            <PasswordField label="Password" autoComplete="current-password" />
            <Button type="submit" className="w-full">
              Accedi
            </Button>
          </form>
        </AuthLayout>
      </Blocco>
    </div>
  );
}
