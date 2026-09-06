# api/main.py
# Proyecto Titán — Aplicación FastAPI que envuelve el motor ``core`` (agnóstico de UI).
#
# Fronteras de arquitectura (las mismas que ya se exigen a ``streamlit_app.py``):
#   * ``api/`` NUNCA importa bibliotecas de UI ni escribe SQL propio: todo acceso a
#     datos pasa por ``core.db_manager``.
#   * ``api/`` NUNCA hardcodea dictámenes/veredictos/umbrales: se importan de
#     ``core.evaluador_diagnostico`` y ``core.ai_advisor``.
#   * ``core/`` jamás importa ``api/`` (dependencia en un solo sentido).
#
# Threading sqlite3: ``core.db_manager.create_connection`` usa ``sqlite3.connect``
# SIN ``check_same_thread=False``; cada conexión queda ligada a su hilo. Por eso los
# endpoints que tocan la BD son ``def`` sincronos y abren la conexión DENTRO del
# cuerpo (nunca en una dependencia ``async``).

from __future__ import annotations

import logging
import os

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.db_manager import get_db_connection, list_sessions

from api.deps import get_db_path
from api.routes import advisor, evaluacion, sesiones
from api.schemas import HealthResponse

logger = logging.getLogger(__name__)

# Versión del contrato de la API (se expone en ``/health`` y en OpenAPI).
API_VERSION: str = "1.0.0"

app = FastAPI(
    title="Proyecto Titán API",
    description=(
        "Capa REST que expone el motor de evaluación pedagógica de Proyecto Titán "
        "(diagnóstico de admisión, comparativa Pre/Post y devolución asistida por "
        "LLM) sin depender de ninguna biblioteca de interfaz. Todos los umbrales y "
        "etiquetas de negocio provienen de core/."
    ),
    version=API_VERSION,
)

# --- CORS ---------------------------------------------------------------------
# Orígenes permitidos configurable por entorno con ``TITAN_API_CORS_ORIGINS``
# (lista separada por comas, p.ej. "https://app.titan.local,https://admin.titan").
# El valor por defecto ``["*"]`` (comodín) es SOLO para desarrollo local: en
# producción DEBEN fijarse orígenes explícitos para no exponer la API a cualquier
# dominio. Se registra la configuración efectiva al arrancar (sin secretos).
_origins_env = os.getenv("TITAN_API_CORS_ORIGINS", "")
_origins = [o.strip() for o in _origins_env.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(
    "CORS configurado con allow_origins=%s (el comodín '*' es SOLO para desarrollo "
    "local; en producción fije TITAN_API_CORS_ORIGINS con orígenes explícitos)",
    _origins,
)


# --- Manejo centralizado de errores de validación (fix K-C1) ------------------
# Claves de ``ctx``/error que podrían contener valores CRUDOS del cliente. Nunca
# se copian a la respuesta: un ``model_validator`` que falla (p.ej. el de
# ``DevolucionRequest``) reporta como ``input`` el cuerpo COMPLETO, incluida la
# ``api_key`` en claro.
_CLAVES_SENSIBLES = frozenset({"input", "value", "values"})


def _sanear_error_validacion(error: dict) -> dict:
    """Reconstruye un error de validación SIN el ``input`` crudo del cliente.

    Conserva SOLO ``type``/``loc``/``msg`` (útiles para que el cliente corrija la
    petición) y un ``ctx`` saneado a texto, omitiendo cualquier clave sensible.
    """
    saneado: dict = {
        "type": error.get("type"),
        "loc": list(error.get("loc", ()) or ()),
        "msg": error.get("msg"),
    }
    ctx = error.get("ctx")
    if isinstance(ctx, dict):
        ctx_seguro = {
            clave: str(valor)
            for clave, valor in ctx.items()
            if clave not in _CLAVES_SENSIBLES
        }
        if ctx_seguro:
            saneado["ctx"] = ctx_seguro
    return saneado


@app.exception_handler(RequestValidationError)
async def manejador_error_validacion(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 que NUNCA devuelve el cuerpo crudo (protege ``api_key`` y otros secretos).

    Sustituye al manejador por defecto de FastAPI, que hace ``jsonable_encoder`` de
    ``exc.errors()`` e incluye el campo ``input`` con los datos tal cual los envió el
    cliente. Aquí se sanea cada error antes de serializarlo.
    """
    errores = [_sanear_error_validacion(error) for error in exc.errors()]
    logger.warning(
        "Petición inválida en %s %s (%d error(es) de validación)",
        request.method,
        request.url.path,
        len(errores),
    )
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": errores}),
    )


# --- Manejo centralizado de errores no controlados ----------------------------
@app.exception_handler(Exception)
async def manejador_error_no_controlado(request: Request, exc: Exception) -> JSONResponse:
    """Devuelve un 500 estructurado SIN filtrar internos ni secretos.

    Se registra únicamente el tipo de la excepción y la ruta (nunca el mensaje
    crudo, que podría contener una URL con secreto o datos sensibles). El detalle
    técnico completo queda en el logger del servidor, no en la respuesta HTTP.
    """
    logger.error(
        "Error no controlado (%s) en %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )
    logger.debug("Detalle del error no controlado", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "error interno del servidor"},
    )


# --- Routers ------------------------------------------------------------------
app.include_router(sesiones.router)
app.include_router(evaluacion.router)
app.include_router(advisor.router)


# --- Health check -------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
def health(db_path: str = Depends(get_db_path)) -> HealthResponse:
    """Estado del servicio y de la base de datos.

    Devuelve SIEMPRE HTTP 200, incluso cuando ``db_disponible=False``: es una
    decisión deliberada para que los health-checks del orquestador no "flapeen"
    (un reinicio por una BD temporalmente inaccesible sería peor que servir
    degradado). El cuerpo informa del estado real para que el operador decida.

    ``list_sessions`` nunca lanza (devuelve ``[]`` en error), de modo que ``sesiones``
    es 0 tanto si la BD está vacía como si no se pudo leer.
    """
    db_disponible = False
    total_sesiones = 0
    try:
        with get_db_connection(db_path) as conn:
            db_disponible = True
            total_sesiones = len(list_sessions(conn))
    except Exception:
        db_disponible = False
        total_sesiones = 0
    return HealthResponse(
        status="ok",
        db_disponible=db_disponible,
        sesiones=total_sesiones,
        version=API_VERSION,
    )
