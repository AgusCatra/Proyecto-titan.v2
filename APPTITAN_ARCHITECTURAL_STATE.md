# AppTitan — Architectural State Document

> **Purpose:** Evidence-based snapshot of the AppTitan (Proyecto Titán v2) codebase, produced for downstream consumption by a Software Architect agent that will plan AppTitan's integration as a **microservice** inside the **SimuTwin** platform.
> **Audience:** An LLM acting as Software Architect. Prefer facts, file paths, and severity over prose.
> **Method:** Static analysis of 100% of first-party Python sources (`core/`, `ui/`, entry scripts, `tests/`, config), the SQL DDL, the live SQLite database, and `requirements.txt`. No assumptions were made where code could be read.
> **Repository state:** Single-package Python monorepo, no build system, no API layer, no containerization, no CI.
> **Revision:** Updated after the completed cleanup/decoupling refactor — dead modules and root debug scripts deleted, `core/plotter.py` migrated to the new `ui/` package, ETL orchestration unified in `core/pipeline.py`, `PyMuPDF` dropped, and a formal pytest suite added under `tests/`. Sections that were resolved by this refactor are marked **✅ RESOLVED**.

---

## 0. Executive Summary

AppTitan is a **desktop/batch analytics tool** — not a service. It ingests forklift-simulator PDF reports, extracts session metadata + telemetry curves, classifies the operator into a behavior profile, and renders text/PDF/chart output through **two independent frontends** (CustomTkinter desktop and Streamlit web) that share a UI-agnostic `core/` package and a local SQLite file.

> **Refactor status (this revision):** The cleanup/decoupling pass is **complete**. `core/` is now **100% UI-agnostic** (no GUI toolkit imports; enforced by an AST guard in `tests/test_arquitectura.py`), the desktop chart coupling moved to a new `ui/` package, the duplicated ETL orchestration was consolidated into `core/pipeline.py::process_simulator_pdf`, the orphaned `training_manager.py`/`training_path.py` and all root debug/ad-hoc scripts were deleted, `PyMuPDF` was dropped from `requirements.txt`, and a formal pytest suite now lives under `tests/`. `core/` is decoupled and **ready to be exposed as a REST API**; `streamlit_app.py` remains the active test bench.

**Readiness verdict for microservice extraction: IMPROVED — domain core is now headless and REST-ready, but service/config/packaging layers are still absent.**

| Dimension | Status | Note |
|---|---|---|
| Domain logic isolation (`core/`) | **Good** | `core/` is UI-agnostic; shared orchestration now lives in `core/pipeline.py` and is consumed by both frontends |
| Service/API layer | Absent | No FastAPI/Flask/HTTP surface yet, but `core/` can now be lifted headless |
| Configuration management | Absent | Zero env vars; all paths, thresholds, page numbers hardcoded |
| Persistence | SQLite file | Denormalized time-series stored as CSV-in-TEXT; destructive schema init |
| Packaging / deploy | Absent | No Docker, no lockfile separation, no CI, undocumented OS dep (Tesseract) |
| Test suite | **Present (pytest)** | Formal suite under `tests/` (fixtures + assertions) incl. an AST architecture guard and a relocated ETL smoke test (`tests/test_pipeline_smoke.py`) |
| Observability | Partial | `logging` adopted across `core/`; CV debug images now gated behind `DEBUG_EXPORT_IMAGES=False` |
| Correctness | Improved | Orphaned training module removed; extractor→mapper contract normalized; single-class ML collapse remains a data concern |

**Top remaining blockers to resolve before integration work:**
1. **No service boundary / no config injection** — everything is hardcoded relative paths and a local file DB (§6-E, §6-H).
2. **Denormalized, non-queryable telemetry storage** and SQLite concurrency limits (§6-F, §6-J).
3. **Degenerate ML model** — classifier still risks single-class collapse without a curated/balanced dataset (§6-I).

---

## 1. Technology Stack & Dependencies

