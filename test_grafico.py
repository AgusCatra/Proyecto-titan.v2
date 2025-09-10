# diagnostic_plotter.py
# Versión 4.5: Añadida máscara para ignorar la leyenda del gráfico.

import os
import sys
import cv2
import numpy as np
import matplotlib.pyplot as plt
from typing import List, Tuple, Dict, Any, Optional
import traceback

# --- Añadir la ruta del proyecto al sys.path ---
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# --- CONFIGURACIÓN DE LA PRUEBA ---
SCREENSHOT_IMAGEN = "pagina_6.png"
DURACION_PRUEBA_SEGUNDOS = 476

GRAFICOS_CONFIG: List[Dict[str, Any]] = [
    {
        "nombre": "Acceleration Pad",
        "crop_box_pixels": (26, 50, 850, 410),
        "color_bgr": np.array([95, 40, 68]),
        "calib_real_y": (0.0, 10.0),
        # --- NUEVO: Coordenadas de la leyenda DENTRO del área recortada ---
        # (x0, y0, x1, y1) -> Esquina sup-izq y esquina inf-der de la leyenda
        "mascara_leyenda": (350, 0, 450, 30) 
    },
]

# =============================================================================
# LÓGICA DE EXTRACCIÓN Y CALIBRACIÓN
# =============================================================================

def _scale_value(pixel_val: int, pixel_range: Tuple[int, int], real_range: Tuple[float, float]) -> float:
    pixel_min, pixel_max = pixel_range
    real_min, real_max = real_range
    if pixel_min > pixel_max:
        pixel_val, pixel_min, pixel_max = -pixel_val, -pixel_min, -pixel_max
    if (pixel_max - pixel_min) == 0:
        return real_min
    return real_min + (pixel_val - pixel_min) * (real_max - real_min) / (pixel_max - pixel_min)

def extract_and_calibrate(screenshot_path: str, config: Dict, total_duration: int) -> Tuple[Optional[List], Optional[List]]:
    """
    Recorta, enmascara la leyenda, extrae los datos y los calibra.
    """
    try:
        full_image = cv2.imread(screenshot_path)
        if full_image is None:
            print(f"Error: No se pudo cargar la imagen '{screenshot_path}'")
            return None, None
            
        x0, y0, x1, y1 = config["crop_box_pixels"]
        cropped_image = full_image[y0:y1, x0:x1]
        
        # --- PASO DE ENMASCARAMIENTO (NUEVO Y CLAVE) ---
        # Antes de cualquier análisis, dibujamos un rectángulo negro sobre la leyenda.
        if "mascara_leyenda" in config:
            mx0, my0, mx1, my1 = config["mascara_leyenda"]
            # Usamos -1 como grosor para que el rectángulo sea relleno
            cv2.rectangle(cropped_image, (mx0, my0), (mx1, my1), (0, 0, 0), thickness=-1)
            print("   - Máscara de leyenda aplicada.")

        cv2.imwrite("debug_extracted_image.png", cropped_image)
        print("   - Imagen recortada (y enmascarada) guardada en 'debug_extracted_image.png'")

        color = config["color_bgr"]
        tolerance = 30
        lower = np.maximum(0, color - tolerance)
        upper = np.minimum(255, color + tolerance)
        mask = cv2.inRange(cropped_image, lower, upper)
        
        cv2.imwrite("debug_color_mask.png", mask)
        print("   - Máscara de color guardada.")
        
        points = cv2.findNonZero(mask)
        if points is None:
            print("   - ¡FALLO! No se encontraron píxeles con el color especificado.")
            return None, None

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
            calibrated_data.append((time_sec, real_val))
            
        return raw_pixel_points, calibrated_data
        
    except Exception as e:
        print(f"Error durante la extracción: {e}")
        traceback.print_exc()
        return None, None

# =============================================================================
# SCRIPT PRINCIPAL DE DIAGNÓSTICO
# =============================================================================
if __name__ == '__main__':
    config_a_probar = GRAFICOS_CONFIG[0]
    nombre_grafico = config_a_probar["nombre"]
    print(f"--- Iniciando diagnóstico para el gráfico: '{nombre_grafico}' ---")
    
    raw_data, calibrated_data = extract_and_calibrate(SCREENSHOT_IMAGEN, config_a_probar, DURACION_PRUEBA_SEGUNDOS)

    if not raw_data or not calibrated_data:
        print("\nNo se pudieron extraer datos. Revisa las imágenes de depuración.")
        sys.exit()

    print(f"\nSe extrajeron {len(raw_data)} puntos. Mostrando visualización...")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle(f'Diagnóstico de Calibración para "{nombre_grafico}"', fontsize=16)

    pixel_x, pixel_y = zip(*raw_data)
    ax1.plot(pixel_x, pixel_y, color='orange')
    ax1.set_title("Datos Crudos (en Píxeles)")
    ax1.set_xlabel("Coordenada X (Relativa al Recorte)")
    ax1.set_ylabel("Coordenada Y (Relativa al Recorte)")
    ax1.grid(True, linestyle='--', alpha=0.5)
    ax1.invert_yaxis()

    time_s, value_real = zip(*calibrated_data)
    ax2.plot(time_s, value_real, color='cyan')
    ax2.set_title("Datos Calibrados (Valores Reales)")
    ax2.set_xlabel("Tiempo (segundos)")
    ax2.set_ylabel("Valor Real de la Métrica")
    ax2.grid(True, linestyle='--', alpha=0.5)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()

