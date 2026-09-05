# tests/test_arquitectura.py
# Guardas de arquitectura: mantienen el core agnóstico del frontend y evitan que
# vuelva a aparecer código muerto en la raíz del proyecto.

import ast
import fnmatch
import importlib
import os

import pytest
from conftest import PROJECT_ROOT

CORE_DIR = os.path.join(PROJECT_ROOT, "core")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
STREAMLIT_APP = os.path.join(PROJECT_ROOT, "streamlit_app.py")
REPORT_GENERATOR = os.path.join(CORE_DIR, "report_generator.py")
AI_ADVISOR = os.path.join(CORE_DIR, "ai_advisor.py")

# Bibliotecas de interfaz/gráficas que NO deben entrar en ``core/``.
BIBLIOTECAS_UI = {
    "streamlit",
    "customtkinter",
    "tkinter",
    "altair",
    "plotly",
    "matplotlib",
    "PyQt5",
    "PySide6",
    "kivy",
}

MODULOS_CORE = [
    "core.ai_advisor",
    "core.analizador_eventos",
    "core.behavior_analyzer",
    "core.db_manager",
    "core.evaluador_diagnostico",
    "core.graph_mapper",
    "core.pdf_parser",
    "core.pipeline",
    "core.report_generator",
    "core.reporter",
    "core.telemetry_extractor",
    "core.telemetry_parser",
]


def _archivos_python(carpeta: str):
    if not os.path.isdir(carpeta):
        return []
    return sorted(
        os.path.join(carpeta, nombre)
        for nombre in os.listdir(carpeta)
        if nombre.endswith(".py")
    )


def _imports_totales(ruta: str):
    """Todos los paquetes importados en el archivo (incluye imports dentro de funciones)."""
    with open(ruta, "r", encoding="utf-8") as fh:
        arbol = ast.parse(fh.read(), filename=ruta)
    paquetes = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                paquetes.add(alias.name.split(".")[0])
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level == 0 and nodo.module:
                paquetes.add(nodo.module.split(".")[0])
    return paquetes


def _imports_de_modulo(ruta: str):
    """Solo los imports ejecutados al importar el módulo (nivel de módulo)."""
    with open(ruta, "r", encoding="utf-8") as fh:
        arbol = ast.parse(fh.read(), filename=ruta)
    paquetes = set()
    for nodo in arbol.body:
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                paquetes.add(alias.name.split(".")[0])
        elif isinstance(nodo, ast.ImportFrom):
            if nodo.level == 0 and nodo.module:
                paquetes.add(nodo.module.split(".")[0])
    return paquetes


def _fuente(ruta: str) -> str:
    with open(ruta, "r", encoding="utf-8") as fh:
        return fh.read()


# =============================================================================
# CORE AGNÓSTICO DEL FRONTEND
# =============================================================================
@pytest.mark.parametrize("ruta", _archivos_python(CORE_DIR), ids=os.path.basename)
def test_core_no_depende_de_bibliotecas_de_ui(ruta):
    prohibidas = _imports_totales(ruta) & BIBLIOTECAS_UI
    assert not prohibidas, (
        f"{os.path.basename(ruta)} depende de bibliotecas de UI {sorted(prohibidas)}; "
        "los adapters gráficos viven en ui/ y los de web en streamlit_app.py"
    )


def test_ui_es_la_capa_grafica_y_core_no_la_importa():
    assert os.path.isdir(UI_DIR), "falta el paquete ui/ (adapters de interfaz)"
    for ruta in _archivos_python(CORE_DIR):
        assert "ui" not in _imports_totales(ruta), (
            f"{os.path.basename(ruta)} importa la capa ui/: dependencia invertida"
        )


@pytest.mark.parametrize("modulo", MODULOS_CORE)
def test_modulos_core_son_importables(modulo):
    """Ningún módulo de core/ queda huérfano ni con imports rotos."""
    importlib.import_module(modulo)


def test_ai_advisor_no_importa_red_a_nivel_de_modulo():
    """La ruta mock debe poder ejecutarse offline: ``requests`` se importa perezoso."""
    assert "requests" not in _imports_de_modulo(AI_ADVISOR)


# =============================================================================
# FUENTE ÚNICA DE VERDAD DE LAS REGLAS DE NEGOCIO
# =============================================================================
def test_report_generator_no_duplica_umbrales_pedagogicos():
    fuente = _fuente(REPORT_GENERATOR)
    assert "UMBRAL_" not in fuente
    assert "veredicto_por_delta_puntaje" in fuente
    assert "MEJORA SIGNIFICATIVA" not in fuente   # la etiqueta vive en el evaluador


def test_evaluador_concentra_las_reglas():
    evaluador = importlib.import_module("core.evaluador_diagnostico")
    for constante in (
        "COLISIONES_CRITICO", "FRENADAS_EVENTOS_MODERADO", "VOLANTAZOS_EVENTOS_ALTO",
        "TRASLADO_ALTO_SEGUNDOS_ALTO", "UMBRAL_MEJORA_SIGNIFICATIVA",
        "SEVERIDAD_NO_APTO", "COBERTURA_MINIMA_SENALES",
    ):
        assert hasattr(evaluador, constante), f"falta el umbral {constante}"


def test_streamlit_app_no_escribe_sql():
    fuente = _fuente(STREAMLIT_APP).upper()
    for sentencia in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE"):
        assert sentencia not in fuente, (
            f"streamlit_app.py contiene SQL ('{sentencia.strip()}'); "
            "el acceso a datos debe vivir en core/db_manager.py"
        )


def test_streamlit_app_no_hardcodea_las_reglas_de_dictamen():
    fuente = _fuente(STREAMLIT_APP)
    assert '"No Apto Crítico"' not in fuente
    assert "MEJORA SIGNIFICATIVA" not in fuente
    assert "puntaje_final + " not in fuente   # la fórmula vive en el evaluador


# =============================================================================
# HIGIENE DEL REPOSITORIO
# =============================================================================
def test_no_quedan_scripts_de_prueba_sueltos_en_la_raiz():
    sueltos = [
        nombre for nombre in os.listdir(PROJECT_ROOT)
        if fnmatch.fnmatch(nombre, "test_*.py") or fnmatch.fnmatch(nombre, "*_test.py")
    ]
    assert not sueltos, f"mover a tests/: {sueltos}"


def test_no_quedan_scripts_de_debug_en_la_raiz():
    """Solo ARCHIVOS: ``debug_outputs/`` es un directorio de salida legítimo."""
    residuos = [
        nombre for nombre in os.listdir(PROJECT_ROOT)
        if os.path.isfile(os.path.join(PROJECT_ROOT, nombre))
        and (
            nombre.startswith("debug_")
            or nombre.startswith("pagina_")
            or nombre in {"color_picker.py", "coordinate_finder.py", "analisis_mvp.py",
                          "smoke_test_pipeline.py", "crop_debug.png"}
        )
    ]
    assert not residuos, f"artefactos residuales en la raíz: {residuos}"


def test_modulos_huerfanos_eliminados_de_core():
    existentes = {os.path.basename(r) for r in _archivos_python(CORE_DIR)}
    assert "training_manager.py" not in existentes
    assert "training_path.py" not in existentes
    assert "plotter.py" not in existentes