### 1.1 Language & Runtime
- **Language:** Python 3 (modern type hints, `numpy` 2.x / `pandas` 2.x era). Exact minor version is **not pinned anywhere** (no `pyproject.toml`, no `runtime.txt`, no `.python-version`).
- **Package manager:** `pip` against a flat, fully-pinned `requirements.txt` (60 packages). The file is a **`pip freeze` dump**: it mixes ~14 direct dependencies with ~46 transitive ones, with no grouping, no comments, and no direct/transitive separation. No lockfile tooling (pip-tools / poetry / uv) is present.

### 1.2 Frontends (two, independent)
| Framework | Version | Where | Role |
|---|---|---|---|
| **CustomTkinter** | 5.2.2 | `app.py`, `ui/plotter.py` | Desktop GUI (`TitanApp` monolith) |
| **Streamlit** | 1.49.1 | `streamlit_app.py` | Browser dashboard |
| **Matplotlib** | 3.10.5 | `ui/plotter.py`, `streamlit_app.py` | Telemetry charts (TkAgg backend on desktop, confined to the `ui/` layer) |

> **Layering note:** All GUI/TkAgg coupling now lives in the new `ui/` package (`ui/plotter.py`, v3.0, migrated from the former `core/plotter.py`). `core/` imports nothing from `ui/` and pulls in no GUI toolkit — this invariant is enforced by an AST-based guard in `tests/test_arquitectura.py`.

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
AppTitan uses **three** different PDF/imaging libraries in the production path (the redundant `PyMuPDF` dependency was removed — see note below):
| Library | Version | Role | Used in |
|---|---|---|---|
| **pdfplumber** | 0.11.7 | Text + table extraction | `core/pdf_parser.py`, `core/telemetry_parser.py`, `core/analizador_eventos.py` |
| **pdfminer.six** | 20250506 | Transitive backend of pdfplumber | (indirect) |
| **pypdfium2** | 4.30.0 | Renders PDF pages to raster images | `core/telemetry_extractor.py` |
| **fpdf2** | 2.8.3 | Generates output PDF reports | `core/report_generator.py` |
| **opencv-python** | 4.12.0.88 | HSV color masking, contour detection, curve extraction | `core/telemetry_extractor.py`, `core/telemetry_parser.py` |
| **pytesseract** | 0.3.13 | OCR of chart titles | `core/telemetry_extractor.py` |
| **Pillow** | 11.3.0 | Icon/image handling | `app.py` |

> **Removed dependency:** `PyMuPDF==1.26.4` was dropped from `requirements.txt`. Its only consumer was the root debug script `debug_text_events.py`, which was deleted in this refactor; it was never part of the production pipeline.

> **Undeclared OS-level dependency:** `pytesseract` is only a wrapper — it requires the **Tesseract OCR binary** installed on the host. This is **not** captured in `requirements.txt` nor documented anywhere. It is a hard deployment blocker for the visual-extraction path.

### 1.5 Persistence
- **SQLite 3** via the Python stdlib `sqlite3` module. **No ORM** (no SQLAlchemy/Django), no migration tool (no Alembic). Raw SQL strings throughout.

### 1.6 Notably Absent (relevant to SimuTwin)
- **No web/API framework:** no FastAPI, Flask, or uvicorn anywhere (grep-confirmed). There is currently **no way to call AppTitan over a network**.
- **No environment/config system:** zero uses of `os.environ` / `getenv` (grep-confirmed). No `.env`, no settings module, no secrets handling.
- **No containerization/CI:** no `Dockerfile`, `docker-compose`, `*.yml/*.yaml`, `pyproject.toml`, or `pytest.ini` (glob-confirmed). A `tests/conftest.py` and a formal pytest suite now exist under `tests/`, but there is still no CI wiring.
- **`README.md` is empty.** `.gitignore` now excludes the generated artifacts and the live DB (`debug_outputs/`, `data/exports/`, `*.db`/`*.sqlite`, `.qoder/`, `.pytest_cache/`, `.env`), so those are no longer version-controlled.

