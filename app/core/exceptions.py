"""
Global exception translation.

Routes keep raising HTTPException with their current detail. This module turns
that detail into a short Spanish message and a stable code before it leaves
the API. Unexpected failures are logged and never include the exception text.
"""

import logging
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("lol_analyzer.errors")

UNEXPECTED = "Ocurrió un error. Intenta nuevamente más tarde."

# English details already raised across the API, keyed in lowercase.
_KNOWN: dict[str, tuple[str, str]] = {
    "an account with this email address already exists.": (
        "account_exists",
        "Ya existe una cuenta con ese correo.",
    ),
    "recovery answers must use a known champion and rank.": (
        "invalid_recovery",
        "Elige un campeón y un rango de la lista.",
    ),
    "incorrect email or password.": (
        "invalid_credentials",
        "El correo o la contraseña no coinciden.",
    ),
    "account is inactive or disabled.": (
        "account_disabled",
        "Esta cuenta está desactivada.",
    ),
    "invalid or unverified google authentication token.": (
        "google_auth",
        "No pudimos verificar el inicio con Google.",
    ),
    "authentication token required to log out.": (
        "session_required",
        "Vuelve a iniciar sesión para salir.",
    ),
    "invalid token structure for logout.": (
        "session_invalid",
        "Tu sesión no es válida. Vuelve a iniciar sesión.",
    ),
    "provided token is not a valid refresh token.": (
        "session_expired",
        "Tu sesión expiró. Vuelve a iniciar sesión.",
    ),
    "refresh token has been revoked.": (
        "session_revoked",
        "Cerraste sesión en este dispositivo. Vuelve a entrar.",
    ),
    "user account no longer exists or is inactive.": (
        "account_missing",
        "No encontramos esa cuenta.",
    ),
    "refresh token is no longer valid.": (
        "session_invalid",
        "Tu sesión ya no es válida. Vuelve a iniciar sesión.",
    ),
    "token signature has expired.": (
        "session_expired",
        "Tu sesión expiró. Vuelve a iniciar sesión.",
    ),
    "invalid authentication token.": (
        "session_invalid",
        "Tu sesión no es válida. Vuelve a iniciar sesión.",
    ),
    "not authenticated. bearer token required.": (
        "session_required",
        "Inicia sesión para continuar.",
    ),
    "invalid token payload structure.": (
        "session_invalid",
        "Tu sesión no es válida. Vuelve a iniciar sesión.",
    ),
    "token has been revoked (user logged out).": (
        "session_revoked",
        "Cerraste sesión. Vuelve a entrar.",
    ),
    "invalid user id in token.": (
        "session_invalid",
        "Tu sesión no es válida. Vuelve a iniciar sesión.",
    ),
    "user associated with token no longer exists.": (
        "account_missing",
        "No encontramos la cuenta de esta sesión.",
    ),
    "user account is inactive or disabled.": (
        "account_disabled",
        "Esta cuenta está desactivada.",
    ),
    "session is no longer valid. sign in again.": (
        "session_invalid",
        "Tu sesión ya no es válida. Vuelve a iniciar sesión.",
    ),
    "no riot account is linked yet.": (
        "riot_not_linked",
        "Todavía no hay una cuenta de Riot vinculada. Hazlo en Ajustes.",
    ),
    "unsupported region.": (
        "region_unsupported",
        "Esa región no está disponible.",
    ),
    "that riot account is already linked to another user.": (
        "riot_taken",
        "Esa cuenta de Riot ya está vinculada a otro usuario.",
    ),
    "riot account or resource was not found.": (
        "riot_not_found",
        "No encontramos ese Riot ID. Revisa el nombre y la etiqueta.",
    ),
    "riot api rate limit reached. try again shortly.": (
        "riot_busy",
        "Riot está recibiendo demasiadas consultas. Intenta de nuevo en unos minutos.",
    ),
    "riot api is temporarily unavailable.": (
        "riot_unavailable",
        "No pudimos consultar Riot en este momento. Intenta de nuevo más tarde.",
    ),
    "riot api key was rejected.": (
        "riot_unavailable",
        "No pudimos consultar Riot en este momento. Intenta de nuevo más tarde.",
    ),
    "riot api is not configured. set riot_api_key on the server.": (
        "riot_unavailable",
        "No pudimos consultar Riot en este momento. Intenta de nuevo más tarde.",
    ),
    "riot api returned an upstream error.": (
        "riot_unavailable",
        "No pudimos consultar Riot en este momento. Intenta de nuevo más tarde.",
    ),
    "riot api rejected the request.": (
        "riot_unavailable",
        "No pudimos consultar Riot en este momento. Intenta de nuevo más tarde.",
    ),
    "match payload did not include the linked player.": (
        "match_unreadable",
        "No pudimos leer esa partida. Intenta de nuevo más tarde.",
    ),
    "riot account response did not include a puuid.": (
        "riot_unavailable",
        "No pudimos confirmar esa cuenta de Riot. Intenta de nuevo más tarde.",
    ),
    "incorrect current password.": (
        "wrong_password",
        "La contraseña actual no coincide.",
    ),
    "incorrect password. account deletion unauthorized.": (
        "wrong_password",
        "La contraseña no coincide. No se borró la cuenta.",
    ),
    "league of legends client is not currently running or not connected via lcu.": (
        "client_offline",
        "Abre el cliente de League of Legends e inténtalo de nuevo.",
    ),
    "unable to extract summoner name from active client.": (
        "client_unreadable",
        "No pudimos leer tu invocador en el cliente. Intenta de nuevo.",
    ),
    "confirmation text must be 'delete' to permanently delete account.": (
        "confirm_delete",
        "Escribe DELETE para confirmar que quieres borrar la cuenta.",
    ),
    "league client not connected or summoner data unavailable.": (
        "client_offline",
        "Abre League of Legends para ver la selección de campeones.",
    ),
    "no se pudo verificar la identidad con esos datos.": (
        "recovery_rejected",
        "No se pudo verificar la identidad con esos datos.",
    ),
    "demasiados intentos. espera unos minutos e inténtalo de nuevo.": (
        "rate_limited",
        "Demasiados intentos. Espera unos minutos e inténtalo de nuevo.",
    ),
    "not found": ("not_found", "No encontramos lo que buscabas."),
    "method not allowed": ("not_allowed", "Esa acción no está disponible."),
}

