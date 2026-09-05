# core/ai_advisor.py
# Proyecto Titán — Asesor pedagógico asistido por LLM (módulo desacoplado).
#
# Contrato de arquitectura:
#   * ENTRADA: ``payload_metricas`` (dict nativo, JSON-serializable) producido por
#     :func:`core.evaluador_diagnostico.obtener_payload_para_llm`. También acepta,
#     por tolerancia, un ``metricas_calculadas`` plano o la salida de
#     :func:`core.evaluador_diagnostico.comparar_sesiones_delta`.
#   * SALIDA: dict nativo con la devolución pedagógica estructurada (diagnóstico,
#     vicios operativos y plan de acción). Nunca devuelve objetos de framework.
#   * Cero dependencias de UI (ni streamlit ni tkinter) y cero dependencias de red
#     en el camino por defecto: sin API key o con ``provider="mock"`` genera la
#     devolución localmente, de forma determinista y sin costo de API.
#
# Frontera de responsabilidades:
#   * Los UMBRALES y la DETECCIÓN de vicios viven en ``core.evaluador_diagnostico``
#     (este módulo los importa, nunca los reimplementa).
#   * Aquí reside únicamente la NARRATIVA pedagógica: el catálogo de acciones
#     correctivas y el system prompt del instructor de simulación.

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.evaluador_diagnostico import (
    COBERTURA_MINIMA_SENALES,
    DICTAMEN_APTO,
    DICTAMEN_NO_APTO_CRITICO,
    DICTAMEN_OBSERVADO,
    FORK_SAFE_THRESHOLD_MTRS,
    HALLAZGO_COLISIONES,
    HALLAZGO_COLISIONES_CRITICAS,
    HALLAZGO_COBERTURA_INSUFICIENTE,
    HALLAZGO_FRENADAS_ALTO,
    HALLAZGO_FRENADAS_MODERADO,
    HALLAZGO_FRENO_SOSTENIDO,
    HALLAZGO_INSEGURIDAD_TORRE,
    HALLAZGO_SESION_INEXISTENTE,
    HALLAZGO_TRASLADO_ALTO,
    HALLAZGO_TRASLADO_ALTO_SEVERO,
    HALLAZGO_VOLANTAZOS_ALTO,
    HALLAZGO_VOLANTAZOS_MODERADO,
    MAYOR_ES_MEJOR,
    MENOR_ES_MEJOR,
    RIESGO_ALTO,
    RIESGO_BAJO,
    RIESGO_MEDIO,
    SENALES_CANONICAS,
    VEREDICTO_MEJORA_MODERADA,
    VEREDICTO_MEJORA_SIGNIFICATIVA,
    VEREDICTO_REGRESION,
    VEREDICTO_SIN_DATOS,
    nivel_riesgo_por_dictamen,
)

logger = logging.getLogger(__name__)

# =============================================================================
# PROVEEDORES Y CONFIGURACIÓN
# =============================================================================
PROVIDER_MOCK: str = "mock"
PROVIDER_OPENAI: str = "openai"
PROVIDER_ANTHROPIC: str = "anthropic"
PROVEEDORES_SOPORTADOS: Tuple[str, ...] = (
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
    PROVIDER_ANTHROPIC,
)

# La clave NUNCA se escribe en código: se recibe por argumento o por entorno.
ENV_API_KEY: str = "TITAN_LLM_API_KEY"
ENV_MODEL: str = "TITAN_LLM_MODEL"

MODELO_POR_DEFECTO: Dict[str, str] = {
    PROVIDER_OPENAI: "gpt-4o-mini",
    PROVIDER_ANTHROPIC: "claude-3-5-sonnet-latest",
}
ENDPOINT_OPENAI: str = "https://api.openai.com/v1/chat/completions"
ENDPOINT_ANTHROPIC: str = "https://api.anthropic.com/v1/messages"
VERSION_ANTHROPIC: str = "2023-06-01"
TIMEOUT_SEGUNDOS: int = 60
MAX_TOKENS_RESPUESTA: int = 1200

# Claves del contrato de salida (idénticas para mock y para LLM real).
CLAVE_DIAGNOSTICO = "diagnostico"
CLAVE_VICIOS = "vicios_operativos"
CLAVE_PLAN = "plan_de_accion"
CLAVE_FOCO = "foco_prioritario"
CLAVE_RIESGO = "nivel_riesgo"