---

## 2. Main Directory Tree

Irrelevant/generated noise (`.git`, `__pycache__`, individual debug PNGs, per-file PDF exports) is collapsed or omitted.

```text
Proyecto-titan.v2/
├── core/                          # Business-logic package — 100% UI-AGNOSTIC (REST-ready "backend")
│   ├── __init__.py                # empty
│   ├── pdf_parser.py              # Session metadata + "Consolidated Results" via pdfplumber + regex
│   ├── telemetry_extractor.py     # VISUAL telemetry extraction (pypdfium2 + OpenCV + Tesseract OCR); debug I/O gated
│   ├── telemetry_parser.py        # ALT config-driven extraction (pdfplumber + OpenCV, hardcoded pages)
│   ├── graph_mapper.py            # Maps/normalizes graph keys -> canonical signal names using mapeo.json
│   ├── db_manager.py              # SQLite access: connection ctx-manager + insert/query helpers
│   ├── pipeline.py                # Unified headless ETL orchestration (process_simulator_pdf) used by both frontends
│   ├── behavior_analyzer.py       # Rule-based profile enum + per-signal telemetry metrics (LIVE, in use)
│   ├── evaluador_diagnostico.py   # Diagnostic evaluation engine (dictamen, delta comparison, LLM payload)
│   ├── analizador_eventos.py      # Raw "Student Console Events" extraction + feedback text
│   ├── ai_advisor.py              # LLM-ready pedagogical feedback (mock/offline provider by default)
│   ├── reporter.py                # Text report generation (individual + evolution)
│   └── report_generator.py        # PDF report generation (fpdf2 subclass)
│
├── ui/                            # GUI adapter layer — owns ALL CustomTkinter/TkAgg coupling
│   ├── __init__.py                # package marker
│   └── plotter.py                 # v3.0 — Matplotlib chart embedded in CustomTkinter (migrated from core/plotter.py)
│
├── database/
│   ├── schema.sql                 # DDL v4.0 — DROP TABLE IF EXISTS + CREATE
│   └── titan.db                   # Live SQLite DB (11 sessions at time of this revision)
│
├── data/
│   ├── reports/                   # INPUT: source simulator PDF reports
│   └── exports/                   # OUTPUT: copied processed PDFs + generated reports
│
├── models/
│   └── modelo_clasificador.joblib # Trained RandomForest classifier artifact
│
├── tests/                         # Formal pytest suite (fixtures + assertions)
│   ├── conftest.py                # Shared fixtures (seeded temp DB with known-score sessions)
│   ├── test_arquitectura.py       # AST guard: core/ imports no UI toolkit; no stale duplication
│   ├── test_pipeline_smoke.py     # Relocated ETL smoke test (temp DB, non-mutation asserts, idempotency)
│   ├── test_evaluador_diagnostico.py
│   ├── test_ai_advisor.py
│   ├── test_report_generator.py
│   └── test_manifest.py
│
├── debug_outputs/                 # CV debug images — written only when DEBUG_EXPORT_IMAGES=True (git-ignored)
├── .qoder/repowiki/               # Auto-generated repo wiki/knowledge (tooling, not app code)
│
├── main.py                        # ENTRY: batch ETL driven by manifest.csv (NO telemetry extraction)
├── app.py                         # ENTRY: CustomTkinter desktop app; imports ui.plotter (GUI preserved standalone)
├── streamlit_app.py               # ENTRY: Streamlit web dashboard (active test bench)
├── entrenador_ia.py               # ENTRY: "Fase 5" ML trainer (RandomForest -> models/modelo_clasificador.joblib)
├── setup_database.py              # ENTRY: (re)creates DB from schema.sql (DELETES existing file)
│
├── manifest.csv                   # CONFIG: PDF filename -> labeled profile / operator / exercise
├── mapeo.json                     # CONFIG: "Graph_X_Y" key -> canonical signal name
├── requirements.txt               # Flat pinned deps (pip-freeze style; PyMuPDF removed)
├── README.md                      # EMPTY
└── .gitignore                     # Excludes generated artifacts, DB, .qoder/, .venv
```

