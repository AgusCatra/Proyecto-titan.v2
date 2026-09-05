# core/evaluador_diagnostico.py
# Proyecto Titán — Motor de evaluación pedagógica (lógica pura, SIN Streamlit).
# Versión: 2.0 (correcciones C1..C9, S1..S3, S7 de la revisión de código)
#
# Este módulo concentra la lógica de negocio del diagnóstico de admisión y de la
# comparativa de evolución (delta) entre dos sesiones de simulador. No depende de
# ninguna librería de UI: solo de sqlite3, numpy y de los helpers ya existentes en
# ``core.db_manager``.
#
# Principios de diseño:
#   * Tolerancia a datos incompletos: si una señal de telemetría no existe, si la
#     sesión no está en la BD o si las series vienen vacías, NO se lanzan
#     excepciones; se devuelven dicts coherentes con valores por defecto (0 / 0.0)
#     y una justificación en texto.
#   * Cobertura explícita [C1]: la AUSENCIA de telemetría NUNCA se confunde con
#     "conducción limpia". Si faltan señales críticas no se emite "Apto".
#   * SQL siempre parametrizado (?), nunca interpolado con f-strings.
#   * Series SIEMPRE ordenadas por tiempo antes de calcular derivadas.
#   * Integración temporal robusta a huecos de muestreo [C5]: los saltos mayores que
#     ``DT_MAX`` NO suman tiempo ni se interpolan (no se inventa velocidad).
#   * Detección de vicios por EVENTOS (rachas / inversiones), combinando tasa y
#     amplitud [C4], alineada con la especificación pedagógica del usuario.
#   * Umbrales definidos como constantes de módulo, documentadas y ajustables.

