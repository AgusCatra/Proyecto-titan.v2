# core/behavior_analyzer.py
# Versión: 6.1 (Consolidada con Orquestador y Análisis de Cobertura)

from typing import Dict, Any, List, Optional
from enum import Enum
import sqlite3
import pandas as pd
import numpy as np
import os

# --- Definición de la ruta a la base de datos ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'titan.db')


class BehaviorProfile(Enum):
    """Define los posibles perfiles de comportamiento del operador."""
    EFICIENTE = "eficiente"
    APURADO = "apurado"
    SIN_NOCION_ESPACIO = "sin_nocion_espacio"
    INEFICIENTE = "ineficiente"
    NOVATO = "novato"

# --- CLASIFICADOR DE PERFIL BASADO EN REGLAS ---

class BehaviorAnalyzer:
    """Analizador de comportamiento para clasificar operadores (basado en reglas de alto nivel)."""
    def __init__(self):
        self.profile_thresholds = {
            'score_threshold': 75.0,
            'duration_threshold': 300,
            'collision_threshold': 3,
            'error_threshold': 5
        }

    def analyze_session(self, session_data: Dict, summary_events: List[Dict]) -> BehaviorProfile:
        metrics = self._extract_metrics(session_data, summary_events)
        return self._classify_behavior(metrics)

    def _extract_metrics(self, session_data: Dict, summary_events: List[Dict]) -> Dict:
        metrics = {
            'score': session_data.get('puntaje_final', 0),
            'duration': session_data.get('duracion_segundos', 0),
            'total_penalties': sum(float(event.get('penalties', 0)) for event in summary_events),
            'total_rewards': sum(float(event.get('rewards', 0)) for event in summary_events),
            'collision_count': self._count_events_by_type(summary_events, 'Collision'),
            'error_count': self._count_events_by_type(summary_events, 'Error')
        }
        return metrics

    def _count_events_by_type(self, summary_events: List[Dict], event_type: str) -> int:
        for event in summary_events:
            if event_type.lower() in event.get('type', '').lower():
                return int(event.get('total_events', 0))
        return 0

    def _classify_behavior(self, metrics: Dict) -> BehaviorProfile:
        if metrics['score'] >= 85 and metrics['total_penalties'] <= 2:
            return BehaviorProfile.EFICIENTE
        elif metrics['duration'] < 180 and metrics['total_penalties'] > 5:
            return BehaviorProfile.APURADO
        elif metrics['collision_count'] >= 3:
            return BehaviorProfile.SIN_NOCION_ESPACIO
        elif metrics['duration'] > 600 and metrics['score'] < 60:
            return BehaviorProfile.INEFICIENTE
        else:
            return BehaviorProfile.NOVATO

# --- ANÁLISIS DETALLADO DE TELEMETRÍA ---

