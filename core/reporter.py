# core/reporter.py
# Proyecto Titán - Módulo de Generación de Reportes
# Versión: 3.1 (Lógica de formateo desacoplada del análisis)

import sqlite3
import os
from typing import Dict, Any

# --- TAREA 3: Ya no se necesita importar las funciones de análisis aquí ---
# La lógica de análisis ahora reside completamente en behavior_analyzer.py

# Definir la ruta a la base de datos
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'database', 'titan.db')

def _get_session_data_for_evolution(id_sesion: int) -> Dict[str, Any]:
    """Recopila datos básicos y análisis de telemetría para una sesión."""
    from core.behavior_analyzer import analizar_comportamiento_completo
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        session_data = conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (id_sesion,)).fetchone()
        if not session_data: 
            return None
        
        # Combina los datos de la sesión con el análisis de comportamiento
        full_analysis = dict(session_data)
        full_analysis.update(analizar_comportamiento_completo(id_sesion))
        return full_analysis

def generar_veredicto(data_inicial: Dict, data_final: Dict) -> str:
    """Analiza los resultados de dos sesiones y genera una conclusión clara."""
    delta_puntaje = data_final['puntaje_final'] - data_inicial['puntaje_final']
    if delta_puntaje > 15 or data_final['perfil_operador'].lower() == 'eficiente':
        return "✅ MEJORA SIGNIFICATIVA"
    if delta_puntaje < -1:
        return "⚠️ REGRESIÓN DETECTADA"
    if delta_puntaje > 1:
        return "↔️ MEJORA MODERADA"
    return "ℹ️ RENDIMIENTO ESTANCADO"

def generar_reporte_evolucion(id_sesion_inicial: int, id_sesion_final: int) -> str:
    """Genera el texto del reporte de evolución, realizando el análisis necesario."""
    data_inicial = _get_session_data_for_evolution(id_sesion_inicial)
    data_final = _get_session_data_for_evolution(id_sesion_final)
    
    if not data_inicial or not data_final:
        return "❌ ERROR: No se pudieron encontrar los datos para una o ambas sesiones."
        
    veredicto = generar_veredicto(data_inicial, data_final)
    reporte = f"{veredicto}\n" + "="*50
    reporte += f"\n\n👤 OPERARIO: {data_inicial['nombre_operador']}"
    reporte += f"\n\n" + "-"*50 + "\n🧠 EVOLUCIÓN DEL PERFIL:\n" + "-"*50
    reporte += f"\n\n- Perfil Inicial: {data_inicial['perfil_operador'].upper()}"
    reporte += f"\n- Perfil Final:   {data_final['perfil_operador'].upper()}"
    reporte += f"\n\n" + "-"*50 + "\n📊 TABLA COMPARATIVA DE MÉTRICAS:\n" + "-"*50
    
    header = f"\n\n{'Métrica':<25} {'Inicial':>10} {'Final':>10} {'Delta':>12}"
    reporte += header
    reporte += "\n" + "-"*len(header.strip())

    def format_row(metric, key, is_score=False, is_sub_metric=False):
        v_inicial = data_inicial['metricas_velocidad'][key] if is_sub_metric else data_inicial[key]
        v_final = data_final['metricas_velocidad'][key] if is_sub_metric else data_final[key]
        
        delta = v_final - v_inicial
        sign = '+' if delta > 0 else ''
        
        # Indicador de mejora: para puntaje, más es mejor. Para el resto, menos es mejor.
        if is_score:
            indicador = "🔼" if delta > 0 else "🔽" if delta < 0 else "➡️"
        else:
            indicador = "🔽" if delta < 0 else "🔼" if delta > 0 else "➡️"
            
        return f"\n{metric:<25} {v_inicial:>10.2f} {v_final:>10.2f} {f'{sign}{delta:.2f} {indicador}':>12}"

    reporte += format_row('Puntaje Final', 'puntaje_final', is_score=True)
    reporte += format_row('Frenadas Bruscas', 'frenadas_bruscas')
    reporte += format_row('Volantazos', 'volantazos')
    reporte += format_row('Aceleraciones Bruscas', 'aceleraciones_bruscas')
    reporte += format_row('Ajustes Elevación', 'ajustes_elevacion')
    reporte += format_row('Ajustes Inclinación', 'ajustes_inclinacion')
    reporte += format_row('Velocidad Máxima', 'velocidad_maxima', is_sub_metric=True)
    
    return reporte

