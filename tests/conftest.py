# tests/conftest.py
# Proyecto Titán — Fixtures y utilidades compartidas de la suite de pruebas.
#
# Todas las pruebas trabajan sobre bases de datos SQLite TEMPORALES creadas desde
# ``database/schema.sql``: la BD productiva ``database/titan.db`` nunca se modifica.

import os
import sqlite3
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

# --- Raíz del proyecto en el sys.path (permite ``import core.*`` desde tests/) ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SCHEMA_SQL = os.path.join(PROJECT_ROOT, "database", "schema.sql")
DB_PRODUCCION = os.path.join(PROJECT_ROOT, "database", "titan.db")
REPORTS_DIR = os.path.join(PROJECT_ROOT, "data", "reports")
MODELS_PATH = os.path.join(PROJECT_ROOT, "models", "modelo_clasificador.joblib")

# Muestreo por defecto de las series sintéticas (por debajo de ``DT_MAX`` = 3.0 s).
DT = 0.5

# Separación entre eventos de frenada: por encima de ``DT_MAX`` para que cada
# evento quede aislado y el hueco no aporte tiempo ni tasa.
SEPARACION_EVENTOS = 4.0


# =============================================================================
# CONSTRUCTORES DE SERIES SINTÉTICAS DE TELEMETRÍA
# =============================================================================
def serie_plana(valor: float, puntos: int = 40, dt: float = DT, t0: float = 0.0) -> List[Tuple[float, float]]:
    """Serie constante: no aporta eventos (dato operativo legítimo)."""
    return [(t0 + i * dt, float(valor)) for i in range(puntos)]


def serie_frenadas_bruscas(eventos: int, dt: float = DT) -> List[Tuple[float, float]]:
    """Pedal de freno 0 -> 10 -> 0 repetido: genera exactamente ``eventos`` frenadas."""
    serie: List[Tuple[float, float]] = []
    t = 0.0
    for _ in range(max(int(eventos), 0)):
        serie.extend([(t, 0.0), (t + dt, 10.0), (t + 2 * dt, 10.0), (t + 3 * dt, 0.0)])
        t += 3 * dt + SEPARACION_EVENTOS
    return serie


def serie_volantazos(inversiones: int, amplitud: float = 5.0, dt: float = DT) -> List[Tuple[float, float]]:
    """Onda triangular de dirección: genera exactamente ``inversiones`` volantazos."""
    puntos = max(int(inversiones), 0) + 2
    return [
        (i * dt, float(amplitud) if i % 2 else 0.0)
        for i in range(puntos)
    ]


def serie_microajustes_torre(inversiones: int, amplitud: float = 0.10, dt: float = DT) -> List[Tuple[float, float]]:
    """Oscilación fina de horquilla: genera microajustes por encima del umbral."""
    puntos = max(int(inversiones), 0) + 2
    base = 0.40
    return [
        (i * dt, base + (amplitud if i % 2 else 0.0))
        for i in range(puntos)
    ]


# =============================================================================
# PERSISTENCIA DE DATOS DE PRUEBA
# =============================================================================
def _csv(pares: Sequence[Tuple[float, float]]) -> Tuple[str, str]:
    """Serializa una serie a los dos CSV paralelos que usa la tabla ``Telemetria``."""
    timestamps = ",".join(f"{t:.2f}" for t, _ in pares)
    valores = ",".join(f"{v:.2f}" for _, v in pares)
    return timestamps, valores


def crear_bd(ruta: str) -> sqlite3.Connection:
    """Crea una BD SQLite en ``ruta`` aplicando el DDL de ``database/schema.sql``."""
    with open(SCHEMA_SQL, "r", encoding="utf-8") as fh:
        ddl = fh.read()
    conn = sqlite3.connect(ruta)
    conn.row_factory = sqlite3.Row
    conn.executescript(ddl)
    conn.commit()
    return conn


