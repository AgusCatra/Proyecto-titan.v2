#!/usr/bin/env python3
# smoke_test_pipeline.py
"""Smoke test reproducible del pipeline ETL unificado (``core/pipeline.py``).

Ejecuta ``process_simulator_pdf()`` sobre un PDF real de ``data/reports/`` usando
una **base de datos SQLite TEMPORAL** (creada desde ``database/schema.sql``), de
modo que la base real (``database/titan.db``) y ``data/exports/`` NO se modifican.

Imprime en consola:
  * si se insertó la sesión (y su id),
  * el perfil de operador asignado,
  * cuáles de las 6 curvas canónicas de telemetría se extrajeron y persistieron.

Uso (dentro del entorno virtual del proyecto):
    python smoke_test_pipeline.py
    python smoke_test_pipeline.py "data/reports/report test.pdf"
    python smoke_test_pipeline.py --engine parser
    python smoke_test_pipeline.py --profile-source none

Códigos de salida: 0 = PASS, 1 = FAIL, 2 = error de entorno/uso.
"""
import argparse
import logging
import os
import sqlite3
import sys
import tempfile

# --- Hacer importable el paquete `core` al ejecutar desde la raíz ---
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

REPORTS_DIR = os.path.join(PROJECT_ROOT, "data", "reports")
SCHEMA_PATH = os.path.join(PROJECT_ROOT, "database", "schema.sql")
MODELS_PATH = os.path.join(PROJECT_ROOT, "models", "modelo_clasificador.joblib")

# Las 6 señales canónicas que el pipeline debe poder persistir.
CANONICAL_SIGNALS = [
    "Steering",
    "Brake Pad",
    "Acceleration Pad",
    "Fork Height In Mtrs",
    "Tilt Angle In Deg",
    "Speed In Km/h",
]

PREFERRED_PDF = "report test.pdf"


def _pick_default_pdf() -> str | None:
    """Elige un PDF de data/reports/ (prefiere 'report test.pdf')."""
    if not os.path.isdir(REPORTS_DIR):
        return None
    pdfs = sorted(f for f in os.listdir(REPORTS_DIR) if f.lower().endswith(".pdf"))
    if not pdfs:
        return None
    if PREFERRED_PDF in pdfs:
        return os.path.join(REPORTS_DIR, PREFERRED_PDF)
    return os.path.join(REPORTS_DIR, pdfs[0])


def _create_temp_db(schema_path: str) -> str:
    """Crea una BD SQLite temporal aplicando el DDL de schema.sql."""
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"No se encontró el esquema SQL en: {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
        ddl = f.read()
    fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="titan_smoke_")
    os.close(fd)
    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(ddl)
        conn.commit()
    finally:
        conn.close()
    return tmp_path


