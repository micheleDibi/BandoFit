"""Criteri tipizzati del modulo partenariati (WP5, docs/partenariati.md C1).

Un `CriterioPartner` è l'UNICA primitiva di copertura: requisiti della call,
vincoli delle posizioni, matching (WP6), candidature (WP7) e validatore (WP8)
lo valutano tutti con la stessa funzione pura
(`services/partenariato_criteri.valuta_criterio`). Il testo libero non conta
mai in automatico: un requisito descritto solo a parole è `manuale`
(non valutabile).

Unione discriminata su `tipo`, STRICT e `extra='forbid'`: i codici vengono solo
dal vocabolario v1 (`Literal` condivisi con `partenariato_vocabolario`), gli id
sono interi veri (niente "3" come stringa), un campo sconosciuto è un errore.
Gli errori sono 422 (`ValidationError`): il criterio lo compone l'editor
tipizzato del wizard, non l'utente a mano libera. Il criterio si salva come
jsonb (`partner_call_requisiti.criterio`) e si rilegge con `CRITERIO_ADAPTER`.

`ProfiloCandidato` raccoglie i dati deterministici di UN'azienda su cui si
valuta il criterio; lo costruisce `partenariato_criteri.profilo_candidato_da`
dalle righe già lette (registro, profilo partner, bilanci).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.schemas.partenariato_vocabolario import Competenza, RuoloPartenariato, TipoSoggetto
from app.schemas.partner_profile import PAESI_ISO2
from app.schemas.regole_finanziarie import RegolaFinanziaria

if TYPE_CHECKING:  # solo per i tipi: gli schemi non importano i servizi
    from app.services.bilanci_indicatori import EsercizioBilancio, Fasce

# ------------------------------------------------------------------ domini

# Classi dimensionali del registro (company_data.derived.classe_dimensionale).
Dimensione = Literal["micro", "piccola", "media", "grande"]

# Categorie di `partner_profilo_pubblico.CATEGORIE_CERTIFICAZIONE` (WP4), nello
# stesso ordine: le certificazioni si confrontano per categoria, come le vedono
# i terzi nei profili anonimi (test di uguaglianza).
CategoriaCertificazione = Literal[
    "qualita",
    "ambiente",
    "sicurezza_lavoro",
    "sicurezza_informazioni",
    "energia",
    "appalti_soa",
    "settoriale",
    "altro",
]

# «sede_attuale»: una sede (legale o unità locale) nella regione già oggi;
# «sede_entro_erogazione»: basta aprirla entro l'erogazione (senza sede oggi
# l'esito è `incerto`, mai `non_coperto`).
ModalitaRegione = Literal["sede_attuale", "sede_entro_erogazione"]

# consorzio = basta un membro (i gap, le voci «cercate»); ogni_membro = vale
# per ciascun membro (territorio, regole finanziarie per partner): filtro rigido.
AmbitoRequisito = Literal["consorzio", "ogni_membro"]

TipoCriterio = Literal[
    "tipo_soggetto",
    "tag",
    "regione",
    "paese",
    "ateco",
    "settore",
    "dimensione",
    "certificazione",
    "esperienza",
    "regola_finanziaria",
    "manuale",
]

# Cardinalità massime (prudenti: servono a respingere input anomali).
MAX_VALORI = 30
MAX_COMPETENZE = 15
MAX_REGIONI = 21
MAX_PAESI = 30
MAX_DIVISIONI_ATECO = 20
MAX_ID_LOOKUP = 30

_ALIAS_PAESI = {"EL": "GR", "UK": "GB"}

IdLookup = Annotated[int, Field(ge=1)]
DivisioneAteco = Annotated[str, Field(pattern=r"^[0-9]{2}$")]


def _dedup(valori: list) -> list:
    return list(dict.fromkeys(valori))


class _Criterio(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _senza_doppioni(self):
        # Liste di codici o id: doppioni tolti, ordine della prima comparsa.
        for nome in type(self).model_fields:
            valore = getattr(self, nome)
            if isinstance(valore, list):
                setattr(self, nome, _dedup(valore))
        return self


class CriterioTipoSoggetto(_Criterio):
    """Il membro è di ALMENO UNO dei tipi indicati."""

    tipo: Literal["tipo_soggetto"] = "tipo_soggetto"
    valori: list[TipoSoggetto] = Field(min_length=1, max_length=MAX_VALORI)


class CriterioTag(_Criterio):
    """Competenze del vocabolario dichiarate nel profilo partner."""

    tipo: Literal["tag"] = "tag"
    tags: list[Competenza] = Field(min_length=1, max_length=MAX_COMPETENZE)
    modalita: Literal["almeno_uno", "tutti"] = "almeno_uno"


class CriterioRegione(_Criterio):
    """Una sede (TUTTE le sedi del registro: legale e unità locali) in una
    delle regioni (id della lookup `regioni` del catalogo)."""

    tipo: Literal["regione"] = "regione"
    regioni_ids: list[IdLookup] = Field(min_length=1, max_length=MAX_REGIONI)
    modalita: ModalitaRegione = "sede_attuale"


class CriterioPaese(_Criterio):
    """Paese della sede (ISO 3166-1 alpha-2). `escludi=True` rovescia il
    criterio: il membro NON deve essere di uno dei paesi."""

    tipo: Literal["paese"] = "paese"
    paesi: list[str] = Field(min_length=1, max_length=MAX_PAESI)
    escludi: bool = False

    @field_validator("paesi", mode="before")
    @classmethod
    def _iso2(cls, valori):
        if not isinstance(valori, list):
            return valori  # l'errore di tipo lo dà la validazione strict
        out = []
        for valore in valori:
            if not isinstance(valore, str):
                raise ValueError("codice paese non valido")
            codice = valore.strip().upper()
            codice = _ALIAS_PAESI.get(codice, codice)
            if codice not in PAESI_ISO2:
                raise ValueError(f"codice paese non valido: {valore[:10]!r}")
            out.append(codice)
        return _dedup(out)


class CriterioAteco(_Criterio):
    """Divisioni ATECO a 2 cifre (principale o secondarie del registro)."""

    tipo: Literal["ateco"] = "ateco"
    divisioni: list[DivisioneAteco] = Field(min_length=1, max_length=MAX_DIVISIONI_ATECO)


class CriterioSettore(_Criterio):
    """Settori della lookup `settori` del catalogo."""

    tipo: Literal["settore"] = "settore"
    settori_ids: list[IdLookup] = Field(min_length=1, max_length=MAX_ID_LOOKUP)


class CriterioDimensione(_Criterio):
    """Classe dimensionale dal Registro Imprese (mai quella dichiarata)."""

    tipo: Literal["dimensione"] = "dimensione"
    valori: list[Dimensione] = Field(min_length=1, max_length=4)


class CriterioCertificazione(_Criterio):
    """Almeno una certificazione di una delle categorie."""

    tipo: Literal["certificazione"] = "certificazione"
    categorie: list[CategoriaCertificazione] = Field(min_length=1, max_length=8)


class CriterioEsperienza(_Criterio):
    """Esperienza dichiarata in uno dei programmi (id della lookup
    `programmi`), eventualmente con il ruolo indicato."""

    tipo: Literal["esperienza"] = "esperienza"
    programmi_ids: list[IdLookup] = Field(min_length=1, max_length=MAX_ID_LOOKUP)
    ruolo: RuoloPartenariato | None = None


class RegolaCriterio(RegolaFinanziaria):
    """Il contratto unico WP1, qui chiuso (niente campi in più) e coerente
    (`bilanci_indicatori.valida_regola` senza errori)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def _coerente(self) -> "RegolaCriterio":
        # Import locale: gli schemi non dipendono dai servizi al caricamento.
        from app.services.bilanci_indicatori import valida_regola

        errori = valida_regola(self)
        if errori:
            raise ValueError("regola finanziaria non valida: " + "; ".join(errori))
        return self