def insertar_sesion(
    conn: sqlite3.Connection,
    id_sesion: int,
    *,
    puntaje_final: float = 50.0,
    penalizaciones: float = 0.0,
    colisiones: int = 0,
    duracion_segundos: int = 120,
    operador: str = "Alumno de Prueba",
    ejercicio: str = "Ejercicio 1",
    clase: str = "Clase A",
    perfil: str = "Novato",
    archivo: Optional[str] = None,
) -> int:
    """Inserta una sesión con su resumen de eventos agregado."""
    conn.execute(
        "INSERT INTO Sesiones (id_sesion, nombre_archivo_origen, nombre_operador, "
        "nombre_clase, nombre_ejercicio, fecha_hora_inicio, duracion_segundos, "
        "puntaje_final, perfil_operador) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            id_sesion,
            archivo or f"reporte_prueba_{id_sesion}.pdf",
            operador,
            clase,
            ejercicio,
            "2026-01-01 08:00:00",
            int(duracion_segundos),
            float(puntaje_final),
            perfil,
        ),
    )
    conn.execute(
        "INSERT INTO ResumenEventos (id_sesion, tipo_evento, conteo_eventos, "
        "recompensas, penalizaciones) VALUES (?, 'Collision', ?, 0.0, ?)",
        (id_sesion, int(colisiones), float(penalizaciones)),
    )
    conn.commit()
    return id_sesion


def insertar_telemetria(
    conn: sqlite3.Connection,
    id_sesion: int,
    nombre_grafico: str,
    pares: Sequence[Tuple[float, float]],
) -> None:
    """Inserta una señal de telemetría en el formato real de la tabla (CSV paralelos)."""
    timestamps, valores = _csv(pares)
    conn.execute(
        "INSERT INTO Telemetria (id_sesion_fk, nombre_grafico, timestamps, valores) "
        "VALUES (?, ?, ?, ?)",
        (id_sesion, nombre_grafico, timestamps, valores),
    )
    conn.commit()


def insertar_sesion_completa(
    conn: sqlite3.Connection,
    id_sesion: int,
    *,
    frenadas: int = 0,
    volantazos: int = 0,
    colisiones: int = 0,
    microajustes_torre: int = 0,
    altura_horquilla: float = 0.10,
    velocidad: float = 5.0,
    puntaje_final: float = 50.0,
    penalizaciones: float = 0.0,
    duracion_segundos: int = 120,
    perfil: str = "Novato",
) -> Dict[str, int]:
    """Inserta una sesión con las 6 señales canónicas y vicios controlados."""
    insertar_sesion(
        conn,
        id_sesion,
        puntaje_final=puntaje_final,
        penalizaciones=penalizaciones,
        colisiones=colisiones,
        duracion_segundos=duracion_segundos,
        perfil=perfil,
    )
    señales: Dict[str, List[Tuple[float, float]]] = {
        "Brake Pad": serie_frenadas_bruscas(frenadas) if frenadas else serie_plana(0.0),
        "Steering": serie_volantazos(volantazos) if volantazos else serie_plana(0.0),
        "Fork Height In Mtrs": serie_plana(altura_horquilla),
        "Tilt Angle In Deg": (
            serie_microajustes_torre(microajustes_torre)
            if microajustes_torre
            else serie_plana(2.0)
        ),
        "Speed In Km/h": serie_plana(velocidad),
        "Acceleration Pad": serie_plana(1.0),
    }
    for nombre, pares in señales.items():
        insertar_telemetria(conn, id_sesion, nombre, pares)
    return {"id_sesion": id_sesion}


# =============================================================================
# FIXTURES
# =============================================================================
@pytest.fixture()
def conn(tmp_path):
    """Conexión a una BD temporal con el esquema completo del proyecto."""
    conexion = crear_bd(str(tmp_path / "titan_test.db"))
    try:
        yield conexion
    finally:
        conexion.close()


@pytest.fixture()
def conn_semillada(conn):
    """BD temporal con dos sesiones comparables (Pre #1 severa, Post #2 limpia)."""
    insertar_sesion_completa(
        conn,
        1,
        frenadas=6,
        volantazos=8,
        colisiones=2,
        altura_horquilla=0.60,
        velocidad=5.0,
        puntaje_final=40.0,
        penalizaciones=-10.0,
        duracion_segundos=120,
        perfil="Apurado",
    )
    insertar_sesion_completa(
        conn,
        2,
        frenadas=2,
        volantazos=2,
        colisiones=0,
        altura_horquilla=0.10,
        velocidad=5.0,
        puntaje_final=70.0,
        penalizaciones=-2.0,
        duracion_segundos=100,
        perfil="Eficiente",
    )
    return conn


@pytest.fixture(scope="session")
def pdf_muestra() -> Optional[str]:
    """Primer PDF disponible en ``data/reports/`` (``None`` si no hay ninguno)."""
    if not os.path.isdir(REPORTS_DIR):
        return None
    pdfs = sorted(f for f in os.listdir(REPORTS_DIR) if f.lower().endswith(".pdf"))
    return os.path.join(REPORTS_DIR, pdfs[0]) if pdfs else None
