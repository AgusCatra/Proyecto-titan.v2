# main.py - Versión adaptada para manifest.csv

import os
import sys
import pandas as pd
from pathlib import Path
from core.pdf_parser import parse_pdf_report
from core.db_manager import (
    get_db_connection,
    check_if_file_processed,
    insert_session,
    insert_summary_events
)

# Configuración
CONFIG = {
    'reports_dir': 'data/reports',
    'db_path': 'database/titan.db',
    'manifest_path': 'manifest.csv'
}

def main():
    """Función principal del pipeline ETL basado en manifest.csv."""
    print("🚀 Iniciando pipeline ETL basado en manifest.csv...")
    
    # Validar estructura de directorios y archivos
    if not _validate_structure():
        sys.exit(1)
    
    # Leer y validar manifest.csv
    manifest_df = _load_manifest()
    if manifest_df is None or manifest_df.empty:
        print("📭 No se encontraron registros para procesar en manifest.csv.")
        sys.exit(0)
    
    print(f"📂 Encontrados {len(manifest_df)} registros en manifest.csv.")
    
    # Procesar archivos según manifest
    stats = _process_manifest_files(manifest_df)
    
    # Mostrar resumen
    _show_processing_summary(stats)

def _validate_structure() -> bool:
    """Valida que existan los directorios y archivos necesarios."""
    reports_path = Path(CONFIG['reports_dir'])
    db_path = Path(CONFIG['db_path'])
    manifest_path = Path(CONFIG['manifest_path'])
    
    if not reports_path.exists():
        print(f"❌ ERROR: El directorio de reportes '{CONFIG['reports_dir']}' no existe.")
        return False
    
    if not db_path.parent.exists():
        print(f"❌ ERROR: El directorio de base de datos '{db_path.parent}' no existe.")
        return False
    
    if not db_path.exists():
        print(f"⚠️  ADVERTENCIA: La base de datos '{CONFIG['db_path']}' no existe.")
        print("   Ejecute 'python setup_database.py' primero.")
        return False
    
    if not manifest_path.exists():
        print(f"❌ ERROR: El archivo manifest '{CONFIG['manifest_path']}' no existe.")
        return False
    
    return True

def _load_manifest() -> pd.DataFrame:
    """Carga y valida el archivo manifest.csv."""
    try:
        print(f"📋 Cargando manifest desde '{CONFIG['manifest_path']}'...")
        
        # Leer el CSV
        df = pd.read_csv(CONFIG['manifest_path'])
        
        # Validar columnas requeridas
        required_columns = ['nombre_archivo_pdf', 'perfil_etiquetado', 'id_operador', 'nombre_ejercicio', 'fecha_creacion']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            print(f"❌ ERROR: Faltan las siguientes columnas en manifest.csv: {missing_columns}")
            return None
        
        # Filtrar filas con datos faltantes críticos
        initial_count = len(df)
        df = df.dropna(subset=['nombre_archivo_pdf', 'perfil_etiquetado'])
        final_count = len(df)
        
        if initial_count > final_count:
            print(f"⚠️  Se omitieron {initial_count - final_count} filas con datos faltantes.")
        
        print(f"✅ Manifest cargado exitosamente: {final_count} registros válidos.")
        return df
        
    except Exception as e:
        print(f"❌ ERROR al cargar manifest.csv: {e}")
        return None

def _process_manifest_files(manifest_df: pd.DataFrame) -> dict:
    """Procesa los archivos PDF según el manifest."""
    stats = {
        'processed': 0,
        'skipped': 0,
        'errors': 0,
        'total': len(manifest_df)
    }
    
    try:
        with get_db_connection(CONFIG['db_path']) as conn:
            # Iterar sobre cada fila del manifest
            for index, row in manifest_df.iterrows():
                result = _process_manifest_row(conn, row, index + 1)
                stats[result] += 1
                
            conn.commit()
            print("💾 Cambios guardados en la base de datos.")
            
    except Exception as e:
        print(f"❌ ERROR CRÍTICO en la conexión a la base de datos: {e}")
        stats['errors'] = stats['total']
    
    return stats

def _process_manifest_row(conn, row: pd.Series, row_number: int) -> str:
    """Procesa una fila individual del manifest."""
    
    # Extraer datos de la fila
    nombre_archivo_pdf = row['nombre_archivo_pdf']
    perfil_etiquetado = row['perfil_etiquetado']
    id_operador = row['id_operador']
    nombre_ejercicio = row['nombre_ejercicio']
    
    print(f"\n--- Procesando registro {row_number}: {nombre_archivo_pdf} ---")
    print(f"    Operador: {id_operador} | Perfil: {perfil_etiquetado} | Ejercicio: {nombre_ejercicio}")
    
    try:
        # Verificar si ya fue procesado
        if check_if_file_processed(conn, nombre_archivo_pdf):
            print(f"🔵 OMISIÓN: El archivo '{nombre_archivo_pdf}' ya fue procesado anteriormente.")
            return 'skipped'
        
        # Construir ruta completa al PDF
        full_pdf_path = Path(CONFIG['reports_dir']) / nombre_archivo_pdf
        
        # Verificar que el archivo PDF existe
        if not full_pdf_path.exists():
            print(f"❌ ERROR: El archivo PDF '{full_pdf_path}' no existe.")
            return 'errors'
        
        # Parsear el PDF
        parsed_data = parse_pdf_report(str(full_pdf_path))
        
        if not parsed_data or not parsed_data.get("session_data"):
            print("  - ⚠️  No se pudieron extraer datos válidos del archivo.")
            return 'errors'
        
        # Insertar sesión con el perfil etiquetado
        session_id = insert_session(conn, parsed_data, perfil_etiquetado)
        
        if not session_id:
            print("  - ⚠️  Hubo un problema al registrar la sesión en la base de datos.")
            return 'errors'
        
        # Insertar eventos de resumen
        summary_events = parsed_data.get("summary_events", [])
        if summary_events:
            insert_summary_events(conn, session_id, summary_events)
        
        print(f"✅ ÉXITO: Archivo procesado correctamente.")
        print(f"    📊 Datos: Operador={id_operador}, Perfil={perfil_etiquetado}, Ejercicio={nombre_ejercicio}")
        return 'processed'
        
    except Exception as e:
        print(f"❌ ERROR al procesar el registro {row_number}: {e}")
        return 'errors'

def _show_processing_summary(stats: dict):
    """Muestra un resumen del procesamiento."""
    print("\n" + "="*60)
    print("📊 RESUMEN DEL PROCESAMIENTO (MANIFEST-BASED)")
    print("="*60)
    print(f"📄 Total de registros: {stats['total']}")
    print(f"✅ Procesados exitosamente: {stats['processed']}")
    print(f"🔵 Omitidos (ya procesados): {stats['skipped']}")
    print(f"❌ Errores: {stats['errors']}")
    
    if stats['total'] > 0:
        success_rate = ((stats['processed'] + stats['skipped']) / stats['total']) * 100
        print(f"📈 Tasa de éxito: {success_rate:.1f}%")
    
    print(f"\n💡 Fuente de datos: {CONFIG['manifest_path']}")
    print("🏁 Proceso ETL completado.")

if __name__ == "__main__":
    main()