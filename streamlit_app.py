import streamlit as st
import os
import sys
import shutil
import tempfile
import sqlite3
import io
from typing import Optional, Dict, Any, List, Tuple

# --- Añadir la ruta del proyecto al sys.path ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- Importaciones de los módulos del backend ---
from core.pdf_parser import parse_pdf_report
from core.db_manager import (
    get_db_connection, insert_session, insert_summary_events,
    get_session_id_by_filename, insert_telemetry_data, get_telemetry_for_graph
)
from core.telemetry_parser import extraer_toda_la_telemetria
from core.reporter import generar_texto_reporte_individual, generar_reporte_evolucion
from core.behavior_analyzer import analizar_comportamiento_completo
from core.report_generator import crear_reporte_pdf, crear_reporte_evolucion_pdf
import joblib
import pandas as pd
import matplotlib.pyplot as plt

# --- Definición de Rutas Clave y Configuración ---
DB_PATH = os.path.join(project_root, 'database', 'titan.db')
MODELS_PATH = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)

# --- Listas y Diccionarios Constantes ---
GRAFICOS_DISPONIBLES = ['Steering', 'Speed In Km/h', 'Brake Pad', 'Acceleration Pad', 'Fork Height In Mtrs', 'Tilt Angle In Deg']
RUTAS_DE_APRENDIZAJE = {
    "Novato": {"titulo": "Ruta de Iniciación", "ejercicios": ["1.1. Controles", "2.1. Conducción básica"]},
    "Sin nocion del espacio": {"titulo": "Ruta de Precisión Espacial", "ejercicios": ["2.2. Curvas en S", "5.7. Carga Vertical"]},
    "Apurado": {"titulo": "Ruta de Control de Impulsos", "ejercicios": ["Módulo 4 (Apilamiento)", "7.1. Operación con Señales"]},
    "Ineficiente": {"titulo": "Ruta de Productividad", "ejercicios": ["Módulo 6 (Estanterías)"]},
    "Eficiente": {"titulo": "Ruta de Especialización", "ejercicios": ["Módulo 8 (Cargas Pesadas)"]}
}

# =============================================================================
# FUNCIONES AUXILIARES DEL BACKEND
# =============================================================================

def _preparar_datos_para_prediccion(parsed_data, feature_names):
    data = {'puntaje_final': parsed_data['session_data']['puntaje_final'], 'duracion_segundos': parsed_data['session_data']['duracion_segundos']}
    for event in parsed_data['summary_events']:
        ev_type = event['type'].replace(' ', '_')
        data[f"conteo_eventos_{ev_type}"] = float(event.get('total_events', 0))
        data[f"penalizaciones_{ev_type}"] = float(event.get('penalties', 0))
    if len(feature_names) == 0:
        return pd.DataFrame([data]).fillna(0)
    df = pd.DataFrame(columns=feature_names)
    df.loc[0] = 0
    for key, value in data.items():
        if key in df.columns:
            df.at[0, key] = value
    return df.fillna(0)

def _procesar_y_obtener_id(pdf_path: str) -> Optional[int]:
    file_name = os.path.basename(pdf_path)
    with get_db_connection(DB_PATH) as conn:
        session_id = get_session_id_by_filename(conn, file_name)
        if session_id:
            return session_id
        parsed_data = parse_pdf_report(pdf_path)
        if not parsed_data:
            st.error("No se pudieron extraer datos del PDF.")
            return None
        try:
            modelo = joblib.load(MODELS_PATH)
        except FileNotFoundError:
            st.error(f"Error Crítico: No se encontró el archivo del modelo en '{MODELS_PATH}'.")
            return None
        df_pred = _preparar_datos_para_prediccion(parsed_data, getattr(modelo, "feature_names_in_", []))
        perfil = modelo.predict(df_pred)[0]
        conn.execute("BEGIN")
        new_id = insert_session(conn, parsed_data, perfil)
        if not new_id:
            conn.rollback(); return None
        insert_summary_events(conn, new_id, parsed_data['summary_events'])
        telemetria = extraer_toda_la_telemetria(pdf_path, parsed_data['session_data']['duracion_segundos'])
        for nombre, datos in telemetria.items():
            if datos: insert_telemetry_data(conn, new_id, nombre, datos)
        conn.commit()
        shutil.copy2(pdf_path, os.path.join(EXPORTS_DIR, file_name))
        return new_id

