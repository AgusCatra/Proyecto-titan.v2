# app.py
# Proyecto Titán – Analizador de Perfiles
# Versión: 8.0 (Rediseño Visual + UX)

import os
import sys
import shutil
import traceback
import sqlite3
from typing import Optional, Dict, Any

# --- UI / System ---
import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk

# --- Data / ML ---
import joblib
import pandas as pd

# --- Imaging for icons ---
from PIL import Image, ImageDraw

# =========================
# Rutas del proyecto
# =========================
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.append(project_root)

# =========================
# Importaciones internas
# =========================
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


# =========================
# Diseño: Paleta & Fuentes
# =========================
class Theme:
    BG      = "#1E1F25"
    SURFACE = "#23262E"
    CARD    = "#2A2E37"
    ACCENT  = "#4A90E2"
    ACCENT_HOVER = "#357ABD"
    SUCCESS = "#00A67E"
    SUCCESS_HOVER = "#008C6B"
    MUTED   = "#8D93A1"
    TEXT    = "#FFFFFF"
    DIVIDER = "#3A3F4B"
    WARNING = "#F5A524"
    DANGER  = "#EF4444"

    @staticmethod
    def apply_dark():
        ctk.set_appearance_mode("dark")

    @staticmethod
    def apply_light():
        ctk.set_appearance_mode("light")


class Fonts:
    H1   = ctk.CTkFont(size=22, weight="bold")
    H2   = ctk.CTkFont(size=16, weight="bold")
    H3   = ctk.CTkFont(size=14, weight="bold")
    BODY = ctk.CTkFont(size=13)
    MONO = ctk.CTkFont(family="Consolas", size=13)


# =========================
# Utilidades UI
# =========================
class Tooltip:
    def __init__(self, widget, text: str, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._unschedule)

    def _schedule(self, _):
        self._id = self.widget.after(self.delay, self._show)

    def _unschedule(self, _):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None
        self._hide()

    def _show(self):
        if self._tip or not self.text:
            return
        x, y, _, h = self.widget.bbox("insert") if self.widget.winfo_class() == "Text" else (0, 0, 0, 0)
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + h + 10
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        frame = ctk.CTkFrame(self._tip, corner_radius=6, fg_color=Theme.CARD)
        frame.pack(padx=1, pady=1)
        label = ctk.CTkLabel(frame, text=self.text, font=Fonts.BODY, text_color=Theme.TEXT)
        label.pack(padx=8, pady=6)

    def _hide(self):
        if self._tip:
            self._tip.destroy()
            self._tip = None


class Toast:
    def __init__(self, master):
        self.master = master

    def show(self, text: str, kind: str = "info", duration=2500):
        bg = Theme.CARD
        if kind == "success":
            bg = Theme.SUCCESS
        elif kind == "warning":
            bg = Theme.WARNING
        elif kind == "error":
            bg = Theme.DANGER

        top = tk.Toplevel(self.master)
        top.overrideredirect(True)
        top.configure(bg=bg)
        top.attributes("-topmost", True)

        # posicionar top-right
        self.master.update_idletasks()
        x = self.master.winfo_x() + self.master.winfo_width() - 320
        y = self.master.winfo_y() + 20
        top.geometry(f"300x48+{x}+{y}")

        label = ctk.CTkLabel(top, text=text, font=Fonts.BODY,
                             text_color="white", fg_color=bg)
        label.pack(fill="both", expand=True, padx=6, pady=6)

        # auto cerrar
        top.after(duration, top.destroy)


class LoadingOverlay:
    def __init__(self, master):
        self.master = master
        self.top = None

    def open(self, text="Procesando..."):
        if self.top: return
        self.top = tk.Toplevel(self.master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.9)

        # full overlay
        self.master.update_idletasks()
        x = self.master.winfo_rootx()
        y = self.master.winfo_rooty()
        w = self.master.winfo_width()
        h = self.master.winfo_height()
        self.top.geometry(f"{w}x{h}+{x}+{y}")

        container = ctk.CTkFrame(self.top, corner_radius=12, fg_color=Theme.CARD)
        container.place(relx=0.5, rely=0.5, anchor="center")

        label = ctk.CTkLabel(container, text=text, font=Fonts.H2, text_color=Theme.TEXT)
        label.pack(padx=20, pady=(20, 10))

        self.progress = ctk.CTkProgressBar(container, width=280, mode="indeterminate")
        self.progress.pack(padx=20, pady=(0, 20))
        self.progress.start()

    def close(self):
        if self.top:
            self.progress.stop()
            self.top.destroy()
            self.top = None


