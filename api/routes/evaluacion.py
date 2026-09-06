# api/routes/evaluacion.py
# Proyecto Titán — Endpoints de evaluación pedagógica (admisión y delta Pre/Post).
#
# ``core`` NUNCA lanza por datos ausentes: devuelve dicts coherentes con flags. La
# capa REST traduce esos flags a códigos HTTP (404 si la sesión no existe, 503 si la
# BD no está disponible). Ningún umbral ni etiqueta de negocio se reimplementa aquí:
# se importa de ``core.evaluador_diagnostico``.
#
# Threading sqlite3: manejadores SINCRONOS que abren la conexión DENTRO del cuerpo.
#
# Payload ligero: los endpoints devuelven un ``dict`` y usan
# ``response_model_exclude_unset`` para que las series RAW (clave ausente cuando no
# se piden) se OMITAN de verdad en el JSON. NO se usa ``exclude_none`` global: los
# ``*_pct`` en ``None`` son información de negocio y deben conservarse.

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from core.evaluador_diagnostico import (
    SENALES_CANONICAS,
    VEREDICTO_SIN_DATOS,
    comparar_sesiones_delta,
    evaluar_diagnostico_inicial,
    obtener_datos_documento_sesion,
)

from api.deps import conexion_bd_o_503, get_db_path
from api.ingesta import MULTIPART_DISPONIBLE, ingerir_pdf_seguro
from api.schemas import (
    EJE_X_PROGRESO,
    CompararDeltaRequest,
    CompararDeltaResponse,
    DiagnosticoResponse,
    EvaluarAdmisionRequest,
)
from api.series import normalizar_series, series_a_listas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["evaluacion"])


def _diagnostico_a_respuesta(
    session_id: int,
    resultado: Dict[str, Any],
    documento: Dict[str, Any],
    incluir_series: bool,
) -> Dict[str, Any]:
    """Construye el ``DiagnosticoResponse`` común a las dos rutas de admisión.

    Traduce el flag de ``core`` a HTTP: si ``metricas_calculadas.sesion_existente``
    es falso -> 404. Ningún umbral ni etiqueta se reimplementa: todo sale de
    ``resultado``/``documento`` (ya saneados por ``core``). Compartido por
    ``POST /evaluar-admision`` (por ``session_id``) y ``POST /evaluar-admision/upload``
    (por PDF subido) para que ambas devuelvan EXACTAMENTE la misma forma.
    """
    metricas = resultado.get("metricas_calculadas", {}) or {}
    if not metricas.get("sesion_existente"):
        raise HTTPException(
            status_code=404, detail=f"la sesión {session_id} no existe"
        )

    respuesta: Dict[str, Any] = {
        "semaforo": resultado["sugerencia_admision"],
        "justificacion": resultado["justificacion"],
        "foco_instructor": resultado["foco_instructor"],
        "nivel_riesgo": resultado["nivel_riesgo"],
        "hallazgos": resultado.get("hallazgos", []) or [],
        "severidad_total": resultado.get("severidad_total", 0),
        "metricas": metricas,
        "estado": "ok",
        "puntaje_depurado": float(documento.get("puntaje_depurado", 0.0)),
    }
    if incluir_series:
        respuesta["series"] = series_a_listas(resultado.get("series", {}) or {})
    return respuesta


@router.post(
    "/evaluar-admision",
    response_model=DiagnosticoResponse,
    response_model_exclude_unset=True,
)
def evaluar_admision(
    peticion: EvaluarAdmisionRequest,
    db_path: str = Depends(get_db_path),
) -> Dict[str, Any]:
    """Dictamen de admisión (Día 1) para una sesión.

    El ``puntaje_depurado`` se toma de ``obtener_datos_documento_sesion`` (que ya lo
    sanea y aplica la fórmula del motor); la API NO recalcula umbrales propios. Las
    series crudas solo se incluyen si ``incluir_series=True`` para aligerar el payload.

    Solo un fallo de ADQUISICIÓN de la conexión es 503; un error real de ``core``
    propaga al manejador centralizado de 500 (fix K-W1).
    """
    session_id = peticion.session_id
    with conexion_bd_o_503(db_path) as conn:
        resultado = evaluar_diagnostico_inicial(session_id, conn)
        documento = obtener_datos_documento_sesion(session_id, conn)

    return _diagnostico_a_respuesta(
        session_id, resultado, documento, peticion.incluir_series
    )


