# api/routes/advisor.py
# Proyecto Titán — Endpoint de devolución pedagógica asistida por LLM.
#
# SEGURIDAD: la ``api_key`` se transmite a ``core.ai_advisor`` pero NUNCA se
# registra en logs ni se incluye en la respuesta. El manejador no la imprime ni la
# serializa; solo la reenvía por argumento.
#
# ``generar_devolucion_pedagogica`` NUNCA lanza: ante cualquier fallo de red, clave
# o proveedor degrada al generador local determinista y lo reporta en
# ``advertencias`` / ``generado_por_llm=False``. Por eso este endpoint no traduce
# errores de LLM a HTTP 5xx; siempre devuelve el contrato completo.

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends

from core.evaluador_diagnostico import obtener_payload_para_llm
from core.ai_advisor import generar_devolucion_pedagogica

from api.deps import conexion_bd_o_503, get_db_path
from api.schemas import DevolucionRequest, DevolucionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["advisor"])


@router.post(
    "/devolucion-pedagogica",
    response_model=DevolucionResponse,
    response_model_exclude_unset=True,
)
def devolucion_pedagogica(
    peticion: DevolucionRequest,
    db_path: str = Depends(get_db_path),
) -> Dict[str, Any]:
    """Genera la devolución pedagógica estructurada para el instructor.

    Fuente de datos (validada por el modelo de petición):
      * Si se aporta ``payload_metricas`` se usa directamente (sin tocar la BD).
      * Si no, se construye con ``obtener_payload_para_llm`` a partir de
        ``session_id_pre`` (y opcionalmente ``session_id_post``).

    ``metricas_interpretadas`` solo se incluye si ``incluir_metricas_interpretadas``
    es verdadero (se omite de verdad vía ``response_model_exclude_unset``).

    Solo un fallo de ADQUISICIÓN de la conexión es 503; un error real de ``core``
    propaga al manejador centralizado de 500 (fix K-W1). La ``api_key`` jamás se
    loguea ni se devuelve.
    """
    payload: Any = peticion.payload_metricas
    if payload is None:
        with conexion_bd_o_503(db_path) as conn:
            payload = obtener_payload_para_llm(
                peticion.session_id_pre, conn, peticion.session_id_post
            )

    # La api_key se reenvía tal cual; jamás se loguea ni se devuelve.
    devolucion = generar_devolucion_pedagogica(
        payload, api_key=peticion.api_key, provider=peticion.provider
    )

    respuesta: Dict[str, Any] = {
        "resumen_ejecutivo": devolucion["resumen_ejecutivo"],
        "puntos_fuertes": devolucion["puntos_fuertes"],
        "vicios_criticos": devolucion["vicios_criticos"],
        "plan_accion_recomendado": devolucion["plan_accion_recomendado"],
        "nivel_riesgo": devolucion["nivel_riesgo"],
        "foco_prioritario": devolucion["foco_prioritario"],
        "provider": devolucion["provider"],
        "modelo": devolucion["modelo"],
        "generado_por_llm": devolucion["generado_por_llm"],
        "advertencias": devolucion["advertencias"],
    }
    if peticion.incluir_metricas_interpretadas:
        respuesta["metricas_interpretadas"] = devolucion.get("metricas_interpretadas")
    return respuesta
