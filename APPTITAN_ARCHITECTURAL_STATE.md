# AppTitan — Architectural State Document

> **Purpose:** Frozen, evidence-based snapshot of the AppTitan (Proyecto Titán v2) codebase, produced for downstream consumption by a Software Architect agent that will plan AppTitan's integration as a **microservice** inside the **SimuTwin** platform.
> **Audience:** An LLM acting as Software Architect. Prefer facts, file paths, and severity over prose.
> **Method:** Static analysis of 100% of first-party Python sources (`core/`, entry scripts, config), the SQL DDL, the live SQLite database, and `requirements.txt`. No assumptions were made where code could be read.
> **Repository state:** Single-package Python monorepo, no build system, no API layer, no containerization, no CI.

---

## 0. Executive Summary

AppTitan is a **desktop/batch analytics tool** — not a service. It ingests forklift-simulator PDF reports, extracts session metadata + telemetry curves, classifies the operator into a behavior profile, and renders text/PDF/chart output through **two independent frontends** (CustomTkinter desktop and Streamlit web) that share a `core/` package and a local SQLite file.

**Readiness verdict for microservice extraction: LOW (needs a refactor pass first).**

| Dimension | Status | Note |
|---|---|---|
| Domain logic isolation (`core/`) | Partial | Logic is separated, but orchestration is duplicated in the frontends |
| Service/API layer | Absent | No FastAPI/Flask/HTTP surface of any kind |
| Configuration management | Absent | Zero env vars; all paths, thresholds, page numbers hardcoded |
| Persistence | SQLite file | Denormalized time-series stored as CSV-in-TEXT; destructive schema init |
| Packaging / deploy | Absent | No Docker, no lockfile separation, no CI, undocumented OS dep (Tesseract) |
| Test suite | Absent (nominal) | `test_*.py` are ad-hoc print scripts; no assertions, no pytest |
| Observability | Absent | `print()`-based logging only; CV debug images written to disk on every run |
| Correctness | At risk | Broken extractor→mapper contract; orphaned training module; ML collapses to 1 class |

**Top 3 blockers to resolve before any integration work:**
1. **No service boundary / no config injection** — everything is hardcoded relative paths and a local file DB (§6-E, §6-H).
2. **Broken telemetry contract** in the Streamlit path — `map_graphs` silently discards all extracted telemetry (§6-A).
3. **Duplicated, divergent ETL orchestration** across the two frontends with two different extraction engines (§6-B).

---

## 1. Technology Stack & Dependencies

### 1.1 Language & Runtime
- **Language:** Python 3 (modern type hints, `numpy` 2.x / `pandas` 2.x era). Exact minor version is **not pinned anywhere** (no `pyproject.toml`, no `runtime.txt`, no `.python-version`).
- **Package manager:** `pip` against a flat, fully-pinned `requirements.txt` (60 packages). The file is a **`pip freeze` dump**: it mixes ~14 direct dependencies with ~46 transitive ones, with no grouping, no comments, and no direct/transitive separation. No lockfile tooling (pip-tools / poetry / uv) is present.

### 1.2 Frontends (two, independent)
| Framework | Version | Where | Role |
|---|---|---|---|
| **CustomTkinter** | 5.2.2 | `app.py`, `core/plotter.py` | Desktop GUI (`TitanApp` monolith) |
| **Streamlit** | 1.49.1 | `streamlit_app.py` | Browser dashboard |
| **Matplotlib** | 3.10.5 | `core/plotter.py`, `streamlit_app.py` | Telemetry charts (TkAgg backend on desktop) |

### 1.3 Data Processing & Machine Learning
| Library | Version | Role |
|---|---|---|
| **pandas** | 2.3.1 | Manifest handling, feature pivoting, telemetry DataFrames |
| **numpy** | 2.2.6 | Numeric ops, curve extraction, signal metrics |
| **scipy** | 1.16.1 | Transitive (scikit-learn); not directly imported |
| **scikit-learn** | 1.7.1 | `RandomForestClassifier` (operator-profile model) in `entrenador_ia.py` |
| **joblib** | 1.5.1 | Model serialization (`models/modelo_clasificador.joblib`) |
| **pyarrow** | 21.0.0 | Transitive (Streamlit/pandas); not directly imported |

