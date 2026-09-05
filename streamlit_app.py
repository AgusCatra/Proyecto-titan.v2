# streamlit_app.py
# Proyecto Titán v2.3 — Interfaz web definitiva (Streamlit).
#
# Dos pestañas de trabajo (Diagnóstico de Admisión y Comparativa Delta),
# persistencia con st.session_state, caché de cálculos pesados y gráficos
# interactivos con ALTAIR (Plotly NO está instalado en el entorno).
#
# La lógica de evaluación vive en core.evaluador_diagnostico (lógica pura); esta
# capa solo orquesta la UI, la ingesta de PDFs y la visualización.

import html
import io
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import altair as alt
import pandas as pd
import streamlit as st

# --- Rutas del proyecto ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- Importaciones del núcleo ---
from core.analizador_eventos import generar_feedback, extraer_eventos_crudos
from core.pipeline import process_simulator_pdf
from core.db_manager import get_db_connection
from core.reporter import generar_texto_reporte_individual, generar_reporte_evolucion
from core.behavior_analyzer import analizar_comportamiento_completo
# Import DEFENSIVO: core.report_generator depende de 'fpdf' (fpdf2), que puede no
# estar instalado en el entorno. Si falta, la app DEBE seguir arrancando y solo se
# deshabilita la exportación a PDF, nunca se cae con ModuleNotFoundError.
try:
    from core.report_generator import crear_reporte_pdf, crear_reporte_evolucion_pdf
    PDF_EXPORT_AVAILABLE = True
except ImportError:
    crear_reporte_pdf = None
    crear_reporte_evolucion_pdf = None
    PDF_EXPORT_AVAILABLE = False
from core.evaluador_diagnostico import (
    evaluar_diagnostico_inicial,
    comparar_sesiones_delta,
    guardar_decision_instructor,
    obtener_decision_instructor,
)

# =============================================================================
# CONFIGURACIÓN Y CONSTANTES
# =============================================================================
DB_PATH = os.path.join(project_root, 'database', 'titan.db')
MODELS_PATH = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Las 6 señales canónicas (nombres EXACTOS de core.graph_mapper.CANONICAL_SIGNALS).
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

# --- Paleta canónica (theme de app.py) ---
COLOR_ACCENT = "#4A90E2"   # azul
COLOR_SUCCESS = "#00A67E"  # verde
COLOR_WARNING = "#F5A524"  # naranja
COLOR_DANGER = "#EF4444"   # rojo

_COLORES_DICTAMEN = {
    "Apto": COLOR_SUCCESS,
    "Observado": COLOR_WARNING,
    "No Apto Crítico": COLOR_DANGER,
}
_ICONOS_DICTAMEN = {
    "Apto": "✅",
    "Observado": "⚠️",
    "No Apto Crítico": "⛔",
}
_COLORES_EVOLUCION = {
    "MEJORA SIGNIFICATIVA": COLOR_SUCCESS,
    "MEJORA MODERADA": COLOR_SUCCESS,
    "REGRESIÓN DETECTADA": COLOR_DANGER,
    "RENDIMIENTO ESTANCADO": COLOR_WARNING,
    "SIN DATOS": COLOR_DANGER,
}

# Criterio de color de los deltas en st.metric (documentado):
#   * Colisiones / Frenadas / Volantazos / Suavidad -> una REDUCCIÓN es MEJORA
#     (delta_color="inverse").
#   * Puntaje depurado -> un AUMENTO es MEJORA (delta_color="normal").
#   * Duración -> completar el ejercicio en MENOS tiempo se interpreta como mayor
#     eficiencia/fluidez, por lo que una REDUCCIÓN es MEJORA (delta_color="inverse").


# =============================================================================
# ADAPTER DE INGESTA (se conserva y reutiliza)
# =============================================================================
def _procesar_reporte(pdf_path: str) -> Optional[int]:
    """Adapter fino de UI: delega 100% en ``core.pipeline.process_simulator_pdf``."""
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


