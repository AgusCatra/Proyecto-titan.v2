# -*- coding: utf-8 -*-
"""
Proyecto Titán - Fase 5: Integración de IA
Script: entrenador_ia.py
Rol: Asistente de Ejecución Técnica
Misión: Entrenar un modelo de Machine Learning para clasificar el perfil de 
        comportamiento de los operarios de autoelevadores.
Versión: 3.0 - Añadido pivoteo de datos para alinear la estructura de la BD
               con las necesidades del modelo.
"""

# --- PASO 0: IMPORTACIÓN DE LIBRERÍAS ---
import sqlite3
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score
import joblib
import os

# --- PASO 1: DEFINICIÓN DE RUTAS Y CONEXIÓN A LA BASE DE DATOS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, 'database', 'titan.db')
MODEL_DIR = os.path.join(BASE_DIR, 'models')
MODEL_FILE = os.path.join(MODEL_DIR, 'modelo_clasificador.joblib')

def entrenar_modelo():
    """
    Función principal que orquesta todo el proceso de entrenamiento del modelo.
    """
    print("--- Iniciando el entrenamiento del modelo de IA para el Proyecto Titán ---")

    if not os.path.exists(DB_FILE):
        print(f"Error: La base de datos no se encontró en la ruta esperada: '{DB_FILE}'")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        df_sesiones = pd.read_sql_query("SELECT * FROM Sesiones", conn)
        df_eventos = pd.read_sql_query("SELECT * FROM ResumenEventos", conn)
        conn.close()
        print(f"Datos cargados: {len(df_sesiones)} sesiones y {len(df_eventos)} filas de eventos.")

    except Exception as e:
        print(f"Error al cargar datos desde la base de datos: {e}")
        return

    # --- NUEVO PASO 2: PIVOTEO DE DATOS DE EVENTOS ---
    # La tabla ResumenEventos tiene un formato 'largo'. Necesitamos transformarla
    # a un formato 'ancho' donde cada tipo de evento sea una columna.
    print("Transformando datos de eventos (pivoteo)...")
    if df_eventos.empty:
        print("Error: La tabla de eventos está vacía. No se puede continuar.")
        return

    # Pivotamos para tener los conteos y penalizaciones como columnas separadas por tipo de evento
    df_eventos_pivot = df_eventos.pivot_table(
        index='id_sesion', 
        columns='tipo_evento', 
        values=['conteo_eventos', 'penalizaciones'],
        aggfunc='sum'
    ).fillna(0)

    # Aplanamos los nombres de las columnas (ej: de ('conteo_eventos', 'Collision') a 'conteo_eventos_Collision')
    df_eventos_pivot.columns = [f'{val}_{col}'.replace(' ', '_') for val, col in df_eventos_pivot.columns]
    
    # Unimos los datos de sesión con los datos de eventos ya pivoteados
    df_completo = pd.merge(df_sesiones, df_eventos_pivot, on='id_sesion', how='left').fillna(0)

    if df_completo.isnull().values.any():
        print("Se encontraron valores nulos. Se eliminarán las filas correspondientes.")
        df_completo.dropna(inplace=True)

    print(f"Dataset combinado y limpio. Total de registros para entrenamiento: {len(df_completo)}")

    # --- PASO 3: PREPARACIÓN DE DATOS PARA EL ENTRENAMIENTO ---
    # MODIFICACIÓN: Se actualiza la lista de características para que coincida
    # con los nombres de las columnas generadas en el pivoteo.
    
    # Primero, definimos las características base que sabemos que existen
    features = ['puntaje_final', 'duracion_segundos']
    
    # Luego, agregamos dinámicamente las columnas de eventos que existen en el dataframe
    # Esto hace el script más robusto si en el futuro aparecen nuevos tipos de eventos.
    event_features = [col for col in df_completo.columns if col.startswith('conteo_eventos_') or col.startswith('penalizaciones_')]
    features.extend(event_features)
    
    # Verificamos que todas las características seleccionadas existan en el DataFrame
    missing_features = [f for f in features if f not in df_completo.columns]
    if missing_features:
        print(f"Error Crítico: Las siguientes columnas de características no se encontraron después del pivoteo: {missing_features}")
        return

    X = df_completo[features]
    y = df_completo['perfil_operador']

    print("Características (X) y etiqueta (y) definidas.")
    print(f"Características seleccionadas para el modelo: {features}")

    # --- PASO 4: DIVISIÓN DEL DATASET ---
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print("Dataset dividido en conjuntos de entrenamiento y prueba.")

    # --- PASO 5: INICIALIZACIÓN Y ENTRENAMIENTO DEL MODELO ---
    print("Inicializando y entrenando el modelo RandomForestClassifier...")
    modelo = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    modelo.fit(X_train, y_train)
    print("¡Modelo entrenado exitosamente!")

    # --- PASO 6: EVALUACIÓN DEL MODELO ---
    print("\n--- Evaluación del Rendimiento del Modelo ---")
    y_pred = modelo.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, zero_division=0)
    print(f"Precisión (Accuracy) del modelo: {accuracy:.2f}")
    print("\nReporte de Clasificación:")
    print(report)

    # --- PASO 7: GUARDADO DEL MODELO ENTRENADO ---
    try:
        if not os.path.exists(MODEL_DIR):
            print(f"Creando directorio para modelos en: '{MODEL_DIR}'")
            os.makedirs(MODEL_DIR)
        joblib.dump(modelo, MODEL_FILE)
        print(f"\nModelo guardado exitosamente en la ruta: '{MODEL_FILE}'")
    except Exception as e:
        print(f"Error al guardar el modelo: {e}")

    print("\n--- Proceso de entrenamiento finalizado ---")

if __name__ == '__main__':
    entrenar_modelo()
