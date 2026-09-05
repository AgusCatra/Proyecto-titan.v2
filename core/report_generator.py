# core/report_generator.py
# Proyecto Titán — Generación de documentos (PDF).
#
# Única fuente de verdad de la generación de documentos del proyecto.
#   * ENTRADAS: dicts nativos ya saneados, o IDs de base de datos + conexión.
#   * SALIDAS: archivo escrito en una ruta o bytes (``io.BytesIO``).
#   * Las reglas de negocio pedagógicas (umbrales de veredicto, puntaje depurado,
#     métricas de vicios) NO se redefinen aquí: se importan de
#     ``core.evaluador_diagnostico`` / se reciben ya calculadas.
#   * Sin dependencias de UI: no importa streamlit, customtkinter ni tkinter.

import io
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from core.evaluador_diagnostico import (
    evaluar_diagnostico_inicial,
    obtener_datos_documento_sesion,
    obtener_datos_evolucion,
    veredicto_por_delta_puntaje,
)

logger = logging.getLogger(__name__)

# Destino de un PDF: ruta de archivo o buffer binario en memoria.
Destino = Union[str, io.BytesIO]

# Fuente core de FPDF. 'Arial' era un alias deprecado que fpdf2 sustituía por
# Helvetica emitiendo un aviso en cada llamada.
FUENTE_PDF = "Helvetica"

# -----------------------------------------------------------------------------
# Saneado de texto para las fuentes Type1 de FPDF, que solo soportan Latin-1:
# cualquier carácter por encima de U+00FF (emoji, flechas, rayas tipográficas)
# provoca UnicodeEncodeError al generar el PDF.
# -----------------------------------------------------------------------------
_TRADUCCIONES = str.maketrans({
    "\u2014": "-",   # —
    "\u2013": "-",   # –
    "\u2018": "'",   # ‘
    "\u2019": "'",   # ’
    "\u201c": '"',   # “
    "\u201d": '"',   # ”
    "\u2026": "...", # …
    "\u2022": "-",   # •
    "\u2192": "->",  # →
    "\u25b2": "+",   # ▲
    "\u25bc": "-",   # ▼
    "\u2264": "<=",  # ≤
    "\u2265": ">=",  # ≥
    "\u00a0": " ",   # nbsp
})


def _txt(valor: Any, default: str = "N/A") -> str:
    """Texto seguro para el PDF: Latin-1, sin caracteres no imprimibles."""
    texto = default if valor is None else str(valor).strip()
    if not texto:
        texto = default
    texto = texto.translate(_TRADUCCIONES)
    # Se descartan los caracteres fuera de Latin-1 (emoji, símbolos exóticos).
    return "".join(ch for ch in texto if ord(ch) <= 0xFF)


def _num(valor: Any, default: float = 0.0) -> float:
    """Número seguro para el PDF (``None``/no numérico -> ``default``)."""
    if valor is None:
        return float(default)
    try:
        return float(valor)
    except (TypeError, ValueError):
        return float(default)


