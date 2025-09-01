# app.py
# Frontend del Proyecto Titán - Analizador de Perfiles
# Versión: 7.0 (Arquitectura de Componentes Refactorizada)

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
    get_db_connection, insert_session, insert_summary_events,
    get_session_id_by_filename, insert_telemetry_data, get_telemetry_for_graph
)
from core.telemetry_parser import extraer_toda_la_telemetria
from core.reporter import generar_reporte_evolucion, generar_texto_reporte_individual
from core.behavior_analyzer import analizar_comportamiento_completo
from core.report_generator import crear_reporte_pdf
from core.plotter import dibujar_grafico_telemetria

# =============================================================================
# CLASE DEL PANEL DE CONTROL (IZQUIERDA)
# =============================================================================
class ControlFrame(ctk.CTkFrame):
    """
    Frame que contiene todos los widgets del panel de control.
    """
    def __init__(self, master, commands: Dict[str, callable], graficos_disponibles: list):
        super().__init__(master, corner_radius=10, fg_color=("gray90", "gray20"))
        self.commands = commands
        self.graficos_disponibles = graficos_disponibles
        self._create_widgets()

    def _create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(7, weight=1) # Espacio para que el panel de info se alinee abajo

        ctk.CTkLabel(self, text="🎯 PANEL DE CONTROL", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="ew", padx=20, pady=(20, 10))

        self.boton_procesar = ctk.CTkButton(self, text="🚀 Procesar Reporte Individual", font=ctk.CTkFont(size=14, weight="bold"),
                                             height=50, corner_radius=8, command=self.commands["process"])
        self.boton_procesar.grid(row=1, column=0, sticky="ew", padx=20, pady=10)

        self.boton_comparar = ctk.CTkButton(self, text="📊 Generar Reporte de Evolución", font=ctk.CTkFont(size=14, weight="bold"),
                                             height=50, corner_radius=8, fg_color="#1F6AA5", hover_color="#144569", command=self.commands["compare"])
        self.boton_comparar.grid(row=2, column=0, sticky="ew", padx=20, pady=10)

        self.boton_exportar = ctk.CTkButton(self, text="⬇️ Exportar a PDF", font=ctk.CTkFont(size=14, weight="bold"),
                                             height=40, corner_radius=8, state="disabled", command=self.commands["export"])
        self.boton_exportar.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 10))
        
        ctk.CTkLabel(self, text="Seleccionar Gráfico de Telemetría:", anchor="w", font=ctk.CTkFont(weight="bold")).grid(
            row=4, column=0, sticky="ew", padx=20, pady=(15, 0))

        self.combo_graficos = ctk.CTkComboBox(self, values=self.graficos_disponibles, state="disabled",
                                               command=self.commands["show_graph"])
        self.combo_graficos.grid(row=5, column=0, sticky="ew", padx=20, pady=5)

        ctk.CTkLabel(self, text="💡 Instrucciones:\n\n- Procesar: Analiza un PDF.\n- Comparar: Mide evolución.\n- Exportar: Guarda el último\n  análisis en un PDF.",
                     font=ctk.CTkFont(size=12), text_color=("gray30", "gray70"), justify="left").grid(
            row=6, column=0, sticky="sw", padx=20, pady=(20, 20))

    def set_button_state(self, is_enabled: bool):
        """Habilita o deshabilita los botones principales."""
        state = "normal" if is_enabled else "disabled"
        self.boton_procesar.configure(state=state)
        self.boton_comparar.configure(state=state)

    def set_post_analysis_state(self, is_enabled: bool):
        """Habilita o deshabilita los controles que dependen de un análisis previo."""
        state = "normal" if is_enabled else "disabled"
        self.boton_exportar.configure(state=state)
        self.combo_graficos.configure(state=state)

# =============================================================================
# CLASE DEL PANEL DE RESULTADOS (DERECHA)
# =============================================================================
class ResultsFrame(ctk.CTkFrame):
    """
    Frame que contiene el área de texto y el gráfico de resultados.
    """
    def __init__(self, master):
        super().__init__(master, corner_radius=10)
        self._create_widgets()

    def _create_widgets(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="📈 RESULTADOS Y GRÁFICO", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, sticky="ew", padx=20, pady=(10, 5))

        paned_window = tk.PanedWindow(self, orient=tk.VERTICAL, bg="#242424", sashwidth=8, sashrelief=tk.FLAT, bd=0)
        paned_window.grid(row=1, column=0, sticky="nsew", padx=10, pady=(5, 10))

        self.area_resultados = ctk.CTkTextbox(paned_window, font=ctk.CTkFont(family="Consolas", size=13),
                                              corner_radius=0, wrap="word", border_width=0)
        paned_window.add(self.area_resultados, minsize=100)

        self.grafico_frame = ctk.CTkFrame(paned_window, corner_radius=0, fg_color=("#DBDBDB", "#2B2B2B"))
        paned_window.add(self.grafico_frame, minsize=200)

        self.area_resultados.insert("0.0", "Sistema listo para procesar reportes...")
    
    def clear_panels(self, clear_graph=True):
        self.area_resultados.delete("1.0", "end")
        if clear_graph:
            for widget in self.grafico_frame.winfo_children():
                widget.destroy()