# =============================================================================
# SYSTEM PROMPT PARA INSTRUCTORES DE SIMULACIÓN
# =============================================================================
SYSTEM_PROMPT_INSTRUCTOR = f"""Eres el asesor pedagógico senior de "Proyecto Titán", un programa de
formación de operadores de carretilla elevadora (montacargas) basado en simulador.
Tu audiencia es un INSTRUCTOR DE SIMULACIÓN humano que debe decidir la admisión del
alumno y planificar su corrección; no es el alumno.

## Qué recibes
Un JSON con métricas físicas extraídas de la telemetría del simulador y ya evaluadas
por el motor de reglas del sistema:
  * Contadores de eventos: frenadas bruscas, volantazos (inversiones de dirección),
    colisiones netas, microajustes de torre.
  * Magnitudes continuas: segundos trasladando con la horquilla por encima de
    {FORK_SAFE_THRESHOLD_MTRS:.2f} m (posición insegura), varianza de la dirección,
    duración del ejercicio, puntaje final y puntaje depurado
    (puntaje final + penalizaciones acumuladas).
  * Cobertura: señales de telemetría disponibles de {len(SENALES_CANONICAS)} posibles.
  * Dictamen del motor ("{DICTAMEN_APTO}", "{DICTAMEN_OBSERVADO}",
    "{DICTAMEN_NO_APTO_CRITICO}"), nivel de riesgo, hallazgos estructurados con su
    severidad y, cuando existe, la comparativa Pre/Post (Día 1 vs Día Final) con
    veredicto de evolución, deltas, mejoras y retrocesos.

## Cómo debes interpretar las métricas físicas
  * Volantazos altos = dirección reactiva: el operador corrige en vez de anticipar;
    suele indicar mala lectura del radio de giro trasero y del eje de la carga.
  * Frenadas bruscas repetidas = gestión deficiente de la inercia y falta de
    anticipación; desgastan la máquina y desestabilizan la carga.
  * Frenado sostenido en rango alto = uso del pedal como apoyo permanente, señal de
    exceso de velocidad de aproximación.
  * Colisiones = fallo de control del espacio y del mástil; son el indicador de
    seguridad más grave y nunca deben relativizarse.
  * Microajustes de torre y segundos en régimen de duda = inseguridad en la
    manipulación de la carga y falta de referencia visual de altura/inclinación.
  * Segundos trasladando con horquilla alta = vicio normativo crítico: el traslado
    debe hacerse con la horquilla recogida y el mástil inclinado hacia atrás.
  * Deltas Pre/Post: una reducción de colisiones, frenadas y volantazos es mejora;
    un aumento es regresión. Una duración menor con los mismos vicios NO es mejora
    por sí sola: puede ser prisa. Interpreta siempre el delta junto al veredicto del
    motor.

## Reglas de redacción obligatorias
1. NO inventes datos: usa únicamente las cifras presentes en el payload. Si una
   métrica falta o vale 0 por ausencia de telemetría, dilo explícitamente.
2. Si la cobertura de señales es inferior a {COBERTURA_MINIMA_SENALES} de
   {len(SENALES_CANONICAS)}, advierte que el diagnóstico es provisional y que los
   contadores en 0 pueden deberse a datos ausentes, no a conducción limpia.
3. Sé concreto y accionable: cada ítem del plan debe poder ejecutarse en una sesión
   de simulador (ejercicio, foco técnico y criterio de éxito observable).
4. Prioriza seguridad antes que productividad: primero colisiones y horquilla alta,
   después suavidad de mandos, por último eficiencia/tiempo.
5. Tono profesional, directo y en español neutro. Sin emojis, sin markdown, sin
   tablas y sin caracteres fuera del alfabeto latino básico.
6. Extensión: diagnóstico de 3 a 5 frases; entre 2 y 6 vicios; entre 3 y 6 acciones.

## Formato de salida (estricto)
Devuelve ÚNICAMENTE un objeto JSON válido, sin texto antes ni después, sin bloques
de código, con exactamente estas claves:
{{
  "{CLAVE_DIAGNOSTICO}": "texto corrido con la valoración del desempeño",
  "{CLAVE_VICIOS}": ["vicio 1 con su evidencia numérica", "vicio 2 ..."],
  "{CLAVE_PLAN}": ["acción correctiva 1 con criterio de éxito", "acción 2 ..."],
  "{CLAVE_FOCO}": "la única prioridad de la próxima sesión",
  "{CLAVE_RIESGO}": "{RIESGO_ALTO} | {RIESGO_MEDIO} | {RIESGO_BAJO}"
}}
"""

# Instrucción de usuario que acompaña al payload en cada llamada.
USER_PROMPT_PLANTILLA = """Interpreta las siguientes métricas de telemetría y redacta la devolución
pedagógica para el instructor, respetando el formato JSON estricto acordado:

{payload_json}
"""