# =============================================================================
# HELPERS DE DATOS / SANEADO (S4)
# =============================================================================
def _sanear_info_sesion(info: Optional[dict]) -> Dict[str, Any]:
    """Sanea una fila de ``Sesiones`` para que la exportación PDF no falle (S4).

    Convierte numéricos ``None`` -> 0 y textos ``None`` -> 'N/A', evitando
    ``AttributeError`` (``perfil.upper()``) y errores de formato (``:.2f``) cuando
    la BD tiene NULLs.
    """
    saneado: Dict[str, Any] = dict(info) if info else {}
    for clave_num in ("puntaje_final", "duracion_segundos", "puntaje_depurado"):
        if saneado.get(clave_num) is None:
            saneado[clave_num] = 0
    for clave_txt in ("perfil_operador", "nombre_operador", "nombre_clase",
                      "nombre_ejercicio"):
        if saneado.get(clave_txt) is None:
            saneado[clave_txt] = "N/A"
    return saneado


def _delta_str(valor_abs: str, pct: Optional[float]) -> str:
    """Formatea el delta de un st.metric mostrando 'n/a' si el pct es None (C6)."""
    return f"{valor_abs} ({pct:+.1f}%)" if pct is not None else f"{valor_abs} (n/a)"


# =============================================================================
# CÁLCULOS CACHEADOS (S6) — la conn NO es hashable, se abre dentro
# =============================================================================
@st.cache_data(show_spinner=False)
def _diagnostico_cacheado(session_id: int) -> Dict[str, Any]:
    """Diagnóstico de admisión cacheado por session_id."""
    with get_db_connection(DB_PATH) as conn:
        return evaluar_diagnostico_inicial(session_id, conn)


@st.cache_data(show_spinner=False)
def _comparativa_cacheada(session_id_pre: int, session_id_post: int) -> Dict[str, Any]:
    """Comparativa delta cacheada por par (pre, post)."""
    with get_db_connection(DB_PATH) as conn:
        return comparar_sesiones_delta(session_id_pre, session_id_post, conn)


@st.cache_data(show_spinner=False)
def _reporte_tecnico_cacheado(session_id: int) -> Tuple[str, Optional[bytes]]:
    """Texto del reporte individual + bytes del PDF (si fpdf2 está disponible)."""
    with get_db_connection(DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM Sesiones WHERE id_sesion = ?", (session_id,)
        ).fetchone()
    info_sesion = _sanear_info_sesion(dict(row) if row else {})
    analisis_comportamiento = analizar_comportamiento_completo(session_id)
    perfil = info_sesion.get("perfil_operador") or "N/A"
    datos = {
        "info_sesion": info_sesion,
        "perfil_predicho": perfil,
        "ruta_recomendada": RUTAS_DE_APRENDIZAJE.get(perfil),
        "analisis_comportamiento": analisis_comportamiento,
    }
    texto = generar_texto_reporte_individual(datos)
    pdf_bytes: Optional[bytes] = None
    if PDF_EXPORT_AVAILABLE and crear_reporte_pdf is not None:
        buf = io.BytesIO()
        crear_reporte_pdf(datos, buf)
        pdf_bytes = buf.getvalue()
    return texto, pdf_bytes


@st.cache_data(show_spinner=False)
def _pdf_evolucion_cacheado(session_id_pre: int, session_id_post: int) -> Optional[bytes]:
    """Bytes del PDF de evolución (si fpdf2 está disponible), con datos saneados."""
    if not (PDF_EXPORT_AVAILABLE and crear_reporte_evolucion_pdf is not None):
        return None
    with get_db_connection(DB_PATH) as conn:
        row_pre = conn.execute(
            "SELECT * FROM Sesiones WHERE id_sesion = ?", (session_id_pre,)
        ).fetchone()
        row_post = conn.execute(
            "SELECT * FROM Sesiones WHERE id_sesion = ?", (session_id_post,)
        ).fetchone()
        pen_pre = conn.execute(
            "SELECT COALESCE(SUM(penalizaciones), 0.0) FROM ResumenEventos WHERE id_sesion = ?",
            (session_id_pre,),
        ).fetchone()[0]
        pen_post = conn.execute(
            "SELECT COALESCE(SUM(penalizaciones), 0.0) FROM ResumenEventos WHERE id_sesion = ?",
            (session_id_post,),
        ).fetchone()[0]
    di = _sanear_info_sesion(dict(row_pre) if row_pre else {})
    df = _sanear_info_sesion(dict(row_post) if row_post else {})
    di["penalizaciones_totales"] = float(pen_pre or 0.0)
    df["penalizaciones_totales"] = float(pen_post or 0.0)
    buf = io.BytesIO()
    crear_reporte_evolucion_pdf(di, df, buf)
    return buf.getvalue()