# =============================================================================
# CLASE PRINCIPAL DE LA APLICACIÓN (ORQUESTADOR)
# =============================================================================
class TitanApp:
    def __init__(self):
        self._setup_app()
        self._load_config()
        self._create_main_layout()

    def _setup_app(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title("Proyecto Titán - v7.0 (Arquitectura Refactorizada)")
        self.root.geometry("1200x800")
        self.root.minsize(900, 700)
        self._center_window()
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

    def _load_config(self):
        self.DB_PATH = os.path.join(project_root, 'database', 'titan.db')
        self.EXPORTS_DIR = os.path.join(project_root, 'data', 'exports')
        os.makedirs(self.EXPORTS_DIR, exist_ok=True)
        self.ultimo_analisis_realizado: Optional[Dict[str, Any]] = None
        self.session_id_actual: Optional[int] = None
        self.GRAFICOS_DISPONIBLES = ['Steering', 'Speed In Km/h', 'Brake Pad', 'Acceleration Pad', 'Fork Height In Mtrs', 'Tilt Angle In Deg']
        self.RUTAS_DE_APRENDIZAJE = {
            "Novato": {"titulo": "Ruta de Iniciación y Ambientación", "ejercicios": ["1.1. Reconocimiento de controles", "2.1. Conducción básica"]},
            "Sin nocion del espacio": {"titulo": "Ruta de Precisión y Control Espacial", "ejercicios": ["2.2. Conducción intermedia (Curvas en S)", "5.7. Operación con Carga Vertical 4"]},
            "Apurado": {"titulo": "Ruta de Seguridad y Control de Impulsos", "ejercicios": ["Módulo 4 completo (apilamiento)", "7.1. Operación con Señales"]},
            "Ineficiente": {"titulo": "Ruta de Fluidez y Productividad", "ejercicios": ["Módulo 6 completo (Estanterías)"]},
            "Eficiente": {"titulo": "Ruta de Especialización Avanzada", "ejercicios": ["Módulo 8 (Cargas Pesadas)", "Módulo 9 (Carga de Vehículos)"]}
        }
    
    def _create_main_layout(self):
        self.root.grid_columnconfigure(0, weight=1, minsize=300)
        self.root.grid_columnconfigure(1, weight=3, minsize=600)
        self.root.grid_rowconfigure(0, weight=1)

        commands = {
            "process": self.procesar_reporte_individual,
            "compare": self.generar_reporte_evolucion,
            "export": self.exportar_a_pdf,
            "show_graph": self.mostrar_grafico_seleccionado,
        }
        
        self.control_frame = ControlFrame(self.root, commands, self.GRAFICOS_DISPONIBLES)
        self.control_frame.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)

        self.results_frame = ResultsFrame(self.root)
        self.results_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 10), pady=10)
    
    def _center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (self.root.winfo_width() // 2)
        y = (self.root.winfo_screenheight() // 2) - (self.root.winfo_height() // 2)
        self.root.geometry(f"{self.root.winfo_width()}x{self.root.winfo_height()}+{x}+{y}")
    
    def _update_ui_state(self, buttons_enabled: bool, analysis_active: bool):
        """Actualiza el estado de todos los widgets interactivos."""
        self.control_frame.set_button_state(buttons_enabled)
        self.control_frame.set_post_analysis_state(analysis_active)
        if not analysis_active:
            self.ultimo_analisis_realizado = None
            self.session_id_actual = None

    def procesar_reporte_individual(self):
        self._update_ui_state(buttons_enabled=False, analysis_active=False)
        try:
            pdf_path = filedialog.askopenfilename(title="Selecciona un reporte PDF", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path: 
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                return

            self.results_frame.clear_panels()
            self.results_frame.area_resultados.insert("1.0", f"🧠 Procesando: {os.path.basename(pdf_path)}...")
            self.root.update()

            self.session_id_actual = self._procesar_y_obtener_id(pdf_path)
            if not self.session_id_actual:
                self.mostrar_error("Error de Procesamiento", "No se pudo procesar o registrar el archivo PDF.")
                return

            with get_db_connection(self.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                info_sesion = dict(conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (self.session_id_actual,)).fetchone())
            
            analisis_comportamiento = analizar_comportamiento_completo(self.session_id_actual)
            
            self.ultimo_analisis_realizado = {
                "info_sesion": info_sesion,
                "perfil_predicho": info_sesion['perfil_operador'],
                "ruta_recomendada": self.RUTAS_DE_APRENDIZAJE.get(info_sesion['perfil_operador']),
                "analisis_comportamiento": analisis_comportamiento
            }
            
            texto_del_reporte = generar_texto_reporte_individual(self.ultimo_analisis_realizado)
            self.results_frame.area_resultados.delete("1.0", "end")
            self.results_frame.area_resultados.insert("1.0", texto_del_reporte)
            
            self.control_frame.combo_graficos.set("Steering")
            self.mostrar_grafico_seleccionado()
            
            messagebox.showinfo("Éxito", "El reporte ha sido procesado y analizado.")

        except Exception as e:
            self.mostrar_error("Error Crítico", traceback.format_exc())
        finally:
            self._update_ui_state(buttons_enabled=True, analysis_active=(self.ultimo_analisis_realizado is not None))

    def generar_reporte_evolucion(self):
        self._update_ui_state(buttons_enabled=False, analysis_active=False)
        try:
            self.results_frame.clear_panels()
            pdf_path_inicial = filedialog.askopenfilename(title="Paso 1: Selecciona el Reporte INICIAL", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path_inicial: 
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                return

            pdf_path_final = filedialog.askopenfilename(title="Paso 2: Selecciona el Reporte FINAL", filetypes=[("Archivos PDF", "*.pdf")])
            if not pdf_path_final: 
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                return
            
            self.results_frame.area_resultados.insert("1.0", "Procesando y comparando reportes...")
            self.root.update()

            id_inicial = self._procesar_y_obtener_id(pdf_path_inicial)
            id_final = self._procesar_y_obtener_id(pdf_path_final)
            if not id_inicial or not id_final:
                self.mostrar_error("Error de Procesamiento", "No se pudo procesar uno o ambos archivos.")
                return

            reporte_comparativo = generar_reporte_evolucion(id_inicial, id_final)
            self.results_frame.area_resultados.delete("1.0", "end")
            self.results_frame.area_resultados.insert("1.0", reporte_comparativo)
            
            messagebox.showinfo("Éxito", "El reporte comparativo ha sido generado.")
        except Exception as e:
            self.mostrar_error("Error Crítico", traceback.format_exc())
        finally:
            self._update_ui_state(buttons_enabled=True, analysis_active=False)

    def mostrar_grafico_seleccionado(self, _=None):
        if not self.session_id_actual: return

        nombre_grafico = self.control_frame.combo_graficos.get()
        datos_grafico = get_telemetry_for_graph(self.session_id_actual, nombre_grafico)
        
        dibujar_grafico_telemetria(self.results_frame.grafico_frame, datos_grafico, nombre_grafico)

    def exportar_a_pdf(self):
        if not self.ultimo_analisis_realizado:
            messagebox.showwarning("Sin Datos", "Primero debes procesar un reporte individual.")
            return
        
        ruta_guardado = filedialog.asksaveasfilename(
            defaultextension=".pdf", filetypes=[("Archivos PDF", "*.pdf")],
            initialfile=f"Reporte-{self.ultimo_analisis_realizado['info_sesion']['nombre_operador'].replace(' ', '_')}.pdf",
            title="Guardar Reporte en PDF"
        )
        if not ruta_guardado: return
        
        try:
            crear_reporte_pdf(self.ultimo_analisis_realizado, ruta_guardado)
            messagebox.showinfo("Éxito", f"Reporte PDF guardado en:\n{ruta_guardado}")
        except Exception as e:
            self.mostrar_error("Error de Exportación", f"No se pudo generar el PDF.\n{traceback.format_exc()}")
            
    def _procesar_y_obtener_id(self, pdf_path: str) -> Optional[int]:
        file_name = os.path.basename(pdf_path)
        with get_db_connection(self.DB_PATH) as conn:
            session_id = get_session_id_by_filename(conn, file_name)
            if session_id: return session_id
            
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

    def _preparar_datos_para_prediccion(self, parsed_data, feature_names):
        data_for_df = {'puntaje_final': parsed_data['session_data']['puntaje_final'], 'duracion_segundos': parsed_data['session_data']['duracion_segundos']}
        for event in parsed_data['summary_events']:
            event_type = event['type'].replace(' ', '_')
            data_for_df[f"conteo_eventos_{event_type}"] = float(event.get('total_events', 0))
            data_for_df[f"penalizaciones_{event_type}"] = float(event.get('penalties', 0))
        return pd.DataFrame([data_for_df], columns=feature_names).fillna(0)

    def mostrar_error(self, titulo, mensaje):
        self.results_frame.clear_panels()
        self.results_frame.area_resultados.insert("1.0", f"❌ {titulo}\n" + "="*50 + f"\n\n{mensaje}")
    
    def cerrar_aplicacion(self):
        self.root.quit()
        self.root.destroy()
        sys.exit(0)
    
    def ejecutar(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = TitanApp()
    app.ejecutar()