# --- TAREA 3: FUNCIÓN DE REPORTE INDIVIDUAL ACTUALIZADA ---
def generar_texto_reporte_individual(datos_analisis: Dict[str, Any]) -> str:
    """
    Toma un diccionario con todos los datos de un análisis y devuelve
    el string de texto formateado para la GUI. No realiza análisis, solo formatea.
    """
    info_sesion = datos_analisis['info_sesion']
    perfil_predicho = datos_analisis['perfil_predicho']
    ruta_recomendada = datos_analisis['ruta_recomendada']
    analisis_comportamiento = datos_analisis['analisis_comportamiento']
    
    # Construcción del texto del reporte
    resultado_texto = f"✅ DIAGNÓSTICO Y ANÁLISIS DE COMPORTAMIENTO\n" + "="*50
    resultado_texto += f"\n\n👤 OPERARIO: {info_sesion['nombre_operador']}"
    resultado_texto += f"\n🎓 CLASE: {info_sesion.get('nombre_clase', 'N/A')}"
    resultado_texto += f"\n💯 PUNTAJE FINAL: {info_sesion['puntaje_final']:.2f}"
    
    resultado_texto += f"\n\n" + "-"*50 + "\n🧠 PREDICCIÓN DEL MODELO DE IA:\n" + "-"*50
    resultado_texto += f"\n\n   El perfil de comportamiento detectado es:\n\n   >>>>>   {perfil_predicho.upper()}   <<<<<"

    if ruta_recomendada:
        ejercicios_formateados = "\n".join([f"  - {ejercicio}" for ejercicio in ruta_recomendada['ejercicios']])
        resultado_texto += f"\n\n" + "-"*50 + "\n📚 RUTA DE APRENDIZAJE RECOMENDADA:\n" + "-"*50
        resultado_texto += f"\n\n   **{ruta_recomendada['titulo']}**\n\n   EJERCICIOS SUGERIDOS:\n{ejercicios_formateados}"
    
    # --- TAREA 3: Formatear las nuevas métricas recibidas ---
    resultado_texto += f"\n\n" + "-"*50 + "\n🔬 ANÁLISIS DE COMPORTAMIENTO (TELEMETRÍA):\n" + "-"*50
    ac = analisis_comportamiento
    vel = ac['metricas_velocidad']
    resultado_texto += f"\n\n- Frenadas Bruscas Detectadas: {ac['frenadas_bruscas']}"
    resultado_texto += f"\n- Volantazos (Cambios de Dirección Agresivos): {ac['volantazos']}"
    resultado_texto += f"\n- Aceleraciones Bruscas: {ac['aceleraciones_bruscas']}"
    resultado_texto += f"\n- Micro-ajustes de Elevación (Dudas): {ac['ajustes_elevacion']}"
    resultado_texto += f"\n- Micro-ajustes de Inclinación (Dudas): {ac['ajustes_inclinacion']}"
    resultado_texto += f"\n- Velocidad Media de Operación: {vel['velocidad_media']:.2f} Km/h"
    resultado_texto += f"\n- Velocidad Máxima Alcanzada: {vel['velocidad_maxima']:.2f} Km/h"
    resultado_texto += f"\n- Incidentes por Exceso de Velocidad (>8 Km/h): {int(vel['incidentes_de_velocidad'])}"

    return resultado_texto
