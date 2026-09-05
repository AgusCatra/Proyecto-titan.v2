# streamlit_app.py
# Proyecto Titán — Interfaz web (Streamlit): CAPA DE PRESENTACIÓN.
#
# Dos pestañas de trabajo (Diagnóstico de Admisión y Comparativa Delta),
# persistencia con st.session_state, caché de cálculos pesados y gráficos
# interactivos con PLOTLY.
#
# Reglas de esta capa (arquitectura API-ready):
#   * NO contiene reglas de negocio pedagógicas: viven en core.evaluador_diagnostico.
#   * NO contiene SQL: el acceso a datos vive en core.db_manager.
#   * NO genera documentos: la exportación PDF vive en core.report_generator.
#   * NO redacta devoluciones: el asesor LLM vive en core.ai_advisor.
#   * Solo orquesta vistas y componentes consumiendo funciones de ``core/``.

import html
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, List, Optional

import numpy as np
import plotly.graph_objects as go
import streamlit as st

# --- Rutas del proyecto ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- Importaciones del núcleo ---
from core.pipeline import process_simulator_pdf
from core.db_manager import get_db_connection, list_sessions
# Import DEFENSIVO: core.report_generator depende de 'fpdf' (fpdf2), que puede no
# estar instalado en el entorno. Si falta, la app DEBE seguir arrancando y solo se
# deshabilita la exportación a PDF, nunca se cae con ModuleNotFoundError.
try:
    from core.report_generator import generar_pdf_diagnostico, generar_pdf_evolucion
    PDF_EXPORT_AVAILABLE = True
except ImportError:
    generar_pdf_diagnostico = None
    generar_pdf_evolucion = None
    PDF_EXPORT_AVAILABLE = False
from core.evaluador_diagnostico import (
    DICTAMEN_APTO,
    DICTAMEN_NO_APTO_CRITICO,
    DICTAMEN_OBSERVADO,
    RIESGO_ALTO,
    RIESGO_BAJO,
    RIESGO_MEDIO,
    SENALES_CANONICAS,
    VEREDICTO_MEJORA_MODERADA,
    VEREDICTO_MEJORA_SIGNIFICATIVA,
    VEREDICTO_REGRESION,
    VEREDICTO_ESTANCADO,
    VEREDICTO_SIN_DATOS,
    comparar_sesiones_delta,
    evaluar_diagnostico_inicial,
    guardar_decision_instructor,
    obtener_decision_instructor,
    obtener_payload_para_llm,
)
from core.ai_advisor import (
    ENV_API_KEY,
    PROVIDER_ANTHROPIC,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
    generar_devolucion_pedagogica,
)

# =============================================================================
# CONFIGURACIÓN Y CONSTANTES DE PRESENTACIÓN
# =============================================================================
DB_PATH = os.path.join(project_root, 'database', 'titan.db')
MODELS_PATH = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)

# Las 6 señales canónicas: fuente única en core.graph_mapper.CANONICAL_SIGNALS.
GRAFICOS_DISPONIBLES: List[str] = list(SENALES_CANONICAS)

# Unidades físicas por señal para el eje Y.
UNIDADES_SENAL = {
    'Steering': '° (grados)',
    'Speed In Km/h': 'Km/h',
    'Brake Pad': 'Presión',
    'Acceleration Pad': 'Aceleración',
    'Fork Height In Mtrs': 'Metros',
    'Tilt Angle In Deg': 'Grados',
}

# --- Paleta canónica ---
COLOR_ACCENT = "#4A90E2"   # azul
COLOR_SUCCESS = "#00A67E"  # verde
COLOR_WARNING = "#F5A524"  # naranja
COLOR_DANGER = "#EF4444"   # rojo

