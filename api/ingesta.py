# api/ingesta.py
# Proyecto Titán — Lógica COMPARTIDA de ingesta segura de PDF del simulador.
#
# La usan las dos rutas multipart de la API para no duplicar la lógica:
#   * ``POST /api/v1/sesiones/ingestar``            (api/routes/sesiones.py)
#   * ``POST /api/v1/evaluar-admision/upload``      (api/routes/evaluacion.py)
#
# Fronteras de arquitectura (idénticas al resto de ``api/``):
#   * ``core.pipeline`` se importa de forma PEREZOSA dentro del manejador: arrastra
#     cv2/pypdfium2/pytesseract/pandas/joblib y no debe penalizar el arranque de la
#     API ni el import de ``api.main``.
#   * No se escribe SQL ni se hardcodea ninguna etiqueta de negocio.

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import tempfile

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

# --- Disponibilidad de multipart (fix K-S1) ----------------------------------
# FastAPI exige ``python-multipart`` para declarar ``UploadFile = File(...)``. Se
# comprueba el nombre CANÓNICO (``python_multipart``) y también el alias histórico
# (``multipart``) para cubrir ambas nomenclaturas según la versión instalada. Las
# rutas de ingesta solo se registran si esta bandera es verdadera.
MULTIPART_DISPONIBLE: bool = (
    importlib.util.find_spec("python_multipart") is not None
    or importlib.util.find_spec("multipart") is not None
)

# --- Tope de tamaño de subida (fix K-S2) -------------------------------------
# Límite duro para no volcar a memoria/disco un archivo arbitrariamente grande.
MAX_BYTES_SUBIDA: int = 25 * 1024 * 1024  # 25 MB
_TAMANO_BLOQUE: int = 1024 * 1024  # se lee de 1 MB en 1 MB


def _leer_acotado(archivo: UploadFile, max_bytes: int = MAX_BYTES_SUBIDA) -> bytes:
    """Lee ``archivo`` por BLOQUES hasta ``max_bytes``; HTTP 413 si se excede.

    Sustituye al ``archivo.file.read()`` sin cota: nunca se materializa en memoria
    un cuerpo mayor que el tope permitido.
    """
    bloques: list[bytes] = []
    leidos = 0
    while True:
        bloque = archivo.file.read(_TAMANO_BLOQUE)
        if not bloque:
            break
        leidos += len(bloque)
        if leidos > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"el archivo supera el tamaño máximo permitido de "
                    f"{max_bytes} bytes"
                ),
            )
        bloques.append(bloque)
    return b"".join(bloques)


def ingerir_pdf_seguro(archivo: UploadFile, db_path: str) -> dict:
    """Vuelca el PDF subido a un temporal y lo procesa de punta a punta.

    Detalles de robustez (compartidos por ambas rutas multipart):
      * Import PEREZOSO de ``core.pipeline`` dentro de la función.
      * El archivo se vuelca a un directorio temporal CONSERVANDO su nombre
        original (``os.path.basename``): la idempotencia del pipeline se basa en ese
        nombre para detectar archivos ya procesados. ``basename`` además neutraliza
        cualquier intento de path-traversal en ``archivo.filename``.
      * Lectura ACOTADA a ``MAX_BYTES_SUBIDA`` (HTTP 413 si se excede).
      * El directorio temporal se elimina SIEMPRE en el ``finally``.

    Devuelve el ``dict`` de ``core.pipeline.process_simulator_pdf`` (claves
    ``session_id``/``cached``/``errors``/``telemetry_loaded``). Un fallo del
    pipeline se traduce a HTTP 500 genérico (sin filtrar internos); los
    ``HTTPException`` (p.ej. 413) se re-lanzan intactos.
    """
    from core import pipeline

    nombre = os.path.basename(archivo.filename or "reporte.pdf")
    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_path = os.path.join(tmp_dir, nombre)
        contenido = _leer_acotado(archivo)
        with open(tmp_path, "wb") as fh:
            fh.write(contenido)

        resultado = pipeline.process_simulator_pdf(
            tmp_path,
            db_path=db_path,
            models_path=pipeline.DEFAULT_MODELS_PATH,
            exports_dir=pipeline.DEFAULT_EXPORTS_DIR,
            engine=pipeline.ENGINE_PARSER,
            profile_source=pipeline.PROFILE_SOURCE_MODEL,
        )
    except HTTPException:
        # 413 (tamaño) u otra HTTPException deliberada: propagar tal cual.
        raise
    except Exception:
        logger.exception("Fallo al procesar el PDF subido (%s)", nombre)
        raise HTTPException(
            status_code=500, detail="no se pudo procesar el archivo PDF"
        ) from None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return resultado
