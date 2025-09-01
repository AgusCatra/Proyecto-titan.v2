# core/report_generator.py
# Proyecto Titán - Módulo de Generación de Reportes en PDF
# Versión: 1.0
# Objetivo: Crear un reporte de desempeño profesional en formato PDF.

from fpdf import FPDF
from datetime import datetime
from typing import Dict, Any

class ReportePDF(FPDF):
    """
    Clase para generar el reporte en PDF, heredando de FPDF para
    personalizar la cabecera y el pie de página.
    """
    def header(self):
        # Logo (opcional, si tuvieras un archivo de logo)
        # self.image('logo.png', 10, 8, 33)
        self.set_font('Arial', 'B', 16)
        self.cell(0, 10, 'Reporte de Desempeño y Plan de Acción', 0, 1, 'C')
        self.set_font('Arial', 'I', 10)
        self.cell(0, 10, 'Generado por Proyecto Titán', 0, 1, 'C')
        self.ln(10) # Salto de línea

    def footer(self):
        self.set_y(-15) # Posición a 1.5 cm del final
        self.set_font('Arial', 'I', 8)
        fecha_actual = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.cell(0, 10, f'Fecha de Generación: {fecha_actual}', 0, 0, 'L')
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'R')

    def seccion_titulo(self, titulo):
        self.set_font('Arial', 'B', 12)
        self.set_fill_color(230, 230, 230) # Un fondo gris claro
        self.cell(0, 8, titulo, 0, 1, 'L', fill=True)
        self.ln(4)

    def contenido_simple(self, etiqueta, valor):
        self.set_font('Arial', 'B', 11)
        self.cell(50, 8, f'{etiqueta}:', 0, 0)
        self.set_font('Arial', '', 11)
        self.cell(0, 8, str(valor), 0, 1)

    def tabla_telemetria(self, datos_telemetria):
        self.set_font('Arial', 'B', 10)
        # Ancho de las celdas
        col_width = self.w / 2.3 
        line_height = self.font_size * 2

        # Cabecera de la tabla
        self.cell(col_width, line_height, 'Métrica de Comportamiento', 1, 0, 'C')
        self.cell(col_width, line_height, 'Resultado', 1, 1, 'C')
        
        self.set_font('Arial', '', 10)
        # Contenido de la tabla
        metricas = {
            "Frenadas Bruscas": datos_telemetria['frenadas_bruscas'],
            "Volantazos": datos_telemetria['volantazos'],
            "Aceleraciones Bruscas": datos_telemetria['aceleraciones_bruscas'],
            "Ajustes de Elevación (Dudas)": datos_telemetria['ajustes_elevacion'],
            "Ajustes de Inclinación (Dudas)": datos_telemetria['ajustes_inclinacion'],
            "Velocidad Media": f"{datos_telemetria['metricas_velocidad']['velocidad_media']:.2f} Km/h",
            "Velocidad Máxima": f"{datos_telemetria['metricas_velocidad']['velocidad_maxima']:.2f} Km/h"
        }
        for metrica, valor in metricas.items():
            self.cell(col_width, line_height, metrica, 1, 0)
            self.cell(col_width, line_height, str(valor), 1, 1, 'C')

    def ruta_aprendizaje(self, ruta):
        self.set_font('Arial', 'B', 11)
        self.cell(0, 8, ruta['titulo'], 0, 1)
        self.ln(2)
        self.set_font('Arial', '', 10)
        for ejercicio in ruta['ejercicios']:
            self.multi_cell(0, 6, f'  - {ejercicio}')


def crear_reporte_pdf(datos_analisis: Dict[str, Any], ruta_guardado: str):
    """
    Función principal que orquesta la creación del reporte en PDF.
    """
    try:
        pdf = ReportePDF()
        pdf.add_page()
        
        # --- Sección 1: Datos Generales ---
        pdf.seccion_titulo('1. Datos de la Sesión')
        pdf.contenido_simple('Operario', datos_analisis['info_sesion']['nombre_operador'])
        pdf.contenido_simple('Clase / Empresa', datos_analisis['info_sesion'].get('nombre_clase', 'N/A'))
        pdf.contenido_simple('Ejercicio', datos_analisis['info_sesion']['nombre_ejercicio'])
        pdf.ln(5)

        # --- Sección 2: Diagnóstico General ---
        pdf.seccion_titulo('2. Diagnóstico General (IA)')
        pdf.contenido_simple('Puntaje Final', f"{datos_analisis['info_sesion']['puntaje_final']:.2f}")
        pdf.contenido_simple('Perfil de Comportamiento', datos_analisis['perfil_predicho'].upper())
        pdf.ln(5)

        # --- Sección 3: Análisis de Comportamiento (Telemetría) ---
        pdf.seccion_titulo('3. Análisis Detallado de Telemetría')
        pdf.tabla_telemetria(datos_analisis['analisis_comportamiento'])
        pdf.ln(5)

        # --- Sección 4: Ruta de Aprendizaje ---
        if datos_analisis['ruta_recomendada']:
            pdf.seccion_titulo('4. Ruta de Aprendizaje Recomendada')
            pdf.ruta_aprendizaje(datos_analisis['ruta_recomendada'])
        
        pdf.output(ruta_guardado)
        print(f"✅ Reporte en PDF guardado exitosamente en: {ruta_guardado}")
        return True

    except Exception as e:
        print(f"❌ ERROR al generar el reporte en PDF: {e}")
        traceback.print_exc()
        return False
