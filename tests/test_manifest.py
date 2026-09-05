# tests/test_manifest.py
# Verificación del manifest de ingesta masiva y del esquema de la base de datos.

import os
import sqlite3

import pandas as pd
import pytest
from conftest import DB_PRODUCCION, PROJECT_ROOT, SCHEMA_SQL

MANIFEST_PATH = os.path.join(PROJECT_ROOT, "manifest.csv")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "data", "reports")

COLUMNAS_REQUERIDAS = [
    "nombre_archivo_pdf",
    "perfil_etiquetado",
    "id_operador",
    "nombre_ejercicio",
    "fecha_creacion",
]


@pytest.fixture(scope="module")
def manifest() -> pd.DataFrame:
    if not os.path.exists(MANIFEST_PATH):
        pytest.skip("manifest.csv no está presente en el repositorio")
    return pd.read_csv(MANIFEST_PATH)


def test_manifest_tiene_las_columnas_requeridas(manifest):
    faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in manifest.columns]
    assert not faltantes, f"columnas faltantes en manifest.csv: {faltantes}"
    assert len(manifest) > 0


def test_filas_criticas_del_manifest_estan_completas(manifest):
    """``main.py`` descarta filas sin archivo o sin perfil: no deben existir."""
    assert manifest["nombre_archivo_pdf"].notna().all()
    assert manifest["perfil_etiquetado"].notna().all()


def test_pdfs_del_manifest_existen(manifest):
    faltantes = [
        nombre for nombre in manifest["nombre_archivo_pdf"].dropna()
        if not os.path.exists(os.path.join(REPORTS_DIR, str(nombre)))
    ]
    assert not faltantes, f"PDFs ausentes en data/reports/: {faltantes[:5]}"


def test_manifest_no_duplica_archivos(manifest):
    """El ETL es idempotente por nombre de archivo: no deben repetirse filas."""
    nombres = list(manifest["nombre_archivo_pdf"].dropna())
    duplicados = {n for n in nombres if nombres.count(n) > 1}
    assert not duplicados, f"filas duplicadas en manifest.csv: {sorted(duplicados)[:5]}"


def test_manifest_cubre_varios_perfiles(manifest):
    """El entrenador de IA necesita varias clases para el clasificador."""
    assert manifest["perfil_etiquetado"].nunique() >= 2


def test_schema_sql_declara_las_tablas_de_negocio():
    with open(SCHEMA_SQL, "r", encoding="utf-8") as fh:
        ddl = fh.read().upper()
    for tabla in ("SESIONES", "RESUMENEVENTOS", "TELEMETRIA", "DECISIONINSTRUCTOR"):
        assert tabla in ddl, f"schema.sql no declara la tabla {tabla}"


@pytest.mark.skipif(not os.path.exists(DB_PRODUCCION), reason="database/titan.db no existe")
def test_bd_desplegada_tiene_las_columnas_clave():
    """La BD real puede estar desfasada respecto a schema.sql: se verifica lo crítico."""
    conn = sqlite3.connect(DB_PRODUCCION)
    try:
        tablas = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        columnas_sesiones = {
            r[1] for r in conn.execute("PRAGMA table_info(Sesiones)").fetchall()
        }
        columnas_telemetria = {
            r[1] for r in conn.execute("PRAGMA table_info(Telemetria)").fetchall()
        }
    finally:
        conn.close()

    assert {"Sesiones", "ResumenEventos", "Telemetria"} <= tablas
    assert {"id_sesion", "perfil_operador", "puntaje_final"} <= columnas_sesiones
    # La FK real de telemetría es id_sesion_fk (distinta a la de ResumenEventos).
    assert {"id_sesion_fk", "nombre_grafico", "timestamps", "valores"} <= columnas_telemetria