# Las claves son las etiquetas que define el motor de evaluación (no se duplican).
_COLORES_DICTAMEN = {
    DICTAMEN_APTO: COLOR_SUCCESS,
    DICTAMEN_OBSERVADO: COLOR_WARNING,
    DICTAMEN_NO_APTO_CRITICO: COLOR_DANGER,
}
_ICONOS_DICTAMEN = {
    DICTAMEN_APTO: "✅",
    DICTAMEN_OBSERVADO: "⚠️",
    DICTAMEN_NO_APTO_CRITICO: "⛔",
}
_COLORES_EVOLUCION = {
    VEREDICTO_MEJORA_SIGNIFICATIVA: COLOR_SUCCESS,
    VEREDICTO_MEJORA_MODERADA: COLOR_SUCCESS,
    VEREDICTO_REGRESION: COLOR_DANGER,
    VEREDICTO_ESTANCADO: COLOR_WARNING,
    VEREDICTO_SIN_DATOS: COLOR_DANGER,
}

# Criterio de color de los deltas en st.metric:
#   * Colisiones / Frenadas / Volantazos / Suavidad -> REDUCCIÓN es MEJORA (inverse).
#   * Puntaje depurado -> AUMENTO es MEJORA (normal).
#   * Duración -> MENOS tiempo = mayor eficiencia (inverse).

_COLORES_RIESGO = {
    RIESGO_ALTO: COLOR_DANGER,
    RIESGO_MEDIO: COLOR_WARNING,
    RIESGO_BAJO: COLOR_SUCCESS,
}

# --- Asesor LLM (core.ai_advisor) ---
PROVEEDORES_IA = {
    "Local (sin costo de API)": PROVIDER_MOCK,
    "OpenAI": PROVIDER_OPENAI,
    "Anthropic": PROVIDER_ANTHROPIC,
}


# =============================================================================
# ADAPTER DE INGESTA
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
# HELPERS DE PRESENTACIÓN
# =============================================================================
def _delta_str(valor_abs: str, pct: Optional[float]) -> str:
    """Formatea el delta de un st.metric mostrando 'n/a' si el pct es None."""
    return f"{valor_abs} ({pct:+.1f}%)" if pct is not None else f"{valor_abs} (n/a)"


# =============================================================================
# CÁLCULOS CACHEADOS — la conn NO es hashable, se abre dentro
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
def _payload_llm_cacheado(
    session_id_pre: int, session_id_post: Optional[int] = None
) -> Dict[str, Any]:
    """Payload de métricas para el asesor pedagógico (core.ai_advisor)."""
    with get_db_connection(DB_PATH) as conn:
        return obtener_payload_para_llm(session_id_pre, conn, session_id_post)


def _pdf_diagnostico(
    session_id: int, devolucion: Optional[Dict[str, Any]] = None
) -> Optional[bytes]:
    """Bytes del PDF de diagnóstico individual (los genera ``core.report_generator``)."""
    if not PDF_EXPORT_AVAILABLE:
        return None
    with get_db_connection(DB_PATH) as conn:
        return generar_pdf_diagnostico(session_id, conn, devolucion=devolucion)


def _pdf_evolucion(
    session_id_pre: int,
    session_id_post: int,
    devolucion: Optional[Dict[str, Any]] = None,
) -> Optional[bytes]:
    """Bytes del PDF de evolución Pre/Post (los genera ``core.report_generator``)."""
    if not PDF_EXPORT_AVAILABLE:
        return None
    with get_db_connection(DB_PATH) as conn:
        return generar_pdf_evolucion(
            session_id_pre, session_id_post, conn, devolucion=devolucion
        )


def _invalidar_caches() -> None:
    """Limpia las cachés de cálculo tras una ingesta nueva."""
    _diagnostico_cacheado.clear()
    _comparativa_cacheada.clear()
    _payload_llm_cacheado.clear()


# =============================================================================
# HELPERS DE VISUALIZACIÓN (PLOTLY)
# =============================================================================
_PLOTLY_LAYOUT_BASE = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(14,17,23,1)",
    margin=dict(l=50, r=20, t=40, b=40),
    font=dict(color="#B0B7C3"),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        font=dict(size=11),
    ),
)