# =============================================================================
# CATÁLOGO NARRATIVO DE ACCIONES CORRECTIVAS (dominio del asesor, no umbrales)
# =============================================================================
_PLAN_POR_HALLAZGO: Dict[str, str] = {
    HALLAZGO_COLISIONES_CRITICAS: (
        "Ejercicio de recorrido a velocidad mínima con paradas obligatorias en cada "
        "cruce: el alumno debe verbalizar el espacio libre antes de avanzar. Criterio "
        "de éxito: 0 colisiones en dos series consecutivas."
    ),
    HALLAZGO_COLISIONES: (
        "Trabajo de gestión del espacio y del mástil en pasillo estrecho, con "
        "referencias visuales laterales. Criterio de éxito: 0 colisiones manteniendo "
        "el mismo tiempo de ciclo."
    ),
    HALLAZGO_FRENADAS_ALTO: (
        "Práctica de anticipación de frenada: soltar acelerador 3 segundos antes del "
        "punto de parada y frenar de forma progresiva. Criterio de éxito: reducir a la "
        "mitad las frenadas bruscas sin alargar el ciclo."
    ),
    HALLAZGO_FRENADAS_MODERADO: (
        "Ejercicio de conducción fluida con velocidad objetivo constante y frenada "
        "escalonada. Criterio de éxito: pedales sin picos en la telemetría."
    ),
    HALLAZGO_FRENO_SOSTENIDO: (
        "Corregir el uso del freno como apoyo permanente: regular la velocidad de "
        "aproximación con el acelerador, no con el pedal. Criterio de éxito: muestras "
        "en rango alto de freno por debajo del umbral del motor."
    ),
    HALLAZGO_VOLANTAZOS_ALTO: (
        "Módulo de radio de giro trasero: circuitos en S a baja velocidad mirando la "
        "trayectoria del contrapeso. Criterio de éxito: varianza de dirección reducida "
        "y correcciones de volante de menor amplitud."
    ),
    HALLAZGO_VOLANTAZOS_MODERADO: (
        "Práctica de trazada planificada: decidir la línea antes de mover la máquina "
        "y corregir con giros suaves. Criterio de éxito: menos de la mitad de los "
        "volantazos actuales."
    ),
    HALLAZGO_TRASLADO_ALTO_SEVERO: (
        "Refuerzo normativo inmediato: trasladar siempre con horquilla recogida por "
        "debajo de {:.2f} m y mástil inclinado atrás. Repetir el ciclo de apilado y "
        "desapilado hasta automatizar el descenso antes de desplazarse.".format(
            FORK_SAFE_THRESHOLD_MTRS
        )
    ),
    HALLAZGO_TRASLADO_ALTO: (
        "Consolidar el hábito de recoger la horquilla antes de trasladar: ejercicio de "
        "ida y vuelta con checkpoint de altura. Criterio de éxito: 0 segundos de "
        "traslado con horquilla alta."
    ),
    HALLAZGO_INSEGURIDAD_TORRE: (
        "Trabajo de precisión en torre: aproximación a estantería con referencia de "
        "altura e inclinación, evitando microcorrecciones. Criterio de éxito: reducir "
        "los microajustes y los segundos en régimen de duda."
    ),
    HALLAZGO_COBERTURA_INSUFICIENTE: (
        "Antes de evaluar: reprocesar el reporte PDF y verificar la extracción de las "
        "señales de telemetría faltantes (probar el motor de páginas fijas si el visual "
        "falla)."
    ),
    HALLAZGO_SESION_INEXISTENTE: (
        "Verificar la carga del reporte en la base de datos: la sesión no existe y no "
        "hay evidencia de telemetría que evaluar."
    ),
}

_ACCION_POR_DEFECTO = (
    "Repetir el ejercicio de referencia con registro de telemetría y comparar la curva "
    "resultante con la sesión anterior."
)
_ACCION_APTO = (
    "Afianzar hábitos normativos y aumentar la exigencia de productividad (ciclo más "
    "corto manteniendo 0 colisiones y mandos suaves)."
)

_VICIO_POR_HALLAZGO: Dict[str, str] = {
    HALLAZGO_COLISIONES_CRITICAS: "Pérdida de control del espacio con impacto repetido contra estructuras",
    HALLAZGO_COLISIONES: "Contactos con el entorno por aproximación sin lectura del espacio",
    HALLAZGO_FRENADAS_ALTO: "Frenado de emergencia recurrente por falta de anticipación",
    HALLAZGO_FRENADAS_MODERADO: "Frenadas bruscas puntuales que desestabilizan la carga",
    HALLAZGO_FRENO_SOSTENIDO: "Uso del freno como apoyo continuo (exceso de velocidad de aproximación)",
    HALLAZGO_VOLANTAZOS_ALTO: "Dirección reactiva con correcciones amplias y continuas",
    HALLAZGO_VOLANTAZOS_MODERADO: "Correcciones de volante frecuentes por trazada no planificada",
    HALLAZGO_TRASLADO_ALTO_SEVERO: "Traslado prolongado con la horquilla en posición insegura",
    HALLAZGO_TRASLADO_ALTO: "Traslado ocasional con la horquilla elevada",
    HALLAZGO_INSEGURIDAD_TORRE: "Inseguridad en la manipulación de la torre (microajustes y dudas)",
    HALLAZGO_COBERTURA_INSUFICIENTE: "Telemetría incompleta: los contadores pueden estar en 0 por datos ausentes",
    HALLAZGO_SESION_INEXISTENTE: "Sesión sin datos: no hay evidencia telemétrica que evaluar",
}