**Modularity assessment:** After the cleanup refactor the repository root is lean: **5 entry scripts** (`main.py`, `app.py`, `streamlit_app.py`, `entrenador_ia.py`, `setup_database.py`) plus config/docs. All former ad-hoc `test_*`/`debug_*` root scripts and the legacy `analisis_mvp.py` ("Fase 3" profiles script) were **deleted**; the ETL smoke test was **relocated** into `tests/test_pipeline_smoke.py` as a formal pytest. The GUI coupling was **moved out of `core/`** into a dedicated `ui/` package, so `core/` is now a clean, headless domain layer with a single orchestration entry point (`core/pipeline.py`). There is still no `src/` layout and no `api/`/`services/` package, but the domain/service boundary is now clear enough to wrap with an HTTP layer.

> **Do not conflate:** `entrenador_ia.py` ("Fase 5", the model trainer that regenerates `models/modelo_clasificador.joblib`) is **preserved** and is *not* the deleted "Fase 3" `analisis_mvp.py` profiles script.

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

### 4.2 Pipeline B — Interactive (`app.py` & `streamlit_app.py`)
```
uploaded/selected PDF
  └─ _procesar_y_obtener_id()  # thin UI adapter — delegates 100% to core.pipeline.process_simulator_pdf()
        core.pipeline.process_simulator_pdf(pdf_path, profile_source, engine=...):
          ├─ get_session_id_by_filename()   # if present → return cached id (idempotent)
          ├─ parse_pdf_report()             # pdfplumber + regex
          ├─ _resolve_profile()             # joblib model prediction (or None / manifest override)
          ├─ _preparar_datos_para_prediccion()  # unified pivot events → wide feature DataFrame
          ├─ transacted DB write (BEGIN … commit)
          ├─ insert_session() / insert_summary_events()
          ├─ _extract_telemetry()           # engine = "visual" (extractor) or "parser"; then map_graphs()
          ├─ insert_telemetry_data() per signal
          └─ _safe_copy_to_exports(pdf → data/exports/)
```
- The ETL orchestration is **no longer duplicated**: both frontends now call the single headless service function `core/pipeline.py::process_simulator_pdf`, and `_procesar_y_obtener_id` in `app.py` / `streamlit_app.py` is just a thin adapter around it. Feature-vector construction (`_preparar_datos_para_prediccion`) is likewise centralized in `core/pipeline.py`.

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
- Debug PNG output to `debug_outputs/` is now **gated behind `DEBUG_EXPORT_IMAGES` (default `False`)** and logs via `logging` — it no longer writes images on every run.
- **Returns keys that are canonical names OR `"Unknown_<page>_<cand>"`** — **never** `"Graph_X_Y"`.

**Then `core/graph_mapper.py` (`map_graphs`)** normalizes the extractor output to the six canonical signals. It now accepts **both** canonical keys (`"Steering"`, …) and legacy `"Graph_X_Y"` keys (mapped via `mapeo.json`), and discards unidentified `"Unknown_<page>_<cand>"` keys.

> **✅ Contract fixed (former §6-A):** `map_graphs` previously searched *only* for `"Graph_X_Y"` keys while Engine 2 emitted canonical/`"Unknown_X_Y"` keys, so the intersection was empty and **no telemetry was persisted**. `map_graphs` is now a safe normalizer that recognizes canonical keys directly (and legacy keys via `mapeo.json`), so the visual engine's telemetry is persisted correctly. Both engines are now selected through the single `core/pipeline.py` `engine=` parameter (`"visual"` or `"parser"`).

