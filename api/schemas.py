# api/schemas.py
# Proyecto Titán — Modelos de request/response de la capa REST (Pydantic v2).
#
# Reglas de diseño:
#   * Los TIPOS DE DOMINIO (dictamen, riesgo, veredicto, sentido) se construyen
#     como ``Literal`` a partir de las CONSTANTES importadas de ``core``; la API
#     NUNCA hardcodea las cadenas de negocio (con tildes incluidas), de modo que
#     un cambio de umbral o de etiqueta en ``core`` se propaga solo.
#   * Los modelos de REQUEST son estrictos (``extra="forbid"``): una clave ajena
#     provoca 422. Los de RESPONSE son permisivos (por defecto ``extra="ignore"``)
#     para tolerar claves nuevas que ``core`` pueda añadir sin romper el contrato.
#   * Sintaxis Pydantic v2: ``ConfigDict``, ``field_validator``,
#     ``model_validator`` y ``.model_dump``. Todo ``Optional[X]`` lleva su ``= None``.
#   * NO se fija ``response_model_exclude_none``: los ``*_pct`` en ``None`` son
#     información de negocio (porcentaje no definido cuando la base es 0), no ruido.

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- Constantes de negocio importadas de core (única fuente de verdad) --------
from core.ai_advisor import PROVEEDORES_SOPORTADOS, PROVIDER_MOCK
from core.evaluador_diagnostico import (
    DICTAMEN_APTO,
    DICTAMEN_NO_APTO_CRITICO,
    DICTAMEN_OBSERVADO,
    MAYOR_ES_MEJOR,
    MENOR_ES_MEJOR,
    RIESGO_ALTO,
    RIESGO_BAJO,
    RIESGO_MEDIO,
    VEREDICTO_ESTANCADO,
    VEREDICTO_MEJORA_MODERADA,
    VEREDICTO_MEJORA_SIGNIFICATIVA,
    VEREDICTO_REGRESION,
    VEREDICTO_SIN_DATOS,
)

# =============================================================================
# TIPOS DE DOMINIO (Literal construidos desde las constantes de core)
# =============================================================================
DictamenLiteral = Literal[
    DICTAMEN_APTO, DICTAMEN_OBSERVADO, DICTAMEN_NO_APTO_CRITICO
]
RiesgoLiteral = Literal[RIESGO_BAJO, RIESGO_MEDIO, RIESGO_ALTO]
VeredictoLiteral = Literal[
    VEREDICTO_MEJORA_SIGNIFICATIVA,
    VEREDICTO_MEJORA_MODERADA,
    VEREDICTO_REGRESION,
    VEREDICTO_ESTANCADO,
    VEREDICTO_SIN_DATOS,
]
SentidoLiteral = Literal[MAYOR_ES_MEJOR, MENOR_ES_MEJOR]
ProveedorLiteral = Literal[
    PROVEEDORES_SOPORTADOS[0],
    PROVEEDORES_SOPORTADOS[1],
    PROVEEDORES_SOPORTADOS[2],
    PROVEEDORES_SOPORTADOS[3],
    PROVEEDORES_SOPORTADOS[4],
]

# Etiqueta del eje X de las series normalizadas a porcentaje de progreso.
EJE_X_PROGRESO: str = "Avance del Ejercicio (%)"


# =============================================================================
# MODELOS BASE
# =============================================================================
class _ModeloRespuesta(BaseModel):
    """Base permisiva para respuestas: tolera claves nuevas de ``core``."""

    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class _ModeloPeticion(BaseModel):
    """Base estricta para peticiones: rechaza claves desconocidas con 422."""

    model_config = ConfigDict(extra="forbid")


# =============================================================================
# HEALTH
# =============================================================================
class HealthResponse(_ModeloRespuesta):
    """Estado del servicio. Siempre HTTP 200 (incluso con ``db_disponible=False``)."""

    status: str = "ok"
    db_disponible: bool
    sesiones: int
    version: str


# =============================================================================
# EVALUACIÓN — DIAGNÓSTICO DE ADMISIÓN
# =============================================================================
class Hallazgo(_ModeloRespuesta):
    """Vicio detectado por el motor: código estable + evidencia y umbral."""

    codigo: str
    descripcion: str
    severidad: int
    valor: Any = None
    umbral: Any = None