### 1.4 PDF Extraction, Computer Vision & OCR
AppTitan uses **four** different PDF/imaging libraries — a redundancy hotspot:
| Library | Version | Role | Used in |
|---|---|---|---|
| **pdfplumber** | 0.11.7 | Text + table extraction | `core/pdf_parser.py`, `core/telemetry_parser.py`, `core/analizador_eventos.py` |
| **pdfminer.six** | 20250506 | Transitive backend of pdfplumber | (indirect) |
| **pypdfium2** | 4.30.0 | Renders PDF pages to raster images | `core/telemetry_extractor.py` |
| **PyMuPDF (fitz)** | 1.26.4 | PDF rendering | **Only** `debug_text_events.py` (not in the production pipeline) |
| **fpdf2** | 2.8.3 | Generates output PDF reports | `core/report_generator.py` |
| **opencv-python** | 4.12.0.88 | HSV color masking, contour detection, curve extraction | `core/telemetry_extractor.py`, `core/telemetry_parser.py` |
| **pytesseract** | 0.3.13 | OCR of chart titles | `core/telemetry_extractor.py` |
| **Pillow** | 11.3.0 | Icon/image handling | `app.py` |

> **Undeclared OS-level dependency:** `pytesseract` is only a wrapper — it requires the **Tesseract OCR binary** installed on the host. This is **not** captured in `requirements.txt` nor documented anywhere. It is a hard deployment blocker for the visual-extraction path.

### 1.5 Persistence
- **SQLite 3** via the Python stdlib `sqlite3` module. **No ORM** (no SQLAlchemy/Django), no migration tool (no Alembic). Raw SQL strings throughout.

### 1.6 Notably Absent (relevant to SimuTwin)
- **No web/API framework:** no FastAPI, Flask, or uvicorn anywhere (grep-confirmed). There is currently **no way to call AppTitan over a network**.
- **No environment/config system:** zero uses of `os.environ` / `getenv` (grep-confirmed). No `.env`, no settings module, no secrets handling.
- **No containerization/CI:** no `Dockerfile`, `docker-compose`, `*.yml/*.yaml`, `pyproject.toml`, `pytest.ini`, or `conftest.py` (glob-confirmed).
- **`README.md` is empty.** `.gitignore` is minimal (venv, `__pycache__`, editor folders) and does **not** exclude `database/titan.db`, `models/`, `data/exports/`, or `debug_outputs/` — so generated artifacts and the live DB are version-controlled.

---

## 2. Main Directory Tree

Irrelevant/generated noise (`.git`, `__pycache__`, individual debug PNGs, per-file PDF exports) is collapsed or omitted.