@router.post(
    "/comparar-delta",
    response_model=CompararDeltaResponse,
    response_model_exclude_unset=True,
)
def comparar_delta(
    peticion: CompararDeltaRequest,
    db_path: str = Depends(get_db_path),
) -> Dict[str, Any]:
    """Comparativa Pre/Post (Día Final) con deltas y veredicto de evolución.

    Si alguna sesión no existe, ``core`` devuelve la clave ``error`` (equivalente a
    ``veredicto_evolucion == VEREDICTO_SIN_DATOS``) y la API responde 404 con las
    ``sesiones_faltantes``. SIEMPRE se añaden las series normalizadas a progreso
    (0->100 %) y el eje X; las series RAW solo si ``incluir_series=True``.

    Solo un fallo de ADQUISICIÓN de la conexión es 503; un error real de ``core``
    propaga al manejador centralizado de 500 (fix K-W1).
    """
    with conexion_bd_o_503(db_path) as conn:
        resultado = comparar_sesiones_delta(
            peticion.session_id_pre, peticion.session_id_post, conn
        )

    sin_datos = "error" in resultado or (
        resultado.get("veredicto_evolucion") == VEREDICTO_SIN_DATOS
    )
    if sin_datos:
        faltantes = resultado.get("sesiones_faltantes", []) or []
        raise HTTPException(
            status_code=404,
            detail={
                "mensaje": resultado.get(
                    "error", "alguna de las sesiones no existe en la base de datos"
                ),
                "sesiones_faltantes": faltantes,
            },
        )

    respuesta: Dict[str, Any] = {
        "pre": resultado["pre"],
        "post": resultado["post"],
        "deltas": resultado["deltas"],
        "veredicto_evolucion": resultado["veredicto_evolucion"],
        "mejoras": resultado.get("mejoras", []) or [],
        "retrocesos": resultado.get("retrocesos", []) or [],
        "series_normalizadas_pre": normalizar_series(
            resultado.get("series_pre", {}) or {}, SENALES_CANONICAS
        ),
        "series_normalizadas_post": normalizar_series(
            resultado.get("series_post", {}) or {}, SENALES_CANONICAS
        ),
        "eje_x": EJE_X_PROGRESO,
    }
    if peticion.incluir_series:
        respuesta["series_pre"] = series_a_listas(resultado.get("series_pre", {}) or {})
        respuesta["series_post"] = series_a_listas(resultado.get("series_post", {}) or {})
    return respuesta


# --- Variante MULTIPART del dictamen de admisión (fix R1) ---------------------
# Se registra SOLO si ``python-multipart`` está disponible (misma bandera que
# /api/v1/sesiones/ingestar). Permite evaluar un PDF del simulador sin que el
# cliente conozca de antemano el ``session_id``: ingiere el archivo y devuelve el
# diagnóstico de la sesión recién creada, con la MISMA forma que /evaluar-admision.
if MULTIPART_DISPONIBLE:

    @router.post(
        "/evaluar-admision/upload",
        response_model=DiagnosticoResponse,
        response_model_exclude_unset=True,
        summary="Dictamen de admisión subiendo el PDF (variante multipart)",
        description=(
            "Variante multipart de `POST /api/v1/evaluar-admision`. En lugar de un "
            "`session_id` ya existente, se sube el PDF del simulador en el campo "
            "`archivo`; la API lo ingiere de punta a punta vía `core.pipeline` "
            "(misma lógica segura que `/api/v1/sesiones/ingestar`: import perezoso, "
            "nombre original preservado para la idempotencia, temporal limpiado, "
            "lectura acotada a 25 MB con HTTP 413 si se excede) y devuelve el "
            "diagnóstico de admisión de la sesión resultante con el mismo esquema "
            "`DiagnosticoResponse`. `incluir_series=true` añade las series crudas. "
            "Si la ingesta no produce `session_id`, responde 422 con los `errors` "
            "del pipeline."
        ),
    )
    def evaluar_admision_upload(
        archivo: UploadFile = File(...),
        incluir_series: bool = Form(False),
        db_path: str = Depends(get_db_path),
    ) -> Dict[str, Any]:
        """Ingiere el PDF subido y devuelve el dictamen de admisión de la sesión."""
        resultado_ingesta = ingerir_pdf_seguro(archivo, db_path)
        session_id = resultado_ingesta.get("session_id")
        if session_id is None:
            errores = list(resultado_ingesta.get("errors", []) or [])
            logger.warning(
                "La ingesta del PDF no produjo sesión evaluable (errores=%d)",
                len(errores),
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "mensaje": "la ingesta del PDF no produjo una sesión evaluable",
                    "errors": errores,
                },
            )

        with conexion_bd_o_503(db_path) as conn:
            resultado = evaluar_diagnostico_inicial(session_id, conn)
            documento = obtener_datos_documento_sesion(session_id, conn)

        return _diagnostico_a_respuesta(
            session_id, resultado, documento, incluir_series
        )