# =========================
# Fábrica de Iconos
# =========================
class IconFactory:
    """Crea iconos programáticamente para evitar archivos externos."""
    @staticmethod
    def create_icon(name: str, size: tuple = (20, 20), color: str = "#FFFFFF") -> ctk.CTkImage:
        image = Image.new("RGBA", size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        if name == "process":  # Play
            draw.polygon([(5, 4), (5, 16), (16, 10)], fill=color)
        elif name == "compare":  # barras
            draw.rectangle([(3, 10), (7, 16)], fill=color)
            draw.rectangle([(9, 4), (13, 16)], fill=color)
            draw.rectangle([(15, 7), (19, 16)], fill=color)
        elif name == "export":  # download
            draw.line([(10, 3), (10, 13)], fill=color, width=2)
            draw.polygon([(5, 9), (10, 14), (15, 9)], fill=color)
            draw.line([(4, 17), (16, 17)], fill=color, width=2)
        elif name == "settings":
            # engranaje simple
            draw.ellipse([(6, 6), (14, 14)], outline=color, width=2)
            draw.line([(10, 3), (10, 6)], fill=color, width=2)
            draw.line([(10, 14), (10, 17)], fill=color, width=2)
            draw.line([(3, 10), (6, 10)], fill=color, width=2)
            draw.line([(14, 10), (17, 10)], fill=color, width=2)

        return ctk.CTkImage(light_image=image, dark_image=image, size=size)


# =========================
# Componentes de Interfaz
# =========================
class Header(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, corner_radius=10, fg_color=Theme.SURFACE)
        self.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(self, text="PROYECTO TITÁN",
                             font=Fonts.H1, text_color=Theme.TEXT)
        subtitle = ctk.CTkLabel(self, text="Visualizador de Telemetría y Análisis de Comportamiento",
                                font=Fonts.BODY, text_color=Theme.MUTED)
        title.grid(row=0, column=0, sticky="w", padx=16, pady=(14, 0))
        subtitle.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 14))


class StatusBar(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, corner_radius=0, fg_color=Theme.SURFACE)
        self.label_left = ctk.CTkLabel(self, text="Listo.", font=Fonts.BODY, text_color=Theme.MUTED)
        self.label_right = ctk.CTkLabel(self, text="", font=Fonts.BODY, text_color=Theme.MUTED)
        self.label_left.pack(side="left", padx=12, pady=6)
        self.label_right.pack(side="right", padx=12, pady=6)

    def set(self, left: str = None, right: str = None):
        if left is not None:
            self.label_left.configure(text=left)
        if right is not None:
            self.label_right.configure(text=right)