```text
Proyecto-titan.v2/
├── core/                          # Business-logic package (the de-facto "backend")
│   ├── __init__.py                # empty
│   ├── pdf_parser.py              # Session metadata + "Consolidated Results" via pdfplumber + regex
│   ├── telemetry_extractor.py     # VISUAL telemetry extraction (pypdfium2 + OpenCV + Tesseract OCR)
│   ├── telemetry_parser.py        # ALT config-driven extraction (pdfplumber + OpenCV, hardcoded pages)
│   ├── graph_mapper.py            # Maps graph keys -> canonical signal names using mapeo.json
│   ├── db_manager.py              # SQLite access: connection ctx-manager + insert/query helpers
│   ├── behavior_analyzer.py       # Rule-based profile enum + per-signal telemetry metrics
│   ├── analizador_eventos.py      # Raw "Student Console Events" extraction + feedback text
│   ├── training_manager.py        # BehaviorAnalyzer + TrainingPath orchestrator  ⚠ ORPHANED/BROKEN
│   ├── training_path.py           # Learning-path catalog per profile             ⚠ UNUSED by frontends
│   ├── reporter.py                # Text report generation (individual + evolution)
│   ├── report_generator.py        # PDF report generation (fpdf2 subclass)
│   └── plotter.py                 # Matplotlib chart embedded in CustomTkinter    ⚠ GUI-coupled "core"
│
├── database/
│   ├── schema.sql                 # DDL v4.0 — DROP TABLE IF EXISTS + CREATE
│   └── titan.db                   # Live SQLite DB (7 sessions at time of analysis)
│
├── data/
│   ├── reports/                   # INPUT: source simulator PDF reports
│   └── exports/                   # OUTPUT: copied processed PDFs + generated reports
│
├── models/
│   └── modelo_clasificador.joblib # Trained RandomForest classifier artifact
│
├── tests/
│   └── test_manifest.py           # Ad-hoc validation script (print-based, NO assertions/pytest)
│
├── debug_outputs/                 # ⚠ CV debug images (overlays/masks/crops) written on EVERY extraction
├── .qoder/repowiki/               # Auto-generated repo wiki/knowledge (tooling, not app code)
│
├── main.py                        # ENTRY: batch ETL driven by manifest.csv (NO telemetry extraction)
├── app.py                         # ENTRY: CustomTkinter desktop app (590-line monolith)
├── streamlit_app.py               # ENTRY: Streamlit web dashboard
├── entrenador_ia.py               # ENTRY: ML training script (RandomForest -> joblib)
├── analisis_mvp.py                # ENTRY: comparative per-profile analysis report
├── setup_database.py              # ENTRY: (re)creates DB from schema.sql (DELETES existing file)
│
├── color_picker.py                # Utility: interactive image color/region picker
├── coordinate_finder.py           # Utility: interactive coordinate finder
├── debug_text_events.py           # Debug: PyMuPDF text extraction
├── debug_text_events_plumber.py   # Debug: pdfplumber text extraction
├── test_debug.py, test_debug2.py, test_extractor.py, test_fallback.py,
├── test_final.py, test_grafico.py, test_mapper.py, test_plot_unknowns.py   # ⚠ Ad-hoc scripts at root
│
├── manifest.csv                   # CONFIG: PDF filename -> labeled profile / operator / exercise
├── mapeo.json                     # CONFIG: "Graph_X_Y" key -> canonical signal name
├── requirements.txt               # Flat pinned deps (pip-freeze style)
├── README.md                      # EMPTY
└── .gitignore                     # Minimal
```

**Modularity assessment:** The `core/` package is a reasonable domain layer, but the repository root is polluted with **6 entry scripts + ~12 ad-hoc `test_*`/`debug_*` scripts** that have no package boundary, no CLI framework, and overlapping responsibilities. There is no `src/` layout, no `api/`, no `services/`, and no clear separation between "application" and "experiment" code.

---

## 3. Current Database Schema

### 3.1 Engine
- **SQLite 3**, single file at `database/titan.db`. DDL lives in `database/schema.sql` (header comments call it "Versión 4.0"). Access is centralized (mostly) in `core/db_manager.py`, but `core/behavior_analyzer.py` and `core/reporter.py` **also open their own connections** to a hardcoded path.

### 3.2 Tables

**`Sesiones`** — one row per processed PDF report (the session aggregate root).
| Column | Type | Notes |
|---|---|---|
| `id_sesion` | INTEGER PK AUTOINCREMENT | |
| `nombre_archivo_origen` | TEXT NOT NULL **UNIQUE** | De-facto idempotency key for re-processing |
| `nombre_operador` | TEXT | Operator is a **free-text label**, not an entity/FK |
| `nombre_clase` | TEXT | |
| `nombre_ejercicio` | TEXT | |
| `fecha_hora_inicio` | TEXT | Stored as string, not a typed datetime |
| `duracion_segundos` | INTEGER | |
| `puntaje_final` | REAL | |
| `perfil_operador` | TEXT | Assigned profile (see §4.5 — 3 different sources) |
| `fecha_carga` | TIMESTAMP DEFAULT CURRENT_TIMESTAMP | |

**`ResumenEventos`** — aggregated "Consolidated Results" rows (long format).
| Column | Type | Notes |
|---|---|---|
| `id_resumen` | INTEGER PK AUTOINCREMENT | |
| `id_sesion` | INTEGER NOT NULL | FK → `Sesiones(id_sesion)` **ON DELETE CASCADE** |
| `tipo_evento` | TEXT | e.g. "Collision", "Error" |
| `conteo_eventos` | INTEGER | |
| `recompensas` | REAL | |
| `penalizaciones` | REAL | |