### 4.5 Profile Assignment — **two sources of truth**
The value written to `Sesiones.perfil_operador` depends on which path ran:
1. **`main.py`** → the human **label** from `manifest.csv` (batch/training path).
2. **`app.py` / `streamlit_app.py`** (via `core/pipeline.py::process_simulator_pdf`) → the **ML model** prediction (`RandomForestClassifier`), or `None` if the model is missing/fails. `core/pipeline.py` also accepts a `perfil_override` for manual/manifest labeling.

> The former third source — the rule-based `BehaviorAnalyzer` invoked through `core/training_manager.py` — is **gone**: `training_manager.py`/`training_path.py` were deleted as orphaned/broken dead code (§6-C). The rule-based engine `core/behavior_analyzer.py` itself is **still present and in use** for per-signal telemetry metrics; it just no longer feeds a separate profile-assignment path. Diagnostic rule logic now lives in `core/evaluador_diagnostico.py`.

### 4.6 Analysis & Presentation (read path)
- `core/behavior_analyzer.analizar_comportamiento_completo(session_id)` re-reads `Telemetria` from SQLite and computes per-signal metrics (hard-braking count, steering jerks, acceleration spikes, fork/tilt micro-adjustments, speed stats) using pandas `.diff()`.
- `core/reporter.generar_texto_reporte_individual(...)` renders a formatted text block + rule-based narrative feedback.
- Streamlit renders one Matplotlib chart per signal (`get_telemetry_for_graph`) and a "smart feedback" section from `analizador_eventos`. Desktop renders charts via `ui/plotter` (the migrated, GUI-coupled layer) and can export PDFs via `core/report_generator` (fpdf2).

---

## 5. Frontend/Backend Coupling

**Verdict: Now cleanly separated — domain logic lives in a UI-agnostic `core/`, the shared ETL orchestration is centralized in `core/pipeline.py`, and all GUI coupling is isolated in the `ui/` package. The remaining hotspots are configuration duplication and the desktop monolith's size.**

### 5.1 What is properly decoupled ✅
- Parsing, extraction, DB access, behavior analysis, diagnostic evaluation, and report generation are all in `core/` and are importable without a UI.
- The reusable "process a report" pipeline now exists **once**, in `core/pipeline.py::process_simulator_pdf`, consumed by both frontends through thin adapters.
- Both frontends import from `core.*` (and `app.py` imports `ui.plotter`); `core/` never imports `ui/` or any GUI toolkit — enforced by the AST guard in `tests/test_arquitectura.py` (dependency direction is strictly outward).

### 5.2 Coupling hotspots
| # | Issue | Evidence | Impact / Status |
|---|---|---|---|
| C-1 | ~~Orchestration duplicated in frontends~~ | **✅ RESOLVED** — `_procesar_y_obtener_id` in both frontends is now a thin adapter delegating to `core/pipeline.py::process_simulator_pdf`; `_preparar_datos_para_prediccion` centralized in `core/pipeline.py` | Single reusable headless service function; fixes apply once |
| C-2 | UI calls embedded in processing logic | The processing logic now lives in `core/pipeline.py`, which returns an `errors` list instead of calling UI APIs; frontends surface those errors | Headless-safe; UI feedback is presentation-only |
| C-3 | ~~A `core/` module depends on the GUI toolkit~~ | **✅ RESOLVED** — `core/plotter.py` was migrated to `ui/plotter.py`; `core/` now has zero customtkinter/TkAgg/tkinter/matplotlib/streamlit/plotly/altair/PyQt/PySide/kivy imports | `core/` is UI-agnostic and can be lifted into a headless service as-is |
| C-4 | **Config constants duplicated per frontend** | `GRAFICOS_DISPONIBLES`, `RUTAS_DE_APRENDIZAJE`, `DB_PATH`, `MODELS_PATH`, `EXPORTS_DIR` redefined in `app.py` and `streamlit_app.py` | Divergence risk; no central settings (still open) |
| C-5 | ~~Frontends choose different extraction engines~~ | **✅ Addressed** — engine selection is now a single `engine=` parameter (`"visual"`/`"parser"`) on `core/pipeline.py`; both go through the same `map_graphs` normalizer | One code path, selectable engine |
| C-6 | **`core/` opens its own DB connections** | `behavior_analyzer.py` builds `DB_PATH` and connects directly (`reporter.py` now uses `db_manager.get_db_connection`) | Hidden I/O inside "pure" logic; hard to inject a test/remote DB (partially open) |
| C-7 | **Desktop monolith** | `app.py` remains a large CustomTkinter module mixing theme, widgets, tooltips, and orchestration calls | High change cost; preserved intentionally as a standalone tool |

