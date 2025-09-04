# core/reporter.py
# Proyecto Titán - Módulo de Generación de Reportes
# Versión: 4.0 (Con Feedback Narrativo Automatizado)

import sqlite3
import os
from typing import Dict, Any, List

from core.db_manager import get_db_connection

# Definir la ruta a la base de datos
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'titan.db')

# =============================================================================
# NUEVA FUNCIÓN: MOTOR DE REGLAS PARA FEEDBACK
# =============================================================================
def generar_feedback_narrativo(datos_analisis: Dict[str, Any]) -> List[str]:
    """
    Analiza las métricas de comportamiento y genera una lista de frases
    de feedback accionable para el instructor.
    """
    feedback = []
    ac = datos_analisis.get('analisis_comportamiento', {})
    vel = ac.get('metricas_velocidad', {})
    perfil = datos_analisis.get('perfil_predicho', 'N/A')

    # Regla 1: Frenadas Bruscas
    if ac.get('frenadas_bruscas', 0) > 10:
        feedback.append("- Se detectaron numerosas frenadas bruscas. Recomendar al operario trabajar en la anticipación y el uso progresivo del freno.")

    # Regla 2: Volantazos
    if ac.get('volantazos', 0) > 8:
        feedback.append("- El uso del volante es muy reactivo, indicando posibles correcciones bruscas. Sugerir giros más suaves y planificados.")

    # Regla 3: Aceleraciones Bruscas
    if ac.get('aceleraciones_bruscas', 0) > 10:
        feedback.append("- El operario tiende a acelerar de forma abrupta. Fomentar un control más suave del acelerador para mejorar la estabilidad de la carga.")

    # Regla 4: Excesos de Velocidad
    if vel.get('incidentes_de_velocidad', 0) > 5:
        feedback.append(f"- Se registraron {int(vel.get('incidentes_de_velocidad', 0))} incidentes de exceso de velocidad. Reforzar la importancia de mantener la velocidad dentro de los límites seguros.")

    # Regla 5: Dudas con la Elevación
    if ac.get('ajustes_elevacion', 0) > 20:
        feedback.append("- Hay un exceso de micro-ajustes en la elevación de la horquilla, lo que puede indicar inseguridad. Practicar la aproximación a estanterías a la altura correcta.")

    # Regla 6: Velocidad muy baja (solo si es Ineficiente)
    if vel.get('velocidad_media', 0) < 2.0 and perfil == 'Ineficiente':
        feedback.append("- La velocidad operativa es consistentemente baja, lo que sugiere falta de confianza. Fomentar la fluidez y la práctica en circuito abierto.")
        
    # Regla 7: Perfil "Apurado"
    if perfil == 'Apurado':
        feedback.append("- El perfil general es 'Apurado'. Es crucial reforzar los procedimientos de seguridad y la importancia de la calma sobre la velocidad.")

    return feedback


