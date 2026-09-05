# core/evaluador_diagnostico.py
# Proyecto Titán — Motor de evaluación pedagógica (lógica pura, SIN frontend).
#
# Este módulo concentra TODAS las reglas de negocio pedagógicas del diagnóstico de
# admisión y de la comparativa de evolución (delta) entre sesiones de simulador.
# No depende de ninguna librería de UI: solo de sqlite3, numpy y de los helpers de
# acceso a datos de ``core.db_manager``.
#
# Principios de diseño:
#   * Tolerancia a datos incompletos: si una señal de telemetría no existe, si la
#     sesión no está en la BD o si las series vienen vacías, NO se lanzan
#     excepciones; se devuelven dicts coherentes con valores por defecto (0 / 0.0)
#     y una justificación en texto.
#   * Cobertura explícita: la AUSENCIA de telemetría NUNCA se confunde con
#     "conducción limpia". Si faltan señales críticas no se emite "Apto".
#   * SQL centralizado en ``core.db_manager`` (siempre parametrizado).
#   * Series SIEMPRE ordenadas por tiempo antes de calcular derivadas.
#   * Integración temporal robusta a huecos de muestreo: los saltos mayores que
#     ``DT_MAX`` NO suman tiempo ni se interpolan (no se inventa velocidad).
#   * Detección de vicios por EVENTOS (rachas / inversiones), combinando tasa y
#     amplitud, alineada con la especificación pedagógica.
#   * Umbrales definidos como constantes de módulo, documentadas y ajustables.
#   * Salidas 100 % nativas y JSON-serializables (dict / list / float / str): el
#     mismo contrato sirve para Streamlit, para la app desktop y para una API REST.

