# core/telemetry_extractor.py
# Versión 3.0 (Híbrido Texto + Fallback de Rango)

import fitz  # PyMuPDF
import cv2
import numpy as np
import os
import re
from typing import List, Tuple, Optional, Dict, Any
import traceback

# =============================================================================
# CONFIGURACIÓN CENTRALIZADA DE GRÁFICOS
# =============================================================================
GRAFICOS_CONFIG: List[Dict[str, Any]] = [
    {
        "nombre": "Steering",
        "crop_box_pixels": (67, 34, 648, 326),
        "color_bgr": np.array([206, 191, 195]),
        "calib_real_y": (-14.0, 14.0),
        "mascara_leyenda": (350, 5, 450, 35)
    },
    {
        "nombre": "Brake Pad",
        "crop_box_pixels": (67, 30, 648, 326),
        "color_bgr": np.array([95, 40, 68]),
        "calib_real_y": (0.0, 10.0),
        "mascara_leyenda": (350, 5, 450, 35)
    },
    {
        "nombre": "Acceleration Pad",
        "crop_box_pixels": (66, 340, 653, 624),
        "color_bgr": np.array([95, 40, 68]),
        "calib_real_y": (0.0, 2.5),
        "mascara_leyenda": (350, 5, 450, 35)
    },
    {
        "nombre": "Fork Height In Mtrs",
        "crop_box_pixels": (65, 27, 649, 322),
        "color_bgr": np.array([95, 40, 68]),
        "calib_real_y": (0.0, 5.0),
        "mascara_leyenda": (350, 5, 450, 35)
    },
    {
        "nombre": "Tilt Angle In Deg",
        "crop_box_pixels": (63, 330, 652, 606),
        "color_bgr": np.array([95, 40, 68]),
        "calib_real_y": (0.0, 8.0),
        "mascara_leyenda": (350, 5, 450, 35)
    },
    {
        "nombre": "Speed In Km/h",
        "crop_box_pixels": (60, 40, 648, 336),
        "color_bgr": np.array([124, 77, 101]),
        "calib_real_y": (0.0, 2.5),
        "mascara_leyenda": (350, 5, 450, 35)
    }
]

# =============================================================================
# FUNCIONES AUXILIARES
# =============================================================================
def _scale_value(pixel_val: int, pixel_range: Tuple[int, int], real_range: Tuple[float, float]) -> float:
    pixel_min, pixel_max = pixel_range
    real_min, real_max = real_range
    if pixel_min > pixel_max:
        pixel_val, pixel_min, pixel_max = -pixel_val, -pixel_min, -pixel_max
    if (pixel_max - pixel_min) == 0: 
        return real_min
    return real_min + (pixel_val - pixel_min) * (real_max - real_min) / (pixel_max - pixel_min)

def _extract_from_page(page: fitz.Page, config: Dict, total_duration: int) -> Optional[List[Tuple[float, float]]]:
    try:
        pix = page.get_pixmap(dpi=200)
        nparr = np.frombuffer(pix.tobytes(), np.uint8)
        full_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        x0, y0, x1, y1 = config["crop_box_pixels"]
        cropped_image = full_image[y0:y1, x0:x1]

        if "mascara_leyenda" in config:
            mx0, my0, mx1, my1 = config["mascara_leyenda"]
            cv2.rectangle(cropped_image, (mx0, my0), (mx1, my1), (0, 0, 0), thickness=-1)

        color = config["color_bgr"]
        tolerance = 40
        lower = np.maximum(0, color - tolerance)
        upper = np.minimum(255, color + tolerance)
        mask = cv2.inRange(cropped_image, lower, upper)

        points = cv2.findNonZero(mask)
        if points is None: 
            return None

        pixel_points_dict = {}
        for point in points:
            x, y = point[0]
            if x not in pixel_points_dict:
                pixel_points_dict[x] = y
            else:
                pixel_points_dict[x] = min(y, pixel_points_dict[x])

        raw_pixel_points = sorted(pixel_points_dict.items())

        height, width, _ = cropped_image.shape
        calib_pixel_x = (width * 0.05, width * 0.95)
        calib_pixel_y = (height * 0.95, height * 0.05)

        calibrated_data = []
        for px, py in raw_pixel_points:
            time_sec = _scale_value(px, calib_pixel_x, (0, total_duration))
            real_val = _scale_value(py, calib_pixel_y, config["calib_real_y"])
            calibrated_data.append((round(time_sec, 2), round(real_val, 2)))

        return calibrated_data
    except Exception as e:
        print(f"   - Error en _extract_from_page para '{config['nombre']}': {e}")
        return None

# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================
def extraer_telemetria_visual(ruta_pdf: str, duracion_total_segundos: int) -> Dict[str, Optional[List[Tuple[float, float]]]]:
    print(f"\n🚀 Iniciando extracción Híbrida (Texto+Fallback) desde '{os.path.basename(ruta_pdf)}'...")
    all_telemetry_data = {}

    try:
        with fitz.open(ruta_pdf) as pdf:
            for config in GRAFICOS_CONFIG:
                graph_name = config["nombre"]
                page_index = None

                # 1. Buscar por texto
                for i, page in enumerate(pdf):
                    text = page.get_text()
                    if re.search(graph_name, text, re.IGNORECASE):
                        page_index = i
                        break

                # 2. Fallback: rango de páginas si no se encontró
                if page_index is None:
                    for i in range(5, min(13, len(pdf))):
                        page_index = i
                        break

                if page_index is not None:
                    page = pdf[page_index]
                    print(f"--- Extrayendo '{graph_name}' de la página {page_index + 1}...")
                    all_telemetry_data[graph_name] = _extract_from_page(page, config, duracion_total_segundos)
                else:
                    print(f"   - No se pudo ubicar el gráfico '{graph_name}'.")
                    all_telemetry_data[graph_name] = None

    except Exception as e:
        print(f"❌ ERROR CRÍTICO durante la extracción: {e}")
        traceback.print_exc()
        return {cfg["nombre"]: None for cfg in GRAFICOS_CONFIG}

    print("\n🏁 Extracción de telemetría completada.")
    return all_telemetry_data
