# api/routes/sesiones.py
# Proyecto Titán — Endpoints de listado de sesiones, telemetría e ingesta de PDF.
#
# Patrón de acceso a BD (crítico por el threading de sqlite3): ``core.db_manager``
# crea las conexiones SIN ``check_same_thread=False``, de modo que cada conexión
# queda ligada al hilo que la abrió. Por eso TODOS los manejadores que tocan la BD
# son funciones SINCRONAS (``def``, ejecutadas por FastAPI en un threadpool) que
# abren la conexión DENTRO del cuerpo con ``with get_db_connection(db_path)``:
# creación y uso ocurren en el mismo hilo. Nunca se abre la conexión en una
# dependencia ``async``.

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core.db_manager import list_sessions
from core.evaluador_diagnostico import SENALES_CANONICAS, evaluar_diagnostico_inicial

from api.deps import conexion_bd_o_503, get_db_path
from api.ingesta import MULTIPART_DISPONIBLE, ingerir_pdf_seguro
from api.schemas import (
    EJE_X_PROGRESO,
    IngestarResponse,
    Session,
    TelemetriaResponse,
)
from api.series import normalizar_series, series_a_listas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sesiones", tags=["sesiones"])

# Etiqueta del eje X cuando se devuelven las series crudas (tiempo real en segundos).
EJE_X_TIEMPO: str = "Tiempo (s)"


@router.get("", response_model=List[Session])
def listar_sesiones(db_path: str = Depends(get_db_path)) -> List[Session]:
    """Lista todas las sesiones descriptivas de la base de datos.

    ``list_sessions`` nunca lanza (devuelve ``[]`` ante un error de lectura), por lo
    que una BD degradada responde una lista vacía en lugar de un 500. Solo un fallo
    real de ADQUISICIÓN de la conexión se traduce a 503 (vía ``conexion_bd_o_503``).
    """
    with conexion_bd_o_503(db_path) as conn:
        filas = list_sessions(conn)
    return [Session(**fila) for fila in filas]


@router.get("/{session_id}/telemetria", response_model=TelemetriaResponse)
def obtener_telemetria(
    session_id: int,
    normalizar: bool = False,
    db_path: str = Depends(get_db_path),
) -> TelemetriaResponse:
    """Series de telemetría de una sesión, crudas o normalizadas a progreso (%).

    Devuelve 404 si la sesión no existe (``core`` no lanza: el estado se deriva del
    flag ``metricas_calculadas.sesion_existente``). Solo un fallo de ADQUISICIÓN de
    la conexión es 503; un error real de ``core`` propaga al 500 centralizado.
    """
    with conexion_bd_o_503(db_path) as conn:
        resultado = evaluar_diagnostico_inicial(session_id, conn)

    metricas = resultado.get("metricas_calculadas", {}) or {}
    if not metricas.get("sesion_existente"):
        raise HTTPException(
            status_code=404, detail=f"la sesión {session_id} no existe"
        )

    series = resultado.get("series", {}) or {}
    if normalizar:
        datos = normalizar_series(series, SENALES_CANONICAS)
        eje_x = EJE_X_PROGRESO
    else:
        datos = series_a_listas(series)
        eje_x = EJE_X_TIEMPO

    return TelemetriaResponse(
        session_id=session_id,
        normalizar=normalizar,
        eje_x=eje_x,
        series=datos,
    )


# --- Ingesta de PDF (OPCIONAL) ------------------------------------------------
# La ruta se registra SOLO si ``python-multipart`` está disponible: FastAPI exige
# esa biblioteca para declarar ``UploadFile = File(...)`` y lanzaría al definir la
# ruta. La disponibilidad se comprueba en ``api.ingesta`` (nombre canónico
# ``python_multipart`` o alias ``multipart``) y la MISMA bandera guarda la ruta
# hermana ``POST /api/v1/evaluar-admision/upload`` en ``api/routes/evaluacion.py``.
if MULTIPART_DISPONIBLE:

    @router.post("/ingestar", response_model=IngestarResponse)
    def ingestar_pdf(
        archivo: UploadFile = File(...),
        db_path: str = Depends(get_db_path),
    ) -> IngestarResponse:
        """Procesa un PDF del simulador de punta a punta vía ``core.pipeline``.

        La lógica de ingesta segura (import perezoso de ``core.pipeline``, volcado a
        un temporal conservando ``os.path.basename`` del nombre original, lectura
        ACOTADA a 25 MB con HTTP 413 si se excede, y limpieza del temporal en el
        ``finally``) vive en ``api.ingesta.ingerir_pdf_seguro`` y se COMPARTE con la
        ruta multipart ``/api/v1/evaluar-admision/upload``.
        """
        resultado = ingerir_pdf_seguro(archivo, db_path)
        return IngestarResponse(
            session_id=resultado.get("session_id"),
            cached=bool(resultado.get("cached")),
            errors=list(resultado.get("errors", []) or []),
            telemetry_loaded=dict(resultado.get("telemetry_loaded", {}) or {}),
        )

else:
    # A3: dejar constancia explícita de que las rutas de ingesta quedan deshabilitadas.
    logger.warning(
        "python-multipart NO está instalado: las rutas de ingesta de PDF "
        "(/api/v1/sesiones/ingestar y /api/v1/evaluar-admision/upload) quedan "
        "DESHABILITADAS. Instale 'python-multipart' para habilitarlas."
    )