**`Telemetria`** — extracted time series per graph.
| Column | Type | Notes |
|---|---|---|
| `id_telemetria` | INTEGER PK AUTOINCREMENT | |
| `id_sesion_fk` | INTEGER NOT NULL | FK → `Sesiones(id_sesion)` **ON DELETE CASCADE**. ⚠ inconsistent naming vs `ResumenEventos.id_sesion` |
| `nombre_grafico` | TEXT NOT NULL | e.g. "Steering", "Brake Pad" |
| `timestamps` | TEXT NOT NULL | ⚠ **Comma-separated floats in a single TEXT blob** (`"0.0,0.48,0.96,..."`) |
| `valores` | TEXT NOT NULL | ⚠ **Comma-separated floats in a single TEXT blob** |

**Indexes:** `idx_sesiones_operador(nombre_operador)`, `idx_sesiones_perfil(perfil_operador)`, `idx_telemetria_sesion(id_sesion_fk)`.

### 3.3 Live Data Snapshot (at analysis time)
- `Sesiones`: **7** rows · `ResumenEventos`: **42** rows · `Telemetria`: **19** rows.
- Distinct `perfil_operador` across all sessions: **only `Ineficiente`** → the classifier currently collapses to a single class (see §6-I).
- Distinct `nombre_grafico` values are the six canonical signals (Steering, Brake Pad, Acceleration Pad, Fork Height In Mtrs, Tilt Angle In Deg, Speed In Km/h). These were persisted by the **desktop** path (`telemetry_parser`), since the Streamlit path is currently broken (§6-A).

### 3.4 Design Observations (migration-relevant)
- **No `Operadores` / `Metricas` entities.** Operators, exercises, and classes are denormalized text on `Sesiones`. There is no operator identity, no tenancy, and no user/auth concept — all of which SimuTwin will require.
- **Time series are not queryable.** Storing `timestamps`/`valores` as CSV-in-TEXT means every consumer must `str.split(',')` and re-hydrate in Python (done in `db_manager.get_telemetry_for_graph` and `behavior_analyzer._get_telemetry_df`). No SQL aggregation, no indexing, no compression. This will not survive a move to PostgreSQL/Supabase without a redesign into a proper `(session_id, signal, t, value)` point table or a time-series store.
- **Destructive lifecycle.** `schema.sql` begins with `DROP TABLE IF EXISTS ...`, and `setup_database.py` **deletes the `titan.db` file** before re-running it. There is no forward migration path — schema changes are wipe-and-recreate.
- **No connection pooling / concurrency strategy.** SQLite + Streamlit's multi-session runtime will hit file locks under concurrent uploads (see §6-J).

---

## 4. ETL Pipeline (Extraction & Processing)

> **Critical structural fact:** AppTitan has **two divergent pipelines** that share only the text parser. They differ in entry point, labeling strategy, telemetry engine, and output.

### 4.1 Pipeline A — Batch / manifest-driven (`main.py`)
```
manifest.csv ──► for each row:
                   check_if_file_processed()      # dedupe by filename
                   parse_pdf_report()             # pdfplumber + regex (page 1)
                   insert_session(perfil = LABEL FROM manifest.csv)
                   insert_summary_events()
                 ──► single conn.commit() at end  ──► print summary
```
- The operator profile comes from the **human-labeled `manifest.csv`** (`perfil_etiquetado` column) — this is the *ground-truth/training* path.
- **This pipeline does NOT extract or store telemetry at all.** `Telemetria` is never populated by `main.py`.

