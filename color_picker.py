# color_picker.py
# Versión 2.0: Permite al usuario elegir qué captura de pantalla analizar.

import cv2
import os
import sys
import numpy as np

def color_picker_callback(event, x, y, flags, param):
    """Callback que se activa con los eventos del ratón."""
    image = param
    if event == cv2.EVENT_LBUTTONDOWN:
        color_bgr = image[y, x]
        print("\n===========================================================")
        print(f"Clic en la coordenada (x={x}, y={y})")
        print(f"Copia esta línea en tu config -> 'color_bgr': np.array([{color_bgr[0]}, {color_bgr[1]}, {color_bgr[2]}])")
        print("===========================================================")

# --- Script Principal ---
if __name__ == '__main__':
    nombre_imagen = input("Por favor, introduce el nombre del archivo de imagen a analizar (ej: pagina_6.png): ")

    if not os.path.exists(nombre_imagen):
        print(f"Error: No se encontró el archivo '{nombre_imagen}'.")
        sys.exit()

    image = cv2.imread(nombre_imagen)
    if image is None:
        print(f"Error: No se pudo cargar la imagen '{nombre_imagen}'.")
        sys.exit()

    window_name = 'Cuentagotas de Color - Haz clic en la linea y presiona una tecla'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, color_picker_callback, image)

    print(f"Mostrando '{nombre_imagen}'. Haz clic en la línea del gráfico para obtener su color BGR.")
    cv2.imshow(window_name, image)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

