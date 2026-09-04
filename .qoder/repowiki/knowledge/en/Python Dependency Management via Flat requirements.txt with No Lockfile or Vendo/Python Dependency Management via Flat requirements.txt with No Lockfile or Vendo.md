---
kind: dependency_management
name: Python Dependency Management via Flat requirements.txt with No Lockfile or Vendoring
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - setup_database.py
    - database/schema.sql
    - models/modelo_clasificador.joblib
---

## What system/approach is used

The project uses the standard Python `pip` ecosystem with a single flat `requirements.txt` file at the repository root to declare all third-party dependencies. There is no virtual environment management, no lockfile (no `requirements.lock`, `Pipfile.lock`, `poetry.lock`, etc.), no vendored packages directory, and no private PyPI registry configuration in this branch.

## Key files and packages

- **`requirements.txt`** — the sole dependency manifest. It pins every package to an exact version using `==` (e.g. `numpy==2.2.6`, `streamlit==1.49.1`, `scikit-learn==1.7.1`, `opencv-python==4.12.0.88`, `pdfplumber==0.11.7`, `PyMuPDF==1.26.4`, `customtkinter==5.2.2`, `altair==5.5.0`). This includes ~60 pinned entries spanning data processing (`pandas`, `numpy`, `pyarrow`), PDF parsing (`pdfminer.six`, `pdfplumber`, `PyMuPDF`, `pypdfium2`), visualization (`matplotlib`, `altair`, `pydeck`), ML (`scikit-learn`, `joblib`, `scipy`), UI (`streamlit`, `customtkinter`, `tkinter` via `customtkinter`), and utilities (`requests`, `tenacity`, `toml`, `Jinja2`, `GitPython`).
- **`setup_database.py`** — not a dependency manager, but demonstrates that runtime data persistence relies on the built-in `sqlite3` module plus a hand-maintained `database/schema.sql` schema; no ORM or migration tooling is declared as a dependency.
- **`models/modelo_clasificador.joblib`** — a pre-trained scikit-learn model serialized with `joblib`; it is committed directly into the repo rather than downloaded at install time.

## Architecture and conventions

- **Flat pinning**: Every dependency is pinned to an exact release version with `==`. This is the only versioning strategy present — there are no caret/tilde ranges, no version constraints beyond equality.
- **No lockfile**: Because there is no lockfile, reproducible installs depend entirely on the static `requirements.txt`. The absence of a lockfile means transitive dependency resolution is left to `pip` at install time.
- **No virtual environment declaration**: There is no `.venv/`, `venv/`, `environment.yml`, `Pipfile`, `pyproject.toml`, or `setup.py`/`pyproject.toml` metadata declaring the project's own package name or entry points. Dependencies are installed globally or in whatever active environment the developer runs `pip install -r requirements.txt` in.
- **No vendoring**: All third-party code is expected to be fetched from PyPI at install time; nothing is checked in under a `vendor/` or similar directory.
- **No private registry / auth**: There is no `.netrc`, `pip.conf`, `~/.config/pip/pip.conf`, or `PYPI_TOKEN` usage visible. All packages appear to come from the public PyPI index.
- **Runtime-only data assets**: Large binary artifacts (the trained model `models/modelo_clasificador.joblib`, sample PDFs under `data/reports/` and `data/exports/`, debug images under `debug_outputs/`) are committed directly to the repository rather than being downloaded or generated during install.

## Conventions and constraints

- **Observed convention**: All third-party packages are pinned with `==` in `requirements.txt`. This was verified by scanning the entire file — every non-empty line follows the `<package>==<version>` pattern.
- **Constraint enforced by the build/install process**: Running `pip install -r requirements.txt` is the documented and observed way to set up the environment; no other install command (e.g. `pipenv install`, `poetry install`) is referenced anywhere in the codebase.
- **Constraint enforced by code imports**: The application code imports only packages listed in `requirements.txt` (e.g. `core/telemetry_extractor.py`, `core/pdf_parser.py`, `core/report_generator.py`, `main.py`, `app.py`, `streamlit_app.py`), so adding a new runtime import without updating `requirements.txt` would break installation.
- **No explicit rule against newer versions**: While every dependency is pinned, there is no automated check (e.g. `pip-audit`, `dependabot`, `pip-upgrader`) visible in CI or scripts to enforce update policies or security auditing.
- **SQLite schema as data dependency**: Database structure is managed declaratively in `database/schema.sql` and applied programmatically by `setup_database.py`, which reads and executes the SQL file at setup time. This is the project's approach to schema evolution rather than a migration framework dependency.