class ControlCard(ctk.CTkFrame):
    def __init__(self, master, commands: Dict[str, callable], icons: Dict[str, ctk.CTkImage], graficos: list):
        super().__init__(master, corner_radius=12, fg_color=Theme.CARD)
        self.commands = commands
        self.icons = icons
        self.graficos_disponibles = graficos
        self._build()

    def _divider(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color=Theme.DIVIDER).pack(fill="x", padx=12, pady=12)

    def _build(self):
        # título
        title = ctk.CTkLabel(self, text="Acciones", font=Fonts.H2, text_color=Theme.TEXT)
        subtitle = ctk.CTkLabel(self, text="Procesá, compará y exportá tus reportes.",
                                font=Fonts.BODY, text_color=Theme.MUTED)
        title.pack(anchor="w", padx=14, pady=(14, 0))
        subtitle.pack(anchor="w", padx=14, pady=(0, 10))

        # Botones principales
        btn_proc = ctk.CTkButton(self, text="Procesar Reporte", image=self.icons["process"], compound="left",
                                 font=Fonts.H3, height=44, corner_radius=10, fg_color=Theme.SUCCESS,
                                 hover_color=Theme.SUCCESS_HOVER, command=self.commands["process"])
        btn_evol = ctk.CTkButton(self, text="Generar Evolución", image=self.icons["compare"], compound="left",
                                 font=Fonts.H3, height=44, corner_radius=10, fg_color=Theme.ACCENT,
                                 hover_color=Theme.ACCENT_HOVER, command=self.commands["compare"])
        btn_proc.pack(fill="x", padx=12, pady=(8, 6))
        btn_evol.pack(fill="x", padx=12, pady=(0, 10))
        Tooltip(btn_proc, "Abrir y analizar un reporte PDF (Ctrl+O)")
        Tooltip(btn_evol, "Comparar dos reportes PDF (Ctrl+Shift+E)")

        self._divider(self)

        # Herramientas
        lab_tools = ctk.CTkLabel(self, text="Herramientas de análisis", font=Fonts.H3, text_color=Theme.MUTED)
        lab_tools.pack(anchor="w", padx=14)

        self.combo_graficos = ctk.CTkComboBox(self, values=self.graficos_disponibles, state="disabled",
                                              command=self.commands["show_graph"], height=36)
        self.combo_graficos.pack(fill="x", padx=12, pady=(6, 6))
        Tooltip(self.combo_graficos, "Seleccioná la variable a visualizar")

        btn_export = ctk.CTkButton(self, text="Exportar a PDF", image=self.icons["export"], compound="left",
                                   font=Fonts.BODY, height=36, corner_radius=10,
                                   fg_color=Theme.SURFACE, hover_color=Theme.DIVIDER,
                                   state="disabled", command=self.commands["export"])
        btn_export.pack(fill="x", padx=12, pady=(0, 12))
        Tooltip(btn_export, "Guardar reporte formateado (Ctrl+S)")

        # Guardar referencias para habilitar/deshabilitar
        self.boton_procesar = btn_proc
        self.boton_comparar = btn_evol
        self.boton_exportar = btn_export

    def set_button_state(self, is_enabled: bool):
        state = "normal" if is_enabled else "disabled"
        self.boton_procesar.configure(state=state)
        self.boton_comparar.configure(state=state)

    def set_post_analysis_state(self, is_enabled: bool):
        state = "normal" if is_enabled else "disabled"
        self.boton_exportar.configure(state=state)
        self.combo_graficos.configure(state=state)


class ResultsArea(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, corner_radius=12, fg_color=Theme.CARD)
        self._build()

    def _build(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # encabezado del panel
        head = ctk.CTkLabel(self, text="Diagnóstico y Visualización", font=Fonts.H2, text_color=Theme.TEXT)
        head.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))

        # Tabs: Resumen / Gráfico
        self.tabs = ctk.CTkTabview(self, fg_color=Theme.SURFACE, segmented_button_selected_color=Theme.ACCENT)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)

        tab_resumen = self.tabs.add("Resumen")
        tab_grafico = self.tabs.add("Gráfico")

        # Resumen: textbox monoespaciado
        tab_resumen.grid_rowconfigure(0, weight=1)
        tab_resumen.grid_columnconfigure(0, weight=1)
        self.area_resultados = ctk.CTkTextbox(tab_resumen, font=Fonts.MONO, corner_radius=8,
                                              wrap="word", border_width=0, fg_color=Theme.BG,
                                              text_color=Theme.TEXT)
        self.area_resultados.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.area_resultados.insert("0.0", "Sistema listo para procesar reportes...")

        # Gráfico: frame contenedor
        tab_grafico.grid_rowconfigure(0, weight=1)
        tab_grafico.grid_columnconfigure(0, weight=1)
        self.grafico_frame = ctk.CTkFrame(tab_grafico, corner_radius=8, fg_color=Theme.BG)
        self.grafico_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

    def clear_panels(self, clear_graph=True):
        self.area_resultados.delete("1.0", "end")
        if clear_graph:
            for widget in self.grafico_frame.winfo_children():
                widget.destroy()


