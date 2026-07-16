from functools import lru_cache

from pydantic import ValidationError, model_validator
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
    # «production» accende i controlli d'avvio in fondo alla classe. Lo imposta
    # docker-compose.yml; chi avvia uvicorn a mano eredita «development», quindi
    # quei controlli non scattano — vale come rete di sicurezza del deploy
    # standard, non come garanzia universale.
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

    # Export PDF (scheda azienda + dossier). "auto" = WeasyPrint se importabile
    # (HTML+CSS → PDF, motore principale), altrimenti ReportLab (fallback
    # pure-Python, nessuna libreria di sistema). Forzabile a "weasyprint" o
    # "reportlab". WeasyPrint richiede pango/cairo/gdk-pixbuf nell'immagine.
    pdf_engine: str = "auto"  # auto | weasyprint | reportlab

    # Anti-enumerazione degli endpoint auth pubblici (migration 0025).
    #
    # trusted_proxy_hops: quanti proxy fidati stanno davanti (Cloudflare + nginx
    # = 2, cfr. docs/deploy.md). L'IP del client è l'elemento a -hops di
    # X-Forwarded-For: contare da DESTRA è ciò che rende l'header non
    # spoofabile, visto che ogni hop appende in coda. Il default è quello di
    # produzione di proposito: con 0 il peer verrebbe preso per il client, e in
    # Docker il peer è il gateway della bridge — uguale per tutti, quindi il
    # primo abusatore bloccherebbe l'intero pianeta. In sviluppo (senza proxy)
    # l'IP resta semplicemente ignoto e il limite per IP non si applica.
    trusted_proxy_hops: int = 2
    # Pepper dell'HMAC con cui si costruiscono i bucket: a DB non finiscono mai
    # IP o email in chiaro. Vuoto = hash non peppato, tollerato solo in
    # sviluppo: con ENV=production il backend si rifiuta di partire (sotto).
    rate_limit_pepper: str = ""
    # Soglie di POST /auth/register. Le PMI stanno dietro NAT aziendale: il
    # burst è tarato su quello, non sul singolo utente domestico.
    register_ip_burst_limit: int = 5
    register_ip_burst_window_seconds: int = 900
    register_ip_daily_limit: int = 50
    register_email_hourly_limit: int = 5
    # Cap globale di SOLO ALLARME: oltre soglia logga e basta, non rifiuta. Se
    # rifiutasse, un solo IP con poche centinaia di richieste spegnerebbe la
    # registrazione a tutti — un DoS creato dalla difesa stessa.
    register_global_hourly_alert: int = 200
    # Durata minima della risposta di /auth/register: livella il ramo «email
    # esistente» (che non crea nulla) e il ramo «email nuova» (create_user +
    # token), altrimenti il tempo di risposta rivela ciò che il body nasconde.
    register_latency_target_seconds: float = 1.5

    # AI-check (API Anthropic). Chiave vuota = feature disattivata (le rotte
    # rispondono 503 ai_not_configured). Ogni report costa ~0,10 $ di API.
    anthropic_api_key: str = ""
    ai_check_model: str = "claude-sonnet-5"
    ai_check_timeout_seconds: float = 120.0
    # Minuti minimi tra due generazioni per la stessa coppia azienda × bando.
    ai_check_cooldown_minutes: int = 5

    @model_validator(mode="after")
    def _segreti_obbligatori_in_produzione(self) -> "Settings":
        """Impedisce l'avvio in produzione senza i segreti che degradano in muto.

        Il criterio per stare qui è preciso: una configurazione mancante che
        SPEGNE una feature non serve (openapi e anthropic hanno già `enabled` e
        rispondono 503: si vede). Serve per ciò che non si spegne affatto.
        rate_limit_pepper vuoto è l'unico caso: rate_limit_service continua a
        contare, e a cadere è solo la segretezza dei bucket — la tabella
        auth_rate_limits torna un dizionario di email e IP attaccabile offline
        da chi ne ottenga un dump, cioè esattamente ciò che l'HMAC deve
        impedire. L'unico segnale odierno è un logger.debug, invisibile al
        livello INFO di produzione.

        Fallire all'avvio non introduce un meccanismo nuovo: è già ciò che
        succede alle chiavi Supabase, che non hanno default e fanno morire
        Settings() all'import di app.main. Meglio un container che non parte di
        uno che parte e mente.
        """
        if self.env.strip().lower() == "production" and not self.rate_limit_pepper.strip():
            raise ValueError(
                "RATE_LIMIT_PEPPER è obbligatorio con ENV=production: generane uno "
                "con `openssl rand -hex 32` e mettilo nel .env (docs/deploy.md). "
                "Sceglilo una volta sola: cambiarlo azzera i contatori in corso."
            )
        return self

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
    """Le Settings del processo, costruite una volta sola.

    Il try/except non è cerimonia: il messaggio di una ValidationError di
    pydantic include `input_value`, un estratto del dict di tutte le variabili
    lette. Pydantic lo tronca a testa e coda, quindi non trapela tutto — ma
    trapela, e COSA trapeli dipende dall'ordine dei campi, cioè dal caso: basta
    che in coda finisca una chiave API viva. Siccome Settings si costruisce
    all'import di app.main, un container mal configurato la stamperebbe nel
    traceback, e i log di produzione si conservano. Si tengono i motivi
    («primary_supabase_url: Field required»), si buttano i valori.

    `from None` è portante quanto la redazione: senza, il traceback aggiunge
    «The above exception was the direct cause» e sotto ristampa la
    ValidationError originale, dump al seguito.
    """
    try:
        return Settings()
    except ValidationError as exc:
        motivi = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(configurazione)'}: {e['msg']}"
            for e in exc.errors()
        )
        raise RuntimeError(f"Configurazione non valida — {motivi}") from None