_STATUS_FALLBACK: dict[int, tuple[str, str]] = {
    400: ("bad_request", "No pudimos completar esa acción. Revisa los datos e inténtalo de nuevo."),
    401: ("session_required", "Tu sesión no es válida. Vuelve a iniciar sesión."),
    403: ("forbidden", "No tienes permiso para hacer eso."),
    404: ("not_found", "No encontramos lo que buscabas."),
    409: ("conflict", "Esa información ya está en uso."),
    422: ("validation", "Revisa los datos e inténtalo de nuevo."),
    429: ("rate_limited", "Hay demasiadas solicitudes. Espera un momento e inténtalo de nuevo."),
}

_TECHNICAL = re.compile(
    r"traceback|exception|sqlalchemy|bearer|puuid|token|api[_ ]?key|\bapi\b|backend|http|error\s*\d{3}",
    re.IGNORECASE,
)


def _detail_text(detail: object) -> str:
    if isinstance(detail, str):
        return detail.strip()
    if isinstance(detail, dict):
        message = detail.get("message") or detail.get("detail")
        if isinstance(message, str):
            return message.strip()
    return ""


_SPANISH_HINT = re.compile(
    r"[áéíóúñÁÉÍÓÚÑ¿¡]|\b(no se|cuenta|contraseña|sesión|intenta|datos|correo|vincul\w*|campeón)\b",
    re.IGNORECASE,
)


def _safe_spanish(text: str) -> bool:
    """Keep a message only when it is already a Spanish sentence."""
    if not text or _TECHNICAL.search(text):
        return False
    return bool(_SPANISH_HINT.search(text))


def public_error(status_code: int, detail: object) -> tuple[str, str]:
    """Return (user_message, code) for an HTTP error."""
    raw = _detail_text(detail)
    known = _KNOWN.get(raw.lower())
    if known:
        return known[1], known[0]
    if raw.lower().startswith("match record #") and "not found" in raw.lower():
        return "No encontramos esa partida.", "match_not_found"
    if _safe_spanish(raw):
        return raw, "app_error"
    if status_code >= 500:
        return UNEXPECTED, "unexpected"
    code, message = _STATUS_FALLBACK.get(
        status_code,
        ("app_error", "No pudimos completar esa acción. Intenta de nuevo."),
    )
    return message, code


def validation_message(exc: RequestValidationError) -> tuple[str, str]:
    blob = " ".join(str(item.get("msg", "")) for item in exc.errors()).lower()
    locations = " ".join(".".join(str(part) for part in item.get("loc", [])) for item in exc.errors()).lower()
    if "passwords do not match" in blob:
        return "Las contraseñas no coinciden.", "validation"
    if "invalid email" in blob or "email" in locations:
        return "Escribe un correo válido.", "validation"
    if "at least 8" in blob or ("password" in locations and "at least" in blob):
        return "La contraseña debe tener al menos 8 caracteres.", "validation"
    if "at least 2" in blob and "username" in locations:
        return "El usuario debe tener al menos 2 caracteres.", "validation"
    return "Revisa los datos e inténtalo de nuevo.", "validation"


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        message, code = public_error(exc.status_code, exc.detail)
        if exc.status_code >= 500:
            logger.error("HTTP %s on %s", exc.status_code, request.url.path)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": message, "code": code},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        message, code = validation_message(exc)
        return JSONResponse(status_code=422, content={"detail": message, "code": code})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled error on %s", request.url.path, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": UNEXPECTED, "code": "unexpected"},
        )
