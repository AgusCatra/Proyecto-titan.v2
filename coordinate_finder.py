# coordinate_finder.py
# Versión 3.0: Permite al usuario elegir qué captura de pantalla analizar.

import cv2
import os
import sys

# Variables globales para el manejo del ratón
drawing = False
start_point = (-1, -1)
image_copy = None

def draw_rectangle(event, x, y, flags, param):
    """Callback para dibujar el rectángulo con el ratón."""
    global start_point, drawing, image_copy
    
    if event == cv2.EVENT_LBUTTONDOWN:
        drawing = True
        start_point = (x, y)
        image_copy = image.copy()

    elif event == cv2.EVENT_MOUSEMOVE:
        if drawing:
            temp_image = image_copy.copy()
            cv2.rectangle(temp_image, start_point, (x, y), (0, 255, 0), 2)
            cv2.imshow(window_name, temp_image)

    elif event == cv2.EVENT_LBUTTONUP:
        drawing = False
        end_point = (x, y)
        
        x0 = min(start_point[0], end_point[0])
        y0 = min(start_point[1], end_point[1])
        x1 = max(start_point[0], end_point[0])
        y1 = max(start_point[1], end_point[1])
        
        print("\n====================================================================")
        print("¡Coordenadas encontradas!")
        print(f'   "crop_box_pixels": ({x0}, {y0}, {x1}, {y1}),')
        print("====================================================================")
        
        cv2.rectangle(image_copy, start_point, end_point, (0, 255, 0), 2)
        cv2.imshow(window_name, image_copy)

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

    window_name = f"Buscador de Coordenadas - Dibuja un rectangulo y presiona una tecla"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, draw_rectangle)

    print(f"Mostrando '{nombre_imagen}'. Dibuja un rectángulo sobre el gráfico que quieres analizar.")
    cv2.imshow(window_name, image)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