def _invalidar_caches() -> None:
    """Limpia las cachés de cálculo tras una ingesta nueva (S6)."""
    _diagnostico_cacheado.clear()
    _comparativa_cacheada.clear()
    _reporte_tecnico_cacheado.clear()
    _pdf_evolucion_cacheado.clear()


# =============================================================================
# HELPERS DE VISUALIZACIÓN (ALTAIR)
# =============================================================================
def _tema_oscuro(chart: alt.Chart) -> alt.Chart:
    """Aplica un tema oscuro coherente a un gráfico de Altair."""
    return (
        chart.configure(background="#0E1117")
        .configure_axis(
            labelColor="#B0B7C3", titleColor="#E6EAF0",
            gridColor="#232833", domainColor="#3A4150",
        )
        .configure_legend(labelColor="#B0B7C3", titleColor="#E6EAF0")
        .configure_view(stroke="#232833")
        .configure_title(color="#E6EAF0", anchor="start", fontSize=14)
    )


def _grafico_serie(series: Dict[str, List], senal: str) -> None:
    """Dibuja una única señal de telemetría con Altair (línea azul)."""
    puntos = series.get(senal) or []
    if not puntos:
        st.warning(f"ℹ️ No hay datos de telemetría para '{senal}' en esta sesión.")
        return
    df = pd.DataFrame(puntos, columns=['tiempo', 'valor'])
    chart = (
        alt.Chart(df)
        .mark_line(color=COLOR_ACCENT, strokeWidth=1.6)
        .encode(
            x=alt.X('tiempo:Q', title='Tiempo (s)'),
            y=alt.Y('valor:Q', title=senal),
            tooltip=[
                alt.Tooltip('tiempo:Q', title='Tiempo (s)', format='.2f'),
                alt.Tooltip('valor:Q', title='Valor', format='.2f'),
            ],
        )
        .properties(title=senal, height=230)
        .interactive()
    )
    st.altair_chart(_tema_oscuro(chart), use_container_width=True)


