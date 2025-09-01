# analisis_mvp.py
# Script de análisis comparativo de perfiles - Proyecto Titán Fase 3
# Versión: 1.1 - Con integración GUI

import sqlite3
import pandas as pd
from pathlib import Path
import io
import sys
from contextlib import redirect_stdout

# Configuración
DB_PATH = 'database/titan.db'

def conectar_base_datos():
    """Establece conexión con la base de datos titan.db."""
    try:
        if not Path(DB_PATH).exists():
            raise FileNotFoundError(f"La base de datos '{DB_PATH}' no existe.")
        
        conn = sqlite3.connect(DB_PATH)
        return conn
    
    except sqlite3.Error as e:
        raise Exception(f"Error al conectar con la base de datos: {e}")

def cargar_datos(conn):
    """Carga las tablas Sesiones y ResumenEventos en DataFrames."""
    try:
        # Cargar tabla Sesiones
        df_sesiones = pd.read_sql_query("SELECT * FROM Sesiones", conn)
        
        # Cargar tabla ResumenEventos
        df_eventos = pd.read_sql_query("SELECT * FROM ResumenEventos", conn)
        
        # Validar que existen datos
        if df_sesiones.empty:
            raise Exception("La tabla Sesiones está vacía")
        
        if df_eventos.empty:
            raise Exception("La tabla ResumenEventos está vacía")
        
        return df_sesiones, df_eventos
    
    except Exception as e:
        raise Exception(f"Error al cargar datos: {e}")

def transformar_datos(df_sesiones, df_eventos):
    """Procesa y combina los datos para el análisis."""
    try:
        # Agregar eventos por sesión (suma de penalizaciones, recompensas, etc.)
        df_eventos_agg = df_eventos.groupby('id_sesion').agg({
            'conteo_eventos': 'sum',
            'recompensas': 'sum',
            'penalizaciones': 'sum'
        }).reset_index()
        
        # Unir (merge) los DataFrames
        df_completo = pd.merge(
            df_sesiones, 
            df_eventos_agg, 
            on='id_sesion', 
            how='inner'
        )
        
        # Validar que el merge fue exitoso
        if df_completo.empty:
            raise Exception("No se pudieron combinar los datos (sin coincidencias en id_sesion)")
        
        return df_completo
    
    except Exception as e:
        raise Exception(f"Error al transformar datos: {e}")

def generar_analisis(df_completo):
    """Genera el análisis comparativo por perfil."""
    try:
        # Agrupar por perfil_operador
        analisis = df_completo.groupby('perfil_operador').agg({
            'id_sesion': 'count',                    # Conteo de sesiones
            'puntaje_final': 'mean',                 # Puntaje promedio
            'duracion_segundos': 'mean',             # Duración promedio
            'penalizaciones': 'mean',                # Penalizaciones promedio
            'recompensas': 'mean',                   # Recompensas promedio
            'conteo_eventos': 'mean'                 # Eventos promedio
        }).round(2)
        
        # Renombrar columnas para mejor presentación
        analisis.columns = [
            'Sesiones',
            'Puntaje Prom.',
            'Duración Prom. (seg)',
            'Penalizaciones Prom.',
            'Recompensas Prom.',
            'Eventos Prom.'
        ]
        
        # Convertir duración a formato MM:SS
        analisis['Duración Prom. (MM:SS)'] = analisis['Duración Prom. (seg)'].apply(
            lambda x: f"{int(x//60):02d}:{int(x%60):02d}"
        )
        
        return analisis
    
    except Exception as e:
        raise Exception(f"Error al generar análisis: {e}")

def formatear_resultados(analisis):
    """Formatea los resultados para mostrar en GUI o consola."""
    
    output = []
    output.append("📊 ANÁLISIS COMPARATIVO DE PERFILES - PROYECTO TITÁN")
    output.append("=" * 65)
    output.append("")
    output.append("📈 RESUMEN POR PERFIL:")
    output.append("-" * 65)
    
    # Encabezados de tabla
    header = f"{'Perfil':<12} | {'Sesiones':<8} | {'Puntaje':<8} | {'Duración':<9} | {'Penaliz.':<9} | {'Recompensas':<10}"
    output.append(header)
    output.append("-" * 65)
    
    # Datos de cada perfil
    for perfil, fila in analisis.iterrows():
        row = f"{perfil:<12} | {int(fila['Sesiones']):<8} | {fila['Puntaje Prom.']:<8.2f} | {fila['Duración Prom. (MM:SS)']:<9} | {fila['Penalizaciones Prom.']:<9.2f} | {fila['Recompensas Prom.']:<10.2f}"
        output.append(row)
    
    output.append("-" * 65)
    output.append("")
    
    # Generar insights
    insights = generar_insights(analisis)
    output.extend(insights)
    
    return "\n".join(output)