### 4.2 Pipeline B — Interactive (`app.py` & `streamlit_app.py`, function `_procesar_y_obtener_id`)
```
uploaded/selected PDF
  ├─ get_session_id_by_filename()   # if present → return cached id (idempotent)
  ├─ parse_pdf_report()             # pdfplumber + regex
  ├─ joblib.load(modelo_clasificador.joblib)
  ├─ _preparar_datos_para_prediccion()  # pivot events → wide feature DataFrame
  ├─ perfil = modelo.predict(df)[0]     # ⚠ profile comes from the ML MODEL here
  ├─ conn.execute("BEGIN")
  ├─ insert_session(perfil = ML prediction)
  ├─ insert_summary_events()
  ├─ TELEMETRY EXTRACTION  ← the two frontends DIVERGE here (see 4.4)
  ├─ insert_telemetry_data() per signal
  ├─ conn.commit()
  └─ shutil.copy2(pdf → data/exports/)
```
- `_procesar_y_obtener_id` is **reimplemented separately** in `app.py` (lines ~514-547) and `streamlit_app.py` (lines ~69-110), with slightly different bodies. It is *not* in `core/`.

### 4.3 PDF Text Parsing (`core/pdf_parser.py`)
1. Force locale `en_US.UTF-8` for date parsing (silently ignored if unavailable).
2. Open with `pdfplumber`, read **page 1 only**, `extract_text(x_tolerance=2)`.
3. Apply a dict of compiled regexes (`REGEX_PATTERNS`) to pull: `Student Name`, `Class name`, `Exercise Name`, `Exercise Duration`, `Score`, `Exercise Start Time`.
4. Parse the **"Consolidated Results"** table with a multiline regex into `{type, total_events, rewards, penalties}` dicts.
5. Normalize: `_parse_float`, `_parse_datetime` (`"%d %B %Y %I:%M %p"` → ISO), `_parse_duration` (`H:M:S` → seconds).
6. Returns `{"session_data": {...}, "summary_events": [...]}` or `None`. Wrapped in a broad `try/except` that **prints and returns `None`** on any failure.

> This parser is **layout-brittle**: it depends on exact English labels, page-1 positioning, and a specific table format. Any change in the simulator's PDF template breaks extraction silently.

### 4.4 Telemetry Extraction — **two incompatible engines**

**Engine 1 — `core/telemetry_parser.py` (`extraer_toda_la_telemetria`)** — used by the **desktop** `app.py`:
- Config-driven via a `GRAPH_CONFIGS` dict with **hardcoded page numbers** (Steering=p9, Brake/Accel=p6, Fork/Tilt=p8, Speed=p10), `image_index`, **hardcoded pixel-calibration coordinates** (`calib_pixel_x=(36,1025)`, `calib_pixel_y=(427,50)`), and per-graph real Y ranges.
- For each graph: crop the page image via pdfplumber, HSV-mask the purple curve, take the topmost masked pixel per column, then linearly scale pixels → `(time, value)`.
- **Returns keys that are already canonical names** ("Steering", ...), so it inserts directly without `graph_mapper`.

**Engine 2 — `core/telemetry_extractor.py` (`extraer_telemetria_visual`)** — used by the **Streamlit** app:
- Renders every page with `pypdfium2` (scale 2.0) → OpenCV.
- HSV purple mask → morphological close → contours → padded bounding boxes filtered by size → dedup by IoU.
- OCRs a strip **above** each box with `pytesseract` to read the chart title → `_guess_name_from_title()` keyword match.
- If OCR fails → `_fallback_assign()` heuristic (guesses the signal from value range/span).
- Extracts the curve (topmost purple pixel per column) and calibrates with `Y_RANGES`.
- Writes many debug PNGs to `debug_outputs/` and prints per-candidate logs.
- **Returns keys that are canonical names OR `"Unknown_<page>_<cand>"`** — **never** `"Graph_X_Y"`.

**Then `core/graph_mapper.py` (`map_graphs`)** is applied **only in the Streamlit path**: it iterates `mapeo.json` (whose keys are `"Graph_5_1"`, `"Graph_5_2"`, …) and keeps a signal only if `mapeo.json`'s `graph` key exists in the extractor output.

> **⚠ Contract break (see §6-A):** Engine 2 emits `"Steering"`/`"Unknown_5_1"` keys, but `map_graphs` searches for `"Graph_5_1"` keys. The intersection is empty → `map_graphs` returns `{}` → **the Streamlit app persists no telemetry**.