**Bottom line for SimuTwin:** A clean service **can** now be carved out by wrapping `core/` — the reusable pipeline (`process PDF → profile + telemetry → persist`) exists as a single headless function (`core/pipeline.py::process_simulator_pdf`) with injectable `db_path`/`models_path`/`exports_dir`/`engine` parameters. Remaining work is configuration management, persistence redesign, and the HTTP/deploy layer.

---

## 6. Technical Debt & Critical Points

Prioritized. Severity: 🔴 Critical (blocks integration) · 🟠 High · 🟡 Medium.

### ✅ A — Broken extractor→mapper contract (RESOLVED)
- **Where:** `core/graph_mapper.py` (`map_graphs`) + `mapeo.json` vs `core/telemetry_extractor.py`; consumed via `core/pipeline.py::_extract_telemetry`.
- **Was:** `map_graphs` looked up only `"Graph_X_Y"` keys while the visual extractor emitted canonical/`"Unknown_X_Y"` keys → empty intersection → `{}` → no telemetry persisted (silent data loss).
- **Fix applied:** `map_graphs` is now a safe normalizer that recognizes canonical keys directly and legacy `"Graph_X_Y"` keys via `mapeo.json`, discarding unidentified keys. Telemetry from the visual engine is now persisted correctly.

### ✅ B — Duplicated & divergent ETL orchestration (RESOLVED)
- **Where:** formerly `app.py` vs `streamlit_app.py`; now consolidated in `core/pipeline.py::process_simulator_pdf`.
- **Fix applied:** A single headless orchestration function (`process_report`-style: `process_simulator_pdf`) is used by all callers; the per-frontend copies were reduced to thin adapters, and the two telemetry engines are selected through one `engine=` parameter.

### ✅ C — Orphaned / broken training subsystem (REMOVED)
- **Where:** `core/training_manager.py` and `core/training_path.py`.
- **Was:** `training_manager.py` imported `get_sessions_by_operator` / `get_summary_events_by_session` from `core.db_manager` — functions that did not exist — so `TrainingManager.evaluate_operator(...)` raised `ImportError` at runtime; neither module was imported by any frontend.
- **Action taken:** Both modules were **deleted** as orphaned/broken dead code. The live rule-based engine `core/behavior_analyzer.py` is **retained and still used**; rule-based diagnostic logic now resides in `core/evaluador_diagnostico.py`.

### 🟠 D — Mixed `print()`/`logging` error handling
- **Where:** `logging` was adopted in the newer/refactored `core/` modules (`pipeline.py`, `evaluador_diagnostico.py`, `graph_mapper.py`, `report_generator.py`, `telemetry_extractor.py`, `ai_advisor.py`), but `print()`-based handling still persists in some modules (`db_manager.py`, `pdf_parser.py`, `behavior_analyzer.py`). (The former `analisis_mvp.py` print-heavy script was deleted.)
- **Pattern:** broad `try/except Exception` → `print(...)` / `logger.warning(...)` → return `None`/`{}`/`0`.
- **Impact:** Inconsistent observability; some paths still swallow failures without structured logs.
- **Fix:** Finish migrating the remaining modules to `logging`, use typed exceptions, and let errors bubble to a service boundary that returns proper HTTP statuses.

