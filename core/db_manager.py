# core/db_manager.py
# Versión: 5.0 - Corregida la función para obtener telemetría para gráficos.

import sqlite3
import os
from typing import Optional, List, Tuple, Dict
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

# --- FUNCIÓN CORREGIDA Y ACTUALIZADA ---
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

# (Aquí debe estar el resto del código de db_manager.py, como insert_session, etc.)
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
