# core/pipeline.py
"""Pipeline ETL unificado y *headless* para reportes PDF del simulador.

Concentra la orquestación que antes estaba **duplicada y divergente** en
``app.py`` y ``streamlit_app.py`` (funciones ``_procesar_y_obtener_id`` y
``_preparar_datos_para_prediccion``), resolviendo la deuda técnica §6-B.

Garantías de diseño:
  * **100% agnóstico de la interfaz gráfica.** No importa ``streamlit`` ni
    ``customtkinter``, no invoca ``st.*`` ni ``print()`` sueltos: usa ``logging``.
  * **Configuración inyectable.** ``db_path``, ``models_path``, ``exports_dir``,
    ``engine`` y ``copy_to_exports`` son parámetros (base para §6-E).
  * **Transaccional.** La escritura en BD se realiza dentro de una transacción
    con *rollback* seguro ante cualquier fallo.

Flujo de ``process_simulator_pdf``:
  1. Parseo de metadatos (``core.pdf_parser``).
  2. Predicción / asignación del perfil de operador.
  3. Extracción de telemetría (best-effort) y normalización a señales canónicas.
  4. Inserción transaccional en BD (sesión + eventos + curvas) con rollback.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

import joblib
import pandas as pd

from .pdf_parser import parse_pdf_report
from .db_manager import (
    get_db_connection,
    insert_session,
    insert_summary_events,
    get_session_id_by_filename,
    insert_telemetry_data,
)
from .telemetry_extractor import extraer_telemetria_visual
from .telemetry_parser import extraer_toda_la_telemetria
from .graph_mapper import CANONICAL_SIGNALS, map_graphs

logger = logging.getLogger(__name__)

# --- Rutas por defecto (calculadas desde la ubicación del paquete) ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "database", "titan.db")
DEFAULT_MODELS_PATH = os.path.join(BASE_DIR, "models", "modelo_clasificador.joblib")
DEFAULT_EXPORTS_DIR = os.path.join(BASE_DIR, "data", "exports")

# --- Valores válidos para los parámetros de estrategia ---
PROFILE_SOURCE_MODEL = "model"   # Predicción mediante el modelo ML (joblib)
PROFILE_SOURCE_NONE = "none"     # Sin asignación de perfil
ENGINE_VISUAL = "visual"         # core.telemetry_extractor (agnóstico de layout)
ENGINE_PARSER = "parser"         # core.telemetry_parser (config-driven, páginas fijas)

# --- Reglas de negocio (marcadores de eventos normalizados) ------------------
_PRECHECK_MARKER = "prechecksviolation"   # checklist inicial (no vicio dinámico)
_FORK_MARKER = "minimumforkheight"        # falso error de horquilla en piso


def _norm_event_type(value: Any) -> str:
    """Normaliza un tipo de evento a minúsculas sin separadores ni símbolos."""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _aplicar_reglas_de_negocio(parsed_data: Dict[str, Any]) -> None:
    """Calcula ``puntaje_depurado`` y ``checklist_completado`` sobre session_data.

    3.1 Falso error de horquilla: si existe el evento ``Minimum Fork Height(mtrs)``
        penalizado al inicio (operación segura en piso), se reintegra ese descuento
        al puntaje para obtener ``puntaje_depurado = puntaje_original + |penalty|``.
    3.2 Checklist inicial: la presencia de ``PrechecksViolation`` marca
        ``checklist_completado = True`` (son prechequeos, no vicios dinámicos).
    """
    session = parsed_data.get("session_data") or {}
    events = parsed_data.get("summary_events", []) or []
    puntaje = float(session.get("puntaje_final", 0.0) or 0.0)

    descuento_horquilla = 0.0
    for ev in events:
        if _FORK_MARKER in _norm_event_type(ev.get("type", "")):
            descuento_horquilla += abs(float(ev.get("penalties", 0.0) or 0.0))
    session["puntaje_depurado"] = round(puntaje + descuento_horquilla, 2)

    session["checklist_completado"] = any(
        _PRECHECK_MARKER in _norm_event_type(ev.get("type", "")) for ev in events
    )
    parsed_data["session_data"] = session


def _completar_senales_ausentes(
    telemetry: Dict[str, List[Tuple[float, float]]],
    duration: Any,
) -> None:
    """3.3 Rellena con un vector continuo en 0.0 las señales canónicas ausentes.

    Si tras procesar todas las páginas una señal canónica no aparece (p. ej. el
    alumno jamás tocó el freno), se inicializa en 0.0 con la misma longitud y rango
    temporal que las demás señales, sin romper la ejecución.
    """
    presentes = [v for v in telemetry.values() if v]
    ref_len = max((len(v) for v in presentes), default=0)
    if ref_len <= 0:
        ref_len = max(2, int(duration or 0) or 2)
    span = float(duration or 0)
    step = span / (ref_len - 1) if ref_len > 1 else 0.0
    for signal in CANONICAL_SIGNALS:
        if telemetry.get(signal):
            continue
        telemetry[signal] = [(round(i * step, 2), 0.0) for i in range(ref_len)]
        logger.info("Señal canónica ausente, se inicializa en 0.0: %s", signal)


def _preparar_datos_para_prediccion(parsed_data: Dict[str, Any], feature_names: Any) -> pd.DataFrame:
    """Construye el DataFrame de características alineado con el modelo ML.

    Versión **unificada y robusta** de la función que estaba duplicada (y
    divergente) en ``app.py`` y ``streamlit_app.py``:
      * Si el modelo no expone ``feature_names_in_``, usa las columnas presentes.
      * Acepta ``feature_names`` como lista, ndarray de NumPy o ``None``.
      * Rellena con ``0`` las características ausentes para evitar errores de
        alineación en ``sklearn``.
    """
    session_data = parsed_data.get("session_data", {}) or {}
    data: Dict[str, float] = {
        "puntaje_final": float(session_data.get("puntaje_final", 0.0) or 0.0),
        "duracion_segundos": float(session_data.get("duracion_segundos", 0) or 0),
    }
    for event in parsed_data.get("summary_events", []) or []:
        raw_type = str(event.get("type", ""))
        ev_type = raw_type.replace(" ", "_")
        if not ev_type:
            continue
        # Los PrechecksViolation corresponden al checklist inicial, no a vicios
        # dinámicos de manejo: se excluyen del conteo de violaciones dinámicas.
        if _PRECHECK_MARKER in _norm_event_type(raw_type):
            continue
        data[f"conteo_eventos_{ev_type}"] = float(event.get("total_events", 0) or 0)
        data[f"penalizaciones_{ev_type}"] = float(event.get("penalties", 0) or 0)

    # Sin nombres de características conocidos -> usar las columnas disponibles.
    if feature_names is None or (hasattr(feature_names, "__len__") and len(feature_names) == 0):
        return pd.DataFrame([data]).fillna(0)

    # Convertir a lista si viene como ndarray (NumPy).
    if not isinstance(feature_names, list):
        feature_names = list(feature_names)

    # Se acumulan las características en un dict estándar y se materializa UNA
    # sola vez como DataFrame float (columnas en el orden del modelo). Esto evita
    # la asignación celda-a-celda ``df.at[0, key] = value`` sobre columnas creadas
    # implícitamente como int64, que disparaba el FutureWarning de Pandas; el
    # DataFrame resultante soporta decimales positivos y negativos sin avisos.
    features: Dict[str, float] = {name: 0.0 for name in feature_names}
    for key, value in data.items():
        if key in features:
            features[key] = float(value)
    return pd.DataFrame([features])


def _resolve_profile(
    parsed_data: Dict[str, Any],
    profile_source: str,
    models_path: str,
    perfil_override: Optional[str],
    errors: List[str],
) -> Optional[str]:
    """Determina el perfil del operador sin interrumpir el pipeline ante fallos."""
    if perfil_override:
        return perfil_override
    if profile_source == PROFILE_SOURCE_NONE:
        return None

    # profile_source == PROFILE_SOURCE_MODEL
    try:
        modelo = joblib.load(models_path)
    except FileNotFoundError:
        msg = f"Modelo ML no encontrado en '{models_path}'. La sesión se guardará sin perfil."
        logger.warning(msg)
        errors.append(msg)
        return None
    except Exception as exc:  # joblib puede lanzar errores de deserialización
        msg = f"No se pudo cargar el modelo ML: {exc}"
        logger.exception(msg)
        errors.append(msg)
        return None

    try:
        df_pred = _preparar_datos_para_prediccion(parsed_data, getattr(modelo, "feature_names_in_", []))
        perfil = modelo.predict(df_pred)[0]
        return str(perfil) if perfil is not None else None
    except Exception as exc:
        msg = f"Fallo al predecir el perfil del operador: {exc}"
        logger.exception(msg)
        errors.append(msg)
        return None


def _extract_telemetry(
    pdf_path: str,
    duration: int,
    engine: str,
    errors: List[str],
) -> Dict[str, List[Tuple[float, float]]]:
    """Extrae y normaliza la telemetría a señales canónicas (best-effort).

    La extracción por visión artificial puede fallar (p. ej. Tesseract ausente);
    en ese caso se registra el error y se devuelve ``{}`` **sin** abortar el
    guardado de la sesión.
    """
    try:
        if engine == ENGINE_PARSER:
            raw = extraer_toda_la_telemetria(pdf_path, duration)
        else:
            raw = extraer_telemetria_visual(pdf_path, duration)
    except Exception as exc:
        msg = f"Fallo al extraer telemetría (engine='{engine}'): {exc}"
        logger.exception(msg)
        errors.append(msg)
        return {}

    # map_graphs es ahora un normalizador seguro (§6-A): conserva las señales
    # canónicas identificadas y descarta claves sin identificar.
    return map_graphs(raw)


def _safe_copy_to_exports(pdf_path: str, exports_dir: str, file_name: str, errors: List[str]) -> None:
    """Copia el PDF procesado a ``exports_dir`` (post-commit, no crítico)."""
    try:
        os.makedirs(exports_dir, exist_ok=True)
        dst = os.path.join(exports_dir, file_name)
        if not os.path.exists(dst):
            shutil.copy2(pdf_path, dst)
    except OSError as exc:
        msg = f"No se pudo copiar el PDF a '{exports_dir}': {exc}"
        logger.warning(msg)
        errors.append(msg)


def process_simulator_pdf(
    pdf_path: str,
    profile_source: str = PROFILE_SOURCE_MODEL,
    *,
    db_path: str = DEFAULT_DB_PATH,
    models_path: str = DEFAULT_MODELS_PATH,
    exports_dir: str = DEFAULT_EXPORTS_DIR,
    engine: str = ENGINE_VISUAL,
    copy_to_exports: bool = True,
    perfil_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Procesa un PDF del simulador de punta a punta, de forma transaccional.

    Parámetros del contrato estable:
      ``pdf_path``:        ruta al reporte PDF del simulador.
      ``profile_source``:  ``"model"`` (predicción ML) o ``"none"`` (sin perfil).

    Parámetros de inyección (opcionales, solo por palabra clave):
      ``db_path``:         ruta a la base de datos SQLite.
      ``models_path``:     ruta al modelo clasificador (``.joblib``).
      ``exports_dir``:     carpeta destino de la copia del PDF procesado.
      ``engine``:          ``"visual"`` (telemetry_extractor) o ``"parser"``
                           (telemetry_parser).
      ``copy_to_exports``: si se copia el PDF a ``exports_dir`` tras procesar.
      ``perfil_override``: fuerza un perfil (p. ej. etiqueta manual de manifest).

    Devuelve:
      Un ``dict`` con las claves:
        * ``session_id``       -> ``int`` de la sesión (o ``None`` si falló).
        * ``metadata``         -> datos de sesión parseados (o fila BD si cacheada).
        * ``telemetry_loaded`` -> ``{señal: nº de puntos}`` persistidos.
        * ``profile``          -> perfil asignado (o ``None``).
        * ``errors``           -> ``list[str]`` con errores controlados.
        * ``cached``           -> ``True`` si el archivo ya estaba procesado.
    """
    errors: List[str] = []
    result: Dict[str, Any] = {
        "session_id": None,
        "metadata": None,
        "telemetry_loaded": {},
        "profile": None,
        "errors": errors,
        "cached": False,
    }

    if not pdf_path or not os.path.exists(pdf_path):
        errors.append(f"PDF no encontrado: '{pdf_path}'.")
        logger.error("PDF no encontrado: %r", pdf_path)
        return result

    file_name = os.path.basename(pdf_path)

    with get_db_connection(db_path) as conn:
        # --- Idempotencia: si el archivo ya fue procesado, reutilizar la sesión. ---
        cached_id = get_session_id_by_filename(conn, file_name)
        if cached_id:
            result["session_id"] = cached_id
            result["cached"] = True
            row = conn.execute(
                "SELECT * FROM Sesiones WHERE id_sesion = ?", (cached_id,)
            ).fetchone()
            if row is not None:
                result["metadata"] = dict(row)
                result["profile"] = row["perfil_operador"]
            logger.info("Archivo ya procesado (sesión #%s): %s", cached_id, file_name)
            if copy_to_exports:
                _safe_copy_to_exports(pdf_path, exports_dir, file_name, errors)
            return result

        # --- 1) Parseo de metadatos ---
        parsed_data = parse_pdf_report(pdf_path)
        if not parsed_data:
            errors.append(
                "No se pudieron extraer metadatos del PDF (parse_pdf_report devolvió None)."
            )
            logger.error("parse_pdf_report devolvió None para %s", file_name)
            return result
        # --- 1.b) Reglas de negocio sobre los metadatos parseados ---
        _aplicar_reglas_de_negocio(parsed_data)
        result["metadata"] = parsed_data.get("session_data")

        # --- 2) Perfil del operador ---
        profile = _resolve_profile(parsed_data, profile_source, models_path, perfil_override, errors)
        result["profile"] = profile

        # --- 3) Telemetría (best-effort, fuera de la transacción de escritura) ---
        duration = (parsed_data.get("session_data") or {}).get("duracion_segundos") or 0
        telemetry = _extract_telemetry(pdf_path, duration, engine, errors)
        # 3.3 Las señales canónicas no extraídas se inicializan en 0.0.
        _completar_senales_ausentes(telemetry, duration)

        # --- 4) Inserción transaccional con rollback seguro ---
        try:
            conn.execute("BEGIN")
            new_id = insert_session(conn, parsed_data, profile)
            if not new_id:
                raise RuntimeError("insert_session no devolvió un id de sesión válido.")

            insert_summary_events(conn, new_id, parsed_data.get("summary_events", []))

            for nombre, datos in telemetry.items():
                if datos:
                    insert_telemetry_data(conn, new_id, nombre, datos)
                    result["telemetry_loaded"][nombre] = len(datos)

            conn.commit()
            result["session_id"] = new_id
            logger.info(
                "Sesión #%s procesada: %d señal(es) de telemetría persistida(s).",
                new_id, len(result["telemetry_loaded"]),
            )
        except Exception as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                logger.exception("El rollback de la transacción también falló.")
            errors.append(f"Transacción de BD revertida (rollback): {exc}")
            logger.exception("Fallo en la transacción de BD para %s", file_name)
            return result

    # --- Copia a exports (post-commit, best-effort) ---
    if copy_to_exports:
        _safe_copy_to_exports(pdf_path, exports_dir, file_name, errors)

    return result