def _get_telemetry_df(id_sesion: int, graph_name: str, col_name: str) -> Optional[pd.DataFrame]:
    """Función auxiliar para obtener y preparar un DataFrame de telemetría."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # Asegúrate de que el nombre de la columna id_sesion_fk sea correcto para tu esquema
            query = "SELECT timestamps, valores FROM Telemetria WHERE id_sesion_fk = ? AND nombre_grafico = ?"
            result = conn.execute(query, (id_sesion, graph_name)).fetchone()
    except sqlite3.Error as e:
        print(f"  - ERROR de base de datos para '{graph_name}': {e}")
        return None

    if not result or not result[0] or not result[1]:
        # Se comenta para no llenar la consola, pero es útil para depurar
        # print(f"  - No se encontraron datos o los datos están vacíos para '{graph_name}' en la sesión {id_sesion}.")
        return None

    try:
        timestamps = [float(t) for t in result[0].split(',')]
        valores = [float(v) for v in result[1].split(',')]
        return pd.DataFrame({'tiempo': timestamps, col_name: valores}).sort_values(by='tiempo').reset_index(drop=True)
    except (ValueError, IndexError) as e:
        print(f"  - ERROR al procesar datos de '{graph_name}': {e}")
        return None

def analizar_frenado(id_sesion: int) -> int:
    """Analiza la telemetría de frenado para contar "frenadas bruscas"."""
    df = _get_telemetry_df(id_sesion, "Brake Pad", "freno")
    if df is None or df.empty: return 0
    df['tasa_cambio'] = df['freno'].diff() / df['tiempo'].diff()
    count = (df['tasa_cambio'] > 5.0).sum()
    return int(count)

def analizar_direccion(id_sesion: int) -> int:
    """Analiza la telemetría de la dirección para contar "volantazos"."""
    df = _get_telemetry_df(id_sesion, "Steering", "direccion")
    if df is None or df.empty: return 0
    df['tasa_cambio'] = df['direccion'].diff().abs() / df['tiempo'].diff()
    count = (df['tasa_cambio'] > 15.0).sum()
    return int(count)

def analizar_aceleracion(id_sesion: int) -> int:
    """Analiza la telemetría de aceleración para contar "aceleraciones bruscas"."""
    df = _get_telemetry_df(id_sesion, "Acceleration Pad", "acelerador")
    if df is None or df.empty: return 0
    df['tasa_cambio'] = df['acelerador'].diff() / df['tiempo'].diff()
    count = (df['tasa_cambio'] > 6.0).sum()
    return int(count)

def analizar_elevacion(id_sesion: int) -> int:
    """Analiza la telemetría de la horquilla para contar "micro-ajustes"."""
    df = _get_telemetry_df(id_sesion, "Fork Height In Mtrs", "altura")
    if df is None or df.empty: return 0
    df['velocidad'] = df['altura'].diff()
    df['signo_velocidad'] = np.sign(df['velocidad'])
    count = (df['signo_velocidad'].diff().abs() == 2).sum()
    return int(count)

def analizar_inclinacion(id_sesion: int) -> int:
    """Analiza la telemetría de inclinación para contar "micro-ajustes"."""
    df = _get_telemetry_df(id_sesion, "Tilt Angle In Deg", "inclinacion")
    if df is None or df.empty: return 0
    df['velocidad'] = df['inclinacion'].diff()
    df['signo_velocidad'] = np.sign(df['velocidad'])
    count = (df['signo_velocidad'].diff().abs() == 2).sum()
    return int(count)

def analizar_velocidad(id_sesion: int) -> Dict[str, float]:
    """Calcula métricas clave de la telemetría de velocidad."""
    df = _get_telemetry_df(id_sesion, "Speed In Km/h", "velocidad")
    if df is None or df.empty:
        return {"velocidad_media": 0.0, "velocidad_maxima": 0.0, "incidentes_de_velocidad": 0.0}
    stats = {
        "velocidad_media": df['velocidad'].mean(),
        "velocidad_maxima": df['velocidad'].max(),
        "incidentes_de_velocidad": float((df['velocidad'] > 8.0).sum())
    }
    return stats

# --- FUNCIÓN PRINCIPAL ORQUESTADORA (INTEGRADA) ---
def analizar_comportamiento_completo(id_sesion: int) -> Dict[str, Any]:
    """
    Ejecuta todos los análisis de telemetría para una sesión y devuelve
    un diccionario consolidado con los resultados.
    """
    print(f"\n--- Ejecutando análisis de telemetría completo (Sesión ID: {id_sesion}) ---")
    
    analisis = {
        "frenadas_bruscas": analizar_frenado(id_sesion),
        "volantazos": analizar_direccion(id_sesion),
        "aceleraciones_bruscas": analizar_aceleracion(id_sesion),
        "ajustes_elevacion": analizar_elevacion(id_sesion),
        "ajustes_inclinacion": analizar_inclinacion(id_sesion),
        "metricas_velocidad": analizar_velocidad(id_sesion)
    }
    
    print("--- Análisis de telemetría finalizado. ---")
    return analisis

# --- ANÁLISIS DE CALIDAD DE DATOS ---
def analizar_cobertura_telemetria(id_sesion: int, duracion_total: int) -> Dict[str, float]:
    """
    Analiza la completitud de los datos de telemetría para una sesión.
    """
    print(f"\n--- Analizando COBERTURA DE TELEMETRÍA (Sesión ID: {id_sesion}) ---")
    cobertura = {}
    
    if duracion_total == 0:
        return {}

    try:
        with sqlite3.connect(DB_PATH) as conn:
            query = "SELECT nombre_grafico, timestamps FROM Telemetria WHERE id_sesion_fk = ?"
            results = conn.execute(query, (id_sesion,)).fetchall()
    except sqlite3.Error as e:
        print(f"  - ERROR de base de datos: {e}")
        return {}

    if not results:
        print("  - No se encontraron datos de telemetría para esta sesión.")
        return {}

    for nombre_grafico, timestamps_str in results:
        try:
            timestamps = [float(t) for t in timestamps_str.split(',')]
            if timestamps:
                duracion_cubierta = timestamps[-1]
                porcentaje_cobertura = (duracion_cubierta / duracion_total) * 100
                cobertura[nombre_grafico] = min(100.0, porcentaje_cobertura)
            else:
                cobertura[nombre_grafico] = 0.0
        except (ValueError, IndexError):
            cobertura[nombre_grafico] = 0.0
    
    print("  - Análisis de cobertura completado.")
    return cobertura


if __name__ == '__main__':
    print("Ejecutando suite de pruebas del módulo behavior_analyzer...")
    id_sesion_de_prueba = 1 
    duracion_sesion_prueba = 476 # Duración ejemplo para el 'report test.pdf'
    
    if os.path.exists(DB_PATH):
        # 1. Prueba del orquestador de análisis de telemetría
        resultados_completos = analizar_comportamiento_completo(id_sesion_de_prueba)
        print("\n--- Resultados Consolidados de Telemetría ---")
        for metrica, valor in resultados_completos.items():
            if isinstance(valor, dict):
                print(f"  - {metrica}:")
                for sub_metrica, sub_valor in valor.items():
                    print(f"    - {sub_metrica}: {sub_valor:.2f}")
            else:
                print(f"  - {metrica}: {valor}")
        
        # 2. Prueba del análisis de cobertura de datos
        resultados_cobertura = analizar_cobertura_telemetria(id_sesion_de_prueba, duracion_sesion_prueba)
        print("\n--- Resultados de Cobertura de Datos ---")
        if resultados_cobertura:
            for grafico, porcentaje in resultados_cobertura.items():
                print(f"  - {grafico}: {porcentaje:.2f}%")
        else:
            print("  - No se pudo calcular la cobertura.")

    else:
        print(f"La base de datos no se encontró en: {DB_PATH}")
