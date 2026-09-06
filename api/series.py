# api/series.py
# Proyecto Titán — Utilidades PURAS de series de telemetría para la capa REST.
#
# ``core`` expone las series crudas ``Dict[señal, List[(t, v)]]`` pero NO tiene
# ninguna función de normalización a porcentaje de progreso: esa transformación
# vivía únicamente en la UI. Se reimplementa aquí en Python puro (sin numpy, sin
# UI) para que la API pueda ofrecer el eje X "Avance del Ejercicio (%)" sin
# tocar ``core`` y sin arrastrar dependencias pesadas.
#
# Regla crítica: los timestamps reales pueden ser NEGATIVOS (p. ej. -25.53 por
# artefactos de calibración), por lo que el origen de la normalización es SIEMPRE
# ``t0 = serie[0][0]`` y el span ``serie[-1][0] - t0``; nunca se asume t0 == 0.

from typing import Any, Dict, List, Sequence, Tuple

Serie = Sequence[Sequence[float]]
SeriesPorSenal = Dict[str, List[Tuple[float, float]]]


def normalizar_progreso(serie: Serie) -> List[List[float]]:
    """Reescala el eje temporal de una serie a porcentaje de progreso (0 -> 100).

    Args:
        serie: secuencia de pares ``(t, v)`` ordenada por tiempo.

    Returns:
        Lista de pares ``[pct, v]`` donde ``pct`` es el avance relativo respecto
        del primer punto (0.0) hasta el último (100.0), redondeado a 4 decimales.

    Casos degenerados (nunca lanza):
      * Menos de 2 puntos -> no hay progreso medible: todos los puntos quedan en
        ``0.0`` (se conserva el valor ``v``).
      * Span temporal nulo (``|span| < 1e-9``) -> idem, para evitar división por 0.
    """
    if len(serie) < 2:
        return [[0.0, v] for _, v in serie]
    t0 = serie[0][0]
    span = serie[-1][0] - t0
    if abs(span) < 1e-9:
        return [[0.0, v] for _, v in serie]
    return [[round((t - t0) / span * 100.0, 4), v] for t, v in serie]


def series_a_listas(series: SeriesPorSenal) -> Dict[str, List[List[float]]]:
    """Convierte las series de tuplas de ``core`` a listas de listas JSON-nativas.

    ``core`` devuelve ``List[Tuple[float, float]]``; aquí se materializan como
    ``List[List[float]]`` para que la serialización y la validación Pydantic sean
    inequívocas (y para no depender de la coerción tupla->lista).
    """
    if not series:
        return {}
    return {
        senal: [[float(t), float(v)] for t, v in (puntos or [])]
        for senal, puntos in series.items()
    }


def normalizar_series(
    series: SeriesPorSenal, senales: Sequence[str]
) -> Dict[str, List[List[float]]]:
    """Normaliza a progreso (0->100 %) cada señal canónica presente en ``series``.

    Itera sobre ``senales`` (las señales canónicas importadas de ``core``) para
    fijar un orden estable y cubrirlas todas: una señal ausente o vacía aporta una
    lista vacía, nunca se inventan puntos.
    """
    if not series:
        return {}
    normalizadas: Dict[str, List[List[float]]] = {}
    for senal in senales:
        puntos = series.get(senal) or []
        normalizadas[senal] = normalizar_progreso([[float(t), float(v)] for t, v in puntos])
    return normalizadas


def contar_puntos(series: Any) -> Dict[str, int]:
    """Devuelve ``{señal: nº de puntos}`` de un dict de series (tolerante a None)."""
    if not isinstance(series, dict):
        return {}
    return {senal: len(puntos or []) for senal, puntos in series.items()}
