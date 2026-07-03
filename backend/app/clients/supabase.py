"""Factory dei client Supabase.

- ``primary``: DB primario con service_role (bypassa la RLS) → dati piattaforma.
- ``secondary``: DB secondario con chiave anon → catalogo bandi in SOLA LETTURA
  (le policy RLS del secondario consentono agli anonimi solo SELECT).
"""

from supabase import AsyncClient, acreate_client

from app.core.config import Settings


async def create_primary_client(settings: Settings) -> AsyncClient:
    return await acreate_client(
        settings.primary_supabase_url,
        settings.primary_supabase_service_role_key,
    )


async def create_secondary_client(settings: Settings) -> AsyncClient:
    return await acreate_client(
        settings.secondary_supabase_url,
        settings.secondary_supabase_anon_key,
    )
