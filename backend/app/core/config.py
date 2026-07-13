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

    # Base pubblica dell'API (come VITE_API_BASE_URL, include /api/v1): serve
    # per i link assoluti nelle email che puntano al backend (unsubscribe).
    api_public_url: str = "http://localhost:8000/api/v1"

    # Alert email sui nuovi bandi (migration 0021). L'aritmetica dei ritardi
    # è su DATE in alert_fuso; la data di attivazione è il gate no-backfill:
    # in produzione va impostata alla data del deploy della feature.
    alert_scheduler_attivo: bool = True
    alert_ora_invio: str = "08:00"
    alert_fuso: str = "Europe/Rome"
    alert_data_attivazione: str = "2026-07-13"
    alert_orizzonte_giorni: int = 60
    alert_max_tentativi: int = 3
    alert_pausa_invii_secondi: float = 0.7

    # Istanza Jitsi self-hosted (APERTA, senza JWT) per le videochiamate
    # delle consulenze. URL stanza = {base}/bandofit-{videocall_token}: a DB
    # vive solo il token, l'URL è derivato.
    jitsi_base_url: str = "https://bandofitvtc.edunews24.it"

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
    # Vita dell'anteprima già pagata, in attesa di conferma. Più lungo del
    # cooldown: chi annulla e ci ripensa non deve ripagare il fetch.
    company_import_draft_ttl_minutes: int = 30

    # Slug dell'addon che attiva il flusso consulenze (l'addon vive a catalogo
    # nel DB; la migration 0017 lo garantisce con un seed idempotente).
    consulting_addon_slug: str = "consulto-esperto"

    # AI-check (API Anthropic). Chiave vuota = feature disattivata (le rotte
    # rispondono 503 ai_not_configured). Ogni report costa ~0,10 $ di API.
    anthropic_api_key: str = ""
    ai_check_model: str = "claude-sonnet-5"
    ai_check_timeout_seconds: float = 120.0
    # Minuti minimi tra due generazioni per la stessa coppia azienda × bando.
    ai_check_cooldown_minutes: int = 5

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
