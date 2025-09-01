# app.py
# Versión: 6.4 (Integración Final de Módulos de Análisis)

import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk
import sys
import os
import shutil
import joblib
import pandas as pd
import traceback
from typing import Optional, Dict, Any
import sqlite3

# Añadir la ruta del proyecto al sys.path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

# Importaciones de nuestros módulos
from core.pdf_parser import parse_pdf_report
from core.db_manager import (
    get_db_connection,
    insert_session,
    insert_summary_events,
    get_session_id_by_filename,
    insert_telemetry_data,
    get_telemetry_for_graph
)
from core.telemetry_parser import extraer_toda_la_telemetria
from core.plotter import dibujar_grafico_telemetria
from core.reporter import generar_reporte_evolucion, generar_texto_reporte_individual
# --- TAREA: Importar el nuevo analizador orquestador ---
from core.behavior_analyzer import analizar_comportamiento_completo
from core.report_generator import crear_reporte_pdf

class TitanApp:
    """
    Aplicación principal del Proyecto Titán con paneles de resultados redimensionables.
    """
    def __init__(self):
        """Inicializa la aplicación y configura la interfaz."""
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.DB_PATH = os.path.join(project_root, 'database', 'titan.db')
        self.EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
        os.makedirs(self.EXPORTS_DIR, exist_ok=True)

        self.ultimo_analisis_realizado: Optional[Dict[str, Any]] = None
        self.session_id_actual: Optional[int] = None

        self.GRAFICOS_DISPONIBLES = [
            'Steering', 'Speed In Km/h', 'Brake Pad',
            'Acceleration Pad', 'Fork Height In Mtrs', 'Tilt Angle In Deg'
        ]

        self.RUTAS_DE_APRENDIZAJE = {
            "Novato": {"titulo": "Ruta de Iniciación y Ambientación", "ejercicios": ["1.1. Reconocimiento de controles", "2.1. Conducción básica", "--- (Al finalizar, proceder al Ejercicio de Diagnóstico) ---"]},
            "Sin nocion del espacio": {"titulo": "Ruta de Precisión y Control Espacial", "ejercicios": ["2.2. Conducción intermedia (Curvas en S)", "5.7. Operación con Carga Vertical 4 (Visión obstruida)", "5.8. Operación en Circuito en S (SIN CARGA)", "Módulo 6 completo (Manipulación en estanterías)"]},
            "Apurado": {"titulo": "Ruta de Seguridad y Control de Impulsos", "ejercicios": ["Módulo 4 completo (Ejercicios de apilamiento deliberado)", "6.4. Cargas Similares 3 (Fuerza a conducir lento)", "7.1. Operación con Señales y Obstáculos"]},
            "Ineficiente": {"titulo": "Ruta de Fluidez y Productividad", "ejercicios": ["Módulo 4 completo (Ejercicios de Apilamiento en Tierra)", "Módulo 6 completo (Ejercicios de Manipulación en Estanterías)"]},
            "Eficiente": {"titulo": "Ruta de Especialización Avanzada", "ejercicios": ["Módulo 8 (Cargas Pesadas)", "Módulo 9 (Carga de Vehículos)"]}
        }

        self.root = ctk.CTk()
        self.configurar_ventana()
        self.configurar_layout()
        self.crear_widgets()

    def configurar_ventana(self):
        """Configura las propiedades básicas de la ventana principal."""
        self.root.title("Proyecto Titán - v6.4 (Análisis Integrado)")
        self.root.geometry("1200x800")
        self.root.minsize(900, 700)
        self.centrar_ventana()
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

    def centrar_ventana(self):
        ancho_pantalla = self.root.winfo_screenwidth()
        alto_pantalla = self.root.winfo_screenheight()
        x = (ancho_pantalla // 2) - (1200 // 2)
        y = (alto_pantalla // 2) - (800 // 2)
        self.root.geometry(f"1200x800+{x}+{y}")

    def configurar_layout(self):
        """Configura las columnas y filas principales de la aplicación."""
        self.root.grid_columnconfigure(0, weight=1, minsize=300)
        self.root.grid_columnconfigure(1, weight=3, minsize=600)
        self.root.grid_rowconfigure(0, weight=1)

    def crear_widgets(self):
        """Crea todos los elementos de la interfaz de usuario."""
        # --- Panel de Control (Izquierda) ---
        self.panel_control = ctk.CTkFrame(self.root, corner_radius=10, fg_color=("gray90", "gray20"))
        self.panel_control.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        self.panel_control.grid_columnconfigure(0, weight=1)
        self.panel_control.grid_rowconfigure(7, weight=1)

        self.titulo_control = ctk.CTkLabel(self.panel_control, text="🎯 PANEL DE CONTROL", font=ctk.CTkFont(size=16, weight="bold"))
        self.titulo_control.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))

        self.boton_procesar = ctk.CTkButton(self.panel_control, text="🚀 Procesar Reporte Individual", font=ctk.CTkFont(size=14, weight="bold"), height=50, corner_radius=8, command=self.procesar_reporte_individual)
        self.boton_procesar.grid(row=1, column=0, sticky="ew", padx=20, pady=10)

        self.boton_comparar = ctk.CTkButton(self.panel_control, text="📊 Generar Reporte de Evolución", font=ctk.CTkFont(size=14, weight="bold"), height=50, corner_radius=8, fg_color="#1F6AA5", hover_color="#144569", command=self.generar_reporte_evolucion)
        self.boton_comparar.grid(row=2, column=0, sticky="ew", padx=20, pady=10)

        self.boton_exportar = ctk.CTkButton(self.panel_control, text="⬇️ Exportar a PDF", font=ctk.CTkFont(size=14, weight="bold"), height=40, corner_radius=8, state="disabled", command=self.exportar_a_pdf)
        self.boton_exportar.grid(row=3, column=0, sticky="ew", padx=20, pady=(10,10))

        self.label_selector_grafico = ctk.CTkLabel(self.panel_control, text="Seleccionar Gráfico de Telemetría:", anchor="w", font=ctk.CTkFont(weight="bold"))
        self.label_selector_grafico.grid(row=4, column=0, sticky="ew", padx=20, pady=(15, 0))

        self.combo_graficos = ctk.CTkComboBox(self.panel_control, values=self.GRAFICOS_DISPONIBLES, state="disabled", command=self.mostrar_grafico_seleccionado)
        self.combo_graficos.grid(row=5, column=0, sticky="ew", padx=20, pady=5)

        self.info_panel = ctk.CTkLabel(self.panel_control, text="💡 Instrucciones:\n\n- Procesar: Analiza un PDF.\n- Comparar: Mide evolución.\n- Exportar: Guarda el último\n  análisis en un PDF.", font=ctk.CTkFont(size=12), text_color=("gray30", "gray70"), justify="left")
        self.info_panel.grid(row=6, column=0, sticky="sw", padx=20, pady=(20, 20))

        # --- Panel de Resultados con Layout Redimensionable (Derecha) ---
        self.panel_resultados = ctk.CTkFrame(self.root, corner_radius=10)
        self.panel_resultados.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
        self.panel_resultados.grid_columnconfigure(0, weight=1)
        self.panel_resultados.grid_rowconfigure(1, weight=1)

        self.titulo_resultados = ctk.CTkLabel(self.panel_resultados, text="📈 RESULTADOS Y GRÁFICO", font=ctk.CTkFont(size=16, weight="bold"))
        self.titulo_resultados.grid(row=0, column=0, sticky="ew", padx=20, pady=(10,5))

        self.paned_window = tk.PanedWindow(self.panel_resultados, orient=tk.VERTICAL, bg="#242424", sashwidth=8, sashrelief=tk.FLAT, bd=0)
        self.paned_window.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))

        self.area_resultados = ctk.CTkTextbox(self.paned_window, font=ctk.CTkFont(family="Consolas", size=13), corner_radius=0, wrap="word", border_width=0)
        self.paned_window.add(self.area_resultados, minsize=100)

        self.grafico_frame = ctk.CTkFrame(self.paned_window, corner_radius=0, fg_color=("#DBDBDB", "#2B2B2B"))
        self.paned_window.add(self.grafico_frame, minsize=200)

        self.area_resultados.insert("0.0", "Sistema listo para procesar reportes...")

    def _procesar_y_obtener_id(self, pdf_path: str) -> Optional[int]:
        file_name = os.path.basename(pdf_path)
        with get_db_connection(self.DB_PATH) as conn:
            session_id = get_session_id_by_filename(conn, file_name)
            if session_id:
                return session_id
            
            parsed_data = parse_pdf_report(pdf_path)
            if not parsed_data: return None

            model_path = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
            modelo = joblib.load(model_path)
            df_pred = self._preparar_datos_para_prediccion(parsed_data, modelo.feature_names_in_)
            perfil_predicho = modelo.predict(df_pred)[0]
            
            conn.execute("BEGIN")
            new_session_id = insert_session(conn, parsed_data, perfil_predicho)
            if not new_session_id: conn.rollback(); return None
            
            insert_summary_events(conn, new_session_id, parsed_data['summary_events'])
            toda_la_telemetria = extraer_toda_la_telemetria(pdf_path, parsed_data['session_data']['duracion_segundos'])
            for nombre_grafico, datos_telemetria in toda_la_telemetria.items():
                if datos_telemetria:
                    insert_telemetry_data(conn, new_session_id, nombre_grafico, datos_telemetria)
            
            conn.commit()
            shutil.copy2(pdf_path, os.path.join(self.EXPORTS_DIR, file_name))
            return new_session_id

    def _limpiar_paneles(self, limpiar_grafico=True):
        self.area_resultados.delete("1.0", "end")
        if limpiar_grafico:
            for widget in self.grafico_frame.winfo_children():
                widget.destroy()

    def procesar_reporte_individual(self):
        self.deshabilitar_botones()
        try:
            pdf_path = filedialog.askopenfilename(title="Selecciona un reporte PDF", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path: 
                self.habilitar_botones()
                return

            self._limpiar_paneles()
            self.area_resultados.insert("1.0", f"🧠 Procesando: {os.path.basename(pdf_path)}...")
            self.root.update()

            self.session_id_actual = self._procesar_y_obtener_id(pdf_path)
            if not self.session_id_actual:
                self.mostrar_error("Error de Procesamiento", "No se pudo procesar o registrar el archivo PDF.")
                return

            with get_db_connection(self.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                info_sesion = dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (self.session_id_actual,)).fetchone())
            
            # --- TAREA: Llamar a la función orquestadora del "Cerebro" ---
            analisis_comportamiento = analizar_comportamiento_completo(self.session_id_actual)
            
            # --- TAREA: Construir el diccionario para el "Presentador" ---
            self.ultimo_analisis_realizado = {
                "info_sesion": info_sesion,
                "perfil_predicho": info_sesion['perfil_operador'],
                "ruta_recomendada": self.RUTAS_DE_APRENDIZAJE.get(info_sesion['perfil_operador']),
                "analisis_comportamiento": analisis_comportamiento
            }
            
            texto_del_reporte = generar_texto_reporte_individual(self.ultimo_analisis_realizado)
            self.area_resultados.delete("1.0", "end")
            self.area_resultados.insert("1.0", texto_del_reporte)
            
            self.combo_graficos.set("Steering")
            self.mostrar_grafico_seleccionado()
            
            messagebox.showinfo("Éxito", "El reporte ha sido procesado y analizado.")

        except Exception as e:
            self.mostrar_error("Error Crítico", traceback.format_exc())
        finally:
            self.habilitar_botones()

    def mostrar_grafico_seleccionado(self, _=None):
        if not self.session_id_actual:
            return

        nombre_grafico_seleccionado = self.combo_graficos.get()
        datos_grafico = get_telemetry_for_graph(self.session_id_actual, nombre_grafico_seleccionado)
        
        for widget in self.grafico_frame.winfo_children():
            widget.destroy()
            
        if datos_grafico:
            dibujar_grafico_telemetria(self.grafico_frame, datos_grafico, nombre_grafico_seleccionado)
        else:
            label_no_data = ctk.CTkLabel(self.grafico_frame, text=f"No hay datos de '{nombre_grafico_seleccionado}' para visualizar.", font=ctk.CTkFont(size=12, slant="italic"))
            label_no_data.pack(expand=True)

    def generar_reporte_evolucion(self):
        self.deshabilitar_botones()
        try:
            self._limpiar_paneles()
            
            pdf_path_inicial = filedialog.askopenfilename(title="Paso 1: Selecciona el Reporte INICIAL", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path_inicial: 
                self.habilitar_botones()
                return
            
            pdf_path_final = filedialog.askopenfilename(title="Paso 2: Selecciona el Reporte FINAL", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path_final: 
                self.habilitar_botones()
                return
            
            self.area_resultados.insert("1.0", "Procesando y comparando reportes...")
            self.root.update()

            id_inicial = self._procesar_y_obtener_id(pdf_path_inicial)
            id_final = self._procesar_y_obtener_id(pdf_path_final)
            if not id_inicial or not id_final:
                self.mostrar_error("Error de Procesamiento", "No se pudo procesar uno o ambos archivos.")
                return

            with get_db_connection(self.DB_PATH) as conn:
                res = conn.execute(f"SELECT id_sesion FROM Sesiones WHERE id_sesion IN (?, ?) ORDER BY fecha_hora_inicio", (id_inicial, id_final)).fetchall()
                id_inicial, id_final = res[0][0], res[1][0]
            
            reporte_comparativo = generar_reporte_evolucion(id_inicial, id_final)
            self.area_resultados.delete("1.0", "end")
            self.area_resultados.insert("1.0", reporte_comparativo)
            
            messagebox.showinfo("Éxito", "El reporte comparativo ha sido generado.")

        except Exception as e:
            self.mostrar_error("Error Crítico", traceback.format_exc())
        finally:
            self.habilitar_botones()

    def exportar_a_pdf(self):
        if not self.ultimo_analisis_realizado:
            messagebox.showwarning("Sin Datos", "Primero debes procesar un reporte individual para poder exportarlo.")
            return

        try:
            operario = self.ultimo_analisis_realizado['info_sesion']['nombre_operador'].replace(" ", "_")
            default_filename = f"Reporte-{operario}.pdf"
            ruta_guardado = filedialog.asksaveasfilename(
                defaultextension=".pdf", filetypes=[("Archivos PDF", "*.pdf")],
                initialfile=default_filename, title="Guardar Reporte en PDF"
            )
            if not ruta_guardado: return
            
            exito = crear_reporte_pdf(self.ultimo_analisis_realizado, ruta_guardado)
            if exito:
                messagebox.showinfo("Éxito", f"Reporte en PDF guardado exitosamente en:\n{ruta_guardado}")
            else:
                messagebox.showerror("Error", "Ocurrió un problema al generar el archivo PDF.")
        except Exception as e:
            self.mostrar_error("Error de Exportación", f"No se pudo generar el PDF. Detalles: {traceback.format_exc()}")

    def habilitar_botones(self):
        self.boton_procesar.configure(state="normal")
        self.boton_comparar.configure(state="normal")
        if self.ultimo_analisis_realizado and self.session_id_actual:
            self.boton_exportar.configure(state="normal")
            self.combo_graficos.configure(state="normal")

    def deshabilitar_botones(self):
        self.boton_procesar.configure(state="disabled")
        self.boton_comparar.configure(state="disabled")
        self.boton_exportar.configure(state="disabled")
        self.combo_graficos.configure(state="disabled")
        self.ultimo_analisis_realizado = None
        self.session_id_actual = None

    def _preparar_datos_para_prediccion(self, parsed_data, feature_names):
        data_for_df = {
            'puntaje_final': parsed_data['session_data']['puntaje_final'],
            'duracion_segundos': parsed_data['session_data']['duracion_segundos']
        }
        for event in parsed_data['summary_events']:
            event_type = event['type'].replace(' ', '_')
            data_for_df[f"conteo_eventos_{event_type}"] = float(event.get('total_events', 0))
            data_for_df[f"penalizaciones_{event_type}"] = float(event.get('penalties', 0))
        return pd.DataFrame([data_for_df], columns=feature_names).fillna(0)

    def mostrar_error(self, titulo, mensaje):
        self._limpiar_paneles()
        self.area_resultados.insert("1.0", f"❌ {titulo}\n" + "="*50 + f"\n\n{mensaje}")
        self.habilitar_botones()
    
    def cerrar_aplicacion(self):
        self.root.quit()
        self.root.destroy()
        sys.exit(0)
    
    def ejecutar(self):
        self.root.mainloop()

def main():
    app = TitanApp()
    app.ejecutar()

if __name__ == "__main__":
    main()
