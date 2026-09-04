import traceback
import streamlit as st
import os
import sys
import tempfile
import sqlite3
import io
from typing import Optional, Dict, Any, List, Tuple

# --- Rutas del proyecto ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- Importaciones ---
from core.analizador_eventos import generar_feedback, extraer_eventos_crudos
from core.pipeline import process_simulator_pdf
from core.db_manager import get_db_connection, get_telemetry_for_graph
from core.reporter import generar_texto_reporte_individual, generar_reporte_evolucion
from core.behavior_analyzer import analizar_comportamiento_completo
from core.report_generator import crear_reporte_pdf, crear_reporte_evolucion_pdf
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

def _procesar_reporte(pdf_path: str) -> Optional[int]:
    """Adapter fino de UI: delega 100% en ``core.pipeline.process_simulator_pdf``.

    Toda la lógica ETL (parseo, perfil, telemetría, transacción y rollback) vive
    en el pipeline headless. Aquí únicamente se traducen a la UI los resultados y
    los errores controlados que devuelve el pipeline.
    """
    resultado = process_simulator_pdf(
        pdf_path,
        profile_source="model",
        db_path=DB_PATH,
        models_path=MODELS_PATH,
        exports_dir=EXPORTS_DIR,
    )

    for err in resultado.get("errors", []):
        st.error(f"⚠️ {err}")

    if resultado.get("cached"):
        st.info("ℹ️ Este reporte ya estaba procesado; se reutilizó la sesión existente.")
    elif resultado.get("telemetry_loaded"):
        total = sum(resultado["telemetry_loaded"].values())
        st.success(
            f"✅ Telemetría guardada: {len(resultado['telemetry_loaded'])} señal(es), {total} puntos."
        )

    return resultado.get("session_id")

def _plot_telemetry_chart(datos: List[Tuple], titulo: str):
    """Dibuja un gráfico bonito en Streamlit."""
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
st.title("🚀 Proyecto Titán v2.0 - Interfaz Web")

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
        session_id = _procesar_reporte(tmp_path)
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

        # --- Devolución inteligente (basada en eventos crudos) ---
        eventos_crudos = extraer_eventos_crudos(tmp_path)
        if eventos_crudos:
            st.subheader("🤖 Devolución inteligente")
            st.markdown(generar_feedback(eventos_crudos))
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
            id_inicial = _procesar_reporte(tmp1_path)
            id_final = _procesar_reporte(tmp2_path)
            if id_inicial and id_final:
                reporte_comp = generar_reporte_evolucion(id_inicial, id_final)
                st.subheader("📊 Comparación de reportes")
                st.code(reporte_comp)
        finally:
            os.remove(tmp1_path); os.remove(tmp2_path)
