# core/telemetry_parser.py
# Proyecto Titán - Fase de Análisis de Telemetría Avanzada
# Versión: 8.0 (Configuración de Telemetría Completa)
# Objetivo: Proveer una estructura escalable para extraer datos de
#           múltiples gráficos de telemetría de un reporte PDF.

import pdfplumber
import cv2
import numpy as np
import os
from typing import List, Tuple, Optional, Dict

# --- CONFIGURACIÓN CENTRALIZADA DE GRÁFICOS ---
# Este diccionario es el "cerebro" del parser. Para añadir un nuevo gráfico,
# solo necesitamos añadir una nueva entrada aquí con sus parámetros de calibración.
GRAPH_CONFIGS = {
    "Steering": {
        "page": 9,
        "image_index": 0,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (-14.0, 14.0)
    },
    "Brake Pad": {
        "page": 6,
        "image_index": 0,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (0.0, 10.0)
    },
    "Acceleration Pad": {
        "page": 6,
        "image_index": 1,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (0.0, 10.0)
    },
    # --- NUEVAS CONFIGURACIONES ---
    "Fork Height In Mtrs": {
        "page": 8,
        "image_index": 0,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (0.0, 5.0) # Valor real de 0 a 5 metros
    },
    "Tilt Angle In Deg": {
        "page": 8,
        "image_index": 1,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (0.0, 8.0) # Valor real de 0 a 8 grados
    },
    "Speed In Km/h": {
        "page": 10,
        "image_index": 0,
        "color_lower": np.array([125, 50, 50]),
        "color_upper": np.array([140, 255, 255]),
        "calib_pixel_x": (36, 1025),
        "calib_pixel_y": (427, 50),
        "calib_real_y": (0.0, 2.5) # Valor real de 0 a 2.5 Km/h
    }
}

def _scale_value(pixel_val: int, pixel_min: int, pixel_max: int, real_min: float, real_max: float) -> float:
    """Función de ayuda para escalar un valor de un rango de píxeles a un rango real."""
    if pixel_min > pixel_max:
        pixel_val, pixel_min, pixel_max = -pixel_val, -pixel_min, -pixel_max
    if (pixel_max - pixel_min) == 0: return real_min
    return real_min + (pixel_val - pixel_min) * (real_max - real_min) / (pixel_max - pixel_min)

def _extract_single_graph(page: pdfplumber.page.Page, config: Dict, total_duration_seconds: int) -> Optional[List[Tuple[float, float]]]:
    """Función genérica que extrae y calibra los datos para un único gráfico."""
    images = page.images
    image_index = config["image_index"]

    if not images or len(images) <= image_index:
        print(f"  - No se encontró la imagen índice {image_index} en la página.")
        return None

    graph_image_data = images[image_index]
    bbox = (graph_image_data['x0'], graph_image_data['top'], graph_image_data['x1'], graph_image_data['bottom'])
    img_object = page.crop(bbox).to_image(resolution=150)
    
    image = cv2.cvtColor(np.array(img_object.original), cv2.COLOR_RGB2BGR)

    hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv_image, config['color_lower'], config['color_upper'])
    points = np.argwhere(mask > 0)
    
    if len(points) == 0:
        print("  - No se encontraron puntos de datos en la máscara de color.")
        return None

    data_points_pixels = {}
    for y, x in points:
        if x not in data_points_pixels or y < data_points_pixels[x]:
            data_points_pixels[x] = y
    
    sorted_pixel_points = sorted(data_points_pixels.items())

    calibrated_data: List[Tuple[float, float]] = []
    px_start, px_end = config['calib_pixel_x']
    py_min, py_max = config['calib_pixel_y']
    ry_min, ry_max = config['calib_real_y']

    for px, py in sorted_pixel_points:
        time_sec = _scale_value(px, px_start, px_end, 0, total_duration_seconds)
        real_val = _scale_value(py, py_min, py_max, ry_min, ry_max)
        time_sec = max(0, time_sec)
        real_val = max(ry_min, min(ry_max, real_val))
        calibrated_data.append((time_sec, real_val))
        
    return calibrated_data

def extraer_toda_la_telemetria(pdf_path: str, total_duration_seconds: int) -> Dict[str, Optional[List[Tuple[float, float]]]]:
    """Función principal orquestadora. Itera sobre todos los gráficos configurados."""
    print(f"\n🚀 Iniciando extracción de TODA la telemetría desde '{os.path.basename(pdf_path)}'...")
    all_telemetry_data = {}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for graph_name, config in GRAPH_CONFIGS.items():
                print(f"--- Procesando gráfico: '{graph_name}' ---")
                page_num = config['page']
                
                if len(pdf.pages) < page_num:
                    print(f"  - ERROR: El PDF no tiene la página {page_num} requerida.")
                    all_telemetry_data[graph_name] = None
                    continue

                page = pdf.pages[page_num - 1]
                telemetry_data = _extract_single_graph(page, config, total_duration_seconds)
                
                if telemetry_data:
                    print(f"  ✅ Éxito. Se extrajeron {len(telemetry_data)} puntos de datos.")
                
                all_telemetry_data[graph_name] = telemetry_data

    except Exception as e:
        print(f"❌ ERROR CRÍTICO durante la extracción de telemetría: {e}")
        return {name: None for name in GRAPH_CONFIGS}

    print("\n🏁 Extracción de telemetría completada.")
    return all_telemetry_data

if __name__ == '__main__':
    print("Ejecutando prueba del módulo telemetry_parser completo...")
    pdf_file = os.path.join('data', 'reports', 'report test.pdf')
    test_duration = 476
    
    if os.path.exists(pdf_file):
        all_data = extraer_toda_la_telemetria(pdf_file, test_duration)
        print("\n--- RESULTADOS DE LA PRUEBA ---")
        for graph_name, data in all_data.items():
            if data:
                print(f"- Resultados para '{graph_name}': {len(data)} puntos extraídos.")
            else:
                print(f"- Resultados para '{graph_name}': No se pudieron extraer datos.")
    else:
        print(f"El archivo de prueba no se encontró en: {pdf_file}")