### 4.5 Profile Assignment — **three sources of truth**
The value written to `Sesiones.perfil_operador` depends on which path ran:
1. **`main.py`** → the human **label** from `manifest.csv`.
2. **`app.py` / `streamlit_app.py`** → the **ML model** prediction (`RandomForestClassifier`).
3. **`core/training_manager.py`** → the **rule-based** `BehaviorAnalyzer` (thresholds on score/duration/penalties) — but this module is orphaned/broken (§6-C).

### 4.6 Analysis & Presentation (read path)
- `core/behavior_analyzer.analizar_comportamiento_completo(session_id)` re-reads `Telemetria` from SQLite and computes per-signal metrics (hard-braking count, steering jerks, acceleration spikes, fork/tilt micro-adjustments, speed stats) using pandas `.diff()`.
- `core/reporter.generar_texto_reporte_individual(...)` renders a formatted text block + rule-based narrative feedback.
- Streamlit renders one Matplotlib chart per signal (`get_telemetry_for_graph`) and a "smart feedback" section from `analizador_eventos`. Desktop renders charts via `core/plotter` and can export PDFs via `core/report_generator` (fpdf2).

---

## 5. Frontend/Backend Coupling

**Verdict: Partially separated — domain logic lives in `core/`, but the *application/orchestration* layer is duplicated inside each frontend, and one `core/` module is GUI-bound.**

### 5.1 What is properly decoupled ✅
- Parsing, extraction, DB access, behavior analysis, and report generation are all in `core/` and are importable without a UI.
- Both frontends import from `core.*` and never the reverse (dependency direction is outward).

### 5.2 Coupling hotspots ⚠
| # | Issue | Evidence | Impact |
|---|---|---|---|
| C-1 | **Orchestration duplicated in frontends** | `_procesar_y_obtener_id` and `_preparar_datos_para_prediccion` exist in **both** `app.py` and `streamlit_app.py` with divergent bodies | No single reusable "process a report" service; bug fixes must be applied twice |
| C-2 | **UI calls embedded in processing logic** | `streamlit_app._procesar_y_obtener_id` calls `st.error(...)` mid-ETL | The processing function cannot run headless (e.g., in a worker/API) without Streamlit |
| C-3 | **A `core/` module depends on the GUI toolkit** | `core/plotter.py` imports `customtkinter` and `matplotlib...backend_tkagg` | "Backend" package is not UI-agnostic; cannot be lifted into a headless service as-is |
| C-4 | **Config constants duplicated per frontend** | `GRAFICOS_DISPONIBLES`, `RUTAS_DE_APRENDIZAJE`, `DB_PATH`, `MODELS_PATH`, `EXPORTS_DIR` redefined in `app.py` and `streamlit_app.py` | Divergence risk; no central settings |
| C-5 | **Frontends choose different extraction engines** | `app.py` → `telemetry_parser`; `streamlit_app.py` → `telemetry_extractor` + `graph_mapper` | Two behaviors for the same feature; only one currently works |
| C-6 | **`core/` opens its own DB connections** | `behavior_analyzer.py` and `reporter.py` build `DB_PATH` and connect directly instead of receiving a connection | Hidden I/O inside "pure" logic; hard to inject a test/remote DB |
| C-7 | **Desktop monolith** | `app.py` = 590 lines mixing theme, widgets, tooltips, ML predict, DB, file copy | High change cost; UI and logic not separable without refactor |

**Bottom line for SimuTwin:** A clean service cannot be carved out by simply "calling `core/`". The reusable pipeline (`process PDF → profile + telemetry → persist`) currently exists **only inside the frontends** and must first be extracted into a headless `core/` service function.

---

## 6. Technical Debt & Critical Points

Prioritized. Severity: 🔴 Critical (blocks integration) · 🟠 High · 🟡 Medium.

### 🔴 A — Broken extractor→mapper contract (silent data loss)
- **Where:** `core/graph_mapper.py` + `mapeo.json` vs `core/telemetry_extractor.py`; consumed in `streamlit_app.py:102-106`.
- **What:** `map_graphs` looks up `"Graph_X_Y"` keys, but the visual extractor emits canonical/`"Unknown_X_Y"` keys. Intersection is empty → returns `{}`.
- **Impact:** The Streamlit web app persists **no telemetry**, so all charts/metrics are empty there. Fails silently (only verbose `print` debug).
- **Fix:** Unify the naming contract — either have the extractor emit `Graph_X_Y` keys, or drop `graph_mapper` and insert extractor output directly (as the desktop path does). Then reconcile `mapeo.json`.

