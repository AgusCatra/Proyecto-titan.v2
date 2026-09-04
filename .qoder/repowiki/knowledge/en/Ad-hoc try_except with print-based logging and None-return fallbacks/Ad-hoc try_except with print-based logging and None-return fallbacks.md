---
kind: error_handling
name: Ad-hoc try/except with print-based logging and None-return fallbacks
category: error_handling
scope:
    - '**'
source_files:
    - core/db_manager.py
    - core/pdf_parser.py
    - core/telemetry_extractor.py
    - app.py
    - analisis_mvp.py
---

## What system/approach is used

The codebase does not define a centralized error-handling framework, custom exception hierarchy, or structured logging library. Instead, it relies on Python's built-in `try`/`except` blocks paired with `print(...)` for diagnostics and returning sentinel values (`None`, empty lists, empty dicts) to signal failure up the call stack. There are no `raise` statements for application-level errors except in `analisis_mvp.py`, where `FileNotFoundError` and generic `Exception` are raised from data-loading helpers.

## Key files and packages

- **`core/db_manager.py`** — Central SQLite access layer. Every DB operation is wrapped in `try`/`except sqlite3.Error` (and sometimes `KeyError`/`ValueError`). On failure it prints an error message, rolls back the transaction when appropriate, and returns `None` (or `False`/empty list). A `@contextmanager get_db_connection` ensures connections are closed even on error; if connection creation fails it raises a bare `Exception("No se pudo conectar a la base de datos")`.
- **`core/pdf_parser.py`** — PDF parsing via `pdfplumber`. The top-level `parse_pdf_report` catches a broad `Exception`, prints a "ERROR CRÍTICO" line, and returns `None`. Helper parsers (`_parse_float`, `_parse_datetime`, `_parse_duration`) catch `ValueError`/`TypeError` and return safe defaults (`0.0`, `None`).
- **`core/telemetry_extractor.py`** — Image/OCR pipeline. Internal functions return `None` when detection fails (e.g., `_extract_curve` returns `None` if too few pixels), and the caller skips that candidate. No exceptions propagate out of `extraer_telemetria_visual`; failures are treated as missing charts.
- **`app.py`** — Tkinter GUI entry point. Each user action (`procesar_reporte_individual`, `generar_reporte_evolucion`, `mostrar_grafico_seleccionado`, `exportar_a_pdf`) wraps its body in `try`/`except Exception:` and delegates to a single `_error_ui(titulo, detalle)` helper that writes the traceback into the results text area, shows a toast notification, and resets UI state. File/model loading errors (e.g., missing `modelo_clasificador.joblib`) are caught explicitly and surfaced via `_error_ui`.
- **`analisis_mvp.py`** — Analysis script that *does* raise: `conectar_base_datos` raises `FileNotFoundError` if the DB file is missing and re-raises `sqlite3.Error` as a generic `Exception`; `cargar_datos`, `transformar_datos`, `generar_analisis` wrap their work in `try`/`except Exception` and re-raise with a prefixed message.

## Architecture and conventions

1. **Fail-silent at the boundary, fail-up at the script level.** Library-like modules (`db_manager`, `pdf_parser`, `telemetry_extractor`) swallow exceptions, log via `print`, and return `None`/empty collections so callers can branch on success/failure without catching exceptions. Scripts that own the workflow (`analisis_mvp.py`, `main.py`, `entrenador_ia.py`) tend to let exceptions bubble up.
2. **UI-layer centralization.** In `app.py`, all user-triggered paths funnel through one `_error_ui` method that formats the title, traceback, status bar, and toast. This is the only place where tracebacks are surfaced to users.
3. **Transaction rollback on partial failure.** `insert_session`, `insert_summary_events`, and `insert_telemetry_data` each catch `sqlite3.Error` and call `connection.rollback()` before returning `None`, ensuring a failed insert does not leave the DB in a half-written state.
4. **Graceful degradation in OCR/vision path.** When OCR cannot identify a chart title, `_fallback_assign` uses value-range heuristics to guess the series name; when OCR fails entirely, the series is still extracted and labeled `Unknown_<index>` rather than aborting the whole page.
5. **Locale and I/O soft-fail.** `_setup_locale` silently ignores `locale.Error`; `get_telemetry_for_graph` returns `None` when no rows match instead of raising.

## Conventions and constraints

- **No custom exception types.** All errors use built-ins (`Exception`, `FileNotFoundError`, `sqlite3.Error`, `ValueError`, `KeyError`). There is no shared `errors` package or module-level exception class hierarchy.
- **Errors are logged by printing.** There is no `logging` import anywhere in the scanned files; diagnostics go to stdout/stderr via `print(f"... {e}")` or `print(f"  - ERROR CRÍTICO: ...")`.
- **Return-value signaling is mandatory.** Callers must check for `None`/empty results after calling `parse_pdf_report`, `get_telemetry_for_graph`, `insert_session`, etc. The contract is documented in docstrings (e.g., `parse_pdf_report` says it returns `Optional[Dict]`; `get_telemetry_for_graph` says it returns `Optional[List[Tuple[float, float]]]`).
- **Broad `except Exception` is used at UI boundaries only.** Core modules prefer specific exceptions (`sqlite3.Error`, `ValueError`, `KeyError`); the Tkinter app uses bare `except Exception:` to guarantee the UI never crashes and always restores button states in `finally`.
- **Database access goes through the context manager.** Direct `sqlite3.connect` calls outside `get_db_connection` are avoided in the UI layer; the context manager enforces `.close()` in `finally`.
- **Missing resources produce explicit user messages.** Missing model files trigger `_error_ui` with a descriptive message pointing to `models/modelo_clasificador.joblib`; missing DB files in `analisis_mvp.py` raise `FileNotFoundError`.

Overall, error handling is pragmatic and localized: robustness is achieved by swallowing low-level failures inside individual components and surfacing problems only at the application boundary (GUI or script entry points), using `print` for diagnostics and `None`/empty returns for control flow.