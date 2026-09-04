# core/graph_mapper.py
# Normalización de claves de telemetría a las 6 señales canónicas del Proyecto Titán.
#
# Este módulo es intencionalmente "ligero" (solo depende de la librería estándar)
# para poder importarse y testearse sin necesidad de OpenCV / Tesseract.
import json
import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAP_PATH = os.path.join(os.path.dirname(__file__), "..", "mapeo.json")

# ---------------------------------------------------------------------------
# Fuente única de verdad: las 6 señales canónicas estandarizadas que consume la
# tabla `Telemetria` de la base de datos y las interfaces (app.py / streamlit).
# ---------------------------------------------------------------------------
CANONICAL_SIGNALS: List[str] = [
    "Steering",
    "Brake Pad",
    "Acceleration Pad",
    "Fork Height In Mtrs",
    "Tilt Angle In Deg",
    "Speed In Km/h",
]

Series = Optional[List[Tuple[float, float]]]


def load_mapping() -> List[Dict]:
    """Carga el mapeo legacy ``Graph_X_Y -> nombre canónico`` desde mapeo.json."""
    if not os.path.exists(MAP_PATH):
        return []
    with open(MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_telemetry(raw_graphs: Dict[str, Series]) -> Dict[str, List[Tuple[float, float]]]:
    """Conserva SOLO las señales canónicas con datos, descartando el resto.

    Reglas:
      * Se descartan series vacías o ``None``.
      * Se descartan claves no canónicas (p. ej. ``"Unknown_5_1"``).
      * Una señal que YA viene identificada con un nombre canónico nunca se anula.
      * Si una señal aparece más de una vez, se conserva la serie más larga.
    """
    result: Dict[str, List[Tuple[float, float]]] = {}
    for key, data in (raw_graphs or {}).items():
        if not data:
            continue
        if key in CANONICAL_SIGNALS:
            if key not in result or len(data) > len(result[key]):
                result[key] = data
        else:
            logger.debug("Señal no canónica descartada: %r", key)
    return result


def map_graphs(raw_graphs: Dict[str, Series]) -> Dict[str, List[Tuple[float, float]]]:
    """Normaliza las claves crudas del extractor a señales canónicas.

    Corrige el bug de contrato (§6-A): antes SOLO buscaba claves ``"Graph_X_Y"``
    definidas en ``mapeo.json``. Como el extractor visual emite nombres canónicos
    (``"Steering"``) o ``"Unknown_X_Y"``, la intersección era vacía y ``map_graphs``
    devolvía ``{}``, perdiéndose TODA la telemetría en el camino de Streamlit.

    Reglas actuales (idempotentes y seguras):
      1. Clave ya canónica             -> se conserva directamente.
      2. Clave legacy ``"Graph_X_Y"``  -> se mapea vía ``mapeo.json``.
      3. Cualquier otra clave          -> se descarta (no canónica).
    """
    legacy_map = {g["graph"]: g["suggested_name"] for g in load_mapping() if "graph" in g}
    result: Dict[str, List[Tuple[float, float]]] = {}

    for key, data in (raw_graphs or {}).items():
        if not data:
            continue
        if key in CANONICAL_SIGNALS:
            canonical = key
        elif key in legacy_map:
            canonical = legacy_map[key]
            logger.debug("Mapeo legacy %r -> %r", key, canonical)
        else:
            logger.debug("Clave no canónica descartada: %r", key)
            continue

        if canonical not in result or len(data) > len(result[canonical]):
            result[canonical] = data

    return result
