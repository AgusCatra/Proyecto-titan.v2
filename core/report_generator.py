# core/report_generator.py
# Proyecto Titán - Módulo de Creación de Archivos PDF
# Versión: 2.1 (Completo con Reporte Individual y de Evolución)

import io
from typing import Dict, Any, Union
from fpdf import FPDF

# =============================================================================
# CLASE PDF BASE CON ENCABEZADO Y PIE DE PÁGINA
# =============================================================================
class PDF(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'PROYECTO TITÁN', 0, 1, 'C')
        self.set_font('Arial', '', 10)
        self.cell(0, 5, 'Reporte de Análisis de Comportamiento', 0, 1, 'C')
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'C')

# =============================================================================
# GENERADOR DE PDF PARA ANÁLISIS INDIVIDUAL
# =============================================================================
def crear_reporte_pdf(datos_analisis: Dict[str, Any], ruta_guardado: Union[str, io.BytesIO]):
    """
    Crea un PDF profesional para un análisis de sesión individual.
    """
    pdf = PDF()
    pdf.add_page()
    pdf.set_font('Arial', '', 11)

    info = datos_analisis.get('info_sesion', {})
    perfil = datos_analisis.get('perfil_predicho', 'N/A')
    ruta = datos_analisis.get('ruta_recomendada', {})
    comportamiento = datos_analisis.get('analisis_comportamiento', {})
    
    # --- Sección de Datos Generales ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Diagnóstico de Sesión Individual', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, f"Operario: {info.get('nombre_operador', 'N/A')}", 0, 1)
    pdf.cell(0, 6, f"Clase: {info.get('nombre_clase', 'N/A')}", 0, 1)
    pdf.cell(0, 6, f"Puntaje Final: {info.get('puntaje_final', 0):.2f}", 0, 1)
    pdf.ln(5)

    # --- Sección de Perfil y Ruta ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Perfil de Comportamiento y Ruta Sugerida', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    pdf.multi_cell(0, 6, f"El perfil de comportamiento detectado por el modelo de IA es: {perfil.upper()}")
    if ruta:
        pdf.set_font('Arial', 'B', 11)
        pdf.cell(0, 8, f"Ruta Recomendada: {ruta.get('titulo', '')}", 0, 1)
        pdf.set_font('Arial', '', 11)
        for ejercicio in ruta.get('ejercicios', []):
            pdf.cell(0, 6, f"  - {ejercicio}", 0, 1)
    pdf.ln(5)
    
    # --- Sección de Análisis de Telemetría ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Análisis Detallado de Telemetría', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    vel = comportamiento.get('metricas_velocidad', {})
    
    pdf.cell(0, 6, f"- Frenadas Bruscas: {comportamiento.get('frenadas_bruscas', 0)}", 0, 1)
    pdf.cell(0, 6, f"- Volantazos (Correcciones): {comportamiento.get('volantazos', 0)}", 0, 1)
    pdf.cell(0, 6, f"- Aceleraciones Bruscas: {comportamiento.get('aceleraciones_bruscas', 0)}", 0, 1)
    pdf.cell(0, 6, f"- Ajustes de Elevación: {comportamiento.get('ajustes_elevacion', 0)}", 0, 1)
    pdf.cell(0, 6, f"- Velocidad Máxima: {vel.get('velocidad_maxima', 0):.2f} Km/h", 0, 1)

    pdf.output(ruta_guardado)

# =============================================================================
# GENERADOR DE PDF PARA REPORTE DE EVOLUCIÓN
# =============================================================================
def crear_reporte_evolucion_pdf(datos_iniciales: Dict, datos_finales: Dict, ruta_guardado: Union[str, io.BytesIO]):
    """
    Crea un PDF profesional para un reporte de evolución comparativo.
    """
    pdf = PDF()
    pdf.add_page()
    
    # --- Helper para el veredicto ---
    def generar_veredicto(v_inicial, v_final):
        delta = v_final - v_inicial
        if delta > 15: return "MEJORA SIGNIFICATIVA"
        if delta > 1: return "MEJORA MODERADA"
        if delta < -1: return "REGRESIÓN DETECTADA"
        return "RENDIMIENTO ESTANCADO"

    # --- Sección de Veredicto ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Reporte de Evolución del Operario', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, f"Operario: {datos_iniciales.get('nombre_operador', 'N/A')}", 0, 1)
    veredicto = generar_veredicto(datos_iniciales.get('puntaje_final', 0), datos_finales.get('puntaje_final', 0))
    pdf.set_font('Arial', 'B', 11)
    pdf.cell(0, 8, f"Veredicto General: {veredicto}", 0, 1)
    pdf.ln(5)

    # --- Sección de Perfil ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Evolución del Perfil de Comportamiento', 0, 1, 'L')
    pdf.set_font('Arial', '', 11)
    pdf.cell(0, 6, f"- Perfil Inicial: {datos_iniciales.get('perfil_operador', 'N/A').upper()}", 0, 1)
    pdf.cell(0, 6, f"- Perfil Final:   {datos_finales.get('perfil_operador', 'N/A').upper()}", 0, 1)
    pdf.ln(5)

    # --- Tabla Comparativa ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, 'Tabla Comparativa de Métricas', 0, 1, 'L')
    pdf.set_font('Arial', 'B', 10)
    
    col_width = [70, 30, 30, 40]
    pdf.cell(col_width[0], 8, 'Métrica', 1, 0, 'C')
    pdf.cell(col_width[1], 8, 'Inicial', 1, 0, 'C')
    pdf.cell(col_width[2], 8, 'Final', 1, 0, 'C')
    pdf.cell(col_width[3], 8, 'Delta', 1, 1, 'C')
    pdf.set_font('Arial', '', 10)

    def draw_row(metric, v_inicial, v_final, is_score=False):
        delta = v_final - v_inicial
        sign = '+' if delta >= 0 else ''
        if is_score:
            arrow = "(+)" if delta > 0 else "(-)" if delta < 0 else " "
            if delta > 0: pdf.set_text_color(0, 128, 0)
            elif delta < 0: pdf.set_text_color(255, 0, 0)
        else:
            arrow = "(-)" if delta < 0 else "(+)" if delta > 0 else " "
            if delta < 0: pdf.set_text_color(0, 128, 0)
            elif delta > 0: pdf.set_text_color(255, 0, 0)
        if delta == 0:
             pdf.set_text_color(0, 0, 0)
        pdf.cell(col_width[0], 8, metric, 1, 0, 'L')
        pdf.cell(col_width[1], 8, f"{v_inicial:.2f}", 1, 0, 'C')
        pdf.cell(col_width[2], 8, f"{v_final:.2f}", 1, 0, 'C')
        pdf.cell(col_width[3], 8, f"{sign}{delta:.2f} {arrow}", 1, 1, 'C')
        pdf.set_text_color(0, 0, 0)

    draw_row('Puntaje Final', datos_iniciales.get('puntaje_final', 0), datos_finales.get('puntaje_final', 0), is_score=True)
    draw_row('Penalizaciones Totales', datos_iniciales.get('penalizaciones_totales', 0), datos_finales.get('penalizaciones_totales', 0))
    
    pdf.output(ruta_guardado)

