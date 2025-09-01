# setup_database.py
# Script para configurar la base de datos del Proyecto Titán.
# Lee y ejecuta el archivo schema.sql para crear la estructura completa.

import sqlite3
import os
from pathlib import Path

def setup_database():
    """
    Crea y configura la base de datos ejecutando el script schema.sql.
    """
    db_path = Path("database/titan.db")
    schema_path = Path("database/schema.sql")
    
    # Crear el directorio 'database' si no existe
    db_path.parent.mkdir(exist_ok=True)
    
    print("🚀 Configurando la base de datos del Proyecto Titán...")
    
    if not schema_path.exists():
        print(f"❌ ERROR: No se encontró el archivo de esquema en '{schema_path}'.")
        return False

    try:
        # Conectar a la base de datos (se creará si no existe)
        with sqlite3.connect(db_path) as conn:
            print(f"✅ Conexión exitosa a '{db_path}'.")
            
            # Leer el contenido del archivo schema.sql
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_script = f.read()
            
            # Ejecutar el script completo para crear/recrear las tablas
            conn.executescript(schema_script)
            print("🔧 Estructura de la base de datos aplicada desde schema.sql.")
            
        print("🎉 ¡Base de datos configurada exitosamente!")
        return True
        
    except sqlite3.Error as e:
        print(f"❌ ERROR CRÍTICO al configurar la base de datos: {e}")
        return False

if __name__ == "__main__":
    # Eliminar la base de datos antigua si existe, para asegurar un inicio limpio.
    db_file = Path("database/titan.db")
    if db_file.exists():
        print(f"🔥 Eliminando base de datos antigua en '{db_file}'...")
        os.remove(db_file)
        
    setup_database()