### 🔴 B — Duplicated & divergent ETL orchestration
- **Where:** `app.py` vs `streamlit_app.py` (`_procesar_y_obtener_id`, `_preparar_datos_para_prediccion`, config dicts); two telemetry engines.
- **Impact:** No single source of truth; the two UIs behave differently; any microservice must first consolidate this into one headless `core` service entry point.
- **Fix:** Extract a `core/pipeline.py::process_report(pdf, conn, profile_source)` used by all callers; delete per-frontend copies.

### 🟠 C — Orphaned / broken training subsystem (dead code + runtime ImportError)
- **Where:** `core/training_manager.py` imports `get_sessions_by_operator` and `get_summary_events_by_session` from `core.db_manager` — **these functions do not exist** (grep-confirmed). `core/training_path.py` and the rule-based `BehaviorAnalyzer` are not used by either frontend (they use a hardcoded `RUTAS_DE_APRENDIZAJE` dict instead).
- **Impact:** Calling `TrainingManager.evaluate_operator(...)` raises `ImportError`. Confuses architects about which recommendation engine is authoritative.
- **Fix:** Either implement the missing DB helpers and wire the module in, or delete `training_manager.py`/`training_path.py` and consolidate on one recommendation source.

### 🟠 D — `print()`-based error handling that swallows failures
- **Where:** pervasive — `db_manager.py`, `pdf_parser.py`, `behavior_analyzer.py`, `telemetry_extractor.py`, `analisis_mvp.py`.
- **Pattern:** broad `try/except Exception` → `print(...)` → return `None`/`{}`/`0`.
- **Impact:** No structured logging, no error propagation, no way for a calling service to distinguish "no data" from "crashed". Unobservable in production.
- **Fix:** Introduce `logging`, typed exceptions, and let errors bubble to a service boundary that returns proper HTTP statuses.

### 🔴 E — Hardcoded configuration & magic numbers (no env injection)
- **Where:** everywhere. `DB_PATH`/`MODELS_PATH`/`EXPORTS_DIR` computed from `__file__`; `GRAPH_CONFIGS` page numbers and pixel-calibration coordinates; `Y_RANGES`; HSV color bounds; classification thresholds (`score>=85`, `penalties<=2`, `duration<180/>600`); learning-path catalogs; `manifest.csv` column names.
- **Impact:** Cannot configure per environment (dev/staging/prod), cannot containerize without editing source, cannot point at an external DB/model registry. **This is the single biggest microservice blocker.**
- **Fix:** Introduce a settings layer (e.g., `pydantic-settings` + `.env`), externalize all paths/thresholds, and inject the DB connection & model.

### 🟠 F — Denormalized, non-queryable telemetry storage
- **Where:** `Telemetria.timestamps` / `.valores` as CSV-in-TEXT (§3.2); re-parsed by string split in `db_manager.get_telemetry_for_graph` and `behavior_analyzer._get_telemetry_df`.
- **Impact:** No SQL aggregation/indexing; heavy Python-side parsing; blocks migration to PostgreSQL/Supabase/time-series stores.
- **Fix:** Model as a point table `(id, session_id, signal, t, value)` or a `JSONB`/array column in Postgres; add proper typing.

### 🟠 G — Side-effecting extraction + committed debug artifacts
- **Where:** `core/telemetry_extractor.py` writes dozens of PNGs into `debug_outputs/` and prints verbose logs **on every run**; `graph_mapper.py` prints debug traces. `debug_outputs/`, `database/titan.db`, `models/`, and `data/exports/` are **not git-ignored**.
- **Impact:** A "stateless" service that writes to the local filesystem on each request; repo pollution; non-deterministic artifacts.
- **Fix:** Gate debug output behind a flag/env var; write to a temp/volume dir; expand `.gitignore`.