### 🔴 E — Hardcoded configuration & magic numbers (no env injection)
- **Where:** everywhere. `DB_PATH`/`MODELS_PATH`/`EXPORTS_DIR` computed from `__file__`; `GRAPH_CONFIGS` page numbers and pixel-calibration coordinates; `Y_RANGES`; HSV color bounds; classification thresholds (`score>=85`, `penalties<=2`, `duration<180/>600`); learning-path catalogs; `manifest.csv` column names.
- **Impact:** Cannot configure per environment (dev/staging/prod), cannot containerize without editing source, cannot point at an external DB/model registry. **This is the single biggest microservice blocker.**
- **Fix:** Introduce a settings layer (e.g., `pydantic-settings` + `.env`), externalize all paths/thresholds, and inject the DB connection & model.

### 🟠 F — Denormalized, non-queryable telemetry storage
- **Where:** `Telemetria.timestamps` / `.valores` as CSV-in-TEXT (§3.2); re-parsed by string split in `db_manager.get_telemetry_for_graph` and `behavior_analyzer._get_telemetry_df`.
- **Impact:** No SQL aggregation/indexing; heavy Python-side parsing; blocks migration to PostgreSQL/Supabase/time-series stores.
- **Fix:** Model as a point table `(id, session_id, signal, t, value)` or a `JSONB`/array column in Postgres; add proper typing.

### 🟡 G — Side-effecting extraction + debug artifacts (mostly resolved)
- **Where:** `core/telemetry_extractor.py` can write PNGs into `debug_outputs/`, but this is now **gated behind `DEBUG_EXPORT_IMAGES` (default `False`)** and logs via `logging` instead of printing on every run. `.gitignore` now excludes `debug_outputs/`, `data/exports/`, and `*.db`/`*.sqlite`.
- **Impact:** Largely mitigated — a normal run is side-effect-free w.r.t. debug images, and generated artifacts/DB are no longer version-controlled. Residual: `models/` is still committed and the debug flag is a module constant, not an env var.
- **Fix:** Drive the debug flag from configuration/env; keep artifacts out of the image; write debug output to a temp/volume dir when enabled.

### 🔴 H — No service, packaging, or deployment layer
- **Where:** repo-wide. No API framework, no `Dockerfile`, no CI, no `pyproject.toml`, no dependency grouping, undocumented **Tesseract** OS dependency, no `python_requires`.
- **Impact:** Nothing to deploy. Integration into SimuTwin requires standing up an HTTP surface (e.g., FastAPI), containerizing (incl. Tesseract + OpenCV system libs), and adding health checks — none exist today.
- **Fix:** Add FastAPI wrapper around the consolidated `core` pipeline; multi-stage Dockerfile with `tesseract-ocr` + `libgl` (OpenCV); split direct vs transitive deps; add CI.

### 🟠 I — Degenerate ML model / fragile feature contract
- **Where:** `entrenador_ia.py` (training, "Fase 5") + `_preparar_datos_para_prediccion` (now a **single unified copy** in `core/pipeline.py`). The live DB previously showed all sessions predicted `Ineficiente`; the current snapshot (11 sessions) contains **both `Eficiente` and `Ineficiente`**, so the collapse is no longer total but the training set remains tiny/imbalanced.
- **Impact:** The classifier still risks weak discrimination on a small `manifest.csv`-derived dataset. Feature-vector construction is now centralized (no longer divergent between frontends).
- **Fix:** Grow/curate the labeled dataset, add class-balance/validation metrics and a model registry.

### 🟠 J — SQLite concurrency & manual transaction control
- **Where:** `core/db_manager.get_db_connection` is a plain context manager that **does not commit/rollback**; transaction boundaries are managed by the caller — now centralized in `core/pipeline.py` (explicit `BEGIN` … `commit`). `behavior_analyzer.py` still opens its own independent connection.
- **Impact:** Under Streamlit's multi-user runtime (or any concurrent service), SQLite file locking will cause `database is locked` errors and inconsistent commits.
- **Fix:** Move to a server DB (PostgreSQL) for SimuTwin, use a pooled connection/ORM, and keep transaction boundaries centralized.

