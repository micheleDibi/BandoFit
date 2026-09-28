"""Schemi del bilancio ufficiale on-demand (WP2): richieste di bilancio ottico
per l'azienda attiva, pagate con l'addon consumabile «bilancio-ufficiale».

L'identificativo della richiesta presso il provider non esce mai verso il
client, e nemmeno i documenti: il PDF si scarica da una rotta dedicata con
autorizzazione live.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_validator

from app.schemas.plan import TipoPrezzo

# Stati della richiesta (CHECK cbr_stato_check della 0033). Aperti: in_invio,
# in_lavorazione, esito_ignoto; gli altri sono terminali.
StatoRichiestaBilancio = Literal[
    "in_invio",
    "in_lavorazione",
    "esito_ignoto",
    "completata",
    "non_disponibile",
    "annullata",
    "errore",
]

# Perché una richiesta è finita senza bilancio (CHECK cbr_errore_codice_check).
ErroreRichiestaBilancio = Literal[
    "bilancio_non_disponibile",
    "forma_non_ammessa",
    "identificativo_non_valido",
    "credito_provider",
    "non_inviata",
    "errore_provider",
    "scaduta",
    "esito_ignoto_scaduto",
]

# Esito della lettura dell'XBRL su una richiesta completata.
XbrlEsito = Literal[
    "ok",
    "assente",
    "firmato_non_leggibile",
    "non_valido",
    "consolidato",
    "cf_non_corrispondente",
    "troppo_grande",
]

ANNO_MINIMO = 2000


class BilancioRichiestaIn(BaseModel):
    """Body di POST /me/company/bilanci/ufficiale. `anno` None = ultimo
    bilancio disponibile."""

    anno: int | None = None

    @field_validator("anno")
    @classmethod
    def _anno_valido(cls, valore: int | None) -> int | None:
        if valore is None:
            return None
        if not ANNO_MINIMO <= valore <= date.today().year:
            raise ValueError(f"l'anno deve essere tra {ANNO_MINIMO} e l'anno corrente")
        return valore


class BilancioRichiestaOut(BaseModel):
    id: str
    stato: StatoRichiestaBilancio
    anno_richiesto: int | None = None
    anno_bilancio: int | None = None
    errore_codice: ErroreRichiestaBilancio | None = None
    messaggio: str | None = None  # italiano semplice, pronto da mostrare
    xbrl_esito: XbrlEsito | None = None
    avvisi_count: int = 0
    rimborsata: bool = False  # unità dell'addon restituita automaticamente
    pdf_disponibile: bool = False
    created_at: str
    completata_at: str | None = None


class AddonBreve(BaseModel):
    """L'addon collegato, nella forma che serve a `prezzoDisplay` nel FE."""

    slug: str
    nome: str
    tipo_prezzo: TipoPrezzo = "importo"
    etichetta_prezzo: str | None = None
    prezzo: Decimal


class BilanciUfficialiOut(BaseModel):
    editable: bool
    richiedibile: bool
    motivo_non_richiedibile: str | None = None
    addon: AddonBreve | None = None  # None se l'addon non è attivo
    quantita: int = 0  # unità nell'inventario del titolare
    anni_acquisiti: list[int] = []  # esercizi già registrati da un XBRL (ruolo corrente)
    richieste: list[BilancioRichiestaOut] = []  # più recenti prima, al massimo 20
