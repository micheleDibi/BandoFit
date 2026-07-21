from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalizza_features(v: list[str] | None) -> list[str] | None:
    """Trim, via le righe vuote, [] → None (così il frontend fa un semplice
    test di verità). Limiti: max 8 voci da 120 caratteri."""
    if v is None:
        return None
    voci = [r.strip() for r in v if isinstance(r, str) and r.strip()]
    if len(voci) > 8:
        raise ValueError("al massimo 8 caratteristiche personalizzate")
    if any(len(r) > 120 for r in voci):
        raise ValueError("ogni caratteristica può avere al massimo 120 caratteri")
    return voci or None

# Come mostrare il prezzo: 'importo' (€), 'gratis' («Gratis»), 'su_richiesta'
# (etichetta al posto del prezzo; l'item non è attivabile self-serve).
TipoPrezzo = Literal["importo", "gratis", "su_richiesta"]


class PlanOut(BaseModel):
    id: int
    nome: str
    slug: str
    descrizione: str | None = None
    prezzo_annuale: Decimal
    # Default 'importo' per robustezza: gli embed di user_service serializzano
    # il piano con lo stesso schema.
    tipo_prezzo: TipoPrezzo = "importo"
    etichetta_prezzo: str | None = None
    ai_check: int
    alert_attivo: bool
    alert_giorni_preavviso: int | None = None
    # Alert nuovi-bandi (0021): giorni di ritardo dalla pubblicazione.
    # NULL = feature esclusa dal piano anche con alert_attivo.
    alert_ritardo_giorni: int | None = None
    num_account_aziendali: int
    # Numero di aziende gestibili col piano (asse distinto da
    # num_account_aziendali = posti persona). Default 1 per robustezza: gli
    # embed di user_service serializzano il piano con lo stesso schema e uno
    # schema non ancora migrato non deve rompere /me.
    max_aziende: int = 1
    # Bullet custom della card piano (0029, usata dal piano «tailored»):
    # se valorizzata sostituisce i tre punti standard derivati dai campi
    # numerici. Default None per robustezza (embed pre-migration).
    features_override: list[str] | None = None
    ordering: int
    is_active: bool
    updated_at: datetime | None = None


class PlanCreate(BaseModel):
    nome: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9-]+$")
    descrizione: str | None = None
    prezzo_annuale: Decimal = Field(ge=0)
    tipo_prezzo: TipoPrezzo = "importo"
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ai_check: int = Field(ge=0)
    alert_attivo: bool = False
    alert_giorni_preavviso: int | None = Field(default=None, gt=0)
    # 0 = alert il giorno stesso della pubblicazione; None = feature esclusa.
    alert_ritardo_giorni: int | None = Field(default=None, ge=0)
    num_account_aziendali: int = Field(ge=1)
    max_aziende: int = Field(default=1, ge=1)
    features_override: list[str] | None = None
    ordering: int = 0
    is_active: bool = True

    _features = field_validator("features_override")(_normalizza_features)

    @model_validator(mode="after")
    def check_alert_coherence(self) -> "PlanCreate":
        if self.alert_attivo and self.alert_giorni_preavviso is None:
            raise ValueError("alert_giorni_preavviso è obbligatorio se gli alert sono attivi")
        return self


class PlanUpdate(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=100)
    descrizione: str | None = None
    prezzo_annuale: Decimal | None = Field(default=None, ge=0)
    tipo_prezzo: TipoPrezzo | None = None
    # None esplicito azzera l'etichetta (come descrizione, via exclude_unset).
    etichetta_prezzo: str | None = Field(default=None, max_length=100)
    ai_check: int | None = Field(default=None, ge=0)
    alert_attivo: bool | None = None
    alert_giorni_preavviso: int | None = Field(default=None, gt=0)
    # None esplicito disattiva gli alert nuovi-bandi per il piano.
    alert_ritardo_giorni: int | None = Field(default=None, ge=0)
    num_account_aziendali: int | None = Field(default=None, ge=1)
    max_aziende: int | None = Field(default=None, ge=1)
    # None esplicito azzera l'override (bullet di nuovo derivate dai campi).
    features_override: list[str] | None = None
    ordering: int | None = None
    is_active: bool | None = None

    _features = field_validator("features_override")(_normalizza_features)
