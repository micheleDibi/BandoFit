"""Moderazione DSA dei partenariati (WP9, W2): ingressi e proiezioni.

Verso chi ha segnalato e verso l'autore del contenuto (`SegnalazioneEsitoOut`)
solo lo stretto necessario: MAI l'identità dell'altra parte (nessun id di
utenti o aziende), mai la descrizione né lo snapshot verso l'autore, lo
statement of reasons solo all'autore. Verso l'admin (`SegnalazioneAdminOut`)
il contenuto segnalato come lo vedeva chi ha segnalato, ma nessun id di
utente (`segnalante_user_id`, `deciso_da` restano nel DB).

I limiti dei testi sono quelli della 0041 (motivazioni e ricorso 20..2000):
fuori limite 400 con lo stesso code che darebbe la RPC."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.errors import AppError
from app.schemas.partner_call import MotivoSegnalazione, OggettoSegnalazione

StatoSegnalazione = Literal[
    "ricevuta", "in_esame", "decisa", "ricorso_presentato", "ricorso_deciso"
]
FiltroCoda = Literal[
    "aperte", "ricevuta", "in_esame", "decisa", "ricorso_presentato", "ricorso_deciso", "tutte"
]
Decisione = Literal["nessuna_azione", "contenuto_rimosso", "call_sospesa", "profilo_sospeso"]
EsitoRicorso = Literal["confermata", "riformata"]
RuoloSegnalazione = Literal["autore", "segnalante"]

TESTO_MIN = 20
TESTO_MAX = 2000
# Sospensione d'ufficio: la motivazione sta per intero nella notifica in-app
# (statement of reasons in forma breve) e sull'oggetto (`sospeso_motivo`).
SOSPENSIONE_MAX = 500
MSG_MOTIVAZIONE = "La motivazione deve avere tra 20 e 2000 caratteri"
MSG_MOTIVAZIONE_SOSPENSIONE = "La motivazione deve avere tra 20 e 500 caratteri"
MSG_RICORSO = "Il ricorso deve avere tra 20 e 2000 caratteri"


def testo_limitato(valore: Any, code: str, messaggio: str, massimo: int = TESTO_MAX) -> str:
    """Testo ripulito dagli spazi ai bordi e lungo 20..`massimo` (come la RPC,
    che conta i caratteri dopo `btrim`): altrimenti 400 `code`."""
    testo = valore.strip() if isinstance(valore, str) else ""
    if not TESTO_MIN <= len(testo) <= massimo:
        raise AppError(400, code, messaggio)
    return testo


class _Motivata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    motivazione: str

    @field_validator("motivazione", mode="before")
    @classmethod
    def _motivazione(cls, valore: Any) -> str:
        return testo_limitato(valore, "motivazione_non_valida", MSG_MOTIVAZIONE)


class DecisioneIn(_Motivata):
    """POST /admin/partenariati/segnalazioni/{id}/decidi (e anteprima).
    La motivazione la leggono sia l'autore sia chi ha segnalato: niente dati
    dell'una o dell'altra parte."""

    decisione: Decisione


class RicorsoDecisioneIn(_Motivata):
    """POST /admin/partenariati/segnalazioni/{id}/ricorso/decidi."""

    esito: EsitoRicorso


class SospensioneIn(_Motivata):
    """POST /admin/partenariati/{oggetto_tipo}/{id}/ripristina."""


class SospendiIn(BaseModel):
    """POST /admin/partenariati/{oggetto_tipo}/{id}/sospendi: motivazione
    20..500. Una sospensione d'ufficio non ha una pagina della segnalazione:
    l'autore legge lo statement of reasons nella notifica in-app (forma breve,
    con la motivazione per intero) e per email (testo completo)."""

    model_config = ConfigDict(extra="forbid")

    motivazione: str

    @field_validator("motivazione", mode="before")
    @classmethod
    def _motivazione(cls, valore: Any) -> str:
        return testo_limitato(valore, "motivazione_non_valida", MSG_MOTIVAZIONE_SOSPENSIONE,
                              SOSPENSIONE_MAX)


class ContestoCompletoIn(_Motivata):
    """POST /admin/partenariati/segnalazioni/{id}/contesto: la conversazione
    intera, con la motivazione (20..2000) nel corpo della richiesta — mai
    nell'URL, che finisce nei log di accesso — e registrata nell'audit."""


class RicorsoIn(BaseModel):
    """POST /partenariati/segnalazioni/{id}/ricorso (autore o segnalante)."""

    model_config = ConfigDict(extra="forbid")

    testo: str

    @field_validator("testo", mode="before")
    @classmethod
    def _testo(cls, valore: Any) -> str:
        return testo_limitato(valore, "ricorso_testo_non_valido", MSG_RICORSO)


class StatementOut(BaseModel):
    """Anteprima dello statement of reasons (nessuna scrittura): `testo`
    None con `nessuna_azione` (non c'è una restrizione da motivare)."""

    versione: str
    testo: str | None = None


class RicorsoOut(BaseModel):
    """Il ricorso di una segnalazione (uno solo): chi l'ha presentato, il
    testo (solo a chi l'ha presentato e all'admin) e la decisione."""

    da: RuoloSegnalazione
    testo: str | None = None
    at: datetime | None = None
    esito: EsitoRicorso | None = None
    motivazione: str | None = None
    deciso_at: datetime | None = None


class AutoreAdminOut(BaseModel):
    """Azienda autrice del contenuto segnalato (solo verso l'admin)."""

    company_profile_id: UUID
    ragione_sociale: str | None = None


class SegnalazioneAdminOut(BaseModel):
    """Una segnalazione nella coda dell'admin: il contenuto come lo vedeva
    chi ha segnalato (`contenuto_snapshot`), mai id di utenti."""

    id: UUID
    codice: str
    oggetto_tipo: OggettoSegnalazione
    oggetto_id: str
    motivo: MotivoSegnalazione
    descrizione: str
    contenuto_snapshot: dict[str, Any]
    stato: StatoSegnalazione
    created_at: datetime | None = None
    autore: AutoreAdminOut | None = None
    decisione: Decisione | None = None
    # La decisione dopo il ricorso: `riformata` la ribalta (nessuna_azione ↔
    # la restrizione del tipo di contenuto); senza ricorso accolto = decisione.
    decisione_effettiva: Decisione | None = None
    motivazione: str | None = None
    sor_testo: str | None = None
    deciso_at: datetime | None = None
    # Termine del ricorso interno (6 mesi dalla decisione).
    ricorso_entro: datetime | None = None
    ricorso: RicorsoOut | None = None
    # Esito dell'effetto sull'oggetto dell'ultima operazione (applicato,
    # gia_applicato, annullato, mantenuto, …): solo nelle risposte alle POST.
    effetto: str | None = None


class SegnalazioneEsitoOut(BaseModel):
    """GET /partenariati/segnalazioni/{id}: la segnalazione come la vede chi
    l'ha fatta (`ruolo = segnalante`) o l'azienda autrice del contenuto
    (`ruolo = autore`, solo se una restrizione l'ha riguardata)."""

    id: UUID
    codice: str
    ruolo: RuoloSegnalazione
    oggetto_tipo: OggettoSegnalazione
    motivo: MotivoSegnalazione
    stato: StatoSegnalazione
    created_at: datetime | None = None
    # Solo a chi ha segnalato: la propria descrizione.
    descrizione: str | None = None
    decisione: Decisione | None = None
    # La decisione dopo il ricorso (vedi `SegnalazioneAdminOut`): per
    # l'autore di un contenuto sospeso dal ricorso accolto di chi aveva
    # segnalato è la restrizione, mentre `decisione` resta `nessuna_azione`.
    decisione_effettiva: Decisione | None = None
    motivazione: str | None = None
    deciso_at: datetime | None = None
    # Solo all'autore: lo statement of reasons (DSA art. 17).
    sor_testo: str | None = None
    ricorso: RicorsoOut | None = None
    # Chi guarda può presentare il ricorso adesso.
    ricorso_possibile: bool = False
    # Termine del ricorso, solo per chi ne ha diritto su questa decisione.
    ricorso_entro: datetime | None = None
    # Chi guarda può agire (il segnalante; per l'autore solo il titolare).
    editable: bool = False


class MessaggioContestoOut(BaseModel):
    """Un messaggio della conversazione per l'admin. `lato`: dell'azienda
    autrice del messaggio segnalato o dell'altra; mai id di utenti o
    aziende. Anche i messaggi oscurati si leggono (servono alla decisione e
    al ricorso)."""

    id: int
    lato: Literal["autore", "altra"]
    testo: str | None = None
    oscurato: bool = False
    segnalato: bool = False
    created_at: datetime | None = None


class ContestoOut(BaseModel):
    """GET /admin/partenariati/segnalazioni/{id}/contesto: finestra di ±10
    messaggi attorno a quello segnalato (`completo = false`); POST sulla
    stessa rotta: la conversazione intera (`completo = true`, solo con una
    motivazione registrata in audit). `altri_prima`/`altri_dopo`: la finestra non
    arriva all'inizio o alla fine; `troncato`: la conversazione intera
    supera il tetto di lettura."""

    completo: bool
    messaggi: list[MessaggioContestoOut]
    altri_prima: bool = False
    altri_dopo: bool = False
    troncato: bool = False


class SospensioneOut(BaseModel):
    """Esito di una sospensione o di un ripristino diretto dell'admin."""

    oggetto_tipo: OggettoSegnalazione
    oggetto_id: str
    esito: str
    stato: str | None = None
    modificato: bool