class MetricasCalculadas(_ModeloRespuesta):
    """Las 18 métricas físicas que calcula ``evaluar_diagnostico_inicial``.

    Los nombres y tipos reflejan EXACTAMENTE el contrato de ``core``. Se aportan
    valores por defecto para que una respuesta parcial siga validando (la API es
    permisiva en salida), aunque ``core`` siempre devuelve las 18 claves.
    """

    frenadas_bruscas: int = 0
    volantazos: int = 0
    colisiones_netas: int = 0
    tiempo_total_segundos: float = 0.0
    inseguridad_torre_microajustes: int = 0
    inseguridad_torre_segundos: float = 0.0
    traslado_horquilla_alta_segundos: float = 0.0
    varianza_steering: float = 0.0
    picos_freno_alto: int = 0
    freno_max: float = 0.0
    steering_max_abs: float = 0.0
    microajustes_horquilla: int = 0
    microajustes_inclinacion: int = 0
    altura_maxima_mtrs: float = 0.0
    puntaje_final: float = 0.0
    perfil_operador: str = "N/A"
    sesion_existente: bool = False
    senales_disponibles: List[str] = Field(default_factory=list)


class EvaluarAdmisionRequest(_ModeloPeticion):
    """Petición del dictamen de admisión (Día 1)."""

    session_id: int
    incluir_series: bool = False


class DiagnosticoResponse(_ModeloRespuesta):
    """Dictamen de admisión saneado para la API.

    ESQUEMA DE SESIÓN: la descripción completa de una sesión se compone de DOS
    bloques complementarios — ``Session`` (los metadatos de la fila: operador,
    clase, ejercicio, fechas y puntajes) y ``MetricasCalculadas`` (las 18 métricas
    físicas de telemetría que calcula el motor). Juntos constituyen el "esquema de
    sesión". ``DiagnosticoResponse`` los COMBINA: toma las ``metricas`` de la sesión
    evaluada (``MetricasCalculadas``) y les superpone el dictamen pedagógico
    (``semaforo``/``justificacion``/``foco_instructor``/``nivel_riesgo``/
    ``hallazgos``). Los metadatos descriptivos de ``Session`` se exponen por
    separado en ``GET /api/v1/sesiones``.
    """

    semaforo: DictamenLiteral
    justificacion: str
    foco_instructor: str
    nivel_riesgo: RiesgoLiteral
    hallazgos: List[Hallazgo] = Field(default_factory=list)
    severidad_total: int
    metricas: MetricasCalculadas
    estado: str
    puntaje_depurado: float
    # Solo presente si ``incluir_series=True`` (si ausente para aligerar el payload).
    series: Optional[Dict[str, List[List[float]]]] = None


# =============================================================================
# EVALUACIÓN — COMPARATIVA DELTA (PRE / POST)
# =============================================================================
class CompararDeltaRequest(_ModeloPeticion):
    """Petición de la comparativa Pre/Post (Día Final)."""

    session_id_pre: int
    session_id_post: int
    incluir_series: bool = False

    @model_validator(mode="after")
    def _validar_sesiones_distintas(self) -> "CompararDeltaRequest":
        if self.session_id_pre == self.session_id_post:
            raise ValueError(
                "session_id_pre y session_id_post deben ser sesiones distintas"
            )
        return self


class DeltaBloque(_ModeloRespuesta):
    """Bloque de métricas de una de las dos sesiones comparadas."""

    session_id: int
    metricas: MetricasCalculadas
    puntaje_depurado: float = 0.0
    perfil: str = "N/A"
    duracion_segundos: float = 0.0


class Deltas(_ModeloRespuesta):
    """Los 12 deltas de la comparativa; cada ``*_pct`` es ``None`` si la base es 0."""

    duracion_seg: float = 0.0
    duracion_pct: Optional[float] = None
    colisiones: int = 0
    colisiones_pct: Optional[float] = None
    frenadas_bruscas: int = 0
    frenadas_bruscas_pct: Optional[float] = None
    volantazos: int = 0
    volantazos_pct: Optional[float] = None
    suavidad: int = 0
    suavidad_pct: Optional[float] = None
    puntaje_depurado: float = 0.0
    puntaje_depurado_pct: Optional[float] = None


class ItemEvolucion(_ModeloRespuesta):
    """Una métrica clasificada como mejora o retroceso por el motor."""

    metrica: str
    pre: Any = None
    post: Any = None
    delta: Any = None
    pct: Optional[float] = None
    sentido: SentidoLiteral