# =============================================================================
# API PÚBLICA
# =============================================================================
def generar_devolucion_pedagogica(
    payload_metricas: dict,
    api_key: str = None,
    provider: str = "mock",
) -> dict:
    """Genera la devolución pedagógica estructurada a partir de las métricas.

    Args:
        payload_metricas: dict con las métricas (idealmente la salida de
            :func:`core.evaluador_diagnostico.obtener_payload_para_llm`).
        api_key: clave del proveedor LLM. Si es ``None`` se intenta leer de la
            variable de entorno ``TITAN_LLM_API_KEY``.
        provider: ``"mock"`` (por defecto, local y determinista), ``"openai"`` o
            ``"anthropic"``.

    Returns:
        Dict con el contrato estable::

            {
              "provider": str,
              "modelo": str,
              "generado_por_llm": bool,
              "diagnostico": str,
              "vicios_operativos": [str],
              "plan_de_accion": [str],
              "foco_prioritario": str,
              "nivel_riesgo": "alto" | "medio" | "bajo",
              "metricas_interpretadas": {...},
              "advertencias": [str],
            }

    Nunca lanza: cualquier fallo de red, de clave o de parseo degrada al generador
    local (``mock``) registrando el motivo en ``advertencias``.
    """
    vista, advertencias = _normalizar_payload(payload_metricas)
    proveedor = (provider or PROVIDER_MOCK).strip().lower()
    clave = api_key or os.getenv(ENV_API_KEY)

    if proveedor not in PROVEEDORES_SOPORTADOS:
        advertencias.append(
            f"Proveedor '{provider}' no soportado; se usó '{PROVIDER_MOCK}'."
        )
        proveedor = PROVIDER_MOCK

    if proveedor == PROVIDER_MOCK:
        resultado = _devolucion_mock(vista)
        resultado["provider"] = PROVIDER_MOCK
        resultado["modelo"] = "motor-local-determinista"
        resultado["generado_por_llm"] = False
    elif not clave:
        advertencias.append(
            f"No hay API key para '{proveedor}' (argumento o variable {ENV_API_KEY}); "
            "se generó la devolución local por defecto."
        )
        resultado = _devolucion_mock(vista)
        resultado["provider"] = PROVIDER_MOCK
        resultado["modelo"] = "motor-local-determinista"
        resultado["generado_por_llm"] = False
    else:
        modelo = os.getenv(ENV_MODEL) or MODELO_POR_DEFECTO[proveedor]
        try:
            resultado = _devolucion_llm(vista, proveedor, clave, modelo)
            resultado["provider"] = proveedor
            resultado["modelo"] = modelo
            resultado["generado_por_llm"] = True
        except Exception as exc:  # red, auth, cuota o respuesta no JSON
            logger.warning("Fallo del proveedor %s: %s", proveedor, exc)
            advertencias.append(
                f"El proveedor '{proveedor}' falló ({type(exc).__name__}: {exc}); "
                "se generó la devolución local por defecto."
            )
            resultado = _devolucion_mock(vista)
            resultado["provider"] = PROVIDER_MOCK
            resultado["modelo"] = "motor-local-determinista"
            resultado["generado_por_llm"] = False

    resultado["metricas_interpretadas"] = vista
    resultado["advertencias"] = advertencias + resultado.get("advertencias", [])
    return _asegurar_contrato(resultado)


# =============================================================================
# NORMALIZACIÓN DEL PAYLOAD
# =============================================================================
def _vista_vacia() -> Dict[str, Any]:
    """Estructura de vista neutra (todos los valores seguros)."""
    return {
        "operador": "N/A",
        "ejercicio": "N/A",
        "perfil": "N/A",
        "session_id_pre": None,
        "session_id_post": None,
        "sugerencia_admision": "N/A",
        "justificacion": "",
        "foco_instructor": "",
        "nivel_riesgo": RIESGO_MEDIO,
        "hallazgos": [],
        "metricas": {},
        "evolucion": None,
    }


def _texto(valor: Any, default: str = "") -> str:
    """Convierte a texto nativo, con valor por defecto para ``None``/vacío."""
    if valor is None:
        return default
    texto = str(valor).strip()
    return texto if texto else default