def generar_insights(analisis):
    """Genera insights automáticos basados en el análisis."""
    insights = []
    insights.append("💡 INSIGHTS AUTOMÁTICOS:")
    
    try:
        # Obtener perfiles para comparación
        perfiles = analisis.index.tolist()
        
        if len(perfiles) < 2:
            insights.append("   ⚠️  Se necesitan al menos 2 perfiles para generar comparaciones")
            return insights
        
        # Comparar puntajes
        mejor_puntaje = analisis['Puntaje Prom.'].idxmax()
        peor_puntaje = analisis['Puntaje Prom.'].idxmin()
        
        insights.append(f"   🎯 Mejor rendimiento: '{mejor_puntaje}' ({analisis.loc[mejor_puntaje, 'Puntaje Prom.']:.2f} puntos)")
        insights.append(f"   ⚠️  Menor rendimiento: '{peor_puntaje}' ({analisis.loc[peor_puntaje, 'Puntaje Prom.']:.2f} puntos)")
        
        # Comparar velocidad (menor duración = más rápido)
        mas_rapido = analisis['Duración Prom. (seg)'].idxmin()
        mas_lento = analisis['Duración Prom. (seg)'].idxmax()
        
        insights.append(f"   ⚡ Más rápido: '{mas_rapido}' ({analisis.loc[mas_rapido, 'Duración Prom. (MM:SS)']})") 
        insights.append(f"   🐌 Más lento: '{mas_lento}' ({analisis.loc[mas_lento, 'Duración Prom. (MM:SS)']})")
        
        # Comparar penalizaciones (menos penalizaciones = mejor)
        menos_penalizaciones = analisis['Penalizaciones Prom.'].idxmax()  # Valores negativos, max = menos penalización
        mas_penalizaciones = analisis['Penalizaciones Prom.'].idxmin()    # Valores negativos, min = más penalización
        
        insights.append(f"   ✅ Menos errores: '{menos_penalizaciones}' ({analisis.loc[menos_penalizaciones, 'Penalizaciones Prom.']:.2f})")
        insights.append(f"   ❌ Más errores: '{mas_penalizaciones}' ({analisis.loc[mas_penalizaciones, 'Penalizaciones Prom.']:.2f})")
        
        # Estadísticas adicionales
        total_sesiones = analisis['Sesiones'].sum()
        insights.append("")
        insights.append("📊 ESTADÍSTICAS GENERALES:")
        insights.append(f"   📈 Total de sesiones analizadas: {int(total_sesiones)}")
        insights.append(f"   🎯 Perfiles únicos detectados: {len(perfiles)}")
        insights.append(f"   📊 Puntaje promedio general: {analisis['Puntaje Prom.'].mean():.2f}")
        
    except Exception as e:
        insights.append(f"   ❌ Error al generar insights: {e}")
    
    insights.append("")
    insights.append("=" * 65)
    insights.append("✨ Análisis generado por Proyecto Titán v1.0")
    
    return insights

def ejecutar_analisis_gui():
    """
    Función principal para ejecutar desde la GUI.
    Retorna el resultado como string en lugar de imprimirlo.
    """
    try:
        # Conectar a la base de datos
        conn = conectar_base_datos()
        
        try:
            # Cargar datos
            df_sesiones, df_eventos = cargar_datos(conn)
            
            # Transformar y combinar datos
            df_completo = transformar_datos(df_sesiones, df_eventos)
            
            # Generar análisis
            analisis = generar_analisis(df_completo)
            
            # Formatear resultados
            resultado = formatear_resultados(analisis)
            
            return resultado
            
        finally:
            conn.close()
    
    except Exception as e:
        return f"""
❌ ERROR DURANTE EL ANÁLISIS

💥 Detalles del error:
   {str(e)}

🔧 POSIBLES SOLUCIONES:
   • Verifica que la base de datos titan.db existe
   • Asegúrate de que contiene datos válidos
   • Ejecuta el pipeline ETL primero: python main.py
   • Revisa que la columna 'perfil_operador' existe en la tabla Sesiones

📁 ESTRUCTURA ESPERADA:
   ├── database/
   │   └── titan.db (con datos procesados)
   ├── main.py
   └── app.py
        """.strip()

def main():
    """Función principal del análisis MVP para ejecución desde terminal."""
    print("🚀 Iniciando Análisis MVP - Proyecto Titán Fase 3")
    print("="*50)
    
    try:
        resultado = ejecutar_analisis_gui()
        print(resultado)
        print(f"\n🎉 Análisis completado exitosamente!")
        
    except Exception as e:
        print(f"❌ Error durante la ejecución: {e}")

if __name__ == "__main__":
    main()