class CompararDeltaResponse(_ModeloRespuesta):
    """Comparativa Pre/Post con series normalizadas a progreso (0->100 %)."""

    pre: DeltaBloque
    post: DeltaBloque
    deltas: Deltas
    veredicto_evolucion: VeredictoLiteral
    mejoras: List[ItemEvolucion] = Field(default_factory=list)
    retrocesos: List[ItemEvolucion] = Field(default_factory=list)
    # SIEMPRE presentes (ligeras): eje X de progreso para graficar la evolución.
    series_normalizadas_pre: Dict[str, List[List[float]]] = Field(default_factory=dict)
    series_normalizadas_post: Dict[str, List[List[float]]] = Field(default_factory=dict)
    eje_x: str = EJE_X_PROGRESO
    # Series RAW (grandes): solo si ``incluir_series=True``.
    series_pre: Optional[Dict[str, List[List[float]]]] = None
    series_post: Optional[Dict[str, List[List[float]]]] = None


# =============================================================================
# ASESOR — DEVOLUCIÓN PEDAGÓGICA
# =============================================================================
class DevolucionRequest(_ModeloPeticion):
    """Petición de la devolución pedagógica asistida por LLM.

    Se admite O bien una sesión Pre (opcionalmente con Post) O bien un
    ``payload_metricas`` ya construido por el cliente. La ``api_key`` NUNCA se
    registra ni se devuelve.
    """

    session_id_pre: Optional[int] = None
    session_id_post: Optional[int] = None
    payload_metricas: Optional[Dict[str, Any]] = None
    provider: str = PROVIDER_MOCK
    api_key: Optional[str] = None
    incluir_metricas_interpretadas: bool = False

    @field_validator("provider")
    @classmethod
    def _validar_provider(cls, valor: str) -> str:
        proveedor = (valor or "").strip().lower()
        if proveedor not in PROVEEDORES_SOPORTADOS:
            raise ValueError(
                f"provider debe ser uno de {list(PROVEEDORES_SOPORTADOS)}"
            )
        return proveedor

    @model_validator(mode="after")
    def _validar_fuente_de_datos(self) -> "DevolucionRequest":
        if self.session_id_pre is None and self.payload_metricas is None:
            raise ValueError(
                "debe proporcionarse session_id_pre o payload_metricas"
            )
        return self


class DevolucionResponse(_ModeloRespuesta):
    """Contrato de salida del asesor pedagógico (las 11 claves de ``core``)."""

    resumen_ejecutivo: str
    puntos_fuertes: List[str] = Field(default_factory=list)
    vicios_criticos: List[str] = Field(default_factory=list)
    plan_accion_recomendado: List[str] = Field(default_factory=list)
    nivel_riesgo: RiesgoLiteral
    foco_prioritario: str
    # Meta (revelan la degradación a generador local determinista).
    provider: str
    modelo: str
    generado_por_llm: bool
    advertencias: List[str] = Field(default_factory=list)
    # Solo presente si ``incluir_metricas_interpretadas=True``.
    metricas_interpretadas: Optional[Dict[str, Any]] = None


# =============================================================================
# SESIONES Y TELEMETRÍA
# =============================================================================
class Session(_ModeloRespuesta):
    """Fila descriptiva de ``Sesiones`` (las 8 claves de ``list_sessions`` + 2)."""

    id_sesion: int
    nombre_operador: Optional[str] = None
    nombre_clase: Optional[str] = None
    nombre_ejercicio: Optional[str] = None
    fecha_hora_inicio: Optional[str] = None
    duracion_segundos: Optional[float] = None
    puntaje_final: Optional[float] = None
    perfil_operador: Optional[str] = None
    # Declaradas en schema.sql pero pueden no existir en la BD productiva.
    puntaje_depurado: Optional[float] = None
    checklist_completado: Optional[int] = None


class TelemetriaResponse(_ModeloRespuesta):
    """Series de telemetría de una sesión, crudas o normalizadas a progreso."""

    session_id: int
    normalizar: bool
    eje_x: str
    series: Dict[str, List[List[float]]] = Field(default_factory=dict)


class IngestarResponse(_ModeloRespuesta):
    """Resultado de la ingesta de un PDF del simulador."""

    session_id: Optional[int] = None
    cached: bool = False
    errors: List[str] = Field(default_factory=list)
    telemetry_loaded: Dict[str, int] = Field(default_factory=dict)
