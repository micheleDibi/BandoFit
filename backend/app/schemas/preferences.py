from pydantic import BaseModel, Field

# Le 7 faccette dei filtri bandi. I nomi coincidono con le colonne `facet`
# di user_preferences; per tipologie/modalita le lookup corrispondenti sono
# tipologie_bando/modalita_erogazione.
FACETS = ("regioni", "settori", "beneficiari", "codici_ateco", "tipologie", "modalita", "programmi")


class PreferencesPayload(BaseModel):
    """Set completo delle preferenze (GET e PUT usano la stessa forma):
    per ogni faccetta, gli id delle lookup del catalogo bandi."""

    regioni: list[int] = Field(default_factory=list, max_length=100)
    settori: list[int] = Field(default_factory=list, max_length=200)
    beneficiari: list[int] = Field(default_factory=list, max_length=100)
    codici_ateco: list[int] = Field(default_factory=list, max_length=200)
    tipologie: list[int] = Field(default_factory=list, max_length=50)
    modalita: list[int] = Field(default_factory=list, max_length=50)
    programmi: list[int] = Field(default_factory=list, max_length=100)