def _normalizar_tiempo(timestamps: List[float]) -> np.ndarray:
    """Convierte timestamps absolutos a porcentaje de avance [0, 100].

    Protege contra división por cero si la serie tiene un solo punto o es constante.
    """
    ts = np.array(timestamps, dtype=float)
    if len(ts) < 2:
        return np.zeros_like(ts)
    span = ts[-1] - ts[0]
    if abs(span) < 1e-9:
        return np.zeros_like(ts)
    return (ts - ts[0]) / span * 100.0


def _grafico_serie_plotly(series: Dict[str, List], senal: str, height: int = 280) -> None:
    """Dibuja una única señal de telemetría con Plotly (línea azul)."""
    puntos = series.get(senal) or []
    if not puntos:
        st.warning(f"ℹ️ No hay datos de telemetría para '{senal}'.")
        return

    tiempos = [p[0] for p in puntos]
    valores = [p[1] for p in puntos]
    unidad = UNIDADES_SENAL.get(senal, '')

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=tiempos, y=valores,
        mode='lines',
        line=dict(color=COLOR_ACCENT, width=1.6),
        name=senal,
    ))
    fig.update_layout(
        **_PLOTLY_LAYOUT_BASE,
        height=height,
        title=dict(text=senal, font=dict(size=13, color="#E6EAF0")),
        xaxis_title="Tiempo (s)",
        yaxis_title=unidad,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_superposicion_plotly(
    series_pre: Dict[str, List],
    series_post: Dict[str, List],
    senal: str,
    normalizado: bool = True,
    height: int = 280,
) -> None:
    """Superpone Día 1 (naranja discontinua) y Día Final (verde sólida).

    Si ``normalizado=True``, el eje X es 'Avance del Ejercicio (%)' de 0 a 100.
    Si ``normalizado=False``, el eje X es 'Tiempo Real (s)'.
    """
    puntos_pre = series_pre.get(senal) or []
    puntos_post = series_post.get(senal) or []

    if not puntos_pre and not puntos_post:
        st.warning(f"ℹ️ Sin datos para superponer '{senal}'.")
        return

    fig = go.Figure()

    for puntos, etiqueta, color, dash in [
        (puntos_pre, 'Día 1 (Pre)', COLOR_WARNING, 'dash'),
        (puntos_post, 'Día Final (Post)', COLOR_SUCCESS, 'solid'),
    ]:
        if not puntos:
            continue
        tiempos = [p[0] for p in puntos]
        valores = [p[1] for p in puntos]

        if normalizado:
            x_data = _normalizar_tiempo(tiempos)
        else:
            x_data = tiempos

        fig.add_trace(go.Scatter(
            x=x_data, y=valores,
            mode='lines',
            line=dict(color=color, width=2, dash=dash),
            name=etiqueta,
        ))

    unidad = UNIDADES_SENAL.get(senal, '')
    x_title = 'Avance del Ejercicio (%)' if normalizado else 'Tiempo Real (s)'

    fig.update_layout(
        **_PLOTLY_LAYOUT_BASE,
        height=height,
        title=dict(text=f"{senal} — Pre vs Post", font=dict(size=13, color="#E6EAF0")),
        xaxis_title=x_title,
        yaxis_title=unidad,
    )
    if normalizado:
        fig.update_xaxes(range=[0, 100])

    st.plotly_chart(fig, use_container_width=True)


def _tarjeta_dictamen(sugerencia: str, justificacion: str) -> None:
    """Renderiza la tarjeta semáforo con el veredicto de la IA."""
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
    """Tarjeta destacada con el foco pedagógico sugerido."""
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
    """Muestra la decisión persistida."""
    if not decision:
        return
    veredicto = html.escape(str(decision.get("veredicto", "")))
    fecha = html.escape(str(decision.get("fecha", "")))
    foco = decision.get("foco_sugerido")
    st.markdown(f"**📌 Decisión guardada:** {veredicto} — {fecha}")
    if foco:
        st.caption(f"Foco sugerido: {html.escape(str(foco))}")
    st.text("Notas del instructor:")
    st.text(decision.get("notas") or "—")


def _panel_devolucion_ia(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Renderiza la devolución pedagógica generada por ``core.ai_advisor``.

    El proveedor y la API key se leen de la barra lateral (``st.session_state``);
    sin clave configurada el asesor responde con su generador local determinista
    (desarrollo y tests offline, sin costo de API).

    Returns:
        El dict de devolución, para reutilizarlo en la exportación a PDF.
    """
    proveedor = st.session_state.get("ia_provider", PROVIDER_MOCK)
    api_key = st.session_state.get("ia_api_key") or None

    with st.spinner("Generando devolución pedagógica..."):
        devolucion = generar_devolucion_pedagogica(
            payload, api_key=api_key, provider=proveedor
        )

    for aviso in devolucion.get("advertencias", []):
        st.caption(f"ℹ️ {aviso}")

    riesgo = devolucion.get("nivel_riesgo", "")
    color = _COLORES_RIESGO.get(riesgo, COLOR_ACCENT)
    origen = (
        f"LLM · {devolucion.get('provider')} / {devolucion.get('modelo')}"
        if devolucion.get("generado_por_llm")
        else "Motor local determinista (sin costo de API)"
    )
    st.markdown(
        f"""
        <div style="border:1px solid {color}; border-left:14px solid {color};
                    border-radius:12px; padding:16px 20px; margin:6px 0;
                    background:rgba(255,255,255,0.03);">
            <div style="font-size:0.85rem; letter-spacing:1.5px; color:#9AA4B2;
                        text-transform:uppercase;">Diagnóstico de desempeño ·
                        riesgo {html.escape(str(riesgo))}</div>
            <div style="font-size:1.02rem; color:#D6DBE3; line-height:1.55;
                        margin-top:8px;">{html.escape(str(devolucion.get('diagnostico', '')))}</div>
            <div style="font-size:0.78rem; color:#7C8797; margin-top:8px;">{html.escape(origen)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_vicios, col_plan = st.columns(2)
    with col_vicios:
        st.markdown("**🔧 Vicios operativos detectados**")
        for vicio in devolucion.get("vicios_operativos", []):
            st.markdown(f"- {vicio}")
    with col_plan:
        st.markdown("**🛠️ Plan de acción correctivo**")
        for accion in devolucion.get("plan_de_accion", []):
            st.markdown(f"- {accion}")

    st.info(f"🎯 Prioridad de la próxima sesión: {devolucion.get('foco_prioritario', '')}")
    return devolucion


# =============================================================================
# INGESTA DE PDF CON DEDUPLICACIÓN POR IDENTIDAD DE CONTENIDO
# =============================================================================
def _huella_archivo(archivo) -> Any:
    """Identidad de contenido estable del archivo subido."""
    file_id = getattr(archivo, "file_id", None)
    if file_id:
        return file_id
    try:
        tam = archivo.size
    except Exception:
        tam = None
    return (archivo.name, tam)


def _ingesta_pdf(archivo, clave_estado_id: str, clave_estado_nombre: str) -> Optional[int]:
    """Guarda el PDF subido a un temporal con su NOMBRE ORIGINAL, lo procesa y cachea."""
    if archivo is None:
        return st.session_state.get(clave_estado_id)

    huella = _huella_archivo(archivo)
    if st.session_state.get(clave_estado_nombre) == huella:
        return st.session_state.get(clave_estado_id)

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
        else:
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
    ("post_session_id", None),
    ("post_archivo_nombre", None),
    ("ia_provider", PROVIDER_MOCK),
    ("ia_api_key", ""),
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
    st.header("🤖 Asesor pedagógico")
    etiqueta_proveedor = st.selectbox(
        "Proveedor de la devolución",
        options=list(PROVEEDORES_IA.keys()),
        key="selectbox_proveedor_ia",
        help=(
            "'Local' usa el generador determinista de core.ai_advisor: funciona "
            "offline y no tiene costo de API."
        ),
    )
    st.session_state["ia_provider"] = PROVEEDORES_IA[etiqueta_proveedor]
    if st.session_state["ia_provider"] != PROVIDER_MOCK:
        st.session_state["ia_api_key"] = st.text_input(
            "API key del proveedor",
            type="password",
            key="input_api_key_ia",
            help=f"También se lee de la variable de entorno {ENV_API_KEY}.",
        )
        if not st.session_state.get("ia_api_key"):
            st.caption("Sin API key se usará el generador local.")

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
    session_id_diag = _ingesta_pdf(archivo_diag, "diag_session_id", "diag_archivo_nombre")

    if not session_id_diag:
        st.info("ℹ️ Sube un reporte PDF para generar el diagnóstico de admisión.")
    else:
        st.markdown("---")
        # Fuente única de verdad: core.evaluador_diagnostico
        diagnostico = _diagnostico_cacheado(session_id_diag)
        with get_db_connection(DB_PATH) as conn:
            decision_previa = obtener_decision_instructor(session_id_diag, conn)

        metricas = diagnostico["metricas_calculadas"]
        series = diagnostico["series"]

        # --- Tarjeta semáforo sugerida por la IA ---
        st.subheader("🚦 2. Dictamen de la IA")
        _tarjeta_dictamen(diagnostico["sugerencia_admision"], diagnostico["justificacion"])

        # --- Métricas calculadas clave (del evaluador) ---
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

        # --- Devolución pedagógica del asesor (core.ai_advisor) ---
        st.markdown("---")
        st.subheader("🤖 3. Devolución pedagógica (IA)")
        st.caption(
            "Diagnóstico, vicios operativos y plan correctivo redactados a partir de "
            "las métricas del motor de evaluación."
        )
        payload_diag = _payload_llm_cacheado(session_id_diag)
        devolucion_diag = _panel_devolucion_ia(payload_diag)

        # --- Panel del instructor ---
        st.markdown("---")
        st.subheader("🧑‍🏫 4. Panel del Instructor")
        st.caption("Valida o corrige el dictamen de la IA y deja constancia de tu decisión.")

        _mostrar_decision_guardada(decision_previa)

        opciones = [DICTAMEN_APTO, DICTAMEN_OBSERVADO, "No Apto"]
        radio_key = f"radio_decision_{session_id_diag}"
        notas_key = f"notas_{session_id_diag}"

        if decision_previa and decision_previa.get("veredicto") in opciones:
            semilla_radio = decision_previa["veredicto"]
        else:
            sugerencia_ia = diagnostico["sugerencia_admision"]
            semilla_radio = (
                "No Apto" if sugerencia_ia == DICTAMEN_NO_APTO_CRITICO else sugerencia_ia
            )
            if semilla_radio not in opciones:
                semilla_radio = DICTAMEN_OBSERVADO
        semilla_notas = (decision_previa.get("notas") or "") if decision_previa else ""

        st.session_state.setdefault(radio_key, semilla_radio)
        st.session_state.setdefault(notas_key, semilla_notas)

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
                with get_db_connection(DB_PATH) as conn:
                    decision_fresca = obtener_decision_instructor(session_id_diag, conn)
                _mostrar_decision_guardada(decision_fresca)
            else:
                st.error("⚠️ No se pudo guardar la decisión. Revisa la base de datos.")

        # --- Gráficos interactivos: cuadrícula 2x3 ---
        st.markdown("---")
        st.subheader("📊 5. Telemetría de la sesión")
        for fila in range(3):
            col1, col2 = st.columns(2)
            with col1:
                _grafico_serie_plotly(series, GRAFICOS_DISPONIBLES[fila * 2], height=280)
            with col2:
                _grafico_serie_plotly(series, GRAFICOS_DISPONIBLES[fila * 2 + 1], height=280)

        # --- Exportación del documento (core.report_generator) ---
        st.markdown("---")
        pdf_diag_bytes = _pdf_diagnostico(session_id_diag, devolucion=devolucion_diag)
        if pdf_diag_bytes:
            st.download_button(
                "⬇️ Descargar PDF de diagnóstico",
                data=pdf_diag_bytes,
                file_name=f"diagnostico_sesion_{session_id_diag}.pdf",
                mime="application/pdf",
            )
        elif not PDF_EXPORT_AVAILABLE:
            st.info(
                "ℹ️ Exportación PDF no disponible: instala la dependencia con "
                "'pip install fpdf2'."
            )

# =============================================================================
# PESTAÑA 2 — COMPARATIVA DELTA (DÍA FINAL)
# =============================================================================
with tab2:
    st.subheader("📋 1. Sesión diagnóstica previa (Pre)")
    try:
        with get_db_connection(DB_PATH) as conn:
            sesiones_disp = list_sessions(conn)
    except Exception as exc:
        sesiones_disp = []
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
        session_id_post = _ingesta_pdf(archivo_post, "post_session_id", "post_archivo_nombre")

        if not session_id_post:
            st.info("ℹ️ Sube el reporte PDF de la prueba final para comparar la evolución.")
        elif session_id_pre == session_id_post:
            st.warning("⚠️ La sesión Pre y Post son la misma. Selecciona sesiones distintas.")
        else:
            st.markdown("---")
            # Fuente única de verdad: core.evaluador_diagnostico
            comparativa = _comparativa_cacheada(session_id_pre, session_id_post)

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
                "Métricas, deltas y veredicto calculados por el motor de evaluación "
                "(core.evaluador_diagnostico): esta capa solo los presenta."
            )

            # --- Gráficos de superposición con normalización temporal ---
            st.markdown("---")
            st.subheader("📈 Superposición de señales (Pre vs Post)")

            usar_normalizado = st.checkbox(
                "Avance Normalizado (%) — desmarcar para ver Tiempo Real (s)",
                value=True,
                key="chk_normalizar_tiempo",
                help=(
                    "Normalizado: ambas curvas se alinean de 0% a 100% del avance "
                    "del ejercicio, independientemente de su duración absoluta."
                ),
            )

            # Cuadrícula compacta 2 columnas x 3 filas
            for fila in range(3):
                col1, col2 = st.columns(2)
                with col1:
                    _grafico_superposicion_plotly(
                        comparativa["series_pre"], comparativa["series_post"],
                        GRAFICOS_DISPONIBLES[fila * 2],
                        normalizado=usar_normalizado, height=280,
                    )
                with col2:
                    _grafico_superposicion_plotly(
                        comparativa["series_pre"], comparativa["series_post"],
                        GRAFICOS_DISPONIBLES[fila * 2 + 1],
                        normalizado=usar_normalizado, height=280,
                    )

            # --- Devolución pedagógica de la evolución (core.ai_advisor) ---
            st.markdown("---")
            st.subheader("🤖 Devolución pedagógica (IA)")
            payload_evo = _payload_llm_cacheado(session_id_pre, session_id_post)
            devolucion_evo = _panel_devolucion_ia(payload_evo)

            # --- Descarga del PDF de evolución (core.report_generator) ---
            st.markdown("---")
            pdf_evo_bytes = _pdf_evolucion(
                session_id_pre, session_id_post, devolucion=devolucion_evo
            )
            if pdf_evo_bytes:
                st.download_button(
                    "⬇️ Descargar PDF de evolución",
                    data=pdf_evo_bytes,
                    file_name=f"evolucion_{session_id_pre}_a_{session_id_post}.pdf",
                    mime="application/pdf",
                )
            elif not PDF_EXPORT_AVAILABLE:
                st.info(
                    "ℹ️ Exportación PDF no disponible: instala la dependencia con "
                    "'pip install fpdf2'."
                )
