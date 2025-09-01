# core/plotter.py
# Proyecto Titán - Módulo de Visualización de Telemetría
# Versión: 2.0
# Objetivo: Integrar gráficos de Matplotlib en la interfaz de CustomTkinter.

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from typing import List, Tuple, Optional
import customtkinter as ctk

# Variable para mantener una referencia al canvas del gráfico
# y poder limpiarlo antes de dibujar uno nuevo.
canvas_actual = None

def dibujar_grafico_telemetria(frame_destino: ctk.CTkFrame, datos: Optional[Tuple[List[float], List[float]]], titulo: str):
    """
    Dibuja un gráfico de telemetría de Matplotlib dentro de un Frame de CustomTkinter.
    Limpia cualquier gráfico anterior que exista en el frame.

    Args:
        frame_destino: El widget CTkFrame donde se incrustará el gráfico.
        datos: Una tupla con dos listas: (timestamps, valores), o None si no hay datos.
        titulo: El título que se mostrará en el gráfico.
    """
    global canvas_actual
    
    # Limpiar el frame de cualquier widget anterior (gráfico o mensaje)
    for widget in frame_destino.winfo_children():
        widget.destroy()
    canvas_actual = None

    # Si no hay datos, mostrar un mensaje y salir.
    if not datos:
        label_no_data = ctk.CTkLabel(frame_destino, text=f"No hay datos de '{titulo}' para visualizar.", font=ctk.CTkFont(size=12, slant="italic"))
        label_no_data.pack(pady=20, padx=10, expand=True)
        return

    try:
        timestamps, valores = datos

        # --- Creación del Gráfico con Matplotlib ---
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(5, 2.5))
        fig.patch.set_facecolor('#2B2B2B')
        
        ax.plot(timestamps, valores, color='#66B2FF')

        # --- Personalización del Gráfico ---
        ax.set_title(titulo, color='white', fontsize=10)
        ax.set_xlabel('Tiempo (segundos)', color='gray', fontsize=8)
        ax.set_ylabel('Valor', color='gray', fontsize=8)
        ax.grid(True, linestyle='--', alpha=0.3)
        ax.tick_params(axis='both', colors='gray', labelsize=7)
        ax.set_facecolor('#343638')

        # --- Integración en CustomTkinter ---
        canvas = FigureCanvasTkAgg(fig, master=frame_destino)
        canvas.draw()
        
        widget_grafico = canvas.get_tk_widget()
        widget_grafico.pack(side='top', fill='both', expand=True, padx=10, pady=10)
        
        canvas_actual = canvas
        plt.close(fig)

    except Exception as e:
        print(f"❌ ERROR al dibujar el gráfico: {e}")
        label_error = ctk.CTkLabel(frame_destino, text=f"Error al dibujar el gráfico:\n{e}", font=ctk.CTkFont(size=12))
        label_error.pack(pady=20, padx=10, expand=True)
