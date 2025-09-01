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
def get_telemetry_for_graph(id_sesion: int, graph_name: str) -> Optional[Tuple[List[float], List[float]]]:
    """
    Obtiene los datos de una serie de telemetría específica desde la BD.
    
    Args:
        id_sesion: El ID de la sesión a consultar.
        graph_name: El nombre del gráfico (ej: "Steering").
        
    Returns:
        Una tupla con dos listas (timestamps, valores), o None si no se encuentran datos.
    """
    try:
        # Usamos la ruta global a la BD
        with create_connection(DB_PATH) as conn:
            # CORRECCIÓN 1: Usar los nombres de columna correctos ('timestamps', 'valores')
            query = "SELECT timestamps, valores FROM Telemetria WHERE id_sesion_fk = ? AND nombre_grafico = ?"
            result = conn.execute(query, (id_sesion, graph_name)).fetchone()
            
            if not result:
                return None
            
            # CORRECIÓN 2: Procesar los strings en Python para convertirlos en listas
            timestamps = [float(t) for t in result['timestamps'].split(',')]
            valores = [float(v) for v in result['valores'].split(',')]
            
            return (timestamps, valores)
            
    except (sqlite3.Error, ValueError) as e:
        print(f"Error al obtener datos de telemetría para '{graph_name}': {e}")
        return None

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
    sql = """INSERT INTO Sesiones(
        nombre_archivo_origen, nombre_operador, nombre_clase, nombre_ejercicio,
        fecha_hora_inicio, duracion_segundos, puntaje_final, perfil_operador
    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)"""
    cursor = connection.cursor()
    try:
        info = parsed_data['session_data']
        data_tuple = (
            info['nombre_archivo_origen'], info['nombre_operador'], info.get('nombre_clase', 'N/A'),
            info['nombre_ejercicio'], info['fecha_hora_inicio'], info['duracion_segundos'], 
            info['puntaje_final'], perfil_operador
        )
        cursor.execute(sql, data_tuple)
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
