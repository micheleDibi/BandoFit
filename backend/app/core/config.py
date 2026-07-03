from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurazione dell'applicazione, letta da variabili d'ambiente / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB primario (piattaforma): il backend usa la service_role, che bypassa la RLS.
    primary_supabase_url: str
    primary_supabase_service_role_key: str
    # JWT secret legacy: necessario solo se il progetto firma i token in HS256.
    primary_supabase_jwt_secret: str = ""

    # DB secondario (catalogo bandi): chiave anon = accesso in sola lettura via RLS.
    secondary_supabase_url: str
    secondary_supabase_anon_key: str

    cors_origins: str = "http://localhost:5173"
    env: str = "development"

    # URL pubblico del frontend (redirect degli inviti, link nelle email).
    frontend_url: str = "http://localhost:5173"

    # Email transazionali via Resend. Chiave vuota = fallback log-only (dev).
    resend_api_key: str = ""
    email_from: str = "BandoFit <onboarding@resend.dev>"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def jwt_issuer(self) -> str:
        return f"{self.primary_supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.jwt_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
