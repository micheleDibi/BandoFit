from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Errore applicativo con codice macchina e messaggio per l'utente."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, message: str = "Risorsa non trovata"):
        super().__init__(404, "not_found", message)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Autenticazione richiesta o token non valido"):
        super().__init__(401, "unauthorized", message)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Operazione non consentita"):
        super().__init__(403, "forbidden", message)


class ConflictError(AppError):
    def __init__(self, message: str = "Conflitto con lo stato attuale"):
        super().__init__(409, "conflict", message)


class BadRequestError(AppError):
    def __init__(self, message: str = "Richiesta non valida"):
        super().__init__(400, "bad_request", message)


class UpstreamError(AppError):
    """Errore dei servizi Supabase a monte (timeout, indisponibilità)."""

    def __init__(self, message: str = "Servizio dati momentaneamente non disponibile"):
        super().__init__(502, "upstream_error", message)


def _error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(exc.code, exc.message))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body("http_error", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body("validation_error", "Parametri della richiesta non validi"),
        )
