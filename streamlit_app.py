import traceback
import streamlit as st
import os
import sys
import shutil
import tempfile
import sqlite3
import io
from typing import Optional, Dict, Any, List, Tuple

# --- Rutas del proyecto ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- Importaciones ---
from core.pdf_parser import parse_pdf_report
from core.telemetry_extractor import extraer_telemetria_visual
from core.db_manager import (
    get_db_connection, insert_session, insert_summary_events,
    get_session_id_by_filename, insert_telemetry_data, get_telemetry_for_graph
)
from core.reporter import generar_texto_reporte_individual, generar_reporte_evolucion
from core.behavior_analyzer import analizar_comportamiento_completo
from core.report_generator import crear_reporte_pdf, crear_reporte_evolucion_pdf
import joblib
import pandas as pd
import matplotlib.pyplot as plt

# --- Configuración ---
DB_PATH = os.path.join(project_root, 'database', 'titan.db')
MODELS_PATH = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)

GRAFICOS_DISPONIBLES = [
    'Steering', 'Speed In Km/h', 'Brake Pad',
    'Acceleration Pad', 'Fork Height In Mtrs', 'Tilt Angle In Deg'
]
RUTAS_DE_APRENDIZAJE = {
    "Novato": {"titulo": "Ruta de Iniciación", "ejercicios": ["1.1. Controles", "2.1. Conducción básica"]},
    "Sin nocion del espacio": {"titulo": "Ruta de Precisión Espacial", "ejercicios": ["2.2. Curvas en S", "5.7. Carga Vertical"]},
    "Apurado": {"titulo": "Ruta de Control de Impulsos", "ejercicios": ["Módulo 4 (Apilamiento)", "7.1. Operación con Señales"]},
    "Ineficiente": {"titulo": "Ruta de Productividad", "ejercicios": ["Módulo 6 (Estanterías)"]},
    "Eficiente": {"titulo": "Ruta de Especialización", "ejercicios": ["Módulo 8 (Cargas Pesadas)"]}
}

# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================

def _preparar_datos_para_prediccion(parsed_data, feature_names: List[str]):
    """Convierte datos parseados en DataFrame compatible con el modelo."""
    data = {
        'puntaje_final': parsed_data['session_data'].get('puntaje_final', 0.0),
        'duracion_segundos': parsed_data['session_data'].get('duracion_segundos', 0)
    }
    for event in parsed_data.get('summary_events', []):
        ev_type = event.get('type', '').replace(' ', '_')
        if ev_type:
            data[f"conteo_eventos_{ev_type}"] = float(event.get('total_events', 0))
            data[f"penalizaciones_{ev_type}"] = float(event.get('penalties', 0))

    df = pd.DataFrame([data], columns=feature_names).fillna(0)
    return df

def _procesar_y_obtener_id(pdf_path: str) -> Optional[int]:
    """Procesa el PDF, extrae datos + telemetría y guarda todo en BD."""
    file_name = os.path.basename(pdf_path)
    with get_db_connection(DB_PATH) as conn:
        session_id = get_session_id_by_filename(conn, file_name)
        if session_id:
            return session_id

        parsed_data = parse_pdf_report(pdf_path)
        if not parsed_data:
            st.error("❌ No se pudieron extraer datos del PDF.")
            return None

        # Cargar modelo
        try:
            modelo = joblib.load(MODELS_PATH)
        except FileNotFoundError:
            st.error(f"⚠️ Modelo no encontrado en '{MODELS_PATH}'.")
            return None

        df_pred = _preparar_datos_para_prediccion(parsed_data, getattr(modelo, "feature_names_in_", []))
        perfil = modelo.predict(df_pred)[0]

        # Guardar en BD
        conn.execute("BEGIN")
        new_id = insert_session(conn, parsed_data, perfil)
        if not new_id:
            conn.rollback()
            return None

        insert_summary_events(conn, new_id, parsed_data['summary_events'])

        # Telemetría
        telemetria = extraer_telemetria_visual(pdf_path, parsed_data['session_data']['duracion_segundos'])
        for nombre, datos in telemetria.items():
            if datos:
                insert_telemetry_data(conn, new_id, nombre, datos)

        conn.commit()
        shutil.copy2(pdf_path, os.path.join(EXPORTS_DIR, file_name))
        return new_id

def _plot_telemetry_chart(datos: List[Tuple], titulo: str):
    if not datos:
        st.warning(f"No hay datos para '{titulo}'.")
        return
    try:
        timestamps, valores = zip(*datos)
    except Exception as e:
        st.error(f"Error procesando datos de '{titulo}': {e}")
        return

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(timestamps, valores, color='#4A90E2', linewidth=1.5)
    ax.set_title(titulo, color='white', fontsize=14)
    ax.set_xlabel('Tiempo (s)', color='gray')
    ax.set_ylabel('Valor', color='gray')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.tick_params(axis='both', colors='gray', labelsize=8)
    st.pyplot(fig)
    plt.close(fig)

# =============================================================================
# INTERFAZ STREAMLIT
# =============================================================================

st.set_page_config(page_title="Proyecto Titán v2.0", page_icon="🤖", layout="wide")
st.title("Proyecto Titán v2.0 - Interfaz Web")

with st.sidebar:
    st.header("Menú")
    individual_file = st.file_uploader("📂 Reporte individual", type=['pdf'])
    st.markdown("---")
    initial_file = st.file_uploader("📂 Reporte inicial", type=['pdf'])
    final_file = st.file_uploader("📂 Reporte final", type=['pdf'])
    compare_button = st.button("🔍 Comparar reportes")

# --- Análisis Individual ---
if individual_file:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(individual_file.getvalue())
        tmp_path = tmp.name
    try:
        session_id = _procesar_y_obtener_id(tmp_path)
        if session_id:
            with get_db_connection(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                info_sesion = dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (session_id,)).fetchone())
            analisis_comportamiento = analizar_comportamiento_completo(session_id)

            st.subheader("📑 Datos de la sesión")
            st.json(info_sesion)

            st.subheader("📋 Reporte en texto")
            st.code(generar_texto_reporte_individual({
                "info_sesion": info_sesion,
                "perfil_predicho": info_sesion.get('perfil_operador'),
                "ruta_recomendada": RUTAS_DE_APRENDIZAJE.get(info_sesion.get('perfil_operador')),
                "analisis_comportamiento": analisis_comportamiento
            }))

            st.subheader("📊 Gráficos de telemetría")
            for g in GRAFICOS_DISPONIBLES:
                datos_grafico = get_telemetry_for_graph(session_id, g)
                _plot_telemetry_chart(datos_grafico, g)
    finally:
        os.remove(tmp_path)

# --- Comparación de reportes ---
elif compare_button:
    if initial_file and final_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp1, \
             tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp2:
            tmp1.write(initial_file.getvalue()); tmp1_path = tmp1.name
            tmp2.write(final_file.getvalue()); tmp2_path = tmp2.name
        try:
            id_inicial = _procesar_y_obtener_id(tmp1_path)
            id_final = _procesar_y_obtener_id(tmp2_path)
            if id_inicial and id_final:
                reporte_comp = generar_reporte_evolucion(id_inicial, id_final)
                st.subheader("📊 Comparación de reportes")
                st.code(reporte_comp)
        finally:
            os.remove(tmp1_path); os.remove(tmp2_path)
