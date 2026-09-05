# core/db_manager.py
# Proyecto Titán — Capa de acceso a datos (SQLite).
#
# Única fuente de verdad de las consultas SQL del proyecto: ni los frontends ni
# los generadores de documentos deben escribir SQL propio. Todas las funciones
# reciben una conexión abierta (inyección de dependencias) para que el ciclo de
# vida de la transacción lo decida el llamador.

import sqlite3
import os
from typing import Any, Optional, List, Tuple, Dict
from contextlib import contextmanager

# Definir la ruta a la base de datos
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'titan.db')

def create_connection(db_path: str) -> Optional[sqlite3.Connection]:
    """Crea una conexión a la base de datos SQLite."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"Error al conectar con la base de datos: {e}")
        return None

@contextmanager
def get_db_connection(db_path: str):
    """Context manager para manejo automático de conexiones."""
    conn = create_connection(db_path)
    if conn is None:
        raise Exception("No se pudo conectar a la base de datos")
    try:
        yield conn
    finally:
        if conn:
            conn.close()

# =============================================================================
# LECTURA DE TELEMETRÍA
# =============================================================================
def get_telemetry_for_graph(
    id_sesion: int,
    graph_name: str,
    conn: sqlite3.Connection = None,
    db_path: str = None
) -> Optional[List[Tuple[float, float]]]:
    """
    Obtiene la serie (t, v) para un gráfico de telemetría.
    Devuelve lista de tuplas [(timestamp, valor), ...] o None si no hay datos.
    """

    close_conn = False
    try:
        if conn is None:
            if db_path is None:
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                db_path = os.path.join(base_dir, "database", "titan.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            close_conn = True

        cur = conn.cursor()
        cur.execute(
            "SELECT timestamps, valores FROM Telemetria WHERE id_sesion_fk = ? AND nombre_grafico = ?",
            (id_sesion, graph_name)
        )
        rows = cur.fetchall()
        if not rows:
            return None

        all_points: List[Tuple[float, float]] = []

        for row in rows:
            ts_str = row["timestamps"]
            val_str = row["valores"]

            if not ts_str or not val_str:
                continue

            # Parsear listas separadas por comas
            ts_list = [float(x) for x in str(ts_str).split(",") if x.strip()]
            val_list = [float(x) for x in str(val_str).split(",") if x.strip()]

            # Asegurar que no se desalineen
            n = min(len(ts_list), len(val_list))
            all_points.extend([(ts_list[i], val_list[i]) for i in range(n)])

        return all_points if all_points else None

    except sqlite3.Error as e:
        print(f"[get_telemetry_for_graph] Error DB: {e}")
        return None
    finally:
        if close_conn and conn:
            conn.close()

# =============================================================================
# LECTURA DE SESIONES Y EVENTOS
# =============================================================================
def _fila_a_dict(cursor: sqlite3.Cursor, row: Any) -> Dict[str, Any]:
    """Convierte una fila a ``dict`` con y sin ``row_factory=sqlite3.Row``."""
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        columnas = [d[0] for d in (cursor.description or [])]
        return dict(zip(columnas, tuple(row)))


def list_sessions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Lista todas las sesiones con los campos descriptivos usados por las UI.

    Devuelve ``[]`` ante cualquier error de BD (lectura degradada, nunca lanza).
    """
    sql = (
        "SELECT id_sesion, nombre_operador, nombre_clase, nombre_ejercicio, "
        "fecha_hora_inicio, duracion_segundos, puntaje_final, perfil_operador "
        "FROM Sesiones ORDER BY id_sesion"
    )
    try:
        cursor = conn.execute(sql)
        return [_fila_a_dict(cursor, row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        print(f"[list_sessions] Error DB: {e}")
        return []


def get_session_row(conn: sqlite3.Connection, session_id: int) -> Optional[Dict[str, Any]]:
    """Devuelve la fila completa de ``Sesiones`` como ``dict``, o ``None``."""
    sql = "SELECT * FROM Sesiones WHERE id_sesion = ?"
    try:
        cursor = conn.execute(sql, (session_id,))
        row = cursor.fetchone()
    except sqlite3.Error as e:
        print(f"[get_session_row] Error DB: {e}")
        return None
    return _fila_a_dict(cursor, row) if row is not None else None


def sumar_penalizaciones(conn: sqlite3.Connection, session_id: int) -> float:
    """Suma de penalizaciones agregadas de la sesión (se almacenan en NEGATIVO)."""
    sql = "SELECT COALESCE(SUM(penalizaciones), 0.0) FROM ResumenEventos WHERE id_sesion = ?"
    try:
        row = conn.execute(sql, (session_id,)).fetchone()
    except sqlite3.Error as e:
        print(f"[sumar_penalizaciones] Error DB: {e}")
        return 0.0
    valor = row[0] if row else None
    return float(valor) if valor is not None else 0.0


def sumar_eventos_por_tipo(conn: sqlite3.Connection, session_id: int, tipo_evento: str) -> int:
    """Suma de ``conteo_eventos`` de un tipo de evento concreto (0 si no existe)."""
    sql = (
        "SELECT COALESCE(SUM(conteo_eventos), 0) FROM ResumenEventos "
        "WHERE id_sesion = ? AND tipo_evento = ?"
    )
    try:
        row = conn.execute(sql, (session_id, tipo_evento)).fetchone()
    except sqlite3.Error as e:
        print(f"[sumar_eventos_por_tipo] Error DB: {e}")
        return 0
    valor = row[0] if row else None
    return int(valor) if valor is not None else 0


# =============================================================================
# ESCRITURA (INGESTA DEL PIPELINE)
# =============================================================================
def get_session_id_by_filename(connection: sqlite3.Connection, filename: str) -> Optional[int]:
    sql = "SELECT id_sesion FROM Sesiones WHERE nombre_archivo_origen = ?"
    cursor = connection.cursor()
    cursor.execute(sql, (filename,))
    result = cursor.fetchone()
    return result['id_sesion'] if result else None

def check_if_file_processed(connection: sqlite3.Connection, filename: str) -> bool:
    sql = "SELECT 1 FROM Sesiones WHERE nombre_archivo_origen = ?"
    cursor = connection.cursor()
    cursor.execute(sql, (filename,))
    return cursor.fetchone() is not None

def insert_session(connection: sqlite3.Connection, parsed_data: dict, perfil_operador: str) -> Optional[int]:
    base_cols = [
        "nombre_archivo_origen", "nombre_operador", "nombre_clase", "nombre_ejercicio",
        "fecha_hora_inicio", "duracion_segundos", "puntaje_final", "perfil_operador",
    ]
    cursor = connection.cursor()
    try:
        info = parsed_data['session_data']
        values = [
            info['nombre_archivo_origen'], info['nombre_operador'], info.get('nombre_clase', 'N/A'),
            info['nombre_ejercicio'], info['fecha_hora_inicio'], info['duracion_segundos'],
            info['puntaje_final'], perfil_operador,
        ]
        cols = list(base_cols)

        # Columnas opcionales de reglas de negocio. Se incluyen SOLO si existen en
        # el esquema de la BD destino, para seguir siendo compatible con bases
        # antiguas (database/titan.db) que no las tienen.
        optional = {
            "puntaje_depurado": info.get("puntaje_depurado"),
            "checklist_completado": 1 if info.get("checklist_completado") else 0,
        }
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(Sesiones)").fetchall()}
        for col, val in optional.items():
            if col in existing and val is not None:
                cols.append(col)
                values.append(val)

        sql = f"INSERT INTO Sesiones({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})"
        cursor.execute(sql, values)
        return cursor.lastrowid
    except (sqlite3.Error, KeyError) as e:
        print(f"Error al insertar la sesión: {e}")
        connection.rollback()
        return None

def insert_summary_events(connection: sqlite3.Connection, session_id: int, summary_events: List[dict]):
    if not summary_events: return
    sql = "INSERT INTO ResumenEventos(id_sesion, tipo_evento, conteo_eventos, recompensas, penalizaciones) VALUES(?, ?, ?, ?, ?)"
    cursor = connection.cursor()
    try:
        events_to_insert = [
            (session_id, event['type'], int(event.get('total_events', 0)), float(event.get('rewards', 0.0)), float(event.get('penalties', 0.0)))
            for event in summary_events
        ]
        cursor.executemany(sql, events_to_insert)
    except (sqlite3.Error, KeyError, ValueError) as e:
        print(f"Error al insertar resumen de eventos: {e}")
        connection.rollback()

def insert_telemetry_data(connection: sqlite3.Connection, session_id: int, graph_name: str, calibrated_data: List[tuple]):
    if not calibrated_data: return
    timestamps, values = zip(*calibrated_data)
    timestamps_str = ",".join(map(lambda x: f"{x:.2f}", timestamps))
    values_str = ",".join(map(lambda x: f"{x:.2f}", values))
    sql = "INSERT INTO Telemetria(id_sesion_fk, nombre_grafico, timestamps, valores) VALUES (?, ?, ?, ?)"
    cursor = connection.cursor()
    try:
        cursor.execute(sql, (session_id, graph_name, timestamps_str, values_str))
    except sqlite3.Error as e:
        print(f"Error al insertar datos de telemetría para '{graph_name}': {e}")
        connection.rollback()