# =============================================================================
# FUNCIÓN PRINCIPAL DE REPORTE INDIVIDUAL (ACTUALIZADA)
# =============================================================================
def generar_texto_reporte_individual(datos_analisis: Dict[str, Any]) -> str:
    """
    Toma un diccionario con todos los datos de un análisis y devuelve
    el string de texto formateado, incluyendo el nuevo feedback narrativo.
    """
    info_sesion = datos_analisis.get('info_sesion', {})
    perfil_predicho = datos_analisis.get('perfil_predicho', 'No determinado')
    ruta_recomendada = datos_analisis.get('ruta_recomendada')
    analisis_comportamiento = datos_analisis.get('analisis_comportamiento', {})
    
    # Construcción del texto del reporte
    reporte = f"✅ DIAGNÓSTICO Y ANÁLISIS DE COMPORTAMIENTO\n" + "="*50
    reporte += f"\n\n👤 OPERARIO: {info_sesion.get('nombre_operador', 'N/A')}"
    reporte += f"\n🎓 CLASE: {info_sesion.get('nombre_clase', 'N/A')}"
    reporte += f"\n💯 PUNTAJE FINAL: {info_sesion.get('puntaje_final', 0):.2f}"
    
    reporte += f"\n\n" + "-"*50 + "\n🧠 PREDICCIÓN DEL PERFIL DE COMPORTAMIENTO:\n" + "-"*50
    reporte += f"\n\n   El perfil detectado es:\n\n   >>>>>   {perfil_predicho.upper()}   <<<<<"

    if ruta_recomendada:
        ejercicios = "\n".join([f"  - {ejercicio}" for ejercicio in ruta_recomendada.get('ejercicios', [])])
        reporte += f"\n\n" + "-"*50 + "\n📚 RUTA DE APRENDIZAJE RECOMENDADA:\n" + "-"*50
        reporte += f"\n\n   **{ruta_recomendada.get('titulo', 'Sin título')}**\n\n   EJERCICIOS SUGERIDOS:\n{ejercicios}"
    
    reporte += f"\n\n" + "-"*50 + "\n🔬 ANÁLISIS DE TELEMETRÍA DETALLADO:\n" + "-"*50
    ac = analisis_comportamiento
    vel = ac.get('metricas_velocidad', {})
    reporte += f"\n\n- Frenadas Bruscas Detectadas: {ac.get('frenadas_bruscas', 0)}"
    reporte += f"\n- Volantazos (Correcciones Bruscas): {ac.get('volantazos', 0)}"
    reporte += f"\n- Aceleraciones Bruscas: {ac.get('aceleraciones_bruscas', 0)}"
    reporte += f"\n- Ajustes de Elevación (Dudas): {ac.get('ajustes_elevacion', 0)}"
    reporte += f"\n- Ajustes de Inclinación (Dudas): {ac.get('ajustes_inclinacion', 0)}"
    reporte += f"\n- Velocidad Media: {vel.get('velocidad_media', 0):.2f} Km/h"
    reporte += f"\n- Velocidad Máxima Alcanzada: {vel.get('velocidad_maxima', 0):.2f} Km/h"
    reporte += f"\n- Incidentes de Exceso de Velocidad: {int(vel.get('incidentes_de_velocidad', 0))}"

    # --- AÑADIR LA NUEVA SECCIÓN DE FEEDBACK NARRATIVO ---
    feedback_narrativo = generar_feedback_narrativo(datos_analisis)
    if feedback_narrativo:
        reporte += f"\n\n" + "-"*50 + "\n💬 FEEDBACK NARRATIVO RECOMENDADO:\n" + "-"*50
        puntos_feedback = "\n".join(feedback_narrativo)
        reporte += f"\n\n{puntos_feedback}"

    return reporte


# =============================================================================
# FUNCIÓN DE REPORTE DE EVOLUCIÓN (SIN CAMBIOS FUNCIONALES)
# =============================================================================
def generar_reporte_evolucion(id_sesion_inicial: int, id_sesion_final: int) -> str:
    """ Genera el texto del reporte de evolución comparando dos sesiones. """
    # Esta función no requiere cambios, pero se mantiene por completitud.
    # (Se asume que existe una función auxiliar para obtener los datos)
    def _get_raw_data(id_sesion):
        with get_db_connection(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (id_sesion,)).fetchone())

    data_inicial = _get_raw_data(id_sesion_inicial)
    data_final = _get_raw_data(id_sesion_final)
    
    if not data_inicial or not data_final:
        return "❌ ERROR: No se pudieron encontrar los datos para una o ambas sesiones."

    # Lógica de comparación y formateo del reporte de evolución...
    reporte = f"📊 REPORTE DE EVOLUCIÓN\n" + "="*50
    reporte += f"\n\n👤 OPERARIO: {data_inicial.get('nombre_operador', 'N/A')}"
    reporte += f"\n\n- Perfil Inicial: {data_inicial.get('perfil_operador', 'N/A').upper()}"
    reporte += f"\n- Perfil Final:   {data_final.get('perfil_operador', 'N/A').upper()}"
    reporte += f"\n\n- Puntaje Inicial: {data_inicial.get('puntaje_final', 0):.2f}"
    reporte += f"\n- Puntaje Final:   {data_final.get('puntaje_final', 0):.2f}"
    delta = data_final.get('puntaje_final', 0) - data_inicial.get('puntaje_final', 0)
    reporte += f"\n\n  MEJORA: {delta:+.2f} puntos"

    return reporte
