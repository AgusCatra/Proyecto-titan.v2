---
kind: configuration_system
name: Hardcoded and File-Based Configuration for Telemetry Extraction, Manifests.md
category: configuration_system
scope:
    - '**'
source_files:
    - core/telemetry_parser.py
    - core/graph_mapper.py
    - mapeo.json
    - manifest.csv
    - main.py
    - core/db_manager.py
    - database/schema.sql
    - setup_database.py
    - app.py
    - streamlit_app.py
---

## What system/approach is used

The project does not use a dedicated configuration framework (no `.env`, YAML/JSON config loader, or environment-variable-based settings). Instead, runtime behavior is controlled through three complementary mechanisms:

1. **In-process Python constants** — module-level dictionaries that act as the central configuration for telemetry graph extraction.
2. **Data-driven configuration files** — JSON mapping tables and CSV manifests that describe how raw PDF graphs map to semantic names and which PDFs to process.
3. **SQLite schema + script-driven DB setup** — database structure is defined in `database/schema.sql` and applied via `setup_database.py`; paths are hard-coded relative to the project root.

There is no layered precedence (e.g., env vars overriding files), no feature-flag toggle, and no secrets management. All configuration is static and checked at startup.

## Key files and packages

- `core/telemetry_parser.py` — defines `GRAPH_CONFIGS`, the single source of truth for which PDF pages, images, color ranges, pixel calibrations, and real-value ranges each telemetry signal uses.
- `core/graph_mapper.py` — loads `mapeo.json` from the project root and maps internal graph identifiers (`Graph_5_1`, etc.) to human-readable names (`Steering`, `Brake Pad`, …).
- `mapeo.json` — JSON array of `{graph, suggested_name}` entries; edited when new graphs appear in reports.
- `manifest.csv` — CSV listing every PDF report to process, with columns `nombre_archivo_pdf`, `perfil_etiquetado`, `id_operador`, `nombre_ejercicio`, `fecha_creacion`. Consumed by `main.py`.
- `main.py` — top-level ETL entry point; holds a local `CONFIG = {'reports_dir', 'db_path', 'manifest_path'}` dict and validates their existence before processing.
- `core/db_manager.py` — hard-codes `DB_PATH = os.path.join(BASE_DIR, 'database', 'titan.db')` and provides connection/session/telemetry helpers.
- `database/schema.sql` — SQLite DDL defining `Sesiones`, `ResumenEventos`, `Telemetria` tables and indexes; re-created on every run of `setup_database.py`.
- `setup_database.py` — one-shot initializer that drops/recreates the DB using `schema.sql`.
- `app.py` (Tkinter) and `streamlit_app.py` — UI entry points that duplicate path constants (`DB_PATH`, `MODELS_PATH`, `EXPORTS_DIR`) and embed learning-path mappings (`RUTAS_DE_APRENDIZAJE`) and available graph lists inline.

## Architecture and conventions

### Telemetry graph configuration
Every extractable signal is described by an entry in `GRAPH_CONFIGS` inside `core/telemetry_parser.py`. Each entry specifies:
- `page`: PDF page number where the graph lives.
- `image_index`: which image on that page contains the plot.
- `color_lower` / `color_upper`: HSV bounds used by OpenCV to mask the plotted line.
- `calib_pixel_x` / `calib_pixel_y`: pixel-coordinate calibration corners.
- `calib_real_y`: real-world value range mapped from the Y-axis pixels.

Adding a new graph means adding one dictionary entry; there is no validation schema beyond what `pdfplumber` and OpenCV can handle at runtime.

### Graph-name resolution
Raw extracted graphs come back under generic keys like `Graph_5_1`. `core/graph_mapper.load_mapping()` reads `mapeo.json` and remaps them to stable names such as `Steering`, `Brake Pad`, `Acceleration Pad`, `Speed In Km/h`, `Fork Height In Mtrs`, `Tilt Angle In Deg`. The same set of names is duplicated as `GRAFICOS_DISPONIBLES` in both `app.py` and `streamlit_app.py`, so the UI and the pipeline must stay in sync manually.

### Batch manifest
`main.py` drives batch processing exclusively from `manifest.csv`. It validates required columns (`nombre_archivo_pdf`, `perfil_etiquetado`, `id_operador`, `nombre_ejercicio`, `fecha_creacion`) and skips rows missing critical fields. A session is considered already processed if its `nombre_archivo_origen` exists in the `Sesiones` table, making the manifest idempotent.

### Database configuration
Database location is fixed to `database/titan.db` relative to the project root. `setup_database.py` deletes the existing file if present and re-applies `database/schema.sql`, which itself starts with `DROP TABLE IF EXISTS` statements to guarantee a clean state. Paths are computed via `os.path.dirname(os.path.abspath(__file__))` so they work regardless of CWD.

### UI-layer configuration
Both GUI frontends hard-code:
- `DB_PATH`, `MODELS_PATH`, `EXPORTS_DIR` relative to `project_root = os.path.dirname(os.path.abspath(__file__))`.
- `RUTAS_DE_APRENDIZAJE` mapping operator profiles (`Novato`, `Sin nocion del espacio`, `Apurado`, `Ineficiente`, `Eficiente`) to recommended training exercises.
- `GRAFICOS_DISPONIBLES` list of chart names shown in dropdowns.

## Conventions and constraints

- **No environment variables**: No code reads `os.environ` or `.env` files for application configuration. All paths and parameters are embedded in source or data files.
- **Paths are relative to the project root**: Both `app.py` and `streamlit_app.py` compute `project_root` and resolve `database/`, `models/`, `data/exports/` from it. Running the app from another directory requires adjusting `sys.path` (which both entry points do explicitly).
- **Schema drift is enforced by re-creation**: `setup_database.py` always drops and recreates tables; there is no migration system. Changing `schema.sql` requires re-running the setup script.
- **Manifest is the source of truth for batch runs**: `main.py._validate_structure()` fails fast if `manifest.csv`, the reports directory, or the DB file is missing. Missing PDFs per row result in an `errors` count, not a crash.
- **Graph configs are the only place to add signals**: The comment in `telemetry_parser.py` states that adding a new graph requires only adding a new entry to `GRAPH_CONFIGS`; callers iterate over this dict automatically.
- **Name consistency is manual**: The six canonical graph names live in three places (`GRAPH_CONFIGS`, `mapeo.json`, and the UI `GRAFICOS_DISPONIBLES`). There is no automated check that they match.
- **Models and exports are expected artifacts**: `app.py` and `streamlit_app.py` assume `models/modelo_clasificador.joblib` exists; if missing, they show an error dialog/message rather than falling back.
- **Telemetry series are stored as comma-separated strings**: `insert_telemetry_data` serializes timestamps and values as `"0.0,0.48,..."` into TEXT columns; retrieval parses them back in `get_telemetry_for_graph`.