def _print_metadata(metadata: dict) -> None:
    if not metadata:
        print("   (sin metadatos)")
        return
    print(f"   Operador     : {metadata.get('nombre_operador')}")
    print(f"   Ejercicio    : {metadata.get('nombre_ejercicio')}")
    print(f"   Clase        : {metadata.get('nombre_clase')}")
    print(f"   Duración (s) : {metadata.get('duracion_segundos')}")
    print(f"   Puntaje      : {metadata.get('puntaje_final')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test headless del pipeline ETL unificado de Proyecto Titán."
    )
    parser.add_argument(
        "pdf", nargs="?", default=None,
        help="Ruta al PDF de reporte. Si se omite, elige uno de data/reports/.",
    )
    parser.add_argument(
        "--engine", choices=["visual", "parser"], default="visual",
        help="Motor de extracción de telemetría (por defecto: visual).",
    )
    parser.add_argument(
        "--profile-source", choices=["model", "none"], default="model",
        help="Fuente del perfil de operador (por defecto: model).",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Muestra logs DEBUG del pipeline y los extractores.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Import tardío: permite mostrar un mensaje claro si faltan dependencias.
    try:
        from core.pipeline import process_simulator_pdf
    except Exception as exc:  # ImportError u OSError por librerías nativas ausentes
        print("❌ No se pudieron importar las dependencias del pipeline:")
        print(f"   {type(exc).__name__}: {exc}")
        print("\n   Activá tu entorno virtual e instalá las dependencias:")
        print("     source .venv/bin/activate      # o tu venv")
        print("     pip install -r requirements.txt")
        print("   Recordá que el engine 'visual' requiere el binario de Tesseract OCR.")
        return 2

    pdf_path = args.pdf or _pick_default_pdf()
    if not pdf_path or not os.path.exists(pdf_path):
        print(f"❌ No se encontró ningún PDF en '{REPORTS_DIR}'.")
        print("   Pasá una ruta explícita: python smoke_test_pipeline.py ruta/al/reporte.pdf")
        return 2

    tmp_db = _create_temp_db(SCHEMA_PATH)
    exit_code = 1
    try:
        print("=" * 74)
        print("SMOKE TEST — core/pipeline.py :: process_simulator_pdf")
        print("=" * 74)
        print(f"PDF          : {os.path.basename(pdf_path)}")
        print(f"Engine       : {args.engine}")
        print(f"Profile src  : {args.profile_source}")
        print(f"BD temporal  : {tmp_db}")
        print("                (la BD real database/titan.db NO se modifica)")
        print("-" * 74)

        resultado = process_simulator_pdf(
            pdf_path,
            profile_source=args.profile_source,
            db_path=tmp_db,
            models_path=MODELS_PATH,
            engine=args.engine,
            copy_to_exports=False,  # no ensuciar data/exports/
        )

        session_id = resultado.get("session_id")
        metadata = resultado.get("metadata") or {}
        profile = resultado.get("profile")
        telemetry_loaded = resultado.get("telemetry_loaded") or {}
        errors = resultado.get("errors") or []
        cached = resultado.get("cached")

        # 1) ¿Se insertó la sesión?
        estado_sesion = "✅ SÍ" if session_id else "❌ NO"
        extra = " (cacheada)" if cached else ""
        print(f"1) Sesión insertada : {estado_sesion}  id={session_id}{extra}")
        _print_metadata(metadata)

        # 2) Perfil asignado
        print(f"2) Perfil asignado  : {profile if profile else '⚠ sin perfil (None)'}")

        # 3) Las 6 curvas canónicas
        print(f"3) Curvas extraídas : {len(telemetry_loaded)}/6")
        for signal in CANONICAL_SIGNALS:
            puntos = telemetry_loaded.get(signal)
            marca = "✅" if puntos else "❌"
            print(f"   {marca} {signal:<20} : {puntos if puntos else 0} puntos")

        # Verificación directa contra la BD temporal
        conn = sqlite3.connect(tmp_db)
        try:
            n_tel = conn.execute("SELECT COUNT(*) FROM Telemetria").fetchone()[0]
            n_ev = conn.execute("SELECT COUNT(*) FROM ResumenEventos").fetchone()[0]
            n_se = conn.execute("SELECT COUNT(*) FROM Sesiones").fetchone()[0]
            nombres = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT nombre_grafico FROM Telemetria ORDER BY nombre_grafico"
                ).fetchall()
            ]
        finally:
            conn.close()

        print("-" * 74)
        print(f"BD temporal -> Sesiones: {n_se} | ResumenEventos: {n_ev} | Telemetria: {n_tel} fila(s)")
        if nombres:
            print(f"Señales persistidas: {', '.join(nombres)}")

        if errors:
            print("⚠ Errores/avisos controlados del pipeline:")
            for e in errors:
                print(f"   - {e}")

        # Criterio de éxito: sesión guardada + al menos 1 curva persistida en BD.
        ok = bool(session_id) and n_se > 0 and n_tel > 0
        exit_code = 0 if ok else 1

        print("=" * 74)
        if ok:
            print(f"RESULTADO: ✅ PASS — sesión #{session_id} guardada y "
                  f"{len(telemetry_loaded)} curva(s) de telemetría persistidas.")
            if len(telemetry_loaded) < 6:
                print("           Nota: no se extrajeron las 6 señales; revisá la "
                      "calibración/OCR del engine elegido (no es un fallo del pipeline).")
        else:
            print("RESULTADO: ❌ FAIL — no se persistió sesión/telemetría. "
                  "Revisá los errores de arriba (¿deps instaladas? ¿Tesseract?).")
        print("=" * 74)
    finally:
        # Dejar todo limpio: eliminar la BD temporal.
        try:
            os.remove(tmp_db)
            print(f"🧹 BD temporal eliminada: {os.path.basename(tmp_db)}")
        except OSError:
            pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