# =========================
# App Principal
# =========================
class TitanApp:
    def __init__(self):
        self._setup_app()
        self._load_config()
        self._build_layout()
        self._bind_shortcuts()

        self.toast = Toast(self.root)
        self.loader = LoadingOverlay(self.root)

    # -------- Setup --------
    def _setup_app(self):
        Theme.apply_dark()
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.title("Proyecto Titán v8.0 – Visualizador de Telemetría")
        self.root.geometry("1200x800")
        self.root.minsize(980, 720)
        self.root.configure(fg_color=Theme.BG)
        self._center_window()
        self.root.protocol("WM_DELETE_WINDOW", self.cerrar_aplicacion)

        # menú
        self._build_menu()

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        archivo = tk.Menu(menubar, tearoff=0)
        archivo.add_command(label="Procesar reporte…    Ctrl+O", command=self.procesar_reporte_individual)
        archivo.add_command(label="Generar evolución…   Ctrl+Shift+E", command=self.generar_reporte_evolucion)
        archivo.add_separator()
        archivo.add_command(label="Exportar PDF…        Ctrl+S", command=self.exportar_a_pdf)
        archivo.add_separator()
        archivo.add_command(label="Salir                Ctrl+Q", command=self.cerrar_aplicacion)
        menubar.add_cascade(label="Archivo", menu=archivo)

        vista = tk.Menu(menubar, tearoff=0)
        vista.add_command(label="Tema oscuro", command=lambda: Theme.apply_dark())
        vista.add_command(label="Tema claro", command=lambda: Theme.apply_light())
        vista.add_separator()
        for scale in (80, 90, 100, 110, 120, 130, 150):
            vista.add_command(
                label=f"Escala UI {scale}%",
                command=lambda s=scale: ctk.set_widget_scaling(s / 100.0)
            )
        menubar.add_cascade(label="Vista", menu=vista)

        ayuda = tk.Menu(menubar, tearoff=0)
        ayuda.add_command(label="Acerca de", command=lambda: messagebox.showinfo(
            "Proyecto Titán",
            "Proyecto Titán v8.0\nRediseño visual y mejoras de UX.\n"
            "Mantiene compatibilidad con tus módulos existentes."
        ))
        menubar.add_cascade(label="Ayuda", menu=ayuda)

        self.root.config(menu=menubar)

    def _load_config(self):
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
            "Novato": {"titulo": "Ruta de Iniciación", "ejercicios": ["1.1. Controles", "2.1. Conducción básica"]},
            "Sin nocion del espacio": {"titulo": "Ruta de Precisión Espacial", "ejercicios": ["2.2. Curvas en S", "5.7. Carga Vertical (Visión Obstruida)"]},
            "Apurado": {"titulo": "Ruta de Control de Impulsos", "ejercicios": ["Módulo 4 (Apilamiento)", "7.1. Operación con Señales"]},
            "Ineficiente": {"titulo": "Ruta de Productividad", "ejercicios": ["Módulo 6 (Estanterías)"]},
            "Eficiente": {"titulo": "Ruta de Especialización", "ejercicios": ["Módulo 8 (Cargas Pesadas)", "Módulo 9 (Carga de Vehículos)"]}
        }

    # -------- Layout --------
    def _build_layout(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=3)

        # Header superior
        self.header = Header(self.root)
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))

        # Sidebar (ControlCard)
        icons = {
            "process": IconFactory.create_icon("process"),
            "compare": IconFactory.create_icon("compare"),
            "export": IconFactory.create_icon("export"),
            "settings": IconFactory.create_icon("settings"),
        }
        commands = {
            "process": self.procesar_reporte_individual,
            "compare": self.generar_reporte_evolucion,
            "export": self.exportar_a_pdf,
            "show_graph": self.mostrar_grafico_seleccionado,
        }

        self.sidebar = ControlCard(self.root, commands, icons, self.GRAFICOS_DISPONIBLES)
        self.sidebar.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(6, 6))
        self.root.grid_columnconfigure(0, weight=1, minsize=340)

        # Área principal de resultados
        self.results = ResultsArea(self.root)
        self.results.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(6, 6))
        self.root.grid_columnconfigure(1, weight=3, minsize=640)

        # Status Bar
        self.status = StatusBar(self.root)
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew")

    def _bind_shortcuts(self):
        self.root.bind_all("<Control-o>", lambda e: self.procesar_reporte_individual())
        self.root.bind_all("<Control-S>", lambda e: self.exportar_a_pdf())
        self.root.bind_all("<Control-s>", lambda e: self.exportar_a_pdf())
        self.root.bind_all("<Control-Shift-E>", lambda e: self.generar_reporte_evolucion())
        self.root.bind_all("<Control-q>", lambda e: self.cerrar_aplicacion())

    def _center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _update_ui_state(self, buttons_enabled: bool, analysis_active: bool):
        self.sidebar.set_button_state(buttons_enabled)
        self.sidebar.set_post_analysis_state(analysis_active)
        if not analysis_active:
            self.ultimo_analisis_realizado = None
            self.session_id_actual = None

    # --------- Lógica principal ---------
    def procesar_reporte_individual(self):
        self._update_ui_state(buttons_enabled=False, analysis_active=False)
        self.status.set(left="Esperando selección de archivo…")
        try:
            pdf_path = filedialog.askopenfilename(
                title="Seleccioná un reporte PDF",
                filetypes=[("Archivos PDF", "*.pdf")]
            )
            if not pdf_path:
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                self.status.set(left="Acción cancelada.")
                return

            self.results.clear_panels()
            self.results.area_resultados.insert("1.0", f"🧠 Procesando: {os.path.basename(pdf_path)}...\n")
            self.status.set(left="Procesando reporte…", right=os.path.basename(pdf_path))
            self.loader.open("Analizando reporte…")

            self.root.update()

            self.session_id_actual = self._procesar_y_obtener_id(pdf_path)
            if not self.session_id_actual:
                self._error_ui("Error de Procesamiento", "No se pudo procesar el archivo PDF.")
                return

            with get_db_connection(self.DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                info_sesion = dict(
                    conn.execute("SELECT * FROM Sesiones WHERE id_sesion = ?", (self.session_id_actual,)).fetchone()
                )

            analisis_comportamiento = analizar_comportamiento_completo(self.session_id_actual)

            self.ultimo_analisis_realizado = {
                "info_sesion": info_sesion,
                "perfil_predicho": info_sesion.get('perfil_operador'),
                "ruta_recomendada": self.RUTAS_DE_APRENDIZAJE.get(info_sesion.get('perfil_operador')),
                "analisis_comportamiento": analisis_comportamiento
            }

            texto = generar_texto_reporte_individual(self.ultimo_analisis_realizado)
            self.results.area_resultados.delete("1.0", "end")
            self.results.area_resultados.insert("1.0", texto)

            # Seleccionar tab de resumen y mostrar gráfico por defecto
            self.results.tabs.set("Gráfico")
            self.sidebar.combo_graficos.set("Steering")
            self.mostrar_grafico_seleccionado()

            self.toast.show("Reporte procesado con éxito", kind="success")
            self.status.set(left="Listo.", right=f"Sesión #{self.session_id_actual}")
        except Exception:
            self._error_ui("Error Crítico", traceback.format_exc())
        finally:
            self.loader.close()
            self._update_ui_state(buttons_enabled=True, analysis_active=(self.ultimo_analisis_realizado is not None))

    def generar_reporte_evolucion(self):
        self._update_ui_state(buttons_enabled=False, analysis_active=False)
        self.status.set(left="Seleccioná reportes para comparar…")
        try:
            self.results.clear_panels()
            pdf_path_inicial = filedialog.askopenfilename(
                title="Paso 1: Reporte INICIAL", filetypes=[("Archivos PDF", "*.pdf")]
            )
            if not pdf_path_inicial:
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                self.status.set(left="Acción cancelada.")
                return

            pdf_path_final = filedialog.askopenfilename(
                title="Paso 2: Reporte FINAL", filetypes=[("Archivos PDF", "*.pdf")]
            )
            if not pdf_path_final:
                self._update_ui_state(buttons_enabled=True, analysis_active=False)
                self.status.set(left="Acción cancelada.")
                return

            self.results.area_resultados.insert("1.0", "Procesando y comparando reportes…\n")
            self.loader.open("Generando reporte de evolución…")
            self.root.update()

            id_inicial = self._procesar_y_obtener_id(pdf_path_inicial)
            id_final = self._procesar_y_obtener_id(pdf_path_final)
            if not id_inicial or not id_final:
                self._error_ui("Error", "No se pudo procesar uno o ambos archivos.")
                return

            reporte_comparativo = generar_reporte_evolucion(id_inicial, id_final)
            self.results.area_resultados.delete("1.0", "end")
            self.results.area_resultados.insert("1.0", reporte_comparativo)

            self.results.tabs.set("Resumen")
            self.toast.show("Reporte comparativo generado", kind="success")
            self.status.set(left="Comparación completada.", right=f"#{id_inicial} → #{id_final}")
        except Exception:
            self._error_ui("Error Crítico", traceback.format_exc())
        finally:
            self.loader.close()
            self._update_ui_state(buttons_enabled=True, analysis_active=False)

    def mostrar_grafico_seleccionado(self, _=None):
        if not self.session_id_actual:
            return
        try:
            nombre_grafico = self.sidebar.combo_graficos.get()
            for w in self.results.grafico_frame.winfo_children():
                w.destroy()
            datos_grafico = get_telemetry_for_graph(self.session_id_actual, nombre_grafico)
            dibujar_grafico_telemetria(self.results.grafico_frame, datos_grafico, nombre_grafico)
            self.status.set(left=f"Mostrando: {nombre_grafico}", right=f"Sesión #{self.session_id_actual}")
        except Exception:
            self._error_ui("Error al dibujar gráfico", traceback.format_exc())

    def exportar_a_pdf(self):
        if not self.ultimo_analisis_realizado:
            messagebox.showwarning("Sin Datos", "Primero debés procesar un reporte individual.")
            return

        nombre = self.ultimo_analisis_realizado['info_sesion'].get('nombre_operador', 'Operador').replace(' ', '_')
        ruta_guardado = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("Archivos PDF", "*.pdf")],
            initialfile=f"Reporte-{nombre}.pdf",
            title="Guardar Reporte en PDF"
        )
        if not ruta_guardado:
            self.status.set(left="Exportación cancelada.")
            return

        try:
            self.loader.open("Exportando a PDF…")
            crear_reporte_pdf(self.ultimo_analisis_realizado, ruta_guardado)
            self.toast.show("Reporte PDF guardado", kind="success")
            self.status.set(left="PDF exportado correctamente.", right=os.path.basename(ruta_guardado))
        except Exception:
            self._error_ui("Error de Exportación", traceback.format_exc())
        finally:
            self.loader.close()

    # --------- Helpers de backend ---------
    def _procesar_y_obtener_id(self, pdf_path: str) -> Optional[int]:
        file_name = os.path.basename(pdf_path)
        with get_db_connection(self.DB_PATH) as conn:
            session_id = get_session_id_by_filename(conn, file_name)
            if session_id:
                # ya existe — copiamos el PDF si faltara en exports
                try:
                    dst = os.path.join(self.EXPORTS_DIR, file_name)
                    if not os.path.exists(dst):
                        shutil.copy2(pdf_path, dst)
                except Exception:
                    pass
                return session_id

            parsed_data = parse_pdf_report(pdf_path)
            if not parsed_data:
                return None

            model_path = os.path.join(project_root, 'models', 'modelo_clasificador.joblib')
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"No se encontró el modelo en: {model_path}")

            modelo = joblib.load(model_path)
            df_pred = self._preparar_datos_para_prediccion(parsed_data, getattr(modelo, "feature_names_in_", []))
            perfil_predicho = modelo.predict(df_pred)[0]

            conn.execute("BEGIN")
            new_session_id = insert_session(conn, parsed_data, perfil_predicho)
            if not new_session_id:
                conn.rollback()
                return None

            insert_summary_events(conn, new_session_id, parsed_data['summary_events'])
            toda_la_telemetria = extraer_toda_la_telemetria(pdf_path, parsed_data['session_data']['duracion_segundos'])
            for nombre_grafico, datos_telemetria in toda_la_telemetria.items():
                if datos_telemetria:
                    insert_telemetry_data(conn, new_session_id, nombre_grafico, datos_telemetria)
            conn.commit()

            # Guardar copia del PDF importado
            try:
                shutil.copy2(pdf_path, os.path.join(self.EXPORTS_DIR, file_name))
            except Exception:
                pass

            return new_session_id

    def _preparar_datos_para_prediccion(self, parsed_data, feature_names):
        # Construir dict base con campos principales
        data_for_df = {
            'puntaje_final': parsed_data['session_data']['puntaje_final'],
            'duracion_segundos': parsed_data['session_data']['duracion_segundos']
        }
        # Eventos resumidos → features esperadas
        for event in parsed_data['summary_events']:
            event_type = event['type'].replace(' ', '_')
            data_for_df[f"conteo_eventos_{event_type}"] = float(event.get('total_events', 0))
            data_for_df[f"penalizaciones_{event_type}"] = float(event.get('penalties', 0))

        # Alinear columnas con el modelo
        if feature_names is None or len(feature_names) == 0:
            # fallback si el modelo no tiene feature_names_in_
            df = pd.DataFrame([data_for_df]).fillna(0)
        else:
            df = pd.DataFrame([data_for_df], columns=feature_names).fillna(0)

        return df

    def _error_ui(self, titulo, detalle):
        self.results.clear_panels()
        self.results.area_resultados.insert("1.0", f"❌ {titulo}\n" + "=" * 60 + f"\n\n{detalle}")
        self.toast.show(titulo, kind="error", duration=3500)
        self.status.set(left="Se produjo un error. Revisá el detalle en Resumen.")

    # --------- Ciclo de vida ---------
    def cerrar_aplicacion(self, *_args):
        if messagebox.askokcancel("Salir", "¿Cerrar Proyecto Titán?"):
            self.root.quit()
            self.root.destroy()
            sys.exit(0)

    def ejecutar(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = TitanApp()
    app.ejecutar()
