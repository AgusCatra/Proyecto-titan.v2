---
kind: frontend_style
name: CustomTkinter + Streamlit Dual-Frontend with Centralized Design Tokens
category: frontend_style
scope:
    - '**'
source_files:
    - app.py
    - streamlit_app.py
---

## Overview

The project ships two distinct frontends for the same telemetry-analysis backend:

1. **Desktop UI** — a `tkinter`-based application built on top of **CustomTkinter** (`customtkinter as ctk`) in `app.py`, providing a dark-mode-first desktop experience with menus, sidebars, tabs, toasts, tooltips, and loading overlays.
2. **Web UI** — a lightweight **Streamlit** app in `streamlit_app.py` that uploads PDFs, runs the analysis pipeline, and renders Matplotlib charts inline.

There is no shared CSS/SCSS/Tailwind layer; styling lives directly in Python via CustomTkinter theme tokens and Streamlit's built-in theming.

## Desktop Frontend (`app.py`)

### Theme system
A module-level `Theme` class centralizes every color token used across the UI:

| Token | Value | Usage |
|---|---|---|
| `BG` | `#1E1F25` | Root window background |
| `SURFACE` | `#23262E` | Header, status bar, tab backgrounds |
| `CARD` | `#2A2E37` | Control cards, toast backgrounds, overlay containers |
| `ACCENT` / `ACCENT_HOVER` | `#4A90E2` / `#357ABD` | Primary action buttons |
| `SUCCESS` / `SUCCESS_HOVER` | `#00A67E` / `#008C6B` | "Process report" button |
| `MUTED` | `#8D93A1` | Secondary labels, status text |
| `TEXT` | `#FFFFFF` | Primary label text |
| `DIVIDER` | `#3A3F4B` | Separator lines |
| `WARNING` | `#F5A524` | Warning toasts |
| `DANGER` | `#EF4444` | Error toasts |

These are applied through CustomTkinter properties such as `fg_color`, `text_color`, and `hover_color` on every widget (frames, labels, buttons, progress bars, tab views). The default appearance mode is set to `dark` at startup via `ctk.set_appearance_mode("dark")` and the built-in `dark-blue` color theme via `ctk.set_default_color_theme("dark-blue")`. Users can toggle between `dark` and `light` from the View menu, which calls `Theme.apply_theme(mode)`.

### Typography
Fonts are defined once in `TitanApp._setup_app` as a `fonts` dict with semantic keys (`H1`, `H2`, `H3`, `BODY`, `MONO`) using `ctk.CTkFont`. Every component receives this dict and uses the matching key — there are no ad-hoc font sizes scattered in widgets.

### Component architecture
Styling is encapsulated in reusable classes rather than inline styles:
- `Header` — title + subtitle banner.
- `StatusBar` — left/right status text.
- `ControlCard` — action buttons, graph selector combo box, export button, plus hover-driven status updates.
- `ResultsArea` — tabbed pane (Resumen / Gráfico) with a monospaced result textbox and a matplotlib embedding frame.
- `Tooltip`, `Toast`, `LoadingOverlay` — floating helper windows styled with `corner_radius`, `fg_color=Theme.CARD`, and consistent fonts.
- `IconFactory` — programmatically draws SVG-like icons into `PIL.Image` objects wrapped as `ctk.CTkImage` so buttons share a uniform icon style.

### Layout conventions
- All frames use `corner_radius` (6–12) for rounded corners.
- Spacing is uniform: `padx=12/14/16`, `pady=(6,10)/(8,12)/(14,0)` etc., giving a consistent rhythm.
- The main layout is a two-column grid: fixed-width sidebar (minsize 340) + flexible results area (minsize 640).
- Menus expose keyboard shortcuts (`Ctrl+O`, `Ctrl+S`, `Ctrl+Shift+E`, `Ctrl+Q`).

## Web Frontend (`streamlit_app.py`)

### Page configuration
`st.set_page_config(page_title="Proyecto Titán v2.0", page_icon="🤖", layout="wide")` sets the browser tab and wide column layout. No custom CSS file is loaded; all visual customization happens via Matplotlib and Streamlit primitives.

### Chart styling
Charts are rendered with Matplotlib inside `_plot_telemetry_chart`:
- `plt.style.use('dark_background')` forces a dark canvas.
- Line color is hardcoded to `#4A90E2` (matching the desktop `ACCENT`).
- Grid: `linestyle='--', alpha=0.3`.
- Axis labels and ticks use gray colors; titles are white.
- Figure size is fixed at `(10, 4)` inches.

This gives the web charts a dark theme that visually matches the desktop app without any CSS.

### Sidebar & layout
The app uses Streamlit's built-in `st.sidebar` for file uploaders and the compare button, then conditionally renders sections with `st.subheader`, `st.json`, `st.code`, and `st.markdown`. There is no responsive CSS framework — Streamlit handles responsiveness.

## Cross-cutting Conventions

1. **Single source of truth for colors**: the desktop `Theme` class owns every hex value; components never hardcode colors elsewhere.
2. **Semantic naming over raw values**: fonts use `H1/H2/H3/BODY/MONO`; chart elements reuse `ACCENT`/`SUCCESS`/`MUTED` names instead of repeating hex codes.
3. **Dark-first design**: both frontends default to dark themes (CustomTkinter `dark` + Streamlit `dark_background` Matplotlib style).
4. **Consistent accent color**: `#4A90E2` is used as the primary brand color in both the desktop buttons and the Streamlit charts.
5. **Rounded corners everywhere**: CustomTkinter frames consistently apply `corner_radius` (6–12); Streamlit relies on its own card-like widgets.
6. **No external stylesheet**: there are no `.css`, `.scss`, `.less`, or Tailwind config files in the repository. All visual decisions live in Python.