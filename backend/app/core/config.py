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

    # Email transazionali. Provider scelto automaticamente:
    # SMTP se smtp_host è valorizzato → altrimenti Resend se c'è la API key →
    # altrimenti le email vengono solo loggate (sviluppo).
    # SMTP (es. OVH: host ssl0.ovh.net, porta 465 SSL o 587 STARTTLS)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    # Resend (alternativa API HTTP)
    resend_api_key: str = ""
    # Mittente, es. "BandoFit <noreply@tuodominio.it>" — con SMTP deve essere
    # un indirizzo autorizzato della casella/dominio.
    email_from: str = "BandoFit <onboarding@resend.dev>"

    # openapi.it (dati aziendali certificati / verifica CF). Credenziali vuote
    # = integrazione disattivata (le rotte rispondono 503 openapi_not_configured).
    # ATTENZIONE: le chiavi API di sandbox e produzione sono DIVERSE — la chiave
    # deve corrispondere all'ambiente scelto in openapi_env.
    openapi_email: str = ""
    openapi_api_key: str = ""
    openapi_env: str = "sandbox"  # sandbox | production
    openapi_timeout_seconds: float = 30.0
    # Minuti minimi tra due import della stessa azienda (ogni import costa credito).
    company_import_cooldown_minutes: int = 10

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
