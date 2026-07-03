# API Backend

> Documento in costruzione: gli endpoint vengono documentati man mano che sono implementati.

Base URL: `http://localhost:8000/api/v1` (sviluppo). Documentazione interattiva: `http://localhost:8000/docs` (Swagger UI).

Autenticazione: header `Authorization: Bearer <access_token>` (JWT emesso da Supabase Auth del progetto primario). Gli endpoint `/admin/*` richiedono ruolo `admin`.

Formato errori:
```json
{ "error": { "code": "not_found", "message": "Bando non trovato" } }
```