def generar_pdf_en_memoria(datos_analisis: Dict[str, Any]) -> Optional[bytes]:
    buffer = io.BytesIO()
    try:
        crear_reporte_pdf(datos_analisis, buffer)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        st.error(f"Ocurrió un error al generar el PDF individual: {e}"); return None

def generar_evolucion_pdf_en_memoria(id_inicial: int, id_final: int) -> Optional[bytes]:
    buffer = io.BytesIO()
    try:
        with get_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            datos_iniciales = dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (id_inicial,)).fetchone())
            datos_finales = dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (id_final,)).fetchone())
            datos_iniciales['penalizaciones_totales'] = sum(row['penalties'] for row in conn.execute("SELECT penalties FROM ResumenEventos WHERE id_sesion = ?", (id_inicial,)).fetchall())
            datos_finales['penalizaciones_totales'] = sum(row['penalties'] for row in conn.execute("SELECT penalties FROM ResumenEventos WHERE id_sesion = ?", (id_final,)).fetchall())
        crear_reporte_evolucion_pdf(datos_iniciales, datos_finales, buffer)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        st.error(f"Ocurrió un error al generar el PDF de evolución: {e}"); return None

def _plot_telemetry_chart(datos: Optional[Tuple[List[float], List[float]]], titulo: str):
    """Genera un gráfico de Matplotlib para la telemetría y lo muestra en Streamlit."""
    if not datos:
        st.info(f"No hay datos de telemetría disponibles para '{titulo}'.")
        return

    try:
        timestamps, valores = datos  # datos debe ser (timestamps, valores)
    except Exception as e:
        st.error(f"Formato inesperado de datos para '{titulo}': {e}")
        return

    # --- Creación del Gráfico con Matplotlib ---
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor('#0E1117')

    ax.plot(timestamps, valores, color='#4A90E2', linewidth=1.5)

    # --- Personalización del Gráfico ---
    ax.set_title(titulo, color='white', fontsize=16)
    ax.set_xlabel('Tiempo (segundos)', color='gray', fontsize=10)
    ax.set_ylabel('Valor', color='gray', fontsize=10)
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.tick_params(axis='both', colors='gray', labelsize=8)
    ax.set_facecolor('#262730')

    # Mostrar en Streamlit
    st.pyplot(fig)
    plt.close(fig)


# =============================================================================
# INTERFAZ PRINCIPAL DE STREAMLIT
# =============================================================================

st.set_page_config(page_title="Proyecto Titán v2.0", page_icon="🤖", layout="wide")
st.title("Proyecto Titán v2.0 - Interfaz Web")

# --- Lógica para limpiar el estado al subir un nuevo archivo ---
def clear_analysis_state():
    for key in ['analysis_complete', 'report_data', 'session_id', 'report_text', 'file_name']:
        if key in st.session_state:
            del st.session_state[key]

with st.sidebar:
    st.header("Herramientas de Análisis")
    st.subheader("Análisis Individual")
    individual_file = st.file_uploader("Selecciona un Reporte PDF", type=['pdf'], key="individual_uploader", on_change=clear_analysis_state)
    st.markdown("---")
    st.subheader("Reporte de Evolución")
    initial_file = st.file_uploader("Reporte INICIAL", type=['pdf'], key="initial_uploader")
    final_file = st.file_uploader("Reporte FINAL", type=['pdf'], key="final_uploader")
    compare_button = st.button("Generar Reporte de Evolución")

# --- LÓGICA PRINCIPAL ---