class CriterioRegolaFinanziaria(_Criterio):
    """Regola finanziaria (contratto WP1). Valutata sui valori esatti solo in
    vista «proprio»; verso terzi sulle FASCE (Q11)."""

    tipo: Literal["regola_finanziaria"] = "regola_finanziaria"
    regola: RegolaCriterio


class CriterioManuale(_Criterio):
    """Requisito descritto solo a testo: non si valuta mai in automatico."""

    tipo: Literal["manuale"] = "manuale"


CriterioPartner = Annotated[
    Union[
        CriterioTipoSoggetto,
        CriterioTag,
        CriterioRegione,
        CriterioPaese,
        CriterioAteco,
        CriterioSettore,
        CriterioDimensione,
        CriterioCertificazione,
        CriterioEsperienza,
        CriterioRegolaFinanziaria,
        CriterioManuale,
    ],
    Field(discriminator="tipo"),
]

# Per rileggere il jsonb (`CRITERIO_ADAPTER.validate_python(riga["criterio"])`)
# e per serializzarlo (`dump_python(criterio, mode="json")`).
CRITERIO_ADAPTER: TypeAdapter[CriterioPartner] = TypeAdapter(CriterioPartner)


# ------------------------------------------------------------------ esito

EsitoCopertura = Literal["coperto", "non_coperto", "dato_mancante", "incerto", "non_valutabile"]
FonteCriterio = Literal["registro", "bilanci", "dichiarato"]