### 🔴 H — No service, packaging, or deployment layer
- **Where:** repo-wide. No API framework, no `Dockerfile`, no CI, no `pyproject.toml`, no dependency grouping, undocumented **Tesseract** OS dependency, no `python_requires`.
- **Impact:** Nothing to deploy. Integration into SimuTwin requires standing up an HTTP surface (e.g., FastAPI), containerizing (incl. Tesseract + OpenCV system libs), and adding health checks — none exist today.
- **Fix:** Add FastAPI wrapper around the consolidated `core` pipeline; multi-stage Dockerfile with `tesseract-ocr` + `libgl` (OpenCV); split direct vs transitive deps; add CI.

### 🟠 I — Degenerate ML model / fragile feature contract
- **Where:** `entrenador_ia.py` (training) + `_preparar_datos_para_prediccion` (two divergent copies). Live DB shows **all 7 sessions predicted `Ineficiente`**.
- **Impact:** The classifier currently provides no discrimination (single-class collapse) — likely a tiny/imbalanced training set from `manifest.csv`. Feature alignment relies on manual DataFrame column juggling that differs between `app.py` and `streamlit_app.py`.
- **Fix:** Grow/curate the labeled dataset, add class-balance/validation metrics and a model registry, and centralize feature-vector construction in `core`.

### 🟠 J — SQLite concurrency & manual transaction control
- **Where:** `core/db_manager.get_db_connection` is a plain context manager that **does not commit/rollback**; callers manage transactions (`streamlit_app` issues `conn.execute("BEGIN")` manually). Multiple modules open independent connections.
- **Impact:** Under Streamlit's multi-user runtime (or any concurrent service), SQLite file locking will cause `database is locked` errors and inconsistent commits.
- **Fix:** Move to a server DB (PostgreSQL) for SimuTwin, use a pooled connection/ORM, and centralize transaction boundaries.

### 🟡 K — No real test suite & brittle PDF parser
- **Where:** root `test_*.py` and `tests/test_manifest.py` are **print-based scripts** with no assertions and no pytest. `core/pdf_parser.py` depends on exact English labels, page-1 layout, and a fixed table format.
- **Impact:** No regression safety net; silent breakage when the simulator's PDF template changes.
- **Fix:** Add pytest with golden-file fixtures (sample PDFs → expected parse output); validate extraction against a known corpus.

### 🟡 L — Version-number drift & empty docs
- **Where:** Per-file version comments disagree (`db_manager` "v5.0", `pdf_parser` "v23.0", `app.py` "v8.2", `telemetry_parser` "v8.0", schema "v4.0", Streamlit title "v2.0"); `README.md` is empty; `.gitignore` has a stray first line ("Fragmento de código").
- **Impact:** No reliable notion of "what version is deployed"; onboarding friction.
- **Fix:** Single source of version truth (package metadata), a real README, and cleaned `.gitignore`.

---

## 7. Microservice Readiness Notes for SimuTwin

**Suggested extraction boundary (target):** a headless `core` service exposing roughly:
```
process_report(pdf_bytes, profile_source="model"|"label") -> {session, summary_events, telemetry[], profile}
analyze_session(session_id) -> behavior metrics
recommend_training(profile) -> learning path
```
wrapped by a FastAPI app, backed by PostgreSQL (redesigned telemetry table), configured via env vars, containerized with Tesseract + OpenCV system libs.

**What can be reused largely as-is:** `pdf_parser`, `telemetry_extractor` (after fixing the mapper contract and gating debug I/O), `behavior_analyzer` metric functions, `reporter`/`report_generator`, the trained-model loading pattern.

**What must be rebuilt before integration:** the orchestration layer (currently duplicated in frontends), configuration management, persistence model & concurrency, error handling/observability, packaging/deployment, and the ML training/validation pipeline.

**What should be deleted or quarantined:** `core/training_manager.py` + `core/training_path.py` (orphaned/broken), the duplicate `telemetry_parser.py` engine (pick one), root-level `test_*`/`debug_*` scripts, and committed `debug_outputs/`.

---

*End of Architectural State Document. All findings above are traceable to specific files/lines in the repository as of the analysis date.*