# 1. EJECUTAR ANÁLISIS INDIVIDUAL (SOLO SI HAY ARCHIVO Y NO SE HA COMPLETADO)
if individual_file and not st.session_state.get('analysis_complete'):
    with st.spinner('Analizando reporte individual...'):
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
                
                # Guardar resultados en el estado de sesión
                st.session_state.report_data = {
                    "info_sesion": info_sesion,
                    "perfil_predicho": info_sesion.get('perfil_operador'),
                    "ruta_recomendada": RUTAS_DE_APRENDIZAJE.get(info_sesion.get('perfil_operador')),
                    "analisis_comportamiento": analisis_comportamiento
                }
                st.session_state.report_text = generar_texto_reporte_individual(st.session_state.report_data)
                st.session_state.session_id = session_id
                st.session_state.file_name = individual_file.name
                st.session_state.analysis_complete = True
        finally:
            os.remove(tmp_path)
            st.rerun() # Forzar un re-run para mostrar los resultados inmediatamente

# 2. MOSTRAR RESULTADOS DE ANÁLISIS INDIVIDUAL (SI ESTÁ COMPLETO)
elif st.session_state.get('analysis_complete'):
    st.subheader(f"Resultado del Análisis: {st.session_state.file_name}")
    st.code(st.session_state.report_text, language=None)
    
    pdf_bytes = generar_pdf_en_memoria(st.session_state.report_data)
    if pdf_bytes:
        info = st.session_state.report_data['info_sesion']
        st.download_button(label="⬇️ Descargar Reporte en PDF", data=pdf_bytes, file_name=f"Reporte_{info.get('nombre_operador', 'Operador').replace(' ', '_')}_S{st.session_state.session_id}.pdf", mime="application/pdf")

    st.markdown("---")
    st.subheader("Visualizador de Telemetría")
    
    selected_chart = st.selectbox("Selecciona un gráfico para visualizar:", GRAFICOS_DISPONIBLES)
    if selected_chart:
        datos_grafico = get_telemetry_for_graph(st.session_state.session_id, selected_chart)
        _plot_telemetry_chart(datos_grafico, selected_chart)

# 3. EJECUTAR Y MOSTRAR REPORTE DE EVOLUCIÓN
elif compare_button:
    if initial_file and final_file:
        with st.spinner('Procesando y comparando reportes...'):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_initial, \
                 tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_final:
                tmp_initial.write(initial_file.getvalue())
                tmp_initial_path = tmp_initial.name
                tmp_final.write(final_file.getvalue())
                tmp_final_path = tmp_final.name
            try:
                id_inicial = _procesar_y_obtener_id(tmp_initial_path)
                id_final = _procesar_y_obtener_id(tmp_final_path)
                if id_inicial and id_final:
                    with get_db_connection(DB_PATH) as conn:
                        res = conn.execute(f"SELECT id_sesion FROM Sesiones WHERE id_sesion IN (?, ?) ORDER BY fecha_hora_inicio", (id_inicial, id_final)).fetchall()
                        id_inicial_s, id_final_s = res[0][0], res[1][0]
                    reporte_comp = generar_reporte_evolucion(id_inicial_s, id_final_s)
                    st.subheader("Resultado del Reporte de Evolución")
                    st.code(reporte_comp, language=None)
                    
                    pdf_bytes_evolucion = generar_evolucion_pdf_en_memoria(id_inicial_s, id_final_s)
                    if pdf_bytes_evolucion:
                        with get_db_connection(DB_PATH) as conn:
                             nombre_op = conn.execute("SELECT nombre_operador FROM Sesiones WHERE id_sesion = ?", (id_inicial_s,)).fetchone()[0]
                        st.download_button(label="⬇️ Descargar Reporte de Evolución en PDF", data=pdf_bytes_evolucion, file_name=f"Evolucion_{nombre_op.replace(' ', '_')}_S{id_inicial_s}_vs_S{id_final_s}.pdf", mime="application/pdf")
            finally:
                os.remove(tmp_initial_path)
                os.remove(tmp_final_path)
    else:
        st.warning("Por favor, cargue ambos reportes (Inicial y Final) para generar la comparación.")

# 4. PANTALLA DE INICIO
else:
    st.info("Seleccione una opción de la barra lateral para comenzar.")