def _grafico_superposicion(
    series_pre: Dict[str, List],
    series_post: Dict[str, List],
    senal: str,
) -> None:
    """Superpone la señal del Día 1 (naranja discontinua) y Día Final (verde sólida)."""
    filas: List[Dict[str, Any]] = []
    for t, v in (series_pre.get(senal) or []):
        filas.append({'tiempo': t, 'valor': v, 'dia': 'Día 1'})
    for t, v in (series_post.get(senal) or []):
        filas.append({'tiempo': t, 'valor': v, 'dia': 'Día Final'})

    if not filas:
        st.warning(f"ℹ️ Sin datos para superponer '{senal}'.")
        return

    df = pd.DataFrame(filas)
    chart = (
        alt.Chart(df)
        .mark_line(strokeWidth=2.0)
        .encode(
            x=alt.X('tiempo:Q', title='Tiempo (s)'),
            y=alt.Y('valor:Q', title=senal),
            color=alt.Color(
                'dia:N',
                scale=alt.Scale(domain=['Día 1', 'Día Final'],
                                range=[COLOR_WARNING, COLOR_SUCCESS]),
                legend=alt.Legend(title='Momento'),
            ),
            strokeDash=alt.StrokeDash(
                'dia:N',
                scale=alt.Scale(domain=['Día 1', 'Día Final'], range=[[6, 4], [0]]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip('dia:N', title='Momento'),
                alt.Tooltip('tiempo:Q', title='Tiempo (s)', format='.2f'),
                alt.Tooltip('valor:Q', title='Valor', format='.2f'),
            ],
        )
        .properties(title=f"{senal} — Día 1 vs Día Final", height=280)
        .interactive()
    )
    st.altair_chart(_tema_oscuro(chart), use_container_width=True)


def _tarjeta_dictamen(sugerencia: str, justificacion: str) -> None:
    """Renderiza la tarjeta semáforo con el veredicto de la IA en grande (H1: escapado)."""
    color = _COLORES_DICTAMEN.get(sugerencia, COLOR_ACCENT)
    icono = _ICONOS_DICTAMEN.get(sugerencia, "ℹ️")
    texto_sug = html.escape(str(sugerencia))
    texto_just = html.escape(str(justificacion))
    st.markdown(
        f"""
        <div style="border:2px solid {color}; border-left:14px solid {color};
                    border-radius:14px; padding:22px 26px; margin:8px 0 4px 0;
                    background:linear-gradient(90deg, rgba(255,255,255,0.04), rgba(255,255,255,0.0));">
            <div style="font-size:0.95rem; letter-spacing:2px; color:#9AA4B2;
                        text-transform:uppercase;">Sugerencia de admisión (IA)</div>
            <div style="font-size:2.9rem; font-weight:800; color:{color}; line-height:1.15;
                        margin:6px 0 10px 0;">{icono} {texto_sug}</div>
            <div style="font-size:1.02rem; color:#D6DBE3; line-height:1.5;">{texto_just}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _tarjeta_foco(foco: str) -> None:
    """Tarjeta destacada con el foco pedagógico sugerido (H1: escapado)."""
    texto_foco = html.escape(str(foco))
    st.markdown(
        f"""
        <div style="border:1px dashed {COLOR_ACCENT}; border-radius:12px;
                    padding:16px 20px; margin:6px 0; background:rgba(74,144,226,0.08);">
            <div style="font-size:0.9rem; letter-spacing:1px; color:{COLOR_ACCENT};
                        text-transform:uppercase; font-weight:700;">🎯 Foco Pedagógico Sugerido</div>
            <div style="font-size:1.2rem; color:#EAF0F8; margin-top:6px; font-weight:600;">{texto_foco}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _mostrar_decision_guardada(decision: Optional[Dict[str, Any]]) -> None:
    """Muestra la decisión persistida. Las notas libres van en st.text (H1: sin markdown)."""
    if not decision:
        return
    veredicto = html.escape(str(decision.get("veredicto", "")))
    fecha = html.escape(str(decision.get("fecha", "")))
    foco = decision.get("foco_sugerido")
    st.markdown(f"**📌 Decisión guardada:** {veredicto} — {fecha}")
    if foco:
        st.caption(f"Foco sugerido: {html.escape(str(foco))}")
    st.text("Notas del instructor:")
    # st.text NO renderiza markdown/HTML: seguro para texto libre del instructor.
    st.text(decision.get("notas") or "—")


# =============================================================================
# INGESTA DE PDF CON DEDUPLICACIÓN POR IDENTIDAD DE CONTENIDO (C8)
# =============================================================================
def _huella_archivo(archivo) -> Any:
    """Identidad de contenido estable del archivo subido (C8).

    Usa ``file_id`` si Streamlit lo expone; si no, una tupla (nombre, tamaño).
    Evita el falso positivo de deduplicar solo por ``archivo.name`` (hay varios
    'Report.pdf' distintos) y detecta cuando el contenido realmente cambia.
    """
    file_id = getattr(archivo, "file_id", None)
    if file_id:
        return file_id
    try:
        tam = archivo.size
    except Exception:
        tam = None
    return (archivo.name, tam)


def _ingesta_pdf(archivo, clave_estado_id: str, clave_estado_nombre: str,
                 clave_estado_feedback: Optional[str] = None) -> Optional[int]:
    """Guarda el PDF subido a un temporal con su NOMBRE ORIGINAL, lo procesa y cachea.

    Correcciones C8:
      * Deduplica por identidad de contenido (``_huella_archivo``), no por nombre.
      * Marca la huella en session_state ANTES de procesar, para no relanzar el ETL
        (OCR/visión) en cada rerun.
      * Si el pipeline devuelve ``None`` (fallo), resetea la huella a ``None`` para
        permitir un reintento explícito.
      * Escribe el temporal con el nombre original dentro de un dir temporal, de
        modo que la idempotencia del pipeline por ``nombre_archivo_origen`` funcione.
    """
    if archivo is None:
        return st.session_state.get(clave_estado_id)

    huella = _huella_archivo(archivo)
    if st.session_state.get(clave_estado_nombre) == huella:
        return st.session_state.get(clave_estado_id)

    # Marcar ANTES de procesar evita reprocesar en reruns concurrentes/fallidos.
    st.session_state[clave_estado_nombre] = huella

    dir_tmp = tempfile.mkdtemp(prefix="titan_upload_")
    tmp_path = os.path.join(dir_tmp, archivo.name)
    try:
        with open(tmp_path, "wb") as fh:
            fh.write(archivo.getvalue())

        session_id = _procesar_reporte(tmp_path)
        if session_id:
            st.session_state[clave_estado_id] = session_id
            _invalidar_caches()
            if clave_estado_feedback is not None:
                try:
                    eventos = extraer_eventos_crudos(tmp_path)
                    st.session_state[clave_estado_feedback] = (
                        generar_feedback(eventos) if eventos else None
                    )
                except Exception as exc:  # pragma: no cover - defensa de UI
                    st.session_state[clave_estado_feedback] = None
                    st.warning(f"⚠️ No se pudo generar la devolución inteligente: {exc}")
        else:
            # Fallo explícito: permitir reintento en el siguiente cambio de archivo.
            st.session_state[clave_estado_nombre] = None
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)

    return st.session_state.get(clave_estado_id)


# =============================================================================
# INTERFAZ STREAMLIT
# =============================================================================
st.set_page_config(
    page_title="Proyecto Titán v2.3",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- Inicialización de session_state ---
for _clave, _default in (
    ("diag_session_id", None),
    ("diag_archivo_nombre", None),
    ("diag_feedback", None),
    ("post_session_id", None),
    ("post_archivo_nombre", None),
):
    if _clave not in st.session_state:
        st.session_state[_clave] = _default

st.title("🚀 Proyecto Titán — Evaluación Pedagógica de Operadores")
st.caption(
    "Motor de diagnóstico de admisión y comparativa de evolución para simulador de "
    "carretilla elevadora."
)

with st.sidebar:
    st.header("ℹ️ Información")
    st.markdown(
        "**Flujo de evaluación**\n\n"
        "1. **Día 1** — Sube el reporte del Ejercicio inicial para obtener el "
        "dictamen de admisión.\n"
        "2. **Día Final** — Compara una sesión previa con la prueba final para "
        "medir la evolución."
    )
    st.markdown("---")
    if st.session_state.get("diag_session_id"):
        st.success(f"📋 Sesión de diagnóstico activa: #{st.session_state['diag_session_id']}")
    if st.session_state.get("post_session_id"):
        st.success(f"📈 Sesión final activa: #{st.session_state['post_session_id']}")

tab1, tab2 = st.tabs([
    "📋 Diagnóstico de Admisión (Día 1)",
    "📈 Comparativa Delta (Día Final)",
])

# =============================================================================
# PESTAÑA 1 — DIAGNÓSTICO DE ADMISIÓN (DÍA 1)
# =============================================================================
with tab1:
    st.subheader("📂 1. Ingesta del reporte inicial (Ejercicio 1)")
    archivo_diag = st.file_uploader(
        "Sube el PDF del ejercicio de diagnóstico",
        type=['pdf'],
        key="uploader_diagnostico",
    )
    session_id_diag = _ingesta_pdf(
        archivo_diag, "diag_session_id", "diag_archivo_nombre", "diag_feedback"
    )

    if not session_id_diag:
        st.info("ℹ️ Sube un reporte PDF para generar el diagnóstico de admisión.")
    else:
        st.markdown("---")
        # Cálculo pesado cacheado (S6); la decisión se lee SIN caché (S6/barata).
        diagnostico = _diagnostico_cacheado(session_id_diag)
        with get_db_connection(DB_PATH) as conn:
            decision_previa = obtener_decision_instructor(session_id_diag, conn)

        metricas = diagnostico["metricas_calculadas"]
        series = diagnostico["series"]

        # --- Tarjeta semáforo sugerida por la IA ---
        st.subheader("🚦 2. Dictamen de la IA")
        _tarjeta_dictamen(diagnostico["sugerencia_admision"], diagnostico["justificacion"])

        # --- Métricas calculadas clave ---
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("🛑 Frenadas bruscas", metricas.get("frenadas_bruscas", 0))
        c2.metric("🌀 Volantazos", metricas.get("volantazos", 0))
        c3.metric("💥 Colisiones netas", metricas.get("colisiones_netas", 0))
        c4.metric("⏱️ Duración (s)", f"{metricas.get('tiempo_total_segundos', 0.0):.1f}")
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("🏗️ Microajustes torre", metricas.get("inseguridad_torre_microajustes", 0))
        c6.metric("⏳ Inseg. torre (s)", f"{metricas.get('inseguridad_torre_segundos', 0.0):.1f}")
        c7.metric(
            "📏 Traslado horq. alta (s)",
            f"{metricas.get('traslado_horquilla_alta_segundos', 0.0):.1f}",
        )
        c8.metric("📐 Varianza dirección", f"{metricas.get('varianza_steering', 0.0):.2f}")

        # --- Foco pedagógico ---
        _tarjeta_foco(diagnostico["foco_instructor"])

        # --- Panel del instructor ---
        st.markdown("---")
        st.subheader("🧑‍🏫 3. Panel del Instructor")
        st.caption("Valida o corrige el dictamen de la IA y deja constancia de tu decisión.")

        _mostrar_decision_guardada(decision_previa)

        opciones = ["Apto", "Observado", "No Apto"]
        radio_key = f"radio_decision_{session_id_diag}"
        notas_key = f"notas_{session_id_diag}"

        # Precarga de widgets con la decisión YA guardada (C2.4). Si no existe, se
        # siembra con la sugerencia de la IA (mapeando 'No Apto Crítico' -> 'No Apto').
        if decision_previa and decision_previa.get("veredicto") in opciones:
            semilla_radio = decision_previa["veredicto"]
        else:
            sugerencia_ia = diagnostico["sugerencia_admision"]
            semilla_radio = "No Apto" if sugerencia_ia == "No Apto Crítico" else sugerencia_ia
            if semilla_radio not in opciones:
                semilla_radio = "Observado"
        semilla_notas = (decision_previa.get("notas") or "") if decision_previa else ""

        st.session_state.setdefault(radio_key, semilla_radio)
        st.session_state.setdefault(notas_key, semilla_notas)

        # Se pasa key= SIN index/value para que el widget tome la semilla de
        # session_state (patrón recomendado: persiste y no genera warning).
        veredicto_instructor = st.radio(
            "Tu decisión de admisión:",
            opciones,
            horizontal=True,
            key=radio_key,
        )
        notas_instructor = st.text_area(
            "Notas libres del instructor",
            key=notas_key,
            height=110,
            placeholder="Observaciones, plan de corrección, incidencias detectadas...",
        )

        if st.button("💾 Guardar decisión", type="primary"):
            with get_db_connection(DB_PATH) as conn:
                ok = guardar_decision_instructor(
                    session_id_diag,
                    veredicto_instructor,
                    notas_instructor,
                    conn,
                    foco_sugerido=diagnostico["foco_instructor"],
                )
            if ok:
                st.success(
                    f"✅ Decisión '{veredicto_instructor}' guardada para la sesión "
                    f"#{session_id_diag}."
                )
                # S5: re-leer y pintar el estado FRESCO tras guardar.
                with get_db_connection(DB_PATH) as conn:
                    decision_fresca = obtener_decision_instructor(session_id_diag, conn)
                _mostrar_decision_guardada(decision_fresca)
            else:
                st.error("⚠️ No se pudo guardar la decisión. Revisa la base de datos.")

        # --- Gráficos interactivos de las 6 señales ---
        st.markdown("---")
        st.subheader("📊 4. Telemetría de la sesión")
        for senal in GRAFICOS_DISPONIBLES:
            _grafico_serie(series, senal)

        # --- Devolución inteligente y reporte técnico (ampliable) ---
        feedback = st.session_state.get("diag_feedback")
        if feedback:
            with st.expander("🤖 Devolución inteligente (basada en eventos crudos)"):
                st.markdown(feedback)

        with st.expander("📑 Reporte técnico completo"):
            st.caption(
                "Reporte legacy de comportamiento (criterio propio de microajustes); "
                "las métricas oficiales de admisión son las de las tarjetas superiores."
            )
            texto_reporte, pdf_bytes = _reporte_tecnico_cacheado(session_id_diag)
            st.code(texto_reporte)
            if PDF_EXPORT_AVAILABLE and pdf_bytes:
                st.download_button(
                    "⬇️ Descargar PDF del diagnóstico",
                    data=pdf_bytes,
                    file_name=f"diagnostico_sesion_{session_id_diag}.pdf",
                    mime="application/pdf",
                )
            else:
                st.info(
                    "ℹ️ Exportación PDF no disponible: instala la dependencia con "
                    "'pip install fpdf2'."
                )

# =============================================================================
# PESTAÑA 2 — COMPARATIVA DELTA (DÍA FINAL)
# =============================================================================
with tab2:
    st.subheader("📋 1. Sesión diagnóstica previa (Pre)")
    sesiones_disp: List[Dict[str, Any]] = []
    try:
        with get_db_connection(DB_PATH) as conn:
            filas = conn.execute(
                "SELECT id_sesion, nombre_operador, nombre_ejercicio, fecha_hora_inicio "
                "FROM Sesiones ORDER BY id_sesion"
            ).fetchall()
            sesiones_disp = [dict(f) for f in filas]
    except Exception as exc:  # pragma: no cover - defensa de UI
        st.error(f"⚠️ No se pudieron cargar las sesiones: {exc}")

    if not sesiones_disp:
        st.warning("⚠️ No hay sesiones cargadas en la base de datos. Procesa un reporte primero.")
    else:
        etiquetas = {
            f"#{s['id_sesion']} — {s.get('nombre_operador') or 'N/A'} | "
            f"{s.get('nombre_ejercicio') or 'N/A'} | {s.get('fecha_hora_inicio') or 's/fecha'}": s['id_sesion']
            for s in sesiones_disp
        }
        etiqueta_pre = st.selectbox(
            "Selecciona la sesión de diagnóstico (Día 1) para comparar:",
            options=list(etiquetas.keys()),
            key="selectbox_pre",
        )
        session_id_pre = etiquetas.get(etiqueta_pre)

        st.markdown("---")
        st.subheader("📂 2. Prueba final (Ejercicio 14)")
        archivo_post = st.file_uploader(
            "Sube el PDF del ejercicio final",
            type=['pdf'],
            key="uploader_final",
        )
        session_id_post = _ingesta_pdf(
            archivo_post, "post_session_id", "post_archivo_nombre"
        )

        if not session_id_post:
            st.info("ℹ️ Sube el reporte PDF de la prueba final para comparar la evolución.")
        elif session_id_pre == session_id_post:
            st.warning("⚠️ La sesión Pre y Post son la misma. Selecciona sesiones distintas.")
        else:
            st.markdown("---")
            comparativa = _comparativa_cacheada(session_id_pre, session_id_post)

            # Validación de existencia (C3): no pintar nada con datos falsos.
            if comparativa.get("error"):
                st.error(f"⚠️ {comparativa['error']}")
                st.stop()

            deltas = comparativa["deltas"]

            # --- Veredicto de evolución ---
            st.subheader("🏁 Veredicto de evolución")
            veredicto_evo = comparativa["veredicto_evolucion"]
            color_evo = _COLORES_EVOLUCION.get(veredicto_evo, COLOR_ACCENT)
            st.markdown(
                f"""
                <div style="border-left:14px solid {color_evo}; border-radius:10px;
                            padding:14px 20px; background:rgba(255,255,255,0.04);">
                    <span style="font-size:1.9rem; font-weight:800; color:{color_evo};">
                    {html.escape(str(veredicto_evo))}</span>
                    <span style="color:#9AA4B2; margin-left:12px;">
                    (Δ puntaje depurado: {deltas['puntaje_depurado']:+.2f})</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            # --- Tarjetas de impacto ---
            st.markdown("#### 📊 Tarjetas de impacto")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(
                "⏱️ Tiempo (s)",
                f"{comparativa['post']['duracion_segundos']:.1f}",
                delta=_delta_str(f"{deltas['duracion_seg']:+.1f}s", deltas["duracion_pct"]),
                delta_color="inverse",
            )
            m2.metric(
                "💥 Colisiones",
                f"{comparativa['post']['metricas'].get('colisiones_netas', 0)}",
                delta=_delta_str(f"{deltas['colisiones']:+d}", deltas["colisiones_pct"]),
                delta_color="inverse",
            )
            m3.metric(
                "🌀 Suavidad (fren.+volant.)",
                f"{comparativa['post']['metricas'].get('frenadas_bruscas', 0) + comparativa['post']['metricas'].get('volantazos', 0)}",
                delta=_delta_str(f"{deltas['suavidad']:+d}", deltas["suavidad_pct"]),
                delta_color="inverse",
            )
            m4.metric(
                "🎯 Puntaje depurado",
                f"{comparativa['post']['puntaje_depurado']:.2f}",
                delta=_delta_str(f"{deltas['puntaje_depurado']:+.2f}", deltas["puntaje_depurado_pct"]),
                delta_color="normal",
            )
            st.caption(
                "Δ Puntaje depurado derivado = puntaje_final + Σ penalizaciones "
                "(las penalizaciones se almacenan en negativo). Suavidad = frenadas "
                "bruscas + volantazos (delta calculado sobre totales)."
            )

            # --- Gráficos de superposición ---
            st.markdown("---")
            st.subheader("📈 Superposición de señales (Día 1 vs Día Final)")
            col_a, col_b = st.columns(2)
            pares = [
                (col_a, 'Steering'),
                (col_b, 'Brake Pad'),
                (col_a, 'Speed In Km/h'),
                (col_b, 'Fork Height In Mtrs'),
            ]
            for col, senal in pares:
                with col:
                    _grafico_superposicion(
                        comparativa["series_pre"], comparativa["series_post"], senal
                    )

            # --- Resumen de evolución imprimible ---
            st.markdown("---")
            st.subheader("📄 Resumen de evolución técnica")
            st.code(comparativa["resumen_evolucion"], language="text")

            with st.expander("🔎 Reporte de evolución (texto clásico)"):
                st.code(generar_reporte_evolucion(session_id_pre, session_id_post))

            # --- Descarga del PDF de evolución (protegida si falta fpdf2) ---
            pdf_evo_bytes = _pdf_evolucion_cacheado(session_id_pre, session_id_post)
            if PDF_EXPORT_AVAILABLE and pdf_evo_bytes:
                st.download_button(
                    "⬇️ Descargar PDF de evolución",
                    data=pdf_evo_bytes,
                    file_name=f"evolucion_{session_id_pre}_a_{session_id_post}.pdf",
                    mime="application/pdf",
                )
            else:
                st.info(
                    "ℹ️ Exportación PDF no disponible: instala la dependencia con "
                    "'pip install fpdf2'."
                )