### 🟡 K — Brittle PDF parser (test suite now present)
- **Where:** the former ad-hoc root `test_*.py` print scripts were **deleted**; `tests/` is now a **formal pytest suite** (`conftest.py` fixtures with assertions, incl. `test_manifest.py`, `test_evaluador_diagnostico.py`, `test_ai_advisor.py`, `test_report_generator.py`, `test_pipeline_smoke.py`, and the AST `test_arquitectura.py`). The remaining risk is that `core/pdf_parser.py` still depends on exact English labels, page-1 layout, and a fixed table format.
- **Impact:** Regression safety net now exists; silent breakage is still possible when the simulator's PDF template changes.
- **Fix:** Add golden-file fixtures (sample PDFs → expected parse output) and validate extraction against a known corpus.

### 🟡 L — Version-number drift & empty docs
- **Where:** Per-file version comments disagree (`db_manager` "v5.0", `pdf_parser` "v23.0", `app.py` "v8.2", `telemetry_parser` "v8.0", `ui/plotter` "v3.0", schema "v4.0", Streamlit title "v2.0"); `README.md` is still empty. (The former stray `.gitignore` first line has been cleaned; `.gitignore` is now comprehensive.)
- **Impact:** No reliable notion of "what version is deployed"; onboarding friction.
- **Fix:** Single source of version truth (package metadata) and a real README.

---

## 7. Microservice Readiness Notes for SimuTwin

**Suggested extraction boundary (target):** a headless `core` service. The primary entry point **already exists** as `core/pipeline.py::process_simulator_pdf(pdf_path, profile_source, *, db_path, models_path, exports_dir, engine, perfil_override) -> {session_id, metadata, telemetry_loaded, profile, errors, cached}`, with injectable paths/engine — it just needs an HTTP wrapper. Alongside it:
```
process_simulator_pdf(pdf_path, profile_source="model"|"none", engine="visual"|"parser") -> {session_id, metadata, telemetry, profile}   # EXISTS in core/pipeline.py
analyze_session(session_id) -> behavior metrics                 # core/behavior_analyzer.analizar_comportamiento_completo
evaluate_diagnostic(session_id[, post]) -> dictamen / delta      # core/evaluador_diagnostico
```
wrapped by a FastAPI app, backed by PostgreSQL (redesigned telemetry table), configured via env vars, containerized with Tesseract + OpenCV system libs.

**What can be reused largely as-is:** `core/pipeline.py` (the consolidated orchestration), `pdf_parser`, `telemetry_extractor` (mapper contract fixed, debug I/O now gated), `behavior_analyzer` metric functions, `evaluador_diagnostico`, `ai_advisor` (LLM-ready), `reporter`/`report_generator`, and the trained-model loading pattern. `core/` is already UI-agnostic and REST-ready.

**What must still be built before integration:** the HTTP/service layer, configuration management (env injection), persistence model & concurrency (PostgreSQL), full observability, packaging/deployment (Docker incl. Tesseract + OpenCV), and ML dataset curation/validation.

**Already deleted / resolved in this refactor:** `core/training_manager.py` + `core/training_path.py` (removed as orphaned/broken), the duplicated frontend orchestration (consolidated into `core/pipeline.py`), `core/plotter.py`'s GUI coupling (migrated to `ui/plotter.py`), the root-level `test_*`/`debug_*`/`analisis_mvp.py` scripts (deleted), the `PyMuPDF` dependency (dropped), and committed `debug_outputs/` (now git-ignored). The duplicate `telemetry_parser.py` engine is **retained** but is now selectable via one `engine=` parameter rather than diverging per frontend.

---

*End of Architectural State Document. All findings above are traceable to specific files/lines in the repository as of the analysis date.*
