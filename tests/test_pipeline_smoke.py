# tests/test_pipeline_smoke.py
# Smoke test automatizado del pipeline ETL unificado (``core/pipeline.py``).
#
# Reubicado desde el script suelto ``smoke_test_pipeline.py`` de la raíz: ahora es
# una prueba formal de la suite (``pytest tests -q``).
#
# Garantías que verifica:
#   * ``process_simulator_pdf`` inserta la sesión y persiste telemetría.
#   * Todo ocurre sobre una BD SQLite TEMPORAL creada desde ``database/schema.sql``:
#     la BD productiva ``database/titan.db`` y ``data/exports/`` NO se modifican.
#   * La ingesta es idempotente por nombre de archivo origen.
#
# Se usa el motor ``parser`` (páginas fijas) porque el motor ``visual`` depende del
# binario de Tesseract OCR, que puede no estar disponible en el entorno de CI.

import os
import sqlite3

import pytest
from conftest import DB_PRODUCCION, MODELS_PATH, crear_bd

from core.graph_mapper import CANONICAL_SIGNALS
from core.pipeline import ENGINE_PARSER, PROFILE_SOURCE_NONE, process_simulator_pdf


@pytest.fixture(scope="module")
def bd_temporal(tmp_path_factory) -> str:
    """BD temporal con el esquema del proyecto (nunca la BD productiva)."""
    ruta = str(tmp_path_factory.mktemp("smoke") / "titan_smoke.db")
    conn = crear_bd(ruta)
    conn.close()
    return ruta


@pytest.fixture(scope="module")
def resultado_pipeline(bd_temporal, pdf_muestra):
    """Ejecuta el pipeline una sola vez para todo el módulo."""
    if not pdf_muestra:
        pytest.skip(f"No hay PDFs de muestra en {os.path.dirname(DB_PRODUCCION)}")

    huella_antes = (
        os.path.getmtime(DB_PRODUCCION) if os.path.exists(DB_PRODUCCION) else None
    )
    resultado = process_simulator_pdf(
        pdf_muestra,
        profile_source=PROFILE_SOURCE_NONE,
        db_path=bd_temporal,
        models_path=MODELS_PATH,
        engine=ENGINE_PARSER,
        copy_to_exports=False,
    )
    resultado["_pdf"] = pdf_muestra
    resultado["_huella_bd_productiva_antes"] = huella_antes
    return resultado


def test_pipeline_inserta_la_sesion(resultado_pipeline):
    assert resultado_pipeline.get("session_id"), (
        "El pipeline no devolvió session_id; errores: "
        f"{resultado_pipeline.get('errors')}"
    )
    assert resultado_pipeline.get("metadata")


def test_pipeline_persiste_telemetria_en_la_bd_temporal(resultado_pipeline, bd_temporal):
    conn = sqlite3.connect(bd_temporal)
    try:
        sesiones = conn.execute("SELECT COUNT(*) FROM Sesiones").fetchone()[0]
        filas = conn.execute("SELECT COUNT(*) FROM Telemetria").fetchone()[0]
        eventos = conn.execute("SELECT COUNT(*) FROM ResumenEventos").fetchone()[0]
        nombres = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT nombre_grafico FROM Telemetria"
            ).fetchall()
        ]
    finally:
        conn.close()

    assert sesiones == 1
    assert filas > 0, "No se persistió ninguna curva de telemetría"
    assert eventos >= 0
    # Los nombres persistidos deben ser señales canónicas del proyecto.
    assert set(nombres).issubset(set(CANONICAL_SIGNALS))


def test_pipeline_no_modifica_la_bd_productiva(resultado_pipeline):
    huella_antes = resultado_pipeline["_huella_bd_productiva_antes"]
    huella_ahora = (
        os.path.getmtime(DB_PRODUCCION) if os.path.exists(DB_PRODUCCION) else None
    )
    assert huella_antes == huella_ahora, "database/titan.db fue modificada por la prueba"


def test_pipeline_no_copia_a_exports(resultado_pipeline):
    assert resultado_pipeline.get("cached") in (False, None)
    assert "exports" not in str(resultado_pipeline.get("errors", [])).lower()


def test_pipeline_es_idempotente_por_nombre_de_archivo(resultado_pipeline, bd_temporal):
    """Procesar dos veces el mismo PDF reutiliza la sesión existente."""
    segundo = process_simulator_pdf(
        resultado_pipeline["_pdf"],
        profile_source=PROFILE_SOURCE_NONE,
        db_path=bd_temporal,
        models_path=MODELS_PATH,
        engine=ENGINE_PARSER,
        copy_to_exports=False,
    )
    assert segundo.get("cached") is True
    assert segundo.get("session_id") == resultado_pipeline.get("session_id")

    conn = sqlite3.connect(bd_temporal)
    try:
        sesiones = conn.execute("SELECT COUNT(*) FROM Sesiones").fetchone()[0]
    finally:
        conn.close()
    assert sesiones == 1