"""Motor de evaluación pedagógica del Proyecto Titán.

Expone cuatro capacidades principales:

1. :func:`evaluar_diagnostico_inicial` — dictamen de admisión (Día 1) a partir de
   los vicios de manejo detectados en la telemetría y las colisiones registradas.
2. :func:`comparar_sesiones_delta` — comparativa Pre/Post (Día Final) con deltas
   absolutos y porcentuales, veredicto de evolución y clasificación estructurada
   de mejoras/retrocesos.
3. Persistencia de la decisión del instructor sobre la tabla ``DecisionInstructor``
   (creada de forma segura con ``CREATE TABLE IF NOT EXISTS``).
4. :func:`obtener_payload_para_llm` — payload plano y JSON-serializable con las
   métricas físicas que consume :mod:`core.ai_advisor`.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# --- Reutilización de helpers existentes ---
# ``map_graphs`` normaliza ``nombre_grafico`` a las 6 señales canónicas (y resuelve
# nombres legacy ``Graph_X_Y`` vía mapeo.json), igual que el pipeline de ingesta.
from core.db_manager import (
    get_session_row,
    sumar_eventos_por_tipo,
    sumar_penalizaciones,
)
from core.graph_mapper import map_graphs

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTES DE UMBRAL (ajustables en un único lugar)
# =============================================================================
# Nombres EXACTOS de las 6 señales canónicas (core.graph_mapper.CANONICAL_SIGNALS).
SENAL_STEERING = "Steering"
SENAL_BRAKE = "Brake Pad"
SENAL_ACCELERATION = "Acceleration Pad"
SENAL_FORK_HEIGHT = "Fork Height In Mtrs"
SENAL_TILT = "Tilt Angle In Deg"
SENAL_SPEED = "Speed In Km/h"

SENALES_CANONICAS: List[str] = [
    SENAL_STEERING,
    SENAL_BRAKE,
    SENAL_ACCELERATION,
    SENAL_FORK_HEIGHT,
    SENAL_TILT,
    SENAL_SPEED,
]

# Señales imprescindibles para poder afirmar que NO hay vicios. Si falta
# alguna, la ausencia de detección se debe a falta de datos, no a buena conducción.
SENALES_CRITICAS: List[str] = [SENAL_BRAKE, SENAL_STEERING, SENAL_FORK_HEIGHT]
# Cobertura mínima (de 6 señales) exigida para poder emitir un "Apto".
COBERTURA_MINIMA_SENALES: int = 4

# --- Muestreo / integración temporal -----------------------------------
# Intervalo máximo (s) entre muestras que se considera un muestreo real continuo.
# Por encima de este valor hay un HUECO (pérdida de extracción): ese tramo NO suma
# tiempo ni se interpola, para no fabricar velocidad/duración sin evidencia.
DT_MAX: float = 3.0

# --- Frenadas bruscas --------------------------------------------------
# Tasa de subida del pedal de freno (unidades/s). Referencia de behavior_analyzer.
FRENADO_TASA_UMBRAL: float = 5.0
# Valor alto del pedal de freno (rango típico 0..10): "subida vertical hacia 10".
FRENADO_VALOR_ALTO: float = 8.0
# Número de EVENTOS de frenada brusca para severidad moderada / alta.
FRENADAS_EVENTOS_MODERADO: int = 4
FRENADAS_EVENTOS_ALTO: int = 10
# Muestras con el pedal en rango alto (frenado sostenido/dureza) que aportan
# severidad al dictamen (antes se calculaba y se descartaba).
PICOS_FRENO_ALTO_UMBRAL: int = 20

# --- Volantazos --------------------------------------------------------
# Tasa de cambio absoluta de la dirección (unidades/s). Referencia behavior_analyzer.
DIRECCION_TASA_UMBRAL: float = 15.0
# Amplitud mínima (unidades de dirección, rango -14..+14) para que una inversión de
# giro se considere un volantazo real y no ruido de calibración.
DIRECCION_AMPLITUD_MIN: float = 2.0
# Varianza de la dirección por encima de la cual se considera conducción nerviosa.
DIRECCION_VARIANZA_ALTA: float = 25.0
# Número de EVENTOS de volantazo para severidad moderada / alta.
VOLANTAZOS_EVENTOS_MODERADO: int = 4
VOLANTAZOS_EVENTOS_ALTO: int = 10

# --- Inseguridad en torre ----------------------------------------------
# Amplitud mínima de un movimiento de horquilla (m) / inclinación (deg) para
# considerarlo un microajuste real y no ruido de calibración.
MICROAJUSTE_AMPLITUD_MIN: float = 0.02
# Inversiones de dirección a partir de las cuales hay inseguridad relevante.
MICROAJUSTE_INVERSIONES_ALTO: int = 40
# Segundos acumulados en régimen de microajuste que aportan severidad.
MICROAJUSTE_SEGUNDOS_ALTO: float = 30.0

# --- Traslado con horquilla alta ---------------------------------------
# Altura de horquilla (m) por encima de la cual NO es seguro trasladarse.
FORK_SAFE_THRESHOLD_MTRS: float = 0.30
# Velocidad mínima (Km/h) para considerar que la máquina se está trasladando.
VELOCIDAD_MOVIMIENTO_MIN: float = 0.5
# Segundos acumulados trasladando con horquilla alta que indican vicio relevante.
TRASLADO_ALTO_SEGUNDOS_ALTO: float = 15.0

# --- Colisiones -------------------------------------------------------------
COLISIONES_CRITICO: int = 3
COLISIONES_OBSERVADO: int = 1
# ``tipo_evento`` tal como lo registra el simulador en ``ResumenEventos``.
TIPO_EVENTO_COLISION: str = "Collision"

# --- Veredicto de evolución (delta de puntaje depurado) ----------------------
# Única definición de la regla: la consume tanto el motor de diagnóstico como el
# generador de documentos (``core.report_generator``).
UMBRAL_MEJORA_SIGNIFICATIVA: float = 15.0
UMBRAL_MEJORA_MODERADA: float = 1.0
UMBRAL_REGRESION: float = -1.0

VEREDICTO_MEJORA_SIGNIFICATIVA: str = "MEJORA SIGNIFICATIVA"
VEREDICTO_MEJORA_MODERADA: str = "MEJORA MODERADA"
VEREDICTO_REGRESION: str = "REGRESIÓN DETECTADA"
VEREDICTO_ESTANCADO: str = "RENDIMIENTO ESTANCADO"
VEREDICTO_SIN_DATOS: str = "SIN DATOS"

# --- Sentido de mejora de cada métrica comparada -----------------------------
MAYOR_ES_MEJOR: str = "mayor_es_mejor"
MENOR_ES_MEJOR: str = "menor_es_mejor"

# --- Dictámenes de admisión y nivel de riesgo --------------------------------
DICTAMEN_APTO: str = "Apto"
DICTAMEN_OBSERVADO: str = "Observado"
DICTAMEN_NO_APTO_CRITICO: str = "No Apto Crítico"

RIESGO_BAJO: str = "bajo"
RIESGO_MEDIO: str = "medio"
RIESGO_ALTO: str = "alto"

# Severidad acumulada que dispara cada dictamen.
SEVERIDAD_OBSERVADO: int = 2
SEVERIDAD_NO_APTO: int = 5

# --- Códigos estables de hallazgos (contrato con UI / documentos / LLM) -------
HALLAZGO_COBERTURA_INSUFICIENTE: str = "cobertura_insuficiente"
HALLAZGO_SESION_INEXISTENTE: str = "sesion_inexistente"
HALLAZGO_COLISIONES_CRITICAS: str = "colisiones_criticas"
HALLAZGO_COLISIONES: str = "colisiones"
HALLAZGO_FRENADAS_ALTO: str = "frenadas_bruscas_alto"
HALLAZGO_FRENADAS_MODERADO: str = "frenadas_bruscas_moderado"
HALLAZGO_FRENO_SOSTENIDO: str = "freno_sostenido"
HALLAZGO_VOLANTAZOS_ALTO: str = "volantazos_alto"
HALLAZGO_VOLANTAZOS_MODERADO: str = "volantazos_moderado"
HALLAZGO_TRASLADO_ALTO_SEVERO: str = "traslado_horquilla_alta_severo"
HALLAZGO_TRASLADO_ALTO: str = "traslado_horquilla_alta"
HALLAZGO_INSEGURIDAD_TORRE: str = "inseguridad_torre"


# =============================================================================
# UTILIDADES INTERNAS DE SERIES Y DERIVADAS
# =============================================================================
def _deserializar_csv(
    timestamps_raw: Any, valores_raw: Any
) -> List[Tuple[float, float]]:
    """Deserializa los CSV paralelos ``timestamps``/``valores`` a pares (t, v) float.

    La BD guarda cada señal como UNA fila con dos columnas de texto CSV a 2 decimales
    (ej. ``"0.00,0.50,1.00"``). Se recorta al tamaño mínimo común para evitar
    desalineaciones y se devuelve lista vacía ante cualquier valor no numérico (nunca
    lanza). Un vector constante en ``0.0`` es DATO LEGÍTIMO y se conserva tal cual:
    NO se descarta por varianza nula (regla de negocio de flatlines).
    """
    if timestamps_raw is None or valores_raw is None:
        return []
    try:
        ts = [float(x) for x in str(timestamps_raw).split(",") if x.strip() != ""]
        vals = [float(x) for x in str(valores_raw).split(",") if x.strip() != ""]
    except (TypeError, ValueError):
        return []
    n = min(len(ts), len(vals))
    return [(ts[i], vals[i]) for i in range(n)]


def _extraer_fila_telemetria(row: Any) -> Tuple[Any, Any, Any]:
    """Lee ``(nombre_grafico, timestamps, valores)`` con o sin ``row_factory=Row``."""
    try:
        return row["nombre_grafico"], row["timestamps"], row["valores"]
    except (TypeError, IndexError, KeyError):
        return row[0], row[1], row[2]


def _cargar_series(
    id_sesion: int, conn: sqlite3.Connection
) -> Dict[str, List[Tuple[float, float]]]:
    """Carga las 6 señales canónicas de la sesión, ORDENADAS por tiempo.

    Realiza UNA única consulta sobre ``Telemetria`` usando la FK real ``id_sesion_fk``
    (``SELECT nombre_grafico, timestamps, valores FROM Telemetria WHERE
    id_sesion_fk = ?``), deserializa los CSV paralelos a pares ``(t, v)`` y normaliza
    ``nombre_grafico`` a las señales canónicas vía :func:`core.graph_mapper.map_graphs`
    (también resuelve nombres legacy ``Graph_X_Y``).

    Una señal se considera DISPONIBLE si existen filas para ella, con independencia de
    sus valores: un flatline en ``0.0`` cuenta como señal presente y válida. Las
    señales ausentes quedan como lista vacía. Las series se ordenan por ``timestamp``
    antes de devolverlas porque algunos tiempos pueden ser negativos o no monótonos
    (artefactos de calibración) y las derivadas exigen orden temporal. Nunca lanza.
    """
    vacio: Dict[str, List[Tuple[float, float]]] = {s: [] for s in SENALES_CANONICAS}
    try:
        cur = conn.execute(
            "SELECT nombre_grafico, timestamps, valores "
            "FROM Telemetria WHERE id_sesion_fk = ?",
            (id_sesion,),
        )
        rows = cur.fetchall()
    except sqlite3.Error:
        return vacio

    raw: Dict[str, List[Tuple[float, float]]] = {}
    for row in rows or []:
        nombre, ts_raw, val_raw = _extraer_fila_telemetria(row)
        if not nombre:
            continue
        serie = _deserializar_csv(ts_raw, val_raw)
        if not serie:
            continue
        # Si la misma señal aparece en varias filas, conservar la serie más larga.
        clave = str(nombre)
        if clave not in raw or len(serie) > len(raw[clave]):
            raw[clave] = serie

    # Normalización a canónicos (idempotente para nombres ya canónicos).
    try:
        mapeado = map_graphs(raw)
    except Exception:  # pragma: no cover - defensa ante mapeo legacy corrupto
        mapeado = {k: v for k, v in raw.items() if k in SENALES_CANONICAS}

    # Garantizar las 6 claves y ORDENAR por tiempo antes de cualquier derivada.
    return {
        senal: sorted(mapeado.get(senal, []), key=lambda tv: tv[0])
        for senal in SENALES_CANONICAS
    }


def _serie_a_arrays(
    serie: List[Tuple[float, float]],
) -> Tuple[np.ndarray, np.ndarray]:
    """Convierte una serie ordenada en dos arrays numpy (tiempo, valores)."""
    if not serie:
        return np.array([]), np.array([])
    arr = np.asarray(serie, dtype=float)
    return arr[:, 0], arr[:, 1]


def _tasa_cambio(tiempo: np.ndarray, valores: np.ndarray) -> np.ndarray:
    """Derivada de ``valores`` respecto a ``tiempo`` (longitud ``n-1``), robusta a dt<=0.

    Los huecos de muestreo (dt > ``DT_MAX``) se tratan como tasa 0 para no atribuir
    variaciones bruscas a saltos de extracción.
    """
    if tiempo.size < 2 or valores.size < 2:
        return np.array([])
    dt = np.diff(tiempo)
    dv = np.diff(valores)
    with np.errstate(divide="ignore", invalid="ignore"):
        tasa = np.where((dt > 0) & (dt <= DT_MAX), dv / np.where(dt > 0, dt, 1.0), 0.0)
    return np.nan_to_num(tasa, nan=0.0, posinf=0.0, neginf=0.0)


def _contar_rachas(mascara: np.ndarray) -> int:
    """Cuenta EVENTOS (rachas) de ``True`` consecutivos en ``mascara``.

    Una racha de muestras contiguas marcadas cuenta como UN único evento, evitando
    inflar el contador por el muestreo fino de una misma maniobra.
    """
    if mascara.size == 0:
        return 0
    b = np.asarray(mascara, dtype=bool)
    previo = np.concatenate(([False], b[:-1]))
    return int(np.sum(b & ~previo))


def _contar_reversiones_por_amplitud(
    tiempo: np.ndarray,
    valores: np.ndarray,
    amplitud_min: float,
    tasa_umbral: Optional[float] = None,
) -> Tuple[int, float]:
    """Cuenta inversiones de dirección con amplitud relevante y su tiempo acumulado.

    Un movimiento se considera "brusco/real" si su amplitud (|Δvalor|) supera
    ``amplitud_min`` o, si se aporta ``tasa_umbral``, si su tasa supera dicho umbral
    (combina tasa + amplitud, C4). Solo se tienen en cuenta movimientos con un
    ``dt`` real (0 < dt <= ``DT_MAX``), de modo que los huecos no cuenten.

    Returns:
        ``(n_inversiones, segundos_acumulados)`` donde los segundos suman el ``dt``
        de los movimientos que provocan cada inversión (tiempo en régimen de
        oscilación / duda).
    """
    if valores.size < 3 or tiempo.size < 3:
        return 0, 0.0
    dv = np.diff(valores)
    dt = np.diff(tiempo)

    brusco = np.abs(dv) >= amplitud_min
    if tasa_umbral is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            tasa = np.where(dt > 0, dv / np.where(dt > 0, dt, 1.0), 0.0)
        tasa = np.nan_to_num(tasa, nan=0.0, posinf=0.0, neginf=0.0)
        brusco = brusco | (np.abs(tasa) > tasa_umbral)

    valido = brusco & (dt > 0) & (dt <= DT_MAX)
    if not np.any(valido):
        return 0, 0.0

    signos = np.sign(dv[valido])
    dts = dt[valido]
    if signos.size < 2:
        return 0, 0.0

    cambios = signos[1:] != signos[:-1]
    inversiones = int(np.sum(cambios))
    segundos = float(np.sum(dts[1:][cambios]))
    return inversiones, segundos


def _campo(metadata: Optional[Dict[str, Any]], nombre: str, default: Any) -> Any:
    """Acceso defensivo a una columna de ``Sesiones``.

    ``metadata`` es el ``dict`` devuelto por :func:`core.db_manager.get_session_row`.
    Devuelve ``default`` si la fila es ``None``, si la columna no existe (BD antigua
    sin ``puntaje_depurado``/etc.) o si el valor es ``NULL``. Nunca lanza.
    """
    if not metadata:
        return default
    valor = metadata.get(nombre)
    return default if valor is None else valor


# =============================================================================
# CÁLCULO DE MÉTRICAS DE VICIOS DE MANEJO
# =============================================================================
def _metricas_frenado(serie: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Frenadas bruscas como EVENTOS: subida vertical del pedal hacia el rango alto.

    Combina tasa (``> FRENADO_TASA_UMBRAL``) y amplitud (alcanzar ``>=
    FRENADO_VALOR_ALTO`` subiendo), agrupando muestras consecutivas en un único
    evento. ``picos_freno_alto`` mide la dureza sostenida del frenado.
    """
    t, v = _serie_a_arrays(serie)
    if t.size < 2:
        return {
            "frenadas_bruscas": 0,
            "picos_freno_alto": int(np.sum(v >= FRENADO_VALOR_ALTO)) if v.size else 0,
            "freno_max": float(np.max(v)) if v.size else 0.0,
        }
    tasa = _tasa_cambio(t, v)          # longitud n-1
    dv = np.diff(v)                     # longitud n-1
    v_destino = v[1:]                   # valor al que llega cada transición
    subida = (tasa > FRENADO_TASA_UMBRAL) | (
        (v_destino >= FRENADO_VALOR_ALTO) & (dv > 0)
    )
    frenadas = _contar_rachas(subida)
    return {
        "frenadas_bruscas": int(frenadas),
        "picos_freno_alto": int(np.sum(v >= FRENADO_VALOR_ALTO)),
        "freno_max": float(np.max(v)),
    }