# =============================================================================
# CLASE PDF BASE CON ENCABEZADO Y PIE DE PÁGINA
# =============================================================================
class PDF(FPDF):
    def header(self):
        self.set_font(FUENTE_PDF, 'B', 14)
        _celda(self, 'PROYECTO TITÁN', alto=10, alineacion='C')
        self.set_font(FUENTE_PDF, '', 10)
        _celda(self, 'Reporte de Análisis de Comportamiento', alto=5, alineacion='C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font(FUENTE_PDF, 'I', 8)
        _celda(self, f'Página {self.page_no()}', alto=10, alineacion='C', salto=False)


def _celda(
    pdf: PDF,
    texto: Any,
    *,
    alto: float = 6,
    ancho: float = 0,
    borde: int = 0,
    alineacion: str = "L",
    salto: bool = True,
) -> None:
    """``cell`` con texto saneado y posicionamiento explícito (API moderna de fpdf2).

    ``salto=True`` devuelve el cursor al margen izquierdo en la línea siguiente;
    ``salto=False`` lo deja a la derecha de la celda (para tablas).
    """
    pdf.cell(
        ancho,
        alto,
        _txt(texto),
        borde,
        new_x=XPos.LMARGIN if salto else XPos.RIGHT,
        new_y=YPos.NEXT if salto else YPos.TOP,
        align=alineacion,
    )


def _parrafo(pdf: PDF, texto: Any) -> None:
    """``multi_cell`` de ancho completo que SIEMPRE devuelve el cursor al margen.

    Con ``w=0`` fpdf2 deja el cursor en el margen derecho al terminar; sin este
    reset la siguiente línea se dibuja sin ancho horizontal y lanza
    ``FPDFException: Not enough horizontal space``.
    """
    pdf.multi_cell(0, 6, _txt(texto), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# =============================================================================
# SECCIONES COMPARTIDAS
# =============================================================================
# Métricas de telemetría que se listan en el reporte individual. Se dibuja SOLO
# la métrica presente en el origen de datos (``None`` = no aplica), de modo que el
# mismo render sirve para ``core.behavior_analyzer`` (app desktop) y para
# ``core.evaluador_diagnostico`` (motor de diagnóstico) sin inventar ceros.
_METRICAS_TELEMETRIA: List[Tuple[str, str, str]] = [
    ("Frenadas Bruscas", "frenadas_bruscas", "{:.0f}"),
    ("Volantazos (Correcciones)", "volantazos", "{:.0f}"),
    ("Aceleraciones Bruscas", "aceleraciones_bruscas", "{:.0f}"),
    ("Ajustes de Elevación", "ajustes_elevacion", "{:.0f}"),
    ("Inseguridad en Torre (microajustes)", "inseguridad_torre_microajustes", "{:.0f}"),
    ("Inseguridad en Torre (s)", "inseguridad_torre_segundos", "{:.1f}"),
    ("Traslado con Horquilla Alta (s)", "traslado_horquilla_alta_segundos", "{:.1f}"),
    ("Colisiones Netas", "colisiones_netas", "{:.0f}"),
    ("Duración de la Sesión (s)", "tiempo_total_segundos", "{:.1f}"),
]

_METRICAS_VELOCIDAD: List[Tuple[str, str, str]] = [
    ("Velocidad Media (Km/h)", "velocidad_media", "{:.2f}"),
    ("Velocidad Máxima (Km/h)", "velocidad_maxima", "{:.2f}"),
    ("Incidentes de Exceso de Velocidad", "incidentes_de_velocidad", "{:.0f}"),
]


def _seccion_telemetria(pdf: PDF, comportamiento: Dict[str, Any]) -> None:
    """Lista las métricas de telemetría disponibles en ``comportamiento``."""
    velocidad = comportamiento.get('metricas_velocidad', {}) or {}
    for origen, catalogo in (
        (comportamiento, _METRICAS_TELEMETRIA),
        (velocidad, _METRICAS_VELOCIDAD),
    ):
        for etiqueta, clave, formato in catalogo:
            valor = origen.get(clave)
            if valor is None:
                continue
            _celda(pdf, f"- {etiqueta}: {formato.format(_num(valor))}")


def _seccion_devolucion(pdf: PDF, devolucion: Optional[Dict[str, Any]]) -> None:
    """Dibuja la devolución pedagógica generada por ``core.ai_advisor``."""
    if not devolucion:
        return

    pdf.ln(4)
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Devolución Pedagógica', alto=10)

    origen = _txt(devolucion.get('provider'), 'desconocido')
    modelo = _txt(devolucion.get('modelo'), '')
    pdf.set_font(FUENTE_PDF, 'I', 9)
    _celda(pdf, f"Generada por: {origen}" + (f" / {modelo}" if modelo else ""), alto=5)

    pdf.set_font(FUENTE_PDF, '', 11)
    diagnostico = devolucion.get('diagnostico')
    if diagnostico:
        _parrafo(pdf, diagnostico)

    for titulo, clave in (
        ('Vicios operativos detectados', 'vicios_operativos'),
        ('Plan de acción correctivo', 'plan_de_accion'),
    ):
        elementos = devolucion.get(clave) or []
        if not elementos:
            continue
        pdf.ln(2)
        pdf.set_font(FUENTE_PDF, 'B', 11)
        _celda(pdf, titulo, alto=8)
        pdf.set_font(FUENTE_PDF, '', 11)
        for elemento in elementos:
            _parrafo(pdf, f"- {elemento}")


# =============================================================================
# GENERADOR DE PDF PARA ANÁLISIS INDIVIDUAL
# =============================================================================
def crear_reporte_pdf(
    datos_analisis: Dict[str, Any],
    ruta_guardado: Destino,
    devolucion: Optional[Dict[str, Any]] = None,
) -> None:
    """Crea un PDF profesional para un análisis de sesión individual.

    Args:
        datos_analisis: dict con ``info_sesion``, ``perfil_predicho``,
            ``ruta_recomendada``, ``analisis_comportamiento`` y, opcionalmente,
            ``dictamen_admision`` / ``justificacion`` / ``foco_instructor``.
        ruta_guardado: ruta de archivo o buffer ``io.BytesIO``.
        devolucion: salida opcional de ``core.ai_advisor.generar_devolucion_pedagogica``.
    """
    datos_analisis = datos_analisis or {}
    pdf = PDF()
    pdf.add_page()
    pdf.set_font(FUENTE_PDF, '', 11)

    info = datos_analisis.get('info_sesion', {}) or {}
    perfil = datos_analisis.get('perfil_predicho') or info.get('perfil_operador') or 'N/A'
    ruta = datos_analisis.get('ruta_recomendada') or {}
    comportamiento = datos_analisis.get('analisis_comportamiento', {}) or {}

    # --- Sección de Datos Generales ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Diagnóstico de Sesión Individual', alto=10)
    pdf.set_font(FUENTE_PDF, '', 11)
    _celda(pdf, f"Operario: {info.get('nombre_operador', 'N/A')}")
    _celda(pdf, f"Clase: {info.get('nombre_clase', 'N/A')}")
    _celda(pdf, f"Ejercicio: {info.get('nombre_ejercicio', 'N/A')}")
    _celda(pdf, f"Puntaje Final: {_num(info.get('puntaje_final')):.2f}")
    pdf.ln(5)

    # --- Sección de Dictamen (motor de evaluación pedagógica) ---
    dictamen = datos_analisis.get('dictamen_admision')
    if dictamen:
        pdf.set_font(FUENTE_PDF, 'B', 12)
        _celda(pdf, 'Dictamen de Admisión', alto=10)
        pdf.set_font(FUENTE_PDF, 'B', 11)
        _celda(pdf, f"Sugerencia: {dictamen}", alto=8)
        pdf.set_font(FUENTE_PDF, '', 11)
        justificacion = datos_analisis.get('justificacion')
        if justificacion:
            _parrafo(pdf, justificacion)
        foco = datos_analisis.get('foco_instructor')
        if foco:
            _parrafo(pdf, f"Foco del instructor: {foco}")
        pdf.ln(5)

    # --- Sección de Perfil y Ruta ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Perfil de Comportamiento y Ruta Sugerida', alto=10)
    pdf.set_font(FUENTE_PDF, '', 11)
    _parrafo(
        pdf,
        f"El perfil de comportamiento detectado por el modelo de IA es: {str(perfil).upper()}",
    )
    if ruta:
        pdf.set_font(FUENTE_PDF, 'B', 11)
        _celda(pdf, f"Ruta Recomendada: {ruta.get('titulo', '')}", alto=8)
        pdf.set_font(FUENTE_PDF, '', 11)
        for ejercicio in ruta.get('ejercicios', []) or []:
            _celda(pdf, f"  - {ejercicio}")
    pdf.ln(5)

    # --- Sección de Análisis de Telemetría ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Análisis Detallado de Telemetría', alto=10)
    pdf.set_font(FUENTE_PDF, '', 11)
    _seccion_telemetria(pdf, comportamiento)

    _seccion_devolucion(pdf, devolucion)

    pdf.output(ruta_guardado)


# =============================================================================
# GENERADOR DE PDF PARA REPORTE DE EVOLUCIÓN
# =============================================================================
def crear_reporte_evolucion_pdf(
    datos_iniciales: Dict[str, Any],
    datos_finales: Dict[str, Any],
    ruta_guardado: Destino,
    devolucion: Optional[Dict[str, Any]] = None,
) -> None:
    """Crea un PDF profesional para un reporte de evolución comparativo.

    El veredicto general se obtiene de la regla única del proyecto
    (:func:`core.evaluador_diagnostico.veredicto_por_delta_puntaje`): este módulo
    no duplica umbrales pedagógicos.

    Args:
        datos_iniciales / datos_finales: dicts saneados de sesión (ver
            :func:`core.evaluador_diagnostico.obtener_datos_documento_sesion`).
        ruta_guardado: ruta de archivo o buffer ``io.BytesIO``.
        devolucion: salida opcional de ``core.ai_advisor``.
    """
    datos_iniciales = datos_iniciales or {}
    datos_finales = datos_finales or {}
    pdf = PDF()
    pdf.add_page()

    # --- Sección de Veredicto ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Reporte de Evolución del Operario', alto=10)
    pdf.set_font(FUENTE_PDF, '', 11)
    _celda(pdf, f"Operario: {datos_iniciales.get('nombre_operador', 'N/A')}")
    _celda(pdf, f"Ejercicio: {datos_iniciales.get('nombre_ejercicio', 'N/A')}")

    puntaje_inicial = _num(datos_iniciales.get('puntaje_final'))
    puntaje_final = _num(datos_finales.get('puntaje_final'))
    veredicto = veredicto_por_delta_puntaje(puntaje_final - puntaje_inicial)
    pdf.set_font(FUENTE_PDF, 'B', 11)
    _celda(pdf, f"Veredicto General: {veredicto}", alto=8)
    pdf.ln(5)

    # --- Sección de Perfil ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Evolución del Perfil de Comportamiento', alto=10)
    pdf.set_font(FUENTE_PDF, '', 11)
    _celda(pdf, f"- Perfil Inicial: {str(datos_iniciales.get('perfil_operador', 'N/A')).upper()}")
    _celda(pdf, f"- Perfil Final:   {str(datos_finales.get('perfil_operador', 'N/A')).upper()}")
    pdf.ln(5)

    # --- Tabla Comparativa ---
    pdf.set_font(FUENTE_PDF, 'B', 12)
    _celda(pdf, 'Tabla Comparativa de Métricas', alto=10)
    pdf.set_font(FUENTE_PDF, 'B', 10)

    col_width = [70, 30, 30, 40]
    for ancho, titulo in zip(col_width[:-1], ('Métrica', 'Inicial', 'Final')):
        _celda(pdf, titulo, alto=8, ancho=ancho, borde=1,
               alineacion='C', salto=False)
    _celda(pdf, 'Delta', alto=8, ancho=col_width[-1], borde=1, alineacion='C')
    pdf.set_font(FUENTE_PDF, '', 10)

    def draw_row(metric: str, v_inicial: Any, v_final: Any, is_score: bool = False) -> None:
        """Fila de la tabla con indicador ASCII y color según el sentido de mejora."""
        delta = _num(v_final) - _num(v_inicial)
        sign = '+' if delta >= 0 else ''
        if is_score:
            arrow = "(+)" if delta > 0 else "(-)" if delta < 0 else "="
            if delta > 0:
                pdf.set_text_color(0, 128, 0)
            elif delta < 0:
                pdf.set_text_color(255, 0, 0)
        else:
            arrow = "(-)" if delta < 0 else "(+)" if delta > 0 else "="
            if delta < 0:
                pdf.set_text_color(0, 128, 0)
            elif delta > 0:
                pdf.set_text_color(255, 0, 0)
        if delta == 0:
            pdf.set_text_color(0, 0, 0)
        _celda(pdf, metric, alto=8, ancho=col_width[0], borde=1, salto=False)
        _celda(pdf, f"{_num(v_inicial):.2f}", alto=8, ancho=col_width[1], borde=1,
               alineacion='C', salto=False)
        _celda(pdf, f"{_num(v_final):.2f}", alto=8, ancho=col_width[2], borde=1,
               alineacion='C', salto=False)
        _celda(pdf, f"{sign}{delta:.2f} {arrow}", alto=8, ancho=col_width[3],
               borde=1, alineacion='C')
        pdf.set_text_color(0, 0, 0)

    draw_row('Puntaje Final', puntaje_inicial, puntaje_final, is_score=True)
    draw_row('Puntaje Depurado', datos_iniciales.get('puntaje_depurado'),
             datos_finales.get('puntaje_depurado'), is_score=True)
    draw_row('Penalizaciones Totales', datos_iniciales.get('penalizaciones_totales'),
             datos_finales.get('penalizaciones_totales'))
    draw_row('Duración (s)', datos_iniciales.get('duracion_segundos'),
             datos_finales.get('duracion_segundos'))

    _seccion_devolucion(pdf, devolucion)

    pdf.output(ruta_guardado)


# =============================================================================
# API DE ALTO NIVEL — ENTRADA: IDs DE BD / SALIDA: BYTES (lista para una API REST)
# =============================================================================
def generar_pdf_evolucion(
    session_id_pre: int,
    session_id_post: int,
    conn,
    devolucion: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Genera el PDF de evolución Pre/Post y lo devuelve como ``bytes``.

    Resuelve los datos de negocio desde la BD a través de
    :func:`core.evaluador_diagnostico.obtener_datos_evolucion` (saneado, puntaje
    depurado y penalizaciones), de modo que ningún frontend deba escribir SQL.
    """
    datos = obtener_datos_evolucion(session_id_pre, session_id_post, conn)
    buffer = io.BytesIO()
    crear_reporte_evolucion_pdf(
        datos["datos_iniciales"], datos["datos_finales"], buffer, devolucion=devolucion
    )
    return buffer.getvalue()


def generar_pdf_diagnostico(
    session_id: int,
    conn,
    devolucion: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Genera el PDF de diagnóstico individual de una sesión y lo devuelve en ``bytes``."""
    documento = obtener_datos_documento_sesion(session_id, conn)
    diagnostico = evaluar_diagnostico_inicial(session_id, conn)
    datos_analisis = {
        "info_sesion": documento,
        "perfil_predicho": documento.get("perfil_operador", "N/A"),
        "ruta_recomendada": {},
        "analisis_comportamiento": diagnostico.get("metricas_calculadas", {}),
        "dictamen_admision": diagnostico.get("sugerencia_admision"),
        "justificacion": diagnostico.get("justificacion"),
        "foco_instructor": diagnostico.get("foco_instructor"),
    }
    buffer = io.BytesIO()
    crear_reporte_pdf(datos_analisis, buffer, devolucion=devolucion)
    return buffer.getvalue()