"""Motor de evaluación pedagógica del Proyecto Titán.

Expone tres capacidades principales:

1. :func:`evaluar_diagnostico_inicial` — dictamen de admisión (Día 1) a partir de
   los vicios de manejo detectados en la telemetría y las colisiones registradas.
2. :func:`comparar_sesiones_delta` — comparativa Pre/Post (Día Final) con deltas
   absolutos y porcentuales, veredicto de evolución y resumen imprimible.
3. Persistencia de la decisión del instructor sobre la tabla ``DecisionInstructor``
   (creada de forma segura con ``CREATE TABLE IF NOT EXISTS``).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# --- Reutilización de helpers existentes (NO reimplementamos el parseo CSV) ---
from core.db_manager import get_telemetry_for_graph

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

# Señales imprescindibles para poder afirmar que NO hay vicios (C1). Si falta
# alguna, la ausencia de detección se debe a falta de datos, no a buena conducción.
SENALES_CRITICAS: List[str] = [SENAL_BRAKE, SENAL_STEERING, SENAL_FORK_HEIGHT]
# Cobertura mínima (de 6 señales) exigida para poder emitir un "Apto" (C1).
COBERTURA_MINIMA_SENALES: int = 4

# --- Muestreo / integración temporal (C5) -----------------------------------
# Intervalo máximo (s) entre muestras que se considera un muestreo real continuo.
# Por encima de este valor hay un HUECO (pérdida de extracción): ese tramo NO suma
# tiempo ni se interpola, para no fabricar velocidad/duración sin evidencia.
DT_MAX: float = 3.0

# --- Frenadas bruscas (C4) --------------------------------------------------
# Tasa de subida del pedal de freno (unidades/s). Referencia de behavior_analyzer.
FRENADO_TASA_UMBRAL: float = 5.0
# Valor alto del pedal de freno (rango típico 0..10): "subida vertical hacia 10".
FRENADO_VALOR_ALTO: float = 8.0
# Número de EVENTOS de frenada brusca para severidad moderada / alta.
FRENADAS_EVENTOS_MODERADO: int = 4
FRENADAS_EVENTOS_ALTO: int = 10
# Muestras con el pedal en rango alto (frenado sostenido/dureza) que aportan
# severidad al dictamen (antes se calculaba y se descartaba — C4).
PICOS_FRENO_ALTO_UMBRAL: int = 20

# --- Volantazos (C4) --------------------------------------------------------
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

# --- Inseguridad en torre (S1) ----------------------------------------------
# Amplitud mínima de un movimiento de horquilla (m) / inclinación (deg) para
# considerarlo un microajuste real y no ruido de calibración.
MICROAJUSTE_AMPLITUD_MIN: float = 0.02
# Inversiones de dirección a partir de las cuales hay inseguridad relevante.
MICROAJUSTE_INVERSIONES_ALTO: int = 40
# Segundos acumulados en régimen de microajuste que aportan severidad.
MICROAJUSTE_SEGUNDOS_ALTO: float = 30.0

# --- Traslado con horquilla alta (C5) ---------------------------------------
# Altura de horquilla (m) por encima de la cual NO es seguro trasladarse.
FORK_SAFE_THRESHOLD_MTRS: float = 0.30
# Velocidad mínima (Km/h) para considerar que la máquina se está trasladando.
VELOCIDAD_MOVIMIENTO_MIN: float = 0.5
# Segundos acumulados trasladando con horquilla alta que indican vicio relevante.
TRASLADO_ALTO_SEGUNDOS_ALTO: float = 15.0

# --- Colisiones -------------------------------------------------------------
COLISIONES_CRITICO: int = 3
COLISIONES_OBSERVADO: int = 1


# =============================================================================
# UTILIDADES INTERNAS DE SERIES Y DERIVADAS
# =============================================================================
def _cargar_serie(
    id_sesion: int,
    nombre_grafico: str,
    conn: sqlite3.Connection,
) -> List[Tuple[float, float]]:
    """Carga una serie (t, v) de telemetría y la devuelve ORDENADA por tiempo.

    Reutiliza :func:`core.db_manager.get_telemetry_for_graph`. Devuelve lista vacía
    si la señal no existe o viene vacía (nunca lanza excepción).
    """
    try:
        puntos = get_telemetry_for_graph(id_sesion, nombre_grafico, conn=conn)
    except Exception:  # pragma: no cover - defensa ante errores de BD imprevistos
        return []
    if not puntos:
        return []
    try:
        return sorted((float(t), float(v)) for t, v in puntos)
    except (TypeError, ValueError):
        return []


def _cargar_series(
    id_sesion: int, conn: sqlite3.Connection
) -> Dict[str, List[Tuple[float, float]]]:
    """Carga las 6 señales canónicas ordenadas. Las ausentes quedan como lista vacía."""
    return {senal: _cargar_serie(id_sesion, senal, conn) for senal in SENALES_CANONICAS}


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
    ``dt`` real (0 < dt <= ``DT_MAX``), de modo que los huecos no cuenten (C5/S1).

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


def _campo(metadata: Optional[sqlite3.Row], nombre: str, default: Any) -> Any:
    """Acceso defensivo a una columna de ``Sesiones`` (S2).

    Devuelve ``default`` si la fila es ``None``, si la columna no existe (BD antigua
    sin ``puntaje_depurado``/etc.) o si el valor es ``NULL``. Nunca lanza.
    """
    if metadata is None:
        return default
    try:
        valor = metadata[nombre]
    except (IndexError, KeyError, TypeError):
        return default
    return default if valor is None else valor


# =============================================================================
# CONSULTAS DE METADATOS Y EVENTOS
# =============================================================================
def _obtener_metadata_sesion(
    session_id: int, conn: sqlite3.Connection
) -> Optional[sqlite3.Row]:
    """Devuelve la fila de ``Sesiones`` para el id dado, o ``None`` si no existe."""
    try:
        cur = conn.execute(
            "SELECT * FROM Sesiones WHERE id_sesion = ?",
            (session_id,),
        )
        return cur.fetchone()
    except sqlite3.Error:
        return None


def _contar_colisiones(session_id: int, conn: sqlite3.Connection) -> int:
    """Colisiones netas registradas en ``ResumenEventos`` para la sesión."""
    try:
        cur = conn.execute(
            "SELECT COALESCE(SUM(conteo_eventos), 0) FROM ResumenEventos "
            "WHERE id_sesion = ? AND tipo_evento = 'Collision'",
            (session_id,),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return 0
    if row is None:
        return 0
    valor = row[0]
    return int(valor) if valor is not None else 0


def _sumar_penalizaciones(session_id: int, conn: sqlite3.Connection) -> float:
    """Suma de penalizaciones agregadas (se guardan en NEGATIVO) de la sesión."""
    try:
        cur = conn.execute(
            "SELECT COALESCE(SUM(penalizaciones), 0.0) FROM ResumenEventos "
            "WHERE id_sesion = ?",
            (session_id,),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return 0.0
    if row is None:
        return 0.0
    valor = row[0]
    return float(valor) if valor is not None else 0.0


# =============================================================================
# CÁLCULO DE MÉTRICAS DE VICIOS DE MANEJO
# =============================================================================
def _metricas_frenado(serie: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Frenadas bruscas como EVENTOS: subida vertical del pedal hacia el rango alto.

    Combina tasa (``> FRENADO_TASA_UMBRAL``) y amplitud (alcanzar ``>=
    FRENADO_VALOR_ALTO`` subiendo), agrupando muestras consecutivas en un único
    evento (C4). ``picos_freno_alto`` mide la dureza sostenida del frenado.
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
    """Volantazos como EVENTOS de inversión de giro con amplitud relevante (C4).

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
    """Inseguridad en torre: microajustes (inversiones) Y tiempo acumulado (S1).

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

    Corrección C5: NO se interpola a través de huecos. Para cada muestra de
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


def _calcular_dictamen(metricas: Dict[str, Any]) -> Tuple[str, str, str]:
    """Combina severidad de vicios y colisiones para decidir el dictamen.

    Devuelve ``(sugerencia_admision, justificacion, foco_instructor)``.

    Regla de seguridad (C1): NUNCA retorna "Apto" si la cobertura de telemetría es
    inferior a ``COBERTURA_MINIMA_SENALES`` o faltan señales críticas; en ese caso
    devuelve "Observado" advirtiendo que los vicios cuentan 0 por ausencia de datos.
    """
    senales_disp = metricas.get("senales_disponibles", [])
    cobertura_ok, faltantes = _evaluar_cobertura(senales_disp)
    if not cobertura_ok:
        justificacion = (
            f"Telemetría insuficiente para certificar aptitud: solo hay "
            f"{len(senales_disp)}/6 señales disponibles"
            + (f" y faltan señales críticas ({', '.join(faltantes)})" if faltantes else "")
            + ". Los contadores de vicios valen 0 por AUSENCIA de datos, no por una "
            "conducción limpia; no es posible emitir un dictamen de aptitud fiable."
        )
        foco = "Reprocesar el PDF y verificar la extracción de telemetría antes de evaluar"
        return "Observado", justificacion, foco

    frenadas = metricas.get("frenadas_bruscas", 0)
    volantazos = metricas.get("volantazos", 0)
    colisiones = metricas.get("colisiones_netas", 0)
    microajustes = metricas.get("inseguridad_torre_microajustes", 0)
    torre_segundos = metricas.get("inseguridad_torre_segundos", 0.0)
    traslado_alto = metricas.get("traslado_horquilla_alta_segundos", 0.0)
    varianza = metricas.get("varianza_steering", 0.0)
    picos_freno = metricas.get("picos_freno_alto", 0)

    severidad = 0
    motivos: List[str] = []

    if colisiones >= COLISIONES_CRITICO:
        severidad += 3
        motivos.append(f"{colisiones} colisiones registradas")
    elif colisiones >= COLISIONES_OBSERVADO:
        severidad += 1
        motivos.append(f"{colisiones} colisión(es) registrada(s)")

    if frenadas >= FRENADAS_EVENTOS_ALTO:
        severidad += 2
        motivos.append(f"{frenadas} frenadas bruscas")
    elif frenadas >= FRENADAS_EVENTOS_MODERADO:
        severidad += 1
        motivos.append(f"{frenadas} frenadas bruscas")

    if picos_freno >= PICOS_FRENO_ALTO_UMBRAL:
        severidad += 1
        motivos.append(f"frenado sostenido/duro ({picos_freno} muestras en rango alto)")

    if volantazos >= VOLANTAZOS_EVENTOS_ALTO or varianza >= DIRECCION_VARIANZA_ALTA:
        severidad += 2
        motivos.append(f"{volantazos} volantazos (varianza {varianza:.1f})")
    elif volantazos >= VOLANTAZOS_EVENTOS_MODERADO:
        severidad += 1
        motivos.append(f"{volantazos} volantazos")

    if traslado_alto >= TRASLADO_ALTO_SEGUNDOS_ALTO:
        severidad += 2
        motivos.append(
            f"{traslado_alto:.0f}s trasladando con horquilla por encima de "
            f"{FORK_SAFE_THRESHOLD_MTRS:.2f} m"
        )
    elif traslado_alto > 0:
        severidad += 1
        motivos.append(f"{traslado_alto:.0f}s de traslado con horquilla alta")

    if (
        microajustes >= MICROAJUSTE_INVERSIONES_ALTO
        or torre_segundos >= MICROAJUSTE_SEGUNDOS_ALTO
    ):
        severidad += 1
        motivos.append(
            f"inseguridad en torre ({microajustes} microajustes, "
            f"{torre_segundos:.0f}s en régimen de duda)"
        )

    if severidad >= 5 or colisiones >= COLISIONES_CRITICO:
        sugerencia = "No Apto Crítico"
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
    elif severidad >= 2:
        sugerencia = "Observado"
        foco = "Trabajar radio de giro trasero, suavidad de mandos y control de la torre"
        justificacion = (
            "Vicios moderados detectados: " + "; ".join(motivos) + ". "
            "El operador es admisible con seguimiento: debe corregir estos hábitos "
            "durante la formación."
        )
    else:
        sugerencia = "Apto"
        foco = "Buen control base: afianzar hábitos normativos y productividad"
        justificacion = (
            ("Buen control base de la máquina. Observaciones menores: "
             + "; ".join(motivos) + ". Admisible: afianzar hábitos normativos.")
            if motivos
            else ("Buen control base de la máquina: sin colisiones relevantes ni vicios "
                  "críticos de manejo detectados en la telemetría. Admisible.")
        )

    return sugerencia, justificacion, foco


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
        ``foco_instructor``, ``metricas_calculadas`` y ``series``. Tolera sesiones
        inexistentes y señales ausentes devolviendo valores por defecto coherentes.
    """
    metadata = _obtener_metadata_sesion(session_id, conn)
    series = _cargar_series(session_id, conn)

    m_frenado = _metricas_frenado(series.get(SENAL_BRAKE, []))
    m_direccion = _metricas_direccion(series.get(SENAL_STEERING, []))
    m_torre = _metricas_torre(
        series.get(SENAL_FORK_HEIGHT, []), series.get(SENAL_TILT, [])
    )
    m_traslado = _metricas_traslado_alto(
        series.get(SENAL_FORK_HEIGHT, []), series.get(SENAL_SPEED, [])
    )

    colisiones = _contar_colisiones(session_id, conn)

    # --- Tiempo total (S2: acceso defensivo a columnas) ---
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

    # --- Dictamen con control de cobertura (C1) ---
    if metadata is None:
        sugerencia = "Observado"
        justificacion = (
            f"La sesión {session_id} no existe en la base de datos o no tiene "
            "metadatos; no es posible emitir un dictamen definitivo. Se marca como "
            "'Observado' por falta de información."
        )
        foco = "Verificar la carga del reporte PDF antes de evaluar"
    else:
        # _calcular_dictamen ya degrada a "Observado" si la cobertura es insuficiente.
        sugerencia, justificacion, foco = _calcular_dictamen(metricas)

    return {
        "sugerencia_admision": sugerencia,
        "justificacion": justificacion,
        "foco_instructor": foco,
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
    """Variación porcentual de ``anterior`` a ``nuevo`` (C6).

    Reglas:
      * ``anterior == 0`` y ``nuevo == 0`` -> ``0.0`` (sin cambio).
      * ``anterior == 0`` y ``nuevo != 0`` -> ``None`` (porcentaje no definido; la UI
        mostrará "n/a" en vez de un +100% falso o con signo invertido).
      * ``anterior != 0`` -> porcentaje real ``(nuevo-anterior)/|anterior|*100``.
    """
    if anterior == 0 or anterior == 0.0:
        return 0.0 if (nuevo == 0 or nuevo == 0.0) else None
    return round(((nuevo - anterior) / abs(anterior)) * 100.0, 2)


def _fmt_pct(pct: Optional[float]) -> str:
    """Formatea un porcentaje opcional para texto/tablas ("n/a" si es None)."""
    return "n/a" if pct is None else f"{pct:+.1f}%"


def _veredicto_por_delta_puntaje(delta: float) -> str:
    """Reutiliza los umbrales de ``report_generator.generar_veredicto``.

    ``> 15`` MEJORA SIGNIFICATIVA · ``> 1`` MEJORA MODERADA · ``< -1`` REGRESIÓN
    DETECTADA · resto RENDIMIENTO ESTANCADO.
    """
    if delta > 15:
        return "MEJORA SIGNIFICATIVA"
    if delta > 1:
        return "MEJORA MODERADA"
    if delta < -1:
        return "REGRESIÓN DETECTADA"
    return "RENDIMIENTO ESTANCADO"


def _deltas_cero() -> Dict[str, Any]:
    """Estructura de deltas neutra (todo 0 / None) para respuestas de error (C3)."""
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
    ``error`` y ``veredicto_evolucion="SIN DATOS"`` y deltas neutros (C3), en lugar
    de fabricar una mejora falsa.

    Returns:
        Dict con claves ``pre``, ``post``, ``deltas``, ``series_pre``,
        ``series_post``, ``veredicto_evolucion``, ``resumen_evolucion`` (y ``error``
        solo cuando aplica).
    """
    diag_pre = evaluar_diagnostico_inicial(session_id_pre, conn)
    diag_post = evaluar_diagnostico_inicial(session_id_post, conn)

    existe_pre = bool(diag_pre["metricas_calculadas"].get("sesion_existente"))
    existe_post = bool(diag_post["metricas_calculadas"].get("sesion_existente"))

    # --- Validación de existencia (C3) ---
    if not existe_pre or not existe_post:
        faltan = []
        if not existe_pre:
            faltan.append(str(session_id_pre))
        if not existe_post:
            faltan.append(str(session_id_post))
        return {
            "error": "Alguna de las sesiones no existe en la base de datos",
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
            "veredicto_evolucion": "SIN DATOS",
            "resumen_evolucion": (
                "No se puede comparar la evolución: la(s) sesión(es) "
                + ", ".join(faltan)
                + " no existe(n) en la base de datos."
            ),
        }

    pen_pre = _sumar_penalizaciones(session_id_pre, conn)
    pen_post = _sumar_penalizaciones(session_id_post, conn)

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

    # Suavidad calculada SOBRE TOTALES (C7): no se suman porcentajes independientes.
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

    veredicto = _veredicto_por_delta_puntaje(delta_puntaje)
    resumen = _construir_resumen_evolucion(bloque_pre, bloque_post, deltas, veredicto)

    return {
        "pre": bloque_pre,
        "post": bloque_post,
        "deltas": deltas,
        "series_pre": diag_pre["series"],
        "series_post": diag_post["series"],
        "veredicto_evolucion": veredicto,
        "resumen_evolucion": resumen,
    }


def _construir_resumen_evolucion(
    pre: Dict[str, Any],
    post: Dict[str, Any],
    deltas: Dict[str, Any],
    veredicto: str,
) -> str:
    """Redacta el resumen de evolución técnica en español, listo para el alumno."""
    def signo(valor: float) -> str:
        return f"+{valor:.2f}" if valor >= 0 else f"{valor:.2f}"

    lineas = [
        "RESUMEN DE EVOLUCIÓN TÉCNICA DEL OPERADOR",
        "=" * 46,
        "",
        f"Veredicto general: {veredicto}",
        "",
        "Métrica                     Día 1        Día Final     Variación",
        "-" * 66,
        (
            f"Puntaje depurado           {pre['puntaje_depurado']:>8.2f}    "
            f"{post['puntaje_depurado']:>8.2f}    {signo(deltas['puntaje_depurado'])} "
            f"({_fmt_pct(deltas['puntaje_depurado_pct'])})"
        ),
        (
            f"Colisiones                 {pre['metricas'].get('colisiones_netas', 0):>8d}    "
            f"{post['metricas'].get('colisiones_netas', 0):>8d}    "
            f"{deltas['colisiones']:+d} ({_fmt_pct(deltas['colisiones_pct'])})"
        ),
        (
            f"Frenadas bruscas           {pre['metricas'].get('frenadas_bruscas', 0):>8d}    "
            f"{post['metricas'].get('frenadas_bruscas', 0):>8d}    "
            f"{deltas['frenadas_bruscas']:+d} ({_fmt_pct(deltas['frenadas_bruscas_pct'])})"
        ),
        (
            f"Volantazos                 {pre['metricas'].get('volantazos', 0):>8d}    "
            f"{post['metricas'].get('volantazos', 0):>8d}    "
            f"{deltas['volantazos']:+d} ({_fmt_pct(deltas['volantazos_pct'])})"
        ),
        (
            f"Suavidad (fren.+volant.)   "
            f"{pre['metricas'].get('frenadas_bruscas', 0) + pre['metricas'].get('volantazos', 0):>8d}    "
            f"{post['metricas'].get('frenadas_bruscas', 0) + post['metricas'].get('volantazos', 0):>8d}    "
            f"{deltas['suavidad']:+d} ({_fmt_pct(deltas['suavidad_pct'])})"
        ),
        (
            f"Duración (s)               {pre['duracion_segundos']:>8.1f}    "
            f"{post['duracion_segundos']:>8.1f}    {signo(deltas['duracion_seg'])} "
            f"({_fmt_pct(deltas['duracion_pct'])})"
        ),
        "",
        "-" * 66,
        f"Perfil inicial: {pre['perfil']}    |    Perfil final: {post['perfil']}",
        "",
    ]

    mejoras: List[str] = []
    retrocesos: List[str] = []
    if deltas["colisiones"] < 0:
        mejoras.append(f"reducción de colisiones ({deltas['colisiones']:+d})")
    elif deltas["colisiones"] > 0:
        retrocesos.append(f"aumento de colisiones ({deltas['colisiones']:+d})")

    if deltas["frenadas_bruscas"] < 0:
        mejoras.append(f"menos frenadas bruscas ({deltas['frenadas_bruscas']:+d})")
    elif deltas["frenadas_bruscas"] > 0:
        retrocesos.append(f"más frenadas bruscas ({deltas['frenadas_bruscas']:+d})")

    if deltas["volantazos"] < 0:
        mejoras.append(f"dirección más suave ({deltas['volantazos']:+d} volantazos)")
    elif deltas["volantazos"] > 0:
        retrocesos.append(f"dirección más nerviosa ({deltas['volantazos']:+d} volantazos)")

    if mejoras:
        lineas.append("Aspectos positivos: " + "; ".join(mejoras) + ".")
    if retrocesos:
        lineas.append("Aspectos a reforzar: " + "; ".join(retrocesos) + ".")
    if not mejoras and not retrocesos:
        lineas.append(
            "El rendimiento se mantiene estable entre ambas sesiones: consolidar "
            "los hábitos adquiridos y buscar mayor fluidez operativa."
        )

    return "\n".join(lineas)


# =============================================================================
# API PÚBLICA — C) PERSISTENCIA DE LA DECISIÓN DEL INSTRUCTOR
# =============================================================================
# Definición con UNIQUE(id_sesion) y ON DELETE CASCADE (C2), coherente con
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
        print(f"[asegurar_tabla_decision_instructor] Error de BD: {exc}")
        return False


def guardar_decision_instructor(
    session_id: int,
    veredicto: str,
    notas: str,
    conn: sqlite3.Connection,
    foco_sugerido: Optional[str] = None,
) -> bool:
    """Persiste la decisión del instructor para una sesión (UPSERT no destructivo).

    Estrategia (C2): UPDATE de la fila existente de la sesión; si no existía
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
        print(f"[guardar_decision_instructor] Error de BD: {exc}")
        conn.rollback()
        return False


def obtener_decision_instructor(
    session_id: int, conn: sqlite3.Connection
) -> Optional[Dict[str, Any]]:
    """Devuelve la decisión guardada del instructor para la sesión, o ``None``.

    Operación de SOLO LECTURA (C9): no crea la tabla ni hace commit. Si la tabla no
    existe todavía o hay cualquier error de BD, devuelve ``None`` sin efectos
    secundarios. Funciona con y sin ``row_factory=sqlite3.Row`` (S3).
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
