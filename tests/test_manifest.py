# test_manifest.py - Script para probar el nuevo pipeline basado en manifest

import pandas as pd
import sqlite3
from pathlib import Path

def test_manifest_structure():
    """Prueba que el manifest.csv tenga la estructura correcta."""
    
    print("🧪 Probando estructura del manifest.csv...")
    
    try:
        # Leer el manifest
        df = pd.read_csv('manifest.csv')
        
        # Verificar columnas
        required_columns = ['nombre_archivo_pdf', 'perfil_etiquetado', 'id_operador', 'nombre_ejercicio', 'fecha_creacion']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            print(f"❌ Faltan columnas: {missing_columns}")
            return False
        
        print(f"✅ Estructura correcta. {len(df)} registros encontrados.")
        
        # Mostrar resumen de datos
        print(f"\n📊 RESUMEN DEL MANIFEST:")
        print(f"   Operadores únicos: {df['id_operador'].nunique()}")
        print(f"   Perfiles únicos: {df['perfil_etiquetado'].nunique()}")
        print(f"   Ejercicios únicos: {df['nombre_ejercicio'].nunique()}")
        
        # Mostrar distribución de perfiles
        print(f"\n🎯 DISTRIBUCIÓN DE PERFILES:")
        perfil_counts = df['perfil_etiquetado'].value_counts()
        for perfil, count in perfil_counts.items():
            print(f"   {perfil}: {count} reportes")
        
        # Verificar archivos PDF
        print(f"\n📁 VERIFICANDO ARCHIVOS PDF:")
        reports_dir = Path('data/reports')
        missing_files = []
        
        for _, row in df.iterrows():
            pdf_path = reports_dir / row['nombre_archivo_pdf']
            if not pdf_path.exists():
                missing_files.append(row['nombre_archivo_pdf'])
        
        if missing_files:
            print(f"❌ Archivos PDF faltantes ({len(missing_files)}):")
            for file in missing_files:
                print(f"   - {file}")
            return False
        else:
            print(f"✅ Todos los archivos PDF existen en {reports_dir}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error al probar manifest: {e}")
        return False

def test_database_schema():
    """Verifica que la base de datos tenga la columna perfil_operador."""
    
    print("\n🗃️  Probando schema de la base de datos...")
    
    try:
        conn = sqlite3.connect('database/titan.db')
        cursor = conn.cursor()
        
        # Verificar estructura de la tabla Sesiones
        cursor.execute("PRAGMA table_info(Sesiones)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'perfil_operador' not in column_names:
            print("❌ La columna 'perfil_operador' no existe en la tabla Sesiones.")
            print("   Ejecuta el script de actualización del schema primero.")
            return False
        
        print("✅ La columna 'perfil_operador' existe en la tabla Sesiones.")
        print(f"   Columnas totales: {len(column_names)}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Error al verificar schema: {e}")
        return False

def run_tests():
    """Ejecuta todas las pruebas."""
    
    print("="*60)
    print("🧪 PRUEBAS DEL PIPELINE BASADO EN MANIFEST")
    print("="*60)
    
    test1 = test_manifest_structure()
    test2 = test_database_schema()
    
    print(f"\n{'='*60}")
    if test1 and test2:
        print("✅ ¡TODAS LAS PRUEBAS PASARON! El pipeline está listo para usar.")
        print("   Ejecuta: python main.py")
    else:
        print("❌ Algunas pruebas fallaron. Revisa los errores anteriores.")
    print("="*60)

if __name__ == "__main__":
    run_tests()