def _metricas_direccion(serie: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Volantazos como EVENTOS de inversión de giro con amplitud relevante.

    Detecta oscilaciones bruscas de dirección (rango -14..+14) combinando tasa y
    amplitud mínima ``DIRECCION_AMPLITUD_MIN``.
    """
    t, v = _serie_a_arrays(serie)
    if t.size < 3:
        return {
            "volantazos": 0,
            "varianza_steering": float(np.var(v)) if v.size else 0.0,
            "steering_max_abs": float(np.max(np.abs(v))) if v.size else 0.0,
        }
    volantazos, _ = _contar_reversiones_por_amplitud(
        t, v, DIRECCION_AMPLITUD_MIN, tasa_umbral=DIRECCION_TASA_UMBRAL
    )
    return {
        "volantazos": int(volantazos),
        "varianza_steering": float(np.var(v)),
        "steering_max_abs": float(np.max(np.abs(v))),
    }


def _metricas_torre(
    serie_fork: List[Tuple[float, float]],
    serie_tilt: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """Inseguridad en torre: microajustes (inversiones) Y tiempo acumulado.

    Se ponderan por tiempo las inversiones de dirección en ``Fork Height In Mtrs`` y
    ``Tilt Angle In Deg``, descartando huecos de muestreo (``DT_MAX``).
    """
    tf, vf = _serie_a_arrays(serie_fork)
    tt, vt = _serie_a_arrays(serie_tilt)
    inv_fork, seg_fork = _contar_reversiones_por_amplitud(
        tf, vf, MICROAJUSTE_AMPLITUD_MIN
    )
    inv_tilt, seg_tilt = _contar_reversiones_por_amplitud(
        tt, vt, MICROAJUSTE_AMPLITUD_MIN
    )
    return {
        "inseguridad_torre_microajustes": int(inv_fork + inv_tilt),
        "inseguridad_torre_segundos": round(float(seg_fork + seg_tilt), 2),
        "microajustes_horquilla": int(inv_fork),
        "microajustes_inclinacion": int(inv_tilt),
    }


def _metricas_traslado_alto(
    serie_fork: List[Tuple[float, float]],
    serie_speed: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """Traslado con horquilla alta: segundos con velocidad>0 y horquilla insegura.

    NO se interpola a través de huecos. Para cada muestra de
    horquilla se busca la muestra de velocidad REAL más cercana y solo se usa si
    está dentro de ``DT_MAX``; si no, la velocidad se considera 0 (sin evidencia).
    El tiempo se integra con ``dt`` hacia la siguiente muestra, descartando saltos
    mayores que ``DT_MAX`` (los huecos no suman tiempo).
    """
    tf, vf = _serie_a_arrays(serie_fork)
    ts, vs = _serie_a_arrays(serie_speed)
    if tf.size < 2 or ts.size == 0:
        return {"traslado_horquilla_alta_segundos": 0.0, "altura_maxima_mtrs": 0.0}

    # dt hacia adelante; el último = 0. Los huecos > DT_MAX no suman tiempo.
    dt = np.diff(tf, append=tf[-1])
    dt = np.where(dt > DT_MAX, 0.0, np.clip(dt, 0.0, None))

    # Velocidad real más cercana (sin interpolar a través de huecos).
    idx = np.searchsorted(ts, tf)
    idx_hi = np.clip(idx, 0, ts.size - 1)
    idx_lo = np.clip(idx - 1, 0, ts.size - 1)
    d_hi = np.abs(ts[idx_hi] - tf)
    d_lo = np.abs(ts[idx_lo] - tf)
    usar_hi = d_hi <= d_lo
    dist = np.where(usar_hi, d_hi, d_lo)
    vel_cercana = np.where(usar_hi, vs[idx_hi], vs[idx_lo])
    velocidad = np.where(dist <= DT_MAX, vel_cercana, 0.0)

    condicion = (velocidad > VELOCIDAD_MOVIMIENTO_MIN) & (
        vf > FORK_SAFE_THRESHOLD_MTRS
    )
    segundos = float(np.sum(dt[condicion]))

    return {
        "traslado_horquilla_alta_segundos": round(segundos, 2),
        "altura_maxima_mtrs": float(np.max(vf)),
    }


# =============================================================================
# COBERTURA Y DICTAMEN DE ADMISIÓN
# =============================================================================
def _evaluar_cobertura(senales_disponibles: List[str]) -> Tuple[bool, List[str]]:
    """Indica si la cobertura de telemetría es suficiente para emitir un dictamen.

    Returns:
        ``(cobertura_ok, senales_criticas_faltantes)``. La cobertura NO es ok si
        faltan ``SENALES_CRITICAS`` o si hay menos de ``COBERTURA_MINIMA_SENALES``.
    """
    faltantes = [s for s in SENALES_CRITICAS if s not in senales_disponibles]
    cobertura_ok = (
        len(senales_disponibles) >= COBERTURA_MINIMA_SENALES and not faltantes
    )
    return cobertura_ok, faltantes


def _hallazgo(
    codigo: str,
    descripcion: str,
    severidad: int,
    valor: Any = None,
    umbral: Any = None,
) -> Dict[str, Any]:
    """Construye un hallazgo estructurado (vicio detectado) JSON-serializable."""
    return {
        "codigo": codigo,
        "descripcion": descripcion,
        "severidad": int(severidad),
        "valor": valor,
        "umbral": umbral,
    }


def _calcular_dictamen(
    metricas: Dict[str, Any],
) -> Tuple[str, str, str, List[Dict[str, Any]], int]:
    """Combina severidad de vicios y colisiones para decidir el dictamen.

    Devuelve ``(sugerencia_admision, justificacion, foco_instructor, hallazgos,
    severidad_total)``.

    Los ``hallazgos`` son la versión ESTRUCTURADA de los motivos del dictamen
    (código estable + descripción + severidad + valor/umbral): permiten que la UI,
    el generador de documentos y :mod:`core.ai_advisor` trabajen con datos en lugar
    de interpretar texto.

    Regla de seguridad: NUNCA retorna "Apto" si la cobertura de telemetría es
    inferior a ``COBERTURA_MINIMA_SENALES`` o faltan señales críticas; en ese caso
    devuelve "Observado" advirtiendo que los vicios cuentan 0 por ausencia de datos.
    """
    senales_disp = metricas.get("senales_disponibles", [])
    cobertura_ok, faltantes = _evaluar_cobertura(senales_disp)
    if not cobertura_ok:
        justificacion = (
            f"Telemetría insuficiente para certificar aptitud: solo hay "
            f"{len(senales_disp)}/{len(SENALES_CANONICAS)} señales disponibles"
            + (f" y faltan señales críticas ({', '.join(faltantes)})" if faltantes else "")
            + ". Los contadores de vicios valen 0 por AUSENCIA de datos, no por una "
            "conducción limpia; no es posible emitir un dictamen de aptitud fiable."
        )
        foco = "Reprocesar el PDF y verificar la extracción de telemetría antes de evaluar"
        hallazgos = [
            _hallazgo(
                HALLAZGO_COBERTURA_INSUFICIENTE,
                justificacion,
                severidad=0,
                valor=len(senales_disp),
                umbral=COBERTURA_MINIMA_SENALES,
            )
        ]
        return DICTAMEN_OBSERVADO, justificacion, foco, hallazgos, 0

    frenadas = metricas.get("frenadas_bruscas", 0)
    volantazos = metricas.get("volantazos", 0)
    colisiones = metricas.get("colisiones_netas", 0)
    microajustes = metricas.get("inseguridad_torre_microajustes", 0)
    torre_segundos = metricas.get("inseguridad_torre_segundos", 0.0)
    traslado_alto = metricas.get("traslado_horquilla_alta_segundos", 0.0)
    varianza = metricas.get("varianza_steering", 0.0)
    picos_freno = metricas.get("picos_freno_alto", 0)

    hallazgos: List[Dict[str, Any]] = []

    if colisiones >= COLISIONES_CRITICO:
        hallazgos.append(_hallazgo(
            HALLAZGO_COLISIONES_CRITICAS, f"{colisiones} colisiones registradas", 3,
            valor=colisiones, umbral=COLISIONES_CRITICO,
        ))
    elif colisiones >= COLISIONES_OBSERVADO:
        hallazgos.append(_hallazgo(
            HALLAZGO_COLISIONES, f"{colisiones} colisión(es) registrada(s)", 1,
            valor=colisiones, umbral=COLISIONES_OBSERVADO,
        ))

    if frenadas >= FRENADAS_EVENTOS_ALTO:
        hallazgos.append(_hallazgo(
            HALLAZGO_FRENADAS_ALTO, f"{frenadas} frenadas bruscas", 2,
            valor=frenadas, umbral=FRENADAS_EVENTOS_ALTO,
        ))
    elif frenadas >= FRENADAS_EVENTOS_MODERADO:
        hallazgos.append(_hallazgo(
            HALLAZGO_FRENADAS_MODERADO, f"{frenadas} frenadas bruscas", 1,
            valor=frenadas, umbral=FRENADAS_EVENTOS_MODERADO,
        ))

    if picos_freno >= PICOS_FRENO_ALTO_UMBRAL:
        hallazgos.append(_hallazgo(
            HALLAZGO_FRENO_SOSTENIDO,
            f"frenado sostenido/duro ({picos_freno} muestras en rango alto)", 1,
            valor=picos_freno, umbral=PICOS_FRENO_ALTO_UMBRAL,
        ))

    if volantazos >= VOLANTAZOS_EVENTOS_ALTO or varianza >= DIRECCION_VARIANZA_ALTA:
        hallazgos.append(_hallazgo(
            HALLAZGO_VOLANTAZOS_ALTO,
            f"{volantazos} volantazos (varianza {varianza:.1f})", 2,
            valor=volantazos, umbral=VOLANTAZOS_EVENTOS_ALTO,
        ))
    elif volantazos >= VOLANTAZOS_EVENTOS_MODERADO:
        hallazgos.append(_hallazgo(
            HALLAZGO_VOLANTAZOS_MODERADO, f"{volantazos} volantazos", 1,
            valor=volantazos, umbral=VOLANTAZOS_EVENTOS_MODERADO,
        ))

    if traslado_alto >= TRASLADO_ALTO_SEGUNDOS_ALTO:
        hallazgos.append(_hallazgo(
            HALLAZGO_TRASLADO_ALTO_SEVERO,
            f"{traslado_alto:.0f}s trasladando con horquilla por encima de "
            f"{FORK_SAFE_THRESHOLD_MTRS:.2f} m", 2,
            valor=traslado_alto, umbral=TRASLADO_ALTO_SEGUNDOS_ALTO,
        ))
    elif traslado_alto > 0:
        hallazgos.append(_hallazgo(
            HALLAZGO_TRASLADO_ALTO, f"{traslado_alto:.0f}s de traslado con horquilla alta", 1,
            valor=traslado_alto, umbral=0.0,
        ))

    if (
        microajustes >= MICROAJUSTE_INVERSIONES_ALTO
        or torre_segundos >= MICROAJUSTE_SEGUNDOS_ALTO
    ):
        hallazgos.append(_hallazgo(
            HALLAZGO_INSEGURIDAD_TORRE,
            f"inseguridad en torre ({microajustes} microajustes, "
            f"{torre_segundos:.0f}s en régimen de duda)", 1,
            valor=microajustes, umbral=MICROAJUSTE_INVERSIONES_ALTO,
        ))

    severidad = sum(h["severidad"] for h in hallazgos)
    motivos = [h["descripcion"] for h in hallazgos]

    if severidad >= SEVERIDAD_NO_APTO or colisiones >= COLISIONES_CRITICO:
        sugerencia = DICTAMEN_NO_APTO_CRITICO
        foco = "Priorizar control de inercia, anticipación de frenada y gestión del espacio"
        cabecera = (
            "Se detectan vicios severos de manejo que comprometen la seguridad operativa."
            if not motivos
            else "Dictamen crítico por: " + "; ".join(motivos) + "."
        )
        justificacion = (
            cabecera
            + " El operador presenta vicios severos y/o colisiones elevadas que "
            "comprometen la seguridad; requiere corrección intensiva antes de operar."
        )
    elif severidad >= SEVERIDAD_OBSERVADO:
        sugerencia = DICTAMEN_OBSERVADO
        foco = "Trabajar radio de giro trasero, suavidad de mandos y control de la torre"
        justificacion = (
            ("Vicios moderados detectados: " + "; ".join(motivos) + ". ")
            if motivos
            else "Vicios moderados detectados. "
        ) + (
            "El operador es admisible con seguimiento: debe corregir estos hábitos "
            "durante la formación."
        )
    else:
        sugerencia = DICTAMEN_APTO
        foco = "Buen control base: afianzar hábitos normativos y productividad"
        justificacion = (
            ("Buen control base de la máquina. Observaciones menores: "
             + "; ".join(motivos) + ". Admisible: afianzar hábitos normativos.")
            if motivos
            else ("Buen control base de la máquina: sin colisiones relevantes ni vicios "
                  "críticos de manejo detectados en la telemetría. Admisible.")
        )

    return sugerencia, justificacion, foco, hallazgos, severidad


def nivel_riesgo_por_dictamen(sugerencia: str) -> str:
    """Traduce el dictamen de admisión a un nivel de riesgo operativo.

    Mapping único del proyecto (lo consumen la UI, los documentos y
    :mod:`core.ai_advisor`) para no reimplementar la regla de severidad.
    """
    if sugerencia == DICTAMEN_NO_APTO_CRITICO:
        return RIESGO_ALTO
    if sugerencia == DICTAMEN_APTO:
        return RIESGO_BAJO
    return RIESGO_MEDIO


# =============================================================================
# API PÚBLICA — A) DIAGNÓSTICO INICIAL
# =============================================================================
def evaluar_diagnostico_inicial(
    session_id: int, conn: sqlite3.Connection
) -> Dict[str, Any]:
    """Evalúa una sesión de admisión (Día 1) y emite un dictamen pedagógico.

    Args:
        session_id: identificador de la sesión en ``Sesiones``.
        conn: conexión SQLite abierta (idealmente con ``row_factory=sqlite3.Row``).

    Returns:
        Dict con claves ``sugerencia_admision``, ``justificacion``,
        ``foco_instructor``, ``nivel_riesgo``, ``hallazgos`` (lista estructurada de
        vicios detectados), ``severidad_total``, ``metricas_calculadas`` y
        ``series``. Tolera sesiones inexistentes y señales ausentes devolviendo
        valores por defecto coherentes.
    """
    metadata = get_session_row(conn, session_id)
    series = _cargar_series(session_id, conn)

    m_frenado = _metricas_frenado(series.get(SENAL_BRAKE, []))
    m_direccion = _metricas_direccion(series.get(SENAL_STEERING, []))
    m_torre = _metricas_torre(
        series.get(SENAL_FORK_HEIGHT, []), series.get(SENAL_TILT, [])
    )
    m_traslado = _metricas_traslado_alto(
        series.get(SENAL_FORK_HEIGHT, []), series.get(SENAL_SPEED, [])
    )

    colisiones = sumar_eventos_por_tipo(conn, session_id, TIPO_EVENTO_COLISION)

    # --- Tiempo total (acceso defensivo a columnas) ---
    tiempo_total = float(_campo(metadata, "duracion_segundos", 0.0) or 0.0)
    if tiempo_total <= 0.0:
        finales = [s[-1][0] for s in series.values() if s]
        tiempo_total = float(max(finales)) if finales else 0.0

    puntaje_final = float(_campo(metadata, "puntaje_final", 0.0) or 0.0)
    perfil_operador = str(_campo(metadata, "perfil_operador", "N/A"))

    metricas: Dict[str, Any] = {
        "frenadas_bruscas": int(m_frenado["frenadas_bruscas"]),
        "volantazos": int(m_direccion["volantazos"]),
        "colisiones_netas": int(colisiones),
        "tiempo_total_segundos": round(float(tiempo_total), 2),
        "inseguridad_torre_microajustes": int(
            m_torre["inseguridad_torre_microajustes"]
        ),
        "inseguridad_torre_segundos": float(m_torre["inseguridad_torre_segundos"]),
        "traslado_horquilla_alta_segundos": float(
            m_traslado["traslado_horquilla_alta_segundos"]
        ),
        "varianza_steering": round(float(m_direccion["varianza_steering"]), 3),
        # --- Métricas adicionales útiles ---
        "picos_freno_alto": int(m_frenado["picos_freno_alto"]),
        "freno_max": round(float(m_frenado["freno_max"]), 2),
        "steering_max_abs": round(float(m_direccion["steering_max_abs"]), 2),
        "microajustes_horquilla": int(m_torre["microajustes_horquilla"]),
        "microajustes_inclinacion": int(m_torre["microajustes_inclinacion"]),
        "altura_maxima_mtrs": round(float(m_traslado["altura_maxima_mtrs"]), 3),
        "puntaje_final": puntaje_final,
        "perfil_operador": perfil_operador,
        "sesion_existente": metadata is not None,
        "senales_disponibles": [s for s in SENALES_CANONICAS if series.get(s)],
    }

    # --- Dictamen con control de cobertura ---
    if metadata is None:
        sugerencia = DICTAMEN_OBSERVADO
        justificacion = (
            f"La sesión {session_id} no existe en la base de datos o no tiene "
            "metadatos; no es posible emitir un dictamen definitivo. Se marca como "
            "'Observado' por falta de información."
        )
        foco = "Verificar la carga del reporte PDF antes de evaluar"
        hallazgos = [_hallazgo(HALLAZGO_SESION_INEXISTENTE, justificacion, 0, valor=session_id)]
        severidad_total = 0
    else:
        # _calcular_dictamen ya degrada a "Observado" si la cobertura es insuficiente.
        sugerencia, justificacion, foco, hallazgos, severidad_total = _calcular_dictamen(metricas)

    return {
        "sugerencia_admision": sugerencia,
        "justificacion": justificacion,
        "foco_instructor": foco,
        "nivel_riesgo": nivel_riesgo_por_dictamen(sugerencia),
        "hallazgos": hallazgos,
        "severidad_total": int(severidad_total),
        "metricas_calculadas": metricas,
        "series": series,
    }


# =============================================================================
# API PÚBLICA — B) COMPARATIVA DELTA (PRE / POST)
# =============================================================================
def _puntaje_depurado_derivado(puntaje_final: float, penalizaciones: float) -> float:
    """Puntaje depurado DERIVADO (la columna real no existe en la BD).

    Fórmula documentada::

        puntaje_depurado = puntaje_final + Σ penalizaciones

    donde ``Σ penalizaciones`` (de ``ResumenEventos``) ya se almacena en NEGATIVO,
    por lo que el resultado reduce el puntaje final en la magnitud de las
    penalizaciones. Es un proxy estricto del rendimiento real del operador.
    """
    return float(puntaje_final) + float(penalizaciones)


def _delta_pct(nuevo: float, anterior: float) -> Optional[float]:
    """Variación porcentual de ``anterior`` a ``nuevo``.

    Reglas:
      * ``anterior == 0`` y ``nuevo == 0`` -> ``0.0`` (sin cambio).
      * ``anterior == 0`` y ``nuevo != 0`` -> ``None`` (porcentaje no definido; la UI
        mostrará "n/a" en vez de un +100% falso o con signo invertido).
      * ``anterior != 0`` -> porcentaje real ``(nuevo-anterior)/|anterior|*100``.
    """
    if anterior == 0 or anterior == 0.0:
        return 0.0 if (nuevo == 0 or nuevo == 0.0) else None
    return round(((nuevo - anterior) / abs(anterior)) * 100.0, 2)


def veredicto_por_delta_puntaje(delta: float) -> str:
    """Clasifica la evolución a partir del delta de puntaje depurado.

    Regla única del proyecto (la reutiliza ``core.report_generator``)::

        delta > UMBRAL_MEJORA_SIGNIFICATIVA  -> MEJORA SIGNIFICATIVA
        delta > UMBRAL_MEJORA_MODERADA       -> MEJORA MODERADA
        delta < UMBRAL_REGRESION             -> REGRESIÓN DETECTADA
        resto                                -> RENDIMIENTO ESTANCADO
    """
    if delta > UMBRAL_MEJORA_SIGNIFICATIVA:
        return VEREDICTO_MEJORA_SIGNIFICATIVA
    if delta > UMBRAL_MEJORA_MODERADA:
        return VEREDICTO_MEJORA_MODERADA
    if delta < UMBRAL_REGRESION:
        return VEREDICTO_REGRESION
    return VEREDICTO_ESTANCADO


def _deltas_cero() -> Dict[str, Any]:
    """Estructura de deltas neutra (todo 0 / None) para respuestas de error."""
    return {
        "duracion_seg": 0.0,
        "duracion_pct": None,
        "colisiones": 0,
        "colisiones_pct": None,
        "frenadas_bruscas": 0,
        "frenadas_bruscas_pct": None,
        "volantazos": 0,
        "volantazos_pct": None,
        "suavidad": 0,
        "suavidad_pct": None,
        "puntaje_depurado": 0.0,
        "puntaje_depurado_pct": None,
    }


def comparar_sesiones_delta(
    session_id_pre: int, session_id_post: int, conn: sqlite3.Connection
) -> Dict[str, Any]:
    """Compara una sesión diagnóstica (Pre) con una final (Post).

    Si alguna de las dos sesiones NO existe en la BD, devuelve un dict con
    ``error`` y ``veredicto_evolucion="SIN DATOS"`` y deltas neutros, en lugar de
    fabricar una mejora falsa.

    Returns:
        Dict con claves ``pre``, ``post``, ``deltas``, ``series_pre``,
        ``series_post``, ``veredicto_evolucion``, ``mejoras`` y ``retrocesos``
        (y ``error`` solo cuando aplica). Todas las claves son JSON-serializables.
    """
    diag_pre = evaluar_diagnostico_inicial(session_id_pre, conn)
    diag_post = evaluar_diagnostico_inicial(session_id_post, conn)

    existe_pre = bool(diag_pre["metricas_calculadas"].get("sesion_existente"))
    existe_post = bool(diag_post["metricas_calculadas"].get("sesion_existente"))

    # --- Validación de existencia ---
    if not existe_pre or not existe_post:
        faltan = []
        if not existe_pre:
            faltan.append(str(session_id_pre))
        if not existe_post:
            faltan.append(str(session_id_post))
        return {
            "error": "Alguna de las sesiones no existe en la base de datos",
            "sesiones_faltantes": faltan,
            "pre": {
                "session_id": session_id_pre,
                "metricas": diag_pre["metricas_calculadas"],
                "puntaje_depurado": 0.0,
                "perfil": "N/A",
                "duracion_segundos": 0.0,
            },
            "post": {
                "session_id": session_id_post,
                "metricas": diag_post["metricas_calculadas"],
                "puntaje_depurado": 0.0,
                "perfil": "N/A",
                "duracion_segundos": 0.0,
            },
            "deltas": _deltas_cero(),
            "series_pre": diag_pre["series"],
            "series_post": diag_post["series"],
            "veredicto_evolucion": VEREDICTO_SIN_DATOS,
            "mejoras": [],
            "retrocesos": [],
        }

    pen_pre = sumar_penalizaciones(conn, session_id_pre)
    pen_post = sumar_penalizaciones(conn, session_id_post)

    pd_pre = _puntaje_depurado_derivado(
        diag_pre["metricas_calculadas"].get("puntaje_final", 0.0), pen_pre
    )
    pd_post = _puntaje_depurado_derivado(
        diag_post["metricas_calculadas"].get("puntaje_final", 0.0), pen_post
    )

    dur_pre = float(diag_pre["metricas_calculadas"].get("tiempo_total_segundos", 0.0))
    dur_post = float(diag_post["metricas_calculadas"].get("tiempo_total_segundos", 0.0))

    col_pre = int(diag_pre["metricas_calculadas"].get("colisiones_netas", 0))
    col_post = int(diag_post["metricas_calculadas"].get("colisiones_netas", 0))

    fren_pre = int(diag_pre["metricas_calculadas"].get("frenadas_bruscas", 0))
    fren_post = int(diag_post["metricas_calculadas"].get("frenadas_bruscas", 0))

    vol_pre = int(diag_pre["metricas_calculadas"].get("volantazos", 0))
    vol_post = int(diag_post["metricas_calculadas"].get("volantazos", 0))

    # Suavidad calculada SOBRE TOTALES: no se suman porcentajes independientes.
    sua_pre = fren_pre + vol_pre
    sua_post = fren_post + vol_post

    bloque_pre = {
        "session_id": session_id_pre,
        "metricas": diag_pre["metricas_calculadas"],
        "puntaje_depurado": round(pd_pre, 2),
        "perfil": diag_pre["metricas_calculadas"].get("perfil_operador", "N/A"),
        "duracion_segundos": round(dur_pre, 2),
    }
    bloque_post = {
        "session_id": session_id_post,
        "metricas": diag_post["metricas_calculadas"],
        "puntaje_depurado": round(pd_post, 2),
        "perfil": diag_post["metricas_calculadas"].get("perfil_operador", "N/A"),
        "duracion_segundos": round(dur_post, 2),
    }

    delta_puntaje = round(pd_post - pd_pre, 2)
    deltas = {
        "duracion_seg": round(dur_post - dur_pre, 2),
        "duracion_pct": _delta_pct(dur_post, dur_pre),
        "colisiones": int(col_post - col_pre),
        "colisiones_pct": _delta_pct(col_post, col_pre),
        "frenadas_bruscas": int(fren_post - fren_pre),
        "frenadas_bruscas_pct": _delta_pct(fren_post, fren_pre),
        "volantazos": int(vol_post - vol_pre),
        "volantazos_pct": _delta_pct(vol_post, vol_pre),
        "suavidad": int(sua_post - sua_pre),
        "suavidad_pct": _delta_pct(sua_post, sua_pre),
        "puntaje_depurado": delta_puntaje,
        "puntaje_depurado_pct": _delta_pct(pd_post, pd_pre),
    }

    veredicto = veredicto_por_delta_puntaje(delta_puntaje)
    mejoras, retrocesos = _clasificar_cambios(bloque_pre, bloque_post, deltas)

    return {
        "pre": bloque_pre,
        "post": bloque_post,
        "deltas": deltas,
        "series_pre": diag_pre["series"],
        "series_post": diag_post["series"],
        "veredicto_evolucion": veredicto,
        "mejoras": mejoras,
        "retrocesos": retrocesos,
    }


def _clasificar_cambios(
    pre: Dict[str, Any],
    post: Dict[str, Any],
    deltas: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Clasifica cada métrica comparada en mejora o retroceso.

    Devuelve estructuras NATIVAS (listas de dicts JSON-serializables) en lugar de
    texto preformateado: la presentación (tabla, PDF, tarjeta de UI) la decide cada
    consumidor. Cada elemento contiene ``metrica``, ``pre``, ``post``, ``delta``,
    ``pct`` y ``sentido``. Las métricas con delta 0 no se listan (rendimiento
    estable en ese aspecto).
    """
    m_pre = pre.get("metricas", {}) or {}
    m_post = post.get("metricas", {}) or {}

    suavidad_pre = int(m_pre.get("frenadas_bruscas", 0)) + int(m_pre.get("volantazos", 0))
    suavidad_post = int(m_post.get("frenadas_bruscas", 0)) + int(m_post.get("volantazos", 0))

    comparables: List[Tuple[str, Any, Any, Any, Optional[float], str]] = [
        (
            "Puntaje depurado",
            float(pre.get("puntaje_depurado", 0.0)),
            float(post.get("puntaje_depurado", 0.0)),
            deltas.get("puntaje_depurado", 0.0),
            deltas.get("puntaje_depurado_pct"),
            MAYOR_ES_MEJOR,
        ),
        (
            "Colisiones",
            int(m_pre.get("colisiones_netas", 0)),
            int(m_post.get("colisiones_netas", 0)),
            deltas.get("colisiones", 0),
            deltas.get("colisiones_pct"),
            MENOR_ES_MEJOR,
        ),
        (
            "Frenadas bruscas",
            int(m_pre.get("frenadas_bruscas", 0)),
            int(m_post.get("frenadas_bruscas", 0)),
            deltas.get("frenadas_bruscas", 0),
            deltas.get("frenadas_bruscas_pct"),
            MENOR_ES_MEJOR,
        ),
        (
            "Volantazos",
            int(m_pre.get("volantazos", 0)),
            int(m_post.get("volantazos", 0)),
            deltas.get("volantazos", 0),
            deltas.get("volantazos_pct"),
            MENOR_ES_MEJOR,
        ),
        (
            "Suavidad (frenadas + volantazos)",
            suavidad_pre,
            suavidad_post,
            deltas.get("suavidad", 0),
            deltas.get("suavidad_pct"),
            MENOR_ES_MEJOR,
        ),
        (
            "Duración (s)",
            float(pre.get("duracion_segundos", 0.0)),
            float(post.get("duracion_segundos", 0.0)),
            deltas.get("duracion_seg", 0.0),
            deltas.get("duracion_pct"),
            MENOR_ES_MEJOR,
        ),
    ]

    mejoras: List[Dict[str, Any]] = []
    retrocesos: List[Dict[str, Any]] = []
    for etiqueta, valor_pre, valor_post, delta, pct, sentido in comparables:
        if not delta:
            continue
        mejoro = delta > 0 if sentido == MAYOR_ES_MEJOR else delta < 0
        item = {
            "metrica": etiqueta,
            "pre": valor_pre,
            "post": valor_post,
            "delta": delta,
            "pct": pct,
            "sentido": sentido,
        }
        (mejoras if mejoro else retrocesos).append(item)
    return mejoras, retrocesos


# =============================================================================
# API PÚBLICA — C) PERSISTENCIA DE LA DECISIÓN DEL INSTRUCTOR
# =============================================================================
# Definición con UNIQUE(id_sesion) y ON DELETE CASCADE, coherente con
# ResumenEventos/Telemetria. Permite un upsert limpio de 1 fila por sesión.
_SQL_CREAR_TABLA_DECISION = """
CREATE TABLE IF NOT EXISTS DecisionInstructor (
    id_decision INTEGER PRIMARY KEY AUTOINCREMENT,
    id_sesion INTEGER NOT NULL UNIQUE,
    veredicto TEXT NOT NULL,
    foco_sugerido TEXT,
    notas TEXT,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(id_sesion) REFERENCES Sesiones(id_sesion) ON DELETE CASCADE
)
"""


def asegurar_tabla_decision_instructor(conn: sqlite3.Connection) -> bool:
    """Crea la tabla ``DecisionInstructor`` si no existe (NUNCA hace DROP).

    Única ruta legítima de DDL; se invoca SOLO desde funciones de escritura.

    Returns:
        ``True`` si la sentencia se ejecutó sin errores, ``False`` en caso contrario.
    """
    try:
        conn.execute(_SQL_CREAR_TABLA_DECISION)
        conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.error("No se pudo crear la tabla DecisionInstructor: %s", exc)
        return False


def guardar_decision_instructor(
    session_id: int,
    veredicto: str,
    notas: str,
    conn: sqlite3.Connection,
    foco_sugerido: Optional[str] = None,
) -> bool:
    """Persiste la decisión del instructor para una sesión (UPSERT no destructivo).

    Estrategia: UPDATE de la fila existente de la sesión; si no existía
    (``rowcount == 0``) se INSERTa. Mantiene exactamente 1 fila por sesión SIN
    borrar destructivamente con DELETE previo.

    Returns:
        ``True`` si se guardó correctamente, ``False`` en caso de error.
    """
    if not asegurar_tabla_decision_instructor(conn):
        return False
    try:
        cur = conn.execute(
            "UPDATE DecisionInstructor "
            "SET veredicto = ?, foco_sugerido = ?, notas = ?, fecha = CURRENT_TIMESTAMP "
            "WHERE id_sesion = ?",
            (veredicto, foco_sugerido, notas, session_id),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO DecisionInstructor (id_sesion, veredicto, foco_sugerido, notas) "
                "VALUES (?, ?, ?, ?)",
                (session_id, veredicto, foco_sugerido, notas),
            )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.error("No se pudo guardar la decisión de la sesión %s: %s", session_id, exc)
        conn.rollback()
        return False


def obtener_decision_instructor(
    session_id: int, conn: sqlite3.Connection
) -> Optional[Dict[str, Any]]:
    """Devuelve la decisión guardada del instructor para la sesión, o ``None``.

    Operación de SOLO LECTURA: no crea la tabla ni hace commit. Si la tabla no
    existe todavía o hay cualquier error de BD, devuelve ``None`` sin efectos
    secundarios. Funciona con y sin ``row_factory=sqlite3.Row``.
    """
    try:
        cur = conn.execute(
            "SELECT id_decision, id_sesion, veredicto, foco_sugerido, notas, fecha "
            "FROM DecisionInstructor WHERE id_sesion = ? "
            "ORDER BY id_decision DESC LIMIT 1",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        columnas = [d[0] for d in cur.description]
        return dict(zip(columnas, tuple(row)))
    except sqlite3.Error:
        # Tabla inexistente u otro error: lectura degradada, sin escribir nada.
        return None


# =============================================================================
# API PÚBLICA — D) DATOS DE NEGOCIO PARA DOCUMENTOS (PDF / TEXTO)
# =============================================================================
def obtener_datos_documento_sesion(
    session_id: int, conn: sqlite3.Connection
) -> Dict[str, Any]:
    """Datos saneados de una sesión para generar documentos (PDF/texto).

    Concentra el saneado que antes estaba disperso en los frontends: garantiza
    numéricos en ``0``/``0.0`` y textos en ``"N/A"`` para que el generador de
    documentos nunca falle por ``None``, e incorpora las magnitudes derivadas de
    negocio (``penalizaciones_totales`` y ``puntaje_depurado``).

    Returns:
        Dict plano JSON-serializable. Si la sesión no existe, devuelve la
        estructura con valores neutros y ``existe=False``.
    """
    metadata = get_session_row(conn, session_id)
    penalizaciones = sumar_penalizaciones(conn, session_id)
    puntaje_final = _a_float(_campo(metadata, "puntaje_final", 0.0))
    return {
        "id_sesion": int(session_id),
        "existe": metadata is not None,
        "nombre_operador": _a_str(_campo(metadata, "nombre_operador", "N/A")),
        "nombre_clase": _a_str(_campo(metadata, "nombre_clase", "N/A")),
        "nombre_ejercicio": _a_str(_campo(metadata, "nombre_ejercicio", "N/A")),
        "nombre_archivo_origen": _a_str(_campo(metadata, "nombre_archivo_origen", "N/A")),
        "fecha_hora_inicio": _a_str(_campo(metadata, "fecha_hora_inicio", "N/A")),
        "perfil_operador": _a_str(_campo(metadata, "perfil_operador", "N/A")),
        "duracion_segundos": _a_float(_campo(metadata, "duracion_segundos", 0.0)),
        "puntaje_final": puntaje_final,
        "penalizaciones_totales": penalizaciones,
        "puntaje_depurado": round(_puntaje_depurado_derivado(puntaje_final, penalizaciones), 2),
    }


def obtener_datos_evolucion(
    session_id_pre: int, session_id_post: int, conn: sqlite3.Connection
) -> Dict[str, Any]:
    """Paquete completo para el reporte de evolución (datos + comparativa).

    Returns:
        Dict con ``datos_iniciales``, ``datos_finales`` (ambos listos para
        :func:`core.report_generator.crear_reporte_evolucion_pdf`) y
        ``comparativa`` (salida de :func:`comparar_sesiones_delta`).
    """
    return {
        "datos_iniciales": obtener_datos_documento_sesion(session_id_pre, conn),
        "datos_finales": obtener_datos_documento_sesion(session_id_post, conn),
        "comparativa": comparar_sesiones_delta(session_id_pre, session_id_post, conn),
    }


# =============================================================================
# API PÚBLICA — E) PAYLOAD JSON-SERIALIZABLE PARA LLM EXTERNA
# =============================================================================
def _a_float(valor: Any, default: float = 0.0) -> float:
    """Convierte ``valor`` a ``float`` nativo de Python de forma defensiva.

    Acepta numpy scalars, ``None`` y cualquier tipo no numérico: devuelve
    ``default`` cuando la conversión no es posible, de modo que el resultado sea
    SIEMPRE un ``float`` nativo serializable a JSON (nunca ``None`` ni numpy).
    """
    if valor is None:
        return float(default)
    try:
        resultado = float(valor)
    except (TypeError, ValueError):
        return float(default)
    # float() de un numpy.floatxx ya devuelve un float nativo de Python.
    return resultado


def _a_int(valor: Any, default: int = 0) -> int:
    """Convierte ``valor`` a ``int`` nativo de Python de forma defensiva (nunca None)."""
    if valor is None:
        return int(default)
    try:
        return int(valor)
    except (TypeError, ValueError):
        return int(default)


def _a_str(valor: Any, default: str = "N/A") -> str:
    """Convierte ``valor`` a ``str`` nativo; usa ``default`` si es ``None``/vacío."""
    if valor is None:
        return default
    texto = str(valor).strip()
    return texto if texto else default


def obtener_payload_para_llm(
    session_id_pre: int,
    conn: sqlite3.Connection,
    session_id_post: Optional[int] = None,
) -> Dict[str, Any]:
    """Construye un payload 100 % serializable a JSON para :mod:`core.ai_advisor`.

    Agrega, en un único dict plano y sin tipos exóticos (nada de numpy, ``datetime``
    sin convertir, ``set`` o ``None``), la información de admisión de la sesión
    diagnóstica (Pre) y, si se aporta ``session_id_post``, la de la sesión final
    (Post) junto con su comparativa de evolución.

    La función es de SOLO LECTURA: reutiliza :func:`evaluar_diagnostico_inicial` y
    :func:`comparar_sesiones_delta`.

    Tolerancia a fallos:
      * Si ``session_id_post`` es ``None`` se devuelve únicamente el diagnóstico de
        admisión (sin bloque ``evolucion``).
      * Si ``session_id_post`` no existe, los campos de Post quedan en ``0`` /
        ``"N/A"`` y el veredicto de evolución en ``"SIN DATOS"`` (nunca se fabrica
        una mejora falsa).
      * Cualquier métrica ausente se degrada a ``0`` / ``0.0`` / ``"N/A"``, nunca a
        ``None`` que rompería el contrato JSON.

    Args:
        session_id_pre: identificador de la sesión diagnóstica (Día 1).
        conn: conexión SQLite abierta (idealmente con ``row_factory=sqlite3.Row``).
        session_id_post: identificador de la sesión final (Día Final), opcional.

    Returns:
        Dict con las claves ``metadatos``, ``fisicas_clave``, ``metricas``,
        ``diagnostico`` y (opcionalmente) ``evolucion``, listo para ``json.dumps``.
    """
    # --- Diagnóstico de la sesión Pre (tolerante a inexistente) ---
    diag_pre = evaluar_diagnostico_inicial(session_id_pre, conn)
    metricas_pre = diag_pre.get("metricas_calculadas", {}) or {}
    existe_pre = bool(metricas_pre.get("sesion_existente"))

    con_comparativa = session_id_post is not None
    if con_comparativa:
        diag_post = evaluar_diagnostico_inicial(session_id_post, conn)
        metricas_post = diag_post.get("metricas_calculadas", {}) or {}
        existe_post = bool(metricas_post.get("sesion_existente"))
        # Nunca lanza: degrada a "SIN DATOS" si alguna sesión no existe.
        delta = comparar_sesiones_delta(session_id_pre, session_id_post, conn)
    else:
        diag_post, metricas_post = {}, {}
        existe_post = False
        delta = {}
    deltas = delta.get("deltas", {}) or {}

    # --- Metadatos de la sesión (operador / ejercicio) desde ``Sesiones`` ---
    meta_pre = get_session_row(conn, session_id_pre)
    meta_post = get_session_row(conn, session_id_post) if con_comparativa else None
    # El operador/ejercicio se toman de Pre; si Pre no existe, se intenta con Post.
    meta_fuente = meta_pre if meta_pre is not None else meta_post
    operador = _a_str(_campo(meta_fuente, "nombre_operador", "N/A"))
    ejercicio = _a_str(_campo(meta_fuente, "nombre_ejercicio", "N/A"))

    metadatos: Dict[str, Any] = {
        "operador": operador,
        "ejercicio": ejercicio,
        "session_id_pre": int(session_id_pre),
        "session_id_post": int(session_id_post) if con_comparativa else None,
        "duracion_pre_segundos": round(
            _a_float(metricas_pre.get("tiempo_total_segundos", 0.0)), 2
        ),
        "duracion_post_segundos": round(
            _a_float(metricas_post.get("tiempo_total_segundos", 0.0)), 2
        ),
        "delta_puntaje": round(_a_float(deltas.get("puntaje_depurado", 0.0)), 2),
        "delta_puntaje_pct": round(
            _a_float(deltas.get("puntaje_depurado_pct"), default=0.0), 2
        ),
        "perfil_pre": _a_str(metricas_pre.get("perfil_operador", "N/A")),
        "perfil_post": _a_str(metricas_post.get("perfil_operador", "N/A")),
    }

    fisicas_clave: Dict[str, Any] = {
        "frenadas_bruscas_pre": _a_int(metricas_pre.get("frenadas_bruscas", 0)),
        "frenadas_bruscas_post": _a_int(metricas_post.get("frenadas_bruscas", 0)),
        "volantazos_pre": _a_int(metricas_pre.get("volantazos", 0)),
        "volantazos_post": _a_int(metricas_post.get("volantazos", 0)),
        "colisiones_netas_pre": _a_int(metricas_pre.get("colisiones_netas", 0)),
        "colisiones_netas_post": _a_int(metricas_post.get("colisiones_netas", 0)),
        "inseguridad_torre_microajustes_pre": _a_int(
            metricas_pre.get("inseguridad_torre_microajustes", 0)
        ),
        "inseguridad_torre_microajustes_post": _a_int(
            metricas_post.get("inseguridad_torre_microajustes", 0)
        ),
        "tiempo_horquilla_alta_pre_seg": round(
            _a_float(metricas_pre.get("traslado_horquilla_alta_segundos", 0.0)), 2
        ),
        "tiempo_horquilla_alta_post_seg": round(
            _a_float(metricas_post.get("traslado_horquilla_alta_segundos", 0.0)), 2
        ),
    }

    diagnostico: Dict[str, Any] = {
        "sugerencia_admision_pre": _a_str(diag_pre.get("sugerencia_admision", "N/A")),
        "justificacion_pre": _a_str(diag_pre.get("justificacion", "N/A")),
        "nivel_riesgo_pre": _a_str(diag_pre.get("nivel_riesgo", "N/A")),
        "hallazgos_pre": list(diag_pre.get("hallazgos", []) or []),
        "foco_instructor": _a_str(
            diag_pre.get("foco_instructor") or diag_post.get("foco_instructor"), "N/A"
        ),
        "senales_disponibles_pre": list(metricas_pre.get("senales_disponibles", []) or []),
    }
    if con_comparativa:
        diagnostico["sugerencia_admision_post"] = _a_str(
            diag_post.get("sugerencia_admision", "N/A")
        )
        diagnostico["justificacion_post"] = _a_str(diag_post.get("justificacion", "N/A"))
        diagnostico["nivel_riesgo_post"] = _a_str(diag_post.get("nivel_riesgo", "N/A"))
        diagnostico["hallazgos_post"] = list(diag_post.get("hallazgos", []) or [])
        diagnostico["senales_disponibles_post"] = list(
            metricas_post.get("senales_disponibles", []) or []
        )

    payload: Dict[str, Any] = {
        "metadatos": metadatos,
        "fisicas_clave": fisicas_clave,
        "metricas": {"pre": metricas_pre, "post": metricas_post},
        "diagnostico": diagnostico,
    }

    if con_comparativa:
        payload["evolucion"] = {
            "veredicto": _a_str(delta.get("veredicto_evolucion", VEREDICTO_SIN_DATOS)),
            "deltas": deltas,
            "mejoras": delta.get("mejoras", []) or [],
            "retrocesos": delta.get("retrocesos", []) or [],
        }

    # Avisos explícitos de cobertura (nunca se fabrican datos).
    if not existe_pre:
        payload["advertencia"] = (
            f"La sesión Pre ({session_id_pre}) no existe en la base de datos; "
            "los valores de Pre son neutros (0 / N/A)."
        )
    elif con_comparativa and not existe_post:
        payload["advertencia"] = (
            f"La sesión Post ({session_id_post}) no existe en la base de datos; "
            "el payload refleja únicamente los datos de la sesión Pre."
        )

    return payload