def _numero(valor: Any, default: Optional[float] = None) -> Optional[float]:
    """Convierte a ``float`` nativo o devuelve ``default`` si no es numérico."""
    if valor is None:
        return default
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def _normalizar_payload(payload: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Aplana cualquier forma conocida de payload a la vista interna del asesor.

    Formas aceptadas:
      1. Payload completo de :func:`obtener_payload_para_llm` (metadatos +
         fisicas_clave + metricas + diagnostico + evolucion).
      2. Salida de :func:`comparar_sesiones_delta` (pre/post/deltas/veredicto).
      3. ``metricas_calculadas`` plano de una única sesión.

    Criterio de referencia: cuando existe comparativa Pre/Post, la vista se
    construye sobre la sesión MÁS RECIENTE (Post) —métricas, hallazgos, dictamen y
    nivel de riesgo— porque es la que describe el estado actual del operador; la
    información de Pre queda en el bloque ``evolucion``.

    Returns:
        ``(vista, advertencias)``. La vista siempre tiene todas sus claves.
    """
    vista = _vista_vacia()
    advertencias: List[str] = []

    if not isinstance(payload, dict) or not payload:
        advertencias.append("Payload vacío o no es un dict: devolución genérica.")
        return vista, advertencias
    if payload.get("advertencia"):
        advertencias.append(_texto(payload["advertencia"]))

    # --- Caso 2: salida directa de comparar_sesiones_delta ---
    if "pre" in payload and "post" in payload and isinstance(payload.get("pre"), dict):
        bloque_pre = payload.get("pre") or {}
        bloque_post = payload.get("post") or {}
        metricas_pre = bloque_pre.get("metricas") or {}
        metricas_post = bloque_post.get("metricas") or {}
        vista["perfil"] = _texto(bloque_post.get("perfil"), _texto(bloque_pre.get("perfil"), "N/A"))
        vista["sugerencia_admision"] = "N/A"
        vista["evolucion"] = {
            "veredicto": _texto(payload.get("veredicto_evolucion"), VEREDICTO_SIN_DATOS),
            "delta_puntaje": _numero(
                (payload.get("deltas") or {}).get("puntaje_depurado"), 0.0
            ),
            "mejoras": list(payload.get("mejoras") or []),
            "retrocesos": list(payload.get("retrocesos") or []),
            "metricas_pre": metricas_pre,
            "metricas_post": metricas_post,
        }
        vista["metricas"] = dict(metricas_post or metricas_pre or {})
        return vista, advertencias

    # --- Caso 3: metricas_calculadas plano ---
    if "frenadas_bruscas" in payload and "metricas" not in payload:
        vista["metricas"] = dict(payload)
        vista["perfil"] = _texto(payload.get("perfil_operador"), "N/A")
        vista["sugerencia_admision"] = _texto(payload.get("sugerencia_admision"), "N/A")
        advertencias.append(
            "Payload sin dictamen del motor: el nivel de riesgo se deduce de los "
            "hallazgos disponibles."
        )
        return vista, advertencias

    # --- Caso 1: payload completo ---
    metadatos = payload.get("metadatos") or {}
    fisicas = payload.get("fisicas_clave") or {}
    metricas = payload.get("metricas") or {}
    metricas_pre = metricas.get("pre") or {}
    metricas_post = metricas.get("post") or {}
    diagnostico = payload.get("diagnostico") or {}
    evolucion = payload.get("evolucion")

    vista["operador"] = _texto(metadatos.get("operador"), "N/A")
    vista["ejercicio"] = _texto(metadatos.get("ejercicio"), "N/A")
    vista["session_id_pre"] = metadatos.get("session_id_pre")
    vista["session_id_post"] = metadatos.get("session_id_post")

    # Las métricas de referencia son las de la sesión MÁS RECIENTE disponible.
    base = metricas_post or metricas_pre
    if base:
        vista["metricas"] = dict(base)
    else:
        # Fallback: reconstruir desde ``fisicas_clave`` (pares pre/post).
        sufijo = "post" if metricas_post else "pre"
        vista["metricas"] = {
            "frenadas_bruscas": fisicas.get(f"frenadas_bruscas_{sufijo}", 0),
            "volantazos": fisicas.get(f"volantazos_{sufijo}", 0),
            "colisiones_netas": fisicas.get(f"colisiones_netas_{sufijo}", 0),
            "traslado_horquilla_alta_segundos": fisicas.get(
                f"tiempo_horquilla_alta_{sufijo}_seg", 0.0
            ),
            "tiempo_total_segundos": metadatos.get(f"duracion_{sufijo}_segundos", 0.0),
        }

    vista["perfil"] = _texto(
        metadatos.get("perfil_post"), _texto(metadatos.get("perfil_pre"), "N/A")
    )
    vista["sugerencia_admision"] = _texto(
        diagnostico.get("sugerencia_admision_post"),
        _texto(diagnostico.get("sugerencia_admision_pre"), "N/A"),
    )
    vista["justificacion"] = _texto(
        diagnostico.get("justificacion_post"), _texto(diagnostico.get("justificacion_pre"))
    )
    vista["foco_instructor"] = _texto(diagnostico.get("foco_instructor"))
    hallazgos_post = diagnostico.get("hallazgos_post")
    if hallazgos_post is not None:
        vista["hallazgos"] = list(hallazgos_post)
    else:
        vista["hallazgos"] = list(diagnostico.get("hallazgos_pre") or [])
    vista["nivel_riesgo"] = _texto(diagnostico.get("nivel_riesgo_post")) or (
        nivel_riesgo_por_dictamen(vista["sugerencia_admision"])
    )

    senales = (
        diagnostico.get("senales_disponibles_post")
        or diagnostico.get("senales_disponibles_pre")
        or []
    )
    # Se registra SIEMPRE (incluso vacía) para poder advertir cobertura nula.
    vista["metricas"]["senales_disponibles"] = list(senales)

    if evolucion:
        deltas = evolucion.get("deltas") or {}
        vista["evolucion"] = {
            "veredicto": _texto(evolucion.get("veredicto"), VEREDICTO_SIN_DATOS),
            "delta_puntaje": _numero(deltas.get("puntaje_depurado"), 0.0),
            "delta_puntaje_pct": _numero(deltas.get("puntaje_depurado_pct")),
            "mejoras": list(evolucion.get("mejoras") or []),
            "retrocesos": list(evolucion.get("retrocesos") or []),
            "metricas_pre": metricas_pre,
            "metricas_post": metricas_post,
        }

    return vista, advertencias


# =============================================================================
# GENERADOR LOCAL (MOCK) — DETERMINISTA Y SIN COSTO DE API
# =============================================================================
def _formatear_valor(valor: Any) -> str:
    """Formatea un valor de hallazgo para la narrativa (entero o 1 decimal)."""
    numero = _numero(valor)
    if numero is None:
        return _texto(valor, "0")
    return f"{numero:.0f}" if abs(numero - round(numero)) < 1e-9 else f"{numero:.1f}"


def _vicios_desde_hallazgos(vista: Dict[str, Any]) -> List[str]:
    """Traduce los hallazgos estructurados del motor a vicios operativos narrados."""
    vicios: List[str] = []
    for hallazgo in vista.get("hallazgos") or []:
        if not isinstance(hallazgo, dict):
            continue
        codigo = _texto(hallazgo.get("codigo"))
        etiqueta = _VICIO_POR_HALLAZGO.get(codigo, _texto(hallazgo.get("descripcion")))
        if not etiqueta:
            continue
        valor = hallazgo.get("valor")
        detalle = "" if valor is None else f" (evidencia: {_formatear_valor(valor)})"
        vicios.append(f"{etiqueta}{detalle}")
    return vicios


def _plan_desde_hallazgos(vista: Dict[str, Any]) -> List[str]:
    """Plan de acción correctivo derivado de los hallazgos, priorizado por severidad."""
    hallazgos = [h for h in (vista.get("hallazgos") or []) if isinstance(h, dict)]
    ordenados = sorted(hallazgos, key=lambda h: -int(_numero(h.get("severidad"), 0) or 0))

    plan: List[str] = []
    for hallazgo in ordenados:
        accion = _PLAN_POR_HALLAZGO.get(_texto(hallazgo.get("codigo")))
        if accion and accion not in plan:
            plan.append(accion)

    if not plan:
        plan.append(_ACCION_APTO if vista.get("sugerencia_admision") == DICTAMEN_APTO
                    else _ACCION_POR_DEFECTO)
    return plan


def _frase_evolucion(evolucion: Dict[str, Any]) -> str:
    """Narra la comparativa Pre/Post a partir de datos estructurados."""
    veredicto = _texto(evolucion.get("veredicto"), VEREDICTO_SIN_DATOS)
    delta = _numero(evolucion.get("delta_puntaje"), 0.0) or 0.0
    mejoras = evolucion.get("mejoras") or []
    retrocesos = evolucion.get("retrocesos") or []

    partes = [f"En la comparativa Pre/Post el motor dictamina {veredicto} "
              f"(delta de puntaje depurado {delta:+.2f})"]
    if mejoras:
        nombres = ", ".join(
            _texto(m.get("metrica")) for m in mejoras if isinstance(m, dict)
        )
        if nombres:
            partes.append(f"mejoró en {nombres}")
    if retrocesos:
        nombres = ", ".join(
            _texto(r.get("metrica")) for r in retrocesos if isinstance(r, dict)
        )
        if nombres:
            partes.append(f"retrocedió en {nombres}")
    if not mejoras and not retrocesos and veredicto != VEREDICTO_SIN_DATOS:
        partes.append("sin variaciones relevantes entre sesiones")
    return "; ".join(partes) + "."


def _diagnostico_local(vista: Dict[str, Any]) -> str:
    """Redacta el diagnóstico de desempeño con los datos disponibles."""
    metricas = vista.get("metricas") or {}
    dictamen = _texto(vista.get("sugerencia_admision"), "N/A")
    riesgo = _texto(vista.get("nivel_riesgo"), RIESGO_MEDIO)
    operador = _texto(vista.get("operador"), "N/A")
    ejercicio = _texto(vista.get("ejercicio"), "N/A")

    frases = [
        f"El operador {operador} obtuvo un dictamen {dictamen} "
        f"(nivel de riesgo {riesgo}) en el ejercicio {ejercicio}."
    ]

    resumen_metricas = []
    for etiqueta, clave in (
        ("colisiones netas", "colisiones_netas"),
        ("frenadas bruscas", "frenadas_bruscas"),
        ("volantazos", "volantazos"),
    ):
        valor = _numero(metricas.get(clave))
        if valor is not None:
            resumen_metricas.append(f"{valor:.0f} {etiqueta}")
    if resumen_metricas:
        frases.append("La telemetría registra " + ", ".join(resumen_metricas) + ".")

    traslado = _numero(metricas.get("traslado_horquilla_alta_segundos"))
    if traslado and traslado > 0:
        frases.append(
            f"Se contabilizan {traslado:.0f}s de traslado con la horquilla por encima "
            f"de {FORK_SAFE_THRESHOLD_MTRS:.2f} m, posición insegura según normativa."
        )

    if vista.get("evolucion"):
        frases.append(_frase_evolucion(vista["evolucion"]))

    justificacion = _texto(vista.get("justificacion"))
    if justificacion and dictamen == DICTAMEN_NO_APTO_CRITICO:
        frases.append(justificacion)

    senales = metricas.get("senales_disponibles")
    if isinstance(senales, list) and len(senales) < COBERTURA_MINIMA_SENALES:
        frases.append(
            f"Atención: solo hay {len(senales)}/{len(SENALES_CANONICAS)} señales de "
            "telemetría disponibles, por lo que el diagnóstico es provisional y los "
            "contadores en 0 pueden deberse a datos ausentes."
        )
    return " ".join(frases)


def _foco_prioritario(vista: Dict[str, Any], plan: List[str]) -> str:
    """Prioridad única para la próxima sesión."""
    hallazgos = [h for h in (vista.get("hallazgos") or []) if isinstance(h, dict)]
    if hallazgos:
        grave = max(hallazgos, key=lambda h: int(_numero(h.get("severidad"), 0) or 0))
        etiqueta = _VICIO_POR_HALLAZGO.get(_texto(grave.get("codigo")))
        if etiqueta:
            return f"Corregir primero: {etiqueta.lower()}."
    foco = _texto(vista.get("foco_instructor"))
    if foco:
        return foco
    return plan[0] if plan else _ACCION_POR_DEFECTO


def _devolucion_mock(vista: Dict[str, Any]) -> Dict[str, Any]:
    """Devolución pedagógica local determinista (sin llamada a LLM)."""
    vicios = _vicios_desde_hallazgos(vista)
    if not vicios:
        vicios = [
            "No se detectaron vicios operativos relevantes por encima de los umbrales "
            "del motor de evaluación."
        ]

    plan = _plan_desde_hallazgos(vista)

    evolucion = vista.get("evolucion")
    if evolucion:
        veredicto = _texto(evolucion.get("veredicto"), VEREDICTO_SIN_DATOS)
        if veredicto in (VEREDICTO_MEJORA_SIGNIFICATIVA, VEREDICTO_MEJORA_MODERADA):
            plan.append(
                "Consolidar lo adquirido: repetir el mismo ejercicio aumentando la "
                "exigencia de ciclo para verificar que la mejora se sostiene bajo presión."
            )
        elif veredicto == VEREDICTO_REGRESION:
            plan.append(
                "Revisar la regresión detectada antes de avanzar: volver al ejercicio "
                "de diagnóstico y confirmar que el alumno no incorporó prisa en lugar "
                "de técnica."
            )

    sugerencia = _texto(vista.get("sugerencia_admision"), "N/A")
    riesgo = nivel_riesgo_por_dictamen(sugerencia) if sugerencia != "N/A" else (
        _texto(vista.get("nivel_riesgo"), RIESGO_MEDIO)
    )

    return {
        CLAVE_DIAGNOSTICO: _diagnostico_local(vista),
        CLAVE_VICIOS: vicios,
        CLAVE_PLAN: plan,
        CLAVE_FOCO: _foco_prioritario(vista, plan),
        CLAVE_RIESGO: riesgo,
    }


# =============================================================================
# GENERADOR REMOTO (LLM)
# =============================================================================
def _payload_para_prompt(vista: Dict[str, Any]) -> str:
    """Serializa la vista interna como JSON estable para enviarla al LLM."""
    resumido = {
        "metadatos": {
            "operador": vista.get("operador"),
            "ejercicio": vista.get("ejercicio"),
            "perfil": vista.get("perfil"),
            "session_id_pre": vista.get("session_id_pre"),
            "session_id_post": vista.get("session_id_post"),
        },
        "metricas": vista.get("metricas") or {},
        "dictamen_motor": {
            "sugerencia_admision": vista.get("sugerencia_admision"),
            "nivel_riesgo": vista.get("nivel_riesgo"),
            "justificacion": vista.get("justificacion"),
            "foco_instructor": vista.get("foco_instructor"),
            "hallazgos": vista.get("hallazgos") or [],
        },
        "evolucion": vista.get("evolucion"),
    }
    return json.dumps(resumido, ensure_ascii=False, indent=2, default=str)


def _extraer_json(texto: str) -> Dict[str, Any]:
    """Extrae el primer objeto JSON de una respuesta de LLM (tolera rodeo textual)."""
    if not texto:
        raise ValueError("respuesta vacía del modelo")
    candidato = texto.strip()
    # Quitar cercos de bloque de código si el modelo los añadió pese a la instrucción.
    if candidato.startswith("```"):
        candidato = re.sub(r"^```[a-zA-Z]*\s*", "", candidato)
        candidato = re.sub(r"\s*```$", "", candidato)
    try:
        datos = json.loads(candidato)
    except json.JSONDecodeError:
        coincidencia = re.search(r"\{.*\}", candidato, re.DOTALL)
        if not coincidencia:
            raise ValueError("la respuesta no contiene un objeto JSON")
        datos = json.loads(coincidencia.group(0))
    if not isinstance(datos, dict):
        raise ValueError("la respuesta JSON no es un objeto")
    return datos


def _llamar_openai(prompt_usuario: str, api_key: str, modelo: str) -> str:
    import requests  # import perezoso: la ruta mock no necesita red

    respuesta = requests.post(
        ENDPOINT_OPENAI,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": modelo,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_INSTRUCTOR},
                {"role": "user", "content": prompt_usuario},
            ],
        },
        timeout=TIMEOUT_SEGUNDOS,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()
    return datos["choices"][0]["message"]["content"]


def _llamar_anthropic(prompt_usuario: str, api_key: str, modelo: str) -> str:
    import requests  # import perezoso: la ruta mock no necesita red

    respuesta = requests.post(
        ENDPOINT_ANTHROPIC,
        headers={
            "x-api-key": api_key,
            "anthropic-version": VERSION_ANTHROPIC,
            "Content-Type": "application/json",
        },
        json={
            "model": modelo,
            "max_tokens": MAX_TOKENS_RESPUESTA,
            "temperature": 0.2,
            "system": SYSTEM_PROMPT_INSTRUCTOR,
            "messages": [{"role": "user", "content": prompt_usuario}],
        },
        timeout=TIMEOUT_SEGUNDOS,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()
    bloques = datos.get("content") or []
    return "".join(b.get("text", "") for b in bloques if isinstance(b, dict))


def _devolucion_llm(
    vista: Dict[str, Any], proveedor: str, api_key: str, modelo: str
) -> Dict[str, Any]:
    """Llama al proveedor configurado y devuelve la devolución parseada."""
    prompt_usuario = USER_PROMPT_PLANTILLA.format(
        payload_json=_payload_para_prompt(vista)
    )
    if proveedor == PROVIDER_OPENAI:
        texto = _llamar_openai(prompt_usuario, api_key, modelo)
    elif proveedor == PROVIDER_ANTHROPIC:
        texto = _llamar_anthropic(prompt_usuario, api_key, modelo)
    else:
        raise ValueError(f"Proveedor sin implementación de red: {proveedor}")

    datos = _extraer_json(texto)
    riesgo = _texto(datos.get(CLAVE_RIESGO)).lower()
    if riesgo not in (RIESGO_ALTO, RIESGO_MEDIO, RIESGO_BAJO):
        # El modelo no respetó la enumeración: se impone el riesgo del motor.
        riesgo = _texto(vista.get("nivel_riesgo"), RIESGO_MEDIO)
        datos["advertencias"] = [
            "El modelo devolvió un nivel de riesgo fuera de contrato; se usó el "
            "calculado por el motor de evaluación."
        ]
    datos[CLAVE_RIESGO] = riesgo
    return datos


# =============================================================================
# CONTRATO DE SALIDA
# =============================================================================
def _lista_textos(valor: Any, maximo: int = 8) -> List[str]:
    """Normaliza una lista de ítems a ``List[str]`` nativa y acotada."""
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    textos = [_texto(item) for item in valor]
    return [t for t in textos if t][:maximo]


def _asegurar_contrato(resultado: Dict[str, Any]) -> Dict[str, Any]:
    """Garantiza que la salida tenga exactamente las claves del contrato.

    Completa huecos con los valores del generador local para que ningún consumidor
    (UI, PDF, API REST) deba validar tipos ni claves ausentes.
    """
    resultado = resultado if isinstance(resultado, dict) else {}
    vacio = _devolucion_mock(_vista_vacia())
    for clave in (CLAVE_DIAGNOSTICO, CLAVE_VICIOS, CLAVE_PLAN, CLAVE_FOCO, CLAVE_RIESGO):
        if clave not in resultado or resultado[clave] in (None, "", []):
            resultado[clave] = vacio[clave]

    resultado[CLAVE_DIAGNOSTICO] = _texto(resultado[CLAVE_DIAGNOSTICO], vacio[CLAVE_DIAGNOSTICO])
    resultado[CLAVE_VICIOS] = _lista_textos(resultado[CLAVE_VICIOS]) or vacio[CLAVE_VICIOS]
    resultado[CLAVE_PLAN] = _lista_textos(resultado[CLAVE_PLAN]) or vacio[CLAVE_PLAN]
    resultado[CLAVE_FOCO] = _texto(resultado[CLAVE_FOCO], vacio[CLAVE_FOCO])
    if resultado[CLAVE_RIESGO] not in (RIESGO_ALTO, RIESGO_MEDIO, RIESGO_BAJO):
        resultado[CLAVE_RIESGO] = RIESGO_MEDIO

    resultado.setdefault("provider", PROVIDER_MOCK)
    resultado.setdefault("modelo", "motor-local-determinista")
    resultado.setdefault("generado_por_llm", False)
    resultado.setdefault("metricas_interpretadas", _vista_vacia())
    resultado["advertencias"] = _lista_textos(resultado.get("advertencias"), maximo=10)
    return resultado