class EsitoCriterio(BaseModel):
    """Esito di un criterio su un'azienda.

    - `testo_pubblico` dipende SOLO dal criterio e dall'esito (mai dai dati
      dell'azienda valutata): si può mostrare anche a terzi;
    - `testo_privato` esiste solo in vista «proprio» (per l'azienda stessa) e
      può citare i suoi dati, anche i valori esatti di bilancio."""

    esito: EsitoCopertura
    fonte: FonteCriterio | None = None
    testo_pubblico: str
    testo_privato: str | None = None


# ------------------------------------------------------------ profilo

# Intervallo chiuso (minimo, massimo) in euro: un punto se il valore è esatto.
# Il massimo può essere Decimal("Infinity") per le fasce senza tetto.
Intervallo = tuple[Decimal, Decimal]


@dataclass(frozen=True)
class EsperienzaCandidato:
    programma_id: int | None
    ruolo: str | None


@dataclass(frozen=True)
class ProfiloCandidato:
    """Dati deterministici di UN'azienda per `valuta_criterio`.

    Registro Imprese (fonte «registro», solo se `registro_presente`): classe
    dimensionale, flag delle sezioni speciali, forma giuridica, tipi di
    soggetto dedotti, regioni di TUTTE le sedi, paese, divisioni ATECO.
    Profilo partner (fonte «dichiarato», solo se `profilo_presente`): tipi
    dichiarati (mai quelli che vengono dal registro), competenze, categorie
    delle certificazioni, esperienze. `settori_ids` = settore dichiarato
    dell'azienda. Bilanci (fonte «bilanci»): esercizi fusi, completezza dello
    storico e fasce (le uniche cose dei bilanci che possono uscire verso terzi).
    """

    registro_presente: bool = False
    classe_dimensionale: Dimensione | None = None
    # startup_innovativa, pmi_innovativa, impresa_artigiana, certificazione_soa:
    # True/False dal registro, assente = ignoto.
    flags: dict[str, bool] = field(default_factory=dict)
    forma_giuridica: str | None = None
    tipi_registro: tuple[str, ...] = ()
    regioni_ids: frozenset[int] = frozenset()
    paese: str | None = None
    ateco_divisioni: tuple[str, ...] = ()

    profilo_presente: bool = False
    tipi_dichiarati: tuple[str, ...] = ()
    competenze: tuple[str, ...] = ()
    certificazioni_categorie: tuple[str, ...] = ()
    esperienze: tuple[EsperienzaCandidato, ...] = ()

    settori_ids: frozenset[int] = frozenset()

    esercizi: tuple["EsercizioBilancio", ...] = ()
    storico_completo: bool = False
    fasce: "Fasce | None" = None
